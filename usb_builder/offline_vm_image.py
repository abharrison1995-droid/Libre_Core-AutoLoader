"""Host-only recipe and publication contract for a future offline VM image.

This module does not build an image and is not imported by the guest service
or production CLI.  The current repository deliberately lacks both a complete
signed worker-root package closure and a deployable rootless launcher pin, so
the public publication entry point always fails closed.  The lower-level
transaction helpers are exercised only with synthetic test fixtures.
"""

from __future__ import annotations

import hashlib
import json
import ctypes
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, NoReturn, Sequence

from usb_builder import offline_vm_worker
from usb_builder.offline_vm_worker import ImageIdentity, WorkerDeployment


RECIPE_FORMAT = "librecore-offline-vm-image-recipe-v2"
CANDIDATE_FORMAT = "librecore-offline-vm-image-candidate-v2"
MAX_INPUT_FILE_BYTES = 64 * 1024 * 1024
MAX_WORKER_IMAGE_BYTES = 64 * 1024 * 1024 * 1024
WORKER_IMAGE_FW_CFG_NAME = offline_vm_worker.WORKER_IMAGE_FW_CFG_NAME
ARCH_ISO_SEED_SHA256 = "684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5"

# Exact source closure for the fixed guest service and the resources it reads.
# offline_vm_image.py is host-side orchestration and intentionally is not an
# input to its own recipe.  A change to any listed source requires review and a
# deliberate pin refresh before this bundle validates.
INPUT_FILE_PINS: dict[str, str] = {
    "usb_builder/__init__.py": "0eea27447961dd910d5c4b0fdf39b3f5dc8400bce24a0ef96e23db86d3ce954f",
    "usb_builder/build_usb.py": "5379f768ff233ac3e9bd51298271042d700694ba215822c9b3575f118f8dfd7a",
    "usb_builder/mount_probe.py": "fbc25ee4d0be65f1d71080e34ea3f135e38e07320df681dbbb8b1acadd1c8de0",
    "usb_builder/network_probe.py": "07abaecf237c947141ce59534706d608ad92b7fb543240381e336e230b8d41ec",
    "usb_builder/offline_vm_build.py": "a70d0ee92fbd5c29566af87f4e3d60fee294e1b4abfff25fdf557139e0663151",
    "usb_builder/offline_vm_build_preflight.py": "6dd72b70141f69c9822c5a653bb66027ed33679ecf1ffdb2e9905f0c01b98320",
    "usb_builder/offline_vm_build_service.py": "006d4b7dee36defd68c8ec21b469aef7495691d2e207e13703b95f3db4a4d88b",
    "usb_builder/offline_vm_evidence.py": "6448eb965b15c53b67070014f71adf42560dfd973dda9f0a916bb67f80ca1dd6",
    "usb_builder/offline_vm_guest_worker.py": "59e65313a8bb747783da4c6e55a2980748ddcfb1506181f2c19c445f772d7a83",
    "usb_builder/offline_vm_package_closure.py": "7bee09492414c96c146da2873b7bf865da6c4f52a2cbaf79bdabd282e4869a81",
    "usb_builder/offline_vm_worker.py": "1d1e5578f855abec20da1a30421c47fa0220048459559c4272294c21aba9e42f",
    "usb_builder/rootless_mount_probe.py": "3f54f535fe0f013c7a50a29733dc0a9165afb4cefb47c793686e8e4dde7a71ce",
    "usb_builder/rootless_offline_launcher.c": "d3b81223eadff085ae927db00cc8a1b0982236a881e1c8700b39be807afc184d",
    "usb_builder/rootless_offline_preflight.py": "ca9a5c926e0810886015c657f778bddf82c12625b29bf3fb4ec898644e0a2398",
    "usb_builder/snapshot.lock.json": "5d66df5f5e6eacd0770399985b65507e84f208a7fbd1352faca13c0ead456cb1",
    "usb_builder/patches/arch-install-scripts-31-2-pacstrap-status.patch": "11394f8b901364236d449c35a84170eda29818bc9d41cbadd9677a04fddbceaa",
    "usb_builder/systemd/librecore-offline-build.path": "138acb90c8bd8ee21af24ce8a52b4b989c04ad83e516e95f8d8ff60670580e31",
    "usb_builder/systemd/librecore-offline-build.service": "ce07f89b760f7c279e418a1d523a5ff117b29fc7d67181d5b205837c320d4455",
    "usb_builder/systemd/librecore-offline-preflight.path": "a9c0a927a2bd566140411ecdaa37ff62cc7e6c97184de95675cafa3931b94977",
    "usb_builder/systemd/librecore-offline-preflight.service": "805a20f08f423021a0cd4ef3dfb6181b3adca118fe643d4c8a026179078ce59a",
}

SYSTEMD_UNIT_PATHS = (
    "usb_builder/systemd/librecore-offline-build.path",
    "usb_builder/systemd/librecore-offline-build.service",
    "usb_builder/systemd/librecore-offline-preflight.path",
    "usb_builder/systemd/librecore-offline-preflight.service",
)

# There is intentionally no value for either production pin.  The worker-root
# package closure must include every signed package/version/archive hash, and
# the launcher must be an independently reviewed deployable binary/policy.
PINNED_FULL_WORKER_ROOT_CLOSURE_SHA256: str | None = None
PINNED_DEPLOYABLE_LAUNCHER_SHA256: str | None = None


class OfflineVmImageError(RuntimeError):
    """An input, recipe, or candidate-image contract failed closed."""


class OfflineVmImageUnavailable(OfflineVmImageError):
    """The current tree lacks prerequisites for any worker-image candidate."""


def _fail(message: str) -> NoReturn:
    raise OfflineVmImageError(message)


def _fchmod(fd: int, mode: int) -> None:
    fchmod = getattr(os, "fchmod", None)
    if not callable(fchmod):
        _fail("descriptor mode setting is unavailable on this platform")
    fchmod(fd, mode)


def _effective_uid() -> int:
    geteuid = getattr(os, "geteuid", None)
    if not callable(geteuid):
        _fail("effective UID evidence is unavailable on this platform")
    return int(geteuid())


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return offline_vm_worker.canonical_json(value)


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _safe_root(root: Path) -> Path:
    if not root.is_absolute() or offline_vm_worker._path_has_symlink_component(root):
        _fail("input bundle root must be absolute and contain no symlink/junction components")
    try:
        info = root.lstat()
    except OSError as exc:
        raise OfflineVmImageError("input bundle root is unavailable") from exc
    if not stat.S_ISDIR(info.st_mode):
        _fail("input bundle root is not a real directory")
    return root


def _safe_relative(value: str) -> Path:
    if not value or "\\" in value or "\x00" in value or value.startswith("/"):
        _fail("input path is not a canonical relative POSIX path")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        _fail("input path is not a canonical relative POSIX path")
    return Path(*parts)


