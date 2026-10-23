"""Fail-closed contract for a future sealed offline build VM.

This module is deliberately not wired to :mod:`usb_builder.build_usb`.  It
defines a fixed QEMU disk/argv contract, request and result formats, and a
bounded supervisor for a separately pinned worker image.  There is no
production deployment pin; callers must not treat synthetic pins or test
fixtures as evidence that offline builds are supported.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator, Mapping, NoReturn, Sequence


REQUEST_FORMAT = "librecore-offline-vm-request-v2"
RESULT_MAGIC = b"LCVMOUT3"
RESULT_HEADER_SIZE = 64 * 1024
RESULT_RECEIPT_MAGIC = b"LCVMSVC2"
RESULT_RECEIPT_SIZE = 2048
RESULT_COMMIT_MAGIC = b"LCVMCMT2"
RESULT_COMMIT_SIZE = 2048
RESULT_TERMINAL_MAGIC = b"LCVMFIN2"
RESULT_TERMINAL_SIZE = 2048
RESULT_RECEIPT_OFFSET = RESULT_HEADER_SIZE - RESULT_RECEIPT_SIZE - RESULT_COMMIT_SIZE - RESULT_TERMINAL_SIZE
RESULT_COMMIT_OFFSET = RESULT_RECEIPT_OFFSET + RESULT_RECEIPT_SIZE
RESULT_TERMINAL_OFFSET = RESULT_COMMIT_OFFSET + RESULT_COMMIT_SIZE
RESULT_ISO_OFFSET = RESULT_HEADER_SIZE
MAX_REQUEST_BYTES = 2 * 1024 * 1024
REQUEST_VOLUME_MAGIC = b"LCVMREQ1"
REQUEST_VOLUME_SIZE = 2 * 1024 * 1024
MAX_PROFILE_FILES = 20_000
MAX_ISO_BYTES = 16 * 1024 * 1024 * 1024
MAX_EVIDENCE_VOLUME_BYTES = 64 * 1024 * 1024 * 1024
MIN_EVIDENCE_VOLUME_BYTES = 2 * 1024 * 1024
MIN_SCRATCH_BYTES = 64 * 1024 * 1024
MAX_SCRATCH_BYTES = 80 * 1024 * 1024 * 1024
MAX_OUTPUT_BYTES = MAX_ISO_BYTES + RESULT_HEADER_SIZE
DEFAULT_WALL_SECONDS = 4 * 60 * 60
TERMINATION_GRACE_SECONDS = 5.0
RESULT_HASH_CHUNK_BYTES = 1024 * 1024

# Future production runs must place operation data directly below this
# reviewed root-owned sticky directory, on ext4. Tests inject an alternate
# observation explicitly; production has no caller-controlled parent.
APPROVED_OPERATION_PARENT = Path("/var/tmp")
APPROVED_PARENT_OWNER_UID = 0
APPROVED_PARENT_MODE = 0o1777
APPROVED_OPERATION_FILESYSTEMS = frozenset({"ext4"})
OPERATION_DIRECTORY_MODE = 0o700
OPERATION_FILE_MODE = 0o600

# Deliberately unset until the worker image, QEMU deployment and native
# no-NIC execution chain have exact reviewed pins.
PRODUCTION_WORKER_DEPLOYMENT: "WorkerDeployment | None" = None

LOCKED_PACKAGES = {
    "archiso": "91-1",
    "arch-install-scripts": "31-2",
    "pacman": "7.1.0.r9.g54d9411-2",
    "util-linux": "2.42.4-1",
    "bash": "5.3.20-1",
    "shadow": "4.20.0.arch1-1",
    "libcap": "2.78-1",
}
SNAPSHOT_LOCK_PATH = Path(__file__).with_name("snapshot.lock.json")

GUEST_FIXED_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/run/librecore-worker",
    "LANG": "C",
    "LC_ALL": "C",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONOPTIMIZE": "0",
}


class OfflineVmContractError(RuntimeError):
    """An offline VM contract or deployment pin failed closed."""


def _fail(message: str) -> NoReturn:
    raise OfflineVmContractError(message)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _validate_relpath(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        _fail("profile inventory path is not a canonical relative POSIX path")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or any(part in ("", ".", "..") for part in parsed.parts):
        _fail("profile inventory path is absolute or traverses its source image")
    normalized = parsed.as_posix()
    if normalized != value:
        _fail("profile inventory path is not normalized")
    return normalized


@dataclass(frozen=True)
class ProfileFile:
    path: str
    size: int
    sha256: str

    def as_dict(self) -> dict[str, object]:
        return {"path": self.path, "size": self.size, "sha256": self.sha256}


def profile_inventory_digest(files: Sequence[ProfileFile]) -> str:
    return _sha256_bytes(canonical_json({"files": [item.as_dict() for item in files]}))


@dataclass(frozen=True)
class ImageIdentity:
    """Host-side path plus immutable content identity for one VM disk."""

    path: Path
    size: int
    sha256: str


@dataclass(frozen=True)
class VolumeIdentity:
    """Operation-owned writable disk; its identity is path/size, not content hash."""

    path: Path
    size: int


@dataclass(frozen=True)
class WorkerDeployment:
    """Exact executable and worker-image identities for a reviewed deployment."""

    qemu: ImageIdentity
    worker_image: ImageIdentity
    worker_image_format: str = "raw"


@dataclass(frozen=True)
class WorkerPlan:
    operation_id: str
    snapshot_lock_sha256: str
    snapshot_date: str
    package_versions: Mapping[str, str]
    worker_image_sha256: str
    source_image: ImageIdentity
    package_image: ImageIdentity
    request_image: ImageIdentity
    scratch_volume: VolumeIdentity
    output_volume: VolumeIdentity
    evidence_volume: VolumeIdentity
    profile_files: tuple[ProfileFile, ...]
    log_path: Path
    timeout_seconds: int = DEFAULT_WALL_SECONDS


@dataclass(frozen=True)
class WorkerRun:
    argv_sha256: str
    returncode: int | None
    timed_out: bool
    elapsed_seconds: float
    readonly_hashes_before: Mapping[str, str]
    readonly_hashes_after: Mapping[str, str]
    outcome: str
    candidate_iso_structurally_validated: bool


def validate_package_versions(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or value != LOCKED_PACKAGES:
        _fail("guest package versions do not exactly match snapshot.lock.json (including pacman -2)")
    return dict(value)


def _pinned_snapshot_lock_sha256() -> str:
    try:
        raw = SNAPSHOT_LOCK_PATH.read_bytes()
        lock = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfflineVmContractError("packaged snapshot.lock.json is missing or malformed") from exc
    if not isinstance(lock, dict) or lock.get("snapshot_date") != "2026-09-30":
        _fail("packaged snapshot lock date differs from the reviewed repository")
    rootless = lock.get("rootless_builder")
    package_map = {
        "archiso": lock.get("archiso_version"),
        "arch-install-scripts": rootless.get("arch_install_scripts_version") if isinstance(rootless, dict) else None,
        "pacman": rootless.get("pacman_version") if isinstance(rootless, dict) else None,
        "util-linux": rootless.get("util_linux_version") if isinstance(rootless, dict) else None,
        "bash": rootless.get("bash_version") if isinstance(rootless, dict) else None,
        "shadow": rootless.get("map_helper_package_version") if isinstance(rootless, dict) else None,
        "libcap": rootless.get("libcap_version") if isinstance(rootless, dict) else None,
    }
    if package_map != LOCKED_PACKAGES:
        _fail("packaged snapshot lock package versions differ from the fixed worker protocol")
    overlay = rootless.get("pacstrap_status_overlay") if isinstance(rootless, dict) else None
    if not isinstance(overlay, dict) or overlay.get("source_sha256") != "c0cbe2eb3e77a2a4437b03021ec1e63f31f20888778cd3c70ff49d2368371207" or overlay.get("patched_sha256") != "3964ed5b509f791e9f98cda494c4af5febe6daf0174df75b9bb10a7af3f2c04c":
        _fail("packaged snapshot lock lacks the reviewed pacstrap child-status patch pin")
    return hashlib.sha256(raw).hexdigest()


def make_request(plan: WorkerPlan) -> dict[str, object]:
    if re.fullmatch(r"[0-9a-f]{32}", plan.operation_id) is None:
        _fail("operation id must be 32 lowercase hexadecimal characters")
    if not _is_sha256(plan.snapshot_lock_sha256) or plan.snapshot_lock_sha256 != _pinned_snapshot_lock_sha256():
        _fail("snapshot lock identity does not match the packaged lock bytes")
    if plan.snapshot_date != "2026-09-30":
        _fail("snapshot date is not the pinned 2026-09-30 repository")
    versions = validate_package_versions(dict(plan.package_versions))
    if not _is_sha256(plan.worker_image_sha256):
        _fail("worker image hash is malformed")
    if not 60 <= plan.timeout_seconds <= 4 * 60 * 60:
        _fail("worker timeout is outside the reviewed 60s..4h bound")
    if not plan.profile_files or len(plan.profile_files) > MAX_PROFILE_FILES:
        _fail("profile inventory is empty or exceeds its bounded entry count")
    seen: set[str] = set()
    files: list[dict[str, object]] = []
    for item in plan.profile_files:
        path = _validate_relpath(item.path)
        if path in seen or isinstance(item.size, bool) or item.size < 0 or not _is_sha256(item.sha256):
            _fail("profile inventory contains a duplicate or malformed file entry")
        seen.add(path)
        files.append(item.as_dict())
    if tuple(sorted(seen)) != tuple(item.path for item in plan.profile_files):
        _fail("profile inventory must be sorted by canonical path")
    for label, identity in (
        ("source", plan.source_image),
        ("packages", plan.package_image),
        ("request", plan.request_image),
    ):
        if identity.size <= 0 or not _is_sha256(identity.sha256):
            _fail(f"{label} image identity is malformed")
    if plan.request_image.size != REQUEST_VOLUME_SIZE or not plan.request_image.path.is_absolute():
        _fail("request image path/size differs from the fixed request volume contract")
    for label, volume in (
        ("scratch", plan.scratch_volume),
        ("output", plan.output_volume),
        ("evidence", plan.evidence_volume),
    ):
        if isinstance(volume.size, bool) or not isinstance(volume.size, int) or volume.size <= 0:
            _fail(f"{label} writable volume size is malformed")
    if plan.output_volume.size <= RESULT_HEADER_SIZE:
        _fail("output volume is too small for the fixed result header")
    if not MIN_SCRATCH_BYTES <= plan.scratch_volume.size <= MAX_SCRATCH_BYTES:
        _fail("scratch volume size is outside the fixed 64 MiB..80 GiB contract")
    if not RESULT_HEADER_SIZE < plan.output_volume.size <= MAX_OUTPUT_BYTES:
        _fail("output volume size is outside the fixed result/ISO bound")
    if not MIN_EVIDENCE_VOLUME_BYTES <= plan.evidence_volume.size <= MAX_EVIDENCE_VOLUME_BYTES:
        _fail("evidence volume capacity is outside the fixed 2 MiB..64 GiB contract")
    if not plan.source_image.path.is_absolute() or not plan.package_image.path.is_absolute():
        _fail("source and package image paths must be absolute")
    if plan.source_image.path == plan.package_image.path:
        _fail("source and package images must be separate files")
    request: dict[str, object] = {
        "format": REQUEST_FORMAT,
        "operation_id": plan.operation_id,
        "profile_id": "librecore-v1",
        "worker_image_sha256": plan.worker_image_sha256,
        "snapshot": {
            "date": plan.snapshot_date,
            "lock_sha256": plan.snapshot_lock_sha256,
            "packages": versions,
        },
        "source": {"sha256": plan.source_image.sha256, "size": plan.source_image.size},
        "packages": {"sha256": plan.package_image.sha256, "size": plan.package_image.size},
        "profile": {"files": files, "sha256": profile_inventory_digest(plan.profile_files)},
        "layout": {
            "source_device": "LC-SOURCE-RO",
            "package_device": "LC-PACKAGES-RO",
            "scratch_device": "LC-SCRATCH-RW",
            "output_device": "LC-OUTPUT-RW",
            "evidence_device": "LC-EVIDENCE-RW",
            "result_header_size": RESULT_HEADER_SIZE,
            "iso_offset": RESULT_ISO_OFFSET,
            "output_capacity": plan.output_volume.size,
            "evidence_capacity": plan.evidence_volume.size,
        },
        "expected_boot_modes": ["bios", "uefi"],
    }
    if len(canonical_json(request)) > MAX_REQUEST_BYTES:
        _fail("worker request exceeds its fixed size bound")
    return request


def encode_request_volume(request: Mapping[str, object]) -> bytes:
    payload = canonical_json(dict(request))
    parse_request(payload)
    encoded = REQUEST_VOLUME_MAGIC + len(payload).to_bytes(4, "big") + payload
    if len(encoded) > REQUEST_VOLUME_SIZE:
        _fail("request volume payload exceeds its fixed capacity")
    return encoded + bytes(REQUEST_VOLUME_SIZE - len(encoded))


def decode_request_volume(data: bytes) -> dict[str, object]:
    if len(data) != REQUEST_VOLUME_SIZE or not data.startswith(REQUEST_VOLUME_MAGIC):
        _fail("request block is missing the fixed protocol header or exact size")
    start = len(REQUEST_VOLUME_MAGIC)
    size = int.from_bytes(data[start : start + 4], "big")
    if not 0 < size <= MAX_REQUEST_BYTES:
        _fail("request block payload length is invalid")
    payload_start = start + 4
    payload_end = payload_start + size
    if any(data[payload_end:]):
        _fail("request block padding is not zero")
    return parse_request(data[payload_start:payload_end])


def write_request_volume(path: Path, request: Mapping[str, object]) -> ImageIdentity:
    """Create a new exclusive request image; existing paths are never overwritten."""
    if not path.is_absolute() or _path_has_symlink_component(path.parent):
        _fail("request volume destination is not an absolute operation-owned path")
    # Validate first: malformed caller data must not leave a created file or
    # descriptor behind if serialization fails.
    encoded = encode_request_volume(request)
    if os.name == "posix":
        _validate_operation_directory(path.parent, path.parent.name)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise OfflineVmContractError("request volume already exists or cannot be created safely") from exc
    owned_fd = fd
    try:
        if os.name == "posix":
            os.fchmod(owned_fd, OPERATION_FILE_MODE)
        stream = os.fdopen(owned_fd, "wb")
        owned_fd = -1
        with stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        if owned_fd >= 0:
            try:
                os.close(owned_fd)
            except OSError:
                pass
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return ImageIdentity(path=path, size=len(encoded), sha256=_sha256_bytes(encoded))


def materialize_request_volume(plan: WorkerPlan) -> tuple[WorkerPlan, dict[str, object]]:
    """Create this operation's request disk and return the plan bound to its bytes."""
    request = make_request(plan)
    identity = write_request_volume(plan.request_image.path, request)
    return replace(plan, request_image=identity), request


