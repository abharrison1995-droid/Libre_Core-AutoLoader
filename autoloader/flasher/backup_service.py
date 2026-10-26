"""Durable, evidence-bound, multi-chip backup capture service.

The service accepts only raw probe records from :mod:`autoloader.flasher.spi`,
requires a validated Phase 02 operation plan or artifact-free build intent, and
records two independent full-chip reads on separately verified persistent
volumes. It has no restore/write operation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, Sequence

from autoloader.exceptions import BackupUnavailableError, BackupVerificationError
from autoloader.workflow.compatibility import CompatibilityValidator
from autoloader.workflow.types import (
    BackupEvidence,
    BackupPairEvidence,
    BuildIntentEvidence,
    CompatibilityDecision,
    OperationPlan,
    ProgrammerKind,
    ProgrammerEvidence,
    build_intent_record,
    build_intent_sha256,
)

from .backup import is_degenerate_dump
from .flashrom import FlashToolIdentity, ProgrammerType
from .spi import ChipProbeEvidence, ReadOnlySPITransport, SPIOperationError


class BackupCaptureError(BackupVerificationError):
    """A backup capture failed; partial files/manifests are intentionally kept."""


class StoragePreflightError(BackupUnavailableError):
    """A backup destination is not demonstrably persistent and usable."""


_SHA256_ONLY = re.compile(r"^[0-9a-f]{64}$")
_PACKAGE_OWNER_ONLY = re.compile(r"^(?:pacman|dpkg): [^\r\n]+$")


@dataclass(frozen=True)
class StorageEvidence:
    path: str
    volume_id: str
    filesystem_type: str | None
    available_bytes: int
    required_bytes: int
    persistence_confirmed: bool
    scratch_readback_verified: bool
    observed_at_utc: str

    @property
    def usable(self) -> bool:
        return (
            bool(self.path)
            and bool(self.volume_id)
            and self.available_bytes >= self.required_bytes
            and self.persistence_confirmed
            and self.scratch_readback_verified
        )


class StorageInspector(Protocol):
    def inspect(
        self,
        directory: str | Path,
        required_bytes: int,
        *,
        persistence_confirmed: bool,
    ) -> StorageEvidence: ...


_VOLATILE_FS = frozenset({"tmpfs", "ramfs", "overlay", "aufs", "squashfs"})


def _mount_type(path: Path) -> str | None:
    mountinfo = Path("/proc/self/mountinfo")
    if not mountinfo.is_file():
        return None
    resolved = path.resolve(strict=True)
    candidates: list[tuple[int, str]] = []
    try:
        lines = mountinfo.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        fields = line.split(" - ", 1)
        if len(fields) != 2:
            continue
        before, after = fields
        left = before.split()
        right = after.split()
        if len(left) < 5 or not right:
            continue
        mount_path = left[4].replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\")
        try:
            mount = Path(mount_path).resolve(strict=False)
            resolved.relative_to(mount)
        except (OSError, ValueError):
            continue
        candidates.append((len(str(mount)), right[0].casefold()))
    return max(candidates)[1] if candidates else None


class FilesystemStorageInspector:
    """Check capacity, persistence evidence and a non-destructive scratch readback."""

    def inspect(
        self,
        directory: str | Path,
        required_bytes: int,
        *,
        persistence_confirmed: bool,
    ) -> StorageEvidence:
        path = Path(directory).expanduser().resolve(strict=False)
        if required_bytes <= 0:
            raise StoragePreflightError("storage preflight requires a positive byte count")
        if not path.is_dir():
            raise StoragePreflightError(f"backup destination is not an existing directory: {path}")
        stat = path.stat()
        free_bytes = shutil.disk_usage(path).free
        filesystem_type = _mount_type(path)
        in_system_temp = _is_below(path, Path(tempfile.gettempdir()).resolve(strict=False))
        if os.name == "nt":
            persistence_proved = persistence_confirmed and not in_system_temp
        else:
            persistence_proved = (
                persistence_confirmed
                and not in_system_temp
                and filesystem_type is not None
                and filesystem_type not in _VOLATILE_FS
            )
        scratch_ok = False
        if persistence_proved and free_bytes >= required_bytes:
            scratch_ok = self._scratch_readback(path)
        return StorageEvidence(
            path=str(path),
            volume_id=str(stat.st_dev),
            filesystem_type=filesystem_type,
            available_bytes=free_bytes,
            required_bytes=required_bytes,
            persistence_confirmed=persistence_proved,
            scratch_readback_verified=scratch_ok,
            observed_at_utc=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _scratch_readback(directory: Path) -> bool:
        data = b"LibreCore persistent storage preflight\x00" + uuid.uuid4().bytes
        path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=".librecore-preflight-", dir=directory, delete=False
            ) as stream:
                path = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            saved = path.read_bytes()
            if hashlib.sha256(saved).digest() != hashlib.sha256(data).digest():
                return False
            return True
        except OSError:
            return False
        finally:
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass


def _is_below(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class BackupManifestError(BackupCaptureError):
    """A manifest is corrupt or cannot be persisted atomically."""


class BackupManifestStore:
    """Atomic append-only manifest; malformed or checksum-invalid data blocks."""

    schema_version = 1

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).expanduser().resolve(strict=False)
        self.path = self.directory / "backup-manifest.json"

    def read_events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BackupManifestError(f"backup manifest cannot be read: {self.path}") from exc
        if not isinstance(document, dict) or document.get("schema_version") != self.schema_version:
            raise BackupManifestError(f"backup manifest has an unsupported schema: {self.path}")
        events = document.get("events")
        if not isinstance(events, list):
            raise BackupManifestError(f"backup manifest events are malformed: {self.path}")
        previous = "0" * 64
        for index, record in enumerate(events, start=1):
            if not isinstance(record, dict) or record.get("sequence") != index:
                raise BackupManifestError(f"backup manifest event {index} has invalid sequence data")
            if record.get("previous_sha256") != previous:
                raise BackupManifestError(f"backup manifest event {index} breaks its checksum chain")
            payload = {key: value for key, value in record.items() if key != "event_sha256"}
            expected_event = _sha256(_canonical(payload))
            if record.get("event_sha256") != expected_event:
                raise BackupManifestError(f"backup manifest event {index} checksum is invalid")
            previous = expected_event
        body = {key: value for key, value in document.items() if key != "manifest_sha256"}
        if document.get("manifest_sha256") != _sha256(_canonical(body)):
            raise BackupManifestError(f"backup manifest checksum is invalid: {self.path}")
        return events

    def append(self, event: dict[str, Any]) -> str:
        events = self.read_events()
        previous = events[-1]["event_sha256"] if events else "0" * 64
        record = {
            "sequence": len(events) + 1,
            "previous_sha256": previous,
            **event,
        }
        record["event_sha256"] = _sha256(_canonical(record))
        events.append(record)
        body = {"schema_version": self.schema_version, "events": events}
        document = {**body, "manifest_sha256": _sha256(_canonical(body))}
        self._atomic_write(_canonical(document))
        return str(document["manifest_sha256"])

    def _atomic_write(self, content: bytes) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=f".{self.path.name}.", suffix=".tmp", dir=self.directory, delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
            _fsync_directory(self.directory)
        except OSError as exc:
            raise BackupManifestError(f"backup manifest could not be persisted: {self.path}") from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


@dataclass(frozen=True)
class CapturedChipBackup:
    chip_id: str
    observed_chip_name: str
    capacity_bytes: int
    first_path: str
    second_path: str
    first_sha256: str
    second_sha256: str
    first_capture_id: str
    second_capture_id: str


@dataclass(frozen=True)
class BackupSetResult:
    operation_id: str
    profile_id: str
    created_at_utc: str
    tool_name: str
    tool_version: str
    programmer: str
    backup: BackupEvidence
    chips: tuple[CapturedChipBackup, ...]
    primary_manifest: str
    recovery_manifest: str
    primary_manifest_sha256: str
    recovery_manifest_sha256: str
    primary_storage: StorageEvidence
    recovery_storage: StorageEvidence


class BackupSetService:
    """Capture a complete two-read backup set without exposing any write path."""

    def __init__(
        self,
        transport: ReadOnlySPITransport,
        *,
        storage_inspector: StorageInspector | None = None,
        validator: CompatibilityValidator | None = None,
    ) -> None:
        self.transport = transport
        self.storage_inspector = storage_inspector or FilesystemStorageInspector()
        self.validator = validator or CompatibilityValidator()

    def capture(
        self,
        plan: OperationPlan | BuildIntentEvidence,
        probes: Sequence[ChipProbeEvidence],
        *,
        programmer: ProgrammerType,
        programmer_evidence: ProgrammerEvidence | None = None,
        primary_directory: str | Path,
        recovery_directory: str | Path,
        primary_persistent_confirmed: bool,
        recovery_persistent_confirmed: bool,
    ) -> BackupSetResult:
        decision = (
            self.validator.validate(plan)
            if isinstance(plan, OperationPlan)
            else self.validator.validate_build_intent(plan)
        )
        if decision.decision is not CompatibilityDecision.ALLOWED:
            raise BackupUnavailableError(
                "backup blocked because the operation plan is not fully compatible: "
                + "; ".join(decision.reasons)
            )
        backup_programmer_evidence = programmer_evidence or plan.programmer
        _validate_external_backup_programmer(plan, programmer, backup_programmer_evidence)
        chip_map = _match_probes_to_topology(plan, probes, programmer)
        current_tool = self.transport.inspect_tool()
        if current_tool is None or not _same_tool_identity(current_tool, probes[0].tool):
            raise BackupUnavailableError(
                "the current flash tool executable/version differs from the exact probe evidence"
            )
        required_bytes = sum(chip.capacity_bytes for chip in plan.topology.chips)
        primary_storage = self.storage_inspector.inspect(
            primary_directory,
            required_bytes,
            persistence_confirmed=primary_persistent_confirmed,
        )
        recovery_storage = self.storage_inspector.inspect(
            recovery_directory,
            required_bytes,
            persistence_confirmed=recovery_persistent_confirmed,
        )
        _validate_storage_pair(primary_storage, recovery_storage, required_bytes)

        operation_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        primary_store = BackupManifestStore(primary_storage.path)
        recovery_store = BackupManifestStore(recovery_storage.path)
        # Parse/check both manifests before touching the programmer.
        primary_store.read_events()
        recovery_store.read_events()
        common = _base_manifest_event(
            plan,
            operation_id,
            created_at,
            programmer,
            backup_programmer_evidence,
            probes,
        )
        start_event = {**common, "status": "in-progress", "failure": None, "chips": []}
        primary_manifest_hash = primary_store.append(start_event)
        recovery_manifest_hash = recovery_store.append(start_event)

        primary_op_dir = Path(primary_storage.path) / operation_id
        recovery_op_dir = Path(recovery_storage.path) / operation_id
        backups: list[CapturedChipBackup] = []
        partial_paths: list[Path] = []
        try:
            primary_op_dir.mkdir(parents=True, exist_ok=False)
            recovery_op_dir.mkdir(parents=True, exist_ok=False)
            for index, chip in enumerate(plan.topology.chips, start=1):
                probe = chip_map[chip.chip_id]
                safe_name = f"chip-{index:02d}-{_safe_name(chip.chip_id)}"
                first_capture = str(uuid.uuid4())
                second_capture = str(uuid.uuid4())
                first_partial = primary_op_dir / f"{safe_name}-read-1.partial"
                second_partial = recovery_op_dir / f"{safe_name}-read-2.partial"
                partial_paths.extend((first_partial, second_partial))
                first_result = self.transport.read_chip(programmer, probe, first_partial)
                if not first_result.success:
                    raise BackupCaptureError(f"first independent read failed for chip {chip.chip_id}: {first_result.output}")
                _validate_dump(first_partial, chip.capacity_bytes, chip.chip_id, "first")
                second_result = self.transport.read_chip(programmer, probe, second_partial)
                if not second_result.success:
                    raise BackupCaptureError(f"second independent read failed for chip {chip.chip_id}: {second_result.output}")
                _validate_dump(second_partial, chip.capacity_bytes, chip.chip_id, "second")
                first_hash = _hash_file(first_partial)
                second_hash = _hash_file(second_partial)
                if first_hash != second_hash:
                    raise BackupCaptureError(
                        f"independent reads differ for chip {chip.chip_id}; both files were preserved"
                    )
                first_path = first_partial.with_suffix(".rom")
                second_path = second_partial.with_suffix(".rom")
                os.replace(first_partial, first_path)
                os.replace(second_partial, second_path)
                _fsync_directory(first_path.parent)
                _fsync_directory(second_path.parent)
                _validate_dump(first_path, chip.capacity_bytes, chip.chip_id, "persisted first")
                _validate_dump(second_path, chip.capacity_bytes, chip.chip_id, "persisted second")
                if _hash_file(first_path) != first_hash or _hash_file(second_path) != second_hash:
                    raise BackupCaptureError(f"persisted backup changed after read for chip {chip.chip_id}")
                backups.append(CapturedChipBackup(
                    chip_id=chip.chip_id,
                    observed_chip_name=probe.chip_name,
                    capacity_bytes=chip.capacity_bytes,
                    first_path=str(first_path),
                    second_path=str(second_path),
                    first_sha256=first_hash,
                    second_sha256=second_hash,
                    first_capture_id=first_capture,
                    second_capture_id=second_capture,
                ))

            backup_evidence = BackupEvidence(tuple(
                BackupPairEvidence(
                    chip_id=entry.chip_id,
                    first_path=entry.first_path,
                    second_path=entry.second_path,
                    first_sha256=entry.first_sha256,
                    second_sha256=entry.second_sha256,
                    first_capture_id=entry.first_capture_id,
                    second_capture_id=entry.second_capture_id,
                    transient=False,
                )
                for entry in backups
            ))
            verified_plan = replace(plan, backup=backup_evidence)
            backup_decision = self.validator.validate_backup(verified_plan)
            if backup_decision.decision is not CompatibilityDecision.ALLOWED:
                raise BackupCaptureError(
                    "saved backup set failed independent policy validation: " + "; ".join(backup_decision.reasons)
                )
            completed_at = datetime.now(timezone.utc).isoformat()
            verified_event = {
                **common,
                "status": "verified",
                "completed_at_utc": completed_at,
                "failure": None,
                "primary_storage": _storage_record(primary_storage),
                "recovery_storage": _storage_record(recovery_storage),
                "chips": [_chip_record(entry) for entry in backups],
                "region_map": [_region_record(region) for region in plan.topology.regions],
                "manifest_contract": "independent full-chip reads; matching size and SHA-256",
            }
            primary_manifest_hash = primary_store.append(verified_event)
            recovery_manifest_hash = recovery_store.append(verified_event)
            return BackupSetResult(
                operation_id=operation_id,
                profile_id=plan.profile_id,
                created_at_utc=created_at,
                tool_name=probes[0].tool.name,
                tool_version=probes[0].tool.version,
                programmer=programmer.value,
                backup=backup_evidence,
                chips=tuple(backups),
                primary_manifest=str(primary_store.path),
                recovery_manifest=str(recovery_store.path),
                primary_manifest_sha256=primary_manifest_hash,
                recovery_manifest_sha256=recovery_manifest_hash,
                primary_storage=primary_storage,
                recovery_storage=recovery_storage,
            )
        except Exception as exc:
            failure_event = {
                **common,
                "status": "failed",
                "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                "failure": f"{type(exc).__name__}: {exc}",
                "preserved_paths": [str(path) for path in partial_paths if path.exists()],
                "chips": [_chip_record(entry) for entry in backups],
                "automatic_retry": False,
                "automatic_reboot": False,
            }
            try:
                primary_manifest_hash = primary_store.append(failure_event)
                recovery_manifest_hash = recovery_store.append(failure_event)
            except BackupManifestError as manifest_error:
                raise BackupCaptureError(
                    f"backup failed and the failure manifest could not be replicated; preserve all files: {manifest_error}"
                ) from exc
            if isinstance(exc, BackupCaptureError):
                raise
            if isinstance(exc, (OSError, SPIOperationError)):
                raise BackupCaptureError(f"backup failed; evidence was retained: {exc}") from exc
            raise

    def load_verified_result(
        self,
        plan: OperationPlan | BuildIntentEvidence,
        *,
        operation_id: str,
        primary_manifest: str | Path,
        recovery_manifest: str | Path,
        primary_manifest_sha256: str,
        recovery_manifest_sha256: str,
        expected_tool: FlashToolIdentity | None = None,
    ) -> BackupSetResult:
        """Reconstruct a result only from matching verified manifests and reread files.

        The method never treats a caller JSON object as a ``BackupSetResult``.
        Hashes, sizes, checksum chains, plan fingerprints, storage records and
        both persisted copies are rechecked before a typed value is returned.
        """
        first_path, second_path = Path(primary_manifest), Path(recovery_manifest)
        for path, expected in (
            (first_path, primary_manifest_sha256),
            (second_path, recovery_manifest_sha256),
        ):
            if (
                not path.is_absolute()
                or path.name != "backup-manifest.json"
                or _path_has_link_component(path)
                or not path.is_file()
            ):
                raise BackupManifestError("verified backup manifest path is missing or unsafe")
            if not isinstance(expected, str) or len(expected) != 64 or any(
                character not in "0123456789abcdef" for character in expected
            ):
                raise BackupManifestError("verified backup checkpoint checksum is malformed")
        primary_store = BackupManifestStore(first_path.parent)
        recovery_store = BackupManifestStore(second_path.parent)
        primary_events = primary_store.read_events()
        recovery_events = recovery_store.read_events()
        primary = _verified_event_for(primary_events, operation_id, plan)
        recovery = _verified_event_for(recovery_events, operation_id, plan)
        try:
            primary_document = json.loads(first_path.read_text(encoding="utf-8"))
            recovery_document = json.loads(second_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BackupManifestError("verified backup manifest cannot be reread") from exc
        # BackupManifestStore.append returns the canonical whole-document
        # checksum. read_events() above independently verifies that checksum
        # and every event in the chain before this checkpoint binding is used.
        if (
            not isinstance(primary_document, dict)
            or primary_document.get("manifest_sha256") != primary_manifest_sha256
            or not isinstance(recovery_document, dict)
            or recovery_document.get("manifest_sha256") != recovery_manifest_sha256
        ):
            raise BackupManifestError("verified backup manifest checksum differs from its checkpoint")
        if _event_payload(primary) != _event_payload(recovery):
            raise BackupManifestError("primary and recovery backup events disagree")
        chips_value = primary.get("chips")
        if not isinstance(chips_value, list) or len(chips_value) != len(plan.topology.chips):
            raise BackupManifestError("verified backup event does not cover every expected chip")
        captured: list[CapturedChipBackup] = []
        pairs: list[BackupPairEvidence] = []
        for item in chips_value:
            if not isinstance(item, dict):
                raise BackupManifestError("verified backup chip record is malformed")
            try:
                chip = CapturedChipBackup(
                    chip_id=_required_string(item, "chip_id"),
                    observed_chip_name=_required_string(item, "observed_chip_name"),
                    capacity_bytes=_required_positive_int(item, "capacity_bytes"),
                    first_path=_required_string(item, "first_path"),
                    second_path=_required_string(item, "second_path"),
                    first_sha256=_required_sha256(item, "first_sha256"),
                    second_sha256=_required_sha256(item, "second_sha256"),
                    first_capture_id=_required_string(item, "first_capture_id"),
                    second_capture_id=_required_string(item, "second_capture_id"),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise BackupManifestError("verified backup chip record is malformed") from exc
            for label, path_value, expected_digest, size_key in (
                ("primary", chip.first_path, chip.first_sha256, "first_size_bytes"),
                ("recovery", chip.second_path, chip.second_sha256, "second_size_bytes"),
            ):
                dump = Path(path_value)
                if (
                    not dump.is_absolute()
                    or _path_has_link_component(dump)
                    or not dump.is_file()
                    or dump.stat().st_size != chip.capacity_bytes
                ):
                    raise BackupManifestError(f"{label} copy for {chip.chip_id} is missing or unsafe")
                if item.get(size_key) != chip.capacity_bytes or _hash_file(dump) != expected_digest:
                    raise BackupManifestError(f"{label} copy for {chip.chip_id} changed since capture")
            expected_chip = next((candidate for candidate in plan.topology.chips if candidate.chip_id == chip.chip_id), None)
            if expected_chip is None or expected_chip.capacity_bytes != chip.capacity_bytes:
                raise BackupManifestError("verified backup chip differs from current observed topology")
            captured.append(chip)
            pairs.append(BackupPairEvidence(
                chip_id=chip.chip_id,
                first_path=chip.first_path,
                second_path=chip.second_path,
                first_sha256=chip.first_sha256,
                second_sha256=chip.second_sha256,
                first_capture_id=chip.first_capture_id,
                second_capture_id=chip.second_capture_id,
                transient=False,
            ))
        primary_storage = _storage_from_record(primary.get("primary_storage"))
        recovery_storage = _storage_from_record(primary.get("recovery_storage"))
        if (
            Path(primary_storage.path).resolve(strict=False) != first_path.parent.resolve(strict=False)
            or Path(recovery_storage.path).resolve(strict=False) != second_path.parent.resolve(strict=False)
        ):
            raise BackupManifestError("storage paths differ from the verified manifest locations")
        required_bytes = sum(chip.capacity_bytes for chip in captured)
        _validate_storage_pair(primary_storage, recovery_storage, required_bytes)
        primary_root = Path(primary_storage.path).resolve(strict=True)
        recovery_root = Path(recovery_storage.path).resolve(strict=True)
        try:
            for chip in captured:
                first_dump = Path(chip.first_path).resolve(strict=True)
                second_dump = Path(chip.second_path).resolve(strict=True)
                first_dump.relative_to(primary_root)
                second_dump.relative_to(recovery_root)
                if first_dump == second_dump or first_dump.samefile(second_dump):
                    raise BackupManifestError("primary and recovery backup copies must be distinct files")
        except (OSError, ValueError) as exc:
            raise BackupManifestError(
                "backup dump paths must stay under their separately recorded storage directories"
            ) from exc
        backup_evidence = BackupEvidence(tuple(pairs))
        rebuilt_plan = replace(plan, backup=backup_evidence) if isinstance(plan, OperationPlan) else replace(plan, backup=backup_evidence)
        decision = self.validator.validate(rebuilt_plan) if isinstance(rebuilt_plan, OperationPlan) else self.validator.validate_build_intent(rebuilt_plan)
        if decision.decision is not CompatibilityDecision.ALLOWED:
            # The pre-build intent can remain allowed without a backup; verify
            # the captured pair against a temporary operation plan instead.
            if not pairs or any(pair.transient for pair in pairs):
                raise BackupManifestError("backup evidence failed policy validation")
        if isinstance(plan, BuildIntentEvidence):
            if primary.get("profile_id") != plan.profile_id or primary.get("build_intent_sha256") != build_intent_sha256(plan):
                raise BackupManifestError("backup event is not bound to the exact current pre-build intent")
        elif primary.get("profile_id") != plan.profile_id:
            raise BackupManifestError("backup event profile differs from the exact operation plan")
        programmer = primary.get("programmer")
        tool = primary.get("tool")
        created = primary.get("completed_at_utc")
        if not isinstance(programmer, str) or not isinstance(tool, dict) or not isinstance(created, str):
            raise BackupManifestError("verified backup event lacks tool, programmer, or timestamp metadata")
        try:
            selected_programmer = ProgrammerType(programmer)
        except ValueError as exc:
            raise BackupManifestError("verified backup event names an unsupported programmer") from exc
        if selected_programmer is ProgrammerType.INTERNAL:
            raise BackupManifestError("verified backup event cannot use the internal programmer")
        tool_name = _required_string(tool, "name")
        tool_version = _required_string(tool, "version")
        tool_executable = _required_string(tool, "executable")
        tool_sha256 = _required_string(tool, "sha256")
        tool_package_owner = _required_string(tool, "package_owner")
        if not Path(tool_executable).is_absolute():
            raise BackupManifestError("verified backup tool executable path is not absolute")
        if not _SHA256_ONLY.fullmatch(tool_sha256) or not _PACKAGE_OWNER_ONLY.fullmatch(tool_package_owner):
            raise BackupManifestError("verified backup tool provenance is malformed")
        if expected_tool is not None and (
            tool_name.casefold() != expected_tool.name.casefold()
            or tool_version != expected_tool.version
            or Path(tool_executable).resolve(strict=False) != Path(expected_tool.executable).resolve(strict=False)
            or tool_sha256 != expected_tool.sha256
            or tool_package_owner != expected_tool.package_owner
        ):
            raise BackupManifestError("backup tool identity differs from the fresh local probe")
        expected_device_identifiers = {
            "raw_dmi_product_name": plan.device.raw_dmi_product_name,
            "raw_mtm": plan.device.raw_mtm,
            "raw_bios_version": plan.device.raw_bios_version,
        }
        if primary.get("device_identifiers") != expected_device_identifiers:
            raise BackupManifestError("backup event device identity differs from the current plan")
        expected_regions = [_region_record(region) for region in plan.topology.regions]
        if primary.get("regions") != expected_regions:
            raise BackupManifestError("backup event region map differs from the current topology")
        topology = primary.get("topology")
        if not isinstance(topology, list) or len(topology) != len(plan.topology.chips):
            raise BackupManifestError("backup event chip topology is missing or incomplete")
        topology_by_id = {item.get("chip_id"): item for item in topology if isinstance(item, dict)}
        if len(topology_by_id) != len(topology):
            raise BackupManifestError("backup event contains duplicate or malformed chip topology")
        for expected_chip in plan.topology.chips:
            item = topology_by_id.get(expected_chip.chip_id)
            captured_chip = next((chip for chip in captured if chip.chip_id == expected_chip.chip_id), None)
            if item is None or captured_chip is None or any(
                item.get(key) != value
                for key, value in (
                    ("physical_label", expected_chip.physical_label),
                    ("capacity_bytes", expected_chip.capacity_bytes),
                    ("voltage_mv", expected_chip.voltage_mv),
                    ("voltage_evidence_source", expected_chip.voltage_evidence_source),
                    ("voltage_attested_by", expected_chip.voltage_attested_by),
                    ("voltage_attested_at", expected_chip.voltage_attested_at),
                    ("voltage_instrument_id", expected_chip.voltage_instrument_id),
                    ("voltage_test_point", expected_chip.voltage_test_point),
                    ("observed_chip_name", captured_chip.observed_chip_name),
                )
            ):
                raise BackupManifestError("backup event chip evidence differs from the current observed topology")
        evidence = primary.get("programmer_evidence")
        if not isinstance(evidence, dict) or any(
            evidence.get(key) != expected
            for key, expected in (
                ("kind", plan.programmer.kind.value),
                ("interface_verified", plan.programmer.interface_verified),
                ("wiring_verified", plan.programmer.wiring_verified),
                ("voltage_mv", plan.programmer.voltage_mv),
                ("evidence_source", plan.programmer.evidence_source),
                ("attested_by", plan.programmer.attested_by),
                ("attested_at", plan.programmer.attested_at),
                ("instrument_id", plan.programmer.instrument_id),
                ("measurement_test_point", plan.programmer.measurement_test_point),
            )
        ):
            raise BackupManifestError("backup programmer setup provenance differs from current attestation")
        try:
            programmer_evidence = ProgrammerEvidence(
                kind=ProgrammerKind(_required_string(evidence, "kind")),
                observed=True,
                interface_verified=evidence.get("interface_verified") is True,
                wiring_verified=evidence.get("wiring_verified") is True,
                voltage_mv=_required_positive_int(evidence, "voltage_mv"),
                evidence_source=_required_string(evidence, "evidence_source"),
                attested_by=_required_string(evidence, "attested_by"),
                attested_at=_required_string(evidence, "attested_at"),
                instrument_id=_required_string(evidence, "instrument_id"),
                measurement_test_point=_required_string(evidence, "measurement_test_point"),
            )
            _validate_external_backup_programmer(plan, selected_programmer, programmer_evidence)
        except (TypeError, ValueError, BackupUnavailableError) as exc:
            raise BackupManifestError("verified backup external programmer evidence failed policy validation") from exc
        return BackupSetResult(
            operation_id=operation_id,
            profile_id=plan.profile_id,
            created_at_utc=created,
            tool_name=tool_name,
            tool_version=tool_version,
            programmer=programmer,
            backup=backup_evidence,
            chips=tuple(captured),
            primary_manifest=str(first_path),
            recovery_manifest=str(second_path),
            primary_manifest_sha256=primary_manifest_sha256,
            recovery_manifest_sha256=recovery_manifest_sha256,
            primary_storage=primary_storage,
            recovery_storage=recovery_storage,
        )


def _validate_external_backup_programmer(
    plan: OperationPlan | BuildIntentEvidence,
    programmer: ProgrammerType,
    evidence: ProgrammerEvidence,
) -> None:
    if evidence.kind is not ProgrammerKind.EXTERNAL_SPI or not evidence.observed:
        raise BackupUnavailableError("backup requires independently observed external SPI programmer evidence")
    if evidence.interface_verified is not True or evidence.wiring_verified is not True:
        raise BackupUnavailableError("backup requires verified programmer interface and wiring evidence")
    if evidence.voltage_mv != 3300:
        raise BackupUnavailableError("backup programmer output must be verified at exactly 3300 mV")
    if programmer is ProgrammerType.INTERNAL:
        raise BackupUnavailableError("the generic internal programmer is not an allowed Phase 03 backup transport")
    for chip in plan.topology.chips:
        if not chip.observed or chip.voltage_mv != evidence.voltage_mv:
            raise BackupUnavailableError(f"chip {chip.chip_id} must have observed voltage matching the programmer")


def _match_probes_to_topology(
    plan: OperationPlan | BuildIntentEvidence,
    probes: Sequence[ChipProbeEvidence],
    programmer: ProgrammerType,
) -> dict[str, ChipProbeEvidence]:
    if not plan.topology.observed or not plan.topology.chips:
        raise BackupUnavailableError("a complete observed chip topology is required before backup capture")
    if len(probes) != len(plan.topology.chips):
        raise BackupUnavailableError("probe evidence must cover exactly every chip in the observed topology")
    if len({probe.probe_id for probe in probes}) != len(probes):
        raise BackupUnavailableError("each chip requires a distinct raw probe record")
    expected_capacities = [chip.capacity_bytes for chip in plan.topology.chips]
    observed_capacities = [probe.capacity_bytes for probe in probes]
    if len(set(expected_capacities)) != len(expected_capacities) or len(set(observed_capacities)) != len(observed_capacities):
        raise BackupUnavailableError("chip-to-probe mapping is ambiguous for duplicate capacities")
    if sorted(expected_capacities) != sorted(observed_capacities):
        raise BackupUnavailableError("raw probe capacities do not match the observed topology exactly")
    mapped: dict[str, ChipProbeEvidence] = {}
    for probe in probes:
        probe.validate_integrity()
        if probe.programmer is not programmer:
            raise BackupUnavailableError("probe programmer differs from the selected backup programmer")
    tools = {
        (
            probe.tool.name,
            probe.tool.executable,
            probe.tool.version,
            probe.tool.sha256,
            probe.tool.package_owner,
        )
        for probe in probes
    }
    if len(tools) != 1:
        raise BackupUnavailableError("all chips must be probed using the same exact tool executable and version")
    by_capacity = {probe.capacity_bytes: probe for probe in probes}
    for chip in plan.topology.chips:
        mapped[chip.chip_id] = by_capacity[chip.capacity_bytes]
    return mapped


def _same_tool_identity(current: FlashToolIdentity, probed: FlashToolIdentity) -> bool:
    return (
        current.name.casefold(),
        str(Path(current.executable).resolve(strict=False)),
        current.version,
        current.raw_version,
        current.sha256,
        current.package_owner,
    ) == (
        probed.name.casefold(),
        str(Path(probed.executable).resolve(strict=False)),
        probed.version,
        probed.raw_version,
        probed.sha256,
        probed.package_owner,
    )


def _validate_storage_pair(primary: StorageEvidence, recovery: StorageEvidence, required_bytes: int) -> None:
    for label, evidence in (("primary", primary), ("recovery", recovery)):
        path = Path(evidence.path)
        if not path.is_absolute() or _path_has_link_component(path):
            raise StoragePreflightError(f"{label} backup destination must be absolute and link-free")
        if not evidence.usable or evidence.required_bytes < required_bytes:
            raise StoragePreflightError(
                f"{label} destination lacks persistent, writable, rereadable capacity for the full backup set"
            )
    if Path(primary.path).resolve(strict=False) == Path(recovery.path).resolve(strict=False):
        raise StoragePreflightError("primary and recovery copies must be stored at different destinations")
    if primary.volume_id == recovery.volume_id:
        raise StoragePreflightError("primary and recovery copies must be on separately identified persistent volumes")


def _path_has_link_component(path: Path) -> bool:
    """Return true when an existing component is a symlink or Windows junction."""
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.relative_to(current).parts:
        current = current / part
        try:
            if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
                return True
        except OSError:
            return True
    return False


def _validate_dump(path: Path, capacity: int, chip_id: str, pass_label: str) -> None:
    if not path.is_file():
        raise BackupCaptureError(f"{pass_label} read did not create a file for chip {chip_id}")
    if path.stat().st_size != capacity:
        raise BackupCaptureError(
            f"{pass_label} read for chip {chip_id} has {path.stat().st_size} bytes; expected exactly {capacity}"
        )
    if is_degenerate_dump(path.read_bytes()):
        raise BackupCaptureError(f"{pass_label} read for chip {chip_id} is blank or degenerate")


def _safe_name(value: str) -> str:
    text = "".join(char if char.isalnum() or char in "-_" else "_" for char in value.strip())
    return text[:48] or "chip"


def _base_manifest_event(
    plan: OperationPlan | BuildIntentEvidence,
    operation_id: str,
    created_at: str,
    programmer: ProgrammerType,
    programmer_evidence: ProgrammerEvidence,
    probes: Sequence[ChipProbeEvidence],
) -> dict[str, Any]:
    probe_by_capacity = {probe.capacity_bytes: probe for probe in probes}
    tool = probes[0].tool
    event: dict[str, Any] = {
        "operation_id": operation_id,
        "created_at_utc": created_at,
        "profile_id": plan.profile_id,
        "requested_model_id": plan.requested_model_id,
        "device_identifiers": {
            "raw_dmi_product_name": plan.device.raw_dmi_product_name,
            "raw_mtm": plan.device.raw_mtm,
            "raw_bios_version": plan.device.raw_bios_version,
        },
        "programmer": programmer.value,
        "programmer_evidence": {
            "kind": programmer_evidence.kind.value,
            "interface_verified": programmer_evidence.interface_verified,
            "wiring_verified": programmer_evidence.wiring_verified,
            "voltage_mv": programmer_evidence.voltage_mv,
            "evidence_source": programmer_evidence.evidence_source,
            "attested_by": programmer_evidence.attested_by,
            "attested_at": programmer_evidence.attested_at,
            "instrument_id": programmer_evidence.instrument_id,
            "measurement_test_point": programmer_evidence.measurement_test_point,
        },
        "tool": {
            "name": tool.name,
            "executable": tool.executable,
            "version": tool.version,
            "sha256": tool.sha256,
            "package_owner": tool.package_owner,
        },
        "probe_records": [
            {
                "probe_id": probe.probe_id,
                "chip_name": probe.chip_name,
                "capacity_bytes": probe.capacity_bytes,
                "raw_output_sha256": probe.raw_output_sha256,
                "observed_at_utc": probe.observed_at_utc,
            }
            for probe in probes
        ],
        "topology": [
            {
                "chip_id": chip.chip_id,
                "physical_label": chip.physical_label,
                "capacity_bytes": chip.capacity_bytes,
                "voltage_mv": chip.voltage_mv,
                "voltage_evidence_source": chip.voltage_evidence_source,
                "voltage_attested_by": chip.voltage_attested_by,
                "voltage_attested_at": chip.voltage_attested_at,
                "voltage_instrument_id": chip.voltage_instrument_id,
                "voltage_test_point": chip.voltage_test_point,
                "observed_chip_name": probe_by_capacity[chip.capacity_bytes].chip_name,
            }
            for chip in plan.topology.chips
        ],
        "regions": [_region_record(region) for region in plan.topology.regions],
    }
    if isinstance(plan, OperationPlan):
        event["source_artifact"] = {
            "sha256": plan.artifact.expected_sha256,
            "size_bytes": plan.artifact.expected_size_bytes,
            "source_revision": plan.artifact.source_revision,
        }
        event["build_intent"] = None
        event["build_intent_sha256"] = None
    else:
        event["source_artifact"] = None
        event["build_intent"] = build_intent_record(plan)
        event["build_intent_sha256"] = build_intent_sha256(plan)
    return event


def _storage_record(evidence: StorageEvidence) -> dict[str, Any]:
    return {
        "path": evidence.path,
        "volume_id": evidence.volume_id,
        "filesystem_type": evidence.filesystem_type,
        "available_bytes": evidence.available_bytes,
        "required_bytes": evidence.required_bytes,
        "persistent_confirmed": evidence.persistence_confirmed,
        "scratch_readback_verified": evidence.scratch_readback_verified,
        "observed_at_utc": evidence.observed_at_utc,
    }


def _region_record(region: Any) -> dict[str, Any]:
    return {"name": region.name, "start_byte": region.start_byte, "end_byte": region.end_byte}


def _chip_record(entry: CapturedChipBackup) -> dict[str, Any]:
    return {
        "chip_id": entry.chip_id,
        "observed_chip_name": entry.observed_chip_name,
        "capacity_bytes": entry.capacity_bytes,
        "first_path": entry.first_path,
        "second_path": entry.second_path,
        "first_size_bytes": Path(entry.first_path).stat().st_size if Path(entry.first_path).is_file() else 0,
        "second_size_bytes": Path(entry.second_path).stat().st_size if Path(entry.second_path).is_file() else 0,
        "first_sha256": entry.first_sha256,
        "second_sha256": entry.second_sha256,
        "first_capture_id": entry.first_capture_id,
        "second_capture_id": entry.second_capture_id,
    }


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in event.items()
        if key not in {"sequence", "previous_sha256", "event_sha256"}
    }


def _verified_event_for(
    events: Sequence[dict[str, Any]],
    operation_id: str,
    plan: OperationPlan | BuildIntentEvidence,
) -> dict[str, Any]:
    matches = [
        event for event in events
        if event.get("operation_id") == operation_id
        and event.get("profile_id") == plan.profile_id
        and event.get("status") == "verified"
    ]
    if len(matches) != 1:
        raise BackupManifestError("backup manifest must contain exactly one verified event for this operation")
    event = matches[0]
    if isinstance(plan, BuildIntentEvidence):
        if event.get("build_intent_sha256") != build_intent_sha256(plan):
            raise BackupManifestError("backup event does not match the current exact build intent")
    else:
        artifact = event.get("source_artifact")
        if not isinstance(artifact, dict) or (
            artifact.get("sha256") != plan.artifact.expected_sha256
            or artifact.get("size_bytes") != plan.artifact.expected_size_bytes
            or artifact.get("source_revision") != plan.artifact.source_revision
        ):
            raise BackupManifestError("backup event does not match the current exact operation artifact")
    return event


def _required_string(value: dict[str, Any], key: str) -> str:
    field_value = value.get(key)
    if not isinstance(field_value, str) or not field_value:
        raise ValueError(f"{key} must be non-empty text")
    return field_value


def _required_positive_int(value: dict[str, Any], key: str) -> int:
    field_value = value.get(key)
    if type(field_value) is not int or field_value <= 0:
        raise ValueError(f"{key} must be a positive integer")
    return field_value


def _required_sha256(value: dict[str, Any], key: str) -> str:
    field_value = _required_string(value, key)
    if len(field_value) != 64 or any(char not in "0123456789abcdef" for char in field_value):
        raise ValueError(f"{key} must be a lowercase SHA-256 digest")
    return field_value


def _storage_from_record(value: Any) -> StorageEvidence:
    if not isinstance(value, dict):
        raise BackupManifestError("verified backup storage record is malformed")
    try:
        result = StorageEvidence(
            path=_required_string(value, "path"),
            volume_id=_required_string(value, "volume_id"),
            filesystem_type=value.get("filesystem_type") if value.get("filesystem_type") is None else _required_string(value, "filesystem_type"),
            available_bytes=_required_positive_int(value, "available_bytes"),
            required_bytes=_required_positive_int(value, "required_bytes"),
            persistence_confirmed=value.get("persistent_confirmed") is True,
            scratch_readback_verified=value.get("scratch_readback_verified") is True,
            observed_at_utc=_required_string(value, "observed_at_utc"),
        )
    except (TypeError, ValueError) as exc:
        raise BackupManifestError("verified backup storage record is malformed") from exc
    if not result.usable:
        raise BackupManifestError("verified backup storage is no longer persistent and readable")
    return result
