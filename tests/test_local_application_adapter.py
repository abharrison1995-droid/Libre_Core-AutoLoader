"""Fake-only coverage for the default local workflow adapter and loaders."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autoloader.application_controller import ApplicationController
from autoloader.detection.bios import BIOSDetector
from autoloader.detection.model import ModelDetector
from autoloader.flasher.backup_service import BackupManifestError, BackupSetService
from autoloader.flasher.flashrom import FlashromRunner, ProgrammerType
from autoloader.flasher.spi import SPIProbeService
from autoloader.rom_builder.artifact_engine import ArtifactEngine
from autoloader.reversion.t430_handoff import T430DetectionCapture
from autoloader.workflow.local_application_adapter import LocalWorkflowEvidenceAdapter
from autoloader.workflow.profiles import MIB
from autoloader.workflow.types import (
    ChipEvidence,
    ChipTopology,
    DeviceEvidence,
    ProgrammerEvidence,
    ProgrammerKind,
    build_intent_sha256,
)
from autoloader.workflow.x200_libreboot import create_x200_build_intent
from tests.test_phase03_spi_safety import FakeStorageInspector, FakeTransport, _probe_line


def _intent():
    now = datetime.now(timezone.utc).isoformat()
    device = DeviceEvidence(
        raw_dmi_product_name="ThinkPad X200",
        raw_dmi_product_version="7458CTO",
        raw_dmi_system_family="ThinkPad X200",
        raw_mtm="7458CTO",
    )
    chip = ChipEvidence(
        "flash0", 4 * MIB, True, physical_label="main SPI chip", voltage_mv=3300,
        voltage_evidence_source="user-reported-manual-measurement",
        voltage_attested_by="test operator", voltage_attested_at=now,
        voltage_instrument_id="meter-test-1", voltage_test_point="chip_vcc_and_programmer_vcc",
    )
    programmer = ProgrammerEvidence(
        ProgrammerKind.EXTERNAL_SPI, True, True, True, 3300,
        evidence_source="user-reported-manual-measurement", attested_by="test operator",
        attested_at=now, instrument_id="meter-test-1",
        measurement_test_point="chip_vcc_and_programmer_vcc",
    )
    return create_x200_build_intent(
        device=device,
        topology=ChipTopology(observed=True, chips=(chip,)),
        programmer=programmer,
    )


def _probe():
    transport = FakeTransport([_probe_line("W25Q32.V", 4 * MIB)], {4 * MIB: bytes([31]) * (4 * MIB)})
    return transport, SPIProbeService(transport).probe(ProgrammerType.CH341A)


def _request(tmp_path: Path) -> dict[str, object]:
    primary, recovery, source, output = (tmp_path / name for name in ("primary", "recovery", "source", "output"))
    primary.mkdir(exist_ok=True)
    recovery.mkdir(exist_ok=True)
    source.mkdir(exist_ok=True)
    return {
        "version": 1,
        "actor": "test operator",
        "attested_at": datetime.now(timezone.utc).isoformat(),
        "programmer": ProgrammerType.CH341A.value,
        "measured_voltage_mv": 3300,
        "interface_verified": True,
        "wiring_verified": True,
        "instrument_id": "meter-test-1",
        "measurement_test_point": "chip_vcc_and_programmer_vcc",
        "primary_directory": str(primary.resolve()),
        "recovery_directory": str(recovery.resolve()),
        "primary_persistent_confirmed": True,
        "recovery_persistent_confirmed": True,
        "source_directory": str(source.resolve()),
        "output_root": str(output.resolve()),
        "cpu_count": 2,
    }


def test_default_controller_constructs_lazy_local_adapter_without_hardware_calls(monkeypatch):
    calls: list[str] = []

    def unexpected(*args, **kwargs):
        calls.append("unexpected")
        raise AssertionError("constructor/status must not call local tools")

    monkeypatch.setattr(FlashromRunner, "run_command", unexpected)
    monkeypatch.setattr(FlashromRunner, "read_rom", unexpected)
    monkeypatch.setattr(FlashromRunner, "write_rom", unexpected)
    monkeypatch.setattr(ArtifactEngine, "build", unexpected)
    monkeypatch.setattr(ModelDetector, "detect", unexpected)
    monkeypatch.setattr(BIOSDetector, "detect", unexpected)
    controller = ApplicationController()
    controller.select_profile("x200-libreboot")
    controller.profile_status()
    assert isinstance(controller.workflow_evidence_adapter, LocalWorkflowEvidenceAdapter)
    assert calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("measured_voltage_mv", None),
        ("measured_voltage_mv", 1800),
        ("wiring_verified", False),
        ("interface_verified", None),
        ("instrument_id", ""),
        ("measurement_test_point", "unknown"),
        ("attested_at", "not-a-timestamp"),
        ("chip_name", "caller-selected-chip"),
    ],
)
def test_operator_request_rejects_missing_mismatched_or_candidate_observation_fields(tmp_path, field, value):
    request = _request(tmp_path)
    request[field] = value
    with pytest.raises(ValueError):
        LocalWorkflowEvidenceAdapter._parse_request(json.dumps(request))


def test_adapter_preparation_checkpoint_is_reopened_only_against_fresh_exact_intent(tmp_path, monkeypatch):
    request = _request(tmp_path)
    document = json.dumps(request)
    intent = _intent()
    transport, probe = _probe()
    capture = T430DetectionCapture(
        "capture-x200-test", datetime.now(timezone.utc), intent.device, "a" * 64
    )
    adapter = LocalWorkflowEvidenceAdapter(transport=transport)
    monkeypatch.setattr(adapter, "_observe", lambda profile, parsed: (capture, probe, intent))
    checkpoint = tmp_path / "session.json"

    saved_intent = adapter.prepare_build_intent("x200-libreboot", checkpoint, document)
    assert build_intent_sha256(saved_intent) == build_intent_sha256(intent)
    assert checkpoint.is_file()
    assert adapter.checkpoint_progress("x200-libreboot", checkpoint) == (False, False)
    reopened = adapter.load_build_intent("x200-libreboot", checkpoint, document)
    assert build_intent_sha256(reopened) == build_intent_sha256(intent)
    with pytest.raises(ValueError, match="new checkpoint"):
        adapter.prepare_build_intent("x200-libreboot", checkpoint, document)

    checkpoint_document = json.loads(checkpoint.read_text(encoding="utf-8"))
    checkpoint_document["operator"]["actor"] = "tampered"
    checkpoint.write_text(json.dumps(checkpoint_document), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        adapter.load_build_intent("x200-libreboot", checkpoint, document)


def test_controller_x220_build_passes_only_workflow_derived_blobs_to_artifact_engine(tmp_path, monkeypatch):
    from autoloader.application_controller import ApplicationAction
    from autoloader.flasher.flashrom import FlashToolIdentity
    from autoloader.flasher.spi import ChipProbeEvidence
    from autoloader.rom_builder.artifact_engine import ArtifactBuildResult, BuildStatus
    from autoloader.workflow.types import BackupEvidence, build_intent_sha256
    from autoloader.workflow.x220_coreboot import X220CorebootWorkflow
    from tests.test_artifact_engine import _make_x220_backup_fixture
    from tests.test_phase03_spi_safety import _probe_line

    fixture_root = tmp_path / "fixture"
    fixture_root.mkdir()
    intent, backup, regions, _fixture_blobs, original = _make_x220_backup_fixture(fixture_root)
    intent = replace(intent, backup=BackupEvidence())
    workflow = X220CorebootWorkflow(intent, journal_path=tmp_path / "operation.json")
    assert workflow.validate_build_intent().decision.value == "allowed"
    workflow.accept_backup(backup)

    setup_root = tmp_path / "operator"
    setup_root.mkdir()
    request_document = json.dumps(_request(setup_root))
    tool = FlashToolIdentity(
        "flashrom", str((tmp_path / "flashrom.exe").resolve()), "1.4.0", "flashrom v1.4.0",
        datetime.now(timezone.utc).isoformat(),
        "a" * 64,
        "pacman: test/flashrom 1.4.0-1",
    )
    probe = ChipProbeEvidence.from_output(
        programmer=ProgrammerType.CH341A,
        tool=tool,
        output=_probe_line("W25Q64.V", 8 * MIB),
    )
    saved = {
        "intent_sha256": build_intent_sha256(workflow.intent),
        "probe": {
            "raw_output_sha256": probe.raw_output_sha256,
            "chip_name": probe.chip_name,
            "capacity_bytes": probe.capacity_bytes,
            "tool_name": probe.tool.name,
            "tool_version": probe.tool.version,
            "tool_executable": probe.tool.executable,
        },
    }
    captured_requests = []

    class CapturingArtifactEngine:
        def build(self, request):
            captured_requests.append(request)
            return ArtifactBuildResult(
                BuildStatus.BLOCKED, "FAKE_BUILD_NOT_RUN", "Synthetic test engine did not compile firmware.",
                request.recipe_id,
            )

    adapter = LocalWorkflowEvidenceAdapter(artifact_engine=CapturingArtifactEngine())
    monkeypatch.setattr(adapter, "_read_checkpoint", lambda *_args: saved)
    monkeypatch.setattr(adapter, "_observe", lambda *_args: (None, probe, workflow.intent))
    monkeypatch.setattr(adapter, "load_verified_backup", lambda *_args: backup)
    controller = ApplicationController(workflow_evidence_adapter=adapter)
    controller.select_profile("x220-coreboot")
    monkeypatch.setattr(controller, "_reconstruct_x_workflow", lambda *_args: workflow)
    checkpoint = tmp_path / "session.json"
    checkpoint.write_text("synthetic checkpoint path", encoding="utf-8")

    result = controller.request_action(
        ApplicationAction.BUILD,
        profile_id="x220-coreboot",
        checkpoint_path=checkpoint,
        request_document=request_document,
    )

    assert result.code == "FAKE_BUILD_NOT_RUN"
    assert len(captured_requests) == 1
    build_request = captured_requests[0]
    assert {blob.name for blob in build_request.blob_inputs} == {
        "descriptor.bin", "me.bin", "gbe.bin"
    }
    assert build_request.backup_intent is workflow.intent
    assert build_request.backup_set is backup
    for blob in build_request.blob_inputs:
        region_name = {"descriptor.bin": "ifd", "me.bin": "me", "gbe.bin": "gbe"}[blob.name]
        region = next(item for item in regions if item.name == region_name)
        assert blob.path.read_bytes() == original[region.start_byte:region.end_byte]


def test_backup_persistent_loader_rehashes_both_fake_reads_and_checks_exact_tool(tmp_path):
    intent = _intent()
    payload = bytes((i * 37 + 23) % 251 for i in range(4 * MIB))
    transport = FakeTransport([_probe_line("W25Q32.V", 4 * MIB)], {4 * MIB: payload})
    probe = SPIProbeService(transport).probe(ProgrammerType.CH341A)
    primary, recovery = tmp_path / "primary", tmp_path / "recovery"
    primary.mkdir()
    recovery.mkdir()
    service = BackupSetService(transport, storage_inspector=FakeStorageInspector())
    result = service.capture(
        intent,
        (probe,),
        programmer=ProgrammerType.CH341A,
        programmer_evidence=intent.programmer,
        primary_directory=primary,
        recovery_directory=recovery,
        primary_persistent_confirmed=True,
        recovery_persistent_confirmed=True,
    )
    reopened = service.load_verified_result(
        intent,
        operation_id=result.operation_id,
        primary_manifest=result.primary_manifest,
        recovery_manifest=result.recovery_manifest,
        primary_manifest_sha256=result.primary_manifest_sha256,
        recovery_manifest_sha256=result.recovery_manifest_sha256,
        expected_tool=transport.identity,
    )
    assert reopened.operation_id == result.operation_id
    assert reopened.backup == result.backup
    assert len(transport.read_calls) == 2

    Path(reopened.chips[0].first_path).write_bytes(b"corrupted")
    with pytest.raises(BackupManifestError, match="missing or unsafe|changed"):
        service.load_verified_result(
            intent,
            operation_id=result.operation_id,
            primary_manifest=result.primary_manifest,
            recovery_manifest=result.recovery_manifest,
            primary_manifest_sha256=result.primary_manifest_sha256,
            recovery_manifest_sha256=result.recovery_manifest_sha256,
            expected_tool=transport.identity,
        )


def test_backup_loader_rejects_matching_hash_copy_outside_recorded_primary_directory(tmp_path):
    import autoloader.flasher.backup_service as backup_module

    intent = _intent()
    payload = bytes((i * 37 + 23) % 251 for i in range(4 * MIB))
    transport = FakeTransport([_probe_line("W25Q32.V", 4 * MIB)], {4 * MIB: payload})
    probe = SPIProbeService(transport).probe(ProgrammerType.CH341A)
    primary, recovery = tmp_path / "primary", tmp_path / "recovery"
    primary.mkdir()
    recovery.mkdir()
    service = BackupSetService(transport, storage_inspector=FakeStorageInspector())
    result = service.capture(
        intent,
        (probe,),
        programmer=ProgrammerType.CH341A,
        programmer_evidence=intent.programmer,
        primary_directory=primary,
        recovery_directory=recovery,
        primary_persistent_confirmed=True,
        recovery_persistent_confirmed=True,
    )

    outside_primary = recovery / "copied-from-primary.rom"
    outside_primary.write_bytes(Path(result.chips[0].first_path).read_bytes())
    rewritten_hashes = []
    for manifest_value in (result.primary_manifest, result.recovery_manifest):
        manifest_path = Path(manifest_value)
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        event = next(item for item in document["events"] if item.get("status") == "verified")
        event["chips"][0]["first_path"] = str(outside_primary)
        event_without_hash = {key: value for key, value in event.items() if key != "event_sha256"}
        event["event_sha256"] = backup_module._sha256(backup_module._canonical(event_without_hash))
        body = {key: value for key, value in document.items() if key != "manifest_sha256"}
        document["manifest_sha256"] = backup_module._sha256(backup_module._canonical(body))
        manifest_path.write_bytes(backup_module._canonical(document))
        rewritten_hashes.append(document["manifest_sha256"])

    with pytest.raises(BackupManifestError, match="under their separately recorded storage"):
        service.load_verified_result(
            intent,
            operation_id=result.operation_id,
            primary_manifest=result.primary_manifest,
            recovery_manifest=result.recovery_manifest,
            primary_manifest_sha256=rewritten_hashes[0],
            recovery_manifest_sha256=rewritten_hashes[1],
            expected_tool=transport.identity,
        )
