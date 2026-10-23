# Current-source software regression — sealed-VM worker prototype — 2026-10-22

This record covers the current software tree after adding the unavailable
sealed-VM worker prototype. It supersedes the earlier 2026-10-22 Arch suite
count for the files listed in the input manifest. It is software evidence
only; it does not prove a sealed VM, package-matched Archiso build, Live USB
boot, firmware build, physical flash, read-back, recovery, or hardware boot.

## Tested source and environments

The source snapshot was based on HEAD
`94abf88b4d894c15c1d960f541da30c5a3e6659a`, with tracked working-tree contents
and these current untracked implementation inputs: the offline worker and
guest worker modules, their tests, and the rootless launcher parser and tests.
The complete per-file input manifest is
[`linux-source-manifest.sha256`](linux-source-manifest.sha256), SHA-256
`1c5e1d6a05d4558b04f21b3a825e2ac2d4b6d944cca9a2861bb2d0b83c2a2fc`.

The Linux run used Ubuntu 26.04 under WSL2, kernel
`6.18.54.1-microsoft-standard-WSL2`, Python 3.14.4, from a snapshot on the
user-approved ext4 partition. Tests used `/tmp` for pytest temporary files.
This is WSL software testing, not the sealed build VM.

The Windows run used the repository's Windows virtual environment on the
Windows host. Its short pytest base path was a newly checked, unused
`C:\\q\\lcw221022` directory; the run retained its JUnit and console logs
here. Neither run accessed hardware.

Key implementation input hashes:

| File | SHA-256 |
|---|---|
| `pyproject.toml` | `8654c1570c1a22c1a0c08674682a1ef382630121d6db437eb71df32d4270884a` |
| `usb_builder/offline_vm_worker.py` | `c0db91f8481e3b25b73d61096c0fd7d133520f8767f01b3e993a870f9932fbc7` |
| `usb_builder/offline_vm_guest_worker.py` | `ea3fd50ff6ec63739c5b3ddb627507a6b0d0127bca089a34e9a238373a9e3e0a` |
| `tests/test_offline_vm_worker.py` | `1b8d05335e86a10a542935fdfa17f3f35032b4e06196da0cb63ef814b587f865` |
| `usb_builder/rootless_offline_launcher.c` | `d3b81223eadff085ae927db00cc8a1b0982236a881e1c8700b39be807afc184d` |
| `tests/test_rootless_offline_launcher_source.py` | `c7fc6f4b21df2788b552b1890fe3caabf23466da296dba079c8ff963e63e6732` |

## Full regression

The same **1,088 collected tests** ran on both platforms with exit status 0
and no failures or errors:

| Environment | Result | Duration |
|---|---|---:|
| Ubuntu 26.04 WSL2 | **1,084 passed, 4 skipped** | 863.37 s |
| Windows | **994 passed, 94 skipped** | 962.03 s |

Linux command (from the ext4 source snapshot):

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  /home/alex/.venvs/autoloader-v1/bin/python -m pytest -q -ra \
  -p no:cacheprovider \
  --basetemp=/tmp/librecore-fullreg-sealed-worker-20261022 \
  --override-ini=tmp_path_retention_policy=failed \
  --junitxml=/mnt/wsl/PHYSICALDRIVE1p2/opt/librecore-autoloader-v1-vm/evidence/fullreg-sealed-worker-20261022/junit.xml
