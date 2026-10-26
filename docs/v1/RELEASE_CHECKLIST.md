# V1 release checklist

## Current-source Linux and Windows software regressions — 2026-10-26

- Ubuntu WSL 2 ran the exact current-source 1,392-case POSIX selection:
  **1,388 passed, 4 skipped, 0 failed, 0 errors** (798.83 seconds; exit 0).
  The source manifest matched the POSIX snapshot and post-run source across
  204 paths. The two Windows-only checks were skipped by platform selection;
  two Arch pacman ownership probes remain **UNVALIDATED**. See the
  [current Linux evidence](evidence/current-linux-regression-20261026/attempt-01/README.md).
- The same current-source Windows selection passed across 61 stable shards:
  **1,118 passed, 274 skipped, 0 failed, 0 errors**. Its platform-specific
  skips do not replace the Linux result. See the
  [Windows evidence](evidence/windows-current-regression-20261026/chunked-attempt-02-20261026/README.md).
- These are software-only results. The independent Arch trust gate,
  authenticated package closure, production worker deployment, product Live
  USB build and boot, and all three real-device workflows remain open.

## Current-source wheel and Windows marker follow-up — 2026-10-26

- The strengthened offline wheel smoke passed for source snapshot
  `e071f9dbd6d8510eefa6c865b36475c08142b3c01f9efc8ca1ec8508a6b727b4` (149
  files). The updated smoke assertions were rerun on unchanged current source.
  It built a 551,061-byte wheel (SHA-256
  `8dd04f50015e45b4bb58e8d38765d4a339d91f4af2002677e7d31dee726fcb86`), imported
  all seven installed worker modules, byte-verified five packaged lock/systemd
  resources, checked all `Requires-Dist` and `Provides-Extra` entries plus
  Python-version metadata against `pyproject.toml`, and passed both CLI help
  commands. This does not prove
  dependency closure or a clean-environment install. See the
  [wheel evidence](evidence/current-source-wheel-install-20261026/README.md).
- The focused shared CLI/Textual controller regression is retained in
  [its evidence bundle](evidence/shared-controller-regression-20261026/README.md).
  The initial run had six passes, one skip, and two synthetic-fixture path
  failures. A complete short-path rerun passed eight tests and skipped one
  POSIX-directory-fsync case. This does not validate SPI hardware or firmware.
- The current-source no-incremental Win32-targeted mypy gate passed with no
  issues in 119 production Python files. See the
  [mypy evidence](evidence/windows-mypy-win32-rerun-20261026/README.md).
- The current-source no-incremental Linux-targeted mypy check passed with no
  issues in the same 119 production Python files. It used an isolated WSL
  tmpfs cache; see the [Linux-targeted evidence](evidence/linux-mypy-rerun-20261026/README.md).
  Static analysis does not satisfy native guest, Live USB, or hardware gates.
- The complete current-source Windows test selection passed in 61 stable
  function shards: **1,118 passed, 274 skipped, 0 failed, 0 errors** across
  1,392 cases. Every shard's JUnit count matched its expected selection, and
  the 204-path source manifest matched before and after. The skips are
  predominantly Linux/POSIX contracts. The full-run environment skipped the
  wheel smoke because its project virtual environment lacked installed
  `setuptools` and `wheel`; a focused native Windows rerun with existing
  bundled tools passed 1 test. Linux host ownership probes remain unvalidated
  on Windows. See [attempt-15 evidence](evidence/windows-current-regression-20261026/chunked-attempt-02-20261026/README.md)
  and the [wheel-smoke follow-up](evidence/windows-current-regression-20261026/wheel-smoke-bundled-runtime-20261026/README.md).
- Arch package acquisition remains blocked at Stage A. A read-only review
  confirmed gaps in independent Ubuntu verifier provenance, dated Arch signer
  trust, exact pacman `-2` signature verification, and complete authenticated
  package closure. This audit acquired no new Arch material; existing evidence
  contains a 119-byte detached-signature packet but no package archive or
  imported key. See the
  [trust-gate audit](evidence/arch-trust-gate-audit-20261026/README.md).
- A focused Windows selection of the 17 platform-marked definitions completed
  **35 skipped, 0 failed, 0 errors** (pytest exit `0`). This verifies the
  Windows selection markers only. It does not validate Linux guest behavior or
  the rest of the Windows worker suite. See the retained
  [marker rerun](evidence/windows-offline-worker-regression-20261026/marker-rerun-20261026/README.md).
- The current-source Windows host rejection guard passed (**1 passed**); it
  confirmed rejection before any build tool or output creation. See the
  [guard evidence](evidence/windows-offline-worker-regression-20261026/host-guard-rerun-20261026/README.md).
- The earlier Windows worker run remains preserved as failed (**135 passed,
  150 skipped, 35 failed**); all 35 failures were Linux-only guest-publication
  or private-directory cases, now isolated by the markers. Native Windows
  attempt 14 separately passed its two-test selection; see its
  [record](evidence/windows-current-regression-20261026/attempt-14-targeted/README.md).
- The earlier unsharded native Windows attempts remain incomplete and are not
  counted as results. The current sharded Windows and Ubuntu WSL selections are complete; the two
  Arch ownership probes remain UNVALIDATED on both platforms. Authenticated
  package trust, production worker deployment, native-Linux build, current Live
  USB boot, and the hardware gates remain open.

## Previous full software regression — attempt 13 — 2026-10-26

