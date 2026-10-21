"""Bounded, diskless QMP/VGA probe for the verified generic Arch ISO.

This diagnostic attaches only the read-only ISO. It has no guest disk, source
share, network adapter, USB device, or physical-device passthrough. Keyboard
input is interactive and must follow a captured frame showing a usable prompt.
"""

from __future__ import annotations

import hashlib
import json
import re
import select
import socket
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path("/mnt/wsl/PHYSICALDRIVE1p2/opt/librecore-autoloader-v1-vm")
ISO = ROOT / "iso-boot/archlinux-2026.10.01-x86_64.iso"
OUT = Path("/mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader/docs/v1/evidence/vm-bringup-20261020")
QMP_PATH = Path("/tmp/librecore-vga-qmp-20261020.sock")
QEMU_LOG = ROOT / "vm-final-20261020/qmp-vga-probe-qemu.log"
SERIAL_LOG = ROOT / "vm-final-20261020/qmp-vga-probe-serial.log"
RESULT = OUT / "archiso-qmp-vga-probe-20261020-result.json"
ARGV = OUT / "archiso-qmp-vga-probe-20261020-argv.json"
MAX_RUNTIME_SECONDS = 300
BOOT_SCREEN_SECONDS = 150


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def qmp_reader(sock: socket.socket) -> tuple[object, object]:
    stream = sock.makefile("rwb", buffering=0)
    greeting = json.loads(stream.readline())
    stream.write(b'{"execute":"qmp_capabilities"}\r\n')
    while True:
        response = json.loads(stream.readline())
        if "return" in response:
            return stream, greeting
        if "error" in response:
            raise RuntimeError(f"QMP capability negotiation failed: {response}")


def qmp_call(stream: object, execute: str, arguments: dict[str, object] | None = None) -> object:
    request: dict[str, object] = {"execute": execute}
    if arguments is not None:
        request["arguments"] = arguments
    stream.write((json.dumps(request) + "\r\n").encode("utf-8"))
    while True:
        response = json.loads(stream.readline())
        if "return" in response:
            return response["return"]
        if "error" in response:
            raise RuntimeError(f"QMP {execute} failed: {response}")


def hmp(stream: object, command: str) -> object:
    return qmp_call(stream, "human-monitor-command", {"command-line": command})


