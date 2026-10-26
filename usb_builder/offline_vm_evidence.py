"""Standalone prototype for a bounded sealed-VM evidence volume.

This format is intentionally not wired to ``BuildResult`` or the offline
builder route.  It gives a future worker a separate, operation-bound place to
return the target root and build evidence without assuming that the ISO output
volume has spare capacity.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import struct
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence, cast


FORMAT = "librecore-offline-vm-evidence-v3"
HEADER_MAGIC = b"LCVMEV3\0"
TRAILER_MAGIC = b"LCVEND3\0"
HEADER_SIZE = 4096
TRAILER_SIZE = 4096
MANIFEST_SLOT_SIZE = 1024 * 1024
PAYLOAD_OFFSET = HEADER_SIZE + MANIFEST_SLOT_SIZE
MAX_MANIFEST_BYTES = MANIFEST_SLOT_SIZE
MAX_ENTRIES = 100_000
# Provisional hard safety bound only.  No completed native build has measured
# the target-root/evidence size against this value.
MAX_EVIDENCE_VOLUME_BYTES = 64 * 1024 * 1024 * 1024
MAX_EVIDENCE_PAYLOAD_BYTES = MAX_EVIDENCE_VOLUME_BYTES - PAYLOAD_OFFSET - TRAILER_SIZE
HASH_CHUNK_BYTES = 1024 * 1024
EXTRACTION_MARKER = ".librecore-evidence-complete.json"
EXTRACTION_DIRECTORY = "evidence-extracted"

_HEADER = struct.Struct(">8sI32s")
_TRAILER = struct.Struct(">8sI32s")
_HEX256 = re.compile(r"[0-9a-f]{64}\Z")
_OPERATION_ID = re.compile(r"[0-9a-f]{32}\Z")
_BINDING_KEYS = frozenset(
    {
        "operation_id",
        "request_sha256",
        "source_sha256",
        "package_sha256",
        "profile_sha256",
        "snapshot_lock_sha256",
        "snapshot_date",
        "candidate_iso_sha256",
        "candidate_iso_size",
        "command_argv_sha256",
        "evidence_capacity",
    }
)
_ENTRY_KEYS = frozenset({"path", "kind", "role", "mode", "offset", "size", "sha256"})
_MANIFEST_KEYS = frozenset({"format", "binding", "entries", "payload_sha256", "payload_size", "volume_size", "volume_capacity"})
_TERMINAL_KEYS = frozenset(
    {"format", "status", "binding", "manifest_sha256", "payload_sha256", "payload_size", "entry_count", "volume_size", "volume_capacity"}
)


class EvidenceBundleError(ValueError):
    """Malformed, unsafe, incomplete, or incorrectly bound evidence volume."""


@dataclass(frozen=True)
class EvidenceBinding:
    operation_id: str
    request_sha256: str
    source_sha256: str
    package_sha256: str
    profile_sha256: str
    snapshot_lock_sha256: str
    snapshot_date: str
    candidate_iso_sha256: str
    candidate_iso_size: int
    command_argv_sha256: str
    evidence_capacity: int

    def as_dict(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "request_sha256": self.request_sha256,
            "source_sha256": self.source_sha256,
            "package_sha256": self.package_sha256,
            "profile_sha256": self.profile_sha256,
            "snapshot_lock_sha256": self.snapshot_lock_sha256,
            "snapshot_date": self.snapshot_date,
            "candidate_iso_sha256": self.candidate_iso_sha256,
            "candidate_iso_size": self.candidate_iso_size,
            "command_argv_sha256": self.command_argv_sha256,
            "evidence_capacity": self.evidence_capacity,
        }


@dataclass(frozen=True)
class VerifiedEntry:
    path: str
    kind: str
    role: str
    mode: int
    offset: int
    size: int
    sha256: str | None
    link_target: str | None = None


@dataclass(frozen=True)
class EvidenceSource:
    """One fixed relative entry to stream into a future evidence volume."""

    path: str
    role: str
    mode: int
    kind: str = "file"
    source_path: Path | None = None
    link_target: str | None = None
    inline_bytes: bytes | None = None


@dataclass(frozen=True)
class VerifiedEvidenceBundle:
    volume_path: Path
    binding: EvidenceBinding
    manifest_sha256: str
    payload_sha256: str
    payload_size: int
    volume_size: int
    volume_capacity: int
    volume_sha256: str
    entries: tuple[VerifiedEntry, ...]
    volume_device: int
    volume_inode: int
    volume_mtime_ns: int
    volume_ctime_ns: int


def canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceBundleError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_canonical_json(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceBundleError(f"invalid {label} JSON") from exc
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise EvidenceBundleError(f"{label} is not a canonical JSON object")
    return value


def _require_hash(value: object, label: str) -> str:
    if not isinstance(value, str) or _HEX256.fullmatch(value) is None:
        raise EvidenceBundleError(f"invalid {label} SHA-256")
    return value


def _fchmod(fd: int, mode: int) -> None:
    fchmod = getattr(os, "fchmod", None)
    if not callable(fchmod):
        raise EvidenceBundleError("descriptor mode setting is unavailable on this platform")
    fchmod(fd, mode)


def _current_uid() -> int:
    """Return the POSIX effective UID through a narrow runtime guard."""
    getter = getattr(os, "geteuid", None)
    if not callable(getter):
        raise EvidenceBundleError("operation-owned evidence requires POSIX identity support")
    return int(getter())


def _binding_from_dict(value: object) -> EvidenceBinding:
    if not isinstance(value, dict) or set(value) != _BINDING_KEYS:
        raise EvidenceBundleError("evidence binding has missing or unexpected fields")
    operation_id = value.get("operation_id")
    if not isinstance(operation_id, str) or _OPERATION_ID.fullmatch(operation_id) is None:
        raise EvidenceBundleError("invalid evidence operation ID")
    snapshot_date = value.get("snapshot_date")
    if not isinstance(snapshot_date, str) or re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", snapshot_date) is None:
        raise EvidenceBundleError("invalid snapshot date")
    candidate_iso_size = value.get("candidate_iso_size")
    if isinstance(candidate_iso_size, bool) or not isinstance(candidate_iso_size, int) or candidate_iso_size <= 0:
        raise EvidenceBundleError("invalid candidate ISO size binding")
    evidence_capacity = value.get("evidence_capacity")
    if (
        isinstance(evidence_capacity, bool)
        or not isinstance(evidence_capacity, int)
        or not PAYLOAD_OFFSET + TRAILER_SIZE <= evidence_capacity <= MAX_EVIDENCE_VOLUME_BYTES
    ):
        raise EvidenceBundleError("invalid evidence volume capacity binding")
    return EvidenceBinding(
        operation_id=operation_id,
        request_sha256=_require_hash(value.get("request_sha256"), "request"),
        source_sha256=_require_hash(value.get("source_sha256"), "source"),
        package_sha256=_require_hash(value.get("package_sha256"), "package"),
        profile_sha256=_require_hash(value.get("profile_sha256"), "profile"),
        snapshot_lock_sha256=_require_hash(value.get("snapshot_lock_sha256"), "snapshot lock"),
        snapshot_date=snapshot_date,
        candidate_iso_sha256=_require_hash(value.get("candidate_iso_sha256"), "candidate ISO"),
        candidate_iso_size=candidate_iso_size,
        command_argv_sha256=_require_hash(value.get("command_argv_sha256"), "fixed command"),
        evidence_capacity=evidence_capacity,
    )


def _safe_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise EvidenceBundleError("invalid evidence path")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise EvidenceBundleError("invalid evidence path encoding") from exc
    if len(encoded) > 1024 or value.startswith("/") or ":" in value:
        raise EvidenceBundleError("unsafe evidence path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} or len(part.encode("utf-8")) > 255 for part in parts):
        raise EvidenceBundleError("unsafe evidence path component")
    if PurePosixPath(value).as_posix() != value:
        raise EvidenceBundleError("non-normalized evidence path")
    return value


def _validated_symlink_target(link_path: str, target_bytes: bytes) -> str:
    if not target_bytes or len(target_bytes) > 4095:
        raise EvidenceBundleError("symlink target length is outside the supported bound")
    try:
        target = target_bytes.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise EvidenceBundleError("symlink target is not valid UTF-8") from exc
    if "\x00" in target or "\\" in target:
        raise EvidenceBundleError("symlink target contains a forbidden character")
    if target == "/dev/null":
        # Keep this exception synchronized with the reviewed runtime mask
        # policy instead of maintaining a second unit-name allowlist here.
        from .build_usb import NETWORK_DISABLED_SYSTEMD_UNITS

        mask_prefix = "target-root/etc/systemd/system/"
        unit_name = link_path.removeprefix(mask_prefix) if link_path.startswith(mask_prefix) else None
        if unit_name not in NETWORK_DISABLED_SYSTEMD_UNITS:
            raise EvidenceBundleError("/dev/null is allowed only for reviewed network systemd masks")
        return target
    if target.startswith("/"):
        stack: list[str] = []
        components = [] if target == "/" else target[1:].split("/")
    else:
        stack = list(PurePosixPath(link_path).parent.parts[1:])
        components = target.split("/")
    for component in components:
        if component == "":
            raise EvidenceBundleError("symlink target is not a normalized POSIX path")
        if component == ".":
            continue
        if component == "..":
            if not stack:
                raise EvidenceBundleError("symlink target escapes target-root")
            stack.pop()
            continue
        if len(component.encode("utf-8")) > 255:
            raise EvidenceBundleError("symlink target component exceeds its bound")
        stack.append(component)
    return target


def _validate_role(path: str, kind: str, role: object) -> str:
    if not isinstance(role, str):
        raise EvidenceBundleError("invalid evidence role")
    if role == "target-root":
        valid = path == "target-root" and kind == "directory"
    elif role == "target-root-directory":
        valid = path.startswith("target-root/") and kind == "directory"
    elif role == "target-root-file":
        valid = path.startswith("target-root/") and kind == "file"
    elif role == "target-root-symlink":
        valid = path.startswith("target-root/") and kind == "symlink"
    elif role == "log-directory":
        valid = (path == "logs" or path.startswith("logs/")) and kind == "directory"
    elif role == "wheel":
        valid = path == "wheel.whl" and kind == "file"
    elif role == "package-inventory":
        valid = path == "package-inventory.json" and kind == "file"
    elif role == "command-status":
        valid = path == "command-status.json" and kind == "file"
    elif role == "provenance":
        valid = path == "build-provenance.json" and kind == "file"
    elif role == "log":
        valid = path.startswith("logs/") and kind == "file"
    else:
        valid = False
    if not valid:
        raise EvidenceBundleError(f"role/path/type combination is not allowed: {role!r} {path!r}")
    return role


def _check_required_roles(entries: Sequence[VerifiedEntry]) -> None:
    required = {"target-root", "wheel", "package-inventory", "command-status", "provenance"}
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.role] = counts.get(entry.role, 0) + 1
    missing = required - {role for role, count in counts.items() if count == 1}
    duplicated = {role for role, count in counts.items() if count > 1 and role in required}
    if missing or duplicated:
        raise EvidenceBundleError(f"required evidence roles missing/duplicated: missing={sorted(missing)}, duplicate={sorted(duplicated)}")
    by_role = {entry.role: entry for entry in entries if entry.role in required}
    if any(by_role[role].size <= 0 for role in {"wheel", "package-inventory", "command-status", "provenance"}):
        raise EvidenceBundleError("required evidence file is empty")


def _read_exact(fd: int, count: int, offset: int) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    cursor = offset
    pread = getattr(os, "pread", None)
    if not callable(pread):
        raise EvidenceBundleError("evidence-volume reads require positional file I/O")
    while remaining:
        data = pread(fd, min(remaining, HASH_CHUNK_BYTES), cursor)
        if not data:
            raise EvidenceBundleError("truncated evidence volume")
        chunks.append(data)
        cursor += len(data)
        remaining -= len(data)
    return b"".join(chunks)


def _require_zero_region(fd: int, start: int, end: int) -> None:
    cursor = start
    while cursor < end:
        chunk = _read_exact(fd, min(HASH_CHUNK_BYTES, end - cursor), cursor)
        if any(chunk):
            raise EvidenceBundleError("nonzero bytes in reserved evidence region")
        cursor += len(chunk)


def _hash_region(fd: int, start: int, length: int) -> str:
    digest = hashlib.sha256()
    cursor = start
    end = start + length
    while cursor < end:
        chunk = _read_exact(fd, min(HASH_CHUNK_BYTES, end - cursor), cursor)
        digest.update(chunk)
        cursor += len(chunk)
    return digest.hexdigest()


def collect_target_root_sources(target_root: Path) -> tuple[EvidenceSource, ...]:
    """Inventory a built airootfs without following links or special files.

    The resulting list is transport input only.  It is not evidence that the
    tree satisfies the USB builder's target-root checks; the host adapter must
    still validate the extracted tree independently.
    """
    root = Path(os.path.abspath(target_root))
    try:
        root_info = root.lstat()
    except OSError as exc:
        raise EvidenceBundleError("fixed airootfs target root is missing") from exc
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise EvidenceBundleError("fixed airootfs target root must be a real directory")
    sources: list[EvidenceSource] = [EvidenceSource("target-root", "target-root", stat.S_IMODE(root_info.st_mode), "directory")]

    def visit(directory: Path, relative: str) -> None:
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            raise EvidenceBundleError("target-root directory could not be inventoried") from exc
        for child in children:
            if child.name in {".", ".."} or "/" in child.name or "\\" in child.name or "\x00" in child.name:
                raise EvidenceBundleError("target-root entry has an unsafe name")
            child_path = Path(child.path)
            archive_path = f"{relative}/{child.name}"
            try:
                info = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise EvidenceBundleError("target-root entry changed during inventory") from exc
            mode = stat.S_IMODE(info.st_mode) & 0o777
            if stat.S_ISDIR(info.st_mode):
                sources.append(EvidenceSource(archive_path, "target-root-directory", mode, "directory"))
                visit(child_path, archive_path)
            elif stat.S_ISREG(info.st_mode):
                sources.append(EvidenceSource(archive_path, "target-root-file", mode, "file", child_path))
            elif stat.S_ISLNK(info.st_mode):
                try:
                    target = os.readlink(child_path)
                    target_bytes = target.encode("utf-8", "strict")
                except (OSError, UnicodeError) as exc:
                    raise EvidenceBundleError("target-root symlink could not be read safely") from exc
                _validated_symlink_target(archive_path, target_bytes)
                sources.append(EvidenceSource(archive_path, "target-root-symlink", 0o777, "symlink", child_path, target))
            else:
                raise EvidenceBundleError(f"unsupported special file in target root: {archive_path}")
            if len(sources) > MAX_ENTRIES:
                raise EvidenceBundleError("target-root evidence exceeds the fixed entry count")

    visit(root, "target-root")
    return tuple(sorted(sources, key=lambda item: item.path))


def _source_parent_is_safe(path: Path, *, allow_symlink_leaf: bool = False) -> os.stat_result:
    absolute = Path(os.path.abspath(path))
    cursor = Path(absolute.anchor)
    for index, component in enumerate(absolute.parts[1:]):
        cursor = cursor / component
        try:
            info = cursor.lstat()
        except OSError as exc:
            raise EvidenceBundleError("evidence source path is missing") from exc
        is_leaf = index == len(absolute.parts[1:]) - 1
        if stat.S_ISLNK(info.st_mode):
            if allow_symlink_leaf and is_leaf:
                return info
            raise EvidenceBundleError("evidence source path contains a symlink")
        if not is_leaf and not stat.S_ISDIR(info.st_mode):
            raise EvidenceBundleError("evidence source path parent is not a directory")
    return info


def _hash_source_file(source: EvidenceSource) -> tuple[int, str, os.stat_result]:
    if source.source_path is None or source.kind != "file":
        raise EvidenceBundleError("regular evidence file has no source path")
    path = Path(os.path.abspath(source.source_path))
    path_info = _source_parent_is_safe(path)
    if not stat.S_ISREG(path_info.st_mode) or path_info.st_nlink != 1:
        raise EvidenceBundleError("evidence source must be a single-link regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = _open_absolute_file_no_follow(path, flags)
    except OSError as exc:
        raise EvidenceBundleError("evidence source could not be opened without following links") from exc
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_nlink) != (
            path_info.st_dev, path_info.st_ino, path_info.st_size, path_info.st_nlink
        ):
            raise EvidenceBundleError("evidence source changed while opening")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, HASH_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
        final = os.fstat(fd)
        if (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns, final.st_ctime_ns) != (
            opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns
        ):
            raise EvidenceBundleError("evidence source changed while hashing")
        return opened.st_size, digest.hexdigest(), opened
    finally:
        os.close(fd)


def _pwrite_all(fd: int, payload: bytes, offset: int) -> None:
    pwrite = getattr(os, "pwrite", None)
    if not callable(pwrite):
        raise EvidenceBundleError("evidence writer requires positional writes")
    view = memoryview(payload)
    cursor = offset
    while view:
        amount = pwrite(fd, view, cursor)
        if amount <= 0:
            raise EvidenceBundleError("short write to evidence volume")
        cursor += amount
        view = view[amount:]


def write_evidence_volume_fd(
    fd: int,
    capacity: int,
    binding: EvidenceBinding,
    sources: Sequence[EvidenceSource],
) -> tuple[int, str]:
    """Create, sync, and read back a complete bundle on a blank fixed volume.

    ``capacity`` is the exact physical volume size from the request.  The
    returned length is the logical bundle prefix; all remaining capacity is
    required to stay zero.  The caller must establish that ``fd`` is the
    unmounted, distinct LC-EVIDENCE-RW block device before calling this API.
    """
    if os.name != "posix" or not callable(getattr(os, "pread", None)):
        raise EvidenceBundleError("evidence writer requires POSIX positional I/O")
    if capacity != binding.evidence_capacity or not PAYLOAD_OFFSET + TRAILER_SIZE <= capacity <= MAX_EVIDENCE_VOLUME_BYTES:
        raise EvidenceBundleError("evidence writer capacity differs from the operation binding")
    if not sources or len(sources) > MAX_ENTRIES:
        raise EvidenceBundleError("evidence source list is empty or exceeds its entry bound")
    volume_identity = os.fstat(fd)
    if stat.S_ISREG(volume_identity.st_mode) and volume_identity.st_size != capacity:
        raise EvidenceBundleError("regular test evidence volume has the wrong capacity")

    # Require a fresh, all-zero device.  This is bounded by the 64 GiB
    # provisional cap, though its runtime has not been measured on real media.
    _require_zero_region(fd, 0, capacity)
    ordered = tuple(sorted(sources, key=lambda item: item.path))
    if tuple(source.path for source in ordered) != tuple(source.path for source in sources):
        raise EvidenceBundleError("evidence sources must be path-sorted")
    raw_entries: list[dict[str, object]] = []
    source_state: list[tuple[EvidenceSource, int, str | None, bytes | None, os.stat_result | None]] = []
    payload_size = 0
    prior_paths: set[str] = set()
    prior_kinds: dict[str, str] = {}
    for source in sources:
        path = _safe_relative_path(source.path)
        if path in prior_paths:
            raise EvidenceBundleError("duplicate evidence source path")
        prior_paths.add(path)
        if source.kind not in {"file", "directory", "symlink"}:
            raise EvidenceBundleError("unsupported evidence source type")
        _validate_role(path, source.kind, source.role)
        if isinstance(source.mode, bool) or not isinstance(source.mode, int) or not 0 <= source.mode <= 0o777:
            raise EvidenceBundleError("invalid evidence source mode")
        if source.kind != "symlink" and source.mode & 0o022:
            raise EvidenceBundleError("evidence source mode is writable by group or others")
        parent = PurePosixPath(path).parent
        while str(parent) != ".":
            parent_name = str(parent)
            if prior_kinds.get(parent_name) != "directory":
                raise EvidenceBundleError(f"missing parent directory source: {parent_name}")
            parent = parent.parent
        if source.kind == "directory":
            if source.source_path is not None or source.link_target is not None or source.inline_bytes is not None:
                raise EvidenceBundleError("directory source contains file/link data")
            raw_entries.append({"path": path, "kind": "directory", "role": source.role, "mode": source.mode, "offset": 0, "size": 0, "sha256": None})
            source_state.append((source, 0, None, None, None))
        elif source.kind == "symlink":
            if source.link_target is None or source.inline_bytes is not None:
                raise EvidenceBundleError("symlink source target is missing")
            target_bytes = source.link_target.encode("utf-8", "strict")
            checked_target = _validated_symlink_target(path, target_bytes)
            symlink_info: os.stat_result | None = None
            if source.source_path is not None:
                info = _source_parent_is_safe(source.source_path, allow_symlink_leaf=True)
                if not stat.S_ISLNK(info.st_mode) or os.readlink(source.source_path) != checked_target:
                    raise EvidenceBundleError("symlink source changed during inventory")
                symlink_info = info
            offset = PAYLOAD_OFFSET + payload_size
            payload_size += len(target_bytes)
            raw_entries.append({"path": path, "kind": "symlink", "role": source.role, "mode": 0o777, "offset": offset, "size": len(target_bytes), "sha256": hashlib.sha256(target_bytes).hexdigest()})
            source_state.append((source, len(target_bytes), hashlib.sha256(target_bytes).hexdigest(), target_bytes, symlink_info))
        else:
            if source.inline_bytes is not None:
                if source.source_path is not None:
                    raise EvidenceBundleError("inline evidence bytes cannot also name a source path")
                size = len(source.inline_bytes)
                digest = hashlib.sha256(source.inline_bytes).hexdigest()
                info = None
            else:
                size, digest, info = _hash_source_file(source)
            offset = PAYLOAD_OFFSET + payload_size
            payload_size += size
            raw_entries.append({"path": path, "kind": "file", "role": source.role, "mode": source.mode, "offset": offset, "size": size, "sha256": digest})
            source_state.append((source, size, digest, source.inline_bytes, info))
        prior_kinds[path] = source.kind
        if payload_size > MAX_EVIDENCE_PAYLOAD_BYTES:
            raise EvidenceBundleError("evidence payload exceeds its fixed bound")
    verified_rows: list[VerifiedEntry] = []
    for row in raw_entries:
        row_mode = row["mode"]
        row_offset = row["offset"]
        row_size = row["size"]
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (row_mode, row_offset, row_size)):
            raise EvidenceBundleError("internal evidence entry has invalid numeric fields")
        digest_value = row["sha256"]
        if digest_value is not None and not isinstance(digest_value, str):
            raise EvidenceBundleError("internal evidence entry has invalid digest")
        verified_rows.append(
            VerifiedEntry(
                path=str(row["path"]), kind=str(row["kind"]), role=str(row["role"]),
                mode=cast(int, row_mode), offset=cast(int, row_offset), size=cast(int, row_size),
                sha256=digest_value,
            )
        )
    verified_entries = tuple(verified_rows)
    _check_required_roles(verified_entries)
    payload_digest = hashlib.sha256()
    logical_size = PAYLOAD_OFFSET + payload_size + TRAILER_SIZE
    if logical_size > capacity:
        raise EvidenceBundleError("logical evidence bundle does not fit the fixed volume capacity")

    manifest = {
        "format": FORMAT,
        "binding": binding.as_dict(),
        "entries": raw_entries,
        "payload_sha256": None,
        "payload_size": payload_size,
        "volume_size": logical_size,
        "volume_capacity": capacity,
    }
    # Compute the whole payload digest in an initial bounded pass.  The actual
    # write pass rehashes each source to detect changes between planning/write.
    for source, size, expected_hash, symlink_bytes, first_stat in source_state:
        if source.kind == "directory":
            continue
        if source.kind == "symlink":
            assert symlink_bytes is not None
            payload_digest.update(symlink_bytes)
            continue
        if source.kind == "file" and symlink_bytes is not None:
            if len(symlink_bytes) != size or hashlib.sha256(symlink_bytes).hexdigest() != expected_hash:
                raise EvidenceBundleError("inline evidence bytes changed before writing")
            payload_digest.update(symlink_bytes)
            continue
        assert source.source_path is not None and first_stat is not None
        file_size, file_hash, current_stat = _hash_source_file(source)
        if (file_size, file_hash, current_stat.st_dev, current_stat.st_ino) != (size, expected_hash, first_stat.st_dev, first_stat.st_ino):
            raise EvidenceBundleError("evidence source changed before writing")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        source_fd = _open_absolute_file_no_follow(source.source_path, flags)
        try:
            while True:
                chunk = os.read(source_fd, HASH_CHUNK_BYTES)
                if not chunk:
                    break
                payload_digest.update(chunk)
        finally:
            os.close(source_fd)
    manifest["payload_sha256"] = payload_digest.hexdigest()
    manifest_raw = canonical_json(manifest)
    if not 1 <= len(manifest_raw) <= MAX_MANIFEST_BYTES:
        raise EvidenceBundleError("evidence manifest exceeds its fixed slot")
    manifest_hash = hashlib.sha256(manifest_raw).digest()
    header = _HEADER.pack(HEADER_MAGIC, len(manifest_raw), manifest_hash)
    header_block = bytearray(HEADER_SIZE)
    header_block[: len(header)] = header
    terminal = {
        "format": FORMAT,
        "status": "COMPLETE",
        "binding": binding.as_dict(),
        "manifest_sha256": manifest_hash.hex(),
        "payload_sha256": manifest["payload_sha256"],
        "payload_size": payload_size,
        "entry_count": len(raw_entries),
        "volume_size": logical_size,
        "volume_capacity": capacity,
    }
    terminal_raw = canonical_json(terminal)
    terminal_block = bytearray(TRAILER_SIZE)
    terminal_header = _TRAILER.pack(TRAILER_MAGIC, len(terminal_raw), hashlib.sha256(terminal_raw).digest())
    if len(terminal_header) + len(terminal_raw) > TRAILER_SIZE:
        raise EvidenceBundleError("evidence terminal exceeds its fixed slot")
    terminal_block[: len(terminal_header)] = terminal_header
    terminal_block[len(terminal_header) : len(terminal_header) + len(terminal_raw)] = terminal_raw

    _pwrite_all(fd, bytes(header_block), 0)
    _pwrite_all(fd, manifest_raw, HEADER_SIZE)
    payload_cursor = PAYLOAD_OFFSET
    for source, size, expected_hash, symlink_bytes, first_stat in source_state:
        if source.kind == "directory":
            continue
        if source.kind == "symlink":
            assert symlink_bytes is not None
            if source.source_path is not None:
                if first_stat is None:
                    raise EvidenceBundleError("symlink source identity was not captured")
                current = _source_parent_is_safe(source.source_path, allow_symlink_leaf=True)
                if (
                    not stat.S_ISLNK(current.st_mode)
                    or (current.st_dev, current.st_ino, current.st_mtime_ns, current.st_ctime_ns)
                    != (first_stat.st_dev, first_stat.st_ino, first_stat.st_mtime_ns, first_stat.st_ctime_ns)
                    or os.readlink(source.source_path).encode("utf-8", "strict") != symlink_bytes
                ):
                    raise EvidenceBundleError("symlink source changed before writing")
            bytes_to_write = symlink_bytes
            if hashlib.sha256(bytes_to_write).hexdigest() != expected_hash:
                raise EvidenceBundleError("symlink target changed before write")
            _pwrite_all(fd, bytes_to_write, payload_cursor)
            payload_cursor += len(bytes_to_write)
            continue
        if source.inline_bytes is not None:
            if len(source.inline_bytes) != size or hashlib.sha256(source.inline_bytes).hexdigest() != expected_hash:
                raise EvidenceBundleError("inline evidence bytes changed before write")
            _pwrite_all(fd, source.inline_bytes, payload_cursor)
            payload_cursor += len(source.inline_bytes)
            continue
        assert source.source_path is not None and first_stat is not None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        source_fd = _open_absolute_file_no_follow(source.source_path, flags)
        source_hasher = hashlib.sha256()
        written_size = 0
        try:
            opened = os.fstat(source_fd)
            if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (
                first_stat.st_dev, first_stat.st_ino, first_stat.st_size, first_stat.st_mtime_ns, first_stat.st_ctime_ns
            ):
                raise EvidenceBundleError("evidence source inode changed before write")
            while True:
                chunk = os.read(source_fd, HASH_CHUNK_BYTES)
                if not chunk:
                    break
                source_hasher.update(chunk)
                written_size += len(chunk)
                _pwrite_all(fd, chunk, payload_cursor)
                payload_cursor += len(chunk)
            final_source = os.fstat(source_fd)
            if written_size != size or source_hasher.hexdigest() != expected_hash or (
                final_source.st_dev, final_source.st_ino, final_source.st_size,
                final_source.st_mtime_ns, final_source.st_ctime_ns,
            ) != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns):
                raise EvidenceBundleError("evidence source changed while writing")
        finally:
            os.close(source_fd)
    if payload_cursor != PAYLOAD_OFFSET + payload_size:
        raise EvidenceBundleError("evidence payload cursor differs from the manifest")
    _pwrite_all(fd, bytes(terminal_block), logical_size - TRAILER_SIZE)
    os.fsync(fd)
    final_stat = os.fstat(fd)
    if (
        final_stat.st_dev, final_stat.st_ino, getattr(final_stat, "st_rdev", 0), final_stat.st_size
    ) != (
        volume_identity.st_dev, volume_identity.st_ino,
        getattr(volume_identity, "st_rdev", 0), volume_identity.st_size
    ):
        raise EvidenceBundleError("evidence volume identity changed during finalization")
    _require_zero_region(fd, logical_size, capacity)
    volume_sha256 = _hash_region(fd, 0, logical_size)
    return logical_size, volume_sha256


def _open_absolute_directory_no_follow(path: Path) -> int:
    """Open every directory component by fd, rejecting symlinks in the chain."""
    absolute = Path(os.path.abspath(path))
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    current_fd = os.open(os.sep, flags)
    try:
        for component in absolute.parts[1:]:
            next_fd = os.open(component, flags, dir_fd=current_fd)
            info = os.fstat(next_fd)
            if not stat.S_ISDIR(info.st_mode):
                os.close(next_fd)
                raise EvidenceBundleError("non-directory in evidence path")
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _open_absolute_file_no_follow(path: Path, flags: int) -> int:
    absolute = Path(os.path.abspath(path))
    parent_fd = _open_absolute_directory_no_follow(absolute.parent)
    try:
        return os.open(absolute.name, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


def _validate_command_status(fd: int, entry: VerifiedEntry, binding: EvidenceBinding) -> None:
    if entry.size > 64 * 1024:
        raise EvidenceBundleError("command status record exceeds its bound")
    raw = _read_exact(fd, entry.size, entry.offset)
    status = _parse_canonical_json(raw, "command status")
    if set(status) != {
        "format", "service", "status", "exit_code", "argv_sha256", "operation_id", "request_sha256",
        "candidate_iso_sha256", "candidate_iso_size", "build_process_result",
    }:
        raise EvidenceBundleError("command status has missing or unexpected fields")
    argv_digest = status.get("argv_sha256")
    exit_code = status.get("exit_code")
    if (
        status.get("format") != FORMAT
        or status.get("service") != "librecore-offline-build.service"
        or status.get("status") != "EXITED_ZERO"
        or isinstance(exit_code, bool)
        or not isinstance(exit_code, int)
        or exit_code != 0
        or not isinstance(argv_digest, str)
        or _HEX256.fullmatch(argv_digest) is None
        or argv_digest != binding.command_argv_sha256
        or status.get("operation_id") != binding.operation_id
        or status.get("request_sha256") != binding.request_sha256
        or status.get("candidate_iso_sha256") != binding.candidate_iso_sha256
        or isinstance(status.get("candidate_iso_size"), bool)
        or not isinstance(status.get("candidate_iso_size"), int)
        or status.get("candidate_iso_size") != binding.candidate_iso_size
    ):
        raise EvidenceBundleError("command status lacks a valid reported zero-exit record")
    process = status.get("build_process_result")
    process_keys = {
        "format", "service_name", "argv_sha256", "returncode", "timed_out", "elapsed_seconds",
        "no_new_privs_before", "no_new_privs_during", "network_namespace_before",
        "network_namespace_during", "pacstrap_exit_codes", "account", "uid", "gid",
        "supplementary_gids", "cap_effective", "cap_permitted", "cap_ambient", "interfaces",
        "ipv4_routes", "ipv6_routes", "cap_bounding",
    }
    if not isinstance(process, dict) or set(process) != process_keys:
        raise EvidenceBundleError("command status build-process result has missing or unexpected fields")
    process_returncode = process.get("returncode")
    process_elapsed = process.get("elapsed_seconds")
    if (
        process.get("format") != "librecore-offline-vm-build-process-result-v1"
        or not isinstance(process.get("service_name"), str)
        or not isinstance(process.get("argv_sha256"), str)
        or _HEX256.fullmatch(process["argv_sha256"]) is None
        or (process_returncode is not None and (
            isinstance(process_returncode, bool)
            or not isinstance(process_returncode, int)
            or not -255 <= process_returncode <= 255
        ))
        or type(process.get("timed_out")) is not bool
        or isinstance(process_elapsed, bool)
        or not isinstance(process_elapsed, (int, float))
        or not math.isfinite(float(process_elapsed))
        or not 0 <= float(process_elapsed) <= 24 * 60 * 60
        or type(process.get("no_new_privs_before")) is not bool
        or type(process.get("no_new_privs_during")) is not bool
        or not isinstance(process.get("network_namespace_before"), str)
        or not isinstance(process.get("network_namespace_during"), str)
        or not isinstance(process.get("account"), str)
        or any(
            isinstance(process.get(name), bool)
            or not isinstance(process.get(name), int)
            or not 0 <= process[name] <= 2**31 - 1
            for name in ("uid", "gid")
        )
    ):
        raise EvidenceBundleError("command status build-process result has malformed scalar values")
    for name, minimum, maximum, limit in (
        ("pacstrap_exit_codes", -1, 255, 4096),
        ("supplementary_gids", 0, 2**31 - 1, 64),
    ):
        values = process.get(name)
        if not isinstance(values, list) or len(values) > limit or any(
            isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum
            for value in values
        ):
            raise EvidenceBundleError(f"command status build-process result {name} are malformed")
    for name in ("interfaces", "ipv4_routes", "ipv6_routes"):
        values = process.get(name)
        if not isinstance(values, list) or len(values) > 64 or any(not isinstance(value, str) for value in values):
            raise EvidenceBundleError(f"command status build-process result {name} are malformed")
    for name in (
        "cap_effective", "cap_permitted", "cap_ambient", "cap_bounding",
    ):
        value = process.get(name)
        if not isinstance(value, str) or len(value) > 32:
            raise EvidenceBundleError(f"command status build-process result {name} is malformed")


def _parse_entries(raw_entries: object, volume_size: int, trailer_offset: int) -> tuple[VerifiedEntry, ...]:
    if not isinstance(raw_entries, list) or not raw_entries or len(raw_entries) > MAX_ENTRIES:
        raise EvidenceBundleError("invalid evidence entry count")
    entries: list[VerifiedEntry] = []
    seen: set[str] = set()
    seen_kinds: dict[str, str] = {}
    previous_path = ""
    next_payload_offset = PAYLOAD_OFFSET
    for raw in raw_entries:
        if not isinstance(raw, dict) or set(raw) != _ENTRY_KEYS:
            raise EvidenceBundleError("evidence entry has missing or unexpected fields")
        path = _safe_relative_path(raw.get("path"))
        if path in seen:
            raise EvidenceBundleError(f"duplicate evidence path: {path}")
        seen.add(path)
        if path <= previous_path:
            raise EvidenceBundleError("evidence entries are not path-sorted")
        previous_path = path
        kind = raw.get("kind")
        if kind not in {"file", "directory", "symlink"}:
            raise EvidenceBundleError("special files are not permitted")
        role = _validate_role(path, kind, raw.get("role"))
        mode = raw.get("mode")
        offset = raw.get("offset")
        size = raw.get("size")
        sha256 = raw.get("sha256")
        if (
            isinstance(mode, bool)
            or not isinstance(mode, int)
            or mode < 0
            or mode > 0o777
            or (kind != "symlink" and mode & 0o022)
        ):
            raise EvidenceBundleError("unsafe evidence mode")
        if isinstance(offset, bool) or not isinstance(offset, int) or isinstance(size, bool) or not isinstance(size, int):
            raise EvidenceBundleError("invalid evidence entry range")
        if size < 0 or offset < 0:
            raise EvidenceBundleError("negative evidence entry range")
        if kind == "directory":
            if size != 0 or offset != 0 or sha256 is not None:
                raise EvidenceBundleError("directory entry contains file data")
        else:
            if _require_hash(sha256, f"entry {path}") == "":  # pragma: no cover - guarded by format
                raise EvidenceBundleError("invalid entry hash")
            if kind == "symlink" and (not path.startswith("target-root/") or mode != 0o777 or not 1 <= size <= 4095):
                raise EvidenceBundleError("unsafe symlink entry")
            if offset != next_payload_offset or size > MAX_EVIDENCE_PAYLOAD_BYTES - (next_payload_offset - PAYLOAD_OFFSET):
                raise EvidenceBundleError("file or symlink payload is not contiguous or exceeds the volume cap")
            next_payload_offset += size
        parent = PurePosixPath(path).parent
        while str(parent) != ".":
            parent_name = str(parent)
            if parent_name not in seen or seen_kinds.get(parent_name) != "directory":
                # Every container must be explicitly represented as a directory.
                raise EvidenceBundleError(f"missing parent directory entry: {parent_name}")
            parent = parent.parent
        entries.append(VerifiedEntry(path, kind, role, mode, offset, size, sha256 if isinstance(sha256, str) else None))
        seen_kinds[path] = kind
    if not any(item.path == "target-root" and item.kind == "directory" for item in entries):
        raise EvidenceBundleError("target-root directory is missing")
    return tuple(entries)


def verify_evidence_volume(
    volume_path: Path,
    expected_binding: EvidenceBinding,
    *,
    max_volume_bytes: int = MAX_EVIDENCE_VOLUME_BYTES,
) -> VerifiedEvidenceBundle:
    """Verify a complete separate evidence volume without extracting it."""
    if os.name != "posix" or not hasattr(os, "pread"):
        raise EvidenceBundleError("evidence-volume verification requires POSIX no-follow file operations")
    volume_path = Path(os.path.abspath(volume_path))
    minimum = PAYLOAD_OFFSET + TRAILER_SIZE
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = _open_absolute_file_no_follow(volume_path, flags)
    except OSError as exc:
        raise EvidenceBundleError("evidence volume could not be opened without following links") from exc
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or stat.S_IMODE(opened.st_mode) != 0o600:
            raise EvidenceBundleError("evidence volume must be a private single-link regular file")
        if opened.st_uid != _current_uid():
            raise EvidenceBundleError("evidence volume owner does not match the current operation")
        volume_capacity = opened.st_size
        if (
            volume_capacity != expected_binding.evidence_capacity
            or volume_capacity < minimum
            or volume_capacity > max_volume_bytes
            or volume_capacity > MAX_EVIDENCE_VOLUME_BYTES
        ):
            raise EvidenceBundleError("evidence volume size is outside the supported bound")
        header = _read_exact(fd, HEADER_SIZE, 0)
        magic, manifest_length, manifest_digest = _HEADER.unpack(header[: _HEADER.size])
        if magic != HEADER_MAGIC or not 1 <= manifest_length <= MAX_MANIFEST_BYTES:
            raise EvidenceBundleError("invalid evidence volume header")
        if any(header[_HEADER.size :]):
            raise EvidenceBundleError("nonzero evidence header padding")
        manifest_raw = _read_exact(fd, manifest_length, HEADER_SIZE)
        manifest_sha256 = hashlib.sha256(manifest_raw).digest()
        if manifest_sha256 != manifest_digest:
            raise EvidenceBundleError("evidence manifest digest mismatch")
        manifest = _parse_canonical_json(manifest_raw, "manifest")
        if set(manifest) != _MANIFEST_KEYS or manifest.get("format") != FORMAT:
            raise EvidenceBundleError("unsupported evidence manifest schema")
        binding = _binding_from_dict(manifest.get("binding"))
        if binding != expected_binding:
            raise EvidenceBundleError("evidence binding does not match this operation/request")
        raw_logical_size = manifest.get("volume_size")
        if (
            isinstance(raw_logical_size, bool)
            or not isinstance(raw_logical_size, int)
            or raw_logical_size < minimum
            or raw_logical_size > volume_capacity
            or manifest.get("volume_capacity") != volume_capacity
        ):
            raise EvidenceBundleError("evidence logical size/capacity binding mismatch")
        volume_size = raw_logical_size
        payload_sha256 = _require_hash(manifest.get("payload_sha256"), "payload")
        payload_size = manifest.get("payload_size")
        if isinstance(payload_size, bool) or not isinstance(payload_size, int) or not 0 <= payload_size <= MAX_EVIDENCE_PAYLOAD_BYTES:
            raise EvidenceBundleError("invalid evidence payload size")
        trailer_offset = volume_size - TRAILER_SIZE
        _require_zero_region(fd, HEADER_SIZE + manifest_length, PAYLOAD_OFFSET)
        entries = _parse_entries(manifest.get("entries"), volume_size, trailer_offset)
        _check_required_roles(entries)
        measured_payload = sum(item.size for item in entries if item.kind in {"file", "symlink"})
        if measured_payload != payload_size:
            raise EvidenceBundleError("evidence payload length mismatch")
        payload_end = PAYLOAD_OFFSET + payload_size
        if payload_end > trailer_offset:
            raise EvidenceBundleError("evidence payload overlaps terminal record")
        checked_entries: list[VerifiedEntry] = []
        for entry in entries:
            if entry.kind in {"file", "symlink"}:
                assert entry.sha256 is not None
                if _hash_region(fd, entry.offset, entry.size) != entry.sha256:
                    raise EvidenceBundleError(f"evidence file digest mismatch: {entry.path}")
                if entry.role == "command-status":
                    _validate_command_status(fd, entry, binding)
                if entry.kind == "symlink":
                    target = _validated_symlink_target(entry.path, _read_exact(fd, entry.size, entry.offset))
                    entry = replace(entry, link_target=target)
            checked_entries.append(entry)
        entries = tuple(checked_entries)
        actual_payload_hash = _hash_region(fd, PAYLOAD_OFFSET, payload_size)
        if actual_payload_hash != payload_sha256:
            raise EvidenceBundleError("evidence payload digest mismatch")
        _require_zero_region(fd, payload_end, trailer_offset)
        trailer = _read_exact(fd, TRAILER_SIZE, trailer_offset)
        terminal_magic, terminal_length, terminal_digest = _TRAILER.unpack(trailer[: _TRAILER.size])
        if terminal_magic != TRAILER_MAGIC or not 1 <= terminal_length <= TRAILER_SIZE - _TRAILER.size:
            raise EvidenceBundleError("missing or malformed evidence terminal record")
        terminal_raw = trailer[_TRAILER.size : _TRAILER.size + terminal_length]
        if hashlib.sha256(terminal_raw).digest() != terminal_digest:
            raise EvidenceBundleError("terminal evidence digest mismatch")
        if any(trailer[_TRAILER.size + terminal_length :]):
            raise EvidenceBundleError("nonzero terminal padding")
        terminal = _parse_canonical_json(terminal_raw, "terminal record")
        if set(terminal) != _TERMINAL_KEYS or terminal.get("format") != FORMAT:
            raise EvidenceBundleError("unsupported terminal evidence schema")
        expected_terminal = {
            "format": FORMAT,
            "status": "COMPLETE",
            "binding": binding.as_dict(),
            "manifest_sha256": manifest_sha256.hex(),
            "payload_sha256": payload_sha256,
            "payload_size": payload_size,
            "entry_count": len(entries),
            "volume_size": volume_size,
            "volume_capacity": volume_capacity,
        }
        if terminal != expected_terminal:
            raise EvidenceBundleError("terminal evidence is incomplete or not bound to this bundle")
        _require_zero_region(fd, volume_size, volume_capacity)
        volume_sha256 = _hash_region(fd, 0, volume_size)
        final_stat = os.fstat(fd)
        if (
            final_stat.st_dev,
            final_stat.st_ino,
            final_stat.st_size,
            final_stat.st_mtime_ns,
            final_stat.st_ctime_ns,
            final_stat.st_uid,
            final_stat.st_nlink,
            stat.S_IMODE(final_stat.st_mode),
        ) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
            opened.st_uid,
            opened.st_nlink,
            stat.S_IMODE(opened.st_mode),
        ):
            raise EvidenceBundleError("evidence volume changed during verification")
    finally:
        os.close(fd)
    return VerifiedEvidenceBundle(
        volume_path=volume_path,
        binding=binding,
        manifest_sha256=manifest_sha256.hex(),
        payload_sha256=payload_sha256,
        payload_size=payload_size,
        volume_size=volume_size,
        volume_capacity=volume_capacity,
        volume_sha256=volume_sha256,
        entries=entries,
        volume_device=opened.st_dev,
        volume_inode=opened.st_ino,
        volume_mtime_ns=opened.st_mtime_ns,
        volume_ctime_ns=opened.st_ctime_ns,
    )


def _check_private_operation_root(operation_root: Path) -> tuple[int, os.stat_result]:
    if os.name != "posix":
        raise EvidenceBundleError("evidence extraction requires Linux/POSIX directory-fd operations")
    operation_root = Path(os.path.abspath(operation_root))
    try:
        fd = _open_absolute_directory_no_follow(operation_root)
    except OSError as exc:
        raise EvidenceBundleError("operation root path must have no symlink components") from exc
    root_stat = os.fstat(fd)
    if not stat.S_ISDIR(root_stat.st_mode) or root_stat.st_uid != _current_uid() or stat.S_IMODE(root_stat.st_mode) != 0o700:
        os.close(fd)
        raise EvidenceBundleError("operation root must be current-user-owned mode 0700")
    return fd, root_stat


def _open_directory_chain(root_fd: int, components: Sequence[str], *, create: bool) -> int:
    current_fd = os.dup(root_fd)
    try:
        for component in components:
            if create:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=current_fd)
                except FileExistsError as exc:
                    raise EvidenceBundleError("evidence extraction destination is not fresh") from exc
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            next_fd = os.open(component, flags, dir_fd=current_fd)
            try:
                if create:
                    _fchmod(next_fd, 0o700)
                info = os.fstat(next_fd)
                if (
                    not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != _current_uid()
                    or info.st_nlink < 2
                    or (create and stat.S_IMODE(info.st_mode) != 0o700)
                ):
                    raise EvidenceBundleError("unsafe directory encountered during evidence extraction")
            except BaseException:
                os.close(next_fd)
                raise
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _create_directory_chain(root_fd: int, components: Sequence[str]) -> int:
    if not components:
        return os.dup(root_fd)
    parent_fd = _open_directory_chain(root_fd, components[:-1], create=False)
    try:
        try:
            os.mkdir(components[-1], mode=0o700, dir_fd=parent_fd)
        except FileExistsError as exc:
            raise EvidenceBundleError("duplicate directory in evidence extraction") from exc
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        final_fd = os.open(components[-1], flags, dir_fd=parent_fd)
        try:
            _fchmod(final_fd, 0o700)
            info = os.fstat(final_fd)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != _current_uid() or info.st_nlink < 2:
                raise EvidenceBundleError("unsafe directory created during evidence extraction")
        except BaseException:
            os.close(final_fd)
            raise
        return final_fd
    finally:
        os.close(parent_fd)


def extract_evidence_volume(
    volume_path: Path,
    operation_root: Path,
    expected_binding: EvidenceBinding,
) -> VerifiedEvidenceBundle:
    """Verify then extract into a new private directory under operation_root.

    A completion marker is fsynced last.  A partial extraction intentionally
    has no marker and is not a usable evidence bundle.
    """
    operation_root = Path(operation_root).absolute()
    volume_path = Path(volume_path).absolute()
    if operation_root.name != expected_binding.operation_id:
        raise EvidenceBundleError("operation directory does not match the evidence operation ID")
    if volume_path.parent != operation_root or volume_path.name != "evidence.raw":
        raise EvidenceBundleError("evidence volume must be the operation-owned evidence.raw file")
    bundle = verify_evidence_volume(volume_path, expected_binding)
    if bundle.binding.operation_id != expected_binding.operation_id:
        raise EvidenceBundleError("cross-operation evidence replay rejected")
    root_fd, root_identity = _check_private_operation_root(operation_root)
    volume_fd = -1
    destination_fd = -1
    open_directories: dict[str, int] = {}
    marker_created = False
    try:
        volume_fd = os.open(
            "evidence.raw",
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            dir_fd=root_fd,
        )
        root_after = os.fstat(root_fd)
        if (root_after.st_dev, root_after.st_ino) != (root_identity.st_dev, root_identity.st_ino):
            raise EvidenceBundleError("operation root identity changed")
        volume_info = os.fstat(volume_fd)
        if (
            volume_info.st_dev,
            volume_info.st_ino,
            volume_info.st_size,
            volume_info.st_nlink,
            volume_info.st_mtime_ns,
            volume_info.st_ctime_ns,
        ) != (
            bundle.volume_device,
            bundle.volume_inode,
            bundle.volume_capacity,
            1,
            bundle.volume_mtime_ns,
            bundle.volume_ctime_ns,
        ):
            raise EvidenceBundleError("evidence volume changed before extraction")
        if volume_info.st_uid != _current_uid() or stat.S_IMODE(volume_info.st_mode) != 0o600:
            raise EvidenceBundleError("evidence volume ownership or mode changed before extraction")
        destination_fd = _open_directory_chain(root_fd, [EXTRACTION_DIRECTORY], create=True)
        open_directories[""] = os.dup(destination_fd)
        directory_entries = [entry for entry in bundle.entries if entry.kind == "directory"]
        for entry in directory_entries:
            components = entry.path.split("/")
            directory_fd = _create_directory_chain(destination_fd, components)
            open_directories[entry.path] = directory_fd
        for entry in bundle.entries:
            if entry.kind != "file":
                continue
            components = entry.path.split("/")
            parent_fd = _open_directory_chain(destination_fd, components[:-1], create=False)
            try:
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
                file_fd = os.open(components[-1], flags, entry.mode, dir_fd=parent_fd)
            finally:
                os.close(parent_fd)
            digest = hashlib.sha256()
            cursor = entry.offset
            remaining = entry.size
            try:
                while remaining:
                    chunk = _read_exact(volume_fd, min(HASH_CHUNK_BYTES, remaining), cursor)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(file_fd, view)
                        if written <= 0:
                            raise EvidenceBundleError("short write extracting evidence")
                        view = view[written:]
                    digest.update(chunk)
                    cursor += len(chunk)
                    remaining -= len(chunk)
                if digest.hexdigest() != entry.sha256:
                    raise EvidenceBundleError(f"evidence changed during extraction: {entry.path}")
                _fchmod(file_fd, entry.mode)
                os.fsync(file_fd)
                extracted = os.fstat(file_fd)
                if (
                    not stat.S_ISREG(extracted.st_mode)
                    or extracted.st_uid != _current_uid()
                    or extracted.st_nlink != 1
                    or stat.S_IMODE(extracted.st_mode) != entry.mode
                ):
                    raise EvidenceBundleError("unsafe extracted evidence file")
            finally:
                os.close(file_fd)
        # Links are created only after all regular files.  Each parent is
        # reopened from the extraction root with O_NOFOLLOW, so a link can
        # never redirect a subsequent write.
        for entry in bundle.entries:
            if entry.kind != "symlink":
                continue
            if entry.sha256 is None or entry.link_target is None:
                raise EvidenceBundleError("verified symlink lacks its bound target")
            target_bytes = _read_exact(volume_fd, entry.size, entry.offset)
            if hashlib.sha256(target_bytes).hexdigest() != entry.sha256:
                raise EvidenceBundleError(f"evidence changed during extraction: {entry.path}")
            target = _validated_symlink_target(entry.path, target_bytes)
            if target != entry.link_target:
                raise EvidenceBundleError(f"symlink target changed during extraction: {entry.path}")
            components = entry.path.split("/")
            parent_fd = _open_directory_chain(destination_fd, components[:-1], create=False)
            try:
                os.symlink(target, components[-1], dir_fd=parent_fd)
                linked = os.stat(components[-1], dir_fd=parent_fd, follow_symlinks=False)
                if not stat.S_ISLNK(linked.st_mode) or linked.st_uid != _current_uid() or linked.st_nlink != 1:
                    raise EvidenceBundleError("unsafe extracted evidence symlink")
                if os.readlink(components[-1], dir_fd=parent_fd) != target:
                    raise EvidenceBundleError("extracted evidence symlink target mismatch")
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        # Check the complete byte stream in manifest order after files and
        # symlinks are materialized.  Symlink target strings are payload too.
        if _hash_region(volume_fd, PAYLOAD_OFFSET, bundle.payload_size) != bundle.payload_sha256:
            raise EvidenceBundleError("evidence payload changed while extracting")
        # Preserve safe source directory modes after all children have been created.
        for entry in sorted(directory_entries, key=lambda item: item.path.count("/"), reverse=True):
            fd = open_directories[entry.path]
            _fchmod(fd, entry.mode)
            os.fsync(fd)
        # Persist the populated tree before publishing the completion marker.
        os.fsync(destination_fd)
        os.fsync(root_fd)
        marker = {
            "format": FORMAT,
            "status": "EXTRACTED_COMPLETE",
            "operation_id": bundle.binding.operation_id,
            "manifest_sha256": bundle.manifest_sha256,
            "payload_sha256": bundle.payload_sha256,
        }
        marker_bytes = canonical_json(marker)
        marker_fd = os.open(
            EXTRACTION_MARKER,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=destination_fd,
        )
        marker_created = True
        try:
            _fchmod(marker_fd, 0o600)
            marker_view = memoryview(marker_bytes)
            while marker_view:
                marker_written = os.write(marker_fd, marker_view)
                if marker_written <= 0:
                    raise EvidenceBundleError("short write of evidence extraction marker")
                marker_view = marker_view[marker_written:]
            os.fsync(marker_fd)
        finally:
            os.close(marker_fd)
        os.fsync(destination_fd)
        os.fsync(root_fd)
    except BaseException as extraction_error:
        # A marker visible after a failed final directory sync is ambiguous.
        # Remove it before returning failure so partial extraction cannot be
        # mistaken for a completed operation.
        cleanup_error: OSError | None = None
        if marker_created and destination_fd >= 0:
            try:
                os.unlink(EXTRACTION_MARKER, dir_fd=destination_fd)
            except OSError as exc:
                cleanup_error = exc
            try:
                os.fsync(destination_fd)
                os.fsync(root_fd)
            except OSError as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        if cleanup_error is not None:
            raise EvidenceBundleError(
                "extraction failed and completion marker cleanup could not be confirmed; "
                "the operation must be treated as incomplete"
            ) from cleanup_error
        raise extraction_error
    finally:
        for fd in open_directories.values():
            os.close(fd)
        if destination_fd >= 0:
            os.close(destination_fd)
        if volume_fd >= 0:
            os.close(volume_fd)
        os.close(root_fd)
    return bundle


def verify_extracted_tree(bundle: VerifiedEvidenceBundle, operation_root: Path) -> None:
    """Rehash the extracted tree against its bundle without following links.

    This is a final host-side recheck after consumers have inspected the
    target root. It detects concurrent edits, extra paths, and path swaps; it
    does not authenticate the worker that supplied the bundle.
    """
    if operation_root.name != bundle.binding.operation_id:
        raise EvidenceBundleError("extracted evidence belongs to another operation")
    root_fd, root_stat = _check_private_operation_root(operation_root)
    extraction_fd = -1
    try:
        extraction_fd = _open_directory_chain(root_fd, [EXTRACTION_DIRECTORY], create=False)
        extraction_stat = os.fstat(extraction_fd)
        if (
            extraction_stat.st_uid != _current_uid()
            or not stat.S_ISDIR(extraction_stat.st_mode)
            or stat.S_IMODE(extraction_stat.st_mode) != 0o700
            or extraction_stat.st_dev != root_stat.st_dev
        ):
            raise EvidenceBundleError("extracted evidence root identity or mode changed")
        expected_entries = {entry.path: entry for entry in bundle.entries}
        marker_value = {
            "format": FORMAT,
            "status": "EXTRACTED_COMPLETE",
            "operation_id": bundle.binding.operation_id,
            "manifest_sha256": bundle.manifest_sha256,
            "payload_sha256": bundle.payload_sha256,
        }
        marker_bytes = canonical_json(marker_value)
        expected_paths = set(expected_entries) | {EXTRACTION_MARKER}
        observed_paths: set[str] = set()

        def walk(directory_fd: int, relative: str) -> None:
            try:
                names = os.listdir(directory_fd)
            except OSError as exc:
                raise EvidenceBundleError("extracted evidence directory cannot be read") from exc
            for name in names:
                if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
                    raise EvidenceBundleError("extracted evidence contains an unsafe path component")
                path = f"{relative}/{name}" if relative else name
                observed_paths.add(path)
                try:
                    info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                except OSError as exc:
                    raise EvidenceBundleError("extracted evidence entry changed during inspection") from exc
                if path == EXTRACTION_MARKER:
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
                        raise EvidenceBundleError("extraction marker type or mode changed")
                    marker_fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
                    try:
                        marker_info = os.fstat(marker_fd)
                        actual = bytearray()
                        while True:
                            block = os.read(marker_fd, 64 * 1024)
                            if not block:
                                break
                            actual.extend(block)
                            if len(actual) > len(marker_bytes):
                                break
                        if (
                            (marker_info.st_dev, marker_info.st_ino) != (info.st_dev, info.st_ino)
                            or bytes(actual) != marker_bytes
                        ):
                            raise EvidenceBundleError("extraction marker identity changed")
                    finally:
                        os.close(marker_fd)
                    continue
                entry = expected_entries.get(path)
                if entry is None:
                    raise EvidenceBundleError(f"extracted evidence contains an unmanifested path: {path}")
                if info.st_uid != _current_uid():
                    raise EvidenceBundleError("extracted evidence owner changed")
                if entry.kind == "directory":
                    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != entry.mode:
                        raise EvidenceBundleError(f"extracted directory differs from its manifest: {path}")
                    child_fd = os.open(
                        name,
                        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
                        dir_fd=directory_fd,
                    )
                    try:
                        child_info = os.fstat(child_fd)
                        if (child_info.st_dev, child_info.st_ino) != (info.st_dev, info.st_ino):
                            raise EvidenceBundleError("extracted directory changed while opening")
                        walk(child_fd, path)
                    finally:
                        os.close(child_fd)
                elif entry.kind == "symlink":
                    if not stat.S_ISLNK(info.st_mode) or entry.link_target is None or entry.sha256 is None:
                        raise EvidenceBundleError(f"extracted symlink differs from its manifest: {path}")
                    target = os.readlink(name, dir_fd=directory_fd)
                    target_bytes = target.encode("utf-8", "strict")
                    if target != entry.link_target or len(target_bytes) != entry.size or hashlib.sha256(target_bytes).hexdigest() != entry.sha256:
                        raise EvidenceBundleError(f"extracted symlink target changed: {path}")
                else:
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_nlink != 1
                        or stat.S_IMODE(info.st_mode) != entry.mode
                        or info.st_size != entry.size
                        or entry.sha256 is None
                    ):
                        raise EvidenceBundleError(f"extracted file differs from its manifest: {path}")
                    file_fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), dir_fd=directory_fd)
                    try:
                        opened = os.fstat(file_fd)
                        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                            raise EvidenceBundleError("extracted file changed while opening")
                        digest = hashlib.sha256()
                        total = 0
                        while True:
                            block = os.read(file_fd, HASH_CHUNK_BYTES)
                            if not block:
                                break
                            digest.update(block)
                            total += len(block)
                        if total != entry.size or digest.hexdigest() != entry.sha256:
                            raise EvidenceBundleError(f"extracted file content changed: {path}")
                    finally:
                        os.close(file_fd)

        walk(extraction_fd, "")
        if observed_paths != expected_paths:
            raise EvidenceBundleError("extracted evidence path set differs from its manifest")
    finally:
        if extraction_fd >= 0:
            os.close(extraction_fd)
        os.close(root_fd)
