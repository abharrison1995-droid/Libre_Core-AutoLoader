"""Phase 10B one-shot external SPI write and independent read-back service.

This module is the only production boundary that may spawn a firmware writer.
It accepts the narrow X200/X220 profiles, reopens the accepted workflow and
ArtifactEngine evidence through :class:`WriteIntentFactory`, consumes a TTY
approval under an exclusive per-operation lock, persists ``WRITE_STARTED``
before the subprocess, and performs one read-only full-chip comparison. It
never restores, retries, reboots, or writes T430 firmware.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol, cast

from autoloader.flasher.backup_service import (
    BackupSetResult,
    FilesystemStorageInspector,
    StorageEvidence,
    StorageInspector,
)
from autoloader.flasher.flashrom import ProgrammerType
from autoloader.flasher.readback import ReadbackComparator, ReadbackComparison
from autoloader.flasher.recovery import RecoveryReadinessRecord
from autoloader.flasher.spi import ChipProbeEvidence, ReadOnlySPITransport, SPIOperationError
from autoloader.workflow.authorization import AuthorizationError
from autoloader.workflow.engine import WorkflowSession
from autoloader.workflow.journal import OperationJournal
from autoloader.workflow.types import OperationPlan, OperationState, VerificationStatus
from autoloader.workflow.write_approval import (
    DeviceCaptureEvidence,
    HumanActionConfirmation,
    WriteApprovalError,
    WriteApprovalChallenge,
    WriteApprovalLedger,
    WriteApprovalToken,
    FixedWriteCommand,
    WriteIntent,
    WriteIntentFactory,
    _MountedTUIConfirmationBridge,
    _directory_fsync_supported,
    _has_link_component,
    _hash_file,
    _json_bytes,
    _parse_utc,
    _write_exclusive,
    collect_cli_confirmation,
    create_write_approval_challenge,
)

_CURRENT_EVIDENCE_MAX_AGE = timedelta(minutes=2)


class WriteExecutionError(RuntimeError):
    """A physical write request failed a pre-write gate and issued no command."""


class WriteExecutionStatus(str, Enum):
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"
    WRITE_VERIFIED = "WRITE_VERIFIED"


@dataclass(frozen=True)
class HardwareSetupAttestation:
    """Fresh human attestations and measurements, never imported JSON.

    The power, AC, battery, orientation and disconnect values are human-reported
    checks. They are not device telemetry or proof supplied by the application.
    """

    actor: str
    instrument_id: str
    test_point: str
    measured_at_utc: str
    programmer_voltage_mv: int
    chip_voltage_mv: int
    interface_verified: bool
    wiring_verified: bool
    target_powered_off: bool = False
    ac_disconnected: bool = False
    removable_battery_removed: bool = False
    chip_pin1_orientation_verified: bool = False
    programmer_disconnect_before_repower: bool = False
    ec_version: str = ""
    ec_evidence_ref: str = ""
    ec_observed_at_utc: str = ""
    ec_update_decision: str = ""
    ec_waiver_reason: str = ""


@dataclass(frozen=True)
class FreshWriteEvidence:
    capture: DeviceCaptureEvidence
    probe: ChipProbeEvidence
    setup: HardwareSetupAttestation


class FreshEvidenceProvider(Protocol):
    """Collect current local DMI/probe evidence and a live setup attestation."""

    def collect(self, plan: OperationPlan) -> FreshWriteEvidence: ...


class WriteCommandRunner(Protocol):
    def run(self, capability: object, timeout_seconds: int) -> subprocess.CompletedProcess[str]: ...


class ReadOnlyChipTransport(Protocol):
    def read_chip(
        self, programmer: ProgrammerType, probe: ChipProbeEvidence, destination: Path
    ) -> object: ...


@dataclass(frozen=True)
class _ReadbackReservation:
    path: Path
    device: int
    inode: int
    volume_id: str


@dataclass
class _LockOutcome:
    result: WriteExecutionResult | None = None


@dataclass(frozen=True)
class _ApprovedWriteCapability:
    capability_id: str
    operation_id: str
    approval_id: str
    intent_sha256: str
    journal_path: str
    lock_path: str
    command: object
    command_sha256: str


_WRITE_CAPABILITIES: dict[str, tuple[str, str, str, str]] = {}
_WRITE_CAPABILITIES_LOCK = threading.Lock()


@dataclass(frozen=True)
class WriteExecutionRequest:
    """Typed references and reopened values from one staged X200/X220 flow."""

    plan: OperationPlan
    operation_id: str
    backup_set: BackupSetResult
    recovery_readiness: RecoveryReadinessRecord
    operation_journal_path: str | Path
    artifact_manifest_path: str | Path


@dataclass
class WriteExecutionResult:
    status: WriteExecutionStatus
    code: str
    operation_id: str
    message: str
    state: OperationState
    approval_id: str | None = None
    artifact_sha256: str | None = None
    readback_path: str | None = None
    readback_sha256: str | None = None
    comparison: ReadbackComparison | None = None
    boot_status: VerificationStatus = VerificationStatus.NOT_PERFORMED
    writer_returncode: int | None = None
    writer_duration_seconds: float | None = None


class SubprocessWriteCommandRunner:
    """Run only a service-minted command after rechecking its durable boundary."""

    def run(self, capability: object, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
        command = _consume_approved_write_capability(capability)
        if timeout_seconds <= 0:
            raise WriteExecutionError("writer timeout must be positive")
        return subprocess.run(
            [command.executable, *command.argv],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
            shell=False,
        )


class ExternalSPIWriteService:
    """Consume exact TTY approval, write once, and verify by independent read-back."""

    def __init__(
        self,
        *,
        intent_factory: WriteIntentFactory,
        approval_ledger: WriteApprovalLedger,
        evidence_provider: FreshEvidenceProvider,
        read_transport: ReadOnlyChipTransport,
        writer: WriteCommandRunner | None = None,
        comparator: ReadbackComparator | None = None,
        storage_inspector: StorageInspector | None = None,
        timeout_seconds: int = 300,
        durability_supported: Callable[[], bool] | None = None,
    ) -> None:
        self.intent_factory = intent_factory
        self.approval_ledger = approval_ledger
        self.evidence_provider = evidence_provider
        self.read_transport = read_transport
        self.writer = writer or SubprocessWriteCommandRunner()
        self.comparator = comparator or ReadbackComparator()
        self.storage_inspector = storage_inspector or FilesystemStorageInspector()
        self.timeout_seconds = timeout_seconds
        self._durability_supported = durability_supported or _directory_fsync_supported
        self._lock_local = threading.local()
        if timeout_seconds <= 0:
            raise ValueError("writer timeout must be positive")

    def execute(
        self,
        request: WriteExecutionRequest,
        *,
        _tui_bridge: _MountedTUIConfirmationBridge | None = None,
    ) -> WriteExecutionResult:
        """Approve and execute one exact, staged whole-chip X200/X220 write.

        This method performs fresh local read-only checks and a human setup
        attestation before displaying the exact challenge. After confirmation,
        it acquires the operation lock, repeats DMI/probe, consumes the approval,
        persists the write-start event, and invokes the fixed subprocess once.
        """
        if not self._durability_supported():
            raise WriteExecutionError("physical writes are unavailable without durable directory fsync support")
        self._validate_request_paths(request)
        initial_evidence = self.evidence_provider.collect(request.plan)
        self._validate_setup(request.plan, initial_evidence.setup)
        intent = self._build_intent(request, initial_evidence)
        challenge = create_write_approval_challenge(intent, setup_attestation=initial_evidence.setup)
        if _tui_bridge is None:
            confirmation = collect_cli_confirmation(challenge, actor=initial_evidence.setup.actor)
        else:
            if type(_tui_bridge) is not _MountedTUIConfirmationBridge:
                raise WriteExecutionError("TUI approval requires the private mounted-screen bridge")
            confirmation = _tui_bridge.collect(challenge, initial_evidence.setup.actor)
        lock_path = self._lock_path(request.operation_journal_path)
        token = self.approval_ledger.issue(intent, confirmation, challenge=challenge)
        with self._operation_lock(
            lock_path,
            request.operation_id,
            on_acquire_failure=lambda: self._burn_approval(token, intent),
        ):
            reservation: _ReadbackReservation | None = None
            try:
                reservation = self._reserve_readback_destination(
                    request, capacity_bytes=intent.chip_capacity_bytes
                )
                self._assert_prewrite_checkpoint(request)
                current_evidence = self.evidence_provider.collect(request.plan)
                self._validate_setup(request.plan, current_evidence.setup)
                if not _same_setup(initial_evidence.setup, current_evidence.setup):
                    raise WriteExecutionError("programmer/chip voltage or wiring attestation changed after approval")
                current_intent = self._build_intent(request, current_evidence)
                if not _same_revalidated_intent(intent, current_intent):
                    raise WriteExecutionError("fresh device/probe or accepted operation evidence changed after approval")
                self._assert_prewrite_checkpoint(request)
                self._assert_readback_reservation(reservation, capacity_bytes=intent.chip_capacity_bytes)
            except Exception:
                self._remove_unstarted_reservation(reservation)
                self._burn_approval(token, intent)
                raise

            receipt = self.approval_ledger.consume(token, intent)
            journal = self._load_prewrite_journal(request)
            command = intent.command
            start_message = self._write_start_record(
                intent, token, receipt, initial_evidence, current_evidence
            )
            try:
                journal.append(
                    action="external-spi-write",
                    from_state=OperationState.BACKUP_VERIFIED,
                    to_state=OperationState.WRITE_STARTED,
                    code="WRITE_STARTED",
                    message=start_message,
                )
            except Exception as exc:
                raise WriteExecutionError(
                    f"approval was consumed but durable WRITE_STARTED could not be recorded; no writer was called: {exc}"
                ) from exc

            if not self._intent_files_still_match(intent):
                return self._terminal_result(
                    journal,
                    OperationState.WRITE_FAILED,
                    WriteExecutionStatus.FAILED,
                    "PREWRITE_FILE_CHANGED",
                    "Artifact or writer executable changed after WRITE_STARTED; writer was not called.",
                    intent,
                    token,
                )

            try:
                capability = _mint_approved_write_capability(intent, token, request, lock_path)
            except Exception as exc:
                return self._terminal_result(
                    journal,
                    OperationState.WRITE_FAILED,
                    WriteExecutionStatus.FAILED,
                    "WRITER_CAPABILITY_REJECTED",
                    f"Durable WRITE_STARTED was recorded, but the sealed writer capability could not be minted; writer was not invoked: {exc}",
                    intent,
                    token,
                )

            started = time.monotonic()
            try:
                process = self.writer.run(capability, self.timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                return self._terminal_result(
                    journal,
                    OperationState.WRITE_UNCERTAIN,
                    WriteExecutionStatus.UNCERTAIN,
                    "WRITER_TIMEOUT",
                    f"Writer timed out; physical state is uncertain and no retry or restore was attempted. {exc}",
                    intent,
                    token,
                    writer_duration=time.monotonic() - started,
                )
            except KeyboardInterrupt:
                return self._terminal_result(
                    journal,
                    OperationState.WRITE_UNCERTAIN,
                    WriteExecutionStatus.UNCERTAIN,
                    "WRITER_INTERRUPTED",
                    "Writer was interrupted; physical state is uncertain and no retry or restore was attempted.",
                    intent,
                    token,
                    writer_duration=time.monotonic() - started,
                )
            except OSError as exc:
                return self._terminal_result(
                    journal,
                    OperationState.WRITE_FAILED,
                    WriteExecutionStatus.FAILED,
                    "WRITER_NOT_STARTED",
                    f"Writer process could not be started; no automatic retry was attempted: {exc}",
                    intent,
                    token,
                    writer_duration=time.monotonic() - started,
                )
            except Exception as exc:
                return self._terminal_result(
                    journal,
                    OperationState.WRITE_UNCERTAIN,
                    WriteExecutionStatus.UNCERTAIN,
                    "WRITER_RUNTIME_ERROR_STATE_UNCERTAIN",
                    f"Writer boundary raised after invocation began; physical state is uncertain and no retry or restore was attempted: {exc}",
                    intent,
                    token,
                    writer_duration=time.monotonic() - started,
                )

            duration = time.monotonic() - started
            if process.returncode != 0:
                return self._terminal_result(
                    journal,
                    OperationState.WRITE_UNCERTAIN,
                    WriteExecutionStatus.UNCERTAIN,
                    "WRITER_NONZERO_EXIT_STATE_UNCERTAIN",
                    "Writer started and returned nonzero; it may have partially changed the chip. Physical state is uncertain. No retry or restore was attempted.",
                    intent,
                    token,
                    writer_returncode=process.returncode,
                    writer_duration=duration,
                )

            readback_path = reservation.path
            if not self._readback_reservation_matches(reservation):
                return self._terminal_result(
                    journal,
                    OperationState.WRITE_UNCERTAIN,
                    WriteExecutionStatus.UNCERTAIN,
                    "READBACK_DESTINATION_UNSAFE",
                    "Writer exited successfully, but a safe new read-back destination is unavailable. Preserve the device state; no retry occurred.",
                    intent,
                    token,
                    writer_returncode=process.returncode,
                    writer_duration=duration,
                )
            try:
                self._assert_readback_storage(request, reservation, intent.chip_capacity_bytes)
                read_result = self.read_transport.read_chip(
                    current_evidence.probe.programmer,
                    current_evidence.probe,
                    readback_path,
                )
                if not bool(getattr(read_result, "success", False)) or not readback_path.is_file():
                    detail = str(getattr(read_result, "output", "read-only full-chip read failed"))
                    raise SPIOperationError(detail)
                if not self._readback_reservation_matches(reservation):
                    raise SPIOperationError("read-back destination identity changed during the chip read")
                comparison = self.comparator.compare(
                    intent.artifact_path,
                    readback_path,
                    chip_capacity_bytes=intent.chip_capacity_bytes,
                )
                artifact_hash_after = _hash_file(Path(intent.artifact_path))
                artifact_size_after = Path(intent.artifact_path).stat().st_size
                readback_hash_after = _hash_file(readback_path)
                readback_size_after = readback_path.stat().st_size
                if (
                    artifact_hash_after != intent.artifact_sha256
                    or artifact_size_after != intent.artifact_size_bytes
                    or comparison.expected_sha256 != intent.artifact_sha256
                    or comparison.observed_sha256 != readback_hash_after
                    or readback_size_after != intent.chip_capacity_bytes
                ):
                    raise SPIOperationError(
                        "artifact or read-back bytes changed after the approved digest was compared"
                    )
            except Exception as exc:
                return self._terminal_result(
                    journal,
                    OperationState.WRITE_UNCERTAIN,
                    WriteExecutionStatus.UNCERTAIN,
                    "READBACK_FAILED",
                    f"Independent read-back could not be verified; preserve artifact and partial read-back. No retry or reboot occurred: {exc}",
                    intent,
                    token,
                    readback_path=readback_path,
                    writer_returncode=process.returncode,
                    writer_duration=duration,
                )

            if comparison.status is not VerificationStatus.VERIFIED:
                return self._terminal_result(
                    journal,
                    OperationState.WRITE_UNCERTAIN,
                    WriteExecutionStatus.UNCERTAIN,
                    "READBACK_MISMATCH",
                    "Full-chip read-back does not exactly match the selected artifact; preserve both files. No retry, restore, or reboot occurred.",
                    intent,
                    token,
                    readback_path=readback_path,
                    comparison=comparison,
                    writer_returncode=process.returncode,
                    writer_duration=duration,
                )

            # The comparison itself reads whole files into memory. Rehash both
            # persisted files at the verification boundary and bind the value
            # written to WRITE_VERIFIED to the approved artifact digest. This
            # catches mutation after compare but before journal persistence.
            readback_hash = _hash_file(readback_path)
            artifact_hash_at_verification = _hash_file(Path(intent.artifact_path))
            if (
                readback_hash != intent.artifact_sha256
                or artifact_hash_at_verification != intent.artifact_sha256
                or readback_path.stat().st_size != intent.chip_capacity_bytes
                or Path(intent.artifact_path).stat().st_size != intent.artifact_size_bytes
                or comparison.expected_sha256 != intent.artifact_sha256
                or comparison.observed_sha256 != intent.artifact_sha256
            ):
                return self._terminal_result(
                    journal,
                    OperationState.WRITE_UNCERTAIN,
                    WriteExecutionStatus.UNCERTAIN,
                    "FINAL_VERIFICATION_DIGEST_CHANGED",
                    "Artifact or read-back changed at the final verification boundary; preserve evidence. No retry, restore, or reboot occurred.",
                    intent,
                    token,
                    readback_path=readback_path,
                    comparison=comparison,
                    writer_returncode=process.returncode,
                    writer_duration=duration,
                )
            verification_message = _canonical_json({
                "approval_id": token.approval_id,
                "intent_sha256": intent.intent_sha256,
                "artifact_sha256": intent.artifact_sha256,
                "readback_sha256": readback_hash,
                "comparison_status": comparison.status.value,
                "boot_status": VerificationStatus.NOT_PERFORMED.value,
            })
            try:
                journal.append(
                    action="external-spi-readback",
                    from_state=OperationState.WRITE_STARTED,
                    to_state=OperationState.WRITE_VERIFIED,
                    code="WRITE_VERIFIED",
                    message=verification_message,
                )
            except Exception as exc:
                return self._publish_result(WriteExecutionResult(
                    WriteExecutionStatus.UNCERTAIN,
                    "VERIFIED_READBACK_JOURNAL_FAILED",
                    request.operation_id,
                    f"Read-back matched but durable verification journal update failed; no boot test or retry occurred: {exc}",
                    OperationState.WRITE_STARTED,
                    token.approval_id,
                    intent.artifact_sha256,
                    str(readback_path),
                    readback_hash,
                    comparison,
                    VerificationStatus.NOT_PERFORMED,
                    process.returncode,
                    duration,
                ))
            return self._publish_result(WriteExecutionResult(
                WriteExecutionStatus.WRITE_VERIFIED,
                "WRITE_VERIFIED",
                request.operation_id,
                "Full-chip read-back exactly matches the selected artifact. Boot validation remains NOT_PERFORMED.",
                OperationState.WRITE_VERIFIED,
                token.approval_id,
                intent.artifact_sha256,
                str(readback_path),
                readback_hash,
                comparison,
                VerificationStatus.NOT_PERFORMED,
                process.returncode,
                duration,
            ))

    def _build_intent(self, request: WriteExecutionRequest, evidence: FreshWriteEvidence) -> WriteIntent:
        try:
            self._validate_setup(request.plan, evidence.setup)
            return self.intent_factory.create(
                request.plan,
                operation_id=request.operation_id,
                capture=evidence.capture,
                probe=evidence.probe,
                backup_set=request.backup_set,
                recovery_readiness=request.recovery_readiness,
                operation_journal_path=request.operation_journal_path,
                artifact_manifest_path=request.artifact_manifest_path,
            )
        except Exception as exc:
            raise WriteExecutionError(f"staged X200/X220 write evidence failed revalidation: {exc}") from exc

    @staticmethod
    def _validate_setup(plan: OperationPlan, setup: HardwareSetupAttestation) -> None:
        if type(setup) is not HardwareSetupAttestation:
            raise WriteExecutionError("fresh typed hardware setup attestation is required")
        if (
            not setup.actor.strip()
            or not setup.instrument_id.strip()
            or not setup.test_point.strip()
            or setup.interface_verified is not True
            or setup.wiring_verified is not True
            or setup.target_powered_off is not True
            or setup.ac_disconnected is not True
            or setup.removable_battery_removed is not True
            or setup.chip_pin1_orientation_verified is not True
            or setup.programmer_disconnect_before_repower is not True
            or setup.programmer_voltage_mv != 3300
            or setup.chip_voltage_mv != 3300
            or setup.programmer_voltage_mv != plan.programmer.voltage_mv
            or len(plan.topology.chips) != 1
            or setup.chip_voltage_mv != plan.topology.chips[0].voltage_mv
            or setup.actor != plan.programmer.attested_by
            or setup.instrument_id != plan.programmer.instrument_id
            or setup.test_point != plan.programmer.measurement_test_point
        ):
            raise WriteExecutionError(
                "fresh human setup attestation must confirm target off, AC/battery removed, pin-1 orientation, "
                "programmer disconnect before repower, measured voltage, interface, wiring, instrument and operator"
            )
        observed = _parse_utc(setup.measured_at_utc, "fresh setup measurement time")
        now = datetime.now(timezone.utc)
        if observed > now + timedelta(seconds=30) or now - observed > _CURRENT_EVIDENCE_MAX_AGE:
            raise WriteExecutionError("fresh hardware setup measurement is stale or future-dated")
        if plan.profile_id not in {"x200-libreboot", "x220-coreboot"}:
            raise WriteExecutionError("EC readiness is required only for the supported X200/X220 replacement routes")
        ec_version_fields = (
            setup.ec_version.strip(),
            setup.ec_evidence_ref.strip(),
            setup.ec_observed_at_utc.strip(),
        )
        has_any_ec_version_field = any(ec_version_fields)
        has_ec_version = all(ec_version_fields)
        has_waiver = bool(setup.ec_waiver_reason.strip())
        if has_any_ec_version_field and not has_ec_version:
            raise WriteExecutionError("EC version, evidence reference, and observation time must be recorded together")
        if has_ec_version and has_waiver:
            raise WriteExecutionError("record an EC version/update decision or one explicit named waiver, not both")
        if has_ec_version:
            if setup.ec_update_decision not in {"already-current", "updated-before-replacement"}:
                raise WriteExecutionError("record whether the observed EC version is already current or was updated before replacement")
            ec_observed = _parse_utc(setup.ec_observed_at_utc, "EC version evidence time")
            if ec_observed > now + timedelta(seconds=30) or now - ec_observed > _CURRENT_EVIDENCE_MAX_AGE:
                raise WriteExecutionError("EC version evidence is stale or future-dated")
        else:
            if setup.ec_update_decision != "waived" or not has_waiver:
                raise WriteExecutionError("an EC waiver must be explicit, timestamped, and contain no partial version fields")
        waiver_lower = setup.ec_waiver_reason.strip().casefold()
        if has_waiver and (
            len(waiver_lower) < 20 or "oem" not in waiver_lower or "update" not in waiver_lower
        ):
            raise WriteExecutionError(
                "EC waiver must be named and explain that later OEM EC update access may be lost"
            )

    def _assert_prewrite_checkpoint(self, request: WriteExecutionRequest) -> None:
        path = Path(request.operation_journal_path).expanduser()
        if _has_link_component(path) or not path.is_file():
            raise WriteExecutionError("operation journal path is missing or contains a symlink/junction")
        try:
            session = WorkflowSession.resume(request.plan, path)
        except Exception as exc:
            raise WriteExecutionError(f"exact staged operation journal could not be reopened: {exc}") from exc
        if session.state is not OperationState.BACKUP_VERIFIED:
            raise WriteExecutionError("only the exact BACKUP_VERIFIED journal state can enter the write boundary")
        if session.journal.operation_id != request.operation_id:
            raise WriteExecutionError("operation ID differs from the staged journal")

    def _load_prewrite_journal(self, request: WriteExecutionRequest) -> OperationJournal:
        try:
            journal = OperationJournal.load_checkpoint(
                request.operation_journal_path,
                expected_plan=request.plan,
            )
        except Exception as exc:
            raise WriteExecutionError(f"pre-write operation journal failed to load: {exc}") from exc
        if journal.operation_id != request.operation_id or journal.state is not OperationState.BACKUP_VERIFIED:
            raise WriteExecutionError("operation journal changed before WRITE_STARTED")
        return journal

    @staticmethod
    def _validate_request_paths(request: WriteExecutionRequest) -> None:
        profile_models = {"x200-libreboot": "x200", "x220-coreboot": "x220"}
        expected = profile_models.get(request.plan.profile_id)
        if (
            expected is None
            or request.plan.requested_model_id != expected
            or request.plan.artifact.model_id != expected
            or request.plan.artifact.model_id not in {"x200", "x220"}
        ):
            raise WriteExecutionError("only the exact X200 Libreboot and X220 Coreboot external profiles are writable")
        try:
            if str(uuid.UUID(request.operation_id)) != request.operation_id:
                raise ValueError("noncanonical UUID")
        except (ValueError, TypeError, AttributeError) as exc:
            raise WriteExecutionError("operation ID is invalid") from exc
        journal_path = Path(request.operation_journal_path).expanduser()
        manifest_path = Path(request.artifact_manifest_path).expanduser()
        if (
            not journal_path.is_absolute()
            or not manifest_path.is_absolute()
            or _has_link_component(journal_path)
            or _has_link_component(manifest_path)
        ):
            raise WriteExecutionError("journal and artifact manifest must be absolute link-free paths")

    @staticmethod
    def _lock_path(journal_path: str | Path) -> Path:
        path = Path(journal_path).expanduser().resolve(strict=True)
        return path.with_name(path.name + ".write.lock")

    @contextmanager
    def _operation_lock(
        self,
        lock_path: Path,
        operation_id: str,
        *,
        on_acquire_failure: Callable[[], None] | None = None,
    ) -> Iterator[None]:
        if not self._durability_supported():
            raise WriteExecutionError("physical writes require durable directory fsync support")
        record = _json_bytes({
            "schema_version": 1,
            "operation_id": operation_id,
            "pid": os.getpid(),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        })
        try:
            _write_exclusive(lock_path, record)
        except FileExistsError as exc:
            if on_acquire_failure is not None:
                on_acquire_failure()
            raise WriteExecutionError("another write is active or a previous attempt left a fail-closed operation lock") from exc
        except Exception as exc:
            if on_acquire_failure is not None:
                on_acquire_failure()
            raise WriteExecutionError(f"write operation lock could not be durably acquired: {exc}") from exc
        outcome = _LockOutcome()
        self._lock_local.outcome = outcome
        try:
            yield
        finally:
            try:
                lock_path.unlink()
                from autoloader.workflow.write_approval import _sync_directory
                _sync_directory(lock_path.parent)
            except OSError as exc:
                # The terminal journal/result is retained, but the caller must
                # see that the lock release itself was not durably confirmed.
                if outcome.result is None:
                    raise WriteExecutionError(
                        f"operation lock could not be durably released; later writes remain blocked: {exc}"
                    ) from exc
                outcome.result.status = WriteExecutionStatus.UNCERTAIN
                outcome.result.code = "LOCK_RELEASE_UNCERTAIN"
                outcome.result.message += (
                    f" Operation result and terminal journal were retained, but lock release could not "
                    f"be durably confirmed; do not retry automatically: {exc}"
                )
            finally:
                self._lock_local.outcome = None

    def _publish_result(self, result: WriteExecutionResult) -> WriteExecutionResult:
        outcome = getattr(self._lock_local, "outcome", None)
        if isinstance(outcome, _LockOutcome):
            outcome.result = result
        return result

    def _burn_approval(self, token: WriteApprovalToken, intent: WriteIntent) -> None:
        try:
            self.approval_ledger.consume(token, intent)
        except (AuthorizationError, WriteApprovalError):
            pass

    def _intent_files_still_match(self, intent: WriteIntent) -> bool:
        artifact = Path(intent.artifact_path)
        tool = Path(intent.command.executable)
        try:
            return (
                not _has_link_component(artifact)
                and not _has_link_component(tool)
                and artifact.is_file()
                and tool.is_file()
                and artifact.stat().st_size == intent.artifact_size_bytes
                and _hash_file(artifact) == intent.artifact_sha256
                and _hash_file(tool) == intent.command.executable_sha256
                and (
                    intent.model_id != "x200"
                    or (
                        intent.mac_preservation_manifest_path is not None
                        and intent.mac_preservation_manifest_sha256 is not None
                        and not _has_link_component(Path(intent.mac_preservation_manifest_path))
                        and Path(intent.mac_preservation_manifest_path).is_file()
                        and _hash_file(Path(intent.mac_preservation_manifest_path)) == intent.mac_preservation_manifest_sha256
                    )
                )
                and intent.command.argv == (
                    "-p", intent.programmer, "-c", intent.chip_name, "-w", intent.artifact_path,
                )
            )
        except OSError:
            return False

    @staticmethod
    def _readback_path(request: WriteExecutionRequest) -> Path:
        if len(request.backup_set.chips) != 1:
            raise WriteExecutionError("X200/X220 read-back requires exactly one verified full-chip backup")
        primary_copy = Path(request.backup_set.chips[0].first_path).expanduser()
        if _has_link_component(primary_copy) or not primary_copy.is_file():
            raise WriteExecutionError("verified primary backup copy is missing or crosses a link")
        primary_copy = primary_copy.resolve(strict=True)
        return primary_copy.parent / f"{request.operation_id}.full-chip-readback.bin"

    def _reserve_readback_destination(
        self, request: WriteExecutionRequest, *, capacity_bytes: int
    ) -> _ReadbackReservation:
        """Reserve and fsync a new persistent readback target before any write."""
        path = self._readback_path(request)
        parent = path.parent
        primary_copy_path = Path(request.backup_set.chips[0].first_path)
        backup_directory = primary_copy_path.parent.resolve(strict=True)
        if parent.resolve(strict=False) != backup_directory:
            raise WriteExecutionError("read-back destination is not under the verified primary backup operation directory")
        if (
            not path.is_absolute()
            or _has_link_component(parent)
            or not parent.is_dir()
            or _has_link_component(path)
            or path.exists()
        ):
            raise WriteExecutionError("full-chip read-back parent or new destination is unsafe or unavailable")
        storage = self._inspect_readback_storage(request, parent, capacity_bytes)
        if not storage.usable:
            raise WriteExecutionError("read-back storage is not independently verified as persistent, readable, and large enough")

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
            try:
                os.fsync(descriptor)
                observed = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            from autoloader.workflow.write_approval import _sync_directory
            _sync_directory(parent)
        except OSError as exc:
            # If creation succeeded but durability failed, keep no ambiguous
            # partial destination where cleanup itself is safe to perform.
            try:
                if path.exists() and not _has_link_component(path):
                    path.unlink()
            except OSError:
                pass
            raise WriteExecutionError(f"read-back destination could not be exclusively reserved and synced: {exc}") from exc
        reservation = _ReadbackReservation(path, observed.st_dev, observed.st_ino, storage.volume_id)
        if not self._readback_reservation_matches(reservation):
            raise WriteExecutionError("read-back destination identity changed while being reserved")
        return reservation

    @staticmethod
    def _readback_reservation_matches(reservation: _ReadbackReservation) -> bool:
        path = reservation.path
        if _has_link_component(path) or not path.is_file():
            return False
        try:
            stat = path.stat()
        except OSError:
            return False
        return stat.st_dev == reservation.device and stat.st_ino == reservation.inode

    def _assert_readback_reservation(
        self, reservation: _ReadbackReservation, *, capacity_bytes: int
    ) -> None:
        if (
            not self._readback_reservation_matches(reservation)
            or reservation.path.stat().st_size != 0
        ):
            raise WriteExecutionError("reserved read-back destination changed or no longer has sufficient free space")

    def _inspect_readback_storage(
        self, request: WriteExecutionRequest, directory: Path, capacity_bytes: int
    ) -> StorageEvidence:
        expected = request.backup_set.primary_storage
        try:
            evidence = self.storage_inspector.inspect(
                directory,
                capacity_bytes,
                persistence_confirmed=expected.persistence_confirmed,
            )
        except Exception as exc:
            raise WriteExecutionError(f"persistent read-back volume could not be independently inspected: {exc}") from exc
        volatile_filesystems = {"tmpfs", "ramfs", "overlay", "aufs", "squashfs"}
        if (
            not evidence.usable
            or evidence.volume_id != expected.volume_id
            or evidence.volume_id != request.recovery_readiness.primary_volume_id
            or Path(evidence.path).resolve(strict=False) != directory.resolve(strict=False)
            or evidence.filesystem_type is None
            or evidence.filesystem_type.casefold() in volatile_filesystems
            or not evidence.persistence_confirmed
            or not evidence.scratch_readback_verified
            or evidence.available_bytes < capacity_bytes
        ):
            raise WriteExecutionError("read-back destination does not match the verified persistent primary backup volume")
        return evidence

    def _assert_readback_storage(
        self,
        request: WriteExecutionRequest,
        reservation: _ReadbackReservation,
        capacity_bytes: int,
    ) -> None:
        if not self._readback_reservation_matches(reservation):
            raise WriteExecutionError("reserved read-back destination identity changed")
        evidence = self._inspect_readback_storage(request, reservation.path.parent, capacity_bytes)
        if evidence.volume_id != reservation.volume_id:
            raise WriteExecutionError("read-back volume identity changed after approval")

    def _remove_unstarted_reservation(self, reservation: _ReadbackReservation | None) -> None:
        if reservation is None or not self._readback_reservation_matches(reservation):
            return
        try:
            if reservation.path.stat().st_size == 0:
                reservation.path.unlink()
                from autoloader.workflow.write_approval import _sync_directory
                _sync_directory(reservation.path.parent)
        except OSError:
            # Leaving an empty path blocks a later attempt and is safer than
            # deleting a destination whose state is uncertain.
            return

    @staticmethod
    def _write_start_record(
        intent: WriteIntent,
        token: WriteApprovalToken,
        receipt: object,
        initial: FreshWriteEvidence,
        current: FreshWriteEvidence,
    ) -> str:
        command_payload = {
            "executable": intent.command.executable,
            "executable_sha256": intent.command.executable_sha256,
            "argv": intent.command.argv,
        }
        payload = {
            "approval_id": token.approval_id,
            "approval_record_sha256": token.record_sha256,
            "approval_consumed_at_utc": getattr(receipt, "consumed_at_utc", ""),
            "intent_sha256": intent.intent_sha256,
            "plan_digests": intent.plan_digests,
            "initial_capture_id": initial.capture.capture_id,
            "revalidated_capture_id": current.capture.capture_id,
            "initial_probe_id": initial.probe.probe_id,
            "revalidated_probe_id": current.probe.probe_id,
            "probe_output_sha256": current.probe.raw_output_sha256,
            "setup_attestation_sha256": _digest_json(current.setup),
            "human_setup_attestations": {
                "target_powered_off": current.setup.target_powered_off,
                "ac_disconnected": current.setup.ac_disconnected,
                "removable_battery_removed": current.setup.removable_battery_removed,
                "chip_pin1_orientation_verified": current.setup.chip_pin1_orientation_verified,
                "programmer_disconnect_before_repower": current.setup.programmer_disconnect_before_repower,
                "semantics": "human-reported attestations; not measured by AutoLoader",
            },
            "ec_readiness": {
                "version": current.setup.ec_version,
                "decision": current.setup.ec_update_decision,
                "evidence_ref": current.setup.ec_evidence_ref,
                "observed_at_utc": current.setup.ec_observed_at_utc,
                "waiver_reason": current.setup.ec_waiver_reason,
                "waiver_actor": current.setup.actor if current.setup.ec_waiver_reason else None,
                "waiver_recorded_at_utc": current.setup.measured_at_utc if current.setup.ec_waiver_reason else None,
                "automatic_update_performed": False,
            },
            "x200_mac_preservation": ({
                "policy": intent.mac_policy,
                "original_mac": intent.original_mac,
                "selected_mac_before": intent.selected_mac_before,
                "final_mac": intent.final_mac,
                "source_artifact_sha256": intent.mac_source_artifact_sha256,
                "preservation_manifest_path": intent.mac_preservation_manifest_path,
                "preservation_manifest_sha256": intent.mac_preservation_manifest_sha256,
                "write_artifact_sha256": intent.artifact_sha256,
            } if intent.model_id == "x200" else None),
            "artifact_sha256": intent.artifact_sha256,
            "artifact_manifest_sha256": intent.artifact_manifest_sha256,
            "backup_operation_id": intent.backup_operation_id,
            "primary_manifest_sha256": intent.primary_manifest_sha256,
            "recovery_manifest_sha256": intent.recovery_manifest_sha256,
            "fixed_command_sha256": _digest_json(command_payload),
        }
        return _canonical_json(payload)

    def _terminal_result(
        self,
        journal: OperationJournal,
        state: OperationState,
        status: WriteExecutionStatus,
        code: str,
        message: str,
        intent: WriteIntent,
        token: WriteApprovalToken,
        *,
        readback_path: Path | None = None,
        comparison: ReadbackComparison | None = None,
        writer_returncode: int | None = None,
        writer_duration: float | None = None,
    ) -> WriteExecutionResult:
        try:
            journal.append(
                action="external-spi-write",
                from_state=OperationState.WRITE_STARTED,
                to_state=state,
                code=code,
                message=_canonical_json({
                    "approval_id": token.approval_id,
                    "intent_sha256": intent.intent_sha256,
                    "artifact_sha256": intent.artifact_sha256,
                    "message": message,
                    "readback_status": comparison.status.value if comparison else None,
                }),
            )
        except Exception as exc:
            return self._publish_result(WriteExecutionResult(
                WriteExecutionStatus.UNCERTAIN,
                f"{code}_JOURNAL_FAILED",
                intent.operation_id,
                f"{message} Terminal journal update failed, so the record remains at WRITE_STARTED: {exc}",
                OperationState.WRITE_STARTED,
                token.approval_id,
                intent.artifact_sha256,
                str(readback_path) if readback_path else None,
                _safe_hash_file(readback_path) if readback_path else None,
                comparison,
                VerificationStatus.NOT_PERFORMED,
                writer_returncode,
                writer_duration,
            ))
        return self._publish_result(WriteExecutionResult(
            status,
            code,
            intent.operation_id,
            message,
            state,
            token.approval_id,
            intent.artifact_sha256,
            str(readback_path) if readback_path else None,
            _safe_hash_file(readback_path) if readback_path else None,
            comparison,
            VerificationStatus.NOT_PERFORMED,
            writer_returncode,
            writer_duration,
        ))

def _same_setup(left: HardwareSetupAttestation, right: HardwareSetupAttestation) -> bool:
    return (
        left.actor == right.actor
        and left.instrument_id == right.instrument_id
        and left.test_point == right.test_point
        and left.programmer_voltage_mv == right.programmer_voltage_mv
        and left.chip_voltage_mv == right.chip_voltage_mv
        and left.interface_verified is right.interface_verified
        and left.wiring_verified is right.wiring_verified
        and left.target_powered_off is right.target_powered_off
        and left.ac_disconnected is right.ac_disconnected
        and left.removable_battery_removed is right.removable_battery_removed
        and left.chip_pin1_orientation_verified is right.chip_pin1_orientation_verified
        and left.programmer_disconnect_before_repower is right.programmer_disconnect_before_repower
        and left.ec_version == right.ec_version
        and left.ec_evidence_ref == right.ec_evidence_ref
        and left.ec_waiver_reason == right.ec_waiver_reason
        and left.ec_observed_at_utc == right.ec_observed_at_utc
        and left.ec_update_decision == right.ec_update_decision
        and _parse_utc(right.measured_at_utc, "fresh setup measurement time")
        >= _parse_utc(left.measured_at_utc, "initial setup measurement time")
    )


def _same_revalidated_intent(original: WriteIntent, current: WriteIntent) -> bool:
    """Compare every binding field while allowing fresh observation IDs/times."""
    transient = {
        "device_capture_id",
        "device_capture_at_utc",
        "probe_id",
        "probe_at_utc",
        "created_at_utc",
        "intent_sha256",
    }
    return all(
        getattr(original, field_name) == getattr(current, field_name)
        for field_name in original.__dataclass_fields__
        if field_name not in transient
    )


def _digest_json(value: object) -> str:
    from dataclasses import asdict, is_dataclass
    from enum import Enum

    def normalize(item: object) -> object:
        if is_dataclass(item):
            return normalize(asdict(cast(Any, item)))
        if isinstance(item, Enum):
            return item.value
        if isinstance(item, dict):
            return {str(key): normalize(child) for key, child in item.items()}
        if isinstance(item, (tuple, list)):
            return [normalize(child) for child in item]
        if isinstance(item, Path):
            return str(item)
        return item

    return hashlib.sha256(json.dumps(normalize(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _canonical_json(value: object) -> str:
    from dataclasses import asdict, is_dataclass
    from enum import Enum

    def normalize(item: object) -> object:
        if is_dataclass(item):
            return normalize(asdict(cast(Any, item)))
        if isinstance(item, Enum):
            return item.value
        if isinstance(item, dict):
            return {str(key): normalize(child) for key, child in item.items()}
        if isinstance(item, (tuple, list)):
            return [normalize(child) for child in item]
        if isinstance(item, Path):
            return str(item)
        return item

    return json.dumps(normalize(value), sort_keys=True, separators=(",", ":"))


def _safe_hash_file(path: Path | None) -> str | None:
    if path is None or not path.is_file() or _has_link_component(path):
        return None
    try:
        return _hash_file(path)
    except OSError:
        return None


def _mint_approved_write_capability(
    intent: WriteIntent,
    token: WriteApprovalToken,
    request: WriteExecutionRequest,
    lock_path: Path,
) -> _ApprovedWriteCapability:
    """Mint a one-use subprocess capability after the durable start event."""
    if type(intent) is not WriteIntent or type(token) is not WriteApprovalToken:
        raise WriteExecutionError("a typed approved write intent is required")
    journal_path = Path(request.operation_journal_path).expanduser().resolve(strict=True)
    if _has_link_component(journal_path) or _has_link_component(lock_path) or not lock_path.is_file():
        raise WriteExecutionError("durable WRITE_STARTED journal and operation lock are required before writer capability")
    journal = OperationJournal.inspect_checkpoint(journal_path)
    if (
        journal.operation_id != request.operation_id
        or journal.state is not OperationState.WRITE_STARTED
        or not journal.entries
    ):
        raise WriteExecutionError("writer capability requires the exact operation's durable WRITE_STARTED record")
    latest = journal.entries[-1]
    try:
        record = json.loads(latest.message)
        lock_record = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WriteExecutionError("write-start record or operation lock is unreadable") from exc
    command = intent.command
    command_sha256 = _digest_json({
        "executable": command.executable,
        "executable_sha256": command.executable_sha256,
        "argv": command.argv,
    })
    if (
        latest.action != "external-spi-write"
        or latest.code != "WRITE_STARTED"
        or record.get("approval_id") != token.approval_id
        or record.get("intent_sha256") != intent.intent_sha256
        or record.get("fixed_command_sha256") != command_sha256
        or lock_record.get("operation_id") != request.operation_id
        or command.argv != ("-p", "ch341a_spi", "-c", intent.chip_name, "-w", intent.artifact_path)
    ):
        raise WriteExecutionError("write-start journal, lock, token, or fixed command does not match the approved intent")
    capability_id = str(uuid.uuid4())
    capability = _ApprovedWriteCapability(
        capability_id,
        request.operation_id,
        token.approval_id,
        intent.intent_sha256,
        str(journal_path),
        str(lock_path),
        command,
        command_sha256,
    )
    with _WRITE_CAPABILITIES_LOCK:
        _WRITE_CAPABILITIES[capability_id] = (
            request.operation_id,
            token.approval_id,
            intent.intent_sha256,
            command_sha256,
        )
    return capability


def _consume_approved_write_capability(capability: object) -> FixedWriteCommand:
    """Revalidate and consume a private capability; raw argv is never accepted."""
    if type(capability) is not _ApprovedWriteCapability:
        raise WriteExecutionError("direct writer calls require a private one-use service capability")
    with _WRITE_CAPABILITIES_LOCK:
        recorded = _WRITE_CAPABILITIES.pop(capability.capability_id, None)
    expected_record = (
        capability.operation_id,
        capability.approval_id,
        capability.intent_sha256,
        capability.command_sha256,
    )
    if recorded != expected_record or type(capability.command) is not FixedWriteCommand:
        raise WriteExecutionError("write capability is forged, stale, changed, or already consumed")
    command = capability.command
    journal_path = Path(capability.journal_path)
    lock_path = Path(capability.lock_path)
    artifact_path = Path(command.argv[-1]) if command.argv else Path()
    executable_path = Path(command.executable)
    if (
        _has_link_component(journal_path)
        or _has_link_component(lock_path)
        or _has_link_component(artifact_path)
        or _has_link_component(executable_path)
        or not journal_path.is_file()
        or not lock_path.is_file()
        or not artifact_path.is_file()
        or not executable_path.is_file()
        or not executable_path.is_absolute()
        or not artifact_path.is_absolute()
        or command.argv[0:1] != ("-p",)
        or len(command.argv) != 6
        or command.argv[1] != "ch341a_spi"
        or command.argv[2] != "-c"
        or command.argv[4] != "-w"
    ):
        raise WriteExecutionError("write capability paths or fixed external SPI argv are invalid")
    if (
        _hash_file(artifact_path) != _hash_file_for_command_intent(capability)
        or _hash_file(executable_path) != command.executable_sha256
    ):
        raise WriteExecutionError("approved artifact or writer executable changed before subprocess invocation")
    journal = OperationJournal.inspect_checkpoint(journal_path)
    if journal.state is not OperationState.WRITE_STARTED or not journal.entries:
        raise WriteExecutionError("durable WRITE_STARTED state is no longer current")
    latest = journal.entries[-1]
    try:
        record = json.loads(latest.message)
        lock_record = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WriteExecutionError("write-start journal or operation lock is unreadable") from exc
    command_sha256 = _digest_json({
        "executable": command.executable,
        "executable_sha256": command.executable_sha256,
        "argv": command.argv,
    })
    if (
        journal.operation_id != capability.operation_id
        or latest.action != "external-spi-write"
        or latest.code != "WRITE_STARTED"
        or record.get("approval_id") != capability.approval_id
        or record.get("intent_sha256") != capability.intent_sha256
        or record.get("fixed_command_sha256") != command_sha256
        or lock_record.get("operation_id") != capability.operation_id
    ):
        raise WriteExecutionError("durable write-start evidence no longer matches the one-use command capability")
    return command


def _hash_file_for_command_intent(capability: _ApprovedWriteCapability) -> str:
    """Read the intent artifact digest from the integrity-checked start event."""
    journal = OperationJournal.inspect_checkpoint(capability.journal_path)
    if journal.state is not OperationState.WRITE_STARTED or not journal.entries:
        raise WriteExecutionError("write-start journal is unavailable during capability verification")
    try:
        record = json.loads(journal.entries[-1].message)
    except json.JSONDecodeError as exc:
        raise WriteExecutionError("write-start journal message is malformed") from exc
    artifact_sha256 = record.get("artifact_sha256")
    if not isinstance(artifact_sha256, str) or len(artifact_sha256) != 64:
        raise WriteExecutionError("write-start journal has no approved artifact digest")
    return artifact_sha256
