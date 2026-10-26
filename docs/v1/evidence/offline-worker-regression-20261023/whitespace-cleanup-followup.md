# Whitespace cleanup follow-up — 2026-10-23

After the 33-module regression capture, the six command rows in
`static-checks.tsv` were trimmed of trailing spaces. One extra final blank line
was removed from five worker source/test files. Command text, test logic, and
runtime behavior were unchanged. The original module logs, JUnit, aggregate,
and `source-manifest.sha256` are preserved as historical evidence for the
pre-cleanup bytes; no full regression was rerun.

## Source hashes

| File | Captured hash | Post-cleanup hash |
|---|---|---|
| `tests/test_offline_vm_evidence.py` | `946629ea79616dcae4bef8953cdb78028653b00a812ace054c1484fa33c8a238` | `1d361d6558cc4b65d139e47cdb82a14f65ba1219d440277b0990b6deeb9f7a0f` |
| `tests/test_offline_vm_evidence_adapter.py` | `52327a419f40340ecabdf8ca0b5c3def17d179ab216af10056b090c2fd703299` | `7edefcb6d618baaa299ac61b611394ab4b2b0409f4727ba1f4196d09d3e971cc` |
| `usb_builder/offline_vm_evidence.py` | `aecf496412878a68becc4ad7d1d158320c76b53ec3c36bc9ce63b4fedc88d05f` | `9c1c483826e8003669be4c8eff8685a7fad258a6e4d1dd99d9d3c6b5fcc1b441` |
| `usb_builder/offline_vm_guest_worker.py` | `7563862c2d42248153d8add276a7e175796e3e2ad879fb10cf41f09c87e0a5a7` | `f22f02dc6d9592e540a460dd283850a955fc83f591c081d038861130ffe36a04` |
| `usb_builder/offline_vm_worker.py` | `e10463b6f1bb105786f149c19853642e789924da1b778c8ccbbfec7332e8e82e` | `321eb76560b4393f21f7f81b2553f10a29dc2cda7433368dd9cca9a8f2800f22` |

The retained historical `source-manifest.sha256` file has SHA-256
`17078357efd7bd575c308d0a0719af1f8cfc2106a0df1a4d2b5510c26be9566d`.
Regenerating `snapshot_inputs.sh source` from the post-cleanup worktree and
hashing its output gives `b70314a7756d052872ce2b8c3317e5ee7f940817749b560543445b39ab03a4c0`.

## Focused validation

The following focused software selection ran against the post-cleanup source
bytes in Ubuntu WSL, using a fresh temporary root:

```sh
wsl.exe -d Ubuntu -- bash -lc 'cd /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader && test ! -e /tmp/lc-worker-whitespace-followup-20261023 && PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp /home/alex/librecore-v1-test-venv/bin/python -m pytest -q -p no:cacheprovider --basetemp=/tmp/lc-worker-whitespace-followup-20261023 tests/test_offline_vm_worker.py tests/test_offline_vm_build_executor.py tests/test_offline_vm_evidence.py tests/test_offline_vm_evidence_adapter.py tests/test_rootless_offline_launcher_source.py'
```

Result: **171 passed in 19.40s**, exit 0. Native `git diff --check` also exited
0 with no whitespace errors in the cleanup diff. These were software-only
checks; no VM worker, hardware, or physical device operation ran.
