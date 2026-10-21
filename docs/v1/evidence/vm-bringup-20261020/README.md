# VM bring-up attempt — 2026-10-20

## Result

**VM boot gate: NOT PASSED.** A final single-vCPU QEMU TCG boot of the official
Arch ISO mounted the live root, printed the Arch welcome banner, and started
systemd services. A harmless `id` command was sent over a host-loopback serial
console, but no identity result or interactive shell was returned within the
360-second run. A separate Arch-boxes guest reached installed-system systemd
startup but did not provide a usable login or SSH session. No guest build,
ROM, product ISO, application boot, or hardware validation was completed.

These are incomplete virtual-machine attempts, not successful Linux, firmware,
or hardware tests. No physical SPI, USB, or other hardware device was attached.

## Storage and base image

The user authorized the unmounted 254.9-GB ext4 partition as the VM workspace.
The workspace was created at
`/opt/librecore-autoloader-v1-vm`; no formatting or changes to pre-existing
partition contents were made. After the user ran the elevated mount command,
Ubuntu WSL mounted the existing ext4 partition read-write at
`/mnt/wsl/PHYSICALDRIVE1p2`. All new VM files were created within the approved
workspace directory.

The Arch-boxes base image is
`images/Arch-Linux-x86_64-basic.qcow2`. Its upstream signature verified with
fingerprint `1B9A16984A4E8CB448712D2AE0B78BF4326C6F8F`; its SHA-256 is
`bb888a084568f4e68b1260ffa614ebf806c79668fa20f946c691973538f4d33c`. The
separate scratch overlay is `vm/arch-builder-overlay.qcow2`; the final
`qemu-img check` reported no errors. The base image was attached read-only.
The overlay is diagnostic scratch state, not a clean or accepted build VM.
The overlay SHA-256 is
`239a727bed7f28e662773242f1765a19daaf3d9b3861c7148890c11ed5361aea`. Its
direct-boot kernel and initramfs copies have SHA-256 values
`d02afb36d5f99390fdd8d04dedf8894354f4bbdc5e7d44afa5b897639bb3707c` and
`8917717b66e93daa2b13788d21c0587d89fb67b9406de7229cd379ddd1ff8959`,
respectively. The guest root UUID used for that diagnostic boot was
`86a96c15-9d21-45be-a21b-c81a5423eac4`.

## Installed Arch-boxes boot

QEMU version: `10.2.1 (Debian 1:10.2.1+ds-1ubuntu3.2)`.

The bounded direct-kernel boot used TCG (`-accel tcg,thread=multi`), a PC
machine, 3 GiB RAM, 4 vCPUs, the scratch overlay as an IDE disk, the copied
kernel/initramfs, and a user-mode network port-forward for SSH. The kernel
command line selected the overlay's root UUID, enabled read-write root, sent
output to `ttyS0`, and requested `multi-user.target`.

The root filesystem mounted and systemd switched into the installed Arch
userspace. Startup continued waiting on device/service jobs; `/dev/ttyS0`
timed out, `systemd-nsresourced.service` failed, and the `systemd-tmpfiles`
device-node and kernel-time-sync jobs were still pending when the VM was
stopped. The forwarded port accepted a TCP connection, but SSH did not return
an SSH server banner or session. The VM did not reach a verified login. It was
stopped with SIGINT after approximately 75 seconds of wall time. This report
does not assign a root cause to those guest messages.

## Official Arch live ISO boot

