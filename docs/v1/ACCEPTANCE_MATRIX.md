# V1 acceptance matrix

`Software tested` means exercised by automated tests or a non-destructive build. `Hardware pending` means no real-device evidence is present in this repository baseline.

| Workflow | Required checks | Current implementation evidence | Status |
|---|---|---|---|
| T430 / 1vyrain | Exact model and BIOS compatibility; IVprep handoff when required; upstream 1vyrain; post-install validation | Phase 02 pins [upstream `start.sh`](https://github.com/n4ru/1vyrain/blob/a3427b34102f8f9887acf65e23d7fd5900fe1d40/start.sh) and applies its T430 numeric rule (`2.64` → `264`, allowed below `265`); `2.65` blocks. IVprep remains an external Win64 human handoff. No hardware evidence exists. | POLICY SOFTWARE TESTED; handoff and hardware pending |
| X200 / Libreboot | Validated upstream artifact; full original backup; external SPI write; read-back; boot checks | Official guide covers regular X200 with 4 or 8 MiB chips. Phase 02 requires exact observed capacity, matching artifact size, per-chip voltage agreement, two stable full-chip backup records and pinned artifact content. No build/write/read-back evidence exists. | POLICY SOFTWARE TESTED; build and hardware pending |
| X220 / Coreboot | Real target config and ROM build; validation; full backup; external SPI write; read-back; recovery and boot checks | Coreboot guide specifies one 8 MiB flash, IFD/GBE/ME/BIOS regions and a 3 MiB BIOS region. Phase 02 requires observed 8 MiB topology, matching complete region maps and per-chip voltage agreement. No ROM compilation or hardware evidence exists. | POLICY SOFTWARE TESTED; build and hardware pending |
| Phase 02 compatibility/state core | Exact model profiles; tri-state validation; operation journal; one-use authorization; zero writer calls when blocked | 12 focused tests cover identity conflicts/aliases/manual candidates, T430 BIOS boundary/unknown, X200 capacity and artifact hash, X220 topology/regions, chip/programmer voltage, incomplete/transient backups, illegal transitions, changed/reused approval and simulation/write blocking. | IMPLEMENTED — PENDING SOL REVIEW; standalone and software-only |
| Shared CLI/TUI | Same backend; operation outcomes drive progress and next actions | Phase 01 quarantines CLI physical writes and removes false success. Phase 02 safety core is not yet wired into the CLI/TUI; that integration is later scope. | IN PROGRESS |
| Live USB | Buildable image, installed dependencies, boot and safe startup | Archiso script/profile exist; no ISO or boot evidence recorded | NOT STARTED; hardware pending |

No row is hardware accepted. Automated tests and simulations cannot satisfy the real-device acceptance criteria.

## Provisional source-backed machine constraints

| Machine | Upstream basis | Anticipated method/programmer | Current safe policy | Unknowns that remain blocking |
|---|---|---|---|---|
| T430 | [Pinned 1vyrain compatibility logic](https://github.com/n4ru/1vyrain/blob/a3427b34102f8f9887acf65e23d7fd5900fe1d40/start.sh); [IVprep upstream](https://github.com/n4ru/IVprep) | Internal software flash through upstream 1vyrain after any human-run Win64 IVprep handoff; external SPI recovery readiness required. | Software policy uses the pinned `<265` numeric threshold, exact DMI/MTM agreement and a two-chip profile. It never executes IVprep or upstream binaries. | Board/MTM, stock BIOS state, physical chip layout and post-install evidence remain to be observed. |
| X200 | [Libreboot X200 guide](https://libreboot.org/docs/install/x200.html) | External SPI programmer only after interface, wiring, programmer output and observed chip voltage all agree at 3300 mV. | Regular X200 only; exact 4 or 8 MiB chip and same-size artifact are required. | X200s/X200si/tablet variants excluded; physical model, chip/capacity/voltage, artifact and complete recovery reads remain unknown. |
| X220 | [Coreboot Sandy Bridge guide](https://doc.coreboot.org/mainboard/lenovo/Sandy_Bridge_series.html) | External SPI programmer for initial flash; exact interface/wiring and chip voltage must match at 3300 mV. | One observed 8 MiB chip; artifact and observed IFD/GBE/ME/3 MiB BIOS maps must match. | Board variant, descriptor/protection state, region map, programmer voltage and image semantics remain unobserved. |

Any conflict between detected hardware and these provisional descriptions blocks work. Sources establish research constraints, not completed device validation.
