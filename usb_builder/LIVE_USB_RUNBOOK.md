# Live USB build and operation runbook

This profile is an in-development Archiso environment. The checked-in package
snapshot and package list are fixed for **candidate** builds. Candidate b2
passed target-root validation. Its same-hash BIOS/UEFI QEMU runs and later
direct-kernel diagnostics provide incomplete emulator evidence only. Direct-
kernel run8 reached a tty9 debug shell and captured read-only unit, job, and
journal state, but established no root cause and did not reach the AutoLoader
app/service. The normal BIOS/UEFI captures did not reach an app/service
session. Emulator exit code 0 after an explicit HMP quit is not guest boot
success. No hardware or USB-media test has occurred. A separately pinned
USB-only initramfs derivative omits only the `kms` hook; it does not establish
that this caused or fixes the startup delay. Fake tests cannot advance these
boot or hardware gates.

## Build contract

The build must run on Linux with the exact Archiso version in
`snapshot.lock.json`, a clean Git checkout, an empty existing output directory,
and a separate existing work parent. The builder does not install host tools or
use `sudo`. It requires `pip`, `setuptools >= 61.0`, and `wheel` in the Python
environment used to invoke it; their versions are recorded, and no installer or
network lookup is run. It packages one locally built application wheel and
checks the target's Python imports from packages pinned by the dated Arch
repository snapshot. The clean Git checkout is read-only during wheel creation:
only the Python packages and packaging metadata are copied to the owned
temporary tree. The ISO's embedded input record contains no host-absolute build
paths. The profile does not accept ROMs, stock firmware, backups, keys, or
credentials.

The application venv is created with `--system-site-packages`; it intentionally
uses the runtime packages installed into the image from the locked Arch
package set. After `mkarchiso` returns, the builder inspects the target root
before atomically publishing the complete output bundle. It requires the venv
interpreter, AutoLoader package, distribution and console-entrypoint metadata,
runtime import set, executable startup script, systemd service and enablement,
and a customize-hook marker containing the built wheel digest. It also runs
read-only import and CLI-help checks with `chroot`. These checks establish
target-root installation contents only; they do not establish ISO boot or
service behavior on a booted system.

The target-root interpreter check resolves POSIX symlink components inside the
image root itself, including absolute guest paths and Arch usrmerge links such
as `/usr/sbin -> bin`. It never resolves a guest absolute link against the host
filesystem. Root escapes, cycles, missing targets, and traversal through a
non-directory component fail closed.

The 2026-09-29 lock pins the exact SHA-256 of both `core.db` and `extra.db`.
The archive currently publishes no detached signature for either database.
The builder stages those exact bytes and configures them as the sole pacman
`Server`; package archives use `CacheServer`, which pacman does not use for
database files. If a database signature appears later, the builder verifies it
with the host Arch keyring and pacman verifies it again. Package archive
signatures remain mandatory, and `LocalFileSigLevel` is also `Required`.
Database bytes and optional-signature state are recorded in both embedded and
external provenance and rechecked after `mkarchiso` uses them.

Offline mode requires an explicit local repository plus a manifest that
enumerates every file and SHA-256 and is bound to the checked-in snapshot lock.
The two databases must match the lock; their `.sig` files may be absent, but if
present must be enumerated, hash-pinned and valid. Every package archive in the
dependency closure, including transitive packages, must have an enumerated
detached signature. Offline pacman uses the staged pinned databases as the sole
`Server` and the verified local package repository as `CacheServer`.
Before creating the build work directory, offline mode resolves and verifies
the canonical `unshare` and `ip` executables, including their exact pacman
package owners, then probes a new user-mapped network namespace. Root and
non-root builders both use `unshare --user --map-root-user --net`; bare root
`unshare --net` is rejected because initial-user-namespace capabilities could
allow returning to the host network namespace. Namespace-aware link, address,
route and rule data must show loopback only and no routes. The exact child
wrapper repeats those checks in the same namespace immediately before and
after running the build command. It uses a sanitized environment, closes file
descriptors, rejects subprocess controls outside the builder's fixed allowlist,
and handles timeout/signal termination by killing the entire process group
after a grace period, even if its leader exits first. This is process-group
cleanup, not a guarantee against a descendant that deliberately calls
`setsid()` and leaves that group. Such a descendant remains in the isolated
network namespace, but it could outlive the command wrapper and continue local
filesystem activity. For that reason this does not establish an offline build
as safe or complete. A failed check stops the command or blocks candidate
publication. This namespace probe has passed in Arch WSL; it does not yet
establish native Linux or offline Archiso build support. Mount, loop, and
chroot behavior inside this namespace remain to be validated. Missing
repository, snapshot, signatures, or package inputs still block before
`mkarchiso`.

Example candidate build on a supported Linux host:

```bash
python3 usb_builder/build_usb.py \
  --source-root /path/to/clean/Libre_Core-AutoLoader \
  --profile-dir /path/to/clean/Libre_Core-AutoLoader/usb_builder/archiso_profile \
  --snapshot-lock /path/to/clean/Libre_Core-AutoLoader/usb_builder/snapshot.lock.json \
  --output-dir /path/to/empty/output \
  --work-parent /path/to/scratch-parent \
  --network-mode online
```

The installed Click command uses the same builder contract:

```text
autoloader build-usb --source-root /path/to/clean/Libre_Core-AutoLoader --profile-dir /path/to/clean/Libre_Core-AutoLoader/usb_builder/archiso_profile --snapshot-lock /path/to/clean/Libre_Core-AutoLoader/usb_builder/snapshot.lock.json --output-dir /path/to/empty/output --work-parent /path/to/scratch-parent --network-mode online
```

The build records the Git commit/tree, profile/package-list hashes, isolated
wheel-source hashes, host package inventory, Archiso executable path/owner/hash,
pip/setuptools/wheel versions, installed image package versions, commands,
logs, and ISO hash. Output is always
marked `CANDIDATE_ISO_BUILD_COMPLETED; NOT_RELEASE_VALIDATED`, with BIOS and
UEFI QEMU boot and USB media writing marked `NOT_PERFORMED`. A completed build
command plus successful target-root checks is not an ISO boot test or firmware
artifact acceptance. The build tool does not mutate `release_validation_status`
in the lock.

On success, the previously empty output directory receives one `candidate-bundle`
directory by a single atomic rename. It contains the ISO, `build.log`,
`wheel-build.log`, `build-provenance.json`, and `SHA256SUMS`; there is no
candidate-looking `.iso` in the output before that complete bundle is published.
Copy, metadata-write, checksum, staged-directory-sync, or atomic-rename failure
removes the hidden staging directory and does not publish an ISO. The checksum
list covers every ISO/build-log/provenance file in the bundle (the checksum
list itself is not self-hashed).

