# Native Windows focused platform checks — attempt 12 — 2026-10-26

Both Windows-only cases skipped by the full WSL run passed on native Windows:
**2 passed, 0 failed** in 7.71 seconds (pytest exit `0`). The tests cover the
directory-fsync approval policy and rejection of the real USB build command
before any subprocess launch.

The run used Python 3.12.14 and pytest 9.1.1 against the current source
manifest and commit `2e28a81b11e6a4fe97472ed0dadb8ea71429127b`. To keep the
synthetic nested Git fixture independent of Windows' installed template and
long-path settings, the process used an empty `GIT_TEMPLATE_DIR` and set
`core.longpaths=true` through Git environment configuration. No persistent Git
configuration was changed.

The exact command, environment, JUnit report, full pytest output, exit, commit,
and source manifest are retained here. This is focused Windows evidence, not a
full native Windows suite. It proves no ROM build, external SPI write,
read-back, recovery, or physical boot. No hardware was accessed.
