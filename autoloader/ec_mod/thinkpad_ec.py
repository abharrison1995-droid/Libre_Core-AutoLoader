import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, NoReturn, Optional

from autoloader.detection.bios import BIOSDetector, VersionComparison
from autoloader.detection.model import ModelDetector
from autoloader.exceptions import ECModError, SafetyError

logger = logging.getLogger(__name__)


@dataclass
class ECPatch:
    name: str
    description: str
    enabled: bool = False


class ThinkPadECManager:
    """Retain EC detection helpers while keeping modification unavailable in V1."""

    _UNAVAILABLE = (
        "EC modification is unavailable in V1: no pinned, reviewed, and "
        "validated EC build/write workflow is implemented."
    )

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

    def download_source(self) -> NoReturn:
        """Reject the legacy mutable, unpinned source-download path."""
        self._raise_unavailable("EC source download")

    def get_available_patches(self) -> List[ECPatch]:
        """Return no patches: V1 has no reviewed, reproducible EC patch workflow."""
        return []

    def build_ec_firmware(self, config_commands: List[str]) -> NoReturn:
        """Reject builds from arbitrary or mutable local checkouts."""
        self._raise_unavailable("EC firmware build")

    def flash_ec(self, img_path: Path, strict: bool = True) -> NoReturn:
        """Reject the legacy EC media/flash instruction path."""
        self._raise_unavailable("EC flash")

    def _check_cve_2019_6171(self, strict: bool = True) -> None:
        """Retain the BIOS cutoff check for callers that use this policy helper."""
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
                logger.warning(
                    "Your BIOS version (%s) includes protections against modified EC firmware "
                    "(CVE-2019-6171 lockout at >=%s)!",
                    self.current_bios,
                    cutoff,
                )
                logger.warning("You MUST downgrade your BIOS (using the Reversion module) BEFORE flashing this EC image.")
            elif comparison == VersionComparison.UNKNOWN and strict:
                raise SafetyError(
                    f"Cannot verify BIOS version '{self.current_bios}' against CVE-2019-6171 cutoff ({cutoff}). "
                    "Downgrade BIOS first."
                )

        if locked and strict:
            raise SafetyError(
                f"BIOS version {self.current_bios} has CVE-2019-6171 digital signature protection. "
                "Downgrade BIOS first."
            )

    def verify_ec(self) -> NoReturn:
        """Fail closed instead of reporting success without EC evidence."""
        self._raise_unavailable("EC verification")

    def _raise_unavailable(self, operation: str) -> NoReturn:
        message = f"{operation} unavailable. {self._UNAVAILABLE}"
        logger.warning(message)
        raise ECModError(message)
