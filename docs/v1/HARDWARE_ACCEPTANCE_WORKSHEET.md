# V1 hardware acceptance worksheet

**Document status: TEMPLATE — all device gates BLOCKED / NOT RECORDED**

This worksheet records evidence for a specific physical machine. It is not an
authorization, does not establish that a machine is compatible, and must not be
used to infer SPI pinout, connector orientation, chip voltage, or recovery
commands. Fill device-dependent fields only from the exact observed device and a
qualified setup. Record evidence paths and hashes so another reviewer can
inspect the original records. Do not replace `NOT RECORDED` with estimates.

Before an X200/X220 write challenge can be issued, the operator must freshly
attest that the target is powered off, AC is disconnected, the removable battery
is removed, chip pin-1 orientation was checked for this exact setup, and the
programmer will be disconnected before repowering. The measured rail, meter,
test point, interface and wiring are also recorded. These are human attestations
and readings; AutoLoader does not measure or independently prove them. Record
an observed EC version with evidence and time plus an explicit `already-current`
or `updated-before-replacement` decision, or a named/time-stamped waiver that
acknowledges possible loss of the later OEM EC update route. Partial version
tuples cannot be combined with a waiver. EC firmware updates are never
automated by AutoLoader.

Phase 10B provides a guarded external SPI write/read-back route only for X200
and X220. T430 is a separate human-run IVprep/1vyrain handoff. Internal SPI and
raw restore/recovery writes remain blocked. Each actual write needs its own
explicit approval. Any recovery requires a separate reviewed recovery plan and
separate approval. Completing this worksheet does not grant either approval.

## ThinkPad T430 — IVprep/1vyrain human handoff

**Gate: HARDWARE PENDING — NOT RECORDED.** AutoLoader presents a human handoff;
it does not run IVprep or 1vyrain.

| Evidence | Record |
|---|---|
| Exact DMI product name/version, machine type/model, system board FRU/revision | NOT RECORDED |
| Device identity evidence reference, capture time, reviewer | NOT RECORDED |
| Exact BIOS vendor/version/date and compatibility decision/source revision | NOT RECORDED |
| Observed board/chip inventory (mandatory for recovery readiness): enumerate every chip with unique chip ID, manufacturer/part, actual capacity, location and probe/evidence reference; do not assume a chip count or capacity | NOT RECORDED |
| Upstream handoff source URL, pinned revision/tree, instruction/document hashes | NOT RECORDED |
| Actual upstream 1vyrain menu choice, exact value (must be option 1/default modified Lenovo BIOS), observer, evidence reference and timestamp after handoff approval | NOT RECORDED — no option selected |
| Operator-verified media name/revision, acquisition/source reference, file size and hashes (record MD5 only where upstream provides it; do not treat it as authentication) | NOT RECORDED |
| Handoff checkpoint/operation ID, journal path/hash, stage and device fingerprint | NOT RECORDED |
| Per-stage human approval/attestation IDs, exact action, actor and timestamp | NOT RECORDED |
| IVprep/1vyrain execution record: operator-controlled host/OS, exact human-run tool/version and result (never imply AutoLoader executed it) | NOT RECORDED |
| Successful reboot after the recorded option selection: operator, evidence reference and timestamp | NOT RECORDED |
| Captured flash command/result evidence, if available; otherwise flash status remains UNKNOWN | NOT RECORDED — flash status UNKNOWN |
| Backup set 1: for every observed chip, map chip ID to one independent full-chip read, dump path, exact byte count, SHA-256, acquisition/read ID and persistent volume identity | NOT RECORDED |
| Backup set 2: repeat one independent full-chip read for every observed chip; record the same mapping and fields, with a persistent volume identity distinct from set 1 for each chip's copy | NOT RECORDED — two chips require four full-chip reads total, two per chip |
| Per-chip pair validation: both reads map to the same observed chip, match its actual capacity, rehash correctly and compare as required by the reviewed recovery procedure | NOT RECORDED |
| Backup-set manifest hashes and recovery-readiness evidence reference; recovery readiness is blocked until the observed chip inventory and both independent reads per chip are verified | NOT RECORDED |
| Write/read-back operation and approval ID, if the human-run procedure performs a write; exact tool/result and evidence reference | NOT RECORDED |
| Full read-back bytes, path, SHA-256 and byte-for-byte comparison result, if required by the reviewed procedure | NOT RECORDED |
| Cold boot result; returned BIOS version; boot logs and function checks | NOT RECORDED |
| Directly observed Advanced BIOS menu feature, with observer/time/evidence reference strictly after the operator-reported successful reboot and before the fresh post-install DMI/BIOS capture; independent boot/function validation | NOT RECORDED — handoff “All done” alone is not write or boot evidence |
| Display, keyboard, storage, power, sleep/resume and other device-specific function results | NOT RECORDED |
| Deviations, failures, unresolved warnings and reviewer disposition | NOT RECORDED |
| Separate recovery plan ID/revision, exact device-specific procedure/command, approval ID and recovery-readiness review | NOT RECORDED — no recovery plan or authorization is supplied here |

