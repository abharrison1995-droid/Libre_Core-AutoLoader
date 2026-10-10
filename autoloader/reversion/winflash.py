"""Quarantined WinFlash compatibility adapter.

V1 presents only an official human-run IVprep handoff. It never locates,
constructs arguments for, launches, or treats a WinFlash process as success.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from autoloader.exceptions import BIOSReversionError

ReversionError = BIOSReversionError
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WinFlashResult:
    """Legacy result shape; V1 cannot mark an unverified process successful."""

    success: bool
    return_code: int
    output: str
    error_message: Optional[str] = None


class WinFlashRunner:
    """Legacy API adapter with all physical-flash entrypoints disabled."""

    def __init__(self, search_paths: Optional[list[Path]] = None) -> None:
        self.search_paths = tuple(search_paths or ())
        self.winflash_exe: Optional[Path] = None

    @staticmethod
    def _disabled() -> BIOSReversionError:
        return BIOSReversionError(
            "WINFLASH_DISABLED: V1 does not locate or execute WinFlash; use the pinned "
            "human IVprep handoff if that destructive external action is required"
        )

    def locate_winflash(self) -> Path:
        raise self._disabled()

    def prepare_flash(self, rom_path: Path) -> list[str]:
        del rom_path
        raise self._disabled()

    def execute_flash(self, rom_path: Path, timeout: int = 300) -> WinFlashResult:
        del rom_path, timeout
        raise self._disabled()

    def check_result(self, return_code: int, stdout: str, stderr: str) -> WinFlashResult:
        """Do not treat externally supplied process output as verified flashing."""
        logger.warning("Ignoring untrusted WinFlash process result; V1 flashing is disabled")
        return WinFlashResult(
            success=False,
            return_code=return_code,
            output=stdout,
            error_message="WinFlash output is not accepted as firmware verification in V1",
        )
