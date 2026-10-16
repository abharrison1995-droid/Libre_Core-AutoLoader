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
