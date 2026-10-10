"""
Flashrom/Flashprog wrapper.
"""
import subprocess
import logging
import re
import shutil
from enum import Enum
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List, Tuple
import time

from pathlib import Path

from autoloader.config import FLASH_TIMEOUT

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
    code: str = "OK"

    @property
    def output(self) -> str:
        return self.log_output


@dataclass(frozen=True)
class FlashToolIdentity:
    """Exact executable and version observed from a safe `--version` query."""

    name: str
    executable: str
    version: str
    raw_version: str
    observed_at_utc: str


class ProbeOutputError(ValueError):
    """A flashrom probe did not identify exactly one unambiguous chip."""


_CHIP_LINE = re.compile(
    r'^\s*Found\s+.+?\s+flash chip\s+["\']([^"\']+)["\']\s+'
    r'\(\s*([0-9]+)\s+kB\s*,\s*SPI\s*\)\s+on\s+(.+?)\.?\s*$',
    re.IGNORECASE,
)
_TOOL_VERSION = re.compile(
    r'\b(flashrom|flashprog)\s+v?([0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.-]+)?)\b',
    re.IGNORECASE,
)
_WRITE_FLAGS = frozenset({
    "-w", "--write", "-e", "-erase", "--erase", "--chipset-enable",
    "--wp-disable", "--wp-set", "--wp-clear",
})


def parse_probe_output(output: str) -> tuple[str, int, str]:
    """Parse exactly one standard flashrom/flashprog SPI chip result.

    Caller-supplied chip selectors are deliberately not part of this parser:
    the name and capacity must come from the tool's raw observation.
    """
    matches = []
    for line in output.splitlines():
        if "flash chip" not in line.casefold():
            continue
        match = _CHIP_LINE.fullmatch(line)
        if match is None:
            raise ProbeOutputError("probe output contains a malformed flash-chip result")
        matches.append(match)
    if len(matches) != 1:
        raise ProbeOutputError(
            "probe output must contain exactly one recognized flash-chip result; "
            f"found {len(matches)}"
        )
    match = matches[0]
    name = match.group(1).strip()
    capacity_bytes = int(match.group(2)) * 1024
    reported_programmer = match.group(3).strip().rstrip(".").strip()
    if not name or capacity_bytes <= 0 or not reported_programmer:
        raise ProbeOutputError("probe result has an empty chip, capacity, or programmer field")
    return name, capacity_bytes, reported_programmer

class FlashromRunner:
    def __init__(self):
        self.tool_path = self.detect_tool()
    
    def detect_tool(self) -> str:
        # Prefer flashprog, fallback to flashrom
        return shutil.which("flashprog") or shutil.which("flashrom") or "flashrom"

    def inspect_tool(self) -> Optional[FlashToolIdentity]:
        """Return an exact tool/version record, or None when unavailable/ambiguous."""
        candidates = ("flashprog", "flashrom")
        for candidate in candidates:
            executable = shutil.which(candidate)
            if executable is None:
                continue
            try:
                result = subprocess.run(
                    [executable, "--version"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            raw = f"{result.stdout}\n{result.stderr}".strip()
            match = _TOOL_VERSION.search(raw)
            if result.returncode != 0 or match is None or match.group(1).casefold() != candidate:
                continue
            return FlashToolIdentity(
                name=candidate,
                executable=str(Path(executable).resolve(strict=False)),
                version=match.group(2),
                raw_version=raw,
                observed_at_utc=datetime.now(timezone.utc).isoformat(),
            )
        return None

    def run_command(self, args: List[str], timeout: int = FLASH_TIMEOUT) -> Tuple[bool, str, float]:
        normalized_args = [str(arg).strip().casefold() for arg in args]
        if any(
            arg in _WRITE_FLAGS
            or arg.startswith(("--write", "--erase", "--wp-disable", "--wp-set", "--wp-clear"))
            or arg.startswith(("-w", "-e"))
            for arg in normalized_args
        ):
            return False, "Physical writes and flash-protection changes are disabled in this backend.", 0.0
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
        try:
            name, capacity, _reported_programmer = parse_probe_output(output)
            return name, capacity
        except ProbeOutputError:
            return None, None

    def probe_chip(self, programmer: ProgrammerType, chip_name: Optional[str] = None) -> FlashResult:
        if chip_name is not None:
            return FlashResult(
                False, None, None, 0.0,
                "User-supplied chip names are not observations; probe without a chip override.",
                "PROBE_OVERRIDE_REJECTED",
            )
        args = ["-p", programmer.value]
        success, output, duration = self.run_command(args)
        try:
            parsed_name, chip_size, reported_programmer = parse_probe_output(output)
        except ProbeOutputError as exc:
            return FlashResult(False, None, None, duration, f"{output}\nProbe rejected: {exc}", "PROBE_UNKNOWN")
        expected_programmer = programmer.value.split(":", 1)[0].casefold()
        reported_base = reported_programmer.split(":", 1)[0].casefold()
        if expected_programmer != reported_base:
            return FlashResult(
                False, None, None, duration,
                f"{output}\nProbe programmer does not match the requested interface.",
                "PROBE_PROGRAMMER_MISMATCH",
            )
        return FlashResult(success, parsed_name, chip_size, duration, output, "OK" if success else "PROBE_FAILED")

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
            return FlashResult(False, None, None, 0.0, err_msg, "ROM_NOT_FOUND")

        del programmer, chip_name, layout_region, extra_args
        err_msg = "Physical ROM writes are disabled; no write command was issued."
        logger.warning(err_msg)
        return FlashResult(False, None, None, 0.0, err_msg, "PHYSICAL_WRITES_DISABLED")

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
