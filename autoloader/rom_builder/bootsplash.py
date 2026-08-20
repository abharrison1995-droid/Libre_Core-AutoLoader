import logging
import subprocess
from pathlib import Path
from typing import List

from autoloader.exceptions import ConfigurationError

logger = logging.getLogger(__name__)

import shutil

class BootsplashManager:
    """Custom splash screen manager."""

    def __init__(self, defaults_dir: Path):
        self.defaults_dir = defaults_dir

    def validate_image(self, image_path: Path) -> bool:
        """Validates image format (must be BMP)."""
        if not image_path.exists():
            raise FileNotFoundError(f"Image not found at {image_path}")
            
        if image_path.suffix.lower() != ".bmp":
            logger.error("Bootsplash image must be a BMP file.")
            return False
            
        return True

    def convert_image(self, input_path: Path, output_path: Path, resolution: str = "1024x768") -> Path:
        """Resizes to appropriate resolution for the payload using ImageMagick."""
        logger.info(f"Converting image to {resolution} BMP...")
        tool = "magick" if shutil.which("magick") else "convert"
        if not shutil.which(tool):
            logger.error("ImageMagick ('magick' or 'convert') not found.")
            raise ConfigurationError("ImageMagick not installed.")

        try:
            cmd = [tool]
            if tool == "magick":
                cmd.append("convert")
            cmd.extend([str(input_path), "-resize", f"{resolution}^", "-gravity", "center", "-extent", resolution, "-type", "truecolor", str(output_path)])
            
            subprocess.run(cmd, check=True, timeout=60)
            return output_path
        except subprocess.CalledProcessError as e:
            logger.error("ImageMagick conversion failed. Ensure it is installed.")
            raise ConfigurationError("Failed to convert bootsplash image.") from e
        except FileNotFoundError:
            logger.error("ImageMagick command not found.")
            raise ConfigurationError("ImageMagick not installed.")

    def inject_splash(self, cbfstool_path: Path, rom_path: Path, splash_path: Path) -> None:
        """Injects splash into ROM via cbfstool."""
        if not self.validate_image(splash_path):
            raise ConfigurationError("Invalid bootsplash image.")

        logger.info(f"Injecting bootsplash {splash_path} into {rom_path}")
        try:
            subprocess.run(
                [
                    str(cbfstool_path),
                    str(rom_path),
                    "add",
                    "-f", str(splash_path),
                    "-n", "bootsplash.bmp",
                    "-t", "bootsplash"
                ],
                check=True
            )
        except subprocess.CalledProcessError as e:
            raise ConfigurationError("Failed to inject bootsplash via cbfstool.") from e

    def list_defaults(self) -> List[Path]:
        """Provides default splash options."""
        if not self.defaults_dir.exists():
            return []
        return list(self.defaults_dir.glob("*.bmp"))
