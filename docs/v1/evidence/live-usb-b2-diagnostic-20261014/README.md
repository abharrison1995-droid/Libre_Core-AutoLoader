# b2 same-hash QEMU diagnostics — 2026-10-14

## Scope and image identity

These are emulator-only diagnostics for the temporary b2 candidate ISO. They do
not establish hardware, USB-media, or release validation. The ISO was
`/home/alex/librecore-usb-kms-output-20261013-b2/candidate-bundle/libre_core_autoloader-2026.09.29-x86_64.iso`, SHA-256
`afde8ba28025530852547ec5042dd94ecece5a9b1d24f5da141365861cc35cbd`. Each
run recorded the same hash before and after. No guest had a firmware writer or
network device attached.

A QEMU exit code of 0 in these records means that the emulator accepted an HMP
`quit` at the diagnostic time bound. It does not mean that the guest booted
successfully.

## Arch WSL acceleration preflight

`qemu-acceleration-preflight-20261015.txt` records a host-only capability check
for QEMU 11.1.2. The listed accelerators were tcg, mshv, nitro, and kvm, but
`/dev/kvm` and `/dev/mshv` were absent. A bounded MSHV probe exited 1 with
`Failed to open /dev/mshv` and `failed to initialize mshv: Operation not
permitted`. This establishes no accelerated guest path in Arch WSL and says
nothing about native Linux or KVM availability. It does not alter or replace
the preserved QEMU TCG boot results below.

## Earlier BIOS and UEFI boots

The `bios/` and `uefi/` directories preserve the prior same-hash VGA, serial,
monitor, command, and result files. BIOS reached systemd's “Starting Namespace
Resource Manager” message and waited; there was no usable shell or
application/service evidence. Its serial log was empty and the exit code was
not captured after the external bounded stop. UEFI reached the Archiso
initramfs and showed systemd startup, but did not provide a usable login or
service/journal session. Both runs are incomplete emulator evidence; neither
is an application/service pass.

## Direct-kernel BIOS diagnostic

`direct-kernel/run2/command.txt` records the QEMU 11.1.2 command. It used
`-accel tcg,thread=multi -m 3072 -smp 4`, the exact ISO as CD-ROM, extracted
kernel/initramfs, `-nic none`, a serial log, and an HMP monitor. The command
passed these kernel arguments:

```
archisobasedir=arch archisosearchuuid=2026-10-13-22-51-40-00 console=tty0 console=ttyS0,115200n8 systemd.log_level=debug systemd.log_target=console systemd.debug_shell=1
```

The extracted kernel was 17,224,192 bytes, SHA-256
`8ebc2c71271e000540f0a7545afd8e73504fcb724671f3d38c720b241842b901`. The
initramfs was 82,554,116 bytes, SHA-256
`c7440ebd5164e83e4a28c3f22ba2695af06a0fd29676ae572967ecdb6218e090`. The
binaries are retained in the temporary diagnostic directory, not duplicated
here; `boot-files.sha256` records their digests.

The serial log records Archiso hooks mounting `/dev/sr0` at
`/run/archiso/bootmnt` and `/dev/loop0` at `/run/archiso/airootfs`, followed by
“Welcome to Arch Linux!”. It also records systemd starting a debug shell on
`/dev/tty9` (child PID 429) and queueing `autoloader.service`, whose start was
still held on `basic.target` in the captured interval. The log contains preset
messages saying `autoloader.service`, `debug-shell.service`, and
`systemd-nsresourced.service` are disabled by preset. Those messages describe
default preset policy; by themselves they do not establish the live
`is-enabled` state or the cause of the incomplete startup.

The builder source creates
`/etc/systemd/system/multi-user.target.d/autoloader.conf` with
`[Unit]` / `Wants=autoloader.service`, and the image customization hook checks
that file. The systemd log's queued start job is consistent with that explicit
Wants dependency. This diagnostic did not capture a successful `systemctl
is-enabled`, `systemctl status`, `systemctl --failed`, or `journalctl` command,
and did not capture `/proc/cmdline` output. Although the exact kernel arguments
are preserved in the QEMU command and `kernel-command-line.txt`, guest
read-back of those arguments is not claimed. The VGA screendumps stayed at
early SeaBIOS text; the attempted tty9 keyboard/read-only commands produced no
usable command output.

