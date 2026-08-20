import os
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional, Dict

from .configurator import ROMConfiguration
from autoloader.config import BUILD_TIMEOUT, CANOEBOOT_REPO
from autoloader.exceptions import BuildError, SystemCommandError

logger = logging.getLogger(__name__)

class CanoebootBuilder:
    """Canoeboot compilation builder."""

    def __init__(self, workspace_dir: Path, progress_callback: Optional[Callable[[str], None]] = None):
        self.workspace = workspace_dir
        self.source_dir = self.workspace / "cbmk"
        self.progress_callback = progress_callback

    def _report_progress(self, message: str) -> None:
        logger.info(message)
        if self.progress_callback:
            self.progress_callback(message)

    def _run_cmd(self, cmd: list, cwd: Path, env: Optional[Dict[str, str]] = None, timeout: int = BUILD_TIMEOUT) -> str:
        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                env=env or os.environ.copy(),
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout
            )
            return result.stdout
        except subprocess.TimeoutExpired as e:
            logger.error(f"Command timed out after {timeout}s: {' '.join(cmd)}")
            raise SystemCommandError(f"Command timed out after {timeout}s") from e
        except subprocess.CalledProcessError as e:
            logger.error(f"Command failed: {' '.join(cmd)}\nOutput: {e.output}")
            raise SystemCommandError(f"Command failed with exit code {e.returncode}") from e

    def clone_source(self, repo_url: str = CANOEBOOT_REPO) -> None:
        """Clone canoeboot/cbmk repo."""
        if self.source_dir.exists():
            self._report_progress("Canoeboot repository already exists, skipping clone.")
            return

        self._report_progress("Cloning Canoeboot repository...")
        self._run_cmd(["git", "clone", repo_url, "cbmk"], cwd=self.workspace)
        
        if shutil.which("apt-get"):
            self._report_progress("Running build dependencies script...")
            try:
                self._run_cmd(["./build", "dependencies", "ubuntu"], cwd=self.source_dir, timeout=600)
            except Exception as e:
                logger.warning(f"Could not auto-install dependencies: {e}")

    def configure_build(self, config: ROMConfiguration) -> None:
        """Configure build for Canoeboot."""
        self._report_progress(f"Configuring Canoeboot for {config.model_id}...")
        pass

    def inject_blobs(self, config: ROMConfiguration, vendor_files_archive: Optional[Path] = None) -> None:
        """No blob injection needed (blob-free). Present for interface consistency."""
        self._report_progress("Canoeboot is blob-free. Skipping blob injection.")

    def compile(self, config: ROMConfiguration) -> None:
        """Build using cbmk system."""
        self._report_progress(f"Compiling Canoeboot for {config.model_id}...")
        try:
            self._run_cmd(["./build", "boot", "roms", config.model_id.lower()], cwd=self.source_dir)
        except SystemCommandError as e:
            logger.error("Canoeboot compilation failed.")
            raise BuildError("Canoeboot build failed. Check logs for details.") from e

    def get_rom_path(self, config: ROMConfiguration) -> Path:
        """Get the path to the built ROMs."""
        rom_dir = self.source_dir / "bin" / config.model_id.lower()
        if not rom_dir.exists():
            raise BuildError(f"Built ROM directory not found at {rom_dir}")
        return rom_dir
