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
    tools = {(probe.tool.name, probe.tool.executable, probe.tool.version) for probe in probes}
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
    ) == (
        probed.name.casefold(),
        str(Path(probed.executable).resolve(strict=False)),
        probed.version,
        probed.raw_version,
    )


def _validate_storage_pair(primary: StorageEvidence, recovery: StorageEvidence, required_bytes: int) -> None:
    for label, evidence in (("primary", primary), ("recovery", recovery)):
        if not evidence.usable or evidence.required_bytes < required_bytes:
            raise StoragePreflightError(
                f"{label} destination lacks persistent, writable, rereadable capacity for the full backup set"
            )
    if Path(primary.path).resolve(strict=False) == Path(recovery.path).resolve(strict=False):
        raise StoragePreflightError("primary and recovery copies must be stored at different destinations")
    if primary.volume_id == recovery.volume_id:
        raise StoragePreflightError("primary and recovery copies must be on separately identified persistent volumes")


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
        },
        "tool": {
            "name": tool.name,
            "executable": tool.executable,
            "version": tool.version,
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
