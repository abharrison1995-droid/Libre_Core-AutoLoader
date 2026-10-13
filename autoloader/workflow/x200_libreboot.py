"""Standalone, fail-closed X200 Libreboot staging workflow.

This workflow binds one pre-build identity/topology intent to a persistent,
two-read original backup, the accepted recovery-readiness service, and a
complete pinned ArtifactEngine manifest. It proposes a whole-chip external
SPI operation but exposes no writer or restore path. Staging never implies a
hardware write, read-back, or successful boot.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Sequence

from autoloader.exceptions import SafetyError
from autoloader.flasher.backup_service import BackupSetResult, BackupSetService
from autoloader.flasher.recovery import RecoveryReadinessRecord, RecoveryReadinessService
from autoloader.flasher.flashrom import ProgrammerType
from autoloader.flasher.spi import ChipProbeEvidence
from autoloader.rom_builder.artifact_engine import (
    X200_4MB,
    X200_8MB,
    ArtifactBuildResult,
    ArtifactRecord,
    ArtifactEngine,
    BuildRequest,
    BuildStatus,
    derive_lbmk_output_inventory,
    sha256_file,
    _LBMK_COREBOOT_CBFSTOOL_SOURCES,
    _LBMK_COREBOOT_ORIGINS,
    _LBMK_COREBOOT_REVISION,
    _LBMK_COREBOOT_TREE,
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
    FirmwareFamily,
    OperationPlan,
    ProgrammerEvidence,
    ProgrammerKind,
    OperationState,
    VerificationStatus,
    build_intent_sha256,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_X200_RECIPES = {recipe.recipe_id: recipe for recipe in (X200_4MB, X200_8MB)}


class X200WorkflowStage(str, Enum):
    DRAFT = "draft"
    BUILD_INTENT_VALIDATED = "build-intent-validated"
    RECOVERY_READY = "recovery-ready"
    STAGED = "staged"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class X200ExternalSPIPlan:
    """Proposed whole-chip plan for later, separately authorized hardware work."""

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
        if self.profile_id != "x200-libreboot" or self.scope != "whole-chip":
            raise ValueError("X200 V1 exposes only the whole-chip external SPI plan")
        for label, identifier in (
            ("operation", self.operation_id),
            ("backup operation", self.backup_operation_id),
            ("recovery record", self.recovery_record_id),
        ):
            try:
                if str(uuid.UUID(identifier)) != identifier:
                    raise ValueError("identifier is not canonical")
            except (ValueError, AttributeError) as exc:
                raise ValueError(f"X200 {label} ID must be a UUID") from exc
        if (
            not self.chip_id.strip()
            or not self.chip_name.strip()
            or self.capacity_bytes not in (4 * MIB, 8 * MIB)
            or self.voltage_mv != 3300
            or self.programmer_kind is not ProgrammerKind.EXTERNAL_SPI
            or not _SHA256.fullmatch(self.artifact_sha256)
            or not Path(self.artifact_manifest_path).is_absolute()
            or not _SHA256.fullmatch(self.artifact_manifest_sha256)
            or not Path(self.artifact_path).is_absolute()
            or not Path(self.operation_journal_path).is_absolute()
        ):
            raise ValueError("X200 external SPI proposal lacks exact chip, voltage, artifact, or journal evidence")
        if (
            self.status != "STAGED/HARDWARE_PENDING"
            or not self.human_hardware_authorization_required
            or self.flash_write_enabled
            or self.restore_write_enabled
            or self.readback_status is not VerificationStatus.NOT_PERFORMED
            or self.boot_status is not VerificationStatus.NOT_PERFORMED
        ):
            raise ValueError("X200 Phase 06 plan cannot claim hardware authorization, write, read-back, or boot success")


@dataclass(frozen=True)
class X200WorkflowResult:
    operation_id: str
    stage: X200WorkflowStage
    status: str
    message: str
    reasons: tuple[str, ...] = ()
    compatibility: CompatibilityResult | None = None
    recovery_readiness: RecoveryReadinessRecord | None = None
    operation_plan: OperationPlan | None = None
    proposed_external_spi_plan: X200ExternalSPIPlan | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "reasons", tuple(self.reasons))
        if self.stage is X200WorkflowStage.STAGED:
            if self.status != "STAGED/HARDWARE_PENDING" or self.proposed_external_spi_plan is None:
                raise ValueError("A staged X200 workflow result must remain hardware-pending and include its plan")
        else:
            if self.status != self.stage.value.upper():
                raise ValueError("X200 workflow status must reflect its non-terminal software stage")
            if self.proposed_external_spi_plan is not None or self.operation_plan is not None:
                raise ValueError("A non-staged X200 workflow result cannot expose a final operation plan")


def create_x200_build_intent(
    *,
    device: DeviceEvidence,
    topology: ChipTopology,
    programmer: ProgrammerEvidence,
) -> BuildIntentEvidence:
    """Create intent from the observed capacity and exact pinned X200 recipe."""
    if len(topology.chips) != 1:
        raise ValueError("X200 build intent requires exactly one observed physical chip candidate")
    capacity = topology.chips[0].capacity_bytes
    recipe = X200_4MB if capacity == 4 * MIB else X200_8MB if capacity == 8 * MIB else None
    if recipe is None:
        raise ValueError("X200 Libreboot V1 supports only an observed 4 MiB or 8 MiB chip")
    profile = V1_PROFILES["x200-libreboot"]
    return BuildIntentEvidence(
        profile_id=profile.profile_id,
        requested_model_id=profile.model_id,
        firmware_family=profile.firmware_family,
        device=device,
        topology=topology,
        programmer=programmer,
        recipe_id=recipe.recipe_id,
        source_url=recipe.source.repository_url,
        source_revision=recipe.source.revision,
        source_tree_id=recipe.source.tree_id,
        configuration_sha256=recipe.expected_config_sha256,
        expected_size_bytes=recipe.expected_size_bytes,
    )


class X200LibrebootWorkflow:
    """Evidence-driven X200 workflow that stops at STAGED/HARDWARE_PENDING."""

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
        self.operation_journal_path: str | None = None
        self.operation_id = str(uuid.uuid4())
        self.stage = X200WorkflowStage.DRAFT
        self.reasons: tuple[str, ...] = ()
        self.compatibility: CompatibilityResult | None = None
        self.backup_set: BackupSetResult | None = None
        self.recovery_readiness: RecoveryReadinessRecord | None = None
        self.operation_plan: OperationPlan | None = None
        self.proposed_external_spi_plan: X200ExternalSPIPlan | None = None

    @property
    def result(self) -> X200WorkflowResult:
        status = "STAGED/HARDWARE_PENDING" if self.stage is X200WorkflowStage.STAGED else self.stage.value.upper()
        message = (
            "Pinned artifact and recovery evidence are bound to a proposed whole-chip external SPI plan. "
            "No firmware write, read-back, or boot test was performed."
            if self.stage is X200WorkflowStage.STAGED
            else "; ".join(self.reasons) if self.reasons else "Workflow evidence has not reached a staged state."
        )
        return X200WorkflowResult(
            operation_id=self.operation_id,
            stage=self.stage,
            status=status,
            message=message,
            reasons=self.reasons,
            compatibility=self.compatibility,
            recovery_readiness=self.recovery_readiness,
            operation_plan=self.operation_plan,
            proposed_external_spi_plan=self.proposed_external_spi_plan,
        )

    def validate_build_intent(self) -> CompatibilityResult:
        if self.stage is not X200WorkflowStage.DRAFT:
            raise SafetyError("X200 build intent can only be validated once from DRAFT")
        recipe = _X200_RECIPES.get(self.intent.recipe_id)
        if recipe is None:
            return self._block("Build intent does not name an allowlisted X200 Libreboot recipe")
        if (
            self.intent.profile_id != "x200-libreboot"
            or self.intent.requested_model_id != "x200"
            or self.intent.firmware_family is not FirmwareFamily.LIBREBOOT
            or self.intent.source_url != recipe.source.repository_url
            or self.intent.source_revision != recipe.source.revision
            or self.intent.source_tree_id != recipe.source.tree_id
            or self.intent.configuration_sha256 != recipe.expected_config_sha256
            or self.intent.expected_size_bytes != recipe.expected_size_bytes
        ):
            return self._block("Build intent differs from the exact pinned X200 recipe")
        result = self.validator.validate_build_intent(self.intent)
        self.compatibility = result
        if result.decision is not CompatibilityDecision.ALLOWED:
            self.stage = X200WorkflowStage.BLOCKED
            self.reasons = result.reasons
            return result
        self.stage = X200WorkflowStage.BUILD_INTENT_VALIDATED
        self.reasons = ()
        return result

    def accept_backup(self, backup: BackupSetResult) -> RecoveryReadinessRecord:
        if self.stage is not X200WorkflowStage.BUILD_INTENT_VALIDATED:
            raise SafetyError("X200 backup acceptance requires a validated pre-build intent")
        if not backup.operation_id or backup.profile_id != self.intent.profile_id:
            self._block("Backup operation/profile binding is missing or mismatched")
            raise SafetyError("X200 backup operation/profile binding is missing or mismatched")
        try:
            uuid.UUID(backup.operation_id)
        except (ValueError, AttributeError) as exc:
            self._block("Backup operation ID is malformed")
            raise SafetyError("X200 backup operation ID is malformed") from exc
        bound_intent = replace(self.intent, backup=backup.backup)
        readiness = self.recovery_service.evaluate(bound_intent, backup)
        self.backup_set = backup
        self.intent = bound_intent
        self.recovery_readiness = readiness
        if readiness.status != "ready" or readiness.operation_id != backup.operation_id:
            self.stage = X200WorkflowStage.BLOCKED
            self.reasons = readiness.reasons or ("Recovery readiness operation binding is invalid",)
            raise SafetyError("X200 staging requires a current ready recovery record: " + "; ".join(self.reasons))
        if readiness.recovery_write_enabled or readiness.hardware_recovery_tested:
            self._block("Recovery record has an invalid write-enabled or hardware-tested status")
            raise SafetyError("X200 recovery readiness claims are inconsistent")
        self.stage = X200WorkflowStage.RECOVERY_READY
        self.reasons = ()
        return readiness

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
        """Capture the two-read backup using only the already validated intent."""
        if self.stage is not X200WorkflowStage.BUILD_INTENT_VALIDATED:
            raise SafetyError("X200 backup capture requires a validated pre-build intent")
        backup = service.capture(
            self.intent,
            probes,
            programmer=programmer,
            programmer_evidence=programmer_evidence,
            primary_directory=primary_directory,
            recovery_directory=recovery_directory,
            primary_persistent_confirmed=primary_persistent_confirmed,
            recovery_persistent_confirmed=recovery_persistent_confirmed,
        )
        return self.accept_backup(backup)

    def build_and_stage(
        self,
        *,
        source_dir: str | Path,
        output_root: str | Path,
        cpu_count: int = 1,
    ) -> X200WorkflowResult:
        """Invoke the pinned local ArtifactEngine, then validate its result.

        The engine itself returns a clear `LINUX_BUILD_ENV_REQUIRED` outcome on
        unsupported hosts and never downloads its upstream checkout.
        """
        if self.stage is not X200WorkflowStage.RECOVERY_READY:
            raise SafetyError("X200 source build requires a ready original-backup/recovery record")
        assert self.backup_set is not None
        result = self.artifact_engine.build(
            BuildRequest(
                recipe_id=self.intent.recipe_id,
                source_dir=Path(source_dir),
                output_root=Path(output_root),
                observed_chip_capacity_bytes=self.intent.expected_size_bytes,
                build_intent=self.intent,
                cpu_count=cpu_count,
            )
        )
        return self.accept_artifact_result(result)

    def accept_artifact_result(self, result: ArtifactBuildResult) -> X200WorkflowResult:
        if self.stage is not X200WorkflowStage.RECOVERY_READY:
            raise SafetyError("X200 artifact acceptance requires ready backup and recovery evidence")
        try:
            self._revalidate_recovery()
            self._validate_artifact_result(result)
            assert self.backup_set is not None
            assert self.intent.topology.chips
            selected_chip = self.intent.topology.chips[0]
            selected_record = self._selected_artifact_record(result)
            artifact_path = self._selected_artifact_path(result)
            manifest = result.manifest
            assert manifest is not None
            assert result.manifest_path is not None
            plan = OperationPlan(
                profile_id=self.intent.profile_id,
                requested_model_id=self.intent.requested_model_id,
                firmware_family=self.intent.firmware_family,
                device=self.intent.device,
                topology=self.intent.topology,
                programmer=self.intent.programmer,
                artifact=ArtifactEvidence(
                    path=str(artifact_path),
                    model_id="x200",
                    firmware_family=FirmwareFamily.LIBREBOOT,
                    expected_sha256=selected_record.sha256,
                    expected_size_bytes=selected_record.size_bytes,
                    source_url=manifest.source_repository_url,
                    source_revision=manifest.source_revision,
                    regions=selected_record.regions,
                ),
                backup=self.intent.backup,
            )
            final_validation = self.validator.validate(plan)
            backup_validation = self.validator.validate_backup(plan)
            if final_validation.decision is not CompatibilityDecision.ALLOWED:
                raise ValueError("Final artifact operation plan is not compatible: " + "; ".join(final_validation.reasons))
            if backup_validation.decision is not CompatibilityDecision.ALLOWED:
                raise ValueError("Bound X200 backup no longer verifies: " + "; ".join(backup_validation.reasons))
            readiness = self.recovery_service.evaluate(self.intent, self.backup_set)
            if readiness.status != "ready" or readiness.operation_id != self.backup_set.operation_id:
                raise ValueError("Recovery readiness is no longer valid: " + "; ".join(readiness.reasons))
            journal_session = self._open_operation_journal(plan)
            self.operation_id = journal_session.journal.operation_id
            journal_path = str(journal_session.journal.path)
            self.operation_journal_path = journal_path
            self.operation_plan = plan
            self.recovery_readiness = readiness
            self.compatibility = final_validation
            self.proposed_external_spi_plan = X200ExternalSPIPlan(
                operation_id=self.operation_id,
                profile_id=self.intent.profile_id,
                chip_id=selected_chip.chip_id,
                chip_name=self._probe_chip_name(self.backup_set, selected_chip.chip_id),
                capacity_bytes=selected_chip.capacity_bytes,
                voltage_mv=selected_chip.voltage_mv or 0,
                programmer_kind=self.intent.programmer.kind,
                artifact_path=str(artifact_path),
                artifact_sha256=selected_record.sha256,
                artifact_manifest_path=str(result.manifest_path.resolve(strict=True)),
                artifact_manifest_sha256=sha256_file(result.manifest_path),
                backup_operation_id=self.backup_set.operation_id,
                recovery_record_id=readiness.record_id,
                operation_journal_path=journal_path,
            )
            self.stage = X200WorkflowStage.STAGED
            self.reasons = ()
            return self.result
        except (OSError, ValueError, TypeError, KeyError, JournalError, WorkflowTransitionError) as exc:
            self._block(str(exc))
            return self.result

    def request_flash(self, *args, **kwargs) -> None:
        """Always reject in Phase 06; never inspect or call injected writers."""
        del args, kwargs
        raise SafetyError("X200 physical writes are disabled; only a STAGED/HARDWARE_PENDING plan is available")

    def request_restore(self, *args, **kwargs) -> None:
        """Always reject in Phase 06; restoration requires a later separate gate."""
        del args, kwargs
        raise SafetyError("X200 restore writes are disabled; recovery readiness is not restore authorization")

    def _revalidate_recovery(self) -> None:
        if self.backup_set is None:
            raise ValueError("Verified full-chip backup set is missing")
        readiness = self.recovery_service.evaluate(self.intent, self.backup_set)
        if readiness.status != "ready" or readiness.operation_id != self.backup_set.operation_id:
            raise ValueError("Backup manifests/copies no longer satisfy recovery readiness: " + "; ".join(readiness.reasons))
        self.recovery_readiness = readiness

    def _open_operation_journal(self, plan: OperationPlan) -> WorkflowSession:
        if self.journal_path is not None and self.journal_path.exists():
            session = WorkflowSession.resume(plan, self.journal_path, validator=self.validator)
        else:
            session = WorkflowSession(
                plan,
                validator=self.validator,
                journal_path=self.journal_path,
            )
        if session.state is OperationState.DRAFT:
            validation = session.validate()
            if validation.decision is not CompatibilityDecision.ALLOWED:
                raise ValueError("Durable X200 operation journal rejected the final plan")
        if session.state is OperationState.VALIDATED:
            backup = session.verify_backup()
            if backup.decision is not CompatibilityDecision.ALLOWED:
                raise ValueError("Durable X200 operation journal rejected the verified backup")
        if session.state is not OperationState.BACKUP_VERIFIED:
            raise ValueError("Durable X200 operation journal is not at the safe backup-verified checkpoint")
        return session

    def _validate_artifact_result(self, result: ArtifactBuildResult) -> None:
        recipe = _X200_RECIPES.get(self.intent.recipe_id)
        if recipe is None or result.recipe_id != self.intent.recipe_id:
            raise ValueError("Artifact result recipe does not match the validated X200 build intent")
        if result.status is not BuildStatus.COMPLETE or result.manifest is None or result.manifest_path is None:
            raise ValueError(f"ArtifactEngine did not return a COMPLETE X200 manifest: {result.code}: {result.message}")
        manifest = result.manifest
        if manifest.status != "complete" or manifest.schema_version != 2:
            raise ValueError("Artifact manifest is not a supported COMPLETE manifest")
        if (
            manifest.recipe_id != recipe.recipe_id
            or manifest.workflow_id != "x200-libreboot"
            or manifest.source_repository_url != recipe.source.repository_url
            or manifest.source_revision != recipe.source.revision
            or manifest.source_tree_id != recipe.source.tree_id
            or manifest.configuration_sha256 != recipe.expected_config_sha256
            or manifest.observed_chip_capacity_bytes != self.intent.expected_size_bytes
            or manifest.expected_regions
            or manifest.failure_code is not None
            or manifest.failure_detail is not None
        ):
            raise ValueError("Artifact manifest source, configuration, capacity, or status differs from the pinned X200 plan")
        if manifest.prebuild_intent_sha256 != build_intent_sha256(self.intent):
            raise ValueError("Artifact manifest is not bound to this exact X200 pre-build intent")
        dependency = manifest.x200_uboot_dependency
        if dependency is None:
            raise ValueError("Artifact manifest lacks exact patched U-Boot dependency provenance")
        if (
            dependency.source_url != "https://source.denx.de/u-boot/u-boot.git"
            or dependency.base_revision != "f919c3a889f0ec7d63a48b5d0ed064386b0980bd"
            or dependency.base_config_sha256 != "775e0c524aaefcffd4133597104dad638ed7db06cec9f76eb7d31e0e8d934839"
            or dependency.overlay_path != "config/u-boot/x86_64/patches/0009-x86-use-output-target-for-efi.patch"
            or dependency.overlay_sha256 != "03c17c53372864265bb6716b96ad90022dcc1b84d5ebd7a54fd93820be8b55e4"
            or dependency.patched_config_sha256 != "75ba0582f3240d1bffe9c1cf4710d0620001651e79c5288773607a7d30cf44fb"
        ):
            raise ValueError("Artifact manifest U-Boot dependency evidence differs from the reviewed X200 overlay")
        if manifest.resolved_configuration_sha256 is None or not _SHA256.fullmatch(manifest.resolved_configuration_sha256):
            raise ValueError("Artifact manifest lacks a valid resolved configuration digest")
        expected_sources = tuple((item.path, item.sha256) for item in recipe.source.files)
        if manifest.source_files != expected_sources:
            raise ValueError("Artifact manifest pinned source-file evidence differs from the X200 recipe")
        if not manifest.toolchain or not manifest.build_environment:
            raise ValueError("Artifact manifest lacks toolchain or build-environment evidence")
        if not any(
            len(command.argv) >= len(recipe.build_command)
            and command.argv[-len(recipe.build_command):] == recipe.build_command
            and command.exit_code == 0
            for command in manifest.commands
        ):
            raise ValueError("Artifact manifest does not record the exact successful pinned X200 build command")
        if not any(
            command.argv == ("./mk", "-f", "u-boot", "x86_64") and command.exit_code == 0
            for command in manifest.commands
        ):
            raise ValueError("Artifact manifest does not record the pinned U-Boot fetch/patch integration command")
        if result.manifest_path is None:
            raise ValueError("ArtifactEngine did not return its durable manifest path")
        self._validate_command_logs(manifest, result.manifest_path)
        if len(manifest.artifacts) != 31:
            raise ValueError("Artifact manifest does not contain the exact pinned 31-variant X200 output inventory")
        source_paths = tuple(sorted(item.source_relative_path for item in manifest.artifacts))
        expected_source_paths = derive_lbmk_output_inventory(recipe.target)
        if source_paths != expected_source_paths:
            raise ValueError("Artifact manifest output inventory differs from pinned lbmk naming rules")
        if sum(item.relative_path is not None for item in manifest.artifacts) != 1:
            raise ValueError("Artifact manifest must stage exactly the selected ROM, not every build variant")
        selected = self._selected_artifact_record(result)
        cbfs = selected.cbfs_inspection
        if (
            selected.source_relative_path != recipe.output_relative_path
            or selected.regions
            or cbfs is None
            or cbfs.region != "whole_flash"
            or cbfs.required_payload != "fallback/payload"
            or cbfs.source_repository_url not in _LBMK_COREBOOT_ORIGINS
            or cbfs.source_revision != _LBMK_COREBOOT_REVISION
            or cbfs.source_tree_id != _LBMK_COREBOOT_TREE
            or cbfs.source_file_hashes != tuple((item.path, item.sha256) for item in _LBMK_COREBOOT_CBFSTOOL_SOURCES)
        ):
            raise ValueError("Selected X200 artifact lacks the exact pinned whole-chip CBFS payload evidence")
        payload_entries = tuple(entry for entry in cbfs.inventory if entry.name == "fallback/payload")
        if (
            len(payload_entries) != 1
            or payload_entries[0].entry_type != "payload"
            or payload_entries[0].data_size_bytes <= 0
        ):
            raise ValueError(
                "Selected X200 artifact must contain exactly one non-empty fallback/payload CBFS payload entry"
            )
        self._validate_manifest_file(result)
        manifest_time = _parse_utc(manifest.created_at_utc, "artifact manifest")
        if self.backup_set is None:
            raise ValueError("Verified X200 backup set is missing")
        backup_time = _parse_utc(self.backup_set.created_at_utc, "backup set")
        if manifest_time <= backup_time:
            raise ValueError("Artifact manifest predates or is not later than the verified original backup")

    def _validate_manifest_file(self, result: ArtifactBuildResult) -> None:
        manifest_path = result.manifest_path
        manifest = result.manifest
        assert manifest_path is not None and manifest is not None
        if manifest_path.name != "manifest.json" or manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError("Artifact manifest path must be the regular manifest.json produced by ArtifactEngine")
        checksum_path = manifest_path.with_name("manifest.sha256")
        if checksum_path.is_symlink() or not checksum_path.is_file():
            raise ValueError("Artifact manifest checksum sidecar is missing or unsafe")
        raw = manifest_path.read_bytes()
        observed = hashlib.sha256(raw).hexdigest()
        if checksum_path.read_text(encoding="ascii").strip() != f"{observed}  manifest.json":
            raise ValueError("Artifact manifest checksum sidecar does not match the persisted manifest")
        try:
            saved = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Persisted artifact manifest is unreadable or malformed") from exc
        expected = json.loads(json.dumps(asdict(manifest), sort_keys=True))
        if saved != expected:
            raise ValueError("ArtifactBuildResult manifest object differs from its persisted manifest")

    @staticmethod
    def _validate_command_logs(manifest, manifest_path: Path) -> None:
        operation_dir = manifest_path.parent.resolve(strict=True)
        try:
            if str(uuid.UUID(manifest.operation_id)) != manifest.operation_id:
                raise ValueError("Artifact manifest operation ID is not a canonical UUID")
        except (ValueError, AttributeError) as exc:
            raise ValueError("Artifact manifest operation ID is malformed") from exc
        if operation_dir.name != manifest.operation_id:
            raise ValueError("Artifact manifest operation ID does not match its operation directory")
        if not manifest.commands:
            raise ValueError("Artifact manifest has no command log evidence")
        for command in manifest.commands:
            relative = PurePosixPath(command.log_relative_path)
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise ValueError("Artifact command log path is not operation-relative")
            candidate = operation_dir
            for component in relative.parts:
                candidate = candidate / component
                if candidate.is_symlink():
                    raise ValueError("Artifact command log path contains a symlink")
            if not candidate.is_file() or sha256_file(candidate) != command.log_sha256:
                raise ValueError("Artifact command log is missing or does not match its recorded SHA-256")

    def _selected_artifact_record(self, result: ArtifactBuildResult) -> ArtifactRecord:
        manifest = result.manifest
        if manifest is None or not manifest.selected_artifact_relative_path or not manifest.selected_artifact_sha256:
            raise ValueError("Complete X200 manifest does not identify a selected artifact")
        candidates = [
            item for item in manifest.artifacts
            if item.relative_path == manifest.selected_artifact_relative_path
            and item.sha256 == manifest.selected_artifact_sha256
        ]
        if len(candidates) != 1:
            raise ValueError("Selected artifact path and digest do not identify exactly one manifest record")
        return candidates[0]

    def _selected_artifact_path(self, result: ArtifactBuildResult) -> Path:
        manifest_path = result.manifest_path
        manifest = result.manifest
        selected = self._selected_artifact_record(result)
        if manifest_path is None or manifest is None or selected.relative_path is None:
            raise ValueError("Selected staged X200 artifact path is missing")
        operation_dir = manifest_path.parent.resolve(strict=True)
        relative_path = PurePosixPath(selected.relative_path)
        if relative_path.is_absolute() or ".." in relative_path.parts or not relative_path.parts:
            raise ValueError("Selected X200 artifact path is not operation-relative")
        candidate = operation_dir
        for component in relative_path.parts:
            candidate = candidate / component
            if candidate.is_symlink():
                raise ValueError("Selected X200 artifact path contains a symlink")
        path = candidate.resolve(strict=True)
        try:
            path.relative_to(operation_dir)
        except ValueError as exc:
            raise ValueError("Selected X200 artifact escapes its ArtifactEngine operation directory") from exc
        if path.is_symlink() or not path.is_file():
            raise ValueError("Selected X200 artifact is missing or not a regular staged file")
        if (
            len(result.artifacts) != 1
            or result.artifacts[0].is_symlink()
            or result.artifacts[0].resolve(strict=True) != path
        ):
            raise ValueError("ArtifactBuildResult path does not match the selected manifest artifact")
        if path.stat().st_size != selected.size_bytes or selected.size_bytes != self.intent.expected_size_bytes:
            raise ValueError("Selected X200 staged artifact size differs from the observed chip capacity")
        if sha256_file(path) != manifest.selected_artifact_sha256 or selected.sha256 != manifest.selected_artifact_sha256:
            raise ValueError("Selected X200 staged artifact digest differs from its COMPLETE manifest")
        return path

    @staticmethod
    def _probe_chip_name(backup: BackupSetResult, chip_id: str) -> str:
        matches = [item.observed_chip_name for item in backup.chips if item.chip_id == chip_id]
        if len(matches) != 1 or not matches[0].strip():
            raise ValueError("Verified backup does not identify exactly one observed X200 flash chip")
        return matches[0]

    def _block(self, *reasons: str) -> CompatibilityResult:
        self.stage = X200WorkflowStage.BLOCKED
        self.reasons = tuple(reason for reason in reasons if reason)
        self.operation_plan = None
        self.proposed_external_spi_plan = None
        result = CompatibilityResult(
            decision=CompatibilityDecision.BLOCKED,
            profile_id="x200-libreboot",
            confidence=self.compatibility.confidence if self.compatibility is not None else self.validator.validate_build_intent(self.intent).confidence,
            reasons=self.reasons,
        )
        self.compatibility = result
        return result


def _parse_utc(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"{label} timestamp is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{label} timestamp must be timezone-aware UTC")
    return parsed
