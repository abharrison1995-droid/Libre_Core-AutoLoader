# V1 implementation plan

This plan tracks the multiphase V1 scope from `pasted-text-1.txt`. Statuses describe the checked-out software and evidence, not intended capability.

| Phase | Scope | Status | Exit evidence |
|---|---|---|---|
| 00 | Repository audit and baseline | SOFTWARE COMPLETE | SOL accepted with non-blocking follow-ups recorded in `PROGRESS.md`. |
| 01 | Startup, CLI/TUI stabilization, no false success | IN PROGRESS | Stabilization commit, smoke tests, and passing release-relevant software tests. |
| 02 | Compatibility engine, shared workflow state and authorization | NOT STARTED | Exact model/artifact/topology checks and negative tests. |
| 03 | SPI probing, complete backup, read-back and recovery | NOT STARTED | Hardware-independent safety tests plus real-device evidence. |
| 04 | Real firmware build and artifact engine | NOT STARTED | Pinned, validated build and artifact pipeline. |
| 05 | 1vyrain and IVprep workflow | NOT STARTED | Upstream-pinned, human-gated workflow and T430 acceptance report. |
| 06 | Libreboot workflow | NOT STARTED | Validated artifact and X200 acceptance report. |
| 07 | Coreboot workflow | NOT STARTED | Reproducible build and X220 acceptance report. |
| 08 | Unified Textual TUI and CLI | NOT STARTED | UI and CLI backed by the same operation service. |
| 09 | Live USB, offline operation and packaging | NOT STARTED | Buildable and bootable image evidence. |
| 10 | Automated testing, hardware validation and release qualification | NOT STARTED | Release tests and three hardware acceptance reports. |
| 11 | Documentation and release | NOT STARTED | Release checklist met; hardware status accurately stated. |
| 12 | Post-V1 expansion (non-blocking) | NOT STARTED | Separate post-V1 scope; does not block the three reference workflows. |

Phases 02–11 remain blocked on their dependencies and are outside the current Phase 00/01 implementation slice. Hardware write, downgrade execution and recovery work require the operator's explicit approval and are not authorized by this plan. Phase 12 is post-V1 expansion and does not block V1.
