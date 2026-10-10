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
- `usb` creates an output directory only; it does not build or write an ISO.

## Live USB development

The Archiso profile and build script are experimental. They require a supported Linux environment with Archiso tooling. The Windows development host used for this release-candidate work has no WSL Linux distribution or container runtime, so the ISO build and boot path have not been validated.

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
