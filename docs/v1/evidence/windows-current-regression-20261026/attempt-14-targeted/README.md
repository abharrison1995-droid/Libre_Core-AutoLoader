# Targeted native Windows regression — attempt 14

**Test result: passed. Scratch cleanup: incomplete.** These are separate outcomes. The successful short-path run passed both selected Windows-only tests; its 28.16 MiB scratch directory remains because the command safety policy rejected exact-root cleanup before execution.

## Successful short-path run

Run receipts: `run-20261026T041918Z-short-712fe04a2b93/`.

- Pytest exit code: `0`; valid JUnit reports **2 tests, 0 failures, 0 errors, 0 skipped**.
- Both selected Windows-specific cases passed:
  - `tests.test_phase10a_write_approval::test_windows_directory_fsync_policy_keeps_approval_closed`
  - `tests.test_usb_builder::test_real_build_usb_cli_fails_before_subprocess_on_windows`
- Source-before and source-after manifests match.
- Firmware and hardware access were not performed.

The run used a short isolated path under `C:\w` for pytest's basetemp and process temp directories.

## Earlier path-length failure

Run receipts: `run-20261026T041620Z-a4c26d57afa3/`.

The first targeted attempt created a generated pytest path **269 characters** long. `test_windows_directory_fsync_policy_keeps_approval_closed` failed while creating a mock backup file because Windows could not resolve that path; it failed before reaching the policy assertion. The other selected Windows-specific test passed. This attempt is retained as a path-length setup failure, not a successful run of both cases. The subsequent short-path run passed both.

## Scratch cleanup limitation

The successful run's scratch root is `C:\w\lc14-83a2dcdd203d429a8bc8` (28.16 MiB). Its cleanup receipt records that PowerShell rejected both exact-root deletion commands before execution, so no deletion occurred. A read-only check confirmed the owner marker, zero reparse entries, and no active process references. The scratch remains preserved; no alternate deletion method was attempted. This cleanup limitation does not change the recorded pytest/JUnit result.

## Evidence integrity

Each run directory has its own `SHA256SUMS`; the first run is incomplete and the short-path run is passed with scratch preserved. The attempt-level `SHA256SUMS` covers this README. Checksums were verified for both run directories and the attempt-level manifest. No tests, Python, runner, cleanup, or hardware operation was run while preparing this note.
