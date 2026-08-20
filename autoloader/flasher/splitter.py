import os
import logging
from pathlib import Path
from typing import Optional, Tuple

from autoloader.exceptions import FlashError
from autoloader.database.models import get_model

logger = logging.getLogger(__name__)

BYTES_PER_MB = 1024 * 1024


def split_rom_for_model(
    rom_path: Path,
    model_id: str,
    *,
    output_dir: Optional[Path] = None
) -> Tuple[Path, Path]:
    """
    Splits a combined ROM file into bottom and top chip images based on model database specifications.
    
    Supports:
    - 12MB dual-chip models (X230, X230t, T430, T530, W530, T440p): 8MB bottom + 4MB top
    - 16MB dual-chip models (T430s): 8MB bottom + 8MB top
    """
    model = get_model(model_id)
    if not model.is_dual_chip:
        raise FlashError(
            f"Model {model_id} is not a dual-chip system with distinct bottom/top chips. "
            f"Use split_rom() directly for single-chip variants."
        )
    
    # Locate chips by label/position
    bottom_chip = next(c for c in model.flash_chips if c.label.lower() == "bottom")
    top_chip = next(c for c in model.flash_chips if c.label.lower() == "top")

    return split_rom(
        rom_path,
        bottom_size_mb=bottom_chip.size_mb,
        top_size_mb=top_chip.size_mb,
        output_dir=output_dir
    )


def split_rom(
    rom_path: Path,
    *,
    bottom_size_mb: int = 8,
    top_size_mb: int = 4,
    output_dir: Optional[Path] = None
) -> Tuple[Path, Path]:
    """
    Splits a combined ROM file into bottom and top chip images.

    For example, a 12MB combined ROM is split into:
    - bottom_8mb.rom (Flash Descriptor + Intel ME + GbE)
    - top_4mb.rom (BIOS region / Coreboot payload)

    Args:
        rom_path: Path to the combined input ROM file.
        bottom_size_mb: Size in MB of the bottom chip (default 8, keyword-only).
        top_size_mb: Size in MB of the top chip (default 4, keyword-only).
        output_dir: Destination directory for the split files. Defaults to rom_path.parent.

    Returns:
        Tuple of (bottom_path, top_path).

    Raises:
        FileNotFoundError: If rom_path does not exist.
        FlashError: If rom_path file size does not match expected combined size.
    """
    rom_path = Path(rom_path)
    if not rom_path.exists():
        raise FileNotFoundError(f"Input ROM file not found: {rom_path}")

    expected_total_bytes = (bottom_size_mb + top_size_mb) * BYTES_PER_MB
    actual_size = rom_path.stat().st_size

    if actual_size != expected_total_bytes:
        raise FlashError(
            f"ROM size mismatch: expected {expected_total_bytes} bytes ({bottom_size_mb + top_size_mb}MB), "
            f"found {actual_size} bytes."
        )

    out_dir = Path(output_dir) if output_dir else rom_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    bottom_bytes = bottom_size_mb * BYTES_PER_MB
    data = rom_path.read_bytes()

    bottom_data = data[:bottom_bytes]
    top_data = data[bottom_bytes:]

    bottom_path = out_dir / f"bottom_{bottom_size_mb}mb.rom"
    top_path = out_dir / f"top_{top_size_mb}mb.rom"

    # Atomic write for bottom ROM
    bottom_tmp = bottom_path.with_suffix(".tmp")
    bottom_tmp.write_bytes(bottom_data)
    os.replace(bottom_tmp, bottom_path)

    # Atomic write for top ROM
    top_tmp = top_path.with_suffix(".tmp")
    top_tmp.write_bytes(top_data)
    os.replace(top_tmp, top_path)

    logger.info(
        f"Split {rom_path.name} ({actual_size} bytes) -> "
        f"{bottom_path.name} ({len(bottom_data)} bytes) + {top_path.name} ({len(top_data)} bytes)"
    )

    return bottom_path, top_path


def merge_roms(
    bottom_path: Path,
    top_path: Path,
    output_path: Path
) -> Path:
    """
    Concatenates bottom and top chip images into a single unified ROM.

    Args:
        bottom_path: Path to the bottom chip ROM (Descriptor/ME/GbE).
        top_path: Path to the top chip ROM (BIOS/Payload).
        output_path: Path to write the unified ROM file.

    Returns:
        Path to the merged output file.

    Raises:
        FileNotFoundError: If bottom_path or top_path does not exist.
    """
    bottom_path = Path(bottom_path)
    top_path = Path(top_path)
    output_path = Path(output_path)

    if not bottom_path.exists():
        raise FileNotFoundError(f"Bottom ROM file not found: {bottom_path}")
    if not top_path.exists():
        raise FileNotFoundError(f"Top ROM file not found: {top_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    bottom_data = bottom_path.read_bytes()
    top_data = top_path.read_bytes()

    if len(bottom_data) >= 20 and bottom_data[16:20] != b"\x5a\xa5\xf0\x0f":
        logger.warning(f"Bottom ROM {bottom_path.name} is missing Intel Flash Descriptor signature (0x5aa5f00f at offset 0x10). Ensure bottom and top ROMs are not swapped.")

    merged_data = bottom_data + top_data
    if len(merged_data) % (1024 * 1024) != 0:
        logger.warning(f"Merged ROM size ({len(merged_data)} bytes) is not an exact MB multiple.")

    # Atomic write for merged ROM
    out_tmp = output_path.with_suffix(".tmp")
    out_tmp.write_bytes(merged_data)
    os.replace(out_tmp, output_path)

    logger.info(
        f"Merged {bottom_path.name} ({len(bottom_data)} bytes) + {top_path.name} ({len(top_data)} bytes) "
        f"-> {output_path.name} ({len(merged_data)} bytes)"
    )

    return output_path
