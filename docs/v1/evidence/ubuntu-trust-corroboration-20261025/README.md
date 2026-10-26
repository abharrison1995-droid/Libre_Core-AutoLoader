# Ubuntu trust-root corroboration — accepted host-relative proposal only — 2026-10-25

**Status: `ACCEPTED_HOST_RELATIVE_PROPOSAL_ONLY`; Stage A remains blocked.** SOL
accepted this bounded capture as a host-relative proposal. It adds
Windows-native HTTPS corroboration for Ubuntu's own verification
documentation and its published archive keyring. It does not authorize
Ubuntu trust bootstrap or Arch key/package acquisition.

## Captured material and result

Windows System32 `curl.exe` 8.13.0 used Schannel. Windows Authenticode reported
`Valid`; signer subject was Microsoft Windows, SHA-1 thumbprint
`667BBA1E1F478221AA65DE4F75187A751B03A453`, and the executable SHA-256 was
`3345339164cf384eff527b6c3160fea8d849a4231ec6ca80513e3a739e505168`. The
captured identity is in [`curl-identity.json`](curl-identity.json); the exact
curl argument vectors, standard output/error and exit codes for each request
are in `logs/`.

Three requests used HTTPS-only redirects, an empty explicit proxy and
`--noproxy *`, with the script's fixed allowlist limited to
`documentation.ubuntu.com` and `archive.ubuntu.com`:

- [Ubuntu image verification documentation](https://documentation.ubuntu.com/security/software-integrity/image-verification/), captured as [`image-verification.html`](downloads/image-verification.html).
- [Ubuntu archive verification documentation](https://documentation.ubuntu.com/security/software-integrity/archive-verification/), captured as [`archive-verification.html`](downloads/archive-verification.html).
- Ubuntu's documented archive-keyring URL, `https://archive.ubuntu.com/ubuntu/project/ubuntu-archive-keyring.gpg`, captured as [`ubuntu-archive-keyring.gpg`](downloads/ubuntu-archive-keyring.gpg).

All three requests returned HTTP 200 with zero redirects and Schannel
`ssl_verify_result=0`. `%{certs}` output, the peer chain, supplemental
Windows-built chain, root-store presence, effective URL and remote address
are retained in the corresponding `logs/*.stdout.txt` and
[`capture-summary.json`](capture-summary.json). The built chain terminated at
ISRG Root X1 (SHA-1 thumbprint
`CABD2A79A1076A31F21D253635CB039D4329A5E8`; certificate SHA-256
`96bcec06264976f37460779acf28c5a7cfe8a3c0aae11a8ffcee05c0bddf08c6`), present
in the Windows CurrentUser/LocalMachine Root store at capture time.

The Schannel requests did not pass `--ssl-no-revoke`. Curl's reported
successful validation is retained, but no separate OCSP/CRL response or
revocation transcript was captured. The supplemental `.NET X509Chain` used
`RevocationMode=NoCheck`; its successful build is not revocation evidence.
The Windows root-store result is also relative to this Windows installation
and its certificate provisioning. The capture does not independently
authenticate that store.

The official image-verification page published these three full primary
fingerprints, and all appeared in both the downloaded keyring and the
existing `ubuntu-keyring` package payload:

| Published key label | Full primary fingerprint |
|---|---|
| Ubuntu Archive Automatic Signing Key (2012) | `790BC7277767219C42C86F933B4FE6ACC0B21F32` |
| Ubuntu CD Image Automatic Signing Key (2012) | `843938DF228D22F7B3742BC0D94AA3F0EFE21092` |
| Ubuntu Archive Automatic Signing Key (2018) | `F6ECB3762474EDA9D21B7022871920D1991BC93C` |

The downloaded archive keyring and the payload extracted earlier from
`ubuntu-keyring_2023.11.28.1build1_all.deb` are byte-identical: 3,607 bytes,
SHA-256
`80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31`.
The package archive was 11,228 bytes with SHA-256
`c377ccf26964f4c206c05c0bc7adb708e031beb919bb9a0ff63af983d064cd66`.
The machine-readable comparison is
[`keyring-fingerprint-comparison.json`](keyring-fingerprint-comparison.json).

Fingerprint parsing used the pre-existing Ubuntu WSL `/usr/bin/gpg` 2.4.8,
whose recorded SHA-256 is
`c8f3dc07a34ab0a0a081368b482724b79a2a6d6b7aa856741c09edf7af854754`. It ran
only `--list-keys` against explicit keyring files with `--batch
--no-options --no-default-keyring`; it did not import keys or execute any
downloaded payload. This parser binary remains host-relative: its package
provenance was not independently established. The first list attempt exited
2 because `/home/alex/.gnupg` did not exist; that diagnostic is preserved in
[`initial-missing-default-home`](logs/initial-missing-default-home/). The
corrected run used a new mode-0700 private homedir, then removed only that
exact scratch directory after recording its contents and exit status; see
[`gnupghome-cleanup.json`](gnupghome-cleanup.json).

## Trust boundary and remaining gate

The capture assumes Windows' active certificate stores and the HTTPS identity
of the two official Ubuntu hostnames. The downloaded HTML and keyring were not
independently signature-authenticated. Matching the keyring to the earlier
`.deb` payload and to fingerprints in the captured official page provides
cross-corroboration, but it does not establish an independent cryptographic
trust anchor for Ubuntu's signing keys.

The earlier Ubuntu signed-index validation remains host-relative because the
installed WSL `gpgv` and Ubuntu root-keyring provisioning have not been
independently authenticated. This acceptance is not an independent trust
anchor or Stage-A authorization. Stage A and all Arch
key/package/signature/resolver acquisition remain blocked pending an
independently authenticated verifier/root-keyring provenance chain. No Arch
material was acquired or imported, no software was
installed, no downloaded payload binary was executed, no VM or hardware
operation was started, and no firmware or recovery action occurred.

The capture script restricts requests to the two fixed Ubuntu HTTPS hosts,
records per-request free space, and requires at least 5 GiB free before and
after each request. C: had 6,306,697,216 bytes free before capture and
6,306,439,168 bytes after; see [`capture-start.txt`](capture-start.txt) and
[`capture-finish.txt`](capture-finish.txt). Exact invocation and environment
are retained in [`capture-invocation.txt`](capture-invocation.txt),
[`capture-start.txt`](capture-start.txt), and [`capture-finish.txt`](capture-finish.txt).

`SHA256SUMS` covers every regular file in this bundle except itself.
