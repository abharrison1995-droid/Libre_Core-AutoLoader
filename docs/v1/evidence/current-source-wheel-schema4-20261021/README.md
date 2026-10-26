# Current-source wheel smoke — 2026-10-21

**Result: PASS — wheel assembled, all four pinned patches present, extracted
package imports resolved inside the extraction target, and CLI help worked.**
This is package/CLI smoke evidence; it is not an ISO, firmware, or hardware
result.

- Environment: Ubuntu 26.04 WSL, Python 3.14.4, setuptools build backend.
- Source snapshot: 350 files from `README.md`, `pyproject.toml`,
  `autoloader/`, and `usb_builder/`; SHA-256
  `274100743a805ea8b998268f41a347a5fd2e21ce815df8dea77687ce03a56af6`.
- Wheel: `libre_core_autoloader-0.1.0-py3-none-any.whl`, 413,727 bytes,
  SHA-256 `e79afc775331daae6ba4cdac5701d6e1197f48ce9aad11df9649663ae7721bc9`.
- The wheel contains the X200 U-Boot, GRUB/Unifont, and vboot `cbfstool`
  patches and the Arch `pacstrap` status-preservation patch. The vboot patch
  hash matches its source pin.
- The wheel was extracted to a disposable directory. `autoloader` and
  `usb_builder` both imported from inside that extraction. Top-level CLI help
  and `flash --help` exited 0 from the extracted wheel.
- `smoke.py`, `run.json`, the build log, and the exact wheel are preserved in
  this folder. `SHA256SUMS` covers each preserved file other than itself.

The smoke did not install dependencies into a clean offline environment and
did not build or boot a Live USB. No firmware or media operation occurred.
