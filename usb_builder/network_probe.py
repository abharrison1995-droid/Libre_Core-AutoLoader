"""Read-only verification of a route-free Linux network namespace.

This module is launched under util-linux ``unshare`` by the offline builder.
It uses namespace-aware netlink views (`ip -j`) alongside procfs views.  The
only procfs IPv6 rows accepted are the exact kernel null/unreachable sentinel
rows described below; every actual route dump must be empty.
"""

from __future__ import annotations

import json
import math
import os
import re
import signal
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


PROBE_MARKER = "AUTOLOADER_NETNS_ISOLATION_OK"
PROCESS_GROUP_TERMINATION_GRACE_SECONDS = 5.0

# WSL2's shared sysfs exposes a stale host eth0 in an otherwise isolated user
# network namespace.  Linux fib6_null_entry_template emits this exact
# procfs-only row twice on the observed kernel: a zero-prefix route with
# metric ffffffff, one ref, no use, flags RTF_NONEXTHOP (0x00200000) |
# RTF_REJECT (0x00000200), and dev lo.  The UAPI flag definitions are in
# include/uapi/linux/ipv6_route.h and include/uapi/linux/route.h; the kernel
# template is in net/ipv6/route.c.  It is an unreachable/null sentinel, not an
# egress route.  No other row or count is accepted.
IPV6_NULL_UNREACHABLE_ROW = (
    "00000000000000000000000000000000",
    "00",
    "00000000000000000000000000000000",
    "00",
    "00000000000000000000000000000000",
    "ffffffff",
    "00000001",
    "00000000",
    "00200200",
    "lo",
)

IPV4_DEFAULT_RULES = (
    {"priority": 0, "src": "all", "table": "local"},
    {"priority": 32766, "src": "all", "table": "main"},
    {"priority": 32767, "src": "all", "table": "default"},
)
IPV6_DEFAULT_RULES = (
    {"priority": 0, "src": "all", "table": "local"},
    {"priority": 32766, "src": "all", "table": "main"},
)


class ProbeError(RuntimeError):
    """The namespace views are incomplete, inconsistent, or permit routing."""


class _ChildInterrupted(Exception):
    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


def validate_ipv6_proc_routes(raw: str) -> int:
    """Accept no proc routes or exactly two copies of the null sentinel."""
    rows = [tuple(line.split()) for line in raw.splitlines() if line.strip()]
    if not rows:
        return 0
    if len(rows) != 2 or any(row != IPV6_NULL_UNREACHABLE_ROW for row in rows):
        raise ProbeError("unexpected IPv6 procfs route row or sentinel count")
    return len(rows)


