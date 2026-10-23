# Offline VM environment diagnostic — 2026-10-23

## Result

**INCONCLUSIVE; no guest boot or worker gate passed.** The host can start QEMU
10.2.1 in TCG mode without a guest network interface. One bounded Arch ISO
attempt displayed `Welcome to Arch Linux!`; a later 210-second console-check
attempt with the same CPU flags stopped at the early `Probing EDD` / TDX
message. Sending `uname` through the QEMU monitor did not produce an
observable guest command result. The two captures are inconsistent, so neither
establishes an interactive shell or a stable live-ISO boot.

## Inputs and isolation

- ISO: `archlinux-2026.10.01-x86_64.iso`, 1,640,497,152 bytes.
- SHA-256: `684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5`;
  matches the value checked on the [Arch Linux download page](https://archlinux.org/download/).
- QEMU: 10.2.1, Ubuntu WSL, TCG (`thread=multi`), 2 vCPUs, 2 GiB RAM, Q35,
  `-cpu max,vmx=off,svm=off`.
- QEMU ran as the unprivileged `alex` account. Arguments used `-nic none`,
  `-display none`, and a single read-only CD-ROM ISO. No guest disk, host
  device, network adapter, or writable host share was attached. The HMP
  monitor saved screenshots to the separately authorized workspace.
- Although `/dev/kvm` exists in WSL, QEMU with a process-only supplementary
  `kvm` group still failed `-accel kvm` with `Could not access KVM kernel
  module: No such device`. WSL's reported CPU flags contain neither `vmx`
  nor `svm`, and its `/proc/modules` has no KVM module. QEMU therefore used
  TCG, not hardware acceleration. No persistent account/group or WSL
  configuration change was made. Microsoft documents WSL's
  `nestedVirtualization` setting as enabled by default in
  [`.wslconfig`](https://learn.microsoft.com/en-us/windows/wsl/wsl-config).
- The KVM probe invoked `qemu-system-x86_64 -accel kvm -cpu host -m 256M
  -smp 1 -machine q35 -nodefaults -nic none -display none -monitor stdio
  -S` as `alex`, using `setpriv` to add only supplementary GID 991 (`kvm`) for
  that process. QEMU exited 1 with the error above before a VM was created.
- The approved external ext4 workspace was remounted read-only after these
  captures.

The QEMU executable arguments were:

```text
qemu-system-x86_64 -accel tcg,thread=multi -cpu max,vmx=off,svm=off \
  -m 2048 -smp 2 -machine q35 -boot order=d \
  -drive file=/mnt/c/Users/Alex/Downloads/LibreCore-VMSetup-20261015/archlinux-2026.10.01-x86_64.iso,media=cdrom,readonly=on \
  -nic none -display none -monitor stdio
```

The process was launched through `runuser -u alex`. The first capture's HMP
sequence waited 180 seconds, then requested `info status`, a screenshot and
`quit`. The later console check waited 210 seconds, sent HMP `sendkey` inputs
for `uname` followed by Enter, waited three more seconds, and requested status,
a screenshot and `quit` (about 213 seconds after launch). Its monitor reported
`VM status: running`, but neither
the display nor monitor showed the command's output. The ISO checksum was
computed on Windows with `Get-FileHash -Algorithm SHA256` and compared with
the value on the linked Arch page.

## Captures

- [`archlinux-welcome-capture.png`](archlinux-welcome-capture.png) — one
  180-second capture displaying the Arch welcome text and completed ISO mount
  hooks. It contains no shell prompt or verified command output.
  SHA-256: `d60d8acd43b4f2828f1b4699b0c09ba61e9f8c4cd75f4e8939678f0f59ec0d55`.
- [`archlinux-console-check-capture.png`](archlinux-console-check-capture.png)
  — later bounded console-check capture, after a 210-second wait and the
  three-second post-command delay. It shows the early boot screen rather than
  an interactive shell.
  SHA-256: `ef417b7596e0df773615aeb34328882b3a321d6a2fb23b6d59827b8826ee7e59`.

## What remains unproven

This diagnostic did not boot the LibreCore worker image, run systemd
preflight or builder units, install the locked package set, execute
`pacstrap -N` or `mkarchiso`, validate a target root or output provenance,
produce a product ISO, or test BIOS/UEFI boot. It does not justify a production
worker pin or enabling `PRODUCTION_WORKER_DEPLOYMENT`. The worker remains
unavailable. These VM diagnostics do not test or authorize any physical
firmware write or recovery operation.
