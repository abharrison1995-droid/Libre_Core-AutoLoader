# Libre_Core AutoLoader

Libre_Core AutoLoader is an in-development Python application for preparing and documenting ThinkPad firmware workflows. The V1 hardware workflows are not validated. Physical firmware writes are quarantined from the CLI and Textual interface until the shared, model-specific safety backend is implemented.

## V1 reference workflows

| Device | Planned pathway | Current status |
|---|---|---|
| ThinkPad T430 | 1vyrain, with an official IVprep handoff if the observed BIOS is incompatible | Software work not complete; hardware validation pending |
| ThinkPad X200 | Libreboot with external SPI programming | Software work not complete; exact board/chip must be observed; hardware validation pending |
| ThinkPad X220 | Coreboot with external SPI programming | Software work not complete; exact board/chip/regions must be observed; hardware validation pending |

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
autoloader build-rom --model x220 --base coreboot
autoloader verify --rom path/to/candidate.rom --programmer ch341a
autoloader backup --model x220
autoloader flash --rom path/to/candidate.rom
autoloader usb --output-dir ./iso_build
```

- `run` opens the Textual interface. Backup capture, building, flashing and post-flash success are not represented as complete when their backends are unavailable.
- `detect` reports locally observable model, BIOS and EC information. Detection is not proof of firmware compatibility.
- `build-rom` validates configuration only; the command exits with an unavailable error because a validated builder is not connected.
- `verify` performs a non-destructive flash read and compares it with the selected image. It is not a compatibility check or boot test.
- `backup` is currently unavailable because the application cannot prove full-chip and recovery coverage for observed hardware.
- `flash` is currently unavailable and cannot invoke a physical writer.
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
