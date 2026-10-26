# Focused Windows offline-worker portability regression — attempt 04

After SOL's review identified that a pure protocol path was also being used
for Linux filesystem operations, the mount representation was split: fixed
guest mount names remain literal POSIX strings, while filesystem roots remain
concrete `Path` values. The collection core now accepts an internal explicit
source root for tests; its production wrapper always supplies the fixed
`SOURCE_MOUNT`.

The focused current-source Windows selection passed **6 tests, skipped 2,
failed 0** in 3.88 seconds (pytest exit `0`). It includes a test that leaves
`SOURCE_MOUNT` unpatched and verifies that production constant remains a
concrete `Path`. The test calls the shared internal collector with an explicit
temporary source root and exercises profile validation, package-version
collection, and evidence collection there. It does not invoke the production
wrapper against a real `/mnt/librecore-source` mount. The two skips remain the
narrowly scoped POSIX durability and Linux worker-launch checks.

This run used Python 3.12.14 and pytest 9.1.1 on Windows 10.0.19044. Its
204-path source/test manifest matches the post-run check. The command,
environment, JUnit report, output, and checksums are retained here. This is
focused software evidence only; the full Windows suite remains unvalidated.
No VM build, firmware write, SPI operation, recovery, or physical boot test
occurred.

The same concrete-path regression was also run under Ubuntu WSL using the
project's recorded Linux virtual environment at
`/home/alex/.venvs/autoloader-v1/bin/python`: **1 passed, 0 failed** in 1.89
seconds (pytest exit `0`). It exercised the Linux filesystem methods in the
shared collector using a temporary source root while leaving the production
`SOURCE_MOUNT` constant unpatched and verifying its concrete `Path` type. It
did not invoke the production wrapper against a real `/mnt/librecore-source`
mount. The first attempted WSL command used system Python, which had no pytest
and collected zero tests; that invocation is retained separately and is not
counted as a test result. The 204-path source manifest also matched after the
successful Linux run.
See `linux-path-regression-attempt-02.command.txt`,
`linux-path-regression-attempt-02.environment.txt`, and its stdout and exit
files for that execution.
