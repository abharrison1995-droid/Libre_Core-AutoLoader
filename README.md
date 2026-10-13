# Libre_Core AutoLoader

Libre_Core AutoLoader is an in-development Python application for preparing and documenting ThinkPad firmware workflows. The CLI and Textual interface share a model-specific controller for the three V1 profiles. Explicit X200/X220 preparation can inspect local identity and SPI evidence; backup actions perform full-chip reads, and build actions invoke only the pinned artifact engine. The guarded CLI and dedicated mounted-screen TUI write routes reopen staged X200/X220 evidence, require fresh evidence and one-use exact-action human approval, and perform a separate full-chip read-back. The TUI challenge is bound to its active screen and can be cancelled until the service consumes the receipt. Both write routes are unavailable on this Windows development host because durable directory fsync is unsupported. Ubuntu 26.04 WSL is now available for Linux build validation, but no firmware compilation has been run. Restore and boot validation remain unavailable. No hardware workflow has been validated on a real device.

## V1 reference workflows

| Device | Planned pathway | Current status |
|---|---|---|
| ThinkPad T430 | 1vyrain, with an official IVprep handoff if the observed BIOS is incompatible | CLI/TUI submit human handoff approvals and fresh return attestations; hardware validation is pending |
| ThinkPad X200 | Libreboot with external SPI programming | CLI/TUI route preparation, verified backup, pinned build/artifact acceptance and safe resume; Linux build and hardware validation are pending |
| ThinkPad X220 | Coreboot with external SPI programming | CLI/TUI route preparation, verified backup, backup-derived blobs, pinned build/artifact acceptance and safe resume; Linux build and hardware validation are pending |

These are development targets, not a claim of supported or safe flashing. Other model entries in the source database are not validated V1 compatibility claims.

## Install

Python 3.11 or newer is required.

```powershell
git clone https://github.com/abharrison1995-droid/Libre_Core-AutoLoader.git
cd Libre_Core-AutoLoader
python -m pip install -e ".[dev]"
```

## Commands

```text
autoloader --help
autoloader run
autoloader detect
autoloader status --profile x220-coreboot
autoloader import-evidence --profile x220-coreboot --file device.json --save-to candidate.json
autoloader prepare-workflow --profile x220-coreboot --checkpoint C:\firmware-work\x220.json --request setup.json
autoloader backup --profile x220-coreboot --checkpoint C:\firmware-work\x220.json --request setup.json
autoloader build-rom --profile x220-coreboot --checkpoint C:\firmware-work\x220.json --request setup.json
autoloader accept-completed-artifact --profile x220-coreboot --checkpoint C:\firmware-work\x220.json --request setup.json
autoloader verify --profile x220-coreboot
autoloader flash --profile x220-coreboot --checkpoint C:\firmware-work\x220.json --request setup.json
autoloader inspect-checkpoint --checkpoint path/to/workflow-journal.json
autoloader resume-checkpoint --profile x220-coreboot --checkpoint C:\firmware-work\x220.json --request setup.json
autoloader t430-stage --request handoff-stage.json --checkpoint C:\firmware-work\t430.json
autoloader resume-checkpoint --profile t430-1vyrain --checkpoint C:\firmware-work\t430.json
autoloader usb --output-dir ./iso_build
autoloader build-usb --source-root /work/Libre_Core-AutoLoader --profile-dir /work/Libre_Core-AutoLoader/usb_builder/archiso_profile --snapshot-lock /work/Libre_Core-AutoLoader/usb_builder/snapshot.lock.json --output-dir /work/iso-output --work-parent /work/iso-work --network-mode online
```

