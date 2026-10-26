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

## Earlier safety-test regression after journal test cleanup — 2026-10-17

These full suites ran after the final current test edits, including removal of
a tautological writer-call assertion from the journal initial-persist test.
The test change only narrows the assertion/comment; no product code changed.
The Windows and Ubuntu results below supersede earlier full-suite counts for
this safety-test source snapshot. Earlier failed Windows attempts remain
preserved in their separate JUnit files and are not treated as passes.

### Windows

Windows Python 3.12.14 / pytest 9.1.1. The run set process-local `TEMP` and
`TMP` to a short user-profile directory to avoid the documented Windows path
length limit:

```powershell
$env:TEMP = 'C:\Users\Alex\Desktop\lcw19'
$env:TMP = $env:TEMP
& "$env:TEMP\LibreCore-v1-test-20261013\Scripts\python.exe" -m pytest -q --basetemp "$env:TEMP\pytest" --override-ini=tmp_path_retention_policy=failed --junitxml=docs/v1/evidence/software-regression-20261017/windows-safety-coverage-final.xml
```

Result: **816 passed, 63 skipped, 0 failures, 0 errors** (879 collected;
860.18 seconds; exit 0). JUnit SHA-256:
`89A4D47283023192F7DD7014BE88672EF1650E9DE9196F6705BE7E3D74CDBB22`.
Skips include POSIX directory-fsync/durability, process, ownership and symlink
semantics; Linux namespace/device/pacman checks; and one account-specific
symlink-creation limitation (`WinError 1314`). They remain unpassed.

### Ubuntu WSL

Ubuntu 26.04 WSL2, using the existing test environment and `/dev/shm` for
temporary files:

```sh
wsl.exe -d Ubuntu --cd /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader -- env TMPDIR=/dev/shm PYTHONDONTWRITEBYTECODE=1 /home/alex/librecore-v1-test-venv/bin/python -m pytest -q -p no:cacheprovider --basetemp=/dev/shm/lc-v1-safe-full-ubuntu-final-20261017 --override-ini=tmp_path_retention_policy=failed --junitxml=docs/v1/evidence/software-regression-20261017/ubuntu-safety-coverage-final.xml
```

Result: **875 passed, 4 skipped, 0 failures, 0 errors** (879 collected;
1,099.02 seconds; exit 0). JUnit SHA-256:
`F3ADC49C06932C002D99599F8F1E3E307BA75E55C6F087793C52891D92FF45F4`.
The four skips are two `UNVALIDATED: host lacks trusted Arch pacman
executable ownership evidence` cases, one Windows-specific host contract,
and one Windows-specific production durability policy. The two Arch host
checks remain unvalidated; WSL is not native Linux.

The Windows and Ubuntu suites include the safety tests present at that
checkpoint. The coverage figure there is historical; the later frozen report
is recorded below. Full software suites do not establish native Linux, VM,
ISO, ROM, hardware, or firmware-write validation. Skips remain unpassed.

## Final safety coverage and crash/replay regression — 2026-10-17

These runs include the final fake-only write-service crash/replay tests. They
ran from Git HEAD `fcfd91179a9f8ae42493c5eec624b1454f873583` with working-tree
test changes in `tests/test_phase03_spi_safety.py`,
`tests/test_phase10b_write_service.py`, and `tests/test_workflow_safety.py`.
No production Python or builder code changed during either run.

### Windows

Python 3.12.14 / pytest 9.1.1. An initial attempt used a pytest base outside
Python's system temp root and failed one storage-preflight test because that
test correctly treats the system temp location as nonpersistent. The failed
JUnit is retained at
[`windows-safety-crash-replay-final.xml`](windows-safety-crash-replay-final.xml),
SHA-256 `CF4A176D1B891BC107A6F82036589B8FE07C182F3A12BAE8D1C593571A9538CE`;
it recorded 875 passed, 63 skipped, and one failed test (939 collected), so it
is not a passing run. The corrected run set process-local `TEMP` and `TMP`
to `C:\Users\Alex\Desktop\lcwtmp-crash-replay-20261017`, verified
`tempfile.gettempdir()` returned that exact root, and placed `--basetemp`
beneath it at `pytest-final`.

Exact corrected PowerShell command from the repository root:

```powershell
$ErrorActionPreference='Stop'
$repo='C:\Users\Alex\Desktop\Libre_Core-AutoLoader'
$python=Join-Path $env:TEMP 'LibreCore-v1-test-20261013\Scripts\python.exe'
$temp='C:\Users\Alex\Desktop\lcwtmp-crash-replay-20261017'
$base=Join-Path $temp 'pytest-final'
$junit=Join-Path $repo 'docs/v1/evidence/software-regression-20261017/windows-safety-crash-replay-final-pass.xml'
$env:TEMP=$temp; $env:TMP=$temp
$probe=& $python -c 'import tempfile; print(tempfile.gettempdir())'
& $python -m pytest -q -p no:cacheprovider --basetemp=$base --override-ini=tmp_path_retention_policy=failed --junitxml=$junit
```

The temp-root probe printed the selected root exactly. Result: **876 passed,
63 skipped, 0 failures, 0 errors** (939 collected; **934.76 seconds**; exit
0). The JUnit report is
[`windows-safety-crash-replay-final-pass.xml`](windows-safety-crash-replay-final-pass.xml),
SHA-256 `6522F8CCEF1D416574DA88D1430AB35E776C39FDFA6721B3B36CE1008DE70798`.
Skips cover POSIX directory durability, ownership, process and symlink
semantics; Linux namespace/device/package checks; unvalidated host-only
checks; and one account-specific symlink privilege failure (`WinError 1314`).
All skipped tests remain unpassed.

### Ubuntu 26.04 WSL

The fresh temp root was `/tmp/lc-v1-crash-replay-20261017`; before the run,
Python reported `tempfile.gettempdir()` exactly equal to `TMPDIR`. The pytest
base was a new child directory under that root. Exact command:

```sh
wsl.exe -d Ubuntu --cd /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader -- env TMPDIR=/tmp/lc-v1-crash-replay-20261017 PYTHONDONTWRITEBYTECODE=1 /home/alex/librecore-v1-test-venv/bin/python -m pytest -q -p no:cacheprovider --basetemp=/tmp/lc-v1-crash-replay-20261017/pytestbase --override-ini=tmp_path_retention_policy=failed --junitxml=docs/v1/evidence/software-regression-20261017/ubuntu-safety-crash-replay-final.xml
```

Result: **935 passed, 4 skipped, 0 failures, 0 errors** (939 collected;
**1221.90 seconds**; exit 0). The JUnit report is
[`ubuntu-safety-crash-replay-final.xml`](ubuntu-safety-crash-replay-final.xml),
SHA-256 `CD6D7CBABF79BC9417C75C5339351C55C203A6BF0F2BE31A4D220E3AF94EAAE4`.
The exact skips are two `UNVALIDATED: host lacks trusted Arch pacman
executable ownership evidence` checks, one Windows-specific production
durability policy, and one Windows-specific host platform contract. The two
Arch tool-ownership checks remain unvalidated, not passing Linux checks.

The latest accumulated coverage export remains **577/678 branches (85.1%)**
on the fixed six-file critical denominator; this is a software-test metric,
not release acceptance. Windows and Ubuntu WSL results do not establish native
Linux, VM, ISO, ROM, Live USB boot, physical write/read-back, recovery, or
hardware validation. All skips remain unpassed.
