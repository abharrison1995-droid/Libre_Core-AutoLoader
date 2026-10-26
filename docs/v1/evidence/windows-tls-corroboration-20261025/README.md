# Windows TLS corroboration for Ubuntu verifier archives (2026-10-25)

**Status: `ACCEPTED_TRANSPORT_CORROBORATION_ONLY`.** SOL accepted this separate, native-Windows capture as transport corroboration: Schannel retrieved the same three Ubuntu `.deb` byte sequences already recorded in [`../ubuntu-verifier-provenance-20261025/README.md`](../ubuntu-verifier-provenance-20261025/README.md). It does not independently provision or authenticate the Ubuntu root keyring, accept the verifier bootstrap, or authorize Arch acquisition.

The capture uses `C:\Windows\System32\curl.exe` only after confirming that `curl -V` identifies the Windows Schannel backend. `curl -V` stdout/stderr/exit are retained, and `Get-AuthenticodeSignature` reports `Valid` for this 711,736-byte curl executable; its SHA-256 is `3345339164cf384eff527b6c3160fea8d849a4231ec6ca80513e3a739e505168`, with signer thumbprint `667BBA1E1F478221AA65DE4F75187A751B03A453` (Microsoft Windows). The exact local signature record is `curl-authenticode-check.stdout.json`. Every package request explicitly passes an empty `--proxy` and `--noproxy *`; redirects are requested one hop at a time, and only HTTPS redirects to `archive.ubuntu.com` with the identical package path are allowed. For each HEAD and GET, the raw stdout/stderr, exact argv JSON, curl exit code, effective URL, response status, remote address, `ssl_verify_result`, and `%{certs}` PEM chain are retained. The script parses the peer certificates, builds a supplemental Windows `X509Chain` against the Windows certificate stores, records chain statuses and the terminal root thumbprint, and requires Schannel verification result 0 plus a clean chain ending at a root in CurrentUser or LocalMachine Root.

The downloaded files are compared byte-for-byte by size and SHA-256 with the package rows and pre-existing archive copies in the Ubuntu provenance bundle. No package is installed, extracted, or executed. No Arch key, package, or signature is acquired. C: free space was sampled before capture, at each package completion, and after capture; every recorded sample remained at or above 5 GiB. This is sampled evidence, not continuous monitoring.

## Captured result

The native process and script both exited `0` (`capture-process.exit`, `capture-run.exit`). Each package had one direct HTTPS request hop with HTTP 200, no redirect, and Schannel `ssl_verify_result=0`. The supplemental Windows chain had no status and ended at ISRG Root X1, thumbprint `CABD2A79A1076A31F21D253635CB039D4329A5E8`, present in a Windows Root store.

| Package | Bytes | SHA-256 | Schannel result | Windows root |
| --- | ---: | --- | ---: | --- |
| `gpgv` | 155,072 | `0fcce72656439d90821bbd23580afc794a01ec2f28622ae31b4f5394a830bb09` | 0 | ISRG Root X1 |
| `gpg` | 636,848 | `fb8da4f691b6e2139fa5cdf7f54d000ef1b7b27753efb2dbdc5a9546057a62c6` | 0 | ISRG Root X1 |
| `ubuntu-keyring` | 11,228 | `c377ccf26964f4c206c05c0bc7adb708e031beb919bb9a0ff63af983d064cd66` | 0 | ISRG Root X1 |

Total downloaded bytes: 803,148. All three downloaded sizes and SHA-256 values match their corresponding rows in the existing Ubuntu signed-index manifest and the pre-existing local archives. Exact `curl` argv, stdout/stderr, HTTP metadata, certificate PEMs, and individual curl exit files are retained under `logs/`; the full result is in `capture-summary.json`. The script invocation and process/script exits are retained at the top level. `capture-summary-correction.txt` documents that the initial post-run aggregation field incorrectly serialized as 0 due to `Measure-Object` operating on ordered dictionaries; the corrected total was recomputed offline from the three captured package rows. No request was repeated. The capture script was changed only to correct this aggregation expression after the downloads; the pre-correction script bytes were not separately preserved. The host facts and source-manifest hash binding are in `host-context.json`. `SHA256SUMS` covers regular files in this directory except itself.

## Trust boundary

This is independent transport corroboration only. It does not break the host-relative bootstrap cycle: the Ubuntu signed-index records and installed root keyring still require an independently authenticated provisioning source, and the already-installed `gpgv` remains host-relative. The evidence remains a proposal pending separate review. Arch key/package acquisition, key import, `vercmp` execution, closure generation, image/VM work, and hardware operations remain blocked.