def validate_namespace_views(
    *,
    host_namespace: str,
    child_namespace: str,
    sysfs_interfaces: Sequence[str],
    socket_interfaces: Sequence[tuple[int, str]],
    link_rows: Sequence[dict[str, Any]],
    address_rows: Sequence[dict[str, Any]],
    ipv4_routes: Sequence[dict[str, Any]],
    ipv6_routes: Sequence[dict[str, Any]],
    ipv4_rules: Sequence[dict[str, Any]],
    ipv6_rules: Sequence[dict[str, Any]],
    proc_dev: str,
    proc_ipv4_routes: str,
    proc_ipv6_routes: str,
    proc_if_inet6: str,
) -> dict[str, Any]:
    """Validate the independent namespace-aware netlink and procfs views."""
    if (
        not isinstance(host_namespace, str)
        or re.fullmatch(r"net:\[\d+\]", host_namespace) is None
        or not isinstance(child_namespace, str)
        or re.fullmatch(r"net:\[\d+\]", child_namespace) is None
        or child_namespace == host_namespace
    ):
        raise ProbeError("network namespace identity did not change")
    if not isinstance(sysfs_interfaces, (tuple, list)) or any(
        not isinstance(name, str) for name in sysfs_interfaces
    ):
        raise ProbeError("sysfs interface view has the wrong shape")
    if "lo" not in sysfs_interfaces:
        raise ProbeError("sysfs interface view does not include loopback")
    if any(
        not isinstance(value, str)
        for value in (proc_dev, proc_ipv4_routes, proc_ipv6_routes, proc_if_inet6)
    ):
        raise ProbeError("a procfs network view has the wrong text shape")
    collections = (
        ("socket interfaces", socket_interfaces),
        ("netlink links", link_rows),
        ("netlink addresses", address_rows),
        ("IPv4 routes", ipv4_routes),
        ("IPv6 routes", ipv6_routes),
        ("IPv4 rules", ipv4_rules),
        ("IPv6 rules", ipv6_rules),
    )
    if any(not isinstance(value, (tuple, list)) for _label, value in collections):
        raise ProbeError("a namespace netlink view has the wrong JSON/list shape")
    if any(not isinstance(row, dict) for _label, rows in collections[1:] for row in rows):
        raise ProbeError("a namespace netlink view contains a non-object row")
    # sysfs is deliberately recorded rather than treated as authoritative:
    # WSL2 may share this mount with the host.  Netlink and procfs below are
    # scoped to the current namespace and must independently show only lo.
    if tuple(socket_interfaces) != ((1, "lo"),):
        raise ProbeError(f"namespace interface indices are not loopback-only: {socket_interfaces!r}")
    if len(link_rows) != 1:
        raise ProbeError("netlink link dump is not loopback-only")
    link = link_rows[0]
    flags = link.get("flags")
    if (
        type(link.get("ifindex")) is not int
        or link.get("ifindex") != 1
        or link.get("ifname") != "lo"
        or not isinstance(flags, list)
        or any(not isinstance(flag, str) for flag in flags)
        or "LOOPBACK" not in flags
        or link.get("link_type") != "loopback"
    ):
        raise ProbeError("netlink link dump contains a non-loopback interface")
    if (
        len(address_rows) != 1
        or address_rows[0].get("ifname") != "lo"
        or not isinstance(address_rows[0].get("addr_info"), list)
    ):
        raise ProbeError("netlink address dump is incomplete or contains another interface")
    if address_rows[0].get("addr_info") != []:
        raise ProbeError("network namespace has an assigned address")
    if ipv4_routes or ipv6_routes:
        raise ProbeError("namespace-aware netlink route dump is not empty")
    if (
        tuple(ipv4_rules) != IPV4_DEFAULT_RULES
        or tuple(ipv6_rules) != IPV6_DEFAULT_RULES
        or any(type(row.get("priority")) is not int for row in (*ipv4_rules, *ipv6_rules))
    ):
        raise ProbeError("namespace-aware policy rules are not the kernel defaults")
    dev_all_lines = proc_dev.splitlines()
    if (
        len(dev_all_lines) < 2
        or not dev_all_lines[0].startswith("Inter-|")
        or not dev_all_lines[1].lstrip().startswith("face |")
    ):
        raise ProbeError("procfs network device table has an unknown header")
    dev_lines = [line for line in dev_all_lines[2:] if line.strip()]
    dev_names = tuple(line.split(":", 1)[0].strip() for line in dev_lines)
    if dev_names != ("lo",):
        raise ProbeError(f"procfs network devices are not loopback-only: {dev_names!r}")
    ipv4_lines = [line for line in proc_ipv4_routes.splitlines() if line.strip()]
    if ipv4_lines:
        # Some kernels expose only the header in an empty table; data rows are
        # never permitted here.
        if len(ipv4_lines) != 1 or not ipv4_lines[0].startswith("Iface Destination Gateway Flags"):
            raise ProbeError("IPv4 procfs route table contains data or an unknown format")
    if proc_if_inet6.strip():
        raise ProbeError("IPv6 addresses are present in procfs")
    null_rows = validate_ipv6_proc_routes(proc_ipv6_routes)
    return {
        "namespace_id": child_namespace,
        "sysfs_interfaces_observed": sorted(set(sysfs_interfaces)),
        "namespace_interfaces": ["lo"],
        "ipv4_netlink_route_count": 0,
        "ipv6_netlink_route_count": 0,
        "ipv4_rule_priorities": [0, 32766, 32767],
        "ipv6_rule_priorities": [0, 32766],
        "ipv6_null_unreachable_proc_rows": null_rows,
    }