The ISO was downloaded from the official Arch mirror at
`https://geo.mirror.pkgbuild.com/iso/2026.10.01/archlinux-2026.10.01-x86_64.iso`.
Its detached signature verified as a good signature from Pierre Schmitz under
the exact fingerprint published on the [Arch download page](https://archlinux.org/download/):
`3E80CA1A8B89F69CBA57D98A76A5EF9054449A5C`. The computed SHA-256 was
`684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5` and the
file size was 1,640,497,152 bytes.

The run notes record QEMU 10.2.1 with `-accel tcg,thread=multi`, a PC machine,
4 GiB RAM, 4 vCPUs, a read-only IDE CD-ROM, `-display none`, a serial log, an
HMP monitor socket, and `-no-reboot`. The exact argv was not retained. The
guest had no writable disk, network adapter, USB device, or physical-device
passthrough. QEMU reported
`VM status: running`; a screen capture taken after 90 seconds showed the
kernel in early initramfs hooks (`udev`, `archiso_pxe_nbd`, and `Triggering
uevents...`). The process showed approximately 110% CPU at 1 minute 56 seconds
elapsed. No shell or guest command was verified. QEMU was stopped with SIGTERM
after the screen capture.

The capture is `archiso-early-boot.png` in this directory. It shows the
observed screen only; it is not a boot-success image. Its SHA-256 is
`7a0f6208c97ddef22c1f8325fd40c6b2796d8dc601bc0f9f31133773fde96c48` and is
also listed in `SHA256SUMS`.

## Host acceleration recheck — 2026-10-20

The earlier status above records the initial TCG attempts. A later read-only
check found Windows 10 IoT Enterprise LTSC 2021, build 19044.7807, with WSL
3.0.3.0 and Ubuntu/Arch WSL2 distributions. `systeminfo` reports that a
hypervisor is detected. WMI still reports
`VirtualizationFirmwareEnabled: False` and
`SecondLevelAddressTranslationExtensions: False`; these flags are not proof
that the CPU lacks virtualization. Intel's specification for this exact
i3-3110M model lists both VT-x and Extended Page Tables (EPT) as supported
([Intel ARK](https://www.intel.com/content/www/us/en/products/sku/65700/intel-core-i33110m-processor-3m-cache-2-40-ghz/specifications.html)).

Ubuntu and Arch WSL exposed a `/dev/kvm` character node before the WSL restart,
but a diskless QEMU `-machine none,accel=kvm` handshake failed with `Could not
access KVM kernel module: No such device`. After explicitly setting
`nestedVirtualization=true` in a temporary `%USERPROFILE%\.wslconfig` and
restarting only WSL, the kernel did not expose VMX; `modprobe kvm_intel` failed
with `VMX not supported by CPU 0`, and WSL printed `Nested virtualization is
not supported on this machine`. The temporary config was removed after that
check. This matches the reported Windows 10 lifted-WSL nested-virtualization
regression ([WSL issue 40735](https://github.com/microsoft/WSL/issues/40735));
it does not show that Intel's hardware lacks VT-x/EPT. The official WSL
configuration reference documents the nested-virtualization setting
([Microsoft WSL configuration](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)).

The host's `WinHvPlatform.dll` is present, and a direct read-only
`WHvGetCapability(WHvCapabilityCodeHypervisorPresent)` call returned `S_OK`
and `HypervisorPresent=1`. QEMU's WHPX backend can use the Windows Hypervisor
Platform API, but QEMU requires the corresponding Windows feature to be
installed ([QEMU WHPX requirements](https://www.qemu.org/docs/master/system/whpx.html)).
Its installed state could not be queried: DISM returned error 740 because this
shell is not elevated; `Get-VM` and the `vmms` service are absent. No Windows
feature was changed.

The QEMU 11.1.0 Windows installer linked from the official QEMU download page
was downloaded for a possible WHPX test. Its SHA-512 matched the published
sidecar (`5bcf9eed634e8575a37b74f445af41a2fe4106da512d0c30c368301d4c105037fdfab40a5287367a28a957624cddebbc8c07e16c88ab6634f554cdf3d16bf543`).
Windows reported an unknown Authenticode status with the listed
Universität Mannheim signer; the download page states newer installers have
an expired signing certificate. PE metadata requests administrator
privileges. The installer was not run or installed. `7z` identified it as an
NSIS archive with about 1.26 GB uncompressed content, so its user-space QEMU
files could be extracted into the approved workspace if a Windows-hosted
QEMU run is needed. The recorded boot attempt instead used the QEMU already
installed in Ubuntu WSL. The QEMU download page links this Windows package and provides
the checksum ([QEMU downloads](https://www.qemu.org/download/)).

At the initial host recheck, the ext4 workspace had detached after a WSL
restart. Reattachment failed before any mount with
`WSL_E_ELEVATION_NEEDED_TO_MOUNT_DISK` (`0x80040315`), and a later
`Start-Process -Verb RunAs` launch was canceled before an elevated process
started. That captured recheck is historical; it records the state before the
user subsequently ran the mount command from elevated PowerShell. The
partition is now mounted read-write in Ubuntu WSL at
`/mnt/wsl/PHYSICALDRIVE1p2`, and the authorized workspace is available at
`/mnt/wsl/PHYSICALDRIVE1p2/opt/librecore-autoloader-v1-vm`. The user-authorized
operation attached the existing partition without formatting it. No Windows
feature change or full Windows restart occurred. Exact earlier host output
and package hash are retained in
[`host-acceleration-recheck-20261020.txt`](host-acceleration-recheck-20261020.txt).
The QEMU installer remains in the Windows temporary directory; the VM route
was attempted with the already-installed QEMU in WSL.

A Windows-native QEMU 11.1.2 WHPX diagnostic was attempted after this host
recheck. It booted the verified ISO's kernel and initramfs directly, with the
ISO attached read-only and no writable guest disk, network, USB, or physical
device. The first run used `q35,pic=off`; a follow-up restored the default
machine PIC. Both QMP handshakes and bounded QMP shutdowns succeeded, but both
guests panicked before a shell: `devtmpfs: unable to create devtmpfs -1`,
`Warning: unable to open an initial console`, and `Failed to execute /init
(error -1)`. The first panic occurred at about 8.7 seconds and the second at
about 9.4 seconds. QEMU reported the VM as running after the panic because
`-no-shutdown` was set; the test harness then sent QMP `quit` and QEMU exited
0. The serial logs and exact argument manifests are retained as
`archiso-whpx-direct-20261020-retry1-*` and
`archiso-whpx-direct-picdefault-20261020-*`.

The extracted initramfs was inspected read-only. Its first CPIO archive ends
at byte 230,476,884; the XZ stream starts at byte 230,481,920 and decompresses
to 67,665,920 bytes. The main archive contains an executable mode-0755 `/init`,
`/usr/bin/ash` symlinked to BusyBox, the executable BusyBox binary, and the
expected `/lib64 -> usr/lib` and dynamic-loader entries. This rules out a
missing `/init` entry in the inspected archive, but does not assign a root
cause to the guest's execution and devtmpfs errors. The boot path is a
kernel-only diagnostic, not a normal BIOS/UEFI ISO boot.

A separate 45-second Windows QEMU 11.1.2 TCG direct-kernel comparison with
the same verified ISO and default PIC reached `running early hook [udev]`,
`archiso_pxe_nbd`, and `Triggering uevents...`. It did not panic, but it also
did not reach a shell or execute a guest command before the harness sent QMP
`quit`; QEMU then exited 0. This matches the incomplete early-initramfs
behavior seen in the earlier TCG attempt and does not establish a working
guest. The argv and captures are retained as
`archiso-tcg-direct-retry1-20261020-*`. The preceding TCG harness file set
`archiso-tcg-direct-20261020-*` records an option-placement error before QEMU
started and is not counted as a guest attempt.

No VM guest login, Linux build, ROM, current-source product ISO, Live USB
application/service startup, or hardware validation was completed. The
initial TCG attempts remain incomplete observations; neither they nor these
WHPX diagnostics prove that TCG cannot boot or establish a working guest. The
native build, product ISO, application/service startup, and all three
real-device workflows remain open. No physical-device passthrough, firmware
write, or hardware recovery was done.

## Retained records and verification boundary

The adjacent raw serial logs, QEMU termination log, detached signatures,
published public keys, and Arch-boxes checksum file were copied from the VM
workspace without changing their originals. `SHA256SUMS` covers those files,
the overlay-check transcript, the host-acceleration recheck, this README, the
screenshots, and the later WHPX probe captures. For the earlier Arch-boxes and
TCG boots, the exact QEMU argv, shell command history,
signature-verification transcript, and original `qemu-img check` transcript
were not retained. The WHPX direct-kernel attempts have separate exact argv
JSON and serial/stderr captures. The older fingerprints, signature results,
launch configuration, timing, and installed-guest observations above remain
explicitly run notes; the retained detached signatures, public keys, and hash
values are provided for independent rechecking. A new read-only `qemu-img
check` after the boot attempts again reported no errors; its output is
preserved in `arch-builder-overlay-qemu-img-check.txt`. This confirms
overlay-file integrity only, not guest boot.

The two `archiso-20261020-*-serial.log` files have the same SHA-256 and are
byte-identical copies, not independent boot evidence.

## Single-vCPU BIOS ISO boot and serial shell probe — 2026-10-20

The final bounded boot used QEMU 10.2.1 with TCG, one vCPU, 4 GiB RAM, a PC
machine, the signature- and hash-verified Arch 2026.10.01 ISO attached as a
read-only IDE CD-ROM, `-nic none`, and no writable guest disk, USB device, or
physical-device passthrough. A QMP monitor controlled the Syslinux menu. The
menu command line was edited to add `console=ttyS0`; the captured
`archiso-tcg-single-serial-20261020-boot-options.png` shows that option. The
serial socket listened only on host loopback (`127.0.0.1`) to observe output
and send a harmless guest command; it was not a guest network adapter.

Serial output shows the kernel and ArchISO hooks, successful mounts of
`/dev/sr0` at `/run/archiso/bootmnt` and `/dev/loop0` at `/run/archiso/airootfs`,
the `Welcome to Arch Linux!` banner, and subsequent systemd service startup.
The harness then sent `id` over the serial connection. It did not observe
`uid=0(root)` or any other command result before the 360-second bound. The
retained result therefore records `welcome_banner: true`, `id_sent: true`,
and `id_root_result: false`; the VM was stopped by the bounded harness. This
is live-root/systemd progress, not a verified login, interactive shell,
command-execution, native-Linux, build, product-ISO, or hardware pass.

Exact launch arguments, result JSON, raw serial output, and the boot-options
capture are retained as `archiso-tcg-single-serial-20261020-*` and covered by
`SHA256SUMS`.

Nothing here verifies a guest login, Linux package installation, source build,
product ISO, firmware artifact, bootable application, physical device, write,
recovery, or hardware boot.

## QMP/VGA early-initramfs follow-up — 2026-10-20

**Result: INCOMPLETE; no shell or guest command was obtained.** A separate
QEMU 10.2.1 TCG diagnostic used one vCPU and 4 GiB RAM with the verified Arch
2026.10.01 ISO attached read-only. It had no guest network adapter, writable
disk, source share, USB device, or physical-device passthrough. The monitor
was local QMP.

At 152.088 seconds, the captured full VGA frame still showed early initramfs
`udev` at `Triggering uevents...`; no prompt was visible. No guest keyboard
input was sent. The operator issued QMP `quit` at 214.277 seconds; QEMU exited
with status 0, which records orderly process shutdown only. The ISO SHA-256
before and after was unchanged at
`684ded26c63240ff4a41e8c25ee84ea6da233f557364821f13d12c2b0a9059a5`.

The run established no shell, verified command, package/build operation,
product ISO, firmware result, or hardware validation. The raw serial log,
argv/result JSON, and both PPM and PNG forms of the VGA capture are listed in
`SHA256SUMS`. The adjacent Python helper was edited after the run only to
rename a misleading guest-command result field; its current hash therefore
identifies that post-run terminology correction, not the exact helper bytes
used during execution. The run-result JSON itself records no keyboard action
or guest command and remains unchanged.
