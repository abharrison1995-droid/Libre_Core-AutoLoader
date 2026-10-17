# Current-source software regression — 2026-10-17

## Source identity

- Git HEAD: `0522fcb3ba68c32df428b998e6f26c0744a50860`.
- The working tree also contained V1 status/evidence documentation updates and
  `tests/test_live_iso_host_package_context.py` (SHA-256
  `ae412b1cfdaadd37796e2638dbdf92294e10a1c1030c30b4f90be148821005ab`).
- No production Python or USB-builder files were modified by this checkpoint.

## Complete Ubuntu WSL suite

- This full-suite run preceded the `pyproject.toml` test-discovery change
  described below; its 845 tests were collected before that setting was added.
- Environment: Ubuntu 26.04 on WSL2, Python 3.14.4, pytest 9.1.1. The source
  checkout was accessed through `/mnt/c`; temporary data went to `/dev/shm`.
- Exact command:

  ```sh
  wsl.exe -d Ubuntu --cd /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader -- env TMPDIR=/dev/shm PYTHONDONTWRITEBYTECODE=1 /home/alex/librecore-v1-test-venv/bin/python -m pytest -q -p no:cacheprovider --basetemp=/dev/shm/lc-v1-current-full-20261017 --override-ini=tmp_path_retention_policy=failed
  ```

- Result: **839 passed, 6 skipped, 0 failures, 0 errors** (845 collected;
  1,391.29 seconds; exit 0).
- The run includes the host/runtime `archiso` distinction and captured-query
  fail-closed regression. The six skips are not counted as passing checks.

## Complete Windows suite

- Environment: Windows, Python 3.12.14, pytest 9.1.1.
- Exact PowerShell command from the repository root:

  ```powershell
  & "$env:TEMP\LibreCore-v1-test-20261013\Scripts\python.exe" -m pytest -q --junitxml=docs/v1/evidence/software-regression-20261017/windows-pytest.xml
  ```
- Result: **752 passed, 62 skipped, 0 failures, 0 errors** (814 collected;
  789.46 seconds; exit 0).
- JUnit records 814 tests, 62 skips, zero failures/errors, and 779.927 seconds
  of pytest test time. Its SHA-256 is
  `E9965D83D58A9431B661E0FC9F3A403CA7348E74F8B0BC2D1DC6DE85B2EA1F87`.
- Skips cover POSIX durability, process, ownership and symlink semantics;
  Linux namespace, device and pacman checks; POSIX-only host checks that remain
  unvalidated; and one symlink creation failure on this Windows account
  (`WinError 1314`). All 62 skips remain unpassed checks.

## Safety-test follow-up full suites — 2026-10-17

These later suites ran from source HEAD
`24fbd5b733ed6d407e1fe9131bb6cfd1970da66c` with four modified test modules:
`test_phase03_spi_safety.py`, `test_phase10a_write_approval.py`,
`test_phase10b_write_service.py`, and `test_workflow_safety.py`. No product
Python or builder code was changed in this test phase. The full-suite runs
included the new functional safety cases. They preceded the final two
assertion-only strengthening edits; those two affected cases were rerun after
the edits and passed on each platform.

### Windows

Environment: Windows, Python 3.12.14, pytest 9.1.1. Exact command from the
repository root:

```powershell
$bt = Join-Path $env:TEMP 'lcfull17a'
if (Test-Path -LiteralPath $bt) { throw "basetemp already exists: $bt" }
& (Join-Path $env:TEMP 'LibreCore-v1-test-20261013\Scripts\python.exe') -m pytest -q --basetemp $bt --override-ini=tmp_path_retention_policy=failed --junitxml docs/v1/evidence/software-regression-20261017/windows-safety-full.xml
```

Result: **778 passed, 63 skipped, 0 failures, 0 errors** (841 collected;
817.34 seconds; exit 0). JUnit is 143,367 bytes, SHA-256
`7AE75D3D25354140D058429079AF8D9B9359141B425454534B16ACDB553848B9`.
Skipped tests remain unpassed and cover POSIX durability, ownership,
process and symlink behavior; Linux namespace, device and pacman checks;
POSIX-only host checks marked unvalidated; and one account-specific symlink
privilege limitation (WinError 1314).

