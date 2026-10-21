#!/usr/bin/env python3
import datetime
import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

root = Path("/mnt/wsl/PHYSICALDRIVE1p2/opt/librecore-autoloader-v1-vm/native-arch-build-20261021")
attempt = root / "attempt-current-retry14-post-failure-observation"
serial = attempt / "serial-output.log"
stderr = attempt / "qemu-stderr.log"
events = attempt / "events.jsonl"
result_path = attempt / "run-result.json"
qmp = Path("/tmp/lcarch-v1-retry14-observation.qmp")
iso = Path("/mnt/wsl/PHYSICALDRIVE1p2/opt/librecore-autoloader-v1-vm/iso-boot/archlinux-2026.10.01-x86_64.iso")
kernel = root / "attempt-current-retry13-direct-kernel/vmlinuz-linux"
initramfs = root / "attempt-current-retry13-direct-kernel/initramfs-linux.img"
workspace_overlay = attempt / "arch-workspace-overlay.qcow2"
results_overlay = attempt / "results-overlay.qcow2"
source = root / "source-8ea32c5.img"
qemu = "/usr/bin/qemu-system-x86_64"
cap_seconds = 600
free_floor_bytes = 120 * 1024**3
serial_cap_bytes = 64 * 1024**2
success_re = re.compile(r"Reached target (Multi-User System|Graphical Interface)", re.I)
fatal_re = re.compile(r"Reached target Emergency Mode|You are in emergency mode|Kernel panic - not syncing|Reached target Power-Off|Reached target Reboot", re.I)

def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def free_bytes():
    st = os.statvfs(attempt)
    return st.f_bavail * st.f_frsize

def record(kind, **fields):
    row = {"utc": utc_now(), "kind": kind, **fields}
    with events.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    if kind == "progress":
        print("elapsed={elapsed_seconds:.0f}s free_gib={free_gib:.1f} serial_bytes={serial_bytes} qemu_alive={qemu_alive}".format(**fields), flush=True)

def send_qmp_quit():
    deadline = time.monotonic() + 15
    last_error = None
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_UNIX) as sock:
                sock.settimeout(5)
                sock.connect(str(qmp))
                stream = sock.makefile("rwb", buffering=0)
                greeting = json.loads(stream.readline())
                if "QMP" not in greeting:
                    raise RuntimeError("QMP greeting missing")
                stream.write(b'{"execute":"qmp_capabilities"}\r\n')
                while True:
                    msg = json.loads(stream.readline())
                    if "error" in msg:
                        raise RuntimeError("QMP capabilities rejected")
                    if "return" in msg:
                        break
                stream.write(b'{"execute":"quit"}\r\n')
                while True:
                    msg = json.loads(stream.readline())
                    if "error" in msg:
                        raise RuntimeError("QMP quit rejected")
                    if "return" in msg:
                        return {"reply": msg}
        except Exception as exc:
            last_error = repr(exc)
            time.sleep(0.5)
    raise RuntimeError("QMP quit failed: " + str(last_error))

if not attempt.is_dir():
    raise SystemExit("attempt directory is missing")
for output in (serial, stderr, events, result_path):
    if output.exists():
        raise SystemExit("refusing to overwrite existing output: " + str(output))
if qmp.exists():
    raise SystemExit("refusing to reuse existing QMP socket path")
for required in (iso, kernel, initramfs, workspace_overlay, results_overlay, source):
    if not required.is_file():
        raise SystemExit("required input missing: " + str(required))
if free_bytes() < free_floor_bytes:
    raise SystemExit("host free-space floor failed before launch")
if shutil.which(qemu) is None:
    raise SystemExit("QEMU binary is unavailable")

args = [
    qemu, "-machine", "pc", "-accel", "tcg,thread=multi", "-cpu", "max",
    "-smp", "4", "-m", "6144", "-nodefaults",
    "-kernel", str(kernel), "-initrd", str(initramfs),
    "-append", "archisobasedir=arch archisosearchuuid=2026-10-01-14-49-03-00 console=ttyS0,115200n8 systemd.getty_auto=0 systemd.debug_shell systemd.default_debug_tty=ttyS0",
    "-drive", "file={0},if=ide,media=cdrom,readonly=on,format=raw".format(iso),
    "-drive", "file={0},if=virtio,format=qcow2".format(workspace_overlay),
    "-drive", "file={0},if=virtio,format=qcow2".format(results_overlay),
    "-drive", "file={0},if=virtio,format=raw,readonly=on".format(source),
    "-nic", "none", "-display", "none", "-serial", "file:{0}".format(serial),
    "-monitor", "none", "-qmp", "unix:{0},server=on,wait=off".format(qmp),
    "-no-reboot",
]
started = utc_now()
start = time.monotonic()
record("launch", started_utc=started, cap_seconds=cap_seconds,
       free_floor_gib=free_floor_bytes / 1024**3, guest_input="none",
       network="none", passthrough=False, qemu_argv=args)
