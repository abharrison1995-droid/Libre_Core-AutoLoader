# Full software regression after Phase 3 — 2026-10-25

This is software-only regression evidence. It covers the repository test suite;
it does not validate a live package closure, a worker VM/image, product boot,
firmware ROM writes, hardware read-back, or recovery.

## Ubuntu WSL result

Environment: Ubuntu WSL 2, Python 3.14.4, pytest 9.1.1 from the existing
unprivileged test virtual environment. The exact environment is recorded in
`environment.txt`.

The full suite completed with **1,375 passed, 4 skipped, 0 failed, 0 errors**
(1,379 tests total), exit `0`, in 858.21 seconds. Pytest output, JUnit, and the
true process exit code are retained in `pytest.log`, `pytest.junit.xml`, and
`pytest.exit`.

The four skips are:

- Two Windows-only policy tests. Both were run successfully on the Windows
  host in the targeted run documented in
  [Windows validation](../full-post-phase3-windows-20261025/README.md).
- Two Linux host probes explicitly marked `UNVALIDATED` because the host lacks
  trusted Arch `pacman` executable ownership evidence. They remain unvalidated;
  the package resolver and production guest are still unavailable.

The retained artifacts have these SHA-256 values:

| Artifact | SHA-256 |
| --- | --- |
| `environment.txt` | `A20189992A54FD60B26994C0704DDA686ED91B47A2367E44BAC565982B389511` |
| `pytest.exit` | `9A271F2A916B0B6EE6CECB2426F0B3206EF074578BE55D9BC94F6F3FE3AB86AA` |
| `pytest.junit.xml` | `D0AC0B2849C0DB058034143E5B2448F7E1A03CC50A796A8797AF2BEF311D0978` |
| `pytest.log` | `8681CDF9C8C5D0F9B1F30DAD885A14DA075518E72A6AAEC11A74E93772FDA572` |

## Windows host result

The current full Windows suite was interrupted when C: reached approximately
1.6 MiB free. It has no completed exit/JUnit result and is not counted. The
targeted Windows contracts passed 2/2 using a short pytest temp path. See the
linked Windows report for artifacts and the interrupted-run details.

## Scope and release state

The earlier full-suite run with nine failures is historical and predates the
accepted Phase 1–3 fixes. This run clears those software regressions. The two
Arch pacman host probes, pinned `vercmp` identity, authenticated package
closure, worker VM/image execution, and all representative hardware workflows
remain open. No package download, firmware write, hardware recovery, or boot
test was performed.
