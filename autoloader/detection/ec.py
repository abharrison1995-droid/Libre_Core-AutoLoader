import platform
import subprocess
import logging
from dataclasses import dataclass, field
from typing import Optional, List
from pathlib import Path
import re

from autoloader.exceptions import HardwareDetectionError
from autoloader.database.models import THINKPAD_MODELS

logger = logging.getLogger(__name__)

@dataclass
class ECInfo:
    version: Optional[str]
    is_modified: bool
    modifications_detected: List[str] = field(default_factory=list)

class ECDetector:
    """Detects the Embedded Controller (EC) firmware version and modifications."""

    def __init__(self):
        self.os_type = platform.system().lower()

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

    def _parse_thinkpad_acpi(self) -> Optional[str]:
        """Parses thinkpad_acpi module info if available."""
        # /sys/devices/platform/thinkpad_acpi/ usually doesn't have raw EC version,
        # but could have indicators. This is a placeholder for actual ACPI parsing if needed.
        acpi_path = Path("/sys/devices/platform/thinkpad_acpi/")
        if acpi_path.exists():
            logger.debug("thinkpad_acpi is loaded.")
        return None

    def detect_linux(self) -> ECInfo:
        """Detects EC version on Linux using sysfs and dmidecode."""
        version = self._read_sysfs("/sys/class/dmi/id/ec_firmware_release")

        if not version:
            dmidecode_out = self._run_subprocess(["dmidecode", "-t", "bios"])
            for line in dmidecode_out.splitlines():
                if "Embedded Controller Firmware Version:" in line:
                    version = line.split(":", 1)[1].strip()

        self._parse_thinkpad_acpi()

        is_modified = False
        mods = []

        # Check for known modified EC version strings
        if version:
            if "mod" in version.lower() or "patched" in version.lower() or "thinkpad-ec" in version.lower():
                is_modified = True
                mods.append("thinkpad-ec")

        return ECInfo(
            version=version,
            is_modified=is_modified,
            modifications_detected=mods
        )

    def detect_windows(self) -> ECInfo:
        """Detects EC version on Windows using wmic (best effort)."""
        wmic_out = self._run_subprocess(["wmic", "bios", "get", "EmbeddedControllerMajorVersion,EmbeddedControllerMinorVersion", "/format:csv"])
        
        version = None
        for line in wmic_out.splitlines():
            line = line.strip()
            if not line or "Node" in line:
                continue
            parts = line.split(",")
            if len(parts) >= 3:
                major = parts[1].strip()
                minor = parts[2].strip()
                if major and minor:
                    version = f"{major}.{minor}"
                break
                
        # Windows detection might lack string indicators of modifications
        return ECInfo(
            version=version,
            is_modified=False,
            modifications_detected=[]
        )

    def detect(self) -> ECInfo:
        """Main entry point to perform EC detection based on OS."""
        try:
            if self.os_type == "linux":
                return self.detect_linux()
            elif self.os_type == "windows":
                return self.detect_windows()
            else:
                logger.warning(f"Unsupported OS for EC detection: {self.os_type}")
                return ECInfo(None, False, [])
        except Exception as e:
            logger.error(f"Error during EC detection: {e}")
            return ECInfo(None, False, [])

    def is_ec_mod_needed(self, model_id: str, desired_mods: List[str]) -> bool:
        """Determines if the EC mod is needed based on the model and desired mods."""
        model_info = THINKPAD_MODELS.get(model_id.lower())
        if not model_info:
            return False
            
        supported_mods = getattr(model_info, "ec_mods_available", [])
        for mod in desired_mods:
            if mod in supported_mods:
                return True
        return False

def detect_ec_version() -> ECInfo:
    """Helper function to create detector and run detection."""
    detector = ECDetector()
    return detector.detect()
