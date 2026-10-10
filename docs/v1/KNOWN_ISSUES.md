# V1 known issues

This list is evidence-based at baseline commit `d5e6810327bad6782f48326ee3f3d707bc6395e`; it will be updated as fixes land.

| Priority | Issue | User impact | Planned resolution |
|---|---|---|---|
| P0 | Orchestrator's build loop fabricates progress and completion without invoking a builder. | Can lead the UI to offer a flash step with a nonexistent ROM. | Phase 01 fail-closed unavailable result. |
| P0 | TUI flash and verification screens show success without accessing hardware or checking a ROM. | Misrepresents an unperformed flash as successful. | Phase 01 remove canned success and keep next action disabled. |
| P0 | Baseline CLI `flash --write` bypasses model-specific compatibility checks and `--yes` skips interactive confirmation. | Unsafe combinations could be written unattended. | Phase 01 quarantines all application CLI physical writes; Phase 02/03 build the shared validated write backend. |
| P0 | Backup strategy does not prove complete original firmware coverage for all supported topologies. | Recovery copy may omit a chip or firmware region. | Phase 03 observed-topology backup/readback. |
| P1 | `build-rom` is configuration validation only; firmware builders are not wired to it or the TUI. | No ROM is produced by the advertised command. | Phase 01 correct status/help; later builder phases. |
| P1 | Model database, compatibility claims and builder targets are not validated against all machine variants. | Model labels alone may not describe observed board/chip topology. | Phases 02 and 07. |
| P1 | Live USB builder and boot behavior are not tested; startup discards TUI output. | Failures may be opaque and image bootability is unknown. | Phase 09. |
| P1 | IVprep automation includes a downgrade execution path with no workflow-level human authorization gate. | A workflow invocation can cause a firmware update/downgrade. | Phases 02 and 05. |
| P1 | README installation repository URL and `generate-usb` example do not match current project/CLI. | Users receive incorrect setup instructions. | Phase 01 docs repair. |

## Hardware state

T430 1vyrain, X200 Libreboot and X220 Coreboot: **HARDWARE PENDING**. No real-device write or boot evidence is recorded.
