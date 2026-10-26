# Exact-tree current-source regression — attempt 10 — 2026-10-26

## Result

The exact current source suite completed on Ubuntu WSL 2 with **1,385 passed,
4 skipped, 0 failed, and 0 errors** (1,389 cases; 800.68 seconds; pytest exit
`0`). The JUnit report records 1,389 tests, zero failures/errors, and four
skips. The retained full pytest output lists the skip reasons.

The run used the current working tree based on commit
`2e28a81b11e6a4fe97472ed0dadb8ea71429127b`, Python 3.14.4, pytest 9.1.1,
Textual 8.2.8, mypy 2.4.0, and Ubuntu 26.04.1 under WSL 2. `/tmp` was remounted
to 6 GiB by the outer WSL root invocation; pytest ran as unprivileged user
`alex`. The exact runner, command, environment, complete JUnit and pytest
output, source manifest, and a post-run manifest recheck are retained here.

The four skips were:

| Cases | Reason | Status |
|---|---|---|
| 2 | Windows-specific production durability and host-platform checks | Not run in WSL; separate focused native Windows evidence exists, but the full native Windows suite remains unvalidated |
| 2 | Arch pacman executable ownership preflight | `UNVALIDATED`; trusted ownership evidence is unavailable |

The runner and pytest both exited `0`. A separately captured comparison of all
204 source-manifest entries passed with identical pre-run and post-run SHA-256
manifests. The runner cleanup receipt is `cleanup.exit=2`: unprivileged cleanup
could not remove some root-owned fixture files. After WSL stopped, the two
attempt-specific `/tmp` and `/run/user/1000` paths were checked and both were
absent. The original cleanup receipt is retained unchanged.

Attempt 09 immediately before this run stopped at its 6 GiB tmpfs-size
preflight: only 4,020,512 KiB was available. It ran no pytest cases and is not
a test failure. Attempt 08 remains preserved as the previous completed run.

This is software regression evidence only. It does not prove a worker VM or
Live USB build/boot, firmware image correctness, physical flashing, recovery,
or hardware boot. No firmware device was accessed and no firmware write or
recovery operation was performed. Hardware validation and the complete native
Windows suite remain pending.
