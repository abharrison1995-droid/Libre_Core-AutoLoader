"""Phase 10A write-intent binding and durable, one-use human approval.

This module does not execute a writer and does not advance an operation journal.
It prepares and consumes a narrowly scoped approval capability for a future
Phase 10B write boundary.  The actual writer must rebuild the intent from fresh
device/probe/backup evidence and consume the capability immediately before its
single fixed subprocess call.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import uuid
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, TextIO, cast

from autoloader.flasher.backup_service import BackupSetResult, BackupSetService
from autoloader.flasher.flashrom import FlashToolIdentity, ProgrammerType
from autoloader.flasher.recovery import RecoveryReadinessRecord, RecoveryReadinessService
from autoloader.flasher.spi import ChipProbeEvidence
from autoloader.workflow.authorization import AuthorizationError, AuthorizationStaleError, AuthorizationReusedError, compute_plan_digests
from autoloader.workflow.compatibility import CompatibilityValidator
from autoloader.workflow.engine import WorkflowSession
from autoloader.workflow.types import (
    BuildIntentEvidence,
    CompatibilityDecision,
    DeviceEvidence,
    OperationPlan,
    OperationState,
    PlanDigests,
    ProgrammerKind,
    RegionEvidence,
    normalized_path,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAC_ADDRESS = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$")
_APPROVAL_SCHEMA = 1
_CAPTURE_MAX_AGE = timedelta(minutes=5)
_CURRENT_EVIDENCE_MAX_AGE = timedelta(minutes=2)
_APPROVAL_MAX_AGE = timedelta(minutes=5)
_FRONTEND_CONFIRMATIONS: dict[str, "HumanActionConfirmation"] = {}
_FRONTEND_CONFIRMATIONS_LOCK = threading.Lock()
_TUI_RECEIPT_BRIDGES: dict[str, "_MountedTUIConfirmationBridge"] = {}
_TUI_BRIDGES: dict[str, "_MountedTUIConfirmationBridge"] = {}
_TUI_BRIDGES_LOCK = threading.Lock()
_FACTORY_INTENTS: dict[int, "WriteIntent"] = {}
_FACTORY_INTENTS_LOCK = threading.Lock()
_CONSUMED_APPROVALS: set[str] = set()
_CONSUMED_APPROVALS_LOCK = threading.Lock()


class WriteAction(str, Enum):
    FLASH = "flash"
    RESTORE = "restore"


class ConfirmationChannel(str, Enum):
    CLI_TTY = "cli-tty"
    TUI_EXPLICIT_ACTION = "tui-explicit-action"


class WriteApprovalError(AuthorizationError):
    """Write intent or human approval evidence was invalid or incomplete."""


@dataclass(frozen=True)
class DeviceCaptureEvidence:
    """A fresh local DMI capture; imported candidate evidence is not accepted."""

    capture_id: str
    observed_at_utc: str
    device: DeviceEvidence
    source: str = "local-dmi"
    device_fingerprint_sha256: str | None = None


@dataclass(frozen=True)
class FixedWriteCommand:
    """Canonical no-shell flashrom write command identity, not an execution."""

    executable: str
    executable_sha256: str
    tool_name: str
    tool_version: str
    argv: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "argv", tuple(self.argv))


@dataclass(frozen=True)
class BackupCopyBinding:
    chip_id: str
    capacity_bytes: int
    first_path: str
    first_sha256: str
    first_capture_id: str
    second_path: str
    second_sha256: str
    second_capture_id: str


@dataclass(frozen=True)
class WriteIntent:
    """Immutable fingerprint of all evidence that a later write may consume."""

    operation_id: str
    action: WriteAction
    profile_id: str
    model_id: str
    plan_digests: PlanDigests
    device_capture_id: str
    device_capture_at_utc: str
    device_evidence_sha256: str
    device_fingerprint_sha256: str
    probe_id: str
    probe_at_utc: str
    probe_output_sha256: str
    programmer: str
    chip_name: str
    chip_capacity_bytes: int
    chip_voltage_mv: int
    programmer_voltage_mv: int
    voltage_attested_by: str
    voltage_attested_at_utc: str
    voltage_instrument_id: str
    voltage_test_point: str
    artifact_path: str
    artifact_sha256: str
    artifact_size_bytes: int
    artifact_regions: tuple[RegionEvidence, ...]
    artifact_manifest_path: str
    artifact_manifest_sha256: str
    artifact_operation_id: str
    artifact_recipe_id: str
    artifact_source_repository_url: str
    artifact_source_revision: str
    artifact_source_tree_id: str
    artifact_selected_relative_path: str
    artifact_source_files: tuple[tuple[str, str], ...]
    artifact_toolchain: tuple[tuple[str, str], ...]
    artifact_command_argv: tuple[tuple[str, ...], ...]
    artifact_tool_fingerprints: tuple[tuple[str, str], ...]
    operation_journal_path: str
    operation_journal_state: str
    operation_journal_checksum: str
    backup_operation_id: str
    backup_copies: tuple[BackupCopyBinding, ...]
    primary_manifest_path: str
    primary_manifest_sha256: str
    recovery_manifest_path: str
    recovery_manifest_sha256: str
    primary_volume_id: str
    recovery_volume_id: str
    recovery_record_id: str
    recovery_ready_status: str
    recovery_write_enabled: bool
    recovery_hardware_tested: bool
    command: FixedWriteCommand
    created_at_utc: str
    intent_sha256: str
    mac_policy: str = "not-applicable"
    mac_source_artifact_sha256: str | None = None
    mac_preservation_manifest_path: str | None = None
    mac_preservation_manifest_sha256: str | None = None
    original_mac: str | None = None
    selected_mac_before: str | None = None
    final_mac: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_regions", tuple(self.artifact_regions))
        object.__setattr__(self, "artifact_source_files", tuple(tuple(item) for item in self.artifact_source_files))
        object.__setattr__(self, "artifact_toolchain", tuple(tuple(item) for item in self.artifact_toolchain))
        object.__setattr__(self, "artifact_command_argv", tuple(tuple(item) for item in self.artifact_command_argv))
        object.__setattr__(self, "artifact_tool_fingerprints", tuple(tuple(item) for item in self.artifact_tool_fingerprints))
        object.__setattr__(self, "backup_copies", tuple(self.backup_copies))

    def validate_integrity(self) -> None:
        """Reject hand-edited intent objects whose own fingerprint is stale."""
        if not _SHA256.fullmatch(self.intent_sha256):
            raise WriteApprovalError("write intent checksum is malformed")
        expected = _intent_checksum(self)
        if not hmac.compare_digest(expected, self.intent_sha256):
            raise WriteApprovalError("write intent checksum does not match its fields")
        _validate_intent_policy(self)


@dataclass(frozen=True)
class WriteApprovalChallenge:
    operation_id: str
    action: WriteAction
    model_id: str
    chip_name: str
    capacity_bytes: int
    artifact_sha256: str
    intent_sha256: str
    exact_phrase: str
    challenge_sha256: str
    executable: str
    argv: tuple[str, ...]
    artifact_path: str
    artifact_manifest_path: str
    artifact_manifest_sha256: str
    primary_manifest_path: str
    primary_manifest_sha256: str
    recovery_manifest_path: str
    recovery_manifest_sha256: str
    backup_copy_paths: tuple[str, ...]
    backup_copy_sha256: tuple[str, ...]
    setup_actor: str
    setup_instrument_id: str
    setup_test_point: str
    programmer_voltage_mv: int
    chip_voltage_mv: int
    setup_measured_at_utc: str
    interface_verified: bool
    wiring_verified: bool
    target_powered_off: bool
    ac_disconnected: bool
    removable_battery_removed: bool
    chip_pin1_orientation_verified: bool
    programmer_disconnect_before_repower: bool
    ec_version: str
    ec_evidence_ref: str
    ec_observed_at_utc: str
    ec_update_decision: str
    ec_waiver_reason: str
    mac_policy: str
    mac_source_artifact_sha256: str | None
    mac_preservation_manifest_path: str | None
    mac_preservation_manifest_sha256: str | None
    original_mac: str | None
    selected_mac_before: str | None
    final_mac: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "argv", tuple(self.argv))
        object.__setattr__(self, "backup_copy_paths", tuple(self.backup_copy_paths))
        object.__setattr__(self, "backup_copy_sha256", tuple(self.backup_copy_sha256))


class _MountedTUIConfirmationBridge:
    """One-shot service-worker bridge bound to one mounted confirmation screen.

    This is deliberately private and cannot be created from a generic event or
    caller callback. The only factory checks the exact dedicated Textual screen,
    current app screen identity and mount-session nonce.
    """

    def __init__(self, *, screen: Any, app: Any, mount_nonce: str, timeout_seconds: int = 300) -> None:
        self._screen = screen
        self._app = app
        self._mount_nonce = mount_nonce
        self._timeout_seconds = timeout_seconds
        self._bridge_id = secrets.token_urlsafe(32)
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._challenge: WriteApprovalChallenge | None = None
        self._actor: str | None = None
        self._confirmation: HumanActionConfirmation | None = None
        self._error: str | None = None
        self._closed = False
        self._receipt_consumed_flag = False

    def collect(self, challenge: WriteApprovalChallenge, actor: str) -> HumanActionConfirmation:
        _validate_challenge(challenge)
        if not actor.strip():
            raise WriteApprovalError("a named human operator is required for the TUI write challenge")
        with self._lock:
            if self._closed or self._challenge is not None:
                raise WriteApprovalError("mounted TUI challenge bridge is closed or already used")
            self._challenge = challenge
            self._actor = actor.strip()
        try:
            self._app.call_from_thread(
                self._screen._present_write_challenge,
                challenge,
                self,
                self._mount_nonce,
            )
        except Exception as exc:
            self.invalidate("The mounted confirmation screen is no longer available.")
            raise WriteApprovalError("could not present the write challenge on the mounted Textual screen") from exc
        if not self._event.wait(self._timeout_seconds):
            self.invalidate("The write challenge expired before confirmation.")
        with self._lock:
            if self._error is not None:
                self._unregister_if_unused()
                raise WriteApprovalError(self._error)
            if self._confirmation is None:
                self._unregister_if_unused()
                raise WriteApprovalError("TUI challenge ended without a one-use confirmation receipt")
            return self._confirmation

    def _confirm_from_screen(self, screen: Any, mount_nonce: str, phrase: str) -> None:
        with self._lock:
            self._require_current_screen(screen, mount_nonce)
            if self._event.is_set() or self._confirmation is not None:
                raise WriteApprovalError("TUI write confirmation was already submitted")
            challenge = self._challenge
            actor = self._actor
            if challenge is None or actor is None:
                raise WriteApprovalError("no live service-generated write challenge is awaiting confirmation")
            if not hmac.compare_digest(phrase, challenge.exact_phrase):
                raise WriteApprovalError("typed phrase does not exactly match the displayed service challenge")
            confirmation = _mint_mounted_tui_confirmation(
                self,
                challenge=challenge,
                actor=actor,
                confirmed_at=datetime.now(timezone.utc),
            )
            self._confirmation = confirmation
            self._event.set()

    def _cancel_from_screen(self, screen: Any, mount_nonce: str) -> None:
        with self._lock:
            self._require_current_screen(screen, mount_nonce, allow_unmounted=True)
            if self._receipt_consumed_flag:
                raise WriteApprovalError("confirmation receipt was consumed; cancellation is no longer available")
            if self._closed:
                return
            self._revoke_confirmation_locked()
            self._error = "The operator cancelled the write challenge; no approval was issued."
            self._closed = True
            self._event.set()
            self._unregister_if_unused()

    def invalidate(self, reason: str) -> None:
        with self._lock:
            if self._receipt_consumed_flag or self._closed:
                return
            self._revoke_confirmation_locked()
            self._closed = True
            self._error = reason
            self._event.set()
            self._unregister_if_unused()

    def _require_current_screen(self, screen: Any, mount_nonce: str, *, allow_unmounted: bool = False) -> None:
        if screen is not self._screen or mount_nonce != self._mount_nonce:
            raise WriteApprovalError("confirmation came from a different screen instance or mount session")
        if getattr(screen, "mount_nonce", None) != self._mount_nonce:
            raise WriteApprovalError("confirmation screen mount nonce changed")
        if not allow_unmounted and (
            not getattr(screen, "is_mounted", False)
            or getattr(self._app, "screen", None) is not screen
        ):
            raise WriteApprovalError("confirmation screen is not the currently mounted app screen")

    def _consume_confirmation(self, confirmation: "HumanActionConfirmation") -> None:
        """Atomically consume a TUI receipt or reject a cancelled/unmounted one."""
        with self._lock:
            if (
                self._closed
                or self._error is not None
                or self._confirmation is not confirmation
            ):
                raise WriteApprovalError("mounted TUI confirmation was cancelled, invalidated, or already consumed")
            with _FRONTEND_CONFIRMATIONS_LOCK:
                issued = _FRONTEND_CONFIRMATIONS.get(confirmation.evidence_id)
                bridge = _TUI_RECEIPT_BRIDGES.get(confirmation.evidence_id)
                if issued is not confirmation or bridge is not self:
                    raise WriteApprovalError("TUI confirmation is fabricated, revoked, or already used")
                _FRONTEND_CONFIRMATIONS.pop(confirmation.evidence_id, None)
                _TUI_RECEIPT_BRIDGES.pop(confirmation.evidence_id, None)
            self._receipt_consumed_flag = True
            self._closed = True
            with _TUI_BRIDGES_LOCK:
                _TUI_BRIDGES.pop(self._bridge_id, None)

    def _revoke_confirmation_locked(self) -> None:
        confirmation = self._confirmation
        if confirmation is None:
            return
        with _FRONTEND_CONFIRMATIONS_LOCK:
            if _FRONTEND_CONFIRMATIONS.get(confirmation.evidence_id) is confirmation:
                _FRONTEND_CONFIRMATIONS.pop(confirmation.evidence_id, None)
            if _TUI_RECEIPT_BRIDGES.get(confirmation.evidence_id) is self:
                _TUI_RECEIPT_BRIDGES.pop(confirmation.evidence_id, None)
        self._confirmation = None

    def _unregister_if_unused(self) -> None:
        with _TUI_BRIDGES_LOCK:
            _TUI_BRIDGES.pop(self._bridge_id, None)


def _create_mounted_tui_confirmation_bridge(screen: Any, *, mount_nonce: str) -> _MountedTUIConfirmationBridge:
    """Create a bridge only for the exact currently mounted confirmation screen."""
    from autoloader.ui.screens.write_confirmation import WriteConfirmationScreen

    if (
        type(screen) is not WriteConfirmationScreen
        or not mount_nonce
        or getattr(screen, "mount_nonce", None) != mount_nonce
        or not getattr(screen, "is_mounted", False)
    ):
        raise WriteApprovalError("TUI write approval requires the exact currently mounted confirmation screen")
    exact_screen = cast(WriteConfirmationScreen, screen)
    app = exact_screen.app
    if app is None or getattr(app, "screen", None) is not exact_screen:
        raise WriteApprovalError("TUI write approval requires the exact currently mounted confirmation screen")
    bridge = _MountedTUIConfirmationBridge(screen=exact_screen, app=app, mount_nonce=mount_nonce)
    with _TUI_BRIDGES_LOCK:
        _TUI_BRIDGES[bridge._bridge_id] = bridge
    return bridge


@dataclass(frozen=True)
class TUIConfirmationEvent:
    """Event payload produced only by the dedicated write-confirmation screen."""

    screen_id: str
    button_id: str
    event_id: str
    challenge_sha256: str
    occurred_at_utc: str


@dataclass(frozen=True)
class HumanActionConfirmation:
    """Typed frontend evidence; booleans and imported JSON are not accepted."""

    evidence_id: str
    challenge_sha256: str
    exact_phrase: str
    actor: str
    confirmed_at_utc: str
    channel: ConfirmationChannel

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise WriteApprovalError("confirmation evidence must be minted by the interactive CLI/TUI route")


@dataclass(frozen=True)
class WriteApprovalToken:
    """In-memory bearer capability; it is intentionally not checkpointed."""

    approval_id: str
    secret: str = field(repr=False)
    operation_id: str
    intent_sha256: str
    actor: str
    issued_at_utc: str
    channel: ConfirmationChannel
    challenge_sha256: str
    record_sha256: str

    def __reduce__(self) -> str | tuple[Any, ...]:
        raise TypeError("write approval tokens are process-local capabilities and cannot be serialized")


@dataclass(frozen=True)
class ConsumedWriteApproval:
    """One-use approval consumption receipt; it is not a write result."""

    approval_id: str
    operation_id: str
    intent_sha256: str
    consumed_at_utc: str


class WriteIntentFactory:
    """Reopen accepted software evidence and bind a fixed X200/X220 write plan."""

    def __init__(
        self,
        backup_service: BackupSetService,
        *,
        validator: CompatibilityValidator | None = None,
        recovery_service: RecoveryReadinessService | None = None,
        artifact_engine: Any | None = None,
    ) -> None:
        self.backup_service = backup_service
        self.validator = validator or CompatibilityValidator()
        self.recovery_service = recovery_service or RecoveryReadinessService(self.validator)
        if artifact_engine is None:
            from autoloader.rom_builder.artifact_engine import ArtifactEngine
            artifact_engine = ArtifactEngine()
        self.artifact_engine = artifact_engine

    def create(
        self,
        plan: OperationPlan,
        *,
        operation_id: str,
        capture: DeviceCaptureEvidence,
        probe: ChipProbeEvidence,
        backup_set: BackupSetResult,
        recovery_readiness: RecoveryReadinessRecord,
        operation_journal_path: str | Path,
        artifact_manifest_path: str | Path,
        action: WriteAction = WriteAction.FLASH,
        now_utc: datetime | None = None,
    ) -> WriteIntent:
        """Build an approval intent after independent, read-only revalidation.

        This method does no SPI or process I/O.  ``probe`` must have come from
        ``SPIProbeService``; production callers must collect it immediately
        before issuing a confirmation challenge and again before the write.
        """
        now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
        _require_uuid(operation_id, "operation")
        if action is not WriteAction.FLASH:
            raise WriteApprovalError("restore approval is a separate, not-yet-enabled operation")
        profile_models = {"x200-libreboot": "x200", "x220-coreboot": "x220"}
        expected_model = profile_models.get(plan.profile_id)
        if expected_model is None or plan.requested_model_id != expected_model or plan.artifact.model_id != expected_model:
            raise WriteApprovalError("only exact X200 Libreboot and X220 Coreboot plans can request external SPI approval")
        if plan.device.manual_model_candidate:
            raise WriteApprovalError("candidate-only or manually selected identity cannot authorize external SPI writes")
        if plan.programmer.kind is not ProgrammerKind.EXTERNAL_SPI:
            raise WriteApprovalError("internal or simulated programmers cannot enter the external SPI write path")
        if not plan.topology.observed or len(plan.topology.chips) != 1:
            raise WriteApprovalError("external SPI write intent requires exactly one observed chip")
        chip = plan.topology.chips[0]
        if not chip.observed:
            raise WriteApprovalError("external SPI target chip is not independently observed")
        if plan.artifact.expected_size_bytes != chip.capacity_bytes:
            raise WriteApprovalError("write intent requires a full-chip artifact with exact observed capacity")
        if plan.artifact.regions != plan.topology.regions:
            raise WriteApprovalError("artifact layout/regions do not match the observed chip topology")

        _validate_capture(capture, plan.device, expected_model, now)
        try:
            probe.validate_integrity()
        except Exception as exc:
            raise WriteApprovalError(f"fresh SPI probe evidence is invalid: {exc}") from exc
        probe_at = _parse_utc(probe.observed_at_utc, "SPI probe time")
        if probe_at > now + timedelta(seconds=30) or now - probe_at > _CAPTURE_MAX_AGE:
            raise WriteApprovalError("fresh SPI probe is stale or future-dated")
        if probe.programmer is ProgrammerType.INTERNAL:
            raise WriteApprovalError("internal programmer probes are excluded from the X200/X220 write path")
        if probe.capacity_bytes != chip.capacity_bytes:
            raise WriteApprovalError("fresh probe capacity differs from the exact plan topology")
        if not probe.chip_name.strip() or backup_set.programmer != probe.programmer.value:
            raise WriteApprovalError("fresh probe programmer differs from the verified original backup")
        if not backup_set.chips or len(backup_set.chips) != 1:
            raise WriteApprovalError("a complete verified one-chip backup set is required")
        if (
            backup_set.chips[0].chip_id != chip.chip_id
            or backup_set.chips[0].observed_chip_name != probe.chip_name
            or backup_set.chips[0].capacity_bytes != probe.capacity_bytes
        ):
            raise WriteApprovalError("fresh probe chip identity/capacity differs from the verified backup set")

        _validate_voltage_attestations(plan, now)
        compatibility = self.validator.validate(plan)
        backup_validation = self.validator.validate_backup(plan)
        if compatibility.decision is not CompatibilityDecision.ALLOWED:
            raise WriteApprovalError("operation plan is not allowed: " + "; ".join(compatibility.reasons))
        if backup_validation.decision is not CompatibilityDecision.ALLOWED:
            raise WriteApprovalError("complete original backup is not verified: " + "; ".join(backup_validation.reasons))
        if backup_set.backup != plan.backup or backup_set.profile_id != plan.profile_id:
            raise WriteApprovalError("verified backup result differs from the exact operation plan")

        try:
            # Original backups are captured against the immutable pre-build
            # intent (there was no firmware artifact yet). Re-open that exact
            # intent here; the final plan's backup binding is checked above
            # and again by the model workflow after the COMPLETE artifact is
            # reopened.
            from autoloader.workflow.x200_libreboot import create_x200_build_intent
            from autoloader.workflow.x220_coreboot import create_x220_build_intent
            if plan.profile_id == "x200-libreboot":
                backup_intent = create_x200_build_intent(
                    device=plan.device, topology=plan.topology, programmer=plan.programmer
                )
            elif plan.profile_id == "x220-coreboot":
                backup_intent = create_x220_build_intent(
                    device=plan.device, topology=plan.topology, programmer=plan.programmer
                )
            else:
                raise WriteApprovalError("backup belongs to an unsupported V1 profile")
            reopened = self.backup_service.load_verified_result(
                backup_intent,
                operation_id=backup_set.operation_id,
                primary_manifest=backup_set.primary_manifest,
                recovery_manifest=backup_set.recovery_manifest,
                primary_manifest_sha256=backup_set.primary_manifest_sha256,
                recovery_manifest_sha256=backup_set.recovery_manifest_sha256,
                expected_tool=probe.tool,
            )
        except Exception as exc:
            raise WriteApprovalError(f"persistent backup manifests/copies failed revalidation: {exc}") from exc
        differences = tuple(
            item.name for item in fields(BackupSetResult)
            if item.name != "created_at_utc"
            and getattr(reopened, item.name) != getattr(backup_set, item.name)
        )
        if differences:
            raise WriteApprovalError(
                "reopened backup result differs from the supplied verified backup set: "
                + ", ".join(differences)
            )
        _validate_recovery_record(self.recovery_service, backup_intent, reopened, recovery_readiness)

        accepted = self._reopen_accepted_workflow(
            plan,
            operation_id=operation_id,
            backup_set=reopened,
            journal_path=operation_journal_path,
            artifact_manifest_path=artifact_manifest_path,
        )
        artifact_result = accepted["artifact_result"]
        workflow_journal = accepted["journal"]
        manifest = artifact_result.manifest
        assert manifest is not None and artifact_result.manifest_path is not None
        selected_matches = [
            item for item in manifest.artifacts
            if item.relative_path == manifest.selected_artifact_relative_path
            and item.sha256 == manifest.selected_artifact_sha256
        ]
        if len(selected_matches) != 1:
            raise WriteApprovalError("reopened COMPLETE manifest does not identify exactly one selected artifact")
        selected_record = selected_matches[0]
        tool_fingerprints: list[tuple[str, str]] = []
        if selected_record.cbfs_inspection is not None:
            tool_fingerprints.append((selected_record.cbfs_inspection.tool_relative_path, selected_record.cbfs_inspection.tool_sha256))
        if selected_record.region_inspection_tool is not None:
            tool_fingerprints.append((selected_record.region_inspection_tool.relative_path, selected_record.region_inspection_tool.executable_sha256))

        digests = compute_plan_digests(plan)
        artifact_path = Path(plan.artifact.path)
        tool_path = Path(probe.tool.executable)
        if _has_link_component(artifact_path) or not artifact_path.is_file():
            raise WriteApprovalError("staged firmware artifact path is missing or crosses a link")
        if _has_link_component(tool_path) or not tool_path.is_file():
            raise WriteApprovalError("flashrom/flashprog executable path is missing or crosses a link")
        artifact_path = artifact_path.resolve(strict=True)
        tool_path = tool_path.resolve(strict=True)
        artifact_sha = _hash_file(artifact_path)
        if artifact_sha != plan.artifact.expected_sha256:
            raise WriteApprovalError("staged firmware artifact changed after validation")
        write_artifact_path = artifact_path
        write_artifact_sha = artifact_sha
        write_artifact_size = artifact_path.stat().st_size
        mac_evidence = None
        if plan.profile_id == "x200-libreboot":
            from autoloader.workflow.x200_mac import prepare_x200_preserved_mac_image

            chip_backup = reopened.chips[0]
            try:
                mac_evidence = prepare_x200_preserved_mac_image(
                    operation_id=operation_id,
                    selected_artifact_path=artifact_path,
                    selected_artifact_sha256=artifact_sha,
                    backup_first_path=chip_backup.first_path,
                    backup_first_sha256=chip_backup.first_sha256,
                    backup_second_path=chip_backup.second_path,
                    backup_second_sha256=chip_backup.second_sha256,
                )
            except Exception as exc:
                raise WriteApprovalError(f"X200 original GbE MAC could not be safely preserved: {exc}") from exc
            write_artifact_path = Path(mac_evidence.write_artifact_path)
            write_artifact_sha = mac_evidence.write_artifact_sha256
            write_artifact_size = mac_evidence.write_artifact_size_bytes
        tool_sha = _hash_file(tool_path)
        expected_argv = (
            "-p", probe.programmer.value,
            "-c", probe.chip_name,
            "-w", str(write_artifact_path),
        )
        command = FixedWriteCommand(
            executable=str(tool_path),
            executable_sha256=tool_sha,
            tool_name=probe.tool.name,
            tool_version=probe.tool.version,
            argv=expected_argv,
        )
        backup_pairs = tuple(
            BackupCopyBinding(
                chip_id=record.chip_id,
                capacity_bytes=record.capacity_bytes,
                first_path=normalized_path(record.first_path),
                first_sha256=record.first_sha256,
                first_capture_id=record.first_capture_id,
                second_path=normalized_path(record.second_path),
                second_sha256=record.second_sha256,
                second_capture_id=record.second_capture_id,
            )
            for record in reopened.chips
        )
        intent = WriteIntent(
            operation_id=operation_id,
            action=action,
            profile_id=plan.profile_id,
            model_id=expected_model,
            plan_digests=digests,
            device_capture_id=capture.capture_id,
            device_capture_at_utc=capture.observed_at_utc,
            device_evidence_sha256=_sha256(_json_bytes(plan.device)),
            device_fingerprint_sha256=capture.device_fingerprint_sha256 or "",
            probe_id=probe.probe_id,
            probe_at_utc=probe.observed_at_utc,
            probe_output_sha256=probe.raw_output_sha256,
            programmer=probe.programmer.value,
            chip_name=probe.chip_name,
            chip_capacity_bytes=probe.capacity_bytes,
            chip_voltage_mv=chip.voltage_mv or 0,
            programmer_voltage_mv=plan.programmer.voltage_mv or 0,
            voltage_attested_by=plan.programmer.attested_by or "",
            voltage_attested_at_utc=plan.programmer.attested_at or "",
            voltage_instrument_id=plan.programmer.instrument_id or "",
            voltage_test_point=plan.programmer.measurement_test_point or "",
            artifact_path=str(write_artifact_path),
            artifact_sha256=write_artifact_sha,
            artifact_size_bytes=write_artifact_size,
            artifact_regions=plan.artifact.regions,
            artifact_manifest_path=normalized_path(str(artifact_result.manifest_path.resolve(strict=True))),
            artifact_manifest_sha256=_hash_file(artifact_result.manifest_path),
            artifact_operation_id=manifest.operation_id,
            artifact_recipe_id=manifest.recipe_id,
            artifact_source_repository_url=manifest.source_repository_url,
            artifact_source_revision=manifest.source_revision,
            artifact_source_tree_id=manifest.source_tree_id,
            artifact_selected_relative_path=manifest.selected_artifact_relative_path or "",
            artifact_source_files=manifest.source_files,
            artifact_toolchain=manifest.toolchain,
            artifact_command_argv=tuple(command.argv for command in manifest.commands),
            artifact_tool_fingerprints=tuple(tool_fingerprints),
            operation_journal_path=normalized_path(str(workflow_journal.path)),
            operation_journal_state=workflow_journal.state.value,
            operation_journal_checksum=_hash_file(workflow_journal.path),
            backup_operation_id=reopened.operation_id,
            backup_copies=backup_pairs,
            primary_manifest_path=normalized_path(reopened.primary_manifest),
            primary_manifest_sha256=reopened.primary_manifest_sha256,
            recovery_manifest_path=normalized_path(reopened.recovery_manifest),
            recovery_manifest_sha256=reopened.recovery_manifest_sha256,
            primary_volume_id=reopened.primary_storage.volume_id,
            recovery_volume_id=reopened.recovery_storage.volume_id,
            recovery_record_id=recovery_readiness.record_id,
            recovery_ready_status=recovery_readiness.status,
            recovery_write_enabled=recovery_readiness.recovery_write_enabled,
            recovery_hardware_tested=recovery_readiness.hardware_recovery_tested,
            command=command,
            created_at_utc=now.isoformat(),
            intent_sha256="",
            mac_policy=mac_evidence.policy if mac_evidence is not None else "not-applicable",
            mac_source_artifact_sha256=mac_evidence.source_artifact_sha256 if mac_evidence is not None else None,
            mac_preservation_manifest_path=mac_evidence.manifest_path if mac_evidence is not None else None,
            mac_preservation_manifest_sha256=mac_evidence.manifest_sha256 if mac_evidence is not None else None,
            original_mac=mac_evidence.original_mac if mac_evidence is not None else None,
            selected_mac_before=mac_evidence.selected_mac_before if mac_evidence is not None else None,
            final_mac=mac_evidence.selected_mac_after if mac_evidence is not None else None,
        )
        intent = _with_intent_checksum(intent)
        intent.validate_integrity()
        with _FACTORY_INTENTS_LOCK:
            _FACTORY_INTENTS[id(intent)] = intent
        return intent

    def _reopen_accepted_workflow(
        self,
        plan: OperationPlan,
        *,
        operation_id: str,
        backup_set: BackupSetResult,
        journal_path: str | Path,
        artifact_manifest_path: str | Path,
    ) -> dict[str, Any]:
        """Reconstruct the accepted model workflow from durable evidence.

        A caller-supplied plan, DMI record and probe are not sufficient. The
        exact pre-write journal and ArtifactEngine manifest are reread, then
        passed through the corresponding model workflow's COMPLETE-result
        validator before an intent is created.
        """
        from autoloader.workflow.x200_libreboot import X200LibrebootWorkflow, create_x200_build_intent
        from autoloader.workflow.x220_coreboot import X220CorebootWorkflow, create_x220_build_intent

        checkpoint = Path(journal_path).expanduser()
        manifest_path = Path(artifact_manifest_path).expanduser()
        if (
            _has_link_component(checkpoint) or not checkpoint.is_file()
            or _has_link_component(manifest_path) or not manifest_path.is_file()
        ):
            raise WriteApprovalError("accepted operation journal and ArtifactEngine manifest must be existing, link-free files")
        try:
            checkpoint = checkpoint.resolve(strict=True)
            manifest_path = manifest_path.resolve(strict=True)
            session = WorkflowSession.resume(plan, checkpoint, validator=self.validator)
            journal = session.journal
        except Exception as exc:
            raise WriteApprovalError(f"accepted pre-write workflow checkpoint failed revalidation: {exc}") from exc
        if journal.state is not OperationState.BACKUP_VERIFIED:
            raise WriteApprovalError("accepted workflow checkpoint must be exactly BACKUP_VERIFIED before write approval")
        initial_checkpoint_sha256 = _hash_file(checkpoint)
        if journal.operation_id != operation_id or journal.path != checkpoint:
            raise WriteApprovalError("operation ID/path does not match the accepted workflow checkpoint")
        if (
            not journal.entries
            or journal.entries[-1].to_state is not OperationState.BACKUP_VERIFIED
            or journal.entries[-1].action != "verify-backup"
            or journal.entries[-1].code != "BACKUP_VERIFIED"
            or journal.entries[-1].failure
        ):
            raise WriteApprovalError("accepted workflow checkpoint lacks the final successful backup-verification event")
        try:
            artifact_result = self.artifact_engine.load_completed_result(manifest_path)
        except Exception as exc:
            raise WriteApprovalError(f"persisted ArtifactEngine COMPLETE manifest failed to reopen: {exc}") from exc

        try:
            if plan.profile_id == "x200-libreboot":
                from autoloader.workflow.x200_libreboot import X200WorkflowStage
                build_intent = create_x200_build_intent(
                    device=plan.device, topology=plan.topology, programmer=plan.programmer
                )
                workflow: Any = X200LibrebootWorkflow(
                    build_intent, validator=self.validator, journal_path=checkpoint
                )
                validation = workflow.validate_build_intent()
                if validation.decision is not CompatibilityDecision.ALLOWED:
                    raise ValueError("X200 recipe intent is no longer allowed")
                workflow.accept_backup(backup_set)
                accepted_result = workflow.accept_artifact_result(artifact_result)
                if accepted_result.stage is not X200WorkflowStage.STAGED:
                    raise ValueError("X200 durable workflow did not reopen to STAGED")
            elif plan.profile_id == "x220-coreboot":
                from autoloader.workflow.x220_coreboot import X220WorkflowStage
                build_intent = create_x220_build_intent(
                    device=plan.device, topology=plan.topology, programmer=plan.programmer
                )
                workflow = X220CorebootWorkflow(
                    build_intent, validator=self.validator, journal_path=checkpoint
                )
                validation = workflow.validate_build_intent()
                if validation.decision is not CompatibilityDecision.ALLOWED:
                    raise ValueError("X220 recipe intent is no longer allowed")
                workflow.accept_backup(backup_set)
                workflow._cpu_count = _manifest_cpu_count(artifact_result.manifest)
                accepted_result = workflow.accept_artifact_result(artifact_result)
                if accepted_result.stage is not X220WorkflowStage.STAGED:
                    raise ValueError("X220 durable workflow did not reopen to STAGED")
            else:
                raise ValueError("profile is not an accepted external-SPI V1 profile")
        except Exception as exc:
            raise WriteApprovalError(f"accepted model workflow/artifact evidence failed revalidation: {exc}") from exc

        if (
            accepted_result.operation_plan != plan
            or accepted_result.operation_id != operation_id
            or workflow.operation_journal_path is None
            or Path(workflow.operation_journal_path).resolve(strict=True) != checkpoint
            or artifact_result.manifest_path is None
            or artifact_result.manifest_path.resolve(strict=True) != manifest_path
        ):
            raise WriteApprovalError("reopened accepted workflow does not bind the exact plan, journal and manifest paths")
        final_session = WorkflowSession.resume(plan, checkpoint, validator=self.validator)
        final_journal = final_session.journal
        if final_journal.state is not OperationState.BACKUP_VERIFIED or _hash_file(checkpoint) != initial_checkpoint_sha256:
            raise WriteApprovalError("workflow checkpoint changed during artifact acceptance")
        return {"artifact_result": artifact_result, "journal": final_journal}


class WriteApprovalLedger:
    """Durable single-use approval ledger using exclusive filesystem claims.

    Token state is intentionally not added to ``OperationJournal`` or returned
    by a JSON loader. An exclusive marker created with ``O_EXCL`` provides
    cross-process single consumption. A crash after the marker is written is
    fail-closed: the approval is consumed and cannot be resumed or replayed.

    Checksums detect accidental corruption; they do not authenticate records
    against a malicious process running as the same local account. A local
    administrator/account owner can alter code or roll back local files. Phase
    10B must re-open all workflow, artifact and backup evidence immediately
    before consuming an approval. Approval issuance is unavailable on
    platforms without durable directory fsync; this implementation currently
    marks Windows unavailable rather than assuming file fsync is enough.
    """

    def __init__(self, directory: str | Path) -> None:
        if not _directory_fsync_supported():
            raise WriteApprovalError("durable write approvals are unavailable: platform directory fsync is unsupported")
        self.directory = Path(directory).expanduser().absolute()
        self.directory.mkdir(parents=True, exist_ok=True)
        if _has_link_component(self.directory):
            raise WriteApprovalError("approval ledger directory must not contain a symlink or junction")
        self._lock = threading.Lock()

    def issue(
        self,
        intent: WriteIntent,
        confirmation: HumanActionConfirmation,
        *,
        challenge: WriteApprovalChallenge | None = None,
    ) -> WriteApprovalToken:
        if type(intent) is not WriteIntent or type(confirmation) is not HumanActionConfirmation:
            raise WriteApprovalError("a typed write intent and frontend human-confirmation record are required")
        if challenge is None or type(challenge) is not WriteApprovalChallenge:
            raise WriteApprovalError("the exact displayed write challenge must be supplied to issue approval")
        if confirmation.channel not in {ConfirmationChannel.CLI_TTY, ConfirmationChannel.TUI_EXPLICIT_ACTION}:
            raise WriteApprovalError("write approval requires a reviewed interactive frontend receipt")
        _require_factory_intent(intent)
        _consume_frontend_confirmation(confirmation)
        intent.validate_integrity()
        if intent.action is not WriteAction.FLASH:
            raise WriteApprovalError("restore requires its own separately reviewed approval path")
        intent_time = _parse_utc(intent.created_at_utc, "write intent time")
        now = datetime.now(timezone.utc)
        if intent_time > now + timedelta(seconds=30) or now - intent_time > _APPROVAL_MAX_AGE:
            raise WriteApprovalError("write intent is future-dated or expired")
        selected_challenge = challenge
        _validate_challenge_against_intent(intent, selected_challenge)
        if (
            confirmation.challenge_sha256 != selected_challenge.challenge_sha256
            or confirmation.exact_phrase != selected_challenge.exact_phrase
            or not confirmation.actor.strip()
        ):
            raise WriteApprovalError("human confirmation does not match this exact operation challenge")
        confirmed_at = _parse_utc(confirmation.confirmed_at_utc, "confirmation time")
        if confirmed_at > now + timedelta(seconds=30) or now - confirmed_at > _APPROVAL_MAX_AGE:
            raise WriteApprovalError("human confirmation is future-dated or expired")

        approval_id = str(uuid.uuid4())
        token_secret = secrets.token_urlsafe(32)
        token_payload = {
            "schema_version": _APPROVAL_SCHEMA,
            "approval_id": approval_id,
            "token_secret_sha256": _sha256(token_secret.encode("utf-8")),
            "operation_id": intent.operation_id,
            "intent_sha256": intent.intent_sha256,
            "actor": confirmation.actor.strip(),
            "issued_at_utc": now.isoformat(),
            "channel": confirmation.channel.value,
            "challenge_sha256": selected_challenge.challenge_sha256,
        }
        document = {**token_payload, "record_sha256": _sha256(_json_bytes(token_payload))}
        record_bytes = _json_bytes(document)
        record_sha256 = str(document["record_sha256"])
        with self._lock:
            confirmation_path = self.directory / f"{selected_challenge.challenge_sha256}.confirmed"
            _write_exclusive(confirmation_path, _json_bytes({"challenge_sha256": selected_challenge.challenge_sha256}))
            record_path = self._record_path(approval_id)
            _write_exclusive(record_path, record_bytes)
        return WriteApprovalToken(
            approval_id=approval_id,
            secret=token_secret,
            operation_id=intent.operation_id,
            intent_sha256=intent.intent_sha256,
            actor=confirmation.actor.strip(),
            issued_at_utc=now.isoformat(),
            channel=confirmation.channel,
            challenge_sha256=selected_challenge.challenge_sha256,
            record_sha256=record_sha256,
        )

    def consume(self, token: WriteApprovalToken, current_intent: WriteIntent) -> ConsumedWriteApproval:
        """Atomically consume once; this returns no writer capability or result."""
        if type(token) is not WriteApprovalToken or type(current_intent) is not WriteIntent:
            raise WriteApprovalError("only a typed in-memory approval and current intent can be consumed")
        _require_factory_intent(current_intent)
        current_intent.validate_integrity()
        record_path = self._record_path(token.approval_id)
        record = self._read_record(record_path)
        if not _token_matches_record(token, record):
            raise WriteApprovalError("approval ledger record does not match the in-memory token")
        consumed_path = self.directory / f"{token.approval_id}.consumed"
        marker = {
            "approval_id": token.approval_id,
            "operation_id": token.operation_id,
            "intent_sha256": token.intent_sha256,
            "consumed_at_utc": datetime.now(timezone.utc).isoformat(),
            "record_sha256": token.record_sha256,
        }
        with _CONSUMED_APPROVALS_LOCK:
            if token.approval_id in _CONSUMED_APPROVALS:
                raise AuthorizationReusedError("write approval has already been consumed in this process")
            try:
                _write_exclusive(consumed_path, _json_bytes(marker))
            except FileExistsError as exc:
                raise AuthorizationReusedError("write approval has already been consumed") from exc
            _CONSUMED_APPROVALS.add(token.approval_id)
        if (
            token.operation_id != current_intent.operation_id
            or current_intent.intent_sha256 != token.intent_sha256
        ):
            raise AuthorizationStaleError("device, artifact, backup, programmer, voltage, or command changed after approval")
        issued_at = _parse_utc(token.issued_at_utc, "approval issue time")
        consumed_at = _parse_utc(marker["consumed_at_utc"], "approval consume time")
        if consumed_at - issued_at > _APPROVAL_MAX_AGE or issued_at > consumed_at + timedelta(seconds=30):
            raise AuthorizationStaleError("write approval is expired or future-dated")
        return ConsumedWriteApproval(
            approval_id=token.approval_id,
            operation_id=token.operation_id,
            intent_sha256=token.intent_sha256,
            consumed_at_utc=marker["consumed_at_utc"],
        )

    def _record_path(self, approval_id: str) -> Path:
        _require_uuid(approval_id, "approval")
        return self.directory / f"{approval_id}.approval.json"

    def _read_record(self, path: Path) -> dict[str, object]:
        if _has_link_component(path) or not path.is_file():
            raise WriteApprovalError("approval ledger record is missing or unsafe")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WriteApprovalError("approval ledger record is unreadable") from exc
        if not isinstance(document, dict):
            raise WriteApprovalError("approval ledger record must be a JSON object")
        digest = document.get("record_sha256")
        payload = {key: value for key, value in document.items() if key != "record_sha256"}
        if not isinstance(digest, str) or not hmac.compare_digest(digest, _sha256(_json_bytes(payload))):
            raise WriteApprovalError("approval ledger record checksum is invalid")
        if document.get("schema_version") != _APPROVAL_SCHEMA:
            raise WriteApprovalError("approval ledger schema is unsupported")
        return document


def create_write_approval_challenge(intent: WriteIntent, *, setup_attestation: Any | None = None) -> WriteApprovalChallenge:
    _require_factory_intent(intent)
    intent.validate_integrity()
    phrase = f"FLASH {intent.model_id.upper()} {intent.operation_id} {intent.intent_sha256[:12].upper()}"
    if setup_attestation is None:
        setup_actor = intent.voltage_attested_by
        setup_instrument_id = intent.voltage_instrument_id
        setup_test_point = intent.voltage_test_point
        programmer_voltage_mv = intent.programmer_voltage_mv
        chip_voltage_mv = intent.chip_voltage_mv
        setup_measured_at_utc = intent.voltage_attested_at_utc
        interface_verified = True
        wiring_verified = True
        target_powered_off = False
        ac_disconnected = False
        removable_battery_removed = False
        chip_pin1_orientation_verified = False
        programmer_disconnect_before_repower = False
        ec_version = ""
        ec_evidence_ref = ""
        ec_observed_at_utc = ""
        ec_update_decision = ""
        ec_waiver_reason = ""
    else:
        setup_actor = setup_attestation.actor
        setup_instrument_id = setup_attestation.instrument_id
        setup_test_point = setup_attestation.test_point
        programmer_voltage_mv = setup_attestation.programmer_voltage_mv
        chip_voltage_mv = setup_attestation.chip_voltage_mv
        setup_measured_at_utc = setup_attestation.measured_at_utc
        interface_verified = setup_attestation.interface_verified
        wiring_verified = setup_attestation.wiring_verified
    target_powered_off = bool(getattr(setup_attestation, "target_powered_off", False))
    ac_disconnected = bool(getattr(setup_attestation, "ac_disconnected", False))
    removable_battery_removed = bool(getattr(setup_attestation, "removable_battery_removed", False))
    chip_pin1_orientation_verified = bool(getattr(setup_attestation, "chip_pin1_orientation_verified", False))
    programmer_disconnect_before_repower = bool(getattr(setup_attestation, "programmer_disconnect_before_repower", False))
    ec_version = str(getattr(setup_attestation, "ec_version", ""))
    ec_evidence_ref = str(getattr(setup_attestation, "ec_evidence_ref", ""))
    ec_observed_at_utc = str(getattr(setup_attestation, "ec_observed_at_utc", ""))
    ec_update_decision = str(getattr(setup_attestation, "ec_update_decision", ""))
    ec_waiver_reason = str(getattr(setup_attestation, "ec_waiver_reason", ""))
    backup_copy_paths: tuple[str, ...] = tuple(
        path for copy in intent.backup_copies for path in (copy.first_path, copy.second_path)
    )
    backup_copy_sha256: tuple[str, ...] = tuple(
        digest for copy in intent.backup_copies for digest in (copy.first_sha256, copy.second_sha256)
    )
    challenge_data = {
        "operation_id": intent.operation_id,
        "action": intent.action.value,
        "model_id": intent.model_id,
        "chip_name": intent.chip_name,
        "capacity_bytes": intent.chip_capacity_bytes,
        "artifact_sha256": intent.artifact_sha256,
        "intent_sha256": intent.intent_sha256,
        "exact_phrase": phrase,
        "executable": intent.command.executable,
        "argv": intent.command.argv,
        "artifact_path": intent.artifact_path,
        "artifact_manifest_path": intent.artifact_manifest_path,
        "artifact_manifest_sha256": intent.artifact_manifest_sha256,
        "primary_manifest_path": intent.primary_manifest_path,
        "primary_manifest_sha256": intent.primary_manifest_sha256,
        "recovery_manifest_path": intent.recovery_manifest_path,
        "recovery_manifest_sha256": intent.recovery_manifest_sha256,
        "backup_copy_paths": backup_copy_paths,
        "backup_copy_sha256": backup_copy_sha256,
        "setup_actor": setup_actor,
        "setup_instrument_id": setup_instrument_id,
        "setup_test_point": setup_test_point,
        "programmer_voltage_mv": programmer_voltage_mv,
        "chip_voltage_mv": chip_voltage_mv,
        "setup_measured_at_utc": setup_measured_at_utc,
        "interface_verified": interface_verified,
        "wiring_verified": wiring_verified,
        "target_powered_off": target_powered_off,
        "ac_disconnected": ac_disconnected,
        "removable_battery_removed": removable_battery_removed,
        "chip_pin1_orientation_verified": chip_pin1_orientation_verified,
        "programmer_disconnect_before_repower": programmer_disconnect_before_repower,
        "ec_version": ec_version,
        "ec_evidence_ref": ec_evidence_ref,
        "ec_observed_at_utc": ec_observed_at_utc,
        "ec_update_decision": ec_update_decision,
        "ec_waiver_reason": ec_waiver_reason,
        "mac_policy": intent.mac_policy,
        "mac_source_artifact_sha256": intent.mac_source_artifact_sha256,
        "mac_preservation_manifest_path": intent.mac_preservation_manifest_path,
        "mac_preservation_manifest_sha256": intent.mac_preservation_manifest_sha256,
        "original_mac": intent.original_mac,
        "selected_mac_before": intent.selected_mac_before,
        "final_mac": intent.final_mac,
    }
    challenge_sha = _sha256(_json_bytes(challenge_data))
    return WriteApprovalChallenge(
        operation_id=intent.operation_id,
        action=intent.action,
        model_id=intent.model_id,
        chip_name=intent.chip_name,
        capacity_bytes=intent.chip_capacity_bytes,
        artifact_sha256=intent.artifact_sha256,
        intent_sha256=intent.intent_sha256,
        exact_phrase=phrase,
        challenge_sha256=challenge_sha,
        executable=intent.command.executable,
        argv=intent.command.argv,
        artifact_path=intent.artifact_path,
        artifact_manifest_path=intent.artifact_manifest_path,
        artifact_manifest_sha256=intent.artifact_manifest_sha256,
        primary_manifest_path=intent.primary_manifest_path,
        primary_manifest_sha256=intent.primary_manifest_sha256,
        recovery_manifest_path=intent.recovery_manifest_path,
        recovery_manifest_sha256=intent.recovery_manifest_sha256,
        backup_copy_paths=backup_copy_paths,
        backup_copy_sha256=backup_copy_sha256,
        setup_actor=setup_actor,
        setup_instrument_id=setup_instrument_id,
        setup_test_point=setup_test_point,
        programmer_voltage_mv=programmer_voltage_mv,
        chip_voltage_mv=chip_voltage_mv,
        setup_measured_at_utc=setup_measured_at_utc,
        interface_verified=interface_verified,
        wiring_verified=wiring_verified,
        target_powered_off=target_powered_off,
        ac_disconnected=ac_disconnected,
        removable_battery_removed=removable_battery_removed,
        chip_pin1_orientation_verified=chip_pin1_orientation_verified,
        programmer_disconnect_before_repower=programmer_disconnect_before_repower,
        ec_version=ec_version,
        ec_evidence_ref=ec_evidence_ref,
        ec_observed_at_utc=ec_observed_at_utc,
        ec_update_decision=ec_update_decision,
        ec_waiver_reason=ec_waiver_reason,
        mac_policy=intent.mac_policy,
        mac_source_artifact_sha256=intent.mac_source_artifact_sha256,
        mac_preservation_manifest_path=intent.mac_preservation_manifest_path,
        mac_preservation_manifest_sha256=intent.mac_preservation_manifest_sha256,
        original_mac=intent.original_mac,
        selected_mac_before=intent.selected_mac_before,
        final_mac=intent.final_mac,
    )


def collect_cli_confirmation(
    challenge: WriteApprovalChallenge,
    *,
    actor: str,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> HumanActionConfirmation:
    """Collect exact confirmation only from real process stdin/stdout TTYs.

    The optional stream arguments exist only to reject compatibility callers
    explicitly; production approval can never be redirected to an arbitrary
    object whose Python ``isatty`` method claims to be a terminal.
    """
    selected_input = sys.stdin if input_stream is None else input_stream
    selected_output = sys.stdout if output_stream is None else output_stream
    if selected_input is not sys.stdin or selected_output is not sys.stdout:
        raise WriteApprovalError("approval requires the real process stdin/stdout file descriptors")
    input_fd = _require_tty_file_descriptor(selected_input, "input")
    output_fd = _require_tty_file_descriptor(selected_output, "output")
    if not os.isatty(input_fd):
        raise WriteApprovalError("interactive OS TTY input is required; non-TTY approval is disabled")
    if not os.isatty(output_fd):
        raise WriteApprovalError("interactive OS TTY output is required to display the irreversible action")
    _validate_challenge(challenge)
    mac_line = (
        f"X200 GbE MAC: source artifact SHA-256={challenge.mac_source_artifact_sha256}; "
        f"original={challenge.original_mac}; selected ROM before preservation={challenge.selected_mac_before}; "
        f"approved final={challenge.final_mac}; preservation manifest={challenge.mac_preservation_manifest_path} "
        f"(SHA-256 {challenge.mac_preservation_manifest_sha256}).\n"
        if challenge.model_id == "x200" else ""
    )
    selected_output.write(
        "PHYSICAL FIRMWARE WRITE: "
        f"{challenge.model_id.upper()} chip {challenge.chip_name} ({challenge.capacity_bytes} bytes), "
        f"operation {challenge.operation_id}, artifact SHA-256 {challenge.artifact_sha256}.\n"
        f"Power/setup attestations: target off={challenge.target_powered_off}, AC disconnected={challenge.ac_disconnected}, "
        f"removable battery removed={challenge.removable_battery_removed}, pin-1 checked={challenge.chip_pin1_orientation_verified}, "
        f"programmer disconnect before repower={challenge.programmer_disconnect_before_repower}.\n"
        f"EC decision={challenge.ec_update_decision}; version={challenge.ec_version or 'not supplied'}; "
        f"evidence={challenge.ec_evidence_ref or 'not supplied'}; observed={challenge.ec_observed_at_utc or 'n/a'}; "
        f"waiver={challenge.ec_waiver_reason or 'none'}; waiver recorded at setup time {challenge.setup_measured_at_utc}. "
        "EC updates are not automated.\n"
        + mac_line
        + f"Type exactly: {challenge.exact_phrase}\n> "
    )
    selected_output.flush()
    typed = selected_input.readline().rstrip("\r\n")
    if not hmac.compare_digest(typed, challenge.exact_phrase):
        raise WriteApprovalError("exact write challenge was not entered; no approval was issued")
    return _mint_frontend_confirmation(
        challenge=challenge,
        actor=actor,
        confirmed_at=datetime.now(timezone.utc),
        channel=ConfirmationChannel.CLI_TTY,
    )


def accept_tui_confirmation(
    challenge: WriteApprovalChallenge,
    *,
    actor: str,
    event: TUIConfirmationEvent,
) -> HumanActionConfirmation:
    """Reject generic event payloads; only the mounted-screen bridge can mint a receipt."""
    del challenge, actor, event
    raise WriteApprovalError("TUI approval requires the live dedicated screen bridge; event payloads cannot mint receipts")


def _mint_frontend_confirmation(
    *, challenge: WriteApprovalChallenge, actor: str,
    confirmed_at: datetime, channel: ConfirmationChannel,
) -> HumanActionConfirmation:
    """Create a one-use in-process receipt for a checked frontend interaction."""
    if channel is not ConfirmationChannel.CLI_TTY:
        raise WriteApprovalError("only the OS-verified CLI TTY frontend can mint approval evidence")
    if not actor.strip():
        raise WriteApprovalError("human approval actor must be recorded")
    confirmation = object.__new__(HumanActionConfirmation)
    evidence_id = secrets.token_urlsafe(32)
    values = {
        "evidence_id": evidence_id,
        "challenge_sha256": challenge.challenge_sha256,
        "exact_phrase": challenge.exact_phrase,
        "actor": actor.strip(),
        "confirmed_at_utc": confirmed_at.astimezone(timezone.utc).isoformat(),
        "channel": channel,
    }
    for name, value in values.items():
        object.__setattr__(confirmation, name, value)
    with _FRONTEND_CONFIRMATIONS_LOCK:
        _FRONTEND_CONFIRMATIONS[evidence_id] = confirmation
    return confirmation


def _mint_mounted_tui_confirmation(
    bridge: _MountedTUIConfirmationBridge,
    *,
    challenge: WriteApprovalChallenge,
    actor: str,
    confirmed_at: datetime,
) -> HumanActionConfirmation:
    if type(bridge) is not _MountedTUIConfirmationBridge:
        raise WriteApprovalError("TUI receipt requires the private mounted-screen bridge")
    with _TUI_BRIDGES_LOCK:
        if _TUI_BRIDGES.get(bridge._bridge_id) is not bridge:
            raise WriteApprovalError("TUI screen bridge is not live or was already invalidated")
    _validate_challenge(challenge)
    if not actor.strip() or not hmac.compare_digest(challenge.exact_phrase, f"FLASH {challenge.model_id.upper()} {challenge.operation_id} {challenge.intent_sha256[:12].upper()}"):
        raise WriteApprovalError("mounted TUI challenge is malformed")
    confirmation = object.__new__(HumanActionConfirmation)
    evidence_id = secrets.token_urlsafe(32)
    values = {
        "evidence_id": evidence_id,
        "challenge_sha256": challenge.challenge_sha256,
        "exact_phrase": challenge.exact_phrase,
        "actor": actor.strip(),
        "confirmed_at_utc": confirmed_at.astimezone(timezone.utc).isoformat(),
        "channel": ConfirmationChannel.TUI_EXPLICIT_ACTION,
    }
    for name, value in values.items():
        object.__setattr__(confirmation, name, value)
    with _FRONTEND_CONFIRMATIONS_LOCK:
        with _TUI_BRIDGES_LOCK:
            if _TUI_BRIDGES.get(bridge._bridge_id) is not bridge:
                raise WriteApprovalError("TUI screen bridge expired before receipt issuance")
            _FRONTEND_CONFIRMATIONS[evidence_id] = confirmation
            _TUI_RECEIPT_BRIDGES[evidence_id] = bridge
    return confirmation


def _consume_frontend_confirmation(confirmation: HumanActionConfirmation) -> None:
    evidence_id = getattr(confirmation, "evidence_id", None)
    if not isinstance(evidence_id, str):
        raise WriteApprovalError("confirmation is not a live frontend interaction receipt")
    with _FRONTEND_CONFIRMATIONS_LOCK:
        issued = _FRONTEND_CONFIRMATIONS.get(evidence_id)
        bridge = _TUI_RECEIPT_BRIDGES.get(evidence_id)
    if issued is not confirmation:
        raise WriteApprovalError("confirmation is fabricated, expired, revoked, or already used")
    if bridge is not None:
        bridge._consume_confirmation(confirmation)
    else:
        with _FRONTEND_CONFIRMATIONS_LOCK:
            if _FRONTEND_CONFIRMATIONS.get(evidence_id) is not confirmation:
                raise WriteApprovalError("confirmation was revoked or already used")
            _FRONTEND_CONFIRMATIONS.pop(evidence_id, None)


def _require_factory_intent(intent: WriteIntent) -> None:
    with _FACTORY_INTENTS_LOCK:
        if _FACTORY_INTENTS.get(id(intent)) is not intent:
            raise WriteApprovalError("write intent was not produced by the validated evidence factory in this process")


def _validate_capture(capture: DeviceCaptureEvidence, plan_device: DeviceEvidence, model_id: str, now: datetime) -> None:
    if type(capture) is not DeviceCaptureEvidence or capture.source != "local-dmi":
        raise WriteApprovalError("fresh local DMI capture is required; imported candidate evidence is not accepted")
    _require_uuid(capture.capture_id, "device capture")
    if not isinstance(capture.device_fingerprint_sha256, str) or not _SHA256.fullmatch(capture.device_fingerprint_sha256):
        raise WriteApprovalError("local device capture must include the serial-derived device fingerprint")
    observed = _parse_utc(capture.observed_at_utc, "device capture time")
    if observed > now + timedelta(seconds=30) or now - observed > _CAPTURE_MAX_AGE:
        raise WriteApprovalError("device identity capture is stale or future-dated")
    if capture.device != plan_device or capture.device.manual_model_candidate:
        raise WriteApprovalError("fresh device capture differs from the validated non-candidate plan identity")
    if not any(model_id in (value or "").casefold() for value in (
        capture.device.raw_dmi_product_name,
        capture.device.raw_dmi_product_version,
        capture.device.raw_dmi_system_family,
    )):
        raise WriteApprovalError("fresh DMI capture does not independently identify the selected model")


def _validate_voltage_attestations(plan: OperationPlan, now: datetime) -> None:
    programmer = plan.programmer
    chip = plan.topology.chips[0]
    if (
        programmer.observed is not True
        or programmer.interface_verified is not True
        or programmer.wiring_verified is not True
        or programmer.voltage_mv != 3300
        or chip.voltage_mv != 3300
    ):
        raise WriteApprovalError("external SPI setup and both measured voltages must be verified at exactly 3300 mV")
    if programmer.voltage_mv != chip.voltage_mv:
        raise WriteApprovalError("observed chip and programmer voltages do not match")
    values = (
        programmer.evidence_source,
        programmer.attested_by,
        programmer.attested_at,
        programmer.instrument_id,
        programmer.measurement_test_point,
        chip.voltage_evidence_source,
        chip.voltage_attested_by,
        chip.voltage_attested_at,
        chip.voltage_instrument_id,
        chip.voltage_test_point,
    )
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise WriteApprovalError("voltage/wiring measurements need human, instrument, and test-point provenance")
    if programmer.attested_by != chip.voltage_attested_by:
        raise WriteApprovalError("chip and programmer voltage attestations must name the same operator")
    for label, value in (("programmer", programmer.attested_at), ("chip", chip.voltage_attested_at)):
        measured = _parse_utc(value or "", f"{label} voltage measurement time")
        if measured > now + timedelta(seconds=30) or now - measured > _CAPTURE_MAX_AGE:
            raise WriteApprovalError(f"{label} voltage measurement is stale or future-dated")


def _validate_recovery_record(
    service: RecoveryReadinessService,
    plan: OperationPlan | BuildIntentEvidence,
    backup: BackupSetResult,
    supplied: RecoveryReadinessRecord,
) -> None:
    if type(supplied) is not RecoveryReadinessRecord:
        raise WriteApprovalError("typed persistent recovery-readiness evidence is required")
    current = service.evaluate(plan, backup)
    relevant = (
        "operation_id", "profile_id", "status", "primary_backup_sha256", "recovery_backup_sha256",
        "primary_manifest_sha256", "recovery_manifest_sha256", "primary_volume_id", "recovery_volume_id",
        "runbook", "recovery_method", "recovery_write_enabled", "hardware_recovery_tested", "reasons",
    )
    if any(getattr(current, key) != getattr(supplied, key) for key in relevant):
        raise WriteApprovalError("persistent recovery-readiness evidence changed or does not match current backup files")
    if current.status != "ready" or current.recovery_write_enabled or current.hardware_recovery_tested:
        raise WriteApprovalError("persistent recovery copies are not ready, or recovery claims an unperformed operation")
    _require_uuid(supplied.record_id, "recovery record")


def _validate_challenge(challenge: WriteApprovalChallenge) -> None:
    if type(challenge) is not WriteApprovalChallenge:
        raise WriteApprovalError("a typed exact-action challenge is required")
    expected_phrase = f"FLASH {challenge.model_id.upper()} {challenge.operation_id} {challenge.intent_sha256[:12].upper()}"
    if (
        challenge.action is not WriteAction.FLASH
        or challenge.exact_phrase != expected_phrase
        or challenge.challenge_sha256 != _challenge_checksum(challenge)
        or not _SHA256.fullmatch(challenge.intent_sha256)
        or not _SHA256.fullmatch(challenge.artifact_sha256)
        or len(challenge.backup_copy_paths) != len(challenge.backup_copy_sha256)
        or not all(_SHA256.fullmatch(item) for item in challenge.backup_copy_sha256)
        or not challenge.setup_actor.strip()
        or not challenge.setup_instrument_id.strip()
        or not challenge.setup_test_point.strip()
        or type(challenge.programmer_voltage_mv) is not int
        or type(challenge.chip_voltage_mv) is not int
        or challenge.programmer_voltage_mv <= 0
        or challenge.chip_voltage_mv <= 0
        or challenge.interface_verified is not True
        or challenge.wiring_verified is not True
        or challenge.target_powered_off is not True
        or challenge.ac_disconnected is not True
        or challenge.removable_battery_removed is not True
        or challenge.chip_pin1_orientation_verified is not True
        or challenge.programmer_disconnect_before_repower is not True
    ):
        raise WriteApprovalError("write challenge is malformed or does not bind an exact flash action")
    observed = _parse_utc(challenge.setup_measured_at_utc, "challenge hardware setup time")
    now = datetime.now(timezone.utc)
    if observed > now + timedelta(seconds=30) or now - observed > _CURRENT_EVIDENCE_MAX_AGE:
        raise WriteApprovalError("challenge hardware setup attestation is stale or future-dated")
    ec_version_fields = (
        challenge.ec_version.strip(),
        challenge.ec_evidence_ref.strip(),
        challenge.ec_observed_at_utc.strip(),
    )
    has_any_ec_version_field = any(ec_version_fields)
    has_ec_version = all(ec_version_fields)
    has_waiver = bool(challenge.ec_waiver_reason.strip())
    if has_any_ec_version_field and not has_ec_version:
        raise WriteApprovalError("challenge EC version, evidence reference, and observation time must be complete together")
    if has_ec_version and has_waiver:
        raise WriteApprovalError("challenge must bind observed EC evidence or one explicit waiver, not both")
    if has_ec_version:
        if challenge.ec_update_decision not in {"already-current", "updated-before-replacement"}:
            raise WriteApprovalError("challenge must bind the observed EC version update/current decision")
        ec_observed = _parse_utc(challenge.ec_observed_at_utc, "challenge EC evidence time")
        if ec_observed > now + timedelta(seconds=30) or now - ec_observed > _CURRENT_EVIDENCE_MAX_AGE:
            raise WriteApprovalError("challenge EC evidence is stale or future-dated")
    else:
        if challenge.ec_update_decision != "waived":
            raise WriteApprovalError("challenge EC waiver must bind the explicit waived decision")
        if (
            len(challenge.ec_waiver_reason.strip()) < 20
            or "oem" not in challenge.ec_waiver_reason.casefold()
            or "update" not in challenge.ec_waiver_reason.casefold()
            or not challenge.setup_actor.strip()
        ):
            raise WriteApprovalError("challenge EC waiver must name its actor and explain the lost OEM-update route")
    if challenge.model_id == "x200":
        if (
            challenge.mac_policy != "preserve-original-gbe-region"
            or challenge.original_mac is None
            or not _MAC_ADDRESS.fullmatch(challenge.original_mac.casefold())
            or challenge.selected_mac_before is None
            or not _MAC_ADDRESS.fullmatch(challenge.selected_mac_before.casefold())
            or challenge.final_mac != challenge.original_mac
            or challenge.mac_source_artifact_sha256 is None
            or not _SHA256.fullmatch(challenge.mac_source_artifact_sha256)
            or challenge.mac_preservation_manifest_path is None
            or not Path(challenge.mac_preservation_manifest_path).is_absolute()
            or challenge.mac_preservation_manifest_sha256 is None
            or not _SHA256.fullmatch(challenge.mac_preservation_manifest_sha256)
        ):
            raise WriteApprovalError("X200 write challenge lacks verified original GbE MAC preservation evidence")
    elif any((
        challenge.mac_policy != "not-applicable",
        challenge.mac_source_artifact_sha256 is not None,
        challenge.mac_preservation_manifest_path is not None,
        challenge.mac_preservation_manifest_sha256 is not None,
        challenge.original_mac is not None,
        challenge.selected_mac_before is not None,
        challenge.final_mac is not None,
    )):
        raise WriteApprovalError("X220 write challenge must not carry X200 GbE MAC preservation claims")


def _challenge_checksum(challenge: WriteApprovalChallenge) -> str:
    payload = {
        item.name: _canonical(getattr(challenge, item.name))
        for item in fields(challenge)
        if item.name != "challenge_sha256"
    }
    return _sha256(_json_bytes(payload))


def _validate_challenge_against_intent(intent: WriteIntent, challenge: WriteApprovalChallenge) -> None:
    _validate_challenge(challenge)
    expected = create_write_approval_challenge(intent)
    compared = (
        "operation_id", "action", "model_id", "chip_name", "capacity_bytes", "artifact_sha256",
        "intent_sha256", "exact_phrase", "executable", "argv", "artifact_path",
        "artifact_manifest_path", "artifact_manifest_sha256", "primary_manifest_path",
        "primary_manifest_sha256", "recovery_manifest_path", "recovery_manifest_sha256",
        "backup_copy_paths", "backup_copy_sha256", "setup_actor", "setup_instrument_id",
        "setup_test_point", "programmer_voltage_mv", "chip_voltage_mv",
        "mac_policy", "mac_source_artifact_sha256", "mac_preservation_manifest_path",
        "mac_preservation_manifest_sha256", "original_mac", "selected_mac_before", "final_mac",
    )
    if any(getattr(challenge, name) != getattr(expected, name) for name in compared):
        raise WriteApprovalError("displayed challenge differs from the immutable staged write intent")


def _manifest_cpu_count(manifest: Any) -> int:
    """Read the exact CPU count from the pinned X220 build command records."""
    if manifest is None:
        raise WriteApprovalError("X220 artifact manifest is missing")
    crossgcc_counts: set[int] = set()
    make_counts: set[int] = set()
    for command in manifest.commands:
        argv = command.argv
        if len(argv) == 3 and argv[:2] == ("make", "crossgcc-i386") and argv[2].startswith("CPUS="):
            value = argv[2][len("CPUS="):]
            if value.isdecimal() and int(value) > 0:
                crossgcc_counts.add(int(value))
        if len(argv) == 2 and argv[0] == "make" and re.fullmatch(r"-j[1-9][0-9]*", argv[1]):
            make_counts.add(int(argv[1][2:]))
    if len(crossgcc_counts) != 1 or crossgcc_counts != make_counts:
        raise WriteApprovalError("X220 artifact manifest has ambiguous/mismatched pinned build CPU commands")
    return next(iter(crossgcc_counts))


def _with_intent_checksum(intent: WriteIntent) -> WriteIntent:
    from dataclasses import replace
    return replace(intent, intent_sha256=_intent_checksum(intent))


def _intent_checksum(intent: WriteIntent) -> str:
    payload = {field.name: _canonical(getattr(intent, field.name)) for field in fields(intent) if field.name != "intent_sha256"}
    return _sha256(_json_bytes(payload))


def _validate_intent_policy(intent: WriteIntent) -> None:
    model_by_profile = {"x200-libreboot": "x200", "x220-coreboot": "x220"}
    if (
        intent.action is not WriteAction.FLASH
        or model_by_profile.get(intent.profile_id) != intent.model_id
        or intent.programmer not in {item.value for item in ProgrammerType if item is not ProgrammerType.INTERNAL}
    ):
        raise WriteApprovalError("write intent profile/action/programmer is outside the external SPI allowlist")
    for name, value in (
        ("operation", intent.operation_id),
        ("device capture", intent.device_capture_id),
        ("backup operation", intent.backup_operation_id),
        ("recovery record", intent.recovery_record_id),
    ):
        _require_uuid(value, name)
    for label, value in (
        ("device evidence", intent.device_evidence_sha256),
        ("device fingerprint", intent.device_fingerprint_sha256),
        ("probe output", intent.probe_output_sha256),
        ("artifact", intent.artifact_sha256),
        ("artifact manifest", intent.artifact_manifest_sha256),
        ("operation journal", intent.operation_journal_checksum),
        ("primary manifest", intent.primary_manifest_sha256),
        ("recovery manifest", intent.recovery_manifest_sha256),
        ("command executable", intent.command.executable_sha256),
        ("plan", intent.plan_digests.plan_sha256),
        ("plan device", intent.plan_digests.device_sha256),
        ("plan artifact", intent.plan_digests.artifact_sha256),
        ("plan topology", intent.plan_digests.topology_sha256),
        ("plan backup", intent.plan_digests.backup_sha256),
    ):
        if not _SHA256.fullmatch(value):
            raise WriteApprovalError(f"{label} fingerprint is malformed")
    allowed_capacities = {4 * 1024 * 1024, 8 * 1024 * 1024} if intent.model_id == "x200" else {8 * 1024 * 1024}
    if (
        intent.chip_capacity_bytes not in allowed_capacities
        or intent.artifact_size_bytes not in allowed_capacities
        or intent.artifact_size_bytes != intent.chip_capacity_bytes
        or intent.chip_voltage_mv != 3300
        or intent.programmer_voltage_mv != 3300
        or intent.chip_voltage_mv != intent.programmer_voltage_mv
    ):
        raise WriteApprovalError("write intent chip size or voltage is outside the model-specific V1 profile")
    if not intent.backup_copies or len(intent.backup_copies) != 1:
        raise WriteApprovalError("write intent must bind exactly one complete dual-read chip backup")
    if intent.profile_id == "x200-libreboot":
        if (
            intent.mac_policy != "preserve-original-gbe-region"
            or intent.mac_preservation_manifest_path is None
            or intent.mac_preservation_manifest_sha256 is None
            or not _SHA256.fullmatch(intent.mac_source_artifact_sha256 or "")
            or not _SHA256.fullmatch(intent.mac_preservation_manifest_sha256)
            or not Path(intent.mac_preservation_manifest_path).is_absolute()
            or intent.original_mac is None
            or not _MAC_ADDRESS.fullmatch(intent.original_mac.casefold())
            or intent.selected_mac_before is None
            or not _MAC_ADDRESS.fullmatch(intent.selected_mac_before.casefold())
            or intent.final_mac != intent.original_mac
        ):
            raise WriteApprovalError("X200 write intent must bind original and final MACs to a preservation manifest")
        artifact_manifest_path = Path(intent.artifact_manifest_path)
        if _has_link_component(artifact_manifest_path) or not artifact_manifest_path.is_file():
            raise WriteApprovalError("X200 source artifact manifest is missing or unsafe")
        try:
            raw_artifact_manifest = artifact_manifest_path.read_bytes()
            artifact_manifest = json.loads(raw_artifact_manifest)
        except (OSError, json.JSONDecodeError) as exc:
            raise WriteApprovalError("X200 source artifact manifest is unreadable") from exc
        if (
            _sha256(raw_artifact_manifest) != intent.artifact_manifest_sha256
            or not isinstance(artifact_manifest, dict)
            or artifact_manifest.get("status") != "complete"
            or artifact_manifest.get("selected_artifact_relative_path") != intent.artifact_selected_relative_path
            or artifact_manifest.get("selected_artifact_sha256") != intent.mac_source_artifact_sha256
        ):
            raise WriteApprovalError("X200 MAC source digest is not bound to the exact COMPLETE selected artifact")
        source_relative = PurePosixPath(intent.artifact_selected_relative_path)
        if source_relative.is_absolute() or ".." in source_relative.parts:
            raise WriteApprovalError("X200 selected source artifact path is unsafe")
        source_artifact_path = artifact_manifest_path.parent.joinpath(*source_relative.parts)
        if (
            _has_link_component(source_artifact_path)
            or not source_artifact_path.is_file()
            or _hash_file(source_artifact_path) != intent.mac_source_artifact_sha256
        ):
            raise WriteApprovalError("X200 selected source artifact changed after GbE MAC inspection")
        mac_manifest_path = Path(intent.mac_preservation_manifest_path)
        if _has_link_component(mac_manifest_path) or not mac_manifest_path.is_file():
            raise WriteApprovalError("X200 MAC preservation manifest is missing or unsafe")
        try:
            raw_mac_manifest = mac_manifest_path.read_bytes()
            mac_manifest = json.loads(raw_mac_manifest)
        except (OSError, json.JSONDecodeError) as exc:
            raise WriteApprovalError("X200 MAC preservation manifest is unreadable") from exc
        if (
            _sha256(raw_mac_manifest) != intent.mac_preservation_manifest_sha256
            or not isinstance(mac_manifest, dict)
            or mac_manifest.get("operation_id") != intent.operation_id
            or mac_manifest.get("policy") != intent.mac_policy
            or mac_manifest.get("source_artifact_path") != normalized_path(str(source_artifact_path.resolve(strict=True)))
            or mac_manifest.get("source_artifact_sha256") != intent.mac_source_artifact_sha256
            or mac_manifest.get("write_artifact_path") != intent.artifact_path
            or mac_manifest.get("write_artifact_sha256") != intent.artifact_sha256
            or mac_manifest.get("write_artifact_size_bytes") != intent.artifact_size_bytes
            or mac_manifest.get("original_mac") != intent.original_mac
            or mac_manifest.get("selected_mac_before") != intent.selected_mac_before
            or mac_manifest.get("selected_mac_after") != intent.final_mac
            or mac_manifest.get("original_backup_sha256") != intent.backup_copies[0].first_sha256
            or mac_manifest.get("independent_backup_sha256") != intent.backup_copies[0].second_sha256
        ):
            raise WriteApprovalError("X200 MAC preservation manifest differs from the approved intent or backups")
    elif any((
        intent.mac_policy != "not-applicable",
        intent.mac_source_artifact_sha256 is not None,
        intent.mac_preservation_manifest_path is not None,
        intent.mac_preservation_manifest_sha256 is not None,
        intent.original_mac is not None,
        intent.selected_mac_before is not None,
        intent.final_mac is not None,
    )):
        raise WriteApprovalError("X220 write intent cannot claim X200 MAC preservation")
    if not intent.backup_copies or len(intent.backup_copies) != 1:
        raise WriteApprovalError("write intent must bind exactly one complete dual-read chip backup")
    backup = intent.backup_copies[0]
    _require_uuid(backup.first_capture_id, "first backup capture")
    _require_uuid(backup.second_capture_id, "second backup capture")
    if (
        backup.capacity_bytes != intent.chip_capacity_bytes
        or not backup.chip_id.strip()
        or backup.first_path == backup.second_path
        or not Path(backup.first_path).is_absolute()
        or not Path(backup.second_path).is_absolute()
        or not _SHA256.fullmatch(backup.first_sha256)
        or not _SHA256.fullmatch(backup.second_sha256)
        or backup.first_sha256 != backup.second_sha256
    ):
        raise WriteApprovalError("write intent backup paths/hashes are incomplete or inconsistent")
    if (
        not Path(intent.artifact_path).is_absolute()
        or not Path(intent.artifact_manifest_path).is_absolute()
        or not Path(intent.operation_journal_path).is_absolute()
        or not Path(intent.primary_manifest_path).is_absolute()
        or not Path(intent.recovery_manifest_path).is_absolute()
        or intent.operation_journal_state != OperationState.BACKUP_VERIFIED.value
        or intent.primary_volume_id == intent.recovery_volume_id
        or intent.recovery_ready_status != "ready"
        or intent.recovery_write_enabled
        or intent.recovery_hardware_tested
    ):
        raise WriteApprovalError("write intent recovery evidence is not separately persistent and ready")
    _require_uuid(intent.artifact_operation_id, "artifact operation")
    if (
        not intent.artifact_recipe_id
        or not intent.artifact_source_repository_url.startswith("https://")
        or not re.fullmatch(r"[0-9a-f]{40}", intent.artifact_source_revision)
        or not re.fullmatch(r"[0-9a-f]{40}", intent.artifact_source_tree_id)
        or not intent.artifact_selected_relative_path
        or PurePosixPath(intent.artifact_selected_relative_path).is_absolute()
        or ".." in PurePosixPath(intent.artifact_selected_relative_path).parts
        or not intent.artifact_source_files
        or not intent.artifact_toolchain
        or not intent.artifact_command_argv
        or not intent.artifact_tool_fingerprints
        or any(not path or not _SHA256.fullmatch(digest) for path, digest in intent.artifact_source_files)
        or any(not name or not version for name, version in intent.artifact_toolchain)
        or any(not path or not _SHA256.fullmatch(digest) for path, digest in intent.artifact_tool_fingerprints)
        or any(not command or any("\x00" in argument for argument in command) for command in intent.artifact_command_argv)
    ):
        raise WriteApprovalError("write intent lacks exact persisted artifact recipe/source/tool evidence")
    try:
        _parse_utc(intent.created_at_utc, "write intent time")
        _parse_utc(intent.device_capture_at_utc, "device capture time")
        _parse_utc(intent.probe_at_utc, "SPI probe time")
        _parse_utc(intent.voltage_attested_at_utc, "voltage measurement time")
    except WriteApprovalError:
        raise
    executable = Path(intent.command.executable)
    if (
        not executable.is_absolute()
        or normalized_path(str(executable)) != intent.command.executable
        or not _SHA256.fullmatch(intent.command.executable_sha256)
        or intent.command.tool_name not in {"flashrom", "flashprog"}
        or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.-]+)?", intent.command.tool_version)
    ):
        raise WriteApprovalError("fixed flash tool identity is malformed")
    expected_argv = ("-p", intent.programmer, "-c", intent.chip_name, "-w", intent.artifact_path)
    if intent.command.argv != expected_argv:
        raise WriteApprovalError("write command differs from the fixed model-specific argv")


def _canonical(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: _canonical(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Path):
        return normalized_path(str(value))
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    return value


def _require_tty_file_descriptor(stream: TextIO, label: str) -> int:
    try:
        descriptor = stream.fileno()
        if isinstance(descriptor, bool) or not isinstance(descriptor, int) or descriptor < 0:
            raise ValueError("invalid descriptor")
        os.fstat(descriptor)
    except (AttributeError, OSError, ValueError) as exc:
        raise WriteApprovalError(f"real OS TTY {label} file descriptor is unavailable") from exc
    return descriptor


def _directory_fsync_supported() -> bool:
    """Windows directory handles are not assumed durable by this V1 ledger."""
    return os.name == "posix"


def _sync_directory(directory: Path) -> None:
    """Durably sync a parent directory or raise; never silently degrade."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_bytes(value: object) -> bytes:
    return json.dumps(_canonical(value), sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write_exclusive(path: Path, data: bytes) -> None:
    if _has_link_component(path):
        raise WriteApprovalError("approval ledger path crosses a symlink or junction")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(descriptor)
    except OSError as exc:
        raise WriteApprovalError(f"approval file could not be durably synced: {exc}") from exc
    finally:
        os.close(descriptor)
    try:
        _sync_directory(path.parent)
    except OSError as exc:
        raise WriteApprovalError(f"approval parent directory could not be durably synced: {exc}") from exc


def _token_matches_record(token: WriteApprovalToken, record: dict[str, object]) -> bool:
    return (
        record.get("approval_id") == token.approval_id
        and record.get("token_secret_sha256") == _sha256(token.secret.encode("utf-8"))
        and record.get("operation_id") == token.operation_id
        and record.get("intent_sha256") == token.intent_sha256
        and record.get("actor") == token.actor
        and record.get("issued_at_utc") == token.issued_at_utc
        and record.get("channel") == token.channel.value
        and record.get("challenge_sha256") == token.challenge_sha256
        and record.get("record_sha256") == token.record_sha256
    )


def _parse_utc(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise WriteApprovalError(f"{label} must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise WriteApprovalError(f"{label} must be timezone-aware UTC")
    return parsed.astimezone(timezone.utc)


def _require_uuid(value: str, label: str) -> None:
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError("noncanonical UUID")
    except (AttributeError, TypeError, ValueError) as exc:
        raise WriteApprovalError(f"{label} ID must be a canonical UUID") from exc


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _has_link_component(path: Path) -> bool:
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    for component in absolute.parts[1:] if absolute.anchor else absolute.parts:
        current = current / component
        if current.is_symlink():
            return True
        is_junction = getattr(current, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
    return False
