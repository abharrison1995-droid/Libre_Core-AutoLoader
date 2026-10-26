"""No-build tests for the fail-closed rootless offline launcher contract."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from usb_builder import network_probe, rootless_offline_preflight as offline


def _context() -> offline.LauncherProbeContext:
    return offline.LauncherProbeContext(
        username="fwbuild",
        uid=1000,
        gid=1000,
        primary_group_name="fwbuild",
        supplementary_gids=(),
        subuid_start=100000,
        subuid_count=65536,
        subgid_start=100000,
        subgid_count=65536,
        cap_bnd="00000000000000c0",
        host_namespace_ids=(("net", "net:[100]"), ("user", "user:[200]"), ("mnt", "mnt:[300]"), ("pid", "pid:[400]")),
        host_net_namespace="net:[100]",
        host_mountinfo_sha256="a" * 64,
        pacstrap_path="/usr/bin/pacstrap",
        pacstrap_sha256="b" * 64,
        pacstrap_version="31-2",
        arch_chroot_path="/usr/bin/arch-chroot",
        arch_chroot_sha256="c" * 64,
        arch_install_scripts_version="31-2",
    )


def _pin() -> offline.LauncherPin:
    return offline.LauncherPin(
        path=offline.PRODUCTION_LAUNCHER_PATH,
        sha256="d" * 64,
        mode=offline.PRODUCTION_LAUNCHER_MODE,
        owner_uid=0,
    )


def _network_view() -> dict[str, Any]:
    return {
        "namespace_id": "net:[101]",
        "sysfs_interfaces": ["lo"],
        "socket_interfaces": [[1, "lo"]],
        "link_rows": [{"ifindex": 1, "ifname": "lo", "flags": ["LOOPBACK"], "link_type": "loopback"}],
        "address_rows": [{"ifname": "lo", "addr_info": []}],
        "ipv4_routes": [],
        "ipv6_routes": [],
        "ipv4_rules": list(network_probe.IPV4_DEFAULT_RULES),
        "ipv6_rules": list(network_probe.IPV6_DEFAULT_RULES),
        "proc_dev": "Inter-| Receive | Transmit\n face | bytes\n lo: 0\n",
        "proc_ipv4_routes": "",
        "proc_ipv6_routes": "\n".join(
            " ".join(network_probe.IPV6_NULL_UNREACHABLE_ROW) for _ in range(2)
        )
        + "\n",
        "proc_if_inet6": "",
    }


def _report(probe_root: Path, *, token: str = "e" * 64) -> dict[str, Any]:
    context = _context()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(probe_root.parent), "LANG": "C", "LC_ALL": "C"}
    command = [
        offline.PRODUCTION_LAUNCHER_PATH,
        "--offline-preflight-v1",
        "--probe-root",
        str(probe_root / "target-root"),
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
    ]
    return {
        "schema": offline.REPORT_SCHEMA,
        "status": "SUPPORTED",
        "launcher_sha256": _pin().sha256,
        "probe_token": token,
        "probe_root": str(probe_root / "target-root"),
        "identity": {
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
        },
        "namespaces": {
            "host_net": context.host_net_namespace,
            "host_user": "user:[200]",
            "host_mnt": "mnt:[300]",
            "host_pid": "pid:[400]",
            "child_net": "net:[101]",
            "child_user": "user:[201]",
            "child_mnt": "mnt:[301]",
            "child_pid": "pid:[401]",
            "map_depth": 1,
        },
        "network_before": _network_view(),
        "network_after": _network_view(),
        "inherited_network_fds": [],
        "stdio": {
            "stdin_devnull": True,
            "stdout_pipe": True,
            "stderr_merged": True,
            "close_fds": True,
            "tty_attached": False,
        },
        "mounts": {
            "host_before_sha256": context.host_mountinfo_sha256,
            "host_after_sha256": context.host_mountinfo_sha256,
            "operations": list(offline.EXPECTED_MOUNT_OPERATIONS),
            "remaining_mounts": [],
        },
        "compatibility": {
            "pacstrap_path": context.pacstrap_path,
            "pacstrap_sha256": context.pacstrap_sha256,
            "pacstrap_version": context.pacstrap_version,
            "arch_chroot_path": context.arch_chroot_path,
            "arch_chroot_sha256": context.arch_chroot_sha256,
            "arch_install_scripts_version": context.arch_install_scripts_version,
            "pacstrap_n_help_argv": [context.pacstrap_path, "-N", "--help"],
            "pacstrap_n_help_returncode": 0,
            "pacstrap_n_operational_compatibility": "UNVALIDATED",
            "arch_chroot_n_argv": [context.arch_chroot_path, "-N", str(probe_root / "target-root"), "/usr/bin/true"],
            "arch_chroot_n_returncode": 0,
            "mapping_depth": 1,
            "package_or_build_actions": False,
        },
        "invocation": {
            "argv": command,
            "environment_keys": sorted(env),
            "environment_sha256": hashlib.sha256(
                __import__("json").dumps(env, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "stdin": "DEVNULL",
            "stdout": "PIPE",
            "stderr": "STDOUT",
            "close_fds": True,
            "timeout_seconds": offline.PROBE_TIMEOUT_SECONDS,
        },
    }


def _expected_invocation(report: dict[str, Any]) -> dict[str, Any]:
    return report["invocation"]


def test_missing_production_launcher_pin_fails_before_subprocess_or_scratch(tmp_path, monkeypatch):
    assert offline.PRODUCTION_LAUNCHER_PIN is None
    monkeypatch.setattr(offline.tempfile, "mkdtemp", lambda **_kwargs: pytest.fail("scratch created"))

    def runner(*_args, **_kwargs):
        pytest.fail("launcher subprocess started")

    with pytest.raises(offline.OfflineLauncherPreflightError, match="OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE"):
        offline.preflight(host=object(), mount={}, tool_pins={}, scratch_parent=tmp_path, runner=runner)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"path": "/tmp/unreviewed-helper"}, "pin is incomplete"),
        ({"sha256": "bad"}, "pin is incomplete"),
        ({"owner_uid": 1000}, "pin is incomplete"),
        ({"mode": 0}, "pin is incomplete"),
        ({"mode": 0o755}, "pin is incomplete"),
    ],
)
def test_launcher_pin_rejects_wrong_path_or_unpinned_identity(mutation, message):
    values = {
        "path": offline.PRODUCTION_LAUNCHER_PATH,
        "sha256": "d" * 64,
        "mode": 0o4755,
        "owner_uid": 0,
    }
    values.update(mutation)
    with pytest.raises(offline.OfflineLauncherPreflightError, match=message):
        offline._validate_pin(offline.LauncherPin(**values))


def test_valid_fixed_posix_launcher_pin_is_host_independent():
    # This validates Linux deployment metadata even when the test host is
    # Windows, where pathlib.Path('/usr/local/...').is_absolute() is false.
    offline._validate_pin(_pin())


@pytest.mark.parametrize(
    ("is_regular", "owner_uid", "mode", "sha256"),
    [
        (False, 0, 0o4755, "d" * 64),
        (True, 1000, 0o4755, "d" * 64),
        (True, 0, 0o755, "d" * 64),
        (True, 0, 0o4755, "f" * 64),
    ],
)
def test_launcher_observation_rejects_type_owner_mode_or_hash_drift(is_regular, owner_uid, mode, sha256):
    with pytest.raises(offline.OfflineLauncherPreflightError, match="owner, mode, type, or hash"):
        offline.validate_helper_observation(
            _pin(), is_regular=is_regular, owner_uid=owner_uid, mode=mode, sha256=sha256
        )


@pytest.mark.parametrize(
    ("is_directory", "is_symlink", "owner_uid", "mode"),
    [
        (False, False, 0, 0o755),
        (True, True, 0, 0o755),
        (True, False, 1000, 0o755),
        (True, False, 0, 0o775),
    ],
)
def test_launcher_install_parent_rejects_untrusted_directory(is_directory, is_symlink, owner_uid, mode):
    with pytest.raises(offline.OfflineLauncherPreflightError, match="launcher parent"):
        offline.validate_launcher_parent_observation(
            is_directory=is_directory, is_symlink=is_symlink, owner_uid=owner_uid, mode=mode
        )


def test_launcher_install_parent_accepts_root_owned_nonwritable_directory():
    offline.validate_launcher_parent_observation(
        is_directory=True, is_symlink=False, owner_uid=0, mode=0o755
    )


@pytest.mark.parametrize(
    ("is_directory", "is_symlink", "owner_uid", "mode"),
    [
        (False, False, 0, 0o1777),
        (True, True, 0, 0o1777),
        (True, False, 1000, 0o1777),
        (True, False, 0, 0o0777),
        (True, False, 0, 0o1755),
    ],
)
def test_scratch_parent_rejects_non_root_sticky_or_unprotected_directory(is_directory, is_symlink, owner_uid, mode):
    with pytest.raises(offline.OfflineLauncherPreflightError, match="scratch parent"):
        offline.validate_scratch_parent_observation(
            is_directory=is_directory, is_symlink=is_symlink, owner_uid=owner_uid, mode=mode
        )


def test_scratch_parent_requires_root_owned_sticky_temp_directory():
    offline.validate_scratch_parent_observation(
        is_directory=True, is_symlink=False, owner_uid=0, mode=0o1777
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"uid": 0}, "dedicated nonroot account"),
        ({"gid": 0}, "dedicated nonroot account"),
        ({"primary_group_name": "users"}, "dedicated nonroot account"),
        ({"supplementary_gids": (5,)}, "dedicated nonroot account"),
        ({"subuid_count": 1}, "subordinate UID/GID"),
        ({"cap_eff": "0000000000000001"}, "capability state"),
        ({"cap_bnd": "bad"}, "bounding set"),
    ],
)
def test_launcher_context_rejects_identity_mapping_or_capability_changes(mutation, message):
    host_fields = {
        "username": "fwbuild",
        "uid": 1000,
        "gid": 1000,
        "primary_group_name": "fwbuild",
        "supplementary_gids": (),
        "subuid_start": 100000,
        "subuid_count": 65536,
        "subgid_start": 100000,
        "subgid_count": 65536,
        "cap_eff": "0000000000000000",
        "cap_prm": "0000000000000000",
        "cap_amb": "0000000000000000",
        "cap_bnd": "00000000000000c0",
        "no_new_privs": 0,
    }
    host_fields.update(mutation)
    host = SimpleNamespace(**host_fields)
    mount = {
        "status": "SUPPORTED",
        "host_namespace_ids": {
            "net": "net:[100]",
            "user": "user:[200]",
            "mnt": "mnt:[300]",
            "pid": "pid:[400]",
        },
        "host_mount_table_sha256": "a" * 64,
    }
    tool_pins = {
        "pacstrap": {"path": "/usr/bin/pacstrap", "sha256": "b" * 64, "version": "31-2"},
        "arch-chroot": {"path": "/usr/bin/arch-chroot", "sha256": "c" * 64, "version": "31-2"},
    }
    with pytest.raises(offline.OfflineLauncherPreflightError, match=message):
        offline._context_from_host(host, mount, tool_pins)


def test_launcher_context_requires_complete_host_namespace_map():
    host = SimpleNamespace(
        username="fwbuild",
        uid=1000,
        gid=1000,
        primary_group_name="fwbuild",
        supplementary_gids=(),
        subuid_start=100000,
        subuid_count=65536,
        subgid_start=100000,
        subgid_count=65536,
        cap_eff="0000000000000000",
        cap_prm="0000000000000000",
        cap_amb="0000000000000000",
        cap_bnd="00000000000000c0",
        no_new_privs=0,
    )
    mount = {
        "status": "SUPPORTED",
        "host_namespace_ids": {"net": "net:[100]"},
        "host_mount_table_sha256": "a" * 64,
    }
    tools = {
        "pacstrap": {"path": "/usr/bin/pacstrap", "sha256": "b" * 64, "version": "31-2"},
        "arch-chroot": {"path": "/usr/bin/arch-chroot", "sha256": "c" * 64, "version": "31-2"},
    }
    with pytest.raises(offline.OfflineLauncherPreflightError, match="full host namespace identity map"):
        offline._context_from_host(host, mount, tools)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data.pop("stdio"), "report is malformed"),
        (lambda data: data["identity"].update(uid=1001), "identity/capability drop"),
        (lambda data: data["identity"].update(primary_group_name="users"), "identity/capability drop"),
        (lambda data: data["namespaces"].update(map_depth=2), "distinct one-level child namespaces"),
        (lambda data: data["namespaces"].update(host_user="user:[202]"), "distinct one-level child namespaces"),
        (lambda data: data["namespaces"].update(child_net="net:[100]"), "distinct one-level child namespaces"),
        (lambda data: data["namespaces"].update(child_user="user:[200]"), "distinct one-level child namespaces"),
        (lambda data: data["namespaces"].update(child_mnt="mnt:[300]"), "distinct one-level child namespaces"),
        (lambda data: data["namespaces"].update(child_pid="pid:[400]"), "distinct one-level child namespaces"),
        (lambda data: data["namespaces"].update(child_net="net:[102]"), "not bound to the reported child"),
        (lambda data: data["network_before"].update(namespace_id="net:[102]"), "not bound to the reported child"),
        (lambda data: data["network_after"].update(namespace_id="net:[102]"), "not bound to the reported child"),
        (lambda data: data["network_before"]["ipv4_routes"].append({"dst": "default"}), "route-free network"),
        (lambda data: data["network_after"]["ipv4_routes"].append({"dst": "default"}), "route-free network"),
        (lambda data: data.update(inherited_network_fds=["socket:[9]"]), "file descriptors crossed"),
        (lambda data: data["stdio"].update(tty_attached=True), "stdio/descriptor boundary"),
        (lambda data: data["mounts"].update(host_after_sha256="f" * 64), "mount cleanup or host mount"),
        (lambda data: data["mounts"].update(remaining_mounts=["/dev"]), "mount cleanup or host mount"),
        (lambda data: data["compatibility"].update(pacstrap_n_help_argv=["/usr/bin/pacstrap", "-Syu"]), "-N compatibility"),
    ],
)
def test_launcher_report_rejects_route_drift_mount_leaks_and_unsafe_boundaries(tmp_path, mutation, message):
    report = _report(tmp_path)
    mutation(report)
    with pytest.raises(offline.OfflineLauncherPreflightError, match=message):
        offline.validate_report(
            report,
            pin=_pin(),
            context=_context(),
            probe_root=tmp_path,
            expected_invocation=_expected_invocation(report),
        )


def test_launcher_report_never_claims_pacstrap_compatibility_from_help_only(tmp_path):
    report = _report(tmp_path)
    with pytest.raises(offline.OfflineLauncherPreflightError, match="operational compatibility"):
        offline.validate_report(
            report,
            pin=_pin(),
            context=_context(),
            probe_root=tmp_path,
            expected_invocation=_expected_invocation(report),
        )


def test_bounded_preflight_runner_uses_fixed_argv_and_closed_stdio():
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "probe\n", "")

    command = [
        offline.PRODUCTION_LAUNCHER_PATH,
        "--offline-preflight-v1",
        "--probe-root",
        "/tmp/probe/target-root",
        "--uid",
        "1000",
        "--gid",
        "1000",
        "--subuid",
        "100000:65536",
        "--subgid",
        "100000:65536",
        "--token",
        "e" * 64,
    ]
    result = offline._run_bounded(command, runner=runner, env={"PATH": "/usr/bin:/bin"})
    assert result.returncode == 0
    assert len(calls) == 1
    _, kwargs = calls[0]
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.PIPE
    assert kwargs["stderr"] is subprocess.STDOUT
    assert kwargs["close_fds"] is True
    assert kwargs["timeout"] == offline.PROBE_TIMEOUT_SECONDS


def test_bounded_preflight_runner_rejects_build_or_arbitrary_command_before_child():
    def runner(*_args, **_kwargs):
        pytest.fail("arbitrary command executed")

    with pytest.raises(offline.OfflineLauncherPreflightError, match="preflight-only launcher protocol"):
        offline._run_bounded(["/usr/bin/pacman", "-Syu"], runner=runner, env={})


@pytest.mark.skipif(os.name != "posix", reason="UNVALIDATED: process-group cleanup requires POSIX signals")
def test_bounded_timeout_kills_launcher_and_lingering_same_group_descendant(tmp_path, monkeypatch):
    launcher = tmp_path / "preflight-launcher"
    probe_root = tmp_path / "probe-root"
    probe_root.mkdir()
    launcher.write_text(
        "#!" + sys.executable + "\n"
        "import pathlib, signal, subprocess, sys, time\n"
        "root = pathlib.Path(sys.argv[sys.argv.index('--probe-root') + 1])\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'])\n"
        "(root / 'child.pid').write_text(str(child.pid))\n"
        "while True: time.sleep(1)\n",
        encoding="utf-8",
    )
    os.chmod(launcher, 0o755)
    monkeypatch.setattr(offline, "PRODUCTION_LAUNCHER_PATH", str(launcher))
    monkeypatch.setattr(offline, "TERMINATION_GRACE_SECONDS", 0.1)
    command = [
        str(launcher),
        "--offline-preflight-v1",
        "--probe-root",
        str(probe_root),
        "--uid",
        "1000",
        "--gid",
        "1000",
        "--subuid",
        "100000:65536",
        "--subgid",
        "100000:65536",
        "--token",
        "e" * 64,
    ]
    with pytest.raises(subprocess.TimeoutExpired):
        offline._run_bounded(command, runner=subprocess.run, env={"PATH": os.environ.get("PATH", "")}, timeout=0.3)
    child_pid = int((probe_root / "child.pid").read_text(encoding="ascii"))
    for _ in range(40):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        status_path = Path(f"/proc/{child_pid}/stat")
        if status_path.exists() and status_path.read_text(encoding="ascii").split()[2] == "Z":
            break
        import time

        time.sleep(0.05)
    else:
        pytest.fail("same-process-group descendant survived bounded cleanup")

