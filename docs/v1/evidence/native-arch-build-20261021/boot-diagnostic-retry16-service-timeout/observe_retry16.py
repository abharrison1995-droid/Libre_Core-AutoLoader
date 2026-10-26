#!/usr/bin/env python3
import datetime
import hashlib
import json
import os
import queue
import re
import select
import shutil
import socket
import stat
import subprocess
import sys
import termios
import time
import tty
from pathlib import Path

from retry16_gate import (FAILURE_LINE, READ_ONLY_COMMANDS, PtyAnnouncementScanner, SerialGate, normalize_serial, query_sequence_ready)

ROOT = Path("/mnt/wsl/PHYSICALDRIVE1p2/opt/librecore-autoloader-v1-vm/native-arch-build-20261021")
ATTEMPT = ROOT / "attempt-current-retry16-nsresourced-status-observation"
ISO = Path("/mnt/wsl/PHYSICALDRIVE1p2/opt/librecore-autoloader-v1-vm/iso-boot/archlinux-2026.10.01-x86_64.iso")
KERNEL = ROOT / "attempt-current-retry13-direct-kernel/vmlinuz-linux"
INITRAMFS = ROOT / "attempt-current-retry13-direct-kernel/initramfs-linux.img"
SOURCE = ROOT / "source-8ea32c5.img"
BASE_WORKSPACE = ROOT / "arch-v1-build.qcow2"
BASE_RESULTS = ROOT / "results-8ea32c5.img"
WORKSPACE = ATTEMPT / "arch-workspace-overlay.qcow2"
RESULTS = ATTEMPT / "results-overlay.qcow2"
SERIAL_OUT = ATTEMPT / "serial-output.log"
SERIAL_IN = ATTEMPT / "serial-input.log"
QEMU_OUTPUT = ATTEMPT / "qemu-output.log"
EVENTS = ATTEMPT / "events.jsonl"
RESULT = ATTEMPT / "run-result.json"
INPUTS_BEFORE = ATTEMPT / "input-hashes-before.txt"
INPUTS_AFTER = ATTEMPT / "input-hashes-after.txt"
QEMU = "/usr/bin/qemu-system-x86_64"
QEMU_IMG = "/usr/bin/qemu-img"
QMP = Path("/tmp/lcarch-v1-retry16-status.qmp")
QEMU_VERSION_EXPECTED = "QEMU emulator version 10.2.1 (Debian 1:10.2.1+ds-1ubuntu3.2)"
KERNEL_ARGS = "archisobasedir=arch archisosearchuuid=2026-10-01-14-49-03-00 console=ttyS0,115200n8 systemd.getty_auto=0 systemd.debug_shell systemd.default_debug_tty=ttyS0"
EXPECTED_INPUTS = {
    ISO: "684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5",
    KERNEL: "8ebc2c71271e000540f0a7545afd8e73504fcb724671f3d38c720b241842b901",
    INITRAMFS: "35b4362207fcabc66535444aea0b97f3508128b32e97e66aa5f56487007bfe52",
    SOURCE: "0751078e2a362361212ddde66377766cf056d3f18bd4af923959f2ad628b194b",
}
WALL_SECONDS = 600
FREE_FLOOR_BYTES = 120 * 1024**3
SERIAL_CAP_BYTES = 64 * 1024**2
QUERY_TIMEOUT_SECONDS = 90
QUERY_OUTPUT_CAP_BYTES = 1024 * 1024
QUERY_STABLE_SECONDS = 1.5
QEMU_OUTPUT_CAP_BYTES = 2 * 1024**2


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def free_bytes():
    st = os.statvfs(ATTEMPT)
    return st.f_bavail * st.f_frsize


def record(event_file, kind, **fields):
    row = {"utc": utc_now(), "kind": kind, **fields}
    event_file.write(json.dumps(row, sort_keys=True) + "\n")
    event_file.flush()
    os.fsync(event_file.fileno())


