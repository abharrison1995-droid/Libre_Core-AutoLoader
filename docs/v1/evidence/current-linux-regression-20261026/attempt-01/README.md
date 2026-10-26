# Current-source Ubuntu WSL full regression — 2026-10-26

**Result: PASS — 1,388 passed, 4 skipped, 0 failed, 0 errors** (1,392
JUnit cases; pytest exit 0; 798.83 seconds).

The full current Linux test selection ran from a POSIX snapshot of the exact
captured working tree. The pre-run source manifest, POSIX snapshot manifest,
and post-run source manifest have the same SHA-256:
007b1a7c70c31431a79359777686971689c249adaf6b3945929840bafb7c995b
(204 paths). The current-source Windows attempt-15 manifest also contains the same 204 paths and hashes; the normalized cross-platform comparison reports zero differences. See cross-platform-source-manifest-comparison.txt. The captured repository commit is
2e28a81b11e6a4fe97472ed0dadb8ea71429127b; git-status.txt and
worktree.patch retain the dirty-tree state.

## Environment and run

- Ubuntu 26.04.1 LTS on WSL 2, Python 3.14.4, pytest 9.1.1, unprivileged
  user alex, umask 0022.
- The test ran from /run/user/1000/librecore-autoloader-v1-exact-source-20261026-r17.
- run.sh records the exact source snapshot, command, environment, pytest log,
  JUnit report, exit code, and before/after source manifests.
- The initial /tmp capacity was 3.9 GiB. At 46% progress, test scratch had
  grown to 3.6 GiB. The tmpfs limit was remounted to 6 GiB for this running
  session; no scratch was removed or moved by that remount. The
  runtime capacity record contains the readings.

## Skips

- Two Windows-only platform checks were skipped under Linux.
- Two Arch pacman executable-ownership probes remain UNVALIDATED; this run
  did not authenticate Arch package ownership evidence.

## Scratch review and scope

The runner's unprivileged cleanup receipt reports protected test fixtures
pending cleanup. A post-run check found both exact run-scoped paths absent and
/tmp empty; see cleanup-review.txt. The run did not access hardware, invoke a
physical SPI programmer, perform firmware writes or recovery, build an Arch
worker image, or boot a product Live USB.

This is full software regression evidence for the captured tree. It does not
complete the independent Arch trust gate, authenticated package closure,
production worker deployment, product Live USB build and boot, or any of the
three required real-device hardware workflows.