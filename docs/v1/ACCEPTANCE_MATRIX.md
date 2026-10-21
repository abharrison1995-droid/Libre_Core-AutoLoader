# V1 acceptance matrix

`Software tested` means exercised by automated tests or a non-destructive build. `Hardware pending` means no real-device evidence is present in this repository baseline.

## Current-source software regression — 2026-10-21

At commit `8ea32c5`, the latest full suite passed on Windows (**955 passed,
71 skipped**) and Ubuntu 26.04 WSL (**1,022 passed, 4 skipped**); both
collected 1,026 tests with zero failures. Windows took 1,687.79 seconds;
Ubuntu WSL took 1,497.18 seconds. Full-project no-incremental mypy passed on
111 source files in Windows native mode, Windows Win32-targeted mode, and
Ubuntu WSL. All skips remain unpassed. JUnit reports, hashes, and commands are
in
[`software regression evidence`](evidence/software-regression-20261021/README.md).
Ubuntu WSL is not native Linux.

The pinned Libreboot 26.01 X200 upstream build and validation-only evidence
cover 31 ROM variants in WSL; they do not create an ArtifactEngine `COMPLETE`
artifact or prove a device-bound image is flash-ready. The historical native
Archiso candidate and BIOS/UEFI QEMU boot at `be2fece` are documented below;
they do not validate the current source. X220 device-bound ROM compilation
and all three physical acceptance workflows remain open.

Current-source native Arch status: the focused X220 selection passed 50 tests,
4 skipped, and 972 deselected; top-level and `build-usb --help` passed. The
exploratory full native suite stopped at about 7% after errors without JUnit,
and native targeted mypy raised an internal error. Four current-source
Archiso build attempts produced no ISO; subsequent startup retries 3–16 were
diagnostics rather than build/test gates and also produced no ISO. Retry13
reached only a systemd debug shell: read-only `id` and `lsblk` showed root and
the expected device map. At that query snapshot, 88 systemd jobs remained
pending around `systemd-nsresourced` and tmpfiles; later in the transcript
systemd printed `[FAILED] Failed to start Namespace Resource Manager` (cause
not established). Retry14 then observed the generic ISO for 600.376 seconds
with no guest input. Its live stop-marker scanner did not strip ANSI; a
post-run normalized audit found four Namespace Resource Manager failures, zero
normal startup target markers, and zero emergency/fatal markers. The cause is
unknown. ISO, kernel, initramfs, and source-image before/after hashes matched;
free space stayed at 153.786 GiB. Retry15 was a helper/serial-PTY handshake
failure with empty serial capture and no guest input. Retry16 captured the PTY
and four Namespace Resource Manager failures but reached the 600-second cap
without a stable post-failure shell prompt; no query or other guest input was
sent. Its ISO, kernel, initramfs, and source-image hashes also matched. These
generic-ISO diagnostics do not establish normal startup. No normal live boot,
rootless preflight, current-source build gate, or product ISO passed. Details and hashes are in
[current-source native Arch evidence](evidence/native-arch-build-20261021/README.md),
the [retry13 diagnostic](evidence/native-arch-build-20261021/boot-diagnostic-retry13-direct-kernel/README.md),
the [retry14 observation bundle](evidence/native-arch-build-20261021/boot-diagnostic-retry14-observation-only/README.md),
[retry15 PTY failure](evidence/native-arch-build-20261021/boot-diagnostic-retry15-pty-failure/README.md),
and [retry16 unresolved diagnostic](evidence/native-arch-build-20261021/boot-diagnostic-retry16-service-timeout/README.md).

## Historical native Archiso candidate and QEMU boot — source revision `be2fece`

At source revision `be2fece`, a native Arch Linux VM run passed **1,017 tests,
2 skipped**, Windows-targeted mypy on 105 files, and CLI help gates. The pinned
Archiso build produced a 1,017,458,688-byte ISO with SHA-256
`a98e2c6039b28d5aad4359ebdbbaeaf2aecedc2135f3afdb01768e2c1cc401e5`. The
candidate booted to the application interface under both SeaBIOS and
UEFI/EDK2 QEMU runs. The gate wrapper exited 1 at the final FAT-volume
`cp -a` because ownership preservation is unsupported; the copied ISO hash
was independently verified. Detailed logs, JUnit XML, package pins, build
provenance, and the screen capture are in [`native Archiso candidate evidence`](evidence/native-arch-iso-be2fece-20261021/README.md).

This is a candidate from `be2fece`, not validation of subsequent source
changes. QEMU boot is not a ThinkPad test. The welcome-screen wording was
corrected in `b35a1ab`; the captured ISO predates that
commit, so a current-source image still needs to be built and booted. The
build-time offline launcher is still fail-closed and unvalidated. The X220
source-isolation correction was accepted by SOL at `8ea32c5`; focused tests
verify operation-owned source/ROM containment and reject symlinked or external
checkouts. A real Coreboot ROM compile still requires fresh device topology and
verified backup evidence, and no physical acceptance workflow has been run.
The current-source debug-shell diagnostic reached only a systemd shell, not a
normal live boot. It showed root identity and the expected ISO, workspace,
results, and read-only source map, but 88 jobs remained pending around
`systemd-nsresourced` and tmpfiles at the query snapshot. The later transcript
recorded a Namespace Resource Manager failure; retry14's post-run scan found
four such failure markers without establishing cause. No native suite/build
gate or product ISO passed; see the current-source status above, the [retry13
evidence bundle](evidence/native-arch-build-20261021/boot-diagnostic-retry13-direct-kernel/README.md),
and [retry14 evidence](evidence/native-arch-build-20261021/boot-diagnostic-retry14-observation-only/README.md).

## Live USB runtime network profile update — 2026-10-20