## ThinkPad X200 — Libreboot / external SPI

**Gate: HARDWARE PENDING — NOT RECORDED.** Use only the guarded X200 external
SPI path after its evidence gates pass. Do not infer a regular X200 identity
from a related model or database label.

| Evidence | Record |
|---|---|
| Exact DMI product name/version, machine type/model, board FRU/revision and identity capture reference | NOT RECORDED |
| Device identity evidence hash, capture time and reviewer; confirm exact supported X200 variant | NOT RECORDED |
| Observed chip count, manufacturer/part, capacity, package/location and read-only probe evidence | NOT RECORDED |
| Observed SPI topology and device-specific connector orientation/pinout source or qualified setup reference | NOT RECORDED — do not use an assumed pinout |
| External programmer make/model/serial, firmware/tool version, cable/adapter and interface evidence | NOT RECORDED |
| Measured programmer/chip voltage, meter/instrument ID and calibration/status, exact test point, wiring/orientation evidence, actor and timestamp | NOT RECORDED — do not enter a nominal or assumed voltage |
| Libreboot source URL, pinned revision/tree, recipe/config and source-file hashes | NOT RECORDED |
| Build operation ID, command/log references, tool identities, overlay/dependency provenance and result | NOT RECORDED |
| Selected artifact path, manifest path/hash, exact image SHA-256/size, layout/CBFS validation and reviewer | NOT RECORDED — no accepted ROM is recorded |
| EC decision before replacement: complete observed version/evidence/timestamp plus `already-current` or `updated-before-replacement`, OR named/time-stamped waiver acknowledging possible loss of later OEM EC updates; no partial tuple | NOT RECORDED — EC decision gate blocked; no automated EC update is performed |
| Selected artifact GbE layout, both redundant NVM part hashes/checksums, and selected ROM MAC before preservation, all tied to selected artifact SHA-256 | NOT RECORDED — no selected ROM MAC inspection is recorded |
| Cross-check of the V1 GbE/FLMAP0 parser against pinned `ifdtool` output for this exact original ROM and selected artifact | NOT RECORDED — no device ROM was available for comparison |
| Original backup GbE layout, both redundant NVM part hashes/checksums and original MAC; independent copies must agree byte-for-byte | NOT RECORDED — no backup MAC inspection is recorded |
| Derived write-ready image path/hash/size and preservation sidecar hash; policy must copy the validated original GbE region, and final MAC must equal original MAC | NOT RECORDED — no MAC-preserved write image is recorded; no OS-override path is enabled |
| Full-chip backup copy A: path, persistent volume ID, bytes, SHA-256 and independent read/capture ID | NOT RECORDED |
| Independent full-chip backup copy B: separate path and persistent volume ID, bytes, SHA-256 and independent read/capture ID | NOT RECORDED |
| Backup manifest hashes, equality/rehash result and RecoveryReadiness evidence reference | NOT RECORDED |
| Final operation plan/journal ID, path/hash, stage and exact artifact/backup bindings | NOT RECORDED |
| Explicit per-write approval ID, action/intent digest, actor and timestamp | NOT RECORDED |
| Fixed writer executable/version and recorded operation result (exit, timeout, uncertainty/failure state, log reference) | NOT RECORDED |
| Full-chip read-back path, byte count, SHA-256, independent capture evidence and exact byte-for-byte comparison with approved artifact | NOT RECORDED |
| Cold boot result, firmware version, boot logs and device-specific function checks | NOT RECORDED |
| Post-boot NIC MAC comparison against the original verified backup MAC, bound to final ROM hash with observer/time/evidence reference | NOT RECORDED — MAC post-boot acceptance pending |
| Display, keyboard, storage, power, sleep/resume and other device-specific function results | NOT RECORDED |
| Deviations, failures, unresolved warnings and reviewer disposition | NOT RECORDED |
| Separate recovery plan ID/revision, device-specific source/procedure, exact command reviewed for this machine/setup, approval ID and recovery-readiness decision | NOT RECORDED — no recovery plan or authorization is supplied here |

