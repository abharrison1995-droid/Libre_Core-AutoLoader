# Native Windows test-sharding diagnostic — attempt 15a — 2026-10-26

## Status: incomplete harness run, not a product failure

This first sharded invocation ran 25 shards, covering **625 cases: 428 passed
and 197 skipped**, then stopped at shard 26. Eight parametrized IDs in that
shard included timestamps generated during collection. A later pytest
collection generated different
timestamps, so pytest rejected those selectors with exit `4` and ran **zero
tests** in the shard. JUnit confirms 0 collected test cases for that rejected
selection. No product assertion failed.

The run's 204-path source/test manifest matched before and after. Its logs,
per-shard JUnit, exits, selected IDs, command, environment, partial aggregate
and checksums are retained here. The task-specific scratch was preserved when
the runner stopped. The subsequent corrected function-selector run in
[`chunked-attempt-02-20261026`](../chunked-attempt-02-20261026/README.md)
reran and accounted for all 1,392 cases: **1,118 passed, 274 skipped, 0
failed, 0 errors**.

No hardware, SPI programmer, firmware write, recovery operation, or boot test
was involved.
