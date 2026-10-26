# Current-source software regression — 2026-10-20

These reports cover the current working-tree Python sources and tests after
flashrom executable trust hardening, fixed writer-subprocess isolation, and the
deterministic storage-preflight regression correction. They are software test
evidence. They do not establish native-Linux operation, firmware builds, Live
USB behavior, or hardware acceptance.

## Earlier full-suite checkpoint (before the 2026-10-20 runtime follow-up)

| Environment | Result | Duration | JUnit report | SHA-256 |
|---|---:|---:|---|---|
| Windows, Python in repository `.venv` | 890 passed, 63 skipped, 0 failures/errors (953 collected) | 562.40 s | `windows-full-final.xml` | `3F995CC3EF73746750DD1A644444716E0135F433811DC36518FB2C874CB6D65C` |
| Ubuntu 26.04 on WSL2, dedicated Linux venv | 949 passed, 4 skipped, 0 failures/errors (953 collected) | 536.37 s | `ubuntu-wsl-full-final.xml` | `EC026C2FDA6BC722914CEC895693BD8C7B562D6DDB578259505709C7864F717B` |

This earlier checkpoint is retained for chronology and is superseded by the
runtime-follow-up runs below for the current working-tree test count. Neither
checkpoint is native-Linux VM, ISO, firmware, or hardware validation.

## Current-source runtime follow-up

The final full suites included the runtime network-policy and systemd target-
root validation changes. Both collected 979 tests and completed with zero
failures or errors:

| Environment | Result | Duration | JUnit report | SHA-256 |
|---|---:|---:|---|---|
| Windows, repository `.venv`, short pytest `--basetemp` (`C:\q`) | 912 passed, 67 skipped | 568.12 s | `windows-runtime-followup-shortpath.xml` | `681196E31521867BCEDC6D6F6FA423D875EA1134275C7AC11AA95988878A4AE7` |
| Ubuntu 26.04 on WSL2, dedicated Linux venv | 975 passed, 4 skipped | 583.37 s | `ubuntu-wsl-runtime-followup.xml` | `5EE2C1255B92E5D0D1204641C23F4708A6A896A802AF8E8E5621BBDD47CDF013` |

The Windows run passed using pytest `--basetemp C:\q`; process `TEMP` and
`TMP` were not changed. The first
Windows attempt is retained as `windows-runtime-followup.xml` for diagnostic
traceability only: **881 passed, 31 failed, 67 skipped** (979 collected;
671.99 s). Its failures were deep target-root fixture path errors
(`FileNotFoundError`/`WinError 206`) caused by the longer temp root
`C:\Users\Alex\AppData\Local\Temp\librecore-windows-runtime-followup-20261020`;
they are not an accepted suite result. The later short-path run passed all
tests that had failed there. The failed report SHA-256 is
`6AAFB2D1DA82627361AC59882A145D6B1982A8DE59D823B93D4EEC621C9D790C`.

The Windows skips remain unpassed and are host-specific POSIX durability,
process, ownership, symlink, Linux namespace/device/package checks, and the
account-specific symlink privilege case. Ubuntu WSL skipped two trusted Arch
`pacman` ownership checks and two Windows-only contracts. Ubuntu WSL is not a
native Linux VM; neither suite proves an ISO boot or physical hardware result.

Full-project mypy passed on 109 source files in Windows with the Win32 target
and in Ubuntu WSL. Runtime/network and target-root policy tests are included
in these full-suite reports. No product ISO, firmware, or hardware operation
was performed.

The following commands belong to the earlier 953-test checkpoint above; they
are retained for chronology and are not the runtime-follow-up invocations:

```powershell
$bt = Join-Path $env:TEMP 'lc-v1-final-20261020'
if (Test-Path -LiteralPath $bt) { throw "basetemp already exists: $bt" }
New-Item -ItemType Directory -Path $bt | Out-Null
& .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp $bt --override-ini=tmp_path_retention_policy=failed --junitxml=docs/v1/evidence/software-regression-20261020/windows-full-final.xml
```

