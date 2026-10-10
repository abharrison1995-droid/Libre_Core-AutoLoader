# V1 implementation plan

This plan tracks the multiphase V1 scope from `pasted-text-1.txt`. Statuses describe the checked-out software and evidence, not intended capability.

| Phase | Scope | Status | Exit evidence |
|---|---|---|---|
| 00 | Repository audit and baseline | SOFTWARE COMPLETE | SOL accepted; review follow-ups finalized in `2d0e2d1` and wording cleanup in `4c33444`. |
| 01 | Startup, CLI/TUI stabilization, no false success | SOFTWARE COMPLETE — SOL ACCEPT | Packaging, CLI/TUI smoke coverage, fail-closed operation outcomes, and passing tests/type check; follow-up resolved in `207a7df`. |
| 02 | Compatibility engine, shared workflow state and authorization | SOFTWARE COMPLETE — SOL ACCEPT | Three-profile tri-state validator, immutable evidence, durable checksummed journal with safe pre-authorization resume, one-use digest-bound approval, blocked write boundary, 21 focused tests, 101 full-suite passes, mypy clean on 82 source files, and CLI help smoke passed; accepted implementation/evidence commits are recorded in `PROGRESS.md`. |
| 03 | SPI probing, complete backup, read-back and recovery | SOFTWARE COMPLETE — SOL ACCEPT | Standalone read-only SPI/probe evidence, multi-chip backup, storage preflight, region-aware read-back and recovery-readiness services; fake transport tests and write quarantine; commits `4ba1858` and `448766a`. Real-device evidence remains pending. |
| 04 | Pinned firmware artifact engine | SOFTWARE COMPLETE — SOL ACCEPT; UPSTREAM LINUX BUILD EVIDENCE PENDING | Exact upstream recipe allowlist, immutable manifests, strict source/config/output checks, pinned CBFS payload parsing for X200/X220, X220 original-backup-derived blobs and non-BIOS byte preservation; real upstream build evidence remains outstanding. |
| 05 | 1vyrain and IVprep workflow | SOFTWARE COMPLETE — SOL ACCEPT; HARDWARE PENDING | SOL accepted the software workflow after commits `24ea28932ddbccb152b2b9aaed924f9f4b131481` and follow-up `4f359c2060e3ca809b2435830781aa95d91cdcec`. This includes current-capture Secure Boot/direct-onboard-USB and DD-mode attestations, pinned Rev 5 legacy-MD5 comparison without authentication claims, explicit IVprep instructions, durable approval checkpoints, and rejection of correctly checksummed v1 checkpoints. Real-device workflow remains unverified. |
| 06 | Libreboot workflow | NOT STARTED | Validated artifact and X200 acceptance report. |
| 07 | Coreboot workflow | NOT STARTED | Reproducible build and X220 acceptance report. |
| 08 | Unified Textual TUI and CLI | NOT STARTED | UI and CLI backed by the same operation service. |
| 09 | Live USB, offline operation and packaging | NOT STARTED | Buildable and bootable image evidence. |
| 10 | Automated testing, hardware validation and release qualification | NOT STARTED | Release tests and three hardware acceptance reports. |
| 11 | Documentation and release | NOT STARTED | Release checklist met; hardware status accurately stated. |
| 12 | Post-V1 expansion (non-blocking) | NOT STARTED | Separate post-V1 scope; does not block the three reference workflows. |

Phases 02, 03, and 04 are accepted software scopes but remain standalone and are not connected to CLI/TUI. Phase 03 contains only read-only transport APIs and software checks: no actual SPI probe/read, voltage measurement, physical backup, firmware compilation/write, post-write read-back, recovery, or boot validation occurred. SOL accepted Phase 04 software commits `dec5cf903c8c4d09b464f14a38d84207f24a523a` and `7c5b14e270c34b4a9856dfbcafdec029be011c8e`. Software gates: 25 focused artifact-engine tests, 152 full-suite tests, mypy clean on 87 source files, editable install, CLI help, and diff-check. No upstream firmware build has run; the pinned CBFS parsers have not been exercised against generated images from a supported Linux build, so firmware artifacts cannot be marked flash-ready. No writer is enabled; all three hardware workflows remain HARDWARE PENDING. Any future physical firmware write or hardware-specific recovery operation requires explicit human authorization. Phase 12 is post-V1 expansion and does not block V1.
