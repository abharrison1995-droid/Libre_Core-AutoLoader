# Current-source Win32-targeted mypy rerun — 2026-10-26

The no-incremental static type check completed on the current `autoloader/`
and `usb_builder/` production source trees:

```text
.venv\Scripts\python.exe -m mypy --no-incremental --platform win32 autoloader usb_builder
```

Result: **success, no issues found in 119 source files** (exit `0`). The
retained output includes mypy's informational notes that bodies of legacy
untyped functions are not checked unless `--check-untyped-defs` is enabled;
the command matches the project's documented Win32-targeted gate. Python and
mypy versions, the exact command/output/exit, and hashes for all 119 current
Python inputs are recorded in this directory.

This is static-analysis evidence only. It does not replace runtime tests,
native Linux/VM build gates, Live USB boot validation, or any physical firmware
workflow. No hardware or firmware was accessed.
