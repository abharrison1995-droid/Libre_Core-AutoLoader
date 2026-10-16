# X200 ArtifactEngine preflight attempt — 2026-10-15

This was a fail-closed software attempt through the pinned X200 ArtifactEngine
and workflow contract. It did **not** compile firmware or produce a ROM.

The clean Libreboot 26.01 source checkout was read-only audited at commit
`1cf3181537b8d1fe1df0e91681f700850a3d9bf6`, tree
`0cb9451c6e2e265d3aa5b7e371752d45cab98f53`; all 35 pinned files matched.
The only target evidence supplied to the compatibility check was explicitly
unknown: no fresh DMI, SPI probe, measured chip capacity/voltage, or verified
backup/recovery set was available. `x200-libreboot-4mb` was named only as a
preflight candidate to exercise the guard; 4 MiB was not claimed as observed
hardware capacity.

Compatibility returned `UNKNOWN`; the X200 workflow entered `BLOCKED`. The
ArtifactEngine returned `ARTIFACT_BUILD_BLOCKED` because raw MTM/model DMI,
physically observed programmer, and chip topology were missing. The workflow's
`build_and_stage` gate also rejected the attempt because no ready original
backup/recovery record existed. No build command ran, the fresh output folder
remained empty, and there is no artifact or COMPLETE manifest to reopen.

The report records timestamps, elapsed time, exact source revision/tree, intent
digest, output path, gate results, and confirmation that no hardware, media,
writer, or recovery operation occurred. The isolated report copy SHA-256 is
`ca344b615366fbc53b5455c387fd45cb286ac0765f04fb92b67b53a832f04dfb`.
`attempt-command.txt` records the invocation and request shape. The original
empty output directory and report remain under
`/home/alex/librecore-x200-artifact-attempt-20261015T133512Z-1d12e198/` in
Ubuntu WSL.
