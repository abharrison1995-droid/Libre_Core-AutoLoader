# V1 progress

## Baseline

- Repository: `https://github.com/abharrison1995-droid/Libre_Core-AutoLoader`
- Development branch: `codex/v1-development`
- Baseline commit: `d5e6810327bad6782f48326ee3f3d707bc6395e9`
- Baseline working tree: clean, as recorded before implementation by the coordinating agent.
- Runtime found for this audit: Python 3.12.14. The bundled interpreter had no pytest/application dependencies. An isolated `%TEMP%\librecore-autoloader-v1-baseline` venv was provisioned with project requirements and pytest.
- Baseline tests: `python -m pytest -q` — 70 passed in 20.31 seconds in that isolated venv.
- Baseline package installation: `pip install -e '.[dev]'` failed because setuptools flat-layout auto-discovery found multiple top-level packages: `backups`, `test_vm`, `autoloader`, and `usb_builder`.
- Baseline CLI/TUI: existing tests import the CLI, invoke `detect` and `build-rom`, mount and compose all nine Textual screens; these checks passed. A real interactive TUI session was not run.
- Baseline mypy: `mypy autoloader` exited 1 with six errors in five files: `utils.py` `geteuid` platform typing, two missing psutil stubs, `model_select.py` `selected_model_id`, `app.py` async `action_back` override, and CLI logging handler list inference.
- Baseline lint: no lint tool or configuration was found in `pyproject.toml`; no lint run.
- Firmware/ISO builds: not run. The Windows 11 execution host has no WSL Linux distribution, Docker/Podman, bash, make or cmake, so Archiso and upstream firmware build pipelines cannot be exercised here.
- Physical hardware operations: not run. No hardware acceptance evidence is available.

## Phase 00 — Repository audit and baseline

**Status: SOFTWARE COMPLETE — SOL ACCEPT**

SOL accepted Phase 00 after the four review follow-ups were finalized in commit `2d0e2d1`; wording was aligned to the final decision in `4c33444`. The Phase 00 report is accepted; device-specific details remain provisional and hardware validation remains pending.

- Entry point: `autoloader.__main__:main` (Click, also `python -m autoloader`). `run` launches `AutoLoaderApp`; `detect` calls model/BIOS/EC detectors; `build-rom` validates configuration only; `usb` creates a directory only; baseline `flash --write` directly called flashrom. Phase 01 quarantines that route pending the shared safety backend.
- TUI: `autoloader/app.py` registers screens; the build, flash and verification screens currently own simulated/canned operation outcomes. Backup routes through `Orchestrator` and `BackupManager`.
- Shared orchestration: `autoloader/orchestrator.py` wraps backup, build and verification. It imports the standalone `verify_flash` helper, which exists in `autoloader/flasher/verify.py`; the alleged import mismatch is not present at this baseline. The helper delegates to `FlashVerifier.verify_flash`.
- Firmware prep: `autoloader/rom_builder/{coreboot_builder,libreboot_builder,configurator,prebuilt}.py`; Coreboot and Libreboot builders are not connected to `Orchestrator.run_build`.
- Flashing and backup: `autoloader/flasher/{flashrom,backup,verify,splitter}.py`; CLI write path calls these directly. The current backup code supports two reads but not a model-complete multi-chip backup policy.
- 1vyrain handoff: `autoloader/reversion/{chains,ivprep,winflash}.py`; downgrade execution is present in code but not gated by a shared approval state.
- 1vyrain flashing: `autoloader/flasher/onevy.py` defines `OnevyRainManager`; it is separate from CLI/TUI orchestration.
- BIOS targets: `autoloader/database/bios_versions.py` stores `ONEVY_REQUIRED_VERSIONS`; values need pin-by-pin upstream verification.
- Backup restore: `BackupManager.restore_from_backup` can invoke a physical write; the TUI restore-guidance button currently only displays a command suggestion.
- VM harness: `test_vm/launch_vm.py`, `launch_vm.bat` and `mock_chips/` contain a QEMU practice harness; it is not evidence of hardware compatibility.
- USB: `usb_builder/build_usb.sh` copies the Python package into an Archiso profile; `start.sh` invokes the TUI and suppresses its output.
- Tests: `tests/` includes unit tests for the database, detection, flash helpers, builders, guides, orchestrator and UI/CLI. Baseline suite passed 70 tests in the isolated venv described above.

## Phase 01 — Stabilization

**Status: IMPLEMENTED — PENDING SOL REVIEW**

