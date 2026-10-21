# Arch-boxes virtio VM follow-up — 2026-10-20

## Outcome

**Usable guest/build gate: NOT PASSED.** With the guest disk exposed as
virtio, QEMU did find the configured Btrfs root device and one bounded boot
reached the Arch userspace welcome screen after `Mounted /sysroot` and
`Reached target Switch Root`. A later network-enabled boot remained in the
Arch initrd at `Create Static Device Nodes in /dev gracefully`; the QEMU
process was stopped by local QMP. The welcome screen is evidence of guest
userspace startup only. No verified login, interactive shell, SSH session,
or guest command was obtained. No Linux build, product ISO, ROM, or hardware
operation was performed.

## Runs and isolation

`argv.json` records the read-only-console run and the loopback-forwarded SSH
diagnostics. Runs used QEMU 10.2.1, TCG, one virtual CPU, 4 GiB RAM, a fresh
QCOW2 overlay backed by the signed Arch-boxes base image, and the virtio block
interface. The base image was never attached writable; its SHA-256 remains
`bb888a084568f4e68b1260ffa614ebf806c79668fa20f946c691973538f4d33c`. No
physical-device passthrough was configured. The SSH diagnostic used QEMU
user-mode NAT with host forwarding bound only to `127.0.0.1:32223`.

The first welcome capture shows the correct root UUID found, `/sysroot`
mounted, and `Welcome to Arch Linux!`. A read-only disk inspection confirmed
the UUID in the GRUB command line matches the Btrfs root partition. It did
not identify a complete root cause for the prior legacy-IDE boot failure.
The later SSH diagnostic did not return a protocol banner or authenticated
session. One login-key attempt was sent while the guest screen still showed
early initrd device initialization; no successful login or shell followed.
No guest shell command was sent.

`latest-early-initrd.png` is the final screen capture from the last bounded
networked run. It shows systemd still waiting during initrd device-node
creation; it is not a successful guest boot capture. The earlier
`root-userspace.png` and `network-root-userspace.png` show the bounded
welcome-screen observations. Raw PPM screenshots and the empty serial/QEMU
logs are also retained. The final overlay passed `qemu-img check`; this
validates the QCOW2 container only, not guest health. Hashes are recorded in
`hashes.txt` and `SHA256SUMS`.

The Arch-boxes project describes its basic image as a local-use image with
the `arch` account and SSH enabled ([upstream project documentation](https://github.com/archlinux/arch-boxes#images)).
This evidence does not establish that this VM's SSH service became usable.
