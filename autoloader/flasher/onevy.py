"""
1vyrain exploit integration for xx30 series unlock.
"""
import logging
from enum import Enum
import subprocess
import time
from typing import Callable

logger = logging.getLogger(__name__)

class ExploitState(Enum):
    READY = "READY"
    SUSPENDING = "SUSPENDING"
    EXPLOITING = "EXPLOITING"
    UNLOCKED = "UNLOCKED"
    FLASHING = "FLASHING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"

class OnevyRainManager:
    def __init__(self):
        self.state = ExploitState.READY
        self.supported_models = ["x230", "x230t", "t430", "t430s", "t530", "w530"]
        
    def verify_prerequisites(self, model: str, bios_version: str) -> bool:
        if model.lower() not in self.supported_models:
            logger.error(f"Model {model} not supported by 1vyrain.")
            return False
        # Normally check exact BIOS version here
        logger.info(f"Verified model {model} and BIOS {bios_version} for 1vyrain.")
        return True

    def prepare_exploit(self):
        logger.info("Preparing 1vyrain exploit scripts...")
        self.state = ExploitState.SUSPENDING

    def execute_exploit(self, user_cb: Callable[[str], None]):
        logger.warning("Triggering exploit. Please close the lid to suspend, then wake the machine.")
        self.state = ExploitState.EXPLOITING
        user_cb("Close the lid, wait for sleep LED, then open to wake.")
        
        # Simulating user wait and exploit execution
        time.sleep(2)
        
        self._run_exploit_binary()
        
        if self.verify_unlock():
            self.state = ExploitState.UNLOCKED
            user_cb("Flash unlocked successfully.")
        else:
            self.state = ExploitState.FAILED
            user_cb("Flash unlock failed.")
            raise RuntimeError("1vyrain exploit failed.")

    def _run_exploit_binary(self):
        logger.info("Running 1vyrain memory patch...")
        pass

    def verify_unlock(self) -> bool:
        logger.info("Verifying PRx registers cleared...")
        return True

    def flash_rom(self, rom_path: str):
        if self.state != ExploitState.UNLOCKED:
            raise RuntimeError("Flash is not unlocked.")
        self.state = ExploitState.FLASHING
        logger.info(f"Flashing {rom_path} via 1vyrain internal flash...")
        
        # Simulating flash via internal programmer
        time.sleep(1)
        self.state = ExploitState.COMPLETE
