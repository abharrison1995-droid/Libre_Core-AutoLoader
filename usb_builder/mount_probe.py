"""Fail-closed probe of the mounts needed by pinned Archiso tooling.

The probe is executed only inside the offline builder's user-mapped network,
mount, and PID namespaces.  It uses disposable directories under the child's
own /tmp and never binds host /dev or /run.  The outer unshare command mounts
its private /proc as namespace bootstrap after making mount propagation
private; this module verifies namespace identity and route-free state before
attempting any of the capability mounts reported here.
"""

from __future__ import annotations

import ctypes
import errno
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from usb_builder.network_probe import ProbeError, collect_namespace_views


PROBE_MARKER = "AUTOLOADER_ARCHISO_MOUNT_CAPABILITY_V1"
SUPPORTED_EXIT = 0
UNSUPPORTED_EXIT = 3
INTEGRITY_EXIT = 4

# Linux mount(2) flags from <sys/mount.h>.
MS_RDONLY = 1
MS_NOSUID = 2
MS_NODEV = 4
MS_NOEXEC = 8
MS_BIND = 4096
MS_REC = 16384
MS_PRIVATE = 1 << 18
MS_STRICTATIME = 1 << 24
MNT_DETACH = 2

NAMESPACE_KINDS = ("user", "net", "mnt", "pid")
_NAMESPACE_PATTERN = re.compile(r"(?:user|net|mnt|pid):\[\d+\]\Z")
MOUNT_OPERATION_NAMES = (
    "target-root-self-bind",
    "proc",
    "sysfs-read-only",
    "devtmpfs",
    "devpts-gid-5",
    "dev-shm-tmpfs",
    "run-bind",
    "run-bind-private",
    "tmpfs-run",
    "tmpfs-tmp",
    "sys-recursive-bind",
    "device-bind-full",
    "device-bind-null",
    "device-bind-random",
    "device-bind-tty",
    "device-bind-urandom",
    "device-bind-zero",
)
_FILESYSTEM_MOUNT_OPERATIONS = frozenset(
    {
        "proc",
        "sysfs-read-only",
        "devtmpfs",
        "devpts-gid-5",
        "dev-shm-tmpfs",
        "tmpfs-run",
        "tmpfs-tmp",
    }
)
_PERMISSION_ERRNOS = frozenset({errno.EPERM, errno.EACCES})
_FILESYSTEM_UNAVAILABLE_ERRNOS = frozenset(
    {errno.ENODEV, getattr(errno, "EOPNOTSUPP", 95)}
)


class MountProbeIntegrityError(RuntimeError):
    """Probe execution, namespace, mount table, or cleanup evidence is invalid."""


def _is_expected_capability_denial(operation: str, error_number: int | None) -> bool:
    """Return whether a fixed mount operation failed for a supported denial.

    Linux mount(2) documents EPERM for missing mount privilege and EACCES for
    permission denial. ENODEV means the requested filesystem type is not
    configured; filesystem implementations may also report EOPNOTSUPP for an
    unsupported filesystem operation. The latter two only describe operations
    that mount an explicit filesystem type in this fixed plan.

    EINVAL is intentionally not accepted. The probe supplies fixed flags and
    options and owns its prevalidated targets/sources. In particular, mount(2)
    documents EINVAL for malformed flags/targets and for certain unprivileged
    non-recursive bind mounts that would expose hidden submounts; our probe
    sources are created inside its own temporary tree and contain no submounts.
    Such a result therefore indicates a changed environment or implementation
    defect, not an established missing capability.
    """

    if type(error_number) is not int or error_number <= 0:
        return False
    if error_number in _PERMISSION_ERRNOS:
        return True
    return operation in _FILESYSTEM_MOUNT_OPERATIONS and error_number in _FILESYSTEM_UNAVAILABLE_ERRNOS


MountCall = Callable[[str | None, str, str | None, int, str | None], None]
UmountCall = Callable[[str, int], None]


def current_namespace_identities() -> dict[str, str]:
    result: dict[str, str] = {}
    for kind in NAMESPACE_KINDS:
        value = os.readlink(f"/proc/self/ns/{kind}")
        if _NAMESPACE_PATTERN.fullmatch(value) is None or not value.startswith(f"{kind}:"):
            raise MountProbeIntegrityError(f"malformed current {kind} namespace identity")
        result[kind] = value
    return result


