import platform
import subprocess
import logging
import re
from dataclasses import dataclass
from typing import Optional, Dict
from pathlib import Path

from autoloader.exceptions import HardwareDetectionError

logger = logging.getLogger(__name__)

@dataclass
class DetectionResult:
    model_id: Optional[str]
    model_name: Optional[str]
    machine_type: Optional[str]
    serial_number: Optional[str]
    confidence: str  # "high", "medium", "low"
    raw_product_name: Optional[str] = None
    raw_product_version: Optional[str] = None
    raw_system_family: Optional[str] = None
    raw_mtm: Optional[str] = None

class ModelDetector:
    """Detects the ThinkPad model and associated metadata."""

    # Map of known Lenovo machine types to our canonical model IDs
    MACHINE_TYPES: Dict[str, str] = {
        "7454": "X200", "7455": "X200", "7457": "X200", "7458": "X200",
        "7459": "X200", "2023": "X200", "2024": "X200",
        "3249": "X201", "3323": "X201", "3357": "X201", "3626": "X201", "3680": "X201",
        "4286": "X220", "4287": "X220", "4289": "X220", "4290": "X220",
        "4291": "X220", "4292": "X220", "4293": "X220",
        "2306": "X230", "2320": "X230", "2322": "X230", "2324": "X230",
        "2325": "X230", "2330": "X230", "2333": "X230",
        "2764": "T400", "2765": "T400", "2766": "T400", "2767": "T400",
        "2768": "T400", "2769": "T400", "2773": "T400",
        "4177": "T420", "4178": "T420", "4180": "T420", "4236": "T420", "4238": "T420",
        "2342": "T430", "2344": "T430", "2347": "T430", "2349": "T430",
        "2350": "T430", "2351": "T430",
        "20AN": "T440p", "20AW": "T440p",
        "20L5": "T480", "20L6": "T480",
        "2055": "T500", "2056": "T500", "2081": "T500", "2082": "T500",
        "2083": "T500", "2084": "T500", "2241": "T500", "2242": "T500",
        "2243": "T500", "2244": "T500", "2245": "T500", "2246": "T500", "2247": "T500"
    }

    def __init__(self):
        self.os_type = platform.system().lower()

    def _extract_raw_mtm(self, *values: str) -> Optional[str]:
        """Retain the full known machine-type/MTM token from raw DMI strings."""
        for value in values:
            if not value:
                continue
            for machine_type in sorted(self.MACHINE_TYPES, key=len, reverse=True):
                match = re.search(
                    rf"(?<![A-Z0-9]){re.escape(machine_type)}[A-Z0-9-]*",
                    value,
                    re.IGNORECASE,
                )
                if match:
                    return match.group(0)
        return None

    def machine_type_to_model(self, machine_type: str) -> Optional[str]:
        """Maps a Lenovo machine type to a known model ID."""
        if not machine_type:
            return None
        # Machine type is usually the first 4 characters
        mt_prefix = machine_type[:4].upper()
        val = self.MACHINE_TYPES.get(mt_prefix)
        return val.lower() if val else None

    def product_name_parser(self, product_name: str) -> Optional[str]:
        """Parses the product name to extract the model ID."""
        if not product_name:
            return None
            
        # Example formats: "ThinkPad X230", "X230", "Lenovo ThinkPad X230 2325B11", "20L5CTO1WW"
        product_name = product_name.strip()
        
        # Check if it matches a known 4-char machine type code (e.g. 2325B11 or 20L5CTO1WW)
        if len(product_name) >= 4:
            model_id = self.machine_type_to_model(product_name)
            if model_id:
                return model_id

        # Look for explicit ThinkPad models like "X230", "T480", "T440p", "X230t", "X230 Tablet"
        match = re.search(r'\b([XTWRLE]\d{2,3}[pst]?)\b', product_name, re.IGNORECASE)
        if match:
            model = match.group(1).lower()
            if "tablet" in product_name.lower() and not model.endswith("t"):
                model += "t"
            return model
            
        return None

    # Alias for API compatibility
    parse_product_name = product_name_parser

    def _read_sysfs(self, path: str) -> str:
        """Reads a file from sysfs on Linux."""
        try:
            p = Path(path)
            if p.exists():
                return p.read_text().strip()
        except Exception as e:
            logger.debug(f"Failed to read sysfs path {path}: {e}")
        return ""

    def _run_subprocess(self, cmd: list, timeout: int = 15) -> str:
        """Runs a command and returns the stripped stdout."""
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

    def detect_linux(self) -> DetectionResult:
        """Detects hardware on Linux using sysfs and dmidecode."""
        product_name = self._read_sysfs("/sys/class/dmi/id/product_name")
        product_version = self._read_sysfs("/sys/class/dmi/id/product_version")
        system_family = self._read_sysfs("/sys/class/dmi/id/product_family")
        sys_vendor = self._read_sysfs("/sys/class/dmi/id/sys_vendor")
        serial_number = self._read_sysfs("/sys/class/dmi/id/product_serial")

        if not product_name and not product_version:
            # Try dmidecode
            dmidecode_out = self._run_subprocess(["dmidecode", "-t", "system"])
            for line in dmidecode_out.splitlines():
                if "Product Name:" in line:
                    product_name = line.split(":", 1)[1].strip()
                elif "Family:" in line:
                    system_family = line.split(":", 1)[1].strip()
                elif "Version:" in line:
                    product_version = line.split(":", 1)[1].strip()
                elif "Serial Number:" in line:
                    serial_number = line.split(":", 1)[1].strip()

        machine_type = None
        if product_version and len(product_version) >= 4 and product_version != "ThinkPad":
            machine_type = product_version[:4]
        elif product_name and len(product_name) >= 4 and "ThinkPad" not in product_name:
            machine_type = product_name[:4]
        
        model_id = self.product_name_parser(product_version) or self.product_name_parser(product_name)

        if not model_id and machine_type:
            model_id = self.machine_type_to_model(machine_type)

        confidence = "low"
        if model_id and machine_type:
            confidence = "high"
        elif model_id:
            confidence = "medium"

        return DetectionResult(
            model_id=model_id,
            model_name=f"ThinkPad {model_id}" if model_id else "Unknown",
            machine_type=machine_type,
            serial_number=serial_number or None,
            confidence=confidence,
            raw_product_name=product_name or None,
            raw_product_version=product_version or None,
            raw_system_family=system_family or None,
            raw_mtm=self._extract_raw_mtm(product_name, product_version),
        )

    def detect_windows(self) -> DetectionResult:
        """Detects hardware on Windows using wmic."""
        # 'wmic computersystem get model,manufacturer'
        wmic_out = self._run_subprocess(["wmic", "computersystem", "get", "model,manufacturer"])
        product_name = ""
        for line in wmic_out.splitlines()[1:]:  # Skip header
            line = line.strip()
            if line:
                # WMIC might return something like "Lenovo   ThinkPad X230 Tablet" or "Lenovo 20L5CTO1WW"
                parts = line.split()
                if len(parts) >= 2:
                    product_name = " ".join(parts[1:])
                elif len(parts) == 1:
                    product_name = parts[0]
                break

        # Keep independent SMBIOS fields when WMIC exposes them; a normalized
        # model inferred from one string is not enough for V1 compatibility.
        product_version = ""
        csproduct_out = self._run_subprocess(["wmic", "csproduct", "get", "name,version", "/format:list"])
        for line in csproduct_out.splitlines():
            if "=" not in line:
                continue
            key, value = (part.strip() for part in line.split("=", 1))
            if key.casefold() == "version" and value:
                product_version = value
            elif key.casefold() == "name" and value and not product_name:
                product_name = value

        family_out = self._run_subprocess(["wmic", "computersystem", "get", "systemfamily", "/format:list"])
        system_family = ""
        for line in family_out.splitlines():
            if "=" not in line:
                continue
            key, value = (part.strip() for part in line.split("=", 1))
            if key.casefold() == "systemfamily" and value:
                system_family = value

        # 'wmic bios get serialnumber'
        wmic_sn = self._run_subprocess(["wmic", "bios", "get", "serialnumber"])
        serial_number = ""
        for line in wmic_sn.splitlines()[1:]:
            line = line.strip()
            if line:
                serial_number = line
                break

        machine_type = None
        if product_name and len(product_name) >= 4:
            # Assuming product name might contain the machine type
            machine_type = product_name[:4]

        model_id = self.product_name_parser(product_name)
        if not model_id and machine_type:
            model_id = self.machine_type_to_model(machine_type)

        confidence = "low"
        if model_id and machine_type:
            confidence = "high"
        elif model_id:
            confidence = "medium"

        return DetectionResult(
            model_id=model_id,
            model_name=f"ThinkPad {model_id}" if model_id else "Unknown",
            machine_type=machine_type,
            serial_number=serial_number or None,
            confidence=confidence,
            raw_product_name=product_name or None,
            raw_product_version=product_version or None,
            raw_system_family=system_family or None,
            raw_mtm=self._extract_raw_mtm(product_name, product_version),
        )

    def detect(self) -> DetectionResult:
        """Main entry point to perform detection based on the OS."""
        try:
            if self.os_type == "linux":
                result = self.detect_linux()
            elif self.os_type == "windows":
                result = self.detect_windows()
            else:
                logger.warning(f"Unsupported OS for detection: {self.os_type}")
                result = DetectionResult(None, None, None, None, "low")
            
            if result.confidence == "low":
                logger.warning("Hardware detection confidence is low, manual selection might be required.")
            return result
        except Exception as e:
            logger.error(f"Error during hardware detection: {e}")
            return DetectionResult(None, None, None, None, "low")

def detect_model() -> DetectionResult:
    """Helper function to create detector and run detection."""
    detector = ModelDetector()
    return detector.detect()
