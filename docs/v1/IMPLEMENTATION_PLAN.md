# V1 implementation plan

This plan tracks the multiphase V1 scope from `pasted-text-1.txt`. Statuses describe the checked-out software and evidence, not intended capability.

| Phase | Scope | Status | Exit evidence |
|---|---|---|---|
| 00 | Repository audit and baseline | IN PROGRESS | Audit and baseline details in this directory; tests need a usable project test environment. |
| 01 | Startup, CLI/TUI stabilization, no false success | IN PROGRESS | Stabilization commit, smoke tests, and passing release-relevant software tests. |
| 02 | Compatibility engine, shared workflow state and authorization | NOT STARTED | Exact model/artifact/topology checks and negative tests. |
| 03 | SPI probing, complete backup, read-back and recovery | NOT STARTED | Hardware-independent safety tests plus real-device evidence. |
| 04 | T430 1vyrain / IVprep workflow | NOT STARTED | Upstream-pinned, gated workflow and T430 acceptance report. |
| 05 | X200 Libreboot preparation and flashing | NOT STARTED | Validated artifact and X200 acceptance report. |
| 06 | X220 Coreboot build and flashing | NOT STARTED | Reproducible build and X220 acceptance report. |
| 07 | Model detection and compatibility data | NOT STARTED | Exact-match tests and source-backed matrix. |
| 08 | Textual interface | NOT STARTED | UI backed only by shared workflow results. |
| 09 | CLI | NOT STARTED | CLI backed only by shared workflow results. |
| 10 | Tests, CI and Live USB validation | NOT STARTED | Release tests and bootable-media evidence. |
| 11 | Documentation and release | NOT STARTED | Release checklist met; hardware status accurately stated. |

Phases 02–11 remain blocked on their dependencies and are outside the current Phase 00/01 implementation slice. Hardware write, downgrade execution and recovery work require the operator's explicit approval and are not authorized by this plan.