def _mountinfo() -> tuple[tuple[str, ...], ...]:
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="ascii").splitlines()
    except OSError as exc:
        raise MountProbeIntegrityError("cannot read child mount table") from exc
    rows: list[tuple[str, ...]] = []
    for line in lines:
        fields = tuple(line.split())
        if "-" not in fields or len(fields) < 10:
            raise MountProbeIntegrityError("child mount table contains a malformed row")
        separator = fields.index("-")
        if separator < 6 or separator + 3 > len(fields):
            raise MountProbeIntegrityError("child mount table row has an invalid separator")
        rows.append(fields)
    if not rows:
        raise MountProbeIntegrityError("child mount table is empty")
    return tuple(rows)


def _assert_private_propagation(rows: Sequence[Sequence[str]]) -> None:
    for fields in rows:
        separator = fields.index("-")
        optional = fields[6:separator]
        if any(item.startswith(("shared:", "master:", "propagate_from:")) for item in optional):
            raise MountProbeIntegrityError("mount namespace still has shared/slave propagation")


def _mount_rows_for_target(rows: Sequence[Sequence[str]]) -> set[str]:
    # Probe paths are created under /tmp and have no whitespace or mountinfo
    # escape sequences, so the fifth field is an exact target path.
    return {fields[4] for fields in rows if len(fields) > 4}


def _linux_mount(
    source: str | None,
    target: str,
    filesystem: str | None,
    flags: int,
    data: str | None,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    mount = libc.mount
    mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p]
    mount.restype = ctypes.c_int
    encode = lambda value: value.encode("utf-8") if value is not None else None
    result = mount(encode(source), encode(target), encode(filesystem), flags, encode(data))
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), target)


def _linux_umount(target: str, flags: int) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    umount2 = libc.umount2
    umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]
    umount2.restype = ctypes.c_int
    if umount2(target.encode("utf-8"), flags) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), target)


def _prepare_probe_tree(root: Path, expected_parent: Path) -> dict[str, Path]:
    if root.parent != expected_parent or not root.name.startswith("autoloader-mount-probe-"):
        raise MountProbeIntegrityError("temporary mount probe directory is outside its owned namespace path")
    if root.is_symlink() or not root.is_dir():
        raise MountProbeIntegrityError("temporary mount probe root is not a regular directory")
    source = root / "sources"
    rootfs = root / "rootfs"
    source.mkdir(mode=0o700)
    rootfs.mkdir(mode=0o700)
    for base in ("proc", "sys", "dev", "run", "tmp", "sys-rbind", "run-tmpfs"):
        (rootfs / base).mkdir(mode=0o755)
    for base in ("run", "sys", "dev"):
        (source / base).mkdir(mode=0o700)
    (source / "sys" / "nested").mkdir(mode=0o700)
    devices_source = source / "dev"
    for device in ("full", "null", "random", "tty", "urandom", "zero"):
        (devices_source / device).touch(mode=0o600)
    return {"source": source, "rootfs": rootfs}


def _create_devtmpfs_children(rootfs: Path) -> None:
    # The child targets must be created after devtmpfs covers rootfs/dev, just
    # as pacstrap/arch-chroot create device targets inside the mounted tree.
    # devtmpfs may already have populated these standard entries; never touch
    # an existing device node because that can change its metadata.
    dev = rootfs / "dev"
    for name in ("pts", "shm"):
        target = dev / name
        try:
            mode = target.lstat().st_mode
        except FileNotFoundError:
            target.mkdir(mode=0o755)
            continue
        if not stat.S_ISDIR(mode):
            raise MountProbeIntegrityError(f"devtmpfs child {name} exists but is not a real directory")
    expected_devices = ("full", "null", "random", "tty", "urandom", "zero")
    for device in expected_devices:
        target = dev / device
        try:
            info = target.lstat()
        except FileNotFoundError:
            target.touch(mode=0o600)
            continue
        if not stat.S_ISCHR(info.st_mode):
            raise MountProbeIntegrityError(f"devtmpfs child {device} exists but is not a character device")