**Passed in Ubuntu WSL 2:** 1,388 passed, 4 skipped, 0 failed, 0 errors
(1,392 collected; pytest exit `0`). The focused regression for the seven
attempt-12 failures and umask-independent fixture modes passed (**8 passed**).
The exact POSIX snapshot matched the captured source manifest before tests;
the retained post-run manifest and comparison receipt confirm no source-tree
change. Two Windows-specific checks remain separately covered only by older
targeted Windows evidence. Two Arch pacman executable ownership checks remain
`UNVALIDATED`. See the [attempt-13 receipt](evidence/software-regression-20261025/exact-tree-recapture-20261025/attempt-13/README.md).

V1 remains **incomplete**: these are software regressions only. The T430
1vyrain, X200 Libreboot, and X220 Coreboot workflows have not yet passed their
required real-device build/flash/read-back/boot acceptance. Hardware writes
and recovery remain deferred; no hardware operation was performed in this
test run. Worker deployment, authenticated package closure, and Live USB
build/boot gates also remain open.

## Previous full software regression — attempt 10 — 2026-10-26

**Passed in Ubuntu WSL 2:** 1,385 passed, 4 skipped, 0 failed, 0 errors
(1,389 collected; pytest exit `0`). The skipped checks are two Windows-specific
platform/durability checks and two Arch pacman executable ownership probes;
the latter remain `UNVALIDATED` without trusted ownership evidence. The
204-entry source manifest is unchanged after the run. See the
[attempt-10 receipt](evidence/software-regression-20261025/exact-tree-recapture-20261025/attempt-10/README.md).

At that earlier checkpoint, the full native Windows suite remained
unvalidated; the current-source attempt-15 selection later passed in shards.
This software suite does not satisfy firmware build, worker VM/image, Live USB
boot, or physical hardware acceptance gates. The 1vyrain/T430, Libreboot/X200, and
Coreboot/X220 hardware workflows remain pending; no physical write or recovery
was performed. Both Windows-specific checks skipped under WSL passed in a
separate current-source native Windows run (2 passed; see [attempt 12](evidence/windows-current-regression-20261026/attempt-12/README.md)).
Two full Windows attempts stopped at 31% under the free-space guard; neither
has a complete pytest summary or JUnit result. See the
[Windows regression record](evidence/windows-current-regression-20261026/README.md).
The six mapped offline-worker cases passed or skipped at their intended
POSIX/Linux boundary (6 passed, 2 skipped), but this is not a full Windows run.

## Current-source targeted checks — 2026-10-26

After the full run below, guarded POSIX API wrappers and four source pins were
updated. SOL accepted the narrow typing change. Win32-target mypy passes on 119
files (see the [retained mypy evidence](evidence/windows-targeted-typing-20261026/README.md)). LUNA reports 19 POSIX/source-closure tests and 2 publication/mode tests
passed under Ubuntu WSL, a separate overlapping 9-test WSL selection passed,
and 8 Windows-compatible tests passed. Raw output/JUnit was not retained for
these focused runs. Five additional current-tree native Windows safety tests
passed: X220 and X200 flash/restore block checks (2 total) and T430
incompatible-IVprep/post-install rejection checks (3). See the retained
[attempt 04](evidence/windows-current-regression-20261026/attempt-04/README.md),
[attempt 05](evidence/windows-current-regression-20261026/attempt-05/README.md),
and [attempt 06](evidence/windows-current-regression-20261026/attempt-06/README.md)
evidence. Attempt 03 collected no tests because of an invocation error and is
recorded separately.
The full current-source suite at that checkpoint is recorded in attempt 10
above. The full native Windows selection was later completed in attempt 15.

## Historical full software regression — attempt 08, prior tree — 2026-10-26

The previous exact-tree snapshot, before the typing follow-up above, completed
with **1,385 passed, 4 skipped,
and 0 failed** in 823.62 seconds; pytest exit `0` (1,389 JUnit cases, zero
failures/errors). This run includes the refreshed `offline_vm_build.py`
source pin. The two Windows-specific skips and two Arch pacman ownership
skips are recorded; pacman ownership remains `UNVALIDATED`. The two Windows
cases skipped by WSL passed natively before the typing follow-up (**2 passed in
8.94 seconds**); at that earlier checkpoint the current full Windows suite
remained unvalidated. See the
[native Windows evidence](evidence/windows-current-regression-20261026/README.md)
and [attempt-08 evidence](evidence/software-regression-20261025/exact-tree-recapture-20261025/attempt-08/README.md).

The runner could not remove root-owned fixtures from its private `/tmp`
scratch and recorded `cleanup.exit=2`. After verifying its exact canonical
path, WSL root removed the attempt-specific scratch; the cleanup receipt is
documented separately and remains unmodified. Attempt 07 remains preserved as
a failed pre-pin-refresh full run in the
[attempt-07 status record](evidence/pending-worker-child-status-20261026/attempt-07-full-suite-status.md).

Attempt 06 previously passed **1,378 tests with 4 skipped and 0 failures**;
SOL accepted its evidence. See the [attempt-06 record](evidence/software-regression-20261025/exact-tree-recapture-20261025/attempt-06/README.md).
The two Windows-specific tests passed separately (**2 passed**; see
[Windows regression evidence](evidence/software-regression-20261025/full-post-phase3-windows-20261025/README.md)).
Two Arch pacman ownership preflights remain skipped and `UNVALIDATED` because
trusted executable-ownership evidence is absent.

This is software-only evidence. Worker deployment, authenticated package
closure, VM/image execution, product build/boot, and hardware acceptance
remain pending.

## Ubuntu verifier provenance — proposal accepted, acquisition still blocked (2026-10-25)

