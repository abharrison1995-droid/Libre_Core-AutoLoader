import logging
import subprocess
import shutil
import sys
from pathlib import Path
from typing import Dict, Any

from autoloader.exceptions import BlobExtractionError

logger = logging.getLogger(__name__)

class MECleaner:
    """Intel ME neutering utility."""

    def __init__(self, me_cleaner_script: Path):
        self.script_path = me_cleaner_script

    def clean_me(self, me_path: Path, output_path: Path, soft_disable: bool = True, strip: bool = True, truncate: bool = False, alt_disable: bool = False) -> Path:
        """Runs me_cleaner on the extracted ME blob with configurable options."""
        if not me_path.exists():
            raise FileNotFoundError(f"ME region not found at {me_path}")

        logger.info(f"Neutering Intel ME at {me_path}...")
        
        cmd = [sys.executable, str(self.script_path), "-O", str(output_path)]
        if soft_disable:
            cmd.append("-S")
        if strip:
            cmd.append("-r")
        if truncate:
            cmd.append("-t")
        if alt_disable:
            cmd.append("-d")

        cmd.append(str(me_path))

        try:
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=120
            )
            return output_path
        except subprocess.CalledProcessError as e:
            logger.error(f"me_cleaner failed: {e.stdout}")
            raise BlobExtractionError("Failed to neuter Intel ME.") from e
        except subprocess.TimeoutExpired as e:
            logger.error("me_cleaner timed out")
            raise BlobExtractionError("me_cleaner process timed out.") from e

    def verify_cleaned(self, cleaned_me_path: Path) -> bool:
        """Validates the cleaned ME output."""
        if not cleaned_me_path.exists():
            return False
        return cleaned_me_path.stat().st_size > 0

    def get_me_info(self, me_path: Path) -> Dict[str, Any]:
        """Runs me_cleaner -c to get info about the ME region."""
        cmd = [sys.executable, str(self.script_path), "-c", str(me_path)]
        try:
            result = subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=60
            )
            return {"output": result.stdout}
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to get ME info: {e.stdout}")
            raise BlobExtractionError("Failed to parse ME region.") from e
        except subprocess.TimeoutExpired as e:
            logger.error("me_cleaner info timed out")
            raise BlobExtractionError("me_cleaner info timed out.") from e
