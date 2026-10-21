"""Standalone, fail-closed X220 Coreboot staging workflow.

Phase 07 binds observed X220 identity/topology, a complete original SPI backup,
the pinned Coreboot artifact contract, and a durable operation journal. It only
proposes a hardware-pending whole-chip plan; it contains no writer path.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Sequence

from autoloader.exceptions import SafetyError
from autoloader.flasher.backup_service import BackupSetResult, BackupSetService
from autoloader.flasher.flashrom import ProgrammerType
from autoloader.flasher.recovery import RecoveryReadinessRecord, RecoveryReadinessService
from autoloader.flasher.spi import ChipProbeEvidence
from autoloader.rom_builder.artifact_engine import (
    COREBOOT_2609,
    X220_COREBOOT,
    ArtifactBuildResult,
    ArtifactEngine,
    ArtifactRecord,
    BackupProvenanceRecord,
    BuildRequest,
    BuildStatus,
    BlobInput,
    compare_region_maps,
    parse_cbfs_parseable_output,
    parse_ifdtool_regions,
    sha256_file,
    validate_x220_backup_evidence,
    validate_x220_region_preservation,
)
from autoloader.workflow.compatibility import CompatibilityValidator
from autoloader.workflow.engine import WorkflowSession, WorkflowTransitionError
from autoloader.workflow.journal import JournalError
from autoloader.workflow.profiles import MIB, V1_PROFILES
from autoloader.workflow.types import (
    ArtifactEvidence,
    BuildIntentEvidence,
    ChipTopology,
    CompatibilityDecision,
    CompatibilityResult,
    DeviceEvidence,
    EvidenceConfidence,
    FirmwareFamily,
    OperationPlan,
    OperationState,
    ProgrammerEvidence,
    ProgrammerKind,
    RegionEvidence,
    VerificationStatus,
    build_intent_sha256,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_X220_OPERATION_BUILD_SOURCE = PurePosixPath("work/coreboot-x220")
_REGION_ROWS = (
    "Flash Region 0 (Flash Descriptor): 00000000 - 00000fff",
    "Flash Region 1 (GbE): 00001000 - 00002fff",
    "Flash Region 2 (Intel ME): 00003000 - 004fffff",
    "Flash Region 3 (BIOS): 00500000 - 007fffff",
)


class X220WorkflowStage(str, Enum):
    DRAFT = "draft"
    BUILD_INTENT_VALIDATED = "build-intent-validated"
    RECOVERY_READY = "recovery-ready"
    STAGED = "staged"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class X220ExternalSPIPlan:
    operation_id: str
    profile_id: str
    chip_id: str
    chip_name: str
    capacity_bytes: int
    voltage_mv: int
    programmer_kind: ProgrammerKind
    artifact_path: str
    artifact_sha256: str
    artifact_manifest_path: str
    artifact_manifest_sha256: str
    backup_operation_id: str
    recovery_record_id: str
    operation_journal_path: str
    scope: str = "whole-chip"
    status: str = "STAGED/HARDWARE_PENDING"
    human_hardware_authorization_required: bool = True
    flash_write_enabled: bool = False
    restore_write_enabled: bool = False
    readback_status: VerificationStatus = VerificationStatus.NOT_PERFORMED
    boot_status: VerificationStatus = VerificationStatus.NOT_PERFORMED

    def __post_init__(self) -> None:
        if self.profile_id != "x220-coreboot" or self.scope != "whole-chip":
            raise ValueError("X220 V1 exposes only a whole-chip external SPI plan")
        for name, value in (("operation", self.operation_id), ("backup", self.backup_operation_id), ("recovery", self.recovery_record_id)):
            try:
                if str(uuid.UUID(value)) != value:
                    raise ValueError("noncanonical UUID")
            except (ValueError, AttributeError) as exc:
                raise ValueError(f"X220 {name} identifier must be a canonical UUID") from exc
        if (
            not self.chip_id.strip()
            or not self.chip_name.strip()
            or self.capacity_bytes != 8 * MIB
            or self.voltage_mv != 3300
            or self.programmer_kind is not ProgrammerKind.EXTERNAL_SPI
            or not _SHA256.fullmatch(self.artifact_sha256)
            or not Path(self.artifact_path).is_absolute()
            or not Path(self.artifact_manifest_path).is_absolute()
            or not _SHA256.fullmatch(self.artifact_manifest_sha256)
            or not Path(self.operation_journal_path).is_absolute()
            or self.status != "STAGED/HARDWARE_PENDING"
            or not self.human_hardware_authorization_required
            or self.flash_write_enabled
            or self.restore_write_enabled
            or self.readback_status is not VerificationStatus.NOT_PERFORMED
            or self.boot_status is not VerificationStatus.NOT_PERFORMED
        ):
            raise ValueError("X220 plan cannot claim hardware authorization, write, read-back, or boot success")


@dataclass(frozen=True)
class X220WorkflowResult:
    operation_id: str
    stage: X220WorkflowStage
    status: str
    message: str
    reasons: tuple[str, ...] = ()
    compatibility: CompatibilityResult | None = None
    recovery_readiness: RecoveryReadinessRecord | None = None
    operation_plan: OperationPlan | None = None
    proposed_external_spi_plan: X220ExternalSPIPlan | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "reasons", tuple(self.reasons))
        if self.stage is X220WorkflowStage.STAGED:
            if self.status != "STAGED/HARDWARE_PENDING" or self.proposed_external_spi_plan is None:
                raise ValueError("A staged X220 result must remain hardware-pending and include its plan")
        elif self.status != self.stage.value.upper() or self.operation_plan is not None or self.proposed_external_spi_plan is not None:
            raise ValueError("A non-staged X220 result cannot expose a final operation plan")


def create_x220_build_intent(
    *, device: DeviceEvidence, topology: ChipTopology, programmer: ProgrammerEvidence
) -> BuildIntentEvidence:
    """Create intent for the pinned X220 recipe; observed region data is required."""
    if not topology.observed or len(topology.chips) != 1 or not topology.chips[0].observed:
        raise ValueError("X220 intent requires exactly one independently observed SPI chip")
    chip = topology.chips[0]
    if chip.capacity_bytes != 8 * MIB or chip.voltage_mv != 3300:
        raise ValueError("X220 requires one observed 8 MiB chip with verified 3300 mV voltage")
    if (
        programmer.kind is not ProgrammerKind.EXTERNAL_SPI
        or not programmer.observed
        or programmer.interface_verified is not True
        or programmer.wiring_verified is not True
        or programmer.voltage_mv != chip.voltage_mv
    ):
        raise ValueError("X220 requires observed external SPI programmer, verified wiring, and matching 3300 mV output")
    profile = V1_PROFILES["x220-coreboot"]
    intent = BuildIntentEvidence(
        profile_id=profile.profile_id,
        requested_model_id=profile.model_id,
        firmware_family=profile.firmware_family,
        device=device,
        topology=topology,
        programmer=programmer,
        recipe_id=X220_COREBOOT.recipe_id,
        source_url=COREBOOT_2609.repository_url,
        source_revision=COREBOOT_2609.revision,
        source_tree_id=COREBOOT_2609.tree_id,
        configuration_sha256=X220_COREBOOT.expected_config_sha256,
        expected_size_bytes=X220_COREBOOT.expected_size_bytes,
    )
    # The validator enforces complete non-overlapping map semantics and exact 3 MiB BIOS.
    from autoloader.rom_builder.artifact_engine import _validate_x220_region_map

    _validate_x220_region_map(topology.regions, chip.capacity_bytes)
    return intent


class X220CorebootWorkflow:
    """Standalone X220 flow that can reach only STAGED/HARDWARE_PENDING."""

    def __init__(
        self,
        intent: BuildIntentEvidence,
        *,
        validator: CompatibilityValidator | None = None,
        artifact_engine: ArtifactEngine | None = None,
        recovery_service: RecoveryReadinessService | None = None,
        journal_path: str | Path | None = None,
    ) -> None:
        self.intent = intent
        self.validator = validator or CompatibilityValidator()
        self.artifact_engine = artifact_engine or ArtifactEngine()
        self.recovery_service = recovery_service or RecoveryReadinessService(self.validator)
        self.journal_path = Path(journal_path) if journal_path is not None else None
        self.operation_id = str(uuid.uuid4())
        self.stage = X220WorkflowStage.DRAFT
        self.reasons: tuple[str, ...] = ()
        self.compatibility: CompatibilityResult | None = None
        self.backup_set: BackupSetResult | None = None
        self.recovery_readiness: RecoveryReadinessRecord | None = None
        self.operation_plan: OperationPlan | None = None
        self.proposed_external_spi_plan: X220ExternalSPIPlan | None = None
        self.operation_journal_path: str | None = None
        self._original_backup: bytes | None = None
        self._backup_provenance: BackupProvenanceRecord | None = None
        self._blob_directory: tempfile.TemporaryDirectory[str] | None = None
        self._blob_inputs: tuple[BlobInput, ...] = ()
        self._cpu_count = 1

    @property
    def result(self) -> X220WorkflowResult:
        status = "STAGED/HARDWARE_PENDING" if self.stage is X220WorkflowStage.STAGED else self.stage.value.upper()
        message = (
            "Pinned X220 artifact, original backup, and recovery evidence are bound to a proposed whole-chip SPI plan. "
            "No firmware write, read-back, or boot test was performed."
            if self.stage is X220WorkflowStage.STAGED
            else "; ".join(self.reasons) if self.reasons else "Workflow evidence is not yet ready."
        )
        return X220WorkflowResult(
            self.operation_id, self.stage, status, message, self.reasons, self.compatibility,
            self.recovery_readiness, self.operation_plan, self.proposed_external_spi_plan,
        )

    @property
    def validated_blob_inputs(self) -> tuple[BlobInput, ...]:
        """Expose only fresh backup-derived blobs for the shared app builder."""
        if self.stage is not X220WorkflowStage.RECOVERY_READY or self.backup_set is None:
            raise SafetyError("X220 build inputs require a current verified backup and recovery readiness")
        self._revalidate_backup()
        if {item.name for item in self._blob_inputs} != {"descriptor.bin", "me.bin", "gbe.bin"}:
            raise SafetyError("X220 build inputs are incomplete or not derived from the observed IFD map")
        return self._blob_inputs

    def validate_build_intent(self) -> CompatibilityResult:
        if self.stage is not X220WorkflowStage.DRAFT:
            raise SafetyError("X220 intent can only be validated once from DRAFT")
        if not self._exact_intent():
            return self._block("Build intent differs from the pinned X220 Coreboot recipe")
        result = self.validator.validate_build_intent(self.intent)
        self.compatibility = result
        if result.decision is not CompatibilityDecision.ALLOWED:
            self.stage, self.reasons = X220WorkflowStage.BLOCKED, result.reasons
            return result
        self.stage, self.reasons = X220WorkflowStage.BUILD_INTENT_VALIDATED, ()
        return result

    def accept_backup(self, backup: BackupSetResult) -> RecoveryReadinessRecord:
        if self.stage is not X220WorkflowStage.BUILD_INTENT_VALIDATED:
            raise SafetyError("X220 backup acceptance requires a validated pre-build intent")
        try:
            if not backup.operation_id or backup.profile_id != self.intent.profile_id:
                raise ValueError("Backup operation belongs to another workflow")
            bound_intent = replace(self.intent, backup=backup.backup)
            # Inspect path ancestry before RecoveryReadinessService opens or
            # hashes any persisted backup artifacts.
            self.intent = bound_intent
            self.backup_set = backup
            self._derive_and_validate_backup_inputs()
            readiness = self.recovery_service.evaluate(bound_intent, backup)
            if readiness.status != "ready" or readiness.operation_id != backup.operation_id:
                raise ValueError("X220 recovery readiness is not current: " + "; ".join(readiness.reasons))
            self.recovery_readiness = readiness
            self.stage, self.reasons = X220WorkflowStage.RECOVERY_READY, ()
            return readiness
        except (OSError, ValueError, TypeError) as exc:
            self._block(str(exc))
            raise SafetyError("X220 staging requires a verified complete original backup: " + str(exc)) from exc

    def capture_backup(
        self,
        service: BackupSetService,
        probes: Sequence[ChipProbeEvidence],
        *,
        programmer: ProgrammerType,
        primary_directory: str | Path,
        recovery_directory: str | Path,
        primary_persistent_confirmed: bool,
        recovery_persistent_confirmed: bool,
        programmer_evidence: ProgrammerEvidence | None = None,
    ) -> RecoveryReadinessRecord:
        if self.stage is not X220WorkflowStage.BUILD_INTENT_VALIDATED:
            raise SafetyError("X220 backup capture requires validated device, topology, voltage and region evidence")
        backup = service.capture(
            self.intent, probes, programmer=programmer, programmer_evidence=programmer_evidence,
            primary_directory=primary_directory, recovery_directory=recovery_directory,
            primary_persistent_confirmed=primary_persistent_confirmed,
            recovery_persistent_confirmed=recovery_persistent_confirmed,
        )
        return self.accept_backup(backup)

    def build_artifact(
        self, *, source_dir: str | Path, output_root: str | Path, cpu_count: int = 1
    ) -> ArtifactBuildResult:
        """Build with freshly revalidated backup-derived descriptor/ME/GbE inputs."""
        if self.stage is not X220WorkflowStage.RECOVERY_READY or self.backup_set is None:
            raise SafetyError("X220 build requires current full-chip backup and recovery readiness")
        blob_inputs = self.validated_blob_inputs
        self._cpu_count = cpu_count
        return self.artifact_engine.build(
            BuildRequest(
                recipe_id=X220_COREBOOT.recipe_id,
                source_dir=Path(source_dir),
                output_root=Path(output_root),
                blob_inputs=blob_inputs,
                expected_regions=self.intent.topology.regions,
                observed_chip_capacity_bytes=8 * MIB,
                backup_intent=self.intent,
                backup_set=self.backup_set,
                cpu_count=cpu_count,
            )
        )

    def build_and_stage(
        self, *, source_dir: str | Path, output_root: str | Path, cpu_count: int = 1
    ) -> X220WorkflowResult:
        return self.accept_artifact_result(
            self.build_artifact(source_dir=source_dir, output_root=output_root, cpu_count=cpu_count)
        )

    def accept_artifact_result(self, result: ArtifactBuildResult) -> X220WorkflowResult:
        if self.stage is not X220WorkflowStage.RECOVERY_READY:
            raise SafetyError("X220 artifact acceptance requires current backup/recovery evidence")
        try:
            self._revalidate_backup()
            staged = self._validate_artifact_result(result)
            assert self.backup_set is not None and self.recovery_readiness is not None
            assert result.manifest is not None and result.manifest_path is not None
            plan = result.manifest.finalized_operation_plan
            assert plan is not None
            compatibility = self.validator.validate(plan)
            backup_validation = self.validator.validate_backup(plan)
            if compatibility.decision is not CompatibilityDecision.ALLOWED:
                raise ValueError("Final X220 operation plan is incompatible: " + "; ".join(compatibility.reasons))
            if backup_validation.decision is not CompatibilityDecision.ALLOWED:
                raise ValueError("Bound X220 original backup no longer verifies: " + "; ".join(backup_validation.reasons))
            # Recovery manifests are bound to the pre-build intent. The final
            # plan adds the generated artifact digest, so evaluating those
            # manifests against it would incorrectly make backup evidence
            # depend on an artifact that did not exist at capture time.
            # Final-plan backup binding is checked independently above.
            readiness = self.recovery_service.evaluate(self.intent, self.backup_set)
            if readiness.status != "ready" or readiness.operation_id != self.backup_set.operation_id:
                raise ValueError("X220 recovery readiness changed: " + "; ".join(readiness.reasons))
            session = self._open_journal(plan)
            self.operation_id = session.journal.operation_id
            self.operation_journal_path = str(session.journal.path)
            self.operation_plan = plan
            self.compatibility = compatibility
            self.recovery_readiness = readiness
            chip = self.intent.topology.chips[0]
            self.proposed_external_spi_plan = X220ExternalSPIPlan(
                operation_id=self.operation_id,
                profile_id=self.intent.profile_id,
                chip_id=chip.chip_id,
                chip_name=self.backup_set.chips[0].observed_chip_name,
                capacity_bytes=chip.capacity_bytes,
                voltage_mv=chip.voltage_mv or 0,
                programmer_kind=self.intent.programmer.kind,
                artifact_path=str(staged.resolve(strict=True)),
                artifact_sha256=sha256_file(staged),
                artifact_manifest_path=str(result.manifest_path.resolve(strict=True)),
                artifact_manifest_sha256=sha256_file(result.manifest_path),
                backup_operation_id=self.backup_set.operation_id,
                recovery_record_id=readiness.record_id,
                operation_journal_path=self.operation_journal_path,
            )
            self.stage, self.reasons = X220WorkflowStage.STAGED, ()
            return self.result
        except (OSError, ValueError, TypeError, KeyError, JournalError, WorkflowTransitionError) as exc:
            self._block(str(exc))
            return self.result

    def resume_from_checkpoint(self, plan: OperationPlan | None = None) -> WorkflowSession:
        if self.stage is not X220WorkflowStage.STAGED or self.operation_journal_path is None or self.operation_plan is None:
            raise SafetyError("Only a staged X220 software checkpoint can be inspected/resumed")
        return WorkflowSession.resume(plan or self.operation_plan, self.operation_journal_path, validator=self.validator)

    def request_flash(self, *args, **kwargs) -> None:
        del args, kwargs
        raise SafetyError("X220 physical writes are disabled; the plan remains STAGED/HARDWARE_PENDING")

    def request_restore(self, *args, **kwargs) -> None:
        del args, kwargs
        raise SafetyError("X220 restore writes are disabled; recovery readiness is not restore authorization")

    def _exact_intent(self) -> bool:
        return (
            self.intent.profile_id == "x220-coreboot"
            and self.intent.requested_model_id == "x220"
            and self.intent.firmware_family is FirmwareFamily.COREBOOT
            and self.intent.recipe_id == X220_COREBOOT.recipe_id
            and self.intent.source_url == COREBOOT_2609.repository_url
            and self.intent.source_revision == COREBOOT_2609.revision
            and self.intent.source_tree_id == COREBOOT_2609.tree_id
            and self.intent.configuration_sha256 == X220_COREBOOT.expected_config_sha256
            and self.intent.expected_size_bytes == 8 * MIB
        )

    def _derive_and_validate_backup_inputs(self) -> None:
        assert self.backup_set is not None
        if self._blob_directory is not None:
            self._blob_directory.cleanup()
        captured = self.backup_set.chips
        if len(captured) != 1:
            raise ValueError("X220 requires a complete backup of its single observed chip")
        original_path, recovery_path = Path(captured[0].first_path), Path(captured[0].second_path)
        manifest_paths = (Path(self.backup_set.primary_manifest), Path(self.backup_set.recovery_manifest))
        if any(_path_has_link_component(path) or not path.is_file() for path in manifest_paths):
            raise ValueError("X220 primary/recovery backup manifests are missing or have symlinked path components")
        if any(_path_has_link_component(path) or not path.is_file() or path.stat().st_size != 8 * MIB for path in (original_path, recovery_path)):
            raise ValueError("X220 original full-chip backup copies are missing, unsafe, or wrong-sized")
        original, recovery = original_path.read_bytes(), recovery_path.read_bytes()
        if original != recovery or sha256_file(original_path) != captured[0].first_sha256 or sha256_file(recovery_path) != captured[0].second_sha256:
            raise ValueError("X220 independent backup reads differ or changed")
        self._blob_directory = tempfile.TemporaryDirectory(prefix="x220-coreboot-inputs-")
        root = Path(self._blob_directory.name)
        inputs: list[BlobInput] = []
        for name, region_name in (("descriptor.bin", "ifd"), ("me.bin", "me"), ("gbe.bin", "gbe")):
            region = next((item for item in self.intent.topology.regions if item.name == region_name), None)
            if region is None:
                raise ValueError(f"Observed X220 IFD map lacks {region_name}")
            data = original[region.start_byte:region.end_byte]
            path = root / name
            path.write_bytes(data)
            inputs.append(BlobInput(name, path, hashlib.sha256(data).hexdigest()))
        observed, provenance = validate_x220_backup_evidence(
            self.intent, self.backup_set, self.intent.topology.regions, tuple(inputs)
        )
        self._original_backup = observed
        self._backup_provenance = provenance
        self._blob_inputs = tuple(inputs)

    def _revalidate_backup(self) -> None:
        self._derive_and_validate_backup_inputs()
        assert self.backup_set is not None
        readiness = self.recovery_service.evaluate(self.intent, self.backup_set)
        if readiness.status != "ready" or readiness.operation_id != self.backup_set.operation_id:
            raise ValueError("X220 recovery readiness is no longer valid: " + "; ".join(readiness.reasons))
        self.recovery_readiness = readiness

    def _validate_artifact_result(self, result: ArtifactBuildResult) -> Path:
        if result.recipe_id != X220_COREBOOT.recipe_id or result.status is not BuildStatus.COMPLETE:
            raise ValueError(f"ArtifactEngine did not return a COMPLETE pinned X220 result: {result.code}: {result.message}")
        manifest, manifest_path = result.manifest, result.manifest_path
        if manifest is None or manifest_path is None or manifest.status != "complete" or manifest.schema_version != 3:
            raise ValueError("X220 COMPLETE result is missing its versioned immutable manifest")
        if (
            manifest.recipe_id != X220_COREBOOT.recipe_id
            or manifest.workflow_id != "x220-coreboot"
            or manifest.source_repository_url != COREBOOT_2609.repository_url
            or manifest.source_revision != COREBOOT_2609.revision
            or manifest.source_tree_id != COREBOOT_2609.tree_id
            or manifest.source_files != tuple((item.path, item.sha256) for item in COREBOOT_2609.files)
            or manifest.configuration_sha256 != X220_COREBOOT.expected_config_sha256
            or not manifest.resolved_configuration_sha256
            or not _SHA256.fullmatch(manifest.resolved_configuration_sha256)
            or manifest.observed_chip_capacity_bytes != 8 * MIB
            or manifest.expected_regions != self.intent.topology.regions
            or manifest.blob_sha256 != tuple((item.name, item.sha256) for item in sorted(self._blob_inputs, key=lambda item: item.name))
            or manifest.failure_code is not None
            or manifest.failure_detail is not None
            or manifest.finalized_operation_plan is None
            or not manifest.toolchain
            or not manifest.build_environment
        ):
            raise ValueError("X220 manifest differs from the pinned recipe, observed regions, blobs, or COMPLETE status")
        if len(result.artifacts) != 1 or len(manifest.artifacts) != 1:
            raise ValueError("X220 manifest/result must expose exactly its single recipe-selected ROM")
        selected = manifest.artifacts[0]
        selected_path_text = selected.relative_path or ""
        selected_path_posix = PurePosixPath(selected_path_text)
        selected_path_windows = PureWindowsPath(selected_path_text)
        if (
            selected.source_relative_path != X220_COREBOOT.output_relative_path
            or selected.relative_path != manifest.selected_artifact_relative_path
            or selected_path_text != "artifacts/coreboot.rom"
            or selected_path_posix.is_absolute()
            or selected_path_windows.is_absolute()
            or bool(selected_path_windows.drive)
            or "\\" in selected_path_text
            or selected.sha256 != manifest.selected_artifact_sha256
            or selected.size_bytes != 8 * MIB
            or selected.regions != self.intent.topology.regions
            or selected.cbfs_inspection is None
            or selected.backup_provenance != self._backup_provenance
        ):
            raise ValueError("X220 selected artifact record is incomplete or differs from the verified original backup")
        if selected_path_posix.is_absolute() or ".." in selected_path_posix.parts:
            raise ValueError("X220 selected artifact path escapes its manifest operation directory")
        if manifest_path.name != "manifest.json" or _path_has_link_component(manifest_path) or not manifest_path.is_file():
            raise ValueError("X220 manifest must be a regular manifest.json file")
        if manifest_path.parent.name != manifest.operation_id:
            raise ValueError("X220 manifest operation ID differs from its operation directory")
        if _path_has_link_component(manifest_path.parent) or not manifest_path.parent.is_dir():
            raise ValueError("X220 manifest operation directory is missing or unsafe")
        operation_root = manifest_path.parent.resolve(strict=True)
        artifact = operation_root.joinpath(*PurePosixPath(selected.relative_path or "").parts)
        current = operation_root
        for part in PurePosixPath(selected.relative_path or "").parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("X220 selected artifact path contains a symlink component")
        if _path_has_link_component(result.artifacts[0]) or result.artifacts[0].resolve(strict=True) != artifact.resolve(strict=True):
            raise ValueError("X220 ArtifactBuildResult path is not the manifest-selected staged file")
        resolved_artifact = artifact.resolve(strict=True)
        try:
            resolved_artifact.relative_to(operation_root)
        except ValueError as exc:
            raise ValueError("X220 selected artifact resolves outside its manifest operation directory") from exc
        if not resolved_artifact.is_file() or resolved_artifact.stat().st_size != 8 * MIB or sha256_file(resolved_artifact) != selected.sha256:
            raise ValueError("X220 staged artifact is missing, wrong-sized or changed")
        try:
            if str(uuid.UUID(manifest.operation_id)) != manifest.operation_id:
                raise ValueError("noncanonical UUID")
        except ValueError as exc:
            raise ValueError("X220 manifest operation ID is invalid") from exc
        checksum_file = manifest_path.with_name("manifest.sha256")
        raw = manifest_path.read_bytes()
        if _path_has_link_component(checksum_file) or not checksum_file.is_file() or checksum_file.read_text(encoding="ascii").strip() != f"{hashlib.sha256(raw).hexdigest()}  manifest.json":
            raise ValueError("X220 persisted manifest checksum is missing or invalid")
        try:
            saved = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("X220 persisted manifest is malformed") from exc
        if saved != json.loads(json.dumps(asdict(manifest), sort_keys=True)):
            raise ValueError("X220 manifest object differs from its persisted document")

        log_texts: dict[tuple[str, ...], str] = {}
        for command in manifest.commands:
            if command.exit_code != 0:
                raise ValueError("X220 manifest contains a failed command")
            log_relative = PurePosixPath(command.log_relative_path)
            if (
                log_relative.is_absolute()
                or ".." in log_relative.parts
                or "\\" in command.log_relative_path
                or len(log_relative.parts) != 2
                or log_relative.parts[0] != "logs"
            ):
                raise ValueError("X220 command log path must remain a simple logs/<file> path under the operation directory")
            log_path = manifest_path.parent.joinpath(*log_relative.parts)
            if _path_has_link_component(log_path) or not log_path.is_file() or sha256_file(log_path) != command.log_sha256:
                raise ValueError("X220 command log is missing, unsafe, or has a checksum mismatch")
            try:
                log_path.resolve(strict=True).relative_to(operation_root)
            except ValueError as exc:
                raise ValueError("X220 command log escapes its operation directory") from exc
            text = log_path.read_text(encoding="utf-8")
            if not text.startswith("$ " + " ".join(command.argv) + "\n"):
                raise ValueError("X220 command log does not record its exact argv")
            log_texts[command.argv] = text.split("\n", 1)[1]
        required_commands = {
            ("make", "olddefconfig"),
            ("make", "crossgcc-i386", f"CPUS={self._cpu_count}"),
            ("make", f"-j{self._cpu_count}"),
        }
        if not required_commands.issubset(log_texts):
            raise ValueError("X220 manifest lacks the exact successful pinned build command sequence")
        ifd_commands = [argv for argv in log_texts if Path(argv[0]).name == "ifdtool" and len(argv) == 3 and argv[1] == "-d"]
        expected_backup_path = self.backup_set.chips[0].first_path if self.backup_set else ""
        original_commands = [argv for argv in ifd_commands if argv[2] == expected_backup_path]
        generated_commands = [argv for argv in ifd_commands if argv[2] != expected_backup_path]
        if len(ifd_commands) != 2 or len(original_commands) != 1 or len(generated_commands) != 1:
            raise ValueError("X220 manifest lacks original and generated pinned ifdtool map inspections")
        for argv in ifd_commands:
            regions = parse_ifdtool_regions(log_texts[argv], 8 * MIB)
            compare_region_maps(regions, self.intent.topology.regions)
        cbfs_commands = [argv for argv in log_texts if Path(argv[0]).name == "cbfstool"]
        if (
            len(cbfs_commands) != 1
            or cbfs_commands[0][1:] != (generated_commands[0][2], "print", "-r", "COREBOOT", "-k")
            or Path(generated_commands[0][2]).name != "coreboot.rom"
        ):
            raise ValueError("X220 manifest lacks the exact pinned cbfstool COREBOOT payload inspection")
        cbfs = selected.cbfs_inspection
        assert cbfs is not None
        expected_cbfs_sources = tuple(sorted((item.path, item.sha256) for item in COREBOOT_2609.files if item.path in {"util/cbfstool/cbfs_image.c", "util/cbfstool/cbfstool.c"}))
        if (
            cbfs.tool_relative_path != "util/cbfstool/cbfstool"
            or not _SHA256.fullmatch(cbfs.tool_sha256)
            or cbfs.source_repository_url != COREBOOT_2609.repository_url
            or cbfs.source_revision != COREBOOT_2609.revision
            or cbfs.source_tree_id != COREBOOT_2609.tree_id
            or cbfs.region != "COREBOOT"
            or cbfs.required_payload != "fallback/payload"
            or cbfs.source_file_hashes != expected_cbfs_sources
        ):
            raise ValueError("X220 CBFS evidence does not name the pinned Coreboot parser/tool")
        cbfs_text = log_texts[cbfs_commands[0]]
        parsed_cbfs = parse_cbfs_parseable_output(
            cbfs_text, required_payload="fallback/payload", region_size_bytes=3 * MIB, expected_region="COREBOOT"
        )
        payloads = tuple(entry for entry in parsed_cbfs if entry.name == "fallback/payload")
        if len(payloads) != 1 or payloads[0].entry_type != "payload" or payloads[0].data_size_bytes <= 0 or parsed_cbfs != cbfs.inventory:
            raise ValueError("X220 CBFS inventory lacks exactly one non-empty fallback/payload entry")
        ifd_tool = selected.region_inspection_tool
        if (
            ifd_tool is None
            or ifd_tool.relative_path != "util/ifdtool/ifdtool"
            or ifd_tool.source_repository_url != COREBOOT_2609.repository_url
            or ifd_tool.source_revision != COREBOOT_2609.revision
            or ifd_tool.source_tree_id != COREBOOT_2609.tree_id
        ):
            raise ValueError("X220 region inspection evidence does not name the pinned ifdtool")
        generated_path = Path(generated_commands[0][2])
        if generated_path.name != "coreboot.rom" or generated_path.parent.name != "build":
            raise ValueError("X220 generated output path differs from the pinned build/coreboot.rom target")
        expected_source_root = operation_root.joinpath(*_X220_OPERATION_BUILD_SOURCE.parts)
        expected_raw_image = expected_source_root / "build" / "coreboot.rom"
        if (
            _path_has_link_component(expected_source_root)
            or not expected_source_root.is_dir()
            or not generated_path.is_absolute()
            or _path_has_link_component(generated_path)
            or not generated_path.is_file()
            or _path_has_link_component(expected_raw_image)
            or not expected_raw_image.is_file()
        ):
            raise ValueError("X220 operation-owned Coreboot source or generated image is missing or unsafe")
        source_root = expected_source_root.resolve(strict=True)
        try:
            source_relative_to_operation = source_root.relative_to(operation_root)
            generated_resolved = generated_path.resolve(strict=True)
            expected_generated_resolved = expected_raw_image.resolve(strict=True)
        except (OSError, ValueError) as exc:
            raise ValueError("X220 operation-owned Coreboot source path cannot be resolved safely") from exc
        if (
            source_relative_to_operation != Path(*_X220_OPERATION_BUILD_SOURCE.parts)
            or generated_resolved != expected_generated_resolved
        ):
            raise ValueError(
                "X220 generated source root must be the operation-owned work/coreboot-x220 checkout"
            )
        if manifest.source_files != tuple((item.path, item.sha256) for item in COREBOOT_2609.files):
            raise ValueError("X220 manifest pinned source-file set differs from Coreboot 26.09")
        for path, digest in manifest.source_files:
            relative_source = PurePosixPath(path)
            if relative_source.is_absolute() or ".." in relative_source.parts or "\\" in path:
                raise ValueError("X220 pinned source path is not a safe repository-relative path")
            source_file = source_root.joinpath(*relative_source.parts)
            if _path_has_link_component(source_file) or not source_file.is_file() or sha256_file(source_file) != digest:
                raise ValueError(f"X220 pinned source file is missing, unsafe, or changed: {path}")
        ifd_relative = PurePosixPath(ifd_tool.relative_path)
        cbfs_relative = PurePosixPath(cbfs.tool_relative_path)
        if any(path.is_absolute() or ".." in path.parts or "\\" in str(path) for path in (ifd_relative, cbfs_relative)):
            raise ValueError("X220 pinned inspection tool path is not repository-relative")
        ifd_binary = source_root.joinpath(*ifd_relative.parts)
        cbfstool_binary = source_root.joinpath(*cbfs_relative.parts)
        for binary, declared in ((ifd_binary, ifd_tool.executable_sha256), (cbfstool_binary, cbfs.tool_sha256)):
            if _path_has_link_component(binary) or not binary.is_file() or sha256_file(binary) != declared:
                raise ValueError("X220 pinned inspection tool is missing, unsafe, or changed")
        if any(_path_has_link_component(Path(argv[0])) or Path(argv[0]).resolve() != ifd_binary.resolve() for argv in ifd_commands):
            raise ValueError("X220 ifdtool command path differs from pinned tool evidence")
        if _path_has_link_component(Path(cbfs_commands[0][0])) or Path(cbfs_commands[0][0]).resolve() != cbfstool_binary.resolve():
            raise ValueError("X220 cbfstool command path differs from pinned tool evidence")
        raw_generated = Path(generated_commands[0][2])
        if _path_has_link_component(raw_generated) or not raw_generated.is_file() or raw_generated.stat().st_size != 8 * MIB or sha256_file(raw_generated) != selected.sha256:
            raise ValueError("X220 generated source image differs from the selected staged artifact")
        if self._original_backup is None:
            raise ValueError("Verified X220 original backup bytes are unavailable")
        validate_x220_region_preservation(self._original_backup, artifact.read_bytes(), self.intent.topology.regions)
        manifest_time = _parse_utc(manifest.created_at_utc)
        if self.backup_set is None or manifest_time <= _parse_utc(self.backup_set.created_at_utc):
            raise ValueError("X220 artifact manifest must be newer than its verified original backup")
        expected_plan = OperationPlan(
            profile_id=self.intent.profile_id,
            requested_model_id=self.intent.requested_model_id,
            firmware_family=self.intent.firmware_family,
            device=self.intent.device,
            topology=self.intent.topology,
            programmer=self.intent.programmer,
            artifact=ArtifactEvidence(
                path=str(artifact.resolve(strict=True)), model_id="x220", firmware_family=FirmwareFamily.COREBOOT,
                expected_sha256=selected.sha256, expected_size_bytes=selected.size_bytes,
                source_url=COREBOOT_2609.repository_url, source_revision=COREBOOT_2609.revision,
                regions=self.intent.topology.regions,
            ),
            backup=self.intent.backup,
        )
        if manifest.finalized_operation_plan != expected_plan:
            raise ValueError("X220 finalized operation plan does not bind the exact generated artifact and backup")
        return artifact

    def _open_journal(self, plan: OperationPlan) -> WorkflowSession:
        if self.journal_path is not None and self.journal_path.exists():
            session = WorkflowSession.resume(plan, self.journal_path, validator=self.validator)
        else:
            session = WorkflowSession(plan, validator=self.validator, journal_path=self.journal_path)
        if session.state is OperationState.DRAFT:
            if session.validate().decision is not CompatibilityDecision.ALLOWED:
                raise ValueError("X220 durable journal rejected the final operation plan")
        if session.state is OperationState.VALIDATED:
            if session.verify_backup().decision is not CompatibilityDecision.ALLOWED:
                raise ValueError("X220 durable journal rejected the original backup")
        if session.state is not OperationState.BACKUP_VERIFIED:
            raise ValueError("X220 durable journal is not at safe BACKUP_VERIFIED state")
        return session

    def _block(self, reason: str) -> CompatibilityResult:
        self.stage = X220WorkflowStage.BLOCKED
        self.reasons = (reason,)
        self.proposed_external_spi_plan = None
        self.operation_plan = None
        return CompatibilityResult(CompatibilityDecision.BLOCKED, self.intent.profile_id, EvidenceConfidence.LOW, self.reasons)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("X220 evidence timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _path_has_link_component(path: str | Path) -> bool:
    """Reject symlink/junction ancestors before resolving an evidence path."""
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    components = absolute.parts[1:] if absolute.anchor else absolute.parts
    for component in components:
        current = current / component
        if current.is_symlink():
            return True
        is_junction = getattr(current, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
    return False
