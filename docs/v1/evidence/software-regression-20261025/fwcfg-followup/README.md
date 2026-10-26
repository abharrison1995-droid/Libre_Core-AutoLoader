# Current-source regression after worker-image digest handoff — 2026-10-25

## Full suite

The latest working-tree suite passed **1,293 tests, skipped 5**, with **0
failures and 0 errors** in **965.13 seconds**. Pytest exited **0**. JUnit
records 1,298 cases, 5 skips, and no failures or errors. The final output is
in [`full.pytest.log`](full.pytest.log), the machine-readable report is
[`full.junit.xml`](full.junit.xml), and [`full.pytest.exit`](full.pytest.exit)
contains `0`.

The tested source is the working tree based on commit
`2e28a81b11e6a4fe97472ed0dadb8ea71429127b`, including the changed files listed
and hashed in [`source-files.sha256`](source-files.sha256). The exact WSL
interpreter, test tools, environment, scratch paths, and run times are recorded
in [`environment.txt`](environment.txt). The repeatable runner is
[`run-full-suite.sh`](run-full-suite.sh).

The SHA-256 of `git diff --binary HEAD -- autoloader tests usb_builder` for
the tested code/test changes is
`d13ad5c8b9c1a3f080fc9847acd05e3c5a75f7c1c49e9ac8d8d1c8a6efc959f6`.

The command was:

```text
wsl.exe -d Ubuntu -u alex -- bash /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader/docs/v1/evidence/software-regression-20261025/fwcfg-followup/run-full-suite.sh
```

The runner used Ubuntu WSL 2, Python 3.14.4, pytest 9.0.2, Textual 2.1.2,
user `alex` with `umask 0022`, and the previously extracted unprivileged test
dependencies. Bytecode, pytest cache, and Python optimization were disabled.
Both pytest's `basetemp` and `TMPDIR` were placed in a new Linux ext4 directory
inside the Ubuntu WSL VHDX on C:, preserving Linux filesystem semantics. The
separate physical Linux partition was not mounted or changed.

The five skips are unchanged and remain unpassed:

| Test | Skip reason |
|---|---|
| `test_windows_directory_fsync_policy_keeps_approval_closed` | Windows-specific production durability policy |
| `test_installed_wheel_contains_runbook_accepted_by_readiness_gate` | Full-run environment lacks installed setuptools and wheel build tools; a separate wheel smoke passed earlier |
| `test_real_offline_network_namespace_has_no_external_routes` | Unvalidated: trusted Arch pacman ownership evidence is unavailable on this host |
| `test_real_production_preflight_reports_mount_gate_after_route_probe` | Unvalidated: trusted Arch pacman ownership evidence is unavailable on this host |
| `test_real_build_usb_cli_fails_before_subprocess_on_windows` | Windows-specific host platform contract |

## Change covered

The source adds a fixed QEMU `fw_cfg` handoff for the deployment-pinned worker
image SHA-256. The guest reads that value from the fixed
`/sys/firmware/qemu_fw_cfg/by_name/opt/org.librecore/worker-image-sha256/raw`
path and rejects unavailable or malformed values before it evaluates the
request binding. This removes the impossible requirement to embed the full
raw-image digest inside that same image.

The separate [native QEMU fw_cfg transport diagnostic](../../native-qemu-fwcfg-20261025/README.md)
passed on QEMU 11.1.2 with WHPX: a hash-pinned upstream Arch kernel/initramfs
guest read the exact 64-byte lower-case digest supplied by the fixed QEMU
argument. That diagnostic proves the fw_cfg transport path only. It did not
boot or execute the LibreCore worker, build a ROM or ISO, verify guest image
attestation or isolation, or test a product boot path.

## Incomplete runner attempts

The first attempt used WSL's 7 GiB `/tmp` tmpfs for all per-test scratch and
was interrupted at roughly 40% when its scratch directory reached 4.2 GiB. A
retry placing `TMPDIR` on C: failed before collecting tests because pytest's
capture files require Linux temporary-file behavior. A later retry with only
`basetemp` on the Windows-mounted filesystem was stopped at 55% after fixture
errors. These attempts are retained with `attempt1-` and `attempt2-` filenames
for transparency; none is a test result. The passing run above used the Linux
ext4 filesystem in the C:-backed Ubuntu WSL VHDX.

## Scope

This is software regression and one non-destructive VM transport diagnostic.
It does not establish a production worker deployment, effective isolation,
offline build, current-source product ISO, BIOS/UEFI product boot, or any of
the three physical ThinkPad workflows. No firmware write, SPI operation,
recovery action, or physical-device passthrough was performed. Hardware
acceptance remains intentionally deferred.

[`SHA256SUMS`](SHA256SUMS) covers this README, the source list, runner,
environment, successful test captures, and retained incomplete-attempt
diagnostics.
