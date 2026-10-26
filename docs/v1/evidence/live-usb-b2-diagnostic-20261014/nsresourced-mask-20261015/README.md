# Direct-kernel nsresourced mask experiment — 2026-10-15

This is a bounded, emulator-only diagnostic of the existing b2 image. It is
not a normal BIOS/UEFI ISO boot or a release pass. The run used the same
checksum-verified ISO (`afde8ba28025530852547ec5042dd94ecece5a9b1d24f5da141365861cc35cbd`),
kernel (`8ebc2c71271e000540f0a7545afd8e73504fcb724671f3d38c720b241842b901`),
and initramfs (`c7440ebd5164e83e4a28c3f22ba2695af06a0fd29676ae572967ecdb6218e090`)
as the earlier direct-kernel diagnostics. QEMU was 11.1.2 with TCG, four
vCPUs, 3 GiB RAM, loopback-only VNC, `-nic none`, no writable disk, and no
firmware writer. The only added kernel parameters were
`systemd.mask=systemd-nsresourced.service` and
`systemd.mask=systemd-nsresourced.socket`.

The serial log confirms systemd linked both units to `/dev/null` and reported
the socket masked. The systemd debug shell service started on tty9, but no
guest command was entered. At the 270-second checkpoint, `sysinit.target` was
still waiting on `systemd-tmpfiles-setup-dev.service`; masking nsresourced did
not clear the observed startup wait within this run. That is a narrow emulator
observation, not proof that nsresourced is unrelated or a root-cause finding.
No application/service readiness or normal boot pass was established.

QEMU ran from `2026-10-15T17:34:29.179221+00:00` to
`2026-10-15T17:39:11.578144+00:00` (282.40 seconds). The host controller sent
HMP `quit`; QEMU exited 0. The controller itself exited 1 because the monitor
connection reset while reading the expected quit response, after QEMU had
stopped and the final frame was saved. The ISO and boot-file hashes were
unchanged. See `result.txt`, `runner-exception.txt`, the serial/checkpoint
logs, monitor transcript, exact argv, and VGA frames. `SHA256SUMS` covers the
preserved files except itself.
