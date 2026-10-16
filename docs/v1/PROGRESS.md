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
- At the initial baseline audit, firmware/ISO builds had not been run and the Windows 11 host had no WSL Linux distribution, Docker/Podman, bash, make or cmake. Ubuntu 26.04 WSL and Arch WSL were installed later; the historical baseline result is not the current environment state.
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

**Status: SOFTWARE COMPLETE — SOL ACCEPT**

- Packaging: constrained setuptools discovery to `autoloader*`; editable `pip install -e '.[dev]'` succeeds in the isolated Python 3.12.14 environment.
- CLI/TUI startup: `python -m autoloader --help` succeeds; CLI command registration, all nine screen mounts/compositions, fail-closed screen state and command routing are covered by automated smoke tests.
- False-success paths: the orchestrator no longer fabricates build completion, backup capture or flash completion. Build and flash return structured unavailable errors; backup is unavailable until observed topology and complete recovery coverage can be established. The Textual build/flash/backup screens do not enable downstream or capture actions, and verification reports `NOT PERFORMED` until an actual result exists. `OnevyRainManager` now fails closed for compatibility, preparation, exploit, unlock verification and flash; it cannot claim `UNLOCKED` or `COMPLETE`.
- Phase 01 acceptance state (historical, superseded for X200/X220 by Phase 10B): the CLI `flash` command routed through the shared orchestrator and failed closed; the old `--write` and `--yes` bypass options were removed. The TUI restore control provided an unavailable notice and did not issue a write command. Low-level flash/restore primitives were retained for later backend work and unit testing.
- Build status: `build-rom` validates configuration but returns `ROM_BUILD_UNAVAILABLE` and does not create an artifact. The `usb` command only prepares a directory and clearly says no ISO was built.
- User-facing claims: welcome screen uses package version and identifies the app as in development; CLI and model-selection database entries are labeled provisional and unvalidated. Model chip sizes are database estimates that must be checked against each observed chip. Backup manifests are shown as recorded until the explicit pair verification action runs.
- Review: SOL accepted Phase 01; its wording follow-up was resolved in `207a7df`. Hardware workflows remain pending.
- Phase 01 software checks at acceptance: `python -m pytest -q` — **80 passed**; `mypy autoloader` — **no issues in 75 source files**; `python -m autoloader --help` — passed; editable install passed. No interactive firmware operation, IVprep execution, or destructive media operation was performed.
- At Phase 01 acceptance, the Windows 11 host still had no WSL Linux distribution or Docker/Podman, bash, make or cmake. Later WSL setup is recorded in Phases 04/09 below; no build is implied by the historical note.
- Hardware evidence: none. The T430 1vyrain, X200 Libreboot and X220 Coreboot workflows remain **HARDWARE PENDING**.

## Phase 02 — Compatibility, workflow state and authorization

**Status: SOFTWARE COMPLETE — SOL ACCEPT**

- Domain evidence: added frozen device, chip topology, programmer, region, artifact/provenance, backup-pair, operation-plan, compatibility/result and verification records in `autoloader/workflow/types.py`. `DeviceEvidence` retains raw DMI product name/version/family, MTM and raw BIOS version; normalized detector values and manual selections cannot substitute for missing raw identity evidence.
- Detection: Linux DMI fallback now preserves `Family:` and Windows detection attempts independent WMIC product version and system family fields. BIOS detection retains the raw BIOS version separately from its parsed display value.
- Explicit profiles: `autoloader/workflow/profiles.py` exposes only T430/1vyrain, regular X200/Libreboot and X220/Coreboot in an immutable allowlist. Broad database support flags are not consulted. Identity requires matching product-name, system-family and allowlisted MTM evidence; aliases, conflicts and manual-only selection fail closed.
- T430 source rule: pinned upstream 1vyrain `start.sh` at commit `a3427b34102f8f9887acf65e23d7fd5900fe1d40` reads field 3 from the full dmidecode `Version:` line, strips `(` and `.`, and accepts T430 only when the resulting number is below `265`. The policy accepts a raw value such as `G1ET74WW (2.64 )`, blocks `G1ET75WW (2.65 )`, and returns UNKNOWN for bare `2.64`, missing, malformed, ambiguous or unspaced parenthesized strings because these do not produce the upstream-compatible third token. The raw `Family:` value is parsed using upstream's `$3$4` extraction; unsupported family formatting remains UNKNOWN. It records an IVprep handoff reason when the threshold is exceeded; it never executes IVprep or 1vyrain.
- Cross-field identity: `_validate_identity` checks MTM prefixes found in each of raw MTM, raw DMI product version and raw DMI product name. Conflicting or unsupported prefixes block. For T430, `raw_bios_vendor` must agree with the detector's `stock_lenovo_bios` classification; missing manufacturer evidence remains UNKNOWN.
- Topology/artifacts: X200 requires one observed 4 or 8 MiB chip and matching artifact size. T430 requires the profile's observed 8 MiB bottom/4 MiB top chips and a 4 MiB artifact. X220 requires one observed 8 MiB chip, complete non-overlapping IFD/GBE/ME/BIOS maps, a 3 MiB BIOS region and exact artifact-to-observed map agreement. Artifact bytes must match the declared SHA-256; moving tags/branches are rejected and source revisions must be full 40-hex commits.
- Programmer and backups: external SPI requires a verified interface/wiring and programmer output at 3300 mV, plus each observed chip recorded at the same voltage. Backups require one exact-capacity pair per chip, separate files and capture IDs, stable/nonblank content, declared SHA-256 matches and identical independent reads. Unknown or conflicting evidence blocks progression.
- State and approval: `WorkflowSession` owns DRAFT → VALIDATED → BACKUP_VERIFIED → AUTHORIZED → WRITE_BLOCKED/BLOCKED transitions and a durable checksummed journal in the per-user application state directory by default. Each record stores the operation ID, UTC timestamp, stage transition, device fingerprint, artifact provenance and actual content metadata, backup capture IDs and file checksums, and failure information. Writes use an atomic file replacement; backup checksums are staged in memory and committed in the same atomic append as the backup-verification transition. Corrupt, stale or concurrently changed checkpoints are rejected. Only DRAFT, VALIDATED and BACKUP_VERIFIED can resume with exact evidence matching, revalidation and a fresh approval. BLOCKED/FAILED/WRITE_BLOCKED records are read-only inspection; AUTHORIZED and later checkpoints cannot resume. Human approval tokens bind the plan, device, artifact content, topology and backup content digests, and are single-use. `request_write` always returns `WRITES_DISABLED_PHASE_02`; no physical writer is connected or called.
- Tests: `tests/test_workflow_safety.py` now has **21 focused tests** covering identity conflict/aliases/manual candidates, explicit rejection of X200 Tablet/X200s/X200si on the regular-X200 profile, cross-field MTM conflict/unsupported prefixes, T430 BIOS version/vendor/family parsing, X200 capacity and artifact corruption, X220 topology/region/layout, external chip voltage, partial/transient/blank backup evidence, durable checkpoint persistence, atomic backup-evidence transition, corruption before and after resume, changed-plan rejection, safe-only resume, illegal transitions, changed/reused approvals and zero writer calls.
- Final independent software checks: focused Phase 02 tests — **21 passed**; full `python -m pytest -q` — **101 passed**; `mypy autoloader` — **no issues in 82 source files**; editable `pip install -e '.[dev]'` — passed in the isolated Python 3.12.14 venv; `python -m autoloader --help` — passed. The final tree was confirmed clean after the implementation/evidence commits. These are software-only checks; they do not establish physical compatibility.
- At Phase 02 completion, this core was standalone. Phase 08 now routes its profile policy and durable workflows through the shared CLI/TUI controller. Input measurements and capture IDs are not hardware-attested; artifact source-commit ownership is not independently fetched/verified. The journal's unkeyed SHA-256 chain detects corruption but is not a signed or hostile-editor-proof attestation. No physical hardware, SPI gear, flash writes, IVprep, recovery operation or destructive media operation was used. No firmware or Live USB build was performed at that stage; later WSL setup is recorded below.
- Review/commit: SOL accepted the Phase 02 software core after the requested corrective work. Accepted implementation commits: `5ceb6f79d6924f73c073a4631cb65434e2058fbd` (durable journal and identity/BIOS corrections) and `263d8d5b4625799f504abfb691ba2ba6bed22ec6` (regular-X200 variant exclusion). Corresponding evidence/documentation commits: `ae494ebd27a427573a06e60c6c079c2d9ba526ae` and `f04f9d35957ee0e7575a17a023985c11d4c1254e`. This acceptance is software-only. Hardware workflows remain **HARDWARE PENDING**; Phase 08 later provided app integration, and Phase 03 remains software-complete but not hardware-validated.

## Phase 03 — SPI evidence, backup, read-back and recovery readiness

**Status: SOFTWARE COMPLETE — SOL ACCEPT**

- Implementation commits: `4ba1858eb38b1c35b748153aea0a6293db928ecb` added the read-only SPI transport and probe evidence, multi-chip backup capture, storage preflight, region-aware read-back comparator, recovery-readiness records and runbook; `448766a544452538ea9425a598384fd304c6a2df` restricted the flashrom command executor to exact version/probe/read/verify forms and rejects unknown or caller-injected options before subprocess execution.
- Phase 03 boundary (historical, superseded by Phase 10B): legacy `FlashromRunner.write_rom`, `InternalFlasher.flash_internal`, and `BackupManager.restore_from_backup` remain hard-blocked. `run_command` has no arbitrary command path; only exact read-only forms are accepted. At Phase 03 acceptance, the CLI/TUI had not yet been integrated with these services. Phase 10B later adds a guarded X200/X220 application writer; T430 and raw restore remain blocked.
- Software evidence: fake transport/storage regression tests cover strict probe parsing, exact tool identity, unforced chip observation, complete observed topology, measured-voltage requirements, two exact-size independent reads per chip, persistent separate backup destinations, checksummed manifests, preserved failure/mismatch evidence, X220 IFD/GBE/ME/BIOS map handling, region comparisons, recovery readiness tied to the current plan, and zero subprocess calls for write/erase/write-protection aliases and injected options.
- Final implementation checks: `python -m pytest -q` — **124 passed**; `mypy autoloader` — **no issues in 86 source files**; editable install/build passed; `python -m autoloader --help` passed; `git diff --check` passed. SOL's independent rerun passed **124 tests in 79.59 seconds**, mypy on 86 files, CLI help, and a clean tree at `448766a`.
- At Phase 03 completion, the services were standalone. Phase 08 now invokes the read-only probe and backup/recovery evidence paths for explicit X200/X220 requests; the read-back comparator is not exposed through the app. No actual SPI probe/read, measured voltage, physical backup, firmware compilation/write, post-write read-back, recovery operation, or boot test occurred. No IVprep or destructive media operation occurred. All three hardware workflows remain **HARDWARE PENDING**.

## Phase 04 — Pinned firmware artifact engine

**Status: SOFTWARE COMPLETE — SOL ACCEPT; UPSTREAM LINUX BUILD EVIDENCE PENDING**

