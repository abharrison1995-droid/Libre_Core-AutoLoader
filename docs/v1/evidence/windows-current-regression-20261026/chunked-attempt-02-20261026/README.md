# Native Windows current-source regression — sharded attempt 15 — 2026-10-26

## Result

The complete current Windows test selection passed across **61 pytest
processes**: **1,118 passed, 274 skipped, 0 failed, 0 errors** from the same
1,392-case collection used by the current Ubuntu WSL regression. Every shard
checked its expected case count against its JUnit report; all shard exits were
`0`. This was a full sharded test selection, not one pytest process.

The skips are predominantly platform-specific contracts that Windows cannot
exercise, including Linux/POSIX worker publication, ownership and mode checks,
process groups, device nodes, namespace behavior, directory fsync, and symlink
cases that require privileges unavailable to this account. The full-run
environment skipped one wheel smoke because the project virtual environment
lacked installed `setuptools` and `wheel`; a focused native Windows rerun with
existing bundled tools then passed **1 test**. Linux host ownership probes
remain explicitly `UNVALIDATED`; these Windows skips do not turn them into
passes. See the [wheel-smoke follow-up](../wheel-smoke-bundled-runtime-20261026/README.md).

## Reproducibility and disk controls

- Python 3.12.14, pytest 9.1.1, Windows 10.0.19044, commit
  `2e28a81b11e6a4fe97472ed0dadb8ea71429127b`.
- The 1,392 current test node IDs were grouped into stable function selectors.
  Parametrized cases were collected fresh for each shard so timestamp-bearing
  IDs could not make later selections stale.
- Each shard ran with a private pytest temp root and system temp directory.
  Successful shard scratch was removed after its JUnit and exit records were
  saved. The runner stopped below 3 GiB free on C:. The lowest recorded
  post-shard free space was 3,351,629,824 bytes; C: ended at 5,064,740,864
  bytes free.
- A SHA-256 manifest of 204 source and test paths is identical before and
  after the run (`ea6c662f697f48dcf7e80bc29b6ac5eb87065091dd26313d47f92469a1c3a942`).
- `SHA256SUMS` covers the command, environment, source manifests, aggregate
  result, all 61 JUnit reports, logs, exits, and selector lists. Verification
  found no checksum mismatch. The task-specific successful-run scratch root
  is absent.

## Preserved runner diagnostic

The first sharding attempt is retained in the sibling
[`chunked-attempt-20261026`](../chunked-attempt-20261026/) bundle. Its first 25
shards covered 625 cases: 428 passed and 197 were skipped. Shard 26 then
selected timestamp-bearing parametrized IDs from an earlier collection.
Pytest correctly rejected those stale IDs with exit `4` and ran zero tests in
that shard. This was a runner-selection error, not a product test failure. The
function-selector attempt documented here reran and accounted for all 1,392
cases.

This result validates the current Windows test selection only. It does not
validate Linux-only worker execution, authenticated package closure, a native
firmware build, Live USB boot, or any physical hardware workflow. No physical
device, SPI programmer, firmware write, recovery operation, or boot test was
used.
