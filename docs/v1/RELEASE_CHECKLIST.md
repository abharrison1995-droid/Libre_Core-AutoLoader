# V1 release checklist

At commit `8ea32c5`, the latest full regression passed on Windows (**955
passed, 71 skipped**; 1,026 collected; 1,687.79s; exit 0) and Ubuntu 26.04 WSL
(**1,022 passed, 4 skipped**; 1,026 collected; 1,497.18s; exit 0).
Full-project no-incremental mypy passed on 111 source files in Windows native
mode, Windows Win32-targeted mode, and Ubuntu WSL. All skips remain unpassed.
JUnit reports, hashes, and commands are in [`2026-10-21 software regression evidence`](evidence/software-regression-20261021/README.md).

The latest current-source wheel smoke passed in Ubuntu 26.04 WSL, including
four pinned patch resources, extracted imports, and CLI help. It verifies
packaging only; see the [current-source wheel record](evidence/current-source-wheel-x200-null-overlap-20261021/README.md).
The pinned Libreboot 26.01 X200 upstream build completed in WSL and produced
31 expected ROM variants. A separate validation-only run passed size and
pinned CBFS structural checks for all 31. This is not an ArtifactEngine
`COMPLETE` artifact or a flash-ready device-bound image; no ROM was copied
into the repository or accessed by hardware. See the [X200 diagnostic
record](evidence/x200-vboot-overlay-build-20261021/README.md).

At revision `be2fece`, the native Arch VM passed **1,017 tests, 2 skipped**,
Windows-targeted mypy on 105 files, and CLI help gates. It built a candidate
Archiso image (SHA-256
`a98e2c6039b28d5aad4359ebdbbaeaf2aecedc2135f3afdb01768e2c1cc401e5`) that
reached the live application interface in BIOS and UEFI QEMU. The gate wrapper
returned 1 at its final ownership-preserving copy to FAT; the output was
copied and its hash verified. This is virtual boot evidence, not ThinkPad
hardware acceptance, and later source changes still need their own build and
boot check. See [native Archiso evidence](evidence/native-arch-iso-be2fece-20261021/README.md).

The welcome-screen wording was corrected in `b35a1ab`; the captured ISO
predates that change and needs a current-source rebuild and boot check. The
X220 source-isolation correction at `8ea32c5` was accepted by SOL and focused
regressions require device-derived blobs, build outputs, tools, and the raw ROM
to remain within the operation-owned source tree. A real Coreboot ROM build
and device-bound artifact still await fresh hardware evidence. The current
Arch VM ran the current-source X220-focused native selection: 50 passed, 4
skipped, 972 deselected. An exploratory full native suite showed multiple
errors at about 7% and was stopped without JUnit output; it is incomplete. The
current native targeted mypy attempt raised an internal error. Top-level CLI
help and `build-usb --help` now pass; the latter passed after correcting the
extracted scratch tree's ownership. Four current-source product build attempts
did not produce an ISO: the last advanced to the mkinitcpio keyboard hook and
was stopped before completing an initramfs. Subsequent startup retries 3–13
were diagnostics rather than native build/test gates and also produced no
ISO. Retry13 reached only a systemd debug shell; read-only `id` and `lsblk`
showed root and the expected ISO/workspace/results/read-only-source devices.
At that query snapshot, 88 jobs remained pending around `systemd-nsresourced`
and tmpfiles; later the transcript recorded the failure `[FAILED] Failed to start Namespace Resource Manager` (cause not established). The ISO hash was
unchanged. No normal live boot or rootless preflight passed. See
[current-source native Arch build evidence](evidence/native-arch-build-20261021/README.md)
and the [retry13 transcript/result](evidence/native-arch-build-20261021/boot-diagnostic-retry13-direct-kernel/README.md).
The current-source build remains open until normal userspace and the reviewed
rootless preflight are ready.
The build-time offline launcher remains fail-closed. No physical firmware
operation or hardware recovery has been performed; all three device workflows
remain pending and require explicit human authorization for writes and
recovery.

