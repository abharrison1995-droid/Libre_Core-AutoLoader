# Native Windows targeted platform and safety checks — 2026-10-26

This bundle contains focused Windows checks and the complete sharded native
Windows test selection. It does not establish hardware validation.

## Current-source complete Windows selection — attempt 15

The complete 1,392-case Windows selection passed in 61 stable function
shards: **1,118 passed, 274 skipped, 0 failed, 0 errors**. Every shard exited
`0` and its JUnit case count matched the planned selection. The 204-path
source/test manifest is identical before and after. The skips are
predominantly Linux/POSIX contracts. The full-run environment skipped the
wheel smoke because its project virtual environment lacked installed
`setuptools` and `wheel`; a focused native Windows rerun with existing bundled
tools then passed **1 test**. Linux host ownership probes remain
`UNVALIDATED`. See the [wheel-smoke follow-up](wheel-smoke-bundled-runtime-20261026/README.md)
and detailed
[attempt-15 record](chunked-attempt-02-20261026/README.md), including all
per-shard JUnit reports, logs, exits, selectors and checksums.

The first functionally complete attempt used collected parameter node IDs and
covered 625 cases: 428 passed and 197 were skipped. It stopped when
timestamp-bearing IDs changed at the next collection. Pytest rejected those
selectors before running tests in that shard. The corrected stable
function-selector attempt reran all 1,392 cases; the selector failure is not
counted as a product test failure.

## Current-tree Windows-only platform checks — attempt 12 — 2026-10-26

The two tests skipped by the full WSL regression both passed on native Windows
against the current source (**2 passed, 0 failed**; 7.71 seconds; pytest exit
`0`). The test-local Git environment used an empty template directory and
`core.longpaths=true` to keep synthetic nested fixtures independent of
machine-wide Git settings. See the [attempt-12 receipt](attempt-12/README.md).
Attempts 07–11 retain harness and fixture-path failures; none is counted as a
test pass or production assertion failure.

## Current-tree native Windows full-suite retries

Two earlier unsharded current-source suite attempts were interrupted by a C: free-space
guard before pytest could produce a summary or JUnit report. The first attempt
stopped after 31% with 1,917,472,768 bytes free; the
[second attempt](full-suite-retry-20261026/README.md) stopped after 31% with
880,734,208 bytes free. Both are **incomplete and unvalidated**, not pass or
failure results. Partial `F` progress markers are retained but cannot identify
final failures without the full test report. The later attempt-15 sharded
selection completed successfully.

The separate attempt-12 result was the only completed full evidence before
attempt 15; it covered the two Windows-only platform cases. Disk space
was recovered afterward by lossless NTFS compression of existing pytest
scratch; no files were deleted. That does not change either full-suite result.

The six Windows assertions mapped from the partial progress stream now pass or
skip at their intended Linux/POSIX boundary (**6 passed, 2 skipped**) after the
mount-path fix. The same collector regression passed under Ubuntu WSL (**1
passed**) using a temporary source root while checking that the production
mount constant stays a concrete `Path`; it did not exercise an actual guest
mount. See [focused attempt 04](full-suite-retry-20261026/focused-six-attempt-04/README.md)
for both run receipts and the post-run source-manifest check. Attempt 15 above
later completed the full Windows test selection in shards.

## Windows platform checks — attempt 02

`attempt-02/` records **2 passed, 0 failed** in 8.94 seconds, pytest exit `0`,
using Python 3.12.14 and pytest 9.1.1. The checks cover the Windows directory
fsync fail-closed policy and rejection of the real USB build command before a
subprocess launch. The invocation, JUnit result, and selected source hashes are
retained in that directory. This run used commit
`2e28a81b11e6a4fe97472ed0dadb8ea71429127b`; later offline worker/image source
changes mean it is not a full current-tree result.

## Current-tree workflow safety checks — attempts 04–06

Five focused tests passed on native Windows against source files whose hashes
are captured per attempt:

- [Attempt 04](attempt-04/README.md): X220 flash and restore entry points remain
  blocked; no writer or external process is invoked (**1 passed**).
- [Attempt 05](attempt-05/README.md): X200 flash and restore entry points remain
  blocked; no flashrom or build-engine process is invoked (**1 passed**).
- [Attempt 06](attempt-06/README.md): an incompatible IVprep return and
  unverified 1vyrain post-install captures cannot record success (**3 passed**).

Attempt 03 is preserved as a JUnit-path invocation error that collected zero
tests; it is not counted as a pass or code failure. Each attempt directory has
its own `SHA256SUMS` manifest.

## Scope and earlier harness error

The full 1,389-case Ubuntu WSL suite passed on current source in
[attempt 10](../software-regression-20261025/exact-tree-recapture-20261025/attempt-10/README.md).
Attempts 04–06 and 12 validate only the named software safety paths; they do
not prove ROM builds, physical SPI writes, read-back, recovery, or boot testing.
No firmware or physical device was accessed.

The root-level `pytest.log`, `command.txt`, `environment.txt`, and `pytest.exit`
preserve the initial harness error: zero tests were collected because a JUnit
path was passed as a test target. Attempt 02 corrected that invocation. The
short attempt-02 temp tree was `C:\w\v1win08-02` (29,522,611 bytes); that host
had more than 6 GiB free after the run.