The current source profile sets `ip=off` in both BIOS and UEFI boot entries,
masks the explicit network-activation unit list to `/dev/null`, and isolates
the AutoLoader service tree with `PrivateNetwork=yes`, `RestrictAddressFamilies=AF_UNIX`,
`NoNewPrivileges=yes`, and a capability bound excluding `CAP_SYS_ADMIN` and
`CAP_NET_ADMIN`. Source and target-root checks enforce these settings, the
target-root check rejects policy-weakening drop-ins and higher-priority unit
overrides, and Linux target-root validation checks every mask link. Following
the precedence review, a focused Ubuntu WSL selection passed 21 tests (350
deselected), and an independent Arch WSL selection passed 15 (356 deselected).
Native and Win32-targeted mypy passed for `usb_builder/build_usb.py`; SOL
accepted this software-only slice. The latest full regression results are
summarized above and include the runtime changes. The source change has not
been built into an ISO, so it is not boot validation. Build-time `--network-mode offline` remains unavailable with
`OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE`; native Linux, ISO boot and hardware
gates remain open.

SOL accepted the separate offline execution guard. A synthetic `SUPPORTED`
no-build launcher report is still rejected as
`OFFLINE_ROOTLESS_BUILD_RUNNER_UNAVAILABLE` before source/work/output
creation, package/build commands, or online-runner selection. Earlier host or
mount preflights may stop production first; if they pass, the absent pin
returns `OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE`. This does not validate the
launcher or `pacstrap -N`;
offline build remains unavailable/unvalidated. These build-time checks are
distinct from runtime network-policy validation and do not establish a booted
ISO.

