"""Guest-side one-shot request validator for the sealed offline worker VM.

The checked-in worker intentionally has no build executor.  It validates the
versioned request and the expected isolated disk/network state, then returns
``BUILD_BACKEND_UNAVAILABLE``.  A separately reviewed, hash-pinned guest image
and native no-NIC proof are required before any executor can be added.
"""

from __future__ import annotations

import json
import os
import re
import stat
import ctypes
import hashlib
import importlib
import struct
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Mapping, Protocol, Sequence, cast

from usb_builder.offline_vm_worker import (
    REQUEST_FORMAT,
    OfflineVmContractError,
    RESULT_HEADER_SIZE,
    build_result_header,
    parse_request,
)

try:
    _fcntl_module: ModuleType | None = importlib.import_module("fcntl")
except ImportError:
    _fcntl_module = None


def _linux_ioctl(fd: int, request: int, argument: object, mutate: bool = False) -> object:
    """Run a Linux-only ioctl or fail closed on hosts without ``fcntl``."""
    ioctl = getattr(_fcntl_module, "ioctl", None) if _fcntl_module is not None else None
    if not callable(ioctl):
        raise OfflineVmContractError("Linux block-device ioctl support is unavailable")
    return ioctl(fd, request, argument, mutate)


REQUEST_PATH = Path("/dev/disk/by-id/virtio-LC-REQUEST-RO")
BLKGETSIZE64 = 0x80081272
DEVICE_IDS = {
    "request": "LC-REQUEST-RO",
    "source": "LC-SOURCE-RO",
    "packages": "LC-PACKAGES-RO",
    "scratch": "LC-SCRATCH-RW",
    "output": "LC-OUTPUT-RW",
    "evidence": "LC-EVIDENCE-RW",
}
MOUNT_TARGETS = {
    "source": "/mnt/librecore-source",
    "packages": "/mnt/librecore-packages",
    "scratch": "/run/librecore-scratch",
}
EVIDENCE_DEVICE_PATH = Path("/dev/disk/by-id/virtio-LC-EVIDENCE-RW")


class _EvidenceDeviceOperations(Protocol):
    """Narrow syscall seam used by the fixed evidence-device adapter tests."""

    def stat(self, path: Path) -> object: ...

    def open(self, path: Path, flags: int) -> int: ...

    def fstat(self, fd: int) -> object: ...

    def capacity(self, fd: int) -> int: ...

    def write(self, fd: int, capacity: int, binding: object, sources: Sequence[object]) -> tuple[int, str]: ...

    def close(self, fd: int) -> None: ...


class _LinuxEvidenceDeviceOperations:
    """Real fixed Linux block-device operations; no caller path/command input."""

    @staticmethod
    def stat(path: Path) -> os.stat_result:
        return path.stat()

    @staticmethod
    def open(path: Path, flags: int) -> int:
        return os.open(path, flags)

    @staticmethod
    def fstat(fd: int) -> os.stat_result:
        return os.fstat(fd)

    @staticmethod
    def capacity(fd: int) -> int:
        raw = _linux_ioctl(fd, BLKGETSIZE64, struct.pack("Q", 0))
        if not isinstance(raw, bytes) or len(raw) != 8:
            raise OfflineVmContractError("fixed evidence block capacity response is malformed")
        return struct.unpack("Q", raw)[0]

    @staticmethod
    def write(fd: int, capacity: int, binding: object, sources: Sequence[object]) -> tuple[int, str]:
        from usb_builder import offline_vm_evidence

        if not isinstance(binding, offline_vm_evidence.EvidenceBinding):
            raise OfflineVmContractError("fixed evidence binding has an unexpected type")
        if any(not isinstance(item, offline_vm_evidence.EvidenceSource) for item in sources):
            raise OfflineVmContractError("fixed evidence source inventory has an unexpected type")
        typed_sources = cast(Sequence[offline_vm_evidence.EvidenceSource], sources)
        return offline_vm_evidence.write_evidence_volume_fd(
            fd, capacity, binding, typed_sources
        )

    @staticmethod
    def close(fd: int) -> None:
        os.close(fd)


