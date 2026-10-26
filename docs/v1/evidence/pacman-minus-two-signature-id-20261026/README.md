# Pinned pacman `-2` signature issuer metadata — 2026-10-26

## Result

The official date-scoped Arch Archive returned the 119-byte detached signature
for the pinned
[`pacman-7.1.0.r9.g54d9411-2` archive](https://archive.archlinux.org/repos/2026/09/30/core/os/x86_64/pacman-7.1.0.r9.g54d9411-2-x86_64.pkg.tar.zst.sig).
The version also appears in the Arch-generated
[pacman manual package metadata](https://man.archlinux.org/man/pacman.conf.5.en).
The captured signature SHA-256 is
`3e31debd8c57ac1c84c9586e57611e95e8a2ff7dfb8f3b54be95028ee36644e2`, exactly
matching the `PGPSIG` digest recorded for this row in the existing
[historical trust dossier](../arch-historical-trust-dossier-20261025/README.md).

The OpenPGP v4 signature packet contains issuer fingerprint
`0429897de5f3bdac537a30696d42bdd116e0068f` and issuer key ID
`6d42bdd116e0068f`. The parser in `inspect-signature.py` checks packet framing,
extracts the issuer-fingerprint subpacket, and confirms the exact signature
digest. **It does not verify the cryptographic signature.** No package archive
or public key was fetched, imported, extracted, or executed.

## Transport capture

`attempt-02/` is the corrected capture. Windows `curl.exe` 8.13.0 used
Schannel, HTTPS only, no proxy, no redirect, and a 64 KiB transfer cap. The
retained response reports HTTP 200, `Content-Length: 119`,
`ssl_verify_result=0`, and `Last-Modified: 2026-05-06 08:56:37 GMT`. The TLS
result is relative to this Windows host's active trust store. The
`Last-Modified` value is transport metadata, not a signature timestamp.

The first capture is retained at the evidence root. Its `curl --write-out`
format used PowerShell backticks that curl printed literally; the response
headers still show HTTP 200 and 119 bytes. Attempt 02 corrected the formatting
and produced the structured transport values above. Both signature files have
the same SHA-256.

## Trust conclusion

This identifies the issuer fingerprint embedded in the pinned pacman `-2`
signature and binds the captured 119-byte object to the pinned DB-row digest.
It does not establish who controls that key, the key's historical
certifications or revocation state, package-byte authenticity, or a valid
signature result. The trust gate, Arch package acquisition, authenticated
package closure, worker/image/VM execution, product boot, and hardware gates
remain blocked or pending. This evidence does not authorize Arch key/package
acquisition or any firmware/recovery operation.

`SHA256SUMS` covers every other file in this bundle.
