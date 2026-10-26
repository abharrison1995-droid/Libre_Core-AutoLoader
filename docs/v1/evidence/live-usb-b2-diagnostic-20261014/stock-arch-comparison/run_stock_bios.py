#!/usr/bin/env python3
"""Capture a bounded, read-only QEMU BIOS boot of the verified stock Arch ISO."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import struct
import subprocess
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path


ISO = Path("/home/alex/archlinux-2026.10.01-x86_64.iso")
EXPECTED_ISO_SHA256 = "684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5"
OUT = Path(
    "/mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader/docs/v1/evidence/"
    "live-usb-b2-diagnostic-20261014/stock-arch-comparison/bios-20261015"
)
QEMU = "/usr/sbin/qemu-system-x86_64"
HMP_PATH = "/tmp/lc-stock-arch-bios-20261015-hmp.sock"
CAP_SECONDS = 600
FRAME_PERIOD_SECONDS = 30


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def png_chunk(kind: bytes, data: bytes) -> bytes:
    payload = kind + data
    return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)


def ppm_to_png(ppm_path: Path, png_path: Path) -> None:
    data = ppm_path.read_bytes()
    match = re.match(rb"P6\s+(\d+)\s+(\d+)\s+255\s", data)
    if match is None:
        raise ValueError(f"Unexpected QEMU PPM header in {ppm_path}")
    width, height = map(int, match.groups())
    pixels = data[match.end() :]
    row_bytes = width * 3
    if len(pixels) != height * row_bytes:
        raise ValueError(f"Incomplete QEMU PPM frame in {ppm_path}: {len(pixels)} bytes")
    scanlines = b"".join(b"\x00" + pixels[y * row_bytes : (y + 1) * row_bytes] for y in range(height))
    png = bytearray(b"\x89PNG\r\n\x1a\n")
    png.extend(png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
    png.extend(png_chunk(b"IDAT", zlib.compress(scanlines, level=6)))
    png.extend(png_chunk(b"IEND", b""))
    png_path.write_bytes(png)
    ppm_path.unlink()


def read_hmp_until_prompt(stream: socket.socket, timeout: float = 4.0) -> bytes:
    stream.settimeout(0.25)
    end = time.monotonic() + timeout
    received = bytearray()
    while time.monotonic() < end:
        try:
            part = stream.recv(8192)
        except TimeoutError:
            continue
        if not part:
            break
        received.extend(part)
        if b"(qemu)" in received:
            break
    return bytes(received)


def hmp_command(stream: socket.socket, command: str) -> bytes:
    stream.sendall(command.encode("ascii") + b"\n")
    return read_hmp_until_prompt(stream)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    frames = OUT / "frames"
    frames.mkdir(exist_ok=True)

    if not ISO.is_file():
        raise FileNotFoundError(ISO)
    iso_before = sha256(ISO)
    if iso_before != EXPECTED_ISO_SHA256:
        raise RuntimeError(f"ISO checksum mismatch: {iso_before}")

    version = subprocess.run([QEMU, "--version"], check=True, capture_output=True, text=True).stdout
    qemu_log = OUT / "qemu.log"
    serial = OUT / "serial.log"
    monitor = f"unix:{HMP_PATH},server=on,wait=off"
    args = [
        QEMU,
        "-accel",
        "tcg,thread=multi",
        "-m",
        "3072",
        "-smp",
        "4",
        "-cdrom",
        str(ISO),
        "-boot",
        "d",
        "-no-reboot",
        "-no-shutdown",
        "-vga",
        "std",
        "-display",
        "vnc=127.0.0.1:7",
        "-nic",
        "none",
        "-serial",
        f"file:{serial}",
        "-monitor",
        monitor,
    ]

    (OUT / "qemu-version.txt").write_text(version, encoding="utf-8")
    (OUT / "command.json").write_text(json.dumps(args, indent=2) + "\n", encoding="utf-8")
    (OUT / "iso-before.sha256").write_text(f"{iso_before}  {ISO}\n", encoding="utf-8")
    (OUT / "run-scope.txt").write_text(
        f"Stock Arch Linux 2026.10.01 normal BIOS boot comparison; started={utc_now()}; "
        f"cap={CAP_SECONDS}s; no NIC/writer; no guest input; loopback VNC display only.\n",
        encoding="utf-8",
    )
    (OUT / "protocol.txt").write_text(
        "Control comparison only, not Libre_Core product acceptance. QEMU boots the official "
        "stock ISO through its normal BIOS entry. The guest receives no input. HMP is used only "
        "for read-only status and complete VGA screendumps. The host stops QEMU at the 600s cap.\n",
        encoding="utf-8",
    )

    start = time.monotonic()
    wall_start = utc_now()
    proc: subprocess.Popen[bytes] | None = None
    monitor_stream: socket.socket | None = None
    stop_reason = f"600-second cap reached at {CAP_SECONDS}s"
    frames_done: list[dict[str, object]] = []

    try:
        with qemu_log.open("wb") as qlog:
            proc = subprocess.Popen(args, stdout=qlog, stderr=subprocess.STDOUT)
            (OUT / "process.txt").write_text(f"pid={proc.pid}\n", encoding="utf-8")
            socket_deadline = time.monotonic() + 20
            while time.monotonic() < socket_deadline:
                if proc.poll() is not None:
                    raise RuntimeError(f"QEMU exited before HMP connection: {proc.returncode}")
                try:
                    monitor_stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    monitor_stream.connect(HMP_PATH)
                    break
                except OSError:
                    if monitor_stream is not None:
                        monitor_stream.close()
                        monitor_stream = None
                    time.sleep(0.2)
            if monitor_stream is None:
                raise TimeoutError("QEMU HMP socket did not become ready")

            (OUT / "hmp-banner.txt").write_bytes(read_hmp_until_prompt(monitor_stream))
            (OUT / "hmp-status.txt").write_bytes(hmp_command(monitor_stream, "info status"))

            next_capture = 10
            while time.monotonic() - start < CAP_SECONDS:
                elapsed = time.monotonic() - start
                if proc.poll() is not None:
                    stop_reason = f"QEMU exited at {elapsed:.1f}s with code {proc.returncode}"
                    break
                if elapsed >= next_capture:
                    frame = frames / f"vga-t{int(elapsed):04d}s.ppm"
                    response = hmp_command(monitor_stream, f"screendump {frame}")
                    if not frame.is_file() or frame.stat().st_size == 0:
                        raise RuntimeError(f"HMP screendump did not create a frame: {response!r}")
                    png = frame.with_suffix(".png")
                    ppm_to_png(frame, png)
                    frames_done.append(
                        {
                            "elapsed_seconds": round(elapsed, 1),
                            "captured_utc": utc_now(),
                            "file": png.name,
                            "sha256": sha256(png),
                        }
                    )
                    (frames / "index.json").write_text(json.dumps(frames_done, indent=2) + "\n", encoding="utf-8")
                    next_capture += FRAME_PERIOD_SECONDS
                time.sleep(0.25)
    finally:
        if proc is not None and proc.poll() is None:
            if monitor_stream is not None:
                try:
                    hmp_command(monitor_stream, "quit")
                except OSError:
                    pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        if monitor_stream is not None:
            monitor_stream.close()

    elapsed_final = time.monotonic() - start
    iso_after = sha256(ISO)
    (OUT / "iso-after.sha256").write_text(f"{iso_after}  {ISO}\n", encoding="utf-8")
    result = {
        "started_utc": wall_start,
        "ended_utc": utc_now(),
        "elapsed_seconds": round(elapsed_final, 1),
        "qemu_exit_code": proc.returncode if proc is not None else None,
        "stop_reason": stop_reason,
        "iso_sha256_before": iso_before,
        "iso_sha256_after": iso_after,
        "iso_unchanged": iso_before == iso_after,
        "guest_input_sent": False,
        "network_device": "none",
        "firmware_writer": "none",
        "screenshots": frames_done,
    }
    (OUT / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if iso_before != iso_after:
        raise RuntimeError("Verified stock ISO bytes changed during the QEMU run")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