def parse_request(data: bytes) -> dict[str, object]:
    if not isinstance(data, bytes) or not data or len(data) > MAX_REQUEST_BYTES:
        _fail("worker request is empty or exceeds its fixed size bound")
    try:
        value = json.loads(data.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfflineVmContractError("worker request is not valid ASCII JSON") from exc
    if not isinstance(value, dict) or canonical_json(value) != data:
        _fail("worker request is not canonical JSON")
    expected_top = {
        "format", "operation_id", "profile_id", "worker_image_sha256", "snapshot", "source", "packages",
        "profile", "layout", "expected_boot_modes",
    }
    if set(value) != expected_top or value.get("format") != REQUEST_FORMAT:
        _fail("worker request has unknown/missing fields or the wrong format")
    if value.get("profile_id") != "librecore-v1" or value.get("expected_boot_modes") != ["bios", "uefi"]:
        _fail("worker request is not the fixed LibreCore V1 dual-boot profile")
    if not _is_sha256(value.get("worker_image_sha256")):
        _fail("worker request does not bind a pinned guest image")
    if re.fullmatch(r"[0-9a-f]{32}", str(value.get("operation_id", ""))) is None:
        _fail("worker request operation id is malformed")
    snapshot = value.get("snapshot")
    if not isinstance(snapshot, dict) or set(snapshot) != {"date", "lock_sha256", "packages"}:
        _fail("worker snapshot binding is malformed")
    if snapshot.get("date") != "2026-09-30" or not _is_sha256(snapshot.get("lock_sha256")):
        _fail("worker snapshot is not bound to the locked 2026-09-30 lock")
    validate_package_versions(snapshot.get("packages"))
    for key in ("source", "packages"):
        binding = value.get(key)
        if not isinstance(binding, dict) or set(binding) != {"sha256", "size"}:
            _fail(f"worker {key} image binding is malformed")
        if not _is_sha256(binding.get("sha256")) or isinstance(binding.get("size"), bool) or not isinstance(binding.get("size"), int) or binding["size"] <= 0:
            _fail(f"worker {key} image binding has invalid size/hash")
    profile = value.get("profile")
    if not isinstance(profile, dict) or set(profile) != {"files", "sha256"} or not _is_sha256(profile.get("sha256")):
        _fail("worker profile binding is malformed")
    raw_files = profile.get("files")
    if not isinstance(raw_files, list) or not raw_files or len(raw_files) > MAX_PROFILE_FILES:
        _fail("worker profile file list is empty or over limit")
    parsed_files: list[ProfileFile] = []
    for item in raw_files:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            _fail("worker profile entry has unknown or missing fields")
        path = _validate_relpath(item.get("path"))
        size = item.get("size")
        digest = item.get("sha256")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0 or not isinstance(digest, str) or not _is_sha256(digest):
            _fail("worker profile entry has invalid size/hash")
        parsed_files.append(ProfileFile(path, size, digest))
    if [entry.path for entry in parsed_files] != sorted({entry.path for entry in parsed_files}):
        _fail("worker profile entries are unsorted or duplicated")
    if profile_inventory_digest(parsed_files) != profile["sha256"]:
        _fail("worker profile inventory digest does not match its entries")
    layout = value.get("layout")
    expected_layout_keys = {
        "source_device", "package_device", "scratch_device", "output_device", "evidence_device",
        "result_header_size", "iso_offset", "output_capacity", "evidence_capacity",
    }
    if not isinstance(layout, dict) or set(layout) != expected_layout_keys:
        _fail("worker disk layout is malformed")
    expected_layout = {
        "source_device": "LC-SOURCE-RO", "package_device": "LC-PACKAGES-RO",
        "scratch_device": "LC-SCRATCH-RW", "output_device": "LC-OUTPUT-RW",
        "evidence_device": "LC-EVIDENCE-RW",
        "result_header_size": RESULT_HEADER_SIZE, "iso_offset": RESULT_ISO_OFFSET,
    }
    if any(layout.get(key) != expected for key, expected in expected_layout.items()):
        _fail("worker disk layout differs from fixed device/offset policy")
    capacity = layout.get("output_capacity")
    if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= RESULT_HEADER_SIZE:
        _fail("worker output capacity is invalid")
    evidence_capacity = layout.get("evidence_capacity")
    if (
        isinstance(evidence_capacity, bool)
        or not isinstance(evidence_capacity, int)
        or not MIN_EVIDENCE_VOLUME_BYTES <= evidence_capacity <= MAX_EVIDENCE_VOLUME_BYTES
    ):
        _fail("worker evidence capacity is invalid")
    return value


def _path_has_symlink_component(path: Path) -> bool:
    absolute = Path(os.path.abspath(os.fspath(path)))
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor = cursor / part
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            return True
        if stat.S_ISLNK(info.st_mode):
            return True
        is_junction = getattr(cursor, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
    return False


def _effective_uid() -> int:
    geteuid = getattr(os, "geteuid", None)
    if not callable(geteuid):
        _fail("effective UID evidence is unavailable on this platform")
    return int(geteuid())


def _mount_filesystem_type(path: Path, mountinfo_path: Path = Path("/proc/self/mountinfo")) -> str:
    """Return the most-specific mount filesystem covering ``path``."""
    try:
        mount_lines = mountinfo_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise OfflineVmContractError("operation filesystem evidence is unavailable") from exc
    absolute = Path(os.path.abspath(os.fspath(path)))
    matches: list[tuple[int, str]] = []
    for line in mount_lines:
        before, separator, after = line.partition(" - ")
        if not separator:
            continue
        fields = before.split()
        tail = after.split()
        if len(fields) < 5 or not tail:
            continue
        mountpoint = fields[4]
        for escaped, decoded in (("\\040", " "), ("\\011", "\t"), ("\\134", "\\")):
            mountpoint = mountpoint.replace(escaped, decoded)
        candidate = Path(mountpoint)
        try:
            absolute.relative_to(candidate)
        except ValueError:
            continue
        matches.append((len(candidate.parts), tail[0]))
    if not matches:
        raise OfflineVmContractError("operation filesystem is not present in mountinfo")
    return max(matches)[1]


def _validate_operation_directory(operation_root: Path, operation_id: str) -> os.stat_result:
    if os.name != "posix":
        _fail("sealed offline VM operation storage is supported only on Linux")
    if operation_root.parent != APPROVED_OPERATION_PARENT or operation_root.name != operation_id:
        _fail("worker operation directory is outside the fixed approved parent")
    if _path_has_symlink_component(APPROVED_OPERATION_PARENT):
        _fail("approved worker parent contains a symlink or junction")
    try:
        parent_info = APPROVED_OPERATION_PARENT.lstat()
        root_info = operation_root.lstat()
    except OSError as exc:
        raise OfflineVmContractError("approved worker parent or operation directory is unavailable") from exc
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != APPROVED_PARENT_OWNER_UID
        or stat.S_IMODE(parent_info.st_mode) != APPROVED_PARENT_MODE
    ):
        _fail("approved worker parent owner/mode differs from the fixed policy")
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or root_info.st_uid != _effective_uid()
        or stat.S_IMODE(root_info.st_mode) != OPERATION_DIRECTORY_MODE
        or root_info.st_dev != parent_info.st_dev
    ):
        _fail("worker operation directory must be caller-owned mode 0700 on the approved filesystem")
    filesystem = _mount_filesystem_type(operation_root)
    if filesystem not in APPROVED_OPERATION_FILESYSTEMS:
        _fail("worker operation directory is not on an approved filesystem")
    return root_info


