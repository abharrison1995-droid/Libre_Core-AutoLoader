import os
import re
import shutil
import logging
import subprocess
from pathlib import Path
from typing import Callable, Optional, Dict, Any

from .configurator import ROMConfiguration
from .blob_extractor import BlobSet
from autoloader.config import BUILD_TIMEOUT, COREBOOT_REPO
from autoloader.exceptions import BuildError, SystemCommandError
from autoloader.database.models import PayloadType, get_model

logger = logging.getLogger(__name__)

class CorebootBuilder:
    """Coreboot compilation builder."""
    
    def __init__(self, workspace_dir: Path, progress_callback: Optional[Callable[[str], None]] = None):
        self.workspace = workspace_dir
        self.source_dir = self.workspace / "coreboot"
        self.progress_callback = progress_callback
        
    def _report_progress(self, message: str) -> None:
        logger.info(message)
        if self.progress_callback:
            self.progress_callback(message)

    def _run_cmd(self, cmd: list, cwd: Path, env: Optional[Dict[str, str]] = None, timeout: int = BUILD_TIMEOUT) -> str:
        """Run a subprocess command and handle errors with timeout."""
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

    def clone_source(self, repo_url: str = COREBOOT_REPO, branch: str = "master") -> None:
        """Clone coreboot repo (with --depth 1 for speed)."""
        if self.source_dir.exists():
            self._report_progress("Coreboot repository already exists, skipping clone.")
            return

        self._report_progress("Cloning coreboot repository...")
        self._run_cmd(["git", "clone", "--depth", "1", "-b", branch, repo_url, str(self.source_dir)], cwd=self.workspace)

    def build_toolchain(self) -> None:
        """Build the cross-compiler toolchain."""
        self._report_progress("Building cross-compiler toolchain (this may take a while)...")
        nproc = str(os.cpu_count() or 1)
        self._run_cmd(["make", "crossgcc-i386", f"CPUS={nproc}"], cwd=self.source_dir)

    def generate_config(self, config: ROMConfiguration, blob_set: Optional[BlobSet] = None) -> None:
        """Generate a .config file programmatically from ROMConfiguration."""
        self._report_progress(f"Generating .config for {config.model_id}...")
        
        # Start with defconfig or default options
        self._run_cmd(["make", "defconfig"], cwd=self.source_dir)
        
        config_path = self.source_dir / ".config"
        
        # Basic mapping of configurations with symbol whitelisting
        sanitized_model = re.sub(r'[^A-Z0-9_]', '', config.model_id.upper())
        config_lines = [
            "CONFIG_VENDOR_LENOVO=y\n",
            f"CONFIG_BOARD_LENOVO_{sanitized_model}=y\n"
        ]

        if config.payload == PayloadType.SEABIOS:
            config_lines.append("CONFIG_PAYLOAD_SEABIOS=y\n")
        elif config.payload == PayloadType.GRUB:
            config_lines.append("CONFIG_PAYLOAD_GRUB2=y\n")
        elif config.payload == PayloadType.TIANOCORE:
            config_lines.append("CONFIG_PAYLOAD_TIANOCORE=y\n")

        if config.use_native_vga_init:
            config_lines.append("CONFIG_VGA_ROM_RUN=n\n")
            config_lines.append("CONFIG_MAINBOARD_DO_NATIVE_VGA_INIT=y\n")
        else:
            config_lines.append("CONFIG_VGA_ROM_RUN=y\n")

        if config.include_cpu_microcode:
            config_lines.append("CONFIG_USE_CPU_MICROCODE=y\n")
        else:
            config_lines.append("CONFIG_USE_CPU_MICROCODE=n\n")

        def _clean_path(p: Path | str) -> str:
            posix_path = Path(p).resolve().as_posix()
            return re.sub(r'[\r\n"\'\\#]', '', posix_path)

        # Blobs configuration with POSIX paths and injection sanitization
        if blob_set:
            desc_posix = _clean_path(blob_set.descriptor_path)
            config_lines.append("CONFIG_HAVE_IFD_BIN=y\n")
            config_lines.append(f'CONFIG_IFD_BIN_PATH="{desc_posix}"\n')
            
            if blob_set.me_path and blob_set.me_path.exists():
                me_posix = _clean_path(blob_set.me_path)
                config_lines.append("CONFIG_HAVE_ME_BIN=y\n")
                config_lines.append(f'CONFIG_ME_BIN_PATH="{me_posix}"\n')
            else:
                config_lines.append("CONFIG_HAVE_ME_BIN=n\n")
                
            if blob_set.gbe_path and blob_set.gbe_path.exists():
                gbe_posix = _clean_path(blob_set.gbe_path)
                config_lines.append("CONFIG_HAVE_GBE_BIN=y\n")
                config_lines.append(f'CONFIG_GBE_BIN_PATH="{gbe_posix}"\n')

        # Append configurations
        with open(config_path, "a", encoding="utf-8") as f:
            f.writelines(config_lines)
            
        # Run make olddefconfig to resolve dependencies
        self._run_cmd(["make", "olddefconfig"], cwd=self.source_dir)

    def place_blobs(self, config: ROMConfiguration, blob_set: BlobSet) -> None:
        """Place extracted blobs in 3rdparty/blobs/mainboard/lenovo/<model>/"""
        self._report_progress("Placing binary blobs...")
        target_dir = self.source_dir / "3rdparty" / "blobs" / "mainboard" / "lenovo" / config.model_id.lower()
        target_dir.mkdir(parents=True, exist_ok=True)
        
        shutil.copy2(blob_set.descriptor_path, target_dir / "descriptor.bin")
        if blob_set.me_path and blob_set.me_path.exists():
            shutil.copy2(blob_set.me_path, target_dir / "me.bin")
        if blob_set.gbe_path and blob_set.gbe_path.exists():
            shutil.copy2(blob_set.gbe_path, target_dir / "gbe.bin")

    def compile(self) -> None:
        """Run make -j$(nproc) to compile."""
        self._report_progress("Compiling coreboot ROM...")
        nproc = str(os.cpu_count() or 1)
        try:
            self._run_cmd(["make", f"-j{nproc}"], cwd=self.source_dir)
        except SystemCommandError as e:
            logger.error("Coreboot compilation failed.")
            raise BuildError("Coreboot build failed. Check logs for details.") from e

    def post_process(self, config: ROMConfiguration) -> Path:
        """Post-processes ROM with cbfstool if needed (add bootsplash, etc.)."""
        self._report_progress("Running post-processing...")
        rom_path = self.source_dir / "build" / "coreboot.rom"
        if not rom_path.exists():
            raise BuildError("Compiled coreboot.rom not found.")

        if config.custom_bootsplash and config.custom_bootsplash.exists():
            self._report_progress("Adding custom bootsplash...")
            cbfstool_path = self.source_dir / "build" / "cbfstool"
            if not cbfstool_path.exists():
                cbfstool_path = self.source_dir / "util" / "cbfstool" / "cbfstool"
            self._run_cmd([
                str(cbfstool_path),
                str(rom_path),
                "add",
                "-f", str(config.custom_bootsplash),
                "-n", "bootsplash.bmp",
                "-t", "bootsplash"
            ], cwd=self.source_dir)

        # Copy to output path if specified
        if config.output_path:
            config.output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(rom_path, config.output_path)
            self._report_progress(f"ROM saved to {config.output_path}")
            return config.output_path
            
        return rom_path
