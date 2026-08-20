"""
Flashrom/Flashprog wrapper.
"""
import subprocess
import logging
import re
from enum import Enum
from dataclasses import dataclass
from typing import Optional, List, Tuple
import time

from pathlib import Path

from autoloader.config import FLASH_TIMEOUT
from autoloader.utils import is_admin

logger = logging.getLogger(__name__)

class ProgrammerType(Enum):
    INTERNAL = "internal"
    CH341A = "ch341a_spi"
    RASPBERRY_PI = "linux_spi:dev=/dev/spidev0.0,spispeed=1000"
    BUS_PIRATE = "buspirate_spi:dev=/dev/ttyUSB0"

@dataclass
class FlashResult:
    success: bool
    chip_name: Optional[str]
    chip_size: Optional[int]
    duration: float
    log_output: str

    @property
    def output(self) -> str:
        return self.log_output

class FlashromRunner:
    def __init__(self):
        self.tool_path = self.detect_tool()
    
    def detect_tool(self) -> str:
        # Prefer flashprog, fallback to flashrom
        try:
            subprocess.run(["flashprog", "--version"], capture_output=True, check=True, timeout=10)
            return "flashprog"
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            try:
                subprocess.run(["flashrom", "--version"], capture_output=True, check=True, timeout=10)
                return "flashrom"
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
                logger.warning("Neither flashprog nor flashrom found in PATH, using 'flashrom' as fallback.")
                return "flashrom"

    def run_command(self, args: List[str], timeout: int = FLASH_TIMEOUT) -> Tuple[bool, str, float]:
        start = time.time()
        cmd = [self.tool_path] + args
        logger.info(f"Running command: {' '.join(cmd)}")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)
            duration = time.time() - start
            success = result.returncode == 0
            log_output = result.stdout + "\n" + result.stderr
            return success, log_output, duration
        except subprocess.TimeoutExpired:
            duration = time.time() - start
            err_msg = f"Flashrom command timed out after {timeout}s: {' '.join(cmd)}"
            logger.error(err_msg)
            return False, err_msg, duration
        except Exception as e:
            logger.error(f"Failed to run command: {e}")
            return False, str(e), time.time() - start

    def parse_chip_info(self, output: str) -> Tuple[Optional[str], Optional[int]]:
        chip_name = None
        chip_size = None
        for line in output.splitlines():
            if "Found" in line and "flash chip" in line:
                match = re.search(r'Found.*?flash chip "(.*?)" \((.*?) kB,', line)
                if match:
                    chip_name = match.group(1)
                    try:
                        chip_size = int(match.group(2)) * 1024
                    except (ValueError, TypeError):
                        chip_size = None
                    break
        return chip_name, chip_size

    def probe_chip(self, programmer: ProgrammerType, chip_name: Optional[str] = None) -> FlashResult:
        args = ["-p", programmer.value]
        if chip_name:
            args.extend(["-c", chip_name])
        success, output, duration = self.run_command(args)
        parsed_name, chip_size = self.parse_chip_info(output)
        return FlashResult(success, chip_name or parsed_name, chip_size, duration, output)

    def read_rom(
        self,
        programmer: ProgrammerType,
        output_path: str,
        chip_name: Optional[str] = None,
        layout_region: Optional[str] = None,
        extra_args: Optional[List[str]] = None
    ) -> FlashResult:
        target_path = Path(output_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        args = ["-p", programmer.value]
        if chip_name:
            args.extend(["-c", chip_name])
        if layout_region:
            args.extend(["--ifd", "-i", layout_region])
        if extra_args:
            args.extend(extra_args)
        args.extend(["-r", str(target_path)])
        success, output, duration = self.run_command(args)
        parsed_name, chip_size = self.parse_chip_info(output)
        return FlashResult(success, chip_name or parsed_name, chip_size, duration, output)

    def write_rom(
        self,
        programmer: ProgrammerType,
        rom_path: str,
        chip_name: Optional[str] = None,
        layout_region: Optional[str] = None,
        extra_args: Optional[List[str]] = None
    ) -> FlashResult:
        source_path = Path(rom_path)
        if not source_path.is_file():
            err_msg = f"ROM file not found for writing: {rom_path}"
            logger.error(err_msg)
            return FlashResult(False, None, None, 0.0, err_msg)

        if programmer == ProgrammerType.INTERNAL and not is_admin():
            err_msg = "Root / Administrator privileges required for internal flashing."
            logger.error(err_msg)
            return FlashResult(False, None, None, 0.0, err_msg)
        
        logger.warning(f"DANGER: Writing {rom_path} to {programmer.value}")
        args = ["-p", programmer.value]
        if chip_name:
            args.extend(["-c", chip_name])
        if layout_region:
            args.extend(["--ifd", "-i", layout_region])
        if extra_args:
            args.extend(extra_args)
        args.extend(["-w", str(source_path)])
        success, output, duration = self.run_command(args)
        parsed_name, chip_size = self.parse_chip_info(output)
        return FlashResult(success, chip_name or parsed_name, chip_size, duration, output)

    def verify_rom(
        self,
        programmer: ProgrammerType,
        rom_path: str,
        chip_name: Optional[str] = None,
        layout_region: Optional[str] = None,
        extra_args: Optional[List[str]] = None
    ) -> FlashResult:
        source_path = Path(rom_path)
        if not source_path.is_file():
            err_msg = f"ROM file not found for verification: {rom_path}"
            logger.error(err_msg)
            return FlashResult(False, None, None, 0.0, err_msg)
        
        args = ["-p", programmer.value]
        if chip_name:
            args.extend(["-c", chip_name])
        if layout_region:
            args.extend(["--ifd", "-i", layout_region])
        if extra_args:
            args.extend(extra_args)
        args.extend(["-v", str(source_path)])
        success, output, duration = self.run_command(args)
        parsed_name, chip_size = self.parse_chip_info(output)
        return FlashResult(success, chip_name or parsed_name, chip_size, duration, output)
    
    def get_chip_info(self, programmer: ProgrammerType) -> Tuple[Optional[str], Optional[int]]:
        res = self.probe_chip(programmer)
        return res.chip_name, res.chip_size