def _validate_open_file(
    *,
    path: Path,
    fd: int,
    expected_size: int,
    label: str,
    writable_operation_file: bool,
    operation_root: Path | None = None,
) -> os.stat_result:
    """Bind an opened regular file to its no-link pathname and fixed policy."""
    if _path_has_symlink_component(path):
        _fail(f"{label} path contains a symlink or junction component")
    opened = os.fstat(fd)
    try:
        named = path.lstat()
    except OSError as exc:
        raise OfflineVmContractError(f"{label} pathname changed during secure open") from exc
    opened_key = (opened.st_dev, opened.st_ino)
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_size != expected_size
        or opened.st_nlink != 1
        or opened_key != (named.st_dev, named.st_ino)
        or stat.S_ISLNK(named.st_mode)
    ):
        _fail(f"{label} must be a single-link exact-size regular file bound to its opened inode")
    if writable_operation_file:
        if operation_root is None or path.parent != operation_root:
            _fail(f"{label} is outside its fixed operation directory")
        root_info = operation_root.stat()
        if (
            opened.st_uid != _effective_uid()
            or stat.S_IMODE(opened.st_mode) != OPERATION_FILE_MODE
            or opened.st_dev != root_info.st_dev
        ):
            _fail(f"{label} owner/mode/filesystem differs from the fixed operation-file policy")
    return opened


def _hash_fd(fd: int) -> str:
    original = os.lseek(fd, 0, os.SEEK_CUR)
    digest = hashlib.sha256()
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            block = os.read(fd, RESULT_HASH_CHUNK_BYTES)
            if not block:
                break
            digest.update(block)
    finally:
        os.lseek(fd, original, os.SEEK_SET)
    return digest.hexdigest()