```sh
wsl -d Ubuntu --cd /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader -- env \
  TMPDIR=/mnt/librecore-partition-readonly/opt/librecore-autoloader-v1-vm/tmp-full-final2-20261020 \
  /home/alex/librecore-v1-test-venv/bin/python -m pytest -q -p no:cacheprovider \
  --basetemp=/mnt/librecore-partition-readonly/opt/librecore-autoloader-v1-vm/pytest-full-final2-20261020 \
  --override-ini=tmp_path_retention_policy=failed \
  --junitxml=docs/v1/evidence/software-regression-20261020/ubuntu-wsl-full-final.xml
```

The WSL suite's temporary files and pytest base were put on the authorized
ext4 workspace because WSL's `/tmp` tmpfs had filled during an earlier,
interrupted run. Only the exact pytest scratch directory created by that
failed run was removed; the interrupted run produced no JUnit report and is
not counted as a result.

The exact runtime-follow-up suite commands, from the repository root, were:

```powershell
$report='docs/v1/evidence/software-regression-20261020/windows-runtime-followup-shortpath.xml'
$bt='C:\q'
New-Item -ItemType Directory -Path $bt | Out-Null
& .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp $bt --override-ini=tmp_path_retention_policy=failed --junitxml=$report
```

Only pytest's `--basetemp` was set to `C:\q`; process `TEMP` and `TMP`
environment variables were not changed.

```sh
wsl -d Ubuntu --cd /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader -- env \
  TMPDIR=/mnt/wsl/PHYSICALDRIVE1p2/opt/librecore-autoloader-v1-vm/tmp-full-runtime-20261020 \
  /home/alex/librecore-v1-test-venv/bin/python -m pytest -q -p no:cacheprovider \
  --basetemp=/mnt/wsl/PHYSICALDRIVE1p2/opt/librecore-autoloader-v1-vm/pytest-full-runtime-20261020 \
  --override-ini=tmp_path_retention_policy=failed \
  --junitxml=docs/v1/evidence/software-regression-20261020/ubuntu-wsl-runtime-followup.xml
```

The Ubuntu WSL temporary directory and pytest base were placed on the approved
ext4 workspace at `/mnt/wsl/PHYSICALDRIVE1p2`; the JUnit report was written
under the repository evidence directory.

For this runtime follow-up, full-project mypy passed on 109 files in native
Windows mode, Win32-targeted Windows mode, and Ubuntu WSL. The commands were:

```powershell
.\.venv\Scripts\python.exe -m mypy --no-incremental --cache-dir="$env:TEMP\mypy-lc-v1-native-followup-20261020" autoloader usb_builder
.\.venv\Scripts\python.exe -m mypy --platform win32 --no-incremental --cache-dir="$env:TEMP\mypy-lc-v1-windows-followup-20261020" autoloader usb_builder
```

```sh
wsl -d Ubuntu --cd /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader -- env \
  TMPDIR=/mnt/wsl/PHYSICALDRIVE1p2/opt/librecore-autoloader-v1-vm/tmp-full-runtime-20261020 \
  /home/alex/librecore-v1-test-venv/bin/python -m mypy --no-incremental \
  --cache-dir=/mnt/wsl/PHYSICALDRIVE1p2/opt/librecore-autoloader-v1-vm/mypy-runtime-followup-20261020 \
  autoloader usb_builder
```

## Skipped checks

Skips are not passes and remain open release evidence.

- **Windows (67):** POSIX directory-fsync and durable-sidecar behavior;
  Linux ownership, symlink, usrmerge, device, `/proc`, process-group and user
  namespace semantics; Archiso/pacman executable ownership; other
  host-specific cases; and one account-specific symlink-creation case denied
  with WinError 1314.
- **Ubuntu WSL (4):** two checks requiring trusted Arch `pacman` ownership
  evidence, unavailable on Ubuntu; and two Windows-only host contracts.

