# V1 progress

## Baseline

- Repository: `https://github.com/abharrison1995-droid/Libre_Core-AutoLoader`
- Development branch: `codex/v1-development`
- Baseline commit: `d5e6810327bad6782f48326ee3f3d707bc6395e9`
- Baseline working tree: clean, as recorded before implementation by the coordinating agent.
- Runtime found for this audit: Python 3.12.14. The bundled interpreter has no `pytest` module; initial baseline test command could not run. Install test dependencies in an isolated environment before recording test results.
- Physical hardware operations: not run. No hardware acceptance evidence is available.

## Phase 00 — Repository audit and baseline

**Status: IN PROGRESS**

- Entry point: `autoloader.__main__:main` (Click, also `python -m autoloader`). `run` launches `AutoLoaderApp`; `detect` calls model/BIOS/EC detectors; `build-rom` validates configuration only; `usb` creates a directory only; `flash` directly calls flashrom when explicitly passed `--write`.
- TUI: `autoloader/app.py` registers screens; the build, flash and verification screens currently own simulated/canned operation outcomes. Backup routes through `Orchestrator` and `BackupManager`.
- Shared orchestration: `autoloader/orchestrator.py` wraps backup, build and verification. It imports the standalone `verify_flash` helper, which exists in `autoloader/flasher/verify.py`; the alleged import mismatch is not present at this baseline. The helper delegates to `FlashVerifier.verify_flash`.
- Firmware prep: `autoloader/rom_builder/{coreboot_builder,libreboot_builder,configurator,prebuilt}.py`; Coreboot and Libreboot builders are not connected to `Orchestrator.run_build`.
- Flashing and backup: `autoloader/flasher/{flashrom,backup,verify,splitter}.py`; CLI write path calls these directly. The current backup code supports two reads but not a model-complete multi-chip backup policy.
- 1vyrain handoff: `autoloader/reversion/{chains,ivprep,winflash}.py`; downgrade execution is present in code but not gated by a shared approval state.
- USB: `usb_builder/build_usb.sh` copies the Python package into an Archiso profile; `start.sh` invokes the TUI and suppresses its output.
- Tests: `tests/` includes unit tests for the database, detection, flash helpers, builders, guides, orchestrator and UI/CLI. Baseline pytest has not run because pytest is absent from the bundled interpreter.

### Defect register (initial)

| Priority | Finding | Evidence | Planned phase |
|---|---|---|---|
| P0 | Build orchestrator sleeps through fake stages, emits completion and names a ROM that was never built. | `autoloader/orchestrator.py:184-223` | 01 |
| P0 | Flash guide simulates a write/readback and unlocks verification; verify screen displays success regardless of an operation. | `autoloader/ui/screens/flash_guide.py`; `verify_screen.py` | 01 |
| P0 | CLI write path is not model/artifact/topology validated; `--yes` bypasses interactive acknowledgement. | `autoloader/__main__.py:168-255` | 02 |
| P0 | Backup selects a database-assumed chip; internal path reads only the BIOS region; dual-chip/full-chip coverage is not ensured. | `autoloader/orchestrator.py:120-149`; `flasher/backup.py` | 02–03 |
| P1 | `build-rom` only validates and prints a summary; it does not build despite command description. | `autoloader/__main__.py:123-154` | 01 |
| P1 | CLI, TUI and README command surfaces disagree; README gives an obsolete repository URL and nonexistent `generate-usb` command. | `README.md`; `autoloader/__main__.py` | 01 |
| P1 | Coreboot board config is assembled from a generic model-name symbol; Libreboot `configure_build` is a no-op. Neither builder is release-validated. | `rom_builder/coreboot_builder.py`; `libreboot_builder.py` | 05–06 |
| P1 | Live USB command reports a prepared directory, not a built image; ISO build/boot are unverified. | `autoloader/__main__.py:157-166`; `usb_builder/` | 10 |
| P1 | IVprep can download and execute a BIOS downgrade through the chain without a dedicated human authorization checkpoint. | `reversion/chains.py`; `reversion/ivprep.py` | 02, 04 |
| P2 | Compatibility data and README enumerate more machines than the three planned V1 reference workflows; support claims lack per-variant evidence. | `database/models.py`; `README.md` | 02, 07, 11 |

## Phase 01 — Stabilization

**Status: IN PROGRESS**

Implementation and test evidence will be appended here with task IDs, changed files, command results, safety notes and commit IDs. The standalone verification helper is already present, so stabilization will preserve that API and test its import/use rather than remove it.
