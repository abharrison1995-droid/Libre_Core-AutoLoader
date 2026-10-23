"""Fail-closed fixed build orchestration for the sealed offline VM prototype.

This module is deliberately separate from :mod:`usb_builder.build_usb` and is
not called by the production CLI.  It defines the only guest build intent,
preflight receipt, fixed mkarchiso invocation, and host-side acceptance
adapter.  The default guest worker remains unavailable until a reviewed
worker image and native no-network execution chain exist.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, NoReturn, Protocol, Sequence

from usb_builder import build_usb
from usb_builder import offline_vm_evidence as evidence_bundle
from usb_builder.offline_vm_worker import (
    LOCKED_PACKAGES,
    MAX_ISO_BYTES,
    OfflineVmContractError,
    RESULT_ISO_OFFSET,
    _path_has_symlink_component,
    canonical_json,
    encode_request_volume,
    parse_request,
    validate_result_volume,
)


BUILD_MANIFEST_FORMAT = "librecore-offline-vm-build-manifest-v1"
BUILD_RECEIPT_FORMAT = "librecore-offline-vm-build-receipt-v1"
BUILD_RESULT_FORMAT = "librecore-offline-vm-build-result-v1"
BUILD_RESULT_READY_FORMAT = "librecore-offline-vm-build-ready-v1"
BUILD_MANIFEST_RELATIVE = "worker/build-manifest.json"
PACSTRAP_SOURCE_RELATIVE = "worker/pacstrap"
PACSTRAP_PATCH_RELATIVE = "usb_builder/patches/arch-install-scripts-31-2-pacstrap-status.patch"
PACSTRAP_PATH = "/usr/bin/pacstrap"
MKARCHISO_PATH = "/usr/bin/mkarchiso"
PACSTRAP_BINARY_SHA256 = "3964ed5b509f791e9f98cda494c4af5febe6daf0174df75b9bb10a7af3f2c04c"
BUILD_INVOCATION_PATH = "/run/librecore-scratch/build-invocation.json"
BUILD_REQUEST_PATH = "/run/librecore-scratch/build-request.json"
BUILD_RESULT_PATH = "/run/librecore-scratch/build-service-result.json"
BUILD_RESULT_READY_PATH = "/run/librecore-scratch/build-service-result-ready.json"
BUILD_SERVICE_STARTED_PATH = "/run/librecore-scratch/build-service-started.json"
BUILD_PREFLIGHT_STARTED_PATH = "/run/librecore-worker/preflight-started.json"
PACSTRAP_EXIT_CODES_PATH = "/run/librecore-scratch/pacstrap-exit-codes"
PACSTRAP_WRAPPER_BYTES = (
    b"#!/usr/bin/bash\n"
    b"set +e\n"
    b"/usr/bin/bash /run/librecore-scratch/bin/pacstrap.real \"$@\"\n"
    b"_lc_status=$?\n"
    b"/usr/bin/printf '%s\\n' \"$_lc_status\" >> /run/librecore-scratch/pacstrap-exit-codes || exit 125\n"
    b"exit \"$_lc_status\"\n"
)
WORKER_PACMAN_CONF = "/run/librecore-scratch/pacman.conf"
WORKER_ROOT = "/run/librecore-scratch"
WORKER_PROFILE = "/mnt/librecore-source/usb_builder/archiso_profile"
WORKER_WORK = "/run/librecore-scratch/archiso-work"
WORKER_OUTPUT = "/run/librecore-scratch/image-output"
WORKER_PACSTRAP_BIN = "/run/librecore-scratch/bin"
BUILD_SERVICE_NAME = "librecore-offline-build.service"
BUILD_PREFLIGHT_SERVICE_NAME = "librecore-offline-preflight.service"
SYSTEMD_UNIT_PATH = "/usr/lib/systemd/system/librecore-offline-build.service"
SYSTEMD_PATH_UNIT_PATH = "/usr/lib/systemd/system/librecore-offline-build.path"
SYSTEMD_PREFLIGHT_UNIT_PATH = "/usr/lib/systemd/system/librecore-offline-preflight.service"
SYSTEMD_PREFLIGHT_PATH_UNIT_PATH = "/usr/lib/systemd/system/librecore-offline-preflight.path"
SYSTEMD_UNIT_SEARCH_PATHS = (
    "etc/systemd/system.control",
    "run/systemd/system.control",
    "run/systemd/transient",
    "run/systemd/generator.early",
    "etc/systemd/system",
    "etc/systemd/system.attached",
    "run/systemd/system",
    "run/systemd/system.attached",
    "run/systemd/generator",
    "usr/local/lib/systemd/system",
    "usr/lib/systemd/system",
    "run/systemd/generator.late",
    "usr/local/lib/systemd/system.attached",
    "usr/lib/systemd/system.attached",
)
CAPABILITY_BOUNDING_MASK = "00000000000000c0"  # CAP_SETUID | CAP_SETGID for pinned map helpers only.
BUILD_TIMEOUT_SECONDS = 4 * 60 * 60
SERVICE_START_GRACE_SECONDS = 30
MAX_ATTACHED_IMAGE_BYTES = 64 * 1024 * 1024 * 1024
MAX_REPOSITORY_DATABASE_BYTES = 512 * 1024 * 1024
MAX_REPOSITORY_DATABASE_MEMBERS = 250_000
MAX_REPOSITORY_PACKAGE_DESC_BYTES = 1024 * 1024
PACMAN_DATABASE_RELATIVE = {
    "core": "repos/core/os/x86_64/core.db",
    "extra": "repos/extra/os/x86_64/extra.db",
}
PACMAN_REPOSITORY_ROOTS = {
    "core": "repos/core/os/x86_64",
    "extra": "repos/extra/os/x86_64",
}
PACMAN_CONFIG_BYTES = (
    b"[options]\n"
    b"Architecture = x86_64\n"
    b"SigLevel = Required DatabaseOptional\n"
    b"LocalFileSigLevel = Required\n"
    b"\n[core]\n"
    b"Server = file:///mnt/librecore-packages/repos/core/os/$arch\n"
    b"\n[extra]\n"
    b"Server = file:///mnt/librecore-packages/repos/extra/os/$arch\n"
)
BUILD_SERVICE_UNIT_BYTES = (
    b"[Unit]\n"
    b"Description=LibreCore sealed offline V1 image builder\n"
    b"After=local-fs.target\n"
    b"RequiresMountsFor=/mnt/librecore-source /mnt/librecore-packages /run/librecore-scratch\n"
    b"ConditionPathExists=/run/librecore-scratch/build-invocation.json\n"
    b"ConditionPathExists=!/run/librecore-scratch/build-service-started.json\n"
    b"ConditionPathExists=!/run/librecore-scratch/build-service-result.json\n"
    b"ConditionPathExists=!/run/librecore-scratch/build-service-result-ready.json\n"
    b"\n[Service]\n"
    b"Type=oneshot\n"
    b"User=fwbuild\n"
    b"Group=fwbuild\n"
    b"WorkingDirectory=/\n"
    b"Environment=PATH=/usr/bin:/bin\n"
    b"Environment=HOME=/run/librecore-worker\n"
    b"Environment=LANG=C\n"
    b"Environment=LC_ALL=C\n"
    b"Environment=PYTHONDONTWRITEBYTECODE=1\n"
    b"Environment=PYTHONOPTIMIZE=0\n"
    b"ExecStart=/usr/bin/python -B -m usb_builder.offline_vm_build_service\n"
    b"NoNewPrivileges=no\n"
    b"CapabilityBoundingSet=CAP_SETUID CAP_SETGID\n"
    b"AmbientCapabilities=\n"
    b"PrivateNetwork=yes\n"
    b"RestrictAddressFamilies=AF_UNIX\n"
    b"ProtectSystem=strict\n"
    b"ProtectHome=yes\n"
    b"ReadOnlyPaths=/mnt/librecore-source /mnt/librecore-packages\n"
    b"ReadWritePaths=/run/librecore-scratch\n"
    b"KillMode=control-group\n"
    b"RuntimeMaxSec=4h\n"
    b"TimeoutStartSec=4h\n"
    b"TimeoutStopSec=5s\n"
)
BUILD_PATH_UNIT_BYTES = (
    b"[Unit]\n"
    b"Description=One-shot trigger for LibreCore sealed offline V1 image builder\n"
    b"\n[Path]\n"
    b"PathExists=/run/librecore-scratch/build-invocation.json\n"
    b"Unit=librecore-offline-build.service\n"
    b"\n[Install]\n"
    b"WantedBy=multi-user.target\n"
)
BUILD_PREFLIGHT_UNIT_BYTES = (
    b"[Unit]\n"
    b"Description=LibreCore sealed offline V1 NNP=1 build preflight\n"
    b"After=local-fs.target\n"
    b"RequiresMountsFor=/mnt/librecore-source /mnt/librecore-packages /run/librecore-scratch\n"
    b"ConditionPathExists=/dev/disk/by-id/virtio-LC-REQUEST-RO\n"
    b"ConditionPathExists=!/run/librecore-worker/preflight-started.json\n"
    b"\n[Service]\n"
    b"Type=oneshot\n"
    b"User=fwbuild\n"
    b"Group=fwbuild\n"
    b"WorkingDirectory=/\n"
    b"Environment=PATH=/usr/bin:/bin\n"
    b"Environment=HOME=/run/librecore-worker\n"
    b"Environment=LANG=C\n"
    b"Environment=LC_ALL=C\n"
    b"Environment=PYTHONDONTWRITEBYTECODE=1\n"
    b"Environment=PYTHONOPTIMIZE=0\n"
    b"ExecStart=/usr/bin/python -B -m usb_builder.offline_vm_build_preflight\n"
    b"NoNewPrivileges=yes\n"
    b"CapabilityBoundingSet=CAP_SETUID CAP_SETGID\n"
    b"AmbientCapabilities=\n"
    b"PrivateNetwork=yes\n"
    b"RestrictAddressFamilies=AF_UNIX\n"
    b"ProtectSystem=strict\n"
    b"ProtectHome=yes\n"
    b"ReadOnlyPaths=/mnt/librecore-source /mnt/librecore-packages /dev/disk/by-id/virtio-LC-REQUEST-RO\n"
    b"ReadWritePaths=/run/librecore-scratch\n"
    b"RuntimeDirectory=librecore-scratch librecore-worker\n"
    b"RuntimeDirectoryMode=0700\n"
    b"KillMode=control-group\n"
    b"RuntimeMaxSec=4h30min\n"
    b"TimeoutStartSec=4h30min\n"
    b"TimeoutStopSec=5s\n"
    b"SuccessAction=poweroff\n"
    b"FailureAction=poweroff\n"
    b"\n[Install]\n"
    b"WantedBy=multi-user.target\n"
)
BUILD_PREFLIGHT_PATH_UNIT_BYTES = (
    b"[Unit]\n"
    b"Description=One-shot trigger for LibreCore sealed offline V1 preflight\n"
    b"\n[Path]\n"
    b"PathExists=/dev/disk/by-id/virtio-LC-REQUEST-RO\n"
    b"Unit=librecore-offline-preflight.service\n"
    b"\n[Install]\n"
    b"WantedBy=multi-user.target\n"
)


class OfflineVmBuildError(OfflineVmContractError):
    """A guest build intent, execution receipt, or host candidate failed closed."""


def _reject(message: str) -> NoReturn:
    raise OfflineVmBuildError(message)


def _linux_runtime_available() -> bool:
    return sys.platform.startswith("linux")


def _require_linux_runtime() -> None:
    if not _linux_runtime_available():
        _reject("sealed offline VM operations are supported only inside Linux")


def _linux_effective_uid() -> int:
    _require_linux_runtime()
    getter = getattr(os, "geteuid", None)
    if not callable(getter):
        _reject("Linux effective UID observation is unavailable")
    value = getter()
    if isinstance(value, bool) or not isinstance(value, int):
        _reject("Linux effective UID observation is malformed")
    return value


def _linux_stat_rdev(info: os.stat_result) -> int:
    _require_linux_runtime()
    value = getattr(info, "st_rdev", None)
    if isinstance(value, bool) or not isinstance(value, int):
        _reject("Linux block-device identity is unavailable")
    return value


def _linux_ioctl(fd: int, request: int, argument: bytes) -> bytes:
    _require_linux_runtime()
    import fcntl

    ioctl = getattr(fcntl, "ioctl", None)
    if not callable(ioctl):
        _reject("Linux block-device ioctl is unavailable")
    result = ioctl(fd, request, argument)
    if not isinstance(result, bytes):
        _reject("Linux block-device ioctl returned malformed data")
    return result


def _linux_pread(fd: int, length: int, offset: int) -> bytes:
    _require_linux_runtime()
    pread = getattr(os, "pread", None)
    if not callable(pread):
        _reject("Linux positional block-device read is unavailable")
    result = pread(fd, length, offset)
    if not isinstance(result, bytes):
        _reject("Linux positional block-device read returned malformed data")
    return result


def _linux_kill_process_group(pid: int, signal_name: str) -> None:
    _require_linux_runtime()
    killpg = getattr(os, "killpg", None)
    signal_value = getattr(signal, signal_name, None)
    if not callable(killpg) or isinstance(signal_value, bool) or not isinstance(signal_value, int):
        _reject("Linux process-group cleanup is unavailable")
    killpg(pid, signal_value)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_file(root: Path, relative: str, label: str) -> Path:
    if not isinstance(relative, str) or "\\" in relative or "\x00" in relative:
        _reject(f"{label} path is not a canonical relative path")
    rel = PurePosixPath(relative)
    if rel.is_absolute() or not rel.parts or any(part in ("", ".", "..") for part in rel.parts):
        _reject(f"{label} path traverses its read-only image")
    candidate = root.joinpath(*rel.parts)
    cursor = root
    for component in rel.parts:
        cursor = cursor / component
        try:
            info = cursor.lstat()
        except OSError as exc:
            raise OfflineVmBuildError(f"{label} path is missing") from exc
        if stat.S_ISLNK(info.st_mode):
            _reject(f"{label} path contains a symlink")
        is_junction = getattr(cursor, "is_junction", None)
        if callable(is_junction) and is_junction():
            _reject(f"{label} path contains a junction")
    try:
        info = candidate.lstat()
    except OSError as exc:
        raise OfflineVmBuildError(f"{label} file is missing") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        _reject(f"{label} must be a single-link regular file")
    return candidate


def _verify_manifest_tree(root: Path, expected: set[str]) -> None:
    observed: set[str] = {BUILD_MANIFEST_RELATIVE}
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or (path.is_dir() and not stat.S_ISDIR(info.st_mode)):
            _reject("package image contains a linked or non-directory path")
        if path.is_file():
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                _reject("package image contains a non-regular or linked file")
            observed.add(relative)
    if observed != expected:
        _reject("package image contains missing or unmanifested files")


def _snapshot_lock(source_root: Path, request: Mapping[str, object]) -> dict[str, Any]:
    lock_path = _safe_file(source_root, "usb_builder/snapshot.lock.json", "snapshot lock")
    raw = lock_path.read_bytes()
    snapshot = request.get("snapshot")
    if not isinstance(snapshot, dict) or _sha256_bytes(raw) != snapshot.get("lock_sha256"):
        _reject("source snapshot lock bytes differ from the request binding")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfflineVmBuildError("source snapshot lock is malformed") from exc
    if not isinstance(value, dict) or value.get("snapshot_date") != "2026-09-30":
        _reject("source snapshot lock is not the exact dated package snapshot")
    rootless = value.get("rootless_builder") if isinstance(value, dict) else None
    if not isinstance(value, dict) or not isinstance(rootless, dict):
        _reject("source snapshot lock is malformed")
    versions = {
        "archiso": value.get("archiso_version"),
        "arch-install-scripts": rootless.get("arch_install_scripts_version"),
        "pacman": rootless.get("pacman_version"),
        "util-linux": rootless.get("util_linux_version"),
        "bash": rootless.get("bash_version"),
        "shadow": rootless.get("map_helper_package_version"),
        "libcap": rootless.get("libcap_version"),
    }
    if versions != LOCKED_PACKAGES:
        _reject("source snapshot lock package versions differ from the fixed worker contract")
    if snapshot.get("packages") != LOCKED_PACKAGES:
        _reject("request package versions differ from the fixed worker contract")
    return value


def _read_build_manifest(package_root: Path) -> tuple[dict[str, Any], bytes]:
    path = _safe_file(package_root, BUILD_MANIFEST_RELATIVE, "build manifest")
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfflineVmBuildError("package build manifest is malformed") from exc
    if not isinstance(value, dict) or canonical_json(value) != raw:
        _reject("package build manifest is not canonical JSON")
    expected_fields = {
        "format", "operation_id", "request_sha256", "source_sha256", "package_image_sha256",
        "snapshot_lock_sha256", "profile_inventory_sha256", "package_list_sha256",
        "repository_databases", "packages", "pacstrap_overlay",
    }
    if set(value) != expected_fields or value.get("format") != BUILD_MANIFEST_FORMAT:
        _reject("package build manifest has unknown or missing fields")
    return value, raw


def _profile_package_names(source_root: Path, request: Mapping[str, object]) -> tuple[str, ...]:
    profile = request.get("profile")
    if not isinstance(profile, dict) or not isinstance(profile.get("files"), list):
        _reject("request profile inventory is malformed")
    entries = [item for item in profile["files"] if isinstance(item, dict) and item.get("path") == "usb_builder/archiso_profile/packages.x86_64"]
    if len(entries) != 1:
        _reject("request must bind the exact profile packages.x86_64 file")
    package_file = _safe_file(source_root, "usb_builder/archiso_profile/packages.x86_64", "profile package list")
    raw = package_file.read_bytes()
    if len(raw) != entries[0].get("size") or _sha256_bytes(raw) != entries[0].get("sha256"):
        _reject("profile package list differs from the request inventory")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OfflineVmBuildError("profile package list is not UTF-8") from exc
    names = tuple(
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if not names or len(names) != len(set(names)) or any(re.fullmatch(r"[a-zA-Z0-9@._+-]+", item) is None for item in names):
        _reject("profile package list is empty, duplicated, or malformed")
    return names


@dataclass(frozen=True)
class GuestSecurityState:
    account: str
    uid: int
    gid: int
    supplementary_gids: tuple[int, ...]
    cap_effective: str
    cap_permitted: str
    cap_ambient: str
    no_new_privs: bool
    network_namespace: str
    interfaces: tuple[str, ...] = ("lo",)
    ipv4_routes: tuple[str, ...] = ()
    ipv6_routes: tuple[str, ...] = ()
    cap_bounding: str = CAPABILITY_BOUNDING_MASK


def validate_preflight_security(state: GuestSecurityState, isolation: Any) -> None:
    if state.account != "fwbuild" or state.uid <= 0 or state.gid <= 0:
        _reject("guest preflight must run as the dedicated non-root fwbuild account")
    if state.supplementary_gids != (state.gid,):
        _reject("guest preflight carries an unexpected supplementary group")
    zero = {"0", "00000000", "0000000000000000"}
    if any(value not in zero for value in (state.cap_effective, state.cap_permitted, state.cap_ambient)):
        _reject("guest preflight must have empty effective/permitted/ambient capabilities")
    if state.cap_bounding != CAPABILITY_BOUNDING_MASK:
        _reject("guest preflight capability bounding set differs from the exact map-helper allowance")
    if not state.no_new_privs:
        _reject("guest build preflight must complete while NoNewPrivileges is enabled")
    if not state.network_namespace:
        _reject("guest network namespace identity is missing")
    if set(isolation.interfaces) != {"lo"} or isolation.ipv4_routes or isolation.ipv6_routes:
        _reject("guest build preflight does not have a route-free loopback-only network")


def validate_build_service_security(state: GuestSecurityState) -> None:
    """Require the reviewed NNP=0 boundary inside the sealed guest only."""
    if state.account != "fwbuild" or state.uid <= 0 or state.gid <= 0 or state.supplementary_gids != (state.gid,):
        _reject("fixed builder service did not run as the dedicated private fwbuild account")
    zero = {"0", "00000000", "0000000000000000"}
    if any(value not in zero for value in (state.cap_effective, state.cap_permitted, state.cap_ambient)):
        _reject("fixed builder service has unexpected effective/permitted/ambient capabilities")
    if state.cap_bounding != CAPABILITY_BOUNDING_MASK:
        _reject("fixed builder service capability bounding set differs from the exact map-helper allowance")
    if state.no_new_privs:
        _reject("fixed builder service must run inside the explicitly approved guest NNP=0 boundary")
    if not state.network_namespace or set(state.interfaces) != {"lo"} or state.ipv4_routes or state.ipv6_routes:
        _reject("fixed builder service is not confined to a route-free loopback-only namespace")


class GuestBuildOperations(Protocol):
    """Narrow, injectable read/verify/stage/service boundary for the guest."""

    def hash_attached_device(self, role: str, expected_size: int) -> str: ...

    def verify_detached_signature(self, signature: Path, artifact: Path) -> bool: ...

    def package_identity(self, archive: Path) -> tuple[str, str]: ...

    def repository_database_versions(self, database: Path) -> Mapping[str, str]: ...

    def stage_pacstrap(self, source: Path, destination: Path, expected_sha256: str) -> str: ...

    def claim_one_shot(self, operation_root: Path, request_sha256: str) -> None: ...

    def run_fixed_builder(self, invocation: "BuildInvocation") -> "BuildProcessResult": ...

    def list_iso_outputs(self, output_dir: Path) -> Sequence[Path]: ...

    def cleanup_overlay(self, overlay: Path) -> None: ...


@dataclass(frozen=True)
class BuildInvocation:
    operation_id: str
    service_name: str
    argv: tuple[str, ...]
    env: Mapping[str, str]
    cwd: str
    timeout_seconds: int
    request_sha256: str
    pacstrap_overlay: str
    pacstrap_overlay_sha256: str
    pacstrap_binary_sha256: str


@dataclass(frozen=True)
class BuildProcessResult:
    service_name: str
    argv_sha256: str
    returncode: int | None
    timed_out: bool
    elapsed_seconds: float
    no_new_privs_before: bool
    no_new_privs_during: bool
    network_namespace_before: str
    network_namespace_during: str
    pacstrap_exit_codes: tuple[int, ...]
    account: str
    uid: int
    gid: int
    supplementary_gids: tuple[int, ...]
    cap_effective: str
    cap_permitted: str
    cap_ambient: str
    interfaces: tuple[str, ...]
    ipv4_routes: tuple[str, ...]
    ipv6_routes: tuple[str, ...]
    cap_bounding: str = CAPABILITY_BOUNDING_MASK


@dataclass(frozen=True)
class GuestBuildResult:
    status: str
    operation_id: str
    request_sha256: str
    iso_path: str | None
    iso_size: int
    iso_sha256: str | None
    command_argv_sha256: str | None
    elapsed_seconds: float
    commands_run: tuple[str, ...]
    cleanup_complete: bool


def _manifest_file_set(manifest: Mapping[str, Any]) -> set[str]:
    expected = {BUILD_MANIFEST_RELATIVE, PACSTRAP_SOURCE_RELATIVE}
    dbs = manifest.get("repository_databases")
    packages = manifest.get("packages")
    overlay = manifest.get("pacstrap_overlay")
    if not isinstance(dbs, dict) or set(dbs) != {"core", "extra"}:
        _reject("package repository database inventory is incomplete")
    if not isinstance(packages, list) or not packages:
        _reject("package archive inventory is empty or malformed")
    if not isinstance(overlay, dict) or set(overlay) != {"path", "source_sha256", "patch_sha256", "patched_sha256"}:
        _reject("pacstrap overlay manifest is malformed")
    for name, record in dbs.items():
        if not isinstance(record, dict) or set(record) != {"path", "sha256", "signature_path", "signature_sha256"}:
            _reject("repository database manifest entry is malformed")
        if record.get("path") != PACMAN_DATABASE_RELATIVE[name]:
            _reject("repository database path differs from the fixed snapshot layout")
        expected.add(record["path"])
        if record.get("signature_path") != record["path"] + ".sig" or not isinstance(record.get("signature_sha256"), str):
            _reject("every pinned repository database requires its adjacent signed snapshot signature")
        if re.fullmatch(r"[0-9a-f]{64}", record["signature_sha256"]) is None:
            _reject("repository database signature hash is malformed")
        expected.add(record["signature_path"])
    seen_names: set[str] = set()
    for record in packages:
        fields = {"name", "version", "archive_path", "archive_size", "archive_sha256", "signature_path", "signature_sha256"}
        if not isinstance(record, dict) or set(record) != fields:
            _reject("package archive manifest entry is malformed")
        name = record.get("name")
        version = record.get("version")
        archive = record.get("archive_path")
        if not isinstance(name, str) or re.fullmatch(r"[a-zA-Z0-9@._+-]+", name) is None or name in seen_names:
            _reject("package names are malformed or duplicated")
        if not isinstance(version, str) or not version or any(ch.isspace() for ch in version):
            _reject("package version is malformed")
        if not isinstance(archive, str) or not any(archive.startswith(root + "/") for root in PACMAN_REPOSITORY_ROOTS.values()):
            _reject("package archive is outside the fixed local repository layout")
        rel = PurePosixPath(archive)
        if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts) or not archive.endswith(".pkg.tar.zst"):
            _reject("package archive path is malformed")
        if record.get("signature_path") != archive + ".sig" or not isinstance(record.get("signature_sha256"), str):
            _reject("every package archive must have its adjacent detached signature")
        if re.fullmatch(r"[0-9a-f]{64}", record["signature_sha256"]) is None:
            _reject("package detached signature hash is malformed")
        expected.add(archive)
        expected.add(archive + ".sig")
        seen_names.add(name)
    if overlay.get("path") != PACSTRAP_SOURCE_RELATIVE:
        _reject("pacstrap overlay source path differs from the fixed package image path")
    for key in ("source_sha256", "patch_sha256", "patched_sha256"):
        if not isinstance(overlay.get(key), str) or re.fullmatch(r"[0-9a-f]{64}", overlay[key]) is None:
            _reject("pacstrap overlay hashes are malformed")
    return expected


def _validate_build_manifest(
    *,
    request: Mapping[str, object],
    source_root: Path,
    package_root: Path,
    operations: GuestBuildOperations,
) -> tuple[dict[str, Any], Path, Path]:
    lock = _snapshot_lock(source_root, request)
    request_sha = _sha256_bytes(canonical_json(dict(request)))
    manifest, _ = _read_build_manifest(package_root)
    source_binding = request.get("source")
    package_binding = request.get("packages")
    snapshot_binding = request.get("snapshot")
    profile_binding = request.get("profile")
    expected_bindings = {
        "operation_id": request.get("operation_id"),
        "request_sha256": request_sha,
        "source_sha256": source_binding.get("sha256") if isinstance(source_binding, dict) else None,
        "package_image_sha256": package_binding.get("sha256") if isinstance(package_binding, dict) else None,
        "snapshot_lock_sha256": snapshot_binding.get("lock_sha256") if isinstance(snapshot_binding, dict) else None,
        "profile_inventory_sha256": profile_binding.get("sha256") if isinstance(profile_binding, dict) else None,
    }
    if any(manifest.get(key) != value for key, value in expected_bindings.items()):
        _reject("package manifest is not bound to this exact worker request")
    files = _manifest_file_set(manifest)
    _verify_manifest_tree(package_root, files)

    lock_rootless = lock.get("rootless_builder")
    if not isinstance(lock_rootless, dict):
        _reject("snapshot lock lacks the pinned pacstrap overlay contract")
    overlay = manifest["pacstrap_overlay"]
    expected_overlay = {
        "source_sha256": lock_rootless.get("pacstrap_sha256"),
        "patch_sha256": lock_rootless.get("pacstrap_status_overlay", {}).get("patch_sha256"),
        "patched_sha256": lock_rootless.get("pacstrap_status_overlay", {}).get("patched_sha256"),
    }
    if any(overlay.get(key) != value for key, value in expected_overlay.items()):
        _reject("operation pacstrap overlay is not bound to the exact locked status patch")

    repository_pins = lock.get("repository_database_sha256")
    if not isinstance(repository_pins, dict) or set(repository_pins) != {"core", "extra"}:
        _reject("snapshot lock database pins are malformed")
    repository_versions: dict[str, str] = {}
    for repo_name, record in manifest["repository_databases"].items():
        if record.get("sha256") != repository_pins.get(repo_name):
            _reject(f"{repo_name} database differs from the exact dated snapshot pin")
        database = _safe_file(package_root, record["path"], f"{repo_name} database")
        if _sha256_path(database) != record["sha256"]:
            _reject(f"{repo_name} database hash differs from the package manifest")
        signature_path = record.get("signature_path")
        if signature_path is not None:
            signature = _safe_file(package_root, signature_path, f"{repo_name} database signature")
            if not isinstance(record.get("signature_sha256"), str) or _sha256_path(signature) != record["signature_sha256"]:
                _reject(f"{repo_name} database signature hash differs from its manifest")
            if not operations.verify_detached_signature(signature, database):
                _reject(f"{repo_name} database signature is invalid")
        else:
            _reject(f"{repo_name} database is missing its detached signature")
        database_map = operations.repository_database_versions(database)
        if not isinstance(database_map, Mapping) or not database_map:
            _reject(f"{repo_name} database package inventory is unavailable")
        for package_name, package_version in database_map.items():
            if package_name in repository_versions:
                _reject("same package name appears in both pinned repositories")
            if not isinstance(package_name, str) or not isinstance(package_version, str):
                _reject("repository package inventory has malformed names or versions")
            repository_versions[package_name] = package_version

    package_names = _profile_package_names(source_root, request)
    profile = request.get("profile")
    if not isinstance(profile, dict) or not isinstance(profile.get("files"), list):
        _reject("request profile inventory is malformed")
    package_file_entries = [
        item for item in profile["files"]
        if isinstance(item, dict) and item.get("path") == "usb_builder/archiso_profile/packages.x86_64"
    ]
    if len(package_file_entries) != 1 or manifest.get("package_list_sha256") != package_file_entries[0].get("sha256"):
        _reject("build manifest package-list hash differs from the bound source profile")
    declared: dict[str, str] = {}
    for record in manifest["packages"]:
        archive = _safe_file(package_root, record["archive_path"], f"{record['name']} package archive")
        signature = _safe_file(package_root, record["signature_path"], f"{record['name']} package signature")
        if isinstance(record.get("archive_size"), bool) or record["archive_size"] != archive.stat().st_size:
            _reject(f"{record['name']} package size differs from the manifest")
        if _sha256_path(archive) != record["archive_sha256"] or _sha256_path(signature) != record["signature_sha256"]:
            _reject(f"{record['name']} package or signature hash differs from the manifest")
        if not operations.verify_detached_signature(signature, archive):
            _reject(f"{record['name']} package signature is invalid")
        actual_name, actual_version = operations.package_identity(archive)
        if actual_name != record["name"] or actual_version != record["version"]:
            _reject(f"{record['name']} package identity differs from its archive metadata")
        if repository_versions.get(actual_name) != actual_version:
            _reject(f"{actual_name} package version is not present in its pinned repository database")
        declared[actual_name] = actual_version
    if not set(package_names).issubset(declared):
        _reject("a profile package is absent from the signed local repository manifest")
    for name, expected_version in LOCKED_PACKAGES.items():
        if name not in declared:
            _reject(f"required locked package {name} is absent from the signed local repository manifest")
        if declared[name] != expected_version:
            _reject(f"required package {name} differs from locked version {expected_version}")
    pacstrap_source = _safe_file(package_root, PACSTRAP_SOURCE_RELATIVE, "patched pacstrap source")
    if _sha256_path(pacstrap_source) != overlay["patched_sha256"]:
        _reject("patched pacstrap bytes differ from the exact locked child-status overlay")
    patch_source = _safe_file(source_root, PACSTRAP_PATCH_RELATIVE, "pacstrap status patch")
    if _sha256_path(patch_source) != overlay["patch_sha256"]:
        _reject("source image pacstrap patch bytes differ from the exact snapshot lock")
    return manifest, pacstrap_source, _safe_directory(source_root, "usb_builder/archiso_profile", "profile directory")


def _safe_directory(root: Path, relative: str, label: str) -> Path:
    if not isinstance(relative, str) or "\\" in relative or "\x00" in relative:
        _reject(f"{label} path is not a canonical relative path")
    rel = PurePosixPath(relative)
    if rel.is_absolute() or not rel.parts or any(part in ("", ".", "..") for part in rel.parts):
        _reject(f"{label} path traverses its read-only image")
    candidate = root.joinpath(*rel.parts)
    cursor = root
    for component in rel.parts:
        cursor = cursor / component
        try:
            info = cursor.lstat()
        except OSError as exc:
            raise OfflineVmBuildError(f"{label} path is missing") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            _reject(f"{label} must contain only real directories")
    return candidate


def _expected_fixed_invocation(request_sha256: str, overlay_path: Path, operation_id: str) -> BuildInvocation:
    env = {
        "PATH": f"{overlay_path.parent}:/usr/bin:/usr/sbin:/bin",
        "HOME": "/run/librecore-worker",
        "TMPDIR": f"{WORKER_ROOT}/tmp",
        "LANG": "C",
        "LC_ALL": "C",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONOPTIMIZE": "0",
    }
    argv = (
        MKARCHISO_PATH,
        "-v",
        "-C",
        WORKER_PACMAN_CONF,
        "-w",
        WORKER_WORK,
        "-o",
        WORKER_OUTPUT,
        WORKER_PROFILE,
    )
    return BuildInvocation(
        operation_id=operation_id,
        service_name=BUILD_SERVICE_NAME,
        argv=argv,
        env=env,
        cwd="/",
        timeout_seconds=BUILD_TIMEOUT_SECONDS,
        request_sha256=request_sha256,
        pacstrap_overlay=str(overlay_path),
        pacstrap_overlay_sha256=_sha256_bytes(PACSTRAP_WRAPPER_BYTES),
        pacstrap_binary_sha256=PACSTRAP_BINARY_SHA256,
    )


def _invocation_payload(invocation: BuildInvocation) -> dict[str, object]:
    return {
        "operation_id": invocation.operation_id,
        "service_name": invocation.service_name,
        "argv": list(invocation.argv),
        "env": dict(invocation.env),
        "cwd": invocation.cwd,
        "timeout_seconds": invocation.timeout_seconds,
        "request_sha256": invocation.request_sha256,
        "pacstrap_overlay": invocation.pacstrap_overlay,
        "pacstrap_overlay_sha256": invocation.pacstrap_overlay_sha256,
        "pacstrap_binary_sha256": invocation.pacstrap_binary_sha256,
    }


def _write_exclusive_private(path: Path, payload: bytes, mode: int = 0o600) -> None:
    if _path_has_symlink_component(path.parent) or path.exists() or path.is_symlink():
        _reject("fixed guest operation file already exists or has a linked path")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags, mode)
    except OSError as exc:
        raise OfflineVmBuildError("fixed guest operation file could not be created") from exc
    try:
        if os.name == "posix":
            os.fchmod(fd, mode)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != mode:
            _reject("fixed guest operation file has unsafe inode identity or mode")
        offset = 0
        while offset < len(payload):
            amount = os.write(fd, payload[offset:])
            if amount <= 0:
                _reject("fixed guest operation file write was incomplete")
            offset += amount
        os.fsync(fd)
    except BaseException:
        try:
            os.close(fd)
        finally:
            try:
                path.unlink()
            except OSError:
                pass
        raise
    else:
        os.close(fd)


def _write_invocation(invocation: BuildInvocation) -> None:
    _write_exclusive_private(
        Path(BUILD_INVOCATION_PATH),
        canonical_json(_invocation_payload(invocation)),
        0o600,
    )


def _create_private_directory(path: Path) -> None:
    if _path_has_symlink_component(path.parent) or path.exists() or path.is_symlink():
        _reject("fixed guest operation directory already exists or has a linked path")
    path.mkdir(mode=0o700)
    if os.name == "posix":
        path.chmod(0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
        _reject("fixed guest operation directory has an unsafe type or mode")
    if os.name == "posix" and info.st_uid != _linux_effective_uid():
        _reject("fixed guest operation directory has an unexpected owner")


def _verify_private_file(path: Path, *, expected_mode: int = 0o600, expected_sha256: str | None = None) -> os.stat_result:
    if _path_has_symlink_component(path):
        _reject("fixed guest operation file has a linked path")
    try:
        info = path.lstat()
    except OSError as exc:
        raise OfflineVmBuildError("fixed guest operation file is missing") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != expected_mode:
        _reject("fixed guest operation file has unsafe type, link count, or mode")
    if os.name == "posix" and info.st_uid != _linux_effective_uid():
        _reject("fixed guest operation file has an unexpected owner")
    if expected_sha256 is not None and _sha256_path(path) != expected_sha256:
        _reject("fixed guest operation file bytes differ from the exact expected hash")
    return info


def _invocation_sha256(invocation: BuildInvocation) -> str:
    return _sha256_bytes(canonical_json({
        "operation_id": invocation.operation_id,
        "request_sha256": invocation.request_sha256,
        "argv": list(invocation.argv),
        "cwd": invocation.cwd,
        "env": dict(invocation.env),
        "pacstrap_overlay": invocation.pacstrap_overlay,
        "pacstrap_overlay_sha256": invocation.pacstrap_overlay_sha256,
        "pacstrap_binary_sha256": invocation.pacstrap_binary_sha256,
        "service_name": invocation.service_name,
        "timeout_seconds": invocation.timeout_seconds,
    }))


def _claim_one_shot(operation_root: Path, request_sha256: str) -> None:
    claim = operation_root / ".offline-build-consumed"
    if _path_has_symlink_component(operation_root) or claim.exists() or claim.is_symlink():
        _reject("guest build request has already been consumed or operation path is linked")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(claim, flags, 0o600)
    except OSError as exc:
        raise OfflineVmBuildError("guest build one-shot claim could not be created") from exc
    try:
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
            _reject("guest build claim file has unsafe identity or mode")
        payload = canonical_json({"format": BUILD_RECEIPT_FORMAT, "request_sha256": request_sha256})
        offset = 0
        while offset < len(payload):
            amount = os.write(fd, payload[offset:])
            if amount <= 0:
                _reject("guest build claim write was incomplete")
            offset += amount
        os.fsync(fd)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            claim.unlink()
        except OSError:
            pass
        raise
    else:
        os.close(fd)


def _verify_source_profile(source_root: Path, request: Mapping[str, object]) -> None:
    from usb_builder.offline_vm_guest_worker import validate_profile_inventory

    validate_profile_inventory(request, source_root)
    profile_root = source_root / "usb_builder" / "archiso_profile"
    try:
        build_usb._profile_boot_modes(profile_root)
        build_usb._audit_profile_line_endings(profile_root, label="sealed VM source profile")
        build_usb._verify_archiso_initramfs_config(profile_root, label="sealed VM source")
        build_usb._verify_usb_initramfs_derivative(profile_root, label="sealed VM source")
    except build_usb.BuildContractError as exc:
        raise OfflineVmBuildError(f"sealed VM source profile failed existing USB builder validation: {exc}") from exc


def prepare_guest_build(
    request_value: Mapping[str, object],
    isolation: Any,
    security: GuestSecurityState,
    source_root: Path,
    package_root: Path,
    scratch_root: Path,
    operations: GuestBuildOperations,
) -> BuildInvocation:
    """Complete all read-only trust checks, then claim and stage one fixed job.

    This phase must run in the worker's restricted preflight service with
    ``NoNewPrivileges=1``.  It cannot execute mkarchiso or select a command.
    """
    request = parse_request(canonical_json(dict(request_value)))
    from usb_builder.offline_vm_guest_worker import validate_guest_isolation

    validate_guest_isolation(request, isolation)
    validate_preflight_security(security, isolation)
    if source_root != Path("/mnt/librecore-source") or package_root != Path("/mnt/librecore-packages") or scratch_root != Path(WORKER_ROOT):
        _reject("guest source, package, or scratch roots differ from the fixed mount layout")
    try:
        scratch_info = scratch_root.lstat()
    except OSError as exc:
        raise OfflineVmBuildError("fixed scratch root is missing") from exc
    if (
        stat.S_ISLNK(scratch_info.st_mode)
        or not stat.S_ISDIR(scratch_info.st_mode)
        or stat.S_IMODE(scratch_info.st_mode) != 0o700
        or (os.name == "posix" and scratch_info.st_uid != _linux_effective_uid())
        or any(scratch_root.iterdir())
    ):
        _reject("fixed scratch root is not a fresh private operation directory")
    source_image = request.get("source")
    package_image = request.get("packages")
    if not isinstance(source_image, dict) or not isinstance(package_image, dict):
        _reject("worker request image bindings are malformed")
    if operations.hash_attached_device("source", int(source_image["size"])) != source_image["sha256"]:
        _reject("read-only source block device hash differs from the worker request")
    if operations.hash_attached_device("packages", int(package_image["size"])) != package_image["sha256"]:
        _reject("read-only package block device hash differs from the worker request")
    _snapshot_lock(source_root, request)
    _verify_source_profile(source_root, request)
    manifest, pacstrap_source, _ = _validate_build_manifest(
        request=request,
        source_root=source_root,
        package_root=package_root,
        operations=operations,
    )
    request_sha256 = _sha256_bytes(canonical_json(dict(request)))
    if manifest.get("request_sha256") != request_sha256:
        _reject("package build manifest does not bind the canonical request bytes")

    # This is the first write in the guest operation: all trust, signature,
    # profile, route, and device checks have completed under NNP=1.
    operations.claim_one_shot(scratch_root, request_sha256)
    created: list[Path] = []
    try:
        _write_exclusive_private(Path(BUILD_REQUEST_PATH), canonical_json(dict(request)), 0o600)
        created.append(Path(BUILD_REQUEST_PATH))
        overlay_dir = scratch_root / "bin"
        _create_private_directory(overlay_dir)
        created.append(overlay_dir)
        pacstrap_real = overlay_dir / "pacstrap.real"
        expected_overlay_hash = manifest["pacstrap_overlay"]["patched_sha256"]
        actual_overlay_hash = operations.stage_pacstrap(pacstrap_source, pacstrap_real, expected_overlay_hash)
        if actual_overlay_hash != expected_overlay_hash:
            _reject("staged operation pacstrap overlay differs from its exact locked hash")
        if pacstrap_real.is_symlink() or not pacstrap_real.is_file() or _sha256_path(pacstrap_real) != expected_overlay_hash:
            _reject("staged operation pacstrap executable is not the exact locked single file")
        if os.name == "posix":
            pacstrap_real.chmod(0o700)
        created.append(pacstrap_real)
        _write_exclusive_private(overlay_dir / "pacstrap", PACSTRAP_WRAPPER_BYTES, 0o700)
        created.append(overlay_dir / "pacstrap")
        _write_exclusive_private(scratch_root / "pacstrap-exit-codes", b"", 0o600)
        created.append(scratch_root / "pacstrap-exit-codes")
        for relative in ("tmp", "archiso-work", "image-output"):
            directory = scratch_root / relative
            _create_private_directory(directory)
            created.append(directory)
        config = scratch_root / "pacman.conf"
        _write_exclusive_private(config, PACMAN_CONFIG_BYTES, 0o600)
        created.append(config)
        invocation = _expected_fixed_invocation(request_sha256, overlay_dir / "pacstrap", str(request["operation_id"]))
        _write_invocation(invocation)
        created.append(Path(BUILD_INVOCATION_PATH))
        return invocation
    except BaseException:
        for path in reversed(created):
            try:
                if path.is_dir() and not path.is_symlink():
                    path.rmdir()
                else:
                    path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def _terminate_builder_process(process: Any, *, grace_seconds: float = 5.0) -> None:
    if process.poll() is not None:
        return
    try:
        _linux_kill_process_group(process.pid, "SIGTERM")
    except OSError:
        pass
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        try:
            _linux_kill_process_group(process.pid, "SIGKILL")
        except OSError:
            pass
        process.wait(timeout=grace_seconds)


class FixedMkarchisoOperations:
    """Concrete fixed-command executor for the dedicated systemd guest unit.

    This class has no command/argument/environment input other than the
    immutable ``BuildInvocation`` contract.  It must be called only after
    ``validate_build_service_security`` in the sealed guest service.
    """

    def __init__(
        self,
        service_state: GuestSecurityState,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._service_state = service_state
        self._popen = popen
        self._clock = clock

    def run_fixed_builder(self, invocation: BuildInvocation) -> BuildProcessResult:
        expected = _expected_fixed_invocation(
            invocation.request_sha256,
            Path(invocation.pacstrap_overlay),
            invocation.operation_id,
        )
        if invocation != expected:
            _reject("fixed guest executor rejected a non-canonical invocation")
        validate_build_service_security(self._service_state)
        scratch = Path(WORKER_ROOT)
        overlay = Path(invocation.pacstrap_overlay)
        if overlay != scratch / "bin" / "pacstrap":
            _reject("fixed pacstrap wrapper path differs from the operation-owned location")
        wrapper = _verify_private_file(overlay, expected_mode=0o700, expected_sha256=invocation.pacstrap_overlay_sha256)
        real_pacstrap = overlay.with_name("pacstrap.real")
        real_info = _verify_private_file(real_pacstrap, expected_mode=0o700, expected_sha256=invocation.pacstrap_binary_sha256)
        if wrapper.st_dev != real_info.st_dev or wrapper.st_uid != real_info.st_uid:
            _reject("fixed pacstrap wrapper and executable do not share the operation identity")
        config = scratch / "pacman.conf"
        _verify_private_file(config, expected_mode=0o600, expected_sha256=_sha256_bytes(PACMAN_CONFIG_BYTES))
        status_path = Path(PACSTRAP_EXIT_CODES_PATH)
        status_info = _verify_private_file(status_path, expected_mode=0o600)
        if status_info.st_size != 0:
            _reject("pacstrap status receipt was not fresh before the fixed build")
        for fixed_dir in (scratch / "tmp", Path(WORKER_WORK), Path(WORKER_OUTPUT)):
            try:
                info = fixed_dir.lstat()
            except OSError as exc:
                raise OfflineVmBuildError("fixed builder directory is missing") from exc
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
                _reject("fixed builder directory has unsafe type or mode")
            if os.name == "posix" and info.st_uid != self._service_state.uid:
                _reject("fixed builder directory is not owned by fwbuild")
        if any(Path(WORKER_OUTPUT).iterdir()) or any(Path(WORKER_WORK).iterdir()):
            _reject("fixed builder work/output directories are not empty")

        log_path = scratch / "build.log"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            log_fd = os.open(log_path, flags, 0o600)
        except OSError as exc:
            raise OfflineVmBuildError("fixed builder log could not be created exclusively") from exc
        process: Any | None = None
        timed_out = False
        returncode: int | None = None
        started = self._clock()
        try:
            if os.name == "posix":
                os.fchmod(log_fd, 0o600)
            log_info = os.fstat(log_fd)
            if not stat.S_ISREG(log_info.st_mode) or log_info.st_nlink != 1 or stat.S_IMODE(log_info.st_mode) != 0o600:
                _reject("fixed builder log has unsafe inode identity or mode")
            process = self._popen(
                invocation.argv,
                executable=MKARCHISO_PATH,
                cwd="/",
                env=dict(invocation.env),
                stdin=subprocess.DEVNULL,
                stdout=log_fd,
                stderr=subprocess.STDOUT,
                close_fds=True,
                shell=False,
                start_new_session=True,
            )
            try:
                returncode = process.wait(timeout=invocation.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate_builder_process(process)
                returncode = process.returncode
        except BaseException:
            if process is not None and process.poll() is None:
                _terminate_builder_process(process)
            raise
        finally:
            os.close(log_fd)
        elapsed = max(0.0, self._clock() - started)
        status_bytes = status_path.read_bytes()
        codes: list[int] = []
        for line in status_bytes.splitlines():
            if not line or any(byte < ord("0") or byte > ord("9") for byte in line):
                codes = [-1]
                break
            code = int(line)
            if not 0 <= code <= 255:
                codes = [-1]
                break
            codes.append(code)
        return BuildProcessResult(
            service_name=BUILD_SERVICE_NAME,
            argv_sha256=_invocation_sha256(invocation),
            returncode=returncode,
            timed_out=timed_out,
            elapsed_seconds=elapsed,
            no_new_privs_before=True,
            no_new_privs_during=self._service_state.no_new_privs,
            network_namespace_before=self._service_state.network_namespace,
            network_namespace_during=self._service_state.network_namespace,
            pacstrap_exit_codes=tuple(codes),
            account=self._service_state.account,
            uid=self._service_state.uid,
            gid=self._service_state.gid,
            supplementary_gids=self._service_state.supplementary_gids,
            cap_effective=self._service_state.cap_effective,
            cap_permitted=self._service_state.cap_permitted,
            cap_ambient=self._service_state.cap_ambient,
            interfaces=self._service_state.interfaces,
            ipv4_routes=self._service_state.ipv4_routes,
            ipv6_routes=self._service_state.ipv6_routes,
            cap_bounding=self._service_state.cap_bounding,
        )

    def list_iso_outputs(self, output_dir: Path) -> Sequence[Path]:
        if output_dir != Path(WORKER_OUTPUT) or _path_has_symlink_component(output_dir):
            _reject("fixed ISO output directory differs from the operation-owned path")
        return tuple(sorted(output_dir.iterdir()))

    def cleanup_overlay(self, overlay: Path) -> None:
        if overlay != Path(WORKER_PACSTRAP_BIN) / "pacstrap" or _path_has_symlink_component(overlay.parent):
            _reject("refusing cleanup outside the fixed pacstrap overlay directory")
        expected_files = {
            "pacstrap": _sha256_bytes(PACSTRAP_WRAPPER_BYTES),
            "pacstrap.real": PACSTRAP_BINARY_SHA256,
        }
        directory = overlay.parent
        if not directory.exists() and not directory.is_symlink():
            return
        if not directory.is_dir() or directory.is_symlink() or {item.name for item in directory.iterdir()} != set(expected_files):
            _reject("pacstrap overlay directory contains unexpected entries")
        for name, digest in expected_files.items():
            path = directory / name
            _verify_private_file(path, expected_mode=0o700, expected_sha256=digest)
            path.unlink()
        directory.rmdir()


class LinuxGuestBuildOperations:
    """Fixed Linux implementation of the NNP=1 guest preflight operations.

    Every executable path and argument shape is compiled here. Package and
    signature checks run inside the route-free preflight service; no request
    field can select a command. The same class can be instantiated by the
    separate NNP=0 builder service for its fixed mkarchiso executor.
    """

    def __init__(self, service_state: GuestSecurityState) -> None:
        self._service_state = service_state
        self._builder = FixedMkarchisoOperations(service_state)

    @staticmethod
    def _block_path(role: str) -> Path:
        from usb_builder.offline_vm_guest_worker import DEVICE_IDS

        if role not in {"source", "packages"}:
            _reject("guest block hashing is restricted to read-only source/package devices")
        return Path("/dev/disk/by-id") / f"virtio-{DEVICE_IDS[role]}"

    @staticmethod
    def _check_ro_device(path: Path, device_number: int) -> None:
        major_fn = getattr(os, "major", None)
        minor_fn = getattr(os, "minor", None)
        if not callable(major_fn) or not callable(minor_fn):
            _reject("guest block identity helpers are unavailable")
        ro_path = Path("/sys/dev/block") / f"{major_fn(device_number)}:{minor_fn(device_number)}" / "ro"
        try:
            observed = ro_path.read_text(encoding="ascii").strip()
        except OSError as exc:
            raise OfflineVmBuildError("guest block read-only state is unavailable") from exc
        if observed != "1":
            _reject("guest source/package block is not kernel-marked read-only")

    def hash_attached_device(self, role: str, expected_size: int) -> str:
        if isinstance(expected_size, bool) or not isinstance(expected_size, int) or not 0 < expected_size <= MAX_ATTACHED_IMAGE_BYTES:
            _reject("attached guest image size is outside the fixed streaming hash bound")
        path = self._block_path(role)
        try:
            before = path.stat()
        except OSError as exc:
            raise OfflineVmBuildError("fixed guest source/package block is unavailable") from exc
        if not stat.S_ISBLK(before.st_mode):
            _reject("fixed guest image role is not a block device")
        device_number = _linux_stat_rdev(before)
        self._check_ro_device(path, device_number)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(path, flags)
        try:
            opened = os.fstat(fd)
            after = path.stat()
            if (
                not stat.S_ISBLK(opened.st_mode)
            or _linux_stat_rdev(opened) != device_number
            or _linux_stat_rdev(after) != device_number
            ):
                _reject("fixed guest image block identity changed during open")
            actual_size = struct.unpack("Q", _linux_ioctl(fd, 0x80081272, struct.pack("Q", 0)))[0]
            if actual_size != expected_size:
                _reject("fixed guest image block capacity differs from the request")
            digest = hashlib.sha256()
            offset = 0
            while offset < expected_size:
                length = min(1024 * 1024, expected_size - offset)
                chunk = _linux_pread(fd, length, offset)
                if not chunk:
                    _reject("fixed guest image block shortened during streaming hash")
                digest.update(chunk)
                offset += len(chunk)
            if _linux_pread(fd, 1, expected_size):
                _reject("fixed guest image block contains bytes beyond its requested size")
            self._check_ro_device(path, device_number)
            if _linux_stat_rdev(path.stat()) != device_number:
                _reject("fixed guest image block identity changed after streaming hash")
            return digest.hexdigest()
        finally:
            os.close(fd)

    @staticmethod
    def _tool_identity(path: Path) -> tuple[os.stat_result, str]:
        if not path.is_absolute() or _path_has_symlink_component(path):
            _reject("fixed package tool path is linked or not absolute")
        try:
            info = path.lstat()
        except OSError as exc:
            raise OfflineVmBuildError("fixed package tool is unavailable") from exc
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not (info.st_mode & 0o111):
            _reject("fixed package tool is not a single-link executable regular file")
        if os.name == "posix" and (info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022):
            _reject("fixed package tool is not root-owned and non-writable")
        for parent in path.parents:
            if parent == Path(parent.anchor):
                break
            parent_info = parent.lstat()
            if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
                _reject("fixed package tool parent is not a real directory")
            if os.name == "posix" and (parent_info.st_uid != 0 or stat.S_IMODE(parent_info.st_mode) & 0o022):
                _reject("fixed package tool has an untrusted writable parent")
        return info, _sha256_path(path)

    @classmethod
    def _run_fixed_query(cls, executable: Path, args: Sequence[str], *, timeout: float = 30.0) -> bytes:
        before, digest = cls._tool_identity(executable)
        try:
            result = subprocess.run(
                [str(executable), *args],
                executable=str(executable),
                cwd="/",
                env={"PATH": "/usr/bin:/bin", "HOME": "/run/librecore-worker", "LANG": "C", "LC_ALL": "C"},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                close_fds=True,
                shell=False,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise OfflineVmBuildError("fixed offline package query exceeded its bound") from exc
        after, after_digest = cls._tool_identity(executable)
        if (before.st_dev, before.st_ino, before.st_uid, before.st_size) != (after.st_dev, after.st_ino, after.st_uid, after.st_size) or digest != after_digest:
            _reject("fixed package tool identity changed during its read-only query")
        if result.returncode != 0 or len(result.stdout) > 1024 * 1024:
            _reject("fixed offline package query failed or exceeded its output bound")
        return result.stdout

    def verify_detached_signature(self, signature: Path, artifact: Path) -> bool:
        output = self._run_fixed_query(Path("/usr/bin/pacman-key"), ("--verify", str(signature), str(artifact)))
        return b"Good signature" in output or b"good signature" in output.lower()

    def package_identity(self, archive: Path) -> tuple[str, str]:
        output = self._run_fixed_query(Path("/usr/bin/pacman"), ("--config", "/dev/null", "-Qp", str(archive)))
        try:
            fields = output.decode("ascii").strip().split()
        except UnicodeDecodeError as exc:
            raise OfflineVmBuildError("fixed local package identity output is not ASCII") from exc
        if len(fields) != 2 or any(not item or any(ch.isspace() for ch in item) for item in fields):
            _reject("fixed local package identity output is malformed")
        return fields[0], fields[1]

    @staticmethod
    def repository_database_versions(database: Path) -> Mapping[str, str]:
        if database.stat().st_size > MAX_REPOSITORY_DATABASE_BYTES:
            _reject("fixed repository database exceeds its size bound")
        versions: dict[str, str] = {}
        try:
            with tarfile.open(database, mode="r:*") as archive:
                for index, member in enumerate(archive):
                    if index >= MAX_REPOSITORY_DATABASE_MEMBERS:
                        _reject("fixed repository database contains too many members")
                    if not member.name.endswith("/desc") and member.name != "desc":
                        continue
                    if not member.isfile() or member.size > MAX_REPOSITORY_PACKAGE_DESC_BYTES:
                        _reject("repository package descriptor has unsafe type or size")
                    stream = archive.extractfile(member)
                    if stream is None:
                        _reject("repository package descriptor cannot be read")
                    with stream:
                        raw = stream.read(MAX_REPOSITORY_PACKAGE_DESC_BYTES + 1)
                    if len(raw) != member.size or len(raw) > MAX_REPOSITORY_PACKAGE_DESC_BYTES:
                        _reject("repository package descriptor changed or exceeds its bound")
                    try:
                        lines = raw.decode("utf-8").splitlines()
                    except UnicodeDecodeError as exc:
                        raise OfflineVmBuildError("repository package descriptor is not UTF-8") from exc
                    records: dict[str, str] = {}
                    position = 0
                    while position < len(lines):
                        token = lines[position]
                        if token in {"%NAME%", "%VERSION%"} and position + 1 < len(lines):
                            records[token] = lines[position + 1]
                            position += 2
                        else:
                            position += 1
                    name, version = records.get("%NAME%"), records.get("%VERSION%")
                    if not name or not version or name in versions:
                        _reject("repository database has missing or duplicate package identity")
                    versions[name] = version
        except (OSError, tarfile.TarError) as exc:
            raise OfflineVmBuildError("fixed repository database is not a readable package archive") from exc
        if not versions:
            _reject("fixed repository database has no package version records")
        return versions

    def stage_pacstrap(self, source: Path, destination: Path, expected_sha256: str) -> str:
        if _path_has_symlink_component(source) or destination != Path(WORKER_PACSTRAP_BIN) / "pacstrap.real":
            _reject("patched pacstrap staging paths differ from fixed operation paths")
        try:
            source_info = source.lstat()
        except OSError as exc:
            raise OfflineVmBuildError("patched pacstrap source is unavailable") from exc
        if not stat.S_ISREG(source_info.st_mode) or source_info.st_nlink != 1 or source_info.st_size > 16 * 1024 * 1024:
            _reject("patched pacstrap source is not a single-link regular file")
        payload = source.read_bytes()
        digest = _sha256_bytes(payload)
        if digest != expected_sha256:
            _reject("patched pacstrap source changed before staging")
        _write_exclusive_private(destination, payload, 0o700)
        return digest

    def claim_one_shot(self, operation_root: Path, request_sha256: str) -> None:
        _claim_one_shot(operation_root, request_sha256)

    def run_fixed_builder(self, invocation: BuildInvocation) -> BuildProcessResult:
        return self._builder.run_fixed_builder(invocation)

    def list_iso_outputs(self, output_dir: Path) -> Sequence[Path]:
        return self._builder.list_iso_outputs(output_dir)

    def cleanup_overlay(self, overlay: Path) -> None:
        self._builder.cleanup_overlay(overlay)


def _verify_installed_systemd_resource(path: Path, expected_name: str, expected_bytes: bytes) -> None:
    if _path_has_symlink_component(path):
        _reject(f"fixed build systemd resource {expected_name} path contains a symlink")
    try:
        info = path.lstat()
    except OSError as exc:
        raise OfflineVmBuildError(f"fixed build systemd resource {expected_name} is missing") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o644:
        _reject(f"fixed build systemd resource {expected_name} has unsafe type, links, or mode")
    if os.name == "posix" and info.st_uid != 0:
        _reject(f"fixed build systemd resource {expected_name} is not root-owned")
    for directory in path.parents:
        if directory == Path(directory.anchor):
            break
        dir_info = directory.lstat()
        if stat.S_ISLNK(dir_info.st_mode) or not stat.S_ISDIR(dir_info.st_mode):
            _reject(f"fixed build systemd resource {expected_name} parent is not a real directory")
        if os.name == "posix" and (dir_info.st_uid != 0 or stat.S_IMODE(dir_info.st_mode) & 0o022):
            _reject(f"fixed build systemd resource {expected_name} has an untrusted writable parent")
    try:
        from importlib.resources import files

        packaged = files("usb_builder").joinpath(f"systemd/{expected_name}").read_bytes()
    except (OSError, ModuleNotFoundError) as exc:
        raise OfflineVmBuildError(f"packaged fixed build systemd resource {expected_name} is unavailable") from exc
    if path.read_bytes() != packaged or packaged != expected_bytes:
        _reject(f"installed build systemd resource {expected_name} differs from its exact packaged bytes")


def _systemd_lstat_from_root(root: Path, relative: str, label: str) -> os.stat_result | None:
    """Inspect an optional systemd path without following a symlink component."""
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    try:
        parts = candidate.relative_to(root).parts
    except ValueError as exc:
        raise OfflineVmBuildError(f"systemd {label} escaped its inspection root") from exc
    cursor = root
    for index, part in enumerate(parts):
        cursor = cursor / part
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise OfflineVmBuildError(f"systemd {label} could not be inspected") from exc
        if stat.S_ISLNK(info.st_mode):
            _reject(f"systemd {label} contains a symlink component")
        if root == Path("/") and os.name == "posix" and (
            info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022
        ):
            _reject(f"systemd {label} has a non-root-owned or writable path component")
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            _reject(f"systemd {label} parent is not a real directory")
    return info


def _verify_no_systemd_override_files(
    unit_name: str,
    canonical_path: Path,
    *,
    root: Path = Path("/"),
) -> None:
    """Reject higher-priority unit files and every unreviewed effective drop-in."""
    if not unit_name.endswith((".service", ".path")) or Path(unit_name).name != unit_name:
        _reject("fixed systemd unit name is malformed")
    canonical_relative = canonical_path.relative_to(root).as_posix()
    expected_canonical = f"usr/lib/systemd/system/{unit_name}"
    if canonical_relative != expected_canonical:
        _reject("fixed systemd fragment is outside the canonical vendor unit path")
    for relative_dir in SYSTEMD_UNIT_SEARCH_PATHS:
        directory = _systemd_lstat_from_root(root, relative_dir, f"search path {relative_dir}")
        if directory is None:
            continue
        if not stat.S_ISDIR(directory.st_mode):
            _reject(f"systemd search path {relative_dir} is not a directory")
        if root == Path("/") and os.name == "posix" and (
            directory.st_uid != 0 or stat.S_IMODE(directory.st_mode) & 0o022
        ):
            _reject(f"systemd search path {relative_dir} is not root-owned and non-writable")
        candidate_relative = f"{relative_dir}/{unit_name}"
        candidate_info = _systemd_lstat_from_root(root, candidate_relative, f"unit override {candidate_relative}")
        if candidate_info is not None and candidate_relative != expected_canonical:
            _reject(f"higher-priority systemd unit override exists at {candidate_relative}")
        dropin_names = [f"{unit_name}.d"]
        if unit_name.endswith(".service"):
            dropin_names.append("service.d")
        for dropin_name in dropin_names:
            dropin_relative = f"{relative_dir}/{dropin_name}"
            dropin = _systemd_lstat_from_root(root, dropin_relative, f"drop-in directory {dropin_relative}")
            if dropin is None:
                continue
            if not stat.S_ISDIR(dropin.st_mode):
                _reject(f"systemd drop-in path {dropin_relative} is not a real directory")
            if root == Path("/") and os.name == "posix" and (
                dropin.st_uid != 0 or stat.S_IMODE(dropin.st_mode) & 0o022
            ):
                _reject(f"systemd drop-in path {dropin_relative} is not root-owned and non-writable")
            try:
                entries = tuple((Path(root) / dropin_relative).iterdir())
            except OSError as exc:
                raise OfflineVmBuildError(f"systemd drop-in path {dropin_relative} could not be read") from exc
            if entries:
                _reject(f"unreviewed systemd drop-in entry exists in {dropin_relative}")


_SYSTEMD_COMMON_PROPERTIES = ("LoadState", "FragmentPath", "DropInPaths", "NeedDaemonReload")
_SYSTEMD_SERVICE_PROPERTIES = (
    "User", "Group", "WorkingDirectory", "Environment", "EnvironmentFiles", "ExecStart",
    "ExecStartPre", "ExecStartPost", "ExecCondition", "ExecStop", "ExecStopPost",
    "NoNewPrivileges", "CapabilityBoundingSet", "AmbientCapabilities", "PrivateNetwork",
    "NetworkNamespacePath", "RestrictAddressFamilies", "ProtectSystem", "ProtectHome",
    "ReadOnlyPaths", "ReadWritePaths", "SuccessAction", "FailureAction", "OnFailure", "OnSuccess",
)
_SYSTEMD_SET_PROPERTIES = frozenset({
    "CapabilityBoundingSet", "AmbientCapabilities", "RestrictAddressFamilies",
    "ReadOnlyPaths", "ReadWritePaths", "Environment",
})


def _parse_systemctl_show(raw: bytes, expected_keys: Sequence[str]) -> dict[str, str]:
    if len(raw) > 256 * 1024:
        _reject("effective systemd property report exceeds its bound")
    try:
        lines = raw.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError as exc:
        raise OfflineVmBuildError("effective systemd property report is not UTF-8") from exc
    properties: dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if not separator or key not in expected_keys or key in properties:
            _reject("effective systemd property report has unknown or duplicate fields")
        properties[key] = value
    if set(properties) != set(expected_keys):
        _reject("effective systemd property report is incomplete")
    return properties


def _exec_start_prefix_matches(value: str, executable: str, argv: str) -> bool:
    if value.count("{") != 1 or value.count("}") != 1 or not value.rstrip().endswith("}"):
        return False
    match = re.match(
        r"^\{\s*path=(?P<path>[^ ;]+)\s*;\s*argv\[\]=(?P<argv>.*?)\s*;\s*ignore_errors=(?P<ignore>[^ ;]+)\s*;",
        value,
    )
    return bool(match and match.group("path") == executable and match.group("argv") == argv and match.group("ignore") == "no")


def _verify_effective_systemd_unit(
    unit_name: str,
    fragment_path: Path,
    *,
    expected: Mapping[str, str],
    runner: Callable[[Path, Sequence[str]], bytes] | None = None,
    root: Path = Path("/"),
) -> None:
    """Check manager-loaded fragment, drop-ins, and effective security properties."""
    _verify_no_systemd_override_files(unit_name, fragment_path, root=root)
    properties_to_read = list(_SYSTEMD_COMMON_PROPERTIES)
    if unit_name.endswith(".service"):
        properties_to_read.extend(_SYSTEMD_SERVICE_PROPERTIES)
    else:
        properties_to_read.append("Triggers")
    if runner is None:
        def run_query(executable: Path, args: Sequence[str]) -> bytes:
            return LinuxGuestBuildOperations._run_fixed_query(executable, args, timeout=10.0)
        runner = run_query
    args = ("show", "--no-pager", "--property=" + ",".join(properties_to_read), unit_name)
    raw = runner(Path("/usr/bin/systemctl"), args)
    properties = _parse_systemctl_show(raw, properties_to_read)
    common = {
        "LoadState": "loaded",
        "FragmentPath": str(fragment_path),
        "DropInPaths": "",
        "NeedDaemonReload": "no",
    }
    if unit_name.endswith(".path"):
        common["Triggers"] = expected["Triggers"]
    for key, value in {**common, **expected}.items():
        if key == "ExecStart":
            executable, separator, argv = value.partition(" ")
            if not separator or not _exec_start_prefix_matches(properties.get(key, ""), executable, value):
                _reject("effective systemd ExecStart differs from the fixed command")
            continue
        actual_value = properties.get(key, "")
        if key in _SYSTEMD_SET_PROPERTIES:
            actual_value = " ".join(sorted(actual_value.split()))
            value = " ".join(sorted(value.split()))
        if actual_value != value:
            _reject(f"effective systemd property {key} differs from the reviewed unit policy")


def _service_expected_properties(
    *,
    no_new_privileges: str,
    readonly_paths: str,
    success_action: str = "none",
    failure_action: str = "none",
) -> dict[str, str]:
    expected = {
        "User": "fwbuild",
        "Group": "fwbuild",
        "WorkingDirectory": "/",
        "Environment": "PATH=/usr/bin:/bin HOME=/run/librecore-worker LANG=C LC_ALL=C PYTHONDONTWRITEBYTECODE=1 PYTHONOPTIMIZE=0",
        "EnvironmentFiles": "",
        "ExecStartPre": "",
        "ExecStartPost": "",
        "ExecCondition": "",
        "ExecStop": "",
        "ExecStopPost": "",
        "NoNewPrivileges": no_new_privileges,
        "CapabilityBoundingSet": "CAP_SETUID CAP_SETGID",
        "AmbientCapabilities": "",
        "PrivateNetwork": "yes",
        "NetworkNamespacePath": "",
        "RestrictAddressFamilies": "AF_UNIX",
        "ProtectSystem": "strict",
        "ProtectHome": "yes",
        "ReadOnlyPaths": readonly_paths,
        "ReadWritePaths": "/run/librecore-scratch",
        "SuccessAction": success_action,
        "FailureAction": failure_action,
        "OnFailure": "",
        "OnSuccess": "",
    }
    return expected


def _verify_installed_service_unit(path: Path = Path(SYSTEMD_UNIT_PATH)) -> None:
    _verify_installed_systemd_resource(path, "librecore-offline-build.service", BUILD_SERVICE_UNIT_BYTES)
    _verify_effective_systemd_unit(
        "librecore-offline-build.service", path,
        expected={
            **_service_expected_properties(
                no_new_privileges="no",
                readonly_paths="/mnt/librecore-source /mnt/librecore-packages",
            ),
            "ExecStart": "/usr/bin/python -B -m usb_builder.offline_vm_build_service",
        },
    )


def _verify_installed_path_unit(path: Path = Path(SYSTEMD_PATH_UNIT_PATH)) -> None:
    _verify_installed_systemd_resource(path, "librecore-offline-build.path", BUILD_PATH_UNIT_BYTES)
    _verify_effective_systemd_unit(
        "librecore-offline-build.path", path,
        expected={"Triggers": "librecore-offline-build.service"},
    )


def _verify_installed_preflight_unit(path: Path = Path(SYSTEMD_PREFLIGHT_UNIT_PATH)) -> None:
    _verify_installed_systemd_resource(path, "librecore-offline-preflight.service", BUILD_PREFLIGHT_UNIT_BYTES)
    _verify_effective_systemd_unit(
        "librecore-offline-preflight.service", path,
        expected={
            **_service_expected_properties(
                no_new_privileges="yes",
                readonly_paths="/mnt/librecore-source /mnt/librecore-packages /dev/disk/by-id/virtio-LC-REQUEST-RO",
                success_action="poweroff",
                failure_action="poweroff",
            ),
            "ExecStart": "/usr/bin/python -B -m usb_builder.offline_vm_build_preflight",
        },
    )


def _verify_installed_preflight_path_unit(path: Path = Path(SYSTEMD_PREFLIGHT_PATH_UNIT_PATH)) -> None:
    _verify_installed_systemd_resource(path, "librecore-offline-preflight.path", BUILD_PREFLIGHT_PATH_UNIT_BYTES)
    _verify_effective_systemd_unit(
        "librecore-offline-preflight.path", path,
        expected={"Triggers": "librecore-offline-preflight.service"},
    )


def _load_service_result(invocation: BuildInvocation) -> GuestBuildResult:
    from usb_builder.offline_vm_build_service import _read_private_json

    payload, _ = _read_private_json(Path(BUILD_RESULT_PATH))
    ready_path = Path(BUILD_RESULT_READY_PATH)
    if not ready_path.exists() or ready_path.is_symlink():
        _reject("fixed build result has no matching final-ready receipt")
    ready, _ = _read_private_json(ready_path)
    started, _ = _read_private_json(Path(BUILD_SERVICE_STARTED_PATH))
    if started != {
        "format": BUILD_RECEIPT_FORMAT,
        "operation_id": invocation.operation_id,
        "request_sha256": invocation.request_sha256,
    }:
        _reject("fixed build result has no matching one-shot service-start receipt")
    if ready != {
        "format": BUILD_RESULT_READY_FORMAT,
        "operation_id": invocation.operation_id,
        "request_sha256": invocation.request_sha256,
        "result_sha256": _sha256_bytes(canonical_json(payload)),
    }:
        _reject("fixed build result has no matching final-ready receipt")
    expected_keys = {
        "format", "status", "operation_id", "request_sha256", "iso_path", "iso_size", "iso_sha256",
        "command_argv_sha256", "elapsed_seconds", "commands_run", "cleanup_complete",
    }
    if set(payload) != expected_keys or payload.get("format") != BUILD_RESULT_FORMAT:
        _reject("fixed build service result has unknown or missing fields")
    if payload.get("operation_id") != invocation.operation_id or payload.get("request_sha256") != invocation.request_sha256:
        _reject("fixed build service result is not bound to this operation")
    if payload.get("command_argv_sha256") != _invocation_sha256(invocation):
        _reject("fixed build service result command digest differs from the fixed invocation")
    status = payload.get("status")
    if status not in {"CANDIDATE_ISO_PENDING_HOST_VALIDATION", "INCOMPLETE_BUILD_EXECUTION", "INCOMPLETE_BUILD_CLEANUP"}:
        _reject("fixed build service returned an unknown outcome")
    iso_path = payload.get("iso_path")
    iso_size = payload.get("iso_size")
    iso_hash = payload.get("iso_sha256")
    if status == "CANDIDATE_ISO_PENDING_HOST_VALIDATION":
        if (
            not isinstance(iso_path, str)
            or Path(iso_path).parent != Path(WORKER_OUTPUT)
            or Path(iso_path).suffix != ".iso"
            or isinstance(iso_size, bool)
            or not isinstance(iso_size, int)
            or not 18 * 2048 <= iso_size <= MAX_ISO_BYTES
            or not isinstance(iso_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", iso_hash) is None
            or payload.get("cleanup_complete") is not True
        ):
            _reject("fixed build service candidate identity is malformed")
    elif iso_path is not None or iso_size != 0 or iso_hash is not None:
        _reject("incomplete fixed build service result cannot claim candidate bytes")
    elapsed = payload.get("elapsed_seconds")
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not 0 <= elapsed <= BUILD_TIMEOUT_SECONDS + SERVICE_START_GRACE_SECONDS:
        _reject("fixed build service elapsed time is malformed")
    commands = payload.get("commands_run")
    if commands != [BUILD_SERVICE_NAME]:
        _reject("fixed build service result records an unexpected command")
    return GuestBuildResult(
        status=status,
        operation_id=invocation.operation_id,
        request_sha256=invocation.request_sha256,
        iso_path=iso_path,
        iso_size=iso_size,
        iso_sha256=iso_hash,
        command_argv_sha256=_invocation_sha256(invocation),
        elapsed_seconds=float(elapsed),
        commands_run=(BUILD_SERVICE_NAME,),
        cleanup_complete=payload["cleanup_complete"],
    )


def activate_fixed_build_service(
    invocation: BuildInvocation,
    *,
    unit_path: Path = Path(SYSTEMD_UNIT_PATH),
    path_unit_path: Path = Path(SYSTEMD_PATH_UNIT_PATH),
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_interval_seconds: float = 0.25,
) -> GuestBuildResult:
    """Wait for the preinstalled path unit to run the byte-pinned NNP=0 service.

    The unprivileged preflight never asks systemd to start a privileged unit.
    A root-owned, enabled path unit observes the final invocation file and
    starts the one-shot service. The preflight accepts only its durable result.
    """
    expected = _expected_fixed_invocation(invocation.request_sha256, Path(WORKER_PACSTRAP_BIN) / "pacstrap", invocation.operation_id)
    if invocation != expected:
        _reject("fixed build service request differs from canonical invocation")
    _verify_installed_service_unit(unit_path)
    _verify_installed_path_unit(path_unit_path)
    from usb_builder.offline_vm_build_service import load_invocation

    stored, request = load_invocation()
    if stored != invocation:
        _reject("stored build invocation changed before fixed service activation")
    result_path = Path(BUILD_RESULT_PATH)
    ready_path = Path(BUILD_RESULT_READY_PATH)
    started_path = Path(BUILD_SERVICE_STARTED_PATH)
    if (
        result_path.exists() or result_path.is_symlink()
        or ready_path.exists() or ready_path.is_symlink()
        or started_path.exists() or started_path.is_symlink()
    ):
        _reject("fixed build service result/start path is not fresh")
    if poll_interval_seconds <= 0 or poll_interval_seconds > 1:
        _reject("fixed build service wait interval is outside its narrow bound")
    deadline = clock() + BUILD_TIMEOUT_SECONDS + SERVICE_START_GRACE_SECONDS
    while True:
        if ready_path.exists() or ready_path.is_symlink():
            result = _load_service_result(invocation)
            if result.status != "CANDIDATE_ISO_PENDING_HOST_VALIDATION":
                return result
            from usb_builder.offline_vm_build_service import candidate_commit_is_present

            if candidate_commit_is_present(request):
                return result
        if clock() >= deadline:
            raise OfflineVmBuildError("fixed build service exceeded its bounded path-triggered result timeout")
        sleep(min(poll_interval_seconds, max(0.0, deadline - clock())))


def run_preflight_and_systemd_build(
    request: Mapping[str, object],
    isolation: Any,
    preflight_security: GuestSecurityState,
    source_root: Path,
    package_root: Path,
    scratch_root: Path,
    operations: GuestBuildOperations,
    *,
    unit_path: Path = Path(SYSTEMD_UNIT_PATH),
    path_unit_path: Path = Path(SYSTEMD_PATH_UNIT_PATH),
) -> GuestBuildResult:
    """Run NNP=1 preflight, then activate the sole fixed NNP=0 guest unit."""
    _verify_installed_service_unit(unit_path)
    _verify_installed_path_unit(path_unit_path)
    invocation = prepare_guest_build(
        request, isolation, preflight_security, source_root, package_root, scratch_root, operations,
    )
    try:
        return activate_fixed_build_service(invocation, unit_path=unit_path, path_unit_path=path_unit_path)
    except (OSError, subprocess.SubprocessError, OfflineVmBuildError):
        try:
            operations.cleanup_overlay(Path(invocation.pacstrap_overlay))
        except (OSError, OfflineVmBuildError):
            pass
        raise


def execute_authorized_guest_build(
    invocation: BuildInvocation,
    service_state: GuestSecurityState,
    operations: GuestBuildOperations,
) -> GuestBuildResult:
    """Execute the fixed mkarchiso command from the separate NNP=0 guest unit.

    The service unit is the only intended caller.  The complete NNP=1
    preflight has already run and the request is one-shot claimed.  No command,
    path, environment entry, or timeout comes from the request.
    """
    if invocation != _expected_fixed_invocation(
        invocation.request_sha256,
        Path(invocation.pacstrap_overlay),
        invocation.operation_id,
    ):
        _reject("authorized build invocation differs from the fixed mkarchiso policy")
    validate_build_service_security(service_state)
    started = time.monotonic()
    elapsed = 0.0
    result_record = GuestBuildResult(
        status="INCOMPLETE_BUILD_EXECUTION",
        operation_id=invocation.operation_id,
        request_sha256=invocation.request_sha256,
        iso_path=None,
        iso_size=0,
        iso_sha256=None,
        command_argv_sha256=_invocation_sha256(invocation),
        elapsed_seconds=0.0,
        commands_run=(BUILD_SERVICE_NAME,),
        cleanup_complete=False,
    )
    cleanup_complete = False
    try:
        result = operations.run_fixed_builder(invocation)
        elapsed = time.monotonic() - started
        if (
            result.service_name != BUILD_SERVICE_NAME
            or result.argv_sha256 != _invocation_sha256(invocation)
            or result.no_new_privs_before is not True
            or result.no_new_privs_during is not False
            or result.network_namespace_before != service_state.network_namespace
            or result.network_namespace_during != service_state.network_namespace
            or result.timed_out
            or result.returncode != 0
            or not result.pacstrap_exit_codes
            or any(code != 0 for code in result.pacstrap_exit_codes)
            or result.account != "fwbuild"
            or result.uid != service_state.uid
            or result.gid != service_state.gid
            or result.supplementary_gids != (result.gid,)
            or any(value not in {"0", "00000000", "0000000000000000"} for value in (
                result.cap_effective, result.cap_permitted, result.cap_ambient,
            ))
            or result.cap_bounding != CAPABILITY_BOUNDING_MASK
            or result.interfaces != ("lo",)
            or result.ipv4_routes
            or result.ipv6_routes
        ):
            result_record = GuestBuildResult(
                status="INCOMPLETE_BUILD_EXECUTION",
                operation_id=invocation.operation_id,
                request_sha256=invocation.request_sha256,
                iso_path=None,
                iso_size=0,
                iso_sha256=None,
                command_argv_sha256=_invocation_sha256(invocation),
                elapsed_seconds=elapsed,
                commands_run=(BUILD_SERVICE_NAME,),
                cleanup_complete=False,
            )
        else:
            outputs = tuple(operations.list_iso_outputs(Path(WORKER_OUTPUT)))
            if len(outputs) != 1:
                _reject("fixed mkarchiso output must contain exactly one candidate ISO")
            candidate = outputs[0]
            if _path_has_symlink_component(candidate) or candidate.parent != Path(WORKER_OUTPUT):
                _reject("candidate ISO path is linked or outside the fixed output directory")
            info = candidate.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 18 * 2048 <= info.st_size <= MAX_ISO_BYTES:
                _reject("fixed mkarchiso output is not a bounded single-link regular ISO")
            digest = _sha256_path(candidate)
            result_record = GuestBuildResult(
                status="CANDIDATE_ISO_PENDING_HOST_VALIDATION",
                operation_id=invocation.operation_id,
                request_sha256=invocation.request_sha256,
                iso_path=candidate.as_posix(),
                iso_size=info.st_size,
                iso_sha256=digest,
                command_argv_sha256=_invocation_sha256(invocation),
                elapsed_seconds=elapsed,
                commands_run=(BUILD_SERVICE_NAME,),
                cleanup_complete=False,
            )
    except (OSError, subprocess.SubprocessError, OfflineVmBuildError):
        result_record = GuestBuildResult(
            status="INCOMPLETE_BUILD_EXECUTION",
            operation_id=invocation.operation_id,
            request_sha256=invocation.request_sha256,
            iso_path=None,
            iso_size=0,
            iso_sha256=None,
            command_argv_sha256=_invocation_sha256(invocation),
            elapsed_seconds=max(elapsed, time.monotonic() - started),
            commands_run=(BUILD_SERVICE_NAME,),
            cleanup_complete=False,
        )
    finally:
        try:
            operations.cleanup_overlay(Path(invocation.pacstrap_overlay))
            cleanup_complete = True
        except (OSError, OfflineVmBuildError):
            cleanup_complete = False
    if not cleanup_complete:
        return GuestBuildResult(
            status="INCOMPLETE_BUILD_CLEANUP",
            operation_id=invocation.operation_id,
            request_sha256=invocation.request_sha256,
            iso_path=None,
            iso_size=0,
            iso_sha256=None,
            command_argv_sha256=_invocation_sha256(invocation),
            elapsed_seconds=max(elapsed, time.monotonic() - started),
            commands_run=(BUILD_SERVICE_NAME,),
            cleanup_complete=False,
        )
    return replace(result_record, cleanup_complete=True)


def run_guest_build_once(
    request: Mapping[str, object],
    isolation: Any,
    preflight_security: GuestSecurityState,
    service_security: GuestSecurityState,
    source_root: Path,
    package_root: Path,
    scratch_root: Path,
    operations: GuestBuildOperations | None = None,
) -> GuestBuildResult:
    """Testable guest coordinator; production entry stays disabled by default."""
    if operations is None:
        return GuestBuildResult(
            "BUILD_BACKEND_UNAVAILABLE",
            str(request.get("operation_id", "")),
            _sha256_bytes(canonical_json(dict(request))),
            None, 0, None, None, 0.0, (), True,
        )
    invocation = prepare_guest_build(request, isolation, preflight_security, source_root, package_root, scratch_root, operations)
    result = execute_authorized_guest_build(invocation, service_security, operations)
    return GuestBuildResult(
        status=result.status,
        operation_id=str(request.get("operation_id", "")),
        request_sha256=result.request_sha256,
        iso_path=result.iso_path,
        iso_size=result.iso_size,
        iso_sha256=result.iso_sha256,
        command_argv_sha256=result.command_argv_sha256,
        elapsed_seconds=result.elapsed_seconds,
        commands_run=result.commands_run,
        cleanup_complete=result.cleanup_complete,
    )


@dataclass(frozen=True)
class HostCandidateReport:
    status: str
    operation_id: str
    request_sha256: str
    target_root_status: str
    package_inventory: tuple[str, ...]
    candidate_iso_sha256: str
    candidate_iso_size: int
    published: bool
    evidence_manifest_sha256: str | None = None
    worker_argv_sha256: str | None = None


def _claim_host_validation(operation_root: Path, operation_id: str, request_sha256: str) -> None:
    claim = operation_root / ".host-candidate-consumed"
    if operation_root.name != operation_id or _path_has_symlink_component(operation_root) or claim.exists() or claim.is_symlink():
        _reject("worker candidate is replayed or outside its exact operation directory")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(claim, flags, 0o600)
    try:
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        os.write(fd, canonical_json({"format": BUILD_RECEIPT_FORMAT, "request_sha256": request_sha256}))
        os.fsync(fd)
    finally:
        os.close(fd)


def validate_candidate_with_usb_builder(
    *,
    request: Mapping[str, object],
    worker_run: Any,
    output_volume: Path,
    operation_root: Path,
    source_root: Path,
    profile_root: Path,
    target_root: Path,
    wheel: Path,
    runner: Any,
) -> HostCandidateReport:
    """Independently validate, but never publish, one stopped guest ISO.

    The existing USB builder checks remain authoritative.  This adapter calls
    its current profile, target-root, and installed-package validators and
    returns only a pending report; candidate bundle publication is not part of
    this prototype.
    """
    parsed = parse_request(canonical_json(dict(request)))
    operation_id = str(parsed["operation_id"])
    request_sha = _sha256_bytes(canonical_json(dict(parsed)))
    if getattr(worker_run, "timed_out", True) or getattr(worker_run, "returncode", None) != 0:
        _reject("host candidate adapter requires a successful, stopped worker process")
    before = getattr(worker_run, "readonly_hashes_before", {})
    after = getattr(worker_run, "readonly_hashes_after", {})
    if not isinstance(before, Mapping) or not isinstance(after, Mapping) or before != after:
        _reject("host worker read-only inputs changed during execution")
    if getattr(worker_run, "candidate_iso_structurally_validated", False) is not True:
        _reject("host candidate adapter requires the stopped worker's structural ISO check")
    if operation_root.name != operation_id or output_volume.parent != operation_root:
        _reject("host candidate paths are not bound to the exact operation directory")
    expected_target = operation_root / "target-root"
    if target_root != expected_target or _path_has_symlink_component(target_root):
        _reject("host target root must be the exact non-linked operation-owned target-root path")
    if source_root != operation_root / "source" or profile_root != source_root / "usb_builder" / "archiso_profile":
        _reject("host source/profile paths are not bound to the exact operation-owned source image")
    if wheel.parent != operation_root or _path_has_symlink_component(wheel):
        _reject("host wheel path is outside the operation directory or linked")
    for path in (target_root, source_root, profile_root, wheel):
        try:
            info = path.lstat()
        except OSError as exc:
            raise OfflineVmBuildError("host candidate validation input is missing") from exc
        if stat.S_ISLNK(info.st_mode):
            _reject("host candidate validation input is linked")
    _claim_host_validation(operation_root, operation_id, request_sha)

    header = validate_result_volume(output_volume, parsed)
    candidate_iso_sha256 = header.get("iso_sha256")
    candidate_iso_size = header.get("iso_size")
    if (
        not isinstance(candidate_iso_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", candidate_iso_sha256) is None
        or isinstance(candidate_iso_size, bool)
        or not isinstance(candidate_iso_size, int)
        or not 0 < candidate_iso_size <= MAX_ISO_BYTES
    ):
        _reject("host candidate ISO identity is malformed")
    try:
        from usb_builder.offline_vm_guest_worker import validate_profile_inventory

        validate_profile_inventory(parsed, source_root)
        build_usb._profile_boot_modes(profile_root)
        build_usb._audit_profile_line_endings(profile_root, label="host candidate source profile")
        build_usb._verify_archiso_initramfs_config(profile_root, label="host candidate source")
        build_usb._verify_usb_initramfs_derivative(profile_root, label="host candidate source")
        target_install = build_usb._validate_target_root_install(target_root, wheel=wheel, runner=runner)
        package_names = tuple(line for line in (profile_root / "packages.x86_64").read_text(encoding="utf-8").splitlines() if line and not line.startswith("#"))
        package_inventory = build_usb._record_package_versions(target_root, runner, package_names)
    except (build_usb.BuildContractError, OSError, UnicodeError) as exc:
        raise OfflineVmBuildError(f"existing USB builder candidate validation failed: {exc}") from exc
    if target_install.get("status") != "APP_INSTALLED_AND_RUNTIME_CHECKED":
        _reject("existing USB builder target-root validation returned no installed-app evidence")
    return HostCandidateReport(
        status="TARGET_ROOT_AND_PROFILE_VALIDATED_PENDING_PROVENANCE_AND_PUBLICATION",
        operation_id=operation_id,
        request_sha256=request_sha,
        target_root_status=str(target_install["status"]),
        package_inventory=tuple(package_inventory),
        candidate_iso_sha256=candidate_iso_sha256,
        candidate_iso_size=candidate_iso_size,
        published=False,
    )


PACKAGE_EVIDENCE_FORMAT = "librecore-offline-vm-package-inventory-v1"
PROVENANCE_EVIDENCE_FORMAT = "librecore-offline-vm-provenance-v1"
TARGET_ROOT_INVENTORY_FORMAT = "librecore-target-root-inventory-v1"
MAX_HOST_EVIDENCE_RECORD_BYTES = 1024 * 1024


def _host_worker_evidence_context(
    *,
    request: Mapping[str, object],
    worker_run: Any,
    output_volume: Path,
    evidence_volume: Path,
    operation_root: Path,
) -> tuple[dict[str, object], str, dict[str, object], evidence_bundle.EvidenceBinding]:
    parsed = parse_request(canonical_json(dict(request)))
    operation_id = str(parsed["operation_id"])
    request_sha = _sha256_bytes(canonical_json(dict(parsed)))
    if getattr(worker_run, "timed_out", True) or getattr(worker_run, "returncode", None) != 0:
        _reject("host evidence adapter requires a successful, stopped worker process")
    if getattr(worker_run, "outcome", None) != "CANDIDATE_STRUCTURALLY_VALIDATED":
        _reject("host evidence adapter requires the worker's candidate-only outcome")
    before = getattr(worker_run, "readonly_hashes_before", {})
    after = getattr(worker_run, "readonly_hashes_after", {})
    if not isinstance(before, Mapping) or not isinstance(after, Mapping) or before != after:
        _reject("host worker read-only inputs changed during execution")
    source_value = parsed.get("source")
    packages_value = parsed.get("packages")
    snapshot_value = parsed.get("snapshot")
    profile_value = parsed.get("profile")
    if not isinstance(source_value, dict) or not isinstance(packages_value, dict) or not isinstance(snapshot_value, dict) or not isinstance(profile_value, dict):
        _reject("host evidence request bindings are malformed")
    source = source_value
    packages = packages_value
    snapshot = snapshot_value
    profile = profile_value
    required_observations = {
        "request": _sha256_bytes(encode_request_volume(parsed)),
        "source": source.get("sha256"),
        "packages": packages.get("sha256"),
        "snapshot_lock": snapshot.get("lock_sha256"),
    }
    for name, expected in required_observations.items():
        if not isinstance(expected, str) or before.get(name) != expected:
            _reject(f"host worker did not bind the requested read-only {name} input")
    if any(not isinstance(name, str) or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None for name, digest in before.items()):
        _reject("host worker input observations are malformed")
    worker_argv_sha = getattr(worker_run, "argv_sha256", None)
    if not isinstance(worker_argv_sha, str) or re.fullmatch(r"[0-9a-f]{64}", worker_argv_sha) is None:
        _reject("host worker QEMU command identity is malformed")
    if getattr(worker_run, "candidate_iso_structurally_validated", False) is not True:
        _reject("host evidence adapter requires the stopped worker's structural ISO check")
    if operation_root.name != operation_id or _path_has_symlink_component(operation_root):
        _reject("host evidence operation directory is linked or has the wrong operation identity")
    if output_volume != operation_root / "output.raw" or output_volume.parent != operation_root:
        _reject("host output path is not the exact operation-owned output volume")
    if evidence_volume != operation_root / "evidence.raw" or evidence_volume.parent != operation_root:
        _reject("host evidence path is not the exact operation-owned evidence volume")
    header = validate_result_volume(output_volume, parsed)
    candidate_iso_sha = header.get("iso_sha256")
    candidate_iso_size = header.get("iso_size")
    command_argv_sha = header.get("command_argv_sha256")
    if (
        not isinstance(candidate_iso_sha, str)
        or re.fullmatch(r"[0-9a-f]{64}", candidate_iso_sha) is None
        or isinstance(candidate_iso_size, bool)
        or not isinstance(candidate_iso_size, int)
        or not 0 < candidate_iso_size <= MAX_ISO_BYTES
        or not isinstance(command_argv_sha, str)
        or re.fullmatch(r"[0-9a-f]{64}", command_argv_sha) is None
    ):
        _reject("host output candidate identity is malformed")
    expected_invocation = _expected_fixed_invocation(
        request_sha,
        Path(WORKER_PACSTRAP_BIN) / "pacstrap",
        operation_id,
    )
    expected_command_sha = _invocation_sha256(expected_invocation)
    if command_argv_sha != expected_command_sha:
        _reject("host output candidate command digest differs from the fixed invocation")
    binding = evidence_bundle.EvidenceBinding(
        operation_id=operation_id,
        request_sha256=request_sha,
        source_sha256=str(source["sha256"]),
        package_sha256=str(packages["sha256"]),
        profile_sha256=str(profile["sha256"]),
        snapshot_lock_sha256=str(snapshot["lock_sha256"]),
        snapshot_date=str(snapshot["date"]),
        candidate_iso_sha256=candidate_iso_sha,
        candidate_iso_size=candidate_iso_size,
        command_argv_sha256=command_argv_sha,
        evidence_capacity=int(parsed["layout"]["evidence_capacity"]),  # type: ignore[index]
    )
    return parsed, request_sha, header, binding


def _validated_evidence_file(
    extraction_root: Path,
    bundle: evidence_bundle.VerifiedEvidenceBundle,
    role: str,
) -> tuple[evidence_bundle.VerifiedEntry, bytes]:
    matches = [entry for entry in bundle.entries if entry.role == role]
    if len(matches) != 1:
        _reject(f"host evidence bundle has no unique {role} entry")
    entry = matches[0]
    if entry.kind != "file" or entry.sha256 is None or entry.size > MAX_HOST_EVIDENCE_RECORD_BYTES:
        _reject(f"host evidence {role} entry has an unsupported type or size")
    path = _safe_file(extraction_root, entry.path, f"evidence {role}")
    try:
        info = path.lstat()
    except OSError as exc:
        raise OfflineVmBuildError(f"extracted {role} evidence is missing") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != entry.size:
        _reject(f"extracted {role} evidence changed type, link count, or size")
    raw = path.read_bytes()
    if _sha256_bytes(raw) != entry.sha256:
        _reject(f"extracted {role} evidence hash changed after extraction")
    return entry, raw


def _target_root_inventory_sha256(bundle: evidence_bundle.VerifiedEvidenceBundle) -> str:
    entries = [
        {
            "path": entry.path,
            "kind": entry.kind,
            "role": entry.role,
            "mode": entry.mode,
            "size": entry.size,
            "sha256": entry.sha256,
            "link_target": entry.link_target,
        }
        for entry in bundle.entries
        if entry.path == "target-root" or entry.path.startswith("target-root/")
    ]
    if not entries or entries[0]["path"] != "target-root":
        _reject("host evidence bundle has no target-root inventory")
    return _sha256_bytes(canonical_json({"format": TARGET_ROOT_INVENTORY_FORMAT, "entries": entries}))


def _parse_host_evidence_record(raw: bytes, label: str) -> dict[str, Any]:
    try:
        return evidence_bundle._parse_canonical_json(raw, label)
    except evidence_bundle.EvidenceBundleError as exc:
        raise OfflineVmBuildError(f"host {label} record is malformed: {exc}") from exc


def _validate_package_evidence(
    raw: bytes,
    *,
    binding: evidence_bundle.EvidenceBinding,
    package_image_sha256: str,
) -> tuple[str, ...]:
    record = _parse_host_evidence_record(raw, "package inventory")
    expected = {
        "format": PACKAGE_EVIDENCE_FORMAT,
        "operation_id": binding.operation_id,
        "request_sha256": binding.request_sha256,
        "package_image_sha256": package_image_sha256,
        "candidate_iso_sha256": binding.candidate_iso_sha256,
        "candidate_iso_size": binding.candidate_iso_size,
    }
    if set(record) != set(expected) | {"packages"} or any(record.get(key) != value for key, value in expected.items()):
        _reject("package inventory does not match the bound operation, request, package image, or ISO")
    raw_packages = record.get("packages")
    if (
        not isinstance(raw_packages, list)
        or not raw_packages
        or any(not isinstance(item, str) or re.fullmatch(r"[A-Za-z0-9@._+-]+\s+\S+", item) is None for item in raw_packages)
        or raw_packages != sorted(set(raw_packages))
    ):
        _reject("package inventory list is empty, malformed, duplicated, or unsorted")
    return tuple(raw_packages)


def _validate_provenance_evidence(
    raw: bytes,
    *,
    binding: evidence_bundle.EvidenceBinding,
    package_image_sha256: str,
    package_inventory_sha256: str,
    wheel_entry: evidence_bundle.VerifiedEntry,
    bundle: evidence_bundle.VerifiedEvidenceBundle,
) -> None:
    record = _parse_host_evidence_record(raw, "build provenance")
    expected = {
        "format": PROVENANCE_EVIDENCE_FORMAT,
        "operation_id": binding.operation_id,
        "request_sha256": binding.request_sha256,
        "source_sha256": binding.source_sha256,
        "package_image_sha256": package_image_sha256,
        "profile_inventory_sha256": binding.profile_sha256,
        "snapshot_lock_sha256": binding.snapshot_lock_sha256,
        "snapshot_date": binding.snapshot_date,
        "candidate_iso_sha256": binding.candidate_iso_sha256,
        "candidate_iso_size": binding.candidate_iso_size,
        "command_argv_sha256": binding.command_argv_sha256,
        "package_inventory_sha256": package_inventory_sha256,
        "wheel_sha256": wheel_entry.sha256,
        "wheel_size": wheel_entry.size,
        "target_root_inventory_sha256": _target_root_inventory_sha256(bundle),
    }
    if set(record) != set(expected) or record != expected:
        _reject("build provenance does not match the bound source, request, command, package, wheel, target root, or ISO")


def _run_existing_usb_candidate_checks(
    *,
    parsed_request: Mapping[str, object],
    source_root: Path,
    profile_root: Path,
    target_root: Path,
    wheel: Path,
    runner: Any,
) -> tuple[str, tuple[str, ...]]:
    try:
        from usb_builder.offline_vm_guest_worker import validate_profile_inventory

        validate_profile_inventory(parsed_request, source_root)
        _snapshot_lock(source_root, parsed_request)
        build_usb._profile_boot_modes(profile_root)
        build_usb._audit_profile_line_endings(profile_root, label="host candidate source profile")
        build_usb._verify_archiso_initramfs_config(profile_root, label="host candidate source")
        build_usb._verify_usb_initramfs_derivative(profile_root, label="host candidate source")
        target_install = build_usb._validate_target_root_install(target_root, wheel=wheel, runner=runner)
        package_names = _profile_package_names(source_root, parsed_request)
        package_inventory = build_usb._record_package_versions(target_root, runner, package_names)
    except (build_usb.BuildContractError, OSError, UnicodeError) as exc:
        raise OfflineVmBuildError(f"existing USB builder candidate validation failed: {exc}") from exc
    if target_install.get("status") != "APP_INSTALLED_AND_RUNTIME_CHECKED":
        _reject("existing USB builder target-root validation returned no installed-app evidence")
    return str(target_install["status"]), tuple(package_inventory)


def validate_evidence_bundle_with_usb_builder(
    *,
    request: Mapping[str, object],
    worker_run: Any,
    output_volume: Path,
    evidence_volume: Path,
    operation_root: Path,
    runner: Any,
) -> HostCandidateReport:
    """Extract and validate a stopped worker's evidence without publishing it.

    The evidence record schemas here are a prototype consistency contract. They
    do not authenticate the worker or prove a build independently; this result
    remains pending and cannot become a BuildResult.
    """
    operation_id = str(request.get("operation_id", ""))
    expected_target = operation_root / evidence_bundle.EXTRACTION_DIRECTORY / "target-root"
    expected_wheel = operation_root / evidence_bundle.EXTRACTION_DIRECTORY / "wheel.whl"
    source_root = operation_root / "source"
    profile_root = source_root / "usb_builder" / "archiso_profile"
    parsed, request_sha, header, binding = _host_worker_evidence_context(
        request=request,
        worker_run=worker_run,
        output_volume=output_volume,
        evidence_volume=evidence_volume,
        operation_root=operation_root,
    )
    if expected_target.is_symlink() or expected_wheel.is_symlink() or (operation_root / evidence_bundle.EXTRACTION_DIRECTORY).exists():
        _reject("evidence extraction destination is not fresh and unlinked")
    initial_bundle = evidence_bundle.verify_evidence_volume(evidence_volume, binding)
    if (
        initial_bundle.volume_size != header.get("evidence_size")
        or initial_bundle.volume_sha256 != header.get("evidence_sha256")
    ):
        _reject("evidence volume size or digest differs from the final output commit binding")
    _claim_host_validation(operation_root, operation_id, request_sha)
    extracted_bundle = evidence_bundle.extract_evidence_volume(evidence_volume, operation_root, binding)
    if extracted_bundle.manifest_sha256 != initial_bundle.manifest_sha256:
        _reject("evidence volume changed between verification and extraction")
    extraction_root = operation_root / evidence_bundle.EXTRACTION_DIRECTORY
    package_entry, package_raw = _validated_evidence_file(extraction_root, extracted_bundle, "package-inventory")
    provenance_entry, provenance_raw = _validated_evidence_file(extraction_root, extracted_bundle, "provenance")
    _, _command_status_raw = _validated_evidence_file(extraction_root, extracted_bundle, "command-status")
    wheel_entry, _wheel_raw = _validated_evidence_file(extraction_root, extracted_bundle, "wheel")
    package_values = parsed["packages"]
    if not isinstance(package_values, dict):
        _reject("request package image binding is malformed")
    reported_packages = _validate_package_evidence(
        package_raw,
        binding=binding,
        package_image_sha256=str(package_values["sha256"]),
    )
    target_root = extraction_root / "target-root"
    wheel = extraction_root / "wheel.whl"
    for path, expected_kind in ((source_root, "directory"), (profile_root, "directory"), (target_root, "directory"), (wheel, "file")):
        if _path_has_symlink_component(path):
            _reject("host candidate input path contains a symlink or is missing")
        try:
            info = path.lstat()
        except OSError as exc:
            raise OfflineVmBuildError("host candidate validation input is missing") from exc
        if (expected_kind == "directory" and not stat.S_ISDIR(info.st_mode)) or (expected_kind == "file" and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)):
            _reject("host candidate validation input has an unexpected type or link count")
    target_status, actual_packages = _run_existing_usb_candidate_checks(
        parsed_request=parsed,
        source_root=source_root,
        profile_root=profile_root,
        target_root=target_root,
        wheel=wheel,
        runner=runner,
    )
    if reported_packages != actual_packages:
        _reject("transported package inventory differs from the host-validated target-root inventory")
    _validate_provenance_evidence(
        provenance_raw,
        binding=binding,
        package_image_sha256=str(package_values["sha256"]),
        package_inventory_sha256=_sha256_bytes(package_raw),
        wheel_entry=wheel_entry,
        bundle=extracted_bundle,
    )
    final_bundle = evidence_bundle.verify_evidence_volume(evidence_volume, binding)
    evidence_bundle.verify_extracted_tree(extracted_bundle, operation_root)
    from usb_builder.offline_vm_guest_worker import validate_profile_inventory

    validate_profile_inventory(parsed, source_root)
    _snapshot_lock(source_root, parsed)
    final_header = validate_result_volume(output_volume, parsed)
    if (
        final_bundle.manifest_sha256 != initial_bundle.manifest_sha256
        or final_bundle.volume_size != header.get("evidence_size")
        or final_bundle.volume_sha256 != header.get("evidence_sha256")
        or final_header != header
        or package_entry.sha256 != _sha256_bytes(package_raw)
        or provenance_entry.sha256 != _sha256_bytes(provenance_raw)
    ):
        _reject("candidate or evidence bytes changed during host validation")
    return HostCandidateReport(
        status="USB_PROFILE_TARGET_ROOT_PACKAGES_AND_TRANSPORT_EVIDENCE_SELF_CONSISTENCY_CHECKED_PENDING_REVIEW_AND_PUBLICATION",
        operation_id=operation_id,
        request_sha256=request_sha,
        target_root_status=target_status,
        package_inventory=actual_packages,
        candidate_iso_sha256=binding.candidate_iso_sha256,
        candidate_iso_size=binding.candidate_iso_size,
        published=False,
        evidence_manifest_sha256=initial_bundle.manifest_sha256,
        worker_argv_sha256=str(getattr(worker_run, "argv_sha256")),
    )


def production_integration_status() -> str:
    """Return the non-deployment state used by callers and status surfaces."""
    return "OFFLINE_VM_WORKER_PROTOTYPE_UNAVAILABLE"