| Requirement | Status | Evidence |
|---|---|---|
| Latest software regression | SOFTWARE TESTED — PASS; RELEASE VALIDATION OPEN | At `8ea32c5`, Windows: 955 passed, 71 skipped; Ubuntu 26.04 WSL: 1,022 passed, 4 skipped; 1,026 collected in each, zero failures/errors. Full-project mypy passed on 111 source files in Windows native, Windows Win32-targeted, and Ubuntu WSL modes. All skips remain unpassed. Exact JUnit reports, hashes and commands are in [`2026-10-21 software regression evidence`](evidence/software-regression-20261021/README.md). Ubuntu is WSL, not a native Linux VM. |
| Native Archiso candidate | HISTORICAL CANDIDATE AND QEMU BOOT PASS AT `be2fece`; CURRENT-SOURCE BUILD/BOOT OPEN | Native Arch test suite at `be2fece`: 1,017 passed, 2 skipped; Windows-targeted mypy passed on 105 files; CLI help passed. That ISO booted to the application interface in BIOS and UEFI QEMU. The harness exited 1 at the final FAT ownership-preserving copy; the copied candidate hash was verified. At current source, the Arch QEMU guest passed the focused X220 selection (50 passed, 4 skipped, 972 deselected); top-level and `build-usb --help` passed. An exploratory full suite stopped at about 7% after errors, without JUnit. Four current-source builds did not produce an ISO; the last stopped during mkinitcpio. Native targeted mypy raised an internal error. [`Historical evidence`](evidence/native-arch-iso-be2fece-20261021/README.md); [`current build evidence`](evidence/native-arch-build-20261021/README.md); [`current software evidence`](evidence/software-regression-20261021/README.md). |
| Meaningful safety branch coverage | SOFTWARE TESTED — 85% TARGET MET; WRITER SOFTWARE REVIEW COMPLETE; POSIX PHYSICAL VALIDATION OPEN | Latest accumulated coverage is 577/678 branches (85.1%) across the fixed compatibility, engine, authorization, journal, backup, and backup-service denominator. Write-approval, write-service, and post-reboot rates remain separate and lower. The two fake-only crash/replay tests are included in the final Windows and Ubuntu WSL full suites. SOL's read-only audit found no new software blocker and accepted the crash/replay coverage. Residual missed branches and their categories are in [`safety coverage evidence`](evidence/safety-coverage-20261017/README.md). This does not prove real POSIX directory fsync, flashrom against a physical device, physical read-back, or boot; those gates remain open. |
| Application and CLI launch cleanly | SOFTWARE TESTED | Phase 03 final independent evidence at `448766a`: SOL reran 124 tests in 79.59s, mypy clean on 86 source files, CLI help passed, and the tree was clean. This software evidence does not include physical hardware validation. |
| No simulated build/flash/verification success | SOFTWARE TESTED | Orchestrator and `OnevyRainManager` fail closed for unimplemented operations; legacy `ThinkPadECManager` source download/build/flash and `verify_ec()` now raise explicit unavailable `ECModError` outcomes, with empty patch reporting. `tests/test_ec_mod_fail_closed.py` proves these calls make no subprocess or file-write calls and do not touch an existing arbitrary checkout. TUI verification remains `NOT_PERFORMED` without evidence; backup records are not presented as reverified. CLI regression tests reject the removed `--write`/`--yes` bypass. No EC workflow is available in V1. |
| CLI and TUI use one shared controller | PHASE 08 AND 10B SOFTWARE COMPLETE — SOL ACCEPT; NATIVE LINUX/HARDWARE PENDING | Phase 08 routes T430 approvals/returns and X200/X220 preparation, backup, build, artifact acceptance and resume through `ApplicationController`. Phase 10B's guarded X200/X220 write/read-back routes from both CLI and the dedicated mounted-screen challenge also call this controller. The screen-bound receipt shows the immutable command/artifact/backup/setup preview and can be cancelled or invalidated before service consumption. Explicit prepare/backup requests can perform read-only hardware I/O, but none was invoked in this implementation pass. Windows directory-fsync constraints prevent write invocation here. ArtifactEngine COMPLETE/device-bound builds, native Linux validation, physical write/read-back and boot evidence remain pending. |
| Post-reboot evidence intake | SOFTWARE TESTED — PENDING HUMAN REVIEW; HARDWARE ACCEPTANCE OPEN | CLI and Textual `BOOT_CHECK` share the same service. It reopens X200/X220 terminal write-verification or a consumed T430 return checkpoint, rechecks model-specific artifact/read-back or handoff binding, captures fresh local DMI/BIOS, and writes a one-use operation-owned sidecar. Reopen validates the full profile schema/chronology and X200 expected MAC. The guided TUI collects model-specific observations with a separate UTC timestamp and evidence reference for each check; X200 MAC observation has its own time/reference. CLI JSON and controller schema remain unchanged. Reports remain `PENDING_HUMAN_REVIEW` / `NOT_ASSESSED`; no success claim or terminal checkpoint transition is made. Earlier service-only fake selection: 24 passed in 319.27s; guided UI/service selection: 43 passed in 359.37s; per-check timestamp UI follow-up: 4 passed in 25.55s. Full-project mypy is clean on 108 source files. No hardware operation or boot validation was performed. |
| Read-only per-device readiness review | SOFTWARE TESTED — INPUT REVIEW ONLY | Shared controller/CLI/Textual report reopens persisted T430/X200/X220 checkpoints and hashes referenced artifact/readback/backup bytes. It uses terminal/consumed-return validators and reports absent current-device, boot, or recovery-volume inputs as `UNKNOWN`; maximum outcome is `REVIEW_INPUTS_COMPLETE`, never READY or write-authorized. Focused Windows tests passed 11 with 1 skipped; Ubuntu WSL passed 9. It performs no probe, backup capture, resume, mutation, approval, or write. These tests are software-only and do not pass hardware acceptance. |
| Model-specific compatibility checks block unsupported combinations | SOFTWARE TESTED — PHASE 02 CORE | Three explicit profiles and fail-closed tri-state identity, BIOS, artifact, topology, voltage, region and backup checks; 21 focused safety tests. Phase 08 connects the policy with explicit workflows; hardware observations and all three physical paths remain pending. |
| Pinned firmware artifact recipes and immutable manifests | SOFTWARE SCOPE ACCEPTED — SOL; X200 UPSTREAM DIAGNOSTIC PASSED; ARTIFACTENGINE COMPLETE/DEVICE-BOUND BUILDS PENDING | SOL accepted Phase 04 software commits `dec5cf903c8c4d09b464f14a38d84207f24a523a` and `7c5b14e270c34b4a9856dfbcafdec029be011c8e`. The implementation pins Coreboot 26.09/X220 and Libreboot 26.01/X200 recipes, a T430 official-source handoff descriptor, strict source/config/output/size/layout checks, pinned cbfstool payload inventories, and X220 backup-derived blob/non-BIOS-preservation checks. X200 validates the exact 31-variant pinned lbmk output inventory and selects only the configured SeaBIOS/corebootfb artifact. X200 binds its `cbfstool print -k` tool and parser-source identity to the pinned nested Coreboot base commit/tree `fcd716d9a272461da1fdbaf1c048eb5a52c7896b` / `e16fe240567cb3fa08619c6a23e51c4af142f6ac`, verifies the post-lbmk tree `627cbbe4f6e7cba7ff22524b82072f1d7b34321c`, and records the operation-generated resulting revision. X220 uses pinned `print -r COREBOOT -k` output and rejects verbose rows. X220 build intent is checked before compilation and the generated digest is bound only after output validation. Ubuntu 26.04 WSL is available and the pinned dependencies are installed except `g++-mipsel-linux-gnu`, unnecessary for x86 X200. SOL accepted the operation-scoped Git identity final-publication correction. A follow-up for the confirmed X200 U-Boot x86_64 objcopy option mismatch applies a one-line overlay only in an isolated lbmk copy and binds its provenance in COMPLETE manifests. The focused artifact/X200/X220 run passed 83 tests in 45.74s; Windows-targeted helper tests passed 1 and skipped 1 because Windows denied symlink creation (WinError 1314), while Linux coverage exercises that branch; `mypy --platform win32 autoloader` found no issues in 100 files; the temporary-copy wheel includes the patch resource; diff-check passed. SOL accepted the X200 U-Boot corrective patch. The pinned Libreboot 26.01 X200 WSL upstream diagnostic later produced 31 expected 4 MiB variants, and a separate validation-only pass accepted their pinned size and CBFS structural inventories. It did not publish an ArtifactEngine COMPLETE artifact. No X220 Coreboot ROM has been built. The 2026-10-15 X200 ArtifactEngine attempt failed closed before launching build commands because physical identity, observed chip capacity, and verified backup/recovery evidence were absent. |
| X220 source isolation before COMPLETE | SOFTWARE FIX COMPLETE — SOL ACCEPTED; REAL BUILD/HARDWARE PENDING | `8ea32c5` binds build inputs, tools, outputs, and the raw ROM to the operation-owned `work/coreboot-x220` tree. Focused Windows and Ubuntu WSL tests passed 39 each; targeted mypy passed on both. SOL accepted the scoped change. No real X220 ROM compile or physical validation occurred. |
| T430 pinned IVprep/1vyrain handoff service | SOFTWARE COMPLETE — SOL ACCEPT; HARDWARE PENDING | SOL accepted Phase 05 after implementation `22269468d05091e8989c4ae6a4ed2427a1f83bed`, correction `24ea28932ddbccb152b2b9aaed924f9f4b131481`, and legacy-checkpoint regression `4f359c2060e3ca809b2435830781aa95d91cdcec`. Current-capture preflight covers BIOS/password/power/UEFI/Secure Boot and selected direct onboard USB without dock/hub; media evidence requires DD mode and matching user-reported Rev 5 MD5. README publishes MD5 only; it is weak/legacy and is not image authentication, and local SHA-256 remains only a fingerprint. IVprep instructions state Win64, BIOS settings, AC power, non-Administrator launch, reboot/BIOS-region write, and brick risk. Phase 08 routes human stages and fresh return attestations through CLI/TUI; it does not download/execute/write any upstream content. No IVprep/1vyrain run or real-device validation occurred. |
| X200 Libreboot workflow | SOFTWARE COMPLETE — SOL ACCEPT; COMPLETE BUILD AND HARDWARE PENDING | SOL accepted Phase 06 commits `91ec91edaadba1e19dba4a15efc4dbe040680377` and `b06293ebba4d57c6809eb06e7ac636e96e2902d4`; these tie validated BuildIntentEvidence, Phase 03 two-read/two-volume backup and recovery evidence, pinned ArtifactEngine COMPLETE manifest, selected staged-file hash, final compatibility plan and durable `BACKUP_VERIFIED` operation journal. Artifact acceptance requires exactly one non-empty `fallback/payload` entry of type `simple elf`. Phase 08 routes preparation, backup, build/artifact acceptance and safe resume through CLI/TUI. Phase 10B adds a separate guarded write/read-back route; Windows directory-fsync blocks it on this host. The positive Phase 06 workflow still ends at STAGED/HARDWARE_PENDING. The pinned Libreboot 26.01 X200 upstream WSL diagnostic later produced 31 expected 4 MiB variants and a separate validation-only pass checked their pinned size and CBFS inventories. It did not publish an ArtifactEngine COMPLETE artifact or establish a device-bound image. No X220 ROM build or physical operation has occurred. |
| X220 Coreboot workflow | SOFTWARE COMPLETE — SOL ACCEPT; LINUX BUILD AND HARDWARE PENDING | SOL formally accepted Phase 07 software staging. The workflow checks exact X220/8 MiB/3300 mV/region-map evidence, complete backup/recovery, backup-derived blobs, pinned artifact/selected-file/source/tool/log validation, CBFS/IFD maps, non-BIOS preservation, and a durable `BACKUP_VERIFIED` journal. Phase 08 routes its preparation, backup, build/artifact acceptance and safe resume through CLI/TUI; a fake controller test proves the three validated blobs reach the artifact engine. This is not a real compile. The Phase 07 workflow reaches only `STAGED/HARDWARE_PENDING`; Phase 10B later adds a separate guarded write/read-back service. Windows directory-fsync blocks it on this host. No Linux build or physical operation occurred. |
| Phase 10B guarded X200/X220 external write/read-back service | SOFTWARE ACCEPTED — SOL; NATIVE LINUX/HARDWARE PENDING | SOL accepted the service and CLI/TUI integration slice. CLI requires a live OS TTY; TUI uses the dedicated mounted-screen challenge and screen-bound one-use receipt. Both routes require exact profile/checkpoint/request, fresh local identity and SPI probe, current user-reported voltage/wiring attestations, reopened full backup/recovery evidence, exact artifact and exact-action approval. It journals before a fixed no-shell external writer and verifies with an independent full-chip read-back. T430/internal/generic/raw restore paths are blocked. TUI cancel/unmount can revoke the receipt until the service consumes it. Final independent software gates: 354 passed, 2 skipped in 716.26s; mypy clean on 102 source files; top-level and flash CLI help and `git diff --check` passed. Tests used synthetic/fake fixtures only. Windows directory-fsync blocks runtime invocation on this host. No physical write, read-back, restore or boot operation was performed; Native Linux build/namespace/QEMU gates and all three hardware workflows remain pending; the WSL X200 diagnostic does not create device-bound build evidence. |
| T430 1vyrain workflow accepted on real hardware | HARDWARE PENDING | `HARDWARE_TEST_REPORT.md`. |
| X200 Libreboot workflow accepted on real hardware | HARDWARE PENDING | `HARDWARE_TEST_REPORT.md`. |
| X220 Coreboot build/workflow accepted on real hardware | HARDWARE PENDING | `HARDWARE_TEST_REPORT.md`. |
| Complete backups and read-back verified on real hardware | HARDWARE PENDING | `HARDWARE_TEST_REPORT.md`. |
| Live USB image builds and boots | CURRENT-SOURCE IMAGE BUILD AND PRODUCT BOOT OPEN; HARDWARE PENDING | Candidate b2 (`afde8ba28025530852547ec5042dd94ecece5a9b1d24f5da141365861cc35cbd`) passed target-root/CLI checks, but same-hash BIOS/UEFI QEMU runs stalled before AutoLoader/service evidence. Retry8's disposable Arch guest query returned 1 after 140.495 seconds with six locked versions but no build-host `archiso` or core/extra sync databases; the preflight stopped before identity/rootless checks, scratch, sync/install, build, or output. SOL confirmed `archiso` is build-host-only. Two newer generic QEMU 10.2.1 debug-shell diagnostics remained incomplete: Arch-boxes waited for a missing root UUID, and the Arch ISO remained at SeaBIOS EDD probing; neither yielded a shell or guest command. These attempts are not native Linux or product-ISO evidence. See [`VM debug-shell evidence`](evidence/vm-debug-shell-20261021/README.md) and [`Arch ISO debug-shell evidence`](evidence/archiso-debug-shell-20261021/README.md), alongside the earlier [`retry8 captures`](evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry8/). No current-source product ISO has been built or booted; app/service boot, offline-isolation validation, and hardware actions remain open. |
| Documentation matches implemented and validated support | UPDATED — SOFTWARE REVIEW OUTCOMES RECORDED; RELEASE VALIDATION IN PROGRESS | Phase 09 software checks and target-root install validation are complete. Candidate b2 BIOS/UEFI runs stalled before AutoLoader/service evidence. Retry8 remains the latest product-builder gate attempt: its package query found six locked versions but lacked host-only `archiso`; the gate stopped before identity/rootless checks and any build work. Retry7 is earlier and had no recorded query result. Two later generic QEMU debug-shell attempts (2026-10-21) also remained incomplete: one waited for a missing root UUID and one remained at SeaBIOS EDD probing. These WSL/QEMU TCG diagnostics are not native Linux/KVM validation or current-source product ISO boot evidence. Successful BIOS/UEFI app/service boot, offline-isolation validation, native build gates, and the three real-device workflows remain pending. Phase 10 release qualification and Phase 11 are incomplete; no device workflow is release validated. |
| Phase 00 SOL review accepted | ACCEPTED | SOL accepted Phase 00; four follow-ups were finalized in `2d0e2d1` and the decision wording was aligned in `4c33444`. |
| Phase 01 SOL review accepted | ACCEPTED | SOL accepted Phase 01; the welcome/version, provisional compatibility and chip-size estimate follow-up was resolved in `207a7df`. |
| Phase 02 SOL review accepted | ACCEPTED — SOFTWARE ONLY | SOL accepted the standalone policy, immutable evidence, durable journal and digest-bound single-use approval. Implementation commits `5ceb6f79d6924f73c073a4631cb65434e2058fbd` and `263d8d5b4625799f504abfb691ba2ba6bed22ec6`; evidence/documentation commits `ae494ebd27a427573a06e60c6c079c2d9ba526ae` and `f04f9d35957ee0e7575a17a023985c11d4c1254e`. Final checks: 101 tests passed, mypy clean on 82 files, editable install and CLI help passed. At Phase 02 acceptance all application write routes were blocked; Phase 10B later adds guarded external writing for X200/X220. T430 and raw restore remain blocked. |
| Phase 03 SOL review accepted | ACCEPTED — SOFTWARE ONLY | SOL accepted implementation commits `4ba1858eb38b1c35b748153aea0a6293db928ecb` and `448766a544452538ea9425a598384fd304c6a2df`. Independent final evidence: 124 tests passed in 79.59s, mypy clean on 86 source files, CLI help passed, clean tree. Phase 08 integrates probe, backup and recovery-evidence paths for explicit X200/X220 actions; Phase 10B later adds a guarded X200/X220 writer. No physical probe/read, measured voltage, backup, firmware compilation/write, post-write read-back, recovery or boot test occurred. All three hardware workflows remain pending. |
| Phase 04 SOL review accepted | ACCEPTED — SOFTWARE ONLY | SOL accepted implementation commit `dec5cf903c8c4d09b464f14a38d84207f24a523a` and corrective commit `7c5b14e270c34b4a9856dfbcafdec029be011c8e`. Software gates: 25 focused artifact-engine tests and 152 full-suite tests, mypy clean on 87 source files, editable install, CLI help and `git diff --check`. Phase 08 routes explicit CLI/TUI build requests through the artifact engine; this host stops them with `LINUX_BUILD_ENV_REQUIRED`. A later pinned X200 WSL upstream diagnostic built 31 images and passed validation-only size and CBFS structural checks, but it did not publish an ArtifactEngine COMPLETE manifest or device-bound image. No physical SPI probe/read, measured voltage, backup, firmware write/read-back, recovery or boot test occurred. All three hardware workflows remain pending. |
| Phase 05 SOL review | ACCEPTED — SOFTWARE ONLY | SOL accepted the handoff software scope, including corrective commit `24ea28932ddbccb152b2b9aaed924f9f4b131481`; the recommended correctly checksummed v1-checkpoint regression is in `4f359c2060e3ca809b2435830781aa95d91cdcec`. Phase 08 routes human T430 handoff stages and return attestations through CLI/TUI. Hardware remains pending; T430 remains an upstream human handoff, separate from the guarded X200/X220 writer. |
| Phase 07 SOL review | ACCEPTED — SOFTWARE ONLY | SOL accepted the software staging scope after reviewing the implementation and reports. LUNA locally passed 240 tests in 149.07s; the root agent independently passed 240 tests in 144.19s, mypy on 90 files, CLI help and diff-check. SOL did not execute the tests. Phase 08 now integrates safe CLI/TUI workflow stages; the X220 Coreboot build and all physical hardware workflows remain pending. |
| Phase 08 SOL review | ACCEPTED — SOFTWARE ONLY | SOL accepted implementation commits `3c6da0c260eaae8f436532244ca824dafc5bdb9f` and `3f6a8a25b2b61dada49690a3bf71b8ae2da69d65` after review. The 276-test run, mypy on 97 files, CLI/prepare-workflow help and diff-check were local verification; SOL did not run those tests. No actual hardware I/O or firmware build was run. |
| Phase 09 Live USB candidate-builder software | SOL ACCEPTED — SOFTWARE ONLY; CANDIDATE BUILT; BOOT/OFFLINE GATES OPEN | Phase 09 builder changes are SOL-accepted. Candidate b2 was built from snapshot `1197d9040a39d6da012f459b8bb1f45385ee0072` / tree `d2588ddff4f0c5d4115565f47360314553106d38`; target-root and packaged CLI checks passed. Same-hash BIOS/UEFI QEMU runs reached systemd on the live system but did not reach AutoLoader/service. The production network probe passes on Arch WSL with user-mapped isolation and exact package-owned tools. The earlier root-oriented probe stopped at devtmpfs with EPERM in Arch WSL; that result does not establish whether the pinned non-root `-N` stack is available. The current source now wires an online production route: pinned non-root/helper and isolated route-free mount preflights run before work/output, then online commands run under the same non-root identity with a sanitized environment and operation-owned pacstrap status overlay. Focused rootless tests passed 70/269 deselected and the direct route selection passed 7/332; native and Win32-targeted mypy each pass on 5 USB-builder source files. A current-source disposable-VM package/build run is pending with the parent; no new ISO is claimed. Offline remains fail-closed before work/output because a compatible no-egress launcher is unresolved. The tmpfs argument correction moves VFS flags into mount flags and filesystem-specific values into mount data; disposable exact-form mount validation succeeds. Post-correction full suites: Windows 595 passed/18 skipped (1093.64s), Ubuntu 26.04 WSL 610 passed/3 skipped (740.43s), and Arch WSL 612 passed/1 skipped (904.35s). Windows-targeted mypy is clean on 104 files from Windows and isolated Ubuntu/Arch runs; native Ubuntu/Arch mypy is clean on 104 files. Concurrent shared-cache mypy invocations initially errored internally; sequential no-incremental isolated-cache reruns passed, with cause not established. Windows top-level CLI help, Ubuntu `flash --help`, and `git diff --check` passed. At the initial rootless `-N` preflight checkpoint, focused tests passed 24 tests; later VM no-build probes confirmed the exact -N map/chroot cleanup shape but found pinned pacstrap 31-2 can return 0 after logging a package-install failure. At this earlier diagnostic checkpoint, the hash-pinned operation-owned pacstrap status-preservation patch remained under review and was not installed over `/usr/bin/pacstrap`; the later accepted implementation stages a hash-verified overlay in the operation PATH without modifying the system executable. A separate VM host-preflight attempt stopped at `setpriv` because one unrelated hash had been reused for four distinct util-linux binaries. The pinned util-linux 2.42.4-1 package is unchanged; per-tool hashes now match the VM evidence and are locked separately. Rootless/pacstrap focused selection passed 30 tests with 277 deselected on Arch WSL and Windows; Windows mypy is clean on four USB-builder source files. Online production execution is now wired behind the reviewed preflights, but the current-source VM package/build result is pending. Offline remains fail-closed; no ISO build was attempted from this implementation pass. WSL results are not native Linux validation. Native Linux build, offline ISO build, successful Live USB app/service boot, and all three hardware workflows remain pending; release is not complete. The earlier CRLF-blocked build attempt is historical and produced no ISO. |
| Phase 10A write-intent approval preparation | ACCEPTED — SOFTWARE ONLY | SOL accepted the approval-preparation scope at remediation commit `eeaa8f64b2af2d3ff8311aacf7016fc01d1952b4`. It reopens and validates the exact staged journal/artifact/backup before issuing intent; checksums do not protect against malicious same-account/admin changes. It does not call a writer or assert a write. |
| Phase 10B guarded write/read-back service | SOFTWARE ACCEPTED — SOL; NATIVE LINUX/HARDWARE PENDING | SOL accepted the service and CLI/TUI integration slice. X200/X220 CLI and dedicated mounted-screen TUI routes use the shared controller; TUI approval binds to the active screen/mount and is revocable until service consumption. Durable directory fsync is unavailable on this Windows host, so no writer invocation is possible here. No physical write/read-back, restore or boot test occurred. Software acceptance is not hardware acceptance. |
| Pinned upstream firmware builds and generated-image evidence | PARTIAL — X200 WSL UPSTREAM DIAGNOSTIC PASSED; RELEASE GATES OPEN | The pinned Libreboot 26.01 X200 upstream build exited zero and produced 31 expected 4 MiB ROM variants. A later validation-only run checked all 31 against pinned size and CBFS structure, including null-row interval overlap checks. This did not publish an ArtifactEngine `COMPLETE` manifest, copy an image into the repository, or establish a device-bound/flash-ready artifact. A real X220 Coreboot ROM build, native Linux build/ISO gates, and all hardware-derived image checks remain open. Current identity, topology and verified backup/recovery evidence remain mandatory before hardware operations. See [`X200 upstream evidence`](evidence/x200-vboot-overlay-build-20261021/README.md). |

