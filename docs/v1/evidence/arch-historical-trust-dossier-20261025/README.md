# Arch historical trust dossier — 2026-10-25

**Status: `PENDING_SOL_REVIEW_ACQUISITION_BLOCKED`.** This is a bounded
official-source research record. It is not Stage-A authorization and does not
establish historical signer trust. No Arch key material, package archive, or
signature body was fetched; no key was imported and no package was extracted
or executed.

## What the current official master-key page says

The current [Arch Master Signing Keys page](https://archlinux.org/master-keys/)
was captured from `https://archlinux.org/master-keys/` on 2026-10-25 at
22:35:18 UTC. It calls these five keys the **current** master-key set and says
each is held by a different developer, with its revocation certificate held by
another developer. Its maintainer policy says repository maintainers should
have signatures from at least three distinct master keys.

| Current owner | Full primary fingerprint | Owner signing key ID | Designated revoker | Revoker signing key ID |
| --- | --- | --- | --- | --- |
| Levente Polyak | `D8AFDDA07A5B6EDFA7D8CCDAD6D055F927843F1C` | `8D8172C8` | Evangelos Foutras | `A9999C34` |
| David Runge | `2AC0A42EFB0B5CBC7A0402ED4DC95B6D7BE9892E` | `5BF0D338` | Christian Hesse | `498E9CEE` |
| Johannes Löthberg | `69E6471E3AE065297529832E6BA0F5A2037F4F41` | `3A9D0BB5` | Maxime Gauduin | `81506130` |
| Leonidas Spyropoulos | `3572FA2A1B067F22C58AF155F8B821B42A6FDCD7` | `7C7FD0EC` | Frederik Schwan | `5426DA0A` |
| Christian Heusel | `99B6618472A3B3B814185BAED7D3D823B88BDB9B` | `4C197E3D` | Robin Candau | `92ACA748` |

These are fingerprints and revoker assignments shown by the page at capture
time; this dossier did not fetch or validate key packets, owner signatures,
certifications, revocation certificates, expiry, or signature timestamps.
The current page is **not historical evidence** for the key set or signer
certifications/revocations on 2026-09-30, or at the keyring package signature
time of 2026-09-09. No dated Arch master-key snapshot or equivalent official
historical key-state record was located in this bounded search. That remains a
trust-chain gap.

## Dated release evidence and the pinned database rows

The existing snapshot lock is
[`../../../../usb_builder/snapshot.lock.json`](../../../../usb_builder/snapshot.lock.json),
SHA-256
`5d66df5f5e6eacd0770399985b65507e84f208a7fbd1352faca13c0ead456cb1`.
Existing metadata records bind the 2026-09-30 `core.db` to 129,884 bytes and
SHA-256
`08d52f5e83f72a9e9f8464441da87b9a399f4decc7110fcfa3825f7c0af42dd2`, and
`extra.db` to 8,865,765 bytes and SHA-256
`d2262e9def7afc76960c5242624cc14029afa47a9407acd84a29aed3a44f1663`. The
source records are
[`seed-inventory.json`](../worker-package-seed-inventory-20261025/seed-inventory.json)
and
[`trust-manifest-proposal-20261025.json`](../worker-package-seed-policy-20261025/trust-manifest-proposal-20261025.json).
This dossier reuses those pinned values and rows; it did not re-fetch or
recompute the database bytes.

The two exact package rows in the pinned metadata are:

| Package | Repo / version / arch | Filename | DB-declared size | DB-declared archive SHA-256 | DB-bound `PGPSIG` SHA-256 |
| --- | --- | --- | ---: | --- | --- |
| `archlinux-keyring` | `core` / `20260909-1` / `any` | `archlinux-keyring-20260909-1-any.pkg.tar.zst` | 1,273,532 | `6a8ea16f51d3b305c18322d5884ca959f12c32a3f186873090fdc18465605293` | `5942acd5b5e27a45051c447b8054696465872e834af1a5bd3cb85e2881cf6cf8` |
| `pacman` | `core` / `7.1.0.r9.g54d9411-2` / `x86_64` | `pacman-7.1.0.r9.g54d9411-2-x86_64.pkg.tar.zst` | 991,730 | `2092ec7a0391416e4a1757c455bc071d7b58a124cedc83f582cb7d37d52f211b` | `3e31debd8c57ac1c84c9586e57611e95e8a2ff7dfb8f3b54be95028ee36644e2` |

The `PGPSIG` hashes are digests of signature metadata embedded in the pinned
database records. They are not independent signature-file pins or proof that
the DB or package signature is authentic. Both package rows remain
`signature_verified: false`; the package archives and detached signatures
remain unacquired.

The [official archlinux-keyring package page](https://archlinux.org/packages/core/any/archlinux-keyring/)
currently shows `20260909-1`, architecture `any`, Christian Hesse as the last
packager and listed signer, build time 2026-09-09 07:45 UTC, signature time
2026-09-09 07:47 UTC, and update time 09:25 UTC. The official Arch packaging
project has the immutable source commit
[`56acf30f9492d6b6e9cc7c16fd1d969afc462046`](https://gitlab.archlinux.org/archlinux/packaging/packages/archlinux-keyring/-/commit/56acf30f9492d6b6e9cc7c16fd1d969afc462046),
whose captured page identifies the release as `upgpkg: 20260909-1: new
upstream release` and records the commit at 2026-09-09 07:47:14 UTC by
Christian Hesse. Inspection of the captured commit diff shows only `.SRCINFO`
and `PKGBUILD` changed: the packaging recipe moves its signed upstream source
tag from `20260902` to `20260909`. This commit is packaging/source metadata;
it does **not** contain the keyring keyset. These release records corroborate
version and chronology; they do not cryptographically verify the package
signature or authenticate the signer’s master-key certifications/revocation
state.

The current official [pacman package page](https://archlinux.org/packages/core/x86_64/pacman/)
shows `7.1.0.r9.g54d9411-3`, not the pinned `-2`. It therefore does not
establish the exact `-2` signer. The pinned DB row has `Christian Hesse` as
packager metadata, which is not proof that he signed that archive.

## Official dated Archive endpoint observations

The official `https://archive.archlinux.org/repos/2026/09/30/` endpoint was
queried using Windows `curl.exe` 8.13.0 with Schannel, `--noproxy '*'`, HTTPS
only, and no redirects in the observed responses. All successful responses
reported `ssl_verify_result=0`; response headers, complete curl certificate
chains, exact argv, stderr and exit codes are retained here. The Archive
responses were dated 2026-10-25 UTC.

| HEAD-only URL | HTTP | Content-Length | Last-Modified | Result |
| --- | --- | ---: | --- | --- |
| `.../2026/09/30/core/os/x86_64/` | 200 | not supplied | not supplied | Dated core index endpoint reachable |
| `.../2026/09/30/core/os/x86_64/core.db` | 200 | 129,884 | 2026-09-29 20:58:06 GMT | Length matches pinned `core.db` metadata |
| `.../2026/09/30/extra/os/x86_64/extra.db` | 200 | 8,865,765 | 2026-09-30 12:05:49 GMT | Length matches pinned `extra.db` metadata |
| `.../2026/09/30/core/os/x86_64/archlinux-keyring-20260909-1-any.pkg.tar.zst` | 200 | 1,273,532 | 2026-09-09 07:47:17 GMT | Length matches keyring row; no body fetched |
| `.../2026/09/30/core/os/x86_64/pacman-7.1.0.r9.g54d9411-2-x86_64.pkg.tar.zst` | 200 | 991,730 | 2026-05-06 08:56:37 GMT | Length matches pacman row; no body fetched |

The date-scoped official Archive paths and returned lengths support the
existence of the dated snapshot and exact artifact names. The recorded ETags
and Last-Modified values are HTTP transport metadata only; they are not
content-authenticity evidence. A `HEAD` response does not verify file content,
the DB’s package rows, or any signature. In
particular, the package archive SHA values above still come only from the
existing pinned metadata; this dossier did not hash archive bytes. The
keyring file’s Last-Modified timestamp is close to the official page’s
signature time, but neither timestamp authenticates a signer. The `pacman -2`
Last-Modified value predates the snapshot directory date; it is not the
package signature time.

The first capture script applied a 512 KiB `--max-filesize` guard to HEAD
requests as well as documentation GETs. Curl returned exit 63 for `extra.db`
and both package metadata requests after reading their response headers; no
response bodies were written. Those failures are preserved. A separate
HEAD-only retry omitted the size guard, still wrote no bodies, and returned
HTTP 200 / exit 0 for all three URLs. The raw first attempt and retry remain
separate.

## Trust conclusion and remaining gate

The official sources provide a current master-key policy, five current
fingerprints and designated revokers; an official release page and immutable
source commit for `archlinux-keyring 20260909-1`; and a date-scoped Archive
path whose `HEAD` metadata matches the two pinned package-row sizes. They do
**not** provide historical evidence that the current master set, the required
three certifications, or designated-revocation status applied on
2026-09-30 / 2026-09-09. The `archlinux-keyring` page’s “Signed By” label is a
site assertion, not verification performed here. The exact `pacman -2`
signer remains unknown; current `pacman -3` information cannot be projected
back to `-2`.

Stage A remains blocked until independently authenticated Arch master-key
material and dated certification/revocation evidence are reviewed, the exact
keyring and pacman detached signatures are obtained and verified under the
accepted policy, and the verifier chain is accepted. This dossier does not
change the prior proposal review outcomes and grants no acquisition
authorization.

## Captured files and checks

[`SHA256SUMS`](SHA256SUMS) records the SHA-256 digest of every file in this
dossier except the checksum manifest itself.

- [`snapshot-binding.json`](snapshot-binding.json) binds the existing lock,
  DB hashes and package rows to the captured source observations.
- [`capture-official-sources.ps1`](capture-official-sources.ps1) records the
  allowlisted first-pass capture workflow. Exact per-request argument vectors,
  output headers, transport/certificate output, stderr and exit codes are
  retained alongside each page.
- [`capture-archive-heads-retry.ps1`](capture-archive-heads-retry.ps1) and
  [`attempt-02-head-only/`](attempt-02-head-only/) retain the bounded HEAD-only
  retry after the first pass’s size-guard failures.
- [`capture-index.json`](capture-index.json) records all first-pass outcomes.

Windows Schannel transport validation is host-relative. `ssl_verify_result=0`
and the captured certificate chain establish what this Windows curl process
reported; they do not independently authenticate the Windows root store or
prove Arch signer trust. No Arch key bytes, package bytes, signature bytes,
key imports, package extraction, verifier execution, `vercmp` execution,
package closure, image, VM, worker, or hardware action occurred.
