# Retry13 direct-kernel diagnostic

This is a generic Arch ISO startup diagnostic, not a current-source product
build or a normal live-boot pass. QEMU 10.2.1 ran with TCG, four vCPUs, 6144
MiB RAM, a 600-second cap, no network device, a read-only ISO CD-ROM, fresh
workspace/results overlays, and a read-only source image. The ISO SHA-256 was
`684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5` both
before and after the run.

The ISO's kernel and initramfs were extracted by exact member path and hash
checked. The direct-kernel command and complete arguments are preserved in
`launch_retry13.sh`; the protocol and PTY/QMP helper bytes are alongside it.
QEMU was stopped by the operator through the restricted QMP `quit` helper
after 250.142 seconds. It exited cleanly. The failed prelaunch option parse
from the earlier CRLF script is retained separately and was not a guest run.

The reviewed systemd debug shell appeared on `ttyS0`, and a stable `sh-5.3#`
prompt was observed. Only the five read-only commands listed in
`run-result.json` were sent. They reported root identity and the expected
ISO, workspace, results, and read-only source device map. At the query
snapshot, systemd had 88 jobs pending: `systemd-nsresourced` and early
tmpfiles setup were activating, while the later tmpfiles `/dev` setup was
inactive. Later in the captured serial transcript, systemd printed
`[FAILED] Failed to start Namespace Resource Manager`. The journal also showed
repeated successful BPF-related syscalls by `systemd-nsresourced`. These are
observations at different points in the transcript; they do not establish a
root cause.

No normal login or completed userspace was established. No native test suite,
rootless builder preflight, current-source product build, or package
operation ran. No product ISO was produced and no physical hardware was
accessed. A native build remains open until normal userspace and the reviewed
rootless preflight are ready. The small retry6 PNG is included to show the
earlier initramfs udev wait; retry13 used serial-only display and has no VGA
capture.

## Files

- `run-result.json` — bounded run facts and limited interpretation.
- `launch_retry13.sh` — exact QEMU argument construction, including kernel
  command line and device controls.
- `serial-input.log` and `serial-output.log` — PTY input and guest transcript.
- `pty_qemu_runner.py`, `qmp_control.py`, and `protocol.txt` — runner,
  restricted monitor helper, and diagnostic controls.
- `prelaunch-qemu-option-rejection.log` — earlier CRLF option-parse failure;
  it did not start QEMU.
- `retry6-boot-tty2.png` — a small screenshot from the earlier initramfs udev
  wait; raw PPM captures remain in scratch.
- `SHA256SUMS` — hashes for every file in this evidence directory except the
  checksum list itself.