The sync-database record distinguishes runtime capture from source-level
evidence. For Archiso 91-1 source commit
`723da192226e1c9d5def9168f13332793c82a85e`, `mkarchiso -C` derives a work
pacman configuration, `pacstrap -C` consumes that generated configuration,
and successful cleanup removes the target's pacman sync database directory.
The builder pins and hashes the exact `core.db` and `extra.db` files served from
its owned staging path and rechecks both those files and the effective config
before and after `mkarchiso`. If the target root retains the sync DB files,
their hashes are captured and compared. If Archiso deletes them, the manifest
records `SOURCE_LEVEL_INVARIANT_ONLY`: the pinned source/config contract and
staged DB hashes identify the configured input, but this does not claim a
runtime capture of pacman's internal reads.

The host package inventory is recorded for audit, but only Archiso 91-1 and the
owned `mkarchiso` executable digest are enforced by this lock; the other host
package versions are not pinned. Exact host-environment reproducibility and
BIOS/UEFI boot validation remain release gates. Phase 09 software was accepted
by SOL after the corrective review. Ubuntu is installed under WSL2 and its
first-run setup is complete; the shell responds as `uid=1000(alex)`. The
signature-verified Archiso 91-1 package is installed in Arch WSL; `pacman -Q
archiso` reports 91-1 and `pacman -Qo /usr/bin/mkarchiso` confirms ownership.
A third online candidate attempt did invoke `mkarchiso`, but failed BIOS
profile validation before package installation because `syslinux` was absent;
it produced no ISO and did not reach target-root validation. A fourth attempt
on 2026-10-13 passed package validation and completed the Archiso build stages,
then failed target-root validation because `/usr/sbin` inside `airootfs` is a
usrmerge link to `/usr/bin`. The output directory remained empty and no ISO was
published. An image-rooted symlink resolver now handles this path safely. A
later temporary pre-initramfs ISO passed target-root checks but failed both
QEMU boot modes, as recorded below. An
Ubuntu WSL2 shell by itself
is not the pinned Archiso 91-1 build environment: the builder still
requires the exact Arch package/tool ownership, pacman repositories and
snapshot-signature validation, plus usable mount, loop, chroot, and network
namespace capabilities. This checkout is currently dirty, so do not start a
candidate build until a reviewed local commit provides a clean Git checkout.
At that earlier checkpoint candidate build, post-build `chroot` checks,
BIOS/UEFI QEMU boot, and ISO production were **NOT_PERFORMED**. The first
namespace implementation was also **UNVALIDATED** because shared WSL sysfs
exposed `eth0` and the IPv6 proc file contained kernel sentinel rows. The
strict user-mapped probe now validates independent namespace-aware views and
accepts only the exact pinned sentinel; see the current WSL evidence below.
The later temporary ISO and its failed QEMU results remain recorded below; no
passing boot or release validation is claimed.

On 2026-10-15, a separate production preflight from Ubuntu 26.04 WSL resolved
`/usr/bin/unshare` but stopped because `pacman` is absent and the trusted
`pacman -Qo` ownership check could not start. It returned
`OFFLINE_NETWORK_ISOLATION_UNAVAILABLE` before namespace/mount checks; it did
not test devtmpfs or create candidate-build work/output. This confirms that
the Ubuntu WSL shell is not the pinned Archiso 91-1 build host. Exact evidence
is in `docs/v1/evidence/offline-namespace-ubuntu-wsl-20261015/`; the separate
Arch WSL run at that checkpoint reached the then-current root-oriented mount
probe and safely failed at devtmpfs. The new exact `-N` probe described below
has not yet been run from this source in the disposable Arch VM.

Historical resolver-focused checks were Arch WSL `tests/test_usb_builder.py` —
68 passed, 2 skipped; Windows — 60 passed, 10 skipped. The later initramfs
fragment guard passed 89 Arch tests with 2 skipped. These are historical
counts; the current namespace validation and focused test results are recorded
below. SOL approved the target-root POSIX symlink resolver and the staged
initramfs-fragment integrity guard. The statement that the fragment had not
been exercised was true at that earlier checkpoint; the later candidate
includes it, with its failed QEMU evidence recorded below.

The first real online candidate attempt stopped at preflight, before
`mkarchiso`, because the package-list pin did not match the checked-in profile.
The checked-in package list's canonical LF hash is
`4fd531281bd659695271de16bdd60283dc36eff5220ae1f07550c72716126262`, now
recorded in the snapshot lock. Windows worktree CRLF bytes hashed to
`70032fecf478ee2b0d9c65837837206596592db671cdf00a61bbdfbf27827595`. The
validator now normalizes CRLF to LF before comparing the canonical pin and
still rejects content drift. SOL accepted the canonical line-ending pin in
commit `3a146f8`; the output directory was empty after the blocked attempt, and
no ISO was produced.

A second clean online candidate attempt then stopped before invoking
`mkarchiso`: discovery returned `/usr/sbin/mkarchiso`, whose parent
`/usr/sbin` is an Arch usrmerge symlink to `/usr/bin`. The output directory
remained empty, and no ISO was produced. The builder now resolves such aliases,
requires an executable regular canonical target, and checks that `pacman -Qo`
output exactly identifies that canonical path as owned by `archiso 91-1`.
Tests cover an usrmerge-style alias and wrong-path/wrong-version owner output.
SOL accepted the canonical-path correction. A third attempt invoked `mkarchiso` but failed BIOS profile validation before package installation because `syslinux` was absent. The output directory remained empty and no ISO was produced. The pinned `core.db` contains `syslinux` 6.04.pre3.r3.g05ac953c-5 with package-signature metadata. The profile and required package policy now include `syslinux`; canonical LF package-list SHA-256 is `520974f323810ab01ea39b7ea7b43c1e7ecbabb823bb4bed4ebab6b1a5cf3329`; SOL accepted the syslinux package-list correction. At that checkpoint, no later retry had run; the fourth attempt is recorded in the current-status section below.

The target-root validator then exposed an in-image path case: the venv
interpreter can resolve through `/usr/sbin/python`, where `/usr/sbin` is a
usrmerge link to `/usr/bin`. The image-rooted resolver and regression tests now
accept this internal chain and reject escapes, cycles, missing targets, and
non-directory traversal. Historical resolver-focused tests passed 68 Arch and 60 Windows cases (with 2 and 10 skipped respectively); the staged-fragment guard checkpoint passed 89 Arch tests with 2 skipped. The accepted hook-package correction passes 105 Arch tests with 2 skipped and 94 Windows tests with 13 skipped. `mypy usb_builder` is clean on both hosts. SOL approved the resolver and package-policy corrections.
The statement that no candidate retry had occurred was accurate at that
checkpoint. A later candidate built with the accepted staged initramfs fragment
and its failed BIOS/UEFI emulator results are recorded below.