```

Windows used this command from the repository root, with its exact reports
retained in this directory:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m pytest -q -ra -p no:cacheprovider `
  --basetemp=C:\q\lcw221022 `
  --junitxml=docs\v1\evidence\software-regression-worker-20261022\windows-junit.xml
```
Exact complete output and skip reasons are in
[`linux-pytest.log`](linux-pytest.log) and
[`windows-pytest.log`](windows-pytest.log); JUnit reports are retained beside
them. JUnit SHA-256 values are `fc8d3452dc4978db742914882ce11f57fc96c4cc710394c3ab239ef2405b95ba`
(Linux) and `52f1be532725249c22511bb77f0d9821adbf0db3c7ce42141c36c993a0eb61a`
(Windows); console-log hashes are `14d4a4aaaff4d810a5ebc0b8b4cd63671bd3cb7c84b7cadb7357becce57f1697`
(Linux) and `eabb0a7078eff295a78b0c3fb90c3e09e573f65c4b8bc1d1a96102bcbe96febe`
(Windows). The refreshed [`SHA256SUMS`](SHA256SUMS) manifest covers this
README, both JUnit reports, both console logs, the Linux static-check log and
source-input manifest, and the preserved pre-edit manifest snapshot
[`SHA256SUMS.capture-20261022`](SHA256SUMS.capture-20261022). That snapshot
retains the original captured checksum list; its SHA-256 is recorded in the
refreshed manifest. The separate `linux-final-SHA256SUMS` covers only the
seven final-run artifacts and intentionally omits this README to avoid a
self-referential checksum; its SHA-256 is
`a47c41ad2b83502cb17eeed518d5d77c0c3bf7829a68116911e13493a8a55ab8`.

Linux skipped the Windows-specific durability and host-platform checks and
two tests requiring trusted Arch pacman executable ownership evidence. The
pacman checks remain **UNVALIDATED**, not passed. Windows skips are platform
contracts requiring POSIX directory fsync, ownership, symlinks, process
groups/signals, namespaces, Linux device inspection, or unavailable symlink
privileges; the full enumerated reasons are retained in the console log.
Skipped cases are not passes.

## Static and focused checks

On the same Linux snapshot, full-project no-incremental mypy passed with no
issues in **113 source files**. Both `python -m autoloader --help` and
`python -m autoloader build-usb --help` exited zero. The captured output is in
[`linux-static-checks.log`](linux-static-checks.log), SHA-256
`2bfb571bbb9b4e93fd5bb8c061b239a549c9c1e1436359c07059746126658de5`.

Exact static-check commands, run sequentially from the ext4 source snapshot:

```sh
PYTHONPATH=. /home/alex/.venvs/autoloader-v1/bin/python -m mypy \
  --no-incremental --cache-dir=/tmp/librecore-current-mypy-20261022 \
  autoloader usb_builder
PYTHONPATH=. /home/alex/.venvs/autoloader-v1/bin/python -m autoloader --help
PYTHONPATH=. /home/alex/.venvs/autoloader-v1/bin/python -m autoloader build-usb --help
```

The worker-focused selection passed **17/17** in Ubuntu WSL. It also passed
on Windows with **11 passed, 6 Linux-only skipped** in 3.43 seconds. The full
cross-platform suite above includes those tests. `git diff --check` passed on
the Windows working tree.

## Scope and outstanding gates

SOL accepted the unavailable prototype only. The user approved a policy
boundary allowing `NoNewPrivileges=0` only inside a sealed, no-network guest.
The packaged builder unit,
[`librecore-offline-build.service`](../../../../usb_builder/systemd/librecore-offline-build.service),
explicitly sets `NoNewPrivileges=no`; the paired
[`librecore-offline-preflight.service`](../../../../usb_builder/systemd/librecore-offline-preflight.service)
sets it to `yes`. These are packaged unit files only: no worker deployment or
service execution has established the effective merged systemd properties at
runtime. The worker has no production deployment pin, `build_usb.py` does not
call it, and the guest returns `BUILD_BACKEND_UNAVAILABLE` without package or
build commands. The current checks therefore do not establish guest
isolation, an operational build worker, package-matched Archiso output, a
product ISO, or BIOS/UEFI boot.

T430 1vyrain, X200 Libreboot, and X220 Coreboot remain **HARDWARE PENDING**.
No physical firmware write, SPI read, read-back, recovery action, or hardware
boot was performed. All such operations remain subject to explicit human
authorization. V1 is not complete.

## Final current-source Linux regression — 2026-10-23