SOL accepted the Ubuntu verifier provenance bundle as a **host-relative
proposal only**. The signed APT metadata and matching package bytes are
consistent with the Ubuntu archive keyring and `gpgv` already installed in
this WSL environment, but neither trust root's provisioning is independently
authenticated. This is not Stage-A authorization to acquire Arch inputs.
Arch key/package acquisition remains blocked pending independently
authenticated verifier and Ubuntu root-keyring provenance, followed by review
of Arch master-key material and package-signer certification/revocation
evidence. See the [Ubuntu verifier provenance record](evidence/ubuntu-verifier-provenance-20261025/README.md).

SOL separately accepted the [Windows TLS corroboration record](evidence/windows-tls-corroboration-20261025/README.md)
as transport corroboration only, not Stage-A authorization. Windows Schannel
and Windows certificate-store validation corroborated the HTTPS transport and
exact bytes of the same three Ubuntu package archives; each matched the signed
index size and SHA-256. The curl Schannel result was `ssl_verify_result=0`.
The supplemental Windows `X509Chain` record used `RevocationMode=NoCheck`, so
revocation was not independently evidenced by that supplemental chain.

Ubuntu root-keyring/verifier provisioning and Arch master-key/signer
certification/revocation remain open. Arch key/package acquisition remains
blocked.

A separate Windows Schannel capture of the [Ubuntu verification pages and
archive keyring](evidence/ubuntu-trust-corroboration-20261025/README.md) was
accepted by SOL as a host-relative proposal only. The three documented primary fingerprints match
the captured keyring and the earlier `.deb` payload, but this is cross-source
consistency over HTTPS, not an independent Ubuntu signature or root-keyring
provisioning proof. Windows trust is relative to its active certificate
store; the supplemental `X509Chain` used `RevocationMode=NoCheck`, so
revocation was not independently evidenced. The list-only WSL `gpg` parser is
also host-relative. Stage A and Arch key/package acquisition remain blocked.
The current Arch master-key page is not historical evidence of signer state
for the pinned 2026-09-30 snapshot; dated certification/revocation evidence
remains open.

### Ubuntu signed-index re-verification — host-relative proposal — 2026-10-25

An offline re-check used the retained Windows Schannel-captured Ubuntu package
archives and signed APT metadata. The `gpgv` payload freshly extracted from the
captured `.deb` matched both `/usr/bin/gpgv` and the Windows-captured bytes.
With a private empty `GNUPGHOME` and explicit archive keyring, both retained
`InRelease` files verified under primary fingerprint
`F6ECB3762474EDA9D21B7022871920D1991BC93C`; the signed `Packages.xz` rows for
`gpgv`, `gpg`, and `ubuntu-keyring` matched each exact archive's size/hash.
Both pockets omit `Valid-Until`; signed dates passed the documented bounded
freshness policy at the captured local clock. Exact logs, argv, exits, hashes,
and failed harness attempts are in the
[Ubuntu signed-index re-verification bundle](evidence/ubuntu-signed-index-reverification-20261025/README.md).

This remains host-relative, not an independent bootstrap: Windows Schannel
depends on the active Windows root store, and the WSL keyring/gpgv plus
runtime provisioning are not independently authenticated. SOL accepted this
bundle as a host-relative proposal only; Stage A and Arch key/package
acquisition remain blocked. No Arch
material was acquired, no package was installed, and no image/VM/worker or
hardware operation was performed.

### Arch historical trust dossier — SOL accepted as bounded research; acquisition blocked — 2026-10-25

The [Arch historical-trust dossier](evidence/arch-historical-trust-dossier-20261025/README.md)
captures the current master-key policy/fingerprints/revokers and the official
date-scoped Archive metadata for the pinned 2026-09-30 snapshot. It binds the
official keyring release page and immutable packaging commit to the exact
`archlinux-keyring 20260909-1` metadata row; the commit changes packaging
metadata, not the keyset. Archive `HEAD` lengths match the pinned DB rows. The
original dossier did not fetch archive bodies or signatures, and the current
master-key page is not historical key-state evidence. A later signature-only
follow-up identifies the issuer fingerprint embedded in pacman `-2`'s detached
signature and matches its digest to the pinned DB row, but does not verify the
signature or establish key control, historical certification, or revocation
state; see the [signature evidence](evidence/pacman-minus-two-signature-id-20261026/README.md).
SOL independently accepted the dossier as bounded research evidence and
confirmed its manifest and the separation of failed and successful HEAD
captures. This proposal has no Stage-A authorization. Arch trust acquisition,
package closure, worker/image/VM execution, product boot and hardware gates
remain pending.

The worker closure, image, guest, product ISO/boot, and physical hardware
release gates remain pending. No Arch keys/packages were acquired or imported,
and no firmware or recovery operation was performed.

## Current resolver and package-closure follow-up — 2026-10-25

SOL accepted the fail-closed resolver slice. The pinned snapshot has no
authenticated ALPM `vercmp` identity, so production closure generation rejects
caller-supplied comparators before database parsing or comparison. Adding a
resolver field to altered lock bytes fails the exact snapshot hash check. The
focused closure/image regression passed **68 tests, 0 skipped, 0 failures or
errors**; scoped mypy passed on both modules. Retained log, JUnit, exit files,
environment, source hashes, and the unavailable trust/seed records are in the
[resolver evidence bundle](evidence/worker-package-seed-policy-20261025/README.md).

This follow-up did not rerun the full suite. Package signatures remain
unverified and acquisition-blocked; no key or package was acquired, no closure
was generated, and no image, VM, product ISO, or hardware workflow was run.

