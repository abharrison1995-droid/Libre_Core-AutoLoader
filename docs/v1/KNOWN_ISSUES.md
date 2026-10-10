# V1 known issues

This list is evidence-based at baseline commit `d5e6810327bad6782f48326ee3f3d707bc6395e`; it will be updated as fixes land.

| Priority | Issue at baseline | User impact | Phase 01 status | Remaining resolution |
|---|---|---|---|---|
| P0 | Orchestrator's build loop fabricates progress and completion without invoking a builder. | Can lead the UI to offer a flash step with a nonexistent ROM. | FIXED: explicit unavailable outcome; no completion or ROM path; regression tested. | Connect and validate the real builders in Phase 04. |
| P0 | TUI flash and verification screens show success without accessing hardware or checking a ROM. | Misrepresents an unperformed flash as successful. | FIXED: write/verify controls are disabled and report remains `NOT PERFORMED`; screen tests cover status. | Connect outcome-backed screens after Phases 02–03 and 08. |
| P0 | `OnevyRainManager` accepts arbitrary BIOS versions for provisional models, simulates exploit execution, always approves unlock, and reports flash completion without a writer. | Can misrepresent unsupported BIOS compatibility, an unperformed unlock, or an unperformed firmware write as successful. | FIXED: compatibility and all exploit/unlock/flash methods return explicit unavailable outcomes; regression tests prove no `UNLOCKED`/`COMPLETE` success state. | Add the pinned, human-gated upstream handoff in Phase 05. |
| P0 | Baseline CLI `flash --write` bypasses model-specific compatibility checks and `--yes` skips interactive confirmation. | Unsafe combinations could be written unattended. | FIXED: application CLI write path routes through fail-closed orchestrator; bypass flags are removed and tested. | Phase 02/03 build the shared validated write backend. |
| P0 | Backup strategy does not prove complete original firmware coverage for all supported topologies. | Recovery copy may omit a chip or firmware region. | OPEN: application backup capture is unavailable pending observed topology and complete recovery coverage. | Phase 03 observed-topology backup/readback. |
| P1 | `build-rom` is configuration validation only; firmware builders are not wired to it or the TUI. | No ROM is produced by the advertised command. | FIXED: help says unavailable and valid configurations exit with `ROM_BUILD_UNAVAILABLE`; tested. | Connect and validate the real builders in Phase 04. |
| P1 | Model database, compatibility claims and builder targets are not validated against all machine variants. | Model labels alone may not describe observed board/chip topology. | PHASE 02 CORE ADDED: only three V1 profiles can pass the standalone validator; broad database flags are ignored and incomplete evidence blocks. | Hardware-specific validation remains pending; firmware builders require Phase 04/06/07. |
| P1 | Live USB builder and boot behavior are not tested; startup discards TUI output. | Failures may be opaque and image bootability is unknown. | OPEN: CLI says no ISO was built; Archiso is not exercised on this host. | Phase 09. |
| P1 | IVprep automation includes a downgrade execution path with no workflow-level human authorization gate. | A workflow invocation can cause a firmware update/downgrade. | OPEN: no application CLI/TUI invocation is exposed in Phase 01; implementation still needs workflow-level gating. | Phases 02 and 05. |
| P1 | README installation repository URL and `generate-usb` example do not match current project/CLI. | Users receive incorrect setup instructions. | FIXED: install URL and current command names documented. | Maintain during Phase 11. |
| P1 | Welcome screen claimed version 1.0.0 and advertised building/flashing; CLI and model-selection labels presented database flags and chip sizes as validated support/evidence. | Users could mistake an in-development release candidate or provisional database entries for implemented capabilities or observed chip topology. | FIXED: welcome uses the package version and states unavailable capabilities; CLI/TUI labels are provisional, and listed chip sizes are marked as estimates to verify. | Keep user-facing claims aligned as workflows are implemented. |

## Phase 02 remaining limitations

- The new safety core is not connected to CLI/TUI and does not perform chip detection, artifact builds, backups, writes, read-back or boot tests. Application write routes remain quarantined until later phases.
- Device, programmer-voltage, capture-ID and region observations are immutable inputs, but the policy core cannot establish that a human supplied observation is true. Physical probe and measurement integration belongs to Phase 03.
- A backup capture ID records distinct reads but is not signed acquisition provenance. The core additionally requires separate files, exact chip capacity, nonblank contents, matching declared hashes and equal reads; capture-origin proof remains to be integrated.
- Artifact content is checked against its declared SHA-256 and source revision must be a full 40-hex commit. The current core does not fetch upstream source or prove that the commit belongs to the URL; Phase 04/05/06/07 must produce independently reviewable provenance.
- The operation journal is append-only only for the lifetime of the process. Durable audit storage and crash-recovery semantics remain future work.
- External SPI profiles currently fail closed unless both the programmer output and every observed chip are independently recorded at exactly 3300 mV. This software policy does not measure voltage.

## Hardware state

T430 1vyrain, X200 Libreboot and X220 Coreboot: **HARDWARE PENDING**. No real-device write or boot evidence is recorded.
