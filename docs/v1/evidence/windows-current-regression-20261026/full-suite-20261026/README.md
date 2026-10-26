# Native Windows full-suite attempt — stopped by disk guard — 2026-10-26

This attempt did not complete and has no test result. Pytest was terminated at
31% after 226.18 seconds when C: reached the configured 2 GiB free-space floor
(1,989,107,712 bytes at detection; 1,917,472,768 bytes after termination).
The partial progress output contains failures, but there is no completed
summary or JUnit report; they cannot be classified or counted as a full-suite
result. The process was no longer present after the process-tree termination.

The run used a compressed pytest basetemp, but pytest recreated that path and
the recreated tree did not retain the compression attribute. Test-side
`tempfile` calls also used the default Local Temp directory. The retained
attempt-specific pytest tree contained 2,164 generated files and 3,677,149,527
logical bytes. Its largest `scratch.raw` fixture had the same SHA-256 before
and after NTFS compression. Compressing that exact generated tree restored C:
to 5,491,126,272 free bytes. No test files were deleted, no source changed,
and no firmware or physical hardware was accessed.

The next native run must put pytest's newly-created basetemp and `TEMP`/`TMP`
under a compressed parent directory. This attempt is an interrupted resource
guard record, not a test pass or completed test failure.
