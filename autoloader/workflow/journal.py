"""Durable, checksummed operation journal and safe checkpoint loading.

The journal persists audit evidence, not write authority. Checkpoints at or
after AUTHORIZED are rejected on resume; Phase 02 has no write-capable resume
path and every write request remains blocked by ``WorkflowSession``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from .types import (
    ArtifactEvidence,
    BackupEvidence,
    OperationPlan,
    OperationState,
    RegionEvidence,
    normalized_path,
)

_SCHEMA_VERSION = 1
_GENESIS_CHECKSUM = "0" * 64
_RESUMABLE_CHECKPOINT_STATES = frozenset({
    OperationState.DRAFT,
    OperationState.VALIDATED,
    OperationState.BACKUP_VERIFIED,
})
_TERMINAL_INSPECTION_STATES = frozenset({
    OperationState.BLOCKED,
    OperationState.FAILED,
    OperationState.WRITE_BLOCKED,
})
_ALLOWED_TRANSITIONS: dict[OperationState, frozenset[OperationState]] = {
    OperationState.DRAFT: frozenset({OperationState.VALIDATED, OperationState.BLOCKED}),
    OperationState.VALIDATED: frozenset({OperationState.BACKUP_VERIFIED, OperationState.BLOCKED}),
    OperationState.BACKUP_VERIFIED: frozenset({OperationState.AUTHORIZED, OperationState.BLOCKED}),
    OperationState.AUTHORIZED: frozenset({OperationState.WRITE_BLOCKED, OperationState.BLOCKED}),
    OperationState.WRITE_BLOCKED: frozenset(),
    OperationState.BLOCKED: frozenset(),
    OperationState.FAILED: frozenset(),
    OperationState.READBACK_VERIFIED: frozenset(),
    OperationState.COMPLETE: frozenset(),
}


def _default_journal_path(operation_id: str) -> Path:
    """Choose a per-user state directory for durable operation records."""
    if os.environ.get("LOCALAPPDATA"):
        root = Path(os.environ["LOCALAPPDATA"]) / "LibreCoreAutoLoader" / "state"
    elif os.environ.get("XDG_STATE_HOME"):
        root = Path(os.environ["XDG_STATE_HOME"]) / "libre-core-autoloader"
    else:
        root = Path.home() / ".local" / "state" / "libre-core-autoloader"
    return root / "operations" / f"{operation_id}.json"


class JournalError(RuntimeError):
    """Base class for journal persistence and checkpoint errors."""


class JournalCorruptionError(JournalError):
    """A checkpoint is malformed or its checksum chain is invalid."""


class UnsafeCheckpointError(JournalError):
    """A checkpoint is not a safe, explicitly supported resume point."""


class JournalPlanMismatchError(JournalError):
    """The current operation evidence differs from the saved checkpoint."""


class JournalConflictError(JournalError):
    """Another writer changed this checkpoint since it was loaded."""


@dataclass(frozen=True)
class ArtifactJournalMetadata:
    path: str
    model_id: str
    firmware_family: str
    source_url: str
    source_revision: str
    expected_size_bytes: int
    expected_sha256: str
    actual_size_bytes: int | None
    actual_sha256: str | None
    regions: tuple[RegionEvidence, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "regions", tuple(self.regions))


@dataclass(frozen=True)
class BackupChecksumMetadata:
    chip_id: str
    capture_id: str
    path: str
    declared_sha256: str
    actual_size_bytes: int | None
    actual_sha256: str | None


@dataclass(frozen=True)
class JournalEntry:
    sequence: int
    operation_id: str
    timestamp_utc: str
    action: str
    from_state: OperationState
    to_state: OperationState
    code: str
    message: str
    failure: bool
    failure_code: str | None
    failure_message: str | None
    device_fingerprint: str
    artifact_metadata: ArtifactJournalMetadata
    backup_checksums: tuple[BackupChecksumMetadata, ...]
    previous_checksum: str
    checksum: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "backup_checksums", tuple(self.backup_checksums))


def _canonical(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {item.name: _canonical(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Path):
        return normalized_path(str(value))
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _checksum(value: Any) -> str:
    serialized = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _parse_utc_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("journal timestamps must be timezone-aware UTC")
    return parsed


def _file_evidence(path_value: str) -> tuple[int | None, str | None]:
    path = Path(path_value)
    try:
        if not path.is_file():
            return None, None
        size = path.stat().st_size
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return size, digest.hexdigest()
    except OSError:
        return None, None


def _device_fingerprint(plan: OperationPlan) -> str:
    return _checksum({
        "profile_id": plan.profile_id,
        "requested_model_id": plan.requested_model_id,
        "firmware_family": plan.firmware_family,
        "device": plan.device,
        "topology": plan.topology,
        "programmer": plan.programmer,
    })


def _artifact_metadata(artifact: ArtifactEvidence) -> ArtifactJournalMetadata:
    path = normalized_path(artifact.path)
    actual_size, actual_sha256 = _file_evidence(artifact.path)
    return ArtifactJournalMetadata(
        path=path,
        model_id=artifact.model_id,
        firmware_family=artifact.firmware_family.value,
        source_url=artifact.source_url,
        source_revision=artifact.source_revision,
        expected_size_bytes=artifact.expected_size_bytes,
        expected_sha256=artifact.expected_sha256,
        actual_size_bytes=actual_size,
        actual_sha256=actual_sha256,
        regions=artifact.regions,
    )


def _backup_checksums(backup: BackupEvidence) -> tuple[BackupChecksumMetadata, ...]:
    records: list[BackupChecksumMetadata] = []
    for pair in backup.pairs:
        for capture_id, path_value, declared_sha256 in (
            (pair.first_capture_id, pair.first_path, pair.first_sha256),
            (pair.second_capture_id, pair.second_path, pair.second_sha256),
        ):
            size, actual_sha256 = _file_evidence(path_value)
            records.append(BackupChecksumMetadata(
                chip_id=pair.chip_id,
                capture_id=capture_id,
                path=normalized_path(path_value),
                declared_sha256=declared_sha256,
                actual_size_bytes=size,
                actual_sha256=actual_sha256,
            ))
    return tuple(records)


def _entry_payload(entry: JournalEntry, *, include_checksum: bool) -> dict[str, Any]:
    payload = {
        "sequence": entry.sequence,
        "operation_id": entry.operation_id,
        "timestamp_utc": entry.timestamp_utc,
        "action": entry.action,
        "from_state": entry.from_state.value,
        "to_state": entry.to_state.value,
        "code": entry.code,
        "message": entry.message,
        "failure": entry.failure,
        "failure_code": entry.failure_code,
        "failure_message": entry.failure_message,
        "device_fingerprint": entry.device_fingerprint,
        "artifact_metadata": _canonical(entry.artifact_metadata),
        "backup_checksums": _canonical(entry.backup_checksums),
        "previous_checksum": entry.previous_checksum,
    }
    if include_checksum:
        payload["checksum"] = entry.checksum
    return payload


def _parse_artifact_metadata(value: Any) -> ArtifactJournalMetadata:
    if not isinstance(value, dict):
        raise JournalCorruptionError("artifact metadata is missing or malformed")
    try:
        regions = tuple(RegionEvidence(**region) for region in value["regions"])
        return ArtifactJournalMetadata(
            path=value["path"],
            model_id=value["model_id"],
            firmware_family=value["firmware_family"],
            source_url=value["source_url"],
            source_revision=value["source_revision"],
            expected_size_bytes=value["expected_size_bytes"],
            expected_sha256=value["expected_sha256"],
            actual_size_bytes=value["actual_size_bytes"],
            actual_sha256=value["actual_sha256"],
            regions=regions,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise JournalCorruptionError("artifact metadata fields are invalid") from exc


def _parse_backup_metadata(value: Any) -> tuple[BackupChecksumMetadata, ...]:
    if not isinstance(value, list):
        raise JournalCorruptionError("backup checksum metadata is malformed")
    try:
        return tuple(BackupChecksumMetadata(**record) for record in value)
    except (TypeError, ValueError) as exc:
        raise JournalCorruptionError("backup checksum metadata fields are invalid") from exc


class OperationJournal:
    """Append-only logical journal persisted with atomic replacement/checksums."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        plan: OperationPlan | None = None,
        operation_id: str | None = None,
    ) -> None:
        self.operation_id = operation_id or str(uuid.uuid4())
        try:
            uuid.UUID(self.operation_id)
        except (ValueError, AttributeError) as exc:
            raise ValueError("operation_id must be a UUID") from exc
        self.path = (
            Path(path).expanduser().resolve(strict=False)
            if path is not None
            else _default_journal_path(self.operation_id).resolve(strict=False)
        )
        if self.path.exists():
            raise FileExistsError(f"operation journal already exists; use load_checkpoint to resume: {self.path}")
        self.created_at_utc = datetime.now(timezone.utc).isoformat()
        self.updated_at_utc = self.created_at_utc
        self._entries: list[JournalEntry] = []
        self._state = OperationState.DRAFT
        self._device_fingerprint: str | None = None
        self._artifact_metadata: ArtifactJournalMetadata | None = None
        self._backup_checksums: tuple[BackupChecksumMetadata, ...] = ()
        self._snapshot_checksum: str | None = None
        self._read_only = False
        self._lock = threading.RLock()
        if plan is not None:
            self.bind_plan(plan)

    @property
    def state(self) -> OperationState:
        with self._lock:
            return self._state

    @property
    def entries(self) -> tuple[JournalEntry, ...]:
        with self._lock:
            return tuple(self._entries)

    @property
    def device_fingerprint(self) -> str | None:
        return self._device_fingerprint

    @property
    def artifact_metadata(self) -> ArtifactJournalMetadata | None:
        return self._artifact_metadata

    @property
    def backup_checksums(self) -> tuple[BackupChecksumMetadata, ...]:
        return self._backup_checksums

    def bind_plan(self, plan: OperationPlan) -> None:
        """Bind a new journal to a plan or require an exact checkpoint match."""
        with self._lock:
            fingerprint = _device_fingerprint(plan)
            artifact = _artifact_metadata(plan.artifact)
            backups = _backup_checksums(plan.backup)
            if self._device_fingerprint is not None:
                if (
                    fingerprint != self._device_fingerprint
                    or artifact != self._artifact_metadata
                    or backups != self._backup_checksums
                ):
                    raise JournalPlanMismatchError("operation evidence differs from the saved journal checkpoint")
                return
            self._device_fingerprint = fingerprint
            self._artifact_metadata = artifact
            self._backup_checksums = backups
            self._persist(self._entries, self._state)

    def update_plan(self, plan: OperationPlan) -> None:
        """Stage backup evidence for the next transition's atomic persistence."""
        with self._lock:
            if self._read_only:
                raise UnsafeCheckpointError("inspected operation journals are read-only")
            if self._device_fingerprint is None or self._artifact_metadata is None:
                raise JournalError("bind a plan before updating journal evidence")
            fingerprint = _device_fingerprint(plan)
            artifact = _artifact_metadata(plan.artifact)
            if fingerprint != self._device_fingerprint or artifact != self._artifact_metadata:
                raise JournalPlanMismatchError("device or artifact evidence cannot change within an operation")
            backups = _backup_checksums(plan.backup)
            if backups == self._backup_checksums:
                return
            self._backup_checksums = backups
            # The next state transition persists this evidence atomically with
            # its event. A crash before then leaves the prior safe checkpoint.

    def append(
        self,
        *,
        action: str,
        from_state: OperationState,
        to_state: OperationState,
        code: str,
        message: str,
    ) -> JournalEntry:
        with self._lock:
            if self._read_only:
                raise UnsafeCheckpointError("inspected operation journals are read-only")
            if self._device_fingerprint is None or self._artifact_metadata is None:
                raise JournalError("journal must be bound to a plan before recording transitions")
            if from_state is not self._state:
                raise JournalError("journal transition does not start at the current checkpoint state")
            if to_state not in _ALLOWED_TRANSITIONS[from_state]:
                raise UnsafeCheckpointError(f"unsupported journal transition {from_state.value} -> {to_state.value}")
            timestamp = datetime.now(timezone.utc).isoformat()
            previous = self._entries[-1].checksum if self._entries else _GENESIS_CHECKSUM
            is_failure = to_state in {OperationState.BLOCKED, OperationState.FAILED}
            entry = JournalEntry(
                sequence=len(self._entries) + 1,
                operation_id=self.operation_id,
                timestamp_utc=timestamp,
                action=action,
                from_state=from_state,
                to_state=to_state,
                code=code,
                message=message,
                failure=is_failure,
                failure_code=code if is_failure else None,
                failure_message=message if is_failure else None,
                device_fingerprint=self._device_fingerprint,
                artifact_metadata=self._artifact_metadata,
                backup_checksums=self._backup_checksums,
                previous_checksum=previous,
                checksum="",
            )
            entry = JournalEntry(**{
                **entry.__dict__,
                "checksum": _checksum({"previous_checksum": previous, "event": _entry_payload(entry, include_checksum=False)}),
            })
            candidate = self._entries + [entry]
            self._persist(candidate, to_state, updated_at_utc=timestamp)
            self._entries = candidate
            self._state = to_state
            self.updated_at_utc = timestamp
            return entry

    def _snapshot_payload(
        self,
        entries: list[JournalEntry],
        state: OperationState,
        updated_at_utc: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "operation_id": self.operation_id,
            "created_at_utc": self.created_at_utc,
            "updated_at_utc": updated_at_utc,
            "state": state.value,
            "device_fingerprint": self._device_fingerprint,
            "artifact_metadata": _canonical(self._artifact_metadata),
            "backup_checksums": _canonical(self._backup_checksums),
            "entries": [_entry_payload(entry, include_checksum=True) for entry in entries],
        }

    def _persist(
        self,
        entries: list[JournalEntry],
        state: OperationState,
        *,
        updated_at_utc: str | None = None,
    ) -> None:
        updated_at = updated_at_utc or datetime.now(timezone.utc).isoformat()
        body = self._snapshot_payload(entries, state, updated_at)
        snapshot_checksum = _checksum(body)
        complete = {**body, "checkpoint_checksum": snapshot_checksum}
        encoded = json.dumps(complete, sort_keys=True, separators=(",", ":")).encode("utf-8")

        if self.path.exists():
            if self._snapshot_checksum is None:
                raise FileExistsError(f"refusing to overwrite an existing operation journal: {self.path}")
            try:
                current = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(current, dict):
                    raise JournalCorruptionError("existing checkpoint root is malformed before append")
                current_checksum = current.get("checkpoint_checksum")
                current_body = {key: value for key, value in current.items() if key != "checkpoint_checksum"}
            except (OSError, json.JSONDecodeError, AttributeError) as exc:
                raise JournalCorruptionError("existing checkpoint cannot be read before append") from exc
            if not isinstance(current_checksum, str) or not hmac.compare_digest(
                current_checksum, _checksum(current_body)
            ):
                raise JournalCorruptionError("existing checkpoint checksum is invalid before append")
            if current_checksum != self._snapshot_checksum:
                raise JournalConflictError("operation checkpoint changed since this journal was loaded")
        elif self._snapshot_checksum is not None:
            raise JournalConflictError("operation checkpoint was removed since it was loaded")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
                delete=False,
            ) as stream:
                temp_path = Path(stream.name)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.path)
            temp_path = None
            self._snapshot_checksum = snapshot_checksum
        except OSError as exc:
            raise JournalError(f"could not persist operation checkpoint: {exc}") from exc
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @classmethod
    def load_checkpoint(
        cls,
        path: str | Path,
        *,
        expected_plan: OperationPlan | None = None,
    ) -> OperationJournal:
        """Load a resumable pre-authorization checkpoint only."""
        return cls._load_checkpoint(path, expected_plan=expected_plan, allow_terminal_inspection=False)

    @classmethod
    def inspect_checkpoint(
        cls,
        path: str | Path,
        *,
        expected_plan: OperationPlan | None = None,
    ) -> OperationJournal:
        """Read a verified journal, including terminal records, without resuming it."""
        return cls._load_checkpoint(path, expected_plan=expected_plan, allow_terminal_inspection=True)

    @classmethod
    def _load_checkpoint(
        cls,
        path: str | Path,
        *,
        expected_plan: OperationPlan | None,
        allow_terminal_inspection: bool,
    ) -> OperationJournal:
        checkpoint_path = Path(path).expanduser().resolve(strict=False)
        try:
            document = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise JournalCorruptionError("checkpoint is missing, truncated or invalid JSON") from exc
        if not isinstance(document, dict):
            raise JournalCorruptionError("checkpoint root must be a JSON object")
        checkpoint_checksum = document.get("checkpoint_checksum")
        body = {key: value for key, value in document.items() if key != "checkpoint_checksum"}
        if not isinstance(checkpoint_checksum, str) or not hmac.compare_digest(checkpoint_checksum, _checksum(body)):
            raise JournalCorruptionError("checkpoint checksum does not match its contents")
        if body.get("schema_version") != _SCHEMA_VERSION:
            raise JournalCorruptionError("checkpoint schema version is unsupported")
        try:
            operation_id = str(body["operation_id"])
            uuid.UUID(operation_id)
            created_at = str(body["created_at_utc"])
            updated_at = str(body["updated_at_utc"])
            created_time = _parse_utc_timestamp(created_at)
            updated_time = _parse_utc_timestamp(updated_at)
            if updated_time < created_time:
                raise ValueError("updated timestamp precedes creation")
            state = OperationState(body["state"])
            device_fingerprint = body["device_fingerprint"]
            if not isinstance(device_fingerprint, str) or len(device_fingerprint) != 64:
                raise ValueError("invalid device fingerprint")
            artifact_metadata = _parse_artifact_metadata(body["artifact_metadata"])
            backup_checksums = _parse_backup_metadata(body["backup_checksums"])
            raw_entries = body["entries"]
            if not isinstance(raw_entries, list):
                raise ValueError("entries must be a list")
        except (KeyError, TypeError, ValueError) as exc:
            raise JournalCorruptionError("checkpoint metadata is malformed") from exc

        entries: list[JournalEntry] = []
        prior_state = OperationState.DRAFT
        prior_checksum = _GENESIS_CHECKSUM
        prior_time = created_time
        for index, raw_entry in enumerate(raw_entries, start=1):
            if not isinstance(raw_entry, dict):
                raise JournalCorruptionError("journal event is not an object")
            try:
                entry = JournalEntry(
                    sequence=raw_entry["sequence"],
                    operation_id=raw_entry["operation_id"],
                    timestamp_utc=raw_entry["timestamp_utc"],
                    action=raw_entry["action"],
                    from_state=OperationState(raw_entry["from_state"]),
                    to_state=OperationState(raw_entry["to_state"]),
                    code=raw_entry["code"],
                    message=raw_entry["message"],
                    failure=raw_entry["failure"],
                    failure_code=raw_entry["failure_code"],
                    failure_message=raw_entry["failure_message"],
                    device_fingerprint=raw_entry["device_fingerprint"],
                    artifact_metadata=_parse_artifact_metadata(raw_entry["artifact_metadata"]),
                    backup_checksums=_parse_backup_metadata(raw_entry["backup_checksums"]),
                    previous_checksum=raw_entry["previous_checksum"],
                    checksum=raw_entry["checksum"],
                )
                event_time = _parse_utc_timestamp(entry.timestamp_utc)
            except (KeyError, TypeError, ValueError) as exc:
                raise JournalCorruptionError(f"journal event {index} has malformed fields") from exc
            if entry.sequence != index or entry.operation_id != operation_id:
                raise JournalCorruptionError(f"journal event {index} has an invalid sequence or operation ID")
            if event_time < prior_time or event_time > updated_time:
                raise JournalCorruptionError(f"journal event {index} has a non-monotonic timestamp")
            if entry.from_state is not prior_state or entry.to_state not in _ALLOWED_TRANSITIONS[prior_state]:
                raise JournalCorruptionError(f"journal event {index} has an illegal state transition")
            if entry.previous_checksum != prior_checksum:
                raise JournalCorruptionError(f"journal event {index} breaks the checksum chain")
            if entry.device_fingerprint != device_fingerprint or entry.artifact_metadata != artifact_metadata:
                raise JournalCorruptionError(f"journal event {index} changes device or artifact identity")
            if index > 1 and entry.backup_checksums != entries[-1].backup_checksums:
                if not (
                    entry.from_state is OperationState.VALIDATED
                    and entry.action == "verify-backup"
                    and entry.to_state in {OperationState.BACKUP_VERIFIED, OperationState.BLOCKED}
                ):
                    raise JournalCorruptionError(f"journal event {index} changes backup evidence outside verification")
            expected_event_checksum = _checksum({
                "previous_checksum": entry.previous_checksum,
                "event": _entry_payload(entry, include_checksum=False),
            })
            if not hmac.compare_digest(entry.checksum, expected_event_checksum):
                raise JournalCorruptionError(f"journal event {index} checksum is invalid")
            expected_failure = entry.to_state in {OperationState.BLOCKED, OperationState.FAILED}
            if entry.failure != expected_failure:
                raise JournalCorruptionError(f"journal event {index} has inconsistent failure fields")
            if expected_failure:
                if not entry.failure_code or entry.failure_code != entry.code or entry.failure_message != entry.message:
                    raise JournalCorruptionError(f"journal event {index} has incomplete failure details")
            elif entry.failure_code is not None or entry.failure_message is not None:
                raise JournalCorruptionError(f"journal event {index} has unexpected failure details")
            prior_state = entry.to_state
            prior_checksum = entry.checksum
            prior_time = event_time
            entries.append(entry)

        if prior_state is not state:
            raise JournalCorruptionError("checkpoint state does not match the final journal transition")
        if entries and entries[-1].backup_checksums != backup_checksums:
            raise JournalCorruptionError("checkpoint backup evidence does not match the final journal event")
        if not entries and state is not OperationState.DRAFT:
            raise JournalCorruptionError("empty journal must remain in DRAFT")
        if state not in _RESUMABLE_CHECKPOINT_STATES:
            if not allow_terminal_inspection or state not in _TERMINAL_INSPECTION_STATES:
                raise UnsafeCheckpointError(f"checkpoint at {state.value} is not a supported resume point")

        journal = object.__new__(cls)
        journal.path = checkpoint_path
        journal.operation_id = operation_id
        journal.created_at_utc = created_at
        journal.updated_at_utc = updated_at
        journal._entries = entries
        journal._state = state
        journal._device_fingerprint = device_fingerprint
        journal._artifact_metadata = artifact_metadata
        journal._backup_checksums = backup_checksums
        journal._snapshot_checksum = checkpoint_checksum
        journal._read_only = allow_terminal_inspection
        journal._lock = threading.RLock()
        if expected_plan is not None:
            if (
                _device_fingerprint(expected_plan) != device_fingerprint
                or _artifact_metadata(expected_plan.artifact) != artifact_metadata
                or _backup_checksums(expected_plan.backup) != backup_checksums
            ):
                raise JournalPlanMismatchError("supplied plan does not match the saved checkpoint evidence")
        return journal
