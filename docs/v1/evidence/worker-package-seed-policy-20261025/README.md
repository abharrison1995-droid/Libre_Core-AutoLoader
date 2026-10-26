# Worker and target package seed policy — 2026-10-25

This is a metadata-only, synthetic-policy checkpoint for the fixed Arch
2026-09-30 repository snapshot. The machine-readable
[`seed-policy.json`](seed-policy.json) has status
`SEED_POLICY_COMPLETE_CLOSURE_UNAVAILABLE`. It records the direct package roots
for the worker/build host and target USB separately. It does not claim a
recursive package closure, verified signatures, a deployable package
repository, an image, or a worker build.

## Pinned inputs and direct-root policy

The snapshot lock SHA-256 is
`5d66df5f5e6eacd0770399985b65507e84f208a7fbd1352faca13c0ead456cb1`.
The staged `core.db` is 129,884 bytes with SHA-256
`08d52f5e83f72a9e9f8464441da87b9a399f4decc7110fcfa3825f7c0af42dd2`; the
staged `extra.db` is 8,865,765 bytes with SHA-256
`d2262e9def7afc76960c5242624cc14029afa47a9407acd84a29aed3a44f1663`.
The exact target profile is
`usb_builder/archiso_profile/packages.x86_64`, SHA-256
`7a7a3fa4127ab6fbaaf952be4bbde82f787a7da30d3111601a9e7fccd6334dbd`.

The worker/build-host policy has 13 direct roots: `base`, `linux`,
`mkinitcpio`, `systemd-sysvcompat`, `python`, `systemd`, `archiso`,
`arch-install-scripts`, `pacman`, `util-linux`, `bash`, `shadow`, and
`libcap`. `archiso` is a worker build-host tool and is deliberately absent
from the target USB profile.

The target USB has the 29 package roots from the hash-pinned profile. They are
listed with their exact profile-line reasons in `seed-policy.json`; the list
includes `mkinitcpio-archiso` and excludes `archiso`. The profile bytes, not
the worker seed list, define this target root.

Database-declared compressed archive sizes for direct roots total
206,233,344 bytes for worker roots and 231,413,691 bytes for target USB roots.
Deduplicating shared archive SHA-256 identities gives a unique direct-root
metadata total of 245,814,629 bytes (about 234.4 MiB). These are `%CSIZE%`
estimates from repository metadata, not fetched or measured files. The
database files total 8,995,649 bytes. Transitive closure, extracted root, and
final disk sizes are unknown.

## Why recursive closure remains unavailable

The snapshot has no exact pinned ALPM `vercmp` executable identity. The
hash-pinned pacman package is present as metadata, but its executable hash is
not in the reviewed lock; no local `vercmp` executable was available in the
checked staging and WSL paths. The resolver therefore cannot select
versioned dependencies, and this record deliberately leaves both recursive
package graphs empty. The metadata parser and synthetic resolver tests do not
prove a live closure.

The production resolver gate also lacks an independent trust chain. No
authenticated Arch master-key bytes or cryptographically checked master
certifications/designated-revocations are retained; the current Arch page and
keyserver listings in the proposal are observations, not verified key
material. The current master-key page is not historical evidence of the
master-key, signer-certification, or revocation state at the pinned
2026-09-30 repository snapshot or the 2026-09-09 `archlinux-keyring` signature
time. That historical state needs dated authoritative evidence and an accepted
signature-time/revocation policy. The exact pacman archive and detached signature have not been
acquired or verified, the observed Ubuntu `gpgv` candidate is not accepted or
bound to authenticated package bytes, and there is no authenticated
certificate/revocation audit tool. Consequently there is no reviewed
`vercmp` executable hash, PT_INTERP/DT_NEEDED runtime identity, or
`alpm_resolver` entry in the hash-pinned snapshot lock. The proposal remains
`ACQUISITION_BLOCKED`; no real package comparison or recursive closure was
run. Synthetic comparator calls are limited to test fixtures and do not
qualify the production resolver.

The JSON decodes and hashes each package `PGPSIG` metadata blob but records
`signature_verified: false`. No package archive was downloaded and no Arch
keyring verification was performed. A `PGPSIG` hash is only metadata bound to
the pinned DB bytes; it is not an independent authenticity pin. Cryptographic
verification of the fetched detached signature against an accepted signer
chain is the package authenticity gate. The repository DB policy is
`Required DatabaseOptional`: exact DB hashes are pinned, and a DB detached
signature is verified if supplied; an absent optional DB `.sig` is allowed.
Package archive signatures remain required before any future repository can
be called deployable.

