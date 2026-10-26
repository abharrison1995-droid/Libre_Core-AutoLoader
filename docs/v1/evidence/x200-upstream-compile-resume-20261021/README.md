# X200 upstream Libreboot compiler diagnostic — 2026-10-21

**Result: INCOMPLETE, UNACCEPTED WSL COMPILER DIAGNOSTIC.** The pinned upstream
build stopped with a host compiler error before producing any ROM. This is not
a validated firmware artifact, an ArtifactEngine `COMPLETE` result, or evidence
that a ROM is ready to flash.

## Run details

- Environment: Ubuntu 26.04 WSL; host GCC/G++ 15.2.0.
- Command used to launch the preserved runner from the repository:
  `wsl.exe -d Ubuntu -- /home/alex/librecore-v1-test-venv/bin/python -B /mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader/docs/v1/evidence/x200-upstream-compile-resume-20261021/run_x200_build.py`
- The runner passed its clean-source and input preflight, then ran exactly
  `./mk -b coreboot x200_4mb` in a fresh scratch tree. Libreboot lbmk was pinned
  to commit `1cf3181537b8d1fe1df0e91681f700850a3d9bf6`, tree
  `0cb9451c6e2e265d3aa5b7e371752d45cab98f53`. The build used the reviewed
  X200 U-Boot and GRUB/Unifont overlays and the hash-verified inputs listed in
  `run-result.json`.
- Scratch root:
  `/home/alex/librecore-x200-upstream-resume-20261021T003745Z-e14b5a72`.
  The earlier scratch was preserved and no previously compiled crossgcc
  objects were reused.
- Build started at `2026-10-21T01:46:37.100703+01:00` and ended at
  `2026-10-21T03:39:08.561016+01:00`; elapsed time was 6,751.435 seconds.
  The upstream command returned exit code 1, before the configured four-hour
  timeout.

## Failure and limits

The pinned Coreboot `cbfstool` host utility failed while compiling
`util/vboot_lib/host/lib/cbfstool.c` with the installed host GCC 15.2.0:

```text
host/lib/cbfstool.c:262:29: error: initialization discards ‘const’ qualifier from pointer target type [-Werror=discarded-qualifiers]
  262 |                 char *end = strchr(start, '\n');
      |                             ^~~~~~
cc1: all warnings being treated as errors
```

The build log also reports an unrecognized `-Wno-unknown-warning` option, but
the fatal diagnostic is the discarded-qualifier warning promoted to an error.
The failure occurred before the X200 target ROM output stage. The runner
recorded `NO_OUTPUT_DIRECTORY`, zero ROMs, and zero CBFS results; none of the
31 expected X200 4 MiB variants was produced. No ROM was copied into the
repository. ArtifactEngine `.build()` was not invoked and no `COMPLETE`
manifest was published.

The scratch tree, pinned source, generated toolchain state, complete build log,
runner, result, and SHA-256 manifest are retained here and in the recorded
Linux-side scratch path. The failure and any remediation are pending review;
no compiler-flag override, source overlay, or retry is included in this record.
No firmware or media operation occurred.

This is WSL-only upstream compiler evidence. Native Linux build validation,
ArtifactEngine acceptance, product ISO/boot validation, and all hardware gates
remain open.
