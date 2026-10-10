# V1 release checklist

| Requirement | Status | Evidence |
|---|---|---|
| Application and CLI launch cleanly | SOFTWARE TESTED | Isolated Python 3.12.14 venv; editable `pip install -e '.[dev]'` succeeds; `python -m autoloader --help` succeeds; `python -m pytest -q` passes 80 tests including CLI routing, the welcome screen and all nine Textual screen mounts. |
| No simulated build/flash/verification success | SOFTWARE TESTED | Orchestrator and `OnevyRainManager` fail closed for unimplemented operations; TUI downstream/capture controls stay disabled and verification shows `NOT PERFORMED`; recorded backup entries are not presented as reverified. CLI regression tests reject the removed `--write`/`--yes` bypass. |
| CLI and TUI call one validated backend | IN PROGRESS | CLI build/backup/flash and TUI build requests use `Orchestrator`; physical build/capture/write controls intentionally report unavailable. A complete model-specific validated backend remains Phase 02–03 work. |
| Model-specific compatibility checks block unsupported combinations | NOT STARTED | Phase 02/07. |
| T430 1vyrain workflow accepted on real hardware | HARDWARE PENDING | `HARDWARE_TEST_REPORT.md`. |
| X200 Libreboot workflow accepted on real hardware | HARDWARE PENDING | `HARDWARE_TEST_REPORT.md`. |
| X220 Coreboot build/workflow accepted on real hardware | HARDWARE PENDING | `HARDWARE_TEST_REPORT.md`. |
| Complete backups and read-back verified on real hardware | HARDWARE PENDING | `HARDWARE_TEST_REPORT.md`. |
| Live USB image builds and boots | NOT STARTED | Phase 09; blocked in this Windows host (no WSL/Linux/container build environment). |
| Documentation matches implemented and validated support | IN PROGRESS | Phase 00 audit accepted; Phase 01 status/evidence updated; Phase 11 remains. |
| Phase 00 SOL review accepted | ACCEPTED | SOL accepted Phase 00; four follow-ups were finalized in `2d0e2d1` and the decision wording was aligned in `4c33444`. |
| Phase 01 SOL review accepted | ACCEPTED WITH NON-BLOCKING FOLLOW-UP | SOL accepted Phase 01; follow-up to correct version, provisional compatibility, and chip-size estimate wording is resolved in this polish change. |

V1 cannot be marked complete while any release-blocking software check or any of the three hardware workflows remains pending.