stop_reason = None
terminal_match = None
last_progress = 0
position = 0
tail = ""
qemu_return = None
qmp_result = None
with stderr.open("wb") as err:
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err, close_fds=True)
    record("process_started", pid=proc.pid)
    while True:
        elapsed = time.monotonic() - start
        try:
            with serial.open("rb") as log:
                log.seek(position)
                chunk = log.read()
                position = log.tell()
            if chunk:
                tail = (tail + chunk.decode("utf-8", errors="replace"))[-16384:]
                match = success_re.search(tail)
                if match:
                    stop_reason = "systemd reached normal multi-user or graphical target"
                    terminal_match = match.group(0)
                else:
                    match = fatal_re.search(tail)
                    if match:
                        stop_reason = "systemd reached an emergency/power terminal target or kernel panic"
                        terminal_match = match.group(0)
        except FileNotFoundError:
            pass
        if stop_reason:
            break
        qemu_return = proc.poll()
        if qemu_return is not None:
            stop_reason = "QEMU exited without a detected terminal systemd outcome"
            break
        if free_bytes() < free_floor_bytes:
            stop_reason = "host free-space safety floor reached"
            break
        serial_size = serial.stat().st_size if serial.exists() else 0
        if serial_size > serial_cap_bytes:
            stop_reason = "serial-output safety cap reached"
            break
        if elapsed >= cap_seconds:
            stop_reason = "600-second observation cap"
            break
        if elapsed - last_progress >= 30:
            record("progress", elapsed_seconds=elapsed,
                   free_gib=free_bytes() / 1024**3, serial_bytes=serial_size,
                   qemu_alive=True)
            last_progress = elapsed
        time.sleep(1)
    if proc.poll() is None:
        try:
            qmp_result = send_qmp_quit()
            record("qmp_quit", stop_reason=stop_reason, qmp_result=qmp_result)
        except Exception as exc:
            record("qmp_quit_error", error=repr(exc), stop_reason=stop_reason)
            proc.terminate()
        try:
            qemu_return = proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            qemu_return = proc.wait(timeout=10)
            record("forced_process_kill", qemu_return=qemu_return)
    else:
        qemu_return = proc.returncode
    err.flush()
elapsed = time.monotonic() - start
final_serial_size = serial.stat().st_size if serial.exists() else 0
record("finished", elapsed_seconds=elapsed, qemu_return=qemu_return,
       stop_reason=stop_reason, terminal_match=terminal_match,
       serial_bytes=final_serial_size, free_gib=free_bytes() / 1024**3)
result = {
    "attempt": "current-source-native-arch-retry14-post-failure-observation",
    "classification": "generic Arch ISO observation-only diagnostic; not a product build or hardware test",
    "started_utc": started,
    "finished_utc": utc_now(),
    "elapsed_seconds": round(elapsed, 3),
    "stop_reason": stop_reason,
    "terminal_match": terminal_match,
    "qemu": {"version": subprocess.check_output([qemu, "--version"], text=True).splitlines()[0],
             "accelerator": "TCG multi-thread", "vcpus": 4, "memory_mib": 6144,
             "wall_cap_seconds": cap_seconds, "exit_code": qemu_return},
    "guest_input": "none; stdin was DEVNULL and serial was output-only file backend",
    "network": "none",
    "passthrough": False,
    "iso": {"path": str(iso), "attachment": "read-only CD-ROM",
            "sha256_expected": "684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5"},
    "kernel": {"path": str(kernel), "sha256_expected": "8ebc2c71271e000540f0a7545afd8e73504fcb724671f3d38c720b241842b901"},
    "initramfs": {"path": str(initramfs), "sha256_expected": "35b4362207fcabc66535444aea0b97f3508128b32e97e66aa5f56487007bfe52"},
    "virtual_devices": {"workspace": str(workspace_overlay), "backing": str(root / "arch-v1-build.qcow2"),
                        "results": str(results_overlay), "backing_results": str(root / "results-8ea32c5.img"),
                        "source": str(source), "source_attachment": "read-only"},
    "kernel_arguments": "archisobasedir=arch archisosearchuuid=2026-10-01-14-49-03-00 console=ttyS0,115200n8 systemd.getty_auto=0 systemd.debug_shell systemd.default_debug_tty=ttyS0",
    "gate_results": {"native_test_suite": "NOT_RUN", "rootless_preflight": "NOT_RUN",
                     "current_source_product_build": "NOT_RUN", "product_iso": "NOT_PRODUCED",
                     "hardware": "NOT_ACCESSED"},
}
result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
if qmp.exists():
    qmp.unlink()
print(json.dumps({"stop_reason": stop_reason, "terminal_match": terminal_match,
                  "elapsed_seconds": round(elapsed, 3), "qemu_exit": qemu_return,
                  "serial_bytes": final_serial_size}, sort_keys=True), flush=True)

