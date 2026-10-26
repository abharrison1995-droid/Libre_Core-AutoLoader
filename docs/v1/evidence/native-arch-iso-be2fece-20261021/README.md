# Native Archiso and QEMU evidence — `be2fece`

This record covers the source revision `be2fece` (`Pin Archiso 91-1 snapshot to September 30`). It is a baseline candidate build; later source changes must be rebuilt and revalidated before release.

## Results

- On a native Arch Linux VM, the full current-source suite passed **1,017 tests**, with **2 skipped**, in 1,167.11 seconds. The JUnit report is `regression.xml`; the run log is `native-arch-gates.log`.
- Windows-targeted mypy passed with no issues in 105 source files. The same gate log records successful CLI help checks for `autoloader` and `build-usb`.
- The pinned Archiso build produced `libre_core_autoloader-2026.09.29-x86_64.iso` (1,017,458,688 bytes), SHA-256 `a98e2c6039b28d5aad4359ebdbbaeaf2aecedc2135f3afdb01768e2c1cc401e5`. The full ISO remains in the authorized ext4 VM workspace and is not checked into Git. Its `SHA256SUMS`, build provenance and build log are retained here.
- The ISO was booted in QEMU 11.1 in both SeaBIOS and UEFI/EDK2 configurations. Both runs reached the live application interface after the Arch first-run prompt. `qemu-tui-screen.png` is the captured interface screen. QEMU had no host disk or SPI programmer passthrough; these are virtual boot checks, not ThinkPad hardware tests.

## Wrapper status and limits

The gate wrapper exited **1** after the test, type-check, CLI and ISO-build steps. Its final `cp -a` to the FAT results volume could not preserve Unix file ownership (`Operation not permitted`). The files had been copied, and the ISO hash was independently verified against the candidate's `SHA256SUMS`. The wrapper therefore did not report a complete green exit; the ownership-copy packaging step remains a harness issue.

The build log also reports that its temporary work tree was retained because safe cleanup could not be proven. The candidate provenance records BIOS/UEFI validation as pending at build time; the separate QEMU observations above were collected afterward.

This evidence does **not** pass a physical probe, device backup, ROM build from device-derived X220 firmware regions, firmware write, SPI read-back, recovery, or boot on a ThinkPad. T430, X200 and X220 hardware acceptance remains pending and requires human authorization for physical writes and hardware-specific recovery.

## Evidence integrity

| File | SHA-256 |
|---|---|
| ISO | `a98e2c6039b28d5aad4359ebdbbaeaf2aecedc2135f3afdb01768e2c1cc401e5` |
| `native-arch-gates.log` | `6480615671f99feedb63bc6ee9b091eff79e2be4f9a6054cfc69ab3dfdc587e0` |
| `regression.xml` | `e24ec7581e964974db4784088e3543ea65c340540ceb29a78c6f6e81189d3738` |
| `qemu-tui-screen.png` | `da27378f31a49f32232874a4ce4272ebd8efac1faf3370a02f016c9d1ddeae5b` |
