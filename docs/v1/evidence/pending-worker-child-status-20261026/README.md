# Pending worker child-status receipt follow-up — 2026-10-26

This software-only follow-up closes a strict parsing gap in the pending-only offline VM build service. The fixed `pacstrap` wrapper writes one ASCII decimal exit code followed by LF for each invocation. `_read_pacstrap_exit_code_snapshot()` previously used `bytes.splitlines()`, which accepted `b"0"` (a truncated final row) and `b"0\r\n"` (non-canonical CRLF) as a successful zero exit. LUNA reported that a pre-fix focused run reproduced both false accepts (2 failed and 4 passed); its console output and exit capture were not retained in this evidence bundle.

The parser now requires LF termination, digit-only nonempty rows, and canonical decimal spelling; malformed/truncated rows return the existing invalid-observation sentinel `(-1,)`. Regression cases cover canonical one/multiple rows, missing LF, CRLF, VT separators, leading zeroes, and empty rows.

## Verification

Focused command:

```text
/home/alex/.venvs/autoloader-v1/bin/python -m pytest -q tests/test_offline_vm_build_executor.py::test_pacstrap_receipt_requires_complete_canonical_lf_rows tests/test_offline_vm_build_executor.py::test_pacstrap_receipt_rejects_same_inode_same_size_rewrite_between_stat_and_open tests/test_offline_vm_worker.py::test_host_command_status_requires_exact_bound_fixed_service_success
```

Ubuntu WSL result: **25 passed**, pytest exit code **0**, 1.84 seconds. The captured output is in `focused-tests.txt`; exit status is in `pytest.exit`.

This proves only the focused parser and existing status/replay regression tests in Ubuntu WSL. No worker image was deployed or booted, no Arch keys/packages were acquired or executed, no ISO or firmware was built, and no physical hardware operation was performed. `PRODUCTION_WORKER_DEPLOYMENT` remains unset; trust acquisition remains blocked; all three hardware workflows remain pending.

## Image input pin follow-up and full-suite attempt 07

Attempt 07's exact-tree full suite completed with **1,383 passed, 4 skipped, 2 failed** (784.25 seconds, pytest exit 1). Both failures were the image input-closure and public-publisher tests: the reviewed input map still named the previous `offline_vm_build.py` digest after the canonical-LF child-status parser change. The source manifest records the actual digest as `f3afd3e97c7e41738fb0d7924b591bddbec3c66fb5b773f89c044f3e523c79a4`; `INPUT_FILE_PINS` now uses that exact value. The two affected image tests plus the receipt parser selection passed **9 tests** in 1.38 seconds (exit 0). The full suite has not been rerun after this one-line pin refresh, so attempt 07 remains a failed full-suite result. Details and hashes are in [the attempt-07 status record](attempt-07-full-suite-status.md).
