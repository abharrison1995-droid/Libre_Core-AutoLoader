# Current architecture and Phase 02 safety core

## Current call paths

```mermaid
flowchart TD
  Entry[autoloader.__main__:main / Click] --> CLI[CLI commands]
  CLI -->|run| App[AutoLoaderApp]
  App --> Screens[Textual screens]
  CLI -->|build, backup, flash| Orch[Orchestrator]
  Screens -->|build request| Orch
  Orch -->|unavailable, fail closed| Result[No build, capture, or write performed]
  CLI -->|read-only comparison| Orch
  Orch --> Verify[verify_flash helper]
  Verify --> FlashVerifier[FlashVerifier]
  FlashVerifier --> Runner[FlashromRunner, read path]
  Screens -->|manifest inspection only| Backup[BackupManager]
  Screens -->|flash and verification status only| Result
  Builder[ROM builder classes] -. not connected .-> Orch
  Restore[BackupManager restore primitive] -. low-level only, not exposed in app workflow .-> Runner
  Reversion --> IVprep[IVPrepManager / WinFlash]
  Reversion --> BIOSDB[BIOS target version database]
  Onevy[OnevyRainManager] -. not connected to app workflow .- Reversion
  USB[Archiso build script/profile] --> Start[Live startup script]
  Start --> App
  VM[test_vm QEMU harness] -. practice only .-> Screens
  Plan[Immutable V1 operation plan] --> Policy[CompatibilityValidator]
  Policy --> Journal[WorkflowSession and OperationJournal]
  Journal --> Approval[Single-use digest-bound approval]
  Approval -. Phase 02 always blocks writes .-> Result
  Policy -. standalone; not wired to CLI/TUI .-> Screens
```

At baseline, the CLI `flash` path bypassed the orchestrator while TUI build and flash completion were simulated. Phase 01 quarantines all application CLI physical writes, routes build/backup/flash requests to explicit unavailable outcomes, and removes false success. The TUI's build request uses the same orchestrator; capture and write controls remain disabled. Verification uses the shared orchestrator and can perform a read-only comparison when invoked.

Phase 02 adds a standalone workflow safety core in `autoloader/workflow/`. It uses three immutable V1 profiles, raw detection evidence, exact chip/programmer/artifact checks, a tri-state validator, an append-only in-memory journal, and one-use approvals bound to current plan and file digests. The core is not yet connected to the CLI/TUI or any hardware backend; `WorkflowSession.request_write` always returns `WRITES_DISABLED_PHASE_02` and never invokes a writer. CLI/TUI unification and hardware I/O remain later phases.

## Main modules

| Concern | Current modules |
|---|---|
| Entry and configuration | `autoloader/__main__.py`, `autoloader/app.py`, `autoloader/config.py` |
| Shared state/work | `autoloader/orchestrator.py` |
| Detection and compatibility data | `autoloader/detection/*`, `autoloader/database/*` |
| ROM preparation | `autoloader/rom_builder/*` |
| Flash, verification, backup | `autoloader/flasher/*` |
| 1vyrain unlock | `autoloader/flasher/onevy.py` (`OnevyRainManager`) |
| BIOS targets and downgrade | `autoloader/database/bios_versions.py`, `autoloader/reversion/{chains,ivprep,winflash}.py` |
| Backup restore | `autoloader/flasher/backup.py` (`restore_from_backup`, physical write surface) |
| UI | `autoloader/ui/screens/*`, `autoloader/ui/widgets/*` |
| Live USB | `usb_builder/build_usb.sh`, `usb_builder/archiso_profile/*` |
| VM/practice harness | `test_vm/launch_vm.py`, `test_vm/launch_vm.bat`, `test_vm/mock_chips/*` |
| Tests | `tests/*` |
| V1 evidence and workflow policy | `autoloader/workflow/{types,profiles,compatibility,authorization,engine}.py` |

## Target direction

Phases 01–02 quarantine physical CLI writes, remove fake completion paths, and implement the model-specific validated operation plan. Phase 02 policy deliberately ignores broad database support flags and fails closed on missing/conflicting evidence. Phase 03 must add a separate write/read/backup backend only after probing and backup/recovery checks are designed and reviewed. Later CLI/TUI integration should use this service; no simulation may invoke physical writes. Real-device hardware validation remains a separate acceptance layer.