| Workflow | Required checks | Current implementation evidence | Status |
|---|---|---|---|
| T430 / 1vyrain | Exact model and BIOS compatibility; IVprep handoff when required; upstream 1vyrain; post-install validation | Phase 02 identity policy and Phase 05 handoff service use pinned `start.sh` commit `a3427b34102f8f9887acf65e23d7fd5900fe1d40` (raw README/start hashes recorded in `PROGRESS.md`). The strict third-token rule accepts `G1ET74WW (2.64 )` as `264`, blocks `2.65`, and returns UNKNOWN for bare/malformed/ambiguous versions. Durable approval checkpoints bind device/capture, preflight, actor/time, and 1vyrain user-reported image/media evidence; fresh same-device redetection is required after reboot and accepted returns consume the checkpoint. IVprep/1vyrain remain official-link human handoffs; no binary/image is bundled or authenticated. | SOFTWARE COMPLETE — SOL ACCEPT; HARDWARE PENDING |
| X200 / Libreboot | Validated upstream artifact; full original backup; external SPI write; read-back; boot checks | SOL accepted Phase 06 staging and Phase 10B shared-controller write/read-back software. The workflow requires exact regular-X200 identity, observed 4/8 MiB topology, two-copy backup and recovery evidence, pinned COMPLETE ArtifactEngine manifest, and exact staged digest. X200 CBFS acceptance requires `fallback/payload` type `simple elf`. The pinned Libreboot 26.01 WSL upstream diagnostic compiled 31 variants; the separate read-only validator passed size and pinned CBFS structure for all 31. This did not publish an ArtifactEngine COMPLETE artifact or establish a device-bound flash-ready image. No physical probe, backup, write, read-back, boot, or recovery occurred. | SOFTWARE COMPLETE — SOL ACCEPT; UPSTREAM DIAGNOSTIC PASS; ARTIFACT/HARDWARE PENDING |
| X220 / Coreboot | Real target config and ROM build; validation; full backup; external SPI write; read-back; recovery and boot checks | Phase 07 accepted staging requires exact X220 identity, one observed 8 MiB chip and matching external 3300 mV SPI evidence, complete descriptor/GBE/ME/3 MiB BIOS map, two-copy persistent backup/recovery readiness, backup-derived blob slices, and a pinned-artifact manifest with CBFS/IFD and non-BIOS-preservation checks. Phase 08 routes prepare, backup, build/artifact acceptance and safe resume through CLI/TUI. Phase 10B adds the same guarded shared-controller CLI and mounted-screen TUI external-write/read-back routes as X200; screen approval is bound to a live challenge and its mounted screen. Windows directory-fsync disables write invocation here. No actual Linux Coreboot build or physical probe/voltage measurement/backup/write/read-back/recovery/boot test occurred. |
| Phase 02 compatibility/state core | Exact model profiles; tri-state validation; durable operation journal; safe resume; one-use authorization; zero writer calls when blocked | SOL accepted the software core. 21 focused tests cover identity conflicts/aliases/manual candidates, regular-X200 exclusion for Tablet/X200s/X200si, cross-field MTMs, BIOS vendor/version/family boundaries, X200 capacity and artifact hash, X220 topology/regions, chip/programmer voltage, incomplete/transient backups, checkpoint persistence/corruption/plan mismatch/safe resume, illegal transitions, changed/reused approval and simulation/write blocking. Final checks: 101 full-suite tests passed, mypy clean on 82 source files, editable install and CLI help passed; commit evidence is in `PROGRESS.md`. | SOFTWARE COMPLETE — SOL ACCEPT; standalone and software-only |
| Phase 03 SPI/backup/read-back/recovery services | Strict read-only transport/probe; durable topology-bound backups; two-read verification; region-aware comparison; recovery readiness | SOL accepted commits `4ba1858eb38b1c35b748153aea0a6293db928ecb` and `448766a544452538ea9425a598384fd304c6a2df`. Phase 08 integrates probe, backup and recovery-evidence paths for explicit X200/X220 actions; Phase 10B later exposes the read-back comparator only inside the guarded write service. Fake-transport tests only. SOL independently reran 124 tests in 79.59s, mypy across 86 source files, CLI help, and confirmed a clean tree. No physical SPI probe/read, measured voltage, backup, firmware compilation/write, post-write read-back, recovery or boot test occurred. | SOFTWARE COMPLETE — SOL ACCEPT; HARDWARE PENDING |
| Phase 04 pinned artifact engine | Exact upstream pins/targets, clean-source checks, resolved config/output/size/layout validation, CBFS payload inventories, X220 backup-bound blob/preservation validation, immutable manifests and no fake builds | SOL accepted software commits `dec5cf903c8c4d09b464f14a38d84207f24a523a` and `7c5b14e270c34b4a9856dfbcafdec029be011c8e`. Pinned Coreboot 26.09/X220 and Libreboot 26.01/X200 4/8MiB recipes; T430 official-source handoff descriptor only. The pinned lbmk X200 target's 31 expected ROM variants are validated and recorded, with only the exact SeaBIOS/corebootfb variant selected. X200's `cbfstool print -k` tool/source identity is tied to lbmk's nested Coreboot commit `fcd716d9a272461da1fdbaf1c048eb5a52c7896b` and tree `e16fe240567cb3fa08619c6a23e51c4af142f6ac`; both parser sources are pinned by hash, the six-column schema is validated, and the executable digest/inventory are recorded. X220 uses the separate pinned Coreboot 26.09 parser source and exact `print -r COREBOOT -k` invocation; its nonverbose output is the six-column table and unexpected verbose rows are rejected. X200 requires one `fallback/payload` of type `simple elf`; X220 retains the separate `payload` type rule. X220 pre-build backup/topology checks use artifact-independent build intent; after generation, validation binds the actual artifact digest to the finalized operation plan. Phase 08 routes explicit build requests through CLI/TUI. Native Windows returns `LINUX_BUILD_ENV_REQUIRED`; Ubuntu 26.04 WSL is available and the pinned Libreboot checkout/dependencies are present except `g++-mipsel-linux-gnu` (not needed for x86 X200). SOL accepted the operation-scoped Git identity final-publication correction and the X200 U-Boot x86_64 overlay/cache provenance follow-up. Focused follow-up passed 83 tests in 45.74s; Windows helper tests passed 1 and skipped 1 because Windows denied symlink creation (Linux coverage exercises that case); mypy passed on 100 files; package-resource and diff checks passed. The full Windows suite passed 395 tests with 9 skipped in 877.46 seconds (exit 0). The pinned Libreboot 26.01 X200 upstream command subsequently completed in WSL and produced 31 expected 4 MiB images; a validation-only run passed size and pinned CBFS structural validation for all 31, including null-placeholder overlap checks. This does not create an ArtifactEngine COMPLETE manifest or a device-bound flash-ready artifact; X220 ROM compilation and hardware-derived checks remain pending. | SOFTWARE SCOPE AND X200 U-BOOT FOLLOW-UP SOL ACCEPTED; X200 UPSTREAM DIAGNOSTIC PASSED; DEVICE-BOUND ARTIFACT/NATIVE LINUX AND HARDWARE PENDING |
| Phase 05 T430 handoff workflow | Pinned version predicate; human-only IVprep/1vyrain approval; capture-bound safety and media evidence; restart-safe return; no automated download/write | Implementation `22269468d05091e8989c4ae6a4ed2427a1f83bed`, correction `24ea28932ddbccb152b2b9aaed924f9f4b131481`, and legacy-checkpoint regression `4f359c2060e3ca809b2435830781aa95d91cdcec`: the 1vyrain gate requires Secure Boot disabled, selected direct onboard USB without dock/ultrabase/hub, DD-mode attestation, exact pinned Rev 5 link and operator-reported MD5 matching the README's `dd30313a8665e870360920b00cec55c4`. This legacy MD5 does not authenticate the image; local SHA-256 is only a fingerprint. IVprep descriptor exposes the required Win64/BIOS settings/power/admin/reboot warnings. Phase 08 routes human handoff stages and fresh return attestations through CLI/TUI. No hardware operation or external binary/media run occurred. | SOFTWARE COMPLETE — SOL ACCEPT; HARDWARE PENDING |
| Phase 06 X200 Libreboot workflow | Exact model/MTM and chip capacity; pre-build intent; full backup/recovery evidence; pinned COMPLETE artifact; plan binding; write quarantine | `autoloader/workflow/x200_libreboot.py` validates identity/topology/programmer evidence, uses the dual-read backup service, requires current recovery readiness, reopens the exact lbmk manifest/selected bytes and persists the final plan. Artifact acceptance requires exactly one non-empty `fallback/payload` entry with type `simple elf`. Phase 08 routes prepare, backup capture/reopen, pinned build, artifact acceptance and safe resume through CLI/TUI. Positive tests use synthetic fixtures. SOL accepted the isolated, manifest-bound U-Boot x86_64 compatibility overlay/cache provenance follow-up; focused artifact/X200/X220 tests passed 83 tests in 45.74s, Windows helper tests passed 1 and skipped 1 because Windows denied symlink creation, Linux covers that symlink case, mypy passed on 100 files, and package-resource/diff checks passed. A pinned Libreboot 26.01 X200 upstream WSL diagnostic has built and structurally validated 31 variants, but did not publish an ArtifactEngine COMPLETE artifact or bind an image to a device. Native Windows returns `LINUX_BUILD_ENV_REQUIRED`; Ubuntu WSL is available. No physical I/O/write/read-back/boot/recovery occurred. | SOFTWARE COMPLETE — SOL ACCEPT; LINUX BUILD AND HARDWARE PENDING |
| Shared CLI/TUI | Same controller; operation outcomes drive progress and next actions | Phase 08 accepted T430 human handoff and X200/X220 preparation, backup, pinned build, artifact acceptance and exact-plan resume. Phase 10B routes guarded X200/X220 external write/read-back requests from both frontends through the shared controller. The TUI has a dedicated challenge screen bound to its active screen/mount nonce; it previews the fixed command, model/chip, artifact and backup digests, and live setup values. Its one-use receipt can be cancelled or revoked before service consumption. The current Windows host blocks write approval before probe/writer because durable parent-directory fsync is unavailable. No physical write, probe, backup, build, read-back or boot action was invoked during this implementation pass. The X200 upstream diagnostic later built and structurally validated 31 images. ArtifactEngine COMPLETE/device-bound output, native Linux build/tool validation and the three hardware workflows remain pending. | PHASE 08 AND PHASE 10B SOFTWARE ACCEPTED; LINUX/HARDWARE PENDING |
| Phase 10B guarded external write/read-back | Exact X200/X220 staged evidence, fresh pre-write capture/probe, persistent backup, single-use human approval, durable journal, fixed writer, full read-back comparison | SOL accepted the Phase 10B service and CLI/TUI integration software. CLI and dedicated mounted-screen TUI routes are integrated through `ApplicationController`; the CLI requires a real OS TTY, and the TUI requires the current screen instance plus exact phrase and explicit confirm action. Both collect live meter/wiring attestations and revalidate staged evidence before consuming approval. T430/internal SPI and generic/raw restore remain blocked. The writer uses one fixed no-shell command after durable `WRITE_STARTED`; interrupted or nonzero operations are marked uncertain and are not retried; verified status requires final readback and artifact digests/sizes to match. TUI cancel/unmount revokes a minted receipt until service consumption. Final independent software gates: 354 passed, 2 skipped in 716.26s; mypy clean on 102 source files; top-level and flash CLI help and `git diff --check` passed. The evidence uses synthetic/fake fixtures only. Windows cannot satisfy the required directory-fsync contract, so write invocation is unavailable on this host. No physical write, read-back, restore, or boot test occurred; Native Linux build/namespace/QEMU gates and all three hardware workflows remain pending; the WSL X200 diagnostic does not create device-bound build evidence. | SOFTWARE ACCEPTED — SOL; LINUX/HARDWARE PENDING |
| Post-reboot evidence intake | Bind a fresh local identity capture and typed human observations to an exact terminal X200/X220 write or T430 return checkpoint; preserve pending-only status | The shared CLI/TUI `BOOT_CHECK` route reopens terminal evidence, verifies profile/device/operation binding and recomputes X200/X220 artifact/read-back bytes, sizes and hashes (including X200 MAC-preservation provenance). T430 uses a consumed return checkpoint and binds the approved option-1/reboot/Advanced BIOS timeline. It captures local DMI/BIOS itself and writes a single operation-owned sidecar; reopening revalidates exact schema, chronology, fingerprint and X200 MAC even if the sidecar checksum is recomputed. The guided TUI shows model-specific checks and requires a separate UTC observation time and evidence reference for each check; X200 MAC has its own time/reference fields. The JSON CLI and shared controller schema are unchanged. All status remains `PENDING_HUMAN_REVIEW` / `NOT_ASSESSED`; no journal is resumed or hardware success inferred. Earlier service-only fake selection: 24 tests in 319.27s. Guided UI/service selection: 43 tests in 359.37s; per-check timestamp UI follow-up: 4 tests in 25.55s. Mypy passed on 108 source files. No hardware or boot action occurred. | SOFTWARE TESTED — HARDWARE ACCEPTANCE PENDING |
| Read-only device readiness review | Review persisted V1 checkpoint and byte evidence without advancing a workflow | Shared controller/CLI/Textual route reopens terminal X200/X220 or consumed T430 evidence, rehashes persisted references, and marks current-device/boot and unrecorded recovery-volume inputs `UNKNOWN`. Maximum outcome is `REVIEW_INPUTS_COMPLETE`; the report cannot mark hardware ready or authorize writes. It performs no probe, backup, resume, mutation, approval or writer call. Focused tests passed 11 with 1 skipped on Windows and 9 on Ubuntu WSL. This is software-only evidence. | SOFTWARE TESTED — INPUT REVIEW ONLY |
| Live USB | Buildable image, installed dependencies, boot and safe startup | Candidate b2 (afde8ba28025530852547ec5042dd94ecece5a9b1d24f5da141365861cc35cbd) passed target-root/CLI checks, but same-hash BIOS/UEFI runs stalled before AutoLoader/service evidence. Retry8's disposable Arch guest query returned 1 after 140.495 seconds with six locked versions but no build-host `archiso` or core/extra sync databases; preflight stopped before identity/rootless checks, scratch, sync/install, build, or output. SOL confirmed `archiso` is build-host-only. Two newer generic QEMU 10.2.1 debug-shell diagnostics were incomplete: Arch-boxes waited for a missing root UUID, and the Arch ISO remained at SeaBIOS EDD probing; neither yielded a shell or guest command. These are not native Linux or product-ISO evidence. Native Windows QEMU 11.1.2 full-ISO diagnostics remain incomplete: the WHPX default boot panicked with No working init found, the init=/bin/sh retry showed devtmpfs creation failure, and the 90-second TCG run stopped at EDD/TDX without a shell. Two legacy-PC WHPX runs reached the Arch boot splash but no shell within 50 seconds over UNC or 90 seconds from a hash-matched local copy. See [native QEMU Windows evidence](evidence/native-qemu-windows-20261021/README.md). See [`VM debug-shell evidence`](evidence/vm-debug-shell-20261021/README.md), [`Arch ISO debug-shell evidence`](evidence/archiso-debug-shell-20261021/README.md), and earlier retry8 captures in `docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry8/`. No current-source product ISO has been built or booted; app/service boot, offline-isolation validation, and hardware actions remain open. | CURRENT-SOURCE IMAGE BUILD AND PRODUCT BOOT OPEN; HARDWARE PENDING |
| Offline rootless launcher preflight contract | Fixed root-owned no-egress launcher, full namespace/route/descriptor/mount evidence, pinned `pacstrap -N` compatibility | SOL accepted the versioned no-build contract. Focused Arch Linux WSL2 re-review: **53 passed in 3.15s**; independent mypy passed on the two touched sources. The contract binds all host namespace IDs, requires distinct child IDs and matches before/after network views to the child network namespace. The existing trusted-host and mount preflights, including their child processes, run first. Production launcher pin remains absent, so offline returns `OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE` before launcher-specific scratch/child creation and builder work/output. The current-source Arch WSL2 non-root full suite passed **809, 2 skipped, 0 failed/error** (811 collected; 1771.18s); exact skips are Windows-specific host durability and platform contracts. JUnit and exact command are recorded in `docs/v1/evidence/software-regression-20261016/README.md`. This WSL test result does not establish deployment or `pacstrap -N` package-input compatibility. | CONTRACT IMPLEMENTED — DEPLOYMENT/COMPATIBILITY UNVALIDATED |
| Legacy EC modification API | No unpinned download/build, flash, or success-only verification path | `ThinkPadECManager` now raises explicit `ECModError` unavailable outcomes for `download_source`, `build_ec_firmware`, `flash_ec`, and `verify_ec`; patch reporting is empty. `tests/test_ec_mod_fail_closed.py` proves rejection before subprocess/file writes and protects an existing arbitrary checkout. This does not add an EC workflow. | SOFTWARE TESTED — UNAVAILABLE IN V1 |
| Earlier cross-platform software regression (`6cabb23d`) | Full test suite and static typing after guided post-reboot UI work | Ubuntu 26.04 WSL: **755 passed, 4 skipped** (759 collected; 1108.07s); Windows: **699 passed, 60 skipped** (759 collected; 770.94s); both report zero failures/errors. Ubuntu skips are two `UNVALIDATED: host lacks trusted Arch pacman executable ownership evidence` cases (not passing Linux checks), one Windows-specific host platform contract, and one Windows-specific production durability policy. Windows skips include POSIX directory-fsync/ownership/process contracts, Linux namespace/device checks, several POSIX symlink-contract cases, and one separate account-specific symlink case (`WinError 1314`); skipped checks are not passes. Full-project mypy passed on 108 files in Ubuntu and with `--platform win32` on Windows. Exact XML reports and hashes are in `docs/v1/evidence/software-regression-20261016/README.md`. Windows 693/45 is a prior pre-commit result, not evidence for `6cabb23d`. Ubuntu is WSL, not native Linux; no ISO, ROM or hardware success is established. | SOFTWARE TESTED — RELEASE GATES OPEN |
| Earlier root-account Arch WSL software regression (`72a302b`) | Full test suite and static typing on the earlier source revision | Arch Linux WSL2, root account: **756 passed, 3 skipped** (759 collected; 1161.62s; exit 0), with zero failures/errors. The skips were Windows-specific production durability, the unvalidated non-root host-ownership contract, and Windows-specific host platform contract. The ownership case was unvalidated under root. Full-project no-incremental mypy passed on 108 source files. JUnit report and SHA-256 are in `docs/v1/evidence/software-regression-20261016/README.md`. | SOFTWARE TESTED — RELEASE GATES OPEN |
| Arch WSL2 full suite as `nobody` (source/test tree `72a302b`) | Full test suite under a non-root identity; software evidence only | Launched from WSL root and dropped by `setpriv` to UID/GID 65534 with no supplementary groups/capabilities and `NoNewPrivs=1`: **757 passed, 2 skipped** (759 collected; 1245.94s; exit 0). Skips: `test_windows_directory_fsync_policy_keeps_approval_closed` (Windows-specific production durability policy) and `test_real_build_usb_cli_fails_before_subprocess_on_windows` (Windows-specific host platform contract). The former host-ownership skip passed. Docs-only commit `0d74d76` landed during the run; tested Python source/tests remained at `72a302b`. Exact command, environment, JUnit report and SHA-256 are in `docs/v1/evidence/software-regression-20261016/README.md`. This Arch WSL2 run is not native Linux or disposable-VM validation and does not test the dedicated build account, privileged launcher, offline production path, package/ISO build, boot, ROM, or hardware. | SOFTWARE TESTED — RELEASE GATES OPEN |
| Current-source RC1 software regression (`7b6000a`) | Full Windows and Linux-stack regression suites plus full-project static typing | Windows: **751 passed, 61 skipped** (812 collected); Win32-targeted mypy has no issues in 109 files. Arch WSL2 under `nobody`: **810 passed, 2 skipped** (812 collected); Arch mypy has no issues in 109 files. Both suites had zero failures/errors. The Windows skips include POSIX, Linux namespace/device, process semantics and one account privilege case; the Arch skips are Windows-only contracts. Exact commands, skip names, XML paths and SHA-256 values are in `docs/v1/evidence/software-regression-20261016/README.md`. | SOFTWARE TESTED — LIVE USB, ROM BUILD AND HARDWARE GATES OPEN |
| Current-source package smoke (`7b6000a`) | Build and isolated installation of exact candidate wheel | Ubuntu WSL2 built and installed the wheel in `/dev/shm` with package-index access disabled. Wheel: 393,143 bytes, SHA-256 `8f0cbc3827e779ab8d236dae62923b238ec05f3ab41f30ee2a7f4faa8244a999`. Installed `autoloader`/`usb_builder` imports, current POSIX path fix and both pinned resources resolved from the target. Full output and hash are in `docs/v1/evidence/software-regression-20261016/wheel-build-and-install-rc1.txt`. This is packaging evidence only. | PACKAGE SMOKE PASS — ROM/ISO/HARDWARE GATES OPEN |

