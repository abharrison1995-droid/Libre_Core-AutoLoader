import pytest
from pathlib import Path
from autoloader.flasher.splitter import split_rom, merge_roms, BYTES_PER_MB
from autoloader.exceptions import FlashError


def test_split_and_merge_12mb_rom(tmp_path: Path):
    """Test splitting a 12MB ROM into 8MB bottom and 4MB top, then merging back."""
    bottom_size_mb = 8
    top_size_mb = 4
    bottom_bytes = b"B" * (bottom_size_mb * BYTES_PER_MB)
    top_bytes = b"T" * (top_size_mb * BYTES_PER_MB)
    combined_bytes = bottom_bytes + top_bytes

    rom_path = tmp_path / "combined_12mb.rom"
    rom_path.write_bytes(combined_bytes)

    out_dir = tmp_path / "split_out"
    bottom_path, top_path = split_rom(
        rom_path=rom_path,
        top_size_mb=top_size_mb,
        bottom_size_mb=bottom_size_mb,
        output_dir=out_dir
    )

    assert bottom_path.exists()
    assert top_path.exists()
    assert bottom_path.name == "bottom_8mb.rom"
    assert top_path.name == "top_4mb.rom"
    assert bottom_path.read_bytes() == bottom_bytes
    assert top_path.read_bytes() == top_bytes

    # Merge back
    merged_path = tmp_path / "merged_12mb.rom"
    result = merge_roms(bottom_path, top_path, merged_path)

    assert result == merged_path
    assert merged_path.exists()
    assert merged_path.read_bytes() == combined_bytes


def test_split_rom_default_output_dir(tmp_path: Path):
    """Test splitting with default output_dir placing files next to source ROM."""
    bottom_size_mb = 8
    top_size_mb = 4
    combined_bytes = (b"\x11" * (bottom_size_mb * BYTES_PER_MB)) + (b"\x22" * (top_size_mb * BYTES_PER_MB))

    rom_path = tmp_path / "coreboot_12mb.rom"
    rom_path.write_bytes(combined_bytes)

    bottom_path, top_path = split_rom(rom_path)

    assert bottom_path.parent == tmp_path
    assert top_path.parent == tmp_path
    assert bottom_path.read_bytes() == b"\x11" * (8 * BYTES_PER_MB)
    assert top_path.read_bytes() == b"\x22" * (4 * BYTES_PER_MB)


def test_split_rom_invalid_size(tmp_path: Path):
    """Test that split_rom raises FlashError on unexpected file size."""
    rom_path = tmp_path / "invalid_size.rom"
    rom_path.write_bytes(b"\x00" * 1024)

    with pytest.raises(FlashError, match="ROM size mismatch"):
        split_rom(rom_path, top_size_mb=4, bottom_size_mb=8)


def test_split_rom_nonexistent_file(tmp_path: Path):
    """Test that split_rom raises FileNotFoundError when input is missing."""
    missing_rom = tmp_path / "nonexistent.rom"
    with pytest.raises(FileNotFoundError):
        split_rom(missing_rom)


def test_merge_roms_missing_file(tmp_path: Path):
    """Test that merge_roms raises FileNotFoundError when a component is missing."""
    valid_file = tmp_path / "valid.rom"
    valid_file.write_bytes(b"DATA")
    missing_file = tmp_path / "missing.rom"
    output_file = tmp_path / "out.rom"

    with pytest.raises(FileNotFoundError):
        merge_roms(missing_file, valid_file, output_file)

    with pytest.raises(FileNotFoundError):
        merge_roms(valid_file, missing_file, output_file)


def test_split_rom_for_model_x230_and_t430s(tmp_path: Path):
    """Test model-aware splitting for 12MB x230 (8+4) and 16MB t430s (8+8)."""
    from autoloader.flasher.splitter import split_rom_for_model

    # X230: 12MB (8MB bottom, 4MB top)
    x230_rom = tmp_path / "x230_12mb.rom"
    x230_rom.write_bytes(b"\xAA" * (12 * BYTES_PER_MB))
    b_path, t_path = split_rom_for_model(x230_rom, "x230")
    assert b_path.name == "bottom_8mb.rom"
    assert t_path.name == "top_4mb.rom"

    # T430s: 16MB (8MB bottom, 8MB top)
    t430s_rom = tmp_path / "t430s_16mb.rom"
    t430s_rom.write_bytes(b"\xBB" * (16 * BYTES_PER_MB))
    b_path_s, t_path_s = split_rom_for_model(t430s_rom, "t430s")
    assert b_path_s.name == "bottom_8mb.rom"
    assert t_path_s.name == "top_8mb.rom"
