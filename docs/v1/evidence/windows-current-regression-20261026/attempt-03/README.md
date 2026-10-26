# Native Windows X220 write-boundary check — attempt 03

## Result

This invocation did not execute the test. Pytest treated the JUnit report path as a test target, collected zero tests, and exited `4`. The malformed PowerShell argument is preserved in `command.txt` and the resulting output is in `pytest.log`; this is a harness error, not a test pass or code failure.

The corrected invocation and actual test result are recorded separately in attempt 04. `source-pins.sha256` records the relevant source and test files at this attempt.
