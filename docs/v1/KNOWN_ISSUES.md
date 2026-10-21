# V1 known issues

This list is evidence-based at baseline commit `d5e6810327bad6782f48326ee3f3d707bc6395e`; it will be updated as fixes land.

| Priority | Issue at baseline | User impact | Phase 01 status | Remaining resolution |
|---|---|---|---|---|
| P0 | Orchestrator's build loop fabricates progress and completion without invoking a builder. | Can lead the UI to offer a flash step with a nonexistent ROM. | FIXED: explicit unavailable outcome; no completion or ROM path; regression tested. | The shared controller and Phase 04 artifact service are integrated. ArtifactEngine COMPLETE/device-bound build evidence remains pending and requires fresh device identity/topology plus complete backup/recovery prerequisites. |
| P0 | Baseline TUI flash and verification screens showed success without accessing hardware or checking a ROM. | Misrepresented an unperformed flash as successful. | FIXED: legacy screens report `NOT_PERFORMED`; Phase 10B adds a dedicated outcome-backed X200/X220 challenge screen and shared write service. The current host cannot pass the durable approval gate, and tests are fake-only. | Phase 10B software review is accepted; Linux durable-storage/tool validation and real-device acceptance remain pending. |
| P0 | `OnevyRainManager` accepts arbitrary BIOS versions for provisional models, simulates exploit execution, always approves unlock, and reports flash completion without a writer. | Can misrepresent unsupported BIOS compatibility, an unperformed unlock, or an unperformed firmware write as successful. | FIXED: compatibility and all exploit/unlock/flash methods return explicit unavailable outcomes; Phase 05 adds a separate pinned human handoff and regression tests prove no `UNLOCKED`/`COMPLETE` success state. | The outcome-driven T430 handoff UI is integrated and SOL-accepted. Human-run IVprep/1vyrain and real-device validation remain pending. |
| P0 | Baseline CLI `flash --write` bypasses model-specific compatibility checks and `--yes` skips interactive confirmation. | Unsafe combinations could be written unattended. | FIXED: application CLI write path routes through fail-closed orchestrator; bypass flags are removed and tested. | Phase 10B provides a guarded X200/X220 external SPI write/read-back path. Supported-Linux durability/tool validation and real-device acceptance remain pending; T430, internal SPI, generic writer, and raw restore/recovery routes remain blocked. |
| P0 | Application backup capture has not been exercised against real hardware. | Recovery copies may be incomplete or unusable on a specific machine. | Phase 08 connects explicit X200/X220 backup requests to the validated dual-read service; all tests use fakes and no physical capture occurred. | On each device, record exact identity and observed chip/topology, capture and independently verify complete backup copies on separate persistent volumes, and confirm recovery readiness before any write approval. |
| P1 | ArtifactEngine has not published a COMPLETE, device-bound firmware artifact. | No firmware artifact has passed the complete build, provenance, target-compatibility and installation-readiness gates. | Phase 08 routes the command through the pinned ArtifactEngine; native Windows returns `LINUX_BUILD_ENV_REQUIRED`. Ubuntu 26.04 WSL is available and the pinned Libreboot checkout/dependencies are present. A disposable-copy X200 follow-up addresses the confirmed U-Boot x86_64 `objcopy --target=efi-app-x86_64` failure with a one-line `--output-target` overlay, and binds the exact in-root cache path/remote, base commit/tree, seven patch contents, overlay and final dependency tree `12e245c8ad4d68099c7127afd5c61a0e36992b1c` into the manifest. SOL accepted the code/tests. The pinned X200 upstream diagnostic later built 31 expected 4 MiB variants and a read-only pass validated their pinned sizes and CBFS structure; it did not publish an ArtifactEngine COMPLETE manifest or device-bound image. X220 Coreboot compilation remains pending. | Obtain fresh local identity and read-only chip/topology evidence, then complete and verify the required backup/recovery set. Only then run the pinned ArtifactEngine X200 build in a supported Linux environment and validate its actual output; keep X220 build and all hardware acceptance pending. |
| P1 | Model database, compatibility claims and builder targets are not validated against all machine variants. | Model labels alone may not describe observed board/chip topology. | PHASE 02 CORE ADDED: only three V1 profiles can pass the standalone validator; broad database flags are ignored and incomplete evidence blocks. | For each physical unit, record exact identity, board and observed chip topology; resolve aliases/variants and match evidence to the pinned recipe before building or writing. The Phase 04/06/07 software recipes are accepted; real-device acceptance remains pending. |
| P1 | Normal BIOS/UEFI Live USB startup has not reached the AutoLoader app or service. The direct-kernel run8/run9 diagnostics captured systemd jobs and journal state, including nsresourced, but did not establish a root cause. | Candidate b2 (`afde8ba28025530852547ec5042dd94ecece5a9b1d24f5da141365861cc35cbd`) passed target-root and CLI package checks; initramfs inspection confirmed the `kms`-omitting derivative and retained PXE hooks. Same-hash BIOS (310s) and UEFI (922s) QEMU reached systemd on the live system but stopped in early startup; neither showed AutoLoader/service, and tty2 had no prompt. The UEFI guest showed repeated `systemd-nsresourced` failures and a `/dev` setup wait; BIOS showed a pending Namespace Resource Manager startup job. See `docs/v1/evidence/live-usb-b2/`. The strict user-mapped network namespace probe now passes in Arch WSL with exact pacman ownership checks for `/usr/bin/unshare` and `/usr/bin/ip`; the independent Ubuntu WSL inventory and production Arch result are recorded in `docs/v1/evidence/offline-namespace-*-wsl-20261015/`. This is WSL-only probe evidence, not an offline build or native Linux validation. | Diagnose the guest startup block and obtain same-hash BIOS/UEFI app, service, journal and no-writer evidence. Validate namespace/mount behavior on native Linux and exercise an offline build. Keep physical USB display/keyboard checks pending on all three models. |
| P1 | IVprep automation included download/downgrade execution paths without a workflow-level human authorization gate. | A workflow invocation could cause a firmware update/downgrade. | PHASE 05 SOFTWARE COMPLETE — SOL ACCEPT: direct download, WinFlash and chain execution APIs are quarantined; Phase 08 CLI/TUI accepts explicit timestamped human approvals and fresh return attestations only. | Real-device validation remains pending; do not execute IVprep from the app. |
| P1 | README installation repository URL and `generate-usb` example do not match current project/CLI. | Users receive incorrect setup instructions. | FIXED: install URL and current command names documented. | Maintain during Phase 11. |
| P1 | Welcome screen claimed version 1.0.0 and advertised building/flashing; CLI and model-selection labels presented database flags and chip sizes as validated support/evidence. | Users could mistake an in-development release candidate or provisional database entries for implemented capabilities or observed chip topology. | FIXED: welcome uses the package version and states unavailable capabilities; CLI/TUI labels are provisional, and listed chip sizes are marked as estimates to verify. | Keep user-facing claims aligned as workflows are implemented. |

