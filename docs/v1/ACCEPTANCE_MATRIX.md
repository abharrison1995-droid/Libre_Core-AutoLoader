# V1 acceptance matrix

`Software tested` means exercised by automated tests or a non-destructive build. `Hardware pending` means no real-device evidence is present in this repository baseline.

| Workflow | Required checks | Current implementation evidence | Status |
|---|---|---|---|
| T430 / 1vyrain | Exact model and BIOS compatibility; IVprep handoff when required; upstream 1vyrain; post-install validation | `autoloader/reversion` contains version checks and IVprep execution; no end-to-end shared workflow or hardware report | NOT STARTED; hardware pending |
| X200 / Libreboot | Validated upstream artifact; full original backup; external SPI write; read-back; boot checks | Libreboot builder is partial; no model-specific artifact/topology gate or hardware report | NOT STARTED; hardware pending |
| X220 / Coreboot | Real target config and ROM build; validation; full backup; external SPI write; read-back; recovery and boot checks | Coreboot builder exists but generates a generic config; no verified X220 build or hardware report | NOT STARTED; hardware pending |
| Shared CLI/TUI | Same backend; operation outcomes drive progress and next actions | Orchestrator has a simulated build; flash/verification screens contain canned success; CLI is separately wired | IN PROGRESS |
| Live USB | Buildable image, installed dependencies, boot and safe startup | Archiso script/profile exist; no ISO or boot evidence recorded | NOT STARTED; hardware pending |

No row is hardware accepted. Automated tests and simulations cannot satisfy the real-device acceptance criteria.
