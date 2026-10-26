# Retry15: serial PTY handshake failure

This bundle records an incomplete, generic Arch ISO diagnostic from
2026-10-21. It is not a current-source product boot, build, or hardware test.

QEMU 10.2.1 used TCG multi-threading, 4 vCPUs, 6144 MiB, no network, and no
passthrough. The Arch ISO and source image were read-only; workspace and result
overlays were fresh. The launch record includes the full argument vector.
QEMU was stopped through QMP after the helper could not discover/open the
serial PTY within 20 seconds. QEMU exited 0 after 32.513 seconds, but the
serial capture was empty. No guest input or query was sent. The helper's
separate stdout/stderr handling missed the PTY announcement; this is a helper
handshake failure and does not establish guest startup status.

Before/after records match for the ISO, kernel, initramfs, and read-only source
image. No native test suite, rootless preflight, current-source build, product
ISO, or hardware operation ran. The raw empty serial/QEMU logs are retained.

`run-result.json`, `events.jsonl`, the protocol, exact launch and observer
scripts, gate tests, input hash records, and logs are preserved here. Verify
the listed files with `SHA256SUMS`.