## Current release blockers — 2026-10-16

- **Live USB:** the prior b2 candidate stalls before the application/service in
  both BIOS and UEFI QEMU runs. The latest disposable-VM devtmpfs attempt was
  denied; patched pacstrap and direct mkarchiso no-repository probes correctly
  exited nonzero without an ISO. The VM builder/lock files were not the final
  working-tree versions. Keep production candidate execution closed until the
  current-source rootless mount, signed package install, full build, and BIOS/
  UEFI app/service boot gates pass. See the VM evidence linked from
  `RELEASE_CHECKLIST.md`.
- **Firmware artifacts:** no ArtifactEngine COMPLETE, device-bound X200
  Libreboot or X220 Coreboot ROM has passed the full parser and manifest gates.
  The X200 upstream diagnostic passed structural checks on 31 images only.
  Those workflows still require exact local identity/topology and verified
  backup/recovery evidence for hardware-derived firmware regions.
- **Hardware:** T430 1vyrain, X200 Libreboot, and X220 Coreboot remain
  **HARDWARE PENDING**. No physical probe, backup, write, read-back, recovery,
  boot, or function check has been performed.

## VM and native-build blocker — 2026-10-20

- The authorized workspace exists on the 254.9-GB ext4 partition. The
  verified Arch-boxes guest entered installed-systemd startup but did not reach
  login or SSH. A final single-vCPU QEMU TCG boot of the verified Arch ISO
  mounted its live root and started systemd, but a loopback-serial `id` probe
  returned no result within 360 seconds; no shell was verified. A later check
  confirmed the i3-3110M
  specification includes VT-x/EPT, but Windows 10 WSL 3.0.3 does not expose
  VMX to the Linux guest; the KVM module reports `VMX not supported by CPU 0`.
  A direct Windows Hypervisor Platform capability query succeeds. Two bounded
  diskless WHPX direct-kernel diagnostics using the already-installed QEMU
  reached the kernel but panicked before a shell with devtmpfs/initial-console
  errors and `Failed to execute /init (error -1)`; using QEMU's default PIC did
  not change the outcome. Read-only archive inspection confirmed the expected
  `/init`, BusyBox shell link and loader entries but did not identify the
  failure cause. These are not normal BIOS/UEFI boots. The separately
  downloaded QEMU installer was not run because its manifest requires
  administrator rights. WSL shutdown detached the ext4 partition; the user
  subsequently ran the elevated mount command, and Ubuntu WSL now sees it at
  `/mnt/wsl/PHYSICALDRIVE1p2`. No Windows feature, BIOS setting, formatting,
  or unrelated partition content was changed. No
  guest build, product ISO, ROM, or application boot has passed. Keep
  VM-login, native-Linux, ISO, and boot gates open. Evidence, hashes, and
  boundaries are in [`VM bring-up evidence`](evidence/vm-bringup-20261020/README.md).