## ThinkPad X220 — Coreboot / external SPI

**Gate: HARDWARE PENDING — NOT RECORDED.** Use only the guarded X220 external
SPI path after its evidence gates pass. Chip and region values must come from
the actual probe and validated device map, not this template.

| Evidence | Record |
|---|---|
| Exact DMI product name/version, machine type/model, board FRU/revision and identity capture reference | NOT RECORDED |
| Device identity evidence hash, capture time and reviewer; confirm exact supported X220 variant | NOT RECORDED |
| Observed chip count, manufacturer/part, capacity, package/location and read-only probe evidence | NOT RECORDED |
| Observed SPI topology, region map/source, connector orientation/pinout source or qualified setup reference | NOT RECORDED — do not use an assumed pinout or region map |
| External programmer make/model/serial, firmware/tool version, cable/adapter and interface evidence | NOT RECORDED |
| Measured programmer/chip voltage, meter/instrument ID and calibration/status, exact test point, wiring/orientation evidence, actor and timestamp | NOT RECORDED — do not enter a nominal or assumed voltage |
| Coreboot source URL, pinned revision/tree, recipe/config and source-file hashes | NOT RECORDED |
| Build operation ID, command/log references, tool identities, source/backup-derived blob provenance and result | NOT RECORDED |
| Selected artifact path, manifest path/hash, exact image SHA-256/size, CBFS/IFD/region validation and reviewer | NOT RECORDED — no accepted ROM is recorded |
| EC decision before replacement: complete observed version/evidence/timestamp plus `already-current` or `updated-before-replacement`, OR named/time-stamped waiver acknowledging possible loss of later OEM EC updates; no partial tuple | NOT RECORDED — EC decision gate blocked; no automated EC update is performed |
| Full-chip backup copy A: path, persistent volume ID, bytes, SHA-256 and independent read/capture ID | NOT RECORDED |
| Independent full-chip backup copy B: separate path and persistent volume ID, bytes, SHA-256 and independent read/capture ID | NOT RECORDED |
| Backup manifest hashes, equality/rehash result and RecoveryReadiness evidence reference | NOT RECORDED |
| Final operation plan/journal ID, path/hash, stage and exact artifact/backup/region bindings | NOT RECORDED |
| Explicit per-write approval ID, action/intent digest, actor and timestamp | NOT RECORDED |
| Fixed writer executable/version and recorded operation result (exit, timeout, uncertainty/failure state, log reference) | NOT RECORDED |
| Full-chip read-back path, byte count, SHA-256, independent capture evidence and exact byte-for-byte comparison with approved artifact | NOT RECORDED |
| Cold boot result, firmware version, boot logs and device-specific function checks | NOT RECORDED |
| Display, keyboard, storage, power, sleep/resume and other device-specific function results | NOT RECORDED |
| Deviations, failures, unresolved warnings and reviewer disposition | NOT RECORDED |
| Separate recovery plan ID/revision, device-specific source/procedure, exact command reviewed for this machine/setup, approval ID and recovery-readiness decision | NOT RECORDED — no recovery plan or authorization is supplied here |

## Review disposition

| Device | Evidence completeness review | Hardware acceptance decision | Reviewer/date |
|---|---|---|---|
| T430 | BLOCKED — required evidence NOT RECORDED | HARDWARE PENDING | NOT RECORDED |
| X200 | BLOCKED — required evidence NOT RECORDED | HARDWARE PENDING | NOT RECORDED |
| X220 | BLOCKED — required evidence NOT RECORDED | HARDWARE PENDING | NOT RECORDED |