def _mount_plan(paths: Mapping[str, Path]) -> tuple[tuple[str, str | None, Path, str | None, int, str | None], ...]:
    source = paths["source"]
    rootfs = paths["rootfs"]
    plan: list[tuple[str, str | None, Path, str | None, int, str | None]] = [
        ("target-root-self-bind", str(rootfs), rootfs, None, MS_BIND, None),
        ("proc", "proc", rootfs / "proc", "proc", MS_NOSUID | MS_NOEXEC | MS_NODEV, None),
        ("sysfs-read-only", "sysfs", rootfs / "sys", "sysfs", MS_NOSUID | MS_NOEXEC | MS_NODEV | MS_RDONLY, None),
        ("devtmpfs", "udev", rootfs / "dev", "devtmpfs", MS_NOSUID, "mode=0755"),
        (
            "devpts-gid-5",
            "devpts",
            rootfs / "dev" / "pts",
            "devpts",
            MS_NOSUID | MS_NOEXEC,
            "mode=0620,gid=5",
        ),
        (
            "dev-shm-tmpfs",
            "shm",
            rootfs / "dev" / "shm",
            "tmpfs",
            MS_NOSUID | MS_NODEV,
            "mode=1777",
        ),
        ("run-bind", str(source / "run"), rootfs / "run", None, MS_BIND, None),
        ("run-bind-private", None, rootfs / "run", None, MS_PRIVATE, None),
        (
            "tmpfs-run",
            "run",
            rootfs / "run-tmpfs",
            "tmpfs",
            MS_NOSUID | MS_NODEV,
            "mode=0755",
        ),
        (
            "tmpfs-tmp",
            "tmp",
            rootfs / "tmp",
            "tmpfs",
            MS_NOSUID | MS_NODEV | MS_STRICTATIME,
            "mode=1777",
        ),
        ("sys-recursive-bind", str(source / "sys"), rootfs / "sys-rbind", None, MS_BIND | MS_REC, None),
    ]
    for device in ("full", "null", "random", "tty", "urandom", "zero"):
        plan.append(
            (
                f"device-bind-{device}",
                str(source / "dev" / device),
                rootfs / "dev" / device,
                None,
                MS_BIND,
                None,
            )
        )
    return tuple(plan)


