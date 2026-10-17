# Libre_Core AutoLoader

Libre_Core AutoLoader is an in-development Python application for preparing and documenting ThinkPad firmware workflows. The CLI and Textual interface share a model-specific controller for the three V1 profiles. Explicit X200/X220 preparation can inspect local identity and SPI evidence; backup actions perform full-chip reads, and build actions invoke only the pinned artifact engine. The guarded CLI and dedicated mounted-screen TUI write routes reopen staged X200/X220 evidence, require fresh evidence and one-use exact-action human approval, and perform a separate full-chip read-back. The TUI challenge is bound to its active screen and can be cancelled until the service consumes the receipt. Both write routes are unavailable on this Windows development host because durable directory fsync is unsupported. Ubuntu 26.04 WSL is available for Linux build validation. An earlier X200 attempt compiled components but did not produce a complete validated ROM; a fresh fail-closed ArtifactEngine preflight correctly stopped before building because physical identity, chip-capacity, and verified-backup evidence were unavailable. No accepted ROM artifact or COMPLETE manifest exists. Raw restore/recovery remains unavailable, and real-device boot validation has not passed. No hardware workflow has been validated on a real device.

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
- `build-rom` routes through the shared controller and invokes only the pinned X200/X220 recipe after revalidating the saved intent and backup. X220 passes descriptor, ME, and GbE blobs sliced from the verified original backup. A supported Linux build environment is required; a native Windows invocation returns `LINUX_BUILD_ENV_REQUIRED` before starting a build command or creating output. Ubuntu 26.04 WSL is available, and the pinned Libreboot source checkout matches its commit/tree; the pinned dependency list was installed except for `g++-mipsel-linux-gnu`, which is not needed by the x86 X200 target. Earlier Linux attempts compiled components but have not produced a complete validated ROM, parser-accepted artifact, or COMPLETE manifest. Passing software tests does not establish that an upstream build has succeeded.
- `accept-completed-artifact` reopens the persisted manifest and selected artifact, revalidates current local evidence, backup files, pinned source/tool records, and model-specific structure checks, then stages only `STAGED/HARDWARE_PENDING`.
- `verify` reports read-back as `NOT_PERFORMED`; it does not probe or read physical flash.
- `flash` accepts only an exact staged X200/X220 checkpoint and current versioned request; arbitrary `--rom` paths are not supported. It requires a real OS TTY, collects live meter/wiring attestations and asks for the exact operation challenge. The controller reopens the pinned artifact and complete backup, then repeats local DMI/SPI checks before the one-shot writer. Independent full-chip read-back is required before `WRITE_VERIFIED`; timeout, nonzero exit or a mismatch is `UNCERTAIN` and never retries. Windows is blocked because the durable approval ledger requires directory fsync. T430 remains a separate IVprep/1vyrain human handoff, and restore is disabled.
- `inspect-checkpoint` validates and summarizes an operation journal read-only. X200/X220 `resume-checkpoint` requires a fresh versioned request, locally re-detects/re-probes the device, reopens the exact backup/artifact evidence, and resumes only the safe staged software state. T430 `t430-stage` and `resume-checkpoint` present human-run IVprep/1vyrain instructions and accept fresh return attestations; neither downloads nor executes upstream code.
- The Textual interface uses the same controller and presents the same explicit preparation, backup, build, artifact, resume, and T430 stage actions. It does not automatically probe hardware at startup. Its X200/X220 write route uses a dedicated mounted-screen challenge and live setup form; the screen binds approval to its mount and challenge, and blocks navigation while the service is active. Workflow status refreshes from the controller when returning from an action screen.
- `usb` creates an output directory only; it does not build or write an ISO. `build-usb` invokes the shared Archiso build contract and requires explicit absolute source/profile/lock/output/work paths and `--network-mode online|offline`. Offline mode also requires `--offline-repository` and `--offline-manifest`. It refuses non-Linux hosts and never writes removable media.

## Live USB development

