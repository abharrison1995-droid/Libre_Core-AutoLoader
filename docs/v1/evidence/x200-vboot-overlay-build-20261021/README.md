# X200 Libreboot upstream build and validation-only record — 2026-10-21

The pinned Libreboot 26.01 upstream command `./mk -b coreboot x200_4mb`
completed in Ubuntu 26.04 WSL with exit code 0 after 6,585.017 seconds. It
produced the expected 31 4 MiB ROM variants. Source revision/tree, command,
build log, output hashes, tool pins, and package inventory are recorded in
`run-result.json` and `SHA256SUMS`.

A separate read-only validation-only pass, `validation-20261021T064904674992Z`,
checked all 31 existing ROMs with pinned size and CBFS structural validation
and returned `VALIDATION_ONLY_PASSED`. It ran 511 recorded commands. The
original build log hash matched before and after the pass. No rebuild or
ArtifactEngine build was invoked; no `COMPLETE` manifest was published, and no
ROM was copied into the repository or accessed by hardware.

This is a WSL upstream build diagnostic. The ROMs remain in Linux scratch and
are not flash-ready artifacts. The record does not establish a validated
device-bound firmware image, external SPI write, read-back, boot, recovery, or
hardware acceptance. All such steps remain pending.