def _run_ip(ip_path: str, *args: str) -> Any:
    try:
        result = subprocess.run(
            [ip_path, *args],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError as exc:
        raise ProbeError(f"cannot run namespace-aware iproute2 view: {exc}") from exc
    if result.returncode != 0:
        output = result.stdout if isinstance(result.stdout, str) else "<no text output>"
        raise ProbeError(f"iproute2 namespace view failed: {output[-1000:]}")
    if not isinstance(result.stdout, str):
        raise ProbeError("iproute2 namespace view returned no text output")
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError("iproute2 namespace view returned invalid JSON") from exc
    if not isinstance(parsed, list) or any(not isinstance(row, dict) for row in parsed):
        raise ProbeError("iproute2 namespace view did not return a JSON object list")
    return parsed


def collect_namespace_views(host_namespace: str, ip_path: str) -> dict[str, Any]:
    child_namespace = os.readlink("/proc/self/ns/net")
    sysfs_interfaces = os.listdir("/sys/class/net")
    socket_interfaces = socket.if_nameindex()
    link_rows = _run_ip(ip_path, "-j", "link", "show")
    address_rows = _run_ip(ip_path, "-j", "address", "show")
    ipv4_routes = _run_ip(ip_path, "-j", "-4", "route", "show", "table", "all")
    ipv6_routes = _run_ip(ip_path, "-j", "-6", "route", "show", "table", "all")
    ipv4_rules = _run_ip(ip_path, "-j", "-4", "rule", "show")
    ipv6_rules = _run_ip(ip_path, "-j", "-6", "rule", "show")
    return validate_namespace_views(
        host_namespace=host_namespace,
        child_namespace=child_namespace,
        sysfs_interfaces=sysfs_interfaces,
        socket_interfaces=socket_interfaces,
        link_rows=link_rows,
        address_rows=address_rows,
        ipv4_routes=ipv4_routes,
        ipv6_routes=ipv6_routes,
        ipv4_rules=ipv4_rules,
        ipv6_rules=ipv6_rules,
        proc_dev=Path("/proc/net/dev").read_text(encoding="ascii"),
        proc_ipv4_routes=Path("/proc/net/route").read_text(encoding="ascii"),
        proc_ipv6_routes=Path("/proc/net/ipv6_route").read_text(encoding="ascii"),
        proc_if_inet6=Path("/proc/net/if_inet6").read_text(encoding="ascii"),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) >= 5 and args[:1] == ["--run"]:
        host_namespace, ip_path = args[1], args[2]
        rest = args[3:]
        timeout: float | None = None
        if rest[:1] == ["--timeout"]:
            if len(rest) < 3:
                return 2
            try:
                timeout = float(rest[1])
            except ValueError:
                return 2
            rest = rest[2:]
        if rest[:1] == ["--"] and len(rest) > 1:
            return run_command_with_namespace_recheck(host_namespace, ip_path, rest[1:], timeout=timeout)
    if len(args) != 2:
        print(
            "usage: python -m usb_builder.network_probe HOST_NETNS IP_PATH\n"
            "   or: python -m usb_builder.network_probe --run HOST_NETNS IP_PATH -- COMMAND [ARGS...]",
            file=sys.stderr,
        )
        return 2
    try:
        result = collect_namespace_views(args[0], args[1])
    except (OSError, ProbeError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(PROBE_MARKER)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


def run_command_with_namespace_recheck(
    host_namespace: str,
    ip_path: str,
    command: Sequence[str],
    *,
    timeout: float | None = None,
) -> int:
    """Run one child only after and before rechecking its own namespace."""
    if not command or any(not isinstance(argument, str) or not argument for argument in command):
        print("ProbeError: command argv is empty or malformed", file=sys.stderr)
        return 2
    if timeout is not None and (
        isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0
    ):
        print("ProbeError: command timeout must be positive", file=sys.stderr)
        return 2
    kill_group = getattr(os, "killpg", None)
    sigkill = getattr(signal, "SIGKILL", None)
    if not callable(kill_group) or not isinstance(sigkill, int):
        print("ProbeError: command requires process-group cleanup support", file=sys.stderr)
        return 2
    try:
        collect_namespace_views(host_namespace, ip_path)
    except (OSError, ProbeError) as exc:
        print(f"ProbeError: pre-command namespace check failed: {exc}", file=sys.stderr)
        return 125
    try:
        child = subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    except OSError as exc:
        print(f"ProbeError: child command could not start: {exc}", file=sys.stderr)
        return 127

    def terminate_child_group(signum: int) -> None:
        if callable(kill_group):
            try:
                kill_group(child.pid, signum)
            except ProcessLookupError:
                pass

    def handle_signal(signum: int, _frame: Any) -> None:
        terminate_child_group(signal.SIGTERM)
        try:
            child.wait(timeout=PROCESS_GROUP_TERMINATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        # Kill the process group even when its leader exited promptly: it may
        # have left descendants in the same group.
        terminate_child_group(sigkill)
        child.wait()
        raise _ChildInterrupted(signum)

    previous_handlers: dict[int, Any] = {}
    handled_signals: list[int] = [signal.SIGTERM, signal.SIGINT]
    sighup = getattr(signal, "SIGHUP", None)
    if isinstance(sighup, int):
        handled_signals.append(sighup)
    for caught_signal in handled_signals:
        try:
            previous_handlers[caught_signal] = signal.signal(
                caught_signal,
                handle_signal,
            )
        except (OSError, ValueError):
            # Signal hooks are unavailable outside the main thread/platform;
            # production offline builds run the wrapper as a Linux process.
            continue
    return_code: int
    try:
        try:
            return_code = child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            terminate_child_group(signal.SIGTERM)
            try:
                child.wait(timeout=PROCESS_GROUP_TERMINATION_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                pass
            # A child may exit after SIGTERM while a grandchild stays in its
            # process group. Always kill the group after the grace period.
            terminate_child_group(sigkill)
            child.wait()
            print("ProbeError: child command timed out; its process group was terminated", file=sys.stderr)
            return_code = 124
        except _ChildInterrupted as exc:
            return_code = 128 + exc.signum
    finally:
        for caught_signal, previous_handler in previous_handlers.items():
            signal.signal(caught_signal, previous_handler)
    try:
        collect_namespace_views(host_namespace, ip_path)
    except (OSError, ProbeError) as exc:
        print(f"ProbeError: post-command namespace check failed: {exc}", file=sys.stderr)
        return 125
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
