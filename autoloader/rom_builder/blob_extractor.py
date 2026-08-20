import os
import shutil
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from autoloader.exceptions import BlobExtractionError

logger = logging.getLogger(__name__)

@dataclass
class BlobSet:
    descriptor_path: Path
    me_path: Optional[Path] = None
    gbe_path: Optional[Path] = None

class BlobExtractor:
    """Binary blob extraction using ifdtool."""

    def __init__(self, workspace_dir: Path):
        self.workspace = workspace_dir
        self.output_dir = self.workspace / "blobs"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def extract_all(self, stock_bios_path: Path, requires_me: bool = True) -> BlobSet:
        """Runs ifdtool -x to extract regions and auto-renames them."""
        stock_bios_path = Path(stock_bios_path)
        if not stock_bios_path.exists():
            raise FileNotFoundError(f"Stock BIOS dump not found at {stock_bios_path}")

        logger.info(f"Extracting blobs from {stock_bios_path}")
        try:
            subprocess.run(
                ["ifdtool", "-x", str(stock_bios_path.resolve())],
                cwd=self.output_dir,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=60
            )
        except subprocess.TimeoutExpired as e:
            raise BlobExtractionError("ifdtool extraction timed out.") from e
        except subprocess.CalledProcessError as e:
            raise BlobExtractionError(f"ifdtool extraction failed: {e.output.decode('utf-8', errors='ignore')}")

        # Auto-rename extracted files
        extracted_desc = self.output_dir / "flashregion_0_flashdescriptor.bin"
        extracted_me = self.output_dir / "flashregion_2_intel_me.bin"
        extracted_gbe = self.output_dir / "flashregion_3_gbe.bin"

        final_desc = self.output_dir / "descriptor.bin"
        final_me = self.output_dir / "me.bin"
        final_gbe = self.output_dir / "gbe.bin"

        if not extracted_desc.exists():
            raise BlobExtractionError("Missing critical flash descriptor region after extraction.")

        if requires_me and not extracted_me.exists():
            raise BlobExtractionError("Missing critical Intel ME region after extraction.")

        shutil.move(str(extracted_desc), str(final_desc))
        
        has_me = extracted_me.exists()
        if has_me:
            shutil.move(str(extracted_me), str(final_me))

        has_gbe = extracted_gbe.exists()
        if has_gbe:
            shutil.move(str(extracted_gbe), str(final_gbe))

        return BlobSet(
            descriptor_path=final_desc,
            me_path=final_me if has_me else None,
            gbe_path=final_gbe if has_gbe else None
        )

    def validate_blobs(self, blob_set: BlobSet, requires_me: bool = True) -> bool:
        """Validates extracted blobs (file exists, size checks, IFD signature)."""
        if not blob_set.descriptor_path.exists() or blob_set.descriptor_path.stat().st_size != 4096:
            logger.error("Flash descriptor is invalid or missing.")
            return False

        # Validate IFD magic at offset 0x10
        desc_data = blob_set.descriptor_path.read_bytes()
        if len(desc_data) < 20 or desc_data[16:20] != b"\x5a\xa5\xf0\x0f":
            logger.error("Flash descriptor is corrupt or missing standard Intel IFD signature (0x5aa5f00f).")
            return False

        if requires_me:
            if not blob_set.me_path or not blob_set.me_path.exists() or blob_set.me_path.stat().st_size == 0:
                logger.error("Intel ME region is invalid or missing.")
                return False

        if blob_set.gbe_path:
            if not blob_set.gbe_path.exists() or blob_set.gbe_path.stat().st_size != 8192:
                logger.warning("GbE region is present but size is not exactly 8KB.")
        
        return True

    def get_blob_set(self, stock_bios_path: Path, requires_me: bool = True) -> BlobSet:
        """Extract and validate blobs in one step."""
        blob_set = self.extract_all(stock_bios_path, requires_me=requires_me)
        if not self.validate_blobs(blob_set, requires_me=requires_me):
            raise BlobExtractionError("Blob validation failed after extraction.")
        return blob_set