## Prior temporary candidate and QEMU evidence

A temporary candidate built before the accepted Archiso initramfs fragment was
added had SHA-256
`5a19235fbfa57c6d32a1d73f4c6868abae60e90b3b7be0d39e508e698e2cc478` and size
897,921,024 bytes. BIOS QEMU failed at `Failed to start Switch Root`; UEFI QEMU
timed out mounting `/dev/gpt-auto-root` and entered the initrd emergency path.
Neither mode reached the application service or yielded usable second-TTY
service/journal evidence. The build log contained a nonfatal mkinitcpio
`failed to detect root filesystem` message. Investigation traced the failure
to the absent `archiso` and `archiso_loop_mnt` initramfs hooks. This ISO is
failed emulator evidence only, not a passing candidate.

## Earlier candidate build and QEMU boot failure (before hook-package correction)

On 2026-10-13, clean source snapshot `349e7961264131f69019ad1ab61cfa950af688a8`
produced a temporary candidate ISO with SHA-256
`9d6fd80e5757b2f5a075fd46f5d60548c782166cacf95378d6bde2ad3dee42fc` and size
1,285,586,944 bytes. The bundle's build manifest SHA-256 is
`e0fd030367e792bf39913f1145804fd3470a9357263cc141486b2bb7a8857689`. This is
temporary emulator evidence, not a release-validated image.

Both QEMU boots used that same ISO hash, QEMU 11.1.2, no network device and no
write-capable firmware interface. BIOS initramfs reported missing `archiso`,
`archiso_loop_mnt`, and PXE hooks, failed to mount the real root and dropped to
`[rootfs~]#`. UEFI ran the udev and memdisk hooks, then also failed mounting the
real root and dropped to `[rootfs~]#`. The BIOS VGA capture SHA-256 is
`1fa299e8e5e4ca4d499bb9c900d15b7e7ca47d2c272cfed4ab28579ff967f848`; the UEFI
capture is `0e76945cb5486cad5f3a7aab41833dcad3fef05561c421633ae8bd286a325ebe`.
Both serial logs were empty. Neither boot reached the application or startup
service, so no service/journal evidence exists and the app, service, and
no-writer behavior were not observed in a booted guest.

The pinned Archiso 91-1 releng package list and its `mkinitcpio-archiso` package
show that the configured hooks require runtime packages missing from this
profile. The exact locked 2026-09-29 core/extra database hashes were verified
while checking that the required packages exist in the snapshot. The accepted
package correction now pins the base and per-hook package map, includes the
required packages in the profile and records them in build provenance. Its
canonical LF package-list SHA-256 is
`7a7a3fa4127ab6fbaaf952be4bbde82f787a7da30d3111601a9e7fccd6334dbd`.
Focused USB tests pass 105 on Arch (2 skipped) and 94 on Windows (13 skipped);
`mypy usb_builder` is clean on both. No new build retry has run after this
correction. SOL accepted the package policy; a fresh candidate and successful
BIOS/UEFI app/service boot evidence remain pending. No USB media or physical
hardware was accessed.

## Current candidate boot investigation and KMS-only profile derivative

On 2026-10-13, clean source snapshot `d87bc03a6b94bdde86ff3e3ebf89b24df0b4b275`
(`0a433bfc6382c56e24b0b5c2bb766ef3d6ff246a`, clean) produced a candidate ISO
of 1,339,064,320 bytes with SHA-256
`b96f83ba84e67ab7e747c95296319d549b57d167010d1ce98a43be2d45c3e4a5`. Its
build-provenance SHA-256 is
`d1d74d087b8598e3b3ef86973dd2d6f5fa1b22154864ea37e98becbe5da082c3`; the
build-time provenance correctly records QEMU as not performed. This candidate
uses the original pinned Archiso 91 fragment, including `kms`.

QEMU 11.1.2 ran without a network device or firmware-write interface. The
multi-thread BIOS run used `-accel tcg,thread=multi -m 3072 -smp 4` and reached
the Arch live-root shell after mounting the ISO and loop-backed root. It did
not show the AutoLoader UI or service status. Attempts to switch to tty2 showed
no usable console, and serial output was empty. The final VGA PPM is
`C:\Users\Alex\AppData\Local\Temp\librecore-qemu-d87bc03\bios-multi\boot-screen.ppm`
(SHA-256 `4d753bdb2c3b735f40d6f3bf4f015b1e9fc448827857302b2e4216acdf7e9975`).
The run was stopped after its bounded interval; its QEMU process exit does not
mean guest success.

The multi-thread UEFI run used the same ISO with OVMF pflash, `-accel
tcg,thread=multi -m 3072 -smp 4`, and a 300-second bound. It reached early udev
and `archiso_pxe_nbd` hooks, then remained at event triggering without mounting
the live root or reaching userspace. Serial output contained the firmware boot
menu but no guest service/journal evidence. The VGA PPM is
`C:\Users\Alex\AppData\Local\Temp\librecore-qemu-d87bc03\uefi-multi-pflash\boot-screen.ppm`
(SHA-256 `17ec9c24a77850c2da9ed2c0d508be2fd2198f4bb98ed0dbbc7816189e8ee604`).
The ISO hash matched before and after both completed runs. BIOS and UEFI app,
service, journal and no-writer validation therefore remain **FAILED/NOT
EVIDENCED**, not passed. An earlier UEFI command used an invalid `-bios`
argument and exited before boot; it is not counted as a guest test.

SOL accepted the software experiment that adds the separately named and
SHA-256-pinned `zz-autoloader-usb.conf` derivative. It is byte-checked against
the pinned Archiso fragment and removes exactly the `kms` hook. It preserves
all other hook order, all PXE hooks, `COMPRESSION="xz"`, and
`COMPRESSION_OPTIONS=(-9e)`; the package map is unchanged. The builder rechecks
source and staged derivative bytes before and after `mkarchiso` and before
publication, then records both hashes and retained-hook order. Windows focused
validation passed 102 tests with 13 skipped; the derivative-specific subset
passed 6 tests; `mypy usb_builder` and `git diff --check` passed. Candidate b2
below exercised this derivative. Its initramfs composition was inspected, but
the guest did not reach AutoLoader startup. Removing `kms` is a bounded
experiment, not a proven cause of the prior delay. Physical display/keyboard
checks on the three ThinkPads remain separate hardware gates.

