# Win32-targeted mypy check — 2026-10-26

## Result

`mypy --platform win32 --no-incremental autoloader usb_builder` completed on
native Windows with **no issues in 119 source files** and exit `0`. The three
`annotation-unchecked` notes for existing untyped function bodies are retained
in `mypy.log`; they are notes, not errors.

This check followed a narrow compatibility change that guards POSIX-only
`fchmod`, `geteuid`, and `fcntl` lookups. SOL reviewed the change and accepted
that POSIX behavior remains unchanged while unavailable APIs fail closed. The
four source pins changed with the implementation are listed in
`source-pins.sha256` and match the current files.

## Reproduction

- Environment: Windows 11, Python 3.12.14, mypy 2.4.0.
- Command: see `command.txt`.
- Exit status and exact mypy output: `mypy.exit` and `mypy.log`.
- Repository baseline: `commit.txt`; changed source pin hashes:
  `source-pins.sha256`.

This is static analysis only. It does not validate the full Windows test suite,
Linux worker deployment, package acquisition, VM/image build or boot, or any
physical firmware operation.

`SHA256SUMS` covers every other file in this evidence directory.
