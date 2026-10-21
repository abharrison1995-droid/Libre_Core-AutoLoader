# Software regression evidence — 2026-10-21

This folder consolidates the current Windows and Ubuntu WSL software results.
It contains no hardware, firmware-write, or successful product-ISO evidence.

## Earlier full-suite checkpoint (before the schema-4 follow-up)

| Environment | Result | Duration | Notes |
|---|---:|---:|---|
| Windows | 921 passed, 68 skipped; 989 total; 0 failures; exit 0 | 768.92s | Command below; pytest used a short `C:\q` base directory. |
| Ubuntu 26.04 WSL | 985 passed, 4 skipped; 989 total; 0 failures; exit 0 | 687.55s | The original external WSL summary and structured result are preserved in `ubuntu-wsl-source-record/`. |

The Windows command was:

```powershell
python -B -m pytest -q -p no:cacheprovider --basetemp=C:\q\v1-readiness-full-final-20261021
```

Full-project mypy passed on **105 source files** in Windows and Ubuntu WSL.
All skipped tests remain unpassed. The current run summary did not include an
exact skip taxonomy or a JUnit XML report, so this record does not infer
which individual checks those skips cover.

The focused readiness checks also passed: Windows had **11 passed, 1 skipped**
in 160.41s; Ubuntu WSL had **9 passed** in 128.49s. These tests exercise the
read-only persisted-evidence report and its controller/UI/CLI route. The
report's maximum result is `REVIEW_INPUTS_COMPLETE`; it does not establish
hardware readiness or authorize a write.

One Windows run using a direct WSL UNC temporary path failed in an X200
MAC-preservation fixture while creating its nested sidecar. This is a
path-compatibility diagnostic only and is not counted as a release test
result. A separate earlier Windows retry stopped when C: reached zero free
bytes; it was incomplete. The current pass completed after storage was
available again. At the later status check C: had approximately 56 GB free;
that is a mutable host-space observation, not a test result.

## Source records

`ubuntu-wsl-source-record/README.md`, `results.json`, and `SHA256SUMS` are
byte-preserved copies from the approved ext4 WSL evidence area. Its original
README also records an earlier incomplete Windows run; the final Windows pass
above supersedes that Windows status only. The source records and this
consolidated summary have separate checksum coverage in `SHA256SUMS`.

## Schema-4 current-source follow-up — 2026-10-21

After the X200 schema-4 provenance change, an integration test exposed that
the synthetic complete-X200 fixture and workflow validator still used the old
pre-patch Coreboot identity. The fixture now supplies schema-4 nested
provenance, and the production workflow validates those same nested rows and
binds CBFS to the post-lbmk Coreboot result. SOL independently accepted the
frozen change. This does not establish a real ROM build.

| Environment | Result | Duration | JUnit SHA-256 |
|---|---:|---:|---|
| Windows `.venv` | 929 passed, 68 skipped; 997 total; 0 failures/errors | 712.265s | `D16204D90FFC18FF4D0F7CF88FA1D2C71960CA72AD49B1AF5FE2DAC0C46DA460` |
| Ubuntu 26.04 WSL | 993 passed, 4 skipped; 997 total; 0 failures/errors | 679.831s | `3D016052FEDABBD85D4284305D444EE0D2179209D3F924572D8DB4D5254A0F68` |

The exact reports are `windows-schema4-followup.xml` and
`ubuntu-schema4-followup.xml`. The Windows run used a short `C:\q` pytest
base; the Ubuntu run placed pytest and `TMPDIR` on the authorized ext4
workspace after an earlier parallel attempt exhausted `/tmp`. That earlier
attempt is excluded: its X200 fixtures predated the correction and its later
Linux errors were caused by the full tmpfs.

Full-project no-incremental mypy passed on **111 source files** in Windows
native mode, Windows Win32-targeted mode, and Ubuntu WSL. Windows skips are
limited to POSIX fsync/durability, symlink/ownership, process-group, Linux
device/namespace, Archiso, and account privilege checks; the exact 68 cases
are recorded in the Windows JUnit report and remain unvalidated. Ubuntu's
four skips are two trusted Arch pacman-owner checks and two Windows-specific
contracts; they remain unvalidated.

SOL-reviewed focused X200/write-approval/readiness/controller suites passed
**113 tests with 20 platform skips** on Windows before the full runs. The
Win32-targeted type check for `autoloader/workflow/x200_libreboot.py` and the
focused diff check passed. The focused skips and full-suite skips are not
passed tests.

## Fresh current-source pass after X200 CBFS overlap fix — 2026-10-21

SOL accepted the CBFS change that includes `(empty)/null` rows in overlap
checks, permits adjacent intervals, and tests null-to-payload and
null-to-null overlap rejection. X200 requires the `simple elf` payload type;
X220's existing `payload` type rule remains unchanged.

| Environment | Result | Duration | JUnit SHA-256 |
|---|---:|---:|---|
| Windows `.venv` | 945 passed, 70 skipped; 1,015 total; 0 failures/errors | 831.38s | `fc23169ea17c9ce666e8604194c7452a5287bf39131406f3f3a7059779970f7f` |
| Ubuntu 26.04 WSL | 1,011 passed, 4 skipped; 1,015 total; 0 failures/errors | 670.76s | `89245058bcab381a96608be87e7c70cfb4aa3f6a413997fac7532684efb441ff` |

The exact JUnit reports are `windows-x200-null-overlap.xml` and
`ubuntu-x200-null-overlap.xml`. The Windows command used
`--basetemp=C:\q\v1-x200-null-overlap-20261021`. Ubuntu used
`/home/alex/.venvs/autoloader-v1/bin/python` with `TMPDIR` and pytest's base
under the user-authorized ext4 workspace. The first Ubuntu launch used the
system Python, which did not contain pytest and exited before test collection;
it is not counted as a test result.

The XML reports retain the skip names and reasons. Windows skips cover POSIX
directory durability, symlink and ownership/mode behavior, process-group and
signal contracts, Linux device/user-namespace and Arch tool checks, plus a
Windows permission-bit case. Ubuntu skips are two trusted Arch pacman ownership
checks and two Windows-specific contracts. Every skip remains unvalidated and
is excluded from the passed counts.

Full-project no-incremental mypy passed on **111 source files** in three
sequential checks: Windows native, Windows `--platform win32`, and Ubuntu
WSL. Each reported no issues. The Windows and Ubuntu skips remain unpassed;
neither suite establishes native Linux, Live USB, firmware, or hardware
acceptance.

The current-source wheel smoke is recorded separately in
[`current-source wheel evidence`](../current-source-wheel-x200-null-overlap-20261021/README.md).

## VM debug-shell diagnostics

The related QEMU captures are archived separately:

- [`Arch-boxes guest debug-shell attempts`](../vm-debug-shell-20261021/README.md)
- [`Arch ISO serial debug-shell attempt`](../archiso-debug-shell-20261021/README.md)

Both are incomplete generic-guest diagnostics. Neither obtained a usable
guest shell or executed guest commands. They do not establish a native Linux
builder, current-source product ISO, AutoLoader startup, firmware artifact,
or hardware workflow.

## Release boundary

Native Linux/Archiso build and current-source product ISO validation remain
pending. A successful product Live USB BIOS/UEFI app/service boot remains
unproven. T430, X200, and X220 physical workflows remain **HARDWARE PENDING**.
No hardware operation occurred in these runs.
