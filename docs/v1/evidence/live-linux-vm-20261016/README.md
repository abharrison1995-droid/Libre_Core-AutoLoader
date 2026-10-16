# Generic Arch ISO VM diagnostic (2026-10-16)

This is a bounded guest-startup check of the official generic Arch Linux
2026.10.01 installation ISO under Ubuntu WSL2. It is not an AutoLoader Live
USB image, product build, native-Linux qualification, or hardware test.

## Attempt 2: bounded capture

Runner command:

```text
wsl.exe -d Ubuntu -- python3 /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader/docs/v1/evidence/live-linux-vm-20261016/run_arch_vm.py --output-dir /tmp/lc-generic-arch-20261016-run2
```

The runner first hashed `/dev/shm/archlinux-2026.10.01-x86_64.iso` and required
SHA-256 `684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5`.
The hash matched before launch and after shutdown. QEMU was Debian QEMU 10.2.1.
The exact fixed argument vector is in
[`attempt2-bounded-run/argv.json`](attempt2-bounded-run/argv.json); it uses TCG,
2 GiB RAM, two vCPUs, a read-only CD-ROM, `-nic none`, no writable disk, and no
USB devices or passthrough.

The HMP monitor connected. It issued one `screendump` at the 120-second capture
point, then `info status` (`VM status: running`), and sent `quit` at the
180-second wall limit. QEMU exited 0 and was confirmed terminated after 180.12
seconds. The unchanged ISO hash, monitor/screenshot/process checks, and result
are recorded in [`attempt2-bounded-run/run-result.json`](attempt2-bounded-run/run-result.json).

The raw HMP screenshot is `attempt2-bounded-run/vga.ppm`; `vga.png` is a
lossless conversion of that same frame for convenient viewing. The VGA frame
shows the Arch Linux splash, `Loading ... vmlinuz-linux... ok`, and
`Loading ... initramfs-linux.img...`. The guest did not reach a login shell
within the bound. The serial and PTY logs are preserved separately. QEMU exit
0 means the emulator accepted the HMP quit request; it is not evidence that the
guest completed boot.

## Attempt 3: Windows-hosted full-system Arch inspection guest

A separate QEMU 11.1.2 process used native Windows QEMU with TCG on a q35
machine, 2 GiB RAM and two vCPUs. It booted the generic Arch Linux 2025.08.01
installer ISO's kernel and initramfs with the explicit kernel argument
`systemd.unit=emergency.target`. The ISO was attached read-only and the
existing inspection qcow2 was attached with `readonly=on`; a user-mode network
adapter was present. No USB device, programmer, physical machine, firmware
writer, or writable guest disk was attached.

At capture, the HMP monitor reported `VM status: running`. The screen reached
the Arch emergency target and displayed its emergency shell. The captured
frame is [`attempt3-full-system-inspection/emergency-shell.png`](attempt3-full-system-inspection/emergency-shell.png);
the raw PPM is preserved beside it. The frame includes prior interactive
commands and is evidence of that emergency-shell state, not a clean boot log.
The screenshot shows a `curl -fsS http://10.0.2.2/inspect-v4-stage-direct.sh |
bash` attempt that failed to connect, followed by another invocation of that
command. The second response, script contents and actions are not captured, so
this evidence cannot establish whether it ran or what it did. “Read-only” here
describes only the attached ISO and qcow2 storage; the guest could execute
commands, and a user-mode network adapter was enabled. This is not an isolated
or clean read-only diagnostic. The temporary inspection guest was stopped
after capture. This confirms the generic guest reached an emergency shell; it
does not establish a normal multi-user boot, current-source build capability,
an AutoLoader image, product app/service startup, or hardware operation.

## Current WSL QEMU state inspection

A later read-only VNC inspection at about 20:22 UTC showed a QEMU 10.2.1 instance on its monitor console. HMP `info status` reported `VM status: running`; `info block` showed only an empty CD-ROM and floppy device. The preceding guest screen showed PXE fallback and `No bootable device.` No guest commands or block-device changes were made. This is not a usable Linux build guest and is separate from the earlier QEMU 11.1.2 Archiso candidate-build attempt. See the [current QEMU inspection evidence](current-qemu-inspection-20261016/README.md).

Input SHA-256 values: ISO
`4b1af44dbeed97acec0204a95f6393818bb8fd903b423a20b7ea141f80b27e59`, kernel
`1a0c0dae7bdd7176e40518966428f05a01061bd9fd5c98389e115246b335f278`, and
initramfs `e5f90bffa6894fa7bda2d7af1b09e2442a6c0f085caa943ed0729d8e22c524c9`.
The capture and its lossless PNG conversion are hashed in the attempt-local
`SHA256SUMS`.

The earlier host-storage checkpoint found about 2.1 GiB free. PowerShell
measurements later found 5,981,900,800 bytes free (about 5.57 GiB) at 20:12
UTC and 6,120,456,192 bytes (about 5.70 GiB) at 20:17 UTC; the corresponding
commands and outputs are recorded in
[`host-storage-20261016.txt`](host-storage-20261016.txt). The 10 GiB figure
used during planning is a conservative reserve, not a measured minimum or
peak. The checked-in evidence does not establish that 5.70 GiB is sufficient
or insufficient for a complete current-source build and VM run.

The earlier candidate-build attempt grew its qcow2 overlay by about 382 MB in
91 seconds while blocked-task warnings were present. It was manually paused
with 3,243,921,408 bytes free, before the previously stated 2 GiB host-space
reserve. That incomplete attempt produced no ISO and no peak-footprint
measurement. Any continuation requires an accessible VM control session and
a monitored stop threshold; this evidence does not authorize an unbounded
resume.

## Attempt 1: rejected runner result

The initial attempt also ran to the same 180-second bound and left the ISO
unchanged. Its first runner version could mark a capture complete based on the
ISO hash alone, without requiring launch, monitor, screenshot, and termination
evidence. It is preserved in `attempt1-initial-run/`, with the original result
JSON alongside a review-reclassified result. Treat attempt 1 as rejected and
use attempt 2 for the bounded diagnostic result.

## Scope

No AutoLoader ISO was built or booted, no product app or service was tested,
and no hardware, firmware, removable media, writer, or recovery operation was
used. This generic guest run does not close any product, native-Linux, ISO, or
hardware acceptance gate.

`SHA256SUMS` covers this README, runner, and all attempt evidence, including
the per-attempt checksum files. Each attempt folder also has its own
`SHA256SUMS`.