- The firmware tools, models, and recovery equipment remain available for a
  later human-authorized stage. No physical hardware action has been taken;
  all three workflows remain **HARDWARE PENDING**.

## Current software regression and executable provenance — 2026-10-20

- The current-source full suite passed on Windows (**890 passed, 63 skipped**)
  and Ubuntu 26.04 WSL (**949 passed, 4 skipped**), with 953 collected and no
  failures/errors on each. Mypy passed on 109 source files on both hosts.
  Skipped checks are explicitly unpassed; see the current
  [`software regression evidence`](evidence/software-regression-20261020/README.md)
  for categories, commands, hashes, and the non-passing earlier WSL report.
- Flashrom executable provenance is hardened in software: the runner pins a
  canonical protected executable, exact trusted package-owner record,
  fingerprint and digest; it revalidates provenance around subprocess calls
  and uses a fixed process policy. Backup evidence and write approvals bind
  tool digest/owner. SOL accepted this software slice. Ubuntu WSL lacks
  flashrom/flashprog; its `gnutrue` Debian ownership smoke check is not
  flashrom integration. Native-Linux package integration and physical SPI
  validation remain open.

## Phase 02 acceptance-era limitations (updated for later phases)

- Phase 08 connects model workflows to CLI/TUI. Explicit X200/X220 prepare/backup can perform read-only hardware probe/read operations, but none were exercised here. Phase 10B later added a guarded X200/X220 external SPI writer with independent full-chip read-back; this software path has not been exercised on hardware. T430 remains a human handoff. Internal SPI and raw restore/recovery remain blocked, and boot checks are unperformed. Phase 03 services and synthetic fixtures do not establish hardware results.
- Device, programmer-voltage, capture-ID and region observations are immutable inputs, but the policy core cannot establish that a human supplied observation is true. Phase 03 provides read-only probe/evidence services that Phase 08 exposes through explicit actions; no real probe or voltage measurement has been performed.
- A backup capture ID records distinct reads but is not signed acquisition provenance. The core additionally requires separate files, exact chip capacity, nonblank contents, matching declared hashes and equal reads; capture-origin proof remains to be integrated.
- Artifact content is checked against its declared SHA-256 and source revision must be a full 40-hex commit. The current core does not fetch upstream source or prove that the commit belongs to the URL; Phase 04/05/06/07 must produce independently reviewable provenance.
- The Phase 02 journal now durably records operation IDs, timestamps, state transitions, device fingerprints, artifact provenance/content checksums, backup capture/checksums and failure details. It rejects corrupt checkpoints and permits resume only from DRAFT, VALIDATED or BACKUP_VERIFIED after exact evidence matching and revalidation. Its SHA-256 chain detects accidental corruption but is not keyed against a hostile editor; the journal is not a signed hardware-attestation source.
- Terminal BLOCKED, FAILED and WRITE_BLOCKED journals can be inspected but cannot be resumed. AUTHORIZED and later states cannot be resumed. At Phase 02 acceptance its writer boundary was blocked; Phase 10B later added a separate evidence-bound X200/X220 external writer/read-back service. T430, generic and restore routes remain blocked.
- External SPI profiles currently fail closed unless both the programmer output and every observed chip are independently recorded at exactly 3300 mV. This software policy does not measure voltage.