@dataclass(frozen=True)
class GuestMount:
    role: str
    device_id: str
    target: str
    filesystem: str
    readonly: bool
    device_major: int
    device_minor: int


@dataclass(frozen=True)
class GuestIsolation:
    worker_image_sha256: str
    interfaces: tuple[str, ...]
    ipv4_routes: tuple[str, ...]
    ipv6_routes: tuple[str, ...]
    mounts: tuple[GuestMount, ...]
    device_numbers: tuple[tuple[str, int, int], ...]
    device_capacities: tuple[tuple[str, int], ...]
    readonly_device_roles: tuple[str, ...]
    all_mounted_devices: tuple[tuple[int, int], ...]


def validate_guest_isolation(request: Mapping[str, object], state: GuestIsolation) -> None:
    parsed = parse_request(json.dumps(request, sort_keys=True, separators=(",", ":")).encode("ascii"))
    if parsed.get("format") != REQUEST_FORMAT:
        raise OfflineVmContractError("guest request format is unsupported")
    if state.worker_image_sha256 != parsed.get("worker_image_sha256"):
        raise OfflineVmContractError("guest worker image marker differs from the requested pinned image")
    if set(state.interfaces) != {"lo"}:
        raise OfflineVmContractError("worker guest has a non-loopback network interface")
    if state.ipv4_routes or state.ipv6_routes:
        raise OfflineVmContractError("worker guest has an IP route")
    by_role: dict[str, GuestMount] = {}
    for mount in state.mounts:
        if mount.role in by_role:
            raise OfflineVmContractError("worker guest reports duplicate mount roles")
        by_role[mount.role] = mount
    mounted_roles = {"source", "packages", "scratch"}
    if set(by_role) != mounted_roles:
        raise OfflineVmContractError("worker guest mount roles are incomplete or unexpected")
    seen_devices: set[tuple[int, int]] = set()
    for role in mounted_roles:
        expected_id = DEVICE_IDS[role]
        mount = by_role[role]
        if mount.device_id != expected_id or mount.target != MOUNT_TARGETS[role] or mount.filesystem != "ext4":
            raise OfflineVmContractError(f"worker {role} mount does not match its fixed device/target")
        expected_readonly = role in {"source", "packages"}
        if mount.readonly != expected_readonly:
            raise OfflineVmContractError(f"worker {role} mount has the wrong read-only policy")
        device_key = (mount.device_major, mount.device_minor)
        if device_key in seen_devices:
            raise OfflineVmContractError("worker guest aliases two disk roles to one block device")
        seen_devices.add(device_key)
    number_map = {role: (major, minor) for role, major, minor in state.device_numbers}
    if set(number_map) != set(DEVICE_IDS):
        raise OfflineVmContractError("worker guest block-device inventory is incomplete")
    if len(set(number_map.values())) != len(DEVICE_IDS):
        raise OfflineVmContractError("worker guest aliases two fixed disk roles to one block device")
    capacity_map = dict(state.device_capacities)
    if set(capacity_map) != set(DEVICE_IDS) or len(capacity_map) != len(state.device_capacities):
        raise OfflineVmContractError("worker guest block capacity inventory is incomplete or duplicated")
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in capacity_map.values()):
        raise OfflineVmContractError("worker guest block capacity inventory is malformed")
    source_binding = parsed.get("source")
    package_binding = parsed.get("packages")
    layout = parsed.get("layout")
    if not isinstance(source_binding, dict) or not isinstance(package_binding, dict) or not isinstance(layout, dict):
        raise OfflineVmContractError("worker guest request is missing fixed capacity bindings")
    expected_capacities = {
        "request": 2 * 1024 * 1024,
        "source": source_binding.get("size"),
        "packages": package_binding.get("size"),
        "output": layout.get("output_capacity"),
        "evidence": layout.get("evidence_capacity"),
    }
    if any(capacity_map.get(role) != expected for role, expected in expected_capacities.items()):
        raise OfflineVmContractError("worker guest block capacity differs from the exact request/QEMU volume map")
    for role, mount in by_role.items():
        if number_map[role] != (mount.device_major, mount.device_minor):
            raise OfflineVmContractError(f"worker {role} mount is not backed by its fixed block device")
    if set(state.readonly_device_roles) != {"request", "source", "packages"}:
        raise OfflineVmContractError("worker read-only block device policy differs from the fixed disk map")
    output_number = number_map["output"]
    evidence_number = number_map["evidence"]
    if output_number in state.all_mounted_devices or evidence_number in state.all_mounted_devices:
        raise OfflineVmContractError("output and evidence block devices must remain unmounted for fixed result/evidence writing")


