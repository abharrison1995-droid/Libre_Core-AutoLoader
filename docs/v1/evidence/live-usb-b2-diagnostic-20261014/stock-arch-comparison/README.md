# Stock Arch Linux control — 2026-10-15

## Purpose and identity

This comparison checks whether the early systemd startup stall is unique to the
Libre_Core Live USB candidate. It is a bounded WSL/QEMU diagnostic, not product,
release, hardware, or USB-media acceptance. The control was the official Arch
Linux 2026.10.01 x86_64 ISO, downloaded from the [Arch Linux release page](https://archlinux.org/releng/releases/2026.10.01/).
Its SHA-256 was verified as
`684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5` before
and after each run. The extracted kernel hash
`8ebc2c71271e000540f0a7545afd8e73504fcb724671f3d38c720b241842b901` is
identical to the b2 candidate's kernel. The stock initramfs hash is
`35b4362207fcabc66535444aea0b97f3508128b32e97e66aa5f56487007bfe52`.

Both normal BIOS and direct-kernel runs used QEMU 11.1.2 under Arch WSL2 with
TCG (`-accel tcg,thread=multi -m 3072 -smp 4`). The normal BIOS run was bounded
at 600 seconds. It had no guest input, network interface, writable disk, or
firmware writer. The ISO stayed byte-identical. QEMU exit code 0 reflects the
bounded host-side stop only.

## Results

The normal BIOS control reached systemd 262-1-arch, then remained in early
startup. The VGA record shows repeated Namespace Resource Manager failures
and a job waiting for creation of static `/dev` nodes; no login prompt or
application was reached.

The direct-kernel debug-shell run used the exact kernel and initramfs extracted
from the verified ISO. A serial marker confirmed the documented root shell on
tty9 at 177.8 seconds. Only after visually confirming the shell prompt did the
operator send a harmless `echo lc_run12_marker`. A single sequential,
read-only command then returned to the prompt. At that snapshot,
`systemd-nsresourced.service` was loaded and still activating as job 207;
`systemctl status` reported 38.267 seconds of service CPU over about 40 seconds
of guest time, 11.8 MiB current / 18.1 MiB peak memory, and the socket trigger.
`systemctl --failed` listed zero failed units at that moment, and the journal
query returned `-- No entries --`. These are point-in-time observations, not a
root-cause diagnosis. In the separate longer direct-kernel capture, the serial
log records nsresourced timing out, being killed, and failing after 2m53.405s
CPU over 3m1.147s wall time. That run's later GUI command screenshots were
captured before each command finished and are explicitly excluded from
interpretation; its serial startup log remains usable.

The same early systemd symptoms on stock Arch make a b2-only profile fault less
likely, but do not prove that WSL, TCG, systemd, or any one service is the
cause. The environment exposed neither `/dev/kvm` nor `/dev/mshv`; QEMU's MSHV
accelerator probe failed because `/dev/mshv` is unavailable. A native Linux/KVM
control is still needed to separate guest behavior from this WSL emulator
environment. The Live USB BIOS/UEFI app/service and offline-namespace gates
remain open. No product profile or systemd unit was changed from these results.

## Evidence map

- `bios-20261015/`: normal BIOS capture, exact command/version, result and ISO hashes.
- `direct-kernel-readonly-20261015/`: visually gated tty9 prompt, harmless marker,
  one serialized read-only state command, complete VGA captures, serial output,
  and unchanged ISO hashes.
- `direct-kernel-capture-20261015/`: earlier longer serial diagnostic. The later
  `systemctl` screenshots are unreliable because commands overlapped; consult
  `analysis.txt` and use its serial log only for the service timeout sequence.
- `direct-kernel-20261015/` and `direct-kernel-info-20261015/`: earlier launcher
  diagnostics with no guest commands; see their per-run analysis notes.
- `run_stock_bios.py`, `run_stock_debug_shell_capture.py`,
  `run_stock_debug_shell_info.py`, `run_stock_debug_shell.py`, and
  `run_stock_debug_shell_readonly.py`: source launchers for the recorded runs.

No attached run presents a QEMU exit code as guest boot success.
