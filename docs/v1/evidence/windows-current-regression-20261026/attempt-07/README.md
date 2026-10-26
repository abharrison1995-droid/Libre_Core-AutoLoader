# Native Windows focused check — attempt 07 — harness failure

Pytest exited `4` before collecting tests. PowerShell supplied an invalid
`--basetemp` argument, so the attempt exercised no test cases. The console
output and command/environment captures are retained. This is a harness error,
not a test result.