def main() -> int:
    for path in (ISO,):
        if not path.is_file():
            raise SystemExit(f"required verified ISO is missing: {path}")
    if QMP_PATH.exists():
        raise SystemExit(f"refusing to reuse a pre-existing QMP path: {QMP_PATH}")
    for path in (RESULT, ARGV):
        if path.exists():
            raise SystemExit(f"refusing to overwrite evidence: {path}")

    argv = [
        "qemu-system-x86_64",
        "-no-user-config",
        "-machine", "pc,accel=tcg",
        "-cpu", "max",
        "-smp", "1",
        "-m", "4096",
        "-drive", f"file={ISO},media=cdrom,readonly=on,if=ide",
        "-boot", "d",
        "-nic", "none",
        "-display", "none",
        "-monitor", "none",
        "-serial", f"file:{SERIAL_LOG}",
        "-qmp", f"unix:{QMP_PATH},server=on,wait=off",
        "-no-reboot",
        "-no-shutdown",
        "-D", str(QEMU_LOG),
        "-d", "guest_errors",
    ]
    OUT.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    source_hash_before = sha256(ISO)
    ARGV.write_text(
        json.dumps(
            {
                "argv": argv,
                "network": "none",
                "attached_read_only_iso": str(ISO),
                "writable_guest_disks": [],
                "source_shares": [],
                "usb_devices": [],
                "physical_passthrough": False,
                "source_sha256_before": source_hash_before,
                "max_runtime_seconds": MAX_RUNTIME_SECONDS,
                "boot_frame_delay_seconds": BOOT_SCREEN_SECONDS,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    qemu_log_handle = QEMU_LOG.open("wb")
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=qemu_log_handle, stderr=subprocess.STDOUT, start_new_session=True)
    result: dict[str, object] = {
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_sha256_before": source_hash_before,
        "qemu_pid": process.pid,
        "qmp_greeting": None,
        "initial_screen": None,
        "keyboard_actions": [],
        "guest_command_sent": False,
        "guest_command_result_observed": False,
        "termination": None,
    }
    sock: socket.socket | None = None
    try:
        deadline = started + 30
        while not QMP_PATH.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        if process.poll() is not None:
            raise RuntimeError(f"QEMU exited before QMP became available: {process.returncode}")
        if not QMP_PATH.exists():
            raise TimeoutError("QMP socket did not appear within 30 seconds")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(str(QMP_PATH))
        stream, greeting = qmp_reader(sock)
        result["qmp_greeting"] = greeting

        while time.monotonic() < started + BOOT_SCREEN_SECONDS:
            if process.poll() is not None:
                raise RuntimeError(f"QEMU exited during boot: {process.returncode}")
            time.sleep(1)

        before = OUT / "archiso-qmp-vga-probe-20261020-before.ppm"
        hmp(stream, f"screendump {before}")
        result["initial_screen"] = str(before)
        result["qemu_status_at_initial_screen"] = qmp_call(stream, "query-status")
        result["qemu_seconds_at_initial_screen"] = round(time.monotonic() - started, 3)
        RESULT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(f"FRAME_READY {before}", flush=True)
        print("Only after inspecting this frame, type :text <harmless command> or :quit.", flush=True)

        while time.monotonic() < started + MAX_RUNTIME_SECONDS and process.poll() is None:
            remaining = max(0.0, started + MAX_RUNTIME_SECONDS - time.monotonic())
            readable, _, _ = select.select([sys.stdin], [], [], min(1.0, remaining))
            if not readable:
                continue
            line = sys.stdin.readline()
            if not line:
                continue
            line = line.rstrip("\r\n")
            if line == ":quit":
                result["termination"] = "operator_requested_qmp_quit"
                break
            if line.startswith(":key "):
                key = line[5:].strip()
                if not re.fullmatch(r"[a-z0-9-]+", key):
                    print("REJECTED invalid QEMU key name", flush=True)
                    continue
                hmp(stream, f"sendkey {key} 120")
                result["keyboard_actions"].append({"key": key, "elapsed_seconds": round(time.monotonic() - started, 3)})
                print(f"KEY_SENT {key}", flush=True)
                continue
            if line.startswith(":text "):
                value = line[6:]
                if not re.fullmatch(r"[a-z0-9 ./_-]+", value):
                    print("REJECTED text outside harmless lowercase console alphabet", flush=True)
                    continue
                mapping = {" ": "spc", ".": "dot", "/": "slash", "_": "shift-minus"}
                for char in value:
                    key = mapping.get(char, char)
                    hmp(stream, f"sendkey {key} 100")
                    time.sleep(0.08)
                hmp(stream, "sendkey ret 120")
                result["keyboard_actions"].append({"text": value, "elapsed_seconds": round(time.monotonic() - started, 3)})
                result["guest_command_sent"] = value == "id"
                print(f"TEXT_SENT {value}", flush=True)
                continue
            if line.startswith(":shot "):
                tag = line[6:].strip()
                if not re.fullmatch(r"[a-z0-9-]+", tag):
                    print("REJECTED invalid screenshot tag", flush=True)
                    continue
                screenshot = OUT / f"archiso-qmp-vga-probe-20261020-{tag}.ppm"
                hmp(stream, f"screendump {screenshot}")
                result["screenshots"] = [*result.get("screenshots", []), str(screenshot)]
                result["last_screen_elapsed_seconds"] = round(time.monotonic() - started, 3)
                RESULT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
                print(f"FRAME_READY {screenshot}", flush=True)
                continue
            print("Use :key <qemu-key>, :text <harmless lowercase command>, :shot <tag>, or :quit.", flush=True)

        if process.poll() is None:
            try:
                hmp(stream, "quit")
                result["termination"] = result["termination"] or "qmp_quit"
            except Exception as exc:
                result["qmp_quit_error"] = repr(exc)
                process.terminate()
                result["termination"] = "sigterm_after_qmp_failure"
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            result["termination"] = "sigkill_after_10_second_shutdown_timeout"
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if sock is not None:
            sock.close()
        qemu_log_handle.close()
        try:
            QMP_PATH.unlink()
        except FileNotFoundError:
            pass
        source_hash_after = sha256(ISO)
        result["source_sha256_after"] = source_hash_after
        result["source_hash_unchanged"] = source_hash_after == source_hash_before
        result["qemu_returncode"] = process.returncode
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        RESULT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(f"RESULT {RESULT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
