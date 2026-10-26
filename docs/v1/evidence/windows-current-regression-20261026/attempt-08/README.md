# Native Windows focused check — attempt 08 — fixture path failure

Pytest collected the two Windows-only tests. One passed; the durability-policy
test failed while `_write_fake_generated_coreboot_tree` wrote a deeply nested
synthetic file beneath the long evidence-directory `basetemp`. The path ended
in `FileNotFoundError`; production behavior was not reached. The complete
failure, JUnit, command, environment, and source manifest are retained.

The run is preserved as a fixture-path failure. It does not count as a passing
or failing production safety assertion.

`SHA256SUMS` covers the top-level receipts and all 11 fixture files retained
under `basetemp/`.
