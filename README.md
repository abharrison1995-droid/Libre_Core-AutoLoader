# Libre_Core AutoLoader

Libre_Core AutoLoader is an in-development Python application for preparing and documenting ThinkPad firmware workflows. The CLI and Textual interface share a model-specific controller for the three V1 profiles. Safe status, candidate-evidence and checkpoint actions are available; firmware builds, physical backup/read/write/restore, read-back and boot checks remain unavailable or pending. No physical writer is enabled, and no hardware workflow has been validated on a real device.

## V1 reference workflows

| Device | Planned pathway | Current status |
|---|---|---|
| ThinkPad T430 | 1vyrain, with an official IVprep handoff if the observed BIOS is incompatible | Human handoff software is implemented; app return-attestation integration and hardware validation are pending |
| ThinkPad X200 | Libreboot with external SPI programming | Standalone software staging workflow exists; Linux build, app preparation integration and hardware validation are pending |
| ThinkPad X220 | Coreboot with external SPI programming | Standalone software staging workflow exists; Linux build, app preparation integration and hardware validation are pending |

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
autoloader build-rom --profile x220-coreboot
autoloader verify --profile x220-coreboot
autoloader backup --profile x220-coreboot
autoloader flash --profile x220-coreboot --rom path/to/candidate.rom
autoloader inspect-checkpoint --checkpoint path/to/workflow-journal.json
autoloader resume-checkpoint --profile t430-1vyrain --checkpoint path/to/t430-handoff.json
autoloader usb --output-dir ./iso_build
```

- `run` opens the Textual interface. Workflow choices and displayed next actions come from the shared controller. Build, backup, flash, restore, physical read-back and boot actions remain unavailable; no operation reports success without backend evidence.
- `detect` explicitly captures read-only local DMI/BIOS evidence; startup does not automatically inspect hardware. Detection alone is not proof of compatibility.
- `status` displays controller status for one of the three V1 profiles. Imported evidence remains an unauthenticated candidate and cannot by itself grant compatibility or workflow readiness.
- `import-evidence` accepts typed raw `DeviceEvidence` JSON and saves a candidate to a new path. Existing paths are not overwritten. Its checksum detects accidental corruption; it does not authenticate the evidence or prove that it came from the device.
- `build-rom` routes the request through the controller and currently returns an unavailable outcome without starting a firmware build or creating an artifact.
- `verify` reports read-back as `NOT_PERFORMED`; it does not probe or read physical flash.
- `backup` is unavailable and performs no hardware read. Full-chip and recovery coverage have not been established on real devices.
- `flash` is unavailable and cannot invoke a physical writer, regardless of the candidate ROM path.
- `inspect-checkpoint` validates and summarizes a supported journal read-only. `resume-checkpoint` accepts only a pending T430 human-handoff checkpoint and does not execute IVprep, 1vyrain, or a firmware action. X200/X220 workflow preparation and resume are not exposed by the app yet.
- `usb` creates an output directory only; it does not build or write an ISO.

## Live USB development

The Archiso profile and build script are experimental. They require a supported Linux environment with Archiso tooling. The Windows development host used for this release-candidate work has no WSL Linux distribution or container runtime, so the ISO build and boot path have not been validated.

## Safety and validation status

No real-device firmware write, IVprep downgrade, restoration write or hardware recovery operation has been performed as part of the current development work. Do not treat a mocked test, a ROM structure heuristic, or a successful tool process exit as proof of a correct installation. Hardware acceptance remains pending for all three V1 devices.

The implementation plan, current acceptance matrix, known issues and hardware report are in [`docs/v1/`](docs/v1/).

## Development tests

```powershell
python -m pytest -q
python -m mypy autoloader
```

## License

This project is licensed under GPL-3.0; see [`LICENSE`](LICENSE).
