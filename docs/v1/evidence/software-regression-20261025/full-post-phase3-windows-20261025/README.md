# Windows host regression — 2026-10-25

This folder records Windows-specific validation for the post-Phase-3 source.

## Full-suite attempt — interrupted, not a result

The current full Windows pytest suite was started with the repository's
`.venv` (Python 3.12.14, pytest 9.1.1). C: fell to approximately 1.6 MiB free
while pytest was still running. The process was interrupted to protect the
system drive. The wrapper did not reach its log/JUnit/exit-code write step, so
there is no completed Windows full-suite result. Its dedicated temp tree was
verified under `%TEMP%` and removed without following reparse points. This
attempt is not counted as a pass or fail.

## Windows-specific checks

A first targeted run used a longer `%TEMP%` base and produced one pass plus a
`WinError 206` path-length error in the Windows directory-fsync policy fixture.
That diagnostic is retained in `targeted.log`, `targeted.exit`, and
`targeted.junit.xml`.

Both Windows-specific tests were rerun with a short `C:\w` pytest temp root:

- `test_windows_directory_fsync_policy_keeps_approval_closed`
- `test_real_build_usb_cli_fails_before_subprocess_on_windows`

Result: **2 passed, 0 failed**, exit `0`, in 5.80 seconds. Artifacts are
`targeted-clean.log`, `targeted-clean.exit`, and `targeted-clean.junit.xml`.
The first check confirms the write-approval path remains closed on Windows;
the second confirms the unsupported production USB build route fails before
subprocess launch. These are software checks, not firmware or hardware tests.

The full Windows suite remains unvalidated. The WSL full suite is recorded in
`../full-post-phase3-20261025`.

SHA-256 values for the successful targeted captures:

| Artifact | SHA-256 |
| --- | --- |
| `environment.txt` | `3ED399B0BB44538CC6E38ABD43311BE60A1359C663F2DA3464DA206F322841E1` |
| `targeted-clean.exit` | `13BF7B3039C63BF5A50491FA3CFD8EB4E699D1BA1436315AEF9CBE5711530354` |
| `targeted-clean.junit.xml` | `BAB0D4DD99162955D7D497B55CFFB4E5DB99535A10E52A08C53D3D49F24D5183` |
| `targeted-clean.log` | `60C82375BFFB4CCE0B6FB25562A18DBDAD04290C50B2D54906E49903EFD9E573` |
