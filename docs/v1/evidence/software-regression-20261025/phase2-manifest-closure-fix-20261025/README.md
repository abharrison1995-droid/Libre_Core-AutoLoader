# Package manifest and closure validation follow-up

This is a focused software-only check of stricter package input binding. It does not validate a live Arch package closure, package signatures, a worker image, a VM boot, or hardware.

The build manifest now requires a canonical closure path and hash. Every archive row must bind its exact repository database hash, metadata identity, archive identity, detached-signature identity, and root membership. The staged archive inventory must equal the physical archive union in the closure document, with membership serialized in the resolver's fixed `worker_root`, then `target_usb_root` order. The closure validator reparses the pinned database bytes and recomputes the selected rows; it remains unavailable for the production snapshot because no exact ALPM resolver identity is pinned.

The fixed source and package mount paths are shared constants across the worker, guest mount plan, build orchestration, and service. The synthetic service fixture now includes a request-bound snapshot lock and exact database/archive metadata. Missing or modified private snapshot-lock checks assert the specific lock rejection reason and no candidate header.

The synthetic worker-image input bundle's source hashes were refreshed for the changed service files. The closure parser was added to that bundle's exact file list because the fixed build verifier imports it. This refresh pins source bytes only; it does not make the unavailable image recipe deployable.

## Checks

Ubuntu WSL 2, Python 3.14.4, pytest 9.0.2 from the existing unprivileged bootstrap, bytecode/cache disabled, temporary basetemp on WSL `/tmp`.

Exact focused command:

```sh
python3 -B -m pytest -p no:cacheprovider --basetemp=/tmp/librecore-phase2-final-typeclean-20261025 --junitxml=docs/v1/evidence/software-regression-20261025/phase2-manifest-closure-fix-20261025/pytest.junit.xml tests/test_offline_vm_package_closure.py tests/test_offline_vm_build_executor.py tests/test_offline_vm_worker.py -q --color=no
```

Result: **201 passed**, exit 0. Full captured output is in `pytest.log`, the process exit status is in `pytest.exit`, and JUnit is in `pytest.junit.xml`. Earlier fixture-migration attempts are retained in the `pytest-initial-failure.*`, `pytest-retry1-failure.*`, and `pytest-pre-type-check.*` files.

Exact image-bundle pin check:

```sh
python3 -B -m pytest -p no:cacheprovider --basetemp=/tmp/librecore-image-pin-final-20261025 tests/test_offline_vm_image.py -q --color=no
```

Result: **35 passed**, exit 0; captured in `image-pin-check.log` and `image-pin-check.exit`.

Changed offline VM source modules passed scoped mypy 1.19.1:

```sh
mypy usb_builder/offline_vm_build.py usb_builder/offline_vm_build_service.py usb_builder/offline_vm_worker.py usb_builder/offline_vm_guest_worker.py usb_builder/offline_vm_package_closure.py
```

Result: **Success: no issues found in 5 source files**. `git -c core.whitespace=cr-at-eol diff --check` completed cleanly.

## Remaining gate

The private database snapshot helpers are not yet wired into preflight/service execution. The guest build still checks the package image directly after child execution, so source-mutation isolation remains a required follow-up before any deployment claim. Production deployment, the guest entry point, and the normal USB build route remain unavailable or disconnected.