Earlier read-only storage checks are historical. On 2026-10-20, the user authorized an isolated workspace on the 254.9-GB ext4 partition; it now holds a verified Arch-boxes image, scratch overlay, and the verified Arch 2026.10.01 ISO. QEMU 10.2.1 TCG booted the installed guest into systemd startup but did not reach a verified login. A later single-vCPU BIOS ISO run mounted the Arch live root and started systemd, but a bounded loopback-serial `id` command returned no result. No guest shell, native Linux build, current-source product ISO, Live USB app/service boot, ROM artifact, or hardware acceptance gate passed. Full hashes, boot observations, and limits are in `docs/v1/evidence/vm-bringup-20261020/README.md`.

A later VM host check confirmed the i3-3110M supports VT-x/EPT but Windows 10 WSL 3.0.3 still does not expose usable nested VMX/KVM. The Windows Hypervisor Platform API is available. Two diskless QEMU WHPX direct-kernel diagnostics reached the kernel but panicked before a shell with devtmpfs/console errors and `Failed to execute /init (error -1)`; a default-PIC retry had the same result. Read-only initramfs inspection confirmed the expected `/init`, BusyBox shell link, and loader entries but did not identify the failure cause. This was not a normal BIOS/UEFI guest boot. WSL detached the authorized ext4 workspace on shutdown; the user subsequently ran the elevated mount command, and Ubuntu WSL now mounts it at `/mnt/wsl/PHYSICALDRIVE1p2`. No Windows feature or firmware setting was changed. See the host acceleration recheck and retained captures in the VM evidence folder. The guest, native build, product ISO, and hardware gates remain open.