### Ubuntu WSL

Environment: Ubuntu 26.04 on WSL2, using the existing test virtual environment.
Exact command from the repository root:

```sh
wsl.exe -d Ubuntu --cd /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader -- env TMPDIR=/dev/shm PYTHONDONTWRITEBYTECODE=1 /home/alex/librecore-v1-test-venv/bin/python -m pytest -q -p no:cacheprovider --basetemp=/dev/shm/lc-v1-safety-full-ubuntu-20261017 --override-ini=tmp_path_retention_policy=failed --junitxml=docs/v1/evidence/software-regression-20261017/ubuntu-safety-full.xml
```

Result: **837 passed, 4 skipped, 0 failures, 0 errors** (841 collected;
1,103.95 seconds; exit 0). The JUnit file is 127,699 bytes, SHA-256
`78A69A9EFD4DDE0BB61B846D1D09BE844BD2D01F5F23D5E60E8A915FDC4D070A`.
Its skips were two `UNVALIDATED: host lacks trusted Arch pacman executable
ownership evidence` cases, one Windows-specific production durability case,
and one Windows-specific host contract. All four remain unpassed; the two
Arch tool-ownership checks are still unvalidated on this Ubuntu WSL host.

The focused changed-module suite passed **41 tests and skipped 1 on Windows**
(36.81 seconds) and **42 tests on Ubuntu WSL** (44.24 seconds). After the final
assertion-only edits, the two affected tests passed on Windows (7.49 seconds)
and Ubuntu WSL (8.97 seconds).

## Test discovery and evidence-probe checks

After the Ubuntu full-suite run, `pyproject.toml` was changed to limit default
pytest discovery to `tests/`, so archived Linux guest probes under
`docs/v1/evidence/` are not collected as application tests. SOL accepted this
collection change. Subsequent LUNA Ubuntu WSL checks found 814 default tests
and no evidence-directory test paths; the explicit evidence
probe selection passed **31 tests, skipped 2**. The frozen retry7/retry8
evidence files remained unchanged. These explicit probe results do not
convert their skipped cases or guest diagnostics into product validation.

## Type checks

Both commands ran sequentially under the same Ubuntu WSL interpreter and
source checkout:

```sh
python -m mypy --no-incremental --cache-dir=/dev/shm/lc-v1-current-mypy-20261017 autoloader usb_builder
python -m mypy --platform win32 --no-incremental --cache-dir=/dev/shm/lc-v1-current-mypy-win32-20261017 autoloader usb_builder
```

Both passed with no issues in **109 source files**. The Windows-targeted run
is a static analysis target, not a Windows test run.

## Read-only pinned-source audits

`ArtifactEngine.inspect_source` also passed against existing clean checkouts
in Ubuntu WSL. X200 lbmk at
`/home/alex/librecore-lbmk-x200` matched origin
`https://codeberg.org/libreboot/lbmk.git`, revision
`1cf3181537b8d1fe1df0e91681f700850a3d9bf6`, tree
`0cb9451c6e2e265d3aa5b7e371752d45cab98f53`, and **35** pinned file hashes.
X220 Coreboot at `/home/alex/librecore-coreboot-x220-8b0e34f90e08` matched
origin `https://github.com/coreboot/coreboot.git`, revision
`8b0e34f90e08547cf19e550a43ed72f69c39d6ed`, tree
`1cb70fc2c4a0229fa17991b19cf6916b71f75ba7`, and **6** pinned file hashes.
The check read Git metadata and pinned file bytes only; it created no ROM,
artifact manifest, or build output and is not native Linux VM evidence.

## Limits

The Ubuntu results are WSL software checks; the Windows suite is host software
validation. They do not establish a native Linux VM build, an offline ISO
build, a successful current Live USB BIOS/UEFI app/service boot, an X200/X220
ROM build, or any hardware operation. Skipped tests remain skips. No physical
firmware write, read-back, recovery, or boot test occurred.