## Historical Phase-3 regression — 1,375 passed / 4 skipped — 2026-10-25 (superseded by the latest captured full regression above)

Ubuntu WSL 2, Python 3.14.4, pytest 9.1.1: **1,375 passed, 4 skipped, 0 failed, 0 errors** (1,379 JUnit cases; 858.21 seconds; exit `0`). The two Windows-only skips were both run successfully in a targeted Windows-host run (**2 passed**). Two real Arch host probes remain `UNVALIDATED` because this host lacks trusted Arch pacman executable-ownership evidence. They are skips, not passes. Exact commands, captures, skip reasons and hashes are in the [post-Phase-3 full regression record](evidence/software-regression-20261025/full-post-phase3-20261025/README.md).

The current full Windows suite was attempted but interrupted when C: fell to approximately 1.6 MiB free; it has no completed result. The two Windows-specific V1 checks passed in a separate short-path targeted run. See the [Windows host record](evidence/software-regression-20261025/full-post-phase3-windows-20261025/README.md). The previous 1,362-case full run with nine failures is historical and predates the Phase 1–3 fixes.

This is software-only evidence. The package resolver and authenticated package closure, worker VM/image execution, product ISO build/boot, and all three representative hardware workflows remain pending. No package acquisition, firmware write, hardware recovery, or physical boot was performed.


At commit `8ea32c5`, the latest full regression passed on Windows (**955
passed, 71 skipped**; 1,026 collected; 1,687.79s; exit 0) and Ubuntu 26.04 WSL
(**1,022 passed, 4 skipped**; 1,026 collected; 1,497.18s; exit 0).
Full-project no-incremental mypy passed on 111 source files in Windows native
mode, Windows Win32-targeted mode, and Ubuntu WSL. All skips in this historical
`8ea32c5` regression remain unpassed.
JUnit reports, hashes, and commands are in [`2026-10-21 software regression evidence`](evidence/software-regression-20261021/README.md).

The final Linux run in the earlier [worker regression evidence bundle](evidence/software-regression-worker-20261022/README.md), after the TUI challenge-readiness test change, passed **1,146 tests, skipped 4** (1,150 collected; 758.975s; zero failures) in Ubuntu 26.04 WSL2. Full-project mypy passed on 116 source files; both CLI help checks and `git diff --check` passed. This result was superseded by the later 2026-10-23 source-pinned 33-module aggregate: **1,221 passed, 4 skipped** (see the latest software-regression row below).

On current Windows source, the UI selection passed 58 with 1 skip and the cancellation race passed 1/1; the full Windows suite was not rerun because C: had only 186,892,288 bytes free. The previous 1,088-test full Windows result is for its recorded 2026-10-22 snapshot, not this final source. Exact reports and input hashes for the 1,146-test run are in the earlier bundle.
The WSL run is software-only; the sealed build VM and all three real-device
workflows remain open.

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
was stopped before completing an initramfs. Subsequent startup retries 3–16
were diagnostics rather than native build/test gates and also produced no
ISO. Retry13 reached only a systemd debug shell; read-only `id` and `lsblk`
showed root and the expected ISO/workspace/results/read-only-source devices.
At that query snapshot, 88 jobs remained pending around `systemd-nsresourced`
and tmpfiles; later the transcript recorded the failure `[FAILED] Failed to start Namespace Resource Manager` (cause not established). The ISO hash was
unchanged. No normal live boot or rootless preflight passed. Retry15 was a
helper/PTY handshake failure with empty serial capture and no guest input.
Retry16 captured serial output and four Namespace Resource Manager failure
lines but timed out at 600 seconds without a stable post-failure prompt; no
query or other guest input was sent. Input hashes for the ISO, kernel,
initramfs, and source matched before and after both attempts. These diagnostics
establish neither normal boot nor root cause. See the
[current-source native Arch build evidence](evidence/native-arch-build-20261021/README.md),
[retry13 transcript/result](evidence/native-arch-build-20261021/boot-diagnostic-retry13-direct-kernel/README.md),
the [retry14 observation bundle](evidence/native-arch-build-20261021/boot-diagnostic-retry14-observation-only/README.md),
[retry15 PTY failure](evidence/native-arch-build-20261021/boot-diagnostic-retry15-pty-failure/README.md),
and [retry16 unresolved diagnostic](evidence/native-arch-build-20261021/boot-diagnostic-retry16-service-timeout/README.md).
Retry14 ran for 600.376 seconds with no guest input. The live stop-marker scan
did not strip ANSI escapes; a separate post-run normalized audit found four
Namespace Resource Manager failures, zero normal startup target markers, and
zero emergency/fatal markers. Cause is unknown. ISO, kernel, initramfs, and
source-image before/after hashes matched, and free space remained 153.786 GiB.
No native tests, build, or product ISO ran.
The current-source build remains open until normal userspace and the reviewed
rootless preflight are ready.
The build-time offline launcher remains fail-closed. No physical firmware
operation or hardware recovery has been performed; all three device workflows
remain pending and require explicit human authorization for writes and
recovery.

