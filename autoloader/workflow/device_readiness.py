"""Read-only review of persisted V1 operation evidence.

The report summarizes evidence already on disk. It never probes a device,
resumes a workflow, changes a checkpoint, requests approval, or authorizes a
write. ``REVIEW_INPUTS_COMPLETE`` means only that the supplied persisted
records and byte references were reopened and checked; it is not a hardware
readiness or acceptance result.
"""
from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from autoloader.workflow.journal import BackupChecksumMetadata, OperationJournal
from autoloader.workflow.post_reboot import PostRebootEvidenceError, PostRebootEvidenceService
from autoloader.workflow.types import OperationState

ReportGateStatus = Literal["PASS", "UNKNOWN"]
_PROFILE_IDS = {"t430-1vyrain", "x200-libreboot", "x220-coreboot"}
_MAX_EVIDENCE_AGE = timedelta(hours=24)
_FUTURE_SKEW = timedelta(seconds=30)


class DeviceReadinessReportError(ValueError):
    """Persisted inputs cannot be safely reviewed as one operation."""


@dataclass(frozen=True)
class ReadinessGate:
    name: str
    status: ReportGateStatus
    detail: str
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class DeviceReadinessReport:
    outcome: str
    profile_id: str
    operation_id: str
    checkpoint_path: str
    checkpoint_sha256: str
    device_fingerprint_sha256: str
    reviewed_at_utc: str
    gates: tuple[ReadinessGate, ...]
    hardware_ready: bool = False
    write_authorized: bool = False
    disclaimer: str = (
        "Persisted inputs were reviewed only. This report does not establish current hardware state, "
        "firmware acceptance, or authorization to write."
    )