## Phase 03 acceptance-era limitations (updated for later phases)

- SOL accepted Phase 03 software scope at commits `4ba1858eb38b1c35b748153aea0a6293db928ecb` and `448766a544452538ea9425a598384fd304c6a2df`. Phase 08 integrates probe and backup/recovery evidence services with explicit X200/X220 workflow actions; Phase 10B later integrates read-back into the guarded write service. Only fake transports/software fixtures were used.
- No actual SPI probe/read, measured voltage, physical backup, firmware write, post-write read-back, recovery operation or hardware boot test occurred. The X200 upstream diagnostic built and structurally validated 31 expected images, but no ArtifactEngine COMPLETE or device-bound firmware artifact exists. All three representative hardware workflows remain **HARDWARE PENDING**. X200/X220 physical writes require the guarded Phase 10B service; T430, generic, and restore routes remain blocked.
- Phase 04 software scope is accepted by SOL. A later pinned WSL X200 upstream diagnostic built 31 expected images and passed read-only pinned size and CBFS structural validation, but ArtifactEngine COMPLETE/device-bound build evidence and the eventual hardware-gated workflows remain pending; CLI/TUI app integration is complete.

## Phase 04 implementation and remaining limitations

- SOL accepted the Phase 04 software scope at commits `dec5cf903c8c4d09b464f14a38d84207f24a523a` and `7c5b14e270c34b4a9856dfbcafdec029be011c8e`; that acceptance checkpoint did not include upstream builds or hardware validation. Recipes are limited to Coreboot 26.09/X220 and Libreboot 26.01/regular-X200 4/8 MiB; T430 only presents an upstream 1vyrain handoff pinned to source commit `a3427b34102f8f9887acf65e23d7fd5900fe1d40`. It does not execute or bundle upstream T430 binaries. X200 output validation accounts for all 31 expected pinned lbmk ROM variants and selects only the exact configured SeaBIOS/corebootfb image; selected X200/X220 output must also pass pinned-source `cbfstool -k` inventory validation. X200 requires `fallback/payload` type `simple elf`; X220 requires type `payload`. X200's cbfstool is bound to lbmk's exact nested Coreboot revision/tree and parser-source hashes.
- X220 full-chip preparation requires a complete matching Phase 03 original backup, identical two reads, separately persistent destinations and checked manifests. Descriptor/ME/GbE inputs must be exact byte slices from that backup; both the original and generated descriptor maps must match the observed map, and all generated non-BIOS regions must byte-match the original. These checks use software evidence and fixtures only; they do not attest physical hardware.
- Ubuntu 26.04 WSL is available for build validation. The pinned Libreboot source checkout matches its commit/tree; its pinned dependencies were installed except `g++-mipsel-linux-gnu`, which is not needed for the x86 X200 target. Native Windows `ArtifactEngine.build` returns `LINUX_BUILD_ENV_REQUIRED`. An earlier X200 attempt compiled some upstream components but produced no complete ROM or accepted artifact. A later pinned X200 WSL diagnostic built 31 expected 4 MiB images, and a separate read-only validator passed pinned size and CBFS structural checks for all 31. It did not produce an ArtifactEngine COMPLETE manifest or device-bound image; no Coreboot/X220 build or validated manifest exists. The production ArtifactEngine gate still requires device identity/topology and complete backup/recovery evidence, plus supported-Linux preflight. The pinned parser has therefore been exercised against upstream-generated ROMs for structural validation, but no complete manifest was published. Fake-command/parser tests are not successful device-bound build evidence. The latest full Windows suite before the Git identity follow-up was 388 passed, 8 skipped in 674.32s. SOL accepted the current identity correction; its focused suite passed 77 tests with one Windows-only POSIX-permission case skipped and mypy is clean on 102 source files. The post-correction full suite passed 395 tests with 9 skipped in 877.46s (exit 0).
- Phase 08 routes pinned X200/X220 build and artifact acceptance requests from CLI/TUI through the workflow state and service validators. The pinned WSL X200 upstream diagnostic later built and structurally validated 31 images, but no ArtifactEngine COMPLETE manifest or device-bound image was produced; the X220 Coreboot build remains pending. Phase 10B adds an evidence-bound X200/X220 write route, but it cannot pass its durable directory-fsync gate on this Windows host; T430 and restore paths remain blocked.
- No measured chip capacity/voltage, physical SPI probe/read, backup, write/read-back, recovery, boot test, IVprep, or destructive media operation occurred. All three hardware workflows remain **HARDWARE PENDING**.