SOL accepted the Phase 09 corrective builder review after publication fault tests, exact archive redirect checks, and explicit pacman database-consumption evidence were added. The current derivative candidate is b2 (details below). Its same-hash BIOS and UEFI QEMU boots reached systemd on the live system but stalled before AutoLoader/service evidence; no passing app/service boot validation exists. An earlier pre-initramfs candidate failed before app startup. After `mkarchiso`, the builder checks the image target root for the application venv interpreter, installed package and entrypoint metadata, required runtime imports, the startup script, systemd unit and enablement, customize-hook marker, and read-only CLI/import smoke results before publishing any output. Provenance records source/profile hashes, package snapshot, host package inventory, Archiso executable identity, pip/setuptools/wheel versions, installed image package inventory, target-root evidence, database-consumption evidence and log hashes. The app venv uses `--system-site-packages` and relies on runtime dependencies installed from the locked Arch package set. Pacman must verify package signatures; repository database detached signatures are optional only when absent from the dated archive, while exact `core.db` and `extra.db` SHA-256 pins are always required and any present signatures must verify. Offline repositories must match a complete local file/hash manifest, and offline subprocesses are run only after a route-free network namespace has been established. The invoking Python environment must already contain pip, setuptools 61.0 or newer, and wheel; the builder does not install host tools. Wheel creation uses a package-only temporary source copy and leaves the checkout unchanged. The ISO, logs, provenance and `SHA256SUMS` are published together inside `candidate-bundle` through one atomic directory rename. The builder records QEMU as `NOT_PERFORMED` in build-time provenance; separate emulator checks are required before release.

The package source is fixed to the official 2026-09-29 archive, Archiso 91-1, its reviewed source commit, exact repository database hashes and a SHA-256-pinned package list. The builder records the complete host package inventory, but versions other than Archiso and the `mkarchiso` executable are not pinned; exact host-environment reproducibility remains a release gate. The production preflight uses `unshare --user --map-root-user --net --mount --pid --fork --propagation private --mount-proc`. In Arch WSL, the real network probe passes with canonical package-owned `unshare` and `ip`. The errno gate first exposed a probe-plan bug: generic VFS flags (nosuid,nodev) were passed as tmpfs filesystem data, producing EINVAL. A disposable same-namespace comparison showed the old syscall failed, while the corrected MS_NOSUID|MS_NODEV flags with mode=0755 data and the exact util-linux mount command succeeded. The mount plan now splits VFS flags from filesystem data. At that earlier checkpoint, the root-oriented preflight returned ARCHISO_MOUNT_CAPABILITY_UNAVAILABLE for devtmpfs in Arch WSL and blocked both modes before work/output. That result is superseded for the online path by the later non-root Archiso -N mount-stack preflight described below; it remains historical WSL evidence, not a native Linux result. Focused USB tests pass 259/1 skipped on Arch WSL and 244/16 on Windows; native and Win32-targeted mypy pass on 104 files. Full diagnostic details are in docs/v1/evidence/offline-namespace-arch-wsl-20261015/tmpfs-option-split-diagnostic-20261015.txt. These are WSL-only results: native Linux behavior and a complete offline image build remain pending. The process-group timeout cleanup cannot stop a descendant that calls `setsid()`; such a process remains network-isolated but could continue local filesystem activity. The first real online candidate attempt stopped before `mkarchiso` because the package-list pin used CRLF-converted bytes instead of the checked-in LF content; the lock now pins the canonical LF hash and validation normalizes CRLF to LF; SOL accepted this correction in commit 3a146f8. The second attempt passed this check but stopped before `mkarchiso` on the symlinked `/usr/sbin/mkarchiso` path described below. The third attempt reached `mkarchiso` but failed BIOS profile validation before package installation because `syslinux` was missing; the profile now includes it. A fourth online attempt (2026-10-13) passed package validation and completed the Archiso build stages, then failed target-root validation because `/usr/sbin` inside the image is a usrmerge link to `/usr/bin`. The output directory remained empty and no ISO was published. The image-rooted resolver was accepted. A pre-fragment ISO failed both QEMU modes. The later candidate and its failed boot evidence are recorded below. The signature-verified Archiso 91-1 package is installed under Arch WSL; `pacman -Q archiso` reports 91-1 and `pacman -Qo /usr/bin/mkarchiso` reports that it is owned by archiso 91-1. Candidate b2 passed target-root install validation. Same-hash BIOS (310s) and UEFI (922s) QEMU reached systemd on the live system but stalled before AutoLoader/service evidence; tty2 showed no login prompt. Successful app/service and media checks remain pending. `build-usb` fails with `LINUX_BUILD_ENV_REQUIRED` on Windows. `usb` remains output-directory preparation only.

### Earlier temporary ISO and emulator result (before package-policy correction)

