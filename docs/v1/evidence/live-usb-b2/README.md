# Live USB candidate b2 emulator evidence

Candidate b2 was built on 2026-10-13 from clean source snapshot commit
`1197d9040a39d6da012f459b8bb1f45385ee0072`, tree
`d2588ddff4f0c5d4115565f47360314553106d38`. The ISO was
1,012,215,808 bytes with SHA-256
`afde8ba28025530852547ec5042dd94ecece5a9b1d24f5da141365861cc35cbd`.
The build provenance file had SHA-256
`c8ad6f6c1b04ed87626a4410566ed11e5d23893293a8945d4e865c28d63e481f`.
The ISO itself is retained in the local Arch WSL candidate bundle and is not
copied into this repository.

The target-root install check passed. The inspected initramfs was 79 MiB,
SHA-256 `c7440ebd…218e090`, with the pinned derivative's `kms` hook removed;
all other hook order, including the PXE hooks, was retained. This confirms the
profile derivative was applied. It does not establish successful boot.

## BIOS

QEMU 11.1.2 used TCG with four vCPUs and 3 GiB of memory for 310 seconds. The
ISO hash before and after matched. The guest remained in systemd startup at
the Namespace Resource Manager job; it did not show the AutoLoader app or
service. The second-TTY capture showed no login prompt. See `bios/` for the
machine-readable run record and screenshots.

## UEFI

QEMU 11.1.2 used TCG with four vCPUs and 3 GiB of memory, OVMF code SHA-256
`2febd26c0b4cf95a636a941afa37d64a552723443ed9ac72f763f6840da98cb4`, and a
fresh copy of the OVMF variables file. The bounded run lasted 922 seconds; the
ISO hash before and after matched. systemd repeatedly failed to start
`systemd-nsresourced.service` and remained in early startup waiting for device
node setup. It did not show the AutoLoader app or service, and the second TTY
showed no login prompt. Serial output stopped at the UEFI boot menu and
contained no guest journal. See `uefi/` for the 300, 600 and 900 second screen
captures, run record and second-TTY capture.

Both QEMU processes exited with status 0. That is emulator-process completion,
not guest boot success. GPT SOL 6 LOW reviewed both records and captures on
2026-10-14; the JSON status `REVIEWED_BOOT_INCOMPLETE` records that evidence
review is complete while BIOS and UEFI live-app startup remain unvalidated. These are emulator-only observations; no physical USB or ThinkPad
was used. `SHA256SUMS` fingerprints the evidence files stored in this folder.