def _validate_evidence_binding(
    request: Mapping[str, object],
    binding: object,
) -> None:
    """Bind the evidence writer to the exact parsed request before device I/O."""
    from usb_builder import offline_vm_evidence
    from usb_builder.offline_vm_worker import canonical_json

    if not isinstance(binding, offline_vm_evidence.EvidenceBinding):
        raise OfflineVmContractError("fixed evidence binding has an unexpected type")
    parsed = parse_request(canonical_json(dict(request)))
    source = parsed.get("source")
    packages = parsed.get("packages")
    profile = parsed.get("profile")
    snapshot = parsed.get("snapshot")
    layout = parsed.get("layout")
    if not all(isinstance(item, dict) for item in (source, packages, profile, snapshot, layout)):
        raise OfflineVmContractError("fixed evidence request identity is incomplete")
    assert isinstance(source, dict) and isinstance(packages, dict) and isinstance(profile, dict)
    assert isinstance(snapshot, dict) and isinstance(layout, dict)
    request_sha256 = hashlib.sha256(canonical_json(dict(parsed))).hexdigest()
    expected = {
        "operation_id": parsed.get("operation_id"),
        "request_sha256": request_sha256,
        "source_sha256": source.get("sha256"),
        "package_sha256": packages.get("sha256"),
        "profile_sha256": profile.get("sha256"),
        "snapshot_lock_sha256": snapshot.get("lock_sha256"),
        "snapshot_date": snapshot.get("date"),
        "evidence_capacity": layout.get("evidence_capacity"),
    }
    if any(getattr(binding, field) != value for field, value in expected.items()):
        raise OfflineVmContractError("fixed evidence binding differs from the exact worker request")
    if (
        re.fullmatch(r"[0-9a-f]{64}", binding.candidate_iso_sha256) is None
        or re.fullmatch(r"[0-9a-f]{64}", binding.command_argv_sha256) is None
        or isinstance(binding.candidate_iso_size, bool)
        or not isinstance(binding.candidate_iso_size, int)
        or binding.candidate_iso_size <= 0
    ):
        raise OfflineVmContractError("fixed evidence binding lacks a valid candidate ISO/command identity")