- Scope implemented: `autoloader/rom_builder/artifact_engine.py` defines immutable source pins, exact allowlisted recipes, build requests, results, source audits, command logs, artifact records, checksummed manifests and reproducibility status. Coreboot, Libreboot and legacy Canoeboot builders are fail-closed adapters; the fabricated moving-release prebuilt catalog, automatic download and blob-injection paths are removed.
- Pinned sources: Libreboot/lbmk 26.01 commit `1cf3181537b8d1fe1df0e91681f700850a3d9bf6`, tree `0cb9451c6e2e265d3aa5b7e371752d45cab98f53`; Coreboot 26.09 commit `8b0e34f90e08547cf19e550a43ed72f69c39d6ed`, tree `1cb70fc2c4a0229fa17991b19cf6916b71f75ba7`; lbmk's nested X200 Coreboot source commit `fcd716d9a272461da1fdbaf1c048eb5a52c7896b`, tree `e16fe240567cb3fa08619c6a23e51c4af142f6ac`; 1vyrain handoff source commit `a3427b34102f8f9887acf65e23d7fd5900fe1d40`, tree `2298fa23e74c7112b7227d8354a0e51404fda4da`.
- Recipe allowlist: T430 returns an official-source 1vyrain human handoff only; it does not download, execute or bundle upstream content. X200 selects only the pinned regular-X200 `x200_4mb` or `x200_8mb` lbmk target and derives the canonical SeaBIOS `libgfxinit_corebootfb` file from pinned `include/rom.sh`. The pinned target emits 31 distinct ROM variants across the two init/display configs and 14 GRUB keymaps; the engine requires the exact inventory and records every variant's source path, size and digest while exposing only the recipe-selected SeaBIOS artifact. X200 and X220 selected artifacts must pass strict `cbfstool -k` inspection and contain exactly one `fallback/payload` entry of type `payload`; each manifest records the executable digest, parser-source hashes, source revision/tree, region and full CBFS inventory. X200's tool comes from lbmk's exact nested Coreboot commit/tree, and `cbfs_image.c`/`cbfstool.c` parser sources are hash checked. X220 requires a compatible plan and complete matching Phase 03 original backup on separate persistent destinations; exact descriptor/ME/GbE slices are derived from the two equal full-chip reads, original and generated descriptor maps are checked against the observed map, and every non-BIOS region in the generated full-chip image must byte-match the original. No X220 full-chip artifact can pass without this provenance.
- Validation: source origin, full commit SHA, tree SHA, clean status and pinned-file hashes are checked; missing, extra, colliding, symlinked or wrong-size X200 outputs fail closed, with no implicit fallback. X200 and X220 resolved Kconfig, exact image size, selected artifact-copy hash and CBFS payload inventory are checked. X220 pre-build checks use artifact-independent `BuildIntentEvidence`; only after generated output passes CBFS, IFD map and non-BIOS preservation checks is the actual artifact digest bound into the finalized operation plan. X220 also records the pinned ifdtool executable identity, original-backup manifests/checksums and preserved region list. Output roots must be outside the application tree. Commands run with a reduced build environment and logs are checksummed. Manifests record source/config/blob/toolchain/command/artifact hashes, sizes, regions, backup provenance, CBFS inventory, failure and reproducibility status.
- Build environment update: a native Windows build request still returns `LINUX_BUILD_ENV_REQUIRED`. Ubuntu 26.04 WSL is now installed and available. The pinned Libreboot checkout matches its release commit/tree, and its pinned dependency list was installed except for `g++-mipsel-linux-gnu`, which is not needed for the x86 X200 target. No Coreboot/Libreboot compilation was run and no firmware artifact was produced. Fake parser fixtures are not generated-ROM structural or upstream build evidence.
- Software review and checks: SOL accepted implementation commit `dec5cf903c8c4d09b464f14a38d84207f24a523a` and corrective commit `7c5b14e270c34b4a9856dfbcafdec029be011c8e`. Final software gates: `tests/test_artifact_engine.py` — **25 passed**; full `python -m pytest -q` — **152 passed**; `mypy autoloader` — **no issues in 87 source files**; editable install, CLI help, and `git diff --check` passed. The corrected X220 `print -r COREBOOT -k` nonverbose parser accepts the pinned six-column table without an FMAP marker and rejects unexpected verbose rows. X220 build intent is validated before compilation, then the generated file's actual digest is bound into the finalized operation plan only after artifact validation. SOL acceptance covers the software implementation, not actual firmware builds or hardware.
- Integration/build/hardware limitations: Phase 08 routes the artifact engine through explicit CLI/TUI workflow actions. Ubuntu 26.04 WSL is available, but no actual firmware compilation or generated-ROM CBFS output was obtained. No measured chip capacity/voltage, physical probe/read/backup/write/read-back, recovery, boot test, IVprep, or destructive media operation occurred. No artifact is flash-ready; all three hardware workflows remain **HARDWARE PENDING**. Phase 10B later added a guarded X200/X220 writer, but the native Windows host's durable directory-fsync gate blocks invoking it.
- Later Windows regression evidence: `C:\Users\Alex\AppData\Local\Temp\librecore-autoloader-v1-baseline\Scripts\python.exe -m pytest -q` passed **388 tests, 8 skipped in 674.32 seconds** before the isolated Git identity correction. SOL accepted the correction, which revalidates the operation-scoped identity after postprocessing and before any COMPLETE manifest is published. Its focused artifact/X200/X220 suite passed **77 tests with 1 skipped**; mypy is clean on 102 source files. The post-correction full Windows suite passed **395 tests, 9 skipped in 877.46 seconds**, exit 0. The focused fixtures use synthetic outputs and fake command runners. No actual upstream ROM build was attempted.

## Phase 05 — T430 1vyrain and IVprep handoff

**Status: SOFTWARE COMPLETE — SOL ACCEPT; HARDWARE PENDING**

