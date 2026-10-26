# Current-source Linux-targeted mypy rerun — 2026-10-26

The `autoloader/` and `usb_builder/` production Python source trees passed
mypy with the Linux target from Ubuntu WSL 2:

```text
PYTHONDONTWRITEBYTECODE=1 /home/alex/librecore-v1-testvenv-288f42c-ready/bin/python -m mypy --no-incremental --cache-dir=/dev/shm/librecore-mypy-cache --platform linux autoloader usb_builder
```

Result: **success, no issues found in 119 source files** (exit `0`). The
output retains mypy's informational notes that bodies of legacy untyped
functions are not checked unless `--check-untyped-defs` is enabled. The
isolated cache was kept on WSL tmpfs. An earlier invocation against the
default cache stopped with `sqlite3.DatabaseError: database disk image is
malformed`; the isolated rerun completed successfully.

The exact invocation, output, exit status, Python/mypy versions, platform, and
SHA-256 hashes for all 119 checked production files are recorded here. This is
static-analysis evidence only. It does not validate a native Linux build or
guest, Live USB, any of the three physical firmware workflows, read-back,
recovery, or boot. No hardware or firmware was accessed.
