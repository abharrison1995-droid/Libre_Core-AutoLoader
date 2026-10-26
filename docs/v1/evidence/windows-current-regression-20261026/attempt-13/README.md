# Native Windows full-suite runner preparation — attempt 13

**Status: the latest `-PreflightOnly` attempt passed.** The probe-only receipt is `run-20261026T041029Z-9c75ef582175/`; compression was verified, exact scratch cleanup passed, and its run manifest passed. The earlier failed attempt `run-20261026T040202Z-aa3e6cd92697/` remains preserved as history. Neither run created a source snapshot or launched Python/pytest, and no hardware was accessed. The earlier run's `run-status.txt` retains its original `prelaunch-blocked` value; this was a classifier bug, and its correct interpretation is `probe-only-failed`.

## Purpose

`run-native-windows-full-suite.ps1` is a guarded runner for a future current-tree native Windows full-suite regression. It stages source files into a short-path workspace under `C:\w`, runs pytest using the repository's existing `.venv\Scripts\python.exe`, retains run receipts in this attempt directory, and removes only its own GUID-named scratch after checking its ownership marker and reparse-point state.

The source manifest includes tracked repository files plus non-ignored, source-like untracked files under `autoloader/`, `tests/`, `usb_builder/`, and `docs/`, and source-like root files. It excludes virtual environments, caches, build outputs, host-generated root artifacts, and this attempt's run outputs. Missing manifest files and reparse paths fail closed. It does not copy `.git`; current tests exercise Git through their own fixtures, and the environment sets an empty `GIT_TEMPLATE_DIR` plus process-only `core.longpaths=true`.

`-PreflightOnly` is a separate, bounded mode for reviewing NTFS compression. It requires at least the 2 GiB live floor, creates one new GUID-named scratch root, and cleans only the marker-verified scratch root. The first attempt found compressed attributes on the directory and file but measured 1,048,576 allocated bytes for a 1 MiB repeated-byte file, so it failed the compression check. In the latest run, `GetCompressedFileSizeW` measured 1,048,576 bytes before file compression and after `compact.exe /C /I /Q`; both compact calls exited 0. The conditional `compact.exe /C /I /Q /F` reduced the allocation to 65,536 bytes (16:1), so the compression check passed. The receipt records each reached allocation stage separately, uses `not-measured` when a stage is skipped or fails before measurement, and retains the final allocation. It also records each command, exit code and output, filesystem, and attributes. `-PreflightOnly` skips source enumeration/copying and never launches Python or pytest. The default full-suite mode keeps the 10 GiB start gate.

The retained latest `preflight.txt` was written by the runner before this receipt clarification: its initial `compression_probe_started=false` sentinel remains beside the final `status=probe-only-passed` and `compression_probe=passed` fields, and it repeats `pytest_started=false`. The probe did run, as confirmed by its compression and cleanup receipts. The runner now changes the in-memory probe-start field to `true` as soon as the probe begins and no longer appends a second pytest-start field, so future preflight receipts will report those states consistently.

Microsoft documents a directory's compressed attribute as the default for newly created files, and documents `GetCompressedFileSize` as returning less than the uncompressed size for a compressed file. The follow-up diagnosis confirmed that the first per-file `compact.exe /C /I /Q` call left allocation unchanged, while `/F` reduced the allocation to 65,536 bytes. [Compression attribute](https://learn.microsoft.com/en-us/windows/win32/fileio/compression-attribute), [compressed file size](https://learn.microsoft.com/en-us/windows/win32/fileio/obtaining-the-size-of-a-compressed-file), [compact command](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/compact).

The full-suite completion gate is pinned to the **1,392 collected cases** from Ubuntu WSL attempt 13. JUnit must parse as one complete suite, its `tests` and testcase-node counts must both equal 1,392, and aggregate skipped/failure/error totals must match their testcase result nodes. It must also contain these two non-skipped, passing Windows cases:

- `tests.test_phase10a_write_approval::test_windows_directory_fsync_policy_keeps_approval_closed`
- `tests.test_usb_builder::test_real_build_usb_cli_fails_before_subprocess_on_windows`

The 1,392 expectation is deliberately fail-closed if the current tree's test collection changes; update it only with a reviewed new baseline.

## Safety gates