- Packaging: constrained setuptools discovery to `autoloader*`; editable `pip install -e '.[dev]'` succeeds in the isolated Python 3.12.14 environment.
- CLI/TUI startup: `python -m autoloader --help` succeeds; CLI command registration, all nine screen mounts/compositions, fail-closed screen state and command routing are covered by automated smoke tests.
- False-success paths: the orchestrator no longer fabricates build completion, backup capture or flash completion. Build and flash return structured unavailable errors; backup is unavailable until observed topology and complete recovery coverage can be established. The Textual build/flash/backup screens do not enable downstream or capture actions, and verification reports `NOT PERFORMED` until an actual result exists.
- Physical writes: the CLI `flash` command now routes through the shared orchestrator and always fails closed; the old `--write` and `--yes` bypass options are removed. The TUI restore control provides an unavailable notice and does not issue a write command. Low-level flash/restore primitives remain for later backend work and unit testing; application paths do not invoke them for physical writes.
- Build status: `build-rom` validates configuration but returns `ROM_BUILD_UNAVAILABLE` and does not create an artifact. The `usb` command only prepares a directory and clearly says no ISO was built.
- Latest software checks: `python -m pytest -q` — **77 passed**; `mypy autoloader` — **no issues in 75 source files**; `python -m autoloader --help` — passed; `git diff --check` — passed. The editable install succeeded after the packaging fix. No interactive firmware operation, IVprep execution, or destructive media operation was performed.
- Remaining environment limit: this Windows 11 host has no WSL Linux distribution or Docker/Podman, bash, make or cmake. Archiso and upstream firmware toolchain builds remain unperformed, not successful.
- Hardware evidence: none. The T430 1vyrain, X200 Libreboot and X220 Coreboot workflows remain **HARDWARE PENDING**.

### Defect register (initial)

| Priority | Finding | Evidence | Planned phase |
|---|---|---|---|
| P0 | Build orchestrator sleeps through fake stages, emits completion and names a ROM that was never built. | `autoloader/orchestrator.py:184-223` | 01 |
| P0 | Flash guide simulates a write/readback and unlocks verification; verify screen displays success regardless of an operation. | `autoloader/ui/screens/flash_guide.py`; `verify_screen.py` | 01 |
| P0 | Baseline CLI write path was not model/artifact/topology validated; `--yes` bypassed interactive acknowledgement. | `autoloader/__main__.py` at baseline | 01 (quarantine), then 02–03 |
| P0 | Backup selects a database-assumed chip; internal path reads only the BIOS region; dual-chip/full-chip coverage is not ensured. | `autoloader/orchestrator.py:120-149`; `flasher/backup.py` | 02–03 |
| P1 | `build-rom` only validates and prints a summary; it does not build despite command description. | `autoloader/__main__.py:123-154` | 01 |
| P1 | CLI, TUI and README command surfaces disagree; README gives an obsolete repository URL and nonexistent `generate-usb` command. | `README.md`; `autoloader/__main__.py` | 01 |
| P1 | Coreboot board config is assembled from a generic model-name symbol; Libreboot `configure_build` is a no-op. Neither builder is release-validated. | `rom_builder/coreboot_builder.py`; `libreboot_builder.py` | 04, 06–07 |
| P1 | Live USB command reports a prepared directory, not a built image; ISO build/boot are unverified. | `autoloader/__main__.py`; `usb_builder/` | 09 |
| P1 | IVprep can download and execute a BIOS downgrade through the chain without a dedicated human authorization checkpoint. | `reversion/chains.py`; `reversion/ivprep.py` | 02, 05 |
| P2 | Compatibility data and README enumerate more machines than the three planned V1 reference workflows; support claims lack per-variant evidence. | `database/models.py`; `README.md` | 02, 07, 11 |

### Feature matrix at baseline

| Category | Current features |
|---|---|
| Operational and tested (software only) | Database lookup/config validation; some detector/parser logic; backup-pair comparison utilities; screen construction/composition. Tests do not establish a safe hardware workflow. |
| Implemented but untested | Firmware toolchain builders, some CLI flash/backup/restore wrappers, IVprep execution path, Archiso build and QEMU harness. No real target workflow is accepted. |
| Partially implemented | Model/BIOS detection, model compatibility data, two-pass flash backup, read-back utility, Coreboot/Libreboot builders, USB payload preparation. |
| Simulated or missing | Orchestrator build, TUI flash result and TUI verification report are simulated/canned at baseline; model-specific authorization/state machine and full-device recovery evidence are missing. |

## Phase 01 — Stabilization

**Status: IN PROGRESS**

The standalone verification helper is already present, so stabilization preserves that API and tests its import/use rather than removing it. Phase 01 quarantines all application CLI physical writes, including the `--yes` bypass, until a shared safety backend and model checks are implemented.
