# V1 known issues

This list is evidence-based at baseline commit `d5e6810327bad6782f48326ee3f3d707bc6395e`; it will be updated as fixes land.

| Priority | Issue at baseline | User impact | Phase 01 status | Remaining resolution |
|---|---|---|---|---|
| P0 | Orchestrator's build loop fabricates progress and completion without invoking a builder. | Can lead the UI to offer a flash step with a nonexistent ROM. | FIXED: explicit unavailable outcome; no completion or ROM path; regression tested. | Connect and validate the real builders in Phase 04. |
| P0 | TUI flash and verification screens show success without accessing hardware or checking a ROM. | Misrepresents an unperformed flash as successful. | FIXED: write/verify controls are disabled and report remains `NOT PERFORMED`; screen tests cover status. | Connect outcome-backed screens after Phases 02–03 and 08. |
| P0 | Baseline CLI `flash --write` bypasses model-specific compatibility checks and `--yes` skips interactive confirmation. | Unsafe combinations could be written unattended. | FIXED: application CLI write path routes through fail-closed orchestrator; bypass flags are removed and tested. | Phase 02/03 build the shared validated write backend. |
| P0 | Backup strategy does not prove complete original firmware coverage for all supported topologies. | Recovery copy may omit a chip or firmware region. | OPEN: application backup capture is unavailable pending observed topology and complete recovery coverage. | Phase 03 observed-topology backup/readback. |
| P1 | `build-rom` is configuration validation only; firmware builders are not wired to it or the TUI. | No ROM is produced by the advertised command. | FIXED: help says unavailable and valid configurations exit with `ROM_BUILD_UNAVAILABLE`; tested. | Connect and validate the real builders in Phase 04. |
| P1 | Model database, compatibility claims and builder targets are not validated against all machine variants. | Model labels alone may not describe observed board/chip topology. | OPEN: README limits claims to provisional V1 development targets. | Phases 02 and 07. |
| P1 | Live USB builder and boot behavior are not tested; startup discards TUI output. | Failures may be opaque and image bootability is unknown. | OPEN: CLI says no ISO was built; Archiso is not exercised on this host. | Phase 09. |
| P1 | IVprep automation includes a downgrade execution path with no workflow-level human authorization gate. | A workflow invocation can cause a firmware update/downgrade. | OPEN: no application CLI/TUI invocation is exposed in Phase 01; implementation still needs workflow-level gating. | Phases 02 and 05. |
| P1 | README installation repository URL and `generate-usb` example do not match current project/CLI. | Users receive incorrect setup instructions. | FIXED: install URL and current command names documented. | Maintain during Phase 11. |

## Hardware state

T430 1vyrain, X200 Libreboot and X220 Coreboot: **HARDWARE PENDING**. No real-device write or boot evidence is recorded.