- Implementation commit: `22269468d05091e8989c4ae6a4ed2427a1f83bed`.
- Phase 05 corrective commit: `24ea28932ddbccb152b2b9aaed924f9f4b131481`.
- SOL-recommended v1 checkpoint regression: `4f359c2060e3ca809b2435830781aa95d91cdcec`. It constructs a correctly checksummed v1-format checkpoint without the new fields and verifies resume rejects it as an unsupported schema/version.
- Scope: added `autoloader/reversion/t430_handoff.py` as a human-operated, evidence-gated T430 workflow. It parses only the pinned upstream BIOS token shape, requires exact identity/vendor evidence, and blocks unknown or contradictory data. It never downloads, launches, flashes, or reports a completed installation.
- Upstream provenance: 1vyrain source commit `a3427b34102f8f9887acf65e23d7fd5900fe1d40`, tree `2298fa23e74c7112b7227d8354a0e51404fda4da`; raw pinned `README.md` SHA-256 `5af0f463e56e8fcfbc6d3b57224859e755e701d4e04087167c9e335b14e8c33a`; raw `start.sh` SHA-256 `d00089253cb56a8967e242a9e73496c413a7c3d378044968acbc6278b31e873a`. The parser follows the third whitespace token rule and accepts only a version it can safely reduce below `265`; bare, malformed, or ambiguous strings remain UNKNOWN. The pinned README publishes Rev 5 at [this Google Drive link](https://drive.google.com/open?id=1yusq98ja6NmI4G4txKVueFqY_ZEwaZvO) with MD5 `dd30313a8665e870360920b00cec55c4` only. The workflow requires the operator-reported MD5 to match that value, but MD5 is a weak legacy checksum and does not authenticate the image; the user-reported SHA-256 is only a local fingerprint. The application does not download, include, verify or authenticate the image. IVprep source commit `7faecc72be8776be32f37e7ff00af22a25dfc3cf`, tree `e5c684e3dc5b90d7e70122229a8b46bd4e4bd012`; README SHA-256 `e1b8bc1feea5d8b52e5bacc8fc520a98cb21b125824800a7662d6119fb8492e5`; `downgrade.bat` SHA-256 `cb5528d6f1d001937b224f3d9b7f075a928c2692e12d8f74b24533dd12d2dd31`. These hashes identify source instructions only; no IVprep binary or 1vyrain image is included or verified. The upstream redistribution restriction is respected by providing links only.
- Human gates: before either external handoff, a named user attests BIOS passwords/settings, AC power, battery level and device fingerprint. IVprep instructions explicitly state Windows 64-bit, `Flash BIOS Updating by End-Users=Enabled`, `Secure RollBack Prevention=Disabled`, no Administrator launch, WinFlash elevation, reboot/BIOS-region write and the power-loss bricking risk. For 1vyrain, current-capture preflight requires UEFI, Secure Boot disabled and an explicit intended direct onboard-USB path without dock/ultrabase/hub; media evidence requires the pinned Rev 5 source, matching user-reported MD5, DD-mode write attestation, image size, local SHA-256 fingerprint and media identity from the same human. Each approval remains UTC-timestamped and device-bound. These human attestations do not mean the application performed or verified the action.
- Restart safety: the session requires an explicit checkpoint path before issuing an approved descriptor. Version 2 atomic checksummed JSON binds the operation ID, raw device evidence, fingerprint, capture, approval actor/time, current preflight and media evidence; checkpoints lacking the new attestation fields are rejected. Resume revalidates source-pinned compatibility and restores only outstanding IVprep/1vyrain handoffs; every return requires a fresh same-device capture strictly later than approval. A valid return atomically consumes the checkpoint and records return evidence, so consumed approvals cannot be replayed. A new 1vyrain approval after IVprep retains the consumed IVprep record in a history file.
- Legacy API quarantine: `IVPrepManager.download/execute_downgrade/verify_downgrade_success`, `WinFlashRunner` process APIs, and `ReversionChain` execution/verification methods now fail closed. A zero-subprocess regression covers direct calls; successful process exit output cannot claim firmware success.
- Software evidence after the Phase 05 correction and accepted v1-checkpoint follow-up: focused handoff/flasher tests — **69 passed**; the last full suite before the test-only v1 fixture — **195 passed in 90.64 seconds**; mypy — **no issues in 88 source files**; CLI help and `git diff --check` passed. The full suite was not rerun after adding the one targeted v1 regression. Tests use synthetic evidence and mocked/disabled legacy paths only.
- At Phase 05 completion, the service was standalone; Phase 08 now routes approval and fresh return attestations through CLI/TUI. No 1vyrain image was downloaded or run; IVprep/WinFlash was not downloaded or run; no hardware was detected for this workflow, BIOS was changed, firmware written, media written, or post-install boot/validation performed. Human-entered preflight and media evidence is not physically attested by this code. The checkpoint checksum detects corruption but is not a keyed signature against an editor. T430 remains **HARDWARE PENDING**.

## Phase 06 — X200 Libreboot workflow

**Status: SOFTWARE COMPLETE — SOL ACCEPT; LINUX BUILD AND HARDWARE PENDING**

- SOL accepted the software scope after implementation commit `91ec91edaadba1e19dba4a15efc4dbe040680377` and CBFS acceptance correction `b06293ebba4d57c6809eb06e7ac636e96e2902d4`.
- Tasks 06.01–06.02: `create_x200_build_intent` selects only the pinned `x200_4mb`/`x200_8mb` recipe from one observed 4/8 MiB chip. The shared validator requires exact regular-X200 DMI/MTM evidence, rejects Tablet/X200s/X200si and conflicting identities, and checks the external programmer, wiring, output and observed chip voltage at 3300 mV. This evaluates supplied observations; it does not detect hardware.
- Tasks 06.03 and artifact binding: the Phase 04 `ArtifactEngine` now requires and records the X200 pre-build intent digest. The workflow accepts only a COMPLETE manifest for the exact recipe, pinned source/config and 31-variant lbmk inventory; it rereads checksummed command logs, persisted manifest/sidecar and the one staged selected SeaBIOS/corebootfb image, then verifies its current SHA-256 and exact observed-chip size. The final `OperationPlan` and proposed external SPI plan bind the selected artifact and persisted manifest fingerprints.
- Tasks 06.05–06.06: `capture_backup` is a workflow facade over the read-only `BackupSetService`. It requires its already validated BuildIntentEvidence and delegates exact probe/topology, two independent full-chip reads, persistent separate volumes and manifests to the accepted service. `RecoveryReadinessService` rereads the copies/manifests before artifact acceptance and again before staging. The tests use only a fake SPI transport and test storage inspector.
- Durable state: a checksummed `OperationJournal` binds the final plan, device fingerprint, selected artifact file and backup checksums. Its only workflow checkpoint is `BACKUP_VERIFIED`; exact resume revalidates the same plan and changed evidence is blocked. No approval is issued/restored. `request_flash` and `request_restore` always raise unavailable safety errors; the proposed plan fixes scope to whole-chip external SPI and leaves write flags false, read-back and boot `NOT_PERFORMED`.
- Tasks 06.04 and 06.07–06.10 remain incomplete: detailed physical disassembly/programmer instructions, real external SPI write, physical read-back, cold boot/function checks and hardware recovery qualification are not provided by this software change. Phase 08 routes its software stages through CLI/TUI; no physical operation has been performed.
- Software checks after the CBFS boundary correction: `tests/test_x200_libreboot_workflow.py` plus `tests/test_artifact_engine.py` — **47 passed in 34.63 seconds**; full `python -m pytest -q` — **218 passed in 113.37 seconds**; `mypy autoloader` — **no issues in 89 source files**; CLI `--help` and `git diff --check` passed. Added regressions reject null/other payload types, zero-sized data, and duplicate `fallback/payload` entries. Positive fixture evidence is synthetic and explicitly not an upstream build or hardware success.
- Limits: this Windows 11 host returns `LINUX_BUILD_ENV_REQUIRED` before starting build subprocesses or creating output. No Libreboot build or release-image import was performed; no physical probe, voltage measurement, backup, write, restoration, post-write read-back, cold boot or recovery test occurred. At Phase 06 staging acceptance no writer was integrated; Phase 10B later added a guarded external X200/X220 writer. X200 remains **HARDWARE PENDING**.

## Phase 07 — X220 Coreboot workflow

**Status: SOFTWARE COMPLETE — SOL ACCEPTED (SOFTWARE ONLY); LINUX BUILD AND HARDWARE PENDING; CLI/TUI INTEGRATION ACCEPTED IN PHASE 08**

- Implementation commits: `44521f1b24280bbb9b00f963452c7a7c22437615`, `82fa48efb23f368aafa3b1f436bcdbd424713cc0`, `fa2e71b090abdd71be87c3ef6b3c625a196d1181`, and `d9cd119c7bbbe370bbc5ac2e379387112f70006f`.
- SOL decision: **ACCEPTED Phase 07 for software staging** after reviewing the implementation and reported verification results. LUNA's local `python -m pytest -q` run passed **240 tests in 149.07 seconds**. The root agent's independent rerun passed **240 tests in 144.19 seconds**, `mypy autoloader` clean on **90 source files**, `python -m autoloader --help`, and `git diff --check`.
- `autoloader/workflow/x220_coreboot.py` adds a standalone evidence-gated X220 staging adapter. It requires exact X220 identity, one observed 8 MiB SPI chip, external SPI programmer/wiring at the same observed 3300 mV, and a complete, nonoverlapping IFD/GBE/ME/BIOS map with a 3 MiB BIOS region.
- Backup capture is a facade over `BackupSetService`; backup acceptance and subsequent revalidation require the two independent full-chip reads, separate persistent storage, current manifests and checksums, and safe file/manifest paths. The workflow derives descriptor, ME and GbE input slices exclusively from the verified full-chip original dump. Recovery readiness is checked before and after artifact validation.
- The artifact acceptance boundary requires the pinned Coreboot 26.09 recipe and revalidates the persisted COMPLETE manifest and checksum sidecar, exact manifest-selected `artifacts/coreboot.rom` path and resolved containment, selected image hash/size, command-log paths and hashes, source/tool files against pinned hashes, CBFS payload inventory, exact ifdtool regions, and byte preservation of every non-BIOS region. Windows drive/UNC paths, symlinked path components, and log traversal are rejected. It independently checks the finalized plan and backup provenance.
- A durable `WorkflowSession` journal binds the finalized artifact plan at safe `BACKUP_VERIFIED`. The only positive outcome is `STAGED/HARDWARE_PENDING` with a proposed whole-chip external SPI plan. Write and restore entrypoints raise unavailable safety errors; the plan leaves read-back and boot `NOT_PERFORMED`.
- Synthetic software evidence: `tests/test_x220_coreboot_workflow.py` exercises the positive staging path and blocks identity/MTM aliases, chip count/capacity/voltage, malformed region maps, CBFS payload type/size, external same-bytes artifacts, traversal/symlink paths, changed source hashes, altered backup copies, plan mismatch, non-BIOS byte changes, and direct writes/restores. No test performs real hardware I/O or firmware compilation.
- Software checks: 22 focused X220 tests passed; LUNA's local full `python -m pytest -q` run passed **240 in 149.07 seconds**; the root agent's independent full run passed **240 in 144.19 seconds**. Mypy reports no issues in 90 source files; CLI help and diff-check passed. SOL reviewed these reports and accepted the software scope; SOL did not run the test suite.
- At Phase 07 staging acceptance, the Windows host had no supported Linux build environment, so no Coreboot build ran. Ubuntu 26.04 WSL was installed later as recorded in Phase 04; still no physical probe, measured voltage, physical backup, firmware write/restore, post-write read-back, recovery procedure, or boot test occurred. At Phase 07 staging acceptance no writer was integrated; Phase 10B later added a guarded external X200/X220 writer. X220 remains **HARDWARE PENDING**.

## Phase 08 — Shared application controller and CLI/TUI integration

**Status: SOFTWARE COMPLETE — SOL ACCEPT; LINUX BUILD AND HARDWARE PENDING**

- Implementation commit: `3c6da0c260eaae8f436532244ca824dafc5bdb9f` (`Integrate shared V1 workflow controller routes`); worker lifecycle correction: `3f6a8a25b2b61dada49690a3bf71b8ae2da69d65` (`Keep active workflow results visible in the TUI`). Both Click commands and Textual screens use one `ApplicationController` for only the T430/1vyrain, regular-X200/Libreboot and X220/Coreboot profiles. Typed controller status exposes reasons, evidence references, operation summaries, checkpoint references and authoritative next actions; the pathway screen refreshes when an action screen returns.
- T430 route: the CLI and TUI submit versioned human approvals, preflight/media attestations and stage-specific post-reboot return evidence to the existing durable handoff session. Fresh DMI/BIOS capture is local and explicit; untrusted candidate JSON cannot advance the session. IVprep and 1vyrain remain manual upstream handoffs; the app does not download, execute or write media.
- X200/X220 routes: an explicit versioned request creates a path-bound workflow checkpoint from fresh local identity and read-only SPI evidence plus separately identified user-reported measurements. Backup actions capture or reopen the two-read, two-volume backup through the validated service. Build invokes the pinned ArtifactEngine after revalidating the intent and backup; the X220 route passes only the validated descriptor/ME/GbE slices derived from that backup. Artifact acceptance and resume reopen manifests/files and rebuild trusted result objects through the service validators rather than deserializing result JSON. Journals alone do not reconstruct trusted plans. User-requested actions may perform physical read-only probe/backup operations; no such action was invoked during this implementation pass.
- At Phase 09 acceptance, physical writes, restore, post-write read-back and boot were outside the USB builder's scope. Phase 10B later adds a guarded X200/X220 write/read-back route; restore and boot remain unavailable. On this Windows host, the explicit firmware build request stops with `LINUX_BUILD_ENV_REQUIRED` before starting build subprocesses or creating build output. Imported evidence stays candidate-only; its checksum is corruption detection, not authenticity.
- Worker visibility: an active Textual action keeps its screen mounted and blocks screen Back, the global `b` binding and `q` quit until its controller result is displayed. The regression holds a fake request in a worker, attempts all three exits, then confirms the result appears before navigation resumes.
- Final software gates after the worker correction: `python -m pytest -q` — **276 passed in 167.44 seconds**; `mypy autoloader` — **Success: no issues found in 97 source files**; `python -m autoloader --help` — passed; `python -m autoloader prepare-workflow --help` — passed; `git diff --check` — passed. Tests use fake transports, fake artifact engines and synthetic workflow evidence. No hardware I/O or firmware build was run.
- SOL formally accepted the Phase 08 software scope after its second review. SOL reviewed the reports; it did not execute the test suite. The final local software gates were 276 tests passed in 167.44 seconds, mypy clean on 97 source files, CLI and `prepare-workflow` help passed, and `git diff --check` passed. Implementation commits are `3c6da0c260eaae8f436532244ca824dafc5bdb9f` and `3f6a8a25b2b61dada49690a3bf71b8ae2da69d65`; documentation commit `c3956d20ccd3ab1550516464b09ebc526a05895c` records the implementation before the acceptance decision.
- Remaining release gates: no pinned upstream Linux build or generated-ROM tool output was produced; no real-device probe, measured voltage, backup, firmware write, IVprep downgrade, media write, restore, post-write read-back, recovery operation or boot test occurred. T430, X200 and X220 remain **HARDWARE PENDING**. Phase 09 software is now accepted; actual Archiso build, QEMU boot and media validation remain pending.

## Phase 09 — Live USB, offline operation and packaging

**Status: SOFTWARE COMPLETE — SOL ACCEPT; LINUX BUILD/BOOT PENDING**

- Scope: replace the permissive Archiso script with a strict host/profile/snapshot contract; build and install the application wheel with a closed runtime dependency set; make offline mode local-only and verified or fail before build; confine temporary cleanup to an owned directory; remove arbitrary ROM inclusion; and make startup/service failures visible without root-shell fallback or restart loops.
- Implementation commits: `4a5343e` (`Add guarded Archiso candidate builder`) and accepted correction `f46e312` (`Validate Live USB target install and offline isolation`). The `build-usb` Click command invokes the shared fail-closed builder contract; `usb` remains directory preparation only. The candidate contract pins the official 2026-09-29 Arch archive, Archiso 91-1 and package-list SHA-256, enforces trusted package signatures and exact database hashes, validates the BIOS/UEFI profile, records host package inventory and the Archiso executable owner/hash, and preflights/records pip, setuptools >=61 and wheel. Wheel creation uses a temporary package-only copy and rechecks the source checkout. At the original Phase 09 acceptance checkpoint (later superseded by the rootless online-execution integration below), offline mode used a route-free namespace before work/output with `unshare --net` as root or `unshare --user --map-root-user --net` when unprivileged, then prefixed build commands so children inherited the namespace. The current offline path fails closed before work/output because the reviewed rootless no-egress launcher remains unavailable.
- After `mkarchiso`, the builder validates the image target root before publishing an ISO bundle: venv interpreter, AutoLoader package and dist-info/entrypoint, runtime imports, executable startup script, systemd unit and enablement, hook marker bound to the wheel SHA-256, plus read-only `chroot` import and CLI-help checks. ISO, logs, provenance and `SHA256SUMS` are staged privately and exposed together in `candidate-bundle` through one atomic directory rename. The app venv uses `--system-site-packages` with runtime dependencies installed from the locked Arch package set. These target-root checks do not prove that an ISO boots or that service startup works on a booted system. There is no ROM/backup/credential import path.
- The corrective review revision resolves SOL findings for partial ISO publication, same-host archive redirects that changed date/resource, and database-consumption evidence. Fault injection covers copy, write, hash, directory-sync and final-rename failures. Provenance captures retained target sync database hashes when available; when Archiso 91-1 cleanup removes them, it labels the evidence `SOURCE_LEVEL_INVARIANT_ONLY`, records the exact source commit/config path and states that no runtime internal-read capture occurred. SOL accepted the corrective software review.
- Earlier independent software evidence from this host: `python -m pytest -q` — **385 passed, 2 skipped in 652.41 seconds**; `mypy autoloader usb_builder` — **no issues in 102 source files**; top-level and `build-usb` CLI help and `git diff --check` passed. Historical resolver-only focused tests were Arch WSL **68 passed, 2 skipped** and Windows **60 passed, 10 skipped**; the staged-fragment guard checkpoint was Arch WSL **89 passed, 2 skipped**. Current accepted hook-package correction: Arch WSL `python3 -m pytest -q tests/test_usb_builder.py` — **105 passed, 2 skipped**; Windows `python -m pytest -q tests/test_usb_builder.py` — **94 passed, 13 skipped**; `mypy usb_builder` is clean on Arch and Windows. These software tests use fake builder/process fixtures except for the real namespace probe.
- Linux validation correction (2026-10-10): the real Arch WSL offline namespace probe is **UNVALIDATED** because the created namespace still exposes `eth0`; the test skips with that explicit reason. Offline isolation fails closed, so no offline builder validation or offline build has occurred. This supersedes the earlier preliminary claim that the namespace contained only loopback and no routes. The signature-verified Archiso 91-1 package is installed (`pacman -Q archiso` reports 91-1 and `pacman -Qo /usr/bin/mkarchiso` confirms ownership). A third attempt invoked `mkarchiso` and failed BIOS profile validation before package installation because `syslinux` was missing; the output directory remained empty and no ISO was produced. The profile and required package policy now include `syslinux` at canonical LF package-list hash `520974f323810ab01ea39b7ea7b43c1e7ecbabb823bb4bed4ebab6b1a5cf3329`; SOL accepted the syslinux package-list correction. Target-root `chroot` smoke, BIOS/UEFI QEMU boot, ISO media writing and all hardware operations remain unperformed. At that checkpoint no ISO had yet been produced; the later temporary pre-initramfs ISO is documented in the current-status section. Actual Linux build/boot review and real-device workflows remain release gates.
- Online build preflight correction (2026-10-10): the first real Arch WSL `build-usb --network-mode online` attempt stopped before `mkarchiso` with a profile package-list pin mismatch; the output directory remained empty and no ISO was produced. The checked-in package list's canonical LF hash is `4fd531281bd659695271de16bdd60283dc36eff5220ae1f07550c72716126262`, recorded in accepted commit `3a146f8`. Windows worktree CRLF bytes hash to `70032fecf478ee2b0d9c65837837206596592db671cdf00a61bbdfbf27827595`; validation normalizes CRLF to LF and regression tests load the checked-in profile/lock plus a CRLF copy. A second clean online attempt passed the accepted package pin but stopped before `mkarchiso` on `/usr/sbin/mkarchiso`, whose parent `/usr/sbin` is a usrmerge symlink to `/usr/bin`; the output remained empty and no ISO was produced. Canonical mkarchiso path resolution and exact `pacman -Qo` binding to `archiso 91-1` are implemented with positive alias and negative owner tests; SOL accepted this second correction. A third online attempt invoked mkarchiso but failed BIOS profile validation before package installation because syslinux was missing from the profile package list. The output directory remained empty and no ISO was produced. The exact pinned core.db contains syslinux 6.04.pre3.r3.g05ac953c-5 with package-signature metadata. The profile and required package policy now include syslinux; the updated canonical LF package-list SHA-256 is 520974f323810ab01ea39b7ea7b43c1e7ecbabb823bb4bed4ebab6b1a5cf3329; SOL accepted the syslinux package-list correction. At that checkpoint, no later retry had run; the fourth attempt is recorded in the current-status section below. Online candidate build, target-root smoke and BIOS/UEFI QEMU remain pending.
- Target-root symlink correction (SOL accepted; historical checkpoint): the interpreter validation path can traverse the internal Arch usrmerge chain `/usr/sbin/python` -> `/usr/bin/python`. The image-rooted resolver processes POSIX link components relative to `airootfs` and rejects root escapes, cycles, missing targets and non-directory traversal. At that correction checkpoint, focused tests passed 68 Arch cases (2 skipped) and 60 Windows cases (10 skipped), with mypy clean on both. A later temporary pre-initramfs ISO failed both BIOS and UEFI QEMU before app startup; the currently accepted initramfs-fragment fix and build status are detailed in the current-status section below.

## Current Live USB build status (2026-10-13)

The fifth online Arch WSL attempt built a temporary candidate from clean snapshot `349e7961264131f69019ad1ab61cfa950af688a8`, including the accepted source/staged initramfs-fragment checks. The ISO SHA-256 was `9d6fd80e5757b2f5a075fd46f5d60548c782166cacf95378d6bde2ad3dee42fc` (1,285,586,944 bytes); its manifest SHA-256 was `e0fd030367e792bf39913f1145804fd3470a9357263cc141486b2bb7a8857689`. Same-hash QEMU 11.1.2 BIOS and UEFI boots both failed before the live root: BIOS explicitly reported missing `archiso`, `archiso_loop_mnt`, and PXE hooks; both modes failed mounting the real root and entered `[rootfs~]#`. Neither reached the app or service, so there is no guest startup/journal or no-writer evidence. The BIOS and UEFI VGA capture hashes are recorded in the Live USB runbook; both serial logs were empty. Diagnosis found that the profile enabled Archiso hooks without listing their runtime packages. The SOL-accepted package correction now pins the base and per-hook package map, includes those packages and records them in build provenance. Its canonical LF package-list SHA-256 is `7a7a3fa4127ab6fbaaf952be4bbde82f787a7da30d3111601a9e7fccd6334dbd`; the exact pinned core/extra database hashes were verified and contain the required package records. Current focused USB suites pass 105 Arch tests with 2 skipped and 94 Windows tests with 13 skipped; `mypy usb_builder` is clean on both. No rebuild has run after the package correction; successful candidate target-root and BIOS/UEFI app/service boot evidence and media writing remain pending. This update supersedes earlier checkpoints that no ISO had been produced or that the accepted initramfs fragment had not been exercised.

## Phase 10A — Write-intent approval preparation (SOL accepted)

- Review remediation commit `eeaa8f64b2af2d3ff8311aacf7016fc01d1952b4` follows the original Phase 10A commit `d888b59`; SOL accepted the Phase 10A approval-preparation scope. `WriteIntentFactory` reopens the exact `BACKUP_VERIFIED` workflow journal and its X200/X220 model workflow, then reloads and revalidates the persisted COMPLETE ArtifactEngine manifest, selected output, source revision/files, build tools, artifact digest/size/layout, backup pair and recovery record before creating an approval intent. A correctly checksummed `AUTHORIZED` checkpoint and a manifest with changed pinned source revision are rejected by regression tests.
- At Phase 10A acceptance, CLI confirmation required the real process `stdin`/`stdout` objects backed by valid OS file descriptors and TTY handles, and TUI issuance was not yet integrated. Phase 10B later adds a dedicated mounted-screen challenge and receipt. Tests substitute a private OS-handle verifier only inside the fake frontend fixture; they do not establish production authentication. Approval persistence is unavailable on Windows because this ledger requires durable parent-directory fsync; unsupported or failing fsync blocks issuance.
- Checksums detect accidental corruption but do not authenticate approval, workflow or manifest files against a malicious process running as the same local account or administrator; that actor can alter code or roll back local files. Phase 10B reopens and revalidates device, probe, backup, artifact and journal evidence immediately before consuming any approval.
- Phase 10A itself does not call a writer or transition the operation journal to `WRITE_STARTED`/`WRITE_VERIFIED`. Its final software evidence for `eeaa8f64b2af2d3ff8311aacf7016fc01d1952b4` was `python -m pytest -q tests/test_phase10a_write_approval.py` — **18 passed in 177.58 seconds**; `python -m pytest -q` — **320 passed, 2 skipped in 339.26 seconds**; `python -m mypy autoloader` — **no issues in 98 source files**; CLI help and `git diff --check` passed. All hardware operations remained unperformed.

## Phase 10B — Guarded external write and read-back (SOL accepted; software only)

- The Phase 10B service accepts only the X200 Libreboot and X220 Coreboot external SPI workflows. It reopens the exact safe staged journal and pinned COMPLETE artifact, revalidates full-chip backup and persistent recovery evidence, obtains fresh local identity/probe evidence and live human-reported voltage/wiring attestations, binds a one-use exact-action approval, and consumes it atomically before the writer boundary. It records `WRITE_STARTED` before the fixed no-shell command. Timeout, nonzero exit or unexpected runner failures remain uncertain and never trigger an automatic retry. A separate full-chip read-back is hash/size compared with the approved artifact before `WRITE_VERIFIED`; boot remains `NOT_PERFORMED`. Raw restore remains separately blocked. The legacy arbitrary writer APIs remain quarantined.
- The Click `flash` command is routed through `ApplicationController` and requires `--profile`, exact staged `--checkpoint` and `--request` paths plus live interactive OS TTY fields and exact-action confirmation. No boolean approval flag, imported approval, or TTY bypass is accepted. T430 is rejected before any external SPI meter prompt. The Textual flash action opens the dedicated `WriteConfirmationScreen` and uses the same controller/service. A private bridge binds the one-use receipt to the active screen instance, mount nonce, challenge digest and operation; exact phrase plus Confirm are required. Setup fields lock after submission and the preview includes actor, instrument, test point, both measured voltages, interface/wiring flags, fixed argv, artifact, manifests and backup copy hashes/paths. Cancel/unmount revokes an unconsumed receipt; navigation is blocked during the worker and the displayed result includes its terminal status/evidence refs.
- Runtime write availability is currently blocked on this Windows host because the approval/journal durability contract requires parent-directory fsync that cannot be established here. CLI gates fail closed before hardware probing/writing in that case. This implementation/test pass invoked no physical probe, backup, build, writer, read-back, restore, IVprep, media operation or boot check. No representative hardware workflow is claimed complete.
- Final independent software evidence: `python -m pytest -q` — **354 passed, 2 skipped in 716.26 seconds**; `python -m mypy autoloader usb_builder` — **no issues in 102 source files**; top-level CLI and `flash --help` passed; `git diff --check` passed. The targeted mounted-screen challenge/cancel/unmount regressions also passed (3 cases). All test plans, journals, transports, writer/read-back runners and hardware results were synthetic/fake fixtures; the run made no real hardware or firmware-build calls.
- SOL accepted the Phase 10B service and CLI/TUI integration software. The current Windows host fails the durable directory-fsync gate before write-side probe/writer invocation. Linux firmware build, network-namespace, Archiso/QEMU validation and all three real-device workflows remain release blockers. No hardware operation or real firmware build was performed.

### Defect register (initial)

| Priority | Finding | Evidence | Planned phase |
|---|---|---|---|
| P0 | Build orchestrator sleeps through fake stages, emits completion and names a ROM that was never built. | `autoloader/orchestrator.py:184-223` | 01 |
| P0 | Flash guide simulates a write/readback and unlocks verification; verify screen displays success regardless of an operation. | `autoloader/ui/screens/flash_guide.py`; `verify_screen.py` | 01 |
| P0 | `OnevyRainManager` accepts any BIOS on provisionally supported models, simulates exploit timing, always approves unlock, and reports flash completion without a writer. | `autoloader/flasher/onevy.py` | 01 |
| P0 | Baseline CLI write path was not model/artifact/topology validated; `--yes` bypassed interactive acknowledgement. | `autoloader/__main__.py` at baseline | 01 (quarantine), then 02–03 |
| P0 | Backup selects a database-assumed chip; internal path reads only the BIOS region; dual-chip/full-chip coverage is not ensured. | `autoloader/orchestrator.py:120-149`; `flasher/backup.py` | 02–03 |
| P1 | `build-rom` only validates and prints a summary; it does not build despite command description. | `autoloader/__main__.py:123-154` | 01 |
| P1 | CLI, TUI and README command surfaces disagree; README gives an obsolete repository URL and nonexistent `generate-usb` command. | `README.md`; `autoloader/__main__.py` | 01 |
| P1 | Legacy Coreboot/Libreboot builders used guessed or moving configurations, and prebuilt catalog entries were fabricated. | `autoloader/rom_builder/{coreboot_builder,libreboot_builder,prebuilt}.py` | 04 IN PROGRESS; integration and real build remain |
| P1 | Live USB command reports a prepared directory, not a built image; ISO build/boot are unverified. | `autoloader/__main__.py`; `usb_builder/` | 09 |
| P1 | IVprep can download and execute a BIOS downgrade through the chain without a dedicated human authorization checkpoint. | `reversion/chains.py`; `reversion/ivprep.py` | 02, 05 |
| P2 | Compatibility data and README enumerate more machines than the three planned V1 reference workflows; support claims lack per-variant evidence. | `database/models.py`; `README.md` | 02, 07, 11 |

### Feature matrix at baseline

| Category | Current features |
|---|---|
| Operational and tested (software only) | Database lookup/config validation; some detector/parser logic; backup-pair comparison utilities; screen construction/composition. Tests do not establish a safe hardware workflow. |
| Implemented but untested | Firmware toolchain builders, some CLI flash/backup/restore wrappers, IVprep execution path, Archiso build and QEMU harness. No real target workflow is accepted. |
| Partially implemented | Model/BIOS detection, model compatibility data, two-pass flash backup, read-back utility, Coreboot/Libreboot builders, USB payload preparation. |
| Simulated or missing | Orchestrator build, TUI flash result, TUI verification report, and `OnevyRainManager` unlock/flash outcomes are simulated/canned at baseline; model-specific authorization/state machine and full-device recovery evidence are missing. |

## Earlier Phase 04 cross-platform regression evidence

At that earlier checkpoint after the accepted Git identity correction, the full Windows suite passed **395 tests, 9 skipped in 877.46 seconds** and the full Ubuntu suite passed **402 tests, 2 skipped in 427.12 seconds**, both exit 0. The first Ubuntu run passed 400 tests, skipped 2 and failed 2 because of test-only portability assumptions; both were corrected before that passing rerun. Native Ubuntu mypy then reported a platform-specific `ctypes.windll` attribute error, which the later guarded Windows API lookup below resolves. No upstream firmware build, hardware, or media operation occurred.

## Full upstream source-pin audit (SOL accepted)

The raw-byte audit found CRLF-derived pins across the ten enforced lbmk 26.01 source files and in the artifact engine's 1vyrain handoff descriptor. Pins now use exact upstream raw bytes; X220 pin corrections remain included. Nested lbmk-Coreboot parser pins and IVprep handoff pins were independently checked and already matched. Regression tests assert source-pin maps and recipe config/hash agreement. Full hash values and commit/tree provenance are recorded in [the Phase 04 artifact-engine audit](PHASE04_ARTIFACT_ENGINE.md). Focused audit-related tests pass: 76 passed, 1 skipped; Windows mypy is clean on 102 source files. SOL accepted the source-pin correction. This only corrects source identity metadata: no upstream build, hardware operation, media action, or firmware validation occurred.

## Phase 04 X200 U-Boot compatibility follow-up — SOL accepted

The pinned X200 `x200_4mb` build's U-Boot target config requests `objcopy --target=efi-app-x86_64`, which fails for the authenticated x86-64 ELF input; `--output-target` is the architecture-appropriate option. The one-line email patch changes only the x86_64 option; IA32 stays unchanged. The ArtifactEngine applies it only to the disposable lbmk copy and leaves the canonical Libreboot checkout untouched. Follow-up validation now matches lbmk's observed cache behavior: `src/u-boot/x86_64` may point to the exact local cache directory under `cache/clone/source.denx.de/u-boot/u-boot.git` or `cache/clone/github.com/u-boot/u-boot.git`, and the corresponding cache must remain under the disposable root, have the expected `.git` metadata location, configured pinned HTTPS remote, and exact U-Boot base commit/tree. Other local origins, symlinked paths and mismatches fail closed.

The final U-Boot tree is pinned to `12e245c8ad4d68099c7127afd5c61a0e36992b1c`; the preceding seven-patch stack produces `3ef37c6bc3181317f609d5a6367aa14be7d58f86`. Every applied commit is checked by applying that exact pinned patch file to a temporary index and comparing the generated tree with the commit tree, so a same-subject content alteration cannot pass; the commands/logs are recorded. A fresh disposable reconstruction confirmed the seven-patch tree, overlay commit/tree, clean source state and `arch/x86/config.mk` as the sole overlay change. Synthetic tests cover the local cache layout, remote/base mismatches, path escape/symlink rejection, fixed final tree and same-subject altered patch content. The ArtifactEngine/X200/X220 suite passed **83 tests in 45.74s**; `mypy --platform win32 autoloader` found no issues in 100 source files; `git diff --check` passed. The Windows-targeted overlay/cache helper tests passed 1 and skipped 1 because Windows denied symlink creation (`WinError 1314`); Ubuntu focused coverage exercises the symlink case. SOL accepted this software/provenance correction. No upstream ROM build, artifact publication, hardware or media operation occurred.


## Phase 09 post-build package inventory correction — SOL accepted

The post-build package inventory validates the target root plus `var/lib/pacman/local` as contained, non-symlink directories with local package records before calling pacman. It queries only that image-local database using fixed argv `pacman --config /dev/null --dbpath <airootfs>/var/lib/pacman -Q`; merged output remains subject to the strict parser, so warnings, malformed or duplicate rows, missing required packages, unsafe or missing DB paths, or nonzero exit prevent candidate publication. Tests cover exact argv, accepted rows, rejection cases, and nonzero-exit no-publication behavior. The acceptance checkpoint passed 79 tests, 2 skipped; mypy and diff-check passed. SOL accepted the correction. A later temporary candidate and failed QEMU results are recorded in the Live USB runbook. No hardware or media operation occurred.

## Earlier Live USB boot investigation and KMS-only derivative

The 2026-10-13 clean Arch snapshot `d87bc03a6b94bdde86ff3e3ebf89b24df0b4b275` (tree `0a433bfc6382c56e24b0b5c2bb766ef3d6ff246a`) produced a 1,339,064,320-byte candidate ISO with SHA-256 `b96f83ba84e67ab7e747c95296319d549b57d167010d1ce98a43be2d45c3e4a5`. Build provenance hash: `d1d74d087b8598e3b3ef86973dd2d6f5fa1b22154864ea37e98becbe5da082c3`. That candidate used the original pinned initramfs fragment and included `kms`.

Bounded QEMU 11.1.2 runs had no network device or firmware-write interface. BIOS (`-accel tcg,thread=multi -m 3072 -smp 4`) mounted the ISO and loop-backed live root and reached the Arch root shell, but the AutoLoader app/service status was not visible. tty2 stayed black and serial was empty. UEFI with OVMF pflash and the same CPU/memory flags reached udev and `archiso_pxe_nbd`, then stalled at event triggering and did not mount the live root. Its guest serial/journal also did not show app/service status. Captures and exact commands are recorded in `usb_builder/LIVE_USB_RUNBOOK.md`; the ISO hash was unchanged. These are failed/incomplete emulator acceptance results, not hardware tests or release success. No same-hash BIOS/UEFI run has demonstrated app startup, active service, journal entries or no-writer behavior.

SOL accepted a separately named and SHA-256-pinned USB profile derivative that removes exactly the `kms` hook while preserving all other hooks/order, every PXE hook, xz compression and the pinned compression options. Its source and staged bytes are checked before and after `mkarchiso` and before publication; the lock and provenance record its identity and hashes. The package map is unchanged. Windows `tests/test_usb_builder.py` passed 102 tests with 13 skipped; derivative-focused cases passed 6; `mypy usb_builder` and diff-check passed. The derivative was exercised by candidate b2 below. The `kms` omission remains an experiment; it has not resolved the startup block. Physical display and keyboard checks on T430, X200 and X220 are also pending.

## Live USB derivative candidate b2 and WSL namespace result

Candidate b2 was built from clean USB-only snapshot commit
`1197d9040a39d6da012f459b8bb1f45385ee0072`, tree
`d2588ddff4f0c5d4115565f47360314553106d38`. Its 1,012,215,808-byte ISO has
SHA-256 `afde8ba28025530852547ec5042dd94ecece5a9b1d24f5da141365861cc35cbd`;
the build-provenance JSON has SHA-256
`c8ad6f6c1b04ed87626a4410566ed11e5d23893293a8945d4e865c28d63e481f`. Target
root installation and packaged CLI checks passed. The inspected 79 MiB
initramfs contained 2,005 CPIO paths and omitted `kms`, retaining the PXE
hooks; the exact full initramfs digest was not preserved in provenance.

Same-hash BIOS QEMU ran for 310 seconds and UEFI QEMU for 922 seconds. Both
reached systemd on the live system but remained in early startup without
AutoLoader/service evidence. BIOS waited on Namespace Resource Manager; UEFI
repeatedly failed `systemd-nsresourced.service` and waited on `/dev` node
setup. Neither second TTY showed a login prompt. UEFI serial output stopped at
the boot menu. Both QEMU processes exited 0, which does not indicate guest boot
success. The machine-readable records and captures are preserved in
`docs/v1/evidence/live-usb-b2/` and described in the Live USB runbook.

At the earlier 2026-10-13 checkpoint, the real offline-isolation test skipped
as **UNVALIDATED** on Arch WSL: it used bare `unshare --net`, shared
`/sys/class/net` exposed `eth0`, and raw IPv6 route data contained the exact
kernel sentinel rows. This was later addressed by a strict user-mapped probe
that independently checks namespace-aware views and allows only zero or two
exact sentinel rows. See the current WSL-only result below; no offline build
has been validated.

## Phase 04 X200 GRUB patch-chain identity correction — SOL accepted

The pinned GRUB base is commit `eaa3b8f0f90605a82c6bfda4c5c4b73c58eb81ac` / tree `c10cf577627e980308fb0cb3785cdc669c2582d9`; the ordered ten patch files remain bound by exact hashes. Their ten intermediate tree IDs, ending at `9e76050f8ab01306e51fb28ab88c1f8481c9e26a`, are the deterministic source evidence. `git am` commit IDs vary with committer metadata, so each run's IDs are recorded in its manifest and later checked for exact linear ancestry and matching per-patch trees; generated IDs are not hard-coded. SOL accepted this correction. The focused ArtifactEngine/X200/X220 suite passed 90 tests with 2 skipped in 116.89s; mypy passed on the three touched Python modules and diff-check passed. Synthetic Git tests prove timestamp-varying IDs with identical trees are accepted and altered trees are rejected. No actual Linux ROM build or manifest reopen from a real build has occurred; Linux build and hardware workflows remain pending.

## Earlier cross-platform full software regression — SOL accepted portability correction

SOL accepted the X200 Unifont manifest portability correction and test-fixture isolation. Build-time validation still checks the real source font hash and package owner/version; manifest reopen validates the operation-owned staged font and the pinned source identity without requiring the original Ubuntu absolute path to exist on the reopening host. Policy-only approval/controller tests use an explicitly synthetic ArtifactEngine adapter; dedicated ArtifactEngine tests retain strict manifest and staged-file validation.

The full Windows suite passed **451 tests, 15 skipped in 981.30 seconds** (exit 0). The full Ubuntu suite passed **464 tests, 2 skipped in 609.63 seconds** (exit 0). After those full runs, `is_admin()` was changed to guard the Windows-only `ctypes.windll` lookup without changing its Windows result; `tests/test_flasher.py` then passed **26 tests in 9.94 seconds**. Windows and Ubuntu `mypy --platform win32 autoloader usb_builder` both passed with no issues in 102 source files, and native Ubuntu `mypy autoloader usb_builder` passed after the guard. Top-level CLI help and `flash --help` passed. An isolated-backend wheel build succeeded (`libre_core_autoloader-0.1.0-py3-none-any.whl`, SHA-256 `2daae38489cbce1e1c33d0f50e95255e3d7140995279a8cee274343872f15e7e`), and `git diff --check` passed. These software tests use fake/synthetic firmware and hardware evidence only. Actual pinned ROM builds, generated artifact validation, a passing Live USB BIOS/UEFI app/service boot, offline namespace validation, and all three physical hardware workflows remain open; no hardware workflow is claimed passed.

## 2026-10-15 release-gate follow-up

The X200 pinned-source audit passed in Ubuntu WSL, but the ArtifactEngine attempt used no target DMI, observed chip capacity, physical programmer evidence, or complete backup/recovery record. Compatibility therefore remained UNKNOWN; the engine correctly returned `ARTIFACT_BUILD_BLOCKED` before any build command, and the output folder remained empty. The exact attempt and hashes are recorded under `docs/v1/evidence/x200-artifact-preflight-20261015/`. Earlier X200 work compiled some upstream components only; no complete pinned ROM, accepted parser result, or COMPLETE manifest was produced.

For the Live USB issue, the checksum-verified official Arch Linux 2026.10.01 ISO was booted with QEMU 11.1.2 under the same WSL2 TCG environment. The normal BIOS control ran for 600 seconds and showed the same early Namespace Resource Manager and static `/dev`-node startup stalls as b2. A separately gated tty9 shell capture issued one serialized read-only state command and returned to the prompt; at its snapshot, stock `systemd-nsresourced` was still activating with 38.267 CPU seconds after about 40 seconds, while the failed-unit list was empty and its journal had no entries. A longer serial capture records the service timing out; screenshots taken before command completion in that older capture are explicitly excluded. Neither control establishes a root cause. WSL exposes neither `/dev/kvm` nor `/dev/mshv`; a native Linux/KVM comparison remains open. Full BIOS/UEFI app/service boot, offline namespace, Linux ROM builds, and all physical device workflows remain release gates.

## 2026-10-15 offline namespace probe correction — WSL only

The production offline-isolation check now always uses
`unshare --user --map-root-user --net`, including for root, so capabilities
are confined to the child user/network namespaces. It verifies canonical
`/usr/bin/unshare` and `/usr/bin/ip` with exact Arch pacman ownership, hashes
both binaries, and checks route/interface state inside the exact child before
and after a build command. The wrapper closes inherited descriptors, sets
stdin to `DEVNULL`, forces stderr to `STDOUT`, and accepts stdout only as
`PIPE` or an already-open regular log file checked with `fstat`. Socket-backed
stdio, raw file descriptors, devices, FIFOs, caller stdin overrides and other
subprocess controls fail before a child starts. It applies a sanitized
environment and sends SIGKILL to the whole child process group after the
termination grace period even if the group leader exits first. This does not
stop a descendant that calls
`setsid()` and escapes the process group. Such a process remains in the
isolated network namespace but could outlive the wrapper and continue local
filesystem activity; no offline build is claimed until this residual is
resolved or bounded for the actual builder.

On Arch WSL (`6.18.54.1-microsoft-standard-WSL2`, x86_64), the production
probe passed with `archiso 91-1`, `util-linux 2.42.4-1`, `iproute2 7.2.0-1`,
and pacman ownership confirmed for both executables. Its namespace ID differed
from the host; namespace-aware views showed loopback only and no routes, and
procfs contained exactly two copies of the pinned IPv6 null/unreachable row.
Ubuntu WSL independently showed the same namespace-aware state while shared
sysfs still listed stale `eth0`. Raw command/output evidence is under
`docs/v1/evidence/offline-namespace-arch-wsl-20261015/` and
`docs/v1/evidence/offline-namespace-ubuntu-wsl-20261015/`.

After the CRLF safeguard, `tests/test_usb_builder.py` passed 176 tests with 1
skipped on Arch WSL and 163 with 14 skipped on Windows. Win32-targeted and
native Arch `mypy usb_builder` each passed with no issues in 3 source files;
`git diff --check` passed. Targeted descriptor/stdin boundary
tests passed 15 cases on each platform. The Linux process-group regressions
passed in Ubuntu WSL (3 passed, including a forked grandchild that ignores
SIGTERM after its leader exits); the Windows-specific subset passed 3 tests
with the fork-specific case skipped. Win32-targeted and native Arch
`mypy usb_builder` each passed on 3 source files. The namespace probe is
validated only in WSL. No successful offline Archiso build has been produced;
the later invocation that failed on CRLF is recorded below. Native Linux
behavior and mount/loop/chroot requirements inside the namespace remain open, as do
the BIOS/UEFI app/service boot and all physical-device gates.

The 2026-10-15 clean temporary offline build attempt reached `mkarchiso` but
failed with `profiledef.sh: line 3: $'<CR>': command not found`; mkarchiso exited
127. `<CR>` denotes the carriage-return byte in the script. The attempt did
not produce an accepted/published candidate. Repository attributes now pin the
known Archiso text inputs and package list to LF, source preflight rejects
carriage returns in the supported profile scripts/configs, and staged profile
checks repeat before and after the builder and at publication. The focused
Windows and Arch WSL suites and both mypy modes now pass. A later private
mount/PID namespace probe is recorded below; no offline build retry was run
after the LF correction. No hardware or media action occurred.

## 2026-10-15 WSL mount capability follow-up

The production isolation wrapper now adds private mount and PID namespaces to
the existing user-mapped network namespace:
`unshare --user --map-root-user --net --mount --pid --fork --mount-proc`.
The Arch WSL child had distinct PID/mount/network namespace IDs and only the
loopback interface. Temporary mount probes succeeded for proc, sysfs and
tmpfs. WSL denied devtmpfs, bind mounts of `/dev` and `/run`, and devpts with
the `gid=5` required by Archiso. Exact command output is preserved in
`docs/v1/evidence/offline-namespace-arch-wsl-20261015/mount-probe-20261015.txt`.
The test used only temporary directories and did not run mkarchiso. Because
the required chroot device/runtime mounts are unavailable in this WSL
namespace, no offline image build is claimed; native Linux build validation
remains pending. The network isolation was not weakened to bare-root netns.

At that earlier checkpoint, `tests/test_usb_builder.py` passed 176 tests with
1 skipped on Arch WSL (11.10s) and 163 with 14 skipped on Windows (40.61s).
After integrating the production mount preflight and dependency-state tests,
the focused USB suite at that checkpoint passed 234 tests with 1 skipped on Arch WSL
(22.54s) and 219 with 16 skipped on Windows. Native Arch and
Win32-targeted `mypy usb_builder` both pass with no issues in 4 source files.
The suite now has distinct real network-namespace and integrated production
preflight tests. On Arch WSL, the integrated test confirms the route-free
child report and unchanged host mount snapshots. At that checkpoint, it
expected `tmpfs-run` EINVAL to fail closed as an integrity error; a later
disposable syscall comparison showed that EINVAL came from probe-plan flags,
not a WSL limitation. These are WSL/software checks only; this does not
establish native Linux behavior.

Historical pre-errno-correction run: the earlier broad OSError classifier
reported `ARCHISO_MOUNT_CAPABILITY_UNAVAILABLE` for `devtmpfs`, `tmpfs-run`,
and `tmpfs-tmp`; see the preserved, explicitly historical
`production-mount-preflight-20261015.txt`. The stricter errno policy correctly
classified the subsequent `tmpfs-run` EINVAL as an integrity failure. A
disposable same-namespace comparison then showed that the probe passed generic
VFS flags as tmpfs filesystem data: the old call returned EINVAL, while
`MS_NOSUID|MS_NODEV` with only `mode=0755` data and the exact util-linux
`mount(8)` command succeeded. The probe plan now splits VFS flags from
filesystem-specific data for devpts, shared memory, `/run`, and `/tmp`. The
current production preflight reports only the unavailable WSL `devtmpfs`
operation. Route-free evidence passed, host mountinfo stayed unchanged, and no
builder work/output or mkarchiso was invoked. Full details are in
`tmpfs-option-split-diagnostic-20261015.txt`. After this correction, focused
USB tests passed 259/1 skipped on Arch WSL and 244/16 on Windows; at this
checkpoint, full suites and mypy had not yet been rerun. The later full-gate
results are recorded below. Native Linux mount behavior and offline image build
remain unvalidated.


## 2026-10-15 release-gate software checkpoint - SOL accepted (pre tmpfs argument correction)

Before the later tmpfs argument-plan correction, the complete Windows suite
passed **594 tests with 18 platform skips** (864.09s) and the complete Arch WSL
suite passed **611 tests with 1 skip** (679.85s). Focused USB
suites passed 243/16 on Windows and 258/1 on Arch WSL. Native Arch and
Win32-targeted mypy passed on 104 source files; CLI top-level and `flash
--help` passed. The evidence checksum manifest and `git diff --check` passed.
SOL accepted the narrowed mount errno policy, the POSIX system-temp rejection,
and the updated historical/current evidence wording.

At the accepted pre-correction checkpoint, the real Arch WSL production
preflight stopped with `ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE` because
`tmpfs-run` returned EINVAL; the earlier devtmpfs result was EPERM. The
disposable syscall investigation found and corrected a probe-plan argument
bug. The current direct production preflight now stops at the unavailable WSL
`devtmpfs` operation. Route-free namespace evidence passed and the host mount
table was unchanged; no builder work/output or mkarchiso was started. This is
WSL-only, not native Linux validation. A native Linux mount probe, offline ISO
build, working BIOS/UEFI live startup, pinned ROM builds, and all three
physical-device workflows remain open. No hardware action occurred.

## 2026-10-15 post-tmpfs-correction full software checkpoint

Full regression results after the tmpfs mount-argument correction:

- Windows: **595 passed, 18 skipped in 1093.64s**.
- Ubuntu 26.04 WSL: **610 passed, 3 skipped in 740.43s**.
- Arch WSL: **612 passed, 1 skipped in 904.35s**.

Windows-targeted mypy passed on 104 source files from Windows and from isolated
Ubuntu and Arch runs. Native Ubuntu and Arch mypy also passed on 104 files.
Concurrent shared-cache mypy invocations initially errored internally;
sequential no-incremental reruns with isolated caches passed. The cause of the
initial errors was not established. Windows top-level CLI help, Ubuntu
`flash --help`, and `git diff --check` passed.

These are software results; Ubuntu and Arch evidence is from WSL, not native
Linux. The current Arch WSL mount preflight safely stops at devtmpfs with
`EPERM` and creates no builder work or output. Native Linux build validation,
an offline ISO build, successful Live USB app/service boot, and all three
hardware workflows remain pending. V1 is not release complete; no hardware
operation occurred.

## Arch WSL QEMU acceleration preflight — 2026-10-15

QEMU 11.1.2 listed `tcg`, `mshv`, `nitro`, and `kvm` in `-accel help`, but
`/dev/kvm` and `/dev/mshv` were absent. The bounded MSHV probe exited 1 with
`Failed to open /dev/mshv` and `failed to initialize mshv: Operation not
permitted`. No accelerated guest path was established in Arch WSL. This is
not a native Linux or QEMU/KVM result; prior WSL TCG boots remain unchanged and
did not reach app/service validation. The exact record is
`docs/v1/evidence/live-usb-b2-diagnostic-20261014/qemu-acceleration-preflight-20261015.txt`.
Native Linux/KVM comparison, offline ISO build, successful Live USB app/service
boot, and hardware workflows remain pending.

## Arch WSL online-candidate readiness check — 2026-10-15

The production mount-capability preflight was run directly from the current
shared checkout. It returned `ARCHISO_MOUNT_CAPABILITY_UNAVAILABLE` for
`devtmpfs`. `execute_build` requires that gate for both online and offline
modes before build work/output creation, so no online build was launched; no
repository sync/download, package installation, `mkarchiso`, or candidate ISO
occurred. The exact command and result are recorded in
`docs/v1/evidence/offline-namespace-arch-wsl-20261015/online-candidate-readiness-preflight-20261015.txt`.
This is Arch WSL evidence only, not native Linux build validation. Existing
workspace changes and prior candidate/TCG evidence were preserved. Native
Linux build, offline ISO build, successful Live USB app/service boot, and all
hardware workflows remain pending.

## Ubuntu WSL trusted-tool preflight — 2026-10-15

A direct production `_establish_offline_network_isolation(subprocess.run)`
call from Ubuntu 26.04 WSL failed closed with
`OFFLINE_NETWORK_ISOLATION_UNAVAILABLE`: `/usr/bin/unshare` resolved, but
`pacman` was absent, so the trusted `pacman -Qo` ownership check could not
start. The run did not reach namespace or mount checks and did not test
devtmpfs. It did not call `execute_build`, create candidate-build work/output,
sync/install packages, or invoke `mkarchiso`. This confirms Ubuntu WSL is not
the pinned Archiso 91-1 build host; it does not replace the separate Arch WSL
preflight, which reaches the mount gate and fails at devtmpfs. Exact command
and result: `docs/v1/evidence/offline-namespace-ubuntu-wsl-20261015/`.
Native Linux, candidate ISO, app/service boot, and hardware gates remain
pending.

## Same-hash b2 nsresourced mask diagnostic — 2026-10-15

Repository and preserved direct-kernel records contained no earlier use of `systemd.mask=systemd-nsresourced.service` or `systemd.mask=systemd-nsresourced.socket`. One bounded 282.40-second QEMU 11.1.2 TCG run used the unchanged b2 ISO SHA-256 `afde8ba28025530852547ec5042dd94ecece5a9b1d24f5da141365861cc35cbd`, kernel SHA-256 `8ebc2c71271e000540f0a7545afd8e73504fcb724671f3d38c720b241842b901`, and initramfs SHA-256 `c7440ebd5164e83e4a28c3f22ba2695af06a0fd29676ae572967ecdb6218e090`. The only added kernel parameters were those two masks; `-nic none` was used with no writable disk or firmware writer. Serial confirms both units were masked to `/dev/null`. At 270s, `sysinit.target` still waited on `systemd-tmpfiles-setup-dev.service`; the debug-shell service had started on tty9, but no guest command was entered. The masks did not clear that observed wait during the run. This is a narrow emulator observation and does not prove whether nsresourced is otherwise causal, establish a root cause, or pass normal boot/app/service validation. QEMU exited 0 after HMP quit; the host controller reported a monitor reset while reading the quit response after QEMU had stopped. Inputs were unchanged. Evidence and hashes are under `docs/v1/evidence/live-usb-b2-diagnostic-20261014/nsresourced-mask-20261015/`. Native Linux/KVM, normal BIOS/UEFI app/service boot, ISO build, and hardware gates remain pending.

## Earlier cross-platform software regression after EC API quarantine — 2026-10-15

The full Windows suite passed **601 tests, 18 skipped in 707.05 seconds**; Ubuntu 26.04 WSL passed **616 tests, 3 skipped in 466.57 seconds**; and Arch WSL passed **618 tests, 1 skipped in 539.81 seconds**. Full-project mypy (`autoloader usb_builder`) passed on 104 source files in Windows, Ubuntu WSL, and Arch WSL. Windows-targeted and native checks were run sequentially with `--no-incremental` and isolated caches. Concurrent shared-cache mypy invocations initially reported internal errors; sequential isolated-cache reruns passed, and the cause was not established. Windows top-level CLI help and Ubuntu `flash --help` passed; `git diff --check` passed before this documentation update.

The full suites include the new fail-closed EC regression. The legacy `ThinkPadECManager` source download/build/flash/verification methods now raise `ECModError` as unavailable, patch reporting is empty, and tests assert zero subprocess/file-write calls and no changes to an existing arbitrary checkout. This quarantines an unsupported packaged API; it does not add an EC workflow or establish simulated hardware success.

These are software checks, with Ubuntu and Arch results coming from WSL rather than native Linux. Native Linux and Archiso build validation, a successful Live USB BIOS/UEFI app/service boot, complete validated X200/X220 ROM builds, and all three physical hardware workflows remain pending. V1 is not release complete.

## Rootless Archiso host preflight checkpoint — 2026-10-16

Added a fail-closed production host preflight for Archiso's non-root `-N`
path. It checks a private account/group (including supplementary groups),
matching real/effective IDs, empty effective/permitted/ambient capabilities,
bounded capability policy, `NoNewPrivs=0`, and distinct non-overlapping subuid
and subgid ranges of at least 65,536 IDs assigned to the invoking UID. The
snapshot lock now binds Archiso 91-1, arch-install-scripts 31-2, shadow
4.20.0.arch1-1 and libcap 2.78-1, plus the signed package identities, helper
hashes/modes/owners and exact map-helper file capabilities (`newuidmap`
`cap_setuid=ep`, `newgidmap` `cap_setgid=ep`).

The focused synthetic USB selection passed **24 tests, 260 deselected**. This
does not validate host-side `-N` execution. On Arch WSL the production route
stops at the root-account check before subprocesses or candidate work/output
creation; the distro also lacks delegated ID maps and the pinned file-capability
state. If a future host passes these checks, `execute_build` still fails closed
until the exact rootless mount/helper path is validated. Thus online and
offline Archiso build execution remain unavailable. No package installation,
ISO build, QEMU run, hardware, or media operation occurred in this checkpoint.
The latest broad suite counts above predate this rootless-preflight change.

## Rootless pacstrap status-propagation correction — 2026-10-16

The disposable Arch VM probe exposed a pinned `arch-install-scripts 31-2`
false-success: `pacstrap -N` logged “no usable package repositories” and
“Failed to install packages to new root” but returned status 0. The exact
command, source identity, output, and control are preserved in
[`namespace-and-pacstrap-probes.txt`](evidence/rootless-archiso-vm-20261016/namespace-and-pacstrap-probes.txt).

A narrowly pinned patch resource now captures the `-N` child status before
cleanup and exits with that status; failed temporary-file cleanup remains a
failure. The source SHA-256, patch ID/hash, and patched-script SHA-256 are
bound in the snapshot lock. Staging helpers require a private, owned,
token-marked operation directory and bind staged file bytes, owner/mode,
operation identity, and the future mkarchiso PATH in evidence. The resource is
included in the `usb_builder` package metadata. This remains an isolated
software correction: `execute_build` is fail-closed, the patch is not
installed over `/usr/bin/pacstrap`, and candidate publication remains disabled
until the rootless execution path and successful build are validated. SOL
reviewed the staging and failure-propagation design and accepted this disabled
software slice.

A separate VM-backed host-preflight check had stopped at `setpriv`: the prior
contract incorrectly reused SHA-256
`4806896cb95e1799b4a0f4754ebbd0665794e563fbc924eb73f3f4ba1828eacc` for all
four util-linux executables. The pinned VM package is `util-linux 2.42.4-1`
(package SHA-256 `95b7096bf4b45274645f6bb96f5b5508af6ea80e5fccba29244abaa53a341962`);
the verified per-file hashes are now independently recorded for `setpriv`,
`unshare`, `mount`, and `umount` in both the snapshot and executable contract.
The correction has exact-lock and per-executable mismatch regressions. This
fixes the incorrect tool identity mapping only: it does not validate the full
rootless builder chain or enable candidate execution.

Focused rootless/pacstrap tests on Arch WSL passed **30**, with 277 deselected.
The Windows selection passed **30**, with 277 deselected; Windows mypy passed
for all four `usb_builder` source files. Tests cover exact
patch context/result hashes, changed/duplicate/missing source context,
path/symlink rejection, operation-root ownership/mode/token binding, staged
tampering, child success/nonzero/no-repository/signal status, timeout cleanup,
cleanup failure, fake mkarchiso failure preventing publication, exact distinct
util-linux binary pins and substitution rejection. These are synthetic tests;
they do not reproduce the real pacstrap package operation.

## Latest software and full-VM checks — 2026-10-16

The final full Windows regression passed **658 tests, 40 skipped in 831.34s**.
The final sequential Ubuntu 26.04 WSL regression passed **694 tests, 4 skipped
in 506.73s**. A prior concurrent Ubuntu run exhausted `/tmp` and produced
cascading setup errors; that run is discarded, its pytest scratch directories
were removed, and it is not counted as validation. SOL accepted the four
model-safety corrections and the Windows/Linux test adaptation. The Windows
suite retains the platform-specific fail-closed durability check; Linux keeps
the successful POSIX durability paths active. Full-project mypy
(`autoloader usb_builder`) reported no issues in **105 source files** on Ubuntu
WSL. Ubuntu top-level and `flash --help` passed; `git diff --check` passed
(Git emitted only the recorded LF-to-CRLF notices). WSL is not native Linux.

The disposable QEMU Arch VM's 2026-10-16 follow-up is recorded in
[`archiso-vm-followup-20261016.txt`](evidence/rootless-archiso-vm-20261016/archiso-vm-followup-20261016.txt).
The required rootless devtmpfs probe returned mount exit 32 (permission
denied), with the VM's mount table unchanged. The exact pinned pacstrap source
and staged status-preservation overlay returned 1 for the deliberately empty
repository; direct `mkarchiso` integration with that incomplete profile also
returned 1 and produced no ISO. These are negative failure-propagation
observations only. The VM snapshot's `build_usb.py` and snapshot-lock hashes
do not match the final working tree; only its `mount_probe.py` and staged
pacstrap bytes match. This is not a successful production builder run, ROM
build, or Live USB boot. At that earlier checkpoint, production candidate
execution remained closed until the current source completed the supported
rootless namespace/mount path, signed package install, full ISO build,
target-root checks, and successful BIOS/UEFI app/service boot. The later
online path wiring is recorded in the section below.

No device was probed and no firmware or recovery operation was performed.
T430 1vyrain, X200 Libreboot and X220 Coreboot hardware acceptance remain
**HARDWARE PENDING**; V1 is not release complete.

## Rootless Archiso `-N` mount-stack preflight — 2026-10-16

The former devtmpfs denial was produced by a root-oriented mount probe and
does not determine whether pinned Archiso 91-1 can use its non-root `-N`
path. A separate preflight now mirrors the pinned `arch-install-scripts
31-2` `arch-chroot -N` mount stack: private root bind, proc, recursive `/sys`
bind, tmpfs `/dev`, six individually validated character-device binds
(`/dev/full`, `null`, `random`, `tty`, `urandom`, `zero`), and tmpfs `/run`
and `/tmp`. It verifies changed user/network/mount/PID namespace IDs, route-
free child state before and after the mounts, private propagation, source
device numbers/owners, cleanup, and unchanged child and host mount tables.

This was the earlier software-only checkpoint. It is superseded by the
rootless online execution integration below; its 23-test and 328-test counts
are historical.

## Rootless online Archiso execution integration — 2026-10-16

Production `execute_build` now requires the pinned non-root host/helper
preflight and the exact isolated route-free Archiso `-N` mount preflight
before creating candidate work or output. Online mode then runs the pinned
build commands directly as the same non-root caller, with a fixed sanitized
environment and the operation-owned, hash-verified pacstrap status overlay
first in `PATH`. The network isolation provenance labels this as online
egress after the isolated preflight; it does not claim build commands remain
inside the probe namespace. The system pacstrap executable is never modified.

Offline mode is still rejected with
`OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE` after the read-only probes and before
work/output creation. No reviewed route-isolation launcher is compatible
with the non-root Archiso `-N` path yet. No current-source package install or
candidate ISO build was run in this implementation slice; the parent is
handling the disposable-VM candidate gate. The previous b2 ISO remains the
latest candidate and its BIOS/UEFI runs did not reach AutoLoader/service
evidence.

Focused rootless builder tests passed **70 tests, 269 deselected** in Ubuntu
26.04 WSL. The narrower production route selection passed **7 tests, 332
deselected**. Native and Windows-targeted `mypy usb_builder` each passed on
**5 source files**. These are software/fake-runner checks, not a successful
current-source VM build or native Linux validation. Offline ISO build,
current-source online candidate build and BIOS/UEFI app/service boot remain
open; all three hardware workflows remain **HARDWARE PENDING**.

## Earlier cross-platform software regression — pre-6cabb23d, 2026-10-16

The full Windows suite passed **693 tests, 45 skipped** (738 collected) in
737.64 seconds, exit 0. The full Ubuntu 26.04 WSL2 suite passed **734 tests,
4 skipped** (738 collected) in 1510.50 seconds, exit 0. Windows skips are
POSIX filesystem, process, Linux namespace/device, and host ownership checks;
Ubuntu skips are two real-host Arch pacman ownership checks and two
Windows-only checks. Full-project no-incremental mypy passed on **106 source
files** on Windows and Ubuntu WSL. Ubuntu CLI help passed. Both JUnit reports,
environment details, hashes, skip reasons, and the focused 21-test SPI safety
fixture run are recorded in
[`evidence/software-regression-20261016/README.md`](evidence/software-regression-20261016/README.md).
`git diff --check` passed with only Windows LF-to-CRLF notices.

The current Archiso v4 TCG guest was resumed without changing accelerators,
then paused with HMP `stop` at 14:55:36 +01:00 before host free space fell
below 2 GiB. It remains incomplete: the guest showed blocked `jbd2/vda-8` and
`python` tasks, and host storage is constrained. Overlay growth and CPU
activity were not proof of useful build progress. The guest and overlay remain
preserved; no current-source ISO, target-root verification, or BIOS/UEFI
AutoLoader/service boot is claimed.

No physical hardware has been touched. The hardware workflows remain
intentionally deferred and **HARDWARE PENDING**. V1 is not release complete.

## X220 pinned-build prerequisite inspection — 2026-10-16

The clean pinned Coreboot checkout is at the expected release commit, and its
`configs/builder/config.lenovo_x220` hash matches the ArtifactEngine pin. The
recipe requires `site-local/descriptor.bin`, `me.bin`, and `gbe.bin`; all three
are absent. The accepted X220 artifact workflow also requires observed chip
topology and a complete verified two-read original backup/recovery evidence.
No build command was run and no synthetic inputs were substituted. Exact
read-only evidence is in
[`evidence/x220-build-prerequisite-preflight-20261016/README.md`](evidence/x220-build-prerequisite-preflight-20261016/README.md).
The X220 ROM build remains blocked pending real device-derived inputs; the
separate Archiso guest is paused and storage is still constrained.

## Post-reboot evidence intake — 2026-10-16

Added one shared `BOOT_CHECK` intake for the CLI and Textual route. X200/X220
reports reopen only the terminal `WRITE_VERIFIED` journal and recheck the
approved derived artifact, full-chip read-back bytes/hash/size, operation and
device fingerprint; X200 also reopens the source-artifact and GbE-preservation
manifest binding. T430 reports reopen only a consumed return checkpoint and
bind the option-1, reboot, Advanced BIOS and fresh-return chronology. Each
intake takes a new local read-only DMI/BIOS capture. It writes one operation-
owned pending sidecar and leaves the terminal write/handoff checkpoint
unchanged. Reopening the sidecar validates its full model-specific schema,
chronology, identity and (for X200) expected MAC even if its checksum was
recomputed.

Every report remains `PENDING_HUMAN_REVIEW` / `NOT_ASSESSED`; operator-entered
checks are not authenticated measurements and never set a hardware-success
flag. The service-only fake selection passed **24 tests in 319.27s**; retain
that as the earlier service checkpoint. The guided Textual form now presents
profile-specific checks and asks for a separate UTC observation time and
evidence reference for each check; X200 MAC observation has its own time and
evidence fields. It still submits the same schema through `BOOT_CHECK`, and an
unsubmitted screen does not claim a pending report exists. The combined
guided-UI/service selection passed **43 tests in 359.37s**, and the focused
per-check timestamp/UI delta passed **4 tests in 25.55s**. Full-project
no-incremental mypy passed on **108 source files**. No physical capture, probe,
write, read-back, recovery or boot was performed. Real-device acceptance,
ROM/ISO build gates and successful Live USB app/service boot remain open.

## Earlier full software regression — commit 6cabb23d, 2026-10-16

The full Ubuntu 26.04 WSL suite passed **755 tests, 4 skipped** (759
collected; 1108.07 seconds; exit 0). The full Windows suite passed **699
tests, 60 skipped** (759 collected; 770.94 seconds; exit 0). Both reports
record zero failures and errors. Ubuntu's four skips are two
`UNVALIDATED: host lacks trusted Arch pacman executable ownership evidence`
cases, one Windows-specific host platform contract, and one Windows-specific
production durability policy. The two Arch trust checks are unvalidated, not
passing Linux checks. Windows skips include POSIX directory-fsync, ownership
and process contracts, Linux-only namespace/device checks, and several POSIX
symlink-contract cases; one separate symlink-creation test was skipped because
this account lacks that privilege (`WinError 1314`). These skipped checks are
not passes. JUnit files, hashes and exact Ubuntu command are
in [`evidence/software-regression-20261016/README.md`](evidence/software-regression-20261016/README.md).
Full-project mypy passed on 108 source files in Ubuntu and with the Windows
target. The earlier Windows full-suite result (693 passed, 45 skipped) predates
this commit; the current-commit run above supersedes it for Windows evidence.

These are software checks. Ubuntu is WSL rather than native Linux; neither run
establishes an ISO build or boot, successful ROM build, or hardware result.
Native Linux/VM validation, Live USB app/service boot and all physical hardware
workflows remain pending. V1 is not release complete.

## Earlier root-account Arch WSL full software regression — 72a302b, 2026-10-16

The full suite on Arch Linux WSL2 at source HEAD `72a302b` passed **756 tests,
3 skipped** (759 collected; 1161.62 seconds; exit 0). Its JUnit XML records
1161.431 seconds, zero failures/errors, and three skips. The skips are
`Windows-specific production durability policy`, `UNVALIDATED: test requires
a non-root host account so host-root ownership is unmapped`, and
`Windows-specific host platform contract`. The ownership case is unvalidated,
not a passing Linux check. Full-project no-incremental mypy passed on 108
source files in Arch WSL.

The exact command, report, and SHA-256 are recorded in
[`evidence/software-regression-20261016/README.md`](evidence/software-regression-20261016/README.md).
This root-account run is retained separately; its host-ownership skip was
unvalidated. Arch WSL is not native Linux. This is software-only evidence and
does not pass the VM/native Linux, ISO, ROM, Live USB boot, or hardware gates.

## Arch WSL non-root privilege-drop full software regression — 2026-10-16

The suite passed **757 tests, 2 skipped** (759 collected; 1245.94 seconds;
exit 0) with JUnit time 1245.846 seconds. The exact skips are the Windows-only
production directory-fsync policy and Windows-specific host platform contract.
The host-ownership test that was unvalidated in the earlier root-account run
passed here. The command was launched from the Arch WSL root account and used
`setpriv` to run pytest as UID/GID 65534 (`nobody`), with no supplementary
groups or capabilities, `NoNewPrivs=1`, `PATH=/usr/bin:/bin`, and
`HOME`/`TMPDIR=/dev/shm`; pytest cache and bytecode writes were disabled.

The tested Python source and tests remained at `72a302b`; docs-only commit
`0d74d76` landed while the suite was running. The exact command, JUnit report
and SHA-256 are in
[`evidence/software-regression-20261016/README.md`](evidence/software-regression-20261016/README.md).
This is an Arch WSL2 test run, not native Linux or disposable-VM evidence. It
does not test the dedicated `fwbuild` account, the fixed privileged launcher,
production offline execution, package installation, an ISO build, boot, ROM,
or hardware operation. The Windows and Ubuntu full-suite results in the earlier
section cover commit `6cabb23d`, not this source/test tree. All release gates
remain open.

## Arch WSL namespace primitive diagnostic — 2026-10-16

At 2026-10-16T17:25:57Z on source HEAD `72a302b`, a clean recapture fed the
probe script through a PowerShell `$probe` variable and the WSL wrapper
`wsl -d archlinux -- sh -c 'tr -d "\r" | sh'`. It created a fresh network
namespace with `unshare --net --fork`, dropped to UID/GID 65534
with no supplementary groups, and inspected interfaces, addresses, routes,
policy rules, capabilities, and `NoNewPrivs`. It exited 0. The host namespace
was `net:[4026531833]`; the child was `net:[4026532308]`. The child exposed
only a down `lo`, no addresses or routes, only default policy rules, zero
capability sets, and `NoNewPrivs=1`. No file, network, package, build, or
candidate-output operation occurred. The exact command and limitations are
recorded in
[`evidence/rootless-archiso-namespace-primitive-20261016/namespace-primitive-probe-20261016.txt`](evidence/rootless-archiso-namespace-primitive-20261016/namespace-primitive-probe-20261016.txt). The captured output is linked from that note and both files are hashed in the adjacent `SHA256SUMS`.

This proves only an ephemeral root-created route-free namespace plus a
privilege drop on Arch WSL2. It does not prove the fixed root-owned launcher,
dedicated `fwbuild` account/subuid mapping, pinned `pacstrap -N` or
`arch-chroot -N` compatibility, namespace-drift/descriptor/timeout handling,
or ISO support. Production offline execution remains closed with
`OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE`; native Linux, ISO, ROM, boot, and
hardware gates remain pending.

## V1 package wheel smoke — 2026-10-16

Built the current wheel from an isolated package-only copy in WSL `/dev/shm`,
installed it into a separate `/dev/shm` target, and confirmed imports resolve
to that target. Both pinned package resources (the U-Boot output-target patch
and pacstrap status-preservation patch) were present through
`importlib.resources`. The wheel was 371,855 bytes, 115 archive entries,
SHA-256 `2103d6e3758c1767de8a4c73e00da44118e42153782b988fecc5ed5350f41c70`.
Detailed evidence is in
[`evidence/software-regression-20261016/wheel-build-and-install.txt`](evidence/software-regression-20261016/wheel-build-and-install.txt).
This closes the current packaging/resource smoke gate only; it does not
change the pending firmware ROM, ISO/boot, or hardware gates.

## Rootless offline launcher preflight contract — 2026-10-16

The offline route now calls an isolated, versioned launcher-preflight contract
after the existing trusted-host and Archiso `-N` mount checks and before build
work or output creation. It validates the dedicated account and private
primary group, delegated UID/GID ranges, exact launcher identity, helper pins,
the complete host namespace map and distinct child namespace identities,
network snapshots bound to the child network namespace, route state,
descriptor/stdio boundaries, mount cleanup, and bounded process-group
cleanup. Synthetic tests exercise rejection paths only.

No reviewed root-owned launcher path/hash/authorization pin is deployed, so
the production pin remains absent and the route stops with
`OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE` before launcher-specific scratch/child
creation and before builder work/output. The existing trusted-host and mount
preflights, including their child processes, run first. No live launcher
probe, pacstrap package action, build command, or builder output/work creation
occurred in this slice. `pacstrap -N --help` evidence alone does not
establish operational compatibility; controlled disposable-VM package-input
evidence remains required. Status is **CONTRACT IMPLEMENTED;
DEPLOYMENT/COMPATIBILITY UNVALIDATED**. Native Linux, ISO/boot, ROM, and all
hardware gates remain open.

If later deployed, the launcher pin requires the exact root-owned setuid mode
`04755`; every launcher parent directory must be root-owned and not writable
by group or other. Its scratch parent must be a root-owned, non-link temporary
directory with exact mode `01777`; the operation-owned child remains caller-
owned mode `0700`. These checks make the default `/tmp` choice explicit and
prevent the current user-owned-temp check from rejecting a correctly protected
system temp directory after a launcher pin is reviewed.

SOL accepted this contract. Independent focused verification on Arch Linux
WSL2 passed **53 tests in 3.15 seconds**. The exact command was:

```sh
wsl -d archlinux --cd /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader -- env PATH=/usr/bin:/bin HOME=/dev/shm TMPDIR=/dev/shm PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider --basetemp=/dev/shm/lc-v1-rootless-offline-contract-20261016 --override-ini=tmp_path_retention_policy=failed tests/test_rootless_offline_preflight.py tests/test_usb_builder.py::test_production_offline_build_stays_closed_after_rootless_preflight
```

Independent mypy passed on the two touched Python source files with
`--no-incremental` and an isolated `/dev/shm` cache. Exact command:

```sh
wsl -d archlinux --cd /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader -- env PATH=/usr/bin:/bin HOME=/dev/shm TMPDIR=/dev/shm PYTHONDONTWRITEBYTECODE=1 python -m mypy --no-incremental --cache-dir=/dev/shm/lc-v1-rootless-offline-mypy-20261016 usb_builder/rootless_offline_preflight.py usb_builder/build_usb.py
```

The full current-source Arch WSL2 non-root suite then passed **809 tests, with
2 Windows-specific skips and 0 failures/errors** (811 collected; 1771.18s;
exit 0). JUnit records 1771.094s; its SHA-256 and exact command are in
[`evidence/software-regression-20261016/README.md`](evidence/software-regression-20261016/README.md).
This remains WSL software evidence only. Launcher deployment, controlled
`pacstrap -N` package-input compatibility, a disposable full-system VM build,
ISO build/boot, ROM, and hardware gates remain open.

## Current-source RC1 regression and VM evidence — 2026-10-16

Source and tests are commit `7b6000ad6503b9ccfd435ec2404674ff26cda6f5` (`Validate POSIX launcher pins on Windows`). The complete Windows run passed **751 tests, skipped 61, with zero failures/errors** (812 collected; 769.55 seconds). Full-project Windows-targeted mypy passed with no issues in 109 source files. The complete Arch WSL2 suite under UID/GID 65534 (`nobody`) passed **810 tests, skipped 2, with zero failures/errors** (812 collected; 1239.32 seconds). Its two skips are Windows-specific contracts; full-project Arch WSL2 mypy also passed with no issues in 109 source files. The exact current-source wheel also built and installed into an isolated `/dev/shm` target with index access disabled; imports and pinned package resources resolved, and the wheel included the current POSIX launcher fix (393,143 bytes; SHA-256 `8f0cbc3827e779ab8d236dae62923b238ec05f3ab41f30ee2a7f4faa8244a999`). Exact commands, skip names, report hashes and limitations are recorded in `docs/v1/evidence/software-regression-20261016/README.md` and the wheel output record.

A separate Windows-hosted QEMU 11.1.2 TCG guest using generic Arch Linux 2025.08.01 reached the explicitly selected emergency target and displayed an emergency shell. It used 2 GiB RAM, two vCPUs, generic Arch kernel/initramfs and read-only attached ISO/qcow2 storage, with user-mode networking enabled. The screenshot shows a failed network `curl` attempt followed by another curl-to-Bash invocation whose response, contents and actions were not captured; it is not an isolated or read-only execution diagnostic. The capture and checksums are in `docs/v1/evidence/live-linux-vm-20261016/attempt3-full-system-inspection/`. This does not establish a normal guest boot, current-source package or ISO build, AutoLoader app/service startup, ROM result, or hardware result. The temporary inspection guest was stopped after capture. The separate 8 GiB Archiso build guest remains paused with its qcow2 overlay unchanged.

At this checkpoint, only host C: was visible and had about 2.1 GiB free, below the space needed for the current-source Live USB build. No candidate ISO, current-source ROM, device probe, chip-voltage measurement, backup, firmware write, read-back, recovery, or real-device boot was performed. The three physical workflows remain **HARDWARE PENDING**. Status is **SOFTWARE REGRESSION PASS; V1 RELEASE GATES OPEN**.
