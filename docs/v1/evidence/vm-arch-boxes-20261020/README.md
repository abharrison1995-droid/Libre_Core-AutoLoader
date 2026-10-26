# Arch-boxes installed guest attempt — 2026-10-20

## Outcome

**VM login gate: NOT PASSED.** This bounded QEMU TCG attempt reached the
installed Arch userspace's emergency mode after waiting for the configured
root UUID. The VGA capture shows the emergency prompt and states that the
root account is locked. No usable shell, login, SSH session, or guest command
was obtained. No guest keyboard input was sent. QEMU was stopped through its
local QMP control socket; a host-side shutdown is not guest success.

Consequently this run does not pass a native-Linux build, product Live USB,
BIOS/UEFI application boot, ROM build, or hardware validation gate.

## Isolation and inputs

The run used QEMU 10.2.1 in TCG mode with one virtual CPU and 4 GiB RAM. The
Arch-boxes base QCOW2 image was used as the backing image for a fresh scratch
overlay inside the user-authorized VM workspace on the existing ext4
partition. The base image was not modified; its SHA-256 before/after remains
`bb888a084568f4e68b1260ffa614ebf806c79668fa20f946c691973538f4d33c`. Its
detached signature had previously verified using the locally imported
Arch-boxes key; the key's certification status was unknown, so this evidence
does not claim independent identity authentication.

The guest had QEMU user-mode networking with an SSH forward bound only to
`127.0.0.1:32222`; no SSH protocol banner or session was obtained. No physical
device passthrough was configured. The overlay passed `qemu-img check` with
no errors. This check establishes overlay container integrity only.

The exact launch arguments are in `argv.json`. `result.json` records the
observed state and the explicit absence of a guest command. The VGA evidence
is supplied as PNG and raw PPM. QEMU and serial log files were empty. Hashes
for the retained bundle files are in `SHA256SUMS`; `hashes.txt` records the
base, overlay, and original PPM fingerprints.

## Reproduction boundary

The command manifest records the QEMU invocation used. It includes a
workspace-specific scratch path and must not be treated as a reusable product
VM configuration. The overlay is diagnostic state and is not a clean builder
image. No firmware source was mounted into the guest, and no artifact or ISO
was produced.
