import platform
import subprocess
import logging
from dataclasses import dataclass
from typing import Optional
from pathlib import Path
from enum import Enum
import re

from autoloader.exceptions import HardwareDetectionError
from autoloader.database.models import THINKPAD_MODELS, get_model

logger = logging.getLogger(__name__)

VERSION_REGEX = re.compile(r'(\d+\.\d+)')
BIOS_ID_REGEX = re.compile(r'([A-Z0-9]{8})')
DIGITS_REGEX = re.compile(r'\d+')

class VersionComparison(Enum):
    MATCHES = "MATCHES"
    NEWER = "NEWER"
    OLDER = "OLDER"
    UNKNOWN = "UNKNOWN"

@dataclass
class BIOSInfo:
    version: Optional[str]
    date: Optional[str]
    vendor: Optional[str]
    is_stock_lenovo: bool

def parse_bios_version_from_dmidecode(dmidecode_out: str) -> Optional[str]:
    match = re.search(r"Version:\s*([^\n]+)", dmidecode_out)
    if match:
        return match.group(1).strip()
    return None

class BIOSDetector:
    """Detects the BIOS version and vendor with caching."""

    def __init__(self):
        self.os_type = platform.system().lower()
        self._cached_info: Optional[BIOSInfo] = None

    def _read_sysfs(self, path: str) -> str:
        try:
            p = Path(path)
            if p.exists():
                return p.read_text().strip()
        except Exception as e:
            logger.debug(f"Failed to read sysfs path {path}: {e}")
        return ""

    def _run_subprocess(self, cmd: list, timeout: int = 15) -> str:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout)
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            logger.warning(f"Command {cmd} timed out after {timeout}s")
        except subprocess.CalledProcessError as e:
            logger.debug(f"Command {cmd} failed with exit code {e.returncode}")
        except FileNotFoundError:
            logger.debug(f"Command {cmd} not found")
        except Exception as e:
            logger.debug(f"Unexpected error running {cmd}: {e}")
        return ""

    def _parse_bios_version(self, version_string: str) -> Optional[str]:
        """Extract typical ThinkPad BIOS version (e.g. G1ET73WW or 1.43)."""
        if not version_string:
            return None

        # Check for numeric version in parentheses like (2.77)
        match = VERSION_REGEX.search(version_string)
        if match:
            return match.group(1)

        # Fallback to standard alphanumeric BIOS ID string
        return version_string.strip()

    def detect_linux(self) -> BIOSInfo:
        """Detects BIOS version and vendor on Linux using sysfs or dmidecode."""
        bios_version = self._read_sysfs("/sys/class/dmi/id/bios_version")
        bios_vendor = self._read_sysfs("/sys/class/dmi/id/bios_vendor")
        bios_date = self._read_sysfs("/sys/class/dmi/id/bios_date")

        if not bios_version:
            # Fallback to dmidecode
            dmidecode_out = self._run_subprocess(["dmidecode", "-s", "bios-version"])
            if dmidecode_out:
                bios_version = dmidecode_out
            dmidecode_vendor = self._run_subprocess(["dmidecode", "-s", "bios-vendor"])
            if dmidecode_vendor:
                bios_vendor = dmidecode_vendor
            dmidecode_date = self._run_subprocess(["dmidecode", "-s", "bios-release-date"])
            if dmidecode_date:
                bios_date = dmidecode_date

        parsed_version = self._parse_bios_version(bios_version)
        is_lenovo = "lenovo" in bios_vendor.lower() or "ibm" in bios_vendor.lower()

        return BIOSInfo(
            version=parsed_version,
            date=bios_date or None,
            vendor=bios_vendor or None,
            is_stock_lenovo=is_lenovo
        )

    def detect_windows(self) -> BIOSInfo:
        """Detects BIOS version and vendor on Windows using wmic."""
        version = None
        vendor = None
        date = None
        
        # Try /format:list first (Key=Value format is immune to column order)
        out_list = self._run_subprocess(["wmic", "bios", "get", "SMBIOSBIOSVersion,Manufacturer,ReleaseDate", "/format:list"])
        for line in out_list.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if k == "SMBIOSBIOSVersion" and v:
                    version = v
                elif k == "Manufacturer" and v:
                    vendor = v
                elif k == "ReleaseDate" and v:
                    date = v

        # Fallback to /format:csv if list format was empty
        if not version:
            out_csv = self._run_subprocess(["wmic", "bios", "get", "Manufacturer,ReleaseDate,SMBIOSBIOSVersion", "/format:csv"])
            for line in out_csv.splitlines():
                line = line.strip()
                if not line or line.startswith("Node"):
                    continue
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 4:
                    vendor = vendor or parts[1]
                    date = date or parts[2]
                    version = version or parts[3]
                    break

        parsed_version = self._parse_bios_version(version) if version else None
        is_lenovo = False
        if vendor:
            is_lenovo = "lenovo" in vendor.lower() or "ibm" in vendor.lower()

        return BIOSInfo(
            version=parsed_version,
            date=date,
            vendor=vendor,
            is_stock_lenovo=is_lenovo
        )

    def detect(self, force_refresh: bool = False) -> BIOSInfo:
        """Runs the platform-specific detection."""
        if self._cached_info is not None and not force_refresh:
            return self._cached_info

        try:
            if self.os_type == "linux":
                info = self.detect_linux()
            elif self.os_type == "windows":
                info = self.detect_windows()
            else:
                logger.warning(f"Unsupported OS for BIOS detection: {self.os_type}")
                info = BIOSInfo(None, None, None, False)
            self._cached_info = info
            return info
        except Exception as e:
            logger.error(f"Error during BIOS detection: {e}")
            return BIOSInfo(None, None, None, False)

    def compare_version(self, current: Optional[str], required: Optional[str]) -> VersionComparison:
        """
        Compares two BIOS versions safely.
        Fails closed: returns VersionComparison.UNKNOWN if either version is missing or cannot be compared.
        """
        if not current or not required:
            return VersionComparison.UNKNOWN

        cur_raw = str(current).strip()
        req_raw = str(required).strip()

        if cur_raw.lower() == req_raw.lower():
            return VersionComparison.MATCHES

        try:
            cur_match = VERSION_REGEX.search(cur_raw)
            req_match = VERSION_REGEX.search(req_raw)

            # Both must be decimal versions or contain decimal version tokens
            if not cur_match or not req_match:
                return VersionComparison.UNKNOWN

            cur_str = cur_match.group(1)
            req_str = req_match.group(1)

            cur_parts = [int(x) for x in DIGITS_REGEX.findall(cur_str)]
            req_parts = [int(x) for x in DIGITS_REGEX.findall(req_str)]
            
            if not cur_parts or not req_parts:
                return VersionComparison.UNKNOWN

            # Normalize 1-part minor versions (e.g. 2.8 vs 2.75 -> [2, 80] vs [2, 75])
            if len(cur_parts) == 2 and len(cur_str.split('.')[-1]) == 1:
                cur_parts[1] = cur_parts[1] * 10
            if len(req_parts) == 2 and len(req_str.split('.')[-1]) == 1:
                req_parts[1] = req_parts[1] * 10

            if cur_parts == req_parts:
                return VersionComparison.MATCHES
            elif cur_parts > req_parts:
                return VersionComparison.NEWER
            else:
                return VersionComparison.OLDER
        except Exception:
            logger.warning(f"Could not compare BIOS versions {current} and {required}")
            return VersionComparison.UNKNOWN

    def get_required_version(self, model_id: str) -> Optional[str]:
        """Gets the required BIOS version for a model (e.g. for 1vyrain)."""
        try:
            model_info = get_model(model_id)
            return model_info.required_bios_version
        except KeyError:
            return None

    def check_1vyrain_compatible(self, model_id: str, bios_version: Optional[str]) -> bool:
        """Checks if the given BIOS version on the model is 1vyrain compatible (exact match required)."""
        if not bios_version:
            return False
        required = self.get_required_version(model_id)
        if not required:
            return False
        comparison = self.compare_version(bios_version, required)
        return comparison == VersionComparison.MATCHES

    def needs_downgrade(self, model_id: str, current_version: Optional[str]) -> bool:
        """Checks if a BIOS downgrade is required (fails closed on UNKNOWN or NEWER)."""
        required = self.get_required_version(model_id)
        if not required:
            return False
        if not current_version:
            return True
        comparison = self.compare_version(current_version, required)
        return comparison in (VersionComparison.NEWER, VersionComparison.UNKNOWN)

_GLOBAL_BIOS_DETECTOR = BIOSDetector()

def detect_bios_version(force_refresh: bool = False) -> BIOSInfo:
    """Helper function to run detection using global cached detector."""
    return _GLOBAL_BIOS_DETECTOR.detect(force_refresh=force_refresh)