class DeviceReadinessReportService:
    """Reopen one terminal V1 checkpoint and rehash its persisted byte inputs."""

    @classmethod
    def inspect(
        cls,
        profile_id: str,
        checkpoint_path: str | Path,
        *,
        now: datetime | None = None,
    ) -> DeviceReadinessReport:
        if profile_id not in _PROFILE_IDS:
            raise DeviceReadinessReportError("an allowlisted V1 profile is required")
        now_utc = now or datetime.now(timezone.utc)
        if now_utc.tzinfo is None or now_utc.utcoffset() != timedelta(0):
            raise DeviceReadinessReportError("report time must be timezone-aware UTC")
        path = cls._safe_regular_file(Path(checkpoint_path))
        before_hash, _ = cls._hash(path)
        try:
            opened = PostRebootEvidenceService._open_checkpoint(profile_id, path)
        except Exception as exc:
            raise DeviceReadinessReportError(f"persisted terminal checkpoint failed validation: {exc}") from exc

        operation_time = opened.get("operation_time")
        if not isinstance(operation_time, datetime) or operation_time.tzinfo is None:
            raise DeviceReadinessReportError("checkpoint operation time is missing or malformed")
        operation_time = operation_time.astimezone(timezone.utc)
        if operation_time > now_utc + _FUTURE_SKEW:
            raise DeviceReadinessReportError("checkpoint operation time is future-dated")
        if now_utc - operation_time > _MAX_EVIDENCE_AGE:
            raise DeviceReadinessReportError(
                "terminal checkpoint operation timestamp exceeds the report's 24-hour recency limit"
            )

        gates: list[ReadinessGate] = [ReadinessGate(
            "terminal-checkpoint",
            "PASS",
            "The checksummed terminal record matches the selected profile and operation identity.",
            (str(path), f"sha256:{before_hash}"),
        )]
        fingerprint = opened.get("device_fingerprint_sha256")
        if not isinstance(fingerprint, str) or len(fingerprint) != 64:
            raise DeviceReadinessReportError("checkpoint device fingerprint is missing or malformed")
        operation_id = opened.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id:
            raise DeviceReadinessReportError("checkpoint operation ID is missing or malformed")

        if profile_id in {"x200-libreboot", "x220-coreboot"}:
            journal = OperationJournal.inspect_checkpoint(path)
            if journal.state is not OperationState.WRITE_VERIFIED or journal.operation_id != operation_id:
                raise DeviceReadinessReportError("terminal journal state or operation binding changed during review")
            gates.append(ReadinessGate(
                "artifact-readback",
                "PASS",
                "Pinned artifact and full-chip read-back bytes rehash to the terminal WRITE_VERIFIED record.",
                (f"operation:{operation_id}",),
            ))
            if profile_id == "x200-libreboot":
                gates.append(ReadinessGate(
                    "x200-mac-preservation",
                    "PASS",
                    "The existing validator reopened the MAC-preservation manifest and bound it to the final ROM hash.",
                    (f"operation:{operation_id}",),
                ))
            backup_refs = cls._verify_backup_bytes(journal)
            gates.append(ReadinessGate(
                "backup-bytes",
                "PASS",
                "Every persisted full-chip backup copy referenced by the journal still matches its recorded size and SHA-256.",
                backup_refs,
            ))
            gates.append(ReadinessGate(
                "recovery-manifests-and-volumes",
                "UNKNOWN",
                "The terminal journal does not persist the typed recovery plan, both manifest paths, or volume identities needed to rerun RecoveryReadinessService without reconstructing unsupported evidence.",
            ))
        else:
            gates.append(ReadinessGate(
                "consumed-t430-return",
                "PASS",
                "The checksummed consumed-return validator confirmed the pinned option-1 attestation and return chronology.",
                (f"operation:{operation_id}",),
            ))
            gates.append(ReadinessGate(
                "firmware-command-and-acceptance",
                "UNKNOWN",
                "The T430 checkpoint records operator-reported return evidence; it does not prove the upstream flash command result or establish independent firmware acceptance.",
            ))

        gates.append(ReadinessGate(
            "current-device-and-boot-state",
            "UNKNOWN",
            "This read-only report uses persisted evidence only and does not capture current DMI/BIOS or test a boot.",
        ))
        sidecar = path.with_name(path.name + ".post-reboot-pending.json")
        if sidecar.exists() or sidecar.is_symlink():
            try:
                payload = PostRebootEvidenceService.verify_report(
                    sidecar,
                    expected_checkpoint_hash=before_hash,
                    expected_operation_id=operation_id,
                )
            except Exception as exc:
                raise DeviceReadinessReportError(f"existing pending post-reboot report failed revalidation: {exc}") from exc
            gates.append(ReadinessGate(
                "post-reboot-observations",
                "PASS",
                "A valid human-reported post-reboot sidecar is present and remains pending acceptance.",
                (str(sidecar), f"status:{payload['report_status']}"),
            ))
        else:
            gates.append(ReadinessGate(
                "post-reboot-observations",
                "UNKNOWN",
                "No persisted post-reboot observation sidecar is present.",
            ))

        after_hash, _ = cls._hash(path)
        if after_hash != before_hash:
            raise DeviceReadinessReportError("terminal checkpoint changed while it was being reviewed")
        return DeviceReadinessReport(
            outcome="REVIEW_INPUTS_COMPLETE",
            profile_id=profile_id,
            operation_id=operation_id,
            checkpoint_path=str(path),
            checkpoint_sha256=before_hash,
            device_fingerprint_sha256=fingerprint,
            reviewed_at_utc=now_utc.isoformat(),
            gates=tuple(gates),
        )

    @staticmethod
    def _safe_regular_file(path: Path) -> Path:
        candidate = Path(os.path.abspath(path.expanduser()))
        current = Path(candidate.anchor)
        for part in candidate.parts[1:]:
            current = current / part
            try:
                mode = current.lstat().st_mode
            except OSError as exc:
                raise DeviceReadinessReportError(f"checkpoint path is missing or inaccessible: {current}") from exc
            if stat.S_ISLNK(mode) or (callable(getattr(current, "is_junction", None)) and current.is_junction()):
                raise DeviceReadinessReportError(f"checkpoint path contains a symlink or junction: {current}")
            if current != candidate and not stat.S_ISDIR(mode):
                raise DeviceReadinessReportError(f"checkpoint parent is not a directory: {current}")
            if current == candidate and not stat.S_ISREG(mode):
                raise DeviceReadinessReportError("checkpoint must be a regular non-symlink file")
        return candidate

    @staticmethod
    def _hash(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
                size += len(block)
        return digest.hexdigest(), size

    @classmethod
    def _verify_backup_bytes(cls, journal: OperationJournal) -> tuple[str, ...]:
        records = journal.backup_checksums
        if not records:
            raise DeviceReadinessReportError("terminal journal has no persisted full-chip backup references")
        by_chip: dict[str, list[BackupChecksumMetadata]] = {}
        refs: list[str] = []
        for record in records:
            if not record.chip_id or not record.capture_id or record.actual_sha256 != record.declared_sha256:
                raise DeviceReadinessReportError("terminal journal backup reference is incomplete or inconsistent")
            backup_path = cls._safe_regular_file(Path(record.path))
            actual_hash, actual_size = cls._hash(backup_path)
            if actual_hash != record.declared_sha256 or record.actual_size_bytes != actual_size:
                raise DeviceReadinessReportError(f"persisted backup copy changed: {backup_path}")
            by_chip.setdefault(record.chip_id, []).append(record)
            refs.append(f"{backup_path}#{actual_hash}")
        for chip_id, copies in by_chip.items():
            if len(copies) != 2 or len({item.capture_id for item in copies}) != 2:
                raise DeviceReadinessReportError(f"journal does not contain exactly two distinct backup reads for {chip_id}")
            first, second = (Path(item.path) for item in copies)
            try:
                if first.resolve(strict=True) == second.resolve(strict=True) or first.samefile(second):
                    raise DeviceReadinessReportError(f"backup copies for {chip_id} alias the same file")
            except OSError as exc:
                raise DeviceReadinessReportError(f"backup copy identity could not be checked for {chip_id}") from exc
        return tuple(refs)
