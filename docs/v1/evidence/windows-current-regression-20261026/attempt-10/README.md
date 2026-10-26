# Native Windows focused check — attempt 10 — harness failure

Pytest exited `4` without collecting tests because PowerShell passed the
intended JUnit output path as a test path. The console output and invocation
environment are retained. This is a harness error, not a test result.
