import hashlib
import logging
import os
import platform
import shutil
import subprocess
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict
import psutil

from autoloader.exceptions import BIOSReversionError, SafetyError, LowBatteryError
from autoloader.detection.model import ModelDetector
from autoloader.detection.bios import BIOSDetector
from autoloader.database.models import get_model
from autoloader.utils import is_admin

logger = logging.getLogger(__name__)

# Known upstream IVprep master archive SHA256 (or configurable override)
IVPREP_DEFAULT_SHA256: Optional[str] = "c24f64c676d4986161726a457492c19a9f20e4b7b3b9b469315d18d4529ee1d3"

@dataclass
class IVprepConfig:
    """Configuration for IVprep downgrade."""
    model: str
    target_bios_version: str
    download_url: str = "https://github.com/n4ru/IVprep/archive/refs/heads/master.zip"
    working_dir: Path = Path("IVprep_working")
    expected_sha256: Optional[str] = None

class IVPrepManager:
    """
    Manages the IVprep downgrade process for xx30 series ThinkPads.
    """
    
    TARGET_VERSIONS: Dict[str, str] = {
        "x230": "2.60",
        "x230t": "2.58",
        "t430": "2.64",
        "t430s": "2.59",
        "t530": "2.60",
        "w530": "2.58",
    }

    def __init__(self, model: Optional[str] = None, target_version: Optional[str] = None, expected_sha256: Optional[str] = None):
        self.model_detector = ModelDetector()
        self.bios_detector = BIOSDetector()
        
        detected_res = self.model_detector.detect()
        self.model = (model or detected_res.model_id or "x230").lower()
        
        target = target_version or self.TARGET_VERSIONS.get(self.model, "2.60")
        self.config = IVprepConfig(
            model=self.model,
            target_bios_version=target,
            expected_sha256=expected_sha256 or IVPREP_DEFAULT_SHA256,
        )
        
    def download(self) -> Path:
        """
        Downloads IVprep from GitHub, verifies SHA256 integrity, and safely extracts it with Zip Slip protection.
        """
        logger.info(f"Downloading IVprep from {self.config.download_url}...")
        self.config.working_dir.mkdir(parents=True, exist_ok=True)
        zip_path = self.config.working_dir / "ivprep.zip"
        
        try:
            # Download with socket timeout and stream hashing
            req = urllib.request.Request(self.config.download_url, headers={"User-Agent": "Libre_Core-AutoLoader"})
            hasher = hashlib.sha256()
            with urllib.request.urlopen(req, timeout=30) as response, open(zip_path, "wb") as out_f:
                while True:
                    chunk = response.read(8192)
                    if not chunk:
                        break
                    hasher.update(chunk)
                    out_f.write(chunk)

            # SHA256 integrity verification if expected hash configured
            computed_hash = hasher.hexdigest()
            logger.debug(f"IVprep archive downloaded. SHA256: {computed_hash}")
            if self.config.expected_sha256:
                if computed_hash.lower() != self.config.expected_sha256.lower():
                    raise BIOSReversionError(
                        f"IVprep archive SHA256 mismatch! Expected {self.config.expected_sha256}, got {computed_hash}"
                    )

            # Safe zip extraction (prevent Zip Slip / path traversal)
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                dest_dir = self.config.working_dir.resolve()
                for member in zip_ref.infolist():
                    member_path = (dest_dir / member.filename).resolve()
                    if not member_path.is_relative_to(dest_dir):
                        raise BIOSReversionError(f"Malicious path traversal detected in zip archive: {member.filename}")
                zip_ref.extractall(self.config.working_dir)

            logger.info("Successfully downloaded and extracted IVprep.")
            return self.config.working_dir / "IVprep-master"
        except Exception as e:
            logger.error(f"Failed to download or extract IVprep: {e}")
            if zip_path.exists():
                try:
                    zip_path.unlink()
                except Exception:
                    pass
            raise BIOSReversionError(f"Failed to download IVprep: {e}")

    def verify_prerequisites(self) -> bool:
        """
        Verifies that all prerequisites for IVprep downgrade are met.
        """
        logger.info("Verifying prerequisites for IVprep downgrade...")
        
        if platform.system().lower() != "windows":
            logger.warning("IVprep requires Windows to automate the downgrade.")
            logger.warning("Please boot into Windows or use alternative hardware flashing methods.")
            raise BIOSReversionError("IVprep automation requires Windows.")

        if not is_admin():
            logger.error("Must run as Windows Administrator for IVprep downgrade.")
            raise SafetyError("Administrator privileges required on Windows for BIOS downgrade.")

        try:
            db_model = get_model(self.model)
        except Exception:
            db_model = None

        if not db_model or not db_model.supports_1vyrain:
            logger.error(f"Model {self.model} is not supported by IVprep (xx30 series only).")
            raise SafetyError(f"Model {self.model} is not an xx30 series ThinkPad.")

        # Power checks via psutil
        battery = psutil.sensors_battery()
        if battery:
            if not battery.power_plugged and battery.percent < 30:
                logger.error(f"Battery level too low ({battery.percent}%) without AC power. Connect AC adapter.")
                raise LowBatteryError("AC adapter must be connected or battery level at least 30%.")

        logger.warning("IMPORTANT: Ensure all BIOS passwords (Supervisor, Power-on) are removed before proceeding.")
        logger.warning("IMPORTANT: If you plan to apply EC mods (battery/keyboard), they MUST be done BEFORE this step.")
        
        logger.info("All prerequisites verified successfully.")
        return True

    def execute_downgrade(self) -> bool:
        """
        Executes the downgrade script on Windows using cmd.exe.
        """
        self.verify_prerequisites()
        
        extracted_dir = self.download()
        downgrade_bat = extracted_dir / "downgrade.bat"
        
        if not downgrade_bat.exists():
            raise BIOSReversionError(f"downgrade.bat not found in {extracted_dir}")
            
        logger.info("Executing downgrade.bat with WinFlash64...")
        try:
            cmd = ["cmd.exe", "/c", "downgrade.bat"]
            result = subprocess.run(
                cmd, 
                cwd=str(extracted_dir),
                capture_output=True,
                text=True,
                timeout=300,
                check=True
            )
            logger.info("Downgrade script executed successfully.")
            logger.debug(f"Downgrade output: {result.stdout}")
            return True
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr or e.stdout or str(e)
            logger.error(f"Failed to execute downgrade.bat: {err_msg}")
            raise BIOSReversionError(f"Downgrade execution failed: {err_msg}")
        except subprocess.TimeoutExpired:
            raise BIOSReversionError("Downgrade process timed out after 300 seconds.")
            
    def verify_downgrade_success(self) -> bool:
        """
        Verifies if the downgrade was successful by checking the current BIOS version.
        """
        logger.info("Verifying downgrade success...")
        bios_info = self.bios_detector.detect(force_refresh=True)
        current_version = bios_info.version or "Unknown"
        
        from autoloader.detection.bios import VersionComparison
        if self.bios_detector.compare_version(current_version, self.config.target_bios_version) == VersionComparison.MATCHES:
            logger.info(f"Success! Current BIOS version is {current_version}.")
            return True
        else:
            logger.warning(f"Expected {self.config.target_bios_version}, current detected is {current_version} (reboot may be required).")
            return False