The build manifest and runtime validate the two exact DB hashes before
package metadata is consumed and recheck them after a successful builder run,
before candidate-result publication. If a DB detached signature is present,
its hash and signature are verified at both checks. This gives the optional
signature policy mandatory pre-use and post-use DB hash checks; it is not a
package-signature trust result.

## Proposed acquisition and trust gates — not performed

No downloads or keyring setup are authorized or performed in this checkpoint.
The concrete trust proposal is in
[`trust-manifest-proposal-20261025.json`](trust-manifest-proposal-20261025.json).
Its status is `ACQUISITION_BLOCKED`; listed fingerprints, package records,
keyserver certification rows, and local Ubuntu verifier hashes are evidence
inputs, not cryptographic verification results. SOL accepted the staged
two-gate review plan: Stage A may acquire only the exact keyring and pacman
archives after separate approval of the signer/master chain and verifier;
Stage B must review the authenticated extracted `vercmp` identity and
dependencies before it is executed. No package bytes have been downloaded.

The observed Ubuntu WSL `/usr/bin/gpgv` (2.4.8-4ubuntu3.1) and
`ubuntu-archive-keyring.gpg` hashes are a verifier candidate only. Ubuntu APT
authentication would establish the Ubuntu package origin, not Arch signer
trust. The signed Ubuntu index, exact package stanza and archive, installed
binary binding, archive keyring binding, and disabled-signature-check audit
are not yet retained. `gpgv` also does not by itself establish the Arch
certification/revocation path, so an authenticated certificate-audit method
must be pinned. The unsigned MSYS GPG candidate remains rejected.

The proposed chain is:

1. Before fetching or importing the keyring, pin exact trusted Arch master
   public-key bytes in a trust manifest, each fingerprint, and an independently
   authenticated source for that material. Pin the bootstrap package-signing
   key's exact public-key bytes and certification chain to those master keys,
   with revocation/expiry evidence and the signature-time acceptance policy.
   Pin the exact `archlinux-keyring` package filename, version, architecture,
   size, archive hash, and detached-signature hash from the fixed database
   metadata. If this pretrust chain or its policy cannot be authenticated
   independently, stop.
2. Fetch only the exact keyring package from the official
   `archive.archlinux.org/repos/2026/09/30/` snapshot. Verify package size and
   archive hash, then verify its detached signature with the pre-pinned
   bootstrap signer key only after validating that key's certification chain
   to the pinned master keys, revocation/expiry state, and signature-time
   policy. Apply this same policy to the keyring-package signature. Do not
   extract or import the keyring before both checks pass. Do not use the host
   keyring or ambient network refresh. Initialize an isolated, disposable
   keyring only from the verified package.
3. Define and validate the package-signer policy against the pinned master
   keys and verified snapshot keyring: require a valid certification chain to
   an explicitly pinned trust root, reject revoked or expired keys, and accept
   a signature only when its signing time is valid for the fixed snapshot date
   and key validity period. Do not mark every key shipped in the keyring as
   trusted by default. Preserve verifier status, signer fingerprint,
   certification path, revocation/expiry result, and signature time for each
   archive.
4. Authenticate the exact pacman package with the isolated verifier before
   extracting `usr/bin/vercmp`. Pin its package filename/version/architecture,
   size, archive SHA, and detached-signature SHA; verify the detached
   signature; extract in a bounded isolated location; hash the executable;
   and add its exact package/signature/path/hash identity to the snapshot
   lock. Alternatively, use an independently pinned host verifier with an
   equally explicit provenance contract. Never execute an unpinned resolver.
5. After the resolver identity is reviewed, independently resolve and
   validate both complete graphs using only the two hash-pinned DBs. Then fetch
   only the exact archive filenames listed in that result from the official
   2026-09-30 core/extra archive paths. Require each archive's DB-declared
   name/version/architecture/size/SHA and exact filename to match, fetch its
   detached signature, and verify it with the isolated trust policy. Hash the
   exact DBs before and after resolution/acquisition; if a DB signature
   exists, verify it too.

The known direct-root archive floor is about 234.4 MiB plus 8.6 MiB of
repository DBs. The complete transitive closure and extracted root are not
measured. An 8 GiB staging allowance is only a provisional planning budget;
measure the exact closure and image layout before downloading and stop if
available scratch is below the measured requirement plus working headroom.
No network download, keyring setup, privilege change, image build, QEMU worker
launch, or hardware operation occurred for this evidence.

