# Native Windows T430 handoff validation checks — attempt 06

## Result

Three current-tree parametrized tests passed: an IVprep return that remains on an incompatible BIOS is not accepted, and unverified 1vyrain post-install identity/BIOS captures consume the approval without recording success. The fixtures are synthetic; no IVprep/1vyrain binary was launched, no media was written, and no firmware or physical device was accessed. Pytest exit, JUnit, full output, environment, command, and source hashes are retained.

This verifies software rejection paths only. Real T430 BIOS compatibility, IVprep handoff, upstream 1vyrain execution, and post-install boot validation remain pending hardware work.
