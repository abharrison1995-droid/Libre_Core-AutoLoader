"""Fake-evidence tests for the X200 Libreboot Phase 06 staging boundary.

No firmware build, flash probe, media write, or hardware writer is invoked.
The backup transport and ArtifactEngine result are deterministic test doubles.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from autoloader.exceptions import SafetyError
from autoloader.flasher.backup_service import BackupSetService, BackupSetResult
from autoloader.flasher.flashrom import ProgrammerType
from autoloader.flasher.recovery import RecoveryReadinessService
from autoloader.rom_builder.artifact_engine import (
    X200_4MB,
    X200_8MB,
    ArtifactBuildResult,
    ArtifactManifest,
    ArtifactRecord,
    BuildStatus,
    CBFSInspectionRecord,
    CBFSEntry,
    CommandRecord,
    ReproducibilityRecord,
    X200GrubFontEvidence,
    X200UBootDependencyEvidence,
    derive_lbmk_output_inventory,
    _build_environment_record,
    _ensure_operation_git_config,
)
from autoloader.workflow.compatibility import CompatibilityValidator
from autoloader.workflow.profiles import MIB
from autoloader.workflow.types import (
    ChipEvidence,
    ChipTopology,
    CompatibilityDecision,
    DeviceEvidence,
    FirmwareFamily,
    ProgrammerEvidence,
    ProgrammerKind,
    VerificationStatus,
    build_intent_sha256,
)
from autoloader.workflow.x200_libreboot import (
    X200ExternalSPIPlan,
    X200LibrebootWorkflow,
    X200WorkflowStage,
    create_x200_build_intent,
)
from autoloader.rom_builder.artifact_engine import (
    _LBMK_COREBOOT_CBFSTOOL_SOURCES,
    _LBMK_COREBOOT_REVISION,
    _LBMK_COREBOOT_TREE,
)
from tests.test_phase03_spi_safety import FakeStorageInspector, FakeTransport, _probe_line
from autoloader.flasher.spi import SPIProbeService


def _payload(size: int, salt: int = 13) -> bytes:
    return bytes((index * 37 + salt) % 251 for index in range(size))


def _regular_x200_intent(capacity: int = 4 * MIB):
    return create_x200_build_intent(
        device=DeviceEvidence(
            raw_dmi_product_name="ThinkPad X200",
            raw_dmi_product_version="7458CTO",
            raw_dmi_system_family="ThinkPad X200",
            raw_mtm="7458CTO",
        ),
        topology=ChipTopology(
            observed=True,
            chips=(ChipEvidence("flash0", capacity, True, physical_label="main", voltage_mv=3300),),
        ),
        programmer=ProgrammerEvidence(
            kind=ProgrammerKind.EXTERNAL_SPI,
            observed=True,
            interface_verified=True,
            wiring_verified=True,
            voltage_mv=3300,
        ),
    )


@pytest.fixture(scope="module")
def captured_backup(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("x200-backup-evidence")
    intent = _regular_x200_intent()
    primary = root / "primary"
    recovery = root / "recovery"
    primary.mkdir()
    recovery.mkdir()
    content = _payload(intent.expected_size_bytes)
    transport = FakeTransport(
        [_probe_line("W25Q32.V", intent.expected_size_bytes)],
        {intent.expected_size_bytes: content},
    )
    probe = SPIProbeService(transport).probe(ProgrammerType.CH341A)
    service = BackupSetService(
        transport,
        storage_inspector=FakeStorageInspector(),
    )
    backup_workflow = X200LibrebootWorkflow(intent, journal_path=root / "capture-stage.json")
    assert backup_workflow.validate_build_intent().decision is CompatibilityDecision.ALLOWED
    backup_workflow.capture_backup(
        service,
        (probe,),
        programmer=ProgrammerType.CH341A,
        primary_directory=primary,
        recovery_directory=recovery,
        primary_persistent_confirmed=True,
        recovery_persistent_confirmed=True,
    )
    assert backup_workflow.backup_set is not None
    return intent, backup_workflow.backup_set


def _workflow(intent, backup: BackupSetResult, journal_path: Path) -> X200LibrebootWorkflow:
    workflow = X200LibrebootWorkflow(intent, journal_path=journal_path)
    validation = workflow.validate_build_intent()
    assert validation.decision is CompatibilityDecision.ALLOWED, validation.reasons
    readiness = workflow.accept_backup(backup)
    assert readiness.status == "ready", readiness.reasons
    return workflow


def _synthetic_uboot_dependency(root: Path) -> X200UBootDependencyEvidence:
    import autoloader.rom_builder.artifact_engine as module

    objcopy = root / "fixture-objcopy"
    objcopy.write_bytes(b"Synthetic GNU objcopy identity; no tool executed.\n")
    objcopy.chmod(0o755)
    patch_commits = tuple(f"{index:x}" * 40 for index in range(1, 9))
    return X200UBootDependencyEvidence(
        source_url=module._UBOOT_X86_64_SOURCE_URL,
        backup_url=module._UBOOT_X86_64_BACKUP_URL,
        package_config_sha256=module._UBOOT_PACKAGE_CFG_SHA256,
        target_config_sha256=module._UBOOT_X86_64_TARGET_CFG_SHA256,
        amd64_target_config_sha256=module._UBOOT_AMD64_TARGET_CFG_SHA256,
        amd64_default_config_sha256=module._UBOOT_AMD64_DEFAULT_SHA256,
        target_tree="x86_64",
        base_revision=module._UBOOT_X86_64_BASE_REVISION,
        base_tree_id=module._UBOOT_X86_64_BASE_TREE,
        base_config_sha256=module._UBOOT_X86_64_BASE_CONFIG_SHA256,
        overlay_path=module._X200_UBOOT_OVERLAY_PATH,
        overlay_sha256=module._X200_UBOOT_OVERLAY_SHA256,
        patched_config_sha256=module._UBOOT_X86_64_PATCHED_CONFIG_SHA256,
        patch_inputs=module._X200_UBOOT_PATCH_STACK,
        patch_commits=patch_commits,
        resulting_revision=patch_commits[-1],
        resulting_tree_id=module._UBOOT_X86_64_PATCHED_TREE,
        cache_origin_relative_path=module._uboot_cache_relative_path(module._UBOOT_X86_64_SOURCE_URL),
        resulting_origin_url=module._UBOOT_X86_64_SOURCE_URL,
        base_ancestor_verified=True,
        tracked_worktree_clean=True,
        objcopy_path=str(objcopy.resolve()),
        objcopy_sha256=hashlib.sha256(objcopy.read_bytes()).hexdigest(),
        objcopy_version="GNU objcopy fixture; no compilation occurred",
    )


def _synthetic_grub_font_evidence() -> X200GrubFontEvidence:
    import autoloader.rom_builder.artifact_engine as module

    return X200GrubFontEvidence(
        source_font_path=module._X200_GRUB_FONT_SOURCE_PATH,
        source_font_sha256=module._X200_GRUB_FONT_SOURCE_SHA256,
        source_package=module._X200_GRUB_FONT_PACKAGE,
        source_package_version=module._X200_GRUB_FONT_PACKAGE_VERSION,
        staged_font_relative_path=module._X200_GRUB_FONT_STAGED_RELATIVE_PATH,
        staged_font_sha256=module._X200_GRUB_FONT_SOURCE_SHA256,
        overlay_resource_path=module._X200_GRUB_FONT_OVERLAY_PATH,
        overlay_resource_sha256=module._X200_GRUB_FONT_OVERLAY_SHA256,
        lbmk_tree_sh_sha256=module._X200_LBMK_TREE_SH_SHA256,
        lbmk_tree_sh_patched_sha256=module._X200_LBMK_TREE_SH_PATCHED_SHA256,
        grub_source_relative_path="work/lbmk/src/grub/default",
        grub_repository_url=module._X200_GRUB_SOURCE_URL,
        grub_cache_relative_path=module._X200_GRUB_CACHE_RELATIVE_PATH,
        grub_base_revision=module._X200_GRUB_BASE_REVISION,
        grub_base_tree_id=module._X200_GRUB_BASE_TREE,
        grub_patch_inputs=module._X200_GRUB_PATCH_INPUTS,
        grub_patch_commits=tuple(f"{index:040x}" for index in range(1, 11)),
        grub_patch_tree_ids=module._X200_GRUB_PATCH_TREE_IDS,
        grub_result_revision=f"{10:040x}",
        grub_result_tree_id=module._X200_GRUB_PATCHED_TREE,
        configure_log_relative_path=module._X200_GRUB_CONFIG_LOG_RELATIVE_PATH,
        configure_log_sha256="c" * 64,
        configure_argv=(
            "./configure",
            "--with-unifont=<operation-output-directory>/inputs/unifont.otf",
            *module._X200_GRUB_CONFIGURE_FIXED_ARGS,
        ),
        generated_payload_relative_path=module._X200_GRUB_PAYLOAD_RELATIVE_PATH,
        generated_payload_sha256="d" * 64,
    )


def _minimal_test_elf_shared_object() -> bytes:
    elf = bytearray(120)
    elf[:16] = b"\x7fELF\x02\x01\x01" + (b"\x00" * 9)
    elf[16:18] = (3).to_bytes(2, "little")
    elf[18:20] = (62).to_bytes(2, "little")
    elf[20:24] = (1).to_bytes(4, "little")
    elf[32:40] = (64).to_bytes(8, "little")
    elf[52:54] = (64).to_bytes(2, "little")
    elf[54:56] = (56).to_bytes(2, "little")
    elf[56:58] = (1).to_bytes(2, "little")
    elf[64:68] = (1).to_bytes(4, "little")
    elf[72:80] = (120).to_bytes(8, "little")
    elf[80:88] = (120).to_bytes(8, "little")
    return bytes(elf)


def _synthetic_nested_repository_evidence():
    """Construct schema-4 nested-source provenance for a synthetic X200 result."""
    import autoloader.rom_builder.artifact_engine as module

    elf = _minimal_test_elf_shared_object()

    coreboot = module.X200NestedRepositoryEvidence(
        role="coreboot",
        relative_path="work/lbmk/src/coreboot/default",
        repository_url=module._X200_COREBOOT_ORIGIN_URL,
        cache_origin_relative_path=f"work/lbmk/{module._X200_COREBOOT_CACHE_RELATIVE_PATH}",
        base_revision=module._LBMK_COREBOOT_REVISION,
        base_tree_id=module._LBMK_COREBOOT_TREE,
        resulting_revision="a" * 40,
        resulting_tree_id=module._X200_COREBOOT_POST_TREE,
        patch_inputs=tuple(
            (f"work/lbmk/config/coreboot/default/patches/{index:04}.patch", "b" * 64)
            for index in range(module._X200_COREBOOT_PATCH_COUNT)
        ),
        patch_revisions=tuple(
            f"{index:040x}" for index in range(1, module._X200_COREBOOT_PATCH_COUNT)
        ) + ("a" * 40,),
        patch_tree_ids=("c" * 40,) * (module._X200_COREBOOT_PATCH_COUNT - 1)
        + (module._X200_COREBOOT_POST_TREE,),
        source_file_hashes=tuple(
            (item.path, item.sha256) for item in module._LBMK_COREBOOT_CBFSTOOL_SOURCES
        ),
        generated_status=(
            " M 3rdparty/libgfxinit",
            " M 3rdparty/vboot",
            "?? 3rdparty/fspcc36ae2b5775fa7400cb3282680afc0f6cb37a3c/",
            "?? util/cbfstool/fmd_parser.h",
            "?? util/cbfstool/fmd_scanner.h",
            "?? util/vboot_lib/libvboot_host.so",
        ),
        generated_files=(
            module.X200GeneratedFileEvidence(
                relative_path="util/cbfstool/fmd_parser.h",
                size_bytes=3607,
                sha256="4e93908f1854f103e832c4c115330689b4bb623f2d94ecdb2dcd7d0d6c9f609f",
                kind="copied-shipped-header",
                source_relative_path="util/cbfstool/fmd_parser.h_shipped",
                source_sha256="4e93908f1854f103e832c4c115330689b4bb623f2d94ecdb2dcd7d0d6c9f609f",
            ),
            module.X200GeneratedFileEvidence(
                relative_path="util/cbfstool/fmd_scanner.h",
                size_bytes=10277,
                sha256="c5b826d911122e6513f1f5c2ad5d30b067caf275e0a949b544b6a853a081cb46",
                kind="copied-shipped-header",
                source_relative_path="util/cbfstool/fmd_scanner.h_shipped",
                source_sha256="c5b826d911122e6513f1f5c2ad5d30b067caf275e0a949b544b6a853a081cb46",
            ),
            module.X200GeneratedFileEvidence(
                relative_path="util/vboot_lib/libvboot_host.so",
                size_bytes=len(elf),
                sha256=hashlib.sha256(elf).hexdigest(),
                kind="elf-shared-object",
                elf_class="ELF64",
                elf_type="ET_DYN",
                elf_machine="EM_X86_64",
            ),
        ),
    )
    libgfxinit = module.X200NestedRepositoryEvidence(
        role="libgfxinit",
        relative_path="work/lbmk/src/coreboot/default/3rdparty/libgfxinit",
        repository_url=module._X200_LIBGFXINIT_ORIGIN_URL,
        cache_origin_relative_path=f"work/lbmk/{module._X200_LIBGFXINIT_CACHE_RELATIVE_PATH}",
        base_revision=module._X200_LIBGFXINIT_BASE_REVISION,
        base_tree_id=module._X200_LIBGFXINIT_BASE_TREE,
        resulting_revision="e" * 40,
        resulting_tree_id=module._X200_LIBGFXINIT_POST_TREE,
        patch_inputs=tuple(
            (f"work/lbmk/{path}", digest) for path, digest in module._X200_LIBGFXINIT_PATCH_INPUTS
        ),
        patch_revisions=("d" * 40, "e" * 40),
        patch_tree_ids=("1" * 40, module._X200_LIBGFXINIT_POST_TREE),
    )
    vboot = module.X200NestedRepositoryEvidence(
        role="vboot",
        relative_path=module._X200_VBOOT_SOURCE_RELATIVE_PATH,
        repository_url=module._X200_VBOOT_ORIGIN_URL,
        cache_origin_relative_path=f"work/lbmk/{module._X200_VBOOT_CACHE_RELATIVE_PATH}",
        base_revision=module._X200_VBOOT_BASE_REVISION,
        base_tree_id=module._X200_VBOOT_BASE_TREE,
        resulting_revision="f" * 40,
        resulting_tree_id=module._X200_VBOOT_POST_OVERLAY_TREE,
        patch_inputs=tuple(
            (f"work/lbmk/{path}", digest) for path, digest in module._X200_VBOOT_PATCH_STACK
        ),
        patch_revisions=("2" * 40, "f" * 40),
        patch_tree_ids=(module._X200_VBOOT_PRE_OVERLAY_TREE, module._X200_VBOOT_POST_OVERLAY_TREE),
        source_file_hashes=((module._X200_VBOOT_SOURCE_FILE, module._X200_VBOOT_POSTIMAGE_SHA256),),
        source_file_preimage_hashes=((module._X200_VBOOT_SOURCE_FILE, module._X200_VBOOT_PREIMAGE_SHA256),),
    )
    fsp = module.X200NestedRepositoryEvidence(
        role="fsp",
        relative_path=module._X200_FSP_RELATIVE_PATH,
        repository_url=module._X200_FSP_ORIGIN_URL,
        cache_origin_relative_path=f"work/lbmk/{module._X200_FSP_CACHE_RELATIVE_PATH}",
        base_revision=module._X200_FSP_REVISION,
        base_tree_id=module._X200_FSP_TREE,
        resulting_revision=module._X200_FSP_REVISION,
        resulting_tree_id=module._X200_FSP_TREE,
        patch_inputs=(),
        patch_revisions=(),
        patch_tree_ids=(),
    )
    return coreboot, libgfxinit, vboot, fsp


def _write_fake_generated_coreboot_tree(operation_dir: Path, evidence) -> None:
    coreboot = operation_dir.joinpath(*Path(evidence.relative_path).parts)
    (coreboot / "3rdparty").mkdir(parents=True)
    (coreboot / "3rdparty" / "libgfxinit").write_text("base libgfxinit gitlink placeholder\n", encoding="utf-8")
    (coreboot / "3rdparty" / "vboot").write_text("base vboot gitlink placeholder\n", encoding="utf-8")
    fixture_dir = Path(__file__).parent / "fixtures" / "x200_coreboot_generated"
    for item in evidence.generated_files:
        if item.kind == "copied-shipped-header":
            source = coreboot.joinpath(*Path(item.source_relative_path).parts)
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes((fixture_dir / Path(item.relative_path).name).read_bytes())
    git = ["git", "-C", str(coreboot)]
    subprocess.run([*git, "init", "--quiet"], check=True, capture_output=True, text=True)
    subprocess.run([*git, "config", "user.name", "X200 fixture"], check=True)
    subprocess.run([*git, "config", "user.email", "x200-fixture@example.invalid"], check=True)
    excludes = coreboot / ".git" / "empty-excludes"
    excludes.write_text("", encoding="utf-8")
    subprocess.run([*git, "config", "core.excludesFile", str(excludes)], check=True)
    subprocess.run([*git, "config", "core.autocrlf", "false"], check=True)
    subprocess.run([*git, "config", "commit.gpgsign", "false"], check=True)
    subprocess.run([*git, "add", "3rdparty/libgfxinit", "3rdparty/vboot", "util/cbfstool/*_shipped"], check=True)
    subprocess.run([*git, "commit", "--quiet", "-m", "synthetic status baseline"], check=True)
    (coreboot / "3rdparty" / "libgfxinit").write_text("patched gitlink placeholder\n", encoding="utf-8")
    (coreboot / "3rdparty" / "vboot").write_text("patched gitlink placeholder\n", encoding="utf-8")
    fsp = coreboot / "3rdparty" / "fspcc36ae2b5775fa7400cb3282680afc0f6cb37a3c"
    fsp.mkdir()
    subprocess.run(["git", "-C", str(fsp), "init", "--quiet"], check=True)
    for item in evidence.generated_files:
        output = coreboot.joinpath(*Path(item.relative_path).parts)
        output.parent.mkdir(parents=True, exist_ok=True)
        if item.kind == "copied-shipped-header":
            source = coreboot.joinpath(*Path(item.source_relative_path).parts)
            source.parent.mkdir(parents=True, exist_ok=True)
            source_bytes = (fixture_dir / Path(item.relative_path).name).read_bytes()
            source.write_bytes(source_bytes)
            output.write_bytes(source_bytes)
        else:
            output.write_bytes(_minimal_test_elf_shared_object())
            output.chmod(0o755)


def _fake_complete_result(
    tmp_path: Path,
    intent,
    *,
    artifact_bytes: bytes | None = None,
    prebuild_digest: str | None = None,
    operation_id: str | None = None,
    created_at_utc: str | None = None,
    cbfs_inventory: tuple[CBFSEntry, ...] | None = None,
) -> ArtifactBuildResult:
    recipe = X200_4MB if intent.expected_size_bytes == 4 * MIB else X200_8MB
    operation_id = operation_id or str(uuid.uuid4())
    run_dir = tmp_path / operation_id
    artifact_dir = run_dir / "artifacts"
    logs_dir = run_dir / "logs"
    artifact_dir.mkdir(parents=True)
    logs_dir.mkdir()
    _, git_config_sha256 = _ensure_operation_git_config(run_dir)
    selected_relative = f"artifacts/{Path(recipe.output_relative_path).name}"
    selected_path = run_dir / selected_relative
    content = artifact_bytes if artifact_bytes is not None else _payload(recipe.expected_size_bytes, 91)
    selected_path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()

    nested_evidence = _synthetic_nested_repository_evidence()
    coreboot_evidence = nested_evidence[0]
    _write_fake_generated_coreboot_tree(run_dir, coreboot_evidence)
    cbfs = CBFSInspectionRecord(
        tool_relative_path="tools/cbfstool",
        tool_sha256="a" * 64,
        source_repository_url=coreboot_evidence.repository_url,
        source_revision=coreboot_evidence.resulting_revision,
        source_tree_id=coreboot_evidence.resulting_tree_id,
        region="whole_flash",
        required_payload="fallback/payload",
        inventory=(
            cbfs_inventory
            if cbfs_inventory is not None
            else (CBFSEntry("fallback/payload", 0, "payload", 0, 4096, 4096),)
        ),
        source_file_hashes=tuple((item.path, item.sha256) for item in _LBMK_COREBOOT_CBFSTOOL_SOURCES),
        nested_repositories=nested_evidence,
    )
    records = []
    for source_path in derive_lbmk_output_inventory(recipe.target):
        is_selected = source_path == recipe.output_relative_path
        records.append(ArtifactRecord(
            relative_path=selected_relative if is_selected else None,
            sha256=digest if is_selected else hashlib.sha256(source_path.encode()).hexdigest(),
            size_bytes=recipe.expected_size_bytes,
            regions=(),
            source_relative_path=source_path,
            cbfs_inspection=cbfs if is_selected else None,
        ))

    command_log = b"fake pinned build command log; no upstream build executed\n"
    log_path = logs_dir / "01-build.log"
    log_path.write_bytes(command_log)
    fetch_log = logs_dir / "00-fetch-uboot.log"
    fetch_log.write_bytes(b"Synthetic pinned U-Boot fetch/patch fixture; no upstream command executed.\n")
    fetch_command = CommandRecord(
        argv=("./mk", "-f", "u-boot", "x86_64"),
        exit_code=0,
        log_relative_path="logs/00-fetch-uboot.log",
        log_sha256=hashlib.sha256(fetch_log.read_bytes()).hexdigest(),
    )
    command = CommandRecord(
        argv=recipe.build_command,
        exit_code=0,
        log_relative_path="logs/01-build.log",
        log_sha256=hashlib.sha256(command_log).hexdigest(),
    )
    manifest = ArtifactManifest(
        schema_version=4,
        operation_id=operation_id,
        created_at_utc=created_at_utc or (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
        recipe_id=recipe.recipe_id,
        workflow_id="x200-libreboot",
        status="complete",
        source_repository_url=recipe.source.repository_url,
        source_revision=recipe.source.revision,
        source_tree_id=recipe.source.tree_id,
        source_files=tuple((item.path, item.sha256) for item in recipe.source.files),
        commands=(fetch_command, command),
        host_platform="fake test fixture; no build executed",
        toolchain=(("make", "fake fixture"),),
        build_environment=_build_environment_record(git_config_sha256),
        container_image_digest=None,
        configuration_sha256=recipe.expected_config_sha256,
        resolved_configuration_sha256="b" * 64,
        observed_chip_capacity_bytes=recipe.expected_size_bytes,
        expected_regions=(),
        blob_sha256=(),
        artifacts=tuple(records),
        selected_artifact_relative_path=selected_relative,
        selected_artifact_sha256=digest,
        failure_code=None,
        failure_detail=None,
        reproducibility=ReproducibilityRecord(
            "not_attempted", digest, None, "Synthetic fixture only; no compilation occurred."
        ),
        prebuild_intent_sha256=prebuild_digest or build_intent_sha256(intent),
        x200_uboot_dependency=_synthetic_uboot_dependency(tmp_path),
        x200_grub_font=_synthetic_grub_font_evidence(),
    )
    manifest_path = run_dir / "manifest.json"
    raw_manifest = (json.dumps(asdict(manifest), sort_keys=True, indent=2) + "\n").encode()
    manifest_path.write_bytes(raw_manifest)
    manifest_path.with_name("manifest.sha256").write_text(
        f"{hashlib.sha256(raw_manifest).hexdigest()}  manifest.json\n", encoding="ascii"
    )
    return ArtifactBuildResult(
        BuildStatus.COMPLETE,
        "ARTIFACT_VERIFIED",
        "Synthetic test evidence only.",
        recipe.recipe_id,
        (selected_path,),
        manifest,
        manifest_path,
    )


def test_x200_workflow_stages_only_a_hardware_pending_plan(captured_backup, tmp_path):
    intent, backup = captured_backup
    workflow = _workflow(intent, backup, tmp_path / "operation.json")
    result = workflow.accept_artifact_result(_fake_complete_result(tmp_path, intent))

    if not hasattr(result, "stage"):
        pytest.fail(f"workflow blocked unexpectedly: {workflow.result.reasons}")
    assert result.stage is X200WorkflowStage.STAGED, result.message
    assert result.status == "STAGED/HARDWARE_PENDING"
    assert result.operation_plan is not None
    assert result.operation_plan.artifact.expected_size_bytes == 4 * MIB
    proposed = result.proposed_external_spi_plan
    assert isinstance(proposed, X200ExternalSPIPlan)
    assert proposed.scope == "whole-chip"
    assert proposed.capacity_bytes == 4 * MIB
    assert proposed.voltage_mv == 3300
    assert Path(proposed.artifact_manifest_path).is_file()
    assert hashlib.sha256(Path(proposed.artifact_manifest_path).read_bytes()).hexdigest() == proposed.artifact_manifest_sha256
    assert proposed.flash_write_enabled is False
    assert proposed.restore_write_enabled is False
    assert proposed.readback_status is VerificationStatus.NOT_PERFORMED
    assert proposed.boot_status is VerificationStatus.NOT_PERFORMED
    assert result.recovery_readiness is not None
    assert result.recovery_readiness.status == "ready"
    assert result.recovery_readiness.recovery_write_enabled is False
    assert result.recovery_readiness.hardware_recovery_tested is False
    assert "No firmware write" in result.message
    pair = backup.backup.pairs[0]
    assert pair.first_capture_id and pair.second_capture_id and pair.first_capture_id != pair.second_capture_id
    assert backup.primary_storage.usable and backup.recovery_storage.usable
    assert backup.primary_storage.volume_id != backup.recovery_storage.volume_id
    assert proposed.operation_id
    assert Path(proposed.operation_journal_path).is_file()
    from autoloader.workflow.engine import WorkflowSession
    from autoloader.workflow.types import OperationState

    resumed = WorkflowSession.resume(result.operation_plan, proposed.operation_journal_path)
    assert resumed.state is OperationState.BACKUP_VERIFIED


def test_x200_workflow_rejects_generated_header_mutation_before_acceptance(captured_backup, tmp_path) -> None:
    intent, backup = captured_backup
    workflow = _workflow(intent, backup, tmp_path / "operation.json")
    artifact_result = _fake_complete_result(tmp_path, intent)
    assert artifact_result.manifest_path is not None
    generated_header = (
        artifact_result.manifest_path.parent
        / "work/lbmk/src/coreboot/default/util/cbfstool/fmd_parser.h"
    )
    generated_header.write_bytes(generated_header.read_bytes() + b"\npost-build change\n")
    with pytest.raises(ValueError, match="header differs|generated-file bytes differ"):
        workflow._validate_artifact_result(artifact_result)


def test_x200_manifest_reopen_rehashes_generated_inventory(monkeypatch, tmp_path) -> None:
    import autoloader.rom_builder.artifact_engine as module

    result = _fake_complete_result(tmp_path, _regular_x200_intent())
    assert result.manifest_path is not None
    engine = module.ArtifactEngine()
    monkeypatch.setattr(engine, "_validate_x200_grub_font_evidence", lambda *_args: None)
    reopened = engine.load_completed_result(result.manifest_path)
    assert reopened.status is BuildStatus.COMPLETE
    library = result.manifest_path.parent / "work/lbmk/src/coreboot/default/util/vboot_lib/libvboot_host.so"
    library.write_bytes(library.read_bytes() + b"changed after persisted result")
    with pytest.raises(ValueError, match="generated-file bytes differ"):
        engine.load_completed_result(result.manifest_path)


@pytest.mark.parametrize("mutation", ["missing-inventory", "tampered-inventory"])
def test_x200_manifest_reopen_rejects_missing_or_tampered_generated_inventory(
    monkeypatch, tmp_path, mutation: str
) -> None:
    import json
    import autoloader.rom_builder.artifact_engine as module

    result = _fake_complete_result(tmp_path, _regular_x200_intent())
    assert result.manifest_path is not None
    engine = module.ArtifactEngine()
    monkeypatch.setattr(engine, "_validate_x200_grub_font_evidence", lambda *_args: None)
    document = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    coreboot = document["artifacts"][0]["cbfs_inspection"]["nested_repositories"][0]
    if mutation == "missing-inventory":
        coreboot.pop("generated_files")
    else:
        coreboot["generated_files"][0]["sha256"] = "e" * 64
    payload = (json.dumps(document, sort_keys=True, indent=2) + "\n").encode("utf-8")
    result.manifest_path.write_bytes(payload)
    result.manifest_path.with_name("manifest.sha256").write_text(
        f"{hashlib.sha256(payload).hexdigest()}  manifest.json\n", encoding="ascii"
    )
    with pytest.raises(ValueError, match="generated-file inventory|shipped source"):
        engine.load_completed_result(result.manifest_path)


def test_x200_manifest_reopens_exact_typed_uboot_provenance_and_rejects_mutation(tmp_path):
    import autoloader.rom_builder.artifact_engine as module

    result = _fake_complete_result(tmp_path, _regular_x200_intent())
    assert result.manifest is not None
    dependency = result.manifest.x200_uboot_dependency
    assert dependency is not None
    restored = module._artifact_manifest_from_mapping(json.loads(result.manifest.canonical_json()))
    assert restored.x200_uboot_dependency == dependency
    with pytest.raises(ValueError, match="requires pinned patched U-Boot"):
        replace(result.manifest, x200_uboot_dependency=None)
    with pytest.raises(ValueError, match="differs from the pinned source and overlay"):
        replace(dependency, overlay_sha256="0" * 64)
    with pytest.raises(ValueError, match="commit chain or clean-tree evidence"):
        replace(dependency, resulting_tree_id="9" * 40)

    document = json.loads(result.manifest.canonical_json())
    document["x200_uboot_dependency"]["patched_config_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="differs from the pinned source and overlay"):
        module._artifact_manifest_from_mapping(document)


def test_x200_durable_stage_revalidates_exact_plan_and_rejects_changed_resume(captured_backup, tmp_path):
    intent, backup = captured_backup
    journal_path = tmp_path / "operation.json"
    artifact_result = _fake_complete_result(tmp_path / "artifact-a", intent)

    first = _workflow(intent, backup, journal_path)
    first_staged = first.accept_artifact_result(artifact_result)
    assert first_staged.stage is X200WorkflowStage.STAGED

    resumed = _workflow(intent, backup, journal_path)
    resumed_staged = resumed.accept_artifact_result(artifact_result)
    assert resumed_staged.stage is X200WorkflowStage.STAGED
    assert resumed_staged.operation_id == first_staged.operation_id

    changed = _workflow(intent, backup, journal_path)
    changed_result = _fake_complete_result(
        tmp_path / "artifact-b",
        intent,
        artifact_bytes=_payload(intent.expected_size_bytes, 17),
    )
    blocked = changed.accept_artifact_result(changed_result)
    assert blocked.stage is X200WorkflowStage.BLOCKED
    assert blocked.proposed_external_spi_plan is None


@pytest.mark.parametrize(
    "product_name,product_version,family,mtm",
    [
        ("ThinkPad X200s", "7458CTO", "ThinkPad X200", "7458CTO"),
        ("ThinkPad X200 Tablet", "7458CTO", "ThinkPad X200", "7458CTO"),
        ("ThinkPad X200si", "7458CTO", "ThinkPad X200", "7458CTO"),
        ("ThinkPad X200", "7460CTO", "ThinkPad X200", "7458CTO"),
    ],
)
def test_x200_workflow_blocks_alias_and_conflicting_identity(product_name, product_version, family, mtm):
    intent = create_x200_build_intent(
        device=DeviceEvidence(
            raw_dmi_product_name=product_name,
            raw_dmi_product_version=product_version,
            raw_dmi_system_family=family,
            raw_mtm=mtm,
        ),
        topology=_regular_x200_intent().topology,
        programmer=_regular_x200_intent().programmer,
    )
    result = CompatibilityValidator().validate_build_intent(intent)
    assert result.decision is CompatibilityDecision.BLOCKED


def test_x200_eight_mib_recipe_is_derived_only_from_observed_capacity():
    intent = _regular_x200_intent(8 * MIB)
    assert intent.recipe_id == X200_8MB.recipe_id
    assert CompatibilityValidator().validate_build_intent(intent).decision is CompatibilityDecision.ALLOWED
    with pytest.raises(ValueError, match="observed 4 MiB or 8 MiB"):
        create_x200_build_intent(
            device=intent.device,
            topology=replace(intent.topology, chips=(replace(intent.topology.chips[0], capacity_bytes=2 * MIB),)),
            programmer=intent.programmer,
        )


def test_x200_prebuild_policy_blocks_chip_voltage_and_topology_mismatches():
    base = _regular_x200_intent()
    validator = CompatibilityValidator()
    cases = (
        replace(base, topology=replace(base.topology, chips=(replace(base.topology.chips[0], capacity_bytes=8 * MIB),))),
        replace(base, topology=replace(base.topology, chips=(replace(base.topology.chips[0], voltage_mv=1800),))),
        replace(base, topology=replace(base.topology, chips=(replace(base.topology.chips[0], voltage_mv=None),))),
        replace(base, programmer=replace(base.programmer, voltage_mv=1800)),
        replace(base, topology=replace(base.topology, chips=(base.topology.chips[0], replace(base.topology.chips[0], chip_id="flash1")))),
        replace(base, topology=replace(base.topology, observed=False)),
    )
    decisions = tuple(validator.validate_build_intent(candidate).decision for candidate in cases)
    assert decisions == (
        CompatibilityDecision.BLOCKED,
        CompatibilityDecision.BLOCKED,
        CompatibilityDecision.UNKNOWN,
        CompatibilityDecision.BLOCKED,
        CompatibilityDecision.BLOCKED,
        CompatibilityDecision.UNKNOWN,
    )


@pytest.mark.parametrize(
    "failure",
    [
        "blocked-result",
        "changed-bytes",
        "missing-file",
        "intent-mismatch",
        "operation-mismatch",
        "stale-manifest",
        "manifest-checksum",
        "cbfs-null-type",
        "cbfs-other-type",
        "cbfs-zero-size",
        "cbfs-duplicate",
    ],
)
def test_x200_artifact_failures_never_reach_staged(captured_backup, tmp_path, failure):
    intent, backup = captured_backup
    bad_cbfs_inventories = {
        "cbfs-null-type": (CBFSEntry("fallback/payload", 0, None, 0, 4096, 4096),),
        "cbfs-other-type": (CBFSEntry("fallback/payload", 0, "raw", 0, 4096, 4096),),
        "cbfs-zero-size": (CBFSEntry("fallback/payload", 0, "payload", 0, 0, 0),),
        "cbfs-duplicate": (
            CBFSEntry("fallback/payload", 0, "payload", 0, 4096, 4096),
            CBFSEntry("fallback/payload", 4096, "payload", 0, 1024, 1024),
        ),
    }
    workflow = _workflow(intent, backup, tmp_path / "operation.json")
    result = _fake_complete_result(
        tmp_path,
        intent,
        prebuild_digest=("0" * 64 if failure == "intent-mismatch" else None),
        operation_id=(str(uuid.uuid4()) if failure == "operation-mismatch" else None),
        created_at_utc=("2020-01-01T00:00:00+00:00" if failure == "stale-manifest" else None),
        cbfs_inventory=bad_cbfs_inventories.get(failure),
    )
    if failure == "blocked-result":
        from autoloader.rom_builder.artifact_engine import ArtifactBuildResult

        result = ArtifactBuildResult(BuildStatus.BLOCKED, "BUILD_BLOCKED", "No Linux build", intent.recipe_id)
    elif failure == "changed-bytes":
        result.artifacts[0].write_bytes(_payload(intent.expected_size_bytes, 27))
    elif failure == "missing-file":
        result.artifacts[0].unlink()
    elif failure == "operation-mismatch":
        # The manifest operation identifier must identify its own operation directory.
        manifest = replace(result.manifest, operation_id=str(uuid.uuid4()))
        raw = (json.dumps(asdict(manifest), sort_keys=True, indent=2) + "\n").encode()
        result.manifest_path.write_bytes(raw)
        result.manifest_path.with_name("manifest.sha256").write_text(
            f"{hashlib.sha256(raw).hexdigest()}  manifest.json\n", encoding="ascii"
        )
        result = replace(result, manifest=manifest)
    elif failure == "manifest-checksum":
        result.manifest_path.with_name("manifest.sha256").write_text("0" * 64 + "  manifest.json\n", encoding="ascii")

    staged = workflow.accept_artifact_result(result)
    assert staged.stage is X200WorkflowStage.BLOCKED
    assert staged.proposed_external_spi_plan is None


def test_x200_backup_operation_and_recovery_bindings_are_revalidated(captured_backup, tmp_path):
    intent, backup = captured_backup
    mismatched_operation = replace(backup, operation_id=str(uuid.uuid4()))
    workflow = X200LibrebootWorkflow(intent)
    assert workflow.validate_build_intent().decision is CompatibilityDecision.ALLOWED
    with pytest.raises(SafetyError, match="current ready recovery"):
        workflow.accept_backup(mismatched_operation)
    assert workflow.stage is X200WorkflowStage.BLOCKED

    bad_storage = replace(backup, primary_storage=replace(backup.primary_storage, persistence_confirmed=False))
    blocked = X200LibrebootWorkflow(intent)
    assert blocked.validate_build_intent().decision is CompatibilityDecision.ALLOWED
    with pytest.raises(SafetyError, match="current ready recovery"):
        blocked.accept_backup(bad_storage)

    from autoloader.workflow.types import BackupEvidence

    incomplete = replace(backup, backup=BackupEvidence(), chips=())
    incomplete_workflow = X200LibrebootWorkflow(intent)
    assert incomplete_workflow.validate_build_intent().decision is CompatibilityDecision.ALLOWED
    with pytest.raises(SafetyError, match="current ready recovery"):
        incomplete_workflow.accept_backup(incomplete)

    original_manifest = Path(backup.primary_manifest).read_bytes()
    try:
        Path(backup.primary_manifest).write_text('{"schema_version":1,"events":[', encoding="utf-8")
        corrupt_workflow = X200LibrebootWorkflow(intent)
        assert corrupt_workflow.validate_build_intent().decision is CompatibilityDecision.ALLOWED
        with pytest.raises(SafetyError, match="current ready recovery"):
            corrupt_workflow.accept_backup(backup)
    finally:
        Path(backup.primary_manifest).write_bytes(original_manifest)

    workflow = _workflow(intent, backup, tmp_path / "operation.json")
    original = Path(backup.chips[0].first_path).read_bytes()
    try:
        Path(backup.chips[0].first_path).write_bytes(_payload(intent.expected_size_bytes, 77))
        staged = workflow.accept_artifact_result(_fake_complete_result(tmp_path, intent))
        assert staged.stage is X200WorkflowStage.BLOCKED
        assert staged.proposed_external_spi_plan is None
    finally:
        Path(backup.chips[0].first_path).write_bytes(original)
    assert RecoveryReadinessService().evaluate(intent, backup).status == "ready"


def test_x200_writers_and_process_paths_remain_unreachable(captured_backup, tmp_path, monkeypatch):
    import autoloader.flasher.flashrom as flashrom_module
    import autoloader.rom_builder.artifact_engine as artifact_module

    intent, backup = captured_backup
    artifact_result = _fake_complete_result(tmp_path, intent)
    workflow = _workflow(intent, backup, tmp_path / "operation.json")

    def forbidden(*args, **kwargs):
        pytest.fail("Phase 06 must not spawn a process or perform a writer call")

    monkeypatch.setattr(flashrom_module.subprocess, "run", forbidden)
    monkeypatch.setattr(artifact_module.subprocess, "run", forbidden)
    staged = workflow.accept_artifact_result(artifact_result)
    assert staged.stage is X200WorkflowStage.STAGED
    for _ in range(2):
        with pytest.raises(SafetyError, match="physical writes are disabled"):
            workflow.request_flash(object(), extra_args=("-w", "ignored"))
    with pytest.raises(SafetyError, match="restore writes are disabled"):
        workflow.request_restore(object(), extra_args=("-w", "ignored"))
    assert workflow.proposed_external_spi_plan is not None
    with pytest.raises(ValueError, match="cannot claim hardware authorization"):
        replace(workflow.proposed_external_spi_plan, flash_write_enabled=True)


def test_x200_build_fails_cleanly_without_supported_linux_tools(captured_backup, tmp_path, monkeypatch):
    import autoloader.rom_builder.artifact_engine as artifact_module

    monkeypatch.setattr(artifact_module.platform, "system", lambda: "Windows")

    def forbidden(*args, **kwargs):
        pytest.fail("unsupported host must not spawn a build command")

    monkeypatch.setattr(artifact_module.subprocess, "run", forbidden)
    intent, backup = captured_backup
    workflow = _workflow(intent, backup, tmp_path / "operation.json")
    result = workflow.build_and_stage(
        source_dir=tmp_path / "no-upstream-checkout",
        output_root=tmp_path / "no-build-output",
    )
    assert result.stage is X200WorkflowStage.BLOCKED
    assert result.proposed_external_spi_plan is None
    assert "LINUX_BUILD_ENV_REQUIRED" in result.reasons[0]
    assert not (tmp_path / "no-build-output").exists()