- `run` opens the Textual interface. Workflow choices and displayed next actions come from the shared controller. Explicit preparation, backup, and build actions use their reviewed backend services. The X200/X220 FLASH action opens a dedicated mounted-screen challenge; this route is software-only and is blocked on this Windows host by the durable approval fsync gate. Restore and boot actions remain unavailable.
- `detect` explicitly captures read-only local DMI/BIOS evidence; startup does not automatically inspect hardware. Detection alone is not proof of compatibility.
- `status` displays controller status for one of the three V1 profiles. Imported evidence remains an unauthenticated candidate and cannot by itself grant compatibility or workflow readiness.
- `import-evidence` accepts typed raw `DeviceEvidence` JSON and saves a candidate to a new path. Existing paths are not overwritten. Its checksum detects accidental corruption; it does not authenticate the evidence or prove that it came from the device.
- `prepare-workflow` requires a new absolute checkpoint path and a versioned operator request. On an explicit X200/X220 request it captures local DMI/BIOS and a read-only SPI probe; X220 also reads the original chip to inspect the pinned IFD map. These are explicit local hardware-read actions. The request's voltage, wiring, and storage values are human-reported evidence with actor, UTC time, instrument, and test point; they are not independently authenticated by the application. Candidate-evidence import cannot set observed chip or programmer fields.
- `backup` (and its `accept-verified-backup` alias) captures two full-chip reads and stores them on the separately selected persistent destinations, or reopens and revalidates an existing verified backup. This is an explicit physical read action when run on a connected device.
- `build-rom` routes through the shared controller and invokes only the pinned X200/X220 recipe after revalidating the saved intent and backup. X220 passes descriptor, ME, and GbE blobs sliced from the verified original backup. A supported Linux build environment is required; a native Windows invocation returns `LINUX_BUILD_ENV_REQUIRED` before starting a build command or creating output. Ubuntu 26.04 WSL is available, and the pinned Libreboot source checkout matches its commit/tree; the pinned dependency list was installed except for `g++-mipsel-linux-gnu`, which is not needed by the x86 X200 target. No actual ROM compile has yet been attempted. Passing software tests does not establish that an upstream build has succeeded.
- `accept-completed-artifact` reopens the persisted manifest and selected artifact, revalidates current local evidence, backup files, pinned source/tool records, and model-specific structure checks, then stages only `STAGED/HARDWARE_PENDING`.
- `verify` reports read-back as `NOT_PERFORMED`; it does not probe or read physical flash.
- `flash` accepts only an exact staged X200/X220 checkpoint and current versioned request; arbitrary `--rom` paths are not supported. It requires a real OS TTY, collects live meter/wiring attestations and asks for the exact operation challenge. The controller reopens the pinned artifact and complete backup, then repeats local DMI/SPI checks before the one-shot writer. Independent full-chip read-back is required before `WRITE_VERIFIED`; timeout, nonzero exit or a mismatch is `UNCERTAIN` and never retries. Windows is blocked because the durable approval ledger requires directory fsync. T430 remains a separate IVprep/1vyrain human handoff, and restore is disabled.
- `inspect-checkpoint` validates and summarizes an operation journal read-only. X200/X220 `resume-checkpoint` requires a fresh versioned request, locally re-detects/re-probes the device, reopens the exact backup/artifact evidence, and resumes only the safe staged software state. T430 `t430-stage` and `resume-checkpoint` present human-run IVprep/1vyrain instructions and accept fresh return attestations; neither downloads nor executes upstream code.
- The Textual interface uses the same controller and presents the same explicit preparation, backup, build, artifact, resume, and T430 stage actions. It does not automatically probe hardware at startup. Its X200/X220 write route uses a dedicated mounted-screen challenge and live setup form; the screen binds approval to its mount and challenge, and blocks navigation while the service is active. Workflow status refreshes from the controller when returning from an action screen.
- `usb` creates an output directory only; it does not build or write an ISO. `build-usb` invokes the shared Archiso build contract and requires explicit absolute source/profile/lock/output/work paths and `--network-mode online|offline`. Offline mode also requires `--offline-repository` and `--offline-manifest`. It refuses non-Linux hosts and never writes removable media.

## Live USB development

SOL accepted the Phase 09 corrective builder review after publication fault tests, exact archive redirect checks, and explicit pacman database-consumption evidence were added. A temporary pre-initramfs ISO was produced, but both QEMU boot attempts failed before app startup; no passing boot validation exists. After `mkarchiso`, the builder checks the image target root for the application venv interpreter, installed package and entrypoint metadata, required runtime imports, the startup script, systemd unit and enablement, customize-hook marker, and read-only CLI/import smoke results before publishing any output. Provenance records source/profile hashes, package snapshot, host package inventory, Archiso executable identity, pip/setuptools/wheel versions, installed image package inventory, target-root evidence, database-consumption evidence and log hashes. The app venv uses `--system-site-packages` and relies on runtime dependencies installed from the locked Arch package set. Pacman must verify package signatures; repository database detached signatures are optional only when absent from the dated archive, while exact `core.db` and `extra.db` SHA-256 pins are always required and any present signatures must verify. Offline repositories must match a complete local file/hash manifest, and offline subprocesses are run only after a route-free network namespace has been established. The invoking Python environment must already contain pip, setuptools 61.0 or newer, and wheel; the builder does not install host tools. Wheel creation uses a package-only temporary source copy and leaves the checkout unchanged. The ISO, logs, provenance and `SHA256SUMS` are published together inside `candidate-bundle` through one atomic directory rename. The builder records QEMU as `NOT_PERFORMED` in build-time provenance; separate emulator checks are required before release.

