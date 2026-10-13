# Live USB build and operation runbook

This profile is an in-development Archiso environment. The checked-in package
snapshot and package list are fixed for **candidate** builds; release validation
remains pending Linux build review and BIOS/UEFI QEMU boot evidence. Fake tests
cannot advance either status.

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
Before creating the build work directory, offline mode probes a new network
namespace and requires only loopback with no IPv4/IPv6 routes. Root builders use
`unshare --net`; unprivileged builders use
`unshare --user --map-root-user --net`. Every later build subprocess, including
`mkarchiso` and its children, inherits the namespace command prefix. If the
namespace cannot be established, the builder stops before creating work or
output. If the repository, snapshot binding, signatures, or package inputs are
absent, the build also stops before `mkarchiso`.

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
BIOS/UEFI QEMU boot, and ISO production were **NOT_PERFORMED**. The offline
network-namespace probe remains **UNVALIDATED** because `unshare` exposes
`eth0`. The later temporary ISO and its failed QEMU results are recorded
below; no passing boot or release validation is claimed.

Historical resolver-focused checks were Arch WSL `tests/test_usb_builder.py` —
68 passed, 2 skipped; Windows — 60 passed, 10 skipped. The latest staged
initramfs-fragment guard passed 89 Arch tests with 2 skipped; Windows focused
rerun is pending. `mypy usb_builder` is clean on the Arch run, and
`git diff --check` passed. The real Arch WSL namespace probe is
**UNVALIDATED** because `unshare` still exposes `eth0`; offline isolation fails
closed, and these focused software tests do not constitute offline builder
validation. Online candidate build and BIOS/UEFI QEMU validation remain
pending. SOL approved the target-root POSIX symlink resolver and the later
staged initramfs-fragment integrity guard. The statement that the fragment had
not been exercised was true at that earlier checkpoint; the later candidate
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

## Latest candidate build and QEMU boot failure

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
