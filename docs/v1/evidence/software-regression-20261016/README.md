# Software regression evidence — 2026-10-16

## Candidate identity

- Development branch: `codex/v1-development`.
- Linux build/test source archive: `62a53885398595bece51b0a0a9f7b7488e8cb6eb13993102b611b6fdd3b2127e` (SHA-256).
- The Ubuntu ext4 staging checkout is `/home/alex/LibreCore-v1-v4-rc-tests-20261016`. Its `tests/test_phase03_spi_safety.py` SHA-256 is `ac41a631327001a2173406cca6915cee87a8275f2a1c40feab03110859f03d2a`, matching the current workspace file. A SHA-256 comparison of the archive against the current working tree found all **129** `autoloader/**`, `usb_builder/**`, `pyproject.toml`, and `autoloader.spec` production/package files present and identical. Documentation and test files are outside that production-source comparison.

## Windows full suite

- Environment: Windows, Python 3.12.14, pytest 9.1.1.
- Command: `python -m pytest -q --basetemp %TEMP%\lc-v1-windows-full-20261016 --junitxml current-v4-windows-full-suite-short-20261016.xml` from the current checkout. The interpreter was the isolated venv at `%TEMP%\LibreCore-v1-test-20261013\Scripts\python.exe`.
- Result: **693 passed, 45 skipped, 0 failures, 0 errors** (738 collected; 737.64 seconds; exit 0).
- An earlier run used a long `Downloads` temp path and produced path-dependent failures (19 Windows path-length failures and one system-temp expectation). That run was discarded; two diagnostics passed with the short system-temp location before the full rerun recorded here.
- Skips cover POSIX-only filesystem durability, symlink, permission, process-group, Linux namespace/device, and Archiso host-ownership contracts unavailable on Windows. They are not passed gates.
- JUnit report: [`windows-pytest.xml`](windows-pytest.xml), SHA-256 `0237ba344b6aa060511acadb1a4c5a6bf91c318a1292af68925651b268caf986`.
- Full-project `mypy --no-incremental --cache-dir=%TEMP%\lc-v1-mypy-final-20261016 autoloader usb_builder`: **no issues in 106 source files**.

## Ubuntu WSL full suite

- Environment: Ubuntu 26.04 on WSL2 (`6.18.54.1-microsoft-standard-WSL2`), Python 3.14.4, pytest 9.1.1, source staged on WSL ext4.
- Full suite result: **734 passed, 4 skipped, 0 failures, 0 errors** (738 collected; 1510.50 seconds; exit 0).
- Skips: two real-host Arch pacman ownership checks (the required trusted Arch package owner is absent), one Windows-specific platform contract, and one Windows-specific durability-policy contract.
- JUnit report: [`ubuntu-wsl-pytest.xml`](ubuntu-wsl-pytest.xml), SHA-256 `6cfa85e69d8d990d5cfa3d031e773ee7e3a62e79e14f4db61fb282a9298a4d19`.
- Full-project `mypy --no-incremental --cache-dir=/dev/shm/librecore-v1-mypy-final autoloader usb_builder`: **no issues in 106 source files**.
- `python -m autoloader --help` succeeded in the candidate venv.

Both suites include the exact-period SPI safety fixture optimization. Focused `tests/test_phase03_spi_safety.py` verification passed **21 tests in 25.65 seconds**. The optimization changes test data generation only; it does not alter production safety behavior.

## Current-commit Ubuntu WSL full suite — 2026-10-16

- Commit under test: `6cabb23d`.
- Command: `wsl.exe -d Ubuntu -- env TMPDIR=/dev/shm /home/alex/librecore-v1-test-venv/bin/python -m pytest -q --basetemp=/dev/shm/lc-v1-release-full-20261016 --override-ini=tmp_path_retention_policy=failed --junitxml=docs/v1/evidence/software-regression-20261016/ubuntu-post-reboot-full.xml`.
- Result: **755 passed, 4 skipped, 0 failures, 0 errors** (759 collected; 1108.07 seconds; exit 0). The JUnit XML records 1107.900 seconds, 0 failures/errors, and 4 skips.
- Exact skip accounting: two tests report `UNVALIDATED: host lacks trusted Arch pacman executable ownership evidence`; one is the Windows-specific host platform contract; one is the Windows-specific production durability policy. The two Arch trust checks are unvalidated, not passing Linux checks.
- JUnit report: [`ubuntu-post-reboot-full.xml`](ubuntu-post-reboot-full.xml), SHA-256 `5f6785a50da796f4977365d9f7d12345512bdb24ce7718ea307b6186aa89073c`.
- Passing temporary directories were removed by the requested retention policy; `/dev/shm` returned to about 43 MiB free. This run is software-only and reports no firmware, ISO, or hardware success.
- The earlier Windows full suite (693 passed, 45 skipped; documented above) predates commit `6cabb23d`; it is not evidence for that commit. Full-project mypy passed on 108 source files after the final guided UI change.