V1 cannot be marked complete while any release-blocking software check or any of the three hardware workflows remains pending.

## Earlier release-candidate gate snapshot — commit 6cabb23d, 2026-10-16

- Full regressions at commit `6cabb23d`: Ubuntu 26.04 WSL **755 passed, 4 skipped** (759 collected; 1108.07s); Windows **699 passed, 60 skipped** (759 collected; 770.94s). Both JUnit reports record 0 failures/errors. Ubuntu skips are two `UNVALIDATED: host lacks trusted Arch pacman executable ownership evidence` cases, one Windows-specific host platform contract, and one Windows-specific production durability policy; the Arch trust checks are unvalidated, not passes. Windows skips include POSIX directory-fsync, ownership and process contracts, Linux-only namespace/device checks, several POSIX symlink-contract skips, and one separate account-restricted symlink case (`WinError 1314`). Skipped checks are not passes. Mypy passed on 108 files in Ubuntu and with `--platform win32` on Windows. Reports, hashes and command details are in [`software-regression evidence`](evidence/software-regression-20261016/README.md). The earlier Windows full result of 693/45 predates commit `6cabb23d`.
- These are software results; Ubuntu is WSL, not native Linux. They do not pass the native Linux/VM build, ISO/app-service boot, firmware build, or hardware gates.
- Current package wheel smoke passed in Ubuntu WSL: isolated wheel built, installed into a separate target, imported from that target, and both pinned patch resources were present. This is packaging validation, not a firmware build; details and hash are in `evidence/software-regression-20261016/wheel-build-and-install.txt`.
- Linux native build and current-source Live USB remain **PENDING**. The TCG Archiso candidate run is paused under the same guest process before the 2 GiB host-free-space reserve. It has not produced a verified ISO; emulator activity is not a pass.
- The pinned X220 Coreboot compile preflight is blocked before build because the real device-derived descriptor, ME and GbE inputs are absent. No synthetic regions were substituted; exact details are in [`X220 build prerequisites`](evidence/x220-build-prerequisite-preflight-20261016/README.md).
- T430 1vyrain, X200 Libreboot, and X220 Coreboot representative hardware acceptance remain **PENDING BY USER REQUEST**. No physical firmware write or recovery was authorized or performed.