A bounded Windows QEMU TCG direct-kernel comparison progressed to Arch's early `udev`/`archiso_pxe_nbd` hooks and `Triggering uevents...`, but did not reach a shell or verified guest command. It was stopped by the harness; this is not a boot pass.

No row is hardware accepted. Automated tests and simulations cannot satisfy the real-device acceptance criteria.

The latest Arch WSL production preflight tested private mount/PID namespaces
alongside the user-mapped network namespace. Its route-free child report passed
and the host mount table was unchanged. The earlier capability-denial result
is historical and superseded by the errno-classification evidence. A
disposable same-namespace comparison showed that the former tmpfs-run EINVAL
came from passing nosuid/nodev as filesystem data; the corrected flag/data
split and exact util-linux mount command succeed. The current direct
production preflight reports devtmpfs unavailable in WSL. The route-free
probe passed, host mountinfo was unchanged, and no build work/output or builder
command was started. See
`docs/v1/evidence/offline-namespace-arch-wsl-20261015/tmpfs-option-split-diagnostic-20261015.txt`.
Native Linux and offline-build gates remain open.

An Arch WSL QEMU acceleration preflight is recorded at
`docs/v1/evidence/live-usb-b2-diagnostic-20261014/qemu-acceleration-preflight-20261015.txt`.
QEMU 11.1.2 listed `tcg`, `mshv`, `nitro`, and `kvm`; `/dev/kvm` and
`/dev/mshv` were absent, and a bounded MSHV probe exited 1 with “Failed to open
/dev/mshv” / “Operation not permitted.” This establishes no accelerated guest
path in Arch WSL and no native Linux/KVM result. Existing WSL TCG evidence is
preserved and remains incomplete for app/service startup.

