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

Online mode points pacman only at the dated official archive. Offline mode
requires an explicit local repository plus a manifest that enumerates every
file and SHA-256, is bound to the checked-in snapshot lock, and includes signed
core/extra package databases. Pacman signature verification remains required.
If those inputs are absent, the build stops before `mkarchiso`.

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
command is not a boot test or firmware artifact acceptance. The build tool does
not mutate `release_validation_status` in the lock.

The host package inventory is recorded for audit, but only Archiso 91-1 and the
owned `mkarchiso` executable digest are enforced by this lock; the other host
package versions are not pinned. Exact host-environment reproducibility and
BIOS/UEFI boot validation remain release gates. This development run was on
Windows, so it produced no ISO.

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