## Earlier root-account Arch WSL software regression — 72a302b, 2026-10-16

- Arch Linux WSL2 full suite: **756 passed, 3 skipped** (759 collected; 1161.62s; exit 0). JUnit reports 0 failures/errors and 1161.431s. The skips are `Windows-specific production durability policy`, `UNVALIDATED: test requires a non-root host account so host-root ownership is unmapped`, and `Windows-specific host platform contract`; the ownership check is unvalidated, not a passing Linux check.
- Full-project no-incremental mypy passed on 108 source files in Arch WSL. Exact command, report, and SHA-256: [`software-regression evidence`](evidence/software-regression-20261016/README.md).
- This root-account run is retained separately; its host-ownership skip was unvalidated. Arch WSL is not native Linux, and this software run does not pass VM/native Linux, ISO, ROM, Live USB boot, or hardware gates.

## Arch WSL2 full suite under setpriv nobody — source/test tree 72a302b, 2026-10-16

- Arch WSL2 full suite: **757 passed, 2 skipped** (759 collected; 1245.94s; exit 0); JUnit time is 1245.846s, with zero failures/errors. The exact skips are `test_windows_directory_fsync_policy_keeps_approval_closed` (Windows-specific production durability policy) and `test_real_build_usb_cli_fails_before_subprocess_on_windows` (Windows-specific host platform contract). The non-root host-ownership test that was unvalidated in the earlier root-account run passed here.
- Invocation used WSL root to launch `setpriv` with UID/GID 65534 (`nobody`), cleared supplementary groups/capabilities, `NoNewPrivs=1`, `PATH=/usr/bin:/bin`, and `HOME`/`TMPDIR=/dev/shm`; pytest cache and bytecode writes were disabled. The exact command, report, and SHA-256 are in [`software-regression evidence`](evidence/software-regression-20261016/README.md).
- Tested Python source and tests remained at `72a302b`; docs-only commit `0d74d76` landed during the run. This is WSL2 software evidence, not native Linux or disposable-VM validation. It does not validate the dedicated `fwbuild` account, fixed privileged launcher, production offline execution, package installation, ISO build, boot, ROM, or hardware gates.

