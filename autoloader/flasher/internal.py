"""
Internal flashing operations.
"""
import os
import logging
from typing import Optional

from autoloader.utils import is_admin
from autoloader.exceptions import FlashError, SafetyError
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
        # Try a quick probe
        res = self.runner.probe_chip(ProgrammerType.INTERNAL)
        if not res.success:
            logger.error("Flash is not unlocked or not accessible via internal programmer.")
            return False
        return True

    def flash_internal(self, rom_path: str, layout_region: Optional[str] = "bios") -> FlashResult:
        logger.warning("WARNING: Internal flashing carries significant brick risk. Proceed with caution.")
        if not self.check_prerequisites():
            raise SafetyError("Prerequisites for internal flashing not met (must run as root/Admin).")
        if not self.is_flash_unlocked():
            raise FlashError("Flash is locked. Use external programmer or unlock first (e.g. 1vyrain for xx30).")
        
        return self.runner.write_rom(ProgrammerType.INTERNAL, rom_path, layout_region=layout_region)

    def verify_internal(self, rom_path: str, layout_region: Optional[str] = "bios") -> FlashResult:
        return self.runner.verify_rom(ProgrammerType.INTERNAL, rom_path, layout_region=layout_region)
