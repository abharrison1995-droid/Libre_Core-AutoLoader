"""
Backup and verification of ROM.
"""
import hashlib
import json
import logging
import math
import os
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from datetime import datetime

from autoloader.exceptions import FlashError, BackupVerificationError
from .flashrom import FlashromRunner, ProgrammerType

logger = logging.getLogger(__name__)


def calculate_entropy(data: bytes) -> float:
    """Calculate the Shannon entropy of a byte string."""
    if not data:
        return 0.0
    entropy = 0.0
    length = len(data)
    # Count byte occurrences
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    for count in counts:
        if count > 0:
            p = count / length
            entropy -= p * math.log2(p)
    return entropy


def is_degenerate_dump(data: bytes) -> bool:
    """
    Check if a flash dump is degenerate (e.g. disconnected clip reading all 0xFF or 0x00).
    A dump is degenerate if it is empty, uniformly 0xFF/0x00, or has near-zero entropy (< 0.05).
    """
    if not data:
        return True
    total = len(data)
    ff_count = data.count(b"\xff")
    zero_count = data.count(b"\x00")
    if ff_count == total or zero_count == total:
        return True
    # If practically entire file is 0xFF or 0x00 (>= 99.9% on large files) and entropy is near 0
    if (ff_count / total >= 0.999 or zero_count / total >= 0.999) and calculate_entropy(data) < 0.05:
        return True
    return calculate_entropy(data) < 0.02


@dataclass
class BackupResult:
    path1: str
    path2: str
    sha256: str
    match: bool
    chip_info: str


class BackupManager:
    def __init__(self, backup_dir: str = "backups", runner: Optional[FlashromRunner] = None):
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_file = self.backup_dir / "manifest.json"
        self.runner = runner or FlashromRunner()
        self._init_manifest()
    
    def _init_manifest(self):
        if not self.manifest_file.exists():
            self._save_manifest([])
    
    def _load_manifest(self) -> List[Dict]:
        try:
            with open(self.manifest_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if not isinstance(data, list):
                    logger.warning(f"Manifest at {self.manifest_file} is not a list (got {type(data).__name__}), resetting.")
                    return []
                return data
        except FileNotFoundError:
            return []
        except json.JSONDecodeError as e:
            logger.warning(f"Manifest at {self.manifest_file} corrupted, resetting manifest: {e}")
            return []

    def _save_manifest(self, data: List[Dict]):
        """Atomically saves manifest data to prevent state loss on interruption."""
        tmp_file = self.manifest_file.with_suffix(".tmp")
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
        os.replace(tmp_file, self.manifest_file)
            
    def _hash_file(self, path: Path) -> str:
        sha256 = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def get_manifest(self) -> List[Dict]:
        return self._load_manifest()

    def create_backup(
        self,
        model: str,
        programmer: ProgrammerType,
        chip_label: str,
        chip_name: Optional[str] = None,
        layout_region: Optional[str] = None,
        extra_args: Optional[List[str]] = None
    ) -> BackupResult:
        if programmer == ProgrammerType.INTERNAL and layout_region is None:
            layout_region = "bios"

        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        path1 = self.backup_dir / f"{model}_{date_str}_{chip_label}_backup1.rom"
        path2 = self.backup_dir / f"{model}_{date_str}_{chip_label}_backup2.rom"

        logger.info(f"Creating first backup at {path1}")
        res1 = self.runner.read_rom(
            programmer,
            str(path1),
            chip_name=chip_name,
            layout_region=layout_region,
            extra_args=extra_args
        )
        if not res1.success:
            raise FlashError(f"First backup failed: {res1.log_output}")
        
        # Immediate validation of first read
        data1 = path1.read_bytes()
        if is_degenerate_dump(data1):
            raise BackupVerificationError(
                f"First backup read from {programmer.value} is blank or corrupted (all 0xFF/0x00). "
                "Check SPI clip connection and seating."
            )
        
        logger.info(f"Creating second backup at {path2}")
        res2 = self.runner.read_rom(
            programmer,
            str(path2),
            chip_name=chip_name,
            layout_region=layout_region,
            extra_args=extra_args
        )
        if not res2.success:
            raise FlashError(f"Second backup failed: {res2.log_output}")
        
        data2 = path2.read_bytes()
        if is_degenerate_dump(data2):
            raise BackupVerificationError(
                f"Second backup read from {programmer.value} is blank or corrupted (all 0xFF/0x00). "
                "Check SPI clip connection and seating."
            )
        
        match, sha = self.verify_backup_pair(path1, path2)
        chip_info = chip_name or res1.chip_name or "Unknown"

        if match:
            logger.info("Backups match perfectly.")
            manifest = self._load_manifest()
            manifest.append({
                "model": model,
                "date": date_str,
                "chip_label": chip_label,
                "path1": str(path1),
                "path2": str(path2),
                "sha256": sha,
                "chip_info": chip_info
            })
            self._save_manifest(manifest)
        else:
            logger.warning("Backups do NOT match. Bad clip connection? Retrying is recommended.")
            raise BackupVerificationError(
                f"Backup verification failed: Pass 1 ({path1.name}) and Pass 2 ({path2.name}) hashes do not match."
            )
        
        return BackupResult(str(path1), str(path2), sha, match, chip_info)

    def verify_backup_pair(self, path1: Path, path2: Path) -> Tuple[bool, str]:
        path1 = Path(path1)
        path2 = Path(path2)
        if not path1.exists() or not path2.exists():
            return False, ""
        data1 = path1.read_bytes()
        data2 = path2.read_bytes()
        if is_degenerate_dump(data1) or is_degenerate_dump(data2):
            return False, ""
        hash1 = self._hash_file(path1)
        hash2 = self._hash_file(path2)
        return hash1 == hash2, hash1

    def list_backups(self) -> List[Dict]:
        return self.get_manifest()

    def restore_from_backup(
        self,
        backup_path: str,
        programmer: ProgrammerType,
        chip_name: Optional[str] = None,
        layout_region: Optional[str] = None,
        extra_args: Optional[List[str]] = None
    ) -> bool:
        if programmer == ProgrammerType.INTERNAL and layout_region is None:
            layout_region = "bios"

        path = Path(backup_path)
        if not path.exists():
            raise FileNotFoundError(f"Backup {backup_path} not found.")
        
        data = path.read_bytes()
        if is_degenerate_dump(data):
            raise BackupVerificationError(f"Cannot restore from blank or degenerate backup: {backup_path}")
            
        logger.warning(f"Restoring backup {backup_path} to {programmer.value}")
        res = self.runner.write_rom(
            programmer,
            backup_path,
            chip_name=chip_name,
            layout_region=layout_region,
            extra_args=extra_args
        )
        if not res.success:
            logger.error(f"Restore write failed: {res.log_output}")
            return False
            
        logger.info("Verifying restored ROM against backup...")
        verify_res = self.runner.verify_rom(
            programmer,
            backup_path,
            chip_name=chip_name,
            layout_region=layout_region,
            extra_args=extra_args
        )
        return verify_res.success
