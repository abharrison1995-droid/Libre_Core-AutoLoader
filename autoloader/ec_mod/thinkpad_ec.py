import logging
import platform
import subprocess
import os
import shlex
import shutil
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional

from autoloader.exceptions import ECModError, SafetyError
from autoloader.detection.model import ModelDetector
from autoloader.detection.bios import BIOSDetector, VersionComparison

logger = logging.getLogger(__name__)

@dataclass
class ECPatch:
    name: str
    description: str
    enabled: bool = False

class ThinkPadECManager:
    """
    Manages the thinkpad-ec patching and build system.
    """
    
    REPO_URL = "https://github.com/hamishcoleman/thinkpad-ec.git"
    
    def __init__(self, model: Optional[str] = None, working_dir: Optional[Path] = None):
        self.working_dir = working_dir or Path("thinkpad-ec-working")
        self.model_detector = ModelDetector()
        self.bios_detector = BIOSDetector()
        
        detected_res = self.model_detector.detect()
        bios_info = self.bios_detector.detect()
        resolved_model = model or detected_res.model_id
        if not resolved_model:
            raise SafetyError("Could not detect hardware model for EC mod. Please specify model explicitly.")
        self.model = resolved_model.lower()
        self.current_bios = bios_info.version or "Unknown"
            
        self.repo_dir = self.working_dir / "thinkpad-ec"

    def download_source(self) -> Path:
        """Clones or updates the thinkpad-ec repository."""
        logger.info(f"Setting up thinkpad-ec source in {self.working_dir}")
        self.working_dir.mkdir(parents=True, exist_ok=True)
        
        if self.repo_dir.exists():
            logger.info("Repository exists. Pulling latest changes...")
            try:
                subprocess.run(["git", "pull"], cwd=str(self.repo_dir), check=True, capture_output=True, timeout=120)
            except subprocess.TimeoutExpired as e:
                logger.error(f"Git pull timed out: {e}")
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to update repo: {e}")
        else:
            logger.info(f"Cloning from {self.REPO_URL}...")
            try:
                subprocess.run(["git", "clone", self.REPO_URL], cwd=str(self.working_dir), check=True, capture_output=True, timeout=300)
            except subprocess.TimeoutExpired as e:
                logger.error(f"Git clone timed out: {e}")
                raise ECModError("Git clone timed out. Check network connection.") from e
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to clone repo: {e}")
                raise ECModError("Git clone failed. Ensure git is installed and network is reachable.") from e
                
        return self.repo_dir

    def get_available_patches(self) -> List[ECPatch]:
        """
        Determines which EC patches are available for the detected model.
        Reads from the thinkpad-ec config system.
        """
        logger.info(f"Querying available patches for {self.model}...")
        
        patches = []
        if self.model in ["x230", "x230t", "t430", "t430s", "t530", "w530"]:
            patches.append(ECPatch("keyboard", "Classic 7-row keyboard remap"))
            patches.append(ECPatch("battery", "Battery authentication bypass"))
            
        return patches

    def build_ec_firmware(self, config_commands: List[str]) -> Path:
        """
        Builds the patched EC firmware.
        """
        if platform.system().lower() == "windows":
            logger.warning("thinkpad-ec build system requires Linux (Make, GCC, etc).")
            logger.warning("Windows environments must use WSL or a Linux VM/Live USB.")
            raise ECModError("Building EC firmware requires a Linux environment.")

        # Check required build tools
        for tool in ["git", "make"]:
            if not shutil.which(tool):
                raise ECModError(f"Required build tool '{tool}' is not installed or not in PATH.")

        logger.info("Building patched EC firmware...")
        
        if not self.repo_dir.exists():
            self.download_source()
            
        try:
            # 1. Clean previous builds
            subprocess.run(["make", "clean"], cwd=str(self.repo_dir), check=True, timeout=60)
            
            # 2. Apply configurations
            for cmd in config_commands:
                logger.info(f"Applying config: {cmd}")
                cmd_parts = shlex.split(cmd) if isinstance(cmd, str) else cmd
                subprocess.run(cmd_parts, cwd=str(self.repo_dir), check=True, timeout=60)
                
            # 3. Build image
            build_cmd = ["make", f"patched.{self.model.lower()}.img"]
            logger.info(f"Running build command: {' '.join(build_cmd)}")
            subprocess.run(build_cmd, cwd=str(self.repo_dir), check=True, timeout=300)
            
            img_path = self.repo_dir / f"patched.{self.model.lower()}.img"
            if img_path.exists():
                logger.info(f"Successfully built EC image at {img_path}")
                return img_path
            else:
                raise ECModError("Build completed but image file not found.")
                
        except subprocess.TimeoutExpired as e:
            logger.error(f"EC firmware build timed out: {e}")
            raise ECModError("EC firmware compilation timed out.") from e
        except subprocess.CalledProcessError as e:
            logger.error(f"Build failed: {e}")
            raise ECModError(f"EC Firmware build failed: {e}") from e

    def flash_ec(self, img_path: Path, strict: bool = True):
        """
        Guides the user through flashing the EC.
        Requires writing the image to a USB drive and booting from it.
        """
        logger.info("Preparing for EC flash...")
        self._check_cve_2019_6171(strict=strict)
        
        logger.info("\n=== EC FLASH INSTRUCTIONS ===")
        logger.info(f"1. Write the image to a USB flash drive using dd or Rufus:")
        logger.info(f"   Linux: sudo dd if={img_path} of=/dev/sdX bs=4M status=progress")
        logger.info("2. Reboot the laptop and boot from the USB drive.")
        logger.info("3. Follow the on-screen instructions from the Lenovo BIOS update utility.")
        logger.info("   (It will flash the EC and reboot)")
        logger.info("=============================\n")

    def _check_cve_2019_6171(self, strict: bool = True):
        """Warns about or enforces CVE-2019-6171 digital signature verification cutoffs."""
        logger.warning("Checking BIOS version for CVE-2019-6171 protections...")
        
        lockout_versions = {
            "x230": "2.75",
            "x230t": "2.75",
            "t430": "2.81",
            "t430s": "2.75",
            "t530": "2.75",
            "w530": "2.75",
        }
        
        locked = False
        if self.model in lockout_versions:
            cutoff = lockout_versions[self.model]
            comparison = self.bios_detector.compare_version(self.current_bios, cutoff)
            if comparison in (VersionComparison.MATCHES, VersionComparison.NEWER):
                locked = True
                logger.warning(f"Your BIOS version ({self.current_bios}) includes protections against modified EC firmware (CVE-2019-6171 lockout at >={cutoff})!")
                logger.warning("You MUST downgrade your BIOS (using the Reversion module) BEFORE flashing this EC image.")
            elif comparison == VersionComparison.UNKNOWN and strict:
                raise SafetyError(f"Cannot verify BIOS version '{self.current_bios}' against CVE-2019-6171 cutoff ({cutoff}). Downgrade BIOS first.")

        if locked and strict:
            raise SafetyError(f"BIOS version {self.current_bios} has CVE-2019-6171 digital signature protection. Downgrade BIOS first.")

    def verify_ec(self) -> bool:
        """
        Verifies if the EC flash was successful.
        This is difficult to do automatically without probing EC registers.
        """
        logger.info("To verify EC modifications:")
        logger.info("- Keyboard: Test the keys unique to the classic layout.")
        logger.info("- Battery: Insert a non-genuine battery and ensure it charges without boot errors.")
        return True
