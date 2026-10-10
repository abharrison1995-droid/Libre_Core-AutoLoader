"""Quarantined compatibility surface for the former fabricated ROM catalog.

V1 does not use an unpinned prebuilt catalog or arbitrary downloads. Firmware
artifacts must be produced from an allowlisted pinned recipe by
``ArtifactEngine``. The checksum helper remains as a pure local-file check.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Dict, List

from autoloader.exceptions import ROMBuildUnavailableError

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class PrebuiltManager:
    """Fail-closed adapter; it never downloads, installs, or modifies ROMs."""

    def __init__(self, storage_dir: Path):
        # Retain the constructor/API but do not create a writable application
        # directory as a side effect.
        self.storage_dir = Path(storage_dir)

    def list_available(self, model_id: str, firmware_base: str) -> List[Dict[str, str]]:
        raise ROMBuildUnavailableError(
            "The fabricated moving-release prebuilt catalog was removed; select a pinned V1 artifact recipe."
        )

    def download_rom(self, url: str, expected_sha256: str) -> Path:
        raise ROMBuildUnavailableError(
            "Arbitrary ROM downloads are disabled; use a pinned V1 artifact recipe."
        )

    def inject_blobs(self, lbmk_dir: Path, archive_path: Path) -> None:
        raise ROMBuildUnavailableError(
            "Prebuilt archive blob injection is disabled; use the pinned upstream build recipe."
        )

    @staticmethod
    def verify_rom(file_path: Path, expected_sha256: str) -> bool:
        """Compare one local file with a well-formed expected SHA-256 digest."""
        path = Path(file_path)
        if not _SHA256_RE.fullmatch(expected_sha256):
            return False
        if not path.is_file():
            return False
        digest = hashlib.sha256()
        with path.open("rb") as file_obj:
            for block in iter(lambda: file_obj.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest().lower() == expected_sha256.lower()
