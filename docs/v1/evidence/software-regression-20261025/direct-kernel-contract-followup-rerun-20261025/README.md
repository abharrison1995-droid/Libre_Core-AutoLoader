# Current-source full regression after direct-kernel worker contract — 2026-10-25

## Result

The Ubuntu WSL 2 full-project suite passed **1,336 tests, skipped 5**, with
zero failures or errors in **851.40 seconds**. Pytest exited `0`; JUnit records
1,341 cases. The run log, exit code, environment, and changed source/test-file
hashes are stored alongside this report.

The source tree was based on commit
`2e28a81b11e6a4fe97472ed0dadb8ea71429127b`. The full suite includes the
worker-image contract, package-database seed parser, direct-kernel boot
contract, sealed-memfd size-bound regression, and the `/proc/<pid>/stat`
process-termination race fix.

The command was:

```text
wsl.exe -d Ubuntu -u alex -- bash -lc 'cd /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader && TMPDIR=/home/alex/librecore-autoloader-v1-vm/full-regression-direct-kernel-rerun-20261025/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONOPTIMIZE=0 PYTHONPATH=/tmp/librecore-autoloader-v1-evidence-20261025-pytest-bootstrap/extracted/usr/lib/python3/dist-packages python3 -B -m pytest -p no:cacheprovider --basetemp=/home/alex/librecore-autoloader-v1-vm/full-regression-direct-kernel-rerun-20261025/pytest --junitxml=docs/v1/evidence/software-regression-20261025/direct-kernel-contract-followup-rerun-20261025/full.junit.xml -q --color=no'
```

The environment was Ubuntu WSL 2, Python 3.14.4, pytest 9.0.2, Textual 2.1.2,
the normal `alex` account/groups, and umask `0022`. Bytecode and pytest cache
were disabled. `TMPDIR` and pytest's `basetemp` used a fresh Linux ext4
directory inside the C:-backed Ubuntu WSL VHDX. The separate physical Linux
partition was not mounted or changed.

## Skipped tests

The five skips remain unpassed:

| Test | Skip reason |
|---|---|
| `test_windows_directory_fsync_policy_keeps_approval_closed` | Windows-specific production durability policy |
| `test_installed_wheel_contains_runbook_accepted_by_readiness_gate` | Full-run environment lacks installed setuptools and wheel build tools |
| `test_real_offline_network_namespace_has_no_external_routes` | Trusted Arch pacman executable ownership evidence is unavailable on this host |
| `test_real_production_preflight_reports_mount_gate_after_route_probe` | Trusted Arch pacman executable ownership evidence is unavailable on this host |
| `test_real_build_usb_cli_fails_before_subprocess_on_windows` | Windows-specific host platform contract |

## Scoped static type check

After the full suite, the pinned WSL mypy 1.19.1 binary checked
`usb_builder/offline_vm_worker.py` and `usb_builder/offline_vm_image.py` and
reported `Success: no issues found in 2 source files`. The full-project native
and Win32 mypy result on 117 files is historical and predates these new source
modules.

## Recovered test race

The immediately preceding full-suite attempt is retained in
[`../direct-kernel-contract-followup/`](../direct-kernel-contract-followup/).
It reported **1,334 passed, 5 skipped, 1 failed**. Its only failure was a
Linux test race: the grandchild had been killed, then its `/proc/<pid>/stat`
entry disappeared between lookup and read, raising `ProcessLookupError` in the
test's liveness helper. The helper now treats both `FileNotFoundError` and
`ProcessLookupError` as a dead process. The grandchild-termination test passed
10 repeated runs, the new race regression passed, and all of
`tests/test_usb_builder.py` passed (**370 passed, 3 skipped**) before this full
rerun.

## Scope

This is software regression evidence only. It does not prove a deployed
worker image, sealed guest build, product ISO, BIOS/UEFI product boot, or any
physical ThinkPad workflow. No QEMU worker launch, firmware write, SPI
operation, recovery action, or physical-device passthrough occurred. The
T430 1vyrain, X200 Libreboot, and X220 Coreboot hardware acceptance gates
remain pending.

[`SHA256SUMS`](SHA256SUMS) covers this README, the environment, source hashes,
pytest/JUnit output, and exit status.