def reject_symlink_components(path: Path):
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            if current.is_symlink():
                raise RuntimeError("symlink path component rejected: " + str(current))
        except OSError as exc:
            raise RuntimeError("cannot inspect path component: " + str(current)) from exc


def hash_file(path: Path) -> str:
    reject_symlink_components(path)
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode):
        raise RuntimeError("expected regular input file: " + str(path))
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def hash_inputs() -> str:
    rows = []
    for path, expected in EXPECTED_INPUTS.items():
        actual = hash_file(path)
        if actual != expected:
            raise RuntimeError("input SHA-256 mismatch: " + str(path))
        rows.append("{}  {}".format(actual, path))
    return "\n".join(rows) + "\n"


def qemu_img_info(path: Path):
    completed = subprocess.run(
        [QEMU_IMG, "info", "--output=json", str(path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=True, timeout=15, close_fds=True,
    )
    return json.loads(completed.stdout.decode("utf-8"))


def validate_overlay(path: Path, backing: Path, backing_format: str, expected_size: int):
    reject_symlink_components(path)
    if not path.is_file():
        raise RuntimeError("overlay missing: " + str(path))
    info = qemu_img_info(path)
    if info.get("format") != "qcow2" or info.get("virtual-size") != expected_size:
        raise RuntimeError("overlay format/virtual size mismatch: " + str(path))
    actual_backing = info.get("backing-filename")
    actual_format = info.get("backing-filename-format")
    if actual_backing is None or os.path.realpath(actual_backing) != os.path.realpath(backing):
        raise RuntimeError("overlay backing path mismatch: " + str(path))
    if actual_format != backing_format:
        raise RuntimeError("overlay backing format mismatch: " + str(path))


def validate_preflight():
    reject_symlink_components(ATTEMPT)
    st = ATTEMPT.stat()
    if not stat.S_ISDIR(st.st_mode) or (st.st_mode & 0o077) != 0:
        raise RuntimeError("attempt directory must be a private directory mode 0700")
    outputs = (SERIAL_OUT, SERIAL_IN, QEMU_OUTPUT, EVENTS, RESULT, INPUTS_BEFORE, INPUTS_AFTER)
    for path in outputs:
        if path.exists() or path.is_symlink():
            raise RuntimeError("refusing to overwrite output: " + str(path))
    if QMP.exists() or QMP.is_symlink():
        raise RuntimeError("refusing to reuse QMP socket path")
    if free_bytes() < FREE_FLOOR_BYTES:
        raise RuntimeError("120 GiB free-space floor failed before launch")
    for path in EXPECTED_INPUTS:
        if not path.is_file():
            raise RuntimeError("input missing: " + str(path))
    validate_overlay(WORKSPACE, BASE_WORKSPACE, "qcow2", 85899345920)
    validate_overlay(RESULTS, BASE_RESULTS, "raw", 4294967296)
    if shutil.which(QEMU) is None or shutil.which(QEMU_IMG) is None:
        raise RuntimeError("pinned QEMU tool path is unavailable")
    version = subprocess.check_output([QEMU, "--version"], text=True, timeout=10).splitlines()[0]
    if version != QEMU_VERSION_EXPECTED:
        raise RuntimeError("unexpected QEMU version: " + version)
    before = hash_inputs()
    INPUTS_BEFORE.write_text(before, encoding="utf-8")
    return version


def build_argv():
    return [
        QEMU, "-machine", "pc", "-accel", "tcg,thread=multi", "-cpu", "max",
        "-smp", "4", "-m", "6144", "-nodefaults",
        "-kernel", str(KERNEL), "-initrd", str(INITRAMFS),
        "-append", KERNEL_ARGS,
        "-drive", "file={},if=ide,media=cdrom,readonly=on,format=raw".format(ISO),
        "-drive", "file={},if=virtio,format=qcow2".format(WORKSPACE),
        "-drive", "file={},if=virtio,format=qcow2".format(RESULTS),
        "-drive", "file={},if=virtio,format=raw,readonly=on".format(SOURCE),
        "-nic", "none", "-display", "none", "-serial", "pty",
        "-monitor", "none", "-qmp", "unix:{},server=on,wait=off".format(QMP),
        "-no-reboot",
    ]


def qmp_execute(command):
    deadline = time.monotonic() + 10
    last_error = None
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_UNIX) as sock:
                sock.settimeout(3)
                sock.connect(str(QMP))
                stream = sock.makefile("rwb", buffering=0)
                greeting = json.loads(stream.readline())
                if "QMP" not in greeting:
                    raise RuntimeError("QMP greeting missing")
                stream.write(b'{"execute":"qmp_capabilities"}\r\n')
                while True:
                    response = json.loads(stream.readline())
                    if "error" in response:
                        raise RuntimeError("QMP capability negotiation failed")
                    if "return" in response:
                        break
                stream.write(json.dumps({"execute": command}).encode("ascii") + b"\r\n")
                while True:
                    response = json.loads(stream.readline())
                    if "error" in response:
                        raise RuntimeError("QMP command failed")
                    if "return" in response:
                        return response
        except Exception as exc:
            last_error = repr(exc)
            time.sleep(0.2)
    raise RuntimeError("QMP command did not complete: " + str(last_error))


def drain_qemu_output(proc, output_file, pty_scanner):
    found = []
    fd = proc.stdout.fileno()
    while True:
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            break
        if not data:
            break
        if output_file.tell() + len(data) > QEMU_OUTPUT_CAP_BYTES:
            raise RuntimeError("2 MiB merged QEMU output capture cap reached")
        output_file.write(data)
        output_file.flush()
        found.extend(pty_scanner.feed(data))
    return found

def open_serial_pty(path: str):
    if not re.fullmatch(r"/dev/pts/[0-9]+", path):
        raise RuntimeError("QEMU returned an unexpected serial PTY path")
    st = os.lstat(path)
    if not stat.S_ISCHR(st.st_mode):
        raise RuntimeError("serial PTY is not a character device")
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    tty.setraw(fd)
    return fd


def pump_once(proc, serial_fd, serial_file, output_file, pty_scanner, gate,
              event_file, started_mono, timeout):
    output_fd = proc.stdout.fileno()
    fds = [output_fd]
    if serial_fd is not None:
        fds.append(serial_fd)
    ready, _, _ = select.select(fds, [], [], timeout)
    serial_chunks = []
    pty_paths = []
    for fd in ready:
        if fd == output_fd:
            pty_paths.extend(drain_qemu_output(proc, output_file, pty_scanner))
        elif serial_fd is not None and fd == serial_fd:
            try:
                data = os.read(serial_fd, 4096)
            except BlockingIOError:
                data = b""
            if data:
                serial_file.write(data)
                serial_file.flush()
                serial_chunks.append(data)
                was_failed = gate.failure_seen
                gate.feed(data, time.monotonic() - started_mono)
                if not was_failed and gate.failure_seen:
                    record(event_file, "first_namespace_resource_manager_failure_seen",
                           elapsed_seconds=round(time.monotonic() - started_mono, 3))
    return serial_chunks, pty_paths

def write_command(fd, line: bytes):
    view = memoryview(line)
    while view:
        count = os.write(fd, view)
        view = view[count:]


def run_query(index, command, serial_fd, serial_file, serial_input_file, output_file,
              pty_scanner, gate, event_file, proc, started_mono, deadline):
    if command != READ_ONLY_COMMANDS[index]:
        raise RuntimeError("query differs from fixed allowlist")
    if not query_sequence_ready(serial_fd is not None,
                               gate.startup_ready(time.monotonic() - started_mono),
                               proc.poll() is None):
        raise RuntimeError("refusing guest input before serial/query gates pass")
    start_offset = gate.offset
    start_utc = utc_now()
    start_mono = time.monotonic()
    line = command.encode("ascii") + b"\n"
    serial_input_file.write("{}  COMMAND {}: {}\n".format(start_utc, index + 1, command).encode("utf-8"))
    serial_input_file.flush()
    os.fsync(serial_input_file.fileno())
    write_command(serial_fd, line)
    record(event_file, "read_only_query_sent", index=index + 1, command=command,
           utc_started=start_utc, serial_offset_before= start_offset,
           input_bytes=len(line), input_sha256=hashlib.sha256(line).hexdigest())
    output_hash = hashlib.sha256()
    output_bytes = 0
    output_chunks = []
    query_deadline = min(deadline, start_mono + QUERY_TIMEOUT_SECONDS)
    while time.monotonic() < query_deadline:
        chunks, paths = pump_once(proc, serial_fd, serial_file, output_file,
                                  pty_scanner, gate, event_file, started_mono, 0.2)
        if paths:
            record(event_file, "unexpected_additional_serial_pty", paths=paths)
            return {"index": index + 1, "command": command, "status": "STOP_UNEXPECTED_PTY"}
        for chunk in chunks:
            output_bytes += len(chunk)
            output_hash.update(chunk)
            if output_bytes > QUERY_OUTPUT_CAP_BYTES:
                return {"index": index + 1, "command": command, "status": "STOP_QUERY_OUTPUT_CAP",
                        "serial_offset_end": gate.offset, "captured_bytes": output_bytes}
        if gate.unexpected_prompt is not None:
            return {"index": index + 1, "command": command, "status": "STOP_UNEXPECTED_PROMPT",
                    "prompt": gate.unexpected_prompt, "serial_offset_end": gate.offset,
                    "captured_bytes": output_bytes}
        if gate.fatal_marker is not None:
            return {"index": index + 1, "command": command, "status": "STOP_FATAL_MARKER",
                    "marker": gate.fatal_marker, "serial_offset_end": gate.offset,
                    "captured_bytes": output_bytes}
        now = time.monotonic() - started_mono
        if gate.stable_prompt(now, after_offset=start_offset):
            return {"index": index + 1, "command": command, "status": "RETURNED_TO_FRESH_PROMPT",
                    "utc_started": start_utc, "utc_finished": utc_now(),
                    "serial_offset_start": start_offset, "serial_offset_end": gate.offset,
                    "captured_bytes": output_bytes, "captured_sha256": output_hash.hexdigest(),
                    "elapsed_seconds": round(time.monotonic() - start_mono, 3)}
        if proc.poll() is not None:
            return {"index": index + 1, "command": command, "status": "STOP_QEMU_EXITED",
                    "qemu_exit": proc.returncode, "serial_offset_end": gate.offset,
                    "captured_bytes": output_bytes}
    return {"index": index + 1, "command": command, "status": "STOP_QUERY_TIMEOUT",
            "serial_offset_end": gate.offset, "captured_bytes": output_bytes,
            "captured_sha256": output_hash.hexdigest()}


def stop_qemu(proc, event_file, stop_reason):
    qmp_result = None
    if proc.poll() is None:
        try:
            qmp_result = qmp_execute("quit")
            record(event_file, "qmp_quit", stop_reason=stop_reason, qmp_result=qmp_result)
        except Exception as exc:
            record(event_file, "qmp_quit_error", error=repr(exc), stop_reason=stop_reason)
            proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
            record(event_file, "forced_process_kill", qemu_return=proc.returncode)
    return qmp_result, proc.poll()


def main():
    version = validate_preflight()
    started_utc = utc_now()
    started_mono = time.monotonic()
    deadline = started_mono + WALL_SECONDS
    argv = build_argv()
    result = {
        "attempt": "current-source-native-arch-retry16-nsresourced-readonly-observation",
        "classification": "generic Arch ISO diagnostic; fixed read-only service queries only; not product build or hardware validation",
        "started_utc": started_utc,
        "qemu_version": version,
        "qemu_argv": argv,
        "kernel_arguments": KERNEL_ARGS,
        "network": "none",
        "passthrough": False,
        "guest_input_policy": "No input before exact Namespace Resource Manager failure plus stable exact sh-5.3# prompt; after that only the three fixed read-only queries are permitted.",
        "identity_device_policy": "Exact root debug-shell prompt and fixed QEMU block-device argv are required. No id/lsblk or other guest command is permitted in this attempt.",
        "qemu_output_capture": "QEMU stdout and stderr are merged into one nonblocking pipe to capture the PTY announcement without pipe deadlock.",
        "query_allowlist": list(READ_ONLY_COMMANDS),
        "queries": [],
        "stop_reason": None,
        "gate_results": {"native_test_suite": "NOT_RUN", "rootless_preflight": "NOT_RUN",
                         "current_source_product_build": "NOT_RUN", "product_iso": "NOT_PRODUCED",
                         "hardware": "NOT_ACCESSED"},
    }
    proc = None
    serial_fd = None
    qmp_result = None
    qemu_return = None
    stop_reason = "runner exception before QEMU launch"
    gate = SerialGate(stable_seconds=QUERY_STABLE_SECONDS)
    pty_scanner = PtyAnnouncementScanner()
    pty_paths = []
    event_file = EVENTS.open("x", encoding="utf-8", buffering=1)
    serial_file = SERIAL_OUT.open("xb", buffering=0)
    serial_input_file = SERIAL_IN.open("xb", buffering=0)
    qemu_output_file = QEMU_OUTPUT.open("xb", buffering=0)
    try:
        record(event_file, "launch", qemu_argv=argv, wall_seconds=WALL_SECONDS,
               free_floor_gib=FREE_FLOOR_BYTES / 1024**3, query_timeout_seconds=QUERY_TIMEOUT_SECONDS,
               query_output_cap_bytes=QUERY_OUTPUT_CAP_BYTES, qemu_output_cap_bytes=QEMU_OUTPUT_CAP_BYTES,
               network="none", passthrough=False, qemu_output="stdout+stderr merged",
               guest_input="none until PTY opened and both startup gates pass")
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, close_fds=True, bufsize=0,
                                start_new_session=True)
        os.set_blocking(proc.stdout.fileno(), False)
        record(event_file, "process_started", pid=proc.pid)
        pty_deadline = min(deadline, time.monotonic() + 20)
        while time.monotonic() < pty_deadline and serial_fd is None:
            chunks, found = pump_once(proc, None, serial_file, qemu_output_file,
                                      pty_scanner, gate, event_file, started_mono, 0.2)
            if found:
                pty_paths.extend(found)
                if len(pty_paths) != 1:
                    stop_reason = "unexpected number of QEMU serial PTYs"
                    break
                serial_fd = open_serial_pty(pty_paths[0])
                record(event_file, "serial_pty_opened", path=pty_paths[0])
                break
            if proc.poll() is not None:
                stop_reason = "QEMU exited before serial PTY became available"
                break
            if free_bytes() < FREE_FLOOR_BYTES:
                stop_reason = "120 GiB free-space floor reached before serial connection"
                break
        if serial_fd is None and stop_reason == "runner exception before QEMU launch":
            stop_reason = "serial PTY unavailable within 20 seconds"
        if serial_fd is not None:
            while time.monotonic() < deadline:
                chunks, found = pump_once(proc, serial_fd, serial_file, qemu_output_file,
                                          pty_scanner, gate, event_file, started_mono, 0.2)
                if found:
                    stop_reason = "unexpected additional serial PTY announcement"
                    break
                if gate.unexpected_prompt is not None:
                    stop_reason = "unexpected shell prompt before query gate"
                    break
                if gate.fatal_marker is not None:
                    stop_reason = "emergency or fatal terminal marker before query gate"
                    break
                now = time.monotonic() - started_mono
                if query_sequence_ready(serial_fd is not None, gate.startup_ready(now),
                                       proc.poll() is None):
                    record(event_file, "query_gate_open", elapsed_seconds=round(now, 3),
                           failure_line=FAILURE_LINE, prompt="sh-5.3#", stable_seconds=QUERY_STABLE_SECONDS,
                           serial_offset=gate.offset)
                    stop_reason = "startup gates passed; query sequence incomplete"
                    for index, command in enumerate(READ_ONLY_COMMANDS):
                        query_result = run_query(index, command, serial_fd, serial_file,
                                                 serial_input_file, qemu_output_file, pty_scanner,
                                                 gate, event_file, proc, started_mono, deadline)
                        result["queries"].append(query_result)
                        record(event_file, "read_only_query_result", **query_result)
                        if query_result.get("status") != "RETURNED_TO_FRESH_PROMPT":
                            stop_reason = "query sequence stopped: " + query_result.get("status", "unknown")
                            break
                    else:
                        stop_reason = "all three fixed read-only queries returned to fresh prompts"
                    break
                if proc.poll() is not None:
                    stop_reason = "QEMU exited before startup query gates passed"
                    break
                if free_bytes() < FREE_FLOOR_BYTES:
                    stop_reason = "120 GiB free-space floor reached before query gate"
                    break
                if SERIAL_OUT.stat().st_size > SERIAL_CAP_BYTES:
                    stop_reason = "64 MiB serial capture cap reached"
                    break
            else:
                stop_reason = "600-second hard wall cap reached before query sequence"
        if proc is not None:
            qmp_result, qemu_return = stop_qemu(proc, event_file, stop_reason)
    except Exception as exc:
        result["runner_error"] = repr(exc)
        stop_reason = "runner exception: " + repr(exc)
        if proc is not None:
            try:
                qmp_result, qemu_return = stop_qemu(proc, event_file, stop_reason)
            except Exception as stop_exc:
                result["stop_error"] = repr(stop_exc)
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
                qemu_return = proc.poll()
    finally:
        if serial_fd is not None:
            try:
                os.close(serial_fd)
            except OSError:
                pass
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            qemu_return = proc.returncode
        if proc is not None and proc.stdout is not None:
            try:
                proc.stdout.close()
            except OSError:
                pass
        try:
            after = hash_inputs()
            INPUTS_AFTER.write_text(after, encoding="utf-8")
            result["input_hashes_match"] = INPUTS_BEFORE.read_bytes() == INPUTS_AFTER.read_bytes()
            if not result["input_hashes_match"]:
                stop_reason = "input hash records changed during run"
        except Exception as exc:
            result["input_hashes_after_error"] = repr(exc)
        result["finished_utc"] = utc_now()
        result["elapsed_seconds"] = round(time.monotonic() - started_mono, 3)
        result["stop_reason"] = stop_reason
        result["qemu_exit_code"] = qemu_return
        result["qmp_quit_result"] = qmp_result
        result["serial_bytes"] = SERIAL_OUT.stat().st_size if SERIAL_OUT.exists() else 0
        result["qemu_output_bytes"] = QEMU_OUTPUT.stat().st_size if QEMU_OUTPUT.exists() else 0
        result["free_gib_end"] = round(free_bytes() / 1024**3, 3)
        result["failure_marker_seen"] = gate.failure_seen
        result["prompt_offset"] = gate.prompt_offset
        record(event_file, "finished", stop_reason=stop_reason,
               elapsed_seconds=result["elapsed_seconds"], qemu_exit_code=qemu_return,
               serial_bytes=result["serial_bytes"], qemu_output_bytes=result["qemu_output_bytes"],
               free_gib_end=result["free_gib_end"])
        event_file.close()
        serial_file.close()
        serial_input_file.close()
        qemu_output_file.close()
        RESULT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    success = stop_reason == "all three fixed read-only queries returned to fresh prompts" and qemu_return == 0 and result.get("input_hashes_match") is True
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())