# Native Arch software regression — 2026-10-22

This evidence records software checks in a QEMU WHPX Arch guest. It is not an
ISO build, product boot, ROM validation, offline-worker proof, or hardware
validation record. The guest used for these checks had a user-mode network
adapter; no no-egress claim is made.

## Tested source and environment

The Linux source archive on the authorized ext4 workspace had SHA-256
`0e25b9e33a61ce9e7bc8b4c62a7acb1f7a64e6195bd91d26f8e0e79db55ac31a` and was
based on HEAD `94abf88b4d894c15c1d960f541da30c5a3e6659a`. It contained the
current guided UI overlays:

- `autoloader/ui/screens/x_workflow_action.py` —
  `C9AE81B78EBE1F14469B0643E2F0987385421D986B4868947AA56CB04D031882`
- `tests/test_ui.py` —
  `02ED80126FE6028B6E1046D606F3EFCB82F76B0B6A356566AB3E904F0D4B42D6`

Before the full run, the source copy received the following current files,
each verified against the Windows workspace hash:

- `pyproject.toml` —
  `0F63CBDACE06BDB4E11B9BCF1FA39E9D5D7C8C034829A1D4DDD1F466EB39181D`
- `usb_builder/rootless_offline_launcher.c` —
  `D3B81223EADFF085AE927DB00CC8A1B0982236A881E1C8700B39BE807AFC184D`
- `tests/test_rootless_offline_launcher_source.py` —
  `C7FC6F4B21DF2788B552B1890FE3CAABF23466DA296DBA079C8FF963E63E6732`

The guest ran Arch kernel `7.2.11-arch1-1`, Python `3.14.8`, and GCC
`16.2.1+r23+gd564253eb6c8-1`. Relevant packages were Archiso `91-1`,
arch-install-scripts `31-2`, util-linux `2.42.4-1`, bash `5.3.20-1`, and
pacman `7.1.0.r9.g54d9411-3`. The locked snapshot pins pacman
`7.1.0.r9.g54d9411-2`, so this is not exact pinned-snapshot build evidence.

## Full suite

From `/home/arch/librecore-source-v1-20261021`, the command was:

```sh
PYTHONPATH=. python -m pytest -q -ra -p no:cacheprovider \
  --basetemp=/home/arch/librecore-test-temp-v1-20261022 \
  --junitxml=/home/arch/librecore-native-arch-20261022.xml
```

Exit status: **0**. Result: **1,069 passed, 2 skipped, 0 failures/errors**
out of 1,071 tests in **1,246.93 seconds**. The skipped cases were:

- `tests/test_phase10a_write_approval.py:839` — Windows-specific production
  durability policy.
- `tests/test_usb_builder.py:5508` — Windows-specific host platform contract.

The retained JUnit report is `native-arch-full.xml`; the captured console
output is `native-arch-full.log`.

## Focused C parser checks

The exact command was:

```sh
PYTHONPATH=. python -m pytest -q -ra -p no:cacheprovider \
  --basetemp=/home/arch/librecore-launcher-test-temp-v1-20261022-run2 \
  --junitxml=/home/arch/librecore-launcher-test-v1-20261022.xml \
  tests/test_rootless_offline_launcher_source.py
```

Exit status: **0**. Result: **17 passed in 3.00 seconds**. This compiles and
checks the fixed-argument parser only. The production helper still returns
unavailable and does not create namespaces, mounts, children, or reports.
Retained files are `native-launcher-focused.xml` and
`native-launcher-focused.log`.

## Type and CLI checks

The exact checks were run sequentially from the source directory:

```sh
PYTHONPATH=. python -m mypy --no-incremental autoloader usb_builder
PYTHONPATH=. python -m autoloader --help
PYTHONPATH=. python -m autoloader build-usb --help
```

Exit status: **0** for all three. Mypy reported no issues in **111 source
files**. CLI output was redirected to `/dev/null`; the retained
`native-static-checks.log` records the successful output and each exit marker.

## Scope

The suite and focused checks are native Linux software evidence only. They do
not prove the no-NIC VM worker, a current-source ISO build, BIOS/UEFI boot, or
any firmware workflow. The old rootless launcher pin is still absent; offline
build execution remains disabled. No firmware write, recovery action, or
physical hardware operation occurred. The T430, X200, and X220 workflows
remain **HARDWARE PENDING**.