The 2026-10-13 candidate ISO SHA-256 was `9d6fd80e5757b2f5a075fd46f5d60548c782166cacf95378d6bde2ad3dee42fc` (1,285,586,944 bytes), built from clean snapshot `349e7961264131f69019ad1ab61cfa950af688a8`. Same-hash QEMU BIOS and UEFI boots failed to mount the live root and dropped to `[rootfs~]#`; BIOS logged missing `archiso`, `archiso_loop_mnt` and PXE hooks. Neither reached the app or systemd service, so app/service/no-writer boot evidence is unavailable. The accepted package-policy correction now includes the pinned hook requirements and records them in provenance. Canonical LF package-list SHA-256: `7a7a3fa4127ab6fbaaf952be4bbde82f787a7da30d3111601a9e7fccd6334dbd`. Focused USB suites: Arch 105 passed, 2 skipped; Windows 94 passed, 13 skipped. `mypy usb_builder` is clean. At that earlier checkpoint a fresh build was pending; no USB media or hardware was accessed.

After that package-list correction, a second clean online candidate attempt stopped
before invoking `mkarchiso`: Arch exposes `/usr/sbin/mkarchiso` through the
usrmerge alias `/usr/sbin -> /usr/bin`. The output directory remained empty and
no ISO was produced. The builder now resolves the alias, checks that the
canonical target is an executable regular file, and requires `pacman -Qo` to
exactly confirm that canonical path is owned by `archiso 91-1`. This correction was accepted by SOL. A third online attempt invoked mkarchiso but failed BIOS profile validation before package installation because syslinux was missing from the profile package list. The output directory remained empty and no ISO was produced. The exact pinned core.db contains syslinux 6.04.pre3.r3.g05ac953c-5 with package-signature metadata. The profile and required package policy now include syslinux; the updated canonical LF package-list SHA-256 is 520974f323810ab01ea39b7ea7b43c1e7ecbabb823bb4bed4ebab6b1a5cf3329; SOL accepted the syslinux package-list correction. At that checkpoint, no later retry had run; the fourth attempt is recorded in the current-status section below.

An earlier candidate (b96, preceding b2) was from clean snapshot `d87bc03a6b94bdde86ff3e3ebf89b24df0b4b275` (tree `0a433bfc6382c56e24b0b5c2bb766ef3d6ff246a`), ISO SHA-256 `b96f83ba84e67ab7e747c95296319d549b57d167010d1ce98a43be2d45c3e4a5`, size 1,339,064,320 bytes. It passed target-root install validation. BIOS QEMU reached the Arch live-root shell but did not show the AutoLoader UI/service; tty2 and serial did not provide service evidence. UEFI QEMU reached early initramfs hooks and stalled before mounting the live root. Both used the same ISO hash, no network device and no write-capable firmware interface. Exact captures, command flags and hashes are in `usb_builder/LIVE_USB_RUNBOOK.md`. App/service/journal/no-writer boot validation remains failed or not evidenced; no emulator result is hardware validation.

SOL accepted a separately pinned USB initramfs derivative that removes only `kms`, preserving all other hook order, the PXE hooks, xz compression and options. Source and staged derivative hashes are checked at pre-build, post-build and publication boundaries. The b96 candidate predates this derivative; b2 below includes it. Windows focused USB validation passed 102 tests, 13 skipped; derivative-specific cases passed 6; `mypy usb_builder` and diff-check passed. Same-hash BIOS/UEFI app/service evidence remains pending. No PXE or compression change is part of this experiment.

### Current derivative candidate b2 (2026-10-14)

Candidate b2 was built from clean USB-only snapshot commit `1197d9040a39d6da012f459b8bb1f45385ee0072`, tree `d2588ddff4f0c5d4115565f47360314553106d38`. Its ISO is 1,012,215,808 bytes with SHA-256 `afde8ba28025530852547ec5042dd94ecece5a9b1d24f5da141365861cc35cbd`. Target-root installation and packaged CLI checks passed. Inspection of the 79 MiB initramfs confirmed the `kms`-omitting derivative with PXE hooks retained. Same-hash BIOS (310s) and UEFI (922s) QEMU boots reached systemd on the live system but stalled before AutoLoader/service evidence; tty2 showed no login prompt. Later direct-kernel diagnostics captured some read-only systemd state but established no root cause and do not pass normal BIOS/UEFI startup acceptance. The current Arch WSL production mount preflight stops at devtmpfs with `EPERM` before candidate-build work/output; native Linux build, new candidate, successful app/service boot, and physical USB checks remain pending. The ISO and evidence are not hardware validation.

Successful candidate publication places the ISO, build logs, provenance and `SHA256SUMS` together inside `candidate-bundle` through one atomic directory rename. When Archiso deletes its target sync database files, provenance records the pinned Archiso 91-1 source/config invariant and exact staged database hashes as `SOURCE_LEVEL_INVARIANT_ONLY`; retained database files, when present, are independently hashed and compared.