The package source is fixed to the official 2026-09-29 archive, Archiso 91-1, its reviewed source commit, exact repository database hashes and a SHA-256-pinned package list. The builder records the complete host package inventory, but versions other than Archiso and the `mkarchiso` executable are not pinned; exact host-environment reproducibility remains a release gate. Offline isolation uses `unshare --net` for root and `unshare --user --map-root-user --net` for an unprivileged builder, verifies that the new namespace has only loopback and no IPv4/IPv6 routes, then applies that command prefix to all build subprocesses. Earlier resolver-only focused verification: Arch WSL `tests/test_usb_builder.py` passed 68 tests with 2 skipped; Windows passed 60 with 10 skipped; `mypy usb_builder` and `git diff --check` passed. The real Arch WSL namespace probe is **UNVALIDATED** because `unshare` still exposes `eth0`, so offline isolation fails closed and no offline builder validation has occurred. The first real online candidate attempt stopped before `mkarchiso` because the package-list pin used CRLF-converted bytes instead of the checked-in LF content; the lock now pins the canonical LF hash and validation normalizes CRLF to LF; SOL accepted this correction in commit 3a146f8. The second attempt passed this check but stopped before `mkarchiso` on the symlinked `/usr/sbin/mkarchiso` path described below. The third attempt reached `mkarchiso` but failed BIOS profile validation before package installation because `syslinux` was missing; the profile now includes it. A fourth online attempt (2026-10-13) passed package validation and completed the Archiso build stages, then failed target-root validation because `/usr/sbin` inside the image is a usrmerge link to `/usr/bin`. The output directory remained empty and no ISO was published. The image-rooted resolver was accepted. A pre-fragment ISO failed both QEMU modes. The later candidate and its failed boot evidence are recorded below. The signature-verified Archiso 91-1 package is installed under Arch WSL; `pacman -Q archiso` reports 91-1 and `pacman -Qo /usr/bin/mkarchiso` reports that it is owned by archiso 91-1. The latest candidate passed target-root install validation but failed both BIOS and UEFI QEMU before app startup; successful boot and media checks remain pending. `build-usb` fails with `LINUX_BUILD_ENV_REQUIRED` on Windows. `usb` remains output-directory preparation only.

### Latest temporary ISO and emulator result

The 2026-10-13 candidate ISO SHA-256 was `9d6fd80e5757b2f5a075fd46f5d60548c782166cacf95378d6bde2ad3dee42fc` (1,285,586,944 bytes), built from clean snapshot `349e7961264131f69019ad1ab61cfa950af688a8`. Same-hash QEMU BIOS and UEFI boots failed to mount the live root and dropped to `[rootfs~]#`; BIOS logged missing `archiso`, `archiso_loop_mnt` and PXE hooks. Neither reached the app or systemd service, so app/service/no-writer boot evidence is unavailable. The accepted package-policy correction now includes the pinned hook requirements and records them in provenance. Canonical LF package-list SHA-256: `7a7a3fa4127ab6fbaaf952be4bbde82f787a7da30d3111601a9e7fccd6334dbd`. Focused USB suites: Arch 105 passed, 2 skipped; Windows 94 passed, 13 skipped. `mypy usb_builder` is clean. A fresh build after this correction is pending; no USB media or hardware was accessed.

After that package-list correction, a second clean online candidate attempt stopped
before invoking `mkarchiso`: Arch exposes `/usr/sbin/mkarchiso` through the
usrmerge alias `/usr/sbin -> /usr/bin`. The output directory remained empty and
no ISO was produced. The builder now resolves the alias, checks that the
canonical target is an executable regular file, and requires `pacman -Qo` to
exactly confirm that canonical path is owned by `archiso 91-1`. This correction was accepted by SOL. A third online attempt invoked mkarchiso but failed BIOS profile validation before package installation because syslinux was missing from the profile package list. The output directory remained empty and no ISO was produced. The exact pinned core.db contains syslinux 6.04.pre3.r3.g05ac953c-5 with package-signature metadata. The profile and required package policy now include syslinux; the updated canonical LF package-list SHA-256 is 520974f323810ab01ea39b7ea7b43c1e7ecbabb823bb4bed4ebab6b1a5cf3329; SOL accepted the syslinux package-list correction. At that checkpoint, no later retry had run; the fourth attempt is recorded in the current-status section below.

Successful candidate publication places the ISO, build logs, provenance and `SHA256SUMS` together inside `candidate-bundle` through one atomic directory rename. When Archiso deletes its target sync database files, provenance records the pinned Archiso 91-1 source/config invariant and exact staged database hashes as `SOURCE_LEVEL_INVARIANT_ONLY`; retained database files, when present, are independently hashed and compared.

## Safety and validation status

No physical hardware action or firmware build was performed during the current Phase 10B implementation and test run. Tests used fake evidence, runners and transports only. No real-device DMI capture, SPI probe/read, backup, voltage measurement, build, firmware write, IVprep downgrade, media write, restoration, post-write read-back, hardware recovery, or boot test occurred. The repository's software services and synthetic tests do not establish a successful upstream firmware build or a correct installation. Hardware acceptance remains pending for all three V1 devices, and physical writes/recovery still require explicit human authorization.

The implementation plan, current acceptance matrix, known issues and hardware report are in [`docs/v1/`](docs/v1/).

## Development tests

```powershell
python -m pytest -q
python -m mypy autoloader
```

## License

This project is licensed under GPL-3.0; see [`LICENSE`](LICENSE).
