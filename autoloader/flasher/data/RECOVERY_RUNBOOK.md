# V1 firmware recovery runbook

This runbook describes recovery readiness and the evidence to collect for the three V1 targets. It is not a recovery authorization and does not claim that any procedure has been tested on hardware. Phase 10B includes a guarded X200/X220 external SPI write/read-back path; raw restore and recovery writes remain blocked. This runbook grants no authorization to invoke either the normal write path or a recovery operation.

## Stop and preserve evidence

If a firmware operation fails or a machine does not boot:

1. Do not reboot automatically, retry a write, or start another flash attempt.
2. Preserve the operation journal, probe output, tool/version record, manifests, all backup files, readback files, and error output. A mismatch or partial read is evidence and must not be deleted or overwritten.
3. Record the machine's exact model/type, board revision, current power state, programmer connection, measured voltage, and the last completed operation stage.
4. Stop if the device identity, chip layout, voltage, or original backup cannot be matched unambiguously. Ask a qualified hardware technician to inspect it.

## Required recovery equipment and checks

Recovery requires an external SPI programmer with an interface and wiring suitable for the observed chip, verified electrical compatibility, and a separate recovery copy of the original full-chip backup. A programmer label or database entry does not prove electrical compatibility. The chip must be positively identified from an unforced probe and its observed capacity must match the backup length exactly.

Before any restoration write, a human operator must separately authorize that exact recovery plan. The plan must bind the machine identity, physical chip, programmer and voltage, original backup checksum, layout or region map, and recovery destination. The original backup stays untouched. The recovery image must be re-read after programming and compared with the intended bytes or named regions. A failed comparison requires stopping and preserving the evidence; there is no automatic retry or reboot.

## Model-specific readiness

### ThinkPad T430 / 1vyrain

The planned normal workflow uses upstream 1vyrain after any required, human-run IVprep handoff. Recovery readiness is a separate external SPI path. Record each physical chip separately and match its observed identity and exact capacity to its own backup. The currently expected two-chip layout is only a provisional policy constraint until observed on the specific machine. Never interchange the 8 MiB and 4 MiB chip images. Do not run IVprep from this application.

### Regular ThinkPad X200 / Libreboot

This procedure applies to the regular X200 only. X200s, X200si, and X200 Tablet machines are excluded from this V1 profile. Record the chip's actual identity, capacity (the upstream guide describes 4 MiB or 8 MiB variants), programmer output voltage, and wiring. Restore only the original backup whose size and checksum match that observed chip. A database estimate is not a substitute for the physical probe.

### ThinkPad X220 / Coreboot

The expected device has one 8 MiB flash containing IFD, GBE, ME, and BIOS regions, with a 3 MiB BIOS region. Before recovery, compare the observed descriptor and complete non-overlapping region map with the preserved map in the manifest. If the observed and recorded layout differ, stop. Any region-only comparison must name the exact byte ranges; it must not be treated as a whole-chip match.

## Readback and boot checks

After a separately authorized recovery write, retain both the intended backup and the fresh readback. Compare the full chip when the image covers the full chip. For a region-only operation, compare every explicitly mapped region and report the un-compared portions. A byte or map mismatch is `FAILED` or `UNKNOWN`; it does not prove that the machine is safe to reboot. Boot validation is a later human-observed step and must be recorded separately.

## Evidence status

Software-created readiness records prove only that the recorded files, hashes, manifest copies, and storage checks passed their software checks. They do not prove that a programmer was connected, that electrical measurements are correct, that a recovery write occurred, or that a real machine booted. Those checks remain hardware-pending until documented on the corresponding device.
