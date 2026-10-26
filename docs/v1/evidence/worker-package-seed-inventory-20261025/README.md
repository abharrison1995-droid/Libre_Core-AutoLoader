# Provisional offline worker package seed inventory — 2026-10-25

The hash-checked parser produced [`seed-inventory.json`](seed-inventory.json)
with status **`SEED_SET_INCOMPLETE`**. It records nine package rows from the
2026-09-30 pinned Arch repository databases. Their declared compressed archive
sizes sum to **37,264,803 bytes** (about 35.54 MiB); the sum comes from each
database `%CSIZE%` field. No package archive was downloaded or measured.

| Package | Repository | Version | Architecture | DB-declared size |
| --- | --- | --- | --- | ---: |
| archiso | extra | 91-1 | any | 103,541 |
| arch-install-scripts | extra | 31-2 | any | 17,105 |
| pacman | core | 7.1.0.r9.g54d9411-2 | x86_64 | 991,730 |
| util-linux | core | 2.42.4-1 | x86_64 | 6,684,541 |
| bash | core | 5.3.20-1 | x86_64 | 2,001,850 |
| shadow | core | 4.20.0.arch1-1 | x86_64 | 1,232,280 |
| libcap | core | 2.78-1 | x86_64 | 854,184 |
| python | core | 3.14.7-1 | x86_64 | 14,184,166 |
| systemd | core | 262-1 | x86_64 | 11,195,406 |

`archiso` is a builder-host package, not a package for the generated target USB
root. The other rows preserve the six exact rootless-builder pins plus the
database-selected `python` and `systemd` rows used by the fixed guest service
contract. This is only a provisional direct-package inventory: the worker
base, account, kernel, and deterministic initramfs seed contract is not
complete, and no recursive dependency closure is asserted.

## Pinned inputs and method

The source `usb_builder/snapshot.lock.json` has SHA-256
`5d66df5f5e6eacd0770399985b65507e84f208a7fbd1352faca13c0ead456cb1`. The
staged WSL inputs were `/home/alex/lc-worker-package-prep-20261025/core.db`
(129,884 bytes; SHA-256
`08d52f5e83f72a9e9f8464441da87b9a399f4decc7110fcfa3825f7c0af42dd2`) and
`/home/alex/lc-worker-package-prep-20261025/extra.db` (8,865,765 bytes;
SHA-256
`d2262e9def7afc76960c5242624cc14029afa47a9407acd84a29aed3a44f1663`). The
database files remain in that external staging directory; they were not
copied into this evidence bundle. The inventory binds both database hashes,
their byte sizes, every selected row's archive filename/declared size/hash,
and the SHA-256 of each decoded `PGPSIG` metadata blob.

The inventory was generated under Ubuntu WSL with:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B -m usb_builder.offline_vm_package_closure \
  --snapshot /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader/usb_builder/snapshot.lock.json \
  --core-db /home/alex/lc-worker-package-prep-20261025/core.db \
  --extra-db /home/alex/lc-worker-package-prep-20261025/extra.db \
  --output /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader/docs/v1/evidence/worker-package-seed-inventory-20261025/seed-inventory.json
```

The result records `UNAVAILABLE_NO_PINNED_ALPM_RESOLVER` for recursive
dependency closure because the reviewed snapshot lock has no hash-pinned
ALPM/libalpm version-comparator identity. The experimental resolver rejects
unsupported metadata rather than guessing; no package-selection lock was
generated. `PGPSIG` data was decoded and hashed only. No Arch keyring
verification was run, so these hashes do not establish package signatures.

## Validation limits

This is metadata-parser and synthetic-test evidence only. It is not a signed
package closure, worker image, image build, VM/guest boot, service execution,
product ISO, or hardware result. The official Arch ISO is only a read-only
bootstrap diagnostic input, not the final worker root. The default guest
entrypoint remains unavailable, `PRODUCTION_WORKER_DEPLOYMENT` remains
`None`, and `build_usb.execute_build` remains disconnected. The separately
reviewed raw-disk boot contract is out of scope for this inventory phase.

The candidate publisher's post-rename cleanup/fsync uncertainty also remains
deferred. It needs explicit recoverable-state handling and failure-injection
coverage before any later candidate publication path is enabled.
