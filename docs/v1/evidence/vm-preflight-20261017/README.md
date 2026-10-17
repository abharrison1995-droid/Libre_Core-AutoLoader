# Disposable VM storage and asset preflight — 2026-10-17

This preflight records the available Windows QEMU and Arch ISO assets, the
state of a pre-existing diagnostic VM, and follow-up diskless QEMU boot
experiments. It does not validate a firmware build, project Live USB ISO, ROM,
or hardware workflow.

## Verified assets

- Windows QEMU: `C:\Users\Alex\msys64\ucrt64\bin\qemu-system-x86_64.exe`,
  version **11.1.2**, SHA-256
  `A812B50865FFCC7DDD43F9C33E9BB1ED7B3501C1F847EE99E8D4B4A4A93742`.
- Existing Arch ISO:
  `C:\Users\Alex\Downloads\LibreCore-VMSetup-20261015\archlinux-2026.10.01-x86_64.iso`,
  **1,640,497,152 bytes**, SHA-256
  `684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5`.
- `usb_builder/snapshot.lock.json` SHA-256:
  `3B73F6F1FC123C223ABC93F58CEF9BB5D2590D4CE5CDF219C6209BA432291F8E`.
  It pins Archiso **91-1** and the **2026-09-29** repository snapshot.
- `usb_builder/archiso_profile/packages.x86_64` SHA-256:
  `7A7A3FA4127AB6FBAAF952BE4BBDE82F787A7DA30D3111601A9E7FCCD6334DBD`.
- C: had **6.66 GiB free** at the preflight check.

## Existing VM and storage limits

The existing QEMU diagnostic process, PID **12540**, remains attached to its
old overlay. A prior HMP snapshot showed it paused. A later read-only
`info status` query returned telnet echo that could not be parsed, so this
record does not claim a newly confirmed paused state. Do not resume or reuse
that process or overlay.

An **unmounted 254.9 GB GPT Linux filesystem partition** has unknown contents
and remains untouched pending user clarification. Its partition metadata was
read only; its contents were not inspected. No mutating VM or partition
command was run. A
2 GiB free-space reserve leaves at most about **4.66 GiB** for additional C:
growth; peak storage use has not been measured. No persistent VM was created
at this preflight stage. The user later requested a full VM, but the unknown
partition remains untouched until a storage location is confirmed.

## Diskless boot experiments

These experiments used the official Arch ISO above as a read-only CD-ROM and
created no virtual disk, USB passthrough, or physical-device mapping. The
launches supplied no explicit network option, so QEMU's default user-mode
networking may have been present; no network traffic was captured. The
pre-existing QEMU process and unknown 254.9 GB partition were not used. The
QEMU executable and ISO paths were the ones listed above.

| Machine / accelerator | Result | Evidence |
| --- | --- | --- |
| `q35`, WHPX | Kernel panic at `No working init found`; this does not identify whether the cause is the kernel, initramfs, or boot configuration. | `arch-live-bios-now.png`, `arch-live-bios-serial.log` |
| `pc` (i440FX), WHPX | Boot screen showed the kernel loaded and initramfs loading, but the attempt did not reach a shell before it was stopped. | `arch-live-pc-now.png`, `arch-live-pc-serial.log` |
| `q35`, TCG with `thread=multi` | Kernel/initramfs progressed into systemd startup. The final retained screen still showed a systemd static-device-node start job running; no interactive shell or usable live session was verified. | `arch-live-q35-tcg-now.png`, `arch-live-q35-tcg-final.png`, `arch-live-q35-tcg-serial.log` |

The q35 TCG invocation recorded during the run was:

```powershell
& 'C:\Users\Alex\msys64\ucrt64\bin\qemu-system-x86_64.exe' -machine q35 -accel tcg,thread=multi -cpu max -m 4096 -cdrom 'C:\Users\Alex\Downloads\LibreCore-VMSetup-20261015\archlinux-2026.10.01-x86_64.iso' -boot order=d -display none -serial file:C:\Users\Alex\Desktop\Libre_Core-AutoLoader\docs\v1\evidence\vm-preflight-20261017\arch-live-q35-tcg-serial.log -monitor stdio -no-reboot
```

This command was transcribed from the run notes; no raw process-argument or
HMP transcript was retained. During the run, HMP `info block` was observed to
report the CD-ROM as `raw, read-only`. HMP `quit` was sent and the QEMU process
exited; the shutdown exchange was not captured. The serial file contains
bootloader output only; the VGA captures show kernel and systemd progress.

The q35 WHPX command used `-machine q35,accel=whpx -cpu max -m 4096 -cdrom
<official Arch ISO> -boot order=d -display none -serial
file:<arch-live-bios-serial.log> -monitor stdio -no-reboot`. The i440FX WHPX
control changed the machine argument to `-machine pc,accel=whpx` and logged to
`arch-live-pc-serial.log`. These command descriptions are reconstructed from
the run notes; raw process arguments were not retained. WHPX printed a
nonfatal performance-monitoring warning during startup.

## Result and limits

**Diskless boot diagnostics only.** Q35 TCG advanced farther than the WHPX
attempts, but no interactive Arch live shell was verified. No persistent VM
was created, and there was no project ROM build, Live USB ISO build, QEMU app
launch, or physical hardware workflow. QEMU's default networking may have
been available, but no network traffic is evidenced. The pre-existing
diagnostic VM was not resumed, reused, or modified. The partition contents
were not inspected or changed; there was no mount, write, or format.
Additional storage must be
identified before a persistent VM or storage-intensive Archiso build because
available C: growth is limited and peak use is unknown.

`SHA256SUMS` covers this note and the retained image/log artifacts.
