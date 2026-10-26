# Arch package trust gate audit — 2026-10-26

**Status: STOP / Stage A remains blocked.** This is a read-only audit of
already-retained local evidence. It does not grant acquisition authorization
or establish a trust chain. A previous evidence bundle already contains the
119-byte pacman detached-signature packet; this audit acquired no new Arch
material. The reviewed source bundles' listed SHA-256
entries matched their local manifests: historical Arch dossier 73/73,
Ubuntu verifier proposal 104/104, Ubuntu trust corroboration 91/91, pacman
`-2` signature-ID capture 22/22, worker seed inventory 2/2, and worker seed
policy 10/10. These checks establish local file consistency only; they do not
authenticate upstream signatures or the provenance of the host trust stores.

## Findings

1. **Ubuntu verifier provenance is still host-relative.** The retained
   verifier proposal checked signed APT index metadata using the installed
   WSL `gpgv` and Ubuntu keyring. The separate Windows HTTPS capture
   corroborates the official Ubuntu documentation and keyring, but the
   captured page/keyring are not independently signature-authenticated. The
   installed verifier, `dpkg-deb`, and root-keyring provisioning therefore do
   not yet form an independently accepted verifier chain.
2. **Arch historical key state is missing.** The captured official page shows
   the current Arch master keys and designated revokers, not authenticated
   key/certification/revocation state at the pinned 2026-09-09 keyring
   signature time or the 2026-09-30 repository snapshot. The current package
   page and commit metadata corroborate chronology only.
3. **The pacman `-2` issuer is identified only as packet metadata.** The
   retained 119-byte signature packet contains issuer fingerprint
   `0429897de5f3bdac537a30696d42bdd116e0068f` and key ID
   `6d42bdd116e0068f`. The packet parser binds that data to the pinned DB-row
   signature digest; it does not verify the OpenPGP signature or establish
   the key's owner, certifications, or revocation status. The pacman archive
   and detached signature have not been cryptographically verified.
4. **The package closure is incomplete.** The retained inventory has nine
   database-declared package rows, no recursive closure, no complete worker
   base/kernel/initramfs seed, and no downloaded package archives. It records
   `UNAVAILABLE_NO_PINNED_ALPM_RESOLVER`; no exact authenticated `vercmp`
   identity is pinned.

## Stop/go checklist

This sequence follows the existing two-gate proposal in
[`worker-package-seed-policy-20261025`](../worker-package-seed-policy-20261025/README.md).
It does not claim that either gate has passed.

### Stage A — pre-acquisition trust review

Keep Stage A at **STOP** until the following pre-acquisition evidence is
complete and independently accepted:

- An independently accepted chain for the Ubuntu verifier and root keyring,
  including provenance for the exact verifier tools and their trust bootstrap.
- Independently authenticated Arch master-key identities and the
  date-applicable signer certification, designated-revoker, expiry, and
  revocation evidence needed for the 2026-09-09 keyring signature and
  2026-09-30 snapshot policy.
- The exact package-row identities and hashes for `archlinux-keyring
  20260909-1` and `pacman 7.1.0.r9.g54d9411-2`, plus the captured pacman
  signature packet issuer fingerprint, all bound to an explicit signer and
  signature-time policy. The packet fingerprint by itself is not a trust
  result.
- A reviewed, narrowly scoped acquisition allowlist and verification plan.

Only after a separate Stage A review may an explicitly bounded action acquire
the exact keyring and pacman archives/signatures. This audit grants no such
authorization; Stage A remains **STOP**.

### Stage B — post-acquisition verification and closure

After Stage A and separate authorization for its bounded acquisition, require
all of these before executing a resolver or allowing worker-image work:

- Verify the exact archive bytes and detached signatures under the accepted
  historical signer policy. Resolve the pacman packet fingerprint to the
  trusted signer; reject missing, ambiguous, expired, revoked, or
  out-of-window signatures.
- Review the authenticated pacman package and extracted `vercmp` identity,
  bind its source/package/signature/path/hash to the lock, and independently
  approve its execution. Check it against authoritative comparison vectors.
- Authenticate the 2026-09-30 repository databases and resolve a complete
  package closure. Verify every archive's repository, exact filename,
  version, architecture, size, digest, and detached signature. Include the
  worker base, account, kernel, and deterministic initramfs inputs.
- Produce and independently review a machine-readable lock of every package
  and its successful verification result.

If any signer, timestamp, certification, revocation, package row, signature,
resolver identity, or seed input is missing or ambiguous, remain at **STOP**.
Only after Stage B is accepted should separate worker-image, sealed-VM, and
production integration gates proceed. This audit imported no keys, executed
no package payloads, started no VM, and accessed no hardware or firmware.

## Local evidence reviewed

`audit.json` records the reviewed bundle names and local manifest counts.
`audit-inputs.txt` and `audit-inputs.sha256` bind the local documents and
metadata used for this audit. `SHA256SUMS` covers this bundle except itself.
The referenced source bundles retain their own raw files and manifests.