## Current-commit Windows full suite — 2026-10-16

- Commit under test: `6cabb23d`.
- Result: **699 passed, 60 skipped, 0 failures, 0 errors** (759 collected; 770.94 seconds; exit 0). The JUnit XML records 770.928 seconds, 0 failures/errors and 60 skips.
- Skips include POSIX directory-fsync, ownership and process semantics, Linux-only namespace/device checks, and several POSIX symlink-contract tests. One separate symlink-creation test was skipped because this Windows account lacks the required privilege (`WinError 1314`). These skipped checks are not passes on Windows.
- JUnit report: [`windows-post-reboot-full.xml`](windows-post-reboot-full.xml), SHA-256 `e743f5be50e6432e60bc0c145eda9aeb8bf11cf017ab031c551ddffa5f15dce4`.
- Per-test temporary-directory cleanup was enabled; the C: drive remained above 2.3 GiB free.
- Windows-targeted full-project `mypy --platform win32` passed on **108 source files**. Together with the Ubuntu result above, this is software-only evidence; WSL is not native Linux and neither result establishes an ISO build, boot, or hardware success.

## Current-commit Arch WSL full suite — 2026-10-16

- Source HEAD under test: `72a302b`.
- Environment: Arch Linux on WSL2, not native Linux.
- Command: `wsl -d archlinux --cd /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader -- env TMPDIR=/dev/shm python -m pytest -q --basetemp=/dev/shm/lc-v1-release-full-arch-20261016 --override-ini=tmp_path_retention_policy=failed --junitxml=docs/v1/evidence/software-regression-20261016/arch-post-reboot-full.xml`.
- Result: **756 passed, 3 skipped, 0 failures, 0 errors** (759 collected; 1161.62 seconds; exit 0). The JUnit XML records 1161.431 seconds, 0 failures/errors, and 3 skips.
- Exact skips: `Windows-specific production durability policy`; `UNVALIDATED: test requires a non-root host account so host-root ownership is unmapped`; `Windows-specific host platform contract`. The host-root ownership case is unvalidated, not a passing Linux check.
- JUnit report: [`arch-post-reboot-full.xml`](arch-post-reboot-full.xml), SHA-256 `C6086D980C67643FCE5A53A29EACB797BE0BDE9730663A3C950BB0E6DC5A6AF5`.
- Full-project `mypy --no-incremental` passed on **108 source files** in Arch WSL.
- This Arch WSL run is for commit `72a302b`. The Windows and Ubuntu WSL reports above are for the earlier commit `6cabb23d`; they do not cover `72a302b`. WSL results are not native Linux, VM, ISO, ROM, boot, or hardware validation.

`git diff --check` passed after the runs. Git printed only the existing Windows LF-to-CRLF working-copy notices. These suites and type checks are software validation. They do not establish native Linux, firmware build, SPI, Live USB boot, or physical hardware success.

## Current Archiso candidate build observation

- QEMU 11.1.2, TCG (`thread=multi`), Arch ISO `2025.08.01`, 8 GiB guest RAM, six vCPUs. No physical host was connected.
- Input archive SHA-256: `62a53885398595bece51b0a0a9f7b7488e8cb6eb13993102b611b6fdd3b2127e`; candidate directory in the guest: `/home/fwbuild/build-candidate-v4-20261016`.
- The paused guest was resumed under the same TCG process. HMP reported `VM status: running` at 2026-10-16 14:53:41 +01:00. At that observation, the qcow overlay was 2,895,577,088 bytes and host `C:` had 3,682,279,424 bytes free.
- A VGA screen capture at 14:55:12 showed `/tmp/run-online-build-v4rootless-r2.sh` still active and the same Linux blocked-task warnings for `jbd2/vda-8` and `python`, each blocked for over 122 seconds. In the 91 seconds between host observations, overlay size grew by about 382 MB and free space fell to 3,300,372,480 bytes; at 14:55:36 it had fallen to 3,243,921,408 bytes. This activity does not establish useful build progress. The capture is [`qemu-screen-before-pause.png`](qemu-screen-before-pause.png), SHA-256 `83f77d6dc2c5573a316f3919f8ec256f70f1fba118557fbac42e1b85268161d9`.
- Following SOL's recommendation, HMP `stop` paused the same TCG guest at 14:55:36, before the 2 GiB reserve. The overlay remains in place; no snapshot, conversion, accelerator change, reboot, or guest command was issued. No completed build log, ISO, or target-root check was verified.
- **Status: INCOMPLETE — NOT A BUILD PASS.** ISO creation, package-integrity checks, BIOS/UEFI boot to AutoLoader and service readiness remain open. Resume only after a storage-safe plan is available.

## Hardware status

No device identity, SPI probe, measured voltage, backup, write, read-back, recovery action, boot, or functional check occurred in these runs. The requested hardware workflows remain **HARDWARE PENDING** and were deferred until software and VM gates are as complete as possible.
