# Exact-tree Ubuntu WSL regression — 2026-10-25

## Result

Pytest completed successfully against the captured source snapshot: **1,378
passed, 4 skipped, 0 failed, 0 errors** out of 1,382 cases. Pytest exited `0`
after **763.45 seconds** (12 minutes 43 seconds). The JUnit report and full
verbose output are retained alongside this record.

The outer runner returned `1` only after pytest completed, while removing
temporary test files created with root ownership. The cleanup reported
permission-denied messages. Those files were in WSL's volatile `/tmp` and
`/run`; both paths were absent after WSL stopped. `pytest.exit` records `0`,
the JUnit report records zero failures and errors, and the runner reached its
post-run source-integrity comparison without producing a mutation report.
This teardown issue does not change the pytest result.

## Source and environment

- Repository HEAD: `2e28a81b11e6a4fe97472ed0dadb8ea71429127b`.
- A SHA-256 source manifest was captured before the run for the importable
  package, test sources, builder sources, and the referenced test fixtures.
  The runner verified the temporary source snapshot against that manifest
  before pytest and rehashed the snapshot after pytest. No source-mutation
  report was produced.
- `git-status.txt`, `worktree.patch`, and `untracked-files.txt` capture the
  worktree state at launch. `git-index-modes.z` and `snapshot-modes.txt`
  record Linux source-file modes used for the run.
- Ubuntu 26.04.1 WSL 2, Python 3.14.4, pytest 9.1.1, Textual 8.2.8, and
  mypy 2.4.0. The interpreter was
  `/home/alex/.venvs/autoloader-v1/bin/python`.
- `/tmp` was a 7 GiB volatile tmpfs. Test temporary data stayed there. The
  runner's full test command is in `command.txt`; environment and package
  versions are in `environment.txt`.

## Skipped tests and limits

Two skips are Windows-specific contracts; the separate Windows regression
record reports **2 passed**. Two Arch pacman preflights remain skipped and
`UNVALIDATED` because trusted Arch pacman executable-ownership evidence is
unavailable. The exact skip reasons appear in `pytest.log`.

This run is software-only. It does not establish authenticated Arch package
acquisition, a deployed worker, a native guest/image build, a product ISO or
ROM build, firmware boot, or any hardware workflow. The T430 1vyrain, X200
Libreboot, and X220 Coreboot physical workflows remain **HARDWARE PENDING**.
No firmware write, recovery action, or physical device access occurred.

## Captures

- `junit.xml`: 1,382 cases; 1,378 passed, 4 skipped, zero failures/errors.
- `pytest.exit`: pytest exit status `0`.
- `pytest.log`: complete verbose run and pytest summary.
- `source-manifest.sha256`: source hashes captured before the run.
- `git-status.txt`, `worktree.patch`, `untracked-files.txt`: launch worktree
  provenance.
- `environment.txt`, `command.txt`, `tmpfs-size-kib.txt`: runtime and command.
- `run-exact-regression.sh`: exact runner used for this attempt.
