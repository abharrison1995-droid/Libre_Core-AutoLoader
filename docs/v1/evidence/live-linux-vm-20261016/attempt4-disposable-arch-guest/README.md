# Disposable Arch guest preflight

This is an evidence-only setup for a disposable full Arch Linux guest. It does
not build an AutoLoader ISO or firmware and passes through no physical
hardware, USB devices, programmer interfaces, or host block devices. The
installer ISO and source checkout are read as file-backed media only; both are
attached read-only.

## Reviewed inputs and limits

- Source revision: `0522fcb3ba68c32df428b998e6f26c0744a50860`.
- Installer ISO: `/mnt/c/Users/Alex/Downloads/LibreCore-VMSetup-20261015/archlinux-2026.10.01-x86_64.iso`, SHA-256
  `684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5`,
  volume label `ARCH_202610`.
- Emulator: Arch WSL `/usr/bin/qemu-system-x86_64`, QEMU 11.1.2, TCG only.
- Guest memory/CPU: 2 GiB and 2 vCPUs. Retry4 used a 600-second wall limit;
  retry5 and retry6 used a 900-second global limit
  and a separate 45-second post-marker prompt window.
- Source: local 9p share mounted read-only. The host runner verifies the exact
  source revision and evidence-only Git status. Before importing any
  `usb_builder` module, the guest verifies a bound regular-file SHA-256
  manifest for guest-relevant source files and requires a source write-open to
  fail with `EROFS`. The guest does not run Git.
- Network: QEMU user-mode networking with `restrict=on`, no forwarding rules,
  and no host devices passed through.
- Writable storage: one new, sparse 3 GiB raw file in the private WSL ext4
  output directory. The guest checks the QEMU-assigned serial, capacity,
  read/write status, and absence of partitions or mounts before formatting it.
  No other writable block device is configured.
- C: free space is checked before setup and throughout the run. The runner
  stops if less than 2 GiB remains.

## Procedure

### Disposable guest writes

The guest setup is intentionally disposable and writes only to its volatile
live filesystem/overlay and the exact scratch disk. Setup creates the
`fwbuild` group and `/home/fwbuild` account/home, writes `/etc/subuid` and
`/etc/subgid`, creates the private `/run/lc-pycache` directory, and copies the
pinned helper to `/run/lc-guest-probe.py`. The scratch check formats the exact
identified 3 GiB virtual scratch disk, mounts it temporarily, writes and
removes its probe marker, then unmounts it. These guest writes do not target
the host source share or installer ISO: the 9p source is mounted read-only and
the ISO is attached as read-only CD media.

Before the run, `/var/tmp` was verified as a real root-owned sticky directory
on WSL ext4, and `/var/tmp/lcvm` was verified absent. That private directory
was then created as UID/GID 65534 with mode 0700. The runner checks that exact
identity, no supplementary groups/capabilities, and `NoNewPrivs=1`; it does
not change `/home/alex`.

The retry4 invocation used the reviewed privilege drop and this output name
(now occupied; do not rerun against it):

```sh
wsl -d archlinux -- setpriv --bounding-set=-all --inh-caps=-all --ambient-caps=-all --reuid=65534 --regid=65534 --clear-groups --no-new-privs --reset-env -- env HOME=/var/tmp/lcvm PATH=/usr/bin:/bin TMPDIR=/var/tmp/lcvm /usr/bin/python /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader/docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/run_arch_guest.py --output-dir /var/tmp/lcvm/lc-vm-arch-guest-20261016-retry4
```

Retry4 added `systemd.debug_shell` and
`systemd.default_debug_tty=ttyS0` while leaving automatic getty generation
enabled. It reached the debug-shell service marker but no prompt. That result
is preserved below; it is not normal boot/login validation.

The output directory must not already exist. The runner records its exact
command line, ISO/kernel/initramfs hashes, source identity, QEMU monitor and
serial logs, screenshots, C: free-space checks, scratch-disk identity, and
guest preflight output. It kills and reaps its own QEMU process group in a
`finally` path and publishes an incomplete result on any failed gate.

