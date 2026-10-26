# Arch-boxes debug-shell attempts - 2026-10-21

**Result: INCOMPLETE. No guest shell, verified command, native Linux build, product ISO, or hardware validation.**

Three fresh qcow2 overlays were created with the verified Arch-boxes base image as backing. Each QEMU run used QEMU 10.2.1 with TCG, four vCPUs, 4 GiB RAM, a PC machine, a virtio disk, no guest network adapter, and no USB or physical-device passthrough. Each overlay is disposable and the base image was left unchanged.

The first run displayed SeaBIOS and GRUB, then booted Arch. The VGA capture showed systemd reaching basic system initialization but waiting for the absent /dev/disk/by-uuid/86a96c15-9d21-45be-a21b-c81a5423eac4 device. A later capture showed a blank display; no prompt or command result was observed. The VM was stopped through its QEMU monitor. Serial output remained empty because the stock kernel command line did not enable serial console output.

A second run attempted to hold Shift during GRUB startup. It proceeded to the default Arch boot; no GRUB editor or menu was captured. A QMP controlled key attempt and reset did not produce a guest shell. QEMU exited; no command was run in the guest.

The images and raw boot captures are retained beside this report where available. Read-only qemu-img check passed on all four overlays from the debug-shell, GRUB menu, GRUB shift, and QMP shift attempts. The signed backing image SHA-256 at the end of the run was bb888a084568f4e68b1260ffa614ebf806c79668fa20f946c691973538f4d33c. A final process check found no running QEMU instance.

This remains only a VM boot diagnostic. It does not pass Linux build, current-source product ISO, Live USB application startup, firmware artifact, or any hardware workflow.
