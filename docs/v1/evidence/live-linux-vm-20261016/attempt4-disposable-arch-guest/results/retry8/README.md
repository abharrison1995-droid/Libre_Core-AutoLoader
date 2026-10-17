# Retry8 result — host package query incomplete

SOL reviewed the frozen retry8 candidate hashes before one bounded launch in a
disposable Arch guest under Windows-hosted QEMU TCG. The run used no USB,
programmer, hardware, or host block-device passthrough. It reached the
systemd debug shell, copied the helper from the read-only source share, and
verified the helper hash and all 24 source-manifest hashes. The source write
check was denied. The installer ISO hash was unchanged.

The read-only command was exactly:

```text
/usr/bin/pacman -Q arch-install-scripts archiso bash libcap pacman shadow util-linux
```

It returned after 140.495 guest seconds with exit status 1. Six versions
matched the frozen lock: `arch-install-scripts 31-2`, `bash 5.3.20-1`,
`libcap 2.78-1`, `pacman 7.1.0.r9.g54d9411-2`, `shadow 4.20.0.arch1-1`,
and `util-linux 2.42.4-1`. `archiso` was absent. Pacman also warned that its
`core` and `extra` repository sync database files do not exist. The captured query is
in `serial.log`; its output SHA-256 is
`cb3498c23e1a58a673def42c345e4ad4a9ecf92de4bbea5426fe2d0ac927d893`.

The result is `GUEST_LOCKED_PACKAGE_QUERY_INCOMPLETE`, not a lock pass. The
probe correctly stopped before `fwbuild` identity, rootless/mount preflights,
scratch probing, and builder execution. It performed no package sync or
installation, firmware build, ISO build, or hardware operation. The scratch
file remained unallocated; the host free-space floor was preserved. QEMU was
reaped after 1,032.341 seconds; its exit status 0 records monitor cleanup,
not gate success.

SOL's review classified this as a diagnostic environment mismatch. The
`archiso 91-1` pin is a **build-host tool requirement** checked by
`_rootless_archiso_host_preflight`; the live runtime profile correctly
includes `mkinitcpio-archiso` and excludes host-only `archiso`. This run does
not establish a Live USB packaging defect. Do not weaken the lock or add the
host builder package to the live-image package list. The next meaningful
builder validation requires a separately prepared disposable build guest
with signature-verified pinned host packages.

Raw captures and per-file hashes are in this directory. The exact frozen
candidate and its review hashes remain under `../../retry8-candidate/`.
Neither the candidate nor its result implies a successful native-Linux
preflight, rootless build, ISO, boot, ROM build, or hardware workflow.