def _read_fd(fd: int, max_bytes: int) -> bytes:
    original = os.lseek(fd, 0, os.SEEK_CUR)
    chunks: list[bytes] = []
    total = 0
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            chunk = os.read(fd, min(1024 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                _fail("opened request volume exceeds its fixed protocol bound")
    finally:
        os.lseek(fd, original, os.SEEK_SET)
    return b"".join(chunks)


def _pread_exact(fd: int, size: int, offset: int) -> bytes:
    pread = getattr(os, "pread", None)
    if not callable(pread):
        _fail("positional output-header inspection is unavailable")
    data = pread(fd, size, offset)
    if not isinstance(data, bytes) or len(data) != size:
        _fail("output header is truncated during pre-launch inspection")
    return data


@contextmanager
def _open_launch_files(plan: WorkerPlan, deployment: WorkerDeployment) -> Any:
    """Pin all QEMU file inputs to opened inodes and yield fd-backed paths."""
    operation_root = plan.scratch_volume.path.parent
    if os.name == "posix":
        _validate_operation_directory(operation_root, plan.operation_id)
    specs: tuple[tuple[str, Path, int, str | None, bool], ...] = (
        ("qemu", deployment.qemu.path, deployment.qemu.size, deployment.qemu.sha256, False),
        ("worker", deployment.worker_image.path, deployment.worker_image.size, deployment.worker_image.sha256, False),
        ("request", plan.request_image.path, plan.request_image.size, plan.request_image.sha256, False),
        ("source", plan.source_image.path, plan.source_image.size, plan.source_image.sha256, False),
        ("packages", plan.package_image.path, plan.package_image.size, plan.package_image.sha256, False),
        ("scratch", plan.scratch_volume.path, plan.scratch_volume.size, None, True),
        ("output", plan.output_volume.path, plan.output_volume.size, None, True),
        ("evidence", plan.evidence_volume.path, plan.evidence_volume.size, None, True),
    )
    descriptors: dict[str, int] = {}
    paths: dict[str, str] = {}
    before: dict[str, str] = {}
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    try:
        for key, path, expected_size, expected_hash, writable in specs:
            flags = (os.O_RDWR if writable else os.O_RDONLY) | nofollow | cloexec
            try:
                fd = os.open(path, flags)
            except OSError as exc:
                raise OfflineVmContractError(f"{key} launch image could not be opened without following links") from exc
            descriptors[key] = fd
            opened = _validate_open_file(
                path=path,
                fd=fd,
                expected_size=expected_size,
                label=key,
                writable_operation_file=writable or key == "request",
                operation_root=operation_root if writable or key == "request" else None,
            )
            if key == "qemu" and not (opened.st_mode & 0o111):
                _fail("pinned QEMU executable is not executable")
            if key == "request":
                request_value = decode_request_volume(_read_fd(fd, REQUEST_VOLUME_SIZE))
                if request_value != make_request(plan):
                    _fail("opened request inode differs from the canonical operation request")
            if key == "output" and _pread_exact(fd, RESULT_HEADER_SIZE, 0) != bytes(RESULT_HEADER_SIZE):
                _fail("output result header is not blank before the worker launch")
            if key == "evidence" and _pread_exact(fd, 4096, 0) != bytes(4096):
                _fail("evidence volume header is not blank before the worker launch")
            if expected_hash is not None:
                actual_hash = _hash_fd(fd)
                if actual_hash != expected_hash:
                    _fail(f"opened {key} launch image SHA-256 differs from its pin")
                before[key] = actual_hash
            paths[key] = f"/proc/self/fd/{fd}"
        yield paths, tuple(descriptors.values()), descriptors, before
        # Recheck both the held inode and its pathname after the child exits.
        for key, path, expected_size, expected_hash, writable in specs:
            fd = descriptors[key]
            opened = _validate_open_file(
                path=path,
                fd=fd,
                expected_size=expected_size,
                label=key,
                writable_operation_file=writable or key == "request",
                operation_root=operation_root if writable or key == "request" else None,
            )
            if expected_hash is not None and _hash_fd(fd) != expected_hash:
                _fail(f"read-only {key} input changed through its opened inode")
            if writable and (opened.st_nlink != 1 or stat.S_IMODE(opened.st_mode) != OPERATION_FILE_MODE):
                _fail(f"writable {key} inode link count or mode changed during QEMU execution")
    finally:
        for fd in descriptors.values():
            try:
                os.close(fd)
            except OSError:
                pass


def verify_file_identity(identity: ImageIdentity, label: str) -> None:
    if not identity.path.is_absolute() or _path_has_symlink_component(identity.path):
        _fail(f"{label} path is not absolute or has a symlink/junction component")
    try:
        info = identity.path.lstat()
    except OSError as exc:
        raise OfflineVmContractError(f"{label} image is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_size != identity.size:
        _fail(f"{label} is not the exact-size regular image")
    if label == "QEMU" and os.name == "posix" and not (info.st_mode & 0o111):
        _fail("QEMU executable pin is not executable")
    if _sha256_file(identity.path) != identity.sha256:
        _fail(f"{label} SHA-256 changed")


def verify_writable_volume(volume: VolumeIdentity, label: str, operation_root: Path) -> None:
    if not volume.path.is_absolute() or _path_has_symlink_component(volume.path):
        _fail(f"{label} volume path is not absolute or has a linked component")
    if volume.path.parent != operation_root:
        _fail(f"{label} volume is outside the fixed worker operation directory")
    try:
        info = volume.path.lstat()
    except OSError as exc:
        raise OfflineVmContractError(f"{label} volume is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_size != volume.size or info.st_nlink != 1:
        _fail(f"{label} volume is not a single-link exact-size regular operation file")
    if os.name == "posix":
        root_info = operation_root.stat()
        if (
        info.st_uid != _effective_uid()
            or stat.S_IMODE(info.st_mode) != OPERATION_FILE_MODE
            or info.st_dev != root_info.st_dev
        ):
            _fail(f"{label} volume owner/mode/filesystem differs from the fixed operation-file policy")


def _verify_operation_file_path(path: Path, size: int, label: str, operation_root: Path) -> os.stat_result:
    if not path.is_absolute() or path.parent != operation_root or _path_has_symlink_component(path):
        _fail(f"{label} path is not a direct, non-linked operation file")
    try:
        info = path.lstat()
    except OSError as exc:
        raise OfflineVmContractError(f"{label} operation file is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_size != size or info.st_nlink != 1:
        _fail(f"{label} must be a single-link exact-size regular operation file")
    if os.name == "posix":
        root_info = operation_root.stat()
        if (
            info.st_uid != _effective_uid()
            or stat.S_IMODE(info.st_mode) != OPERATION_FILE_MODE
            or info.st_dev != root_info.st_dev
        ):
            _fail(f"{label} owner/mode/filesystem differs from the fixed operation-file policy")
    return info


def _json_blockdev(file_node: str, drive_node: str, path: Path, readonly: bool) -> tuple[str, str]:
    file_config = {"driver": "file", "filename": str(path), "node-name": file_node, "read-only": readonly}
    raw_config = {"driver": "raw", "file": file_node, "node-name": drive_node, "read-only": readonly}
    return json.dumps(file_config, separators=(",", ":")), json.dumps(raw_config, separators=(",", ":"))


def build_qemu_argv(
    plan: WorkerPlan,
    deployment: WorkerDeployment,
    *,
    fd_paths: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Build the only permitted QEMU argv; this function never executes it."""
    request = make_request(plan)
    if plan.worker_image_sha256 != deployment.worker_image.sha256:
        _fail("worker request guest-image hash differs from the pinned deployment")
    if plan.request_image.size != REQUEST_VOLUME_SIZE:
        _fail("request image must have the exact fixed volume size")
    for label, identity in (
        ("QEMU", deployment.qemu), ("worker base", deployment.worker_image),
        ("source", plan.source_image), ("package repository", plan.package_image),
        ("request", plan.request_image),
    ):
        verify_file_identity(identity, label)
    operation_root = plan.scratch_volume.path.parent
    if os.name == "posix":
        _validate_operation_directory(operation_root, plan.operation_id)
    verify_writable_volume(plan.scratch_volume, "scratch", operation_root)
    verify_writable_volume(plan.output_volume, "output", operation_root)
    verify_writable_volume(plan.evidence_volume, "evidence", operation_root)
    _verify_operation_file_path(plan.request_image.path, plan.request_image.size, "request", operation_root)
    if deployment.worker_image_format != "raw":
        _fail("worker deployment image format must be raw")
    identities = [deployment.worker_image, plan.source_image, plan.package_image, plan.request_image]
    volume_paths = [plan.scratch_volume.path, plan.output_volume.path, plan.evidence_volume.path]
    if len({(p.stat().st_dev, p.stat().st_ino) for p in volume_paths}) != len(volume_paths):
        _fail("scratch and output volumes must be separate files")
    physical: set[tuple[int, int]] = set()
    for identity in identities:
        stat_result = identity.path.stat()
        key = (stat_result.st_dev, stat_result.st_ino)
        if key in physical:
            _fail("worker, input, scratch, and output images must be separate files")
        physical.add(key)
    for path in volume_paths:
        info = path.stat()
        key = (info.st_dev, info.st_ino)
        if key in physical:
            _fail("writable volumes must be separate from every read-only worker/input image")
        physical.add(key)
    if plan.source_image.sha256 != request["source"]["sha256"] or plan.package_image.sha256 != request["packages"]["sha256"]:  # type: ignore[index]
        _fail("worker request inputs do not match the verified source/package images")
    request_bytes = plan.request_image.path.read_bytes()
    if decode_request_volume(request_bytes) != request:
        _fail("worker request file differs from the canonical operation request")
    if any(item.path.parent != operation_root for item in (plan.request_image, plan.scratch_volume, plan.output_volume, plan.evidence_volume)):
        _fail("request, scratch, output, and evidence images must share one operation-owned directory")
    if not plan.log_path.is_absolute() or plan.log_path.exists() or plan.log_path.parent != operation_root:
        _fail("worker log path must be new within the private operation directory")

    argv: list[str] = [
        str(deployment.qemu.path),
        "-nodefaults", "-no-user-config", "-no-reboot",
        "-machine", "q35,accel=tcg", "-cpu", "max", "-smp", "2", "-m", "4096",
        "-display", "none", "-monitor", "none", "-serial", "null", "-nic", "none",
    ]
    def block_path(key: str, path: Path) -> Path:
        if fd_paths is None:
            return path
        raw = fd_paths.get(key)
        if not isinstance(raw, str) or not raw.startswith("/proc/self/fd/"):
            _fail("launch file descriptor map is incomplete or malformed")
        return Path(raw)

    disks = (
        ("worker-file", "worker-drive", deployment.worker_image, "worker", "LC-WORKER-RO", True),
        ("request-file", "request-drive", plan.request_image, "request", "LC-REQUEST-RO", True),
        ("source-file", "source-drive", plan.source_image, "source", "LC-SOURCE-RO", True),
        ("packages-file", "packages-drive", plan.package_image, "packages", "LC-PACKAGES-RO", True),
    )
    for file_node, drive_node, identity, disk_key, serial, readonly in disks:
        file_arg, raw_arg = _json_blockdev(file_node, drive_node, block_path(disk_key, identity.path), readonly)
        argv.extend(("-blockdev", file_arg, "-blockdev", raw_arg))
        argv.extend(("-device", f"virtio-blk-pci,drive={drive_node},serial={serial}"))
    for file_node, drive_node, volume, volume_key, serial in (
        ("scratch-file", "scratch-drive", plan.scratch_volume, "scratch", "LC-SCRATCH-RW"),
        ("output-file", "output-drive", plan.output_volume, "output", "LC-OUTPUT-RW"),
        ("evidence-file", "evidence-drive", plan.evidence_volume, "evidence", "LC-EVIDENCE-RW"),
    ):
        file_arg, raw_arg = _json_blockdev(file_node, drive_node, block_path(volume_key, volume.path), False)
        argv.extend(("-blockdev", file_arg, "-blockdev", raw_arg))
        argv.extend(("-device", f"virtio-blk-pci,drive={drive_node},serial={serial}"))
    if any("-netdev" in item or "hostfwd" in item or "virtfs" in item or "usb-host" in item for item in argv):
        _fail("fixed QEMU argv unexpectedly contains network, host-share, or passthrough access")
    return tuple(argv)


def _readonly_identities(deployment: WorkerDeployment, plan: WorkerPlan) -> dict[str, ImageIdentity]:
    return {
        "qemu": deployment.qemu,
        "worker": deployment.worker_image,
        "request": plan.request_image,
        "source": plan.source_image,
        "packages": plan.package_image,
    }


def _fixed_host_environment(operation_root: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(operation_root),
        "TMPDIR": str(operation_root),
        "LANG": "C",
        "LC_ALL": "C",
    }


def _create_worker_log_fd(path: Path, operation_root: Path) -> int:
    if (
        not path.is_absolute()
        or path.parent != operation_root
        or _path_has_symlink_component(path.parent)
        or path.exists()
    ):
        _fail("QEMU log must be a new direct file in the validated private operation directory")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags, OPERATION_FILE_MODE)
    except OSError as exc:
        raise OfflineVmContractError("QEMU log could not be created exclusively in the operation directory") from exc
    try:
        os.fchmod(fd, OPERATION_FILE_MODE)
        info = os.fstat(fd)
        named = path.lstat()
        root_info = operation_root.stat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != _effective_uid()
            or stat.S_IMODE(info.st_mode) != OPERATION_FILE_MODE
            or info.st_dev != root_info.st_dev
            or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)
        ):
            _fail("QEMU log owner/mode/link-count/filesystem differs from the fixed policy")
        return fd
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _assert_unprivileged_host() -> None:
    if not hasattr(os, "geteuid") or _effective_uid() == 0:
        _fail("sealed offline worker host must be an unprivileged Linux account")
    try:
        lines = Path("/proc/self/status").read_text(encoding="ascii").splitlines()
    except OSError as exc:
        raise OfflineVmContractError("host capability state is unavailable") from exc
    fields = {line.partition(":")[0]: line.partition(":")[2].strip() for line in lines if ":" in line}
    if any(fields.get(name) != "0000000000000000" for name in ("CapEff", "CapPrm", "CapAmb")):
        _fail("sealed offline worker host must have empty effective/permitted/ambient capabilities")


def _terminate_process(process: Any) -> None:
    if os.name == "nt":
        try:
            process.terminate()
        except OSError:
            pass
    else:
        try:
            _kill_process_group(process.pid, signal.SIGTERM)
        except OSError:
            pass
    try:
        process.wait(timeout=TERMINATION_GRACE_SECONDS)
        if os.name != "nt":
            # The QEMU parent may exit while a descendant remains in its new
            # session/process group. Remove any remainder after bounded TERM.
            try:
                _kill_process_group(process.pid, int(getattr(signal, "SIGKILL", 9)))
            except OSError:
                pass
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "nt":
        try:
            process.kill()
        except OSError:
            pass
    else:
        try:
            _kill_process_group(process.pid, int(getattr(signal, "SIGKILL", 9)))
        except OSError:
            pass
    try:
        process.wait(timeout=TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        _fail("QEMU process group could not be reaped after forced termination")


def _kill_process_group(pid: int, sig: int) -> None:
    killpg = getattr(os, "killpg", None)
    if callable(killpg):
        killpg(pid, sig)


def run_worker(
    plan: WorkerPlan,
    deployment: WorkerDeployment | None = None,
    *,
    popen: Callable[..., Any] = subprocess.Popen,
) -> WorkerRun:
    """Run a reviewed worker image with bounded cleanup; never invokes a build command."""
    if deployment is None or PRODUCTION_WORKER_DEPLOYMENT is None:
        _fail("OFFLINE_VM_WORKER_DEPLOYMENT_UNAVAILABLE: no reviewed deployment pin is installed")
    if deployment != PRODUCTION_WORKER_DEPLOYMENT:
        _fail("offline VM deployment does not equal the compiled production pin")
    if os.name != "posix":
        _fail("offline VM worker execution is supported only by the reviewed Linux host contract")
    _assert_unprivileged_host()
    started = time.monotonic()
    process: Any | None = None
    timed_out = False
    returncode: int | None = None
    before: dict[str, str] = {}
    after: dict[str, str] = {}
    argv: tuple[str, ...] = ()
    fd = -1
    host_output_fsync_ok: bool | None = None
    host_evidence_fsync_ok: bool | None = None
    try:
        with _open_launch_files(plan, deployment) as (fd_paths, pass_fds, descriptors, opened_hashes):
            argv = build_qemu_argv(plan, deployment, fd_paths=fd_paths)
            before = dict(opened_hashes)
            before["snapshot_lock"] = _pinned_snapshot_lock_sha256()
            if before["snapshot_lock"] != plan.snapshot_lock_sha256:
                _fail("packaged snapshot lock changed after request construction")
            # Do not create any operation output until the operation root, all
            # input/output paths, inode identities, and lock binding passed.
            fd = _create_worker_log_fd(plan.log_path, plan.scratch_volume.path.parent)
            with os.fdopen(fd, "wb") as log_stream:
                fd = -1
                process = popen(
                    argv,
                    executable=fd_paths["qemu"],
                    cwd=os.path.abspath(os.sep),
                    env=_fixed_host_environment(plan.scratch_volume.path.parent),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=log_stream,
                    close_fds=True,
                    pass_fds=pass_fds,
                    shell=False,
                    start_new_session=True,
                )
                try:
                    returncode = process.wait(timeout=plan.timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    _terminate_process(process)
                    returncode = process.returncode
                if not timed_out and returncode == 0:
                    try:
                        _fsync_output_after_worker(plan.output_volume.path, descriptors["output"])
                    except OSError:
                        host_output_fsync_ok = False
                    else:
                        host_output_fsync_ok = True
                    try:
                        _fsync_evidence_after_worker(plan.evidence_volume.path, descriptors["evidence"], plan.evidence_volume.size)
                    except OSError:
                        host_evidence_fsync_ok = False
                    else:
                        host_evidence_fsync_ok = True
            after = {
                name: _hash_fd(descriptors[name])
                for name in opened_hashes
            }
    except BaseException:
        if process is not None and process.poll() is None:
            _terminate_process(process)
        raise
    finally:
        if fd >= 0:
            os.close(fd)
    after["snapshot_lock"] = _pinned_snapshot_lock_sha256()
    elapsed = time.monotonic() - started
    for label in before:
        if before[label] != after[label]:
            _fail(f"read-only {label} input changed during worker execution")
    candidate_valid = False
    if timed_out:
        outcome = "TIMEOUT"
    elif returncode != 0:
        outcome = "QEMU_FAILED"
    elif host_output_fsync_ok is not True:
        outcome = "OUTPUT_HOST_FSYNC_FAILED"
    elif host_evidence_fsync_ok is not True:
        outcome = "EVIDENCE_HOST_FSYNC_FAILED"
    else:
        try:
            header = read_result_header(plan.output_volume.path, make_request(plan))
        except OfflineVmContractError:
            outcome = "OUTPUT_MISSING_OR_INVALID"
        else:
            if header["status"] == "BUILD_BACKEND_UNAVAILABLE":
                outcome = "BUILD_BACKEND_UNAVAILABLE"
            else:
                validate_result_volume(plan.output_volume.path, make_request(plan))
                candidate_valid = True
                outcome = "CANDIDATE_STRUCTURALLY_VALIDATED"
    return WorkerRun(
        argv_sha256=_sha256_bytes(canonical_json({"argv": list(argv)})),
        returncode=returncode,
        timed_out=timed_out,
        elapsed_seconds=elapsed,
        readonly_hashes_before=before,
        readonly_hashes_after=after,
        outcome=outcome,
        candidate_iso_structurally_validated=candidate_valid,
    )


def _fsync_output_after_worker(path: Path, fd: int) -> None:
    """Flush the stopped guest output image and bind the held inode to its path."""
    try:
        path_info = path.lstat()
        opened = os.fstat(fd)
    except OSError as exc:
        raise OfflineVmContractError("stopped worker output volume cannot be re-opened") from exc
    identity = (opened.st_dev, opened.st_ino)
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or opened.st_size <= RESULT_HEADER_SIZE
        or not stat.S_ISREG(path_info.st_mode)
        or path_info.st_nlink != 1
        or (path_info.st_dev, path_info.st_ino) != identity
        or path_info.st_size != opened.st_size
    ):
        raise OfflineVmContractError("stopped worker output volume is not bound to its opened inode before host fsync")
    os.fsync(fd)
    try:
        after_path = path.lstat()
        after_open = os.fstat(fd)
    except OSError as exc:
        raise OfflineVmContractError("stopped worker output volume disappeared after host fsync") from exc
    if (
        (after_open.st_dev, after_open.st_ino) != identity
        or (after_path.st_dev, after_path.st_ino) != identity
        or after_open.st_size != opened.st_size
        or after_path.st_size != opened.st_size
        or after_open.st_nlink != 1
        or after_path.st_nlink != 1
    ):
        raise OfflineVmContractError("stopped worker output volume identity changed during host fsync")


def _fsync_evidence_after_worker(path: Path, fd: int, expected_capacity: int) -> None:
    """Flush and rebind the stopped evidence device before parsing its logical bundle."""
    try:
        path_info = path.lstat()
        opened = os.fstat(fd)
    except OSError as exc:
        raise OfflineVmContractError("stopped worker evidence volume cannot be re-opened") from exc
    identity = (opened.st_dev, opened.st_ino)
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or opened.st_size != expected_capacity
        or not stat.S_ISREG(path_info.st_mode)
        or path_info.st_nlink != 1
        or (path_info.st_dev, path_info.st_ino) != identity
        or path_info.st_size != expected_capacity
    ):
        raise OfflineVmContractError("stopped worker evidence volume is not bound to its opened capacity before host fsync")
    os.fsync(fd)
    try:
        after_path = path.lstat()
        after_open = os.fstat(fd)
    except OSError as exc:
        raise OfflineVmContractError("stopped worker evidence volume disappeared after host fsync") from exc
    if (
        (after_open.st_dev, after_open.st_ino) != identity
        or (after_path.st_dev, after_path.st_ino) != identity
        or after_open.st_size != expected_capacity
        or after_path.st_size != expected_capacity
        or after_open.st_nlink != 1
        or after_path.st_nlink != 1
    ):
        raise OfflineVmContractError("stopped worker evidence volume identity changed during host fsync")


def build_result_header(
    request: Mapping[str, object],
    *,
    status: str,
    iso_size: int = 0,
    iso_sha256: str | None = None,
    command_argv_sha256: str | None = None,
    evidence_size: int = 0,
    evidence_sha256: str | None = None,
) -> bytes:
    """Serialize the guest's fixed result header; candidate status is not build approval."""
    parsed = parse_request(canonical_json(dict(request)))
    allowed = {"BUILD_BACKEND_UNAVAILABLE", "CANDIDATE_ISO"}
    if status not in allowed:
        _fail("guest result status is not in the fixed worker protocol")
    if status == "CANDIDATE_ISO":
        if (
            isinstance(iso_size, bool)
            or not 0 < iso_size <= MAX_ISO_BYTES
            or not _is_sha256(iso_sha256)
            or not _is_sha256(command_argv_sha256)
            or isinstance(evidence_size, bool)
            or not 0 < evidence_size <= MAX_EVIDENCE_VOLUME_BYTES
            or not _is_sha256(evidence_sha256)
        ):
            _fail("candidate result has invalid ISO/evidence size, hash, or command digest")
    elif (
        iso_size != 0 or iso_sha256 is not None or command_argv_sha256 is not None
        or evidence_size != 0 or evidence_sha256 is not None
    ):
        _fail("unavailable worker result cannot claim ISO bytes")
    header = {
        "format": "librecore-offline-vm-result-v3",
        "operation_id": parsed["operation_id"],
        "request_sha256": _sha256_bytes(canonical_json(dict(request))),
        "snapshot_lock_sha256": parsed["snapshot"]["lock_sha256"],  # type: ignore[index]
        "profile_id": parsed["profile_id"],
        "worker_image_sha256": parsed["worker_image_sha256"],
        "profile_inventory_sha256": parsed["profile"]["sha256"],  # type: ignore[index]
        "source_sha256": parsed["source"]["sha256"],  # type: ignore[index]
        "package_image_sha256": parsed["packages"]["sha256"],  # type: ignore[index]
        "status": status,
        "iso_offset": RESULT_ISO_OFFSET,
        "iso_size": iso_size,
        "iso_sha256": iso_sha256,
        "command_argv_sha256": command_argv_sha256,
        "evidence_size": evidence_size,
        "evidence_sha256": evidence_sha256,
    }
    encoded = canonical_json(header)
    if len(encoded) > RESULT_RECEIPT_OFFSET - len(RESULT_MAGIC) - 4:
        _fail("worker result header exceeds its fixed slot")
    return RESULT_MAGIC + len(encoded).to_bytes(4, "big") + encoded + bytes(RESULT_HEADER_SIZE - len(RESULT_MAGIC) - 4 - len(encoded))


def build_service_success_receipt(
    request: Mapping[str, object],
    *,
    iso_size: int,
    iso_sha256: str,
    command_argv_sha256: str,
    evidence_size: int,
    evidence_sha256: str,
) -> bytes:
    """Serialize the durable completion receipt written before the candidate header."""
    parsed = parse_request(canonical_json(dict(request)))
    if (
        isinstance(iso_size, bool)
        or not 0 < iso_size <= MAX_ISO_BYTES
        or not _is_sha256(iso_sha256)
        or not _is_sha256(command_argv_sha256)
        or isinstance(evidence_size, bool)
        or not 0 < evidence_size <= MAX_EVIDENCE_VOLUME_BYTES
        or not _is_sha256(evidence_sha256)
    ):
        _fail("service success receipt has invalid ISO or command identity")
    payload = canonical_json({
        "format": "librecore-offline-vm-service-receipt-v2",
        "status": "CANDIDATE_ISO_PENDING_HOST_VALIDATION",
        "operation_id": parsed["operation_id"],
        "request_sha256": _sha256_bytes(canonical_json(dict(request))),
        "iso_size": iso_size,
        "iso_sha256": iso_sha256,
        "command_argv_sha256": command_argv_sha256,
        "evidence_size": evidence_size,
        "evidence_sha256": evidence_sha256,
    })
    if len(payload) > RESULT_RECEIPT_SIZE - len(RESULT_RECEIPT_MAGIC) - 4:
        _fail("service success receipt exceeds its fixed slot")
    return RESULT_RECEIPT_MAGIC + len(payload).to_bytes(4, "big") + payload + bytes(
        RESULT_RECEIPT_SIZE - len(RESULT_RECEIPT_MAGIC) - 4 - len(payload)
    )


def build_result_commit_marker(
    request: Mapping[str, object],
    *,
    header_prefix: bytes,
    service_receipt: bytes,
    evidence_size: int,
    evidence_sha256: str,
) -> bytes:
    """Bind the final header segment to the already-durable receipt and header prefix."""
    parsed = parse_request(canonical_json(dict(request)))
    if len(header_prefix) != RESULT_RECEIPT_OFFSET or len(service_receipt) != RESULT_RECEIPT_SIZE:
        _fail("candidate commit inputs differ from their fixed slots")
    if not service_receipt.startswith(RESULT_RECEIPT_MAGIC):
        _fail("candidate commit has no fixed service receipt")
    if isinstance(evidence_size, bool) or not 0 < evidence_size <= MAX_EVIDENCE_VOLUME_BYTES or not _is_sha256(evidence_sha256):
        _fail("candidate commit lacks an evidence-volume identity")
    payload = canonical_json({
        "format": "librecore-offline-vm-commit-v2",
        "operation_id": parsed["operation_id"],
        "request_sha256": _sha256_bytes(canonical_json(dict(request))),
        "header_prefix_sha256": _sha256_bytes(header_prefix),
        "service_receipt_sha256": _sha256_bytes(service_receipt),
        "evidence_size": evidence_size,
        "evidence_sha256": evidence_sha256,
    })
    if len(payload) > RESULT_COMMIT_SIZE - len(RESULT_COMMIT_MAGIC) - 4:
        _fail("candidate commit marker exceeds its fixed slot")
    return RESULT_COMMIT_MAGIC + len(payload).to_bytes(4, "big") + payload + bytes(
        RESULT_COMMIT_SIZE - len(RESULT_COMMIT_MAGIC) - 4 - len(payload)
    )


def build_result_terminal_marker(
    request: Mapping[str, object],
    *,
    header_prefix: bytes,
    service_receipt: bytes,
    commit_marker: bytes,
    evidence_size: int,
    evidence_sha256: str,
) -> bytes:
    """Bind a terminal success marker to bytes whose commit fsync returned."""
    parsed = parse_request(canonical_json(dict(request)))
    if (
        len(header_prefix) != RESULT_RECEIPT_OFFSET
        or len(service_receipt) != RESULT_RECEIPT_SIZE
        or len(commit_marker) != RESULT_COMMIT_SIZE
        or not service_receipt.startswith(RESULT_RECEIPT_MAGIC)
        or not commit_marker.startswith(RESULT_COMMIT_MAGIC)
        or isinstance(evidence_size, bool)
        or not 0 < evidence_size <= MAX_EVIDENCE_VOLUME_BYTES
        or not _is_sha256(evidence_sha256)
    ):
        _fail("candidate terminal marker inputs differ from their fixed slots")
    payload = canonical_json({
        "format": "librecore-offline-vm-terminal-v2",
        "status": "COMMIT_FSYNC_RETURNED",
        "operation_id": parsed["operation_id"],
        "request_sha256": _sha256_bytes(canonical_json(dict(request))),
        "header_prefix_sha256": _sha256_bytes(header_prefix),
        "service_receipt_sha256": _sha256_bytes(service_receipt),
        "commit_sha256": _sha256_bytes(commit_marker),
        "evidence_size": evidence_size,
        "evidence_sha256": evidence_sha256,
    })
    if len(payload) > RESULT_TERMINAL_SIZE - len(RESULT_TERMINAL_MAGIC) - 4:
        _fail("candidate terminal marker exceeds its fixed slot")
    return RESULT_TERMINAL_MAGIC + len(payload).to_bytes(4, "big") + payload + bytes(
        RESULT_TERMINAL_SIZE - len(RESULT_TERMINAL_MAGIC) - 4 - len(payload)
    )


def _validate_terminal_marker(
    terminal_slot: bytes,
    *,
    request: Mapping[str, object],
    header_prefix: bytes,
    service_receipt: bytes,
    commit_marker: bytes,
    evidence_size: int,
    evidence_sha256: str,
) -> None:
    if len(terminal_slot) != RESULT_TERMINAL_SIZE or not terminal_slot.startswith(RESULT_TERMINAL_MAGIC):
        _fail("candidate result has no post-commit durability marker")
    length_start = len(RESULT_TERMINAL_MAGIC)
    length = int.from_bytes(terminal_slot[length_start : length_start + 4], "big")
    if not 0 < length <= RESULT_TERMINAL_SIZE - len(RESULT_TERMINAL_MAGIC) - 4:
        _fail("candidate terminal marker length is invalid")
    payload = terminal_slot[length_start + 4 : length_start + 4 + length]
    if any(terminal_slot[length_start + 4 + length :]):
        _fail("candidate terminal marker padding is not zero")
    try:
        terminal = json.loads(payload.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfflineVmContractError("candidate terminal marker is malformed") from exc
    parsed = parse_request(canonical_json(dict(request)))
    expected = {
        "format": "librecore-offline-vm-terminal-v2",
        "status": "COMMIT_FSYNC_RETURNED",
        "operation_id": parsed["operation_id"],
        "request_sha256": _sha256_bytes(canonical_json(dict(request))),
        "header_prefix_sha256": _sha256_bytes(header_prefix),
        "service_receipt_sha256": _sha256_bytes(service_receipt),
        "commit_sha256": _sha256_bytes(commit_marker),
        "evidence_size": evidence_size,
        "evidence_sha256": evidence_sha256,
    }
    if not isinstance(terminal, dict) or canonical_json(terminal) != payload or terminal != expected:
        _fail("candidate terminal marker does not bind the post-fsync output bytes")


def validate_result_volume(path: Path, request: Mapping[str, object]) -> dict[str, object]:
    """Reopen a stopped guest output volume and independently inspect its result/ISO bytes."""
    parsed_request = parse_request(canonical_json(dict(request)))
    if _path_has_symlink_component(path) or not path.is_absolute():
        _fail("result volume path is linked or not absolute")
    try:
        info = path.lstat()
    except OSError as exc:
        raise OfflineVmContractError("result volume is missing") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != parsed_request["layout"]["output_capacity"]:  # type: ignore[index]
        _fail("result volume is not the exact-size regular output device")
    if os.name == "posix":
        _validate_operation_directory(path.parent, str(parsed_request["operation_id"]))
        verify_writable_volume(VolumeIdentity(path, info.st_size), "result", path.parent)
    header = read_result_header(path, request)
    if header.get("status") != "CANDIDATE_ISO":
        _fail("guest worker did not produce a candidate ISO")
    size = header.get("iso_size")
    digest = header.get("iso_sha256")
    if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_ISO_BYTES or not _is_sha256(digest):
        _fail("result ISO size or digest is invalid")
    if RESULT_ISO_OFFSET + size > info.st_size:
        _fail("result ISO extends beyond the dedicated output volume")
    if size < 18 * 2048:
        _fail("candidate does not contain an ISO-9660 primary volume descriptor")
    digest_state = hashlib.sha256()
    with path.open("rb") as stream:
        stream.seek(RESULT_ISO_OFFSET)
        remaining = size
        while remaining:
            chunk = stream.read(min(RESULT_HASH_CHUNK_BYTES, remaining))
            if not chunk:
                _fail("result ISO region is shorter than the guest-reported size")
            digest_state.update(chunk)
            remaining -= len(chunk)
        if digest_state.hexdigest() != digest:
            _fail("result ISO bytes differ from the guest-reported digest")
        stream.seek(RESULT_ISO_OFFSET + 32769)
        primary_descriptor_signature = stream.read(5)
        stream.seek(RESULT_ISO_OFFSET + 17 * 2048)
        boot_descriptor = stream.read(2048)
    if primary_descriptor_signature != b"CD001":
        _fail("candidate does not contain an ISO-9660 primary volume descriptor")
    if len(boot_descriptor) != 2048 or boot_descriptor[1:6] != b"CD001" or b"EL TORITO SPECIFICATION" not in boot_descriptor:
        _fail("candidate does not contain an El Torito boot record")
    return header


def read_result_header(path: Path, request: Mapping[str, object]) -> dict[str, object]:
    """Read and bind the fixed result header, including explicit unavailable outcomes."""
    parsed_request = parse_request(canonical_json(dict(request)))
    if not path.is_absolute() or _path_has_symlink_component(path):
        _fail("result volume path is linked or not absolute")
    try:
        info = path.lstat()
    except OSError as exc:
        raise OfflineVmContractError("result volume is missing") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != parsed_request["layout"]["output_capacity"]:  # type: ignore[index]
        _fail("result volume is not the exact-size regular output device")
    if os.name == "posix":
        _validate_operation_directory(path.parent, str(parsed_request["operation_id"]))
        verify_writable_volume(VolumeIdentity(path, info.st_size), "result", path.parent)
    with path.open("rb") as stream:
        if stream.read(len(RESULT_MAGIC)) != RESULT_MAGIC:
            _fail("result volume has no worker protocol header")
        raw_size = stream.read(4)
        if len(raw_size) != 4:
            _fail("result header length is truncated")
        size = int.from_bytes(raw_size, "big")
        if not 0 < size <= RESULT_RECEIPT_OFFSET - len(RESULT_MAGIC) - 4:
            _fail("result header length is outside its fixed slot")
        raw = stream.read(size)
        if len(raw) != size:
            _fail("result header payload is truncated")
        try:
            header = json.loads(raw.decode("ascii"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OfflineVmContractError("result header is not valid ASCII JSON") from exc
        if not isinstance(header, dict) or canonical_json(header) != raw:
            _fail("result header is not canonical JSON")
        padding_size = RESULT_RECEIPT_OFFSET - len(RESULT_MAGIC) - 4 - size
        padding = stream.read(padding_size)
        if len(padding) != padding_size or any(padding):
            _fail("result header padding is not zero")
        header_prefix = RESULT_MAGIC + raw_size + raw + padding
        receipt_slot = stream.read(RESULT_RECEIPT_SIZE)
        if len(receipt_slot) != RESULT_RECEIPT_SIZE:
            _fail("service receipt slot is truncated")
        commit_slot = stream.read(RESULT_COMMIT_SIZE)
        if len(commit_slot) != RESULT_COMMIT_SIZE:
            _fail("candidate commit slot is truncated")
        terminal_slot = stream.read(RESULT_TERMINAL_SIZE)
        if len(terminal_slot) != RESULT_TERMINAL_SIZE:
            _fail("candidate terminal slot is truncated")
    expected = {
        "format": "librecore-offline-vm-result-v3",
        "operation_id": parsed_request["operation_id"],
        "request_sha256": _sha256_bytes(canonical_json(dict(request))),
        "snapshot_lock_sha256": parsed_request["snapshot"]["lock_sha256"],  # type: ignore[index]
        "profile_id": parsed_request["profile_id"],
        "worker_image_sha256": parsed_request["worker_image_sha256"],
        "profile_inventory_sha256": parsed_request["profile"]["sha256"],  # type: ignore[index]
        "source_sha256": parsed_request["source"]["sha256"],  # type: ignore[index]
        "package_image_sha256": parsed_request["packages"]["sha256"],  # type: ignore[index]
        "iso_offset": RESULT_ISO_OFFSET,
    }
    expected_header_keys = set(expected) | {"status", "iso_size", "iso_sha256", "command_argv_sha256", "evidence_size", "evidence_sha256"}
    if set(header) != expected_header_keys or any(header.get(key) != value for key, value in expected.items()):
        _fail("result header is not bound to this operation, snapshot, source, and profile")
    if header.get("status") == "BUILD_BACKEND_UNAVAILABLE":
        if (
            header.get("iso_size") != 0
            or header.get("iso_sha256") is not None
            or header.get("command_argv_sha256") is not None
            or header.get("evidence_size") != 0
            or header.get("evidence_sha256") is not None
        ):
            _fail("unavailable result cannot claim output bytes")
        if any(receipt_slot):
            _fail("unavailable result contains a service success receipt")
        if any(commit_slot):
            _fail("unavailable result contains a candidate commit marker")
        if any(terminal_slot):
            _fail("unavailable result contains a candidate terminal marker")
    elif header.get("status") == "CANDIDATE_ISO":
        if (
            isinstance(header.get("iso_size"), bool)
            or not isinstance(header.get("iso_size"), int)
            or not 0 < header["iso_size"] <= MAX_ISO_BYTES
            or not _is_sha256(header.get("iso_sha256"))
            or not _is_sha256(header.get("command_argv_sha256"))
            or isinstance(header.get("evidence_size"), bool)
            or not isinstance(header.get("evidence_size"), int)
            or not 0 < header["evidence_size"] <= MAX_EVIDENCE_VOLUME_BYTES
            or not _is_sha256(header.get("evidence_sha256"))
        ):
            _fail("candidate result has invalid ISO size or hash")
        if not receipt_slot.startswith(RESULT_RECEIPT_MAGIC):
            _fail("candidate result has no durable service success receipt")
        length_start = len(RESULT_RECEIPT_MAGIC)
        receipt_length = int.from_bytes(receipt_slot[length_start : length_start + 4], "big")
        if not 0 < receipt_length <= RESULT_RECEIPT_SIZE - len(RESULT_RECEIPT_MAGIC) - 4:
            _fail("service success receipt length is invalid")
        receipt_bytes = receipt_slot[length_start + 4 : length_start + 4 + receipt_length]
        if any(receipt_slot[length_start + 4 + receipt_length :]):
            _fail("service success receipt padding is not zero")
        try:
            receipt = json.loads(receipt_bytes.decode("ascii"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OfflineVmContractError("service success receipt is malformed") from exc
        expected_receipt = {
            "format": "librecore-offline-vm-service-receipt-v2",
            "status": "CANDIDATE_ISO_PENDING_HOST_VALIDATION",
            "operation_id": parsed_request["operation_id"],
            "request_sha256": _sha256_bytes(canonical_json(dict(request))),
            "iso_size": header["iso_size"],
            "iso_sha256": header["iso_sha256"],
            "command_argv_sha256": header["command_argv_sha256"],
            "evidence_size": header["evidence_size"],
            "evidence_sha256": header["evidence_sha256"],
        }
        if not isinstance(receipt, dict) or canonical_json(receipt) != receipt_bytes or receipt != expected_receipt:
            _fail("service success receipt does not match the candidate header and operation")
        if not commit_slot.startswith(RESULT_COMMIT_MAGIC):
            _fail("candidate result has no final commit marker")
        commit_length_start = len(RESULT_COMMIT_MAGIC)
        commit_length = int.from_bytes(commit_slot[commit_length_start : commit_length_start + 4], "big")
        if not 0 < commit_length <= RESULT_COMMIT_SIZE - len(RESULT_COMMIT_MAGIC) - 4:
            _fail("candidate commit marker length is invalid")
        commit_bytes = commit_slot[commit_length_start + 4 : commit_length_start + 4 + commit_length]
        if any(commit_slot[commit_length_start + 4 + commit_length :]):
            _fail("candidate commit marker padding is not zero")
        try:
            commit = json.loads(commit_bytes.decode("ascii"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OfflineVmContractError("candidate commit marker is malformed") from exc
        expected_commit = {
            "format": "librecore-offline-vm-commit-v2",
            "operation_id": parsed_request["operation_id"],
            "request_sha256": _sha256_bytes(canonical_json(dict(request))),
            "header_prefix_sha256": _sha256_bytes(header_prefix),
            "service_receipt_sha256": _sha256_bytes(receipt_slot),
            "evidence_size": header["evidence_size"],
            "evidence_sha256": header["evidence_sha256"],
        }
        if not isinstance(commit, dict) or canonical_json(commit) != commit_bytes or commit != expected_commit:
            _fail("candidate commit marker does not bind the final header and receipt")
        _validate_terminal_marker(
            terminal_slot,
            request=request,
            header_prefix=header_prefix,
            service_receipt=receipt_slot,
            commit_marker=commit_slot,
            evidence_size=header["evidence_size"],
            evidence_sha256=str(header["evidence_sha256"]),
        )
    else:
        _fail("worker result status is not recognized")
    return header