After the TUI challenge-readiness test was changed from a five-second poll to
a 30-second bounded wait with worker-exit diagnostics, the full current-source
suite completed in Ubuntu 26.04 WSL2 with **1,146 passed, 4 skipped, 0 failed**
out of **1,150** in 758.975 seconds. The four skips are one Windows-specific
durability policy, one Windows-specific host contract, and two unvalidated
Arch pacman ownership-trust checks. The pacman checks remain unvalidated.
Before the wait was extended, one full run had a single TUI challenge timeout;
the focused race then passed repeated Linux and Windows runs, and the final
full Linux run passed. SOL reviewed the test adjustment and accepted it, with
a non-blocking cleanup suggestion.

This run used Python 3.14.4, kernel
`6.18.54.1-microsoft-standard-WSL2`, source from the Windows-mounted working
tree, and `/dev/shm` for pytest scratch. It is software regression evidence,
not a native guest, sealed worker, Live USB, ROM build, firmware write, or
hardware acceptance test. The source-input manifest,
[`linux-final-source-manifest.sha256`](linux-final-source-manifest.sha256),
covers 179 files under `autoloader/`, `usb_builder/`, and `tests/`, plus
`pyproject.toml`; its SHA-256 is
`a27203935f42b7c170d40c9a73a86ef129750d0afbf4fd1f9f66fd2bfb92840a`.
The test input `tests/test_offline_vm_build_executor.py` was semantically
reconstructed from a pytest-rewritten CPython 3.12 bytecode cache after its
source file was found empty; it is not byte-identical to the lost source.

Full-suite JUnit and console captures are
[`full-linux-final.xml`](full-linux-final.xml) and
[`full-linux-final.log`](full-linux-final.log). Their SHA-256 values are
`1b1ff67329c0ff20429d41e56321931e76423c7f9736e7c15e9b708a9ab00512` and
`7b1e551bbdce0eae71f82d44eb0e88c8d6158f6b9371046f41d570f3de782048`.
Full-project no-incremental mypy passed on **116 source files**; both CLI help
checks passed. Captured output is in
[`linux-static-final.log`](linux-static-final.log), SHA-256
`31d9b5c03f23f12ec853cdd36e926ccca9c67edbe1ebaf2605c0516fc1f9429a`.
`git diff --check` exited 0.
The seven current-run artifact hashes are collected in
[`linux-final-SHA256SUMS`](linux-final-SHA256SUMS), whose SHA-256 is
`a47c41ad2b83502cb17eeed518d5d77c0c3bf7829a68116911e13493a8a55ab8`.

On current Windows source, the UI selection passed **58, skipped 1**, and the
focused TUI cancellation race passed **1/1**. Reports are
[`windows-ui-current-short-path.xml`](windows-ui-current-short-path.xml),
[`tui-race-final-windows.xml`](tui-race-final-windows.xml), and
[`tui-race-final-linux.xml`](tui-race-final-linux.xml). The complete Windows
suite was not rerun on this source snapshot: C: had only 186,892,288 bytes
free, and the prior full Windows temporary tree is preserved. The earlier
1,088-test Windows result applies to its recorded 2026-10-22 snapshot, not
this final source snapshot.

An earlier environment check found the 254.9 GB Linux partition was not
visible in Ubuntu WSL. **Dated follow-up — 2026-10-23:** it was later
inspected read-only. The approved private VM workspace was then created
there; only that workspace and diagnostic screen captures were written, and
the partition was remounted read-only afterward. The TCG ISO attempts showed
inconsistent screen progress and did not verify a guest boot. See the
[2026-10-23 VM ISO diagnostic](../vm-iso-tcg-diagnostic-20261023/README.md).
The native Archiso build and boot gates remain open. No physical hardware
workflow was attempted. T430 1vyrain, X200 Libreboot, and X220 Coreboot
hardware acceptance remain **PENDING**; firmware writes and hardware-specific
recovery still require explicit human authorization. V1 remains incomplete.
