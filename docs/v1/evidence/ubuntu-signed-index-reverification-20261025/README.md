# Ubuntu signed-index and package-byte re-verification — 2026-10-25

**Status: `ACCEPTED_HOST_RELATIVE_PROPOSAL_ONLY` (bounded re-verification
passed).** The exact
retained Ubuntu metadata and three `.deb` archives were rechecked with the
installed `gpgv` after proving its bytes match the exact Schannel-captured
`gpgv` package payload. This is a consistency/re-verification record only. It
does not independently bootstrap Ubuntu trust, authorize Stage A, or authorize
Arch package/key acquisition.

## Successful bounded run

`runs/attempt-06/` contains the exact command records, logs, extracted package
payloads, environment summary, JSON result, and exit files. The run executed
under Ubuntu WSL 2 as `alex` (uid/gid 1000), with Python 3.14.4 and installed
`gpgv` 2.4.8. Its captured UTC was `2026-10-25T22:22:34.532307+00:00`.
The runner, verifier, and WSL process all exited `0`.

The helper revalidated all entries in the existing Windows TLS bundle (46
entries) and Ubuntu APT provenance bundle (104 entries), then checked every
staged input hash. It used only those retained local captures; this phase made
no network request and installed no package. It re-extracted the hash-matched
`gpgv` and `ubuntu-keyring` `.deb` payloads under the unique run directory.
The extracted `gpgv`, `/usr/bin/gpgv`, and the Windows Schannel-captured
`gpgv` `.deb` payload agree by the recorded archive and executable hashes.
The extracted Ubuntu archive keyring matches the separate Windows TLS
keyring capture byte for byte.

Before signature checks, the verified `gpgv` executable was inspected using
its local manpage, `gpgv --help`, `readelf`, and `ldd`. The ELF interpreter,
`DT_NEEDED` names, resolved runtime object paths, sizes, and SHA-256 values are
retained in `runs/attempt-06/verification-summary.json`. Those runtime and
toolchain observations describe this WSL host; they are not independent
provenance for its libraries or system image. The manpage says gpgv has no
configuration files and that an explicit `--keyring` suppresses lookup of the
default keyring. The help output advertises `--keyring`, `--status-fd`, and
`--output`; the manpage documents `--homedir`. Unsupported `--no-options`,
`--batch`, and `--no-default-keyring` options were omitted.

For both checks, the helper created a fresh mode-0700 empty directory under
`/tmp`, set it as both `HOME` and `GNUPGHOME`, passed it with `--homedir`, and
passed the exact captured keyring with `--keyring`. The environment was
restricted to `HOME`, `GNUPGHOME`, `LANG=C`, `LC_ALL=C`, `PATH=/usr/bin:/bin`,
and `TZ=UTC`. It confirmed the directory was empty before and after use and
removed only that freshly created directory after verification. The exact argv
and captured stdout/stderr/exit are in the per-command `*.argv.json`,
`*.stdout.bin`, `*.stderr.bin`, and `*.exit` files.

Both retained clear-signed `InRelease` files produced a good signature from
primary fingerprint
`F6ECB3762474EDA9D21B7022871920D1991BC93C`:

- `resolute_InRelease`: `Date: Thu, 23 Apr 2026 17:07:15 UTC`, suite and
  codename `resolute`.
- `resolute-updates_InRelease`: `Date: Sun, 25 Oct 2026 19:14:45 UTC`, suite
  `resolute-updates`, codename `resolute`.

Neither signed Release contains `Valid-Until`. At the captured local UTC clock,
the helper rejected dates more than five minutes in the future and applied
explicit maximum ages of 400 days for the base pocket and 14 days for
`resolute-updates`; both were within those bounds. The complete parsed fields,
clock, age values, and decisions are retained in the JSON summary. This is a
bounded freshness check, not an independent trusted-time source.

The signed Release SHA256 rows bind the retained `main/binary-amd64/Packages.xz`
files by exact size and hash. Each expected package appeared exactly once in
its indicated index, and each signed index row's name, version, architecture,
filename, size, and SHA-256 matched the local `.deb` and the independently
captured Windows TLS `.deb` bytes:

- `gpgv` 2.4.8-4ubuntu3.1, amd64, 155,072 bytes;
- `gpg` 2.4.8-4ubuntu3.1, amd64, 636,848 bytes;
- `ubuntu-keyring` 2023.11.28.1build1, all, 11,228 bytes.

The detailed rows and all hashes are in `runs/attempt-06/verification-summary.json`.

## Failed harness attempts retained

All earlier attempts and their outputs are preserved; no prior extraction or
log tree was removed or overwritten.

1. `logs/initial-failure-input-name/`: the helper expected the wrong local
   Packages filename and stopped before extraction or gpgv execution.
2. `logs/second-failure-gpgv-no-options/`: this installed gpgv rejected
   `--no-options`; no signature result was produced.
3. `logs/third-failure-extraction-replay-guard/`: the helper correctly
   refused the old extraction path with the exact error recorded in
   `exact-refusal.txt`: `ValueError: refusing pre-existing extraction target:
   /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader/docs/v1/evidence/ubuntu-signed-index-reverification-20261025/extracted/gpgv`.
   It did not overwrite that path.
4. `logs/fourth-failure-manpage-policy/`: the roff manpage's formatting caused
   the first supported-option text check to fail closed before any signature
   check. The exact exception and command logs are retained. Its copied
   root-level `gpgv-resolute_InRelease.*` logs are stale outputs from the
   earlier unsupported-option attempt, as identified by
   `stale-log-attribution.json`; they are not attributed to this failure.
5. `runs/attempt-05/`: the harness counted its own shell-created stdout/stderr
   files as pre-existing run contents and exited `73` before extraction or
   gpgv execution. This attempt is retained; the guard was narrowed to the
   extraction/runtime directories, and the successful retry used a new run
   ID (`attempt-06`).

The legacy root-level `verification-summary.json`, `verification.exit`,
`capture-process.exit`, and `logs/runner.*` remain the preserved fail-closed
manpage-policy attempt. The final successful result is only
`runs/attempt-06/verification-summary.json` with its adjacent exit records.

The run-directory scheme now requires a fresh `attempt-NN` identifier and
refuses to reuse an existing run path. Every attempt keeps its own log,
extraction, runtime, summary, and exit outputs.

## Trust boundary and remaining gate

This result is host-relative. The Windows Schannel capture corroborates
transport and exact bytes, but relies on the active Windows root store and its
captured revocation limits. The WSL signature check uses a keyring whose
provisioning is not independently authenticated here; the installed
`gpgv`/runtime observations also depend on the local Ubuntu environment. A
valid Ubuntu signature under that locally present keyring therefore does not
close the independent Ubuntu verifier/root-keyring bootstrap. The Arch master
key and package signer certification/revocation chain are also not established.

No Arch keys, package archives, signatures, or resolver were acquired or
executed. No package was installed. No image, VM, guest, product build/boot, or
hardware operation was performed. SOL accepted this bundle as a host-relative
proposal only. Stage A and all Arch acquisition remain blocked pending the
independent trust-chain evidence.

## Integrity

`SHA256SUMS` covers the regular files in this evidence directory except the
manifest itself. Verify from this directory with:

```sh
sha256sum --check SHA256SUMS
```
