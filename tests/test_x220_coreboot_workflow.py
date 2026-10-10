"""Synthetic-evidence tests for the standalone X220 Phase 07 staging adapter.

The fake artifact result is not a Coreboot build or hardware observation.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from autoloader.exceptions import SafetyError
from autoloader.rom_builder.artifact_engine import (
    COREBOOT_2609,
    X220_COREBOOT,
    ArtifactBuildResult,
    ArtifactManifest,
    ArtifactRecord,
    BackupProvenanceRecord,
    BuildStatus,
    CBFSInspectionRecord,
    CBFSEntry,
    CommandRecord,
    PinnedFile,
    ReproducibilityRecord,
    ToolEvidence,
)
from autoloader.workflow.compatibility import CompatibilityValidator
from autoloader.workflow.profiles import MIB
from autoloader.workflow.types import (
    ArtifactEvidence,
    BackupEvidence,
    ChipEvidence,
    ChipTopology,
    CompatibilityDecision,
    DeviceEvidence,
    EvidenceConfidence,
    FirmwareFamily,
    OperationPlan,
    ProgrammerEvidence,
    ProgrammerKind,
    RegionEvidence,
    VerificationStatus,
)
from autoloader.workflow.x220_coreboot import (
    X220CorebootWorkflow,
    X220WorkflowStage,
    create_x220_build_intent,
)

_REGIONS = (
    RegionEvidence("ifd", 0, 0x1000),
    RegionEvidence("gbe", 0x1000, 0x3000),
    RegionEvidence("me", 0x3000, 0x500000),
    RegionEvidence("bios", 0x500000, 8 * MIB),
)
_IFD_TEXT = "\n".join((
    "Flash Region 0 (Flash Descriptor): 00000000 - 00000fff",
    "Flash Region 1 (GbE): 00001000 - 00002fff",
    "Flash Region 2 (Intel ME): 00003000 - 004fffff",
    "Flash Region 3 (BIOS): 00500000 - 007fffff",
))
_CBFS_TEXT = "\n".join((
    "Name\tOffset\tType\tMetadata Size\tData Size\tTotal Size",
    "fallback/payload\t0x0\tpayload\t0x10\t0x1000\t0x1010",
))


def _prepare(tmp_path: Path, monkeypatch, *, accept_backup=True):
    import autoloader.workflow.x220_coreboot as module
    from tests.test_artifact_engine import _make_x220_backup_fixture

    tmp_path.mkdir(parents=True, exist_ok=True)
    backup_root = tmp_path / "backup"
    backup_root.mkdir()
    intent, backup, _regions, _old_blobs, original = _make_x220_backup_fixture(backup_root)
    intent = replace(intent, backup=BackupEvidence())
    fake_files = []
    source_bytes = {}
    for item in COREBOOT_2609.files:
        data = ("synthetic pinned source fixture:" + item.path).encode("utf-8")
        source_bytes[item.path] = data
        fake_files.append(PinnedFile(item.path, hashlib.sha256(data).hexdigest()))
    fake_pin = replace(COREBOOT_2609, files=tuple(fake_files))
    fake_recipe = replace(X220_COREBOOT, source=fake_pin)
    monkeypatch.setattr(module, "COREBOOT_2609", fake_pin)
    monkeypatch.setattr(module, "X220_COREBOOT", fake_recipe)
    workflow = X220CorebootWorkflow(intent, journal_path=tmp_path / "operation.json")
    assert workflow.validate_build_intent().decision is CompatibilityDecision.ALLOWED
    if accept_backup:
        workflow.accept_backup(backup)
        assert workflow.stage is X220WorkflowStage.RECOVERY_READY
    return workflow, backup, original, fake_pin, source_bytes, module


def _fake_result(tmp_path: Path, workflow, fake_pin, source_bytes, *, cbfs_entry=None, operation_id=None):
    operation_id = operation_id or str(uuid.uuid4())
    run = tmp_path / operation_id
    logs = run / "logs"
    staged_dir = run / "artifacts"
    logs.mkdir(parents=True)
    staged_dir.mkdir()
    staged = staged_dir / "coreboot.rom"
    assert workflow._original_backup is not None
    generated = bytearray(workflow._original_backup)
    generated[5 * MIB:] = b"\xA7" * (3 * MIB)
    generated_bytes = bytes(generated)
    staged.write_bytes(generated_bytes)

    source = tmp_path / "synthetic-coreboot-source"
    (source / "build").mkdir(parents=True)
    (source / "util" / "ifdtool").mkdir(parents=True)
    (source / "util" / "cbfstool").mkdir(parents=True)
    for rel, data in source_bytes.items():
        path = source / Path(*Path(rel).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    ifd_binary = source / "util" / "ifdtool" / "ifdtool"
    cbfstool_binary = source / "util" / "cbfstool" / "cbfstool"
    ifd_binary.write_bytes(b"synthetic ifdtool executable")
    cbfstool_binary.write_bytes(b"synthetic cbfstool executable")
    raw_output = source / "build" / "coreboot.rom"
    raw_output.write_bytes(generated_bytes)

    entry = cbfs_entry or CBFSEntry("fallback/payload", 0, "payload", 16, 4096, 4112)
    cbfs = CBFSInspectionRecord(
        tool_relative_path="util/cbfstool/cbfstool",
        tool_sha256=hashlib.sha256(cbfstool_binary.read_bytes()).hexdigest(),
        source_repository_url=fake_pin.repository_url,
        source_revision=fake_pin.revision,
        source_tree_id=fake_pin.tree_id,
        region="COREBOOT",
        required_payload="fallback/payload",
        inventory=(entry,),
        source_file_hashes=tuple(sorted(
            (item.path, item.sha256) for item in fake_pin.files
            if item.path in {"util/cbfstool/cbfs_image.c", "util/cbfstool/cbfstool.c"}
        )),
    )
    ifd_tool = ToolEvidence(
        relative_path="util/ifdtool/ifdtool",
        executable_sha256=hashlib.sha256(ifd_binary.read_bytes()).hexdigest(),
        source_repository_url=fake_pin.repository_url,
        source_revision=fake_pin.revision,
        source_tree_id=fake_pin.tree_id,
    )
    selected_relative = "artifacts/coreboot.rom"
    digest = hashlib.sha256(generated_bytes).hexdigest()
    plan = OperationPlan(
        profile_id=workflow.intent.profile_id,
        requested_model_id=workflow.intent.requested_model_id,
        firmware_family=workflow.intent.firmware_family,
        device=workflow.intent.device,
        topology=workflow.intent.topology,
        programmer=workflow.intent.programmer,
        artifact=ArtifactEvidence(
            path=str(staged.resolve()), model_id="x220", firmware_family=FirmwareFamily.COREBOOT,
            expected_sha256=digest, expected_size_bytes=8 * MIB,
            source_url=fake_pin.repository_url, source_revision=fake_pin.revision,
            regions=workflow.intent.topology.regions,
        ),
        backup=workflow.intent.backup,
    )
    provenance = workflow._backup_provenance
    assert isinstance(provenance, BackupProvenanceRecord)
    record = ArtifactRecord(
        relative_path=selected_relative,
        sha256=digest,
        size_bytes=8 * MIB,
        regions=workflow.intent.topology.regions,
        source_relative_path="build/coreboot.rom",
        cbfs_inspection=cbfs,
        backup_provenance=provenance,
        region_inspection_tool=ifd_tool,
    )

    raw_commands = (
        (("make", "olddefconfig"), ""),
        (("make", "crossgcc-i386", "CPUS=1"), ""),
        (("make", "-j1"), ""),
        ((str(ifd_binary), "-d", workflow.backup_set.chips[0].first_path), _IFD_TEXT),
        ((str(ifd_binary), "-d", str(raw_output)), _IFD_TEXT),
        ((str(cbfstool_binary), str(raw_output), "print", "-r", "COREBOOT", "-k"), _CBFS_TEXT),
    )
    commands = []
    for index, (argv, output) in enumerate(raw_commands, 1):
        rel = f"logs/{index:02d}.log"
        content = ("$ " + " ".join(argv) + "\n" + output).encode("utf-8")
        (run / rel).write_bytes(content)
        commands.append(CommandRecord(argv, 0, rel, hashlib.sha256(content).hexdigest()))
    manifest = ArtifactManifest(
        schema_version=1,
        operation_id=operation_id,
        created_at_utc=(datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
        recipe_id=workflow.intent.recipe_id,
        workflow_id="x220-coreboot",
        status="complete",
        source_repository_url=fake_pin.repository_url,
        source_revision=fake_pin.revision,
        source_tree_id=fake_pin.tree_id,
        source_files=tuple((item.path, item.sha256) for item in fake_pin.files),
        commands=tuple(commands),
        host_platform="synthetic fixture; no compilation occurred",
        toolchain=(("make", "synthetic"),),
        build_environment=(("fixture", "synthetic only"),),
        container_image_digest=None,
        configuration_sha256=workflow.intent.configuration_sha256,
        resolved_configuration_sha256="b" * 64,
        observed_chip_capacity_bytes=8 * MIB,
        expected_regions=workflow.intent.topology.regions,
        blob_sha256=tuple((item.name, item.sha256) for item in sorted(workflow._blob_inputs, key=lambda item: item.name)),
        artifacts=(record,),
        selected_artifact_relative_path=selected_relative,
        selected_artifact_sha256=digest,
        failure_code=None,
        failure_detail=None,
        reproducibility=ReproducibilityRecord("not_attempted", digest, None, "Synthetic fixture only."),
        finalized_operation_plan=plan,
        prebuild_intent_sha256=None,
    )
    manifest_path = run / "manifest.json"
    raw = (json.dumps(asdict(manifest), sort_keys=True, indent=2) + "\n").encode("utf-8")
    manifest_path.write_bytes(raw)
    manifest_path.with_name("manifest.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  manifest.json\n", encoding="ascii")
    return ArtifactBuildResult(BuildStatus.COMPLETE, "ARTIFACT_VERIFIED", "Synthetic fixture only.", workflow.intent.recipe_id, (staged,), manifest, manifest_path)


class _FakeArtifactEngine:
    def __init__(self, tmp_path, workflow, fake_pin, source_bytes):
        self.tmp_path, self.workflow, self.fake_pin, self.source_bytes = tmp_path, workflow, fake_pin, source_bytes
        self.request = None
        self.blob_bytes = ()

    def build(self, request):
        self.request = request
        self.blob_bytes = tuple((item.name, item.path.read_bytes()) for item in request.blob_inputs)
        return _fake_result(self.tmp_path / "artifact", self.workflow, self.fake_pin, self.source_bytes)


def test_x220_positive_synthetic_path_stops_hardware_pending(tmp_path, monkeypatch):
    workflow, backup, original, fake_pin, source_bytes, _module = _prepare(tmp_path, monkeypatch)
    engine = _FakeArtifactEngine(tmp_path, workflow, fake_pin, source_bytes)
    workflow.artifact_engine = engine
    result = workflow.build_and_stage(source_dir=tmp_path / "source", output_root=tmp_path / "out", cpu_count=1)

    assert engine.request.backup_intent == workflow.intent
    assert engine.request.backup_set == backup
    assert engine.request.expected_regions == _REGIONS
    assert len(engine.request.blob_inputs) == 3
    for blob_name, blob_bytes in engine.blob_bytes:
        region_name = {"descriptor.bin": "ifd", "me.bin": "me", "gbe.bin": "gbe"}[blob_name]
        region = next(item for item in _REGIONS if item.name == region_name)
        assert blob_bytes == original[region.start_byte:region.end_byte]
    assert result.stage is X220WorkflowStage.STAGED
    assert result.status == "STAGED/HARDWARE_PENDING"
    assert result.operation_plan is not None
    proposal = result.proposed_external_spi_plan
    assert proposal is not None
    assert proposal.capacity_bytes == 8 * MIB and proposal.voltage_mv == 3300
    assert proposal.programmer_kind is ProgrammerKind.EXTERNAL_SPI
    assert proposal.flash_write_enabled is False and proposal.restore_write_enabled is False
    assert proposal.readback_status is VerificationStatus.NOT_PERFORMED
    assert proposal.boot_status is VerificationStatus.NOT_PERFORMED
    assert result.recovery_readiness and result.recovery_readiness.status == "ready"
    assert result.recovery_readiness.recovery_write_enabled is False
    assert "No firmware write" in result.message
    assert workflow.resume_from_checkpoint().state.value == "backup-verified"
    assert original[:0x500000] == Path(backup.chips[0].first_path).read_bytes()[:0x500000]


@pytest.mark.parametrize(
    "device",
    [
        DeviceEvidence(raw_dmi_product_name="ThinkPad X220i", raw_dmi_product_version="4291CTO", raw_dmi_system_family="ThinkPad X220", raw_mtm="4291CTO", raw_bios_version="G1ET99WW (9.99)"),
        DeviceEvidence(raw_dmi_product_name="ThinkPad X220", raw_dmi_product_version="4299CTO", raw_dmi_system_family="ThinkPad X220", raw_mtm="4299CTO", raw_bios_version="G1ET99WW (9.99)"),
    ],
)
def test_x220_identity_alias_and_unsupported_mtm_block(device):
    intent = create_x220_build_intent(
        device=device,
        topology=ChipTopology(observed=True, chips=(ChipEvidence("main", 8 * MIB, True, "main", 3300),), regions=_REGIONS),
        programmer=ProgrammerEvidence(ProgrammerKind.EXTERNAL_SPI, True, True, True, 3300),
    )
    result = X220CorebootWorkflow(intent).validate_build_intent()
    assert result.decision is CompatibilityDecision.BLOCKED


@pytest.mark.parametrize(
    "topology,programmer",
    [
        (ChipTopology(True, (ChipEvidence("main", 4 * MIB, True, "main", 3300),), _REGIONS), ProgrammerEvidence(ProgrammerKind.EXTERNAL_SPI, True, True, True, 3300)),
        (ChipTopology(True, (ChipEvidence("main", 8 * MIB, True, "main", None),), _REGIONS), ProgrammerEvidence(ProgrammerKind.EXTERNAL_SPI, True, True, True, 3300)),
        (ChipTopology(True, (ChipEvidence("main", 8 * MIB, True, "main", 1800),), _REGIONS), ProgrammerEvidence(ProgrammerKind.EXTERNAL_SPI, True, True, True, 1800)),
        (ChipTopology(True, (ChipEvidence("main", 8 * MIB, True, "main", 3300), ChipEvidence("flash1", 8 * MIB, True, "top", 3300)), _REGIONS), ProgrammerEvidence(ProgrammerKind.EXTERNAL_SPI, True, True, True, 3300)),
    ],
)
def test_x220_prebuild_rejects_capacity_voltage_and_chip_count(topology, programmer):
    with pytest.raises(ValueError):
        create_x220_build_intent(
            device=DeviceEvidence(raw_dmi_product_name="ThinkPad X220", raw_dmi_product_version="4291CTO", raw_dmi_system_family="ThinkPad X220", raw_mtm="4291CTO", raw_bios_version="G1ET99WW (9.99)"),
            topology=topology,
            programmer=programmer,
        )


def test_x220_prebuild_rejects_incomplete_overlapping_or_wrong_bios_region_map():
    good_chip = (ChipEvidence("main", 8 * MIB, True, "main", 3300),)
    programmer = ProgrammerEvidence(ProgrammerKind.EXTERNAL_SPI, True, True, True, 3300)
    maps = (_REGIONS[:-1], (
        RegionEvidence("ifd", 0, 0x2000), _REGIONS[1], _REGIONS[2], _REGIONS[3],
    ), (
        *_REGIONS[:-1], RegionEvidence("bios", 0x500000, 8 * MIB - 1),
    ))
    for regions in maps:
        with pytest.raises(ValueError):
            create_x220_build_intent(
                device=DeviceEvidence(raw_dmi_product_name="ThinkPad X220", raw_dmi_product_version="4291CTO", raw_dmi_system_family="ThinkPad X220", raw_mtm="4291CTO", raw_bios_version="G1ET99WW (9.99)"),
                topology=ChipTopology(True, good_chip, regions),
                programmer=programmer,
            )


@pytest.mark.parametrize("bad_entry", [
    CBFSEntry("fallback/payload", 0, None, 16, 4096, 4112),
    CBFSEntry("fallback/payload", 0, "raw", 16, 4096, 4112),
    CBFSEntry("fallback/payload", 0, "payload", 16, 0, 16),
])
def test_x220_cbfs_payload_type_or_size_mismatch_blocks(tmp_path, monkeypatch, bad_entry):
    workflow, _backup, _original, fake_pin, source_bytes, _module = _prepare(tmp_path, monkeypatch)
    result = _fake_result(tmp_path / "artifact", workflow, fake_pin, source_bytes, cbfs_entry=bad_entry)
    staged = workflow.accept_artifact_result(result)
    assert staged.stage is X220WorkflowStage.BLOCKED
    assert staged.proposed_external_spi_plan is None


def test_x220_result_path_must_match_manifest_selected_path(tmp_path, monkeypatch):
    workflow, _backup, _original, fake_pin, source_bytes, _module = _prepare(tmp_path, monkeypatch)
    result = _fake_result(tmp_path / "artifact", workflow, fake_pin, source_bytes)
    external = tmp_path / "external-copy.rom"
    external.write_bytes(result.artifacts[0].read_bytes())
    staged = workflow.accept_artifact_result(replace(result, artifacts=(external,)))
    assert staged.stage is X220WorkflowStage.BLOCKED
    assert "manifest-selected" in staged.reasons[0]


def test_x220_manifest_log_path_traversal_is_rejected(tmp_path, monkeypatch):
    workflow, _backup, _original, fake_pin, source_bytes, _module = _prepare(tmp_path, monkeypatch)
    result = _fake_result(tmp_path / "artifact", workflow, fake_pin, source_bytes)
    command = result.manifest.commands[0]
    object.__setattr__(command, "log_relative_path", "../outside.log")
    result = _rewrite_manifest(result, result.manifest)
    staged = workflow.accept_artifact_result(result)
    assert staged.stage is X220WorkflowStage.BLOCKED
    assert "log path" in staged.reasons[0].lower()


def test_x220_manifest_symlinked_log_component_is_rejected(tmp_path, monkeypatch):
    workflow, _backup, _original, fake_pin, source_bytes, _module = _prepare(tmp_path, monkeypatch)
    result = _fake_result(tmp_path / "artifact", workflow, fake_pin, source_bytes)
    operation_root = result.manifest_path.parent
    logs_path = operation_root / "logs"
    path_type = type(logs_path)
    real_is_symlink = path_type.is_symlink

    def report_log_directory_as_symlink(path):
        if path == logs_path:
            return True
        return real_is_symlink(path)

    # Simulate the filesystem reporting a symlink so this safety boundary is
    # covered on Windows hosts where creating directory symlinks may be denied.
    monkeypatch.setattr(path_type, "is_symlink", report_log_directory_as_symlink)
    staged = workflow.accept_artifact_result(result)
    assert staged.stage is X220WorkflowStage.BLOCKED
    assert "log" in staged.reasons[0].lower() or "unsafe" in staged.reasons[0].lower()


def test_x220_pinned_source_files_are_rehashed_at_acceptance(tmp_path, monkeypatch):
    workflow, _backup, _original, fake_pin, source_bytes, _module = _prepare(tmp_path, monkeypatch)
    result = _fake_result(tmp_path / "artifact", workflow, fake_pin, source_bytes)
    generated = next(
        command for command in result.manifest.commands
        if Path(command.argv[0]).name == "ifdtool" and command.argv[2] != workflow.backup_set.chips[0].first_path
    )
    source_root = Path(generated.argv[2]).parents[1]
    pinned_file = fake_pin.files[0]
    (source_root / Path(*Path(pinned_file.path).parts)).write_bytes(b"changed after synthetic build")
    staged = workflow.accept_artifact_result(result)
    assert staged.stage is X220WorkflowStage.BLOCKED
    assert "pinned source file" in staged.reasons[0].lower()


def test_x220_non_bios_byte_flip_is_blocked_even_with_recomputed_manifest(tmp_path, monkeypatch):
    workflow, _backup, _original, fake_pin, source_bytes, _module = _prepare(tmp_path, monkeypatch)
    result = _fake_result(tmp_path / "artifact", workflow, fake_pin, source_bytes)
    selected = result.manifest.artifacts[0]
    raw_command = next(command for command in result.manifest.commands if Path(command.argv[0]).name == "ifdtool" and command.argv[2] != workflow.backup_set.chips[0].first_path)
    raw = Path(raw_command.argv[2])
    changed = bytearray(result.artifacts[0].read_bytes())
    changed[0x1000] ^= 0x01
    changed_bytes = bytes(changed)
    result.artifacts[0].write_bytes(changed_bytes)
    raw.write_bytes(changed_bytes)
    digest = hashlib.sha256(changed_bytes).hexdigest()
    new_record = replace(selected, sha256=digest)
    new_plan = replace(result.manifest.finalized_operation_plan, artifact=replace(result.manifest.finalized_operation_plan.artifact, expected_sha256=digest))
    manifest = replace(result.manifest, artifacts=(new_record,), selected_artifact_sha256=digest, finalized_operation_plan=new_plan)
    result = _rewrite_manifest(result, manifest)
    staged = workflow.accept_artifact_result(result)
    assert staged.stage is X220WorkflowStage.BLOCKED
    assert "region" in staged.reasons[0].lower()


def _rewrite_manifest(result, manifest):
    raw = (json.dumps(asdict(manifest), sort_keys=True, indent=2) + "\n").encode("utf-8")
    result.manifest_path.write_bytes(raw)
    result.manifest_path.with_name("manifest.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  manifest.json\n", encoding="ascii")
    return replace(result, manifest=manifest)


def test_x220_backup_copy_change_blocks_and_journal_plan_mismatch(tmp_path, monkeypatch):
    workflow, backup, _original, fake_pin, source_bytes, _module = _prepare(tmp_path, monkeypatch)
    original_bytes = Path(backup.chips[0].first_path).read_bytes()
    Path(backup.chips[0].first_path).write_bytes(b"\0" * (8 * MIB))
    blocked = workflow.accept_artifact_result(_fake_result(tmp_path / "artifact-a", workflow, fake_pin, source_bytes))
    assert blocked.stage is X220WorkflowStage.BLOCKED
    Path(backup.chips[0].first_path).write_bytes(original_bytes)

    workflow, _backup, _original, fake_pin, source_bytes, _module = _prepare(tmp_path / "second", monkeypatch)
    staged = workflow.accept_artifact_result(_fake_result(tmp_path / "artifact-b", workflow, fake_pin, source_bytes))
    assert staged.stage is X220WorkflowStage.STAGED, staged.reasons
    changed_plan = replace(staged.operation_plan, requested_model_id="x220i")
    with pytest.raises(Exception):
        workflow.resume_from_checkpoint(changed_plan)


def test_x220_backup_ancestor_symlink_blocks_before_recovery_manifest_access(tmp_path, monkeypatch):
    workflow, backup, _original, _fake_pin, _source_bytes, _module = _prepare(tmp_path, monkeypatch, accept_backup=False)
    backup_path = Path(backup.chips[0].first_path)
    linked_ancestor = backup_path.parent
    path_type = type(backup_path)
    real_is_symlink = path_type.is_symlink

    def report_backup_directory_as_symlink(path):
        if path == linked_ancestor:
            return True
        return real_is_symlink(path)

    monkeypatch.setattr(path_type, "is_symlink", report_backup_directory_as_symlink)

    class RecoveryMustNotReadPaths:
        def evaluate(self, *_args, **_kwargs):
            pytest.fail("Recovery readiness must not follow symlinked backup paths")

    workflow.recovery_service = RecoveryMustNotReadPaths()
    with pytest.raises(SafetyError, match="verified complete original backup"):
        workflow.accept_backup(backup)
    assert workflow.stage is X220WorkflowStage.BLOCKED


def test_x220_write_and_restore_entrypoints_are_unconditionally_blocked(tmp_path, monkeypatch):
    import autoloader.flasher.flashrom as flashrom_module
    import autoloader.rom_builder.artifact_engine as artifact_module

    def forbidden(*args, **kwargs):
        pytest.fail("Phase 07 cannot invoke a writer or external process")

    monkeypatch.setattr(flashrom_module.subprocess, "run", forbidden)
    monkeypatch.setattr(artifact_module.subprocess, "run", forbidden)
    intent = create_x220_build_intent(
        device=DeviceEvidence(raw_dmi_product_name="ThinkPad X220", raw_dmi_product_version="4291CTO", raw_dmi_system_family="ThinkPad X220", raw_mtm="4291CTO", raw_bios_version="G1ET99WW (9.99)"),
        topology=ChipTopology(True, (ChipEvidence("main", 8 * MIB, True, "main", 3300),), _REGIONS),
        programmer=ProgrammerEvidence(ProgrammerKind.EXTERNAL_SPI, True, True, True, 3300),
    )
    workflow = X220CorebootWorkflow(intent)
    for _ in range(2):
        with pytest.raises(SafetyError, match="physical writes are disabled"):
            workflow.request_flash(object(), extra_args=("-w", "ignored"))
    with pytest.raises(SafetyError, match="restore writes are disabled"):
        workflow.request_restore(object(), extra_args=("--write",))

