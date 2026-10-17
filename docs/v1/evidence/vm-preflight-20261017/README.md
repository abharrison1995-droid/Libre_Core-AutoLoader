# Disposable VM storage and asset preflight — 2026-10-17

This read-only preflight records the available Windows QEMU and Arch ISO
assets, current C: free space, and the state of a pre-existing diagnostic VM.
It does not validate a VM boot, build, ISO, ROM, or hardware workflow.

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
growth; peak storage use has not been measured. Any new Arch and Ubuntu VM
must have separate, safe storage identified first. This preflight does not
authorize creating a VM or using the unknown partition.

## Result and limits

**Preflight only; no VM/build/ISO/ROM/hardware validation.** The existing
diagnostic VM was not resumed, reused, or modified. The partition contents
were not inspected or changed; there was no mount, write, or format. No new
VM was created. A safe destination must be identified before further VM setup
because available C: growth is limited and peak use is unknown.

`SHA256SUMS` covers this note.
