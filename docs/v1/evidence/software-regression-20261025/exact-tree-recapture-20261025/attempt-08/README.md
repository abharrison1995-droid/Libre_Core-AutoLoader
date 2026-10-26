# Exact-tree full software regression — attempt 08 — 2026-10-26

## Result

Ubuntu WSL 2 ran the full suite on an exact copy of the current working source tree. Pytest collected **1,389 cases** and completed with **1,385 passed, 4 skipped, 0 failed** in **823.62 seconds**; pytest exit was **0**. The JUnit report records 1,389 tests, 0 failures, 0 errors, and 4 skips. The retained runner performs a post-test manifest comparison and exits `90` on mismatch. The invoked runner and its exit status are bound in [`runner-execution.txt`](runner-execution.txt); its successful in-run comparison is also independently reproduced against the same 204 manifest entries by [`source-manifest-postrun-recheck.sh`](source-manifest-postrun-recheck.sh), with matching manifest digests recorded in `source-manifest-recheck.txt`.

The snapshot is bound to repository HEAD `2e28a81b11e6a4fe97472ed0dadb8ea71429127b`, plus the working-tree and untracked source files captured by `source-manifest.sha256` and the retained patch/status records. The `offline_vm_build.py` row is `f3afd3e97c7e41738fb0d7924b591bddbec3c66fb5b773f89c044f3e523c79a4`, matching the refreshed `INPUT_FILE_PINS` entry.

## Skips

- Two Windows-specific platform cases were skipped on Ubuntu. Their Windows-specific coverage passed separately: **2 passed**, documented in [`../../full-post-phase3-windows-20261025/README.md`](../../full-post-phase3-windows-20261025/README.md).
- Two Arch pacman executable-ownership checks remain `UNVALIDATED` because trusted ownership evidence is not available in this host. These are not counted as hardware or Arch build validation.

## Environment and command

The captured environment is Ubuntu 26.04.1 under WSL 2, Linux `6.18.54.1-microsoft-standard-WSL2`, Python 3.14.4, pytest 9.1.1, and a 7 GiB `/tmp` tmpfs. `command.txt` records the exact invocation; `environment.txt` records platform and installed test tooling. `pytest.log`, `junit.xml`, the source manifest, source snapshot modes, `worktree.patch`, Git status, and exit receipts are retained beside this README.

## Cleanup receipt

Pytest passed and `pytest.exit` is `0`. The runner preserved `cleanup.exit=2`: its unprivileged cleanup could not remove root-owned fixture files created by tests in the private `/tmp/librecore-autoloader-v1-exact-tests-20261026-r5` directory. The runner's `cleanup.log` retains those permission errors. After verifying that the path resolved exactly to that attempt-specific directory under `/tmp` and was not a symlink, WSL root removed only that scratch directory. The original cleanup receipt remains unchanged; [`cleanup-followup.txt`](cleanup-followup.txt) records the subsequent removal. This cleanup issue did not change the pytest result or source-mutation check.

Attempt 07 is retained as a failed pre-pin-refresh run. Its two image closure failures and the focused post-refresh check are documented in [`../../../pending-worker-child-status-20261026/attempt-07-full-suite-status.md`](../../../pending-worker-child-status-20261026/attempt-07-full-suite-status.md). Attempt 08 is software-only evidence: worker deployment, authenticated Arch package closure, native Arch image build/boot, and physical firmware workflows remain pending.