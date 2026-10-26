# Native Windows WHPX Arch live-environment direct-kernel diagnostic — 2026-10-25

## Result

**The hash-verified upstream Arch 2026.10.01 live environment reached a serial
root shell under native Windows QEMU WHPX after QEMU loaded the ISO's kernel
and initramfs directly. The ISO was attached read-only as a CD-ROM. This did
not exercise the ISO's BIOS/UEFI bootloader path. Read-only guest commands ran
and their output was captured. This is an environment diagnostic only; it does
not validate the LibreCore worker, product ISO, firmware build, USB boot, or
any hardware workflow.**

The corrected repeat reached the serial login prompt and `root@archiso` shell,
then ran `id`, `cat /proc/cmdline`, `systemctl is-system-running`, and
`systemctl --failed --no-pager`. The captured marker confirms the command
sequence completed. `id` reported `uid=0(root) gid=0(root)`, and
`/proc/cmdline` contained the ISO's exact BIOS UUID options plus
`console=ttyS0,115200n8`. `systemctl is-system-running` reported `starting`;
the failed-unit listing contained `vmtoolsd.service`. The VM was stopped after
the observations; QEMU exited 0.

The run used QEMU 11.1.2, `pc` with explicit `accel=whpx`, `qemu64`, 2 GiB RAM,
two vCPUs, direct `-kernel`/`-initrd` loading of the ISO's exact kernel and
initramfs, and
`archisobasedir=arch archisosearchuuid=2026-10-01-14-49-03-00`. The ISO was
attached as a read-only CD-ROM. `-nodefaults` and `-nic none` were set; no
guest disk, guest network adapter, USB device, writable host share, physical
device, or firmware device was attached. HMP and the guest serial console used
host-loopback-only sockets. The QEMU invocation had a 600-second hard limit;
the successful second attempt completed in about 71 seconds.

Input hashes were checked before and after the run. The official ISO remained
`684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5`; the
kernel was `8ebc2c71271e000540f0a7545afd8e73504fcb724671f3d38c720b241842b901`,
and the initramfs was
`35b4362207fcabc66535444aea0b97f3508128b32e97e66aa5f56487007bfe52`.

## Retained records

The corrected successful repeat is in [`attempt-2`](attempt-2/):

- [`run-record.txt`](attempt-2/run-record.txt) — command, isolation, hashes,
  timing, and result.
- [`serial.log`](attempt-2/serial.log) — login prompt, shell, and guest command
  output.
- [`monitor.log`](attempt-2/monitor.log) — HMP status and screen-capture
  command.
- [`qemu.stderr.log`](attempt-2/qemu.stderr.log) and
  [`qemu.stdout.log`](attempt-2/qemu.stdout.log) — emulator diagnostics.
- [`qemu.exit`](attempt-2/qemu.exit) and
  [`result.json`](attempt-2/result.json) — process exit and bounded result.
- [`display.bmp`](attempt-2/display.bmp) — early VGA capture.

The reproducible PowerShell harness is [`run-whpx-diagnostic.ps1`](run-whpx-diagnostic.ps1).
It requires the verified ISO and extracted kernel/initramfs at the paths
recorded in the script, verifies their hashes, allows WHPX without a TCG
fallback, and caps the process at 600 seconds.

## Initial harness attempt and correction

The first bounded attempt also reached the Arch serial login and root shell,
but a prompt parser failed to strip terminal cursor/control sequences, so it
did not run the requested guest commands and timed out. Its raw records are in
[`attempt-1`](attempt-1/). Its generated `result.json` incorrectly labels the
run as an unavailable WHPX accelerator because the initial classifier matched
the benign stderr warning “Failed to get performance monitoring features.”
The same stderr file also records the guest's unsupported SVM feature warning;
the serial transcript proves the guest booted. The first result classification
is retained unchanged and is superseded by the corrected harness and successful
read-only command capture in `attempt-2`.

## Scope and release gates

This diagnostic proves that this host can run the upstream Arch live
environment to a serial root shell with native WHPX and harmless read-only
commands, with the ISO attached read-only. It does not exercise the ISO's
BIOS/UEFI bootloader or test a pinned LibreCore VM worker, effective worker isolation or
`NoNewPrivileges`, locked guest packages, `pacstrap -N`, `mkarchiso`, candidate
provenance, the current-source product ISO, BIOS/UEFI product boot, or any
ThinkPad firmware operation. `PRODUCTION_WORKER_DEPLOYMENT` remains unset and
the production build path remains unavailable. T430, X200, and X220 hardware
acceptance, including all physical writes and recovery actions, remains
pending explicit authorization.

`SHA256SUMS` covers this README, the harness, and all records retained under
both attempts.
