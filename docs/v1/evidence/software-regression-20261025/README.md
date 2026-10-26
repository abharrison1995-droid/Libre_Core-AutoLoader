# Captured full software regression — 2026-10-25

## Latest exact-tree regression — current source — 2026-10-26

The current source suite completed on Ubuntu WSL 2 with **1,385 passed, 4
skipped, 0 failed, and 0 errors** (1,389 cases; pytest exit `0`). The 4 skips
were two Windows-specific checks and two Arch pacman executable-ownership
probes that remain `UNVALIDATED`. All 204 source-manifest entries matched the
post-run recheck. Complete command, environment, receipts, cleanup outcome,
and limitations are in the
[attempt-10 exact-tree record](exact-tree-recapture-20261025/attempt-10/README.md).
Attempt 09 is retained as a tmpfs-size preflight that ran no tests; attempt 08
is a completed run against an earlier source tree. The full native Windows
suite and all physical firmware workflows remain pending.

## Latest captured full software regression — 2026-10-25

The latest completed Ubuntu WSL 2 regression passed **1,378, skipped 4, with
0 failures and 0 errors** in **777.97 seconds**; pytest exit was `0` (1,382
JUnit cases). This run used Python 3.14.4, pytest 9.1.1, Textual 8.2.8, and
Linux `/tmp`, with the retained per-test temporary-path cleanup fixture. See
the [captured full regression record](final-current-source-posix-autouse-cleanup-v2-20261025/README.md)
for environment, captures, and skip reasons. Two Windows-only tests passed
separately (**2 passed**); the two host pacman preflights remain
`UNVALIDATED` for lack of trusted Arch pacman executable-ownership evidence.

This is software-only evidence. Worker deployment, package closure, VM/image
execution, product build/boot, and physical hardware gates remain pending.
The run used a working tree based on HEAD
`2e28a81b11e6a4fe97472ed0dadb8ea71429127b`. The exact worktree diff, source
hashes, and full command were not retained.
It is credible captured evidence, not a reproducible exact-tree gate.

## Earlier post-Phase-3 regression — 2026-10-25 (superseded by the captured full regression above)

Ubuntu WSL 2, Python 3.14.4, pytest 9.1.1: **1,375 passed, 4 skipped, 0 failed, 0 errors** (1,379 JUnit cases; 858.21 seconds; exit `0`). The two Windows-only skips were both run successfully in a targeted Windows-host run (**2 passed**). Two real Arch host probes remain `UNVALIDATED` because this host lacks trusted Arch pacman executable-ownership evidence. They are skips, not passes. Exact commands, captures, skip reasons and hashes are in the [post-Phase-3 full regression record](full-post-phase3-20261025/README.md).

The current full Windows suite was attempted but interrupted when C: fell to approximately 1.6 MiB free; it has no completed result. The two Windows-specific V1 checks passed in a separate short-path targeted run. See the [Windows host record](full-post-phase3-windows-20261025/README.md). The previous 1,362-case full run with nine failures is historical and predates the Phase 1–3 fixes.

This is software-only evidence. The package resolver and authenticated package closure, worker VM/image execution, product ISO build/boot, and all three representative hardware workflows remain pending. No package acquisition, firmware write, hardware recovery, or physical boot was performed.

A later fail-closed resolver follow-up passed **68 focused closure/image tests**
and scoped mypy, with retained JUnit, log, exit files, and hashes. It confirmed
the missing authenticated resolver chain and did not rerun the full suite or
generate a package closure. See the [resolver evidence bundle](../worker-package-seed-policy-20261025/README.md).


## Historical full-suite run after worker-image digest handoff

The newest working-tree run passed **1,293 tests, skipped 5**, with **0
failures and 0 errors** in **965.13 seconds**; pytest exited **0**. JUnit
records 1,298 cases. This run includes the fixed QEMU fw_cfg handoff for the
deployment-pinned worker-image digest and its success, mismatch, missing, and
malformed-value regression cases. Full details, exact environment, changed
source hashes, and all captures are in the
[`fw_cfg follow-up record`](fwcfg-followup/README.md).

The related bounded [QEMU fw_cfg transport diagnostic](../native-qemu-fwcfg-20261025/README.md)
passed separately. It proves the QEMU-to-guest transport of the 64-byte value
only; it is not a deployed worker, guest build, product ISO, boot, or hardware
result.