The 2026-10-15 Arch WSL mount-capability follow-up added private mount/PID
namespaces to the existing user-mapped network namespace. Proc, sysfs and
tmpfs mounts succeeded in a disposable namespace; devtmpfs, bind mounts for
`/dev` and `/run`, and Archiso's `devpts gid=5` mount failed in the then-current
root-oriented probe. This historical result does not test the pinned
non-root `-N` mount stack. A separate exact `arch-chroot -N` preflight is now
implemented and synthetically tested; it has not yet run from the current
source in the disposable Arch VM. The later rootless `-N` preflight passed all
12 mount operations and cleanup/rechecks in the disposable VM. The current
source now wires online builder execution after the pinned host and mount
preflights; the parent is handling the current-source VM package/build gate.
Offline execution remains fail-closed. Signed install, full ISO build,
target-root check, and same-hash BIOS/UEFI app/service evidence remain open.
The test and mypy counts in the
historical checkpoint below are not current.

The separate Arch WSL QEMU acceleration preflight is recorded in
`docs/v1/evidence/live-usb-b2-diagnostic-20261014/qemu-acceleration-preflight-20261015.txt`.
QEMU 11.1.2 listed tcg, mshv, nitro, and kvm, but `/dev/kvm` and `/dev/mshv`
were absent. A bounded `-accel mshv` probe exited 1 with `Failed to open
/dev/mshv` and `failed to initialize mshv: Operation not permitted`. This
establishes no accelerated guest path in Arch WSL, and does not establish
native Linux/KVM availability. Existing WSL TCG boot results are unchanged;
native Linux comparison, successful app/service boot, offline ISO build, and
hardware workflows remain pending.

