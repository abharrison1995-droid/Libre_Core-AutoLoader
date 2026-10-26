# Initial full regression attempt before `/proc` test-race fix — 2026-10-25

This retained first full-suite attempt reported **1,334 passed, 5 skipped, and
1 failed** in **881.65 seconds**. JUnit records 1,340 cases, one failure, and
five skips. This is not a passing regression result; the successful rerun is
documented in
[`../direct-kernel-contract-followup-rerun-20261025/`](../direct-kernel-contract-followup-rerun-20261025/README.md).

The sole failure was
`tests/test_usb_builder.py::test_network_probe_timeout_kills_grandchild_after_leader_exits_on_sigterm`.
The fixture confirmed the child command timed out and its process group was
terminated. While the test inspected the killed grandchild's `/proc/<pid>/stat`,
the proc entry disappeared between lookup and read, raising `ProcessLookupError`
from the test's liveness helper. The helper was updated to treat that race as a
dead process and a synthetic regression was added. The test passed 10 repeated
runs; the full builder test module passed **370 passed, 3 skipped**, and the
subsequent full rerun passed **1,336 passed, 5 skipped**.

The run used Ubuntu WSL 2, Python 3.14.4, pytest 9.0.2, Textual 2.1.2, the
normal `alex` account/groups, and umask `0022`. Bytecode and pytest cache were
disabled; `TMPDIR` and pytest's `basetemp` were inside the C:-backed WSL VHDX.
The source-file hashes for this pre-fix attempt are in
[`source-files-failed-run.sha256`](source-files-failed-run.sha256).

This failed software run exercised no QEMU worker, firmware build, physical
device, firmware writer, SPI programmer, or recovery procedure.

[`SHA256SUMS`](SHA256SUMS) covers this report and all retained first-attempt
inputs, environment, source hashes, logs, and JUnit output.