The follow-up online-candidate readiness check invoked the production mount
preflight from the shared checkout and failed closed at `devtmpfs` with
`ARCHISO_MOUNT_CAPABILITY_UNAVAILABLE`. Because `execute_build` requires this
preflight for online and offline modes before creating build/output state, no
online build, package sync/install, `mkarchiso`, or candidate ISO was run. The
exact WSL result is recorded in
`docs/v1/evidence/offline-namespace-arch-wsl-20261015/online-candidate-readiness-preflight-20261015.txt`.
This is not native Linux evidence; a native build and successful app/service
boot remain pending.

A separate Ubuntu 26.04 WSL preflight stopped earlier at the trusted-tool
check: `/usr/bin/unshare` resolved, but `pacman` was absent, so the required
`pacman -Qo` ownership command could not start and the production result was
`OFFLINE_NETWORK_ISOLATION_UNAVAILABLE`. It did not reach namespace or mount
checks and did not test devtmpfs. No candidate-build work/output, package
sync/install, or `mkarchiso` was started. This confirms Ubuntu WSL is not the
pinned Archiso build host; see
`docs/v1/evidence/offline-namespace-ubuntu-wsl-20261015/`. The separate Arch
WSL devtmpfs result is from an earlier root-oriented preflight; it does not
test the pinned non-root `-N` mount stack. The new exact `arch-chroot -N`
preflight is implemented and synthetically tested but has not yet run from
the current source in the disposable Arch VM. Native Linux and candidate-build
validation remain open.

## Provisional source-backed machine constraints