At the 300-second bound the supervisor issued HMP `quit`; QEMU exited 0 after
307.5 seconds including shutdown handling. ISO SHA-256 before and after was
`afde8ba28025530852547ec5042dd94ecece5a9b1d24f5da141365861cc35cbd`. No
AutoLoader screen, active service status, guest journal inspection, complete
boot, or physical-hardware result was obtained.

## TTY9 follow-up diagnostics (runs 3–8)

These are still direct-kernel diagnostics, not BIOS/UEFI ISO-boot acceptance.
All runs use the unchanged b2 ISO SHA-256 above, extracted kernel SHA-256
`8ebc2c71271e000540f0a7545afd8e73504fcb724671f3d38c720b241842b901`, and
initramfs SHA-256
`c7440ebd5164e83e4a28c3f22ba2695af06a0fd29676ae572967ecdb6218e090`. The
QEMU command records, serial logs, VGA captures, launcher records, and
before/after hashes are under `direct-kernel/run3-emergency/` through
`run8-vnc/`.

- Run3 reached `emergency.target`, but sulogin reported that the root account
  was locked. No shell command was entered; the supervisor stopped QEMU at
  168.9 seconds. Emulator exit 0 followed SIGTERM and is not guest success.
- Run4 completed the loopback RFB handshake but the temporary VNC client failed
  while constructing its framebuffer request. It stopped at 7.6 seconds before
  obtaining guest VGA or serial evidence.
- Run5 reached `debug-shell.service` running in serial by its 300-second bound,
  but the VGA remained at early firmware text and no command output was
  obtained.
- Run6 attempted HMP only after the serial marker, but the monitor socket was
  absent by the 300-second stop. The helper did not deliver a keypress; this is
  not evidence of a failed keypress.
- Run7 kept HMP connected from launch, but its serial-file watcher hit a
  transient `OSError: [Errno 61] No data available` on the mounted Windows
  path at 150.1 seconds. The launcher stopped QEMU before the debug-shell
  marker; no key or guest command was sent.
- Run8 retried transient serial reads and kept the HMP connection open from
  launch. At `2026-10-14T03:14:52Z` (262.9 seconds after launch), the serial
  log reported `debug-shell.service: Changed dead -> running`; HMP sent
  `sendkey ctrl-alt-f9`. VGA then showed a tty9 shell prompt. The harmless
  `echo lc_tty9_marker` command visibly printed its marker, and `cat
  /proc/cmdline` visibly confirmed the injected kernel command line.

At the run8 snapshots, `systemctl list-jobs --no-pager` showed 66 jobs,
including `systemd-nsresourced.service` running and `basic.target` waiting.
`systemctl --failed --no-pager` showed zero loaded failed units at that
snapshot. `systemctl status autoloader.service --no-pager` showed the unit
loaded, disabled, inactive (dead), with Job 174. The nsresourced status showed
it activating and triggered by its socket. A later capture showed
`systemd-remount-fs.service` succeeded while `basic.target` and `sysinit.target`
were inactive. `journalctl -b --no-pager -n 25` displayed nsresourced audit and
watchdog messages. The serial log later records nsresourced timing out and
being terminated. These observations identify a blocked startup path but do
not establish its root cause. The read-only `systemctl is-active` command did
not produce a retained result. `systemctl default --no-block` was not run.

Run8 was stopped by the operator at 594.2 seconds; QEMU exited 0 after SIGTERM
and shutdown handling (604.4 seconds total in the supervisor record). The ISO
hash was unchanged. No AutoLoader UI or active service was reached, no normal
BIOS/UEFI boot gate passed, and no product source/profile/root-account change
was made. No network device or firmware writer was attached in any run.

## Evidence integrity

Raw PPM captures, PNG previews, serial/monitor/host logs, exact commands,
version files, and result summaries are preserved by mode. `SHA256SUMS` covers
the copied files except itself. The extracted kernel and initramfs are retained
in the referenced temporary directory and are represented by their recorded
sizes and SHA-256 digests.

## Additional timed tty9 diagnostics (runs 9–12; 2026-10-15)