## Derivative candidate b2 (2026-10-14)

The temporary candidate was built from clean USB-only snapshot commit
`1197d9040a39d6da012f459b8bb1f45385ee0072`, tree
`d2588ddff4f0c5d4115565f47360314553106d38`. This isolated snapshot did not
include the concurrent X200 firmware-engine changes. The ISO is
1,012,215,808 bytes, SHA-256
`afde8ba28025530852547ec5042dd94ecece5a9b1d24f5da141365861cc35cbd`; the
build-provenance JSON SHA-256 is
`c8ad6f6c1b04ed87626a4410566ed11e5d23893293a8945d4e865c28d63e481f`. The ISO
remains in the local Arch WSL output directory and is not stored in Git. Target
root installation and the packaged CLI runtime check passed. The build
provenance correctly records both QEMU modes as `NOT_PERFORMED` at build time
and overall release validation as `PENDING_LINUX_QEMU_REVIEW`.

The extracted initramfs was inspected at 79 MiB with 2,005 CPIO paths. Its
hooks were `base udev microcode modconf memdisk archiso archiso_loop_mnt
archiso_pxe_common archiso_pxe_nbd archiso_pxe_http archiso_pxe_nfs block
filesystems keyboard`; `kms` was absent. Loop/NBD, USB-storage and virtio
support were present. No GSP firmware or Intel/AMD/NVIDIA display module was
present; `virtio-gpu.ko.zst` and unrelated NVIDIA network/HID modules were
present, and the root squashfs retains `linux-firmware`. The initramfs digest
was abbreviated in the transient inspection notes and was not included in the
build-provenance JSON, so this runbook does not claim a complete retained
digest.

The same ISO hash was used before and after each emulator run:

| Mode | Emulator interval (UTC) | Guest observation | Result |
|---|---|---|---|
| BIOS | 2026-10-13 23:20:38–23:25:48 (310 seconds) | Systemd reached the live system and waited at `systemd-nsresourced.service` / Namespace Resource Manager startup. The app and service did not appear; the second TTY had no login prompt. | `REVIEWED_BOOT_INCOMPLETE`; no app/service pass |
| UEFI | 2026-10-13 23:33:07–23:48:29 (922 seconds), OVMF code SHA-256 `2febd26c0b4cf95a636a941afa37d64a552723443ed9ac72f763f6840da98cb4` | At 300/600/900-second captures, systemd remained in early startup. It repeatedly failed `systemd-nsresourced.service`; the main TTY waited on `/dev` node setup, the second TTY had no login prompt, and serial stopped at the UEFI boot menu. | `REVIEWED_BOOT_INCOMPLETE`; no app/service pass |

Both QEMU processes exited with code 0; this is emulator completion, not guest
boot success. Preserved run records and PNG captures are in
[`docs/v1/evidence/live-usb-b2/`](../docs/v1/evidence/live-usb-b2/), with a
folder-local `SHA256SUMS`. BIOS/UEFI service state, guest journal, TTY login,
and no-writer evidence were not obtained. The Live USB boot gate remains open.

## Same-hash systemd diagnostic (2026-10-14)

The original BIOS/UEFI captures and the baseline direct-kernel BIOS run2 are
preserved in
[`docs/v1/evidence/live-usb-b2-diagnostic-20261014/`](../docs/v1/evidence/live-usb-b2-diagnostic-20261014/).
Every run used the b2 ISO SHA-256
`afde8ba28025530852547ec5042dd94ecece5a9b1d24f5da141365861cc35cbd`; the
direct-kernel record confirms the same hash before and after. The QEMU
emulator was version 11.1.2. The baseline direct-kernel run2 used
`-accel tcg,thread=multi -m 3072 -smp 4 -nic none` and the ISO as CD-ROM. It
extracted kernel and initramfs from that ISO; their SHA-256 values are in the
evidence README and `boot-files.sha256`.

The run2 direct-kernel command included
`archisobasedir=arch archisosearchuuid=2026-10-13-22-51-40-00 console=tty0
console=ttyS0,115200n8 systemd.log_level=debug systemd.log_target=console
systemd.debug_shell=1`. The serial log confirms the Archiso hooks mounted
`/dev/sr0` and `/dev/loop0`, then printed “Welcome to Arch Linux!”. It records
`debug-shell.service` forking `/bin/sh` on `/dev/tty9` and entering running
state. It also records `autoloader.service` receiving a start job that remained
held until `basic.target`. The run did not capture `/proc/cmdline` output, and
no `systemctl is-enabled`, `systemctl status`, `systemctl --failed`, or
`journalctl` result was obtained. The VGA screendumps remained at early
SeaBIOS text, so the attempted tty9 switch and read-only command entry did not
yield usable shell evidence. Service state and cause remain unconfirmed.

The serial log includes systemd preset messages that say
`autoloader.service`, `debug-shell.service`, and
`systemd-nsresourced.service` are disabled by preset. These messages show the
default preset policy; they do not by themselves establish runtime enablement
or explain the incomplete startup. Source inspection confirms the builder
creates `/etc/systemd/system/multi-user.target.d/autoloader.conf` with
`Wants=autoloader.service`, and the target-root customization hook checks that
drop-in. The queued start job is consistent with the explicit Wants edge, so
the preset output is not treated as the root cause and no source change was
made from this evidence.

In run2, the direct-kernel supervisor issued HMP `quit` at its 300-second bound; QEMU
exited with code 0 after 307.5 seconds including shutdown handling. This is an
emulator stop result, not guest success. The earlier BIOS run had an uncaptured
exit code after its bounded stop; the earlier UEFI run exited 0 after HMP quit
at 307.8 seconds. None of the earlier BIOS/UEFI runs or baseline run2 reached
a verified AutoLoader app screen, active-service status, or guest-journal
inspection. The boot and release gates remain open; the later tty9 diagnostic
is recorded below.

### Timed tty9 diagnostics (runs 3–8)

These follow-ups are direct-kernel debugging, not normal BIOS/UEFI boot
acceptance. The preserved commands and raw evidence are in the
`direct-kernel/run3-emergency/` through `run8-vnc/` subdirectories. All use
the same unchanged b2 ISO hash
`afde8ba28025530852547ec5042dd94ecece5a9b1d24f5da141365861cc35cbd`, extracted
kernel hash `8ebc2c71271e000540f0a7545afd8e73504fcb724671f3d38c720b241842b901`,
and initramfs hash
`c7440ebd5164e83e4a28c3f22ba2695af06a0fd29676ae572967ecdb6218e090`. The
fixed QEMU settings were `-accel tcg,thread=multi -m 3072 -smp 4 -nic none`,
with loopback-only VNC and the ISO attached as CD-ROM. No writer or network
device was attached.