def run_mount_capability_probe(
    host_namespace_ids: Mapping[str, str],
    host_net_namespace: str,
    ip_path: str,
    *,
    mount_call: MountCall = _linux_mount,
    umount_call: UmountCall = _linux_umount,
    namespace_reader: Callable[[], Mapping[str, str]] = current_namespace_identities,
    mountinfo_reader: Callable[[], Sequence[Sequence[str]]] = _mountinfo,
    network_reader: Callable[[str, str], dict[str, Any]] = collect_namespace_views,
    temp_root: str = "/tmp",
) -> dict[str, Any]:
    if set(host_namespace_ids) != set(NAMESPACE_KINDS) or any(
        not isinstance(host_namespace_ids.get(kind), str) or not host_namespace_ids[kind]
        for kind in NAMESPACE_KINDS
    ):
        raise MountProbeIntegrityError("host namespace identity set is incomplete")
    if host_namespace_ids.get("net") != host_net_namespace:
        raise MountProbeIntegrityError("host network namespace identity is inconsistent")
    child_ids = dict(namespace_reader())
    if set(child_ids) != set(NAMESPACE_KINDS) or any(
        child_ids.get(kind) == host_namespace_ids[kind]
        or not isinstance(child_ids.get(kind), str)
        or _NAMESPACE_PATTERN.fullmatch(child_ids[kind]) is None
        or not child_ids[kind].startswith(f"{kind}:")
        for kind in NAMESPACE_KINDS
    ):
        raise MountProbeIntegrityError("user/net/mount/PID namespace identity did not all change")
    before_rows = tuple(mountinfo_reader())
    _assert_private_propagation(before_rows)
    network_report = network_reader(host_net_namespace, ip_path)
    if network_report.get("namespace_id") != child_ids["net"]:
        raise MountProbeIntegrityError("route-free network report is not bound to the current child namespace")

    parent = Path(temp_root)
    if parent.is_symlink() or not parent.is_dir() or (parent != Path("/tmp") and mount_call is _linux_mount):
        raise MountProbeIntegrityError("production probe temporary root must be regular /tmp")
    temp_path = Path(tempfile.mkdtemp(prefix="autoloader-mount-probe-", dir=str(parent)))
    os.chmod(temp_path, 0o700)
    paths: dict[str, Path] | None = None
    mounted: list[Path] = []
    attempts: list[dict[str, Any]] = []
    cleanup_errors: list[str] = []
    unsupported = False
    devtmpfs_ready = False
    run_bind_ready = False
    try:
        paths = _prepare_probe_tree(temp_path, parent)
        for label, source, target, filesystem, flags, data in _mount_plan(paths):
            depends_on_devtmpfs = (
                label in {"devpts-gid-5", "dev-shm-tmpfs"} or label.startswith("device-bind-")
            )
            depends_on_run_bind = label == "run-bind-private"
            if (depends_on_devtmpfs and not devtmpfs_ready) or (depends_on_run_bind and not run_bind_ready):
                unsupported = True
                attempts.append({"name": label, "status": "blocked", "errno": None})
                continue
            try:
                mount_call(source, str(target), filesystem, flags, data)
            except OSError as exc:
                if not _is_expected_capability_denial(label, exc.errno):
                    raise MountProbeIntegrityError(
                        f"mount operation {label} failed with unexpected errno {exc.errno!r}"
                    ) from exc
                unsupported = True
                attempts.append({"name": label, "status": "denied", "errno": exc.errno})
            else:
                # Propagation changes do not add a mount and must not create a
                # second unmount entry for the same bind target.
                if label != "run-bind-private":
                    mounted.append(target)
                if label == "devtmpfs":
                    try:
                        _create_devtmpfs_children(paths["rootfs"])
                    except OSError as exc:
                        raise MountProbeIntegrityError("could not create targets inside mounted devtmpfs") from exc
                    devtmpfs_ready = True
                if label == "run-bind":
                    run_bind_ready = True
                attempts.append(
                    {
                        "name": label,
                        "status": "applied" if label == "run-bind-private" else "mounted",
                        "errno": None,
                    }
                )
        active_rows = tuple(mountinfo_reader())
        _assert_private_propagation(active_rows)
        active_targets = _mount_rows_for_target(active_rows)
        for target in mounted:
            if str(target) not in active_targets:
                raise MountProbeIntegrityError("a successful mount syscall is absent from child mountinfo")
        after_network_report = network_reader(host_net_namespace, ip_path)
        if after_network_report != network_report:
            raise MountProbeIntegrityError("network namespace state changed during mount capability checks")
    finally:
        for target in reversed(mounted):
            try:
                umount_call(str(target), MNT_DETACH)
            except OSError as exc:
                cleanup_errors.append(f"{target.name}:{exc.errno or errno.EIO}")
        try:
            shutil.rmtree(temp_path)
        except OSError as exc:
            cleanup_errors.append(f"temp-root:{exc.errno or errno.EIO}")
    if cleanup_errors:
        raise MountProbeIntegrityError("failed to clean probe-owned mounts/directories: " + ",".join(cleanup_errors))
    return {
        "schema_version": 1,
        "status": "UNSUPPORTED" if unsupported else "SUPPORTED",
        "namespace_ids": child_ids,
        "propagation_private": True,
        "network": network_report,
        "mount_attempts": attempts,
        "mounts_cleaned": True,
        "probe_owned_sources_only": True,
        "host_dev_or_run_bound": False,
    }