The guest checks exact locked package versions before creating the temporary
`fwbuild` identity. It then runs only the read-only trusted-tool/host and
mount-capability preflights, verifies the isolated writable scratch file, and
stops immediately. It does not run `build_usb`, `pacstrap` with package names,
`mkarchiso`, or any firmware command. A successful result would establish
only this disposable VM's debug-shell preflight behavior; it would not validate
normal systemd/login boot, an AutoLoader ISO, native Linux, or hardware.

## Current state

The first launcher attempt is preserved at
`/var/tmp/lcvm/lc-vm-arch-guest-20261016/run-result.json`. It stopped before
QEMU launch because Git rejected the C:-mounted repository as dubious
ownership under UID 65534. The result records that the C: free-space floor was
preserved and the installer ISO hash was unchanged; no QEMU process or scratch
disk was created. The runner now gives each read-only Git command exact
per-command `safe.directory`, `core.autocrlf=true`, and `core.filemode=false`
settings for the resolved source path and sets `GIT_OPTIONAL_LOCKS=0`; it does
not modify global Git configuration. Host status has a 120-second bound and
guest status has a 150-second bound; both remain below retry4's 600-second
wall limit.

The first launched retry used output directory
`/var/tmp/lcvm/lc-vm-arch-guest-20261016-retry1`. Its archived records are in
`results/retry1/`; `SHA256SUMS` verifies the copied `run-result.json`,
`serial.log`, `hmp-transcript.txt`, and `vga.ppm`. The QEMU guest mounted the
`/dev/disk/by-label/ARCH_202610` installer and `/dev/loop0` live root in its
initramfs, but no serial login prompt appeared within 300 seconds. The runner
stopped and reaped QEMU. The ISO hash before and after was
`684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5`; QEMU's
post-quit exit code was 0, which does not indicate guest login or preflight
success. The sparse scratch file remained at 0 allocated bytes, the C: 2 GiB
free-space floor remained preserved, and no product build, package sync, or
hardware validation occurred.

At retry2 preflight, Git status returned seven visible evidence paths. The
repository's `.gitignore:60:*.log` intentionally omits
`results/retry1/serial.log`; its bytes are still retained in retry1's
`SHA256SUMS`. Retry2's prior allowlist mistakenly expected that ignored log in
the visible status output, so the runner stopped before QEMU launch. Its
`run-result.json` and a new `SHA256SUMS` are preserved under
`results/retry2-prelaunch/`; the result confirms no QEMU launch, no scratch
file creation, unchanged ISO hash, and a preserved C: space floor. Because
that runner did not record the Git command output before the assertion, the
preserved result records the failure reason but not the observed status lines.

The revised source check now expects twenty-six Git-visible evidence paths:
the seven retry1-visible paths, two retry2-prelaunch records, four visible
records for each of retry3, retry4, retry5, and retry6.
The retry1 through retry5 `serial.log` files are intentionally ignored by
`.gitignore:60:*.log`; retry6's serial log is ignored by the same rule. Exact
`git check-ignore --quiet` checks are required for all five serial logs, and
all five remain hash-listed in their manifests. The runner and guest helper
verify all six manifests and every artifact listed by each against pinned
hashes.
Run results record Git HEAD/status outputs and return codes before comparing
the allowlist.

Retry3 used QEMU TCG with `thread=multi`, 2 vCPUs, and the 300-second bound.
Its unchanged ISO hash was
`684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5` both
before and after. Serial output confirms the installer volume and `/dev/loop0`
live root mounted, then reaches the late `archiso_pxe_common` and `udev`
cleanup hooks; no serial shell appeared before the bound. The runner stopped
and reaped QEMU (exit code 0, which is not a guest-success signal). The scratch
file remained 0 bytes allocated, the C: 2 GiB floor was preserved, and no
product build, package sync, or hardware validation occurred. The copied
`results/retry3/` records are pinned by its `SHA256SUMS`. This archived
retry3 result predates the elapsed-time-field correction; the runner now
records `elapsed_seconds_before_stop` for every launched-run exit path.

