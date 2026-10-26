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

from autoloader.exceptions import BackupUnavailableError, BackupVerificationError
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
    # Count dominant blank values in optimized bytes operations. Sampling for
    # entropy can alias periodic firmware data into a constant-looking sample
    # (for example, a 64-byte pattern sampled every 128 bytes), so do not use
    # approximate entropy to reject a candidate backup.
    if ff_count / total >= 0.999 or zero_count / total >= 0.999:
        return True
    return False


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
                    raise BackupVerificationError(
                        f"Manifest at {self.manifest_file} is malformed; refusing to reset or trust it."
                    )
                if any(not isinstance(item, dict) for item in data):
                    raise BackupVerificationError(
                        f"Manifest at {self.manifest_file} contains a malformed entry; refusing to trust it."
                    )
                return data
        except FileNotFoundError:
            return []
        except json.JSONDecodeError as e:
            raise BackupVerificationError(
                f"Manifest at {self.manifest_file} is corrupted; refusing to reset or trust it: {e}"
            ) from e

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
        del model, programmer, chip_label, chip_name, layout_region, extra_args
        raise BackupUnavailableError(
            "Single-chip backup capture is disabled. Use the evidence-bound multi-chip backup service; "
            "no hardware was read."
        )

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
        del backup_path, programmer, chip_name, layout_region, extra_args
        logger.warning("Physical restoration is disabled; no backup file was read and no write was attempted.")
        return False