| Requirement | Status | Evidence |
|---|---|---|
| Latest software verification | EXACT-TREE LINUX AND WINDOWS SOFTWARE SELECTIONS PASSED; 2 ARCH TRUST PROBES UNVALIDATED | Ubuntu WSL 2, Python 3.14.4, pytest 9.1.1: **1,388 passed, 4 skipped, 0 failures/errors** (1,392 JUnit cases; exit 0; 798.83s). The 204-path source manifest, POSIX snapshot, and post-run source manifest match. Two Windows-only checks were skipped under Linux; two Arch pacman ownership probes remain UNVALIDATED. The current-source Windows selection passed 1,118 and skipped 274 across 61 shards. The runner recorded cleanup pending for protected fixtures; post-run checks found the exact scratch paths absent. See the [current Linux evidence](evidence/current-linux-regression-20261026/attempt-01/README.md) and [current Windows evidence](evidence/windows-current-regression-20261026/chunked-attempt-02-20261026/README.md). Authenticated package closure, production worker deployment, product build/boot, and all three hardware workflows remain open. |
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

After the older QEMU diagnostics listed above, a bounded native Windows QEMU
11.1.2 WHPX run on 2026-10-25 used direct kernel/initramfs loading from the
hash-verified upstream Arch 2026.10.01 ISO, which was attached read-only as a
CD-ROM. It reached a serial root shell and captured `id`, `/proc/cmdline`, and
systemd observations; it did not exercise the ISO bootloader. The system
reported `starting` and one failed
`vmtoolsd.service`. This is only an upstream live-ISO environment diagnostic;
it does not complete the worker, build, current-source product ISO, application
boot, or any hardware gate. See the
[WHPX diagnostic record](evidence/native-qemu-whpx-20261025/README.md).

The 2026-10-23 33-module aggregate is a historical capture of the source bytes
listed in its original manifest. After capture, whitespace-only cleanup
removed trailing spaces from six static-check command rows and one extra final
blank line from five worker source/test files. The focused post-cleanup worker
selection passed 171 tests; the original full-suite artifacts and manifest
were preserved unchanged. See the [whitespace cleanup follow-up](evidence/offline-worker-regression-20261023/whitespace-cleanup-followup.md)
for current hashes and the diff check.

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
and test boundaries. The earlier 2026-10-21 native Windows QEMU 11.1.2 WHPX/TCG full-ISO attempts failed to reach a guest shell; see [native QEMU evidence](evidence/native-qemu-windows-20261021/README.md). A later acceleration check confirms the i3-3110M itself
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

## Sealed-VM worker prototype — 2026-10-22

The approved guest-only `NoNewPrivileges=0` boundary is recorded in
[`D-009`](DECISIONS.md). The current worker is an **unavailable prototype**:
the default guest entrypoint returns `BUILD_BACKEND_UNAVAILABLE`; the separate
fixed executor prototype described below is not wired into `build_usb.py` and
has no production deployment pin. The current-source full suite passed with zero failures/errors:
Ubuntu 26.04 WSL2 passed 1,084 and skipped 4; Windows passed 994 and skipped
94, with 1,088 collected on each platform. Linux full-project mypy passed on
113 source files and both CLI help commands passed. Worker-focused tests
passed 17/17 in WSL and 11/11 Windows-applicable cases (6 Linux-only skips).
The Linux and Windows skips are enumerated in the linked regression evidence;
all remain unpassed. SOL accepted the unavailable prototype only. No sealed
VM build, product ISO, or hardware operation ran. All three real-device
workflows remain **HARDWARE PENDING**.

### Guest executor implementation follow-up — 2026-10-22

The separate unavailable prototype now includes a fixed guest `mkarchiso`
executor and an NNP=1 preflight to NNP=0 sealed-guest systemd handoff, plus a
fixed host QEMU adapter and candidate validation entrypoint. The four packaged
unit resources match their embedded bytes. Its focused executor/host selection
passed 32 tests in Ubuntu WSL; targeted mypy passed on five source modules and
`git diff --check` passed. No deployment pin or `build_usb.py` integration was
added. The unit pair has not run in a sealed VM, and package installation,
`pacstrap -N`, `mkarchiso`, candidate target-root/provenance/BIOS/UEFI checks,
and ISO validation remain open. This is not an offline-build pass; no VM/build,
ISO, or hardware operation occurred.

### Worker deployment gate assessment — 2026-10-23

The guest remains unavailable. The native Windows host has no QEMU executable;
QEMU 10.2.1 is available only in Ubuntu WSL, where `systemd-detect-virt`
reports `wsl`. There is no bootable worker image or reviewed QEMU/image pin in
the repository, and the approved Linux workspace partition was absent from
WSL at the latest read-only recheck. No guest or build was run. The existing
fixed QEMU contract specifies TCG, `-nic none`, block devices only, and a
guest-local NNP=1 preflight followed by the fixed NNP=0 systemd builder unit;
these source rules have not been proven against an installed image.

Release remains gated on building a bootable worker image from the exact
package lock, pinning the image and supported QEMU executable, and exercising
them on a package-matched Linux host. The run must verify that the guest has no
NIC, device passthrough, or writable host share; that NNP=0 is confined to the
fixed guest systemd service after NNP=1 preflight; and that locked
`pacstrap -N`, `mkarchiso`, target-root/provenance checks, and BIOS/UEFI boot
validation complete. Independent review of those operational captures is
required before setting `PRODUCTION_WORKER_DEPLOYMENT` or connecting the path
to `build_usb.py`. No physical firmware operation is included.

### Offline VM environment follow-up — 2026-10-23

The user subsequently authorized read-only inspection of the Linux partition
and a new private VM workspace there. The ext4 partition had about 137 GiB
free; the workspace received a copy of the verified Arch ISO, its extracted
kernel and initramfs, and diagnostic screen captures. The partition was
remounted read-only afterward. This supersedes the earlier absent-mount
observation as the current environment state.

