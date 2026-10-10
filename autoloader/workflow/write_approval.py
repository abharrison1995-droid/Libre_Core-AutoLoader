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
import threading
import uuid
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, TextIO

from autoloader.flasher.backup_service import BackupSetResult, BackupSetService
from autoloader.flasher.flashrom import FlashToolIdentity, ProgrammerType
from autoloader.flasher.recovery import RecoveryReadinessRecord, RecoveryReadinessService
from autoloader.flasher.spi import ChipProbeEvidence
from autoloader.workflow.authorization import AuthorizationError, AuthorizationStaleError, AuthorizationReusedError, compute_plan_digests
from autoloader.workflow.compatibility import CompatibilityValidator
from autoloader.workflow.types import (
    CompatibilityDecision,
    DeviceEvidence,
    OperationPlan,
    PlanDigests,
    ProgrammerKind,
    RegionEvidence,
    normalized_path,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_APPROVAL_SCHEMA = 1
_CAPTURE_MAX_AGE = timedelta(minutes=5)
_APPROVAL_MAX_AGE = timedelta(minutes=5)
_FRONTEND_CONFIRMATIONS: dict[str, "HumanActionConfirmation"] = {}
_FRONTEND_CONFIRMATIONS_LOCK = threading.Lock()
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

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_regions", tuple(self.artifact_regions))
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
    ) -> None:
        self.backup_service = backup_service
        self.validator = validator or CompatibilityValidator()
        self.recovery_service = recovery_service or RecoveryReadinessService(self.validator)

    def create(
        self,
        plan: OperationPlan,
        *,
        operation_id: str,
        capture: DeviceCaptureEvidence,
        probe: ChipProbeEvidence,
        backup_set: BackupSetResult,
        recovery_readiness: RecoveryReadinessRecord,
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
            reopened = self.backup_service.load_verified_result(
                plan,
                operation_id=backup_set.operation_id,
                primary_manifest=backup_set.primary_manifest,
                recovery_manifest=backup_set.recovery_manifest,
                primary_manifest_sha256=backup_set.primary_manifest_sha256,
                recovery_manifest_sha256=backup_set.recovery_manifest_sha256,
                expected_tool=probe.tool,
            )
        except Exception as exc:
            raise WriteApprovalError(f"persistent backup manifests/copies failed revalidation: {exc}") from exc
        if reopened != backup_set:
            raise WriteApprovalError("reopened backup result differs from the supplied verified backup set")
        _validate_recovery_record(self.recovery_service, plan, reopened, recovery_readiness)

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
        tool_sha = _hash_file(tool_path)
        expected_argv = (
            "-p", probe.programmer.value,
            "-c", probe.chip_name,
            "-w", str(artifact_path),
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
            artifact_path=str(artifact_path),
            artifact_sha256=artifact_sha,
            artifact_size_bytes=artifact_path.stat().st_size,
            artifact_regions=plan.artifact.regions,
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
        )
        intent = _with_intent_checksum(intent)
        intent.validate_integrity()
        with _FACTORY_INTENTS_LOCK:
            _FACTORY_INTENTS[id(intent)] = intent
        return intent


class WriteApprovalLedger:
    """Durable single-use approval ledger using exclusive filesystem claims.

    Token state is intentionally not added to ``OperationJournal`` or returned
    by a JSON loader. An exclusive marker created with ``O_EXCL`` provides
    cross-process single consumption. A crash after the marker is written is
    fail-closed: the approval is consumed and cannot be resumed or replayed.
    """

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).expanduser().absolute()
        self.directory.mkdir(parents=True, exist_ok=True)
        if _has_link_component(self.directory):
            raise WriteApprovalError("approval ledger directory must not contain a symlink or junction")
        self._lock = threading.Lock()

    def issue(self, intent: WriteIntent, confirmation: HumanActionConfirmation) -> WriteApprovalToken:
        if type(intent) is not WriteIntent or type(confirmation) is not HumanActionConfirmation:
            raise WriteApprovalError("a typed write intent and frontend human-confirmation record are required")
        _require_factory_intent(intent)
        _consume_frontend_confirmation(confirmation)
        intent.validate_integrity()
        if intent.action is not WriteAction.FLASH:
            raise WriteApprovalError("restore requires its own separately reviewed approval path")
        intent_time = _parse_utc(intent.created_at_utc, "write intent time")
        now = datetime.now(timezone.utc)
        if intent_time > now + timedelta(seconds=30) or now - intent_time > _APPROVAL_MAX_AGE:
            raise WriteApprovalError("write intent is future-dated or expired")
        challenge = create_write_approval_challenge(intent)
        if (
            confirmation.challenge_sha256 != challenge.challenge_sha256
            or confirmation.exact_phrase != challenge.exact_phrase
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
            "challenge_sha256": challenge.challenge_sha256,
        }
        document = {**token_payload, "record_sha256": _sha256(_json_bytes(token_payload))}
        record_bytes = _json_bytes(document)
        record_sha256 = str(document["record_sha256"])
        with self._lock:
            confirmation_path = self.directory / f"{challenge.challenge_sha256}.confirmed"
            _write_exclusive(confirmation_path, _json_bytes({"challenge_sha256": challenge.challenge_sha256}))
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
            challenge_sha256=challenge.challenge_sha256,
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


