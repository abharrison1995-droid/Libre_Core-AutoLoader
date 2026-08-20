# Virtual Machine (VM) Testing Guide

This guide explains how to safely test **Libre_Core AutoLoader** in a Virtual Machine environment before deploying on physical hardware.

---

## 1. Testing the Standalone Application (Windows VM)

### Requirements
- Any Windows VM (Windows 10 / 11) in VirtualBox, VMware, Hyper-V, or QEMU.
- No Python installation required (standalone bundle).

### Steps
1. Copy the `dist/autoloader` folder into your Windows VM.
2. Open PowerShell / Command Prompt inside the VM.
3. Run:
   ```cmd
   autoloader.exe detect
   autoloader.exe build-rom --model x230 --base coreboot --payload seabios
   autoloader.exe run
   ```
4. The Textual TUI wizard will launch directly inside your terminal.

---

## 2. Testing the Bootable USB Environment (Linux VM / QEMU)

The bootable USB environment is built with Archiso. You can boot the generated `.iso` directly in any hypervisor.

### Option A: VirtualBox
1. Create a new VM: **Type**: Linux, **Version**: Arch Linux (64-bit).
2. **Memory**: At least 2048 MB (2 GB RAM).
3. **Storage**: Attach the built `libre-core-autoloader-*.iso` to the Optical Drive.
4. **System -> Motherboard**: 
   - Test **Legacy BIOS**: Leave "Enable EFI" unchecked.
   - Test **UEFI**: Check "Enable EFI (special OSes only)".
5. Start VM. The system will auto-login to tty1 and automatically start the AutoLoader wizard.

### Option B: QEMU (Quick Terminal Test)
```bash
# Test in Legacy BIOS mode:
qemu-system-x86_64 -m 2048 -cdrom build_usb/libre-core-autoloader-*.iso -boot d

# Test in UEFI mode (using OVMF):
qemu-system-x86_64 -m 2048 -bios /usr/share/ovmf/OVMF.fd -cdrom build_usb/libre-core-autoloader-*.iso -boot d
```

---

## 3. Safe SPI Hardware Emulation Testing (Dummy Flashrom)

You can test full read/backup/verify/write cycles inside a VM **without physical hardware or risk of bricking** using `flashrom`'s built-in dummy programmer.

### Create a Virtual 8MB SPI Chip with Mock Lenovo BIOS
```bash
# 1. Create a mock 8MB ROM file (filled with 0xFF pattern)
python3 -c "open('dummy_flash.rom', 'wb').write(b'\xFF' * (8 * 1024 * 1024))"

# 2. Test probing the virtual SPI chip
flashrom -p dummy:emulate=W25Q64FV,image=dummy_flash.rom

# 3. Test reading / backup from dummy chip
flashrom -p dummy:emulate=W25Q64FV,image=dummy_flash.rom -r backup1.rom
flashrom -p dummy:emulate=W25Q64FV,image=dummy_flash.rom -r backup2.rom
sha256sum backup1.rom backup2.rom

# 4. Test writing to dummy chip
flashrom -p dummy:emulate=W25Q64FV,image=dummy_flash.rom -w new_coreboot.rom
```

---

## 4. Testing Hardware SPI Programmers via USB Passthrough

If you want to test physical hardware (CH341A, Raspberry Pi Pico / Bus Pirate) from within your VM:
1. In **VirtualBox**: Go to VM Settings -> **USB** -> Enable USB Controller -> Add USB Filter for `WinChipHead CH341` or your programmer.
2. In **VMware**: Go to VM -> Removable Devices -> Select your USB Programmer -> **Connect (Disconnect from host)**.
3. Verify detection inside the VM:
   ```bash
   lsusb
   flashrom -p ch341a_spi
   ```