QEMU 10.2.1 in Ubuntu WSL ran the official-hash-verified Arch 2026.10.01 ISO
under unprivileged TCG with no guest NIC, passthrough, writable host share, or
disk. One screen capture reached the Arch welcome text; a later roughly
213-second console check remained on the early `Probing EDD` / TDX screen, and
the injected `uname` had no observable result. This is inconsistent screen
progress, **not a verified guest boot**. QEMU's KVM accelerator also failed
with `No such device` despite process-only access to `/dev/kvm`, so this host
is limited to TCG for the current attempt. The images and SHA-256 values are in
the [VM ISO diagnostic](evidence/vm-iso-tcg-diagnostic-20261023/README.md).

The worker image, exact locked package installation, systemd preflight and
builder execution, `pacstrap -N`, `mkarchiso`, target-root/provenance
validation, and BIOS/UEFI guest boot remain untested. Keep the worker
unavailable and do not set `PRODUCTION_WORKER_DEPLOYMENT` until these checks
are performed and reviewed on a stable package-matched Linux VM. The separate
T430, X200, and X220 hardware acceptance remains deferred; physical writes
and recovery still require explicit authorization.

A later minimal direct-kernel initramfs run used `rdinit=/bin/sh` and reached
BusyBox; HMP `uname` returned `Linux`. This demonstrates only a minimal
initramfs shell command, not a full ISO, product, or worker boot. In a final
full-ISO TCG attempt with floppy drives disabled, the guest progressed through
initramfs hooks, `/dev/sr0` and loop mounts, and a cleanup hook to the Arch
welcome screen, but showed no interactive shell or systemd. The exact QEMU
arguments, HMP command sequences, screenshots, and limits are recorded in the
[VM ISO diagnostic](evidence/vm-iso-tcg-diagnostic-20261023/README.md) and its
[run record](evidence/vm-iso-tcg-diagnostic-20261023/run-record-20261023.txt).

### Fixed guest evidence handoff prototype — 2026-10-23

The initial pending-only build-service prototype collected the exact
operation-owned candidate ISO, `x86_64/airootfs`, unique application wheel,
fixed pacman package inventory, command-status record, and provenance. At that
stage the status record described only the named fixed service result and did
not carry the executor's child observations. The later follow-up below adds
that bounded observed result; it still does not synthesize a `mkarchiso` exit
status. A
one-use callback streams the records and target root to `LC-EVIDENCE-RW`, then
returns the synced size/hash before the service result and output
receipt/header/commit/terminal markers can be published. Host validation checks
the exact service status, package rows, and provenance. Focused synthetic Linux
checks passed 38 worker/finalizer cases and 98 evidence/adapter/executor cases.

This source and synthetic-test result does not establish VM deployment or
guest execution. `PRODUCTION_WORKER_DEPLOYMENT` remains `None`, the default
guest entrypoint remains `BUILD_BACKEND_UNAVAILABLE`, and `build_usb.execute_build`
remains disconnected. A pinned guest image, effective isolation/NNP evidence,
locked guest build and product ISO, BIOS/UEFI guest validation, and all hardware
acceptance remain open.

### Child-process observation and regression results — 2026-10-25

The complete suite passed **1,262 tests**, with **4 skipped**, in **1,012.64
seconds** and exit **0** on the unchanged pre-child-observation tree. The
retained JUnit report totals 1,266 tests, 0 failures, 0 errors, and 4 skipped;
it is 196,663 bytes with SHA-256
`bc6e416c4ca02f961d25844c3c65c20153137d98ff304d50bf30af6ece7f98dc` at
`/mnt/wsl/PHYSICALDRIVE1p2/opt/librecore-autoloader-v1-vm/pytest-final-20261023/junit.xml`.
This is a pre-change checkpoint and does not validate the child-observation
changes.

After those changes, the four impacted modules (`test_offline_vm_worker.py`,
`test_offline_vm_build_executor.py`, `test_offline_vm_evidence.py`, and
`test_offline_vm_evidence_adapter.py`) passed **217 tests** in **14.87 seconds**
with exit **0** under Ubuntu WSL/Python 3.14.4 and pytest 9.0.2. The four
module selection used a fresh `/tmp` basetemp; pytest and its pure-Python
dependencies were unpacked unprivileged for this run. At that point the full
suite had not yet been rerun. The subsequent current-source full suite passed
**1,283 tests, with 5 skipped and no failures or errors**, in 795.76 seconds,
exit **0**, under Ubuntu WSL/Python 3.14.4, pytest 9.0.2, and Textual 2.1.2.
The JUnit report totals 1,288 cases. Exact invocation, skip reasons, and
preserved prior diagnostics are in the [2026-10-25 regression evidence](evidence/software-regression-20261025/README.md).
That result predates the final parser typing and integer-as-boolean test
follow-up; it is superseded as the latest suite by the 1,285/5 run recorded
below.

The versioned service result now carries a bounded typed snapshot copied from
the actual `BuildProcessResult`: service return code and timeout, fixed argv
digest, elapsed time, captured `pacstrap` exits, NNP and network-namespace
values, account and IDs, supplementary groups, capability values, interfaces,
and routes. A candidate requires the exact fixed service/argv and command list,
zero service and captured `pacstrap` exits, no timeout, and the expected
recorded isolation/capability values. No `mkarchiso` exit is fabricated. The
host checks the exact nested command-status schema and the raw command-status
SHA-256 bound in provenance. A valid nested-value change with the old digest is
rejected. These values are guest-reported consistency observations, not an
independent measurement or attestation.

