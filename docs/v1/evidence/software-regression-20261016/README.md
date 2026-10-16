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

## Earlier root-account Arch WSL full suite — source 72a302b, 2026-10-16

- Source HEAD under test: `72a302b`.
- Environment: Arch Linux on WSL2 running as the root account, not native Linux.
- Command: `wsl -d archlinux --cd /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader -- env TMPDIR=/dev/shm python -m pytest -q --basetemp=/dev/shm/lc-v1-release-full-arch-20261016 --override-ini=tmp_path_retention_policy=failed --junitxml=docs/v1/evidence/software-regression-20261016/arch-post-reboot-full.xml`.
- Result: **756 passed, 3 skipped, 0 failures, 0 errors** (759 collected; 1161.62 seconds; exit 0). The JUnit XML records 1161.431 seconds, 0 failures/errors, and 3 skips.
- Exact skips: `Windows-specific production durability policy`; `UNVALIDATED: test requires a non-root host account so host-root ownership is unmapped`; `Windows-specific host platform contract`. The host-root ownership case is unvalidated, not a passing Linux check.
- JUnit report: [`arch-post-reboot-full.xml`](arch-post-reboot-full.xml), SHA-256 `C6086D980C67643FCE5A53A29EACB797BE0BDE9730663A3C950BB0E6DC5A6AF5`.
- Full-project `mypy --no-incremental` passed on **108 source files** in Arch WSL.
- This run is retained as root-account evidence. Its unmapped-host-ownership skip is expected for that account and does not establish the non-root host contract.

## Arch WSL full suite under setpriv nobody — 2026-10-16

- Tested Python source and tests: `72a302b`. Docs-only commit `0d74d76` landed while the suite was running; Python source and test files remained at `72a302b`.
- Environment: Arch Linux on WSL2, launched from the WSL root account and dropped by `setpriv` to UID/GID 65534 (`nobody`), with no supplementary groups or capabilities and `NoNewPrivs=1`. `PATH=/usr/bin:/bin`; `HOME=/dev/shm`; `TMPDIR=/dev/shm`; bytecode and pytest cache creation disabled. This was not the dedicated `fwbuild` account and did not exercise a privileged offline launcher.
- Exact command:

  ```sh
  wsl -d archlinux --cd /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader -- setpriv --bounding-set=-all --inh-caps=-all --ambient-caps=-all --reuid=65534 --regid=65534 --clear-groups --no-new-privs --reset-env -- env PATH=/usr/bin:/bin HOME=/dev/shm TMPDIR=/dev/shm PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider --basetemp=/dev/shm/lc-v1-arch-nonroot-full-20261016 --override-ini=tmp_path_retention_policy=failed --junitxml=/dev/shm/lc-v1-arch-nonroot-full-20261016.xml
  ```

- Result: **757 passed, 2 skipped, 0 failures, 0 errors** (759 collected; 1245.94 seconds; exit 0). JUnit time is 1245.846 seconds.
- Exact skips: `Windows-specific production durability policy` (`test_windows_directory_fsync_policy_keeps_approval_closed`) and `Windows-specific host platform contract` (`test_real_build_usb_cli_fails_before_subprocess_on_windows`). The previously unvalidated non-root host-ownership case passed in this `nobody`-identity run.
- JUnit report: [`arch-post-reboot-nonroot-full.xml`](arch-post-reboot-nonroot-full.xml), SHA-256 `0d7d33b29e3efd03c62e2aac47ec507935d06dbee3ea972a59f8aaee170f9c07`.
- This remains WSL2 software-test evidence, not native Linux, disposable-VM, package/build, ISO, boot, ROM, or hardware validation. Passing as `nobody` does not validate the fixed launcher, the dedicated build account/subuid setup, or production offline execution.

The Windows and Ubuntu WSL reports above are for the earlier commit `6cabb23d`; they do not cover Python source or tests at `72a302b`.

`git diff --check` passed after the runs. Git printed only the existing Windows LF-to-CRLF working-copy notices. These suites and type checks are software validation. They do not establish native Linux, firmware build, SPI, Live USB boot, or physical hardware success.

## Current Archiso candidate build observation