## Safety and validation status

No physical hardware action, media write or real-device boot test was performed during the current implementation pass. An earlier X200 attempt compiled some upstream components but did not produce a complete validated ROM. Tests used fake evidence, runners and transports only. No real-device DMI capture, SPI probe/read, backup, voltage measurement, firmware write, IVprep downgrade, restoration, post-write read-back or hardware recovery occurred. The QEMU boots above are emulator evidence only and did not verify app/service startup. The repository's software services and synthetic tests do not establish a successful upstream firmware build or a correct installation. Hardware acceptance remains pending for all three V1 devices, and physical writes/recovery still require explicit human authorization.

The implementation plan, current acceptance matrix, known issues and hardware report are in [`docs/v1/`](docs/v1/).

## Development tests

```powershell
python -m pytest -q
python -m mypy autoloader
```

## License

This project is licensed under GPL-3.0; see [`LICENSE`](LICENSE).

## Current-source software regression — 2026-10-17

The current-source Windows full suite passed **752 tests with 62 skipped**
(814 collected; zero failures/errors; 789.46 seconds) on Python 3.12.14 and
pytest 9.1.1. The Ubuntu 26.04 WSL2 suite passed 839 with 6 skipped. Windows
skips include POSIX durability/process/ownership/symlink cases, Linux-only
namespace/device/pacman checks, POSIX host checks not validated on Windows,
and one account-specific symlink privilege limitation (`WinError 1314`).
Skipped checks remain unpassed. Exact reports and hashes are in the
[`software regression evidence`](docs/v1/evidence/software-regression-20261017/README.md).

The Ubuntu full suite preceded the pytest discovery change. SOL later accepted
the `testpaths = ["tests"]` setting restricting default collection to
application tests; subsequent collection found 814 tests with no
evidence-directory paths. The explicit evidence-probe selection passed 31
tests with 2 skipped, and frozen retry7/retry8 evidence files were unchanged.
These software checks do not
establish native Linux builder readiness, a current ISO or successful Live
USB startup, a complete ROM, or hardware validation. Those release gates
remain pending.


Historical pre-correction suite counts are retained in the earlier checkpoints above. The latest software regression evidence is summarized below; WSL results are distinct from native Linux validation.

The latest Arch WSL2 full suite ran against Python source/tests at `72a302b`, using `setpriv` to test as UID/GID 65534 (`nobody`): **757 passed, 2 skipped** (759 collected; 1245.94s; exit 0). A docs-only commit `0d74d76` landed during the run; the tested Python source and test files remained at `72a302b`. Earlier Windows and Ubuntu WSL full suites were **699 passed, 60 skipped** and **755 passed, 4 skipped**, respectively, on `6cabb23d`; they do not cover the later source/test tree. Exact commands, skip reasons, reports and hashes are in [the software regression evidence](docs/v1/evidence/software-regression-20261016/README.md). The `nobody` test is WSL2 software evidence; it does not validate the dedicated build account, fixed privileged launcher, production offline route, native Linux, VM/ISO, ROM, boot, or hardware gates. Offline production execution remains fail-closed. Native Linux build, offline ISO build, successful Live USB app/service boot, and all hardware workflows remain pending; release is not complete.

A separate Ubuntu 26.04 WSL production preflight stopped before namespace/mount checks because `pacman` is absent, so the trusted `pacman -Qo` ownership check could not start (`OFFLINE_NETWORK_ISOLATION_UNAVAILABLE`). It did not test devtmpfs or create candidate-build work/output. Ubuntu WSL is not the pinned Archiso 91-1 build host; exact evidence is in `docs/v1/evidence/offline-namespace-ubuntu-wsl-20261015/`. This does not change the distinct Arch WSL preflight result or establish native Linux build support.


Current builder implementation checkpoint (2026-10-16): production online execution now uses pinned non-root/helper and isolated route-free Archiso -N mount preflights before creating work/output, then runs with a fixed sanitized environment and a hash-verified operation-owned pacstrap status overlay. The current-source disposable-VM package/build gate is being handled separately; this code change does not establish an ISO build or boot result. Offline execution remains fail-closed before work/output because a no-egress launcher compatible with the non-root -N contract has not been reviewed. Focused rootless software checks passed 70 tests (269 deselected), a direct route selection passed 7 (332 deselected), and native plus Win32-targeted mypy each passed on 5 USB-builder files. Existing b2 BIOS/UEFI evidence still stops before AutoLoader/service; native Linux, current-source candidate, app/service boot, and hardware checks remain open.
