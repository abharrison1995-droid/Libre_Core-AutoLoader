# Native Windows X220 write-boundary check — attempt 04

## Result

The current-tree test `test_x220_write_and_restore_entrypoints_are_unconditionally_blocked` passed on Windows. It verifies that X220 flash and restore entry points reject calls and that the workflow does not spawn flashrom or build subprocesses. The test uses synthetic evidence and does not access hardware. Pytest exit is recorded in `pytest.exit`; `junit.xml`, full output, environment, command, and relevant source hashes are retained.

This is a focused software safety check. It does not validate ROM compilation, external SPI writes, read-back, recovery, or booting.
