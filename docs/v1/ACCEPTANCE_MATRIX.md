# V1 acceptance matrix

`Software tested` means exercised by automated tests or a non-destructive build. `Hardware pending` means no real-device evidence is present in this repository baseline.

| Workflow | Required checks | Current implementation evidence | Status |
|---|---|---|---|
| T430 / 1vyrain | Exact model and BIOS compatibility; IVprep handoff when required; upstream 1vyrain; post-install validation | Upstream `start.sh` lists T430 and uses a normalized numeric BIOS version `<265`; local database requires exact `2.64`, so the rule must be reconciled to a pinned upstream revision. IVprep is Win64-only and warns power loss during downgrade can brick the device. No safe handoff or hardware evidence exists. | NOT STARTED; hardware pending; exact target/source pin and board variant need validation |
| X200 / Libreboot | Validated upstream artifact; full original backup; external SPI write; read-back; boot checks | Official Libreboot X200 guide covers regular X200 and documents 4 MiB or 8 MiB chips. Local code has a partial builder; exact chip/topology must be probed and no hardware evidence exists. | NOT STARTED; hardware pending; observed chip identity/capacity required |
| X220 / Coreboot | Real target config and ROM build; validation; full backup; external SPI write; read-back; recovery and boot checks | Coreboot Sandy Bridge docs specify X220 with one 8 MiB flash, IFD/GBE/ME/BIOS regions and a 3 MiB BIOS region; external initial flashing is required. Local builder is generic and no verified build/hardware evidence exists. | NOT STARTED; hardware pending; observed board/chip and region layout required |
| Shared CLI/TUI | Same backend; operation outcomes drive progress and next actions | Orchestrator had a simulated build; flash/verification screens had canned success; CLI was separately wired. Phase 01 quarantines CLI physical writes and removes false success. | IN PROGRESS |
| Live USB | Buildable image, installed dependencies, boot and safe startup | Archiso script/profile exist; no ISO or boot evidence recorded | NOT STARTED; hardware pending |

No row is hardware accepted. Automated tests and simulations cannot satisfy the real-device acceptance criteria.

## Provisional source-backed machine constraints

| Machine | Upstream basis | Anticipated method/programmer | Current safe policy | Unknowns that remain blocking |
|---|---|---|---|---|
| T430 | [1vyrain compatibility logic](https://github.com/n4ru/1vyrain/blob/master/start.sh); [IVprep upstream](https://github.com/n4ru/IVprep) | Internal software flash through 1vyrain after a human-run Win64 IVprep handoff when BIOS number is incompatible; external SPI recovery readiness required. | Upstream currently accepts normalized T430 BIOS numbers `<265`; never auto-execute downgrade or redistribute upstream binaries. | Pin upstream revision/hash, verify normalization and aliases, confirm board/MTM and stock firmware state. Local database's exact `2.64` rule is not reconciled. |
| X200 | [Libreboot X200 guide](https://libreboot.org/docs/install/x200.html) | External SPI programmer; candidate CH341A or Raspberry Pi Pico serprog only after interface and 3.3 V electrical suitability is verified. | Limit candidate scope to regular X200; require physical chip detection and capacity match before artifact preparation or writes. | X200s/X200si/tablet variants remain excluded; actual machine type, 4/8 MiB chip, programmer wiring/voltage and firmware image shape must be observed. |
| X220 | [Coreboot Sandy Bridge guide](https://doc.coreboot.org/mainboard/lenovo/Sandy_Bridge_series.html) | External SPI programmer for initial flash; exact programmer to be chosen only after electrical and interface checks. | Require one observed 8 MiB chip and validated IFD/GBE/ME/BIOS layout. | Board variant, descriptor/protection state, programmer wiring/voltage, region map and image semantics must be read and validated. |

Any conflict between detected hardware and these provisional descriptions blocks work. Sources establish research constraints, not completed device validation.
