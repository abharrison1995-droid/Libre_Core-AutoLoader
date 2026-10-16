# Current WSL QEMU inspection — 2026-10-16

At about 20:22 UTC, the currently visible QEMU instance was inspected through its local VNC display. The first capture showed PXE fallback and `No bootable device.` The QEMU monitor capture then showed QEMU 10.2.1, `VM status: running`, and `info block` entries only for an empty `ide1-cd0` and an empty `floppy0`. No disk or bootable ISO was attached.

The monitor was opened with the VNC Ctrl+Alt+2 display shortcut. Only the read-only monitor commands `info status` and `info block` were entered. No guest command, disk attachment, VM resume/pause command, firmware operation, or hardware interaction was performed. The screenshots are [`qemu-no-bootable-device.bmp`](qemu-no-bootable-device.bmp) and [`qemu-info-status-block.bmp`](qemu-info-status-block.bmp).

This running QEMU instance is not a usable Linux build guest. In particular, it is distinct from the earlier QEMU 11.1.2 Archiso candidate-build attempt recorded in the software regression evidence. It establishes no current-source build, ISO, boot, or hardware result. The 10 GiB figure elsewhere is a conservative planning reserve only; the checked-in evidence does not establish a storage minimum or peak for a successful build.
