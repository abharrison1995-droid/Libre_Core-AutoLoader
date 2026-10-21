# Arch ISO serial debug shell attempt - 2026-10-21

**Result: INCOMPLETE. No shell, guest command, package build, product ISO, or hardware validation.**

QEMU 10.2.1 TCG ran the previously signature- and hash-verified Arch 2026.10.01 ISO as a read-only IDE CD-ROM with four vCPUs and 4 GiB RAM. The VM had no writable disk, guest network adapter, USB device, source share, or physical-device passthrough.

The Syslinux boot editor was used to add systemd.debug_shell=1, systemd.default_debug_tty=ttyS0, and console=ttyS0,115200n8. Captures preserve the menu and edited arguments. After boot was requested, the serial capture only recorded the BIOS loading the kernel and initramfs. At 30 and 60 seconds the VGA screen remained at SeaBIOS EDD probing. No shell prompt or guest command output was observed. No guest command was sent.

The VM was stopped through QMP with a host-requested quit. QEMU is no longer running. The ISO was attached read-only; no firmware or physical hardware was present.

This remains a bounded boot diagnostic and does not pass native Linux build, product ISO, Live USB startup, or any representative hardware workflow. The three hardware validations remain pending.
