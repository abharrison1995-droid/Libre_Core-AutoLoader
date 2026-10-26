# Shared CLI/Textual controller regression — 2026-10-26

## Scope

This focused native Windows selection exercises the application-facing
`ApplicationController` used by both Click commands and Textual screens. It
covers common action/status routing, T430 stage handoff, typed X200/X220
workflow submission, CLI live-setup/write routing, fail-closed T430/readback
routes, and the dedicated mounted Textual write challenge. Cancellation and
unmount cases assert that no writer call occurs.

## Results

The initial nine-test invocation is retained at this directory root. It
reported **6 passed, 1 skipped, 2 failed**. Both failures were fixture setup
errors: Git for Windows rejected a synthetic Coreboot tree path because its
nested `.git/hooks/applypatch-msg.sample` path exceeded the Windows path limit.
The log and JUnit report preserve the complete failure output; no production
write route was reached by those tests.

The complete nine-test selection was rerun from a short isolated pytest base
(`C:\lc3`) and passed **8 tests with 1 skip** in 30.14 seconds. The skipped
mounted-Textual approval test requires POSIX directory-fsync support, so it is
intentionally unavailable on Windows. The two previously failing tests both
passed in this run. An earlier two-test short-path rerun (`C:\lcawt`) is also
retained in `short-path-rerun/`.

These tests use synthetic evidence and monkeypatched services. They do not
access SPI hardware, modify firmware, prove a physical write, or validate
device recovery/boot. The full Windows regression gate remains open.

## Files

- `command.txt`, `stdout.txt`, `exit.txt`, `junit.xml`: initial invocation.
- `short-path-full-rerun/`: the complete selection with a short temp root.
- `short-path-rerun/`: the earlier two-test short-path rerun.
- `SHA256SUMS`: hashes of evidence files, excluding the checksum file itself.
