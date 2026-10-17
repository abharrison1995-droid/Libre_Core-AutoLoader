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