def validate_mount_probe_output(
    output: str,
    *,
    host_namespace_ids: Mapping[str, str],
    host_net_namespace: str,
    allow_synthetic: bool,
) -> tuple[str, dict[str, Any]] | None:
    lines = output.strip().splitlines()
    if len(lines) != 2 or lines[0] != PROBE_MARKER:
        return None
    try:
        report = json.loads(lines[1])
    except json.JSONDecodeError:
        return None
    if not isinstance(report, dict) or set(report) != {
        "schema_version",
        "status",
        "namespace_ids",
        "propagation_private",
        "network",
        "mount_attempts",
        "mounts_cleaned",
        "probe_owned_sources_only",
        "host_dev_or_run_bound",
    }:
        return None
    status = report.get("status")
    if (
        type(report.get("schema_version")) is not int
        or report["schema_version"] != 1
        or not isinstance(status, str)
        or status not in {"SUPPORTED", "UNSUPPORTED"}
        or report.get("propagation_private") is not True
        or report.get("mounts_cleaned") is not True
        or report.get("probe_owned_sources_only") is not True
        or report.get("host_dev_or_run_bound") is not False
        or not isinstance(report.get("network"), dict)
    ):
        return None
    namespace_ids = report.get("namespace_ids")
    if not isinstance(namespace_ids, dict) or set(namespace_ids) != set(NAMESPACE_KINDS):
        return None
    for kind in NAMESPACE_KINDS:
        identity = namespace_ids.get(kind)
        if (
            not isinstance(identity, str)
            or identity == host_namespace_ids.get(kind)
            or _NAMESPACE_PATTERN.fullmatch(identity) is None
            or not identity.startswith(f"{kind}:")
        ):
            return None
    network = report["network"]
    if set(network) != {
        "namespace_id",
        "sysfs_interfaces_observed",
        "namespace_interfaces",
        "ipv4_netlink_route_count",
        "ipv6_netlink_route_count",
        "ipv4_rule_priorities",
        "ipv6_rule_priorities",
        "ipv6_null_unreachable_proc_rows",
    }:
        return None
    if (
        network.get("namespace_id") != namespace_ids["net"]
        or network.get("namespace_id") == host_net_namespace
        or network.get("namespace_interfaces") != ["lo"]
        or network.get("ipv4_netlink_route_count") != 0
        or network.get("ipv6_netlink_route_count") != 0
        or network.get("ipv4_rule_priorities") != [0, 32766, 32767]
        or network.get("ipv6_rule_priorities") != [0, 32766]
        or type(network.get("ipv6_null_unreachable_proc_rows")) is not int
        or network.get("ipv6_null_unreachable_proc_rows") not in (0, 2)
        or not isinstance(network.get("sysfs_interfaces_observed"), list)
        or not all(isinstance(name, str) for name in network["sysfs_interfaces_observed"])
    ):
        return None
    if not allow_synthetic and any(not isinstance(value, str) or re.fullmatch(r"\w+:\[\d+\]", value) is None for value in host_namespace_ids.values()):
        return None
    attempts = report.get("mount_attempts")
    expected_names = list(MOUNT_OPERATION_NAMES)
    if not isinstance(attempts, list) or len(attempts) != len(expected_names):
        return None
    denied_names: set[str] = set()
    devtmpfs_dependents = {"devpts-gid-5", "dev-shm-tmpfs"} | {
        name for name in expected_names if name.startswith("device-bind-")
    }
    dependency_names = devtmpfs_dependents | {"run-bind-private"}
    state_by_name: dict[str, str] = {}
    for expected, attempt in zip(expected_names, attempts):
        if not isinstance(attempt, dict) or set(attempt) != {"name", "status", "errno"} or attempt.get("name") != expected:
            return None
        if attempt.get("status") == "mounted" and attempt.get("errno") is None:
            state_by_name[expected] = "mounted"
            continue
        if expected == "run-bind-private" and attempt.get("status") == "applied" and attempt.get("errno") is None:
            state_by_name[expected] = "applied"
            continue
        if attempt.get("status") == "denied" and _is_expected_capability_denial(expected, attempt.get("errno")):
            denied_names.add(expected)
            state_by_name[expected] = "denied"
            continue
        if attempt.get("status") in {"blocked", "not_attempted"} and attempt.get("errno") is None:
            if expected not in dependency_names:
                return None
            state_by_name[expected] = attempt["status"]
            continue
        return None

    # devpts, /dev/shm and device binds are meaningful only after devtmpfs
    # covered the target directory. If that prerequisite was denied, every
    # dependent operation must be explicitly blocked in the same fixed order;
    # if it succeeded, each dependent entry must represent an actual syscall.
    if state_by_name.get("devtmpfs") == "denied":
        if any(state_by_name.get(name) not in {"blocked", "not_attempted"} for name in devtmpfs_dependents):
            return None
    elif any(state_by_name.get(name) in {"blocked", "not_attempted"} for name in devtmpfs_dependents):
        return None
    if state_by_name.get("run-bind") == "denied":
        if state_by_name.get("run-bind-private") not in {"blocked", "not_attempted"}:
            return None
    elif state_by_name.get("run-bind-private") in {"blocked", "not_attempted"}:
        return None

    expected_status = "UNSUPPORTED" if denied_names else "SUPPORTED"
    if status != expected_status:
        return None
    return expected_status, report


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        print("usage: python -m usb_builder.mount_probe HOST_NAMESPACE_IDS_JSON HOST_NETNS IP_PATH", file=sys.stderr)
        return INTEGRITY_EXIT
    try:
        host_ids = json.loads(args[0])
        if not isinstance(host_ids, dict):
            raise MountProbeIntegrityError("host namespace JSON is not an object")
        report = run_mount_capability_probe(host_ids, args[1], args[2])
    except (OSError, ProbeError, MountProbeIntegrityError, ValueError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return INTEGRITY_EXIT
    print(PROBE_MARKER)
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return SUPPORTED_EXIT if report["status"] == "SUPPORTED" else UNSUPPORTED_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