Retry1 used Q35 with single-threaded TCG (`accel=tcg`); retry3 used TCG with
`thread=multi`. Both reached the ISO and loop-root mounts but no serial shell
within the 300-second cap. Retry3's final serial output advances through the
late PXE hook into the udev cleanup hook, while retry1 ends after the late
PXE hook. This is a difference in the recorded endpoint, not proof that the
accelerator caused the extra progress or that either guest completed normal
boot.

Retry4 used output directory
`/var/tmp/lcvm/lc-vm-arch-guest-20261016-retry4`, with the existing device,
network, storage, source-share, and C: controls unchanged. The 600.002-second
run reached systemd's `debug-shell.service` on `ttyS0` near the wall deadline.
Serial output includes `Started Early root shell on /dev/ttyS0 FOR DEBUGGING
ONLY` and the OSC marker `servicename=debug-shell.service`, but the runner did
not match a shell prompt. It recorded zero guest commands and stopped/reaped
QEMU at the 600-second bound. The status is
`SYSTEMD_DEBUG_SHELL_NOT_REACHED`; this means the service-start marker was
observed without an interactive prompt, not that the debug shell or a normal
login completed.

The ISO hash was
`684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5` both
before and after. QEMU's post-quit exit code was 0, which is not guest success.
The scratch image had 0 allocated bytes and the C: free-space floor remained
preserved. No guest probe command, product build, package sync, firmware
validation, or hardware access occurred. The four captured files and their
manifest are preserved in `results/retry4/`. Retry4 does not validate normal
boot/login, the AutoLoader product, native Linux, or hardware. Product build
and deployment compatibility remain unvalidated.

## Retry5 (executed, incomplete)

Retry5 used the fresh output directory
`/var/tmp/lcvm/lc-vm-arch-guest-20261016-retry5`. It retained the same ISO,
kernel/initramfs, TCG multi-threading, 2 vCPUs, read-only source share,
restricted user-mode networking, scratch-device controls, and 2 GiB C: floor
as retry4. The kernel command line used `systemd.getty_auto=0`,
`systemd.debug_shell`, and `systemd.default_debug_tty=ttyS0` with the serial
console retained. This suppresses automatically generated serial-getty units
on the debug shell's tty; static serial-getty unit absence was not checked, so
another statically enabled unit could still contend for ttyS0.

The exact bounded OSC `servicename=debug-shell.service` marker appeared and
the runner sent one newline. Serial briefly contained a `sh-5.3#` prompt, but
subsequent systemd output followed before the prompt remained the final line
for the required quiet interval. The fresh stable-prompt check therefore
rejected it and sent zero guest commands. The runner stopped and reaped QEMU
at 505.307 seconds; QEMU exit code was 0, which is not guest success. The ISO
hash remained
`684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5` before
and after, the 3 GiB scratch file had zero allocated bytes, and the C: floor
remained preserved. No package sync, product build, firmware validation, or
hardware access occurred. The four payload files and SHA256SUMS are preserved
in `results/retry5/`; its run result records runner SHA-256
`6bb8b65af95dd8b79d199b7c2d3082dfb3fab43729897cbc8b31a9143ff2e67c`.

Retry5 does not establish a stable interactive debug shell, normal boot/login,
the AutoLoader product, native Linux, or hardware behavior. The debug-shell
approach remains diagnostic-only.

## Retry6 (executed, incomplete)

Retry6 used the fresh output path
`/var/tmp/lcvm/lc-vm-arch-guest-20261016-retry6`, retaining retry5's verified
ISO/kernel/initramfs, QEMU 11.1.2 TCG multi-threaded configuration, 2 GiB RAM,
2 vCPUs, read-only source share, restricted user-mode networking, sparse
3 GiB scratch device, 2 GiB C: floor, 900-second global bound, 45-second
debug-prompt window, exact marker/newline gates, and the read-only probe.
The only kernel command-line addition from retry5 was
`systemd.show_status=0`; the other arguments remained in order. The executed
runner SHA-256 was
`87481c731ee41649f7891c1106716f5e79c43369514401dd636801b1b796ede3`.

