"""
Generate virtual mock SPI flash dumps for zero-risk QEMU / VM simulation.
"""
from pathlib import Path
import hashlib

def generate_mock_chips():
    dest_dir = Path(__file__).parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. 4MB Top Chip (BIOS Region for X230/T430/T440p)
    top_4mb = dest_dir / "mock_top_4mb.rom"
    top_data = (b"MOCK_TOP_BIOS_4MB_" * 100).ljust(4 * 1024 * 1024, b"\xFF")
    top_4mb.write_bytes(top_data)
    
    # 2. 8MB Bottom Chip (IFD + ME + GbE for X230/T430/T440p)
    bottom_8mb = dest_dir / "mock_bottom_8mb.rom"
    bottom_data = (b"MOCK_BOTTOM_IFD_ME_8MB_" * 100).ljust(8 * 1024 * 1024, b"\xFF")
    bottom_8mb.write_bytes(bottom_data)
    
    # 3. 12MB Combined ROM
    combined_12mb = dest_dir / "mock_combined_12mb.rom"
    combined_12mb.write_bytes(bottom_data + top_data)
    
    # 4. 8MB Monolithic ROM (X200/T400/T500)
    mono_8mb = dest_dir / "mock_monolithic_8mb.rom"
    mono_8mb.write_bytes((b"MOCK_MONO_X200_8MB_" * 100).ljust(8 * 1024 * 1024, b"\xFF"))
    
    # 5. 16MB System ROM (T480)
    t480_16mb = dest_dir / "mock_t480_16mb.rom"
    t480_16mb.write_bytes((b"MOCK_T480_BOOTGUARD_16MB_" * 100).ljust(16 * 1024 * 1024, b"\xFF"))
    
    print(f"Generated 5 mock SPI flash images in {dest_dir}")

if __name__ == "__main__":
    generate_mock_chips()
