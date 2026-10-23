# Current-source offline worker regression — 2026-10-23

**Result: 1,221 passed, 4 skipped, 0 failed across the complete module inventory; native VM and hardware gates remain open.**

This record covers the current working tree at Git `HEAD` `94abf88b4d894c15c1d960f541da30c5a3e6659a` plus its recorded, uncommitted worktree inputs. It is not a VM build, ISO, firmware, or hardware acceptance result.

The captured module results, JUnit files, and `source-manifest.sha256` preserve the pre-cleanup bytes used for that run. A later whitespace-only cleanup removed trailing spaces from six recorded static-check command rows and one extra final blank line from five worker source/test files. The 33-module result remains historical for those captured bytes; see the [whitespace cleanup follow-up](whitespace-cleanup-followup.md) for the post-cleanup hashes and focused validation.

## Software checks

- Environment: Ubuntu 26.04 WSL2, Python 3.14.4, pytest 9.1.1; exact details are in `environment.txt`.
- The source-pinned runner executed the exact top-level inventory as a fresh run from the first module (`RESUME` unset, equivalent to `RESUME=0`): **33 modules, 1,225 test cases, 1,221 passed, 4 skipped, 0 failures, 0 errors**, and final runner exit **0**. Every module used its own pytest process and fresh `--basetemp` directory. This avoids the earlier single-process `/dev/shm` exhaustion; no single-process whole-suite pass is claimed.
- The exact invocation was `wsl -d Ubuntu -- bash /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader/docs/v1/evidence/offline-worker-regression-20261023/launch_module_run.sh`. `module-run.log`, per-module console logs, JUnit XML, `module-results.tsv`, `module-results.json`, source/environment manifests, and `module-run.exit` are retained. The aggregator rechecks current source and environment fingerprints, expected module inventory, unique rows, JUnit counts and skip records before writing the aggregate. The runner supports fresh runs only; `RESUME=1` is refused before prior results, manifests, or logs are touched. It does not retain module results for continuation.
- The README records the completed run's invocation, and `module-run.exit` records the runner's exit code as a separate record. The then-current `module-run.log` did not embed the launch command, per-module pytest commands, or wrapper pipeline status. The scripts now capture those details in future runs; this completed regression was not rerun or rewritten.
- The Linux aggregate retains four skips: the Windows directory-fsync policy test, the Windows production-preflight contract test, and two real Arch pacman ownership/preflight tests. The two Windows-specific tests later passed in a separate native-Windows run; only the two Arch ownership/preflight tests remain `UNVALIDATED` because Ubuntu WSL does not provide the trusted Arch pacman executable ownership evidence. The Linux aggregate remains 1,221 passed and 4 skipped. See its module JUnit XML and `module-results.json` for the recorded skip names and reasons.
- Separate native-Windows follow-up: Python 3.12.14 ran those two Windows-specific nodes with process-only `GIT_CONFIG_COUNT=1`, `GIT_CONFIG_KEY_0=core.longpaths`, and `GIT_CONFIG_VALUE_0=true`; both passed (2 passed, exit 0). The exact command, output, and status are in `windows-platform-specific-tests.log` and `windows-platform-specific-tests.exit`. Earlier unsupported UNC and long-path `--basetemp` attempts are retained as `windows-unc-basetemp-diagnostic.*` and `windows-long-path-diagnostic.*`, and are excluded from the passing counts.
- These aggregate results are from one source-pinned run. `source-manifest.sha256` hashes the `HEAD` plus current files under `autoloader`, `usb_builder`, and `tests` and the root build/test configuration; `environment.txt` records the OS, Python, pytest and installed packages.
- Supporting checks were rerun against the same recorded source and environment; every command exited 0:

- `run_static_checks.sh` recorded 1,225 collected tests, clean mypy for 117 files in native Linux and `--platform win32` modes, and successful top-level, `flash`, and `build-rom` CLI help checks. Per-command console output, exact command lines and exit codes are in `static-logs/` and `static-checks.tsv`; the wrapper exit is in `static-checks.exit`.
- The project Windows virtual environment used Python **3.12.14**. It imported all eight offline worker modules with `fcntl` absent; the command output and exit code are in `windows-import-smoke.log` and `windows-import-smoke.exit`. Linux-only device operations remain guarded and fail closed on Windows.
- Native Windows `git diff --check` exited **0**; its output and status are in `diff-check-windows.log` and `diff-check-windows.exit`. A WSL Git attempt is retained as `static-logs/diff_check_wsl_crlf_mismatch.log` but excluded: WSL Git treated the Windows checkout's CRLF files as added whitespace, whereas the native check with the configured Windows Git behavior passed.
- The 171-test focused offline worker/evidence selection passed earlier; its cases are also included in the 1,225-case module run above.

An earlier single-process full-suite attempt was stopped after `/dev/shm` reached 100% capacity. Its filesystem-related errors are not counted. The complete per-module run above is separate and has retained logs and JUnit for every module.

## Source pins

These hashes identify the prototype files at regression capture and are retained as historical source pins. They are not the post-cleanup hashes; the focused post-cleanup validation and updated hashes are recorded in the follow-up linked above.

```text
usb_builder/offline_vm_build.py                         0ef5f583737c63347eb4fcd29db639191019283e06f8aa4181ec17d4138c7906
usb_builder/offline_vm_build_preflight.py               6dd72b70141f69c9822c5a653bb66027ed33679ecf1ffdb2e9905f0c01b98320
usb_builder/offline_vm_build_service.py                 656fb179ee2c4ceed5f85bd6d80bd781075083b73923107bca33dd44629a7450
usb_builder/offline_vm_evidence.py                      aecf496412878a68becc4ad7d1d158320c76b53ec3c36bc9ce63b4fedc88d05f
usb_builder/offline_vm_guest_worker.py                  7563862c2d42248153d8add276a7e175796e3e2ad879fb10cf41f09c87e0a5a7
usb_builder/offline_vm_worker.py                       e10463b6f1bb105786f149c19853642e789924da1b778c8ccbbfec7332e8e82e
usb_builder/rootless_offline_launcher.c                 d3b81223eadff085ae927db00cc8a1b0982236a881e1c8700b39be807afc184d
tests/test_offline_vm_build_executor.py                 56fccb2221d82d44b1d72f0440e1d177c0a954087948cd59cb61258588cc3d53
tests/test_offline_vm_evidence.py                       946629ea79616dcae4bef8953cdb78028653b00a812ace054c1484fa33c8a238
tests/test_offline_vm_evidence_adapter.py               52327a419f40340ecabdf8ca0b5c3def17d179ab216af10056b090c2fd703299
tests/test_offline_vm_worker.py                         65ac05deebc9184afbff190145bbee8e64ae94e39ed8836255d7deec31fc5291
tests/test_rootless_offline_launcher_source.py          c7fc6f4b21df2788b552b1890fe3caabf23466da296dba079c8ff963e63e6732
docs/v1/offline-vm-evidence-prototype.md                3427e6e87bdec4aae38081e1c6e984f976271b34e477c6b8a6d67f4942ae5665
```

## Still pending

The worker is unavailable by default and has no production deployment pin. No sealed no-network VM build, real `mkarchiso` target-root evidence, product ISO, BIOS/UEFI application boot, or firmware artifact publication occurred. At the post-run read-only recheck recorded for this 2026-10-23 bundle, the approved Linux partition was online in Windows but absent from Ubuntu WSL block devices and mount points. No ThinkPad was accessed. The T430 1vyrain, X200 Libreboot, and X220 Coreboot hardware workflows remain pending; physical writes and hardware recovery still require explicit authorization.