Run3 reached emergency mode, where sulogin reported the root account locked;
no commands were entered. Run4 was a VNC-client construction failure before
guest observation. Run5 reached the serial `debug-shell.service` running
marker, but the screen stayed at firmware text. Run6's HMP socket disappeared
at the 300-second bound before its helper could deliver a key. Run7 stopped at
150.1 seconds because the mounted-Windows serial log briefly returned
`OSError: [Errno 61] No data available`; it never reached the marker.

Run8 kept HMP connected from launch and retried transient serial-file reads.
At `2026-10-14T03:14:52Z`, 262.9 seconds after launch, serial reported
`debug-shell.service: Changed dead -> running`. HMP sent `sendkey ctrl-alt-f9`
and the VGA capture showed a tty9 shell prompt. A harmless `echo
lc_tty9_marker` command printed visibly. `cat /proc/cmdline` visibly confirmed
the direct-kernel command line. These records prove the debugging shell was
reachable in this emulator run; they do not prove the normal ISO boot.

At the captured run8 snapshots, `systemctl list-jobs --no-pager` showed 66
jobs, including `systemd-nsresourced.service` running and `basic.target`
waiting. `systemctl --failed --no-pager` returned zero loaded failed units at
that moment. `autoloader.service` was loaded, disabled, inactive (dead), with
Job 174. `systemd-nsresourced.service` was activating and triggered by its
socket. `systemd-remount-fs.service` had succeeded while `basic.target` and
`sysinit.target` were inactive. The journal screenshot shows nsresourced audit
and watchdog messages; the later serial log records its start operation
timing out and termination. This locates the incomplete startup path but does
not establish its root cause. The read-only `systemctl is-active` command did
not produce a retained result. `systemctl default --no-block` was not run.

Run8 was explicitly stopped by the operator at 594.2 seconds; QEMU then exited
0 after SIGTERM and shutdown handling, with the supervisor recording 604.4
seconds total. The ISO hash remained unchanged. No AutoLoader UI or active
service was reached, no root cause was confirmed, and no product image,
profile, or root account was changed. These findings do not satisfy the BIOS
or UEFI boot gate.

The project's earlier real offline-namespace test was **UNVALIDATED** in Arch
WSL. It used bare `unshare --net`; a read-only diagnostic changed the
namespace ID, but shared `/sys/class/net` still exposed `eth0` and raw IPv6
proc data contained the kernel's loopback sentinel rows. The strict probe has
since been revised to require a user-mapped network namespace, independent
namespace-aware iproute2/socket views, exact default rules, and either no
IPv6 proc rows or exactly two copies of the pinned null/unreachable sentinel.
The current production result and the separate Ubuntu inventory are recorded
under `docs/v1/evidence/offline-namespace-arch-wsl-20261015/` and
`docs/v1/evidence/offline-namespace-ubuntu-wsl-20261015/`. No network traffic
was sent during either probe.

The production isolation gate's network probe passed in Arch WSL on
2026-10-15. The exact canonical `/usr/bin/unshare` and `/usr/bin/ip`
executables were verified by pacman as `util-linux 2.42.4-1` and `iproute2
7.2.0-1`; the child had a different namespace ID and reported loopback only,
zero IPv4/IPv6 netlink routes, expected rules, and two exact IPv6 sentinels.
Historical pre-errno-correction run: the integrated preflight reported
`ARCHISO_MOUNT_CAPABILITY_UNAVAILABLE` under a broad OSError classifier; its
record is retained in
`docs/v1/evidence/offline-namespace-arch-wsl-20261015/production-mount-preflight-20261015.txt`.
The corrected production check initially returned
`ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE` because the probe passed generic
VFS options (`nosuid,nodev`) as tmpfs filesystem data, which yielded EINVAL.
A disposable same-namespace comparison confirmed the plan defect: the old
syscall failed, while split mount flags and the exact util-linux
`mount(8)` options succeeded. The probe plan now maps VFS controls to
`MS_NOSUID`, `MS_NODEV`, `MS_NOEXEC`, and `MS_STRICTATIME`, leaving only
filesystem-specific `mode`/`gid` values in data. The current direct production
preflight now reports `ARCHISO_MOUNT_CAPABILITY_UNAVAILABLE` for `devtmpfs`;
the route probe passes and host mountinfo is unchanged. It still stops before
builder work/output. See
`docs/v1/evidence/offline-namespace-arch-wsl-20261015/mount-errno-classification-20261015.txt`
and `tmpfs-option-split-diagnostic-20261015.txt`. Native Linux behavior and
the offline build remain pending.

