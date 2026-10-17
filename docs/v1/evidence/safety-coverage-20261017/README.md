# Safety software branch-coverage baseline — 2026-10-17

## Scope and result

This is a diagnostic coverage baseline for **14 selected software test
modules**, not the full test suite. The Ubuntu 26.04 WSL run used coverage.py
7.16.2 and completed with **295 passed, 1 skipped** in **961.41 seconds**.
There was no hardware, VM, ISO, ROM, or device validation.

## Exact invocation

Run from the repository root:

```sh
wsl.exe -d Ubuntu --cd /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader -- env TMPDIR=/dev/shm PYTHONDONTWRITEBYTECODE=1 COVERAGE_FILE=/dev/shm/lc-v1-safety-coverage-20261017 /home/alex/librecore-v1-test-venv/bin/python -m coverage run --branch --source=autoloader.workflow,autoloader.flasher.backup,autoloader.flasher.backup_service,autoloader.flasher.readback,autoloader.flasher.recovery,autoloader.flasher.spi -m pytest -q -p no:cacheprovider --basetemp=/dev/shm/lc-v1-safety-coverage-test-20261017 --override-ini=tmp_path_retention_policy=failed tests/test_application_controller.py tests/test_workflow_safety.py tests/test_phase03_spi_safety.py tests/test_phase10a_write_approval.py tests/test_phase10b_write_service.py tests/test_post_reboot_evidence.py tests/test_t430_handoff.py tests/test_x200_libreboot_workflow.py tests/test_x200_mac.py tests/test_x220_coreboot_workflow.py tests/test_orchestrator.py tests/test_local_application_adapter.py tests/test_splitter.py tests/test_flasher.py
```

The aggregate coverage report displays **74% total coverage**: 79% of
statements and 62% of branches (1,295/2,094, or 61.8%). This is
below the specification's recommended **85% meaningful branch coverage
target**. The run is a baseline for deciding where meaningful safety-branch
tests are needed; it is not a release pass.

## Per-file coverage

The percentages below are coverage.py's displayed combined line-and-branch
coverage. Statement and branch hit counts are included to make the result
auditable.

| Source file | Coverage | Statements | Branches |
|---|---:|---:|---:|
| `autoloader/flasher/backup.py` | 70% | 73/94 | 11/26 |
| `autoloader/flasher/backup_service.py` | 73% | 432/551 | 114/194 |
| `autoloader/flasher/readback.py` | 86% | 79/88 | 19/26 |
| `autoloader/flasher/recovery.py` | 80% | 120/144 | 48/66 |
| `autoloader/flasher/spi.py` | 76% | 64/80 | 6/12 |
| `autoloader/workflow/__init__.py` | 100% | 7/7 | 0/0 |
| `autoloader/workflow/authorization.py` | 84% | 93/107 | 32/42 |
| `autoloader/workflow/candidate_evidence.py` | 74% | 77/99 | 28/42 |
| `autoloader/workflow/compatibility.py` | 82% | 349/416 | 210/262 |
| `autoloader/workflow/engine.py` | 90% | 83/89 | 22/28 |
| `autoloader/workflow/journal.py` | 80% | 328/388 | 83/126 |
| `autoloader/workflow/local_application_adapter.py` | 48% | 185/355 | 57/152 |
| `autoloader/workflow/post_reboot.py` | 69% | 355/478 | 142/244 |
| `autoloader/workflow/profiles.py` | 100% | 18/18 | 0/0 |
| `autoloader/workflow/types.py` | 99% | 188/190 | 0/0 |
| `autoloader/workflow/write_approval.py` | 70% | 755/1006 | 185/334 |
| `autoloader/workflow/write_service.py` | 74% | 423/541 | 85/150 |
| `autoloader/workflow/x200_libreboot.py` | 76% | 320/396 | 99/158 |
| `autoloader/workflow/x200_mac.py` | 84% | 160/180 | 42/60 |
| `autoloader/workflow/x220_coreboot.py` | 79% | 365/435 | 112/172 |

## Preserved report and limitations

The JSON report is the preserved artifact:
[`coverage.json`](coverage.json), **1,688,897 bytes**, SHA-256
`7F9F8DE9C3F660A30609A2FE21A4D0B1EEBB3695BBD575B7BED9ECDD36357632`.
`SHA256SUMS` verifies it and this README. The coverage database in `/dev/shm`
is session-local; any stale database left there is local to that session and
is not the preserved artifact. The JSON report is the preserved result.

The result covers only the selected software tests. It does not establish
hardware safety, VM operation, ISO creation or boot, ROM build validity, or
any physical write/read-back/recovery behavior. The one skipped test remains
unpassed. SOL's prioritized follow-up is to address backup capture/reopen,
journal and approval integrity, write-service failure boundaries, and
conflicting identity/topology evidence. Product code was not changed and no
coverage-driven tests were added in this baseline pass.