Ubuntu WSL is not a native Linux VM or booted Live USB. Passing its tests does
not close these native platform checks.

## Static typing and focused checks

Full-project mypy succeeded on 109 source files in both environments:

```powershell
.\.venv\Scripts\python.exe -m mypy --platform win32 --no-incremental --cache-dir="$env:TEMP\mypy-librecore-v1-final-20261020" autoloader usb_builder
```

```sh
wsl -d Ubuntu -- /home/alex/librecore-v1-test-venv/bin/python -m mypy \
  --no-incremental \
  --cache-dir=/mnt/librecore-partition-readonly/opt/librecore-autoloader-v1-vm/mypy-cache-final-20261020 \
  autoloader usb_builder
```

The focused flashrom runner isolation regressions passed on Windows and Ubuntu
WSL. The corrected deterministic storage-preflight test passed on both hosts.
The full suites above include these tests. SOL's read-only review accepted
the flashrom trust-hardening software slice; that review is not native Linux
or hardware evidence.

## Current-source package and CLI smoke

The Windows CLI top-level help and guarded `flash --help` both exited 0. The
current working tree also built a wheel using pip's isolated build
environment; it was **397,975 bytes** with SHA-256
`274c85444c4fc425e4a089901a2e75561ed520b98314e33f1479a69127b11378`.
The wheel was installed with `pip --target` into a fresh temporary directory.
After changing the working directory outside the repository, imports resolved
to that isolated target for both `autoloader` and `usb_builder`. This is
packaging/import smoke evidence; it does not prove a firmware build or device
workflow.

## Flashrom package-owner smoke check

Ubuntu WSL had neither `flashrom` nor `flashprog` installed. Accordingly,
`FlashromRunner` returned unavailable for tool lookup and inspection, as
required by its fail-closed policy. A separate read-only smoke check exercised
the production provenance resolver against the canonical `/usr/bin/gnutrue`
executable: it was root-owned, passed the protected mode/digest checks, and
resolved to `dpkg: gnu-coreutils: /usr/bin/gnutrue` with SHA-256
`913a39cd38f353497086bcf317b12f91f93c23b51869cea763b8340b4f84cfd3`.
This verifies the Debian package-owner lookup path with a non-flashrom
executable. It is not flashrom package integration; no flashrom command,
probe, chip read, SPI programmer, or hardware was used.

## Earlier WSL report and correction

An additional retained Windows XML report, `windows-full.xml`, contains 942
test cases, 63 skips, and no recorded failures/errors (SHA-256
`6c04df660d9c794ad4dff163a8bab52f474d69fa76bbcd8c0d4094983b05f4e1`). It has
no matching command or source-revision record, so it is kept for traceability
and excluded from the current-source acceptance result.

The first full WSL run using persistent ext4 `tmp_path` storage is retained as
`ubuntu-wsl-full.xml` (952 collected; 947 passed, 4 skipped, 1 failed; 706.041
s; exit 1; SHA-256
`1A69827EAAB6B4B77B2FFF904CA0692F442D07E8CE19B2567430A1E494971499`). The
single failure was a test assumption: it expected `tmp_path` to be transient,
while this approved ext4 location was persistent. The preflight behavior was
correct; the test now mocks unknown/transient storage classifications
deterministically. Its focused regression passed on Windows and WSL, and the
final full suites above pass. Keep the original report as a non-passing
historical result; it is not overwritten or counted as a pass.

## Release boundary

The final suites and typing checks establish software results on Windows and
Ubuntu WSL only. The native Linux VM and Live USB build/boot gates remain
unproven. No complete validated X200 Libreboot or X220 Coreboot ROM, physical
SPI probe/read/backup, firmware write, post-write read-back, recovery action,
IVprep/1vyrain run, or real-device boot test was performed. The T430, X200,
and X220 hardware workflows remain **HARDWARE PENDING**. Any physical write
or hardware-specific recovery still requires explicit human authorization.

Report hashes are repeated in `SHA256SUMS` for traceability.
