# Libre_Core AutoLoader

Libre_Core AutoLoader is an in-development Python application for preparing and documenting ThinkPad firmware workflows. The CLI and Textual interface share a model-specific controller for the three V1 profiles. Explicit X200/X220 preparation can inspect local identity and SPI evidence; backup actions perform full-chip reads, and build actions invoke only the pinned artifact engine. On this Windows development host the firmware build stops with `LINUX_BUILD_ENV_REQUIRED`. Physical writing, restore, post-write read-back and boot checks are unavailable. No hardware workflow has been validated on a real device.

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
autoloader flash --profile x220-coreboot --rom path/to/candidate.rom
autoloader inspect-checkpoint --checkpoint path/to/workflow-journal.json
autoloader resume-checkpoint --profile x220-coreboot --checkpoint C:\firmware-work\x220.json --request setup.json
autoloader t430-stage --request handoff-stage.json --checkpoint C:\firmware-work\t430.json
autoloader resume-checkpoint --profile t430-1vyrain --checkpoint C:\firmware-work\t430.json
autoloader usb --output-dir ./iso_build
autoloader build-usb --source-root /work/Libre_Core-AutoLoader --profile-dir /work/Libre_Core-AutoLoader/usb_builder/archiso_profile --snapshot-lock /work/Libre_Core-AutoLoader/usb_builder/snapshot.lock.json --output-dir /work/iso-output --work-parent /work/iso-work --network-mode online
```

- `run` opens the Textual interface. Workflow choices and displayed next actions come from the shared controller. Explicit preparation, backup, and build actions use their reviewed backend services; flash, restore, physical read-back, and boot actions remain unavailable.
- `detect` explicitly captures read-only local DMI/BIOS evidence; startup does not automatically inspect hardware. Detection alone is not proof of compatibility.
- `status` displays controller status for one of the three V1 profiles. Imported evidence remains an unauthenticated candidate and cannot by itself grant compatibility or workflow readiness.
- `import-evidence` accepts typed raw `DeviceEvidence` JSON and saves a candidate to a new path. Existing paths are not overwritten. Its checksum detects accidental corruption; it does not authenticate the evidence or prove that it came from the device.
- `prepare-workflow` requires a new absolute checkpoint path and a versioned operator request. On an explicit X200/X220 request it captures local DMI/BIOS and a read-only SPI probe; X220 also reads the original chip to inspect the pinned IFD map. These are explicit local hardware-read actions. The request's voltage, wiring, and storage values are human-reported evidence with actor, UTC time, instrument, and test point; they are not independently authenticated by the application. Candidate-evidence import cannot set observed chip or programmer fields.
- `backup` (and its `accept-verified-backup` alias) captures two full-chip reads and stores them on the separately selected persistent destinations, or reopens and revalidates an existing verified backup. This is an explicit physical read action when run on a connected device.
- `build-rom` routes through the shared controller and invokes only the pinned X200/X220 recipe after revalidating the saved intent and backup. X220 passes descriptor, ME, and GbE blobs sliced from the verified original backup. A supported Linux build environment is required; this Windows host returns `LINUX_BUILD_ENV_REQUIRED` before starting a build command or creating build output. Passing software tests does not establish that an upstream build has succeeded.
- `accept-completed-artifact` reopens the persisted manifest and selected artifact, revalidates current local evidence, backup files, pinned source/tool records, and model-specific structure checks, then stages only `STAGED/HARDWARE_PENDING`.
- `verify` reports read-back as `NOT_PERFORMED`; it does not probe or read physical flash.
- `flash` is unavailable and cannot invoke a physical writer, regardless of the candidate ROM path.
- `inspect-checkpoint` validates and summarizes an operation journal read-only. X200/X220 `resume-checkpoint` requires a fresh versioned request, locally re-detects/re-probes the device, reopens the exact backup/artifact evidence, and resumes only the safe staged software state. T430 `t430-stage` and `resume-checkpoint` present human-run IVprep/1vyrain instructions and accept fresh return attestations; neither downloads nor executes upstream code.
- The Textual interface uses the same controller and presents the same explicit preparation, backup, build, artifact, resume, and T430 stage actions. It does not automatically probe hardware at startup. Workflow status refreshes from the controller when returning from an action screen.
- `usb` creates an output directory only; it does not build or write an ISO. `build-usb` invokes the shared Archiso build contract and requires explicit absolute source/profile/lock/output/work paths and `--network-mode online|offline`. Offline mode also requires `--offline-repository` and `--offline-manifest`. It refuses non-Linux hosts and never writes removable media.

## Live USB development

Phase 09 software scope is accepted by SOL (`f46e312`), but the Archiso image is not Linux-build or boot validated. A Linux invocation may produce only a candidate ISO. After `mkarchiso`, the builder checks the image target root for the application venv interpreter, installed package and entrypoint metadata, required runtime imports, the startup script, systemd unit and enablement, customize-hook marker, and read-only CLI/import smoke results before copying the ISO into the output directory. This verifies target-root contents; it does not prove that the ISO boots or that its startup service works on boot. Candidate provenance records source/profile hashes, package snapshot, host package inventory, Archiso executable identity, pip/setuptools/wheel versions, installed image package inventory, target-root evidence, and log hashes. The app venv uses `--system-site-packages` and relies on runtime dependencies installed from the locked Arch package set. Pacman requires signed repository databases and packages; offline repositories must match a complete local file/hash manifest, and offline subprocesses are run only after a route-free network namespace has been established. The invoking Python environment must already contain pip, setuptools 61.0 or newer, and wheel; the builder does not install host tools. Wheel creation uses a package-only temporary source copy and leaves the checkout unchanged. Candidate status does not mean the image boots or is release validated; BIOS and UEFI QEMU boot remain separate gates and are recorded `NOT_PERFORMED`.

The package source is fixed to the official 2026-09-29 archive, Archiso 91-1 and a SHA-256-pinned package list. The builder records the complete host package inventory, but versions other than Archiso and the `mkarchiso` executable are not pinned; exact host-environment reproducibility remains a release gate. Offline isolation uses `unshare --net` for root and `unshare --user --map-root-user --net` for an unprivileged builder, verifies that the new namespace has only loopback and no IPv4/IPv6 routes, then applies that command prefix to all build subprocesses. The Windows development host has no WSL Linux distribution or container runtime, so the namespace probe, real `mkarchiso` build, BIOS/UEFI QEMU boot, and target-root runtime smoke have not been run here. `build-usb` fails with `LINUX_BUILD_ENV_REQUIRED` on Windows. `usb` remains output-directory preparation only; no removable media is written.

## Safety and validation status

No physical hardware action or firmware build was performed during the current Phase 08 implementation and test run. The explicit prepare/backup routes were exercised only with fake adapters; no real-device DMI capture, SPI probe/read, backup, voltage measurement, build, firmware write, IVprep downgrade, media write, restoration, post-write read-back, hardware recovery, or boot test occurred. The repository's software services and synthetic tests do not establish a successful upstream firmware build or a correct installation. Hardware acceptance remains pending for all three V1 devices, and physical writes/recovery still require explicit human authorization.

The implementation plan, current acceptance matrix, known issues and hardware report are in [`docs/v1/`](docs/v1/).

## Development tests

```powershell
python -m pytest -q
python -m mypy autoloader
```

## License

This project is licensed under GPL-3.0; see [`LICENSE`](LICENSE).
