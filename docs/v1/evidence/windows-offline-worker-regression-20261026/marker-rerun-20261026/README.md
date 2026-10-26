# Windows offline-worker marker selection — 2026-10-26

## Result

The selected 17 test definitions expanded to **35 cases** on Windows. Pytest
reported **35 skipped, 0 failed, 0 errors** and exited `0`. JUnit and captured
console output are retained here. The test-source SHA-256 is in
`test-source.sha256`; the source snapshot hashes and file counts are recorded
in `source-before.json` and `source-after.json`.

This run verifies that Windows skips the Linux-only guest-publication and
private-directory E2E cases that failed selection in the earlier broader run.
It does not execute those Linux tests, rerun the other 285 worker cases, or
validate the full native Windows suite. The prior broad failure is preserved
under the parent directory.

The first invocation in this directory failed to collect tests because the
JUnit output path was passed incorrectly. Its output and exit receipt are
retained as `invocation-01-*`; the corrected invocation then collected and
skipped all 35 selected cases.

No firmware or physical hardware was accessed. The full Windows suite remains
open. See `SHA256SUMS` for checksums of this evidence (excluding the checksum
file itself).
