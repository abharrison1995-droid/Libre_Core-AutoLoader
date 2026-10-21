# Retry14 generic Arch startup observation — 2026-10-21

This is a bounded, observation-only diagnostic using the generic Arch Linux
2026.10.01 ISO. It followed retry13's later serial observation of a Namespace
Resource Manager failure. It is not a product ISO, native build, or hardware
validation result.

## Controls

- QEMU 10.2.1 under TCG multi-thread, 4 vCPUs, 6144 MiB, with a 600-second
  observation cap. QMP sent `quit` at the cap; QEMU exited 0 after 600.376 s.
- The ISO was attached as a read-only CD-ROM. The extracted kernel and initramfs
  were the same retry13 inputs. A read-only source image and fresh workspace and
  results overlays were attached.
- No network, USB/programmer passthrough, physical disk, or guest input was used.
  QEMU stdin was `/dev/null`; the serial backend only captured output.
- Free space stayed stable at 153.786 GiB. Raw serial capture is 10,040 bytes.

## Observation

The live stop-marker scanner did not remove ANSI escape sequences. Its
`terminal_match: null` is therefore not evidence that terminal markers were
absent. A separate post-run audit stripped ANSI sequences from the retained
transcript and counted four `Failed to start Namespace Resource Manager`
markers, zero normal startup target markers, and zero emergency/fatal terminal
markers. The recorded result does not establish the cause of the failures.

The before and after input-hash records match exactly for all four inputs:

| Input | SHA-256 |
| --- | --- |
| Arch ISO | `684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5` |
| Kernel | `8ebc2c71271e000540f0a7545afd8e73504fcb724671f3d38c720b241842b901` |
| Initramfs | `35b4362207fcabc66535444aea0b97f3508128b32e97e66aa5f56487007bfe52` |
| Read-only source image | `0751078e2a362361212ddde66377766cf056d3f18bd4af923959f2ad628b194b` |

No native tests, rootless preflight, current-source build, or product ISO ran.
No physical device or hardware was accessed. The capture does not pass native
Linux, product boot, or hardware gates.

## Files

The launch script, observer, protocol, event log, raw and normalized serial,
post-run audit, result, input hash records, and QEMU stderr capture are retained
in this directory. `SHA256SUMS` verifies this bundle; the parent evidence index
also lists each file and the bundle manifest.