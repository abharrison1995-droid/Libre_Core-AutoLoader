"""Fail-closed 1vyrain workflow surface.

The upstream exploit is not bundled or executed by AutoLoader. A pinned,
model-specific compatibility implementation and a verified operation backend
must exist before this manager can approve or perform any 1vyrain action.
"""
import logging
from enum import Enum
from typing import Callable

from autoloader.exceptions import OnevyUnavailableError

logger = logging.getLogger(__name__)


class ExploitState(Enum):
    READY = "READY"
    SUSPENDING = "SUSPENDING"
    EXPLOITING = "EXPLOITING"
    UNLOCKED = "UNLOCKED"
    FLASHING = "FLASHING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class OnevyRainManager:
    """Expose the 1vyrain workflow without simulating hardware outcomes."""

    def __init__(self) -> None:
        self.state = ExploitState.READY
        self.supported_models = ["x230", "x230t", "t430", "t430s", "t530", "w530"]

    def _unavailable(self, action: str, detail: str) -> OnevyUnavailableError:
        self.state = ExploitState.UNAVAILABLE
        message = f"1vyrain {action} is unavailable: {detail}"
        logger.warning(message)
        return OnevyUnavailableError(message)

    def verify_prerequisites(self, model: str, bios_version: str) -> bool:
        """Fail closed until a pinned upstream compatibility rule is implemented.

        The model list is informational only. It does not prove compatibility,
        and no local rule currently validates BIOS versions.
        """
        if model.lower() not in self.supported_models:
            logger.error("Model %s is not in the provisional 1vyrain model list.", model)
            return False
        raise self._unavailable(
            "compatibility check",
            f"no pinned upstream BIOS rule is available for {model} BIOS {bios_version!r}; "
            "compatibility was not verified.",
        )

    def prepare_exploit(self) -> None:
        """Report unavailable rather than claiming exploit preparation occurred."""
        raise self._unavailable(
            "preparation",
            "AutoLoader does not bundle or prepare upstream exploit media.",
        )

    def execute_exploit(self, user_cb: Callable[[str], None]) -> None:
        """Do not execute an exploit or report the SPI unlock as successful."""
        error = self._unavailable(
            "execution",
            "use the official upstream user-run workflow after compatibility and recovery checks; "
            "AutoLoader did not execute an exploit or unlock flash.",
        )
        user_cb(str(error))
        raise error

    def _run_exploit_binary(self) -> None:
        """No upstream exploit binary is bundled or invoked by this project."""
        raise self._unavailable(
            "binary execution",
            "no upstream binary or execution backend is configured.",
        )

    def verify_unlock(self) -> bool:
        """Never claim an unlock without reading and validating hardware state."""
        raise self._unavailable(
            "unlock verification",
            "PRx state has not been read from hardware; unlock status is unknown.",
        )

    def flash_rom(self, rom_path: str) -> None:
        """Fail closed; this class has no verified internal flash backend."""
        raise self._unavailable(
            "internal flash",
            f"no verified 1vyrain writer is connected for {rom_path!r}; no write was attempted.",
        )
