"""No-build preflight contract for a future rootless offline launcher.

This module intentionally does not provide an offline build runner.  Until a
root-owned launcher and its authorization policy have an exact reviewed pin,
the production contract remains ``None`` and :func:`preflight` stops before it
creates scratch paths or starts a child process.

The validators below define the evidence a launcher must return before a
future implementation can be considered.  They are exercised with synthetic
fixtures only; those tests do not establish that the host has a launcher or
that offline builds are supported.
"""

from __future__ import annotations

import hashlib
import errno
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, NoReturn, Sequence, cast

from usb_builder import network_probe


REPORT_MARKER = "LIBRECORE_ROOTLESS_OFFLINE_PREFLIGHT_V1"
REPORT_SCHEMA = 1
# This is the reviewed deployment location. There is deliberately no
# executable/hash/mode pin yet; an installed file here is not trusted until
# its source, authorization rule, and exact bytes receive review.
PRODUCTION_LAUNCHER_PATH = "/usr/local/libexec/librecore-rootless-offline-preflight"
PRODUCTION_LAUNCHER_PIN: "LauncherPin | None" = None
# The fixed helper creates the fresh network/mount/PID namespaces with root
# privilege before dropping to the dedicated build identity. Its reviewed
# deployment mode must therefore be root-owned setuid, without writable
# parent directories.
PRODUCTION_LAUNCHER_MODE = 0o4755
PROBE_TIMEOUT_SECONDS = 90.0
TERMINATION_GRACE_SECONDS = 5.0
EXPECTED_MOUNT_OPERATIONS = (
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


class OfflineLauncherPreflightError(RuntimeError):
    """A missing or invalid offline launcher preflight condition."""


@dataclass(frozen=True)
class LauncherPin:
    """Exact root-owned launcher executable identity supplied by reviewed policy."""

    path: str
    sha256: str
    mode: int
    owner_uid: int = 0


@dataclass(frozen=True)
class LauncherProbeContext:
    username: str
    uid: int
    gid: int
    primary_group_name: str
    supplementary_gids: tuple[int, ...]
    subuid_start: int
    subuid_count: int
    subgid_start: int
    subgid_count: int
    cap_bnd: str
    host_namespace_ids: tuple[tuple[str, str], ...]
    host_net_namespace: str
    host_mountinfo_sha256: str
    pacstrap_path: str
    pacstrap_sha256: str
    pacstrap_version: str
    arch_chroot_path: str
    arch_chroot_sha256: str
    arch_install_scripts_version: str


def _fail(message: str) -> NoReturn:
    raise OfflineLauncherPreflightError(message)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_pin(pin: LauncherPin) -> None:
    # The deployment pin is a Linux/POSIX path even when validators are run
    # from a Windows test host.  pathlib.Path follows the host convention and
    # would treat `/usr/local/...` as a Windows-relative path there.
    path = PurePosixPath(pin.path)
    if (
        str(path) != PRODUCTION_LAUNCHER_PATH
        or not path.is_absolute()
        or re.fullmatch(r"[0-9a-f]{64}", pin.sha256) is None
        or type(pin.mode) is not int
        or pin.mode != PRODUCTION_LAUNCHER_MODE
        or pin.owner_uid != 0
    ):
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: launcher pin is incomplete or not root-owned")


def validate_launcher_parent_observation(
    *, is_directory: bool, is_symlink: bool, owner_uid: int, mode: int
) -> None:
    """Require a root-controlled directory for the privileged helper path."""
    if not is_directory or is_symlink or owner_uid != 0 or mode & 0o022:
        _fail(
            "OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: launcher parent must be a root-owned, "
            "non-link directory not writable by group or other"
        )


def validate_scratch_parent_observation(
    *, is_directory: bool, is_symlink: bool, owner_uid: int, mode: int
) -> None:
    """Require the root-owned sticky temporary directory used for private scratch."""
    if not is_directory or is_symlink or owner_uid != 0 or mode != 0o1777:
        _fail(
            "OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: scratch parent must be a root-owned "
            "non-link directory with exact sticky temporary-directory mode 01777"
        )


def validate_helper_observation(
    pin: LauncherPin,
    *,
    is_regular: bool,
    owner_uid: int,
    mode: int,
    sha256: str,
) -> None:
    """Compare an observed helper against its exact root-owned pin."""
    _validate_pin(pin)
    if (
        not is_regular
        or owner_uid != pin.owner_uid
        or mode != pin.mode
        or sha256 != pin.sha256
    ):
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: launcher owner, mode, type, or hash differs from its pin")


def _assert_secure_root_owned_parent_components(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    components = (None, *absolute.parts[1:-1])
    for part in components:
        if part is not None:
            current = current / part
        try:
            info = current.lstat()
        except OSError as exc:
            _fail(f"OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: launcher path component is unavailable: {current}")
        attrs = getattr(info, "st_file_attributes", 0)
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        validate_launcher_parent_observation(
            is_directory=stat.S_ISDIR(info.st_mode),
            is_symlink=stat.S_ISLNK(info.st_mode) or bool(attrs & reparse),
            owner_uid=info.st_uid,
            mode=stat.S_IMODE(info.st_mode),
        )


def _validate_temp_scratch_parent(path: Path) -> Path:
    if os.name != "posix" or not path.is_absolute():
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: scratch parent must be an absolute POSIX temp directory")
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in (None, *absolute.parts[1:-1]):
        if part is not None:
            current = current / part
        try:
            info = current.lstat()
        except OSError as exc:
            raise OfflineLauncherPreflightError(
                f"OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: scratch parent component is unavailable: {current}"
            ) from exc
        attrs = getattr(info, "st_file_attributes", 0)
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        validate_launcher_parent_observation(
            is_directory=stat.S_ISDIR(info.st_mode),
            is_symlink=stat.S_ISLNK(info.st_mode) or bool(attrs & reparse),
            owner_uid=info.st_uid,
            mode=stat.S_IMODE(info.st_mode),
        )
    try:
        info = absolute.lstat()
    except OSError as exc:
        raise OfflineLauncherPreflightError(
            "OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: scratch parent is unavailable"
        ) from exc
    attrs = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    validate_scratch_parent_observation(
        is_directory=stat.S_ISDIR(info.st_mode),
        is_symlink=stat.S_ISLNK(info.st_mode) or bool(attrs & reparse),
        owner_uid=info.st_uid,
        mode=stat.S_IMODE(info.st_mode),
    )
    return absolute


def _assert_no_symlink_components(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except OSError as exc:
            _fail(f"OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: launcher path component is unavailable: {current}")
        attrs = getattr(info, "st_file_attributes", 0)
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if stat.S_ISLNK(info.st_mode) or attrs & reparse:
            _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: launcher path contains a symlink or reparse point")


def inspect_launcher(pin: LauncherPin) -> dict[str, Any]:
    """Read the pinned launcher identity without executing it."""
    _validate_pin(pin)
    path = Path(pin.path)
    _assert_no_symlink_components(path)
    _assert_secure_root_owned_parent_components(path)
    try:
        info = path.lstat()
    except OSError as exc:
        raise OfflineLauncherPreflightError(
            "OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: pinned launcher is missing"
        ) from exc
    validate_helper_observation(
        pin,
        is_regular=stat.S_ISREG(info.st_mode),
        owner_uid=info.st_uid,
        mode=stat.S_IMODE(info.st_mode),
        sha256=_sha256_file(path),
    )
    return {
        "path": str(path),
        "sha256": pin.sha256,
        "mode": stat.S_IMODE(info.st_mode),
        "owner_uid": info.st_uid,
    }


def _host_value(host: Any, name: str) -> Any:
    try:
        return getattr(host, name)
    except AttributeError as exc:
        raise OfflineLauncherPreflightError(
            f"OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: validated host evidence lacks {name}"
        ) from exc


def _context_from_host(host: Any, mount: Mapping[str, Any], tools: Mapping[str, Mapping[str, str]]) -> LauncherProbeContext:
    uid = _host_value(host, "uid")
    gid = _host_value(host, "gid")
    username = str(_host_value(host, "username"))
    primary_group_name = str(_host_value(host, "primary_group_name"))
    supplementary = tuple(_host_value(host, "supplementary_gids"))
    if (
        type(uid) is not int
        or uid <= 0
        or type(gid) is not int
        or gid <= 0
        or primary_group_name != username
        or supplementary
    ):
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: launcher requires the validated dedicated nonroot account")
    if (
        _host_value(host, "subuid_count") < 65536
        or _host_value(host, "subgid_count") < 65536
        or _host_value(host, "subuid_start") <= 0
        or _host_value(host, "subgid_start") <= 0
    ):
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: subordinate UID/GID ranges are insufficient")
    status = str(_host_value(host, "cap_eff")), str(_host_value(host, "cap_prm")), str(_host_value(host, "cap_amb"))
    if any(value != "0000000000000000" for value in status) or _host_value(host, "no_new_privs") != 0:
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: invoking account capability state is not the pinned rootless state")
    cap_bnd = str(_host_value(host, "cap_bnd"))
    if re.fullmatch(r"[0-9a-f]{16}", cap_bnd) is None:
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: invoking account capability bounding set is malformed")
    host_namespaces = mount.get("host_namespace_ids")
    namespace_kinds = ("net", "user", "mnt", "pid")
    mount_hash = mount.get("host_mount_table_sha256")
    if not isinstance(host_namespaces, Mapping) or set(host_namespaces) != set(namespace_kinds):
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: full host namespace identity map is required")
    if any(
        not isinstance(host_namespaces.get(kind), str)
        or re.fullmatch(rf"{kind}:\[\d+\]", host_namespaces[kind]) is None
        for kind in namespace_kinds
    ):
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: host namespace identity map is malformed")
    host_net = host_namespaces["net"]
    if (
        mount.get("status") != "SUPPORTED"
        or not isinstance(host_net, str)
        or re.fullmatch(r"net:\[\d+\]", host_net) is None
        or not isinstance(mount_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", mount_hash) is None
    ):
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: exact rootless mount and namespace preflight is required")
    pacstrap = tools.get("pacstrap")
    arch_chroot = tools.get("arch-chroot")
    if not isinstance(pacstrap, Mapping) or not isinstance(arch_chroot, Mapping):
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: pinned pacstrap and arch-chroot identities are required")
    if (
        set(pacstrap) != {"path", "sha256", "version"}
        or set(arch_chroot) != {"path", "sha256", "version"}
        or pacstrap["path"] != "/usr/bin/pacstrap"
        or arch_chroot["path"] != "/usr/bin/arch-chroot"
        or re.fullmatch(r"[0-9a-f]{64}", str(pacstrap["sha256"])) is None
        or re.fullmatch(r"[0-9a-f]{64}", str(arch_chroot["sha256"])) is None
        or not pacstrap["version"]
        or not arch_chroot["version"]
    ):
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: pacstrap/arch-chroot pins are malformed or unexpected")
    return LauncherProbeContext(
        username=username,
        uid=uid,
        gid=gid,
        primary_group_name=primary_group_name,
        supplementary_gids=supplementary,
        subuid_start=int(_host_value(host, "subuid_start")),
        subuid_count=int(_host_value(host, "subuid_count")),
        subgid_start=int(_host_value(host, "subgid_start")),
        subgid_count=int(_host_value(host, "subgid_count")),
        cap_bnd=cap_bnd,
        host_namespace_ids=tuple((kind, host_namespaces[kind]) for kind in namespace_kinds),
        host_net_namespace=host_net,
        host_mountinfo_sha256=mount_hash,
        pacstrap_path=str(pacstrap["path"]),
        pacstrap_sha256=str(pacstrap["sha256"]),
        pacstrap_version=str(pacstrap["version"]),
        arch_chroot_path=str(arch_chroot["path"]),
        arch_chroot_sha256=str(arch_chroot["sha256"]),
        arch_install_scripts_version=str(arch_chroot["version"]),
    )


def _validate_network_view(view: Any, host_net: str, child_net: str) -> None:
    expected = {
        "namespace_id",
        "sysfs_interfaces",
        "socket_interfaces",
        "link_rows",
        "address_rows",
        "ipv4_routes",
        "ipv6_routes",
        "ipv4_rules",
        "ipv6_rules",
        "proc_dev",
        "proc_ipv4_routes",
        "proc_ipv6_routes",
        "proc_if_inet6",
    }
    if not isinstance(view, dict) or set(view) != expected:
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: launcher network observation schema is incomplete")
    if view["namespace_id"] != child_net:
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: network view is not bound to the reported child network namespace")
    socket_interfaces = view["socket_interfaces"]
    if not isinstance(socket_interfaces, (tuple, list)) or any(
        not isinstance(row, (tuple, list)) or len(row) != 2 for row in socket_interfaces
    ):
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: launcher socket interface report is malformed")
    try:
        network_probe.validate_namespace_views(
            host_namespace=host_net,
            child_namespace=view["namespace_id"],
            sysfs_interfaces=view["sysfs_interfaces"],
            socket_interfaces=tuple((row[0], row[1]) for row in socket_interfaces),
            link_rows=view["link_rows"],
            address_rows=view["address_rows"],
            ipv4_routes=view["ipv4_routes"],
            ipv6_routes=view["ipv6_routes"],
            ipv4_rules=view["ipv4_rules"],
            ipv6_rules=view["ipv6_rules"],
            proc_dev=view["proc_dev"],
            proc_ipv4_routes=view["proc_ipv4_routes"],
            proc_ipv6_routes=view["proc_ipv6_routes"],
            proc_if_inet6=view["proc_if_inet6"],
        )
    except (KeyError, TypeError, ValueError, network_probe.ProbeError) as exc:
        raise OfflineLauncherPreflightError(
            "OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: launcher did not prove a route-free network namespace"
        ) from exc


def validate_report(
    report: Any,
    *,
    pin: LauncherPin,
    context: LauncherProbeContext,
    probe_root: Path,
    expected_invocation: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate strict launcher evidence; synthetic callers do not imply support."""
    expected_keys = {
        "schema",
        "status",
        "launcher_sha256",
        "probe_token",
        "probe_root",
        "identity",
        "namespaces",
        "network_before",
        "network_after",
        "inherited_network_fds",
        "stdio",
        "mounts",
        "compatibility",
        "invocation",
    }
    if not isinstance(report, dict) or set(report) != expected_keys:
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: launcher report is malformed")
    if report["invocation"] != dict(expected_invocation):
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: launcher invocation/stdio policy is not exact")
    if report["schema"] != REPORT_SCHEMA or report["status"] != "SUPPORTED" or report["launcher_sha256"] != pin.sha256:
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: launcher report is not bound to the pinned helper")
    token = report["probe_token"]
    if not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{64}", token) is None:
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: launcher probe token is malformed")
    expected_root = str(probe_root / "target-root")
    if report["probe_root"] != expected_root:
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: probe root escaped its operation-owned scratch directory")

    identity = report["identity"]
    expected_identity = {
        "username": context.username,
        "uid": context.uid,
        "gid": context.gid,
        "primary_group_name": context.primary_group_name,
        "supplementary_gids": [],
        "subuid_start": context.subuid_start,
        "subuid_count": context.subuid_count,
        "subgid_start": context.subgid_start,
        "subgid_count": context.subgid_count,
        "cap_bnd": context.cap_bnd,
        "cap_eff": "0000000000000000",
        "cap_prm": "0000000000000000",
        "cap_amb": "0000000000000000",
        "no_new_privs": 1,
    }
    if not isinstance(identity, dict) or identity != expected_identity:
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: child identity/capability drop differs from the dedicated account")

    namespaces = report["namespaces"]
    expected_namespace_keys = {
        "host_net",
        "host_user",
        "host_mnt",
        "host_pid",
        "child_net",
        "child_user",
        "child_mnt",
        "child_pid",
        "map_depth",
    }
    host_namespace_map = dict(context.host_namespace_ids)
    expected_host_namespace_fields = {
        "host_net": host_namespace_map["net"],
        "host_user": host_namespace_map["user"],
        "host_mnt": host_namespace_map["mnt"],
        "host_pid": host_namespace_map["pid"],
    }
    child_namespace_fields = {
        "child_net": "net",
        "child_user": "user",
        "child_mnt": "mnt",
        "child_pid": "pid",
    }
    if (
        not isinstance(namespaces, dict)
        or set(namespaces) != expected_namespace_keys
        or any(namespaces.get(field) != value for field, value in expected_host_namespace_fields.items())
        or namespaces.get("map_depth") != 1
        or any(
            not isinstance(namespaces.get(child_field), str)
            or re.fullmatch(rf"{kind}:\[\d+\]", namespaces[child_field]) is None
            or namespaces[child_field] == host_namespace_map[kind]
            for child_field, kind in child_namespace_fields.items()
        )
    ):
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: launcher did not prove distinct one-level child namespaces")

    _validate_network_view(report["network_before"], context.host_net_namespace, namespaces["child_net"])
    _validate_network_view(report["network_after"], context.host_net_namespace, namespaces["child_net"])
    if report["network_before"] != report["network_after"]:
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: network interfaces/routes/rules drifted during probe")
    if report["inherited_network_fds"] != []:
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: network file descriptors crossed the launcher boundary")
    if report["stdio"] != {
        "stdin_devnull": True,
        "stdout_pipe": True,
        "stderr_merged": True,
        "close_fds": True,
        "tty_attached": False,
    }:
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: launcher stdio/descriptor boundary is not closed")

    mounts = report["mounts"]
    if (
        not isinstance(mounts, dict)
        or set(mounts) != {"host_before_sha256", "host_after_sha256", "operations", "remaining_mounts"}
        or mounts.get("host_before_sha256") != context.host_mountinfo_sha256
        or mounts.get("host_after_sha256") != context.host_mountinfo_sha256
        or mounts.get("operations") != list(EXPECTED_MOUNT_OPERATIONS)
        or mounts.get("remaining_mounts") != []
    ):
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: probe mount cleanup or host mount invariance failed")

    compatibility = report["compatibility"]
    expected_compatibility = {
        "pacstrap_path": context.pacstrap_path,
        "pacstrap_sha256": context.pacstrap_sha256,
        "pacstrap_version": context.pacstrap_version,
        "arch_chroot_path": context.arch_chroot_path,
        "arch_chroot_sha256": context.arch_chroot_sha256,
        "arch_install_scripts_version": context.arch_install_scripts_version,
        "pacstrap_n_help_argv": [context.pacstrap_path, "-N", "--help"],
        "pacstrap_n_help_returncode": 0,
        "pacstrap_n_operational_compatibility": "UNVALIDATED",
        "arch_chroot_n_argv": [context.arch_chroot_path, "-N", expected_root, "/usr/bin/true"],
        "arch_chroot_n_returncode": 0,
        "mapping_depth": 1,
        "package_or_build_actions": False,
    }
    if not isinstance(compatibility, dict) or compatibility != expected_compatibility:
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: pinned -N compatibility evidence is incomplete or changed")
    _fail(
        "OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: pacstrap -N operational compatibility requires a controlled "
        "disposable-VM package-input proof; --help parsing is insufficient"
    )


def _run_bounded(
    command: Sequence[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
    env: Mapping[str, str],
    timeout: float = PROBE_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Run only the fixed launcher probe with closed stdio and group cleanup."""
    if not command or any(not isinstance(part, str) or not part for part in command):
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: fixed launcher probe argv is malformed")
    if (
        len(command) != 14
        or command[0] != PRODUCTION_LAUNCHER_PATH
        or command[1] != "--offline-preflight-v1"
        or command[2] != "--probe-root"
        or command[4] != "--uid"
        or command[6] != "--gid"
        or command[8] != "--subuid"
        or command[10] != "--subgid"
        or command[12] != "--token"
        or re.fullmatch(r"[0-9]+", command[5]) is None
        or re.fullmatch(r"[0-9]+", command[7]) is None
        or re.fullmatch(r"[0-9]+:[0-9]+", command[9]) is None
        or re.fullmatch(r"[0-9]+:[0-9]+", command[11]) is None
        or re.fullmatch(r"[0-9a-f]{64}", command[13]) is None
    ):
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: command is outside the fixed preflight-only launcher protocol")
    kwargs: dict[str, Any] = {
        "check": False,
        "text": True,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
        "env": dict(env),
        "timeout": timeout,
    }
    if runner is not subprocess.run:
        return runner(list(command), **kwargs)
    killpg_candidate = getattr(os, "killpg", None)
    sigterm_candidate = getattr(signal, "SIGTERM", None)
    sigkill_candidate = getattr(signal, "SIGKILL", None)
    if (
        os.name != "posix"
        or not callable(killpg_candidate)
        or not isinstance(sigterm_candidate, int)
        or not isinstance(sigkill_candidate, int)
    ):
        _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: bounded process-group cleanup is unavailable")
    killpg = cast(Callable[[int, int], None], killpg_candidate)
    sigterm = sigterm_candidate
    sigkill = sigkill_candidate
    child = subprocess.Popen(
        list(command),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        env=dict(env),
        start_new_session=True,
    )
    try:
        stdout, _ = child.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as timeout_error:
        def signal_group(number: int) -> None:
            try:
                killpg(child.pid, number)
            except ProcessLookupError:
                pass
            except OSError as exc:
                if exc.errno != errno.ESRCH:
                    raise

        try:
            signal_group(sigterm)
            try:
                child.communicate(timeout=TERMINATION_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                pass
            finally:
                # Always kill the group after grace; the leader may have exited
                # while a descendant still holds the captured pipe open.
                signal_group(sigkill)
        except OSError as exc:
            _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: timed-out probe group could not be terminated")
        try:
            child.communicate(timeout=TERMINATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired as cleanup_error:
            _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: timed-out probe process group could not be reaped")
        raise subprocess.TimeoutExpired(command, timeout, output=timeout_error.output) from timeout_error
    return subprocess.CompletedProcess(list(command), child.returncode, stdout, "")


def preflight(
    *,
    host: Any,
    mount: Mapping[str, Any],
    tool_pins: Mapping[str, Mapping[str, str]],
    scratch_parent: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Run a fixed no-build helper probe or fail closed when no exact pin exists."""
    pin = PRODUCTION_LAUNCHER_PIN
    if pin is None:
        _fail(
            "OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: no reviewed root-owned launcher identity/authorization pin exists "
            f"for {PRODUCTION_LAUNCHER_PATH}"
        )
    _validate_pin(pin)
    helper_evidence = inspect_launcher(pin)
    context = _context_from_host(host, mount, tool_pins)
    parent = _validate_temp_scratch_parent(scratch_parent)

    scratch = Path(tempfile.mkdtemp(prefix="lc-offline-preflight-", dir=parent))
    try:
        scratch_info = scratch.lstat()
        if (
            not stat.S_ISDIR(scratch_info.st_mode)
            or stat.S_ISLNK(scratch_info.st_mode)
            or scratch_info.st_uid != context.uid
            or stat.S_IMODE(scratch_info.st_mode) != 0o700
        ):
            _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: operation-owned probe root is unsafe")
        probe_root = scratch / "target-root"
        probe_root.mkdir(mode=0o700)
        token = os.urandom(32).hex()
        command = (
            pin.path,
            "--offline-preflight-v1",
            "--probe-root",
            str(probe_root),
            "--uid",
            str(context.uid),
            "--gid",
            str(context.gid),
            "--subuid",
            f"{context.subuid_start}:{context.subuid_count}",
            "--subgid",
            f"{context.subgid_start}:{context.subgid_count}",
            "--token",
            token,
        )
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(scratch),
            "LANG": "C",
            "LC_ALL": "C",
        }
        result = _run_bounded(command, runner=runner, env=env)
        if result.returncode != 0 or not isinstance(result.stdout, str):
            _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: fixed no-build launcher probe failed")
        lines = result.stdout.strip().splitlines()
        if len(lines) != 2 or lines[0] != REPORT_MARKER:
            _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: fixed launcher report marker is malformed")
        try:
            report = json.loads(lines[1])
        except json.JSONDecodeError as exc:
            raise OfflineLauncherPreflightError(
                "OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: fixed launcher report is invalid JSON"
            ) from exc
        if not isinstance(report, dict) or report.get("probe_token") != token:
            _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: launcher report does not bind the fresh probe token")
        expected_invocation = {
            "argv": list(command),
            "environment_keys": sorted(env),
            "environment_sha256": hashlib.sha256(
                json.dumps(env, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "stdin": "DEVNULL",
            "stdout": "PIPE",
            "stderr": "STDOUT",
            "close_fds": True,
            "timeout_seconds": PROBE_TIMEOUT_SECONDS,
        }
        report = validate_report(
            report,
            pin=pin,
            context=context,
            probe_root=scratch,
            expected_invocation=expected_invocation,
        )
        if inspect_launcher(pin) != helper_evidence:
            _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: launcher identity changed during preflight")
        return report
    finally:
        # Only remove the exact new scratch child after checking it remains a
        # direct child of the validated parent and is still owned by the caller.
        try:
            current = scratch.lstat()
        except FileNotFoundError:
            _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: launcher removed the operation-owned probe root")
        if (
            scratch.parent != parent
            or not stat.S_ISDIR(current.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or current.st_uid != context.uid
        ):
            _fail("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: operation-owned probe root changed before cleanup")
        try:
            shutil.rmtree(scratch)
        except OSError as exc:
            raise OfflineLauncherPreflightError(
                "OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: operation-owned probe root cleanup failed"
            ) from exc

