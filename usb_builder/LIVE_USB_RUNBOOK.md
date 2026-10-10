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
first-run setup is complete; the shell responds as `uid=1000(alex)`. Namespace,
Archiso, ISO, and QEMU evidence is still pending. An Ubuntu WSL2 shell by itself
is not the pinned Archiso 91-1 build environment: the builder still
requires the exact Arch package/tool ownership, pacman repositories and
snapshot-signature validation, plus usable mount, loop, chroot, and network
namespace capabilities. This checkout is currently dirty, so do not start a
candidate build until a reviewed local commit provides a clean Git checkout.
The offline network-namespace probe, real `mkarchiso` build, post-build
`chroot` checks, BIOS/UEFI QEMU boot, and ISO production remain
**NOT_PERFORMED**. No candidate ISO or Linux validation result is claimed.

Latest focused software checks are Arch WSL `tests/test_usb_builder.py` — 60
passed, 2 skipped; Windows — 57 passed, 5 skipped; `mypy usb_builder` and
`git diff --check` passed. The real Arch WSL namespace probe is
**UNVALIDATED** because `unshare` still exposes `eth0`; offline isolation fails
closed, and these focused software tests do not constitute offline builder
validation. Online candidate build and BIOS/UEFI QEMU validation remain
pending.

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