## Earlier full-suite run after parser/type follow-up

The latest full-suite artifacts are from the working tree after the strict
child-observation parser typing and integer-as-boolean rejection tests. The
run completed with **1,285 passed, 5 skipped, 0 failures, 0 errors** in
**813.16 seconds**; pytest exit **0**. JUnit totals 1,290 tests, 0 failures,
0 errors, and 5 skipped. The retained files are
[`final-full.junit.xml`](final-full.junit.xml) (200,773 bytes; SHA-256
`e365e96cef0fbafa3ef724954c56801a5203cd1774df5f5aca75bc58d160ddd4`),
[`final-full.pytest.log`](final-full.pytest.log) (SHA-256
`0ddde9c84cdcd6ab85d5a675ef3f24feba358d07ad1a5ad97bc6733fad8a0780`), and
[`final-full.pytest.exit`](final-full.pytest.exit) (contents `0`; SHA-256
`9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa`).
The pytest log contains the full output and final summary. The new artifacts
are the current full-suite result; earlier captures below remain preserved as
historical runs.

Environment: Ubuntu WSL 2, Python 3.14.4 system interpreter, pytest 9.0.2,
Textual 2.1.2, user `alex` (UID/GID 1000 with normal supplementary groups),
and `umask 0022`. Pytest and Textual came from the unprivileged extracted
bootstrap at `/tmp/librecore-autoloader-v1-evidence-20261025-pytest-bootstrap/extracted/usr/lib/python3/dist-packages`.
`TMPDIR=/tmp`, `PYTHONDONTWRITEBYTECODE=1`, `PYTHONOPTIMIZE=0`, and
`-p no:cacheprovider` disabled bytecode, optimization, and pytest cache.

The invocation was:

```text
cd /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader && umask 022 && export TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONOPTIMIZE=0 PYTHONPATH=/tmp/librecore-autoloader-v1-evidence-20261025-pytest-bootstrap/extracted/usr/lib/python3/dist-packages && python3 -B -m pytest -p no:cacheprovider --basetemp=/tmp/librecore-autoloader-v1-evidence-20261025-full-final --junitxml=/mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader/docs/v1/evidence/software-regression-20261025/final-full.junit.xml -q --color=no
```

The retained exit file contains `0`; the captured pytest output and JUnit
report both show zero failures and errors.

The five skips were:

| Test | Skip reason |
|---|---|
| `test_windows_directory_fsync_policy_keeps_approval_closed` | Windows-specific production durability policy |
| `test_installed_wheel_contains_runbook_accepted_by_readiness_gate` | The full-suite environment lacked the already-installed setuptools and wheel build tools; a separate smoke run below passed with them available |
| `test_real_offline_network_namespace_has_no_external_routes` | Unvalidated: host lacks trusted Arch pacman executable ownership evidence |
| `test_real_production_preflight_reports_mount_gate_after_route_probe` | Unvalidated: host lacks trusted Arch pacman executable ownership evidence |
| `test_real_build_usb_cli_fails_before_subprocess_on_windows` | Windows-specific host platform contract |

The full-suite wheel test remained skipped for its recorded environment
reason. A separate run of `tests/test_recovery_runbook_package.py` with Python
3.14.4, pytest 9.1.1, setuptools 84.2.1, and wheel 0.48.0 passed **1 test** in
**5.78 seconds**, exit **0**. Its captures are
[`wheel-smoke-final.junit.xml`](wheel-smoke-final.junit.xml),
[`wheel-smoke-final.pytest.log`](wheel-smoke-final.pytest.log), and
[`wheel-smoke-final.pytest.exit`](wheel-smoke-final.pytest.exit). This closes
that narrow wheel-content smoke check only; it does not change the five-skip
count in the full-suite report.

Additional software gates were recaptured after the type follow-up by running
[`rerun-software-gates.sh`](rerun-software-gates.sh) under Ubuntu WSL with the
same extracted pytest dependency path and the temporary mypy 1.19.1
environment. The focused modules `test_offline_vm_worker.py`,
`test_offline_vm_build_executor.py`, `test_offline_vm_evidence.py`, and
`test_offline_vm_evidence_adapter.py` passed **219 tests in 17.38 seconds**.
The focused JUnit, pytest output, and exit status are retained in
[`focused-final.junit.xml`](focused-final.junit.xml),
[`focused-final.pytest.log`](focused-final.pytest.log), and
[`focused-final.pytest.exit`](focused-final.pytest.exit).