Mount syscall errors are classified narrowly. `EPERM` and `EACCES` are
permission denials; `ENODEV` (filesystem type unavailable) and `EOPNOTSUPP`
(filesystem-specific unsupported operation) count only for operations that
mount an explicit filesystem type. Other errors, including `ENOENT`, `EFAULT`,
`EBADF`, `EIO` and `EINVAL`, are integrity failures and cannot be reported as
an unsupported host. Linux `mount(2)` documents `EINVAL` for bad flags/targets
and for certain unprivileged nonrecursive binds that would expose hidden
submounts. This probe supplies fixed flags and uses its own validated temporary
tree with no source submounts, so `EINVAL` indicates an unexpected condition.
The child report validator enforces the same operation-aware allowlist. See
the [Linux `mount(2)` manual](https://man7.org/linux/man-pages/man2/mount.2.html).
These errno regressions are synthetic tests; the archived WSL production
exception did not include raw errno values.

After the tmpfs argument-plan correction, focused USB tests passed 259/1
skipped on Arch WSL and 244/16 on Windows. Native Arch and Win32-targeted
`mypy autoloader usb_builder` passed on 104 source files. The production
preflight regression separately captures the route-free child report and
unchanged host mount snapshots. The direct production preflight now reports
`ARCHISO_MOUNT_CAPABILITY_UNAVAILABLE` for `devtmpfs` only. These checks
validate behavior under WSL; native Linux mount behavior and a successful
offline builder run remain pending. The complete test suite has not yet been
rerun after this correction.

The guarded subprocess closes inherited descriptors, sets stdin to `DEVNULL`,
forces stderr to `STDOUT`, and accepts stdout only as `PIPE` or an already-open
regular log file verified with `fstat`. Sockets, socket-backed streams, raw
file descriptors, devices, FIFOs, caller stdin overrides and other subprocess
controls are rejected before a child starts. On timeout the wrapper terminates
the child process group and sends SIGKILL after the grace period even if its
leader exits first. A descendant that calls `setsid()` can escape that process
group; it remains in the restricted network namespace but could outlive the
wrapper and continue local filesystem activity. This residual prevents claiming
complete process-tree cleanup.

### CRLF profile script build failure (2026-10-15)

A clean temporary source snapshot reached `mkarchiso` and failed with
`profiledef.sh: line 3: $'<CR>': command not found`; mkarchiso exited 127.
`<CR>` denotes the carriage-return byte present in the profile script. No
candidate image from that attempt was accepted or published. The checked-out
profile also had CRLF in its systemd service, startup shell script and pacman
configuration. Repository attributes pin the known Archiso text inputs and
package list to LF; current text inputs are normalized to LF, and preflight plus staged-profile checks
reject carriage returns before and after mkarchiso and at publication. Focused
tests cover both source CRLF rejection and a staged script changed to CRLF by
the fake builder. The safeguards and focused gates are complete; a new offline
build attempt is required, and no successful image build is claimed.

### WSL private-mount namespace capability probe (2026-10-15)

To diagnose whether rootless Archiso setup could proceed without weakening the
network boundary, the probe used the production user-mapped flags plus private
mount/PID namespaces and a private proc mount:
`unshare --user --map-root-user --net --mount --pid --fork --mount-proc`.
The child had distinct PID, mount and network namespace IDs; namespace-aware
`ip -j link` showed only `lo`. In that namespace, mounting `proc`, `sysfs` and
`tmpfs` at temporary test paths succeeded. Mounting `devtmpfs` was denied;
binding the host `/dev` and `/run` failed; `devpts` with Archiso's `gid=5`
failed because that GID is unavailable in the mapped namespace. The temporary
mount test used no builder and its owned directories were removed after the
namespace exited. Exact output is in
[`mount-probe-20261015.txt`](../docs/v1/evidence/offline-namespace-arch-wsl-20261015/mount-probe-20261015.txt).

This resolves the earlier `/proc` mount limitation but does not provide the
device and runtime mounts required by `arch-chroot`/`pacstrap`; therefore no
offline candidate build was attempted. The WSL production network-isolation
probe and focused tests still pass, but native Linux build support and a real
offline build remain pending. No fallback to bare-root `unshare --net` was
added.

## Post-build package inventory guard

Before candidate publication, the builder validates the target root and its `var/lib/pacman/local` database as contained, non-symlink directories and requires package records with regular `desc` files. It then runs the fixed query `pacman --config /dev/null --dbpath <airootfs>/var/lib/pacman -Q`, using merged stdout/stderr and the strict inventory parser. This uses only the image's local package database; it does not read the host pacman configuration or refresh a repository. Missing or unsafe database paths, a nonzero pacman exit, warnings, malformed or duplicate rows, or missing required packages block the build before atomic candidate publication. The fake-only regression suite passed 79 tests with 2 skipped; `mypy usb_builder` and `git diff --check` passed. SOL accepted this inventory correction. No ISO build or retry was run at that earlier checkpoint.

## Runtime storage and logs

The UI is assigned to tty1. It runs from a local wheel in an isolated virtual
environment with the exact Python dependencies installed from the pinned
profile package set. Startup failures remain visible on the console and in
`journalctl -u autoloader.service`; no root shell or manual flashrom fallback is
provided. The service does not restart-loop.

The live root overlay is volatile. Any backup requires two separate, mounted,
persistent storage volumes, and the application must pass its existing
capacity, filesystem, write/readback and distinct-volume checks. The Live USB
is not a backup destination. No physical writes, restores, post-write reads,
recovery actions, or media writes are enabled by this profile.

### Direct-kernel retries (runs 9–12; 2026-10-15)

The exact commands, serial/HMP logs, screenshots, launcher records, and before/after hashes are preserved under `docs/v1/evidence/live-usb-b2-diagnostic-20261014/direct-kernel/run9-vnc/` through `run12-vnc/`. All runs used the same unchanged b2 ISO hash `afde8ba28025530852547ec5042dd94ecece5a9b1d24f5da141365861cc35cbd`, kernel hash `8ebc2c71271e000540f0a7545afd8e73504fcb724671f3d38c720b241842b901`, and initramfs hash `c7440ebd5164e83e4a28c3f22ba2695af06a0fd29676ae572967ecdb6218e090`. QEMU 11.1.2 ran with `-accel tcg,thread=multi -m 3072 -smp 4 -nic none`; loopback VNC was used and no firmware writer was attached.

Run9 requested its first frames only seconds after Ctrl+Alt+F9 and sent an echo before visually confirming the complete frame. The screen showed early SeaBIOS/TDX text, not a prompt; no status query was sent. Run10 later captured complete frames that visibly showed `sh-5.3#`, but at about 362 seconds the short acknowledgement-file window expired before its launcher consumed the review decisions. Cleanup finished at 383.4 seconds. No guest text or status/journal command ran. Run11 reached `debug-shell.service` running in serial at 256.4s and sent the chord, then failed in its manual input gate with `NameError: sys is not defined` before any post-key frame or guest text.

Run12 fixed the launcher and kept the 600-second hard cap independent of manual review. At 249.1s serial reported the debug-shell marker and HMP sent Ctrl+Alt+F9. Twenty-one complete VGA frames through 565.3s showed SeaBIOS/TDX early-startup text, with no visible tty9 prompt. One repeat chord was sent through a second local HMP connection; the client got no reply within 3s, and later frames did not change. No guest text, `systemctl`, journal, or service-status command was sent. The launcher ended at T+575 after no visible prompt was confirmed and stopped QEMU; its exit code 0 only records emulator shutdown. Neither run11 nor run12 reached a guest status/session, and none of runs9–12 proves app/service startup or root cause. All four are emulator diagnostics only; the normal BIOS/UEFI acceptance gate remains open.

### Stock Arch Linux control (2026-10-15)

To check whether the startup stall was unique to b2, the official Arch Linux 2026.10.01 x86_64 ISO was booted under the same Arch WSL2/QEMU 11.1.2 TCG environment. The release ISO SHA-256 was verified as `684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5` before and after each run. Its extracted kernel SHA-256 (`8ebc2c71271e000540f0a7545afd8e73504fcb724671f3d38c720b241842b901`) matches the b2 kernel; the stock initramfs SHA-256 is `35b4362207fcabc66535444aea0b97f3508128b32e97e66aa5f56487007bfe52`. Exact commands, per-run results, captures and hash manifests are under `docs/v1/evidence/live-usb-b2-diagnostic-20261014/stock-arch-comparison/`; see its README for the full evidence map.

The stock normal BIOS run used `-accel tcg,thread=multi -m 3072 -smp 4`, no guest input, no NIC, no writable disk, and no firmware writer. It ran for the 600-second cap, reached systemd 262-1-arch, and displayed the same Namespace Resource Manager and static `/dev` node startup stalls. It did not reach a login prompt.

A separate direct-kernel run used the exact stock kernel/initramfs and a visually gated tty9 shell. After a harmless echo was visibly confirmed, one serialized read-only command returned to the shell prompt. The captured `systemd-nsresourced.service` was activating as job 207 after about 40 seconds, with 38.267 seconds of CPU time, and its journal had no entries; `systemctl --failed` showed zero failed units at that point. A different longer run's serial log records the same unit timing out and being killed. Later screenshots from that longer run have unreliable command/file associations because its launcher sent further commands before the prompt returned, so they are excluded from analysis. No root cause has been established.

The host exposes neither `/dev/kvm` nor `/dev/mshv`; QEMU's MSHV accelerator probe fails because the device node is unavailable. Thus this control weakens a b2-only profile explanation but does not distinguish guest/systemd behavior from the WSL/TCG environment. A native Linux/KVM control is still required. The Live USB BIOS/UEFI app/service and offline-isolation gates remain open. No product unit or profile was changed from this evidence.

### Rootless Archiso host preflight implementation checkpoint (2026-10-16)

The production builder now requires a non-root caller with matching real and
effective identity, a private primary group, and no supplementary groups other
than that primary GID. The process must have empty effective, permitted and
ambient capability sets; `CAP_SYS_ADMIN` and `CAP_NET_ADMIN` must be absent
from its bounding set, while `CAP_SETUID` and `CAP_SETGID` remain available to
the pinned `-N` helper chain. `NoNewPrivs` must remain clear pending an exact
`pacstrap -N`/`arch-chroot -N` proof. Both subuid and subgid files must provide
one non-overlapping range of at least 65,536 IDs for the invoking UID.

The helper contract is bound to Archiso 91-1, arch-install-scripts 31-2,
shadow 4.20.0.arch1-1 and libcap 2.78-1. For the pinned shadow archive,
`newuidmap` is a root-owned mode-0755 file with exactly
`cap_setuid=ep`, and `newgidmap` is root-owned mode-0755 with exactly
`cap_setgid=ep`; both binary hashes, package ownership and the `getcap`
binary/hash/owner are pinned. The signed shadow and libcap package archives,
signature hashes and helper hashes are recorded in `snapshot.lock.json`.

The read-only helper/package provenance was verified against the locked
2026-09-29 core database. The Arch WSL distro cannot satisfy the production
preflight: it runs as UID 0, has no delegated subuid/subgid mappings, and its
map helpers have no file capabilities. Production `execute_build` rejected
root before command or output/work creation at that earlier checkpoint. No
online or offline build mode was enabled then. The later non-root `-N` mount
and online execution integration is described below. No package install,
`mkarchiso`, candidate build, hardware, or media operation was performed for
that historical checkpoint.

### Pacstrap status-propagation correction (2026-10-16)

The disposable Arch VM subsequently proved the pinned `arch-install-scripts
31-2` `pacstrap -N` script can log a package-install error yet exit 0 because
the script runs cleanup without preserving the child status. Exact evidence
and the exit-7 control are linked at
[`namespace-and-pacstrap-probes.txt`](../docs/v1/evidence/rootless-archiso-vm-20261016/namespace-and-pacstrap-probes.txt).

The implementation now carries a hash-pinned, operation-owned patch resource
that saves the child status before cleanup, returns cleanup failures, and
preserves the child status after successful cleanup. Staging verifies the
original system file and exact patch/result hashes, writes only inside a
private token-marked operation directory, checks owner/mode and staged bytes,
and records the future mkarchiso PATH and argv in provenance. The system
`/usr/bin/pacstrap` is never modified. SOL accepted the overlay and its failure
propagation checks. The current source wires it into the rootless online
`execute_build` route after the pinned host and mount preflights; offline mode
remains fail-closed until a compatible no-egress launcher is reviewed. A
VM-backed host preflight had stopped at `setpriv` because the previous
contract reused one unrelated SHA-256 for `setpriv`, `unshare`, `mount`, and
`umount`. The pinned util-linux 2.42.4-1 VM package and each executable now
have separate locked hashes, with exact-value and per-tool mismatch tests.
Focused rootless/pacstrap checks passed 30 tests with 277 deselected on both
Arch WSL and Windows; Windows mypy is clean on four USB-builder source files.
This corrects the tool identity pin but does not validate the complete rootless
chain or enable builds. No build, ISO, media, or hardware operation occurred.

### Online candidate readiness check (2026-10-15)

The post-tmpfs-correction production mount-capability preflight was invoked
directly from the current shared checkout. It returned
`ARCHISO_MOUNT_CAPABILITY_UNAVAILABLE` for `devtmpfs`. This preflight is
mandatory for both online and offline `execute_build` modes, before work/output
creation. The candidate builder was not launched at that earlier checkpoint;
there was no repository sync/download, package installation, `mkarchiso`
invocation, or new ISO. Exact
command and output are in
`docs/v1/evidence/offline-namespace-arch-wsl-20261015/online-candidate-readiness-preflight-20261015.txt`.
This is Arch WSL evidence only, not native Linux validation. Native Linux
build, offline ISO, and successful BIOS/UEFI app/service boot remain pending.

### Same-hash nsresourced mask experiment (2026-10-15)

A bounded direct-kernel QEMU TCG run used the unchanged b2 ISO, kernel and initramfs and added only `systemd.mask=systemd-nsresourced.service` and `systemd.mask=systemd-nsresourced.socket`. Serial confirms both units were masked. It still showed `sysinit.target` waiting on `systemd-tmpfiles-setup-dev.service` at the 270-second checkpoint. The debug-shell service started on tty9, but no guest command was entered; no app/service readiness, normal boot pass or root cause was established. QEMU 11.1.2 ran for 282.40 seconds and exited 0 after HMP quit; its controller reported a monitor reset while reading the quit response after QEMU stopped. ISO/kernel/initramfs hashes were unchanged. This is oriented emulator evidence, not a release pass. Exact command, logs, frames, result and checksums are in `docs/v1/evidence/live-usb-b2-diagnostic-20261014/nsresourced-mask-20261015/`.

Disposition: this experiment did not establish that masking either unit is necessary or safe. The default profile and target-root checks leave the pinned systemd service/socket behavior unchanged; only the AutoLoader `multi-user.target.wants` link is required for image acceptance.

### Rootless Archiso `-N` mount-stack preflight implementation — 2026-10-16

The earlier devtmpfs denial was from a root-oriented probe. A separate
preflight now mirrors the pinned `arch-install-scripts 31-2`
`arch-chroot -N` stack: private bind of the target root, proc, recursive
`/sys`, tmpfs `/dev`, six individually validated device binds (`full`,
`null`, `random`, `tty`, `urandom`, `zero`), and tmpfs `/run` and `/tmp`.
The child verifies fresh user/network/mount/PID namespace identities and a
route-free network before and after mounting, checks exact device types and
numbers, then unmounts and proves its mount table is restored. The parent
requires byte-identical host mountinfo before and after.

The rootless mount probe and execution integration now have 70 focused tests
passing (269 deselected) in Ubuntu 26.04 WSL. The production online path
performs pinned host/helper checks and the exact isolated route-free `-N`
mount preflight before creating build work or output, then runs as the same
non-root caller with a fixed environment and operation-owned pacstrap status
overlay. Egress is available to online package/build commands only after the
isolated probes pass. A separate focused route selection passed 7 tests
(332 deselected); native and Windows-targeted `mypy usb_builder` each passed
on 5 source files.

Offline mode remains fail-closed before work/output because there is no
reviewed route-isolation launcher compatible with the non-root Archiso `-N`
contract. No current-source package install or `mkarchiso` candidate build was
run in this implementation slice; the parent is handling the disposable-VM
build gate. The existing b2 ISO and its incomplete BIOS/UEFI app/service boot
evidence remain historical and unchanged. WSL results are not native Linux
validation, and all three hardware workflows remain pending.

A clean Arch WSL2 recapture at 2026-10-16T17:25:57Z established only that an
ephemeral root-created network namespace could be made route-free and then
dropped to UID/GID 65534 with no capabilities. The raw output records host
`net:[4026531833]` and child `net:[4026532308]`, with only a down loopback,
no addresses/routes, default policy rules, zero capability sets, and
`NoNewPrivs=1`. It did not exercise a fixed root-owned launcher, the
dedicated `fwbuild` account/subuid mapping, pinned Archiso `-N` commands,
descriptor or timeout controls, or an ISO build. Offline production execution
remains blocked; this primitive check is not launcher evidence or acceptance.
See the exact PowerShell `$probe` input, CR-stripping WSL wrapper, raw output,
limitations, and SHA-256 records at
[`namespace primitive diagnostic`](../docs/v1/evidence/rootless-archiso-namespace-primitive-20261016/namespace-primitive-probe-20261016.txt).

## Offline rootless launcher status

The no-build launcher preflight contract is implemented and called by the
offline path after the existing trusted-host and Archiso `-N` mount checks,
before build work or output creation. Synthetic tests validate the strict
report contract, including the full host namespace map, a distinct child
namespace for each namespace type, and network views bound to the reported
child network namespace. They cover rejection cases only. There is no reviewed
root-owned launcher path/hash/authorization pin deployed, so the production
pin remains absent and offline mode returns
`OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE` before launcher-specific scratch/child
creation and before builder work/output. The existing trusted-host and mount
preflights, including their child processes, run first. No live launcher probe
or pacstrap package action was performed in this implementation slice.
`pacstrap -N --help` alone would not prove operational
compatibility; controlled disposable-VM package-input evidence is still
required. Status: **CONTRACT IMPLEMENTED;
DEPLOYMENT/COMPATIBILITY UNVALIDATED**.

SOL accepted the separate production execution guard. A synthetic
`SUPPORTED` response in the no-build test still stops with
`OFFLINE_ROOTLESS_BUILD_RUNNER_UNAVAILABLE` before source/work/output
creation, package/build commands, or online-runner selection. In production,
earlier host or mount preflights may stop the operation first; if they pass,
the absent launcher pin stops it with
`OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE`. The synthetic response does not
validate a deployed launcher or `pacstrap -N`; offline build support remains
unavailable and unvalidated. This build-time status is separate from the
runtime network profile below, which has not been validated in a booted ISO.

The future launcher identity requires exact root-owned setuid mode `04755`,
with every parent directory root-owned and not writable by group or other.
Its scratch parent must be a root-owned, non-link directory of exact mode
`01777` (the protected system temporary directory); the fresh operation-owned
scratch child is checked as caller-owned mode `0700`. This does not validate an
ISO build, boot, ROM, or hardware workflow.

SOL accepted the contract. The independent Arch Linux WSL2 focused selection
passed **53 tests in 3.15s**, and mypy passed on the two touched source files.
The current-source Arch WSL2 non-root full suite passed **809 tests, 2
Windows-specific skips, 0 failures/errors** (811 collected; 1771.18s; exit
0). This WSL result does not establish a deployed launcher or operational
`pacstrap -N` package-input compatibility; offline execution remains
unavailable until those gates are separately proved. The JUnit report and
exact command are recorded in
`docs/v1/evidence/software-regression-20261016/README.md`.

## Live USB runtime network defaults — software-only update (2026-10-20)

The source profile now ends both BIOS Syslinux and UEFI systemd-boot kernel
argument lines with the sole `ip=off` argument. The image customization hook
masks an explicit list of network activation services/sockets by linking each
unit path to `/dev/null`. The AutoLoader service runs with `PrivateNetwork=yes`,
`RestrictAddressFamilies=AF_UNIX`, `NoNewPrivileges=yes`, and a capability
bounding set that excludes `CAP_SYS_ADMIN` and `CAP_NET_ADMIN`. The service's
process tree is intended to remain isolated, while local console and USB
workflows are intended to remain available; none of these runtime properties
has been validated in a booted candidate image yet.

Source preflight requires the exact boot arguments, mask-unit list and masking
loop. Target-root publication requires the exact reviewed AutoLoader unit
bytes, rejects every unreviewed `.conf` drop-in in the systemd unit and
`system.control` search paths, rejects higher-priority complete-unit files and
symlinked override paths, and on Linux verifies every expected mask link
points to `/dev/null`. The candidate manifest records the policy and link
checks. Focused Ubuntu WSL tests passed **21 cases** (350 deselected); an
independent Arch WSL selection passed **15** (356 deselected). Native and
Win32-targeted mypy passed for `build_usb.py`, and SOL accepted this
software-only slice. The full regression suite has not been rerun after these
runtime changes.

This change has not been used to build or boot an ISO, so runtime behavior is
unvalidated. It does not implement the build-time `--network-mode offline`
launcher: that path still fails closed with
`OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE`. Native Linux build, BIOS/UEFI
AutoLoader/service boot, and hardware validation remain pending.
