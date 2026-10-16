"""Probe the exact rootless mount stack used by pinned arch-chroot -N.

This is a preflight only. It runs inside a fresh mapped user, network, mount,
and PID namespace, verifies route-free networking and private propagation, and
attaches mounts only below an owned temporary root. The only host paths bound
are the pinned arch-chroot inputs: /sys recursively and six validated device
nodes. Their exact host-side metadata is captured before namespace entry and
rechecked in the parent afterward; the child validates the UID/GID maps and
the kernel overflow IDs that represent host-root ownership in the mapped view.
It never writes to those sources or invokes package/build commands.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from usb_builder import mount_probe as common
from usb_builder.network_probe import ProbeError, collect_namespace_views


PROBE_MARKER = "AUTOLOADER_ARCHISO_ROOTLESS_MOUNT_V1"
SUPPORTED_EXIT = 0
UNSUPPORTED_EXIT = 3
INTEGRITY_EXIT = 4

DEVICE_NODES: tuple[tuple[str, str, int, int], ...] = (
    ("full", "/dev/full", 1, 7),
    ("null", "/dev/null", 1, 3),
    ("random", "/dev/random", 1, 8),
    ("tty", "/dev/tty", 5, 0),
    ("urandom", "/dev/urandom", 1, 9),
    ("zero", "/dev/zero", 1, 5),
)
MOUNT_OPERATION_NAMES = (
    "target-root-self-bind",
    "proc",
    "sys-recursive-bind",
    "tmpfs-dev",
    "device-bind-full",
    "device-bind-null",
    "device-bind-random",
    "device-bind-tty",
    "device-bind-urandom",
    "device-bind-zero",
    "tmpfs-run",
    "tmpfs-tmp",
)

_MOUNT_DEPENDENT_NAMES = frozenset(name for name in MOUNT_OPERATION_NAMES if name.startswith("device-bind-"))
_ROOT_DEPENDENT_NAMES = frozenset(MOUNT_OPERATION_NAMES[1:])
_CAPABILITY_ERRNOS = frozenset(
    {
        errno.EPERM,
        errno.EACCES,
        errno.ENODEV,
        getattr(errno, "EOPNOTSUPP", 95),
    }
)
_FILESYSTEM_ERRNOS = frozenset({errno.ENODEV, getattr(errno, "EOPNOTSUPP", 95)})

MountCall = Callable[[str | None, str, str | None, int, str | None], None]
UmountCall = Callable[[str, int], None]
DeviceInspector = Callable[[Path, Mapping[str, Any], Mapping[str, int]], Mapping[str, Any]]
DirectoryInspector = Callable[[Path, Mapping[str, Any], Mapping[str, int]], Mapping[str, Any]]


class RootlessMountProbeIntegrityError(RuntimeError):
    """A malformed environment, unexpected syscall result, or cleanup fault."""


def _inspect_device(path: Path, expected_major: int, expected_minor: int) -> dict[str, Any]:
    """Inspect a host-side root-owned, non-link character node at pinned dev_t."""
    dev_dir = Path("/dev")
    try:
        parent = dev_dir.lstat()
        info = path.lstat()
    except OSError as exc:
        raise RootlessMountProbeIntegrityError(f"cannot inspect required device source {path}") from exc
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != 0
        or stat.S_IMODE(parent.st_mode) & 0o022
        or path.parent != dev_dir
        or stat.S_ISLNK(info.st_mode)
        or not stat.S_ISCHR(info.st_mode)
        or info.st_uid != 0
        or not _device_number_matches(info, expected_major, expected_minor)
    ):
        raise RootlessMountProbeIntegrityError(f"device source {path} is not the exact trusted character node")
    return {
        "path": str(path),
        "kind": "character-device",
        "major": expected_major,
        "minor": expected_minor,
        "owner_uid": info.st_uid,
        "owner_gid": info.st_gid,
        "mode": stat.S_IMODE(info.st_mode),
        "st_dev": info.st_dev,
        "st_ino": info.st_ino,
        "st_rdev": getattr(info, "st_rdev", None),
    }


def _inspect_sys_source(path: Path = Path("/sys")) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise RootlessMountProbeIntegrityError("cannot inspect the pinned /sys bind source") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != 0:
        raise RootlessMountProbeIntegrityError("/sys source must be a root-owned, non-link directory")
    return {
        "path": str(path),
        "kind": "directory",
        "owner_uid": info.st_uid,
        "owner_gid": info.st_gid,
        "mode": stat.S_IMODE(info.st_mode),
        "st_dev": info.st_dev,
        "st_ino": info.st_ino,
    }


def _inspect_parent_directory(path: Path, label: str) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise RootlessMountProbeIntegrityError(f"cannot inspect required {label} directory") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        raise RootlessMountProbeIntegrityError(f"{label} must be a root-owned, non-link, non-writable directory")
    return {
        "path": str(path),
        "kind": "directory",
        "owner_uid": info.st_uid,
        "owner_gid": info.st_gid,
        "mode": stat.S_IMODE(info.st_mode),
        "st_dev": info.st_dev,
        "st_ino": info.st_ino,
    }


def _read_overflow_id(name: str) -> int:
    if name not in {"overflowuid", "overflowgid"}:
        raise RootlessMountProbeIntegrityError("unexpected kernel overflow ID name")
    try:
        raw = Path("/proc/sys/kernel", name).read_text(encoding="ascii").strip()
        value = int(raw, 10)
    except (OSError, UnicodeError, ValueError) as exc:
        raise RootlessMountProbeIntegrityError(f"cannot read kernel {name}") from exc
    if value < 0 or value >= 2**32:
        raise RootlessMountProbeIntegrityError(f"kernel {name} is outside the supported ID range")
    return value


def _validate_id_map(value: Any, label: str) -> list[list[int]]:
    if not isinstance(value, list) or not value:
        raise RootlessMountProbeIntegrityError(f"{label} is missing")
    result: list[list[int]] = []
    previous_inside_end = -1
    previous_outside_ranges: list[tuple[int, int]] = []
    for row in value:
        if not isinstance(row, list) or len(row) != 3 or any(type(item) is not int for item in row):
            raise RootlessMountProbeIntegrityError(f"{label} has a malformed row")
        inside, outside, count = row
        if inside < 0 or outside < 0 or count <= 0 or inside + count > 2**32 or outside + count > 2**32:
            raise RootlessMountProbeIntegrityError(f"{label} has an out-of-range row")
        if inside < previous_inside_end:
            raise RootlessMountProbeIntegrityError(f"{label} rows overlap or are unordered")
        if any(outside < end and start < outside + count for start, end in previous_outside_ranges):
            raise RootlessMountProbeIntegrityError(f"{label} host ranges overlap")
        previous_inside_end = inside + count
        previous_outside_ranges.append((outside, outside + count))
        result.append([inside, outside, count])
    return result


def _read_namespace_id_map(path: Path) -> list[list[int]]:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise RootlessMountProbeIntegrityError(f"cannot read mapped namespace ID file {path}") from exc
    rows: list[list[int]] = []
    for line in lines:
        pieces = line.split()
        if len(pieces) != 3:
            raise RootlessMountProbeIntegrityError(f"mapped namespace ID file {path} is malformed")
        try:
            rows.append([int(piece, 10) for piece in pieces])
        except ValueError as exc:
            raise RootlessMountProbeIntegrityError(f"mapped namespace ID file {path} is malformed") from exc
    return _validate_id_map(rows, str(path))


def _namespace_id_for_host_id(host_id: int, mapping: Sequence[Sequence[int]], overflow_id: int) -> int:
    for inside, outside, count in mapping:
        if outside <= host_id < outside + count:
            return inside + host_id - outside
    return overflow_id


def collect_parent_source_observations(
    *,
    uid_map: Sequence[Sequence[int]],
    gid_map: Sequence[Sequence[int]],
) -> dict[str, Any]:
    """Capture trusted host-side source identities before entering map-auto."""
    normalized_uid_map = _validate_id_map([list(row) for row in uid_map], "expected uid_map")
    normalized_gid_map = _validate_id_map([list(row) for row in gid_map], "expected gid_map")
    devices = [
        _inspect_device(Path(path), major, minor)
        for _name, path, major, minor in DEVICE_NODES
    ]
    sys_source = _inspect_sys_source()
    dev_directory = _inspect_parent_directory(Path("/dev"), "/dev")
    return {
        "schema_version": 1,
        "overflow_uid": _read_overflow_id("overflowuid"),
        "overflow_gid": _read_overflow_id("overflowgid"),
        "uid_map": normalized_uid_map,
        "gid_map": normalized_gid_map,
        "dev_directory": dev_directory,
        "device_sources": devices,
        "sys_source": sys_source,
    }


def _validate_parent_source_observations(value: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "schema_version", "overflow_uid", "overflow_gid", "uid_map", "gid_map",
        "dev_directory", "device_sources", "sys_source",
    }
    if set(value) != fields or type(value.get("schema_version")) is not int or value.get("schema_version") != 1:
        raise RootlessMountProbeIntegrityError("parent source observations have an invalid schema")
    for name in ("overflow_uid", "overflow_gid"):
        if type(value.get(name)) is not int or not 0 <= value[name] < 2**32:
            raise RootlessMountProbeIntegrityError(f"parent {name} observation is malformed")
    uid_map = _validate_id_map(value.get("uid_map"), "parent uid_map")
    gid_map = _validate_id_map(value.get("gid_map"), "parent gid_map")
    dev = value.get("dev_directory")
    expected_dir_fields = {"path", "kind", "owner_uid", "owner_gid", "mode", "st_dev", "st_ino"}
    dev_mode = dev.get("mode") if isinstance(dev, Mapping) else None
    if not isinstance(dev, Mapping) or set(dev) != expected_dir_fields or (
        dev.get("path") != "/dev"
        or dev.get("kind") != "directory"
        or type(dev.get("owner_uid")) is not int
        or dev.get("owner_uid") != 0
        or type(dev.get("owner_gid")) is not int
        or type(dev_mode) is not int
        or dev_mode & 0o022
        or type(dev.get("st_dev")) is not int
        or type(dev.get("st_ino")) is not int
    ):
        raise RootlessMountProbeIntegrityError("parent /dev identity is not a trusted root-owned directory")
    devices = value.get("device_sources")
    if not isinstance(devices, list) or len(devices) != len(DEVICE_NODES):
        raise RootlessMountProbeIntegrityError("parent device source inventory is incomplete")
    normalized_devices = [
        _validate_device_record(record, path, major, minor)
        for record, (_name, path, major, minor) in zip(devices, DEVICE_NODES)
    ]
    sys_value = value.get("sys_source")
    if not isinstance(sys_value, Mapping):
        raise RootlessMountProbeIntegrityError("/sys source identity is malformed")
    sys_source = _validate_sys_record(sys_value, "/sys")
    if sys_source.get("owner_uid") != 0:
        raise RootlessMountProbeIntegrityError("parent /sys source is not host-root-owned")
    return {
        "schema_version": 1,
        "overflow_uid": value["overflow_uid"],
        "overflow_gid": value["overflow_gid"],
        "uid_map": uid_map,
        "gid_map": gid_map,
        "dev_directory": dict(dev),
        "device_sources": normalized_devices,
        "sys_source": sys_source,
    }


def _visible_owner_record(parent_record: Mapping[str, Any], uid_map: Sequence[Sequence[int]],
                          gid_map: Sequence[Sequence[int]], overflow_uid: int, overflow_gid: int) -> dict[str, int]:
    return {
        "uid": _namespace_id_for_host_id(int(parent_record["owner_uid"]), uid_map, overflow_uid),
        "gid": _namespace_id_for_host_id(int(parent_record["owner_gid"]), gid_map, overflow_gid),
    }


def _inspect_child_source(
    path: Path,
    parent_record: Mapping[str, Any],
    expected_visible_owner: Mapping[str, int],
    *,
    kind: str,
    major: int | None = None,
    minor: int | None = None,
) -> dict[str, int]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise RootlessMountProbeIntegrityError(f"cannot inspect mapped child source {path}") from exc
    valid_type = stat.S_ISDIR(info.st_mode) if kind == "directory" else stat.S_ISCHR(info.st_mode)
    if (
        stat.S_ISLNK(info.st_mode)
        or not valid_type
        or stat.S_IMODE(info.st_mode) != parent_record.get("mode")
        or info.st_dev != parent_record.get("st_dev")
        or info.st_ino != parent_record.get("st_ino")
        or info.st_uid != expected_visible_owner.get("uid")
        or info.st_gid != expected_visible_owner.get("gid")
    ):
        raise RootlessMountProbeIntegrityError(f"mapped child source {path} differs from parent observation")
    if kind == "character-device" and (
        not stat.S_ISCHR(info.st_mode)
        or major is None
        or minor is None
        or not _device_number_matches(info, major, minor)
        or getattr(info, "st_rdev", None) != parent_record.get("st_rdev")
    ):
        raise RootlessMountProbeIntegrityError(f"mapped child device {path} has a different device number")
    return {"uid": info.st_uid, "gid": info.st_gid}


def _device_number_matches(info: Any, expected_major: int, expected_minor: int) -> bool:
    raw_device = getattr(info, "st_rdev", None)
    major_reader = getattr(os, "major", None)
    minor_reader = getattr(os, "minor", None)
    if type(raw_device) is not int:
        return False
    if callable(major_reader) and callable(minor_reader):
        return major_reader(raw_device) == expected_major and minor_reader(raw_device) == expected_minor
    # The only fallback targets are the six fixed Linux character devices whose
    # major and minor values fit the conventional low-byte device encoding.
    return expected_major < 256 and expected_minor < 256 and raw_device == ((expected_major << 8) | expected_minor)


def _check_identity(expected: Mapping[str, Any], current: Mapping[str, Any], label: str) -> None:
    for key in ("path", "kind", "owner_uid", "owner_gid", "mode", "st_dev", "st_ino"):
        if current.get(key) != expected.get(key):
            raise RootlessMountProbeIntegrityError(f"{label} source identity changed during mount probe")
    if label != "sys" and any(current.get(key) != expected.get(key) for key in ("major", "minor", "st_rdev")):
        raise RootlessMountProbeIntegrityError(f"{label} device identity changed during mount probe")


def _validate_device_record(record: Mapping[str, Any], path: str, major: int, minor: int) -> dict[str, Any]:
    owner_gid = record.get("owner_gid")
    expected_fields = {
        "path", "kind", "major", "minor", "owner_uid", "owner_gid", "mode", "st_dev", "st_ino", "st_rdev"
    }
    if set(record) != expected_fields or (
        record.get("path") != path
        or record.get("kind") != "character-device"
        or type(record.get("major")) is not int
        or record.get("major") != major
        or type(record.get("minor")) is not int
        or record.get("minor") != minor
        or type(record.get("owner_uid")) is not int
        or record.get("owner_uid") != 0
        or type(owner_gid) is not int
        or owner_gid < 0
        or type(record.get("mode")) is not int
        or type(record.get("st_dev")) is not int
        or type(record.get("st_ino")) is not int
        or type(record.get("st_rdev")) is not int
        or record.get("st_rdev") != ((major << 8) | minor)
    ):
        raise RootlessMountProbeIntegrityError(f"device source {path} does not match its pinned type and device number")
    return dict(record)


def _validate_sys_record(record: Mapping[str, Any], path: str = "/sys") -> dict[str, Any]:
    owner_gid = record.get("owner_gid")
    if set(record) != {"path", "kind", "owner_uid", "owner_gid", "mode", "st_dev", "st_ino"} or (
        record.get("path") != path
        or record.get("kind") != "directory"
        or type(record.get("owner_uid")) is not int
        or record.get("owner_uid") != 0
        or type(owner_gid) is not int
        or owner_gid < 0
        or type(record.get("mode")) is not int
        or type(record.get("st_dev")) is not int
        or type(record.get("st_ino")) is not int
    ):
        raise RootlessMountProbeIntegrityError("/sys source identity is malformed")
    return dict(record)


def _child_device_inspector(path: Path, parent_record: Mapping[str, Any], owner: Mapping[str, int]) -> Mapping[str, Any]:
    return _inspect_child_source(
        path,
        parent_record,
        owner,
        kind="character-device",
        major=int(parent_record["major"]),
        minor=int(parent_record["minor"]),
    )


def _child_directory_inspector(path: Path, parent_record: Mapping[str, Any], owner: Mapping[str, int]) -> Mapping[str, Any]:
    return _inspect_child_source(path, parent_record, owner, kind="directory")


def _checked_visible_owner(
    observed: Mapping[str, Any], expected: Mapping[str, int], label: str
) -> dict[str, int]:
    if (
        not isinstance(observed, Mapping)
        or set(observed) != {"uid", "gid"}
        or type(observed.get("uid")) is not int
        or type(observed.get("gid")) is not int
        or dict(observed) != dict(expected)
    ):
        raise RootlessMountProbeIntegrityError(f"mapped child ownership for {label} differs from parent-bound mapping")
    return {"uid": int(observed["uid"]), "gid": int(observed["gid"])}


def _network_report_is_route_free(report: Mapping[str, Any], child_net_namespace: str) -> bool:
    ipv4_rules = report.get("ipv4_rule_priorities")
    ipv6_rules = report.get("ipv6_rule_priorities")
    return (
        set(report)
        == {
            "namespace_id",
            "sysfs_interfaces_observed",
            "namespace_interfaces",
            "ipv4_netlink_route_count",
            "ipv6_netlink_route_count",
            "ipv4_rule_priorities",
            "ipv6_rule_priorities",
            "ipv6_null_unreachable_proc_rows",
        }
        and report.get("namespace_id") == child_net_namespace
        and report.get("namespace_interfaces") == ["lo"]
        and type(report.get("ipv4_netlink_route_count")) is int
        and report.get("ipv4_netlink_route_count") == 0
        and type(report.get("ipv6_netlink_route_count")) is int
        and report.get("ipv6_netlink_route_count") == 0
        and isinstance(ipv4_rules, list)
        and all(type(value) is int for value in ipv4_rules)
        and ipv4_rules == [0, 32766, 32767]
        and isinstance(ipv6_rules, list)
        and all(type(value) is int for value in ipv6_rules)
        and ipv6_rules == [0, 32766]
        and type(report.get("ipv6_null_unreachable_proc_rows")) is int
        and report.get("ipv6_null_unreachable_proc_rows") in (0, 2)
        and isinstance(report.get("sysfs_interfaces_observed"), list)
        and all(isinstance(name, str) for name in report["sysfs_interfaces_observed"])
        and "lo" in report["sysfs_interfaces_observed"]
    )


def _prepare_tree(root: Path, expected_parent: Path) -> dict[str, Path]:
    if root.parent != expected_parent or not root.name.startswith("autoloader-rootless-mount-"):
        raise RootlessMountProbeIntegrityError("temporary rootless mount probe escaped its owned /tmp path")
    if root.is_symlink() or not root.is_dir():
        raise RootlessMountProbeIntegrityError("temporary rootless mount probe root is not a regular directory")
    rootfs = root / "rootfs"
    rootfs.mkdir(mode=0o700)
    for name in ("proc", "sys", "dev", "run", "tmp"):
        (rootfs / name).mkdir(mode=0o755)
    return {"rootfs": rootfs}


def _create_dev_targets(rootfs: Path) -> None:
    dev = rootfs / "dev"
    for name, _source, _major, _minor in DEVICE_NODES:
        target = dev / name
        try:
            target.lstat()
        except FileNotFoundError:
            descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        else:
            raise RootlessMountProbeIntegrityError(f"tmpfs /dev unexpectedly already contains {name}")
    for name, destination in (
        ("fd", "/proc/self/fd"),
        ("stdin", "/proc/self/fd/0"),
        ("stdout", "/proc/self/fd/1"),
        ("stderr", "/proc/self/fd/2"),
    ):
        target = dev / name
        try:
            target.lstat()
        except FileNotFoundError:
            os.symlink(destination, target)
        else:
            raise RootlessMountProbeIntegrityError(f"tmpfs /dev unexpectedly already contains {name}")


def _mount_plan(rootfs: Path) -> tuple[tuple[str, str | None, Path, str | None, int, str | None], ...]:
    plan: list[tuple[str, str | None, Path, str | None, int, str | None]] = [
        ("target-root-self-bind", str(rootfs), rootfs, None, common.MS_BIND, None),
        ("proc", "proc", rootfs / "proc", "proc", common.MS_NOSUID | common.MS_NOEXEC | common.MS_NODEV, None),
        ("sys-recursive-bind", "/sys", rootfs / "sys", None, common.MS_BIND | common.MS_REC, None),
        ("tmpfs-dev", "tmpfs", rootfs / "dev", "tmpfs", common.MS_NOSUID | common.MS_NODEV, "mode=0755"),
    ]
    for name, source, _major, _minor in DEVICE_NODES:
        plan.append((f"device-bind-{name}", source, rootfs / "dev" / name, None, common.MS_BIND, None))
    plan.extend(
        (
            ("tmpfs-run", "tmpfs", rootfs / "run", "tmpfs", common.MS_NOSUID | common.MS_NODEV, "mode=0755"),
            (
                "tmpfs-tmp",
                "tmpfs",
                rootfs / "tmp",
                "tmpfs",
                common.MS_NOSUID | common.MS_NODEV | common.MS_STRICTATIME,
                "mode=1777",
            ),
        )
    )
    return tuple(plan)


def run_rootless_archiso_mount_probe(
    host_namespace_ids: Mapping[str, str],
    host_net_namespace: str,
    ip_path: str,
    parent_source_observations: Mapping[str, Any],
    *,
    mount_call: MountCall = common._linux_mount,
    umount_call: UmountCall = common._linux_umount,
    namespace_reader: Callable[[], Mapping[str, str]] = common.current_namespace_identities,
    mountinfo_reader: Callable[[], Sequence[Sequence[str]]] = common._mountinfo,
    network_reader: Callable[[str, str], dict[str, Any]] = collect_namespace_views,
    id_map_reader: Callable[[Path], list[list[int]]] = _read_namespace_id_map,
    overflow_id_reader: Callable[[str], int] = _read_overflow_id,
    device_inspector: DeviceInspector = _child_device_inspector,
    directory_inspector: DirectoryInspector = _child_directory_inspector,
    temp_root: str = "/tmp",
) -> dict[str, Any]:
    if set(host_namespace_ids) != set(common.NAMESPACE_KINDS) or host_namespace_ids.get("net") != host_net_namespace:
        raise RootlessMountProbeIntegrityError("host namespace evidence is incomplete or inconsistent")
    child_ids = dict(namespace_reader())
    if set(child_ids) != set(common.NAMESPACE_KINDS) or any(
        child_ids.get(kind) == host_namespace_ids.get(kind)
        or not isinstance(child_ids.get(kind), str)
        or common._NAMESPACE_PATTERN.fullmatch(child_ids[kind]) is None
        or not child_ids[kind].startswith(f"{kind}:")
        for kind in common.NAMESPACE_KINDS
    ):
        raise RootlessMountProbeIntegrityError("mapped user/net/mount/PID namespace identity did not all change")
    before_rows = tuple(mountinfo_reader())
    common._assert_private_propagation(before_rows)
    network = network_reader(host_net_namespace, ip_path)
    if not _network_report_is_route_free(network, child_ids["net"]):
        raise RootlessMountProbeIntegrityError("route-free network evidence is malformed or not bound to the current namespace")
    source_observations = _validate_parent_source_observations(parent_source_observations)
    current_overflow = (overflow_id_reader("overflowuid"), overflow_id_reader("overflowgid"))
    if current_overflow != (source_observations["overflow_uid"], source_observations["overflow_gid"]):
        raise RootlessMountProbeIntegrityError("kernel overflow IDs changed after parent source observation")
    current_uid_map = id_map_reader(Path("/proc/self/uid_map"))
    current_gid_map = id_map_reader(Path("/proc/self/gid_map"))
    if current_uid_map != source_observations["uid_map"] or current_gid_map != source_observations["gid_map"]:
        raise RootlessMountProbeIntegrityError("mapped child UID/GID maps differ from the parent contract")

    source_owners: dict[str, dict[str, int]] = {}
    dev_parent = source_observations["dev_directory"]
    dev_owner = _visible_owner_record(
        dev_parent,
        current_uid_map,
        current_gid_map,
        source_observations["overflow_uid"],
        source_observations["overflow_gid"],
    )
    source_owners["/dev"] = _checked_visible_owner(
        directory_inspector(Path("/dev"), dev_parent, dev_owner), dev_owner, "/dev"
    )
    for parent_record, (_name, path_text, major, minor) in zip(source_observations["device_sources"], DEVICE_NODES):
        path = Path(path_text)
        owner = _visible_owner_record(
            parent_record,
            current_uid_map,
            current_gid_map,
            source_observations["overflow_uid"],
            source_observations["overflow_gid"],
        )
        source_owners[path_text] = _checked_visible_owner(
            device_inspector(path, parent_record, owner), owner, path_text
        )
    sys_parent = source_observations["sys_source"]
    sys_owner = _visible_owner_record(
        sys_parent,
        current_uid_map,
        current_gid_map,
        source_observations["overflow_uid"],
        source_observations["overflow_gid"],
    )
    source_owners["/sys"] = _checked_visible_owner(
        directory_inspector(Path("/sys"), sys_parent, sys_owner), sys_owner, "/sys"
    )

    parent = Path(temp_root)
    if parent.is_symlink() or not parent.is_dir() or (parent != Path("/tmp") and mount_call is common._linux_mount):
        raise RootlessMountProbeIntegrityError("production rootless probe temporary root must be regular /tmp")
    temp_path = Path(tempfile.mkdtemp(prefix="autoloader-rootless-mount-", dir=str(parent)))
    os.chmod(temp_path, 0o700)
    mounted: list[Path] = []
    attempts: list[dict[str, Any]] = []
    cleanup_errors: list[str] = []
    unsupported = False
    root_ready = False
    dev_ready = False
    try:
        paths = _prepare_tree(temp_path, parent)
        rootfs = paths["rootfs"]
        for label, source, target, filesystem, flags, data in _mount_plan(rootfs):
            if label != "target-root-self-bind" and not root_ready:
                unsupported = True
                attempts.append({"name": label, "status": "blocked", "errno": None})
                continue
            if label in _MOUNT_DEPENDENT_NAMES and not dev_ready:
                unsupported = True
                attempts.append({"name": label, "status": "blocked", "errno": None})
                continue
            try:
                mount_call(source, str(target), filesystem, flags, data)
            except OSError as exc:
                expected_denials = {errno.EPERM, errno.EACCES}
                if filesystem is not None:
                    expected_denials.update(_FILESYSTEM_ERRNOS)
                if type(exc.errno) is not int or exc.errno not in expected_denials:
                    raise RootlessMountProbeIntegrityError(
                        f"mount operation {label} failed with unexpected errno {exc.errno!r}"
                    ) from exc
                unsupported = True
                attempts.append({"name": label, "status": "denied", "errno": exc.errno})
                continue
            mounted.append(target)
            if label == "target-root-self-bind":
                root_ready = True
            if label == "tmpfs-dev":
                _create_dev_targets(rootfs)
                dev_ready = True
            attempts.append({"name": label, "status": "mounted", "errno": None})
        source_owners_after: dict[str, dict[str, int]] = {}
        source_owners_after["/dev"] = _checked_visible_owner(
            directory_inspector(Path("/dev"), dev_parent, dev_owner), dev_owner, "/dev"
        )
        for parent_record, (_name, path_text, _major, _minor) in zip(source_observations["device_sources"], DEVICE_NODES):
            path = Path(path_text)
            owner = _visible_owner_record(
                parent_record,
                current_uid_map,
                current_gid_map,
                source_observations["overflow_uid"],
                source_observations["overflow_gid"],
            )
            source_owners_after[path_text] = _checked_visible_owner(
                device_inspector(path, parent_record, owner), owner, path_text
            )
        source_owners_after["/sys"] = _checked_visible_owner(
            directory_inspector(Path("/sys"), sys_parent, sys_owner), sys_owner, "/sys"
        )
        if source_owners_after != source_owners:
            raise RootlessMountProbeIntegrityError("mapped source owner or identity changed during mount probe")
        active_rows = tuple(mountinfo_reader())
        common._assert_private_propagation(active_rows)
        active_targets = common._mount_rows_for_target(active_rows)
        for target in mounted:
            if str(target) not in active_targets:
                raise RootlessMountProbeIntegrityError("successful rootless mount is absent from child mountinfo")
        after_network = network_reader(host_net_namespace, ip_path)
        if not _network_report_is_route_free(after_network, child_ids["net"]) or after_network != network:
            raise RootlessMountProbeIntegrityError("network namespace changed during rootless mount checks")
    finally:
        for target in reversed(mounted):
            try:
                umount_call(str(target), common.MNT_DETACH)
            except OSError as exc:
                cleanup_errors.append(f"{target.name}:{exc.errno or errno.EIO}")
        try:
            shutil.rmtree(temp_path)
        except OSError as exc:
            cleanup_errors.append(f"temp-root:{exc.errno or errno.EIO}")
    if cleanup_errors:
        raise RootlessMountProbeIntegrityError("failed to clean temporary rootless mounts: " + ",".join(cleanup_errors))
    after_rows = tuple(mountinfo_reader())
    if after_rows != before_rows:
        raise RootlessMountProbeIntegrityError("child mount table did not return to its original state")
    return {
        "schema_version": 1,
        "status": "UNSUPPORTED" if unsupported else "SUPPORTED",
        "namespace_ids": child_ids,
        "propagation_private": True,
        "network": network,
        "parent_source_observations": source_observations,
        "namespace_source_owners": source_owners,
        "mount_attempts": attempts,
        "mounts_cleaned": True,
        "device_sources": source_observations["device_sources"],
        "sys_source": source_observations["sys_source"],
        "bound_device_count": sum(
            item["status"] == "mounted" and item["name"].startswith("device-bind-") for item in attempts
        ),
        "whole_dev_bound": False,
        "run_source_bound": False,
        "child_mount_table_restored": True,
    }


def validate_probe_output(
    output: str,
    *,
    host_namespace_ids: Mapping[str, str],
    host_net_namespace: str,
    parent_source_observations: Mapping[str, Any],
    allow_synthetic: bool,
) -> tuple[str, dict[str, Any]] | None:
    lines = output.strip().splitlines()
    if len(lines) != 2 or lines[0] != PROBE_MARKER:
        return None
    try:
        report = json.loads(lines[1])
    except json.JSONDecodeError:
        return None
    fields = {
        "schema_version",
        "status",
        "namespace_ids",
        "propagation_private",
        "network",
        "parent_source_observations",
        "namespace_source_owners",
        "mount_attempts",
        "mounts_cleaned",
        "device_sources",
        "sys_source",
        "bound_device_count",
        "whole_dev_bound",
        "run_source_bound",
        "child_mount_table_restored",
    }
    if not isinstance(report, dict) or set(report) != fields:
        return None
    if (
        type(report.get("schema_version")) is not int
        or report["schema_version"] != 1
        or report.get("status") not in {"SUPPORTED", "UNSUPPORTED"}
        or report.get("propagation_private") is not True
        or report.get("mounts_cleaned") is not True
        or report.get("whole_dev_bound") is not False
        or report.get("run_source_bound") is not False
        or report.get("child_mount_table_restored") is not True
    ):
        return None
    namespace_ids = report.get("namespace_ids")
    if not isinstance(namespace_ids, dict) or set(namespace_ids) != set(common.NAMESPACE_KINDS):
        return None
    for kind in common.NAMESPACE_KINDS:
        identity = namespace_ids.get(kind)
        if (
            not isinstance(identity, str)
            or identity == host_namespace_ids.get(kind)
            or common._NAMESPACE_PATTERN.fullmatch(identity) is None
            or not identity.startswith(f"{kind}:")
        ):
            return None
    network = report.get("network")
    if not isinstance(network, dict) or network.get("namespace_id") == host_net_namespace:
        return None
    if not _network_report_is_route_free(network, namespace_ids["net"]):
        return None
    try:
        expected_sources = _validate_parent_source_observations(parent_source_observations)
    except RootlessMountProbeIntegrityError:
        return None
    if report.get("parent_source_observations") != expected_sources:
        return None
    if report.get("uid_map") is not None or report.get("gid_map") is not None:
        # Mapping rows are carried only inside the parent-bound observation
        # object. Reject stray fields so the evidence has one canonical form.
        return None
    source_owners = report.get("namespace_source_owners")
    expected_owner_paths = {"/dev", "/sys", *(path for _name, path, _major, _minor in DEVICE_NODES)}
    if not isinstance(source_owners, dict) or set(source_owners) != expected_owner_paths:
        return None
    expected_owner_records: dict[str, dict[str, int]] = {}
    expected_owner_records["/dev"] = _visible_owner_record(
        expected_sources["dev_directory"],
        expected_sources["uid_map"],
        expected_sources["gid_map"],
        expected_sources["overflow_uid"],
        expected_sources["overflow_gid"],
    )
    for record, (_name, path, _major, _minor) in zip(expected_sources["device_sources"], DEVICE_NODES):
        expected_owner_records[path] = _visible_owner_record(
            record,
            expected_sources["uid_map"],
            expected_sources["gid_map"],
            expected_sources["overflow_uid"],
            expected_sources["overflow_gid"],
        )
    expected_owner_records["/sys"] = _visible_owner_record(
        expected_sources["sys_source"],
        expected_sources["uid_map"],
        expected_sources["gid_map"],
        expected_sources["overflow_uid"],
        expected_sources["overflow_gid"],
    )
    for path, expected_owner in expected_owner_records.items():
        owner = source_owners.get(path)
        if not isinstance(owner, dict) or set(owner) != {"uid", "gid"} or (
            type(owner.get("uid")) is not int
            or type(owner.get("gid")) is not int
            or owner != expected_owner
        ):
            return None
    devices = report.get("device_sources")
    if devices != expected_sources["device_sources"] or report.get("sys_source") != expected_sources["sys_source"]:
        return None
    attempts = report.get("mount_attempts")
    if not isinstance(attempts, list) or len(attempts) != len(MOUNT_OPERATION_NAMES):
        return None
    states: dict[str, str] = {}
    denied: set[str] = set()
    for name, item in zip(MOUNT_OPERATION_NAMES, attempts):
        if not isinstance(item, dict) or set(item) != {"name", "status", "errno"} or item.get("name") != name:
            return None
        state = item.get("status")
        number = item.get("errno")
        if state == "mounted" and number is None:
            states[name] = "mounted"
        elif state == "denied" and type(number) is int and number in _CAPABILITY_ERRNOS:
            if number in _FILESYSTEM_ERRNOS and name not in {"proc", "tmpfs-dev", "tmpfs-run", "tmpfs-tmp"}:
                return None
            states[name] = "denied"
            denied.add(name)
        elif state == "blocked" and number is None and name in (_MOUNT_DEPENDENT_NAMES | _ROOT_DEPENDENT_NAMES):
            states[name] = "blocked"
        else:
            return None
    if states.get("target-root-self-bind") == "denied":
        if any(states.get(name) != "blocked" for name in _ROOT_DEPENDENT_NAMES):
            return None
    elif any(
        states.get(name) == "blocked" for name in (_ROOT_DEPENDENT_NAMES - _MOUNT_DEPENDENT_NAMES)
    ):
        return None
    elif states.get("tmpfs-dev") == "denied":
        if any(states.get(name) != "blocked" for name in _MOUNT_DEPENDENT_NAMES):
            return None
    elif any(states.get(name) == "blocked" for name in _MOUNT_DEPENDENT_NAMES):
        return None
    expected_bound_count = sum(states.get(name) == "mounted" for name in _MOUNT_DEPENDENT_NAMES)
    if type(report.get("bound_device_count")) is not int or report.get("bound_device_count") != expected_bound_count:
        return None
    expected_status = "UNSUPPORTED" if denied else "SUPPORTED"
    if report.get("status") != expected_status:
        return None
    if not allow_synthetic and any(
        not isinstance(value, str) or common._NAMESPACE_PATTERN.fullmatch(value) is None
        for value in host_namespace_ids.values()
    ):
        return None
    return expected_status, report


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 4:
        print(
            "usage: python -m usb_builder.rootless_mount_probe HOST_IDS_JSON HOST_NETNS IP_PATH SOURCE_EVIDENCE_JSON",
            file=sys.stderr,
        )
        return INTEGRITY_EXIT
    try:
        host_ids = json.loads(args[0])
        source_evidence = json.loads(args[3])
        if not isinstance(host_ids, dict) or not isinstance(source_evidence, dict):
            raise RootlessMountProbeIntegrityError("host namespace JSON is not an object")
        report = run_rootless_archiso_mount_probe(host_ids, args[1], args[2], source_evidence)
    except (OSError, ProbeError, RootlessMountProbeIntegrityError, ValueError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return INTEGRITY_EXIT
    print(PROBE_MARKER)
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return SUPPORTED_EXIT if report["status"] == "SUPPORTED" else UNSUPPORTED_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
