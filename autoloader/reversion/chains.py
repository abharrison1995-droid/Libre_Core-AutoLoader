"""Deprecated reversion facade kept as a fail-closed compatibility adapter."""
from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from typing import Callable, Optional

from autoloader.exceptions import BIOSReversionError

logger = logging.getLogger(__name__)


class ReversionState(enum.Enum):
    DETECTING = "DETECTING"
    CHECKING_EC = "CHECKING_EC"
    PREPARING = "PREPARING"
    DOWNLOADING = "DOWNLOADING"
    FLASHING = "FLASHING"
    VERIFYING = "VERIFYING"
    HANDOFF_REQUIRED = "HANDOFF_REQUIRED"
    AWAITING_REDETECTION = "AWAITING_REDETECTION"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ReversionEvent:
    state: ReversionState
    message: str
    progress: float = 0.0


class ReversionChain:
    """Legacy chain whose automation methods are intentionally unavailable.

    Use ``T430HandoffSession`` for pinned, human-operated instructions and
    fresh re-detection checkpoints. This facade cannot complete a reversion.
    """

    def __init__(self, state_callback: Optional[Callable[[ReversionEvent], None]] = None):
        self.state = ReversionState.DETECTING
        self.state_callback = state_callback
        self.model = ""
        self.current_bios = ""
        self.target_bios = ""

    def _emit(self, state: ReversionState, message: str, progress: float = 0.0) -> None:
        self.state = state
        event = ReversionEvent(state=state, message=message, progress=progress)
        logger.info("Reversion state: %s - %s", state.value, message)
        if self.state_callback:
            self.state_callback(event)

    def run(self) -> None:
        """Fail closed without detection subprocesses or external execution."""
        self._emit(
            ReversionState.FAILED,
            "Automated IVprep/reversion is disabled; use the explicit T430 handoff workflow. No action was attempted.",
            0.0,
        )

    def _step_4_execute_reversion(self) -> None:
        raise BIOSReversionError(
            "IVPREP_EXECUTION_DISABLED: the legacy reversion chain cannot download or execute downgrade.bat"
        )

    def _step_5_verify(self) -> None:
        raise BIOSReversionError(
            "BIOS_AUTOMATED_VERIFICATION_DISABLED: a fresh post-reboot T430 evidence capture is required"
        )
