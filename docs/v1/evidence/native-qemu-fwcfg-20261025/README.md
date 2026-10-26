# Native Windows QEMU fw_cfg transport diagnostic — 2026-10-25

## Result

**The single bounded run passed its fw_cfg transport check.** Native Windows
QEMU 11.1.2 with explicit WHPX booted the hash-verified Arch 2026.10.01 kernel
and initramfs directly. A serial root shell read the fixed fw_cfg sysfs entry;
it contained exactly 64 bytes matching the supplied lowercase hexadecimal
fixture, and the guest emitted the recorded `__LIBRECORE_FWCFG_PASS` marker.
QEMU exited 0 after the HMP `quit` command in about 75.5 seconds, within the
240-second cap.

This is a **QEMU fw_cfg transport diagnostic only**. It does not demonstrate a
LibreCore worker image, production worker deployment, product build, full ISO
boot path, firmware build/write/recovery, or hardware acceptance. The guest
used the upstream Arch live environment, not LibreCore's worker.

## Run setup and bounds

The run used the official Arch ISO at the path and SHA-256 recorded in
[`run-record.txt`](run-record.txt). Its kernel and initramfs were extracted
with the built-in Windows `tar.exe` into a new temporary directory, then their
hashes were checked against the recorded pins. QEMU ran with `pc`, `qemu64`,
2 GiB RAM, two vCPUs, and `-machine pc,accel=whpx`; no accelerator fallback was
configured. The ISO was the only guest media and was attached as a read-only
CD-ROM. The command used `-nic none`; it attached no writable disk, host share,
USB device, physical device, or physical firmware device passthrough. Serial and HMP sockets
listened only on `127.0.0.1`.

The guest check only read
`/sys/firmware/qemu_fw_cfg/by_name/opt/org.librecore/worker-image-sha256/raw`,
checked its byte count and exact fixture value, and printed a result marker.
It made no guest filesystem changes. ISO, QEMU, kernel, and initramfs hashes
were checked before and after; the recorded before/after values match.

## Retained evidence

- [`run-record.txt`](run-record.txt): timestamps, exact QEMU arguments,
  isolation, input hashes, read-only guest command, and outcome.
- [`qemu.argv.json`](qemu.argv.json): executable and argument vector.
- [`serial.raw.bin`](serial.raw.bin) and [`serial.log`](serial.log): raw and
  decoded guest serial transcript, including the shell and exact pass marker.
- [`monitor.raw.bin`](monitor.raw.bin) and [`monitor.log`](monitor.log): raw
  and decoded HMP transcript, including VM status and shutdown.
- [`qemu.stdout.log`](qemu.stdout.log), [`qemu.stderr.log`](qemu.stderr.log),
  [`qemu.exit`](qemu.exit), and [`result.json`](result.json): process output,
  exit, and bounded result.
- [`run-fwcfg-transport.ps1`](run-fwcfg-transport.ps1): the fixed-path,
  hash-checking, 240-second diagnostic harness.
- [`SHA256SUMS`](SHA256SUMS): hashes for every retained file in this directory
  except the manifest itself.

