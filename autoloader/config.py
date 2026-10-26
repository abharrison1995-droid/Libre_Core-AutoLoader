"""Global configuration for the AutoLoader."""

import json
import os
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Optional

PROJECT_NAME = "Libre_Core AutoLoader"
try:
    VERSION = version("libre-core-autoloader")
except PackageNotFoundError:
    VERSION = "0+unknown"

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
BIOS_IMAGES_DIR = DATA_DIR / "bios_images"
SPLASH_DIR = DATA_DIR / "splash"
DIAGRAMS_DIR = DATA_DIR / "diagrams"
EC_PATCHES_DIR = DATA_DIR / "ec_patches"
ROM_OUTPUT_DIR = BASE_DIR / "output" / "roms"
BACKUP_DIR = BASE_DIR / "output" / "backups"
CONFIG_FILE = BASE_DIR / "config.json"

# URLs
COREBOOT_REPO = "https://review.coreboot.org/coreboot.git"
LIBREBOOT_REPO = "https://codeberg.org/libreboot/lbmk.git"
CANOEBOOT_REPO = "https://codeberg.org/canoeboot/canoeboot.git"
IVPREP_REPO = "https://github.com/1vyra1/1vyrain.git"
ONEVY_REPO = "https://github.com/1vyra1/1vyrain.git"
THINKPAD_EC_REPO = "https://github.com/hamishcoleman/thinkpad-ec.git"
ME_CLEANER_REPO = "https://github.com/corna/me_cleaner.git"

# Timeouts
FLASH_TIMEOUT = 600  # seconds
BUILD_TIMEOUT = 3600  # seconds

# Safety constants
MIN_BATTERY_PERCENT = 30
REQUIRED_FREE_SPACE_MB = 2048


@dataclass
class Config:
    """Configuration class for the AutoLoader."""
    
    verbose: bool = False
    log_file: Path = BASE_DIR / "autoloader.log"
    data_dir: Path = DATA_DIR
    rom_output_dir: Path = ROM_OUTPUT_DIR
    backup_dir: Path = BACKUP_DIR

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Config":
        """Load configuration from a JSON file.
        
        Args:
            path: Path to the configuration file. Defaults to CONFIG_FILE.
            
        Returns:
            A Config instance.
        """
        config_path = path or CONFIG_FILE
        if not config_path.exists():
            return cls()
        
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Convert string paths back to Path objects where applicable
            if "log_file" in data:
                data["log_file"] = Path(data["log_file"])
            if "data_dir" in data:
                data["data_dir"] = Path(data["data_dir"])
            if "rom_output_dir" in data:
                data["rom_output_dir"] = Path(data["rom_output_dir"])
            if "backup_dir" in data:
                data["backup_dir"] = Path(data["backup_dir"])
                
            return cls(**data)
        except (json.JSONDecodeError, OSError, TypeError) as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to parse config file {config_path}: {e}. Using default configuration.")
            return cls()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Unexpected error loading config file {config_path}: {e}. Using default configuration.")
            return cls()

    def save(self, path: Optional[Path] = None) -> None:
        """Save configuration to a JSON file.
        
        Args:
            path: Path to the configuration file. Defaults to CONFIG_FILE.
        """
        config_path = path or CONFIG_FILE
        data = {
            "verbose": self.verbose,
            "log_file": str(self.log_file),
            "data_dir": str(self.data_dir),
            "rom_output_dir": str(self.rom_output_dir),
            "backup_dir": str(self.backup_dir),
        }
        
        config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = config_path.with_suffix(".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        os.replace(tmp_file, config_path)
