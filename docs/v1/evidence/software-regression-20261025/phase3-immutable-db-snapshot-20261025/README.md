# Operation-owned repository database snapshot — 2026-10-25

This is synthetic software evidence for the offline guest service. It does not
validate the live Arch package closure, package signatures, a worker image,
VM execution, product boot, or hardware.

## What changed

Preflight copies the already hash-verified repository databases, optional DB
signatures, build manifest, and closure document into an operation-owned
private hash-checked copy. Files are mode `0400`; directories are mode `0500`
after creation. Those modes restrict ordinary writes but do not make the copy
immutable against its owner. The copy index binds the copied bytes to the
request, source/package identities, snapshot lock, manifest, and closure. The
fixed pacman configuration uses the copy's DBPath and lists each private
repository before the read-only package-image archive fallback.

The service reopens and validates the private copy after the builder returns
and before evidence/candidate publication. The fixed builder service unit
declares `/run/librecore-package-dbs` in `ReadOnlyPaths`, and the expected
effective systemd properties require that path. This is a static contract;
runtime proof of the read-only mount remains pending. A fake builder test
temporarily mutates the source `core.db` while reading the copied database and
confirms the builder-visible bytes still equal the verified synthetic DB.
Snapshot reopen rejects changed bytes, unexpected files, link/type/mode drift,
and child owner drift. The pacstrap status reader binds device, inode, size,
mtime, ctime, mode, link count, and owner across the initial stat, opened FD,
post-read FD, and current path. A same-inode, same-size rewrite between stat
and open is covered by a regression test. A post-executor validation failure
now publishes a request-bound
`INCOMPLETE_BUILD_EXECUTION` result and ready receipt when no result publication
has begun; the preflight reader returns that failure promptly. This path creates
no candidate header, commit, or terminal marker.

The package resolver remains unavailable for the production snapshot because
there is no pinned `vercmp` executable/runtime identity. No live package
archives were downloaded and no package signatures were verified.

## Checks

Ubuntu WSL 2, Python 3.14.4, pytest 9.0.2 from the existing unprivileged
bootstrap, bytecode/cache disabled, and pytest scratch under WSL `/tmp`.

The initial focused run is preserved in `focused-final.log`,
`focused-final.exit`, and `focused-final.junit.xml`. It ran:

```sh
python3 -B -m pytest -p no:cacheprovider \
  --basetemp=/tmp/librecore-phase3-final-captured-20261025 \
  --junitxml=docs/v1/evidence/software-regression-20261025/phase3-immutable-db-snapshot-20261025/focused-final.junit.xml \
  tests/test_offline_vm_image.py \
  tests/test_offline_vm_package_closure.py \
  tests/test_offline_vm_build_executor.py \
  tests/test_offline_vm_worker.py \
  tests/test_offline_vm_evidence.py \
  tests/test_offline_vm_evidence_adapter.py -q --color=no
```

Result: **306 passed, 0 failed, 0 errors, 0 skipped**, exit `0`.

A later run initially hit `ENOSPC` while building a synthetic evidence fixture;
that failed diagnostic is retained as `final-pytest.log`,
`final-pytest.exit`, and `final-pytest.junit.xml` (305 passed, 1 failed due to
the full `/tmp` filesystem). After freeing only the completed run's dedicated
pytest scratch, the same six modules were run again through a Python
`subprocess.run` wrapper that retained pytest's output and return code. The
successful result is in `final-pytest-space-recovered.log`,
`final-pytest-space-recovered.exit`, and
`final-pytest-space-recovered.junit.xml`: **306 passed, 0 failed, 0 errors,
0 skipped in 17.72s**, exit `0`. JUnit records 306 tests, 0 failures, 0 errors,
and 0 skipped. SHA-256 values are:

| Artifact | SHA-256 |
| --- | --- |
| `final-pytest-space-recovered.log` | `4286c86b0bf54138c4c84713608b570f2fbebeeb4a7077ed41f8c01452b20a089` |
| `final-pytest-space-recovered.exit` | `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa` |
| `final-pytest-space-recovered.junit.xml` | `d054555a4fd06b122874596cda1169535bf636e266d9cdd6233d6efa81af3f01` |

Scoped mypy 1.19.1 passed after the final service change:
`mypy --no-incremental --cache-dir=/tmp/librecore-phase3-mypy-cache`
over six offline worker source modules. The tracked diff whitespace check
completed with no whitespace errors; Git emitted only the existing
LF-to-CRLF working-copy warnings.

### Full stat identity follow-up — 2026-10-25

After hardening pacstrap exit-receipt reads to compare device, inode, size,
mtime, ctime, full mode, link count, and owner from the initial stat through
the opened descriptor, post-read descriptor, and current path, the same six
modules passed again: **307 passed, 0 failed, 0 errors, 0 skipped in 39.61s**,
exit `0`. JUnit records 307 tests and zero failures, errors, or skips. The new
same-inode/same-size rewrite race test is included. The result artifacts are
`focused-stat-identity.log`, `focused-stat-identity.exit`, and
`focused-stat-identity.junit.xml`:

```sh
python3 -B -m pytest -p no:cacheprovider \
  --basetemp=/tmp/librecore-phase3-stat-identity-20261025 \
  --junitxml=docs/v1/evidence/software-regression-20261025/phase3-immutable-db-snapshot-20261025/focused-stat-identity.junit.xml \
  tests/test_offline_vm_image.py \
  tests/test_offline_vm_package_closure.py \
  tests/test_offline_vm_build_executor.py \
  tests/test_offline_vm_worker.py \
  tests/test_offline_vm_evidence.py \
  tests/test_offline_vm_evidence_adapter.py -q --color=no
```

| Artifact | SHA-256 |
| --- | --- |
| `focused-stat-identity.log` | `8f045ac464e21279032cf5b93c1bba5a236d74254e3ecd9c280acf17b14fc7bb` |
| `focused-stat-identity.exit` | `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa` |
| `focused-stat-identity.junit.xml` | `f19764833404234086ddc948df04d8100436d73ed67a05aa38d5cd405f7123a6` |

Scoped mypy 1.19.1 again passed for six offline worker source modules after
this follow-up. The service's static `ReadOnlyPaths` contract includes
`/run/librecore-package-dbs`; no effective runtime mount proof was performed.

This focused run is not a replacement for a full-suite rerun. Production
deployment remains unset, the default guest remains unavailable, the regular
USB build route remains disconnected, and image publication remains closed.
No image build, QEMU/VM run, package acquisition, product boot, or hardware
operation was performed.
