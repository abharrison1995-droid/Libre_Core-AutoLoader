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
unpassed. This is the historical baseline; targeted test additions and the
latest coverage export are recorded below.

## Targeted safety follow-up — 2026-10-17

No production code changed. The initial full 14-module coverage run for this
follow-up completed with **309 passed, 1 skipped** in **1,155.72 seconds**.
The same coverage database was then extended with focused safety cases,
including a 9-test append batch, a 27-test append batch, and a final recovery
manifest-start test (1 passed in 3.89 seconds). The 27-test batch completed in
68.72 seconds. The batch work exercised four test modules:
`tests/test_phase03_spi_safety.py`,
`tests/test_phase10a_write_approval.py`,
`tests/test_phase10b_write_service.py`, and
`tests/test_workflow_safety.py`. The report aggregates execution from those
commands; it is not a fresh isolated run of every listed test module.

The earlier aggregated report remains preserved as
[`coverage-followup.json`](coverage-followup.json) (SHA-256
`4F33E27DD2C770BAF4AB24C526157708C470AB9645D486C4E7394B1BE4107566`). The
latest export is [`coverage-post-additions.json`](coverage-post-additions.json),
SHA-256 `82D7FB274A5B4EE469CBF3738ECC51DB56A9B466E9CA11FAC75088F3B948DD49`.
Its complete covered source inventory is the six frozen critical files below
plus `readback.py`, `recovery.py`, `spi.py`, and the additional workflow files
listed in the report. The frozen critical denominator is exactly:

| Critical source file | Covered branches | Branches | Rate |
|---|---:|---:|---:|
| `autoloader/flasher/backup.py` | 16 | 26 | 61.5% |
| `autoloader/flasher/backup_service.py` | 126 | 194 | 64.9% |
| `autoloader/workflow/compatibility.py` | 214 | 262 | 81.7% |
| `autoloader/workflow/engine.py` | 22 | 28 | 78.6% |
| `autoloader/workflow/authorization.py` | 32 | 42 | 76.2% |
| `autoloader/workflow/journal.py` | 84 | 126 | 66.7% |
| **Frozen critical total** | **494** | **678** | **72.9%** |

The critical target remains **85%** and has not been reached. Separate rates
are `write_approval.py` 186/334 (55.7%), `write_service.py` 93/150 (62.0%),
and `post_reboot.py` 142/244 (58.2%). Across all instrumented source files,
the report records 1,326/2,094 branches (63.3%) and 79.8% statement coverage.

The most recent focused rerun after the final test edits passed **41 tests and
skipped 1 on Windows** in 36.81 seconds, and passed **42 tests on Ubuntu WSL**
in 44.24 seconds. After two assertion-only strengthenings, the affected pair
passed again on Windows (**2 passed, 7.49 seconds**) and Ubuntu WSL
(**2 passed, 8.97 seconds**). These focused results do not replace the full
suite results recorded in the software regression report.

Coverage review found remaining misses across manifest/filesystem error
branches, parser and topology combinations, and journal/approval persistence
and concurrency paths. Some such cases are platform-specific; Windows skips
for POSIX durability, ownership, process and symlink behavior remain
unpassed, and Ubuntu WSL does not establish native Linux behavior. The
legacy `calculate_entropy` helper in `backup.py` is not used by the current
degenerate-dump check and its arithmetic branches are lower priority. No
remaining branch is claimed unreachable. The missed branches remain open for
review; this report makes no claim that the 85% target or hardware gates pass.
