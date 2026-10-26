# Exact-tree full-suite attempt 07 status — 2026-10-26

Attempt 07 ran the full pytest suite on the exact copied source snapshot whose manifest is retained beside the run outputs. The run used Ubuntu WSL, Python 3.14.4, pytest 9.1.1, and the command recorded in the attempt's `command.txt`.

Result: **1,383 passed, 4 skipped, 2 failed** in 784.25 seconds; pytest exit code **1**. Both failures were `tests/test_offline_vm_image.py::test_recipe_verifies_exact_input_closure_and_reports_unavailable` and `tests/test_offline_vm_image.py::test_public_publisher_fails_before_creating_candidate_or_lock`. They failed because `usb_builder/offline_vm_image.py` pinned `usb_builder/offline_vm_build.py` to stale SHA-256 `ae34b4cb07d0dee9a371caf5a4ddb1d4c62ce027d914ca3b68c0ffdfa3317875`; the exact source snapshot hash was `f3afd3e97c7e41738fb0d7924b591bddbec3c66fb5b773f89c044f3e523c79a4`.

The image pin was refreshed to that exact source hash. The two affected tests and the canonical-LF receipt regression then passed together: **9 passed** in 1.38 seconds, pytest exit **0**. The focused output and status are `image-pin-followup-tests.txt` and `image-pin-followup.exit` in this bundle.

The complete suite has **not** been rerun after the pin refresh. Attempt 07 remains a failed run; there is no claim that the full suite currently passes.

## Retained attempt-07 provenance

The original run artifacts are under `../software-regression-20261025/exact-tree-recapture-20261025/attempt-07/`:

- `pytest.log` SHA-256: `e0f96d220f3d93ed1aa6b535b39aa4cd6929d864d435fbe22f27494e43188d69`
- `junit.xml` SHA-256: `00021a382b6cf48585f79204765e6559ed8d9cf73300c1d1124756bb83eefaf4`
- `pytest.exit` SHA-256: `4355a46b19d348dc2f57c046f8ef63d4538ebb936000f3c9ee954a27460dd865`
- `source-manifest.sha256` SHA-256: `3e917b526ff93c32de73cb4389c57e10a9a84fe4156a8c2a67e10c6bec0f0385`
- `command.txt` SHA-256: `cb258c083126123ede41d895274c629b845ce4a6a4db4a8857a3a6a6b0b09f1c`
- `environment.txt` SHA-256: `e902b49919f417de93196d04efa144910b864afb523f5db55ff1941662424ae7`

This evidence is software-only. It does not prove a deployed or booted VM worker, an Arch build, a product ISO, or any hardware workflow.
