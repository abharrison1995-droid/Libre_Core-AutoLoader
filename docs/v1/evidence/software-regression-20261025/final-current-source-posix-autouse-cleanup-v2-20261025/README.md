# Captured full software regression — 2026-10-25

The completed Ubuntu WSL 2 run passed **1,378 tests, skipped 4, with 0
failures and 0 errors** in **777.97 seconds**; pytest exited **0**. JUnit
records 1,382 cases: 1,378 passed, 4 skipped, 0 failures, and 0 errors.
This is software-only regression evidence for the working tree based on
`2e28a81b11e6a4fe97472ed0dadb8ea71429127b`; it does not validate a worker,
image, product build, VM boot, or hardware workflow.
The exact worktree diff, source hashes, and full command were not retained.
This is a credible captured run, but not a reproducible exact-tree gate.

## Environment and captures

- Ubuntu WSL 2; Python 3.14.4; pytest 9.1.1; Textual 8.2.8.
- Interpreter: `/home/alex/.venvs/autoloader-v1/bin/python`.
- Test scratch was on Linux `/tmp`.
- The attached `pytest_tmp_cleanup.py` autouse fixture kept each test's own
  `tmp_path` available through teardown, then removed that path after the
  test. For post-test cleanup only, it restored directory write mode before
  removing that test's temporary tree.
- The full invocation string was not separately retained in this bundle.
  The pytest output, JUnit report, and exit file are retained as
  [`pytest.log`](pytest.log), [`pytest.junit.xml`](pytest.junit.xml), and
  [`pytest.exit`](pytest.exit); `pytest.exit` contains `0`.

The four skips are deliberately not counted as passes:

| Test | Result / reason |
|---|---|
| `test_windows_directory_fsync_policy_keeps_approval_closed` | Skipped: Windows-specific production durability policy. |
| `test_real_offline_network_namespace_has_no_external_routes` | `UNVALIDATED`: host lacks trusted Arch pacman executable-ownership evidence. |
| `test_real_production_preflight_reports_mount_gate_after_route_probe` | `UNVALIDATED`: host lacks trusted Arch pacman executable-ownership evidence. |
| `test_real_build_usb_cli_fails_before_subprocess_on_windows` | Skipped: Windows-specific host platform contract. |

The two Windows-only tests were separately run on Windows and passed (**2
passed**); see the [Windows regression record](../full-post-phase3-windows-20261025/README.md).
The two host pacman preflights remain unvalidated.

## Diagnostic attempt excluded from the full result

A partial attempt using C: DrvFS was stopped after C: filled. Its first
failure was the expected owner-only gitconfig check on DrvFS. The focused
same test passed on Linux `/tmp`. That partial attempt is diagnostic only and
is not included in the full-suite count above.

No project application source or test files were changed for this evidence
update. The included `pytest_tmp_cleanup.py` is test-run support used by the
captured run. Worker deployment, package closure, VM/image execution, product
build/boot, and physical hardware gates remain pending.
