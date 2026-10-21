# Retry16: unresolved Namespace Resource Manager diagnostic

This bundle records an incomplete, generic Arch ISO diagnostic from
2026-10-21. It is not a current-source product boot, build, or hardware test.

QEMU 10.2.1 used TCG multi-threading, 4 vCPUs, 6144 MiB, no network, and no
passthrough. The pinned ISO and source image were read-only; workspace and
result overlays were fresh. QEMU stdout and stderr were merged so the helper
captured the serial PTY announcement. The serial log contains startup output.
A post-run ANSI-normalized scan counted four `Failed to start Namespace
Resource Manager` lines, first seen at 267.281 seconds; it counted zero normal
startup target markers and zero emergency/fatal markers. The 10,040-byte raw
serial log has SHA-256
`44ff3d95d59463fcaaaf190f013cd82d5e203ba245477a97d07dfb508c3f7a63`. The
7,487-byte normalized stream has SHA-256
`da6b88878e182a575d84f867f4fb222a59a8f5ae5f0e8ee66e10a37f4ec2a02a`.

The 600-second hard wall cap expired before a stable post-failure shell prompt
was recognized. `prompt_offset` remained zero, `queries` is empty, and the
serial input log is empty: no guest command or other guest input was sent.
QMP quit succeeded and QEMU exited 0 after 612.783 seconds including teardown.
The service failure's cause is unknown; the captured output does not establish
why startup remained incomplete.

Before/after records match for the ISO, kernel, initramfs, and read-only source
image. No normal boot/login, native tests, rootless preflight, current-source
build, product ISO, or hardware operation ran. The raw serial and merged QEMU
output are retained byte-for-byte; `.gitattributes` marks logs as binary to
preserve CR and ANSI bytes. The [post-run scan](postrun-scan.json) records the
hashes, marker counts, and normalization method. It was computed from the
preserved serial file without guest input.

`run-result.json`, `events.jsonl`, the protocol, exact launch and observer
scripts, gate tests, input hash records, raw logs, and post-run scan helper are
preserved here. Verify the listed files with `SHA256SUMS`.
