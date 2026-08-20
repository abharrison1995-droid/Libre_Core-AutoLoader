# Libre_Core AutoLoader - VM Sandbox & Hardware Simulator

This directory provides a **1-click, zero-command virtual sandbox** to test and practice ThinkPad BIOS modifications in an isolated QEMU environment without any risk of bricking physical hardware.

---

## 🚀 How to Launch the Simulator

### On Windows (1-Click):
Double-click [`launch_vm.bat`](file:///c:/Users/P50/Desktop/Libre_Core%20AutoLoader/test_vm/launch_vm.bat) or run in PowerShell:
```powershell
python test_vm/launch_vm.py
```

### What Happens Automatically:
1. **Dependency Verification**: The launcher checks if QEMU is installed. If not, it offers an automatic 1-keystroke install via `winget`.
2. **Interactive ThinkPad Selection**: Select which laptop model you want to simulate (`X230`, `T430`, `T440p`, `T480`, `X200`, `T500`).
3. **SMBIOS Hardware Injection**: Injects real Lenovo DMI strings (`manufacturer=LENOVO`, `product=2325B11`, `version=G2ET64WW (2.60)`).
4. **Zero-Risk Virtual Flash Chips**: Automatically connects virtual 4MB, 8MB, and 12MB SPI flash chips located in `mock_chips/`.

---

## 📁 Directory Structure

| File | Purpose |
| :--- | :--- |
| `launch_vm.bat` | Double-clickable 1-click launcher for Windows |
| `launch_vm.py` | Interactive menu, QEMU runner, and SMBIOS manager |
| `smbios_profiles.json` | Authentic DMI hardware tables for all 10 ThinkPad models |
| `mock_chips/` | Pre-generated 4MB/8MB/12MB virtual SPI flash chips for read/write testing |
