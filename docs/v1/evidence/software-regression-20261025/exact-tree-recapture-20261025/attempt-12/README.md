# Current-tree Ubuntu WSL regression — attempt 12 — 2026-10-26

## Status: failed release gate (software)

The first launch stopped at preflight (`TMPFS_CAPACITY_OR_AVAILABLE_SPACE_TOO_LOW`,
4,020,516 KiB total/available) before writing run artifacts or executing
tests. WSL had reset `/tmp` to its default size after the earlier remount shell
exited. The second launch remounted `/tmp` and ran this script in the same WSL
session. See `preflight-reset.txt` for the first-launch observation.

This is a software regression run only. It takes a POSIX source snapshot of
the exact current checkout, restores Git file modes, verifies the snapshot
against the source manifest, and runs pytest from that snapshot. Attempt 11 is
retained as an interrupted invalid invocation and is not used as a pass or a
failure count for this run.

SOL reviewed and accepted the runner before launch. Its preflight checks total
and available `/tmp` capacity, available `/run/user/1000` snapshot space, and
free space on the evidence filesystem. It rejects existing or symlink scratch
paths, then rechecks that the snapshot archive fits with a 128 MiB reserve.
After capturing the logs and checking that the source snapshot did not change,
the runner removes only its two fixed, run-specific tmpfs paths and records
the cleanup result here. An unexpected ENOSPC or cleanup failure leaves this
attempt incomplete and must be reviewed from its receipts.

Pytest collected 1,391 cases and reported **1,380 passed, 7 failed, 4
skipped** (exit `1`). All seven failures are in
`tests/test_offline_vm_worker.py`; the service returned
`INCOMPLETE_BUILD_SERVICE` with reason `evidence source mode is writable by
group or others`. The five failures expecting a complete service result and
the two follow-on missing-marker assertions all trace to that rejection. The
test log, JUnit, exact command, environment, source manifest, and snapshot
mode/index captures are retained here. This is a real release-blocking result,
not a successful full-suite gate. A LUNA investigation is in progress.

The 20 image input pins were independently audited against current worktree
bytes before launch; all matched. Both recently refreshed worker pins match
the current checkout, but they do not establish upstream source provenance.
The post-run temporary manifest and source-mutation diff are absent. That is
consistent with the runner having completed an equal-manifest check, but no
explicit comparison receipt or outer runner exit receipt was retained, so the
comparison result is not independently proven by this evidence bundle.

`cleanup.status` is a reconstructed fresh-session observation, not a receipt
from the runner's EXIT trap. The trap's permission-denied output while removing
read-only test fixture files was observed but not separately retained. A fresh
WSL session had reset `/tmp` to its default size and both exact run-scoped paths
were absent. This does not prove that the EXIT trap removed them and does not
affect the failed pytest result.

No firmware device, SPI programmer, recovery procedure, or physical boot
operation was used in this attempt.