## Phase 05 implementation and remaining limitations

- Phase 05 now provides a standalone T430 handoff state machine, pinned source-instruction descriptors, strict BIOS token checks, human preflight/media evidence, UTC approvals, and checksummed durable checkpoints. The 1vyrain preflight binds Secure Boot disabled and intended direct onboard USB use (no dock/ultrabase/hub) to the current capture; media evidence binds DD mode and the operator-reported Rev 5 MD5. SOL accepted the software scope; a correctly checksummed legacy-v1 checkpoint rejection regression is included in `4f359c2060e3ca809b2435830781aa95d91cdcec`.
- A valid IVprep or 1vyrain return consumes the pending checkpoint and records the reported result/capture; consumed authorizations cannot resume. A fresh same-device detection after the recorded approval is required before recording return evidence. The evidence is human-reported and not hardware-attested.
- The pinned 1vyrain README publishes the Rev 5 image at its Google Drive URL with MD5 `dd30313a8665e870360920b00cec55c4` only. A matching user-reported MD5 is a weak legacy checksum comparison, not authentication; the local SHA-256 and USB/media identity are user-reported fingerprints only. No 1vyrain image or IVprep binary is bundled. IVprep/WinFlash/reversion download and execution APIs are fail-closed. Descriptors carry the pinned IVprep settings/power/reboot warnings and 1vyrain boot/media instructions.
- Phase 08 wires stage-specific T430 approval and return-attestation submissions through CLI/TUI. No physical T430 detection, BIOS downgrade, 1vyrain run, firmware/media write, boot test or post-install hardware validation occurred. T430 remains **HARDWARE PENDING**; Phase 05 software status is **SOL ACCEPTED**.

## Phase 06 implementation and remaining limitations

- SOL accepted Phase 06 software commits `91ec91edaadba1e19dba4a15efc4dbe040680377` and `b06293ebba4d57c6809eb06e7ac636e96e2902d4`. The standalone regular-X200 Libreboot staging service at `autoloader/workflow/x200_libreboot.py` validates evidence supplied by the caller, calls the read-only two-read backup service, revalidates recovery readiness and the selected pinned artifact, requires exactly one non-empty `fallback/payload` entry of type `simple elf`, and records a final `BACKUP_VERIFIED` operation journal.
- The positive tests use a fake SPI transport and a hand-built synthetic COMPLETE artifact manifest; this proves software binding and failure behavior only. An earlier X200 attempt compiled some upstream components but produced no complete validated `lbmk` ROM or verified release image. The later pinned WSL diagnostic built and structurally validated 31 images, but it did not create an ArtifactEngine COMPLETE manifest or device-bound firmware. The Windows host returns `LINUX_BUILD_ENV_REQUIRED` before an ArtifactEngine build command can run.
- Phase 08 routes the X200 workflow's preparation, backup, build/artifact acceptance and safe resume stages through CLI/TUI. It did not perform a physical operation during this validation run, and the software test fixtures do not validate device compatibility. The accepted write service is now integrated separately; this staging service still proposes only `STAGED/HARDWARE_PENDING` and does not authorize restore.

