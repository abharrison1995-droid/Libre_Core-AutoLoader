# Live USB BIOS and UEFI acceptance guide

Use this guide only after the Live USB builder has produced a candidate ISO and
passed its target-root installation checks. A candidate build is not boot
validation. The BIOS and UEFI runs must use the same candidate ISO and record
its SHA-256 so the results identify the exact image tested.

## If no candidate ISO exists

Record both BIOS and UEFI validation as **NOT_PERFORMED**. Do not substitute an
older image, a hand-built image, a directory, or a simulated test result. The
release checklist remains pending until one candidate ISO passes both boot
modes and the evidence is reviewed.

## Acceptance prerequisites

- Use a disposable Linux VM with QEMU and a compatible UEFI firmware image
  (OVMF) installed. Do not attach a USB programmer or physical firmware device.
- Use the exact same candidate ISO for both runs. Record its canonical path,
  byte size, and SHA-256 before and after testing; the values must remain
  unchanged.
- Capture the QEMU command, QEMU version, host platform, timestamps, exit
  status, and complete host stdout/stderr for each run.
- Capture the VGA guest display from power-on through visible AutoLoader
  startup. For example, attach to QEMU's HMP monitor and save a frame with
  `screendump /absolute/path/boot-screen.ppm`; alternatively use a named
  screenshot/video capture tool. Record the capture method, timestamp, and
  screenshot/video hash. Host stdout/stderr alone is not VGA evidence.
- Capture `systemctl status autoloader.service` and
  `journalctl -b -u autoloader.service --no-pager` from a second guest TTY while
  the AutoLoader app remains running on tty1. From the QEMU HMP monitor, switch
  with `sendkey ctrl-alt-f2`, use an available guest console login, run those
  commands, and capture their complete output. Return to the app with
  `sendkey ctrl-alt-f1`. Record errors and the service's final state. If the
  guest offers no usable login or console, the required service evidence is
  missing and the boot gate cannot pass. Do not change the image to add a root
  shell solely for this test.
- Do not run prepare, probe, backup, build, flash, restore, recovery, or other
  hardware-facing actions during this boot test.

## BIOS boot

Boot the candidate ISO in QEMU's default BIOS mode. A representative command
is:

```bash
qemu-system-x86_64 -m 2048 -cdrom "$ISO" -boot d
```

Record the exact command rather than relying on this example if the VM uses
additional options. Confirm that the guest reaches the visible AutoLoader
screen, save a VGA capture, switch to tty2 for service/journal evidence, and
collect all other evidence above. Mark BIOS **PASS** only when the tested ISO
hash and both display/service evidence are recorded; otherwise record **FAIL**
or **NOT_PERFORMED** with the reason.

## UEFI boot

Boot the same candidate ISO with the host's reviewed OVMF firmware. Substitute
the actual firmware path and record its package/version and SHA-256:

```bash
qemu-system-x86_64 -m 2048 -bios "$OVMF" -cdrom "$ISO" -boot d
```

Repeat the VGA capture, tty2 service/journal inspection, command, and log
capture. Mark UEFI **PASS** only when the tested ISO hash exactly matches the
BIOS run and all required display/service evidence is available; otherwise
record **FAIL** or **NOT_PERFORMED** with the reason.

## Evidence report

Store the evidence outside the ISO output directory. Include:

- candidate ISO canonical path, size, SHA-256, and builder manifest reference;
- separate BIOS and UEFI QEMU commands, QEMU/OVMF identities, timestamps, and
  exit statuses;
- VGA screenshot/video showing visible application startup in each mode, with
  the capture method and hash;
- tty2 `systemctl status` and `journalctl` output for each guest boot while
  the app remains running on tty1;
- host stdout/stderr logs, any observed failure, and the final BIOS/UEFI result.

The report validates boot and service startup only. It does not establish
firmware compatibility or permit a probe, backup, write, restore, recovery, or
USB-media operation. No image or missing evidence must remain explicitly
**NOT_PERFORMED**; never describe a simulated or unperformed boot as successful.
