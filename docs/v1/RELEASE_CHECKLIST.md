# V1 release checklist

| Requirement | Status | Evidence |
|---|---|---|
| Application and CLI launch cleanly | SOFTWARE TESTED | Baseline in isolated venv: imports, `detect`, `build-rom`, and all nine Textual screens; 70 tests passed. Phase 01 smoke tests remain pending. |
| No simulated build/flash/verification success | IN PROGRESS | Phase 01 implementation and regression tests pending. |
| CLI and TUI call one validated backend | NOT STARTED | Phase 02 and later. |
| Model-specific compatibility checks block unsupported combinations | NOT STARTED | Phase 02/07. |
| T430 1vyrain workflow accepted on real hardware | HARDWARE PENDING | `HARDWARE_TEST_REPORT.md`. |
| X200 Libreboot workflow accepted on real hardware | HARDWARE PENDING | `HARDWARE_TEST_REPORT.md`. |
| X220 Coreboot build/workflow accepted on real hardware | HARDWARE PENDING | `HARDWARE_TEST_REPORT.md`. |
| Complete backups and read-back verified on real hardware | HARDWARE PENDING | `HARDWARE_TEST_REPORT.md`. |
| Live USB image builds and boots | NOT STARTED | Phase 09; blocked in this Windows host (no WSL/Linux/container build environment). |
| Documentation matches implemented and validated support | IN PROGRESS | Phase 00 audit; Phase 11 remains. |
| Phase 00 SOL review accepted | ACCEPTED WITH NON-BLOCKING FOLLOW-UP | Phase 00 audit accepted; follow-ups recorded in `PROGRESS.md`. |
| Phase 01 SOL review accepted | NOT STARTED | Awaiting stabilization review. |

V1 cannot be marked complete while any release-blocking software check or any of the three hardware workflows remains pending.
