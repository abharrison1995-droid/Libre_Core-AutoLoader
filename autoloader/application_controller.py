"""Single application-facing API for the three reviewed V1 workflows.

This facade owns the status shown by the CLI and Textual UI.  It deliberately
does not expose a generic firmware configuration surface and does not invoke
any SPI read/write, firmware build, restore, read-back, or boot operation from
an interactive request.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import Enum
import json
import hashlib
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from autoloader.detection.bios import BIOSDetector
from autoloader.detection.model import ModelDetector
from autoloader.flasher.backup import BackupManager
from autoloader.reversion.t430_handoff import (
    T430Assessment,
    T430DetectionCapture,
    T430HandoffSession,
    T430HandoffStage,
    assess_t430_handoff,
)
from autoloader.workflow.compatibility import CompatibilityValidator
from autoloader.workflow.candidate_evidence import CandidateEvidenceStore, device_evidence_from_mapping
from autoloader.workflow.journal import JournalError, OperationJournal
from autoloader.workflow.profiles import V1_PROFILES
from autoloader.workflow.types import (
    CompatibilityDecision,
    DeviceEvidence,
    OperationPlan,
)
from autoloader.workflow.x200_libreboot import X200LibrebootWorkflow
from autoloader.workflow.x220_coreboot import X220CorebootWorkflow


class ApplicationAction(str, Enum):
    DETECT_DEVICE = "detect-device"
    IMPORT_EVIDENCE = "import-evidence"
    SELECT_PROFILE = "select-profile"
    VIEW_EVIDENCE = "view-evidence"
    PREPARE = "prepare"
    HANDOFF_GUIDANCE = "handoff-guidance"
    RESUME = "resume"
    INSPECT_CHECKPOINT = "inspect-checkpoint"
    VERIFY_BACKUPS = "verify-backups"
    BACKUP = "backup"
    BUILD = "build"
    FLASH = "flash"
    RESTORE = "restore"
    READBACK = "readback"
    BOOT_CHECK = "boot-check"


class ApplicationStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    BLOCKED = "BLOCKED"
    READY = "READY"
    BIOS_PREDICATE_READY = "BIOS_PREDICATE_READY"
    STAGED_HARDWARE_PENDING = "STAGED/HARDWARE_PENDING"
    NOT_PERFORMED = "NOT_PERFORMED"


@dataclass(frozen=True)
class ProfileStatus:
    """Authoritative controller view consumed by both interfaces."""

    profile_id: str
    model_id: str
    firmware_family: str
    status: ApplicationStatus
    decision: CompatibilityDecision
    stage: str
    reasons: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    allowed_next_actions: tuple[ApplicationAction, ...]
    operation_id: str | None = None
    evidence_summary: tuple[str, ...] = ()
    checkpoint_ref: str | None = None


@dataclass(frozen=True)
class ApplicationActionResult:
    action: ApplicationAction
    status: ApplicationStatus
    code: str
    message: str
    reasons: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    progress_percent: int | None = None


class ApplicationController:
    """Application facade shared by Click commands and Textual screens."""

    def __init__(
        self,
        *,
        model_detector: ModelDetector | None = None,
        bios_detector: BIOSDetector | None = None,
        backup_manager: BackupManager | None = None,
        validator: CompatibilityValidator | None = None,
    ) -> None:
        self.model_detector = model_detector or ModelDetector()
        self.bios_detector = bios_detector or BIOSDetector()
        # Do not construct the legacy manager by default: its constructor
        # creates a backup directory/manifest. The controller remains
        # side-effect free until an explicitly configured local verifier is
        # supplied.
        self.backup_manager = backup_manager
        self.validator = validator or CompatibilityValidator()
        self.selected_profile_id: str | None = None
        self.capture: T430DetectionCapture | None = None
        self.device_evidence: DeviceEvidence | None = None
        self.evidence_ref: str | None = None
        self.t430_session: T430HandoffSession | None = None
        self.x200_workflow: X200LibrebootWorkflow | None = None
        self.x220_workflow: X220CorebootWorkflow | None = None

    @property
    def profile_ids(self) -> tuple[str, ...]:
        return tuple(V1_PROFILES)

    def select_profile(self, profile_id: str) -> ProfileStatus:
        """Select one of the explicit V1 profiles; database aliases are rejected."""
        if profile_id not in V1_PROFILES:
            raise ValueError(f"Unsupported V1 profile: {profile_id}")
        self.selected_profile_id = profile_id
        return self.profile_status(profile_id)

    def detect_device(self) -> tuple[T430DetectionCapture, ProfileStatus | None]:
        """Capture read-only DMI/BIOS evidence; this never probes SPI hardware."""
        capture = T430DetectionCapture.detect_now(
            model_detector=self.model_detector,
            bios_detector=self.bios_detector,
        )
        self.capture = capture
        self.device_evidence = capture.device
        self.evidence_ref = f"dmi-capture:{capture.capture_id}"
        detected_model = self._canonical_model(capture.device)
        if detected_model is not None:
            profile_id = {
                "t430": "t430-1vyrain",
                "x200": "x200-libreboot",
                "x220": "x220-coreboot",
            }[detected_model]
            if self.selected_profile_id is None:
                self.selected_profile_id = profile_id
        selected = self.selected_profile_id
        return capture, self.profile_status(selected) if selected is not None else None

    def import_device_evidence(
        self,
        profile_id: str,
        evidence: DeviceEvidence,
        *,
        evidence_ref: str,
    ) -> ProfileStatus:
        """Import externally collected raw DMI/BIOS evidence as an untrusted candidate."""
        if profile_id not in V1_PROFILES:
            raise ValueError(f"Unsupported V1 profile: {profile_id}")
        if not evidence_ref.strip():
            raise ValueError("An evidence reference is required")
        evidence = device_evidence_from_mapping(asdict(evidence))
        if self.t430_session is not None or self.x200_workflow is not None or self.x220_workflow is not None:
            raise ValueError("Candidate evidence cannot replace an active workflow or its durable checkpoint")
        self.selected_profile_id = profile_id
        self.device_evidence = evidence
        self.evidence_ref = evidence_ref.strip()
        self.capture = None
        self.t430_session = None
        self.x200_workflow = None
        self.x220_workflow = None
        return self.profile_status(profile_id)

    def import_device_evidence_document(
        self,
        profile_id: str,
        document: str,
        *,
        evidence_ref: str,
        store_path: str | Path | None = None,
    ) -> ProfileStatus:
        """Parse a strict JSON DeviceEvidence document, preserving it as candidate evidence."""
        try:
            value = json.loads(document)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Device evidence JSON is malformed: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise ValueError("Device evidence JSON must be an object")
        try:
            evidence = device_evidence_from_mapping(value)
        except ValueError as exc:
            raise ValueError(f"Device evidence JSON is invalid: {exc}") from exc
        if self.t430_session is not None or self.x200_workflow is not None or self.x220_workflow is not None:
            raise ValueError("Candidate evidence cannot replace an active workflow or its durable checkpoint")
        if store_path is not None:
            CandidateEvidenceStore.save(
                store_path, profile_id=profile_id, evidence=evidence, evidence_ref=evidence_ref
            )
        return self.import_device_evidence(profile_id, evidence, evidence_ref=evidence_ref)

    def load_candidate_evidence(
        self, path: str | Path, *, expected_profile: str | None = None
    ) -> ProfileStatus:
        profile_id, evidence, evidence_ref = CandidateEvidenceStore.load(path)
        if expected_profile is not None and profile_id != expected_profile:
            raise ValueError(
                f"Saved candidate profile {profile_id} does not match selected profile {expected_profile}"
            )
        return self.import_device_evidence(profile_id, evidence, evidence_ref=evidence_ref)

    def inspect_operation_checkpoint(self, path: str | Path) -> ApplicationActionResult:
        """Validate and summarize an operation journal in read-only mode."""
        if not self._safe_checkpoint_path(path):
            raise ValueError("operation checkpoint path must be an existing regular file without symlink components")
        journal = OperationJournal.inspect_checkpoint(path)
        artifact = journal.artifact_metadata
        refs = [f"operation:{journal.operation_id}"]
        summary = [f"State: {journal.state.value}", f"Transitions: {len(journal.entries)}"]
        if journal.device_fingerprint:
            refs.append(f"device-sha256:{journal.device_fingerprint}")
        if artifact is not None:
            summary.extend((
                f"Artifact: {artifact.model_id}/{artifact.firmware_family} · {artifact.path}",
                f"Artifact source: {artifact.source_url}@{artifact.source_revision}",
                f"Artifact size: {artifact.expected_size_bytes} bytes; expected SHA-256 {artifact.expected_sha256}",
                f"Artifact observed SHA-256: {artifact.actual_sha256 or 'not recorded'}",
                f"Artifact regions: {', '.join(region.name for region in artifact.regions) or 'none recorded'}",
            ))
            refs.append(f"artifact-sha256:{artifact.expected_sha256}")
        summary.append(f"Verified backup records: {len(journal.backup_checksums)}")
        if journal.entries:
            latest = journal.entries[-1]
            summary.append(f"Last transition: {latest.action} · {latest.code} · {latest.timestamp_utc}")
        return ApplicationActionResult(
            ApplicationAction.INSPECT_CHECKPOINT,
            ApplicationStatus.UNKNOWN,
            "CHECKPOINT_INSPECTED_READ_ONLY",
            "\n".join(summary),
            tuple(f"{entry.from_state.value} -> {entry.to_state.value}: {entry.code}" for entry in journal.entries),
            tuple(refs),
        )

    def persist_current_candidate(self, path: str | Path) -> str:
        """Persist current DMI/BIOS fields explicitly as an unauthenticated candidate."""
        if self.device_evidence is None or self.selected_profile_id is None:
            raise ValueError("Capture device evidence and select a V1 profile before persisting it")
        document = json.dumps(asdict(self.device_evidence), sort_keys=True, separators=(",", ":"))
        reference = self.evidence_document_reference(document)
        CandidateEvidenceStore.save(
            path, profile_id=self.selected_profile_id, evidence=self.device_evidence, evidence_ref=reference
        )
        return reference

    def resume_t430_checkpoint(self, path: str | Path) -> ProfileStatus:
        """Restore one validated, pending human handoff checkpoint without hardware I/O."""
        if self.selected_profile_id not in (None, "t430-1vyrain"):
            raise ValueError("A T430 checkpoint cannot be resumed for a different selected profile")
        session = T430HandoffSession.resume_from_checkpoint(path, validator=self.validator)
        self.capture = None
        return self.attach_t430_session(session)

    def attach_t430_session(self, session: T430HandoffSession) -> ProfileStatus:
        """Attach an existing validated handoff session without initiating it."""
        if self.selected_profile_id not in (None, "t430-1vyrain"):
            raise ValueError("A T430 handoff cannot be attached to another selected profile")
        self.selected_profile_id = "t430-1vyrain"
        self.t430_session = session
        self.device_evidence = None
        self.evidence_ref = f"t430-operation:{session.operation_id}"
        return self.profile_status("t430-1vyrain")

    def attach_x200_workflow(self, workflow: X200LibrebootWorkflow) -> ProfileStatus:
        if workflow.intent.profile_id != "x200-libreboot":
            raise ValueError("Only the allowlisted X200 Libreboot workflow can be attached here")
        self.selected_profile_id = "x200-libreboot"
        self.x200_workflow = workflow
        self.device_evidence = workflow.intent.device
        self.evidence_ref = f"x200-operation:{workflow.operation_id}"
        return self.profile_status("x200-libreboot")

    def attach_x220_workflow(self, workflow: X220CorebootWorkflow) -> ProfileStatus:
        if workflow.intent.profile_id != "x220-coreboot":
            raise ValueError("Only the allowlisted X220 Coreboot workflow can be attached here")
        self.selected_profile_id = "x220-coreboot"
        self.x220_workflow = workflow
        self.device_evidence = workflow.intent.device
        self.evidence_ref = f"x220-operation:{workflow.operation_id}"
        return self.profile_status("x220-coreboot")

    def profile_status(self, profile_id: str | None = None) -> ProfileStatus:
        profile_id = profile_id or self.selected_profile_id
        if not isinstance(profile_id, str) or profile_id not in V1_PROFILES:
            raise ValueError("A V1 profile must be selected")
        profile = V1_PROFILES[profile_id]
        evidence_refs = (self.evidence_ref,) if self.evidence_ref else ()

        if profile_id == "t430-1vyrain" and self.t430_session is not None:
            assessment = self.t430_session.assessment
            stage = self.t430_session.stage.value
            status = self._t430_status(profile_id, assessment, stage, evidence_refs)
            return replace(
                status,
                operation_id=self.t430_session.operation_id,
                evidence_summary=self._t430_session_summary(self.t430_session),
                checkpoint_ref=str(self.t430_session.checkpoint_path) if self.t430_session.checkpoint_path else None,
            )
        if profile_id == "t430-1vyrain" and self.device_evidence is not None and self.capture is not None:
            if not self.capture.device_fingerprint_sha256:
                return ProfileStatus(
                    profile_id, "t430", profile.firmware_family.value,
                    ApplicationStatus.UNKNOWN, CompatibilityDecision.UNKNOWN, "device-fingerprint-required",
                    ("A stable device fingerprint is required before any T430 handoff stage.",),
                    evidence_refs, (ApplicationAction.DETECT_DEVICE, ApplicationAction.VIEW_EVIDENCE),
                )
            assessment = assess_t430_handoff(self.device_evidence, validator=self.validator)
            return self._t430_status(profile_id, assessment, assessment.stage.value, evidence_refs)
        if profile_id == "t430-1vyrain" and self.device_evidence is not None:
            return ProfileStatus(
                profile_id, "t430", profile.firmware_family.value,
                ApplicationStatus.UNKNOWN, CompatibilityDecision.UNKNOWN, "fresh-capture-required",
                ("Imported T430 evidence is only a candidate; a fresh DMI/BIOS capture is required before the pinned handoff predicate can be evaluated.",),
                evidence_refs, (ApplicationAction.DETECT_DEVICE, ApplicationAction.VIEW_EVIDENCE, ApplicationAction.RESUME),
                evidence_summary=self._device_evidence_summary(self.device_evidence),
            )

        workflow: Any = self.x200_workflow if profile_id == "x200-libreboot" else self.x220_workflow
        if workflow is not None:
            result = workflow.result
            decision = result.compatibility.decision if result.compatibility else CompatibilityDecision.UNKNOWN
            staged = result.status == "STAGED/HARDWARE_PENDING"
            if staged:
                actions = [ApplicationAction.VIEW_EVIDENCE]
                checkpoint_path = getattr(workflow, "operation_journal_path", None)
                checkpoint_ref = str(checkpoint_path) if self._safe_checkpoint_path(checkpoint_path) else None
                if checkpoint_ref is not None:
                    actions.append(ApplicationAction.INSPECT_CHECKPOINT)
                return ProfileStatus(
                    profile_id, profile.model_id, profile.firmware_family.value,
                    ApplicationStatus.STAGED_HARDWARE_PENDING, decision, result.stage.value,
                    tuple(result.reasons), evidence_refs, tuple(actions),
                    operation_id=result.operation_id,
                    evidence_summary=self._workflow_evidence_summary(workflow),
                    checkpoint_ref=checkpoint_ref,
                )
            state = ApplicationStatus.BLOCKED if decision is CompatibilityDecision.BLOCKED else ApplicationStatus.UNKNOWN
            actions = [ApplicationAction.VIEW_EVIDENCE]
            checkpoint_path = getattr(workflow, "operation_journal_path", None)
            checkpoint_ref = str(checkpoint_path) if self._safe_checkpoint_path(checkpoint_path) else None
            if checkpoint_ref is not None:
                actions.append(ApplicationAction.INSPECT_CHECKPOINT)
            return ProfileStatus(
                profile_id, profile.model_id, profile.firmware_family.value, state,
                decision, result.stage.value, tuple(result.reasons) or ("Workflow evidence is not complete.",),
                evidence_refs, tuple(actions),
                operation_id=result.operation_id,
                evidence_summary=self._workflow_evidence_summary(workflow),
                checkpoint_ref=checkpoint_ref,
            )

        if self.device_evidence is None:
            return ProfileStatus(
                profile_id, profile.model_id, profile.firmware_family.value,
                ApplicationStatus.UNKNOWN, CompatibilityDecision.UNKNOWN, "evidence-required",
                ("Raw device, observed chip topology, and pinned recipe evidence have not been imported.",),
                (), (ApplicationAction.DETECT_DEVICE, ApplicationAction.IMPORT_EVIDENCE)
                + ((ApplicationAction.RESUME,) if profile_id == "t430-1vyrain" else ()),
            )
        return ProfileStatus(
            profile_id, profile.model_id, profile.firmware_family.value,
            ApplicationStatus.UNKNOWN, CompatibilityDecision.UNKNOWN, "topology-and-recipe-evidence-required",
            ("DMI/BIOS is only a device candidate; observed chip topology, voltage, backup, and pinned artifact evidence are still required.",),
            evidence_refs,
            (ApplicationAction.IMPORT_EVIDENCE, ApplicationAction.VIEW_EVIDENCE),
            evidence_summary=self._device_evidence_summary(self.device_evidence),
        )

    def _t430_status(
        self,
        profile_id: str,
        assessment: T430Assessment,
        stage: str,
        evidence_refs: tuple[str, ...],
    ) -> ProfileStatus:
        if stage in {
            T430HandoffStage.AWAITING_IVPREP_REDETECTION.value,
            T430HandoffStage.AWAITING_POST_INSTALL_REDETECTION.value,
        }:
            return ProfileStatus(
                profile_id, "t430", V1_PROFILES[profile_id].firmware_family.value,
                ApplicationStatus.UNKNOWN, CompatibilityDecision.UNKNOWN, stage,
                ("A human handoff is awaiting fresh return evidence. This application does not yet collect and submit the required T430 return attestations; no handoff can be repeated from this state.",),
                evidence_refs, (ApplicationAction.VIEW_EVIDENCE,),
            )
        if stage == T430HandoffStage.POST_INSTALL_EVIDENCE_PENDING_ACCEPTANCE.value:
            return ProfileStatus(
                profile_id, "t430", V1_PROFILES[profile_id].firmware_family.value,
                ApplicationStatus.UNKNOWN, CompatibilityDecision.UNKNOWN, stage,
                ("Post-installation evidence is pending acceptance; no successful install or boot is claimed.",),
                evidence_refs, (ApplicationAction.VIEW_EVIDENCE,),
            )
        if assessment.decision is CompatibilityDecision.ALLOWED:
            status = ApplicationStatus.BIOS_PREDICATE_READY
            actions: tuple[ApplicationAction, ...] = (ApplicationAction.HANDOFF_GUIDANCE, ApplicationAction.VIEW_EVIDENCE)
        elif assessment.stage is T430HandoffStage.IVPREP_REQUIRED:
            status = ApplicationStatus.BLOCKED
            actions = (ApplicationAction.HANDOFF_GUIDANCE, ApplicationAction.VIEW_EVIDENCE)
        elif assessment.decision is CompatibilityDecision.BLOCKED:
            status = ApplicationStatus.BLOCKED
            actions = (ApplicationAction.VIEW_EVIDENCE,)
        else:
            status = ApplicationStatus.UNKNOWN
            actions = (ApplicationAction.VIEW_EVIDENCE,)
        return ProfileStatus(
            profile_id, "t430", V1_PROFILES[profile_id].firmware_family.value,
            status, assessment.decision, stage, tuple(assessment.reasons), evidence_refs, actions,
            evidence_summary=(
                f"Pinned upstream BIOS predicate: {assessment.decision.value}; numeric version: {assessment.numeric_bios}",
                "Handoff execution and post-install validation remain human-run and pending.",
            ),
        )

    def all_profile_statuses(self) -> tuple[ProfileStatus, ...]:
        return tuple(self.profile_status(profile_id) for profile_id in V1_PROFILES)

    def request_action(
        self,
        action: ApplicationAction,
        *,
        profile_id: str | None = None,
        device_evidence: DeviceEvidence | None = None,
        evidence_ref: str | None = None,
        checkpoint_path: str | Path | None = None,
    ) -> ApplicationActionResult:
        """Route interface requests through a single fail-closed action gate."""
        profile_id = profile_id or self.selected_profile_id
        if action is ApplicationAction.DETECT_DEVICE:
            capture, status = self.detect_device()
            if status is not None and status.stage in {
                T430HandoffStage.AWAITING_IVPREP_REDETECTION.value,
                T430HandoffStage.AWAITING_POST_INSTALL_REDETECTION.value,
            }:
                return ApplicationActionResult(
                    action, ApplicationStatus.UNKNOWN, "T430_RETURN_CAPTURE_CANDIDATE_ONLY",
                    "Read-only DMI/BIOS capture recorded separately as a candidate. The T430 handoff session was not advanced because this interface does not collect the required stage-specific human return attestations.",
                    status.reasons, (f"dmi-capture:{capture.capture_id}",),
                )
            return ApplicationActionResult(
                action, status.status if status else ApplicationStatus.UNKNOWN,
                "DEVICE_EVIDENCE_CAPTURED", "Read-only DMI/BIOS evidence captured; no SPI operation was performed.",
                status.reasons if status else (), (f"dmi-capture:{capture.capture_id}",),
            )
        if action is ApplicationAction.VERIFY_BACKUPS:
            return self.verify_recorded_backups()
        if action is ApplicationAction.INSPECT_CHECKPOINT:
            if checkpoint_path is None:
                return self._unavailable(
                    action, "CHECKPOINT_PATH_REQUIRED", "Supply an explicit operation journal path to inspect it.", profile_id
                )
            try:
                return self.inspect_operation_checkpoint(checkpoint_path)
            except (JournalError, OSError, ValueError) as exc:
                return self._unavailable(action, "CHECKPOINT_INSPECTION_FAILED", str(exc), profile_id)
        if action is ApplicationAction.RESUME:
            if profile_id != "t430-1vyrain":
                return self._unavailable(
                    action, "SAFE_RESUME_UNAVAILABLE",
                    "Only the T430 human-handoff checkpoint has an application resume adapter; X200/X220 require the exact typed operation plan.",
                    profile_id,
                )
            if checkpoint_path is None:
                return self._unavailable(action, "CHECKPOINT_PATH_REQUIRED", "Supply an explicit T430 checkpoint path to resume it safely.", profile_id)
            try:
                status = self.resume_t430_checkpoint(checkpoint_path)
            except (OSError, ValueError, RuntimeError) as exc:
                return self._unavailable(action, "T430_CHECKPOINT_REJECTED", str(exc), profile_id)
            return ApplicationActionResult(
                action, status.status, "T430_CHECKPOINT_RESUMED",
                "Validated T430 checkpoint restored as a pending human handoff; no hardware operation was performed.",
                status.reasons, status.evidence_refs,
            )
        if action is ApplicationAction.IMPORT_EVIDENCE:
            if not isinstance(profile_id, str) or profile_id not in V1_PROFILES or device_evidence is None or not evidence_ref:
                return self._unavailable(
                    action, "TYPED_EVIDENCE_REQUIRED",
                    "Import requires an allowlisted profile, raw DeviceEvidence, and a stable evidence reference.",
                    profile_id,
                )
            status = self.import_device_evidence(profile_id, device_evidence, evidence_ref=evidence_ref)
            return ApplicationActionResult(
                action, status.status, "DEVICE_EVIDENCE_IMPORTED",
                "Raw device evidence imported as a candidate; model, chip, compatibility, and artifact validation remain separate.",
                status.reasons, status.evidence_refs,
            )
        if action is ApplicationAction.VIEW_EVIDENCE:
            if profile_id is None:
                return self._unavailable(action, "PROFILE_REQUIRED", "Select one V1 profile first.")
            status = self.profile_status(profile_id)
            return ApplicationActionResult(action, status.status, "PROFILE_STATUS", status.stage,
                                           status.reasons, status.evidence_refs)
        if action is ApplicationAction.HANDOFF_GUIDANCE:
            if profile_id != "t430-1vyrain":
                return self._unavailable(action, "PROFILE_MISMATCH", "Manual 1vyrain handoff guidance is only available for the T430 profile.", profile_id)
            status = self.profile_status(profile_id)
            if status.stage == T430HandoffStage.IVPREP_REQUIRED.value:
                return ApplicationActionResult(
                    action, ApplicationStatus.BLOCKED, "IVPREP_HUMAN_HANDOFF_GUIDANCE",
                    "The T430 BIOS predicate requires IVprep. Use the pinned IVprep instructions manually on Windows 64-bit; require Flash BIOS Updating by End-Users enabled, Secure RollBack Prevention disabled, AC connected, and do not run as Administrator. Power loss during the BIOS-region downgrade can brick the system. The application does not download or execute IVprep.",
                    status.reasons, status.evidence_refs,
                )
            if status.decision is not CompatibilityDecision.ALLOWED:
                return self._unavailable(action, "T430_HANDOFF_NOT_READY", "T430 evidence does not meet the pinned upstream BIOS/identity predicate.", profile_id)
            return ApplicationActionResult(
                action, ApplicationStatus.BIOS_PREDICATE_READY, "HUMAN_HANDOFF_GUIDANCE_ONLY",
                "Review the pinned upstream 1vyrain and IVprep instructions manually. This application does not download, execute, write media, approve, or perform a BIOS handoff. Complete the stage-specific attestations and fresh re-detection first.",
                status.reasons, status.evidence_refs,
            )
        unavailable = {
            ApplicationAction.IMPORT_EVIDENCE: ("IMPORT_REQUIRES_TYPED_EVIDENCE", "Import raw DMI/BIOS evidence through the typed controller API."),
            ApplicationAction.SELECT_PROFILE: ("PROFILE_REQUIRED", "Select exactly one allowlisted V1 profile."),
            ApplicationAction.PREPARE: ("PREPARATION_EVIDENCE_REQUIRED", "Workflow preparation requires validated model-specific evidence and is not available from this screen."),
            ApplicationAction.HANDOFF_GUIDANCE: ("HUMAN_HANDOFF_REQUIRED", "A T430 handoff requires the reviewed stage-specific attestations and explicit human approval."),
            ApplicationAction.RESUME: ("RESUME_EVIDENCE_REQUIRED", "Resume requires the exact durable workflow plan and fresh evidence."),
            ApplicationAction.BACKUP: ("BACKUP_UNAVAILABLE", "Backup capture is unavailable through the interactive interface; no SPI read was attempted."),
            ApplicationAction.BUILD: ("BUILD_UNAVAILABLE", "No firmware build was invoked; a validated, supported build environment and complete workflow evidence are required."),
            ApplicationAction.FLASH: ("FLASH_UNAVAILABLE", "Physical firmware writes are disabled; no writer or subprocess was invoked."),
            ApplicationAction.RESTORE: ("RESTORE_UNAVAILABLE", "Firmware restore is disabled; no writer or subprocess was invoked."),
            ApplicationAction.READBACK: ("READBACK_NOT_PERFORMED", "Hardware read-back is unavailable through this interface; no SPI read was attempted."),
            ApplicationAction.BOOT_CHECK: ("BOOT_CHECK_NOT_PERFORMED", "No hardware boot check has been performed."),
        }
        code, message = unavailable.get(action, ("ACTION_UNAVAILABLE", "This action is unavailable."))
        return self._unavailable(action, code, message, profile_id)

    @staticmethod
    def evidence_document_reference(document: str) -> str:
        """Return a content reference without claiming that imported data is authenticated."""
        return "imported-device-evidence:sha256:" + hashlib.sha256(document.encode("utf-8")).hexdigest()

    @staticmethod
    def _device_evidence_summary(device: DeviceEvidence) -> tuple[str, ...]:
        return tuple(
            f"{label}: {value}"
            for label, value in (
                ("DMI product name", device.raw_dmi_product_name),
                ("DMI product version", device.raw_dmi_product_version),
                ("DMI family", device.raw_dmi_system_family),
                ("MTM", device.raw_mtm),
                ("BIOS version", device.raw_bios_version),
                ("BIOS vendor", device.raw_bios_vendor),
            )
            if value
        )

    @classmethod
    def _workflow_evidence_summary(cls, workflow: X200LibrebootWorkflow | X220CorebootWorkflow) -> tuple[str, ...]:
        intent = workflow.intent
        rows = [f"Operation ID: {workflow.operation_id}"]
        rows.extend(cls._device_evidence_summary(intent.device))
        rows.append(f"Programmer: {intent.programmer.kind.value}; observed={intent.programmer.observed}; output={intent.programmer.voltage_mv} mV")
        rows.extend(
            f"Observed chip {chip.chip_id}: {chip.capacity_bytes} bytes; {chip.voltage_mv} mV; observed={chip.observed}"
            for chip in intent.topology.chips
        )
        rows.extend(
            f"Observed region {region.name}: [{region.start_byte:#x}, {region.end_byte:#x})"
            for region in intent.topology.regions
        )
        backup = getattr(workflow, "backup_set", None)
        if backup is not None:
            rows.extend((
                f"Backup operation: {backup.operation_id}; tool={backup.tool_name} {backup.tool_version}",
                f"Backup manifests: primary SHA-256 {backup.primary_manifest_sha256}; recovery SHA-256 {backup.recovery_manifest_sha256}",
            ))
            rows.extend(
                f"Backup chip {chip.chip_id}: two reads {chip.first_sha256}; {chip.second_sha256}"
                for chip in backup.chips
            )
        recovery = getattr(workflow, "recovery_readiness", None)
        if recovery is not None:
            rows.append(
                f"Recovery record: {recovery.status}; id={recovery.record_id}; writes-enabled={recovery.recovery_write_enabled}; hardware-tested={recovery.hardware_recovery_tested}"
            )
        plan = getattr(workflow, "proposed_external_spi_plan", None)
        if plan is not None:
            rows.extend((
                f"Artifact: {plan.artifact_path}; SHA-256={plan.artifact_sha256}",
                f"Artifact manifest: {plan.artifact_manifest_path}; SHA-256={plan.artifact_manifest_sha256}",
                f"Read-back: {plan.readback_status.value}; boot: {plan.boot_status.value}; write-enabled={plan.flash_write_enabled}; restore-enabled={plan.restore_write_enabled}",
            ))
        return tuple(rows)

    @staticmethod
    def _t430_session_summary(session: T430HandoffSession) -> tuple[str, ...]:
        assessment = session.assessment
        rows = [
            f"BIOS predicate: {assessment.decision.value}; numeric version: {assessment.numeric_bios}",
            f"Current device fingerprint: {session._device_fingerprint_sha256 or 'unknown'}",
            f"Current capture: {session._capture_id} at {session._observed_at.isoformat()}",
        ]
        preflight = session._preflight
        if preflight is not None:
            rows.append(
                f"Preflight: attested by {preflight.attested_by} at {preflight.attested_at.isoformat()} "
                f"for capture {preflight.capture_id}; AC={preflight.ac_connected}; "
                f"battery={preflight.battery_percent}%"
            )
            rows.append(
                "BIOS settings: passwords-removed=" + str(preflight.bios_passwords_removed)
                + "; end-user-update-enabled=" + str(preflight.end_user_bios_update_enabled)
                + "; secure-rollback-prevention-disabled=" + str(preflight.secure_rollback_prevention_disabled)
                + "; UEFI=" + str(preflight.uefi_booted)
                + "; Secure-Boot-disabled=" + str(preflight.secure_boot_disabled)
            )
            rows.append(
                "Selected 1vyrain media path: direct-onboard-USB/no-dock-or-hub="
                + str(preflight.will_boot_1vyrain_from_direct_onboard_usb_without_dock_or_hub)
            )
        else:
            rows.append("Preflight attestations: not recorded")
        media = session._media
        if media is not None:
            rows.extend((
                f"User-reported media image: {media.image_size_bytes} bytes; SHA-256 fingerprint {media.user_reported_image_sha256}; upstream MD5 reported {media.user_reported_image_md5 or 'not supplied'}",
                f"Media source: {media.image_source_url}; media fingerprint {media.user_reported_media_fingerprint_sha256}; DD mode={media.written_in_dd_mode}; selected by {media.selected_by} at {media.selected_at.isoformat()}",
                "Media authenticity: upstream authenticity is unverified; local SHA-256 is only a fingerprint.",
            ))
        else:
            rows.append("1vyrain media evidence: not recorded")
        rows.append(
            f"Explicit handoff approval: {session._approved_actor or 'not recorded'} at "
            f"{session._approval_timestamp.isoformat() if session._approval_timestamp else 'not recorded'}"
        )
        rows.append("No firmware write, successful installation, or boot test is asserted.")
        return tuple(rows)

    def verify_recorded_backups(self) -> ApplicationActionResult:
        """Verify checksums of locally recorded legacy pairs without hardware access."""
        try:
            if self.backup_manager is None:
                return ApplicationActionResult(
                    ApplicationAction.VERIFY_BACKUPS, ApplicationStatus.UNKNOWN,
                    "BACKUP_MANIFEST_NOT_CONFIGURED", "No local backup manifest was opened or created.",
                )
            manifest = self.backup_manager.get_manifest()
            if not manifest:
                return ApplicationActionResult(
                    ApplicationAction.VERIFY_BACKUPS, ApplicationStatus.UNKNOWN,
                    "NO_RECORDED_BACKUPS", "No backup pairs are recorded.",
                )
            valid = 0
            reasons: list[str] = []
            for item in manifest:
                first = Path(item.get("path1", ""))
                second = Path(item.get("path2", ""))
                if not first.is_file() or not second.is_file():
                    reasons.append(f"Recorded pair is missing: {item.get('timestamp', 'unknown timestamp')}")
                    continue
                matches, _ = self.backup_manager.verify_backup_pair(first, second)
                if matches:
                    valid += 1
                else:
                    reasons.append(f"Recorded pair differs: {first.name}, {second.name}")
            status = ApplicationStatus.READY if valid == len(manifest) else ApplicationStatus.BLOCKED
            return ApplicationActionResult(
                ApplicationAction.VERIFY_BACKUPS, status, "BACKUP_CHECKSUMS_VERIFIED" if status is ApplicationStatus.READY else "BACKUP_CHECKSUM_CHECK_FAILED",
                f"Verified {valid} of {len(manifest)} recorded backup pairs; this does not validate hardware topology or recovery readiness.",
                tuple(reasons),
            )
        except Exception as exc:
            return self._unavailable(ApplicationAction.VERIFY_BACKUPS, "BACKUP_MANIFEST_UNAVAILABLE", str(exc))

    def _unavailable(
        self,
        action: ApplicationAction,
        code: str,
        message: str,
        profile_id: str | None = None,
    ) -> ApplicationActionResult:
        refs: tuple[str, ...] = ()
        reasons: tuple[str, ...] = ()
        if profile_id in V1_PROFILES:
            profile = self.profile_status(profile_id)
            refs, reasons = profile.evidence_refs, profile.reasons
        return ApplicationActionResult(action, ApplicationStatus.NOT_PERFORMED, code, message, reasons, refs)

    @staticmethod
    def _safe_checkpoint_path(path: str | Path | None) -> bool:
        if path is None:
            return False
        candidate = Path(path).expanduser()
        if not candidate.is_absolute() or not candidate.is_file():
            return False
        absolute = Path(os.path.abspath(candidate))
        current = Path(absolute.anchor)
        for component in absolute.parts[1:]:
            current = current / component
            if current.is_symlink():
                return False
            is_junction = getattr(current, "is_junction", None)
            if callable(is_junction) and is_junction():
                return False
        return True

    @staticmethod
    def _canonical_model(device: DeviceEvidence) -> str | None:
        raw_values = (device.raw_dmi_product_name or "", device.raw_dmi_product_version or "", device.raw_dmi_system_family or "")
        found: set[str] = set()
        raw_joined = " ".join(raw_values).casefold()
        if re.search(r"(?<![a-z0-9])(?:t430s|x200s(?:i)?|x200\s+tablet(?:\s+pc)?|x220i)(?![a-z0-9])", raw_joined):
            return None
        for value in raw_values:
            folded = value.casefold()
            for model in ("t430", "x200", "x220"):
                if re.search(rf"(?<![a-z0-9]){model}(?![a-z0-9])", folded):
                    found.add(model)
        prefixes = set()
        if device.raw_mtm:
            prefixes.add(device.raw_mtm[:4].upper())
        by_mtm = {
            prefix: model
            for model, prefixes_for_model in (
                ("t430", V1_PROFILES["t430-1vyrain"].accepted_mtm_prefixes),
                ("x200", V1_PROFILES["x200-libreboot"].accepted_mtm_prefixes),
                ("x220", V1_PROFILES["x220-coreboot"].accepted_mtm_prefixes),
            )
            for prefix in prefixes_for_model
        }
        found.update(by_mtm[prefix] for prefix in prefixes if prefix in by_mtm)
        return next(iter(found)) if len(found) == 1 else None