## Earlier Phase 04 cross-platform software evidence

At the earlier checkpoint after the accepted Git identity correction, the full Windows suite passed **395 tests, 9 skipped in 877.46 seconds** and the full Ubuntu suite passed **402 tests, 2 skipped in 427.12 seconds**, both exit 0. Native Ubuntu mypy then reported a Windows-only `ctypes.windll` attribute issue, resolved by the later guarded lookup below. No firmware build or hardware/media operation was part of these software tests.

## Earlier Live USB root-mount regression

A later clean Arch WSL attempt built temporary ISO SHA-256 `9d6fd80e5757b2f5a075fd46f5d60548c782166cacf95378d6bde2ad3dee42fc` (1,285,586,944 bytes) from snapshot `349e7961264131f69019ad1ab61cfa950af688a8`. Target-root install validation passed. Same-hash BIOS and UEFI QEMU boots both failed to mount the live root; BIOS reported missing `archiso`, `archiso_loop_mnt` and PXE hooks, and neither mode reached the app/service or produced guest journal evidence. The accepted profile/package-lock correction addresses missing hook runtime packages; focused Arch tests pass 105 with 2 skipped and mypy is clean. A new build and successful BIOS/UEFI app/service boot remain release gates.

## Source-pin audit correction (SOL accepted)

The full exact-byte audit corrected CRLF-derived source pins for lbmk X200 and the artifact-engine 1vyrain handoff, and includes the prior X220 corrections. Nested lbmk-Coreboot parser and IVprep handoff pins matched exact raw source bytes. Regression coverage asserts source maps and recipe-config hashes; the focused suites pass 76 tests with 1 skipped and mypy is clean on 102 source files. See [the Phase 04 source audit](PHASE04_ARTIFACT_ENGINE.md) for exact commit/tree/hash evidence. SOL accepted this correction. At this exact-byte audit checkpoint, no firmware build or hardware/media operation had been performed; hardware workflow validation remains pending.

## Phase 09 package inventory follow-up

SOL accepted this correction. The builder prevalidates target-root var/lib/pacman/local and queries only the image DB with fixed pacman argv. Historical fake-only USB acceptance checkpoint: 79 passed, 2 skipped; mypy usb_builder and git diff --check passed. This was the earlier software-acceptance checkpoint; a later temporary ISO/QEMU attempt is recorded above. No hardware or media action occurred.

## Phase 04 X200 GRUB patch-chain identity follow-up — SOL accepted

The GRUB base commit/tree and ten exact ordered patch hashes are pinned, with every intermediate tree verified through the final tree. Generated `git am` commit IDs are recorded per operation and verified on reopen against their linear ancestry and pinned trees; they are not treated as fixed values. SOL accepted this correction. Focused ArtifactEngine/X200/X220 tests passed 90 with 2 skipped, mypy passed for the three touched modules, and diff-check passed. At this patch-chain checkpoint, Linux ROM compilation and real manifest reopen were pending. The later X200 upstream diagnostic passed structural validation but produced no ArtifactEngine COMPLETE manifest; X220 build and hardware qualification remain pending.

## Earlier cross-platform full software regression — SOL accepted

The SOL-accepted X200 Unifont manifest-portability correction keeps real source-font hash and package-owner/version checks at build time, while manifest reopen binds the pinned source identity to the operation-owned staged font bytes. Approval/controller policy tests use a test-local synthetic artifact adapter; dedicated ArtifactEngine tests continue to enforce strict persisted-manifest validation.

