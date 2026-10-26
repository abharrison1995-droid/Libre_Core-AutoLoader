# Offline VM environment diagnostic — 2026-10-23

## Result

**A minimal initramfs shell was reached; full-ISO, worker, build, production
boot, and hardware proof remain pending.** A direct kernel/initramfs run with
`rdinit=/bin/sh` reached BusyBox, and HMP input produced `Linux` from `uname`.
That establishes only a minimal early-userspace shell command. It does not
establish an Arch live-ISO or product boot. A later debug-shell request did not
produce a shell; the final full-ISO attempt with floppy drives disabled
progressed through the initramfs hooks to the Arch welcome screen, without an
interactive shell or visible systemd. All runs used QEMU TCG without a guest
network interface.

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

## Latest copied-ISO direct-kernel TCG follow-up — 2026-10-23

The official Arch ISO was copied into the user-approved private ext4
workspace. Both the source ISO and workspace copy have SHA-256
`684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5`. The
extracted kernel hash is
`8ebc2c71271e000540f0a7545afd8e73504fcb724671f3d38c720b241842b901`; the
initramfs hash is
`35b4362207fcabc66535444aea0b97f3508128b32e97e66aa5f56487007bfe52`.

QEMU 10.2.1 ran as unprivileged `alex` in TCG mode with a `pc` machine,
`qemu64` CPU, two vCPUs, and 2 GiB RAM. The ISO was attached as a read-only
CD-ROM with `-nic none`; no disk, writable host share, or passthrough device
was attached. Direct kernel/initramfs boot reached systemd. A serial attempt
waited for `/dev/ttyS0` and yielded no shell. Curses and VGA attempts reached
Arch ISO hooks and the welcome screen; one later showed systemd status.

In a repeat, the no-network time-sync and udev-settle units were masked.
Systemd logged a failed preset for the masked timesyncd unit and
`[FAILED] Failed to start Namespace Resource Manager`, then continued waiting
for the static device-node `/dev` service. No guest prompt appeared. HMP
`sendkey` input for `uname -a` produced no verified command output.

A read-only Windows WMI query reported the host CPU as Intel Core i3-3110M and
returned `VirtualizationFirmwareEnabled=False`,
`VMMonitorModeExtensions=False`, and
`SecondLevelAddressTranslationExtensions=False`. The exact query and output,
captured during this run, are retained in the
[run record](run-record-20261023.txt). The approved ext4 workspace is now
read-only. The latest masked-service QEMU invocation, input hashes, HMP
`Ctrl-Alt-F1` / `uname -a` / Enter input, `screendump /tmp/lcguest-final.ppm`
command, and outcome are also recorded there. Its
screen is preserved as [`boot-screen-20261023.bmp`](boot-screen-20261023.bmp)
with SHA-256
`227b8bcb14c5e1d01e333cf2467660aec7aa852907469bc3d94830cde536f3dd`.
These results remain diagnostic only: they are not a shell, worker, build,
boot, or hardware pass. See the
[`SHA256SUMS`](SHA256SUMS) manifest for retained-artifact hashes and the
separately labeled external input hashes.

### Bounded direct-kernel debug-shell attempt — 2026-10-23

A later bounded TCG attempt used the same official ISO as a read-only CD-ROM,
with no NIC, guest disk, writable host share, or passthrough. It requested
`systemd.debug_shell=1` while leaving the NNP setting otherwise unchanged.
After 120 seconds, the guest remained at Arch initramfs hooks and the welcome
screen; the requested debug shell did not appear. HMP input for `id` yielded
no verified guest command. The exact QEMU command and monitor sequence are in
the [run record](run-record-20261023.txt). This is not a shell, worker, build,
or boot pass, and it does not prove the effective guest NNP state.

A 720x400 screenshot was viewed during the run and was reported at
`\\wsl.localhost\Ubuntu\tmp\lcguest-debug-shell.bmp`. Ubuntu WSL later cleaned
`/tmp`; the file was absent at that path when checked for this update. It was
not retained in the repository, so no screenshot artifact or hash is claimed.

### Minimal initramfs shell and final full-ISO attempt — 2026-10-23

A separate minimal initramfs run used the approved workspace kernel and
initramfs with `rdinit=/bin/sh`, one emulated vCPU, and 1 GiB RAM. QEMU ran as
an unprivileged Ubuntu WSL user under TCG with `-nic none`; it had no guest
disk, network, host share, or passthrough. After 55 seconds, HMP typed `uname`
and Return. The retained screen shows `/bin/sh` as init, a BusyBox prompt,
and `uname` returning `Linux`. This proves only the requested minimal
initramfs shell command. See
[`guest-initramfs-shell-capture-20261023.bmp`](guest-initramfs-shell-capture-20261023.bmp)
and the [run record](run-record-20261023.txt).

The final full-ISO attempt used the same kernel/initramfs with the official
Arch ISO attached read-only, `pc`/`qemu64`, two vCPUs and 2 GiB RAM. Floppy
drives were disabled; no NIC, writable guest disk, host share, or passthrough
was attached. At about 120 seconds, HMP switched TTY, sent `uname`, captured
the screen, and quit. The screen shows `/dev/sr0` and loop mounts, a cleanup
hook, and `Welcome to Arch Linux!`; no interactive shell or systemd was
visible. Exact QEMU arguments, HMP command sequences, and the capture are in the
[run record](run-record-20261023.txt) and
[`boot-screen-no-fdc-20261023.bmp`](boot-screen-no-fdc-20261023.bmp).

The minimal shell result does not establish full-ISO, worker, build, product
boot, or hardware success. The latest read-only Windows WMI virtualization
fields remain `False`; the command and captured output are in the run record.

## What remains unproven

This diagnostic did not boot the LibreCore worker image, run systemd
preflight or builder units, install the locked package set, execute
`pacstrap -N` or `mkarchiso`, validate a target root or output provenance,
produce a product ISO, or test BIOS/UEFI boot. It does not justify a production
worker pin or enabling `PRODUCTION_WORKER_DEPLOYMENT`. The worker remains
unavailable. These VM diagnostics do not test or authorize any physical
firmware write or recovery operation.