The exact debug-shell OSC marker appeared, the runner sent one blank newline,
and it accepted a fresh `sh-5.3#` prompt. It ran its built-in identity,
read-only source-mount, and helper-transfer steps. The pinned guest helper's
SHA-256 matched, but its first Git identity check failed because the live Arch
ISO does not contain `/usr/bin/git`; the helper emitted
`GUEST_PROBE_EXCEPTION=FileNotFoundError` and returned 1. The package, host,
mount, and scratch preflights therefore did not complete. No package sync or
product build ran, and no hardware was accessed.

The runner exited 1 after 758.401 seconds and reaped QEMU. QEMU's post-quit
exit code was 0, which is not guest or preflight success. The ISO hash was
`684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5` both
before and after; the 3 GiB scratch file had 0 allocated bytes and the C:
free-space floor remained preserved. The prompt is an ephemeral systemd
debug shell, not normal boot/login validation. The four raw captures and
their SHA256SUMS are preserved under `results/retry6/`.

The archived result embeds the exact command argv, marker/newline/prompt
evidence, guest helper exception, hashes, timings, and runner SHA. Retry6 does
not establish successful package/mount preflight, normal boot/login, the
AutoLoader product, native Linux, an ISO build, or hardware behavior.

The runner will act only after a complete OSC 3008 sequence, at most 4096
bytes, containing exactly one `servicename=debug-shell.service` field. It then
sends exactly one blank newline and records the marker, byte offsets, send
time, and response hash. It sends no guest command until fresh serial bytes
after that newline end in one of these exact normalized prompt lines:
`# `, `sh-5.3# ` / `bash-5.3# ` (version digits are matched), `root@host …# `,
or `[root@host …]# `. Complete ANSI CSI/OSC controls are removed before
matching; unknown or partial escape sequences fail the match. The prompt must
remain the final line across a one-second quiet interval. A prompt followed
by systemd output, a prompt that predates the newline, or a partial escape
sequence is rejected. The prompt wait ends after 45 seconds or at the 900-
second global bound, whichever comes first. No prompt means an incomplete
result and zero guest commands.

Once that ephemeral debug-root prompt is confirmed, every command-return wait
(`stty -echo`, the harmless echo check, the read-only source mount, helper
copy steps, and the guest preflight) uses the same strict debug-root matcher
against bytes received after that individual command was sent; marker-based
commands require the prompt bytes to follow that command's unique marker as
well. A stale prompt from before a command or before its marker cannot
complete it. The runner retains a separate
default matcher for the normal Arch live-login prompt path; retry5 explicitly
selects debug mode for all its shell interactions. Each command record stores
the selected prompt mode.

Retry1–retry6 captures and hashes are preserved and unchanged after capture.
Retry7 reached an ephemeral debug-root shell, but that proves only that the
diagnostic shell is interactive. It does not prove normal boot/login,
AutoLoader application/service startup, an AutoLoader ISO, native Linux
support, or hardware behavior.

## Offline ISO package inventory

The ISO's embedded package descriptions were inspected offline against the
snapshot lock. They report `archiso 91-1`, `arch-install-scripts 31-2`,
`pacman 7.1.0.r9.g54d9411-2`, `util-linux 2.42.4-1`, `bash 5.3.20-1`,
`shadow 4.20.0.arch1-1`, and `libcap 2.78-1`, matching the locked versions.
The inspected installer ISO hash was
`684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5`. This is
only an offline package-description inventory; it does not establish guest
package installation or any build/preflight result.

## Retry7 result (incomplete diagnostic)