- QEMU 11.1.2, TCG (`thread=multi`), Arch ISO `2025.08.01`, 8 GiB guest RAM, six vCPUs. No physical host was connected.
- Input archive SHA-256: `62a53885398595bece51b0a0a9f7b7488e8cb6eb13993102b611b6fdd3b2127e`; candidate directory in the guest: `/home/fwbuild/build-candidate-v4-20261016`.
- The paused guest was resumed under the same TCG process. HMP reported `VM status: running` at 2026-10-16 14:53:41 +01:00. At that observation, the qcow overlay was 2,895,577,088 bytes and host `C:` had 3,682,279,424 bytes free.
- A VGA screen capture at 14:55:12 showed `/tmp/run-online-build-v4rootless-r2.sh` still active and the same Linux blocked-task warnings for `jbd2/vda-8` and `python`, each blocked for over 122 seconds. In the 91 seconds between host observations, overlay size grew by about 382 MB and free space fell to 3,300,372,480 bytes; at 14:55:36 it had fallen to 3,243,921,408 bytes. This activity does not establish useful build progress. The capture is [`qemu-screen-before-pause.png`](qemu-screen-before-pause.png), SHA-256 `83f77d6dc2c5573a316f3919f8ec256f70f1fba118557fbac42e1b85268161d9`.
- Following SOL's recommendation, HMP `stop` paused the same TCG guest at 14:55:36, before the 2 GiB reserve. The overlay remains in place; no snapshot, conversion, accelerator change, reboot, or guest command was issued. No completed build log, ISO, or target-root check was verified.
- **Status: INCOMPLETE — NOT A BUILD PASS.** ISO creation, package-integrity checks, BIOS/UEFI boot to AutoLoader and service readiness remain open. Resume only after a storage-safe plan is available.

## Current-source Arch WSL non-root full suite — 2026-10-16

- Source and tests under test: `588a48f05083652f0146edb3c95b7132341b84fe`.
  The four reviewed documentation updates were also present in the working
  tree; Python source and tests were unchanged from this commit.
- Environment: Arch Linux WSL2, launched with `setpriv` as UID/GID 65534
  (`nobody`), without supplementary groups or capabilities and with
  `NoNewPrivs=1`; `PATH=/usr/bin:/bin`, `HOME=/dev/shm`,
  `TMPDIR=/dev/shm`, bytecode and pytest cache disabled. This was not the
  dedicated `fwbuild` account or a native Linux VM.
- Exact command:

  ```sh
  wsl -d archlinux --cd /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader -- setpriv --bounding-set=-all --inh-caps=-all --ambient-caps=-all --reuid=65534 --regid=65534 --clear-groups --no-new-privs --reset-env -- env PATH=/usr/bin:/bin HOME=/dev/shm TMPDIR=/dev/shm PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider --basetemp=/dev/shm/lc-v1-arch-nonroot-full-20261016-rc1 --override-ini=tmp_path_retention_policy=failed --junitxml=/dev/shm/lc-v1-arch-nonroot-full-20261016-rc1.xml
  ```

- Result: **809 passed, 2 skipped, 0 failures, 0 errors** (811 collected;
  console time 1771.18 seconds; JUnit time 1771.094 seconds; exit 0).
- Exact skips: `test_windows_directory_fsync_policy_keeps_approval_closed`
  (Windows-specific production durability policy) and
  `test_real_build_usb_cli_fails_before_subprocess_on_windows` (Windows-
  specific host platform contract). They are not Linux validation gaps.
- JUnit report: [`arch-post-reboot-nonroot-rc1-full.xml`](arch-post-reboot-nonroot-rc1-full.xml),
  SHA-256 `2552199c4ccfa25ef60af5116057cece2d9fcb37dffad4935e060fb5b99da246`.
- The full current-source Python suite passed in this non-root WSL run. It
  does not establish native Linux or full-system VM package/build behavior,
  a Live USB build/boot, ROM build, or any hardware result.

## QEMU host preparation — 2026-10-16

- Installed Ubuntu packages `qemu-system-x86` and `qemu-utils`; QEMU reports
  version 10.2.1, and the binary advertises TCG, MSHV, and KVM accelerators.
  This setup uses TCG unless a guest probe proves another accelerator is
  actually available.
- Downloaded the official Arch 2026.10.01 x86_64 installer to Ubuntu WSL
  `/dev/shm`; its SHA-256 matches the official release value
  `684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5`.
  The volatile copy is temporary and is not a project artifact.
- No VM guest has been booted in this preparation step. C: had 3.54 GiB free,
  below the documented space needed to resume the existing build guest or
  create the full Live USB build workspace. Guest build and ISO gates remain
  open.

## Hardware status

No device identity, SPI probe, measured voltage, backup, write, read-back, recovery action, boot, or functional check occurred in these runs. The requested hardware workflows remain **HARDWARE PENDING** and were deferred until software and VM gates are as complete as possible.
