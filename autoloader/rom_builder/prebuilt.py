import os
import shutil
import hashlib
import logging
import subprocess
import urllib.request
from pathlib import Path
from typing import List, Dict

from autoloader.config import BUILD_TIMEOUT
from autoloader.exceptions import BuildError

logger = logging.getLogger(__name__)

class PrebuiltManager:
    """Pre-built ROM management."""

    def __init__(self, storage_dir: Path):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def list_available(self, model_id: str, firmware_base: str) -> List[Dict[str, str]]:
        """Catalogs available ROMs by model, payload, and version."""
        logger.info(f"Fetching available {firmware_base} releases for {model_id}...")
        # Release catalog
        return [
            {
                "version": "20240504",
                "url": f"https://mirrors.mit.edu/libreboot/stable/20240504/roms/libreboot-20240504_{model_id}.tar.xz",
                "sha256": "3a829a2632b7e568f2379377484df58d6411f56ef308ecbaeead470fcf052b61"
            }
        ]

    def download_rom(self, url: str, expected_sha256: str) -> Path:
        """Downloads pre-built ROMs with path traversal validation and atomic saving."""
        filename = Path(url.split('/')[-1]).name  # Safe filename without directory traversal
        if not filename or filename in ('.', '..'):
            filename = "prebuilt_rom.tar.xz"
            
        output_path = self.storage_dir / filename
        tmp_path = output_path.with_suffix(".tmp")
        
        if output_path.exists():
            if self.verify_rom(output_path, expected_sha256):
                logger.info(f"ROM already downloaded and verified: {output_path}")
                return output_path
                
        logger.info(f"Downloading {url} to {output_path}...")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Libre_Core-AutoLoader"})
            with urllib.request.urlopen(req, timeout=30) as response, open(tmp_path, "wb") as out_f:
                shutil.copyfileobj(response, out_f)
                
            if not self.verify_rom(tmp_path, expected_sha256):
                if tmp_path.exists():
                    tmp_path.unlink()
                raise BuildError(f"Downloaded ROM checksum mismatch for {url}")
                
            os.replace(tmp_path, output_path)
            return output_path
        except Exception as e:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            if isinstance(e, BuildError):
                raise
            raise BuildError(f"Failed to download pre-built ROM from {url}: {e}") from e

    def inject_blobs(self, lbmk_dir: Path, archive_path: Path) -> None:
        """Handles blob injection for Libreboot pre-builts."""
        logger.info(f"Injecting blobs into {archive_path} using lbmk...")
        try:
            subprocess.run(
                ["./build", "inject", str(Path(archive_path).resolve())],
                cwd=lbmk_dir,
                check=True,
                timeout=BUILD_TIMEOUT
            )
        except subprocess.TimeoutExpired as e:
            raise BuildError("Blob injection timed out.") from e
        except subprocess.CalledProcessError as e:
            raise BuildError("Failed to inject blobs into pre-built ROM.") from e

    def verify_rom(self, file_path: Path, expected_sha256: str) -> bool:
        """Verifies ROM checksums."""
        file_path = Path(file_path)
        if not file_path.exists():
            return False
            
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
                
        actual_hash = sha256_hash.hexdigest().lower()
        expected = expected_sha256.lower().strip()
        if actual_hash != expected:
            logger.error(f"Checksum mismatch! Expected {expected}, got {actual_hash}")
            return False
            
        return True