This update remains a source and fake-test result only. The default guest
entrypoint is `BUILD_BACKEND_UNAVAILABLE`,
`PRODUCTION_WORKER_DEPLOYMENT` remains `None`, and `build_usb.execute_build`
remains disconnected. VM deployment, effective isolation proof, guest build,
product ISO, full-ISO/worker boot, and hardware acceptance remain open.

### Final current-source regression after type follow-up — 2026-10-25

The current full suite passed **1,285 tests, skipped 5**, with zero failures or
errors in **813.16 seconds**; the retained exit file contains `0`. JUnit has
1,290 tests, is 200,773 bytes, and has SHA-256
`e365e96cef0fbafa3ef724954c56801a5203cd1774df5f5aca75bc58d160ddd4`. The pytest
log SHA-256 is
`0ddde9c84cdcd6ab85d5a675ef3f24feba358d07ad1a5ad97bc6733fad8a0780`; the exit
file SHA-256 is
`9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa`. The run
was under Ubuntu WSL 2 with Python 3.14.4, pytest 9.0.2, Textual 2.1.2, normal
`alex` groups and `umask 0022`; see the regression README for the exact
invocation and skip names.

Native and Win32-targeted mypy 1.19.1 reported no issues in 117 files. The
four focused offline worker/evidence modules passed 219 tests. The separate
wheel smoke passed 1 test in 5.78 seconds with setuptools 84.2.1 and wheel
0.48.0 available. The five full-suite skips remain recorded: two Windows-only
contracts, the wheel-build-tools requirement in the full-run environment, and
two unvalidated Arch host-ownership preflights. The standalone wheel smoke is
separate from the full-suite aggregate.

These checks do not validate a deployed worker, effective guest isolation,
VM build, product ISO, full-ISO/worker boot, or hardware. The guest entrypoint
remains unavailable by default, production deployment remains unset, and
`build_usb.execute_build` remains disconnected. No physical workflow is
accepted.

### Sealed worker-image recipe contract — 2026-10-25

The host-only recipe verifier binds the current source input closure, the exact
four fixed systemd resources, candidate `fwbuild` UID/GID/subid lines, the
patched pacstrap hashes, and the snapshot lock. Its output labels these as
verified source inputs and keeps image-build provenance separate and
`NOT_BUILT`. The external QEMU `fw_cfg` SHA-256 is derived from the eventual
raw-image identity and is never written into that image.

This recipe is not publishable yet. The current lock covers the builder
toolchain and repository database hashes, but not the complete signed
base/systemd/Python transitive root package/version/archive closure. The
hash-pinned Arch 2026.10.01 ISO is a read-only bootstrap seed only; retry8
records `pacman -3` in its root and `archiso 91-1` absent from the target root.
The rootless launcher remains an unavailable source stub without a reviewed
deployable binary and authorization pin. The public candidate path rejects
with `IMAGE_BUILD_UNAVAILABLE` before creating output. Synthetic transaction
tests cover atomic/fsynced publication and reopen identity checks only; no
worker image was produced or booted.

`PRODUCTION_WORKER_DEPLOYMENT` remains `None`, the default guest entrypoint
returns `BUILD_BACKEND_UNAVAILABLE`, and `build_usb.execute_build` remains
disconnected. Full signed package closure, deployable launcher, networkless
guest-worker build/boot, product BIOS/UEFI validation, and all hardware gates
remain pending.

### Provisional worker package seed inventory — 2026-10-25

The new parser checked the staged `core.db` and `extra.db` against the fixed
snapshot hashes and captured nine direct package metadata rows. Their
database-declared compressed archive sizes sum to 37,264,803 bytes. The
inventory is explicitly `SEED_SET_INCOMPLETE`: it does not include a complete
worker base/account or kernel/initramfs seed policy, and no recursive closure
was generated because `snapshot.lock.json` has no hash-pinned exact ALPM
comparator identity. `archiso` is a builder-host package, not a target USB
root package. No package archives were fetched; decoded signature metadata
was hashed but not cryptographically verified. See the
[inventory record](evidence/worker-package-seed-inventory-20261025/README.md)
and its machine-readable rows.

The scoped parser tests passed **16/16**, and targeted mypy passed on the
module and test file. The 1,293 passed / 5 skipped full-suite result above is
from before this parser change; a post-change full suite is not recorded yet.
The guest remains unavailable by default, production deployment remains
unset, `build_usb.execute_build` remains disconnected, and the candidate
publisher's post-rename cleanup/fsync behavior remained unresolved at this
checkpoint; see the later synthetic-only recovery follow-up below.
Worker image construction, guest boot/build, product ISO validation, and
hardware workflows remain open.

### Direct-kernel worker boot contract — 2026-10-25

The deployment contract now requires separate immutable kernel and initramfs
identities. QEMU receives their exact pinned bytes through sealed read-only
memfds using only the fixed `-kernel`, `-initrd`, and `-append` boot arguments.
The append string fixes `root=PARTUUID=4c435752-0001-4000-8000-000000000001`,
`rootfstype=ext4`, `ro`, and `console=ttyS0,115200n8 loglevel=4`; there is no
caller argument override. The candidate sidecar requires both boot hashes and
the exact root contract.

The planned disk uses GPT, partition 1 with Linux filesystem type GUID
`0fc63daf-8483-4772-8e79-3d69d8477de4`, the fixed PARTUUID above, and ext4
filesystem UUID `4c435752-0001-4000-8000-000000000002`. The initramfs must
include `virtio_pci`, `virtio_blk`, and `ext4` and mount the root read-only.
No such disk, kernel, or initramfs has been generated or validated. The full
signed guest root seed/closure must include base, systemd, Python, kernel,
mkinitcpio, and exact initramfs contents; the existing Arch ISO is bootstrap
input only. SeaBIOS is a host QEMU firmware dependency if selected, not a
guest package.

