# Ubuntu verifier provenance candidate (2026-10-25)

**Status: `ACCEPTED_HOST_RELATIVE_PROPOSAL_ONLY`.** SOL accepted this as a non-installing consistency/provenance proposal for Ubuntu tools proposed as a Stage-A verifier. It is not an accepted trust anchor, does not independently authenticate the installed Ubuntu root keyring or verifier provisioning, and does not authorize Arch key/package acquisition.

## Result

In Ubuntu 26.04.1 LTS WSL (`amd64`), the exact signed `Packages.xz` rows for the installed packages `gpgv 2.4.8-4ubuntu3.1`, `gpg 2.4.8-4ubuntu3.1`, and `ubuntu-keyring 2023.11.28.1build1` were located. Three `.deb` archives were fetched over direct HTTP with `curl --noproxy '*'`; each archive's size and SHA-256 matched its row in a `Packages.xz` index whose compressed size/hash was present in a signature-verified Ubuntu `InRelease`. Each archive was unpacked with `dpkg-deb --extract` into this evidence directory, without package installation or execution of any extracted payload binaries. The selected installed file from each package matched the extracted file byte-for-byte:

| Package | Version / architecture | Archive size | Archive SHA-256 | Installed payload SHA-256 |
| --- | --- | ---: | --- | --- |
| `gpgv` | `2.4.8-4ubuntu3.1` / `amd64` | 155,072 | `0fcce72656439d90821bbd23580afc794a01ec2f28622ae31b4f5394a830bb09` | `/usr/bin/gpgv`: `95ecd00d02b79d091f103af175b1dbf95b5b0c66503104bfcea447cca7e3829d` |
| `gpg` | `2.4.8-4ubuntu3.1` / `amd64` | 636,848 | `fb8da4f691b6e2139fa5cdf7f54d000ef1b7b27753efb2dbdc5a9546057a62c6` | `/usr/bin/gpg`: `c8f3dc07a34ab0a0a081368b482724b79a2a6d6b7aa856741c09edf7af854754` |
| `ubuntu-keyring` | `2023.11.28.1build1` / `all` | 11,228 | `c377ccf26964f4c206c05c0bc7adb708e031beb919bb9a0ff63af983d064cd66` | `/usr/share/keyrings/ubuntu-archive-keyring.gpg`: `80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31` |

`gpgv` returned exit 0 for the three retained `InRelease` files using the already-installed Ubuntu keyring. The reported primary fingerprint was `F6ECB3762474EDA9D21B7022871920D1991BC93C`. The base `resolute` Release is dated 2026-04-23; `resolute-updates` is dated 2026-10-25 19:14:45 UTC; `resolute-security` is dated 2026-10-25 13:27:49 UTC. The `gpgv` and `gpg` archive rows are identical in the security and updates indexes; this capture selected the updates index. The keyring archive row is in the older base index.

This proves a hash-linked consistency chain relative to the Ubuntu archive keyring present in this WSL image. It **does not independently authenticate the provisioning or identity of that installed root keyring or the installed `gpgv` binary**. Installed `gpgv` is itself used to validate the InRelease files that bind the package rows; both ends of that check are host-relative until their provisioning is independently established. The same keyring was used to verify the Release that binds the keyring package archive, so that comparison is circular as an independent bootstrap proof. `dpkg-deb` only unpacked the already hash-checked archive; its installed origin was not independently re-verified here. The captured mode bits under the C: mounted evidence directory are not treated as package mode evidence.

The system APT source file and the private refresh source are retained in `manifest.json`; the private source was restricted to Ubuntu `main` and explicitly used `Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg`. The recorded APT policy had `AllowInsecureRepositories`, `AllowDowngradeToInsecureRepositories`, and `AllowWeakRepositories` set to `0`; the listed APT/GPG/proxy override environment variables were unset. Values of any such variables, had they been present, would not have been recorded. No credential-bearing source URI was found. The private `apt-get update` used a separate `Dir::State::lists`; its output and exit 0 are retained. Its original full argv was not captured, so that refresh invocation is not represented as an exact command transcript. Exact argv for the direct `gpgv`, `curl`, `apt-config`, and `dpkg-deb` operations and the retained `dpkg-query -W` package-identity queries are in the corresponding `logs/*.log` files. The `dpkg-query -S` owner results are recorded in the manifest, but their argv was not separately logged.

The signed index file SHA-256/size rows are verified by `verify_bundle.py` and recorded in `manifest.json`. The raw signed `InRelease`, raw compressed indexes, downloaded archives, extraction trees, command logs, and exit files are all listed in `SHA256SUMS`. The five focused synthetic validator tests passed; their command, log, JUnit XML, and exit are retained. `focused-initial-failed.junit.xml` preserves an earlier failed test attempt caused by two test-helper expectation issues that were corrected before the passing run; it is not a product test result.

## Review boundary

This bundle does not import keys, install packages, run the extracted `gpg`/`gpgv`, run `vercmp`, resolve an Arch package closure, build an image, launch a VM, or touch firmware/hardware. No Arch keys, signatures, or package archives were fetched. The existing root-keyring provenance remains the blocking trust gap; this evidence is a candidate proposal only. The exact APT trust-chain review, independently sourced Ubuntu root-keyring proof, and independent `dpkg-deb`/verifier provenance review remain required before this can support any Arch bootstrap.

## Focused check

The final captured check ran under `/home/alex/.venvs/autoloader-v1/bin/python` (Python 3.14.4, pytest 9.1.1):

```text
wsl -d Ubuntu -- /home/alex/.venvs/autoloader-v1/bin/python -B /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader/docs/v1/evidence/ubuntu-verifier-provenance-20261025/run_focused_checks.py
5 passed in 13.60s
exit 0
```

See `focused-final.pytest.log`, `focused-final.pytest.exit`, and `focused-final.junit.xml`. `SHA256SUMS` covers the bundle's regular files except itself; `SYMLINKS.txt` records symlink paths/targets. Run `python -B write_checksums.py --verify` to validate both the file set and bytes/links, or `sha256sum -c SHA256SUMS` to independently check regular-file bytes.