The full Windows suite passed **451 tests, 15 skipped in 981.30 seconds** (exit 0). The full Ubuntu suite passed **464 tests, 2 skipped in 609.63 seconds** (exit 0). After those full runs, `is_admin()` gained a guarded Windows-only DLL lookup; the focused flasher tests passed **26 in 9.94 seconds**. Windows and Ubuntu Windows-targeted mypy passed on 102 source files, and native Ubuntu mypy passed after the guard. Top-level and flash CLI help passed, the isolated-backend wheel build passed, and `git diff --check` passed. These are software-only tests with synthetic/fake evidence. ArtifactEngine COMPLETE/device-bound output and X220 ROM compilation (the X200 diagnostic later passed structural validation), Live USB offline isolation and successful BIOS/UEFI app/service boot, and the three real hardware workflows remain pending. No workflow is marked hardware passed.

## Current Arch WSL online-build preflight

The production mount-capability preflight was called directly from the shared
checkout and failed closed at `devtmpfs` with
`ARCHISO_MOUNT_CAPABILITY_UNAVAILABLE`. Because both online and offline
`execute_build` modes require this check before work/output creation, no
candidate build, repository sync/download, package installation, or
`mkarchiso` invocation was made. The exact command and result are recorded in
`docs/v1/evidence/offline-namespace-arch-wsl-20261015/online-candidate-readiness-preflight-20261015.txt`.
This is Arch WSL evidence only, not native Linux build validation. Native
build, successful ISO app/service boot, and hardware workflows remain pending.

## Ubuntu WSL trusted-tool prerequisite check — 2026-10-15

Ubuntu 26.04 WSL resolved `/usr/bin/unshare` but has no `pacman`; therefore
the production `pacman -Qo` ownership check could not start and the preflight
returned `OFFLINE_NETWORK_ISOLATION_UNAVAILABLE`. It stopped before namespace
and mount checks, did not test devtmpfs, and created no candidate-build
work/output or invoked `mkarchiso`. Ubuntu WSL is not the pinned Archiso 91-1
build host. Exact evidence is in
`docs/v1/evidence/offline-namespace-ubuntu-wsl-20261015/`. The separate Arch
WSL run reaches mount checks and safely fails at devtmpfs; native Linux build,
successful ISO app/service boot and hardware workflows remain pending.

A 2026-10-15 direct-kernel diagnostic masked only nsresourced service/socket on the unchanged b2 image. Serial confirmed the masks, but the guest still reported `sysinit.target` waiting on `systemd-tmpfiles-setup-dev.service` at 270 seconds. No guest command, app/service success, normal boot pass, or root cause was established. It is emulator-only evidence; QEMU exited 0 after the planned HMP quit, and its controller reported a monitor reset after shutdown. Details and hashes are in `docs/v1/evidence/live-usb-b2-diagnostic-20261014/nsresourced-mask-20261015/`. Normal BIOS/UEFI app/service and native Linux/KVM gates remain open.

The offline rootless launcher preflight contract is implemented, but no
reviewed root-owned launcher path/hash/authorization pin is deployed. The
production route remains fail-closed with
`OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE` before launcher-specific scratch/child
creation and before builder work/output. The contract binds all four host
namespace IDs and requires each child namespace ID to differ, with
before/after network observations bound to
the child network namespace. The existing trusted-host and mount preflights,
including their child processes, run before this contract. The missing pin
stops only before launcher-specific scratch/child creation and builder
work/output. The planned helper mode is exact root-owned
setuid `04755`, with root-owned non-writable parent directories; its scratch
parent must be root-owned sticky mode `01777`, and its fresh child caller-owned
mode `0700`. Synthetic contract tests are not operational compatibility
evidence; controlled disposable-VM package-input proof for `pacstrap -N`
remains required. Offline ISO build remains pending.
SOL accepted the separate build-execution guard: a synthetic `SUPPORTED`
no-build preflight result is still stopped with
`OFFLINE_ROOTLESS_BUILD_RUNNER_UNAVAILABLE` before source/work/output,
package/build commands, or online-runner selection. Earlier host or mount
preflights may stop production first; if they pass, the absent pin returns
`OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE`. Offline
build and `pacstrap -N` remain unavailable/unvalidated. This is distinct from
the runtime network-policy work, which has no booted-ISO validation.
SOL accepted this contract; the independent focused Arch Linux WSL2 re-review
passed **53 tests in 3.15s**, and mypy passed on the two touched source files.
The current-source Arch WSL2 non-root full suite passed **809 tests, 2
Windows-specific skips, 0 failures/errors** (811 collected; 1771.18s; exit
0). JUnit hash and exact command are in
`docs/v1/evidence/software-regression-20261016/README.md`. These are WSL
software checks; they do not validate launcher deployment, `pacstrap -N`
package-input compatibility, or an ISO build.

## Retry8 builder-host diagnostic

The one SOL-reviewed disposable Arch guest run stopped at the exact locked
host-package query. Six versions matched; host-only `archiso 91-1` was absent
and the `core`/`extra` repository sync databases were missing. The guest correctly stopped before
rootless/mount checks, scratch probing, or build work. This is a diagnostic
environment mismatch, not evidence of a live-image packaging defect; the
host lock and runtime profile remain unchanged. No package install/sync, ROM,
ISO, or hardware operation occurred. Raw captures and checksums are under
`docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry8/`.
SOL accepted the no-launch host/runtime distinction regression; its focused
Ubuntu run passed **2 tests in 1.11s**. This confirms fail-closed behavior
against the captured result, not successful builder-host readiness.

**Still release-blocking:** prepare a disposable build guest with signature-
verified pinned host tools, pass rootless and mount preflights, complete the
current-source Live USB build, and boot the same ISO through BIOS and UEFI to
app/service/no-writer evidence. Complete and inspect the X200/X220 ROM builds.
All three physical workflows and their hardware-specific tests remain
deferred until these non-hardware gates pass; explicit human authorization is
still required before any physical firmware write or hardware-specific
recovery.

## Earlier current-source Ubuntu WSL regression checkpoint — 2026-10-17

The complete suite passed **839 tests, skipped 6, with 0 failures/errors**
(845 collected; 1,391.29 seconds; exit 0). Native-Linux and Win32-targeted
mypy each reported no issues in 109 source files. The exact command and
environment are recorded in
`docs/v1/evidence/software-regression-20261017/README.md`. This is WSL
software validation; the skipped tests are not passed checks, and native VM
builder, Live USB boot, ROM, and hardware release gates remain open.

The same checkpoint read-only audited the existing pinned X200 lbmk source
(35 files) and X220 Coreboot source (6 files); both origins, revisions, trees,
and pinned hashes matched. These checkouts are not a clean native builder VM
and the audits created no firmware artifacts.

## Earlier current-source Windows regression checkpoint — 2026-10-17