## Phase 07 implementation and remaining limitations

- SOL accepted Phase 07 for software staging after reviewing the implementation and verification reports. LUNA locally passed 240 tests in 149.07 seconds; the root agent independently passed 240 in 144.19 seconds, mypy on 90 source files, CLI help, and diff-check. SOL did not execute the test suite. The software does not establish hardware readiness.
- `autoloader/workflow/x220_coreboot.py` requires exact X220 evidence, one observed 8 MiB chip and matching external 3300 mV programmer evidence, a complete nonoverlapping region map with the 3 MiB BIOS area, persistent two-copy full-chip backup/recovery evidence, and backup-derived descriptor/ME/GbE slices.
- Artifact acceptance rechecks the persisted pinned manifest/sidecar, exact `artifacts/coreboot.rom` path (including Windows drive/UNC rejection and containment), operation logs, source/tool hashes, CBFS payload, generated/original IFD maps, non-BIOS byte preservation, finalized plan and backup provenance. Linked backup and log paths fail closed. A durable journal checkpoints only at safe `BACKUP_VERIFIED`; at Phase 07 acceptance the proposed whole-chip plan remained `STAGED/HARDWARE_PENDING`. Phase 10B later added a separate guarded X200/X220 external write/read-back path; raw restore/recovery remains blocked.
- Phase 08 routes X220 preparation, backup, build/artifact acceptance and safe resume through CLI/TUI. A synthetic controller test verifies that only backup-derived descriptor/ME/GbE blobs reach the artifact engine; it does not compile firmware. No pinned Linux Coreboot build, actual SPI probe, voltage measurement, physical backup, firmware write/read-back, recovery operation or boot test occurred. X220 remains **HARDWARE PENDING**.

## Phase 08 shared application controller and remaining limitations

- Code commits `3c6da0c260eaae8f436532244ca824dafc5bdb9f` and `3f6a8a25b2b61dada49690a3bf71b8ae2da69d65` route Click and Textual actions through one typed `ApplicationController` for the three V1 profiles. CLI/TUI can submit T430 approvals and fresh return attestations; prepare X200/X220 sessions from local identity/probe evidence plus explicit user-reported measurement/storage attestations; capture or reopen verified dual-read backups; run the pinned artifact engine; reopen/validate artifacts; and resume exact safe checkpoints. X220 build input is derived from and revalidated against its complete original backup. Journals alone do not create trusted workflow plans; imported JSON remains candidate-only.
- Worker visibility: active Textual actions keep their screen mounted and block screen Back, the global `b` binding and `q` quit until the actual controller result is shown. A fake-worker regression covers all three exit attempts and eventual result visibility.
- Current software gates: `python -m pytest -q` passed **276 tests in 167.44 seconds**; mypy reported no issues in **97 source files**; CLI help and prepare-workflow help passed; `git diff --check` passed. Phase 08 is SOL accepted.
- Explicit prepare/backup can issue read-only local SPI probe/read operations when a user runs them on a connected machine. None were invoked during this development/test pass. The Windows host returns `LINUX_BUILD_ENV_REQUIRED` before an ArtifactEngine build subprocess or output creation. A later WSL X200 upstream diagnostic built and structurally validated 31 variants, but did not publish an ArtifactEngine COMPLETE manifest or device-bound image; the 2026-10-15 ArtifactEngine workflow attempt blocked before build. No physical voltage/backup, firmware write, restore, post-write read-back, recovery or hardware boot test occurred. All three machines remain **HARDWARE PENDING**. The Phase 10B guarded X200/X220 application writer is now software-integrated but cannot pass this host's durable directory-fsync gate; T430 and raw restore remain blocked.

