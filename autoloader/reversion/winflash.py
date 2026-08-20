import logging
import os
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from autoloader.exceptions import BIOSReversionError, SafetyError
from autoloader.flasher.internal import is_admin

# Backwards compatibility alias
ReversionError = BIOSReversionError

logger = logging.getLogger(__name__)

@dataclass
class WinFlashResult:
    """Result of a WinFlash execution."""
    success: bool
    return_code: int
    output: str
    error_message: Optional[str] = None

class WinFlashRunner:
    """
    Automates WinFlash64 execution for BIOS modifications.
    """
    
    def __init__(self, search_paths: Optional[list[Path]] = None):
        self.search_paths = search_paths or [
            Path("C:\\Program Files\\WinFlash"),
            Path("C:\\Program Files (x86)\\WinFlash"),
            Path("IVprep_working/IVprep-master")
        ]
        self.winflash_exe: Optional[Path] = None

    def locate_winflash(self) -> Path:
        """
        Locates the WinFlash64.exe binary.
        """
        logger.info("Searching for WinFlash64.exe...")
        for path in self.search_paths:
            exe_path = path / "WINUPTP.EXE" # Commonly Lenovo uses WINUPTP or WinFlash64
            if not exe_path.exists():
                exe_path = path / "WinFlash64.exe"
            
            if exe_path.exists() and exe_path.is_file():
                logger.info(f"Found WinFlash64 at: {exe_path}")
                self.winflash_exe = exe_path
                return exe_path
                
        raise ReversionError("WinFlash64.exe could not be found in standard locations.")

    def prepare_flash(self, rom_path: Path) -> list[str]:
        """
        Constructs the command line arguments for BIOS flash.
        """
        if not rom_path.exists():
            raise ReversionError(f"ROM file not found: {rom_path}")
            
        if not self.winflash_exe:
            self.locate_winflash()
            
        # Common WinFlash64 arguments for silent flashing without battery checks etc.
        args = [
            str(self.winflash_exe),
            "/s", # Silent mode
            "/c", # Clear CMOS
            "/mode=3", # Flash mode
            f"/file={rom_path.absolute()}"
        ]
        logger.debug(f"Prepared WinFlash arguments: {' '.join(args)}")
        return args

    def execute_flash(self, rom_path: Path, timeout: int = 300) -> WinFlashResult:
        """
        Executes the flash process and monitors progress.
        """
        logger.info(f"Executing WinFlash for {rom_path}...")
        
        if not is_admin():
            raise SafetyError("Administrator privileges required to execute WinFlash64.")
            
        args = self.prepare_flash(rom_path)
        
        try:
            process = subprocess.run(
                args,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout
            )
            
            return self.check_result(process.returncode, process.stdout, process.stderr)
            
        except subprocess.TimeoutExpired:
            logger.error(f"WinFlash execution timed out after {timeout} seconds.")
            return WinFlashResult(False, -1, "", f"WinFlash timed out after {timeout}s.")
        except subprocess.SubprocessError as e:
            logger.error(f"WinFlash execution failed catastrophically: {e}")
            raise ReversionError(f"Failed to run WinFlash64: {e}")

    def check_result(self, return_code: int, stdout: str, stderr: str) -> WinFlashResult:
        """
        Analyzes the return code and output to determine success or specific errors.
        """
        success = return_code == 0
        error_msg = None
        
        if return_code == 0:
            logger.info("WinFlash64 completed successfully.")
        elif return_code == 1:
            error_msg = "Access Denied. Please ensure the tool is run as Administrator."
            logger.error(error_msg)
        elif return_code == 2:
            error_msg = "Wrong Architecture. Are you running WinFlash64 on a 32-bit OS?"
            logger.error(error_msg)
        else:
            error_msg = f"WinFlash64 failed with return code {return_code}. Check logs for details."
            logger.error(error_msg)
            logger.debug(f"WinFlash stderr: {stderr}")
            
        return WinFlashResult(
            success=success,
            return_code=return_code,
            output=stdout,
            error_message=error_msg
        )
