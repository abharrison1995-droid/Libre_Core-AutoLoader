# Current-tree Ubuntu WSL regression — attempt 13 — 2026-10-26

## Status: software regression passed — full suite

This attempt checks the fixture correction accepted by SOL after attempt 12's
seven test failures. The production evidence-mode gate remains unchanged. The
focused selection covers all seven failed attempt-12 cases and the new
end-to-end regression that runs under umask `0002`.

The focused selection passed **8 cases**, including all seven failed cases
from attempt 12 and the new umask `0002` regression. The full POSIX-snapshot
suite collected **1,392 cases: 1,388 passed, 4 skipped, 0 failed** (pytest
exit `0`). Two skips require Windows-only platform behavior, and two
Arch-pacman ownership checks remain explicitly `UNVALIDATED` because trusted
Arch package ownership evidence is unavailable in this Ubuntu WSL run.

The root launcher ran focused tests as unprivileged `alex` and started the
full suite only after they passed. The complete suite ran as `alex`, with
umask `0022`. Pre-test source and POSIX snapshot manifest files had identical
SHA-256 values; the post-test source manifest remained byte-identical to the
pre-test manifest. See `snapshot-manifest-comparison.txt` and
`postrun-source-manifest-comparison.txt` for receipts. The root wrapper
recorded focused and full-suite exit codes as `0`, then verified its exact
scratch paths were absent or removed. See `cleanup.status` and
`unprivileged-cleanup.status` for both cleanup stages.

An earlier shell stopped before the runner because `/run/user/1000` was absent;
it executed no tests. That stop is preserved in `prelaunch-reset.txt` and does
not count as an attempt result.

The full runner's preflights covered `/tmp`, `/run/user/1000`, C: evidence
space, and snapshot archive size. Its environment capture records the test
umask.

No firmware device, SPI programmer, recovery operation, or physical boot test
is involved in this software regression.
