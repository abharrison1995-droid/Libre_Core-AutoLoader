# X200 upstream compile smoke — 2026-10-15

**Outcome: timed out; no validated ROM artifact.** This is an unaccepted compiler diagnostic only. The command did not complete the selected recipe and did not run ArtifactEngine completion, device operations, media writes, or hardware actions.

The disposable run was `/home/alex/librecore-x200-upstream-smoke-20261015T185606Z-ca594b8e` on Ubuntu 26.04 WSL. It used the pinned Libreboot `lbmk` source from `https://codeberg.org/libreboot/lbmk.git`, commit `1cf3181537b8d1fe1df0e91681f700850a3d9bf6`, tree `0cb9451c6e2e265d3aa5b7e371752d45cab98f53`; all 35 repository source pins passed preflight.

The only firmware build command was `./mk -b coreboot x200_4mb`. It ran from 2026-10-15 18:56:09 UTC to 19:56:09 UTC (3600.025 seconds). U-Boot built; Coreboot fetched and patched its source and entered `crossgcc-i386`; GMP completed and MPFR had just started when the bound expired. The runner sent SIGTERM and then bounded SIGKILL to its isolated process group. No build process remained afterward.

The checked-in U-Boot objcopy overlay was `0009-x86-use-output-target-for-efi.patch` (SHA-256 `03c17c53372864265bb6716b96ad90022dcc1b84d5ebd7a54fd93820be8b55e4`). The GRUB Unifont overlay was `0010-lbmk-grub-explicit-unifont.patch` (SHA-256 `3356bf29879cf87659230a201ab81f5e45cc5a0e9c80161463a9b868955f036d`). The staged Unifont font SHA-256 was `0e3981ab552231b5a2a870f2b61741903a4bf25c23ef5aeb05fdced1b3c7af4d`, matching `/usr/share/fonts/opentype/unifont/unifont.otf`; package was `fonts-unifont` version `1:16.0.04-1build1`.

Tool evidence recorded Git 2.53.0, GNU Make 4.4.1, GCC/G++ 15.2.0, and GNU objcopy 2.46 at `/usr/bin/x86_64-linux-gnu-objcopy`, SHA-256 `05f4473d24f7330a9b13f43d007d619a2e792e33de453cd7533a2d97c30da770`.

The recipe expected 31 outputs of 4,194,304 bytes each. The `bin/x200_4mb` output directory was absent at termination; zero ROMs were produced, so there was no CBFS inventory to inspect. No ROM was copied into the repository. There is no COMPLETE manifest.

Evidence files: `runner-report.json` is the raw runner report; `upstream-build.log` is the full build log; `logs/01.log`–`logs/10.log` preserve preflight and overlay command records. The raw runner report's `build_status` field says `RUNNING`, but its `build_exit=-1`, end time, `build_stop_reason`, and `failure` record the terminal timeout. The raw report SHA-256 is `36e2b6b7eba779657101f4edac25aae2f6903030e9a8cae73d3b3070c5e7d7e4`; the build-log SHA-256 is `13eb78af02982e08b4c17ea1697f053991b965d2de3300503102ee1cd46ab3da`.

This evidence does not establish a successful X200 ROM build, firmware correctness, device compatibility, or any hardware result.
