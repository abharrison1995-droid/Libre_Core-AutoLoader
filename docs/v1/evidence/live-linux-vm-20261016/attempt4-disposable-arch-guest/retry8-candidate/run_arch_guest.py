#!/usr/bin/env python3
"""Bounded disposable Arch live guest and rootless-preflight probe.

This runner does not invoke the product builder, package installation, or any
firmware/hardware interface. The ISO and source share are read-only. The only
writable block device is an exact 3 GiB sparse scratch file in a fresh WSL
ext4 directory. QEMU is stopped and reaped on every exit path.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ISO = Path("/mnt/c/Users/Alex/Downloads/LibreCore-VMSetup-20261015/archlinux-2026.10.01-x86_64.iso")
EXPECTED_ISO_SHA256 = "684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5"
EXPECTED_ISO_LABEL = "ARCH_202610"
EXPECTED_QEMU_VERSION = "QEMU emulator version 11.1.2"
EXPECTED_SOURCE_HEAD = "0522fcb3ba68c32df428b998e6f26c0744a50860"
EXPECTED_GUEST_PROBE_SHA256 = "c46545f294f4ac9a3e17e0e4146376b8bb2b8f359e6e30a74af1cef9fe31de84"
EXPECTED_USB_BUILDER_MANIFEST_SHA256 = "176ae9db968ddf6fae1718c793e924dc42937b0bb9d3044c08e45937fc5396c5"
SCRATCH_SIZE = 3 * 1024**3
SCRATCH_SERIAL = "LCV1SCRATCH3G"
WALL_SECONDS = 1800
HASH_SECONDS = 240
CONNECT_SECONDS = 30
COMMAND_SECONDS = 180
GUEST_PROBE_COMMAND_SECONDS = 900
C_FREE_FLOOR = 2 * 1024**3
RUNNER_HOME = Path("/var/tmp/lcvm")
GUEST_PROBE_RELATIVE = Path(
    "docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/retry8-candidate/guest_probe.py"
)
EVIDENCE_RELATIVE = Path(
    "docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest"
)
EXPECTED_UNTRACKED = {
    f"?? {GUEST_PROBE_RELATIVE.as_posix()}",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/guest_probe.py",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/run_arch_guest.py",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/README.md",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/test_retry7_guest_probe.py",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry1/run-result.json",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry1/hmp-transcript.txt",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry1/vga.ppm",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry1/SHA256SUMS",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry2-prelaunch/run-result.json",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry2-prelaunch/SHA256SUMS",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry3/run-result.json",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry3/hmp-transcript.txt",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry3/vga.ppm",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry3/SHA256SUMS",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry4/run-result.json",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry4/hmp-transcript.txt",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry4/vga.ppm",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry4/SHA256SUMS",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry5/run-result.json",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry5/hmp-transcript.txt",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry5/vga.ppm",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry5/SHA256SUMS",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry6/run-result.json",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry6/hmp-transcript.txt",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry6/vga.ppm",
    "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry6/SHA256SUMS",
}
EXPECTED_UNTRACKED.update(
    {
        "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry7/run-result.json",
        "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry7/argv.json",
        "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry7/hmp-transcript.txt",
        "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry7/vga.ppm",
        "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry7/SHA256SUMS",
        "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/retry8-candidate/README.md",
        "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/retry8-candidate/guest_probe.py",
        "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/retry8-candidate/run_arch_guest.py",
        "?? docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/retry8-candidate/test_retry8_guest_probe.py",
    }
)
EXPECTED_MODIFIED = {
    " M docs/v1/ACCEPTANCE_MATRIX.md",
    " M docs/v1/PROGRESS.md",
    " M docs/v1/RELEASE_CHECKLIST.md",
}
RETRY1_SERIAL_RELATIVE = Path(
    "docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry1/serial.log"
)
RETRY3_SERIAL_RELATIVE = Path(
    "docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry3/serial.log"
)
RETRY4_SERIAL_RELATIVE = Path(
    "docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry4/serial.log"
)
RETRY5_SERIAL_RELATIVE = Path(
    "docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry5/serial.log"
)
RETRY6_SERIAL_RELATIVE = Path(
    "docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry6/serial.log"
)
RETRY7_SERIAL_RELATIVE = Path(
    "docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry7/serial.log"
)
RETRY_ARCHIVE_EXPECTED = {
    "results/retry1": {
        "manifest_sha256": "f854341312fe036d2104d424369f7425fde8d25988813f35f30900a1595227c6",
        "files": {
            "run-result.json": "6aa456051d1bef66173e78cd6347e57fa486b0b34c2b3e25bc00237136d015e2",
            "serial.log": "25ea9424fcf940869759c4322d2cc9896ef460a0e5080d4afee9ba4808ac4ab6",
            "hmp-transcript.txt": "2a760b9b885ced543a1964ee15fcae254b567c0454160ed00b482b5e236a40e8",
            "vga.ppm": "711378f4bef4cce5f3c3b95aba1f01692d0368c8dc5e82b21762bd808db3beec",
        },
    },
    "results/retry2-prelaunch": {
        "manifest_sha256": "74404c7156016d0c5290a0de6c78c8f6544416d3856e4920c8063dda0305daec",
        "files": {
            "run-result.json": "01c0d7bac6bd7d2fa86996e113445559364169b03b716d8e239a009d353c246a",
        },
    },
    "results/retry3": {
        "manifest_sha256": "9098fccc2d2f4f7fd5bc7ea1dab0e53ac7e3c2a508a9bac8dbe5a42e75d90a75",
        "files": {
            "run-result.json": "0508775e4af3efa15098e11f36229cd90f71761360e25b2738a09a88c04758b2",
            "serial.log": "c6de37f9c221b252f692d9ca0e1bff471f2c167ece33d4f8dacd9dbefeec2625",
            "hmp-transcript.txt": "5586c683f668ecc0f915b2b506c590f53e7582ef2143aa4f2c24feaf6f789ef8",
            "vga.ppm": "2cb0382369604884fd63ae0b5ba6a6bc658e1cb66e6239d09a698ed50d8fcc25",
        },
    },
    "results/retry4": {
        "manifest_sha256": "373cb75123dc6e7ae5d84f34db4152b7fdc396ba6630ba757e6ab96db6c2cf19",
        "files": {
            "run-result.json": "be3e63815d8ce2fd90aa935913aebfbcb71cf0fdafdc8c45005b54d503ca5462",
            "serial.log": "722adaa927749d7ba70775a5fb0479e1c9fe5eaf27c7909949a8dc569339fc72",
            "hmp-transcript.txt": "8d0b0a2912cdf4dab9356a3f33f0e388a02bb6205de399e3fb3914a9d34b1043",
            "vga.ppm": "d5dc4ecc27f947e41ebd1a948bf88d5ead3d12dfb2734e3bcad239c158db5227",
        },
    },
    "results/retry5": {
        "manifest_sha256": "504a913a422ad0232de4279fc5ffd21b3be01d5e2cd336ee33661cd54ee212b8",
        "files": {
            "run-result.json": "e6cb2f95d6c51a4a3729cc9d4e0796a7be17ea9d8e39c7453dbeaaf7ab1c4108",
            "serial.log": "1453123437effabc4120fbc9334e3fc2b5375d7246152ee92dc9d71cc7e2bd05",
            "hmp-transcript.txt": "8536ab4e67b5f24c78ad5edd239544b4f9d108bbc35334ea36f31dd9bea046ee",
            "vga.ppm": "843c2cf78a128d4253b0210e1750098d416a4b3232bc1298e82e8d78ee56d310",
        },
    },
    "results/retry6": {
        "manifest_sha256": "f33b74c84f1b0c306ed3486c893285c17675b02c863d4500f20bae94ff9be563",
        "files": {
            "run-result.json": "bb7a1edac63ab4afa36abb541e2f979c2410d34d6a3485f2e977987c6e5ce83e",
            "serial.log": "0867095973a43a1dc83bd490e905746e63db33af5de3cab6c8dc1324c28dff36",
            "hmp-transcript.txt": "10b77d1818dbf5881fed07e889c3d5fdf1d266ab597ac3da03edb332412c7c44",
            "vga.ppm": "233e15ddaa525015605cbfc06d68188f57a02fe45b211eea4d63ef546899e95a",
        },
    },
}
ANSI_ESCAPE = re.compile(rb"\x1b\[[0-9;?]*[ -/]*[@-~]")
SHELL_PROMPT = re.compile(rb"(?:^|\r?\n)(?:\[root@|root@)[^\r\n]*[#\$][ \t]*$")
DEBUG_SHELL_OSC_START = b"\x1b]3008;"
DEBUG_SHELL_OSC_END = b"\x1b\\"
DEBUG_SHELL_OSC_MAX_BYTES = 4096
DEBUG_SHELL_SERVICE_FIELD = b"servicename=debug-shell.service"
DEBUG_SHELL_PROMPT_SECONDS = 45
DEBUG_SHELL_PROMPT_QUIET_SECONDS = 1.0
DEBUG_SHELL_PROMPT_PATTERNS = (
    ("bare-root-sh", re.compile(rb"^# $")),
    ("versioned-sh", re.compile(rb"^(?:sh|bash)-[0-9]+(?:\.[0-9]+){1,3}# $")),
    ("root-at-host", re.compile(rb"^root@[A-Za-z0-9_.-]+(?: [^\r\n#]{0,256})?# $")),
    ("bracketed-root-at-host", re.compile(rb"^\[root@[A-Za-z0-9_.-]+(?: [^\r\n#\]]{0,256})?\]# $")),
)


class RunnerError(RuntimeError):
    pass


def find_debug_shell_service_marker(data: bytes) -> dict[str, Any] | None:
    """Find one complete, bounded OSC 3008 sequence with the exact service field."""
    start = data.find(DEBUG_SHELL_OSC_START)
    while start >= 0:
        body_start = start + len(DEBUG_SHELL_OSC_START)
        end = data.find(DEBUG_SHELL_OSC_END, body_start)
        if end < 0:
            start = data.find(DEBUG_SHELL_OSC_START, body_start)
            continue
        sequence_end = end + len(DEBUG_SHELL_OSC_END)
        if sequence_end - start <= DEBUG_SHELL_OSC_MAX_BYTES:
            body = data[body_start:end]
            fields = body.split(b";")
            service_fields = [field for field in fields if field.startswith(b"servicename=")]
            if len(service_fields) == 1 and service_fields[0] == DEBUG_SHELL_SERVICE_FIELD:
                sequence = data[start:sequence_end]
                return {
                    "start_offset": start,
                    "end_offset": sequence_end,
                    "sequence_length_bytes": len(sequence),
                    "sequence_sha256": sha256_bytes(sequence),
                    "service_field": DEBUG_SHELL_SERVICE_FIELD.decode("ascii"),
                }
        start = data.find(DEBUG_SHELL_OSC_START, start + 1)
    return None


def send_debug_shell_newline_once(
    serial: Any,
    serial_data: bytearray,
    serial_log: Any,
    result: dict[str, Any],
    guest_inputs: list[dict[str, Any]],
    marker: dict[str, Any],
) -> int:
    """Send and record one newline only for a validated service marker."""
    if (
        marker.get("service_field") != DEBUG_SHELL_SERVICE_FIELD.decode("ascii")
        or type(marker.get("sequence_length_bytes")) is not int
        or marker["sequence_length_bytes"] <= 0
        or marker["sequence_length_bytes"] > DEBUG_SHELL_OSC_MAX_BYTES
        or result.get("debug_shell_service_marker_seen") is not True
        or result.get("debug_shell_newline_sent") is True
        or result.get("debug_shell_newline_send_count") != 0
    ):
        raise RunnerError("debug-shell newline is not authorized or was already sent")
    response_start_offset = len(serial_data)
    sent_at = time.time()
    serial.sendall(b"\n")
    result["debug_shell_newline_sent"] = True
    result["debug_shell_newline_send_count"] = 1
    guest_inputs.append(
        {
            "input_description": "one blank newline",
            "input_base64": base64.b64encode(b"\n").decode("ascii"),
            "reason": "exact bounded OSC servicename=debug-shell.service marker observed",
            "observed_marker_utc_epoch": marker.get("observed_utc_epoch"),
            "sent_utc_epoch": sent_at,
            "response_start_offset": response_start_offset,
        }
    )
    serial_log.write(
        b"\n[runner sent exactly one blank newline after exact OSC 3008 servicename=debug-shell.service marker]\n"
    )
    serial_log.flush()
    return response_start_offset


def normalize_debug_shell_terminal(data: bytes) -> bytes | None:
    """Remove complete CSI/OSC controls; refuse unknown or partial escapes."""
    normalized = bytearray()
    index = 0
    while index < len(data):
        if data[index] != 0x1B:
            normalized.append(data[index])
            index += 1
            continue
        if index + 1 >= len(data):
            return None
        kind = data[index + 1]
        if kind == ord("["):
            end = index + 2
            while end < len(data) and (0x30 <= data[end] <= 0x3F or 0x20 <= data[end] <= 0x2F):
                end += 1
            if end >= len(data) or not 0x40 <= data[end] <= 0x7E:
                return None
            index = end + 1
            continue
        if kind == ord("]"):
            end = index + 2
            while end < len(data):
                if data[end] == 0x07:
                    break
                if data[end] == 0x1B:
                    if end + 1 >= len(data) or data[end + 1] != ord("\\"):
                        return None
                    break
                end += 1
            if end >= len(data) or end - index > DEBUG_SHELL_OSC_MAX_BYTES:
                return None
            index = end + (2 if data[end] == 0x1B else 1)
            continue
        return None
    return bytes(normalized).replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def match_debug_shell_prompt(data: bytes) -> dict[str, str] | None:
    """Match only an explicit root prompt on the final normalized line."""
    normalized = normalize_debug_shell_terminal(data)
    if normalized is None:
        return None
    final_line = normalized.split(b"\n")[-1]
    for shape, pattern in DEBUG_SHELL_PROMPT_PATTERNS:
        if pattern.fullmatch(final_line):
            return {
                "shape": shape,
                "normalized_line": final_line.decode("ascii"),
                "line_sha256": sha256_bytes(final_line),
            }
    return None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_c_floor() -> int:
    free = shutil.disk_usage("/mnt/c").free
    if free < C_FREE_FLOOR:
        raise RunnerError(f"C: free-space floor reached: {free} bytes available")
    return free


def sha256_file(path: Path, *, timeout_seconds: int = HASH_SECONDS) -> str:
    digest = hashlib.sha256()
    deadline = time.monotonic() + timeout_seconds
    with path.open("rb") as source:
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError(f"hash deadline expired for {path}")
            check_c_floor()
            block = source.read(4 * 1024 * 1024)
            if not block:
                return digest.hexdigest()
            digest.update(block)


def regular_repo_file(repo: Path, relative: Path) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        raise RunnerError(f"archived evidence path is not a safe relative path: {relative}")
    current = repo
    for part in relative.parts[:-1]:
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise RunnerError(f"archived evidence path has a linked/non-directory component: {current}")
    path = current / relative.name
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RunnerError(f"archived evidence must be a regular non-symlink file: {relative}")
    return path


def verify_archived_evidence(repo: Path) -> dict[str, str]:
    verified: dict[str, str] = {}
    for directory, expected in RETRY_ARCHIVE_EXPECTED.items():
        manifest_relative = Path(directory) / "SHA256SUMS"
        manifest = regular_repo_file(repo, manifest_relative)
        manifest_hash = sha256_file(manifest)
        if manifest_hash != expected["manifest_sha256"]:
            raise RunnerError(f"archived evidence manifest SHA-256 mismatch: {manifest_relative}")
        manifest_bytes = manifest.read_bytes()
        try:
            manifest_lines = manifest_bytes.decode("ascii").splitlines()
        except UnicodeDecodeError as exc:
            raise RunnerError(f"archived evidence manifest is not ASCII: {manifest_relative}") from exc
        parsed: dict[str, str] = {}
        for line in manifest_lines:
            digest, separator, filename = line.partition("  ")
            if not separator or filename in parsed or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise RunnerError(f"archived evidence manifest has malformed/duplicate rows: {manifest_relative}")
            parsed[filename] = digest
        if parsed != expected["files"]:
            raise RunnerError(f"archived evidence manifest entries differ from pins: {manifest_relative}")
        verified[manifest_relative.as_posix()] = manifest_hash
        for filename, expected_hash in expected["files"].items():
            relative = Path(directory) / filename
            evidence = regular_repo_file(repo, relative)
            actual = sha256_file(evidence)
            if actual != expected_hash:
                raise RunnerError(f"archived evidence SHA-256 mismatch: {relative}")
            verified[relative.as_posix()] = actual
    return verified


def lstat_directory_chain(path: Path) -> list[dict[str, Any]]:
    if not path.is_absolute():
        raise RunnerError(f"path must be absolute: {path}")
    current = Path("/")
    result: list[dict[str, Any]] = []
    for part in path.parts[1:]:
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise RunnerError(f"unsafe/non-directory path component: {current}")
        result.append(
            {
                "path": str(current),
                "uid": info.st_uid,
                "gid": info.st_gid,
                "mode": stat.S_IMODE(info.st_mode),
                "device": info.st_dev,
            }
        )
    return result


def runner_capability_status() -> dict[str, str]:
    return {
        line.split(":", 1)[0]: line.split(":", 1)[1].strip()
        for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines()
        if line.startswith(("CapInh:", "CapPrm:", "CapEff:", "CapBnd:", "CapAmb:", "NoNewPrivs:"))
    }


def validate_output_dir(path: Path) -> tuple[Path, list[dict[str, Any]], str]:
    if (os.getuid(), os.geteuid(), os.getgid(), os.getegid(), os.getgroups()) != (
        65534,
        65534,
        65534,
        65534,
        [],
    ):
        raise RunnerError("runner must use the dedicated unprivileged UID/GID 65534 with no supplementary groups")
    if os.environ.get("HOME") != str(RUNNER_HOME):
        raise RunnerError(f"HOME must be the private runner directory {RUNNER_HOME}")
    capability_status = runner_capability_status()
    if set(capability_status) != {"CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb", "NoNewPrivs"}:
        raise RunnerError("could not read the complete runner capability/NoNewPrivs state")
    if any(capability_status[key] != "0000000000000000" for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")):
        raise RunnerError("runner must have no Linux capabilities")
    if capability_status["NoNewPrivs"] != "1":
        raise RunnerError("runner requires NoNewPrivs=1")

    var_tmp = Path("/var/tmp")
    var_tmp_info = var_tmp.lstat()
    if stat.S_ISLNK(var_tmp_info.st_mode) or not stat.S_ISDIR(var_tmp_info.st_mode):
        raise RunnerError("/var/tmp must be a real, non-symlink directory")
    if (
        var_tmp_info.st_uid != 0
        or var_tmp_info.st_gid != 0
        or stat.S_IMODE(var_tmp_info.st_mode) != 0o1777
    ):
        raise RunnerError("/var/tmp must retain its root-owned sticky 01777 mode")
    var_tmp_fs = subprocess.run(
        ["/usr/bin/findmnt", "-n", "-o", "FSTYPE", "-T", str(var_tmp)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        close_fds=True,
        timeout=10,
    )
    if var_tmp_fs.returncode != 0 or var_tmp_fs.stdout.strip() not in {"ext4", "ext3", "ext2"}:
        raise RunnerError(f"/var/tmp must be on a Linux ext filesystem: {var_tmp_fs.stdout.strip()!r}")

    home = RUNNER_HOME
    chain = lstat_directory_chain(home)
    if path.parent != home or not re.fullmatch(r"lc-vm-arch-guest-[a-z0-9-]{1,40}", path.name):
        raise RunnerError("output must be a fresh, single child of /var/tmp/lcvm with the reviewed name form")
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    else:
        raise RunnerError(f"refusing to reuse output path: {path}")
    home_info = home.stat()
    if (
        home_info.st_uid != 65534
        or home_info.st_gid != 65534
        or stat.S_IMODE(home_info.st_mode) != 0o700
    ):
        raise RunnerError("/var/tmp/lcvm must be owned by UID/GID 65534 and have exact mode 0700")
    if Path("/mnt/c").stat().st_dev == home_info.st_dev:
        raise RunnerError("/var/tmp/lcvm unexpectedly shares the C:-mounted filesystem")
    fs = subprocess.run(
        ["/usr/bin/findmnt", "-n", "-o", "FSTYPE", "-T", str(home)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        close_fds=True,
        timeout=10,
    )
    filesystem_type = fs.stdout.strip()
    if fs.returncode != 0 or filesystem_type not in {"ext4", "ext3", "ext2"}:
        raise RunnerError(f"/var/tmp/lcvm is not on the expected Linux ext filesystem: {filesystem_type!r}")
    if len(str(path / "monitor.sock").encode("utf-8")) >= 100:
        raise RunnerError("output socket paths exceed the safe UNIX socket path limit")
    return path, chain, filesystem_type


def run_capture(
    argv: list[str], *, timeout: int = 15, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        close_fds=True,
        timeout=timeout,
        env=env,
    )


def extract_iso_file(iso: Path, member: str, destination: Path) -> None:
    with destination.open("xb") as output:
        result = subprocess.run(
            ["/usr/bin/bsdtar", "-xOf", str(iso), member],
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.PIPE,
            check=False,
            close_fds=True,
            timeout=90,
        )
        output.flush()
        os.fsync(output.fileno())
    if result.returncode != 0 or destination.stat().st_size == 0:
        raise RunnerError(
            f"could not extract {member} from verified ISO: "
            f"rc={result.returncode} stderr={result.stderr.decode('utf-8', 'replace')}"
        )


def connect_unix(path: Path, process: subprocess.Popen[bytes], deadline: float) -> socket.socket:
    last_error: OSError | None = None
    while time.monotonic() < deadline and process.poll() is None:
        check_c_floor()
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(0.2)
        try:
            connection.connect(str(path))
            return connection
        except OSError as exc:
            last_error = exc
            connection.close()
            time.sleep(0.1)
    raise RunnerError(f"QEMU socket unavailable: {path}; last error={last_error}")


def read_until_monitor_prompt(connection: socket.socket, timeout: float = 8.0) -> bytes:
    deadline = time.monotonic() + timeout
    received = bytearray()
    while time.monotonic() < deadline:
        try:
            block = connection.recv(65536)
        except socket.timeout:
            continue
        if not block:
            break
        received.extend(block)
        if b"(qemu)" in received:
            break
    return bytes(received)


def hmp_command(connection: socket.socket, transcript: Any, command: str) -> bytes:
    connection.sendall(command.encode("ascii") + b"\n")
    response = read_until_monitor_prompt(connection)
    transcript.write(f"\n>>> {command}\n".encode("utf-8"))
    transcript.write(response)
    transcript.flush()
    if b"(qemu)" not in response:
        raise RunnerError(f"HMP command did not return to monitor prompt: {command}")
    return response


def stop_process(
    process: subprocess.Popen[bytes], monitor: socket.socket | None, transcript: Any | None
) -> tuple[int | None, str]:
    leader_was_running = process.poll() is None
    if leader_was_running and monitor is not None:
        try:
            monitor.sendall(b"quit\n")
            if transcript is not None:
                transcript.write(b"\n>>> quit (runner shutdown)\n")
                transcript.flush()
            process.wait(timeout=10)
        except (OSError, RunnerError, subprocess.TimeoutExpired, socket.timeout):
            pass

    def group_exists() -> bool:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return False
        return True

    # HMP quit normally ends QEMU cleanly. Check and clean the whole session
    # anyway: a QEMU child may outlive a leader that has already exited.
    if group_exists():
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        grace_deadline = time.monotonic() + 5
        while time.monotonic() < grace_deadline and group_exists():
            time.sleep(min(0.1, grace_deadline - time.monotonic()))
        # Send SIGKILL after the grace interval even if the session leader has
        # already been reaped; descendants can otherwise remain alive.
        if group_exists():
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    try:
        if process.poll() is None:
            process.wait(timeout=5)
    except subprocess.TimeoutExpired as exc:
        raise RunnerError("QEMU process did not reap after process-group cleanup") from exc
    if process.poll() is None:
        raise RunnerError("QEMU leader remains alive after process-group cleanup")
    reason = (
        "QEMU exited after HMP quit and process-group cleanup"
        if leader_was_running
        else "QEMU was already exited; residual process-group cleanup completed"
    )
    return process.returncode, reason


def append_serial(serial_log: Any, serial_data: bytearray, chunk: bytes) -> None:
    if not chunk:
        return
    serial_log.write(chunk)
    serial_log.flush()
    serial_data.extend(chunk)
    if len(serial_data) > 2 * 1024 * 1024:
        del serial_data[: len(serial_data) - 2 * 1024 * 1024]


def receive_serial(
    serial: socket.socket,
    serial_log: Any,
    serial_data: bytearray,
    *,
    process: subprocess.Popen[bytes],
    deadline: float,
) -> bytes:
    if time.monotonic() >= deadline:
        return b""
    try:
        chunk = serial.recv(65536)
    except socket.timeout:
        return b""
    except OSError:
        if process.poll() is not None:
            return b""
        raise
    append_serial(serial_log, serial_data, chunk)
    return chunk


def await_shell_prompt(
    serial: socket.socket,
    serial_log: Any,
    serial_data: bytearray,
    *,
    process: subprocess.Popen[bytes],
    deadline: float,
    start_offset: int = 0,
    prompt_mode: str = "normal",
) -> bool:
    if prompt_mode not in {"normal", "debug"}:
        raise RunnerError(f"unsupported shell prompt mode: {prompt_mode}")
    if type(start_offset) is not int or start_offset < 0 or start_offset > len(serial_data):
        raise RunnerError("shell prompt start offset is invalid or not fresh")
    while time.monotonic() < deadline and process.poll() is None:
        check_c_floor()
        fresh = bytes(serial_data[start_offset:])
        if prompt_mode == "debug":
            matched = match_debug_shell_prompt(fresh) is not None
        else:
            matched = SHELL_PROMPT.search(ANSI_ESCAPE.sub(b"", fresh)) is not None
        if matched:
            return True
        receive_serial(serial, serial_log, serial_data, process=process, deadline=deadline)
    return False


def await_debug_shell_prompt(
    serial: socket.socket,
    serial_log: Any,
    serial_data: bytearray,
    *,
    process: subprocess.Popen[bytes],
    deadline: float,
    start_offset: int,
) -> dict[str, Any] | None:
    """Require a fresh final-line root prompt, stable across a quiet interval."""
    candidate: dict[str, str] | None = None
    quiet_started: float | None = None
    candidate_data_end: int | None = None
    while time.monotonic() < deadline and process.poll() is None:
        check_c_floor()
        fresh = bytes(serial_data[start_offset:])
        latest = match_debug_shell_prompt(fresh)
        now = time.monotonic()
        if latest is None:
            candidate = None
            quiet_started = None
            candidate_data_end = None
        elif candidate != latest or candidate_data_end != len(serial_data):
            candidate = latest
            quiet_started = now
            candidate_data_end = len(serial_data)
        elif quiet_started is not None and now - quiet_started >= DEBUG_SHELL_PROMPT_QUIET_SECONDS:
            return {
                **candidate,
                "fresh_data_start_offset": start_offset,
                "fresh_data_end_offset": len(serial_data),
                "quiet_interval_seconds": DEBUG_SHELL_PROMPT_QUIET_SECONDS,
                "observed_utc_epoch": time.time(),
            }
        receive_serial(serial, serial_log, serial_data, process=process, deadline=deadline)
    return None


def send_shell_line_wait_prompt(
    serial: socket.socket,
    serial_log: Any,
    serial_data: bytearray,
    *,
    process: subprocess.Popen[bytes],
    command: str,
    deadline: float,
    command_records: list[dict[str, Any]],
    prompt_mode: str = "normal",
) -> bytes:
    if prompt_mode not in {"normal", "debug"}:
        raise RunnerError(f"unsupported shell prompt mode: {prompt_mode}")
    command_records.append(
        {
            "command": command,
            "completion": "shell_prompt",
            "prompt_mode": prompt_mode,
            "utc_epoch": time.time(),
        }
    )
    offset = len(serial_data)
    serial.sendall(command.encode("ascii") + b"\n")
    if not await_shell_prompt(
        serial,
        serial_log,
        serial_data,
        process=process,
        deadline=deadline,
        start_offset=offset,
        prompt_mode=prompt_mode,
    ):
        raise RunnerError("guest shell did not return after a bounded setup command")
    return bytes(serial_data[offset:])


def send_shell_command(
    serial: socket.socket,
    serial_log: Any,
    serial_data: bytearray,
    *,
    process: subprocess.Popen[bytes],
    command: str,
    marker: str,
    deadline: float,
    command_records: list[dict[str, Any]],
    prompt_mode: str = "normal",
) -> bytes:
    if prompt_mode not in {"normal", "debug"}:
        raise RunnerError(f"unsupported shell prompt mode: {prompt_mode}")
    command_records.append(
        {
            "command": command,
            "marker": marker,
            "completion": "marker_and_shell_prompt",
            "prompt_mode": prompt_mode,
            "utc_epoch": time.time(),
        }
    )
    offset = len(serial_data)
    serial.sendall(command.encode("ascii") + b"\n")
    marker_bytes = marker.encode("ascii")
    while time.monotonic() < deadline and process.poll() is None:
        check_c_floor()
        current = ANSI_ESCAPE.sub(b"", bytes(serial_data[offset:]))
        marker_match = re.search(
            rb"(?:^|\r?\n)" + re.escape(marker_bytes) + rb"(?::[0-9]+)?(?:\r?\n|$)",
            current,
        )
        if marker_match is not None:
            prompt_offset = offset + marker_match.end()
            if not await_shell_prompt(
                serial,
                serial_log,
                serial_data,
                process=process,
                deadline=min(deadline, time.monotonic() + 5),
                start_offset=prompt_offset,
                prompt_mode=prompt_mode,
            ):
                raise RunnerError("guest command marker appeared but shell prompt did not return")
            return bytes(serial_data[offset:])
        receive_serial(serial, serial_log, serial_data, process=process, deadline=deadline)
    raise RunnerError(f"guest command did not complete before deadline: {marker}")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        output_dir, output_parent_chain, output_filesystem = validate_output_dir(args.output_dir)
        c_free_at_entry = check_c_floor()
    except (OSError, RunnerError) as exc:
        parser.error(str(exc))

    repo = next((parent for parent in Path(__file__).resolve().parents if (parent / ".git").exists()), None)
    if repo is None:
        parser.error("cannot locate the current source checkout")
    repo = repo.resolve(strict=True)
    if not str(repo).startswith("/mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader"):
        parser.error("source must resolve to the current C:-mounted checkout")

    helper_path = repo / GUEST_PROBE_RELATIVE
    try:
        helper_info = helper_path.lstat()
    except OSError as exc:
        parser.error(f"guest helper is unavailable: {exc}")
    if not stat.S_ISREG(helper_info.st_mode) or helper_path.is_symlink():
        parser.error("guest helper must be a regular non-symlink file")
    helper_bytes = helper_path.read_bytes()
    helper_hash = sha256_bytes(helper_bytes)
    if helper_hash != EXPECTED_GUEST_PROBE_SHA256:
        parser.error("guest helper bytes do not match the runner's reviewed SHA-256 pin")

    output_dir.mkdir(mode=0o700)
    output_info = output_dir.lstat()
    home_info = RUNNER_HOME.stat()
    if (
        not stat.S_ISDIR(output_info.st_mode)
        or stat.S_ISLNK(output_info.st_mode)
        or output_info.st_uid != os.geteuid()
        or stat.S_IMODE(output_info.st_mode) != 0o700
        or output_info.st_dev != home_info.st_dev
    ):
        parser.error("created output directory is not a private child on the validated ext filesystem")

    result: dict[str, Any] = {
        "schema_version": 1,
        "attempt_id": "retry8",
        "purpose": "disposable Arch systemd debug-shell source-integrity and bounded read-only package-query candidate only",
        "guest_shell_method": "systemd.debug_shell on ttyS0; ephemeral diagnostic shell",
        "guest_shell_is_normal_login": False,
        "normal_boot_or_login_validated": False,
        "normal_login_getty_autogeneration_disabled": True,
        "systemd_getty_auto": "0",
        "static_serial_getty_absence_checked": False,
        "debug_shell_service_marker_seen": False,
        "debug_shell_newline_sent": False,
        "debug_shell_newline_send_count": 0,
        "debug_shell_prompt_window_seconds": DEBUG_SHELL_PROMPT_SECONDS,
        "debug_shell_prompt_quiet_seconds": DEBUG_SHELL_PROMPT_QUIET_SECONDS,
        "guest_shell_prompt_seen": False,
        "guest_probe_marker_seen": False,
        "diagnostic_kernel_arguments": [
            "systemd.getty_auto=0",
            "systemd.show_status=0",
            "systemd.debug_shell",
            "systemd.default_debug_tty=ttyS0",
        ],
        "debug_shell_prompt_accepted_shapes": [shape for shape, _ in DEBUG_SHELL_PROMPT_PATTERNS],
        "debug_shell_prompt_line_rule": "after exact OSC marker and one newline, the final normalized line must full-match an explicit root prompt and remain unchanged across a one-second quiet interval",
        "command_return_prompt_modes": {
            "normal": "fresh bytes after each command must end in the existing Arch root login prompt form",
            "debug": "fresh bytes after each command must end in an accepted explicit debug-root prompt form",
        },
        "source_share_read_only": True,
        "network": "QEMU user-mode NIC with restrict=on; no forwarding or host access configured",
        "hardware_or_firmware_validation": False,
        "product_build_invoked": False,
        "package_install_or_sync_invoked": False,
        "scratch_format": "raw sparse file, exact maximum virtual size 3 GiB",
        "wall_limit_seconds": WALL_SECONDS,
        "locked_package_query_timeout_seconds": 300,
        "locked_package_query_heartbeat_seconds": 15,
        "guest_probe_command_timeout_seconds": GUEST_PROBE_COMMAND_SECONDS,
        "c_free_floor_bytes": C_FREE_FLOOR,
        "output_dir": str(output_dir),
        "output_parent_chain": output_parent_chain,
        "output_filesystem_type": output_filesystem,
        "runner_invocation": {
            "argv": [str(Path(sys.argv[0]).resolve()), *sys.argv[1:]],
            "uid": os.getuid(),
            "euid": os.geteuid(),
            "gid": os.getgid(),
            "egid": os.getegid(),
            "supplementary_groups": os.getgroups(),
            "capabilities_and_no_new_privs": runner_capability_status(),
            "home": os.environ.get("HOME"),
            "path": os.environ.get("PATH"),
            "tmpdir": os.environ.get("TMPDIR"),
        },
        "runner_sha256": sha256_file(Path(__file__).resolve()),
        "guest_probe_sha256": helper_hash,
        "guest_usb_builder_source_manifest_sha256": EXPECTED_USB_BUILDER_MANIFEST_SHA256,
        "guest_usb_builder_source_manifest_file_count_expected": 24,
        "guest_git_check": "NOT_PERFORMED; host Git HEAD/status validation only",
        "expected_source_head": EXPECTED_SOURCE_HEAD,
        "c_free_before_bytes": c_free_at_entry,
    }
    process: subprocess.Popen[bytes] | None = None
    monitor: socket.socket | None = None
    serial: socket.socket | None = None
    monitor_transcript: Any | None = None
    serial_log: Any | None = None
    qemu_log: Any | None = None
    result_path = output_dir / "run-result.json"
    iso_hash_before: str | None = None
    scratch: Path | None = None
    launch_started: float | None = None
    return_code = 2

    try:
        c_free_before = check_c_floor()
        result["c_free_before_launch_bytes"] = c_free_before

        if not ISO.is_file() or ISO.is_symlink():
            raise RunnerError(f"pinned ISO is missing or linked: {ISO}")
        iso_hash_before = sha256_file(ISO)
        result["iso_path"] = str(ISO)
        result["iso_sha256_before"] = iso_hash_before
        if iso_hash_before != EXPECTED_ISO_SHA256:
            raise RunnerError("pinned ISO SHA-256 differs from the approved hash")

        label = run_capture(["/usr/bin/blkid", "-p", "-s", "LABEL", "-o", "value", str(ISO)])
        result["iso_label_output"] = label.stdout.strip()
        if label.returncode != 0 or label.stdout.strip() != EXPECTED_ISO_LABEL:
            raise RunnerError(f"ISO volume label mismatch: {label.stdout.strip()!r}")
        qemu_version = run_capture(["/usr/bin/qemu-system-x86_64", "--version"])
        result["qemu_version"] = qemu_version.stdout.strip()
        if qemu_version.returncode != 0 or not qemu_version.stdout.startswith(EXPECTED_QEMU_VERSION):
            raise RunnerError("QEMU version is not the approved 11.1.2 build")

        git_env = os.environ.copy()
        git_env["GIT_OPTIONAL_LOCKS"] = "0"
        git_prefix = [
            "/usr/bin/git",
            "-C",
            str(repo),
            "-c",
            f"safe.directory={repo}",
            "-c",
            "core.autocrlf=true",
            "-c",
            "core.filemode=false",
        ]
        head = run_capture([*git_prefix, "rev-parse", "HEAD"], env=git_env)
        source_status = run_capture(
            [*git_prefix, "status", "--porcelain=v1", "--untracked-files=all"],
            timeout=120,
            env=git_env,
        )
        ignored_serial_checks = []
        for serial_path in (
            RETRY1_SERIAL_RELATIVE,
            RETRY3_SERIAL_RELATIVE,
            RETRY4_SERIAL_RELATIVE,
            RETRY5_SERIAL_RELATIVE,
            RETRY6_SERIAL_RELATIVE,
            RETRY7_SERIAL_RELATIVE,
        ):
            ignored_serial_argv = [
                *git_prefix,
                "check-ignore",
                "--quiet",
                "--",
                serial_path.as_posix(),
            ]
            ignored_serial = run_capture(ignored_serial_argv, env=git_env)
            ignored_serial_checks.append(
                {
                    "path": serial_path.as_posix(),
                    "argv": ignored_serial_argv,
                    "returncode": ignored_serial.returncode,
                    "stdout": ignored_serial.stdout,
                }
            )
        source_status_lines = sorted(source_status.stdout.splitlines())
        result["source_path"] = str(repo)
        result["source_head"] = head.stdout.strip()
        result["source_head_returncode"] = head.returncode
        result["source_head_output"] = head.stdout
        result["source_status"] = source_status_lines
        result["source_status_returncode"] = source_status.returncode
        result["source_status_output"] = source_status.stdout
        result["ignored_serial_log_paths"] = [
            RETRY1_SERIAL_RELATIVE.as_posix(),
            RETRY3_SERIAL_RELATIVE.as_posix(),
            RETRY4_SERIAL_RELATIVE.as_posix(),
            RETRY5_SERIAL_RELATIVE.as_posix(),
            RETRY6_SERIAL_RELATIVE.as_posix(),
            RETRY7_SERIAL_RELATIVE.as_posix(),
        ]
        result["ignored_serial_log_checks"] = ignored_serial_checks
        result["source_git_validation"] = {
            "safe_directory": str(repo),
            "core_autocrlf": "true",
            "core_filemode": "false",
            "git_optional_locks": "0",
            "head_argv": [*git_prefix, "rev-parse", "HEAD"],
            "head_returncode": head.returncode,
            "head_stdout": head.stdout,
            "status_argv": [*git_prefix, "status", "--porcelain=v1", "--untracked-files=all"],
            "status_returncode": source_status.returncode,
            "status_stdout": source_status.stdout,
            "check_ignore_checks": ignored_serial_checks,
        }
        result["archived_evidence_hashes"] = verify_archived_evidence(repo / EVIDENCE_RELATIVE)
        if head.returncode != 0 or head.stdout.strip() != EXPECTED_SOURCE_HEAD:
            raise RunnerError(f"source HEAD is not the exact reviewed revision: {head.stdout.strip()!r}")
        if source_status.returncode != 0 or set(source_status_lines) != (EXPECTED_UNTRACKED | EXPECTED_MODIFIED):
            raise RunnerError("source checkout status differs from the retry8 reviewed candidate allowlist")
        if any(check["returncode"] != 0 or check["stdout"] for check in ignored_serial_checks):
            raise RunnerError("an exact archived serial log is not the expected ignored Git path")

        kernel = output_dir / "vmlinuz-linux"
        initramfs = output_dir / "initramfs-linux.img"
        extract_iso_file(ISO, "arch/boot/x86_64/vmlinuz-linux", kernel)
        extract_iso_file(ISO, "arch/boot/x86_64/initramfs-linux.img", initramfs)
        result["kernel"] = {"path": str(kernel), "size": kernel.stat().st_size, "sha256": sha256_file(kernel)}
        result["initramfs"] = {
            "path": str(initramfs),
            "size": initramfs.stat().st_size,
            "sha256": sha256_file(initramfs),
        }

        scratch = output_dir / "scratch-3g.raw"
        scratch_fd = os.open(scratch, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        try:
            os.ftruncate(scratch_fd, SCRATCH_SIZE)
            os.fsync(scratch_fd)
        finally:
            os.close(scratch_fd)
        scratch_info = scratch.stat()
        if scratch_info.st_size != SCRATCH_SIZE:
            raise RunnerError("sparse scratch file has an unexpected virtual size")
        result["scratch"] = {
            "path": str(scratch),
            "format": "raw",
            "virtual_size_bytes": scratch_info.st_size,
            "allocated_bytes_before_guest": scratch_info.st_blocks * 512,
            "serial": SCRATCH_SERIAL,
        }

        serial_path = output_dir / "serial.sock"
        monitor_path = output_dir / "monitor.sock"
        screenshot_path = output_dir / "vga.ppm"
        qemu_log_path = output_dir / "qemu.log"
        serial_log_path = output_dir / "serial.log"
        monitor_log_path = output_dir / "hmp-transcript.txt"
        if any(path.exists() or path.is_symlink() for path in (serial_path, monitor_path, screenshot_path)):
            raise RunnerError("unexpected pre-existing QEMU socket/capture path")
        argv = [
            "/usr/bin/qemu-system-x86_64",
            "-machine", "q35",
            "-accel", "tcg,thread=multi",
            "-nodefaults",
            "-cpu", "max",
            "-m", "2048",
            "-smp", "2",
            "-kernel", str(kernel),
            "-initrd", str(initramfs),
            "-append",
            f"archisobasedir=arch archisolabel={EXPECTED_ISO_LABEL} copytoram=n console=tty0 console=ttyS0,115200n8 systemd.getty_auto=0 systemd.show_status=0 systemd.debug_shell systemd.default_debug_tty=ttyS0",
            "-device", "ich9-ahci,id=ahci",
            "-drive", f"file={ISO},format=raw,media=cdrom,readonly=on,if=none,id=installer",
            "-device", "ide-cd,drive=installer,bus=ahci.0",
            "-drive", f"if=none,id=scratch,file={scratch},format=raw,cache=none",
            "-device", f"virtio-blk-pci,drive=scratch,serial={SCRATCH_SERIAL}",
            "-fsdev", f"local,id=source,path={repo},security_model=none,readonly=on",
            "-device", "virtio-9p-pci,fsdev=source,mount_tag=source",
            "-netdev", "user,id=net0,restrict=on",
            "-device", "virtio-net-pci,netdev=net0",
            "-chardev", f"socket,id=serial0,path={serial_path},server=on,wait=off",
            "-serial", "chardev:serial0",
            "-monitor", f"unix:{monitor_path},server=on,wait=off",
            "-display", "none",
            "-vga", "std",
            "-no-reboot",
            "-no-user-config",
            "-name", "librecore-disposable-arch-preflight-20261016-retry8",
        ]
        if any("usb" in arg.lower() for arg in argv):
            raise RunnerError("QEMU argv unexpectedly includes a USB option/device")
        if "-drive" not in argv or any("readonly=on" not in arg for arg in argv if arg.startswith("file=" + str(ISO))):
            raise RunnerError("QEMU ISO drive is not explicitly read-only")
        result["qemu_argv"] = argv
        atomic_json(output_dir / "argv.json", {"argv": argv, "cwd": str(output_dir)})

        monitor_transcript = (output_dir / "hmp-transcript.txt").open("xb")
        serial_log = serial_log_path.open("xb")
        qemu_log = qemu_log_path.open("xb")
        launch_started = time.monotonic()
        deadline = launch_started + WALL_SECONDS
        result["launch_utc_epoch"] = time.time()
        result["status"] = "STARTING"
        atomic_json(result_path, result)

        process = subprocess.Popen(
            argv,
            cwd=output_dir,
            stdin=subprocess.DEVNULL,
            stdout=qemu_log,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
            env={"PATH": "/usr/bin:/bin", "HOME": str(RUNNER_HOME), "LC_ALL": "C"},
        )
        result["process_launched"] = True
        result["qemu_pid"] = process.pid
        monitor = connect_unix(monitor_path, process, min(deadline, time.monotonic() + CONNECT_SECONDS))
        monitor.settimeout(0.2)
        initial_hmp = read_until_monitor_prompt(monitor)
        monitor_transcript.write(initial_hmp)
        monitor_transcript.flush()
        if b"(qemu)" not in initial_hmp:
            raise RunnerError("QEMU monitor did not produce its prompt")
        result["monitor_connected"] = True
        serial = connect_unix(serial_path, process, min(deadline, time.monotonic() + CONNECT_SECONDS))
        serial.settimeout(0.2)
        result["serial_connected"] = True
        result["status"] = "BOOTING"
        hmp_command(monitor, monitor_transcript, "info status")
        hmp_command(monitor, monitor_transcript, "info block")

        serial_data = bytearray()
        command_records = result.setdefault("guest_shell_commands", [])
        guest_inputs = result.setdefault("guest_shell_inputs", [])
        shell_ready = False
        debug_shell_newline_attempted = False
        last_screenshot = 0.0
        last_c_check = 0.0
        while time.monotonic() < deadline and process.poll() is None:
            now = time.monotonic()
            if now - last_c_check >= 2:
                result["last_c_free_bytes"] = check_c_floor()
                last_c_check = now
            if now - last_screenshot >= 45:
                hmp_command(monitor, monitor_transcript, f"screendump {screenshot_path}")
                last_screenshot = now
            receive_serial(serial, serial_log, serial_data, process=process, deadline=deadline)
            if not debug_shell_newline_attempted:
                marker = find_debug_shell_service_marker(bytes(serial_data))
                if marker is not None:
                    marker_seen_at = time.time()
                    result["debug_shell_service_marker_seen"] = True
                    result["debug_shell_service_marker"] = {
                        **marker,
                        "observed_utc_epoch": marker_seen_at,
                        "osc_type": 3008,
                        "match_rule": "complete OSC 3008 sequence; exactly one semicolon-delimited servicename field equal to debug-shell.service",
                        "maximum_sequence_bytes": DEBUG_SHELL_OSC_MAX_BYTES,
                    }
                    response_start_offset = send_debug_shell_newline_once(
                        serial,
                        serial_data,
                        serial_log,
                        result,
                        guest_inputs,
                        result["debug_shell_service_marker"],
                    )
                    debug_shell_newline_attempted = True
                    prompt_deadline = min(
                        deadline,
                        time.monotonic() + DEBUG_SHELL_PROMPT_SECONDS,
                    )
                    prompt_deadline_utc_epoch = time.time() + max(
                        0.0,
                        prompt_deadline - time.monotonic(),
                    )
                    prompt_match = await_debug_shell_prompt(
                        serial,
                        serial_log,
                        serial_data,
                        process=process,
                        deadline=prompt_deadline,
                        start_offset=response_start_offset,
                    )
                    response_bytes = bytes(serial_data[response_start_offset:])
                    result["debug_shell_newline_response"] = {
                        "observed_utc_epoch": time.time(),
                        "start_offset": response_start_offset,
                        "length_bytes": len(response_bytes),
                        "sha256": sha256_bytes(response_bytes),
                        "fresh_root_shell_prompt_seen": prompt_match is not None,
                        "prompt_match": prompt_match,
                        "prompt_deadline_utc_epoch": prompt_deadline_utc_epoch,
                        "prompt_window_seconds": DEBUG_SHELL_PROMPT_SECONDS,
                    }
                    guest_inputs[-1]["response_length_bytes"] = len(response_bytes)
                    guest_inputs[-1]["response_sha256"] = sha256_bytes(response_bytes)
                    guest_inputs[-1]["fresh_root_shell_prompt_seen"] = prompt_match is not None
                    guest_inputs[-1]["prompt_match"] = prompt_match
                    if prompt_match is not None:
                        shell_ready = True
                        result["guest_shell_prompt_seen"] = True
                        result["guest_shell_prompt_utc_epoch"] = time.time()
                        result["guest_shell_prompt_serial_offset"] = response_start_offset
                        result["guest_shell_prompt_match"] = prompt_match
                        result["status"] = "SYSTEMD_DEBUG_SHELL_PROMPT_AFTER_MARKER_AND_NEWLINE"
                        result["guest_shell_prompt_type"] = "SYSTEMD_DEBUG_SHELL_EPHEMERAL_NOT_NORMAL_LOGIN"
                        hmp_command(monitor, monitor_transcript, f"screendump {screenshot_path}")
                        last_screenshot = time.monotonic()
                    break
        if not shell_ready:
            if result["debug_shell_service_marker_seen"]:
                result["status"] = "DEBUG_SHELL_SERVICE_STARTED_NO_PROMPT_AFTER_NEWLINE"
                result["stop_reason"] = (
                    f"exact debug-shell.service marker was observed and one newline sent, but no accepted fresh prompt appeared within {DEBUG_SHELL_PROMPT_SECONDS} seconds or before the {WALL_SECONDS}-second global bound/QEMU exit"
                )
            else:
                result["status"] = "DEBUG_SHELL_SERVICE_MARKER_NOT_REACHED"
                result["stop_reason"] = (
                    f"{WALL_SECONDS}-second bound, QEMU exit, or C: floor before exact OSC servicename=debug-shell.service marker"
                )
            raise RunnerError(result["stop_reason"])

        # Disable serial terminal input echo, then prove a unique command token
        # is absent from the output before relying on command markers.
        echo_canary = "LC_ECHO_CANARY_" + secrets.token_hex(8)
        echo_marker = "LC_ECHO_OFF_" + secrets.token_hex(8)
        send_shell_line_wait_prompt(
            serial,
            serial_log,
            serial_data,
            process=process,
            command="stty -echo",
            deadline=min(deadline, time.monotonic() + COMMAND_SECONDS),
            command_records=command_records,
            prompt_mode="debug",
        )
        echo_command = f": {echo_canary}; printf '\\n{echo_marker}\\n'"
        echo_output = send_shell_command(
            serial,
            serial_log,
            serial_data,
            process=process,
            command=echo_command,
            marker=echo_marker,
            deadline=min(deadline, time.monotonic() + COMMAND_SECONDS),
            command_records=command_records,
            prompt_mode="debug",
        )
        if echo_canary.encode("ascii") in echo_output:
            raise RunnerError("serial terminal command echo remains enabled; marker parsing is unsafe")
        result["serial_command_echo_disabled"] = True

        mount_marker = "LC_SOURCE_MOUNT_" + secrets.token_hex(8)
        mount_command = (
            "if mkdir -p /mnt/source && "
            "mount -t 9p -o trans=virtio,version=9p2000.L,ro source /mnt/source; "
            f"then rc=0; else rc=$?; fi; printf '\\n{mount_marker}:%s\\n' \"$rc\""
        )
        mount_output = send_shell_command(
            serial,
            serial_log,
            serial_data,
            process=process,
            command=mount_command,
            marker=mount_marker,
            deadline=min(deadline, time.monotonic() + COMMAND_SECONDS),
            command_records=command_records,
            prompt_mode="debug",
        )
        mount_rc_match = re.search(re.escape(mount_marker.encode("ascii")) + rb":([0-9]+)", mount_output)
        if mount_rc_match is None or mount_rc_match.group(1) != b"0":
            raise RunnerError("read-only source 9p mount did not return success")
        result["source_mount"] = "9p read-only mount command returned zero"

        copy_marker = "LC_HELPER_COPY_" + secrets.token_hex(8)
        guest_source_helper = f"/mnt/source/{GUEST_PROBE_RELATIVE.as_posix()}"
        copy_command = (
            f"cp -- '{guest_source_helper}' /run/lc-guest-probe.py && "
            "chmod 0755 /run/lc-guest-probe.py; rc=$?; "
            "sha256sum /run/lc-guest-probe.py; "
            f"printf '\\n{copy_marker}:%s\\n' \"$rc\""
        )
        copy_output = send_shell_command(
            serial,
            serial_log,
            serial_data,
            process=process,
            command=copy_command,
            marker=copy_marker + ":0",
            deadline=min(deadline, time.monotonic() + COMMAND_SECONDS),
            command_records=command_records,
            prompt_mode="debug",
        )
        if helper_hash.encode("ascii") not in copy_output:
            raise RunnerError("read-only 9p helper copy hash does not match the host-reviewed bytes")
        result["guest_helper_copy_sha256"] = helper_hash
        result["guest_helper_copy_method"] = "cp from verified read-only 9p source share; guest hash checked before execution"

        probe_marker = "LC_GUEST_PROBE_RC_" + secrets.token_hex(8)
        probe_command = (
            f"LC_EXPECTED_GUEST_HELPER_SHA256={helper_hash} "
            f"LC_EXPECTED_USB_BUILDER_MANIFEST_SHA256={EXPECTED_USB_BUILDER_MANIFEST_SHA256} "
            "PYTHONOPTIMIZE=0 "
            "PYTHONDONTWRITEBYTECODE=1 "
            "/usr/bin/python3 /run/lc-guest-probe.py; rc=$?; "
            f"printf '\\n{probe_marker}:%s\\n' \"$rc\""
        )
        result["guest_package_query_status_before_probe"] = "NOT_STARTED"
        probe_output = send_shell_command(
            serial,
            serial_log,
            serial_data,
            process=process,
            command=probe_command,
            marker=probe_marker,
            deadline=min(deadline, time.monotonic() + GUEST_PROBE_COMMAND_SECONDS),
            command_records=command_records,
            prompt_mode="debug",
        )
        probe_lines = probe_output.decode("utf-8", errors="replace").splitlines()
        result["guest_step_timing_records"] = [line for line in probe_lines if line.startswith(("STEP_START=", "STEP_END="))]
        result["guest_locked_package_query_records"] = [
            line
            for line in probe_lines
            if line.startswith(
                (
                    "LOCKED_PACKAGE_QUERY_START_UTC=",
                    "LOCKED_PACKAGE_QUERY_ARGV=",
                    "LOCKED_PACKAGE_QUERY_PID=",
                    "LOCKED_PACKAGE_QUERY_HEARTBEAT=",
                    "LOCKED_PACKAGE_QUERY_TIMEOUT=",
                    "LOCKED_PACKAGE_QUERY_CAPTURE=",
                    "LOCKED_PACKAGE_QUERY_OUTCOME=",
                )
            )
        ]
        probe_rc_match = re.search(re.escape(probe_marker.encode("ascii")) + rb":([0-9]+)", probe_output)
        if probe_rc_match is None:
            raise RunnerError("guest helper command produced no exit marker")
        result["guest_probe_exit"] = int(probe_rc_match.group(1))
        if result["guest_probe_exit"] != 0:
            query_outcome = next(
                (
                    line.partition("=")[2]
                    for line in result["guest_locked_package_query_records"]
                    if line.startswith("LOCKED_PACKAGE_QUERY_OUTCOME=")
                ),
                "NOT_REPORTED",
            )
            result["guest_package_query_status_before_probe"] = query_outcome
            result["status"] = (
                "GUEST_LOCKED_PACKAGE_QUERY_INCOMPLETE"
                if query_outcome.startswith("INCOMPLETE_")
                else "GUEST_ROOTLESS_PREFLIGHT_FAILED"
            )
            raise RunnerError(
                f"guest helper returned {result['guest_probe_exit']} after package-query outcome {query_outcome}"
            )
        query_outcome = next(
            (
                line.partition("=")[2]
                for line in result["guest_locked_package_query_records"]
                if line.startswith("LOCKED_PACKAGE_QUERY_OUTCOME=")
            ),
            "NOT_REPORTED",
        )
        if query_outcome != "COMPLETE_PIN_MATCH":
            result["status"] = "GUEST_LOCKED_PACKAGE_QUERY_INCOMPLETE"
            result["guest_package_query_status_before_probe"] = query_outcome
            raise RunnerError("guest helper did not report a completed exact locked package query")
        result["guest_package_query_status_before_probe"] = "COMPLETE_PIN_MATCH"
        expected_guest_integrity_lines = (
            "CHILD_PYTHON_OPTIMIZE=0",
            f"GUEST_HELPER_SHA256={helper_hash}",
            f"USB_BUILDER_SOURCE_MANIFEST_SHA256={EXPECTED_USB_BUILDER_MANIFEST_SHA256}",
            "USB_BUILDER_SOURCE_MANIFEST_FILE_COUNT=24",
            "USB_BUILDER_SOURCE_WRITE_OPEN=BLOCKED errno=30",
            "LOCKED_PACKAGE_QUERY_OUTCOME=COMPLETE_PIN_MATCH",
            "GUEST_GIT_CHECK=NOT_PERFORMED; host Git HEAD/status validation only",
            f"CHILD_GUEST_HELPER_SHA256={helper_hash}",
            f"CHILD_USB_BUILDER_MANIFEST_SHA256={EXPECTED_USB_BUILDER_MANIFEST_SHA256}",
            "CHILD_USB_BUILDER_MANIFEST_FILE_COUNT=24",
            "CHILD_PYTHON_CACHE_POLICY=PASS",
        )
        probe_lines = probe_output.decode("utf-8", errors="replace").splitlines()
        if any(line not in probe_lines for line in expected_guest_integrity_lines):
            raise RunnerError("guest helper omitted or mismatched a pinned source/cache integrity record")
        result["guest_source_integrity"] = {
            "helper_sha256": helper_hash,
            "manifest_sha256": EXPECTED_USB_BUILDER_MANIFEST_SHA256,
            "manifest_file_count": 24,
            "source_write_open": "BLOCKED errno=30 (EROFS)",
            "guest_git_check": "NOT_PERFORMED; host Git HEAD/status validation only",
            "private_pycache_prefix": "/run/lc-pycache",
            "pycache_policy": "PYTHONPYCACHEPREFIX plus PYTHONDONTWRITEBYTECODE; child verified both settings and fresh empty private prefix",
            "python_optimize": "PYTHONOPTIMIZE=0; child rejects sys.flags.optimize != 0 and emits CHILD_PYTHON_OPTIMIZE=0",
            "helper_copy_method": "cp from verified read-only 9p source share; guest hash checked before execution",
        }
        if b"GUEST_PROBE_COMPLETE_NO_BUILD" not in probe_output:
            raise RunnerError("guest helper returned zero without its final no-build marker")
        result["status"] = "HOST_AND_MOUNT_PREFLIGHTS_COMPLETED"
        result["guest_probe_marker_seen"] = True
        result["guest_probe_exit"] = 0
        result["stop_reason"] = "all bounded generic-guest checks completed; stopping immediately"
        hmp_command(monitor, monitor_transcript, f"screendump {screenshot_path}")
        result["guest_screendump"] = str(screenshot_path)
        result["qemu_exit_code_before_stop"] = None
        result["status"] = "GUEST_PROBE_COMPLETED_STOPPING"

    except Exception as exc:
        result["status"] = result.get("status", "INCOMPLETE")
        result["runner_error"] = f"{type(exc).__name__}: {exc}"
        return_code = 1
    finally:
        if process is not None and launch_started is not None:
            result["elapsed_seconds_before_stop"] = round(time.monotonic() - launch_started, 3)
        if process is not None:
            try:
                result["qemu_exit_code"], result["termination"] = stop_process(
                    process, monitor, monitor_transcript
                )
                result["qemu_terminated"] = process.poll() is not None
            except Exception as exc:
                result["qemu_terminated"] = process.poll() is not None
                result["cleanup_error"] = f"{type(exc).__name__}: {exc}"
                return_code = 2
        for connection in (serial, monitor):
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass
        for stream in (serial_log, monitor_transcript, qemu_log):
            if stream is not None:
                try:
                    stream.flush()
                    os.fsync(stream.fileno())
                    stream.close()
                except OSError:
                    return_code = 2
        try:
            evidence_files: dict[str, dict[str, Any]] = {}
            for evidence_path in (
                output_dir / "argv.json",
                output_dir / "vmlinuz-linux",
                output_dir / "initramfs-linux.img",
                output_dir / "vga.ppm",
                output_dir / "serial.log",
                output_dir / "hmp-transcript.txt",
                output_dir / "qemu.log",
            ):
                try:
                    evidence_info = evidence_path.lstat()
                except FileNotFoundError:
                    continue
                if not stat.S_ISREG(evidence_info.st_mode) or stat.S_ISLNK(evidence_info.st_mode):
                    raise RunnerError(f"evidence output is not a regular non-symlink file: {evidence_path}")
                evidence_files[evidence_path.name] = {
                    "size_bytes": evidence_info.st_size,
                    "sha256": sha256_file(evidence_path),
                }
            result["evidence_files"] = evidence_files
            if iso_hash_before is not None:
                result["iso_sha256_after"] = sha256_file(ISO)
                result["iso_unchanged"] = result["iso_sha256_after"] == iso_hash_before
            result["scratch_allocated_bytes_after"] = scratch.stat().st_blocks * 512 if scratch and scratch.exists() else None
            result["c_free_after_bytes"] = shutil.disk_usage("/mnt/c").free
            result["c_free_floor_preserved"] = result["c_free_after_bytes"] >= C_FREE_FLOOR
            result["hardware_or_firmware_validation"] = False
            result["product_build_invoked"] = False
            result["package_install_or_sync_invoked"] = False
            result["offline_launcher_status"] = "OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE; not invoked"
            if process is not None and result.get("qemu_terminated"):
                completed = (
                    result.get("process_launched") is True
                    and result.get("monitor_connected") is True
                    and result.get("serial_connected") is True
                    and result.get("runner_error") is None
                    and result.get("cleanup_error") is None
                    and result.get("qemu_exit_code") == 0
                    and result.get("iso_unchanged") is True
                    and result.get("c_free_floor_preserved") is True
                    and result.get("guest_probe_marker_seen") is True
                )
                result["diagnostic_complete"] = completed
                if completed:
                    result["status"] = "DEBUG_SHELL_PREFLIGHT_COMPLETE_NO_NORMAL_BOOT_VALIDATION"
                    return_code = 0
                else:
                    if result.get("status") == "GUEST_PROBE_COMPLETED_STOPPING":
                        result["status"] = "INCOMPLETE_OR_INTEGRITY_FAILURE"
                    return_code = 1 if return_code == 0 else return_code
            else:
                result["diagnostic_complete"] = False
                return_code = 1 if return_code == 0 else return_code
            atomic_json(result_path, result)
        except Exception as exc:
            result["result_publication_error"] = f"{type(exc).__name__}: {exc}"
            return_code = 2
            try:
                atomic_json(result_path, result)
            except Exception:
                pass
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
