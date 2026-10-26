# Native Windows full-suite retry — interrupted — 2026-10-26

This was a second current-tree native Windows attempt to run the complete
pytest suite. It is **not a completed test result** and is not counted as a
pass or as a product failure report.

The run used Python 3.12.14 and pytest 9.1.1 on Windows 10.0.19044 at commit
`2e28a81b11e6a4fe97472ed0dadb8ea71429127b`. It started with
5,449,564,160 bytes free on C:. `TEMP`, `TMP`, `TMPDIR`, and pytest's explicit
`--basetemp` were directed to a project-specific compressed directory under
`C:\w\lc-v1wfr-20261026`. A 2 GiB free-space guard terminated the process
after 225.82 seconds when C: reached 880,734,208 bytes free. Pytest exited 1.

The partial progress output reached 31% and showed test failure markers before
the guard stopped the process. There is no JUnit report, complete collection,
or final pytest summary. These partial markers cannot establish which tests
failed or whether failures were product, fixture, or resource-related. The
Windows full suite remains **UNVALIDATED**. The two Windows-only focused
platform cases passed separately in [attempt 12](../attempt-12/README.md).

The exact command, environment, stdout/stderr, stop reason, exit status,
pre-run source manifest, and commit are captured here. The post-run check
script compares all 204 recorded source and test paths against their
pre-run SHA-256 values. `SHA256SUMS` covers the evidence files in this
directory (excluding itself).

## Scratch and disk recovery

The attempt's generated scratch remains at
`C:\w\lc-v1wfr-20261026`; it was preserved and NTFS-compressed after the
guard stop. Its recorded test data and directory structure were not deleted.
Other older pytest-generated fixtures under the user's Temp directory were
also NTFS-compressed without deleting or rewriting their file contents.
Before/after inventories retained file counts and logical byte sizes, and
representative `scratch.raw` hashes matched. C: subsequently recovered to
5,045,014,528 bytes free. This storage recovery does not change the interrupted
test status.

The initial six-test diagnostic reproduced the partial failures. After fixing
the host-dependent protocol mount separators and isolating the two POSIX/Linux
contracts, [attempt 04](focused-six-attempt-04/README.md) passed **6 tests and
skipped 2** on Windows, then passed the collector regression (**1 passed**)
under Ubuntu WSL using a temporary source root. Both runs checked that the
production mount constant remains a concrete `Path`; neither invoked the
production wrapper against a real guest mount. The 204-path source manifest
matched after both successful runs. The skip reasons are specific to POSIX
durable publication and Linux worker launch. This focused pass does not
convert either interrupted full run into a full-suite result.

No firmware build, hardware access, SPI operation, recovery action, or boot
test occurred.
