"""
Post-flash verification routines.
"""
import hashlib
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Optional

from .flashrom import FlashromRunner, ProgrammerType

logger = logging.getLogger(__name__)

# Intel Flash Descriptor (IFD) Signature at offset 0x10 (16)
IFD_MAGIC = b"\x5a\xa5\xf0\x0f"
# Coreboot Filesystem (CBFS) Magic Signatures
CBFS_HEADER_MAGIC = b"ORBC"
CBFS_HEADER_MAGIC_LE = b"CBRO"
CBFS_FILE_MAGIC = b"LARCHIVE"


def calculate_entropy(data: bytes) -> float:
    """Calculates the Shannon entropy of a byte string in a single O(N) pass."""
    if not data:
        return 0.0
    length = len(data)
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    return -sum((c / length) * math.log2(c / length) for c in counts if c > 0)


@dataclass
class VerificationResult:
    verified: bool
    sha256_match: bool
    structure_valid: bool
    details: str


class FlashVerifier:
    def __init__(self):
        self.runner = FlashromRunner()

    def _hash_file(self, path: Path | str) -> str:
        sha256 = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def verify_flash(
        self,
        programmer: ProgrammerType,
        expected_rom_path: str,
        temp_read_path: str,
        chip_name: Optional[str] = None,
        layout_region: Optional[str] = None,
        cleanup_temp: bool = True
    ) -> VerificationResult:
        logger.info(f"Verifying flash against {expected_rom_path}")
        if not Path(expected_rom_path).exists():
            return VerificationResult(False, False, False, f"Expected ROM file not found: {expected_rom_path}")

        try:
            res = self.runner.read_rom(programmer, temp_read_path, chip_name=chip_name, layout_region=layout_region)
            if not res.success:
                return VerificationResult(False, False, False, f"Failed to read flash for verification: {res.log_output}")
            
            try:
                expected_hash = self._hash_file(expected_rom_path)
                read_hash = self._hash_file(temp_read_path)
            except Exception as e:
                return VerificationResult(False, False, False, f"Error computing file hashes: {e}")
            
            match = expected_hash == read_hash
            structure_valid = self.check_rom_structure(temp_read_path)
            
            details = "Verification successful" if (match and structure_valid) else (
                "Hash mismatch" if not match else "Invalid ROM structure / Degenerate dump"
            )
            
            return VerificationResult(
                match and structure_valid, 
                match, 
                structure_valid, 
                details
            )
        finally:
            if cleanup_temp:
                try:
                    Path(temp_read_path).unlink(missing_ok=True)
                except Exception:
                    pass

    def check_rom_structure(self, rom_path: str | Path) -> bool:
        """
        Validates the internal structure of a ROM file.
        Checks for non-degenerate entropy, IFD signature, or CBFS components.
        """
        path = Path(rom_path)
        if not path.exists():
            return False
            
        try:
            st_size = path.stat().st_size
            if st_size < 4096 or st_size > 64 * 1024 * 1024:
                return False

            data = path.read_bytes()
                
            # Degenerate blank check
            ff_count = data.count(b"\xff")
            zero_count = data.count(b"\x00")
            total = len(data)
            if ff_count == total or zero_count == total:
                return False
            if (ff_count / total >= 0.999 or zero_count / total >= 0.999) and calculate_entropy(data) < 0.05:
                return False
                
            entropy = calculate_entropy(data)
            if entropy < 0.02:
                return False
                
            # If the dump has IFD signature at offset 0x10, it is a valid Intel Flash Descriptor
            has_ifd = len(data) >= 20 and data[16:20] == IFD_MAGIC
            
            # Or if it contains CBFS headers (Coreboot / Libreboot / Canoeboot payload)
            has_cbfs = CBFS_HEADER_MAGIC in data or CBFS_HEADER_MAGIC_LE in data or CBFS_FILE_MAGIC in data
            
            # For standalone top-chip payloads (e.g. top 4MB on X230), IFD is on bottom chip,
            # but CBFS or x86 reset vector at the end of ROM (0xFFFFFFF0) will be present
            has_reset_vector = len(data) >= 16 and data[-16:] != b"\xff" * 16
            
            if has_ifd or has_cbfs or has_reset_vector:
                return True
                
            # General fallback: if entropy is healthy (> 2.0) and not degenerate, treat as valid structure
            return entropy > 2.0
        except Exception as e:
            logger.warning(f"Error checking ROM structure for {rom_path}: {e}")
            return False

    def generate_report(self, result: VerificationResult) -> Dict[str, Any]:
        return {
            "verified": result.verified,
            "sha256_match": result.sha256_match,
            "structure_valid": result.structure_valid,
            "details": result.details
        }


def verify_flash(
    programmer: ProgrammerType,
    expected_rom_path: str | Path,
    temp_read_path: Optional[str | Path] = None,
    chip_name: Optional[str] = None,
    layout_region: Optional[str] = None,
    cleanup_temp: bool = True
) -> VerificationResult:
    """Convenience function to run FlashVerifier with default temporary read path."""
    verifier = FlashVerifier()
    if temp_read_path is None:
        temp_read_path = Path(expected_rom_path).parent / f"verify_tmp_{Path(expected_rom_path).name}"
    return verifier.verify_flash(
        programmer=programmer,
        expected_rom_path=str(expected_rom_path),
        temp_read_path=str(temp_read_path),
        chip_name=chip_name,
        layout_region=layout_region,
        cleanup_temp=cleanup_temp
    )