## Checks and limits

On Ubuntu WSL 2 / Python 3.14.4, pytest 9.0.2 ran the package-closure and
build-executor modules with the extracted test bootstrap and `/tmp` basetemp:

```sh
cd /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader
PYTHONPATH=/tmp/librecore-autoloader-v1-evidence-20261025-pytest-bootstrap/extracted/usr/lib/python3/dist-packages \
PYTHONDONTWRITEBYTECODE=1 PYTHONOPTIMIZE=0 TMPDIR=/tmp \
python3 -B -m pytest -p no:cacheprovider \
  --basetemp=/tmp/lc-worker-root-closure-final3-20261025 -q \
  tests/test_offline_vm_package_closure.py tests/test_offline_vm_build_executor.py
```

Result: **75 passed in 2.31 seconds**, exit 0. Mypy 1.19.1 passed on
`offline_vm_package_closure.py`, `offline_vm_build.py`, and
`offline_vm_build_service.py` with `--no-incremental`; no issues were found.
These are parser, contract, and synthetic-test results only. No full-suite
rerun followed this phase. The earlier nine-row inventory is
preserved separately as historical evidence and is superseded as a direct
seed policy by this 13-worker/29-target record.

The follow-up unavailable-gate regression additionally asserts that a
caller-supplied comparator is rejected before `compare()` is called, and that
inserting an apparent resolver identity into a modified lock fails the exact
snapshot hash check before comparison. It checks the retained inventory and
trust proposal still say closure unavailable, signatures unverified, and
acquisition blocked. This is a fail-closed software regression, not evidence
that a comparator, trust anchor, package closure, or image exists.

On Ubuntu WSL 2, Python 3.14.4 / pytest 9.1.1 reran the package-closure and
synthetic image-contract modules after refreshing the exact source pin for
`offline_vm_package_closure.py`:

```sh
cd /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader
PYTHONDONTWRITEBYTECODE=1 PYTHONOPTIMIZE=0 TMPDIR=/tmp \
  /home/alex/.venvs/autoloader-v1/bin/python -B -m pytest \
  -p no:cacheprovider \
  --basetemp=/tmp/lc-worker-trust-gate-final-20261025 -q \
  tests/test_offline_vm_package_closure.py tests/test_offline_vm_image.py
```

Result: **68 passed in 7.64 seconds**, exit 0. Mypy 1.19.1 passed with
`--no-incremental` for `offline_vm_package_closure.py` and
`offline_vm_image.py`. The initial combined run had 66 passing tests and two
image-contract failures because the reviewed source hash for
`offline_vm_package_closure.py` was stale; after updating that exact input pin,
the rerun passed. No full suite was run for this follow-up.

`PRODUCTION_WORKER_DEPLOYMENT` remains `None`, the default guest entrypoint
remains unavailable, `build_usb.execute_build` remains disconnected, and
candidate publication remains closed. No image, package closure, VM/worker
boot, product ISO, firmware write, recovery operation, or hardware acceptance
is claimed.

## Retained focused regression capture

The reviewer requested machine-readable output for the focused result. A
second run on Ubuntu WSL 2 used Python 3.14.4, pytest 9.1.1, and mypy 1.19.1.
The closure and image-contract selection passed **68 tests in 11.39 seconds**
with **0 failures, 0 errors, and 0 skips**; its JUnit report records all 68
cases. Scoped mypy passed on both modules. The exact log, exit files, JUnit,
and environment record are in
[`focused-resolver-gate-20261025`](focused-resolver-gate-20261025/).

The retained pytest command was:

```sh
cd /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader
PYTHONDONTWRITEBYTECODE=1 PYTHONOPTIMIZE=0 TMPDIR=/tmp \
  /home/alex/.venvs/autoloader-v1/bin/python -B -m pytest \
  -p no:cacheprovider \
  --basetemp=/tmp/lc-trust-gate-captured-20261025 -q \
  --junitxml=docs/v1/evidence/worker-package-seed-policy-20261025/focused-resolver-gate-20261025/pytest.junit.xml \
  tests/test_offline_vm_package_closure.py tests/test_offline_vm_image.py
```

Mypy used `--no-incremental --cache-dir=/tmp/lc-trust-mypy-cache-20261025`
against `usb_builder/offline_vm_package_closure.py` and
`usb_builder/offline_vm_image.py`. Captured source hashes, result hashes, and
environment versions are recorded alongside the logs and in `SHA256SUMS`.
The full suite was not rerun for this follow-up.
