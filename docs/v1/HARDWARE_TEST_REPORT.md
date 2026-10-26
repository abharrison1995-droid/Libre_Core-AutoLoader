# Hardware test report

**Overall status: HARDWARE PENDING**

This report summarizes the real-device acceptance state. The detailed, still-unfilled checklist is in [Hardware acceptance worksheet](HARDWARE_ACCEPTANCE_WORKSHEET.md), with separate T430, X200 and X220 sections. The worksheet is a record template, not an operation plan or authorization.

No physical device identity, board/chip observation, SPI probe, voltage/wiring measurement, complete backup, write, read-back, cold boot, or function test is recorded. No hardware acceptance gate is passed.

| Device/workflow | Board and variant | Backup/readback evidence | Boot and function checks | Status |
|---|---|---|---|---|
| ThinkPad T430 / 1vyrain | Not recorded | Not performed | Not performed | HARDWARE PENDING |
| ThinkPad X200 / Libreboot | Not recorded | Not performed | Not performed | HARDWARE PENDING |
| ThinkPad X220 / Coreboot | Not recorded | Not performed | Not performed | HARDWARE PENDING |

Phase 10B provides a guarded external SPI write/read-back path only for X200 and X220. T430 remains a separate human-run IVprep/1vyrain handoff; internal SPI and raw restore/recovery writes remain blocked. A successful software test, emulator run, or writer exit code does not complete acceptance. Device-specific evidence, independent full-chip read-back, cold-boot/function checks, and recovery readiness must be recorded before review. Any recovery operation needs its own reviewed plan and separate explicit approval. This report and worksheet grant no authorization.
