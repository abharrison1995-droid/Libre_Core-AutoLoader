# Current-source wheel smoke after X200 CBFS parser fix — 2026-10-21

**Result: PASS.** The wheel assembled from the source snapshot containing the
reviewed X200 null-interval overlap check. The smoke verified the pinned patch
resources, extracted imports for `autoloader` and `usb_builder`, and top-level
and `flash` CLI help.

- Environment: Ubuntu 26.04 WSL, Python 3.14.4. Temporary build and extraction
  files used the user-authorized ext4 workspace.
- Source snapshot: 350 files; SHA-256
  `626b26ed30b27ec0e4c7fcfc75d0fbaef09327ec3e58b906abcb1c5fca4215d2`.
- Wheel: 417,524 bytes; SHA-256
  `12fa189a8b662183060d1c1c0d3a85af70e6b3d3099087f974896d3e26186da8`.
- The wheel contains the X200 U-Boot, GRUB/Unifont, Vboot `cbfstool`, and
  Arch pacstrap status-preservation patches.
- `smoke.py`, `run.json`, `build.log`, and the exact wheel are preserved here;
  `SHA256SUMS` covers those files.

This is packaging and CLI smoke evidence. It does not validate a Live USB
image, native Linux build VM, firmware write, or hardware workflow. No media or
hardware operation occurred.