Direct kernel loading skips the disk bootloader but may still execute QEMU's
default x86 firmware. The fixed argv omits `-bios`; the default QEMU ROM-set
identity is unresolved. This contract is neither firmware bypass nor a
BIOS/UEFI boot result. Candidate publication, production deployment, guest
entrypoint, and `build_usb.execute_build` remain closed. No package download,
image build, QEMU launch, hardware action, or recovery operation occurred.
At this checkpoint the candidate publisher's post-rename cleanup/fsync handling
remained unresolved; the synthetic-only follow-up below hardens transaction
state and recovery inspection without enabling publication.

## Candidate publisher transaction recovery — 2026-10-25 / SYNTHETIC ONLY

The private publisher now records `PRE_RENAME`, `RENAMED_UNCOMMITTED`, and
`COMMITTED_REOPENED` in a durable sibling journal. Its read-only inspector is
idempotent and checks destination inode and manifest digest. It infers a rename
that completed before the journal update, reports incomplete or mismatched
state, and never deletes a destination once rename may have happened. Pre-rename
cleanup, lock unlink, and directory-fsync failures surface to the caller;
retained journal/lock state blocks replay.

Synthetic failure-injection tests cover rename, parent fsync, reopen, stage
cleanup, lock unlink, and lock-unlink parent fsync. The private helper returns
no deployment on those failures. This is not a worker-image publication or
acceptance result: `PRODUCTION_WORKER_DEPLOYMENT` remains `None`, the guest is
unavailable, and the public publisher/build route remains closed. The focused
image module passed 35 tests; the 1,336/5 full-suite capture predates this
follow-up. No image build, QEMU launch, package acquisition, or hardware action
was performed.

## Separate worker and target package roots — 2026-10-25 / METADATA ONLY

The current seed policy supersedes the earlier nine-row direct inventory: it
records 13 worker/build-host roots and the 29 target USB roots from the
hash-pinned profile. `archiso` is a worker-host package and is excluded from
the target profile. Database-declared direct archive size estimates are
206,233,344 bytes for worker roots, 231,413,691 bytes for target roots, and
245,814,629 bytes after deduplicating shared archives. These are not fetched
archive sizes or transitive closure totals.

Recursive closure and package acquisition remain blocked because the snapshot
does not pin the exact pacman `vercmp` executable identity. Both closure graph
statuses are `UNAVAILABLE_NO_PINNED_ALPM_RESOLVER`; package signatures have
not been cryptographically verified. The DB policy is `Required
DatabaseOptional`: DB hashes are mandatory, supplied DB signatures are
verified, and runtime rechecks exact DB hashes before metadata use and after a
successful builder run before result publication. Package archive signatures
remain mandatory. The proposed
official snapshot fetch and isolated trust chain is recorded in the
[seed-policy evidence](evidence/worker-package-seed-policy-20261025/README.md)
for separate review; no downloads or trust setup occurred. An 8 GiB staging
allowance is provisional only; transitive closure and extracted-root size are
unknown.

The scoped parser/build-executor selection passed 75 tests and targeted mypy
was clean for the three changed source modules. This does not enable package
repository use, candidate publication, production deployment, guest
execution, image build, product ISO/boot, or hardware workflows.

### Operation-owned repository DB snapshot — 2026-10-25 / SOFTWARE ONLY

Preflight now creates a private hash-checked copy of the exact request-verified
repository DBs and their associated metadata. It applies 0400 file and 0500
directory modes, which do not prevent the owner from changing them. The fixed
builder unit declares `/run/librecore-package-dbs` in `ReadOnlyPaths`, and the
expected effective systemd properties require that path; runtime proof of the
read-only mount remains pending. The fixed pacman config selects that private
DB copy before the read-only archive repository; the service revalidates the
copy after the fake builder returns and before evidence or candidate
publication. Tests cover source DB mutation during the child call, private-copy
owner/content checks, a same-inode/same-size pacstrap status rewrite between
stat and open, and a prompt bound incomplete receipt when post-executor
validation fails before result publication.

The six focused modules passed **307 tests** with zero failures/errors/skips;
scoped mypy passed on six worker modules. A new regression rejects a
same-inode/same-size pacstrap-status rewrite between initial stat and open.
See the
[phase 3 regression evidence](evidence/software-regression-20261025/phase3-immutable-db-snapshot-20261025/README.md).
The package closure still lacks the pinned `vercmp` executable/runtime and
cryptographically verified archive signatures. This does not enable package
acquisition, image publication, production deployment, guest/VM execution,
product ISO/boot, or hardware acceptance. The earlier full-suite result
predates this follow-up and has not been rerun afterward.

### Phase 09 child-status receipt strict parsing — 2026-10-26

The pacstrap status receipt parser now requires canonical decimal rows ending
in LF and rejects truncated, CRLF, alternate-separator, leading-zero, and
empty-row forms. The focused Ubuntu WSL selection passed **25 tests** in 1.84
seconds with pytest exit `0`; its command, output, exit status, and hashes are
in the [focused regression evidence](evidence/pending-worker-child-status-20261026/README.md).
LUNA reported that the pre-fix run reproduced two false accepts, but its red
output and exit capture were not retained. The complete suite was not rerun
after this focused follow-up; SOL accepted the focused software fix. This does
not validate a deployed/booted worker,
package acquisition, ISO/firmware build, or hardware workflow; those gates
remain open.
