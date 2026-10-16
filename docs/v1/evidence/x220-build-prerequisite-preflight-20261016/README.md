# X220 Coreboot build prerequisite inspection — 2026-10-16

**Outcome: BLOCKED BEFORE BUILD. No ROM was produced.** This was a read-only
check of the pinned builder inputs, not a compilation attempt or hardware test.

- Checkout: `/home/alex/librecore-coreboot-x220-8b0e34f90e08`.
- Upstream HEAD: `8b0e34f90e08547cf19e550a43ed72f69c39d6ed`, detached and clean.
- Pinned builder configuration: `configs/builder/config.lenovo_x220`, SHA-256
  `beeb15673bd4ef1394e19c7bbbac7f9ab3589552671d9502de4621b0b32c2cd1`.
  The digest matches the X220 recipe's pinned config digest in
  `autoloader/rom_builder/artifact_engine.py`.
- The config requires `site-local/descriptor.bin`, `site-local/me.bin`, and
  `site-local/gbe.bin`. The checkout has no `site-local` directory; each
  required file check returned status 1.
- The accepted X220 artifact path also requires exact physical identity and
  observed chip topology, a complete verified original two-read backup and
  recovery evidence. None was present in this software-only environment.

No build command, artifact-engine completion, ROM output, or COMPLETE manifest
was created. The build was not run with synthetic descriptor, ME, or GbE data.
The candidate remains blocked until genuine device-derived inputs and
recovery evidence are available through the accepted workflow. No hardware,
writer, media, or recovery operation occurred.