The Windows full suite passed **752 tests, skipped 62, with zero failures or
errors** (814 collected; 789.46 seconds; exit 0) on Python 3.12.14 / pytest
9.1.1. The Ubuntu full suite listed above ran before the accepted pytest
discovery change; the later default-collection and explicit-probe checks are
recorded separately in the evidence report. The skips cover POSIX durability/process/ownership/symlink behavior,
Linux namespace/device/pacman checks, POSIX-only host checks that remain
unvalidated, and one account-specific symlink privilege limitation
(`WinError 1314`). Skips are not passed checks. Exact command and JUnit hash
are in the linked evidence report. The accepted pytest discovery setting
keeps archived evidence probes out of the default suite; default collection
found 814 application tests, while explicit evidence probes passed 31 with 2
skipped. Frozen retry7/retry8 evidence files were unchanged. This remains
software-only evidence. Native Linux VM build,
current-source ISO, BIOS/UEFI app/service boot, ROM validation and hardware
workflows remain pending.

## Historical read-only VM storage preflight — 2026-10-17

Windows QEMU 11.1.2, the pinned Arch ISO, snapshot lock, and package-list
hashes were verified. The user selected a full VM on this host. Current
read-only checks at that historical checkpoint showed C: had 3,187,257,344
bytes free (about 2.97 GiB). At that time, the 254.9 GB Linux GPT partition
was still unmounted while permission to inspect and use it was pending. The
user later authorized it as the VM workspace and ran the elevated mount
command on 2026-10-20. It is now mounted at
`/mnt/wsl/PHYSICALDRIVE1p2`, with VM files confined to the approved
`/opt/librecore-autoloader-v1-vm` directory on that partition. No persistent
build VM was created. This preflight is not VM/build/ISO/ROM/hardware
validation. See
[`VM preflight evidence`](evidence/vm-preflight-20261017/README.md).

## VM workspace and boot attempt — 2026-10-20

- [x] User-authorized workspace created at
  `/opt/librecore-autoloader-v1-vm` on the 254.9-GB ext4 partition; no
  formatting or changes to pre-existing files.
- [x] Arch-boxes base image signature and SHA-256 verified; diagnostic
  overlay passed `qemu-img check` with no errors.
- [x] Official Arch 2026.10.01 ISO detached signature and SHA-256 verified.
- [ ] Full guest reaches a verified login — **NOT PASSED**. Installed-image
  startup entered root systemd but did not reach login/SSH. A final single-vCPU
  QEMU 10.2.1 TCG boot of the verified generic Arch ISO mounted its live root
  and started systemd, but a loopback-serial `id` probe returned no identity
  result within 360 seconds. This is not a shell, native-Linux, product-ISO,
  or application boot pass.
- [ ] Native Linux pinned build, current-source product ISO, and Live USB
  app/service boot — **OPEN**.
- [ ] Three physical firmware workflows — **HARDWARE PENDING**.

See [`VM bring-up evidence`](evidence/vm-bringup-20261020/README.md) for
hashes, retained serial/signature records, run configuration, observations,
and test boundaries. Native Windows QEMU 11.1.2 WHPX/TCG full-ISO attempts also failed to reach a guest shell; see [native QEMU evidence](evidence/native-qemu-windows-20261021/README.md). A later acceleration check confirms the i3-3110M itself
supports VT-x/EPT, but Windows 10 WSL 3.0.3 does not expose usable nested VMX;
the KVM ioctl/module fails. Two diskless QEMU WHPX direct-kernel diagnostics
reached the kernel but panicked before a shell with devtmpfs/console errors and
`Failed to execute /init (error -1)`; a retry with the default PIC had the
same outcome. Read-only initramfs inspection confirmed expected `/init`,
BusyBox shell link, and loader entries but did not identify the failure cause.
These tests are not a normal BIOS/UEFI boot. The user subsequently ran the
elevated mount command; Ubuntu WSL now mounts the approved ext4 workspace at
`/mnt/wsl/PHYSICALDRIVE1p2`. No BIOS setting, Windows feature, formatting, or
full Windows restart was changed; see the host recheck and captured logs in
the VM evidence folder.

A bounded Windows QEMU TCG direct-kernel comparison reached Arch's early
`udev`/`archiso_pxe_nbd` hooks and `Triggering uevents...`, but did not reach a
shell or verified guest command. It was stopped by the harness and is not a
boot pass.

## Earlier build evidence — 2026-10-21

The current-source wheel smoke passed package assembly and import-path checks
in Ubuntu 26.04 WSL; it is not ISO or firmware-build evidence. See the
[wheel smoke record](evidence/current-source-wheel-20261021/README.md).

The fresh pinned X200 upstream compile diagnostic ran for 6,751.435 seconds
and exited 1 while host GCC 15.2.0 compiled the pinned Coreboot `cbfstool`
utility. The `const`-qualifier warning was fatal under `-Werror`; no X200 ROM
was generated, ArtifactEngine `.build()` was not invoked, and no `COMPLETE`
manifest was published. The full log and provenance are in the
[X200 upstream diagnostic record](evidence/x200-upstream-compile-resume-20261021/README.md).
This Ubuntu WSL result is unaccepted compiler evidence only. At that
checkpoint, any retry or compiler/source adjustment awaited review; SOL later
accepted the exact one-line const-correctness overlay.

## Schema-4 software release checkpoint — 2026-10-21

After the reviewed X200 provenance correction, the complete software suites
passed on Windows (**929 passed, 68 skipped; 997 collected; 0 failures/errors**)
and Ubuntu 26.04 WSL (**993 passed, 4 skipped; 997 collected; 0 failures/errors**).
Full-project mypy passed on 111 source files in Windows native mode, Windows
Win32-targeted mode, and Ubuntu WSL. The skips remain unvalidated. The updated
wheel passed packaging, inclusion of all four pinned patch resources, extracted
imports, and extracted CLI help. Detailed reports and hashes are in
[`software regression evidence`](evidence/software-regression-20261021/README.md)
and [`wheel evidence`](evidence/current-source-wheel-schema4-20261021/README.md).

These are software and WSL results. Native Linux build-VM and Live USB
BIOS/UEFI app/service gates remain open. At this checkpoint, a fresh X200
upstream build with the reviewed vboot overlay was being evaluated; the later
31-ROM build and validation-only result is recorded above. It did not create
a flash-ready artifact. The T430, X200, and X220
real-device workflows remain **HARDWARE PENDING**. No physical write or
hardware-specific recovery operation has occurred; those actions still
require explicit human authorization.
