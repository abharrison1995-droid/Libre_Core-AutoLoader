# Native Windows QEMU Arch ISO diagnostics — 2026-10-21

**Result: INCOMPLETE. No guest shell, verified guest command, native Linux build, product ISO, Live USB application startup, firmware ROM, or hardware validation.**

Native Windows QEMU 11.1.2 was installed from the MSYS2 UCRT64 package and reported both WHPX and TCG accelerators. Its executable SHA-256 is recorded in run.json. The input was the previously verified Arch 2026.10.01 ISO from the approved ext4 VM workspace. After all five runs, SHA-256 checks of the ext4 source and local copy matched the earlier preserved value 684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5. There was no per-attempt pre-run hash capture.

Five bounded BIOS boots used QEMU's read-only CD-ROM ISO attachment, 4 GiB RAM, two vCPUs, no guest network, no writable disk, no USB device, no source share, and no physical-device passthrough. QMP queried emulator status and captured VGA screenshots. The host requested QMP quit after each observation.

## WHPX

The full BIOS ISO reached the Syslinux menu and loaded the Linux kernel and initramfs. At the 50-second observation, the VGA capture showed a Linux kernel panic: No working init found. No shell appeared and no guest command was run. QEMU's query-status returned running; this describes the emulator process, not a successful guest boot.

A second WHPX run edited the Syslinux boot line through QMP to append init=/bin/sh, then waited 20 seconds. The screen showed devtmpfs: unable to create devtmpfs -1 and a TDX-not-supported message. No shell appeared. No guest command was sent. This confirms that the attempted initramfs shell override did not make the guest usable; its serial log and PPM/PNG capture are included.

A third WHPX run used the legacy PC machine and qemu64 CPU with the ISO read through the WSL UNC path. At 50 seconds, the screen still showed the Arch boot splash and the serial capture ended while loading the initramfs. No shell appeared.

A fourth WHPX run used the same legacy PC/qemu64 profile with a local copy of the ISO. The local copy SHA-256 matched the preserved source and is recorded in local-iso-copy.sha256. At 90 seconds the screen still showed the Arch boot splash. No shell or guest command was observed. Both bounded runs were stopped by host-requested QMP quit.

## TCG

The full BIOS ISO reached Probing EDD (edd=off to disable)... ok, then showed virtio/vtdx: TDX not supported by the host platform in the 90-second capture. No shell or verified command appeared within the observation window. QEMU's query-status returned running; this does not establish a boot.

The PPM files are the raw QMP screendump captures. PNG files are convenience conversions generated with the included standard-library helper. Serial logs, captures, and the run record are covered by SHA256SUMS.

This diagnostic does not pass the native Linux guest, pinned build, current-source product ISO, Live USB BIOS/UEFI startup, or any of the three hardware workflows. Physical hardware remained disconnected and deferred.