def _write_guest_evidence_volume_with_operations(
    request: Mapping[str, object],
    state: GuestIsolation,
    binding: object,
    sources: Sequence[object],
    *,
    operations: _EvidenceDeviceOperations,
) -> tuple[int, str]:
    """Validate then write via the exact unmounted LC-EVIDENCE-RW device.

    ``operations`` is an internal syscall seam for synthetic tests. Production
    code uses :func:`write_guest_evidence_volume`, which always constructs the
    fixed Linux implementation below.
    """
    from usb_builder import offline_vm_evidence
    from usb_builder.offline_vm_evidence import EvidenceBundleError

    validate_guest_isolation(request, state)
    _validate_evidence_binding(request, binding)
    if any(not isinstance(item, offline_vm_evidence.EvidenceSource) for item in sources):
        raise OfflineVmContractError("fixed evidence source inventory has an unexpected type")
    if not sources:
        raise OfflineVmContractError("fixed evidence source inventory is empty")
    capacity_map = dict(state.device_capacities)
    layout = request.get("layout")
    if not isinstance(layout, dict):
        raise OfflineVmContractError("fixed evidence request layout is malformed")
    expected_capacity = layout.get("evidence_capacity")
    if capacity_map.get("evidence") != expected_capacity:
        raise OfflineVmContractError("fixed evidence device capacity differs from request")
    number_map = {role: (major, minor) for role, major, minor in state.device_numbers}
    expected_number = number_map.get("evidence")
    if expected_number is None or expected_number == number_map.get("output"):
        raise OfflineVmContractError("fixed evidence device aliases the output device")

    try:
        before = operations.stat(EVIDENCE_DEVICE_PATH)
    except OSError as exc:
        raise OfflineVmContractError("fixed evidence block device is unavailable") from exc
    before_mode = getattr(before, "st_mode", 0)
    before_rdev = getattr(before, "st_rdev", -1)
    if not stat.S_ISBLK(before_mode):
        raise OfflineVmContractError("fixed evidence path is not a block device")
    major_fn = getattr(os, "major", None)
    minor_fn = getattr(os, "minor", None)
    if not callable(major_fn) or not callable(minor_fn) or (major_fn(before_rdev), minor_fn(before_rdev)) != expected_number:
        raise OfflineVmContractError("fixed evidence path differs from the probed operation device number")

    fd = -1
    try:
        fd = operations.open(EVIDENCE_DEVICE_PATH, os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
        opened = operations.fstat(fd)
        opened_rdev = getattr(opened, "st_rdev", -1)
        if (
            not stat.S_ISBLK(getattr(opened, "st_mode", 0))
            or opened_rdev != before_rdev
            or getattr(opened, "st_dev", None) != getattr(before, "st_dev", None)
            or getattr(opened, "st_ino", None) != getattr(before, "st_ino", None)
        ):
            raise OfflineVmContractError("fixed evidence block identity changed while opening")
        if (major_fn(opened_rdev), minor_fn(opened_rdev)) != expected_number:
            raise OfflineVmContractError("opened evidence block differs from the probed operation device number")
        actual_capacity = operations.capacity(fd)
        if actual_capacity != expected_capacity:
            raise OfflineVmContractError("fixed evidence block capacity differs from request/QEMU layout")
        logical_size, volume_sha256 = operations.write(fd, actual_capacity, binding, sources)
        after = operations.fstat(fd)
        current_link = operations.stat(EVIDENCE_DEVICE_PATH)
        if (
            not stat.S_ISBLK(getattr(after, "st_mode", 0))
            or getattr(after, "st_rdev", -1) != opened_rdev
            or getattr(after, "st_dev", None) != getattr(opened, "st_dev", None)
            or getattr(after, "st_ino", None) != getattr(opened, "st_ino", None)
            or getattr(current_link, "st_rdev", -1) != opened_rdev
            or getattr(current_link, "st_dev", None) != getattr(opened, "st_dev", None)
            or getattr(current_link, "st_ino", None) != getattr(opened, "st_ino", None)
            or operations.capacity(fd) != actual_capacity
        ):
            raise OfflineVmContractError("fixed evidence block identity/capacity changed during writing")
        if (
            isinstance(logical_size, bool)
            or not isinstance(logical_size, int)
            or not 0 < logical_size <= actual_capacity
            or re.fullmatch(r"[0-9a-f]{64}", volume_sha256) is None
        ):
            raise OfflineVmContractError("fixed evidence writer returned malformed readback identity")
        return logical_size, volume_sha256
    except EvidenceBundleError as exc:
        raise OfflineVmContractError(str(exc)) from exc
    finally:
        if fd >= 0:
            operations.close(fd)


def write_guest_evidence_volume(
    request: Mapping[str, object],
    state: GuestIsolation,
    binding: object,
    sources: Sequence[object],
) -> tuple[int, str]:
    """Write one complete bundle to the fixed, separately probed evidence disk.

    This helper is not called from the checked-in guest entrypoint or the
    candidate publication path. Its fixed 64 GiB ceiling is provisional and
    has not been measured against a native worker build.
    """
    return _write_guest_evidence_volume_with_operations(
        request,
        state,
        binding,
        sources,
        operations=_LinuxEvidenceDeviceOperations(),
    )


def validate_profile_inventory(request: Mapping[str, object], source_root: Path) -> None:
    profile = request.get("profile")
    if not isinstance(profile, dict) or not isinstance(profile.get("files"), list):
        raise OfflineVmContractError("worker request profile inventory is malformed")
    root = source_root.resolve(strict=True)
    for entry in profile["files"]:
        if not isinstance(entry, dict):
            raise OfflineVmContractError("worker profile inventory entry is malformed")
        relative = entry.get("path")
        if not isinstance(relative, str):
            raise OfflineVmContractError("worker profile inventory path is malformed")
        candidate = root.joinpath(*relative.split("/"))
        cursor = root
        for component in relative.split("/"):
            cursor = cursor / component
            if cursor.is_symlink():
                raise OfflineVmContractError("worker profile file path contains a symlink")
        try:
            info = candidate.lstat()
        except OSError as exc:
            raise OfflineVmContractError("worker profile file is missing") from exc
        if not stat.S_ISREG(info.st_mode) or info.st_size != entry.get("size"):
            raise OfflineVmContractError("worker profile file is not the exact-size regular file")
        digest = hashlib.sha256()
        with candidate.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != entry.get("sha256"):
            raise OfflineVmContractError("worker profile file hash differs from the pinned inventory")


def run_one_shot(request_bytes: bytes, state: GuestIsolation, source_root: Path) -> dict[str, object]:
    """Validate one request and return an unavailable result without running commands."""
    request = parse_request(request_bytes)
    validate_guest_isolation(request, state)
    validate_profile_inventory(request, source_root)
    return {
        "format": "librecore-offline-vm-guest-result-v1",
        "operation_id": request["operation_id"],
        "request_sha256": hashlib.sha256(request_bytes).hexdigest(),
        "status": "BUILD_BACKEND_UNAVAILABLE",
        "commands_run": [],
    }


def collect_fixed_build_output_sources(
    request: Mapping[str, object],
    result: object,
) -> tuple[Path, Path, tuple[object, ...]]:
    """Derive the only permitted airootfs and wheel after a fixed build.

    This is a transport helper only. It neither runs the build nor performs the
    host USB target-root checks. Missing, ambiguous, changed, or out-of-layout
    executor outputs fail before a caller can write an evidence volume.
    """
    from usb_builder import build_usb, offline_vm_evidence, offline_vm_build
    from usb_builder.offline_vm_worker import canonical_json

    parsed = parse_request(canonical_json(dict(request)))
    operation_id = str(parsed["operation_id"])
    request_sha256 = hashlib.sha256(canonical_json(dict(parsed))).hexdigest()
    expected_invocation = offline_vm_build._expected_fixed_invocation(
        request_sha256,
        Path(offline_vm_build.WORKER_PACSTRAP_BIN) / "pacstrap",
        operation_id,
    )
    iso_path_value = getattr(result, "iso_path", None)
    iso_size = getattr(result, "iso_size", None)
    iso_sha256 = getattr(result, "iso_sha256", None)
    if (
        getattr(result, "status", None) != "CANDIDATE_ISO_PENDING_HOST_VALIDATION"
        or getattr(result, "operation_id", None) != operation_id
        or getattr(result, "request_sha256", None) != request_sha256
        or getattr(result, "command_argv_sha256", None) != offline_vm_build._invocation_sha256(expected_invocation)
        or getattr(result, "cleanup_complete", None) is not True
        or not isinstance(iso_path_value, str)
        or isinstance(iso_size, bool)
        or not isinstance(iso_size, int)
        or not 0 < iso_size <= offline_vm_build.MAX_ISO_BYTES
        or not isinstance(iso_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", iso_sha256) is None
    ):
        raise OfflineVmContractError("fixed build result is incomplete or not bound to this request")
    candidate = Path(iso_path_value)
    output_root = Path(offline_vm_build.WORKER_OUTPUT)
    if candidate.parent != output_root or not candidate.is_absolute() or offline_vm_build._path_has_symlink_component(candidate):
        raise OfflineVmContractError("fixed build ISO is outside the operation-owned output directory")
    try:
        candidate_info = candidate.lstat()
    except OSError as exc:
        raise OfflineVmContractError("fixed build ISO output is missing") from exc
    if (
        not stat.S_ISREG(candidate_info.st_mode)
        or candidate_info.st_nlink != 1
        or candidate_info.st_size != iso_size
        or offline_vm_build._sha256_path(candidate) != iso_sha256
    ):
        raise OfflineVmContractError("fixed build ISO output changed after executor validation")

    target_root = Path(offline_vm_build.WORKER_WORK) / "x86_64" / "airootfs"
    if offline_vm_build._path_has_symlink_component(target_root):
        raise OfflineVmContractError("fixed mkarchiso airootfs path contains a symlink")
    try:
        target_info = target_root.lstat()
    except OSError as exc:
        raise OfflineVmContractError("fixed mkarchiso airootfs output is missing") from exc
    if not stat.S_ISDIR(target_info.st_mode):
        raise OfflineVmContractError("fixed mkarchiso airootfs output is not a real directory")
    wheel_dir = target_root / "opt" / "autoloader" / "wheelhouse"
    try:
        wheel = build_usb._one_wheel(wheel_dir)
        wheel_info = wheel.lstat()
    except (build_usb.BuildContractError, OSError) as exc:
        raise OfflineVmContractError("fixed target root has no unique application wheel") from exc
    if (
        offline_vm_build._path_has_symlink_component(wheel)
        or not stat.S_ISREG(wheel_info.st_mode)
        or wheel_info.st_nlink != 1
        or not build_usb._is_within(wheel, target_root)
    ):
        raise OfflineVmContractError("fixed target-root wheel is linked or outside the airootfs")
    try:
        target_sources = offline_vm_evidence.collect_target_root_sources(target_root)
    except offline_vm_evidence.EvidenceBundleError as exc:
        raise OfflineVmContractError(f"fixed target-root evidence inventory is unsafe: {exc}") from exc
    wheel_source = offline_vm_evidence.EvidenceSource(
        "wheel.whl", "wheel", stat.S_IMODE(wheel_info.st_mode) & 0o777, "file", wheel,
    )
    sources = tuple(sorted((*target_sources, wheel_source), key=lambda item: item.path))
    return target_root, wheel, sources


def _write_unavailable_header(output_device: Path, request: Mapping[str, object]) -> None:
    """Write only the fixed result slot to the dedicated output block device."""
    link_info = output_device.stat()
    if not stat.S_ISBLK(link_info.st_mode):
        raise OfflineVmContractError("fixed output path is not a block device")
    flags = os.O_WRONLY | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(output_device, flags)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISBLK(opened.st_mode) or getattr(opened, "st_rdev") != getattr(link_info, "st_rdev"):
            raise OfflineVmContractError("output block identity changed while opening")
        payload = build_result_header(request, status="BUILD_BACKEND_UNAVAILABLE")
        if len(payload) != RESULT_HEADER_SIZE:
            raise OfflineVmContractError("worker result header has an unexpected size")
        offset = 0
        while offset < len(payload):
            pwrite = getattr(os, "pwrite", None)
            if not callable(pwrite):
                raise OfflineVmContractError("positional block writes are unavailable")
            written = pwrite(fd, payload[offset:], offset)
            if written <= 0:
                raise OfflineVmContractError("worker result header write was incomplete")
            offset += written
        os.fsync(fd)
    finally:
        os.close(fd)


def _power_off_guest() -> None:
    """Power off this disposable guest after its one-shot result is durable."""
    sync = getattr(os, "sync", None)
    if not callable(sync):
        raise OfflineVmContractError("guest filesystem sync is unavailable")
    sync()
    libc = ctypes.CDLL(None, use_errno=True)
    # Linux RB_POWER_OFF, available only within the disposable guest.
    result = libc.reboot(0x4321FEDC)
    error = ctypes.get_errno()
    if result != 0:
        raise OSError(error, "guest poweroff syscall failed")


def _read_mount_inventory(path: Path = Path("/proc/self/mountinfo")) -> tuple[GuestMount, ...]:
    mounts: list[GuestMount] = []
    wanted = {target: role for role, target in MOUNT_TARGETS.items()}
    for line in path.read_text(encoding="utf-8").splitlines():
        before, separator, after = line.partition(" - ")
        if not separator:
            continue
        fields = before.split()
        tail = after.split()
        if len(fields) < 6 or len(tail) < 3:
            continue
        target = fields[4].replace("\\040", " ")
        role = wanted.get(target)
        if role is None:
            continue
        major_text, colon, minor_text = fields[2].partition(":")
        if not colon or not major_text.isdecimal() or not minor_text.isdecimal():
            raise OfflineVmContractError("worker mountinfo has an invalid device number")
        options = set(fields[5].split(","))
        mounts.append(
            GuestMount(
                role=role,
                device_id=DEVICE_IDS[role],
                target=target,
                filesystem=tail[0],
                readonly="ro" in options,
                device_major=int(major_text),
                device_minor=int(minor_text),
            )
        )
    return tuple(mounts)


def _read_all_mounted_devices(path: Path = Path("/proc/self/mountinfo")) -> tuple[tuple[int, int], ...]:
    result: list[tuple[int, int]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) < 3:
            raise OfflineVmContractError("worker mountinfo contains a malformed row")
        major_text, colon, minor_text = fields[2].partition(":")
        if not colon or not major_text.isdecimal() or not minor_text.isdecimal():
            raise OfflineVmContractError("worker mountinfo has an invalid device number")
        result.append((int(major_text), int(minor_text)))
    return tuple(result)


def collect_guest_isolation() -> GuestIsolation:
    """Read fixed Linux kernel views; never shells out or reads caller paths."""
    net_root = Path("/sys/class/net")
    interfaces = tuple(sorted(item.name for item in net_root.iterdir()))
    ipv4_lines = Path("/proc/net/route").read_text(encoding="ascii").splitlines()[1:]
    ipv6_lines = tuple(line for line in Path("/proc/net/ipv6_route").read_text(encoding="ascii").splitlines() if line.strip())
    mounts = _read_mount_inventory()
    all_mounted_devices = _read_all_mounted_devices()
    device_numbers: list[tuple[str, int, int]] = []
    device_capacities: list[tuple[str, int]] = []
    readonly_roles: list[str] = []
    for role, device_id in DEVICE_IDS.items():
        link = Path("/dev/disk/by-id") / f"virtio-{device_id}"
        try:
            info = link.stat()
        except OSError as exc:
            raise OfflineVmContractError(f"worker block device {device_id} is unavailable") from exc
        if not stat.S_ISBLK(info.st_mode):
            raise OfflineVmContractError(f"worker device {device_id} is not a block device")
        major_fn = getattr(os, "major", None)
        minor_fn = getattr(os, "minor", None)
        if not callable(major_fn) or not callable(minor_fn):
            raise OfflineVmContractError("block device number helpers are unavailable")
        device_number = getattr(info, "st_rdev")
        major, minor = major_fn(device_number), minor_fn(device_number)
        device_numbers.append((role, major, minor))
        try:
            fd = os.open(link, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        except OSError as exc:
            raise OfflineVmContractError(f"worker block device {device_id} cannot be opened for identity inspection") from exc
        try:
            opened = os.fstat(fd)
            if not stat.S_ISBLK(opened.st_mode) or getattr(opened, "st_rdev") != device_number:
                raise OfflineVmContractError(f"worker block device {device_id} changed while opening")
            capacity_buffer = bytearray(8)
            try:
                _linux_ioctl(fd, BLKGETSIZE64, capacity_buffer, True)
            except OSError as exc:
                raise OfflineVmContractError(f"worker block capacity is unavailable for {device_id}") from exc
            capacity = struct.unpack("Q", capacity_buffer)[0]
            if capacity <= 0:
                raise OfflineVmContractError(f"worker block capacity is invalid for {device_id}")
            device_capacities.append((role, capacity))
            reopened = os.fstat(fd)
            if (reopened.st_dev, reopened.st_ino, getattr(reopened, "st_rdev")) != (
                opened.st_dev, opened.st_ino, device_number
            ):
                raise OfflineVmContractError(f"worker block identity changed during capacity inspection for {device_id}")
        finally:
            os.close(fd)
        try:
            readonly = (Path("/sys/dev/block") / f"{major}:{minor}" / "ro").read_text(encoding="ascii").strip()
        except OSError as exc:
            raise OfflineVmContractError(f"worker read-only state is unavailable for {device_id}") from exc
        if readonly == "1":
            readonly_roles.append(role)
        elif readonly != "0":
            raise OfflineVmContractError(f"worker block read-only state is malformed for {device_id}")
    # The marker is baked into the separately hash-pinned worker base image.
    # Host-side QEMU argv validation is what enforces no shares/passthrough.
    image_marker = Path("/run/librecore-worker/worker-image.sha256")
    if image_marker.is_symlink() or not image_marker.is_file():
        raise OfflineVmContractError("worker image identity marker is missing or linked")
    worker_image_sha256 = image_marker.read_text(encoding="ascii").strip()
    if re.fullmatch(r"[0-9a-f]{64}", worker_image_sha256) is None:
        raise OfflineVmContractError("worker image identity marker is malformed")
    return GuestIsolation(
        worker_image_sha256=worker_image_sha256,
        interfaces=interfaces,
        ipv4_routes=tuple(line for line in ipv4_lines if line.strip()),
        ipv6_routes=ipv6_lines,
        mounts=mounts,
        device_numbers=tuple(device_numbers),
        device_capacities=tuple(device_capacities),
        readonly_device_roles=tuple(readonly_roles),
        all_mounted_devices=all_mounted_devices,
    )


def main() -> int:
    try:
        request_stat = REQUEST_PATH.stat()
        if not stat.S_ISBLK(request_stat.st_mode):
            raise OfflineVmContractError("request path is not the fixed block device")
        fd = os.open(REQUEST_PATH, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        try:
            if getattr(os.fstat(fd), "st_rdev") != getattr(request_stat, "st_rdev"):
                raise OfflineVmContractError("request device identity changed while opening")
            chunks: list[bytes] = []
            remaining = 2 * 1024 * 1024
            while remaining:
                chunk = os.read(fd, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            request_volume = b"".join(chunks)
        finally:
            os.close(fd)
        from usb_builder.offline_vm_worker import canonical_json, decode_request_volume

        request = decode_request_volume(request_volume)
        request_bytes = canonical_json(request)
        state = collect_guest_isolation()
        result = run_one_shot(request_bytes, state, Path("/mnt/librecore-source"))
        output = Path("/dev/disk/by-id") / f"virtio-{DEVICE_IDS['output']}"
        _write_unavailable_header(output, request)
        _power_off_guest()
    except (OSError, OfflineVmContractError) as exc:
        print(json.dumps({"status": "INCOMPLETE", "reason": str(exc)}, sort_keys=True, separators=(",", ":")))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
