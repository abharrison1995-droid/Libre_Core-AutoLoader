#!/usr/bin/env python3
"""Bounded, generic Arch ISO guest boot diagnostic (no product image)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pty
import select
import signal
import socket
import struct
import subprocess
import termios
import time
import fcntl
from pathlib import Path
from typing import Any


ISO = Path("/dev/shm/archlinux-2026.10.01-x86_64.iso")
EXPECTED_ISO_SHA256 = "684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5"
RUN_SECONDS = 180
SCREENSHOT_AT_SECONDS = 120
HASH_LIMIT_SECONDS = 240


def sha256_file(path: Path, *, timeout_seconds: int) -> str:
    digest = hashlib.sha256()
    deadline = time.monotonic() + timeout_seconds
    with path.open("rb") as stream:
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError(f"hash deadline expired for {path}")
            block = stream.read(4 * 1024 * 1024)
            if not block:
                return digest.hexdigest()
            digest.update(block)


def receive_prompt(connection: socket.socket, timeout: float) -> bytes:
    deadline = time.monotonic() + timeout
    received = bytearray()
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        ready, _, _ = select.select([connection], [], [], min(0.5, remaining))
        if not ready:
            continue
        block = connection.recv(65536)
        if not block:
            break
        received.extend(block)
        if b"(qemu)" in received:
            break
    return bytes(received)


def hmp_command(connection: socket.socket, command: str, transcript: Any) -> bytes:
    connection.sendall(command.encode("ascii") + b"\n")
    response = receive_prompt(connection, timeout=8.0)
    transcript.write(f"\n>>> {command}\n".encode("utf-8"))
    transcript.write(response)
    transcript.flush()
    return response


def drain_pty(master_fd: int, output: Any) -> None:
    while True:
        ready, _, _ = select.select([master_fd], [], [], 0)
        if not ready:
            return
        try:
            block = os.read(master_fd, 65536)
        except OSError:
            return
        if not block:
            return
        output.write(block)
        output.flush()


def stop_process(process: subprocess.Popen[bytes], connection: socket.socket | None) -> tuple[int | None, str]:
    if process.poll() is not None:
        return process.returncode, "process_exited_before_stop_request"
    if connection is not None:
        try:
            connection.sendall(b"quit\n")
            process.wait(timeout=10)
            return process.returncode, "HMP quit; process exited"
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
    return process.returncode, "process-group termination after bounded run"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    output_dir: Path = args.output_dir.resolve()
    if output_dir.exists():
        parser.error(f"output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True, mode=0o700)

    result: dict[str, Any] = {
        "diagnostic": "generic Arch Linux ISO guest only",
        "iso_path": str(ISO),
        "expected_iso_sha256": EXPECTED_ISO_SHA256,
        "qemu_wall_limit_seconds": RUN_SECONDS,
        "screenshot_target_seconds": SCREENSHOT_AT_SECONDS,
        "guest_login_shell": "not_assessed",
        "hardware_or_product_validation": False,
    }
    (output_dir / "run-result.json").write_text("{}\n", encoding="utf-8")
    try:
        before_hash = sha256_file(ISO, timeout_seconds=HASH_LIMIT_SECONDS)
    except (OSError, TimeoutError) as exc:
        result.update(status="BLOCKED_BEFORE_LAUNCH", error=f"ISO hash failed: {exc}")
        (output_dir / "run-result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        return 2
    result["iso_sha256_before"] = before_hash
    if before_hash != EXPECTED_ISO_SHA256:
        result.update(status="BLOCKED_BEFORE_LAUNCH", error="ISO hash mismatch")
        (output_dir / "run-result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        return 2

    version = subprocess.run(
        ["qemu-system-x86_64", "--version"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        close_fds=True,
        timeout=10,
        text=True,
    )
    (output_dir / "qemu-version.txt").write_text(version.stdout, encoding="utf-8")
    if version.returncode != 0:
        result.update(status="BLOCKED_BEFORE_LAUNCH", error=f"QEMU version command exited {version.returncode}")
        (output_dir / "run-result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        return 2

    monitor_path = output_dir / "monitor.sock"
    screenshot_path = output_dir / "vga.ppm"
    serial_path = output_dir / "serial.log"
    stderr_path = output_dir / "qemu-stderr.log"
    pty_path = output_dir / "qemu-display-pty.log"
    argv = [
        "qemu-system-x86_64",
        "-machine", "pc,accel=tcg",
        "-cpu", "max",
        "-m", "2048",
        "-smp", "2",
        "-display", "curses",
        "-vga", "std",
        "-monitor", f"unix:{monitor_path},server=on,wait=off",
        "-serial", f"file:{serial_path}",
        "-drive", f"file={ISO},media=cdrom,readonly=on,if=ide,index=2",
        "-boot", "order=d",
        "-nic", "none",
        "-no-reboot",
        "-no-user-config",
        "-name", "arch-generic-live-diagnostic-20261016",
    ]
    (output_dir / "argv.json").write_text(json.dumps(argv, indent=2) + "\n", encoding="utf-8")
    env = os.environ.copy()
    env["TERM"] = "xterm"
    master_fd, slave_fd = pty.openpty()
    fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    stderr_stream = (output_dir / "qemu-stderr.log").open("wb")
    display_stream = (output_dir / "qemu-display-pty.log").open("wb")
    process: subprocess.Popen[bytes] | None = None
    monitor: socket.socket | None = None
    screenshot_attempted = False
    screenshot_file_created = False
    monitor_connected = False
    process_launched = False
    monitor_transcript = (output_dir / "hmp-transcript.txt").open("wb")
    start = time.monotonic()
    stop_reason = "startup_failed"
    try:
        process = subprocess.Popen(
            argv,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=stderr_stream,
            env=env,
            close_fds=True,
            start_new_session=True,
        )
        process_launched = True
        result["process_launched"] = True
        os.close(slave_fd)
        slave_fd = -1
        result["qemu_pid"] = process.pid
        result["launch_utc_epoch"] = time.time()
        connect_deadline = time.monotonic() + 20
        while time.monotonic() < connect_deadline and process.poll() is None:
            drain_pty(master_fd, display_stream)
            try:
                monitor = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                monitor.settimeout(1.0)
                monitor.connect(str(monitor_path))
                break
            except OSError:
                if monitor is not None:
                    monitor.close()
                monitor = None
                time.sleep(0.25)
        if monitor is None:
            result["monitor_connected"] = False
            stop_reason = "HMP monitor unavailable before 20-second startup bound"
        else:
            monitor_connected = True
            result["monitor_connected"] = True
            monitor.settimeout(0.5)
            greeting = receive_prompt(monitor, timeout=5.0)
            monitor_transcript.write(b"<<< monitor greeting >>>\n" + greeting)
            monitor_transcript.flush()
            while True:
                elapsed = time.monotonic() - start
                drain_pty(master_fd, display_stream)
                if process.poll() is not None:
                    stop_reason = "QEMU exited before wall limit"
                    break
                if not screenshot_attempted and elapsed >= SCREENSHOT_AT_SECONDS:
                    screenshot_attempted = True
                    response = hmp_command(monitor, f"screendump {screenshot_path}", monitor_transcript)
                    result["screendump_response"] = response.decode("utf-8", errors="replace")[-2000:]
                    screenshot_file_created = screenshot_path.is_file() and screenshot_path.stat().st_size > 0
                    result["screendump_file_created"] = screenshot_file_created
                    hmp_command(monitor, "info status", monitor_transcript)
                if elapsed >= RUN_SECONDS:
                    stop_reason = "180-second guest wall limit reached"
                    break
                time.sleep(0.25)
        result["elapsed_guest_seconds"] = round(time.monotonic() - start, 3)
    except BaseException as exc:
        result["runner_error"] = f"{type(exc).__name__}: {exc}"
        stop_reason = "runner exception"
    finally:
        if process is not None:
            try:
                returncode, termination = stop_process(process, monitor)
                result["qemu_exit_code"] = returncode
                result["termination"] = termination
            except BaseException as exc:
                result["stop_error"] = f"{type(exc).__name__}: {exc}"
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    except OSError as kill_exc:
                        result["cleanup_error"] = f"{type(kill_exc).__name__}: {kill_exc}"
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        except OSError as kill_exc:
                            result["cleanup_error"] = f"{type(kill_exc).__name__}: {kill_exc}"
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            result["cleanup_error"] = "QEMU remained alive after bounded SIGKILL wait"
                result["qemu_exit_code"] = process.poll()
                result["termination"] = "stop path raised; bounded process-group cleanup attempted"
            result["process_terminated"] = process.poll() is not None
        else:
            result["process_launched"] = False
            result["process_terminated"] = False
        result["stop_reason"] = stop_reason
        result["screendump_attempted"] = screenshot_attempted
        if monitor is not None:
            monitor.close()
        monitor_transcript.close()
        display_stream.close()
        stderr_stream.close()
        if slave_fd >= 0:
            os.close(slave_fd)
        os.close(master_fd)

    try:
        after_hash = sha256_file(ISO, timeout_seconds=HASH_LIMIT_SECONDS)
        result["iso_sha256_after"] = after_hash
        result["iso_unchanged"] = after_hash == before_hash == EXPECTED_ISO_SHA256
    except (OSError, TimeoutError) as exc:
        result["iso_after_hash_error"] = f"{type(exc).__name__}: {exc}"
        result["iso_unchanged"] = False
    capture_complete = (
        process_launched
        and monitor_connected
        and screenshot_attempted
        and screenshot_file_created
        and "runner_error" not in result
        and result.get("process_terminated") is True
        and result.get("qemu_exit_code") == 0
        and result.get("iso_unchanged") is True
    )
    result["status"] = (
        "DIAGNOSTIC_CAPTURE_COMPLETE"
        if capture_complete
        else "INCOMPLETE_OR_FAILED"
    )
    (output_dir / "run-result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0 if capture_complete else 3


if __name__ == "__main__":
    raise SystemExit(main())
