# Rootless Archiso preflight in the disposable VM

Date: 2026-10-16

This evidence covers the current Windows working-tree snapshot `9a0b8aa03c1661b37cb4aa7123e79c5bfab0d7f26040735db79462e16a457aa3` (875 files, SHA-256 recorded in `source-snapshot.txt`). The preflight ran against its `autoloader/`, `usb_builder/`, `pyproject.toml`, `README.md`, and `LICENSE` files in the VM. It exercised the current `build_usb._rootless_archiso_host_preflight()` and `_rootless_archiso_mount_preflight()` functions.

## VM and pinned toolchain

- Disposable QEMU `q35` VM, Arch Linux live ISO, booted kernel `6.15.8-arch1-2`.
- Scratch disk `/dev/vda`, ext4 UUID `dbc9e10f-6a7b-472c-93d4-cf5af3c4fbe3`.
- Prepared Arch root on that disk; exact `archiso 91-1` archive came from the project’s dated 2026-09-29 official snapshot URL.
- The package archive SHA-256 is `3d08984593f235fc5de8ec379bdf7860fbcd2e62fa7e36b14dc53ba663289d1c`; detached signature SHA-256 is `779167c909d3dbdb60d91e9ca6f13e4d0300a2303ffe455a8ac3b452baf61e3c`. The target’s Arch keyring reported a good signature from David Runge. Installation used a temporary config with `LocalFileSigLevel = Required`; the prior toolroot cache had `archiso 90-1`, so it was not used for this package.
- Installed host tool versions in the prepared root: `archiso 91-1`, `arch-install-scripts 31-2`, `pacman 7.1.0.r9.g54d9411-2`, `util-linux 2.42.4-1`, `bash 5.3.20-1`, `shadow 4.20.0.arch1-1`, `libcap 2.78-1`, and `iproute2 7.2.0-1`.
- Build identity: `fwbuild`, UID/GID 1000, no supplementary groups, subuid/subgid range `100000:65536`. The recorded capability bounding set is `0xc0` (only setuid/setgid), with effective/permitted/ambient sets empty. All rootless executable hashes, package ownership, modes, and helper file capabilities match `snapshot.lock.json`.

## Result

`current-source-rootless-preflight.txt` records the raw output and parsed report. The host preflight passed. The real mapped `net`, `mount`, and `PID` namespace probe returned `SUPPORTED` with all 12 mount operations succeeding, including the six individual device-node binds. It observed a route-free network namespace, private mount propagation, mapped host-root source ownership as UID/GID 65534, and successful mount cleanup. The child mount table was restored, and the parent preflight captured and rechecked host mount-table and `/dev`/`/sys` source identities.

This validates the pre-build namespace/mount gate in this disposable VM only. It does **not** validate a Live USB build, an ISO boot, a firmware workflow, or a physical write. Those remain separate release gates.