def create_write_approval_challenge(intent: WriteIntent) -> WriteApprovalChallenge:
    _require_factory_intent(intent)
    intent.validate_integrity()
    phrase = f"FLASH {intent.model_id.upper()} {intent.operation_id} {intent.intent_sha256[:12].upper()}"
    challenge_sha = _sha256(phrase.encode("utf-8"))
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
    )


def collect_cli_confirmation(
    challenge: WriteApprovalChallenge,
    *,
    actor: str,
    input_stream: TextIO,
    output_stream: TextIO,
) -> HumanActionConfirmation:
    """Collect exact confirmation only from an interactive TTY pair."""
    if not callable(getattr(input_stream, "isatty", None)) or not input_stream.isatty():
        raise WriteApprovalError("interactive TTY input is required; non-TTY approval is disabled")
    if not callable(getattr(output_stream, "isatty", None)) or not output_stream.isatty():
        raise WriteApprovalError("interactive TTY output is required to display the irreversible action")
    _validate_challenge(challenge)
    output_stream.write(
        "PHYSICAL FIRMWARE WRITE: "
        f"{challenge.model_id.upper()} chip {challenge.chip_name} ({challenge.capacity_bytes} bytes), "
        f"operation {challenge.operation_id}, artifact SHA-256 {challenge.artifact_sha256}.\n"
        f"Type exactly: {challenge.exact_phrase}\n> "
    )
    output_stream.flush()
    typed = input_stream.readline().rstrip("\r\n")
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
    """Convert the dedicated screen's explicit confirm button event."""
    _validate_challenge(challenge)
    if type(event) is not TUIConfirmationEvent:
        raise WriteApprovalError("a typed confirmation-screen event is required")
    if (
        event.screen_id != "write-confirmation"
        or event.button_id != "confirm-write"
        or event.challenge_sha256 != challenge.challenge_sha256
    ):
        raise WriteApprovalError("TUI event does not confirm the displayed exact write operation")
    _require_uuid(event.event_id, "TUI event")
    return _mint_frontend_confirmation(
        challenge=challenge,
        actor=actor,
        confirmed_at=_parse_utc(event.occurred_at_utc, "TUI event time"),
        channel=ConfirmationChannel.TUI_EXPLICIT_ACTION,
    )


def _mint_frontend_confirmation(
    *, challenge: WriteApprovalChallenge, actor: str,
    confirmed_at: datetime, channel: ConfirmationChannel,
) -> HumanActionConfirmation:
    """Create a one-use in-process receipt for a checked frontend interaction."""
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


def _consume_frontend_confirmation(confirmation: HumanActionConfirmation) -> None:
    evidence_id = getattr(confirmation, "evidence_id", None)
    if not isinstance(evidence_id, str):
        raise WriteApprovalError("confirmation is not a live frontend interaction receipt")
    with _FRONTEND_CONFIRMATIONS_LOCK:
        issued = _FRONTEND_CONFIRMATIONS.pop(evidence_id, None)
    if issued is not confirmation:
        raise WriteApprovalError("confirmation is fabricated, expired, or already used")


def _require_factory_intent(intent: WriteIntent) -> None:
    with _FACTORY_INTENTS_LOCK:
        if _FACTORY_INTENTS.get(id(intent)) is not intent:
            raise WriteApprovalError("write intent was not produced by the validated evidence factory in this process")


def _validate_capture(capture: DeviceCaptureEvidence, plan_device: DeviceEvidence, model_id: str, now: datetime) -> None:
    if type(capture) is not DeviceCaptureEvidence or capture.source != "local-dmi":
        raise WriteApprovalError("fresh local DMI capture is required; imported candidate evidence is not accepted")
    _require_uuid(capture.capture_id, "device capture")
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
    plan: OperationPlan,
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
        or challenge.challenge_sha256 != _sha256(challenge.exact_phrase.encode("utf-8"))
        or not _SHA256.fullmatch(challenge.intent_sha256)
        or not _SHA256.fullmatch(challenge.artifact_sha256)
    ):
        raise WriteApprovalError("write challenge is malformed or does not bind an exact flash action")


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
        ("probe output", intent.probe_output_sha256),
        ("artifact", intent.artifact_sha256),
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
        or not Path(intent.primary_manifest_path).is_absolute()
        or not Path(intent.recovery_manifest_path).is_absolute()
        or intent.primary_volume_id == intent.recovery_volume_id
        or intent.recovery_ready_status != "ready"
        or intent.recovery_write_enabled
        or intent.recovery_hardware_tested
    ):
        raise WriteApprovalError("write intent recovery evidence is not separately persistent and ready")
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
    finally:
        os.close(descriptor)
    try:
        directory_fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    except OSError:
        pass
    finally:
        os.close(directory_fd)


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
