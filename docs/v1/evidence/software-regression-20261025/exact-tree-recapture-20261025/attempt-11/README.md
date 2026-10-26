# Interrupted Ubuntu WSL regression — wrong filesystem invocation — 2026-10-26

This is **not a full-suite result** and is not counted as a product regression.
The command collected 1,391 tests, but was launched from the checkout directly
on `/mnt/c` instead of the POSIX snapshot required by the accepted runner in
attempt 10. The test-specific run scratch was placed on `/tmp` tmpfs.

The process was interrupted after pytest reported **839 passed, 189 failed,
1 skipped** in the 1,029 cases reached (626.54 seconds; pytest exit `2`). Many
later failures include repeated `OSError: [Errno 28] No space left on device`
after the 6 GiB tmpfs filled. POSIX mode/filesystem-sensitive assertions also
ran against the Windows-mounted source tree. Separately, image-contract testing
found a real stale input pin for `offline_vm_guest_worker.py`. After that value
was corrected, a focused rerun exposed a second stale pin for
`offline_vm_worker.py`. Both are source-contract defects, distinct from the
invalid `/mnt/c` full-suite invocation. The LUNA implementation refreshed both
values; an independent 20-entry worktree audit now reports zero mismatches,
and its Windows closure selection reports 7 passed and 2 skipped (Windows
symlink-only skips). These pins match the current modified worktree and do not
independently establish upstream source provenance. Attempt 12 is being
prepared to rerun the full suite from the accepted POSIX snapshot. No final
1,391-case report exists. The remaining failures from this invocation cannot
distinguish product behavior from the invalid source filesystem and exhausted
test scratch.

The 204-entry current source/test manifest matched before and after this
invocation; the run changed no source or test files. The correct full-suite
follow-up is the attempt-12 recording of the attempt-10 snapshot procedure,
which copies the exact current tree into `/run/user/1000` and preserves Git
file modes before pytest runs. It uses run-specific pytest scratch isolated
from prior attempts.

The combined pytest log, partial JUnit report, command, environment, exit code,
pre-run source manifest, and manifest checks are retained here. No firmware,
hardware, SPI, recovery, or boot operation occurred. The incomplete result
does not supersede the previously completed Ubuntu run or the current focused
Windows/Linux regression receipts.
