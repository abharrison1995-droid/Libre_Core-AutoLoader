# Current-source wheel smoke — 2026-10-21

**Result: PASS — package build and extracted-wheel import with path assertions.** This does not establish Live USB build/boot, firmware build, or hardware acceptance.

- Environment: Ubuntu 26.04 WSL, Python 3.14.4, setuptools 78.1.1.
- Source snapshot: 349 files from `README.md`, `pyproject.toml`, `autoloader/`, and `usb_builder/`; SHA-256 `8b03671a26ba9547a9f63020f9dc6352f539973d643247129d0c1b8bd13e4922`.
- Build: `setuptools.build_meta.build_wheel` in a disposable `/tmp` copy; no build output was written into the working tree.
- Wheel: `libre_core_autoloader-0.1.0-py3-none-any.whl`, 405,663 bytes, SHA-256 `b5be865f1a8052999454f8a1de1e6a3524cc0c959959aa1f0d47490bc6f6db7a`; preserved at `dist/` (ignored distribution output).
- The wheel contains the application CLI/backend modules and all three pinned patch resources: X200 U-Boot, X200 GRUB/Unifont, and Arch `pacstrap` status preservation.
- The smoke extracted the wheel to `/tmp/librecore-current-wheel-0qg8hyq5/target`, imported `autoloader` and `usb_builder`, and asserted that both resolved module paths are inside that target. Exact paths and result are in [`run.json`](run.json); the build log and smoke script are preserved alongside this report.
- Reproduce the smoke with `wsl -d Ubuntu -- python3 <path-to-smoke.py>`; the script stages its own disposable source copy under `/tmp`.

This is package assembly and basic module-import evidence. It does not install dependency resolution into a clean environment or run CLI commands from the extracted wheel.
