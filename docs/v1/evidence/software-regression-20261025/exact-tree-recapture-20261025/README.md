# Exact-tree software regression captures

This directory preserves exact-tree software regression attempts against the
development tree. Each attempt has its own immutable captures and
status. Failed preflights and earlier source snapshots remain historical
records; they are not silently replaced by later outcomes.

## Completed baseline — attempt 10 — 2026-10-26

The full Ubuntu WSL 2 suite passed **1,385 tests**, skipped **4**, and had no
failures or errors (1,389 cases; pytest exit `0`). Two Windows-specific checks
and two Arch pacman executable ownership probes were skipped. Pacman ownership
remains `UNVALIDATED`. The 204-entry source manifest matches its post-run
recheck. See the [attempt-10 record](attempt-10/README.md) for environment,
cleanup status, limitations, command, and retained captures.

Subsequent work refreshed two stale source pins in the offline worker image
closure. Attempt 11 is an **invalid, interrupted full-suite run** from `/mnt/c`
with exhausted scratch; its genuine pin findings and the limits of its partial
JUnit report are recorded in [attempt 11](attempt-11/README.md). Attempt 12
captured seven failures because synthetic evidence fixtures inherited
group-writable modes; the production evidence-mode rejection was correct. The
fixture now assigns explicit safe modes and includes an umask `0002`
regression. Attempt 13 passed the focused cases and the full POSIX-snapshot
suite with 1,388 passed, 4 skipped, and no failures. Its source comparison,
runner, and cleanup receipts are in [attempt 13](attempt-13/README.md). Two
Arch pacman ownership probes remain `UNVALIDATED`; worker deployment, package
closure, Live USB build/boot, and physical hardware gates remain pending.

Attempt 09 stopped before running tests because the 6 GiB tmpfs preflight found
only 4,020,512 KiB. Its preflight receipt is retained under `attempt-09/`.

## Earlier records

- [Attempt 11](attempt-11/README.md) is retained as an incomplete and invalid
  environment run, not a product regression result.
- [Attempt 08](attempt-08/README.md) is the previous completed full-suite run on
  an earlier source tree.
- Attempt 07 is a failed pre-pin-refresh run, documented in the
  [worker status record](../../pending-worker-child-status-20261026/attempt-07-full-suite-status.md).
- Attempts 01–06 preserve their original success/failure artifacts and are
  historical checkpoints.

These are software tests only. Worker-image deployment, authenticated package
closure, Live USB build/boot, and physical hardware acceptance are tracked
separately and remain pending.