Native and `--platform win32` mypy each reported no issues across 117 source
files. Their outputs and exit statuses are retained in
[`mypy-native.log`](mypy-native.log), [`mypy-native.exit`](mypy-native.exit),
[`mypy-win32.log`](mypy-win32.log), and [`mypy-win32.exit`](mypy-win32.exit).
Top-level, `flash`, and `build-usb` CLI help checks exited **0**; their combined
commands, output, and aggregate exit status are in [`cli-help.log`](cli-help.log)
and [`cli-help.exit`](cli-help.exit). The three command groups are separate
from the full-suite aggregate.

## Earlier passing full-suite checkpoint

The prior full-suite run completed with **1,283 passed, 5 skipped, 0
failures, 0 errors** in **795.76 seconds**, exit **0**. It predates the final
type-narrowing and integer-as-boolean regression-test follow-up and is retained
as [`normal-umask-full.junit.xml`](normal-umask-full.junit.xml),
[`normal-umask-full.pytest.log`](normal-umask-full.pytest.log), and
[`normal-umask-full.pytest.exit`](normal-umask-full.pytest.exit). It ran under
Ubuntu WSL, Python 3.14.4, pytest 9.0.2, Textual 2.1.2, user `alex` with
normal supplementary groups, and `umask 0022`. The historical invocation
used `--basetemp=/tmp/librecore-autoloader-v1-evidence-20261025-full-normal`
and wrote its JUnit/log/exit captures under this directory. The newer
final-full artifacts above supersede it as the latest run.

## Preserved failed runs

The first full-suite run with Textual 2.1.2 completed with **1,282 passed,
5 skipped, and 1 failed** in 821.91 seconds, exit 1. Its JUnit/log/exit are
preserved as [`textual-2.1.2-failure.junit.xml`](textual-2.1.2-failure.junit.xml),
[`textual-2.1.2-failure.pytest.log`](textual-2.1.2-failure.pytest.log), and
[`textual-2.1.2-failure.pytest.exit`](textual-2.1.2-failure.pytest.exit). The
failure was `test_welcome_and_app_do_not_detect_implicitly`: Textual 2.1.2
returned a Rich `Text` value for `Button.label`, while the assertion compared
it directly to a Python string. The application UI was unchanged. The test now
reads `label.plain` when available and otherwise compares the string directly:

```python
start_label = app.screen.query_one("#btn-start", Button).label
assert getattr(start_label, "plain", start_label) == "Start Guided Setup"
```

The isolated UI test then passed (**1 passed in 4.63 seconds**, exit 0). The
later full-suite run above passed with this assertion.

A second full run used a login shell whose `umask` was `0002`. It completed
with **1,276 passed, 5 skipped, and 7 failed** in 790.70 seconds, exit 1. The
seven failures were the synthetic fixed-service evidence tests: their normal
file creation produced group-writable sources, and the service correctly
rejected them with `evidence source mode is writable by group or others`.
That run is preserved as [`umask-0002-mode-failure.junit.xml`](umask-0002-mode-failure.junit.xml),
[`umask-0002-mode-failure.pytest.log`](umask-0002-mode-failure.pytest.log), and
[`umask-0002-mode-failure.pytest.exit`](umask-0002-mode-failure.pytest.exit).
The passing 1,283-test rerun used normal WSL account groups and `umask 0022`,
and the later 1,285-test run used the same environment. No safety check was
weakened.

An earlier collection diagnostic without the extracted Textual dependency is
also retained as `collection-missing-textual.*`; it did not execute the test
suite and is not a test result.

## Scope and limits

These are software-only regression results. The separate fw_cfg diagnostic
proves transport of a supplied worker-image hash only. They do not prove a
deployed VM worker, effective guest isolation, guest build, product ISO,
full-ISO or worker boot, or hardware acceptance. `run_one_shot()` remains `BUILD_BACKEND_UNAVAILABLE`,
`PRODUCTION_WORKER_DEPLOYMENT` remains `None`, and `build_usb.execute_build`
remains disconnected. Physical firmware operations and recovery were not run.

`SHA256SUMS` records this README, the recapture script, all retained current
and historical software-regression captures, and the additional
`fwcfg-followup/` evidence bundle.