SOL reviewed and accepted the exact retry7 candidate hashes before the
one bounded launch. The runner reached the systemd debug-shell service marker,
sent one blank newline, accepted the fresh prompt, disabled terminal echo,
mounted the source share read-only, and transferred the hash-pinned helper.
The guest verified the helper hash and all 24 entries of the canonical
`usb_builder` source manifest. A write-open of `usb_builder/build_usb.py`
failed with `EROFS`. The guest records that Git HEAD/status were checked on
the host only; no independent guest Git check was performed.

Imports use `PYTHONPYCACHEPREFIX=/run/lc-pycache`,
`PYTHONDONTWRITEBYTECODE=1`, and `PYTHONOPTIMIZE=0`. Immediately after its
imports, the preflight unconditionally rejects `sys.flags.optimize != 0`, so
inherited `PYTHONOPTIMIZE=1` is overridden and an optimized interpreter is
rejected before assertion-based integrity checks. The child emits
`CHILD_PYTHON_OPTIMIZE=0` only after this guard. The guest must create that prefix fresh as a
private empty directory; the child verifies `sys.pycache_prefix`,
`sys.dont_write_bytecode`, owner, mode, and emptiness. The no-launch harness
checks that a timestamp-valid malicious adjacent source `__pycache__` cannot
override source when the private prefix is active, and that an existing or
nonempty prefix is rejected.

The helper command began about 726.5 seconds into the 900-second run. Its
guest UTC date command returned around second 833; the guest then reported its
identity and kernel, checked the source, and sent the read-only `pacman -Q`
query for the locked package set. No query result or process state was
recorded before the global deadline. At most 67 seconds remained for the
source checks, package query, and completion. The evidence therefore does
not isolate a `pacman` hang; slow guest execution or serial/source-transfer
overhead remain possible. The helper exit marker was not seen, so the package
version check, temporary `fwbuild` identity, UID/GID 1000 rootless preflights,
and scratch-disk probe were not completed or validated. In particular, the 3
GiB scratch file remained unformatted with zero allocated bytes.

The runner stopped and reaped its QEMU process at 900.059 seconds with QEMU
exit code 0. The result records `diagnostic_complete=false`, no guest probe
completion marker, no product build, no package installation or sync, and no
hardware or firmware validation. The installer ISO hash was unchanged and the
2 GiB C: free-space floor was preserved. This run does not establish guest
package-lock compatibility or a successful rootless preflight.

The five archived payloads (`argv.json`, `hmp-transcript.txt`,
`run-result.json`, `serial.log`, and `vga.ppm`) and their `SHA256SUMS` are in
`results/retry7/`. No further guest launch is part of this reviewed retry7
candidate; any later attempt requires a newly reviewed candidate.

## Retry8 result (host package query incomplete)

SOL reviewed the frozen retry8 candidate hashes before one bounded launch in a
disposable Arch guest under Windows-hosted QEMU TCG. The guest reached its
systemd debug shell, copied the helper from the read-only source share, and
verified its hash and all 24 source-manifest entries. The source write check
was denied. There was no hardware or host block-device passthrough.

The exact read-only `/usr/bin/pacman -Q` query returned after 140.495 guest
seconds with exit 1. Six pinned versions matched, but `archiso` was absent;
pacman also warned that the `core` and `extra` repository sync database files
do not exist.
The probe stopped before rootless/mount preflights, scratch probing, and all
builder work. No sync, package installation, build, ISO, or hardware action
occurred. QEMU was reaped after 1,032.341 seconds; QEMU exit 0 is cleanup
status, not a pass. The ISO remained unchanged, scratch allocation remained
zero, and the host free-space floor remained preserved.

SOL's independent review classified the result as a diagnostic-host mismatch,
not a target-image package defect. `archiso 91-1` is a build-host tool
requirement checked by `_rootless_archiso_host_preflight`; the live profile
correctly contains `mkinitcpio-archiso` without the host-only `archiso`
package. The package lock and profile remain unchanged. Raw captures and
checksums are in `results/retry8/`, with an interpretation in its
[`README`](results/retry8/README.md). Any next builder run must use a
separately prepared disposable guest with signature-verified pinned host
packages and a new reviewed candidate.