These remain direct-kernel emulator diagnostics, not normal BIOS/UEFI acceptance. Each run used the unchanged b2 ISO SHA-256 `afde8ba28025530852547ec5042dd94ecece5a9b1d24f5da141365861cc35cbd`, the extracted kernel SHA-256 `8ebc2c71271e000540f0a7545afd8e73504fcb724671f3d38c720b241842b901`, and initramfs SHA-256 `c7440ebd5164e83e4a28c3f22ba2695af06a0fd29676ae572967ecdb6218e090`. QEMU was 11.1.2, `-accel tcg,thread=multi -m 3072 -smp 4`, with `-nic none`, loopback-only VNC, and no firmware writer.

- Run9 waited only a few seconds after the tty9 chord before capture; it sent a harmless echo before full-frame visual review. The complete frame showed early SeaBIOS/TDX text, no prompt/marker. No status or journal command was sent. It stopped at 334.7 seconds because the review continuation timed out; this did not test the later appearance of the shell.
- Run10 captured complete VGA frames; frames 1, 3, 5, and 7 visibly showed `sh-5.3#`. At about 362 seconds, the launcher acknowledgement gate expired before the manual review result was consumed. No guest text or status/journal query was sent. Cleanup finished at 383.4 seconds; the emulator exit 0 is a host shutdown result.
- Run11 reached the serial debug-shell marker at 256.4 seconds and issued HMP `sendkey ctrl-alt-f9`, then the temporary launcher failed with `NameError: sys is not defined` before a complete post-key frame. No guest text or status/journal command was sent. The launcher exception cleanup stopped QEMU at 261.8 seconds; the exit 0 is not guest success.
- Run12 used a corrected manual PTY gate and independent 600-second cap. It reached the serial marker at 249.1 seconds and sent HMP `sendkey ctrl-alt-f9`. Twenty-one complete 720x400 frames through 565.3 seconds showed only SeaBIOS/TDX early-startup text; no tty9 prompt appeared. One repeat chord over a second HMP connection had no response bytes within 3 seconds and is marked unacknowledged; later frames were unchanged. No guest text, systemctl/status command, journal query, or application/service evidence was obtained. The launcher ended at its T+575 review boundary and terminated QEMU; QEMU exit 0 is not guest success.

Run9–12 retained before/after ISO hashes, serial and host logs, monitor transcripts, commands, launcher versions, and complete frames where available. Their per-run `checksums.sha256` files validate each preserved folder. These runs establish neither a root cause nor a normal ISO boot pass. No ISO/profile/source/root-account change, hardware operation, media write, or firmware write was made.

## Stock Arch Linux control (2026-10-15)

The separate `stock-arch-comparison/` folder preserves a checksum-verified official Arch Linux 2026.10.01 ISO control. Its normal BIOS boot and direct-kernel tty9 capture show similar early systemd symptoms under WSL/QEMU TCG, including a CPU-bound `systemd-nsresourced` service. The comparison weakens a b2-only profile hypothesis but proves no root cause. A native Linux/KVM comparison and successful b2 BIOS/UEFI app/service boot are still pending. The comparison README indexes the usable evidence and explicitly qualifies the earlier overlapping-command screenshots.

## Direct-kernel nsresourced mask experiment

The 2026-10-15 experiment in `nsresourced-mask-20261015/` used the unchanged b2 ISO, kernel and initramfs in QEMU TCG, adding only `systemd.mask=systemd-nsresourced.service` and `systemd.mask=systemd-nsresourced.socket`. Serial output confirms both units were linked to `/dev/null`, but at the 270-second checkpoint `sysinit.target` still waited on `systemd-tmpfiles-setup-dev.service`. The debug-shell service started on tty9, but no guest command was entered. This did not establish app/service readiness, a normal boot pass, or root cause.

The run lasted 282.40 seconds. QEMU exited 0 after the planned HMP quit; the host controller exited 1 because its monitor read was reset after QEMU had stopped. ISO/kernel/initramfs hashes were unchanged. This is an oriented emulator diagnosis, not release or hardware evidence. The new folder contains the exact argv, serial/checkpoint/monitor logs, VGA frames, result, controller exception and per-run checksum manifest.
