"""
Internal flashing operations.
"""
import os
import logging
from typing import Optional

from autoloader.utils import is_admin
from .flashrom import FlashromRunner, ProgrammerType, FlashResult

logger = logging.getLogger(__name__)


class InternalFlasher:
    def __init__(self, runner: Optional[FlashromRunner] = None) -> None:
        self.runner = runner or FlashromRunner()

    def check_prerequisites(self) -> bool:
        # Check if running as root or Windows Administrator
        if not is_admin():
            logger.error("Must run as root / Administrator for internal flashing.")
            return False
        
        # Check iomem=relaxed on Linux
        if os.name != 'nt':
            try:
                with open("/proc/cmdline", "r", encoding="utf-8") as f:
                    cmdline = f.read()
                    if "iomem=relaxed" not in cmdline:
                        logger.warning("iomem=relaxed not found in kernel parameters.")
            except (FileNotFoundError, PermissionError) as e:
                logger.debug("Could not inspect /proc/cmdline: %s", e)
        
        return True

    def is_flash_unlocked(self) -> bool:
        logger.warning("A successful chip probe does not prove that firmware writes are unlocked.")
        return False

    def flash_internal(self, rom_path: str, layout_region: Optional[str] = "bios") -> FlashResult:
        del rom_path, layout_region
        logger.warning("Internal firmware writes are disabled; no probe or write was attempted.")
        return FlashResult(
            False,
            None,
            None,
            0.0,
            "Internal firmware writes are unavailable until a reviewed, evidence-bound writer exists; no operation was attempted.",
            "PHYSICAL_WRITES_DISABLED",
        )

    def verify_internal(self, rom_path: str, layout_region: Optional[str] = "bios") -> FlashResult:
        return self.runner.verify_rom(ProgrammerType.INTERNAL, rom_path, layout_region=layout_region)
