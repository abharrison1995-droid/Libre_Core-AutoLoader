# Current-source native Arch build attempt — 2026-10-21

This evidence records native Arch checks against commit
`8ea32c522a942a6367a3adce78b9efddbd64e521` in a disposable Arch Linux VM
running under QEMU TCG. It does not record a completed current-source ISO or a
successful product boot.

## Checks completed

- The guest installed the pinned host tools: Archiso `91-1`, pacman
  `7.1.0.r9.g54d9411-2`, util-linux `2.42.4-1`, bash `5.3.20-1`,
  arch-install-scripts `31-2`, shadow `4.20.0.arch1-1`, and libcap `2.78-1`.
- The current-source X220 native selection passed **50 tests**, skipped 4,
  and deselected 972. Its JUnit report is in the current software regression
  evidence folder.
- Top-level CLI help passed. `build-usb --help` initially failed because the
  extracted source tree's log directory was root-owned; after correcting that
  scratch-tree ownership, the same help command passed. Its capture is
  `native-build-usb-help.log`.

## Incomplete checks

An exploratory full native test run showed multiple errors at about 7% and was
stopped before it produced JUnit output. It is incomplete and is not a passing
full-suite result. The current native targeted mypy attempt raised an internal
error; Windows and Ubuntu WSL full-project mypy results are recorded
separately.

The current-source ISO build used fresh scratch output/work directories and
the supported online mode. Attempts 1–3 stopped before a complete package
install: the live ISO's 256 MiB CoW root was too small; a 3 GiB retry then hit
package retrieval failures; the next retry hit a permissions error replacing
a manually staged package signature. The matching raw build logs and CLI
captures are included here.

After correcting package-cache ownership, attempt 4 advanced through package
installation and post-install configuration into `(15/16) Updating linux
initcpios`. Its last captured output is the `[keyboard]` mkinitcpio hook. The
VM was stopped after prolonged CPU-active work without a completed initramfs
or ISO. No current-source candidate was produced, and no BIOS or UEFI product
boot was attempted. The wrapper log for this stopped run is empty because the
VM process was terminated before it flushed; the preserved `build.log` ends
at the last observed hook.

The production offline build path remains fail-closed because the reviewed
fixed offline build runner is not deployed. These online attempts do not test
offline package isolation.

No physical device was accessed. T430, X200, and X220 hardware workflows
remain pending, and writes and hardware-specific recovery still require
explicit human authorization.

## Follow-up startup diagnostic (retry13)

After the four incomplete current-source build attempts, a bounded generic
Arch ISO startup diagnostic used the ISO's hash-verified kernel and initramfs
with the exact base arguments `archisobasedir=arch
archisosearchuuid=2026-10-01-14-49-03-00` and reviewed serial/debug-shell
arguments. It used QEMU 10.2.1 TCG, a read-only ISO, no NIC, fresh scratch
overlays, and a read-only source image. Retry13 reached a systemd debug shell
and allowed only read-only `id`, `lsblk`, systemd job/status, and journal
queries. At the query snapshot, the guest reported root identity and the
expected device map and had 88 pending jobs: `systemd-nsresourced` and early
tmpfiles setup were activating, while the later tmpfiles `/dev` setup was
inactive. Later in the serial transcript, systemd printed
`[FAILED] Failed to start Namespace Resource Manager`. The journal showed
repeated successful BPF-related syscalls by nsresourced. These observations
do not establish a root cause.

The QEMU runner exited 0 after 250.142 seconds when stopped through QMP. The
ISO SHA-256 matched before and after. This did not reach normal userspace or a
normal login, and no native test suite, rootless preflight, package
operation, current-source build, product ISO, or hardware workflow ran. It is
not a native build or product boot pass. A native build remains open until
normal userspace and the reviewed rootless preflight are ready. See the
self-contained [retry13 evidence bundle](boot-diagnostic-retry13-direct-kernel/README.md).
The selected small retry6 screenshot shows the earlier initramfs udev wait;
retry13 used serial-only display and has no VGA capture.

## Captures

- `native-build-usb-help.log` — successful current-source command help.
- `native-product-build.log` — first build attempt, stopped by the 256 MiB
  root-space limit.
- `native-product-build-cow3g.log` — 3 GiB retry, package retrieval failures.
- `native-product-build-cow3g-retry.log` — package signature cache permission
  failure.
- `build-attempt-01-256m-root.log` through
  `build-attempt-04-interrupted-during-initramfs.log` — builder logs for the
  four attempts.
- `pacman-sync.log`, `pacman-install.log`, and `pinned-packages.txt` — guest
  bootstrap and installed package records.
- `boot-diagnostic-retry6-tty2/` and `boot-diagnostic-retry5/` — earlier
  bounded startup captures with their own checksum manifests.
- `boot-diagnostic-retry13-direct-kernel/` — exact direct-kernel launch,
  PTY input/output, read-only guest observations, helper hashes, and a small
  retry6 screenshot; see its `SHA256SUMS`.
- `SHA256SUMS` — hashes for the top-level captured files and this README;
  each retry directory also carries its own manifest.