## Phase 10A approval preparation — SOL accepted

- SOL accepted Phase 10A remediation commit `eeaa8f64b2af2d3ff8311aacf7016fc01d1952b4`. It reopens the exact `BACKUP_VERIFIED` X200/X220 journal and pinned COMPLETE ArtifactEngine evidence before issuing a write-intent approval. CLI confirmation requires real process TTY handles. At Phase 10A acceptance there was no TUI route; Phase 10B later added a SOL-accepted dedicated mounted-screen receipt.
- Ledger, journal and manifest checksums provide corruption detection, not authentication against a malicious process running as the same local account or an administrator; such an actor can edit or roll back local files. Phase 10B re-detects/re-probes and reopens the exact backup, artifact and journal before consuming approval.
- Phase 10A itself makes no writer call and does not journal `WRITE_STARTED` or `WRITE_VERIFIED`. Its software tests are recorded in `PROGRESS.md`. This approval-preparation acceptance is not hardware acceptance.

## Phase 10B guarded external write and read-back — SOL accepted, software only

- SOL accepted the Phase 10B service and CLI/TUI integration software. Only exact X200 Libreboot and X220 Coreboot external SPI plans can reach the guarded service; T430, internal flash, arbitrary command arguments and raw restore paths remain blocked. The Click CLI write command goes through the shared `ApplicationController`, requires profile/checkpoint/request paths, a real OS TTY, live user-reported meter/wiring attestations and exact-action confirmation, then reopens/revalidates the workflow journal, COMPLETE pinned artifact, full backup and recovery evidence, and fresh local identity/probe before atomically consuming approval.
- The service records `WRITE_STARTED` before a fixed no-shell external writer, classifies timeout/nonzero/unexpected post-start failure as uncertain without retry, and only records `WRITE_VERIFIED` after a separate full-chip read-back whose final digest/size still matches the approved artifact. CLI and a dedicated mounted-screen TUI challenge route use the same controller/service. The TUI preview binds the displayed command and evidence digests plus current human setup values to its screen/mount; cancel or unmount revokes a receipt until service consumption. SOL accepted this software integration; it is not hardware-validated.
- The current Windows host cannot establish the required durable parent-directory fsync, so the live CLI route fails closed before hardware probing/writing here. No actual SPI probe, backup, build, write, read-back, restore, recovery or boot test was invoked. Linux tool validation and all three representative hardware workflows remain release-blocking.
- Software test fixtures and fake writer/readback runners do not demonstrate physical operation. Windows durable-directory-fsync constraints block runtime invocation on this host. Linux tool validation and all three real-device workflows remain release blockers; do not mark a V1 hardware workflow complete.

## Native VM and product-image gates — 2026-10-20

- The earlier IDE-backed Arch-boxes guest attempt entered emergency mode while waiting for its root UUID; the root account is locked. No shell, login, SSH session, or guest command was obtained. Evidence is retained in `docs/v1/evidence/vm-arch-boxes-20261020/`. This remains an unresolved VM boot failure and does not pass any native Linux build or Live USB gate.
- Generic Arch ISO attempts likewise did not provide a verified guest command. Ubuntu WSL software tests do not substitute for native Linux. A successful current-source Archiso build and BIOS/UEFI app/service boot remain release-blocking.
- A virtio-backed Arch-boxes run reached the Arch userspace welcome screen after mounting `/sysroot`, but no login or guest command was verified. The loopback-only SSH diagnostic produced no banner; a later bounded networked run remained in initrd device-node initialization. See `docs/v1/evidence/vm-arch-boxes-virtio-20261020/`. This does not close the native build-guest gate.

## Hardware state

T430 1vyrain, X200 Libreboot and X220 Coreboot: **HARDWARE PENDING**. No physical probe/read, measured voltage, backup, complete validated firmware ROM, write, post-write read-back, recovery or hardware boot evidence is recorded. Earlier X200 component compilation did not produce a complete validated artifact.