| Machine | Upstream basis | Anticipated method/programmer | Current safe policy | Unknowns that remain blocking |
|---|---|---|---|---|
| T430 | [Pinned 1vyrain compatibility logic](https://github.com/n4ru/1vyrain/blob/a3427b34102f8f9887acf65e23d7fd5900fe1d40/start.sh); [IVprep upstream](https://github.com/n4ru/IVprep) | Internal software flash through upstream 1vyrain after any human-run Win64 IVprep handoff; external SPI recovery readiness required. | Software policy uses the pinned `<265` numeric threshold, exact DMI/MTM agreement and a two-chip profile. It never executes IVprep or upstream binaries. | Board/MTM, stock BIOS state, physical chip layout and post-install evidence remain to be observed. |
| X200 | [Libreboot X200 guide](https://libreboot.org/docs/install/x200.html) | External SPI programmer only after interface, wiring, programmer output and observed chip voltage all agree at 3300 mV. | Regular X200 only; exact 4 or 8 MiB chip and same-size artifact are required. | X200s/X200si/Tablet variants are excluded and require their own documented support; physical model, chip/capacity/voltage, artifact and complete recovery reads remain unknown. |
| X220 | [Coreboot Sandy Bridge guide](https://doc.coreboot.org/mainboard/lenovo/Sandy_Bridge_series.html) | External SPI programmer for initial flash; exact interface/wiring and chip voltage must match at 3300 mV. | One observed 8 MiB chip; artifact and observed IFD/GBE/ME/3 MiB BIOS maps must match. | Board variant, descriptor/protection state, region map, programmer voltage and image semantics remain unobserved. |

Any conflict between detected hardware and these provisional descriptions blocks work. Sources establish research constraints, not completed device validation.

## Phase 04 cross-platform software gate

At the earlier checkpoint after the accepted Git identity correction, the full Windows suite passed **395 tests, 9 skipped in 877.46 seconds** and the full Ubuntu suite passed **402 tests, 2 skipped in 427.12 seconds**, both exit 0. Native Ubuntu mypy then reported a Windows-only `ctypes.windll` attribute issue; the latest guarded lookup below resolves it. Fake/synthetic tests do not demonstrate upstream firmware compilation or hardware success; those gates remain pending.

## Source-pin audit correction (SOL accepted)

The full exact-byte audit updated CRLF-derived values in the X200 Libreboot pin map and unified the artifact-engine 1vyrain handoff pins with the T430 workflow's raw-source pins. It also verifies the X220 correction; nested lbmk-Coreboot and IVprep hashes already matched. Tests now assert the full pin maps and recipe-config binding; focused suites pass 76 tests with 1 skipped, and mypy is clean on 102 source files. Full provenance and hashes are in [the Phase 04 artifact-engine audit](PHASE04_ARTIFACT_ENGINE.md). SOL accepted this source-metadata correction; Linux firmware builds and all hardware tests remain pending.

## Earlier Live USB root-mount regression

The fifth clean online attempt built a temporary ISO with SHA-256 `9d6fd80e5757b2f5a075fd46f5d60548c782166cacf95378d6bde2ad3dee42fc` (1,285,586,944 bytes) from snapshot `349e7961264131f69019ad1ab61cfa950af688a8`. Its target-root install validation passed, but same-hash BIOS and UEFI QEMU boots both failed before mounting the live root. BIOS explicitly reported missing `archiso`, `archiso_loop_mnt` and PXE hooks; both modes dropped to `[rootfs~]#`. Neither reached app/service startup, so there is no guest service/journal or no-writer evidence. The accepted initramfs-fragment guard was exercised by this build. Diagnosis found the corresponding runtime packages were absent from the profile. SOL accepted the per-hook package map, required-package policy and provenance correction; canonical LF package-list SHA-256 is `7a7a3fa4127ab6fbaaf952be4bbde82f787a7da30d3111601a9e7fccd6334dbd`. The exact pinned core/extra database hashes were verified to contain the listed packages. The latest focused Arch USB suite passed 105 tests with 2 skipped and `mypy usb_builder` is clean. A fresh post-correction build and successful same-hash BIOS/UEFI app/service boots remain pending.

SOL accepted the package-inventory correction; its acceptance checkpoint passed 79 USB tests with 2 skipped. The builder validates contained non-symlink local pacman DB paths and runs only `pacman --config /dev/null --dbpath <airootfs>/var/lib/pacman -Q`; malformed/warning output or any failure blocks publication. Those are historical software results; the later temporary ISO and failed QEMU runs are documented above.

## Phase 04 X200 GRUB patch-chain identity follow-up — SOL accepted

The GRUB base commit/tree and ten exact ordered patch hashes remain pinned; each intermediate tree ID is checked through final tree `9e76050f8ab01306e51fb28ab88c1f8481c9e26a`. Generated `git am` commit IDs vary with committer metadata and are recorded per operation, then reopened against the exact base ancestry and pinned tree sequence. SOL accepted this software correction. Focused ArtifactEngine/X200/X220 checks passed 90 tests with 2 skipped; mypy passed for the three touched modules; diff-check passed. At this patch-chain checkpoint, Linux compilation and real manifest reopen were pending. The later X200 diagnostic passed structural validation but produced no ArtifactEngine COMPLETE manifest; X220 build and all hardware workflows remain pending.

## Earlier cross-platform full software regression — SOL accepted

SOL accepted the X200 Unifont manifest-portability correction and the synthetic-fixture isolation used by approval/controller policy tests. Build-time font source and package-owner/version checks remain strict; manifest reopen validates pinned source identity and the operation-owned staged bytes without relying on the original host-specific absolute path. Dedicated ArtifactEngine tests continue to exercise strict persisted-manifest validation.

The full Windows suite passed **451 tests, 15 skipped in 981.30 seconds** (exit 0), and the full Ubuntu suite passed **464 tests, 2 skipped in 609.63 seconds** (exit 0). After those runs, the `is_admin()` Windows-only DLL lookup was guarded without changing Windows behavior; the focused flasher suite passed 26 tests in 9.94 seconds. Windows and Ubuntu Windows-targeted mypy both passed on 102 source files, and native Ubuntu mypy passed after the guard. Top-level and flash CLI help passed, an isolated-backend wheel build passed, and `git diff --check` passed. All tests use synthetic/fake firmware and hardware evidence. ArtifactEngine COMPLETE/device-bound output and X220 ROM compilation (the X200 diagnostic later passed structural validation), Live USB offline namespace and BIOS/UEFI app/service validation, and all three physical hardware workflows remain pending; no hardware workflow is accepted.

An oriented direct-kernel QEMU diagnostic then masked only `systemd-nsresourced.service` and `.socket` on the unchanged b2 ISO/kernel/initramfs. Serial confirmed the masks, but `sysinit.target` still waited on `systemd-tmpfiles-setup-dev.service` at 270 seconds. No guest command or app/service readiness was captured; this does not establish root cause or normal boot success. QEMU exited 0 after the bounded HMP quit; the controller reported a monitor reset during shutdown. Full evidence is in `docs/v1/evidence/live-usb-b2-diagnostic-20261014/nsresourced-mask-20261015/`. Native Linux/KVM and normal BIOS/UEFI app/service validation remain open.

## Retry8 host-package query diagnostic — 2026-10-17

The SOL-reviewed retry8 Arch TCG guest reached a debug shell and verified the
copied helper plus all 24 read-only source-manifest entries. Its exact locked
`pacman -Q` query returned six matching versions but lacked host-only
`archiso 91-1`; the `core` and `extra` repository sync databases were also absent. The
fail-closed probe stopped before rootless/mount preflight, scratch probing,
and build execution. No package installation/sync, ISO, or firmware action
occurred. SOL determined that this is an unprepared diagnostic build host,
not evidence of a live-image package defect: `archiso` is a builder-host
dependency, while the runtime profile correctly includes
`mkinitcpio-archiso`. Exact captures and interpretation are under
`docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry8/`.
The no-launch regression in `tests/test_live_iso_host_package_context.py`
checks that distinction and confirms the captured missing package cannot
advance the probe. A pinned, signature-verified disposable build guest and a
successful current-source ISO plus BIOS/UEFI app/service boot remain open.

## Earlier current-source software regression checkpoint — 2026-10-17

Ubuntu 26.04 WSL2, Python 3.14.4, pytest 9.1.1: **839 passed, 6 skipped,
0 failures/errors** (845 collected; 1,391.29 seconds; exit 0). The run
includes the retry8 host/runtime package distinction regression. Native-Linux
and Win32-targeted mypy each passed with no issues in 109 source files. Exact
command and scope are recorded in
`docs/v1/evidence/software-regression-20261017/README.md`. This does not
establish native Linux, build/ISO, boot, ROM, or hardware success; skipped
tests remain open where their platform behavior is required.

Read-only `ArtifactEngine.inspect_source` audited existing clean upstream
checkouts: X200 lbmk matched its pinned revision/tree and 35 file hashes;
X220 Coreboot matched its pinned revision/tree and 6 file hashes. This closes
only source-identity inspection. It creates no compiled ROM or accepted
artifact and does not close the clean builder VM gate.

### Earlier current-source Windows regression checkpoint — 2026-10-17

Windows Python 3.12.14 / pytest 9.1.1 full suite: **752 passed, 62 skipped,
0 failed, 0 errors** (814 collected; 789.46 seconds; exit 0). The JUnit report
and SHA-256 are recorded in
[`2026-10-17 software regression evidence`](evidence/software-regression-20261017/README.md).
Skip categories include POSIX durability/process/ownership/symlink behavior,
Linux namespace/device/pacman checks, POSIX-only host checks not validated on
Windows, and one account-specific symlink privilege skip (`WinError 1314`).
Skipped checks remain unpassed. The accepted `testpaths = ["tests"]` setting
limits default collection to application tests; LUNA's default collection
found 814 tests and no evidence-directory paths, while explicit evidence-probe
tests passed 31 with 2 skipped. Frozen retry7/retry8 evidence files are
unchanged. This is software regression evidence only; native Linux, image,
boot, ROM and hardware gates remain open.

### Earlier selected safety test coverage baseline — 2026-10-17

Fourteen selected software test modules passed 295 tests and skipped 1 in
Ubuntu WSL (961.41s). The preserved report displays 74% total coverage and
62% branch coverage, below the specification's recommended 85% meaningful
branch target. This is a diagnostic baseline, not an acceptance pass; it
establishes no VM, ISO, ROM, or hardware behavior. See the [coverage report
and per-file figures](evidence/safety-coverage-20261017/README.md).

### Earlier targeted safety coverage follow-up — 2026-10-17

The preserved post-additions report measures the frozen critical source set
(`backup.py`, `backup_service.py`, `compatibility.py`, `engine.py`,
`authorization.py`, and `journal.py`) at **494/678 branches (72.9%)**. The
85% meaningful-branch target remains open. Write-approval, write-service, and
post-reboot rates are documented separately. Known platform-specific and
lower-priority misses are described without claiming the rest unreachable.

At that checkpoint, full tests completed with **778 passed / 63 skipped** on
Windows and **837 passed / 4 skipped** on Ubuntu 26.04 WSL, with no failures or
errors. Skipped checks remain unpassed; two Ubuntu skips concern missing
trusted Arch pacman ownership evidence. The final two assertion-only test
strengthenings were rerun on both hosts (2 passed on each). These runs do not
establish native Linux, ISO, VM boot, ROM build, or hardware validation. See
the [coverage report](evidence/safety-coverage-20261017/README.md) and
[software-regression evidence](evidence/software-regression-20261017/README.md).

An earlier full-run checkpoint after the journal-test assertion cleanup:
Windows passed **816 / skipped 63** and Ubuntu 26.04 WSL passed **875 / skipped
4** (879 collected on each, no failures/errors). At that checkpoint, the frozen
denominator was **529/678 branches (78.0%)** and the 85% target remained open.
Ubuntu's skips include two
unvalidated Arch pacman-ownership checks; all Windows and Ubuntu skips remain
unpassed. This remains software-only evidence, not native Linux, VM, ISO, ROM,
or hardware validation. See the current
[coverage](evidence/safety-coverage-20261017/README.md) for the latest result and
[regression](evidence/software-regression-20261017/README.md) reports.

### Latest fixed-denominator coverage and crash/replay tests — 2026-10-17

The latest accumulated six-file critical coverage report measures **577/678
branches (85.1%)**, reaching the recommended meaningful-branch target. Two
additional fake-only write-service tests cover terminal journal sync failure
after a single writer call and process loss after durable `WRITE_STARTED`;
the reopened service rejects replay. The focused selection passed **4 tests
on Windows** and **4 on Ubuntu 26.04 WSL**. The final full suites then passed
**876 tests with 63 skipped on Windows** and **935 with 4 skipped on Ubuntu
WSL** (939 collected on each, zero failures/errors). The corrected Windows
JUnit SHA-256 is `6522F8CCEF1D416574DA88D1430AB35E776C39FDFA6721B3B36CE1008DE70798`;
the Ubuntu JUnit SHA-256 is
`CD6D7CBABF79BC9417C75C5339351C55C203A6BF0F2BE31A4D220E3AF94EAAE4`. The
initial Windows attempt with a basetemp outside the system temp root is
preserved as a failed environment setup and is not counted as a pass. Ubuntu
skips include two unvalidated Arch pacman-ownership checks, which remain
unvalidated. All skips remain unpassed. SOL's read-only audit found no new
software blocker and accepted the crash/replay coverage. Writer software
review is complete; real POSIX directory fsync, flashrom against a physical
device, physical read-back, boot, and recovery remain unproven/open. See the latest coverage export and
full regression evidence in the
[safety coverage evidence](evidence/safety-coverage-20261017/README.md) and
[software regression evidence](evidence/software-regression-20261017/README.md).