def _read_regular_bytes(path: Path, *, max_bytes: int, label: str) -> bytes:
    if offline_vm_worker._path_has_symlink_component(path):
        _fail(f"{label} has a symlink/junction path component")
    try:
        before = path.lstat()
    except OSError as exc:
        raise OfflineVmImageError(f"{label} is unavailable") from exc
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > max_bytes:
        _fail(f"{label} is not a bounded single-link regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise OfflineVmImageError(f"{label} cannot be opened without following links") from exc
    raw = bytearray()
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or (
            opened.st_dev, opened.st_ino, opened.st_size
        ) != (before.st_dev, before.st_ino, before.st_size):
            _fail(f"{label} changed while opening")
        while True:
            chunk = os.read(fd, min(1024 * 1024, max_bytes + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > max_bytes:
                _fail(f"{label} exceeds its bounded size")
        after = os.fstat(fd)
        final_path = path.lstat()
        identity_before = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        if identity_after != identity_before or (final_path.st_dev, final_path.st_ino) != (opened.st_dev, opened.st_ino):
            _fail(f"{label} changed while being read")
        if len(raw) != opened.st_size:
            _fail(f"{label} length changed while being read")
    finally:
        os.close(fd)
    return bytes(raw)


def _read_pinned_file(root: Path, rel: str, expected_sha256: str) -> tuple[int, str]:
    path = root / _safe_relative(rel)
    raw = _read_regular_bytes(path, max_bytes=MAX_INPUT_FILE_BYTES, label=f"input {rel}")
    size = len(raw)
    actual = _sha256(raw)
    if actual != expected_sha256:
        _fail(f"input SHA-256 differs from its reviewed pin: {rel}")
    return size, actual


def _enumerate_bundle_files(root: Path) -> set[str]:
    found: set[str] = set()
    allowed_dirs = {
        "/".join(parts[:index])
        for path in INPUT_FILE_PINS
        for parts in [path.split("/")]
        for index in range(1, len(parts))
    }
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        relative_dir = current_path.relative_to(root).as_posix()
        if relative_dir == ".":
            relative_dir = ""
        kept_dirs: list[str] = []
        for name in dirs:
            child = current_path / name
            rel = (Path(relative_dir) / name).as_posix() if relative_dir else name
            try:
                info = child.lstat()
            except OSError as exc:
                raise OfflineVmImageError(f"input directory is unavailable: {rel}") from exc
            if stat.S_ISLNK(info.st_mode) or (callable(getattr(child, "is_junction", None)) and child.is_junction()):
                _fail(f"input bundle contains a linked directory: {rel}")
            if not stat.S_ISDIR(info.st_mode) or rel not in allowed_dirs:
                _fail(f"input bundle contains an extra directory: {rel}")
            kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in files:
            path = current_path / name
            rel = (Path(relative_dir) / name).as_posix() if relative_dir else name
            try:
                info = path.lstat()
            except OSError as exc:
                raise OfflineVmImageError(f"input file is unavailable: {rel}") from exc
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                _fail(f"input bundle contains a linked or non-regular file: {rel}")
            found.add(rel)
    return found


def verify_input_bundle(root: Path) -> tuple[dict[str, object], ...]:
    """Verify the complete, exact, hash-pinned source/resource input closure."""
    _safe_root(root)
    found = _enumerate_bundle_files(root)
    expected_paths = set(INPUT_FILE_PINS)
    if found != expected_paths:
        missing = sorted(expected_paths - found)
        extra = sorted(found - expected_paths)
        _fail(f"input closure file set differs (missing={missing!r}, extra={extra!r})")
    records: list[dict[str, object]] = []
    for rel, expected in sorted(INPUT_FILE_PINS.items()):
        size, actual = _read_pinned_file(root, rel, expected)
        records.append({"path": rel, "size": size, "sha256": actual})
    return tuple(records)


def _snapshot_from_bundle(root: Path) -> tuple[dict[str, Any], str]:
    raw = _read_regular_bytes(
        root / "usb_builder" / "snapshot.lock.json",
        max_bytes=MAX_INPUT_FILE_BYTES,
        label="snapshot lock",
    )
    digest = _sha256(raw)
    if digest != INPUT_FILE_PINS["usb_builder/snapshot.lock.json"]:
        _fail("snapshot lock bytes differ from their reviewed input pin")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfflineVmImageError("snapshot lock is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict) or value.get("snapshot_date") != "2026-09-30":
        _fail("snapshot lock date/schema differs from the reviewed source snapshot")
    if value.get("repository_database_sha256") != {
        "core": "08d52f5e83f72a9e9f8464441da87b9a399f4decc7110fcfa3825f7c0af42dd2",
        "extra": "d2262e9def7afc76960c5242624cc14029afa47a9407acd84a29aed3a44f1663",
    }:
        _fail("snapshot repository database pins differ from the reviewed lock")
    rootless = value.get("rootless_builder")
    if not isinstance(rootless, dict):
        _fail("snapshot lock lacks the rootless builder package pins")
    overlay = rootless.get("pacstrap_status_overlay")
    if not isinstance(overlay, dict) or overlay != {
        "id": "arch-install-scripts-31-2-pacstrap-preserve-child-status-v1",
        "source_path": "/usr/bin/pacstrap",
        "source_sha256": "c0cbe2eb3e77a2a4437b03021ec1e63f31f20888778cd3c70ff49d2368371207",
        "patch_path": "usb_builder/patches/arch-install-scripts-31-2-pacstrap-status.patch",
        "patch_sha256": INPUT_FILE_PINS["usb_builder/patches/arch-install-scripts-31-2-pacstrap-status.patch"],
        "patched_sha256": "3964ed5b509f791e9f98cda494c4af5febe6daf0174df75b9bb10a7af3f2c04c",
        "path_policy": "operation-owned pacstrap directory first, then /usr/bin:/usr/sbin",
    }:
        _fail("snapshot lock pacstrap source/patch/output pins differ from the fixed overlay")
    return value, digest


def expected_builder_package_records(snapshot: Mapping[str, Any]) -> tuple[dict[str, object], ...]:
    rootless = snapshot.get("rootless_builder")
    if not isinstance(rootless, dict):
        _fail("snapshot lock lacks a rootless builder package table")
    values = (
        ("archiso", snapshot.get("archiso_version"), None, None),
        ("arch-install-scripts", rootless.get("arch_install_scripts_version"), rootless.get("arch_install_scripts_package_sha256"), rootless.get("arch_install_scripts_signature_sha256")),
        ("pacman", rootless.get("pacman_version"), rootless.get("pacman_package_sha256"), rootless.get("pacman_signature_sha256")),
        ("util-linux", rootless.get("util_linux_version"), rootless.get("util_linux_package_sha256"), rootless.get("util_linux_signature_sha256")),
        ("bash", rootless.get("bash_version"), rootless.get("bash_package_sha256"), rootless.get("bash_signature_sha256")),
        ("shadow", rootless.get("map_helper_package_version"), rootless.get("map_helper_package_sha256"), rootless.get("map_helper_signature_sha256")),
        ("libcap", rootless.get("libcap_version"), rootless.get("libcap_package_sha256"), rootless.get("libcap_signature_sha256")),
    )
    records: list[dict[str, object]] = []
    for name, version, archive_sha256, signature_sha256 in values:
        if not isinstance(version, str) or not version:
            _fail(f"snapshot lock lacks a fixed version for builder package {name}")
        if archive_sha256 is not None and not _is_sha256(archive_sha256):
            _fail(f"snapshot lock package archive digest is malformed for {name}")
        if signature_sha256 is not None and not _is_sha256(signature_sha256):
            _fail(f"snapshot lock package signature digest is malformed for {name}")
        records.append({
            "name": name,
            "version": version,
            "archive_sha256": archive_sha256,
            "signature_sha256": signature_sha256,
        })
    return tuple(records)


def validate_builder_package_records(value: object, snapshot: Mapping[str, Any]) -> tuple[dict[str, object], ...]:
    expected = expected_builder_package_records(snapshot)
    if not isinstance(value, list) or value != list(expected):
        _fail("builder package rows are missing, extra, reordered, or differ from locked version/archive/signature hashes")
    for record in value:
        if not isinstance(record, dict) or set(record) != {"name", "version", "archive_sha256", "signature_sha256"}:
            _fail("builder package row has unknown or missing fields")
    return expected


def _unit_record(root: Path, rel: str) -> dict[str, object]:
    raw = _read_regular_bytes(root / _safe_relative(rel), max_bytes=MAX_INPUT_FILE_BYTES, label=f"systemd resource {rel}")
    if _sha256(raw) != INPUT_FILE_PINS[rel]:
        _fail(f"systemd resource bytes differ from their reviewed pin: {rel}")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise OfflineVmImageError(f"systemd resource is not ASCII: {rel}") from exc
    properties: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"User", "Group", "NoNewPrivileges", "CapabilityBoundingSet", "PrivateNetwork"}:
            if key in properties:
                _fail(f"systemd resource repeats fixed policy property {key}: {rel}")
            properties[key] = value
    if rel.endswith(".service"):
        expected_nnp = "no" if "offline-build.service" in rel else "yes"
        expected = {
            "User": "fwbuild",
            "Group": "fwbuild",
            "NoNewPrivileges": expected_nnp,
            "CapabilityBoundingSet": "CAP_SETUID CAP_SETGID",
            "PrivateNetwork": "yes",
        }
        if properties != expected:
            _fail(f"systemd service differs from the fixed fwbuild/NPP/capability/network contract: {rel}")
    else:
        if properties:
            _fail(f"systemd path unit unexpectedly contains service execution policy: {rel}")
    return {
        "path": rel,
        "sha256": _sha256(raw),
        "kind": "service" if rel.endswith(".service") else "path-trigger",
        "user": properties.get("User"),
        "group": properties.get("Group"),
        "no_new_privileges": properties.get("NoNewPrivileges"),
        "capability_bounding_set": properties.get("CapabilityBoundingSet"),
        "private_network": properties.get("PrivateNetwork"),
    }


def _input_closure_digest(records: Sequence[Mapping[str, object]]) -> str:
    return _sha256(_canonical_json({"files": list(records)}))


def build_recipe_manifest(input_root: Path) -> dict[str, object]:
    """Return deterministic recipe evidence, never an image-build claim."""
    records = verify_input_bundle(input_root)
    snapshot, snapshot_sha = _snapshot_from_bundle(input_root)
    package_records = validate_builder_package_records(list(expected_builder_package_records(snapshot)), snapshot)
    systemd = [_unit_record(input_root, rel) for rel in SYSTEMD_UNIT_PATHS]
    if len(systemd) != 4 or {item["path"] for item in systemd} != set(SYSTEMD_UNIT_PATHS):
        _fail("recipe must bind exactly the four fixed systemd resources")
    closure_sha = _input_closure_digest(records)
    launcher_sha = INPUT_FILE_PINS["usb_builder/rootless_offline_launcher.c"]
    body: dict[str, object] = {
        "format": RECIPE_FORMAT,
        "status": "CANDIDATE_BUILD_UNAVAILABLE",
        "verified_source_input_closure": {
            "status": "HASH_VERIFIED",
            "sha256": closure_sha,
            "snapshot_lock_sha256": snapshot_sha,
            "files": list(records),
        },
        "builder_toolchain_pins": {
            "status": "PARTIAL_TOOLCHAIN_PINS_ONLY",
            "packages": list(package_records),
            "repository_database_sha256": snapshot["repository_database_sha256"],
            "signature_policy": snapshot.get("package_signature_policy"),
            "patch": {
                "path": "usb_builder/patches/arch-install-scripts-31-2-pacstrap-status.patch",
                "source_pacstrap_sha256": "c0cbe2eb3e77a2a4437b03021ec1e63f31f20888778cd3c70ff49d2368371207",
                "patched_pacstrap_sha256": "3964ed5b509f791e9f98cda494c4af5febe6daf0174df75b9bb10a7af3f2c04c",
                "patch_sha256": INPUT_FILE_PINS["usb_builder/patches/arch-install-scripts-31-2-pacstrap-status.patch"],
            },
            "worker_root_closure": {
                "status": "INCOMPLETE",
                "full_signed_archive_lock_sha256": PINNED_FULL_WORKER_ROOT_CLOSURE_SHA256,
                "reason": (
                    "full dated base/systemd/Python/kernel/mkinitcpio/initramfs runtime and "
                    "tool transitive package/version/archive/signature closure is not pinned"
                ),
            },
            "arch_iso_seed": {
                "sha256": ARCH_ISO_SEED_SHA256,
                "role": "read-only bootstrap seed only",
                "final_worker_root": False,
            },
            "retry8_root_observation": {
                "source": "docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry8",
                "status": "ISO root pacman was -3; host-only archiso 91-1 was absent from target root",
                "means_final_worker_root": False,
            },
        },
        "systemd_resources": systemd,
        "boot_contract": {
            "status": "UNAVAILABLE_UNPINNED_KERNEL_AND_INITRAMFS",
            "qemu_executable_identity": None,
            "kernel_image": None,
            "initramfs_image": None,
            "direct_kernel_arguments": {
                "kernel_option": "-kernel",
                "initramfs_option": "-initrd",
                "append": offline_vm_worker.WORKER_KERNEL_APPEND,
                "caller_arguments": "FORBIDDEN",
                "explicit_bios_option": False,
                "skips_disk_bootloader": True,
                "bypasses_default_x86_firmware": False,
            },
            "root_layout": {
                "partition_table": "gpt",
                "root_partition_number": offline_vm_worker.WORKER_ROOT_PARTITION_NUMBER,
                "partition_type_guid": offline_vm_worker.WORKER_ROOT_PARTITION_TYPE_GUID,
                "partuuid": offline_vm_worker.WORKER_ROOT_PARTUUID,
                "filesystem": offline_vm_worker.WORKER_ROOT_FILESYSTEM,
                "filesystem_uuid": offline_vm_worker.WORKER_ROOT_FILESYSTEM_UUID,
                "mount_mode": "read-only",
            },
            "initramfs_requirements": {
                "must_include_modules": ["virtio_pci", "virtio_blk", "ext4"],
                "must_mount_root_read_only": True,
                "generation_and_contents_validated": False,
            },
            "default_qemu_firmware": {
                "status": "UNRESOLVED_QEMU_DEFAULT_FIRMWARE_DEPENDENCY",
                "rom_set_identity_sha256": None,
                "note": "Direct kernel loading skips the disk bootloader but may still execute QEMU's default x86 firmware.",
            },
            "host_firmware_dependency": "QEMU default x86 ROM set is unresolved; SeaBIOS is not a guest package pin",
        },
        "fwbuild_identity_contract": {
            "status": "FIXED_CANDIDATE_CONTRACT_ONLY",
            "evidence_basis": "synthetic fixed-service/preflight contract fixtures; not observed in an image",
            "username": "fwbuild",
            "uid": 1000,
            "primary_group": "fwbuild",
            "gid": 1000,
            "supplementary_gids": [1000],
            "home": "/run/librecore-worker",
            "subuid_line": "fwbuild:100000:65536\n",
            "subgid_line": "fwbuild:100000:65536\n",
        },
        "launcher": {
            "status": "DEPLOYMENT_UNAVAILABLE",
            "source_path": "usb_builder/rootless_offline_launcher.c",
            "source_sha256": launcher_sha,
            "deployable_binary_sha256": PINNED_DEPLOYABLE_LAUNCHER_SHA256,
            "source_behavior": "stub returns EX_UNAVAILABLE before namespaces, mounts, or exec",
        },
        "fw_cfg": {
            "name": WORKER_IMAGE_FW_CFG_NAME,
            "digest_source": "external fixed QEMU argument from deployment.worker_image.sha256",
            "embedded_in_raw_image": False,
        },
        "image_build_provenance": {
            "status": "NOT_BUILT",
            "raw_image_sha256": None,
            "command_result": None,
            "boot_result": None,
        },
    }
    body["recipe_sha256"] = _sha256(_canonical_json(body))
    return body


def publication_blockers(recipe: Mapping[str, object]) -> tuple[str, ...]:
    blockers: list[str] = []
    package_info = recipe.get("builder_toolchain_pins")
    root_closure = package_info.get("worker_root_closure") if isinstance(package_info, dict) else None
    if (
        PINNED_FULL_WORKER_ROOT_CLOSURE_SHA256 is None
        or not isinstance(root_closure, dict)
        or root_closure.get("status") != "COMPLETE"
    ):
        blockers.append("complete signed worker-root transitive package/archive closure is not pinned")
    launcher = recipe.get("launcher")
    if (
        PINNED_DEPLOYABLE_LAUNCHER_SHA256 is None
        or not isinstance(launcher, dict)
        or launcher.get("deployable_binary_sha256") != PINNED_DEPLOYABLE_LAUNCHER_SHA256
    ):
        blockers.append("deployable rootless launcher binary and authorization policy are not pinned")
    boot = recipe.get("boot_contract")
    if not isinstance(boot, dict) or boot.get("status") != "PINNED_AND_VALIDATED":
        blockers.append("kernel/initramfs identities, root layout, and initramfs contents are not pinned and validated")
    if not isinstance(boot, dict) or boot.get("qemu_executable_identity") is None:
        blockers.append("QEMU executable identity is not pinned in the deployment recipe")
    firmware = boot.get("default_qemu_firmware") if isinstance(boot, dict) else None
    if not isinstance(firmware, dict) or firmware.get("status") != "PINNED_QEMU_DEFAULT_FIRMWARE":
        blockers.append("default x86 QEMU firmware/ROM identity is unresolved")
    return tuple(blockers)


def require_publication_ready(recipe: Mapping[str, object]) -> None:
    blockers = publication_blockers(recipe)
    if blockers:
        raise OfflineVmImageUnavailable("IMAGE_BUILD_UNAVAILABLE: " + "; ".join(blockers))


def _boot_asset_record(identity: ImageIdentity, label: str) -> dict[str, object]:
    if not isinstance(identity, ImageIdentity):
        _fail(f"candidate {label} ImageIdentity is required")
    if isinstance(identity.size, bool) or not isinstance(identity.size, int) or identity.size <= 0:
        _fail(f"candidate {label} size is invalid")
    if not _is_sha256(identity.sha256):
        _fail(f"candidate {label} SHA-256 is malformed")
    return {
        "source": "external immutable deployment asset",
        "size": identity.size,
        "sha256": identity.sha256,
    }


def _candidate_boot_contract(
    qemu_identity: ImageIdentity,
    kernel_image: ImageIdentity,
    initramfs_image: ImageIdentity,
) -> dict[str, object]:
    return {
        "qemu_executable": _boot_asset_record(qemu_identity, "QEMU executable"),
        "kernel": _boot_asset_record(kernel_image, "kernel"),
        "initramfs": _boot_asset_record(initramfs_image, "initramfs"),
        "root_layout": {
            "partition_table": "gpt",
            "root_partition_number": offline_vm_worker.WORKER_ROOT_PARTITION_NUMBER,
            "partition_type_guid": offline_vm_worker.WORKER_ROOT_PARTITION_TYPE_GUID,
            "partuuid": offline_vm_worker.WORKER_ROOT_PARTUUID,
            "filesystem": offline_vm_worker.WORKER_ROOT_FILESYSTEM,
            "filesystem_uuid": offline_vm_worker.WORKER_ROOT_FILESYSTEM_UUID,
            "mount_mode": "read-only",
        },
        "kernel_append": offline_vm_worker.WORKER_KERNEL_APPEND,
        "caller_arguments": "FORBIDDEN",
        "explicit_bios_option": False,
        "default_qemu_firmware": {
            "status": "UNRESOLVED_QEMU_DEFAULT_FIRMWARE_DEPENDENCY",
            "rom_set_identity_sha256": None,
            "direct_kernel_bypasses_firmware": False,
        },
    }


def _validate_candidate_manifest(
    value: object,
    *,
    recipe_sha256: str,
    input_closure_sha256: str,
    package_closure_sha256: str,
    launcher_sha256: str,
    qemu_identity: ImageIdentity,
    kernel_image: ImageIdentity,
    initramfs_image: ImageIdentity,
) -> dict[str, Any]:
    expected_keys = {
        "format", "status", "recipe_sha256", "input_closure_sha256",
        "package_closure_sha256", "launcher_sha256", "raw_image", "fw_cfg",
        "boot_contract", "build_provenance",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        _fail("candidate sidecar has unknown or missing fields")
    if value.get("format") != CANDIDATE_FORMAT or value.get("status") != "CANDIDATE_ONLY_PENDING_REVIEW":
        _fail("candidate sidecar is not the fixed pending-only format")
    for key, expected in (
        ("recipe_sha256", recipe_sha256),
        ("input_closure_sha256", input_closure_sha256),
        ("package_closure_sha256", package_closure_sha256),
        ("launcher_sha256", launcher_sha256),
    ):
        if not _is_sha256(expected) or value.get(key) != expected:
            _fail(f"candidate sidecar {key} is not bound to its reviewed pin")
    raw_image = value.get("raw_image")
    if not isinstance(raw_image, dict) or set(raw_image) != {"path", "format", "size", "sha256"}:
        _fail("candidate raw-image identity is malformed")
    if (
        raw_image.get("path") != "worker.raw"
        or raw_image.get("format") != "raw"
        or isinstance(raw_image.get("size"), bool)
        or not isinstance(raw_image.get("size"), int)
        or not 0 < raw_image["size"] <= MAX_WORKER_IMAGE_BYTES
        or not _is_sha256(raw_image.get("sha256"))
    ):
        _fail("candidate raw-image format, size, or digest is invalid")
    fw_cfg = value.get("fw_cfg")
    if not isinstance(fw_cfg, dict) or set(fw_cfg) != {"name", "value", "source", "embedded_in_raw_image"}:
        _fail("candidate fw_cfg handoff is malformed")
    if (
        fw_cfg.get("name") != WORKER_IMAGE_FW_CFG_NAME
        or fw_cfg.get("value") != raw_image["sha256"]
        or fw_cfg.get("source") != "external QEMU -fw_cfg argument"
        or fw_cfg.get("embedded_in_raw_image") is not False
    ):
        _fail("candidate fw_cfg digest is not the exact external raw-image identity")
    if value.get("boot_contract") != _candidate_boot_contract(qemu_identity, kernel_image, initramfs_image):
        _fail("candidate QEMU/kernel/initramfs identities or fixed boot contract differ from deployment")
    provenance = value.get("build_provenance")
    if not isinstance(provenance, dict) or set(provenance) != {
        "status", "builder_sha256", "snapshot_lock_sha256", "package_inventory_sha256", "created_utc"
    }:
        _fail("candidate build provenance is malformed")
    if (
        provenance.get("status") != "guest-reported-build-result-pending-host-review"
        or not _is_sha256(provenance.get("builder_sha256"))
        or not _is_sha256(provenance.get("snapshot_lock_sha256"))
        or not _is_sha256(provenance.get("package_inventory_sha256"))
        or not isinstance(provenance.get("created_utc"), str)
        or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
            provenance.get("created_utc", ""),
        ) is None
    ):
        _fail("candidate build provenance is missing a bounded identity")
    return value


def _candidate_manifest_for_contract(
    *,
    raw_size: int,
    raw_sha256: str,
    qemu_identity: ImageIdentity,
    kernel_image: ImageIdentity,
    initramfs_image: ImageIdentity,
    recipe_sha256: str,
    input_closure_sha256: str,
    package_closure_sha256: str,
    launcher_sha256: str,
    build_provenance: Mapping[str, object],
) -> dict[str, object]:
    """Build the exact sidecar shape for a validated pending candidate.

    The public path cannot currently obtain the required package/launcher
    pins. Tests use synthetic values to exercise the file-format machinery.
    """
    return {
        "format": CANDIDATE_FORMAT,
        "status": "CANDIDATE_ONLY_PENDING_REVIEW",
        "recipe_sha256": recipe_sha256,
        "input_closure_sha256": input_closure_sha256,
        "package_closure_sha256": package_closure_sha256,
        "launcher_sha256": launcher_sha256,
        "raw_image": {
            "path": "worker.raw",
            "format": "raw",
            "size": raw_size,
            "sha256": raw_sha256,
        },
        "fw_cfg": {
            "name": WORKER_IMAGE_FW_CFG_NAME,
            "value": raw_sha256,
            "source": "external QEMU -fw_cfg argument",
            "embedded_in_raw_image": False,
        },
        "boot_contract": _candidate_boot_contract(qemu_identity, kernel_image, initramfs_image),
        "build_provenance": dict(build_provenance),
    }


def _json_no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            _fail("candidate sidecar contains a duplicate JSON field")
        value[key] = item
    return value


def _open_candidate_artifact(
    candidate_dir: Path,
    qemu_identity: ImageIdentity,
    *,
    recipe_sha256: str,
    input_closure_sha256: str,
    package_closure_sha256: str,
    launcher_sha256: str,
    kernel_image: ImageIdentity,
    initramfs_image: ImageIdentity,
) -> WorkerDeployment:
    """Re-open a candidate through the existing immutable identity checks."""
    offline_vm_worker.verify_file_identity(qemu_identity, "QEMU")
    if not candidate_dir.is_absolute() or offline_vm_worker._path_has_symlink_component(candidate_dir):
        _fail("candidate directory is not absolute or has a linked path component")
    try:
        directory_info = candidate_dir.lstat()
    except OSError as exc:
        raise OfflineVmImageError("candidate directory is unavailable") from exc
    if not stat.S_ISDIR(directory_info.st_mode):
        _fail("candidate path is not a real directory")
    if os.name == "posix" and (
        directory_info.st_uid != _effective_uid() or stat.S_IMODE(directory_info.st_mode) != 0o700
    ):
        _fail("candidate directory owner/mode differs from the private candidate policy")
    try:
        names = {entry.name for entry in os.scandir(candidate_dir)}
    except OSError as exc:
        raise OfflineVmImageError("candidate directory cannot be enumerated") from exc
    if names != {"worker.raw", "manifest.json"}:
        _fail("candidate directory contains missing or extra files")
    manifest_path = candidate_dir / "manifest.json"
    manifest_info = _verify_candidate_mode(manifest_path, label="candidate manifest", mode=0o600)
    if manifest_info.st_size > 1024 * 1024:
        _fail("candidate manifest exceeds the fixed size bound")
    try:
        raw_manifest = _read_regular_bytes(
            manifest_path,
            max_bytes=1024 * 1024,
            label="candidate manifest",
        )
        parsed = json.loads(raw_manifest.decode("ascii"), object_pairs_hook=_json_no_duplicate_keys)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfflineVmImageError("candidate manifest is missing or malformed") from exc
    if not isinstance(parsed, dict) or raw_manifest != _canonical_json(parsed) + b"\n":
        _fail("candidate manifest bytes are not canonical JSON")
    validated = _validate_candidate_manifest(
        parsed,
        recipe_sha256=recipe_sha256,
        input_closure_sha256=input_closure_sha256,
        package_closure_sha256=package_closure_sha256,
        launcher_sha256=launcher_sha256,
        qemu_identity=qemu_identity,
        kernel_image=kernel_image,
        initramfs_image=initramfs_image,
    )
    image = validated["raw_image"]
    image_identity = ImageIdentity(candidate_dir / "worker.raw", image["size"], image["sha256"])
    _verify_candidate_mode(image_identity.path, label="candidate raw image", mode=0o600)
    offline_vm_worker.verify_file_identity(image_identity, "worker image")
    if _scan_for_digest(image_identity.path, image_identity.sha256):
        _fail("candidate raw image contains its own external fw_cfg digest bytes")
    offline_vm_worker.verify_file_identity(kernel_image, "worker kernel")
    offline_vm_worker.verify_file_identity(initramfs_image, "worker initramfs")
    return WorkerDeployment(
        qemu=qemu_identity,
        worker_image=image_identity,
        kernel_image=kernel_image,
        initramfs_image=initramfs_image,
        worker_image_format="raw",
    )


def _candidate_file_info(path: Path, *, label: str) -> os.stat_result:
    if not path.is_absolute() or offline_vm_worker._path_has_symlink_component(path):
        _fail(f"{label} path must be absolute with no symlink/junction components")
    try:
        info = path.lstat()
    except OSError as exc:
        raise OfflineVmImageError(f"{label} is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        _fail(f"{label} must be a single-link regular file")
    return info


def _scan_for_digest(path: Path, digest: str) -> bool:
    needle = digest.encode("ascii")
    overlap = b""
    before = _candidate_file_info(path, label="raw image")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    found = False
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (before.st_dev, before.st_ino, before.st_size):
            _fail("raw image changed before external-digest scan")
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            combined = overlap + chunk
            if needle in combined:
                found = True
            overlap = combined[-(len(needle) - 1):]
        after = os.fstat(fd)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
            opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns
        ):
            _fail("raw image changed during external-digest scan")
    finally:
        os.close(fd)
    return found


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        raise OfflineVmImageUnavailable("atomic directory fsync is unavailable on this host")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _rename_noreplace(source: Path, destination: Path) -> None:
    """Atomically rename a staged directory without replacing a target."""
    if not sys.platform.startswith("linux") or source.parent != destination.parent:
        raise OfflineVmImageUnavailable("atomic no-replace directory rename requires Linux renameat2")
    parent = source.parent
    try:
        parent_info = parent.lstat()
    except OSError as exc:
        raise OfflineVmImageError("candidate parent changed before atomic publication") from exc
    parent_fd = os.open(
        parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened_parent = os.fstat(parent_fd)
        if (parent_info.st_dev, parent_info.st_ino) != (opened_parent.st_dev, opened_parent.st_ino):
            _fail("candidate parent changed before atomic publication")
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise OfflineVmImageUnavailable("atomic no-replace renameat2 is unavailable")
        renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        renameat2.restype = ctypes.c_int
        result = renameat2(parent_fd, os.fsencode(source.name), parent_fd, os.fsencode(destination.name), 1)
        if result != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), os.fspath(destination))
    finally:
        os.close(parent_fd)


def _verify_candidate_mode(path: Path, *, label: str, mode: int) -> os.stat_result:
    info = _candidate_file_info(path, label=label)
    if os.name == "posix" and (info.st_uid != _effective_uid() or stat.S_IMODE(info.st_mode) != mode):
        _fail(f"{label} owner/mode differs from the private candidate policy")
    return info


PUBLICATION_STATE_FORMAT = "librecore-offline-vm-publication-state-v1"
PUBLICATION_PRE_RENAME = "PRE_RENAME"
PUBLICATION_RENAMED_UNCOMMITTED = "RENAMED_UNCOMMITTED"
PUBLICATION_COMMITTED_REOPENED = "COMMITTED_REOPENED"
_PUBLICATION_PHASES = frozenset(
    {PUBLICATION_PRE_RENAME, PUBLICATION_RENAMED_UNCOMMITTED, PUBLICATION_COMMITTED_REOPENED}
)


def _publication_paths(destination: Path) -> tuple[Path, Path, Path]:
    parent = destination.parent
    stem = f".{destination.name}.publish"
    return (
        parent / f"{stem}.lock",
        parent / f"{stem}.state.json",
        parent / f"{stem}.state.json.tmp",
    )


def _write_publication_state(state_path: Path, state: Mapping[str, Any], *, create: bool) -> None:
    """Durably replace the journal; leave failed temporary records for inspection."""
    temp_path = state_path.with_name(state_path.name + ".tmp")
    payload = _canonical_json(dict(state)) + b"\n"
    fd = os.open(
        temp_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        if os.name == "posix":
            _fchmod(fd, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write while storing candidate publication state")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    if create:
        _rename_noreplace(temp_path, state_path)
    else:
        _verify_candidate_mode(state_path, label="candidate publication state", mode=0o600)
        os.replace(temp_path, state_path)
    _fsync_directory(state_path.parent)


def inspect_candidate_publication(destination: Path) -> dict[str, object]:
    """Idempotently classify a private publication attempt without mutating it.

    This is transaction-state inspection only. A ``COMMITTED_REOPENED`` record
    says the publisher reopened the candidate before recording that phase; use
    the normal candidate verifier again before any later operation.
    """
    if not destination.is_absolute() or destination.name in {"", ".", ".."}:
        _fail("publication inspection requires an absolute candidate path")
    if offline_vm_worker._path_has_symlink_component(destination.parent):
        _fail("publication inspection parent contains a symlink/junction component")
    lock_path, state_path, temp_path = _publication_paths(destination)
    if temp_path.exists() or temp_path.is_symlink():
        return {"status": "RECOVERY_REQUIRED_STATE_UPDATE_INCOMPLETE", "lock_present": lock_path.exists()}
    if not state_path.exists() and not state_path.is_symlink():
        if destination.exists() or destination.is_symlink() or lock_path.exists() or lock_path.is_symlink():
            return {"status": "RECOVERY_REQUIRED_UNTRACKED_ATTEMPT", "lock_present": lock_path.exists()}
        return {"status": "NO_PUBLICATION", "lock_present": False}

    _verify_candidate_mode(state_path, label="candidate publication state", mode=0o600)
    try:
        raw = _read_regular_bytes(state_path, max_bytes=64 * 1024, label="candidate publication state")
        state = json.loads(raw.decode("ascii"), object_pairs_hook=_json_no_duplicate_keys)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfflineVmImageError("candidate publication state is unavailable or malformed") from exc
    expected_fields = {
        "format", "candidate", "phase", "stage", "directory_dev", "directory_ino", "manifest_sha256"
    }
    if not isinstance(state, dict) or set(state) != expected_fields or raw != _canonical_json(state) + b"\n":
        _fail("candidate publication state has an unknown or noncanonical schema")
    if state["format"] != PUBLICATION_STATE_FORMAT or state["candidate"] != destination.name:
        _fail("candidate publication state is not bound to this destination")
    phase = state["phase"]
    if not isinstance(phase, str) or phase not in _PUBLICATION_PHASES:
        _fail("candidate publication state has an unknown phase")
    if not _is_sha256(state["manifest_sha256"]):
        _fail("candidate publication state has a malformed manifest digest")
    stage_name = state["stage"]
    directory_dev = state["directory_dev"]
    directory_ino = state["directory_ino"]
    if stage_name is None:
        if directory_dev is not None or directory_ino is not None:
            _fail("candidate publication state has incomplete stage identity")
    elif (
        not isinstance(stage_name, str)
        or Path(stage_name).name != stage_name
        or not stage_name.startswith(f".{destination.name}.stage-")
        or type(directory_dev) is not int
        or type(directory_ino) is not int
        or directory_dev < 0
        or directory_ino <= 0
    ):
        _fail("candidate publication state has malformed stage identity")

    lock_info: os.stat_result | None = None
    try:
        lock_info = lock_path.lstat()
    except FileNotFoundError:
        pass
    if lock_info is not None and not stat.S_ISREG(lock_info.st_mode):
        _fail("candidate publication lock is not a regular file")

    stage_present = False
    if isinstance(stage_name, str):
        stage_path = destination.parent / stage_name
        try:
            stage_info = stage_path.lstat()
        except FileNotFoundError:
            pass
        else:
            stage_present = True
            if not stat.S_ISDIR(stage_info.st_mode) or (stage_info.st_dev, stage_info.st_ino) != (
                directory_dev, directory_ino
            ):
                return {
                    "status": "RECOVERY_REQUIRED_STAGE_IDENTITY_MISMATCH",
                    "lock_present": lock_info is not None,
                    "stage_present": True,
                }
    else:
        orphan_prefix = f".{destination.name}.stage-"
        if any(entry.name.startswith(orphan_prefix) for entry in destination.parent.iterdir()):
            return {
                "status": "RECOVERY_REQUIRED_UNBOUND_STAGE",
                "lock_present": lock_info is not None,
                "stage_present": True,
            }

    effective_phase = phase
    try:
        destination_info = destination.lstat()
    except FileNotFoundError:
        destination_info = None
    if destination_info is not None:
        if (
            not stat.S_ISDIR(destination_info.st_mode)
            or directory_dev is None
            or directory_ino is None
            or (destination_info.st_dev, destination_info.st_ino) != (directory_dev, directory_ino)
        ):
            return {
                "status": "RECOVERY_REQUIRED_DESTINATION_IDENTITY_MISMATCH",
                "lock_present": lock_info is not None,
                "stage_present": stage_present,
            }
        if phase == PUBLICATION_PRE_RENAME:
            # A crash may land between the atomic directory rename and the
            # durable journal transition. The inode binding distinguishes our
            # destination from a concurrent or unrelated directory.
            effective_phase = PUBLICATION_RENAMED_UNCOMMITTED
        manifest_path = destination / "manifest.json"
        try:
            manifest_bytes = _read_regular_bytes(
                manifest_path,
                max_bytes=1024 * 1024,
                label="published candidate manifest",
            )
        except (OSError, OfflineVmImageError):
            return {
                "status": "RECOVERY_REQUIRED_MANIFEST_UNAVAILABLE",
                "lock_present": lock_info is not None,
                "stage_present": stage_present,
            }
        if _sha256(manifest_bytes) != state["manifest_sha256"]:
            return {
                "status": "RECOVERY_REQUIRED_MANIFEST_MISMATCH",
                "lock_present": lock_info is not None,
                "stage_present": stage_present,
            }
    elif phase != PUBLICATION_PRE_RENAME:
        return {
            "status": "RECOVERY_REQUIRED_PUBLISHED_DIRECTORY_MISSING",
            "lock_present": lock_info is not None,
            "stage_present": stage_present,
        }

    return {
        "status": effective_phase,
        "lock_present": lock_info is not None,
        "stage_present": stage_present,
        "candidate_inode": (
            [destination_info.st_dev, destination_info.st_ino] if destination_info is not None else None
        ),
        "manifest_sha256": state["manifest_sha256"],
    }


def _publication_state_record(
    destination: Path,
    phase: str,
    stage: Path | None,
    stage_info: os.stat_result | None,
    manifest_sha256: str,
) -> dict[str, object]:
    return {
        "format": PUBLICATION_STATE_FORMAT,
        "candidate": destination.name,
        "phase": phase,
        "stage": stage.name if stage is not None and stage_info is not None else None,
        "directory_dev": stage_info.st_dev if stage_info is not None else None,
        "directory_ino": stage_info.st_ino if stage_info is not None else None,
        "manifest_sha256": manifest_sha256,
    }


def _publish_candidate_artifact(
    source_raw: Path,
    destination: Path,
    qemu_identity: ImageIdentity,
    manifest: Mapping[str, Any],
    *,
    recipe_sha256: str,
    input_closure_sha256: str,
    package_closure_sha256: str,
    launcher_sha256: str,
    kernel_image: ImageIdentity,
    initramfs_image: ImageIdentity,
) -> WorkerDeployment:
    """Atomic, same-parent candidate publisher used only after readiness proof.

    This private transaction helper does not authorize a build.  The public
    function checks the current pinned closure/launcher gates first; synthetic
    tests exercise this routine to verify durability and replay behavior.
    """
    offline_vm_worker.verify_file_identity(qemu_identity, "QEMU")
    offline_vm_worker.verify_file_identity(kernel_image, "worker kernel")
    offline_vm_worker.verify_file_identity(initramfs_image, "worker initramfs")
    if not destination.is_absolute() or destination.name in {"", ".", ".."}:
        _fail("candidate destination must be an absolute new directory path")
    parent = destination.parent
    if offline_vm_worker._path_has_symlink_component(parent):
        _fail("candidate parent contains a symlink/junction component")
    try:
        parent_info = parent.lstat()
    except OSError as exc:
        raise OfflineVmImageError("candidate parent is unavailable") from exc
    if not stat.S_ISDIR(parent_info.st_mode):
        _fail("candidate parent is not a real directory")
    if os.name == "posix" and (
        parent_info.st_uid != _effective_uid() or stat.S_IMODE(parent_info.st_mode) & 0o022
    ):
        _fail("candidate parent is not private to the current owner")
    if destination.exists() or destination.is_symlink():
        _fail("candidate destination already exists; replay is rejected")
    lock_path, state_path, state_temp_path = _publication_paths(destination)
    if any(path.exists() or path.is_symlink() for path in (lock_path, state_path, state_temp_path)):
        _fail("candidate publication state already exists; replay or recovery inspection is required")
    source_info = _candidate_file_info(source_raw, label="raw-image source")
    if source_info.st_size <= 0 or source_info.st_size > MAX_WORKER_IMAGE_BYTES:
        _fail("raw-image source size is outside the fixed bound")
    src_fd = os.open(source_raw, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(src_fd)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (source_info.st_dev, source_info.st_ino, source_info.st_size):
            _fail("raw-image source changed while opening")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(src_fd, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_WORKER_IMAGE_BYTES:
                _fail("raw-image source exceeds the fixed size bound")
            digest.update(chunk)
        after = os.fstat(src_fd)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
            opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns
        ):
            _fail("raw-image source changed while hashing")
    finally:
        os.close(src_fd)
    image_sha256 = digest.hexdigest()
    candidate_manifest = _validate_candidate_manifest(
        dict(manifest),
        recipe_sha256=recipe_sha256,
        input_closure_sha256=input_closure_sha256,
        package_closure_sha256=package_closure_sha256,
        launcher_sha256=launcher_sha256,
        qemu_identity=qemu_identity,
        kernel_image=kernel_image,
        initramfs_image=initramfs_image,
    )
    raw_identity = candidate_manifest["raw_image"]
    if size != raw_identity["size"] or image_sha256 != raw_identity["sha256"]:
        _fail("raw-image source bytes differ from the candidate sidecar identity")
    if _scan_for_digest(source_raw, image_sha256):
        _fail("raw image contains its own external fw_cfg digest bytes")
    manifest_bytes = _canonical_json(candidate_manifest) + b"\n"
    manifest_sha256 = _sha256(manifest_bytes)
    lock_fd: int | None = None
    stage: Path | None = None
    moved = False
    stage_info: os.stat_result | None = None
    lock_created = False
    state_write_pending = False
    try:
        lock_fd = os.open(
            lock_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        lock_created = True
        if any(path.exists() or path.is_symlink() for path in (destination, state_path, state_temp_path)):
            _fail("candidate destination or publication state appeared during publication; replay is rejected")
        state_write_pending = True
        _write_publication_state(
            state_path,
            _publication_state_record(destination, PUBLICATION_PRE_RENAME, None, None, manifest_sha256),
            create=True,
        )
        state_write_pending = False
        if destination.exists() or destination.is_symlink():
            _fail("candidate destination appeared during publication; replay is rejected")
        stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.stage-", dir=parent))
        if os.name == "posix":
            os.chmod(stage, 0o700)
        stage_info = stage.lstat()
        state_write_pending = True
        _write_publication_state(
            state_path,
            _publication_state_record(
                destination, PUBLICATION_PRE_RENAME, stage, stage_info, manifest_sha256
            ),
            create=False,
        )
        state_write_pending = False
        raw_out = stage / "worker.raw"
        manifest_out = stage / "manifest.json"
        in_fd = os.open(source_raw, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
        out_fd = os.open(
            raw_out,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        if os.name == "posix":
            _fchmod(out_fd, 0o600)
        copy_hash = hashlib.sha256()
        copied = 0
        try:
            copy_opened = os.fstat(in_fd)
            if (copy_opened.st_dev, copy_opened.st_ino, copy_opened.st_size) != (
                source_info.st_dev, source_info.st_ino, source_info.st_size
            ):
                _fail("raw-image source changed before its staged copy")
            while True:
                chunk = os.read(in_fd, 1024 * 1024)
                if not chunk:
                    break
                view = memoryview(chunk)
                while view:
                    written = os.write(out_fd, view)
                    if written <= 0:
                        raise OSError("short write while copying raw worker image")
                    copy_hash.update(view[:written])
                    copied += written
                    view = view[written:]
            copy_after = os.fstat(in_fd)
            if (copy_after.st_dev, copy_after.st_ino, copy_after.st_size, copy_after.st_mtime_ns, copy_after.st_ctime_ns) != (
                copy_opened.st_dev, copy_opened.st_ino, copy_opened.st_size, copy_opened.st_mtime_ns, copy_opened.st_ctime_ns
            ):
                _fail("raw-image source changed during its staged copy")
            os.fsync(out_fd)
        finally:
            os.close(in_fd)
            os.close(out_fd)
        if copied != size or copy_hash.hexdigest() != image_sha256:
            _fail("staged raw image differs from the hashed source")
        manifest_fd = os.open(
            manifest_out,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        if os.name == "posix":
            _fchmod(manifest_fd, 0o600)
        try:
            view = memoryview(manifest_bytes)
            while view:
                written = os.write(manifest_fd, view)
                if written <= 0:
                    raise OSError("short write while storing candidate manifest")
                view = view[written:]
            os.fsync(manifest_fd)
        finally:
            os.close(manifest_fd)
        _fsync_directory(stage)
        stage_info = stage.lstat()
        if destination.exists() or destination.is_symlink():
            _fail("candidate destination appeared before atomic publication")
        _rename_noreplace(stage, destination)
        moved = True
        state_write_pending = True
        _write_publication_state(
            state_path,
            _publication_state_record(
                destination,
                PUBLICATION_RENAMED_UNCOMMITTED,
                stage,
                stage_info,
                manifest_sha256,
            ),
            create=False,
        )
        state_write_pending = False
        deployment = _open_candidate_artifact(
            destination,
            qemu_identity,
            recipe_sha256=recipe_sha256,
            input_closure_sha256=input_closure_sha256,
            package_closure_sha256=package_closure_sha256,
            launcher_sha256=launcher_sha256,
            kernel_image=kernel_image,
            initramfs_image=initramfs_image,
        )
        state_write_pending = True
        _write_publication_state(
            state_path,
            _publication_state_record(
                destination,
                PUBLICATION_COMMITTED_REOPENED,
                stage,
                stage_info,
                manifest_sha256,
            ),
            create=False,
        )
        state_write_pending = False
        os.close(lock_fd)
        lock_fd = None
        lock_path.unlink()
        _fsync_directory(parent)
        return deployment
    except BaseException as primary_error:
        if not moved and stage_info is not None:
            try:
                observed = destination.lstat()
            except FileNotFoundError:
                observed = None
            if observed is not None and (observed.st_dev, observed.st_ino) == (
                stage_info.st_dev, stage_info.st_ino
            ):
                # renameat2 may complete before a wrapper, interruption, or
                # injected failure is observed by this frame.
                moved = True

        close_error: BaseException | None = None
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except BaseException as exc:
                close_error = exc
            lock_fd = None

        # Once rename may have happened, preserve the destination and journal
        # for explicit inspection. A journal write/fsync failure is also
        # uncertain, so retain its lock and any state/temp files.
        if moved or state_write_pending or close_error is not None:
            if close_error is not None:
                raise OfflineVmImageError(
                    f"candidate publication state is uncertain after {primary_error}; lock close failed: {close_error}"
                ) from close_error
            raise

        cleanup_error: BaseException | None = None
        if stage is not None:
            try:
                try:
                    observed_stage = stage.lstat()
                except FileNotFoundError:
                    observed_stage = None
                if observed_stage is not None:
                    if stage_info is None or not stat.S_ISDIR(observed_stage.st_mode) or (
                        observed_stage.st_dev, observed_stage.st_ino
                    ) != (stage_info.st_dev, stage_info.st_ino):
                        _fail("pre-rename stage identity changed; cleanup refused")
                    shutil.rmtree(stage)
                    _fsync_directory(parent)
            except BaseException as exc:
                cleanup_error = exc

        if cleanup_error is None and lock_created:
            try:
                lock_path.unlink()
                _fsync_directory(parent)
            except BaseException as exc:
                cleanup_error = exc

        if cleanup_error is not None:
            raise OfflineVmImageError(
                f"pre-rename publication failed ({primary_error}); cleanup or lock durability failed "
                f"({cleanup_error}); inspect the retained publication state"
            ) from cleanup_error
        raise


def publish_candidate(
    input_root: Path,
    source_raw: Path,
    destination: Path,
    *,
    qemu_identity: ImageIdentity,
    kernel_image: ImageIdentity,
    initramfs_image: ImageIdentity,
    build_provenance: Mapping[str, object],
) -> WorkerDeployment:
    """Fail closed until complete package closure and launcher pins exist."""
    recipe = build_recipe_manifest(input_root)
    _candidate_file_info(source_raw, label="raw-image source")
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        _fail("candidate destination must be an absolute path that does not already exist")
    offline_vm_worker.verify_file_identity(qemu_identity, "QEMU")
    offline_vm_worker.verify_file_identity(kernel_image, "worker kernel")
    offline_vm_worker.verify_file_identity(initramfs_image, "worker initramfs")
    if not isinstance(build_provenance, Mapping):
        _fail("candidate build provenance must be an exact mapping")
    require_publication_ready(recipe)
    # The current tree cannot reach this branch because both source pins below
    # are intentionally unset. A future reviewed update must supply a real
    # complete closure and launcher pin before creating a pending candidate.
    package_closure_sha256 = PINNED_FULL_WORKER_ROOT_CLOSURE_SHA256
    launcher_sha256 = PINNED_DEPLOYABLE_LAUNCHER_SHA256
    if package_closure_sha256 is None or launcher_sha256 is None:
        raise OfflineVmImageUnavailable("IMAGE_BUILD_UNAVAILABLE: required image pins are unset")
    image_sha256, image_size = _hash_candidate_source(source_raw)
    manifest = _candidate_manifest_for_contract(
        raw_size=image_size,
        raw_sha256=image_sha256,
        qemu_identity=qemu_identity,
        kernel_image=kernel_image,
        initramfs_image=initramfs_image,
        recipe_sha256=str(recipe["recipe_sha256"]),
        input_closure_sha256=str(recipe["verified_source_input_closure"]["sha256"]),  # type: ignore[index]
        package_closure_sha256=package_closure_sha256,
        launcher_sha256=launcher_sha256,
        build_provenance=build_provenance,
    )
    return _publish_candidate_artifact(
        source_raw,
        destination,
        qemu_identity,
        manifest,
        recipe_sha256=str(recipe["recipe_sha256"]),
        input_closure_sha256=str(recipe["verified_source_input_closure"]["sha256"]),  # type: ignore[index]
        package_closure_sha256=package_closure_sha256,
        launcher_sha256=launcher_sha256,
        kernel_image=kernel_image,
        initramfs_image=initramfs_image,
    )


def open_candidate(
    input_root: Path,
    candidate_dir: Path,
    qemu_identity: ImageIdentity,
    *,
    kernel_image: ImageIdentity,
    initramfs_image: ImageIdentity,
) -> WorkerDeployment:
    """Reopen a candidate only when current source/package/launcher pins are complete."""
    recipe = build_recipe_manifest(input_root)
    require_publication_ready(recipe)
    package_closure_sha256 = PINNED_FULL_WORKER_ROOT_CLOSURE_SHA256
    launcher_sha256 = PINNED_DEPLOYABLE_LAUNCHER_SHA256
    if package_closure_sha256 is None or launcher_sha256 is None:
        raise OfflineVmImageUnavailable("IMAGE_BUILD_UNAVAILABLE: required image pins are unset")
    closure = recipe["verified_source_input_closure"]
    if not isinstance(closure, dict):
        _fail("verified source closure is malformed")
    return _open_candidate_artifact(
        candidate_dir,
        qemu_identity,
        recipe_sha256=str(recipe["recipe_sha256"]),
        input_closure_sha256=str(closure["sha256"]),
        package_closure_sha256=package_closure_sha256,
        launcher_sha256=launcher_sha256,
        kernel_image=kernel_image,
        initramfs_image=initramfs_image,
    )


def _hash_candidate_source(path: Path) -> tuple[str, int]:
    """Hash a bounded single-link image without reading it into memory."""
    before = _candidate_file_info(path, label="raw-image source")
    if before.st_size <= 0 or before.st_size > MAX_WORKER_IMAGE_BYTES:
        _fail("raw-image source size is outside the fixed bound")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    size = 0
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (before.st_dev, before.st_ino, before.st_size):
            _fail("raw-image source changed while opening")
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_WORKER_IMAGE_BYTES:
                _fail("raw-image source exceeds the fixed size bound")
            digest.update(chunk)
        after = os.fstat(fd)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
            opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns
        ):
            _fail("raw-image source changed while hashing")
    finally:
        os.close(fd)
    return digest.hexdigest(), size
