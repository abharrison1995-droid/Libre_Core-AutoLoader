# V1 release checklist

| Requirement | Status | Evidence |
|---|---|---|
| Application and CLI launch cleanly | SOFTWARE TESTED | Isolated Python 3.12.14 venv; editable `pip install -e '.[dev]'` succeeds; `python -m autoloader --help` succeeds; `python -m pytest -q` passes 101 tests including CLI routing, the welcome screen, all nine Textual screen mounts and Phase 02 policy/state/journal tests. |
| No simulated build/flash/verification success | SOFTWARE TESTED | Orchestrator and `OnevyRainManager` fail closed for unimplemented operations; TUI downstream/capture controls stay disabled and verification shows `NOT PERFORMED`; recorded backup entries are not presented as reverified. CLI regression tests reject the removed `--write`/`--yes` bypass. |
| CLI and TUI call one validated backend | IN PROGRESS | CLI build/backup/flash and TUI build requests use `Orchestrator`; the Phase 02 safety core exists but is not yet wired into either interface. Physical build/capture/write controls intentionally report unavailable; integration remains Phase 08. |
| Model-specific compatibility checks block unsupported combinations | SOFTWARE TESTED — PHASE 02 CORE | Three explicit profiles and fail-closed tri-state identity, BIOS, artifact, topology, voltage, region and backup checks; 21 focused safety tests. Hardware evidence and app integration remain pending. |
| T430 1vyrain workflow accepted on real hardware | HARDWARE PENDING | `HARDWARE_TEST_REPORT.md`. |
| X200 Libreboot workflow accepted on real hardware | HARDWARE PENDING | `HARDWARE_TEST_REPORT.md`. |
| X220 Coreboot build/workflow accepted on real hardware | HARDWARE PENDING | `HARDWARE_TEST_REPORT.md`. |
| Complete backups and read-back verified on real hardware | HARDWARE PENDING | `HARDWARE_TEST_REPORT.md`. |
| Live USB image builds and boots | NOT STARTED | Phase 09; blocked in this Windows host (no WSL/Linux/container build environment). |
| Documentation matches implemented and validated support | IN PROGRESS | Phase 00 audit accepted; Phase 01 status/evidence updated; Phase 11 remains. |
| Phase 00 SOL review accepted | ACCEPTED | SOL accepted Phase 00; four follow-ups were finalized in `2d0e2d1` and the decision wording was aligned in `4c33444`. |
| Phase 01 SOL review accepted | ACCEPTED | SOL accepted Phase 01; the welcome/version, provisional compatibility and chip-size estimate follow-up was resolved in `207a7df`. |
| Phase 02 SOL review accepted | ACCEPTED — SOFTWARE ONLY | SOL accepted the standalone policy, immutable evidence, durable journal and digest-bound single-use approval. Implementation commits `5ceb6f79d6924f73c073a4631cb65434e2058fbd` and `263d8d5b4625799f504abfb691ba2ba6bed22ec6`; evidence/documentation commits `ae494ebd27a427573a06e60c6c079c2d9ba526ae` and `f04f9d35957ee0e7575a17a023985c11d4c1254e`. Final checks: 101 tests passed, mypy clean on 82 files, editable install and CLI help passed. CLI/TUI integration and Phase 03 remain pending; all physical writes remain blocked. |

V1 cannot be marked complete while any release-blocking software check or any of the three hardware workflows remains pending.