- The runner refuses elevated PowerShell, unexpected repository placement, non-NTFS storage, missing venv Python/Git/`C:\w`, reparse-point roots or source files, out-of-parent paths, an oversized source snapshot, and any scratch path that already exists.
- It requires **10 GiB free before staging** and enforces a live **2 GiB free-space floor** during pytest. It records the pytest root PID and CreationDate, observes descendant PIDs and CreationDates, and revalidates each target identity immediately before each `taskkill` request. Scratch removal is withheld if the root exit or observed descendant state cannot be verified. The latest probe recorded **7,991,623,680 bytes** free on C: (about **7.44 GiB**), so the full-suite start gate remains closed.
- Only after capacity and structural preflight pass does it create unique scratch, request compression on that new directory, and verify compression inheritance plus actual allocation on a 1 MiB operation-owned repetitive probe. It removes only the exact probe file and empty probe directory before copying source.
- It records source-before, staged-snapshot, and source-after SHA-256 manifests. A source change during the run prevents a passing status.
- It runs the full `tests` collection with `-p no:cacheprovider`, an isolated pytest basetemp, and process-local `TEMP`, `TMP`, `TMPDIR`, and Git configuration. It retains command JSON, environment, JUnit XML, stdout/stderr, exit, guard, cleanup, and checksum receipts. Success requires pytest exit `0`, a parseable and reconciled JUnit report with exactly **1,392** cases, zero failures/errors, and passing non-skipped results for both native Windows-specific case IDs recorded in the test receipt.
- It samples **observed pytest descendants** by PID and CreationDate once per second, revalidates those identities before termination, and removes scratch only after the root exit and observed descendant checks pass. Unverified observed writers or reparse points preserve the exact scratch root and fail the run.
- It never invokes flashrom, firmware writers, SPI devices, recovery operations, or hardware.

## Expected behavior at current capacity

The latest probe run began with **7,991,623,680 bytes** free on C: (about **7.44 GiB**), below the **10 GiB** full-suite requirement. A full-suite launch at this capacity remains blocked before scratch creation, source snapshot, Python, or pytest. The latest `-PreflightOnly` run passed its compression check and exact scratch cleanup; this is only a preflight result, not a regression-suite pass. The earlier failed probe remains intact as historical evidence. Neither probe accessed hardware.

Do not count this preparation or a preflight-only result as a regression pass. A future native Windows suite result is valid only if the runner reaches pytest, exits `0`, records a complete JUnit summary, verifies source stability, and records successful exact-scratch cleanup. Any capacity abort or guard kill remains incomplete.

## Validation performed for this preparation

- Earlier full-suite attempts were incomplete. The latest `-PreflightOnly` run did not create a source snapshot or launch Python/pytest and did not access hardware.
- Latest probe receipt: `run-20261026T041029Z-9c75ef582175/compression-probe.txt`. Both compact commands exited 0; allocation was 1,048,576 bytes before compression, remained 1,048,576 after the first command, then fell to 65,536 after `/F`. The run status and run hash manifest both passed.
- The latest retained `preflight.txt` contains the earlier runner's initial `compression_probe_started=false` sentinel despite its final passed status, plus a duplicate `pytest_started=false`; this is a receipt-assembly issue, not a claim that the probe was skipped. Future receipts use the corrected in-memory state and omit the redundant pytest field.
- Latest cleanup receipt confirms removal of the exact probe, directory, and marker-verified scratch root. The historical failed receipt remains unchanged; its run manifest still verifies. Existing `C:\w\lc-v1wfr-20261026` task evidence and earlier attempt directories were preserved.
- The earlier probe's raw run-status says `prelaunch-blocked`; the attempt-level correction records its actual classification as `probe-only-failed` without altering that historical receipt.
- Parser-only AST validation and attempt SHA-256 verification passed. No full-suite mode, Python, pytest, or hardware action was performed.

## Output layout on a future run

Each invocation uses a new `run-<UTC>-<id>/` evidence directory. It records preflight status even when capacity is insufficient. A `-PreflightOnly` run records the probe result and no source/test files. A full-suite run additionally records:

| File | Contents |
| --- | --- |
| `command.json` | Executable, argument vector, working directory, log paths, PID and launch state |
| `environment.txt` | Python/pytest/OS/PowerShell, isolated temp and process-local Git settings |
| `source-manifest-before.csv` | SHA-256 and byte length for included source files |
| `snapshot-manifest.csv` | SHA-256 and byte length for the staged source snapshot |
| `source-manifest-after.csv` | Source manifest rechecked after pytest |
| `junit.xml`, `junit-validation.txt`, `pytest.log`, `pytest.stderr.log`, `pytest.exit` | Reconciled test totals and native Windows case results, test output and process status |
| `compression-setup.txt`, `compression-probe.txt` | NTFS setup, exact-file compact command/exit/output, attributes, and pre/after-first/after-force allocation receipts, only when reached |
| `process-tree.txt` | Observed test descendants and stop verification; retained scratch is preserved if writers cannot be ruled out |
| `live-space-guard.txt`, `taskkill.log` | Populated only if the free-space floor stops pytest |
| `cleanup.txt`, `run-status.txt`, `SHA256SUMS` | Exact scratch outcome, final status and run evidence hashes. `run-status.txt` is written only after hash-manifest generation and records that manifest's SHA-256; it is excluded from the manifest to avoid a circular hash. |

Native Windows full-suite validation remains open. The WSL attempt-13 suite and Windows focused attempts are separate evidence; none establishes hardware acceptance.
