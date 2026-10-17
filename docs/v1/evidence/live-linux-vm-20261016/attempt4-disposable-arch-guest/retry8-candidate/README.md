# Retry8 candidate (not launched)

This is a separate, no-launch diagnostic candidate. It does not modify the
retry7 runner/helper or its archived captures. No QEMU/VM was launched for this
candidate, and it has produced no guest result.

## Purpose and retained gates

Retry7 reached the exact read-only package query and then produced no output or
exit marker before its 900-second outer deadline. Retry8 makes that query
bounded and observable while preserving the exact package set from the pinned
`usb_builder/snapshot.lock.json`; it remains `pacman -Q` only. It does not run
`pacman -S`, refresh databases, install packages, invoke the product builder,
or touch firmware/hardware.

The proposed runner keeps source HEAD/status and the full source/evidence hash
checks, ISO/kernel/initramfs pins, read-only source share, process/device
isolation, 3 GiB sparse scratch identity, 2 GiB C: floor, and ephemeral debug
shell controls. Its outer QEMU bound is 1800 seconds. The guest helper command
has a 900-second ceiling; the `pacman -Q` subprocess has a 300-second deadline
and emits a heartbeat every 15 seconds. Timeout, launch error, nonzero exit,
oversized/malformed output, or version mismatch is explicitly incomplete and
stops before creation of the temporary build identity, rootless preflights, or
scratch checks.

The query uses an exclusive, mode-0600 output file under guest `/run`, captures
stdout and stderr there, and records the exact argv, start/end time, PID,
elapsed time, process state, wait channel, output byte count and output hash.
Process timeout sends SIGTERM to the query process group, observes a bounded
grace interval, then sends SIGKILL to the original group even if its leader
exited early. The result can therefore distinguish an observed timeout from an
outer runner deadline; it does not assume why the previous query stalled.

To reduce serial traffic, the helper is copied from the already verified,
read-only 9p source share. The runner first hashes its local helper bytes;
after the copy, the guest hashes `/run/lc-guest-probe.py` and requires the same
pin before execution. Retry7's archived bytes are included in the retry8
archive verification map. No guest Git check is claimed.

## Retry7 evidence and interpretation

Retry7's result is archived under `../results/retry7/`. It records an unchanged
installer ISO hash (`684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5`),
zero allocated scratch bytes, preserved C: free-space floor, and no package
installation/sync, product build, or hardware/firmware validation. Its serial
log ends after:

```text
COMMAND=["/usr/bin/pacman", "-Q", "arch-install-scripts", "archiso", "bash", "libcap", "pacman", "shadow", "util-linux"]
```

There is no query output, exit marker, helper exception, or process-state
sample. The host runner stopped and reaped QEMU at 900.059 seconds. This is
evidence of an incomplete guest command, not proof that pacman itself was
stuck. Slow access to the live ISO's SquashFS/package database under TCG and a
local package-database lock or I/O wait remain hypotheses only; retry7 captured
no syscall, lock, or wait-channel evidence to distinguish them.

## No-launch checks and candidate pins

The final focused retry8 run passed: **19 passed, 1 skipped in 6.51 seconds**.
The tested command was:

```text
wsl.exe -d Ubuntu -- env PYTHONDONTWRITEBYTECODE=1 timeout 120s /home/alex/librecore-v1-test-venv/bin/python -m pytest -q --basetemp=/dev/shm/lc-retry8-final-20261017 docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/retry8-candidate/test_retry8_guest_probe.py
```

Coverage includes the exact `pacman -Q` argv and locked-version parsing,
captured output, heartbeat and timeout behavior, process-group cleanup,
exclusive output-path handling, fail-closed sequencing before later identity,
preflight, or scratch checks, and verification of archived retry7 evidence.
Python syntax compilation passed for the helper, runner, and focused test file.
The runner's exact Git source-status check matched all 38 expected entries
(retry1–7 evidence, retry8 candidate files, and the three pre-existing modified
status documents); no entries were missing or unexpected. `git diff --check`
exited 0, with only existing CRLF conversion notices for the three status
documents. No VM/QEMU was launched. SOL review of these exact candidate bytes
is still required before any launch.

```text
guest_probe.py SHA-256: c46545f294f4ac9a3e17e0e4146376b8bb2b8f359e6e30a74af1cef9fe31de84
run_arch_guest.py SHA-256: 6f8738016e5434c7011cc03eaa4d7e781929c5c54fe01c5cd72454e0248c5271
test_retry8_guest_probe.py SHA-256: be31bb3fa7917ae9078884507ea6604aec7ab6d2bc3be7d2407abe26a077eaba
```

No retry8 QEMU result, package compatibility result, rootless-preflight result,
ISO build, normal boot/login, AutoLoader service result, or hardware validation
is claimed.
