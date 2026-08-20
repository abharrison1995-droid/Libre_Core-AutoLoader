import enum
import logging
import platform
from dataclasses import dataclass
from typing import Callable, Optional, List

from autoloader.exceptions import UnsupportedModelError, BIOSReversionError, SafetyError
from autoloader.reversion.ivprep import IVPrepManager
from autoloader.detection.model import ModelDetector
from autoloader.detection.bios import BIOSDetector, VersionComparison
from autoloader.database.models import get_model, Generation
from autoloader.database.bios_versions import ONEVY_REQUIRED_VERSIONS, needs_bios_downgrade

logger = logging.getLogger(__name__)

class ReversionState(enum.Enum):
    DETECTING = "DETECTING"
    CHECKING_EC = "CHECKING_EC"
    PREPARING = "PREPARING"
    DOWNLOADING = "DOWNLOADING"
    FLASHING = "FLASHING"
    VERIFYING = "VERIFYING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"

@dataclass
class ReversionEvent:
    state: ReversionState
    message: str
    progress: float = 0.0

class ReversionChain:
    """
    Orchestrates the full BIOS reversion process.
    Uses a state machine pattern to transition between steps.
    """
    
    def __init__(self, state_callback: Optional[Callable[[ReversionEvent], None]] = None):
        self.state = ReversionState.DETECTING
        self.state_callback = state_callback
        self.model_detector = ModelDetector()
        self.bios_detector = BIOSDetector()
        self.model = ""
        self.current_bios = ""
        self.target_bios = ""

    def _emit(self, state: ReversionState, message: str, progress: float = 0.0):
        self.state = state
        event = ReversionEvent(state=state, message=message, progress=progress)
        logger.info(f"State Transition: {state.value} - {message}")
        if self.state_callback:
            self.state_callback(event)

    def run(self):
        """Executes the full reversion chain."""
        try:
            self._step_1_detect()
            if not self._step_2_check_needed():
                self._emit(ReversionState.COMPLETE, "Reversion not needed. BIOS is already compatible.", 100.0)
                return
                
            self._step_3_check_ec()
            self._step_4_execute_reversion()
            self._step_5_verify()
            
        except Exception as e:
            logger.exception("Reversion chain failed")
            if self.state != ReversionState.FAILED:
                self._emit(ReversionState.FAILED, f"Error: {str(e)}", 0.0)

    def _step_1_detect(self):
        self._emit(ReversionState.DETECTING, "Detecting current model and BIOS version...", 10.0)
        res = self.model_detector.detect()
        bios_info = self.bios_detector.detect()
        
        self.model = res.model_id or ""
        self.current_bios = bios_info.version or ""
        logger.info(f"Detected Model: {self.model}, BIOS: {self.current_bios}")
        
        if self.model.lower() in ONEVY_REQUIRED_VERSIONS:
            self.target_bios = ONEVY_REQUIRED_VERSIONS[self.model.lower()]
        else:
            self.target_bios = ""

    def _step_2_check_needed(self) -> bool:
        self._emit(ReversionState.PREPARING, "Determining if reversion is required...", 20.0)
        
        if not self.model:
            self._emit(ReversionState.FAILED, "Could not detect ThinkPad model.", 0.0)
            raise UnsupportedModelError("Could not detect ThinkPad model.")

        db_model = get_model(self.model.lower())
        if not db_model or not db_model.supports_1vyrain:
            logger.warning(f"Model {self.model} does not support software reversion via 1vyrain/IVprep.")
            logger.warning("For non-xx30 models, external hardware flashing with an SPI programmer is required.")
            self._emit(ReversionState.FAILED, "External hardware flasher required for this model.", 0.0)
            raise UnsupportedModelError("Hardware flasher required.")

        if not self.target_bios:
            self._emit(ReversionState.FAILED, f"No target BIOS version known for {self.model}.", 0.0)
            raise UnsupportedModelError(f"Unknown target BIOS version for {self.model}.")

        if not self.current_bios or self.current_bios.lower() in ("unknown", "none", ""):
            self._emit(ReversionState.FAILED, "Could not determine current BIOS version.", 0.0)
            raise BIOSReversionError("Could not determine current BIOS version.")

        comparison = self.bios_detector.compare_version(self.current_bios, self.target_bios)
        if comparison == VersionComparison.UNKNOWN:
            self._emit(ReversionState.FAILED, f"Could not compare BIOS versions {self.current_bios} and {self.target_bios}.", 0.0)
            raise BIOSReversionError("Could not compare BIOS versions.")

        if comparison == VersionComparison.MATCHES:
            logger.info("Current BIOS is compatible with 1vyrain. No downgrade needed.")
            return False
            
        logger.info(f"Downgrade required from {self.current_bios} to {self.target_bios}")
        return True

    def _step_3_check_ec(self):
        self._emit(ReversionState.CHECKING_EC, "Checking for EC modification requirements...", 30.0)
        logger.info("Reminder: If you plan to apply EC mods, they MUST be done BEFORE this step.")

    def _step_4_execute_reversion(self):
        if platform.system() != "Windows":
            logger.warning("Linux detected. Guided instructions:")
            logger.warning("1. Download IVprep from https://github.com/n4ru/IVprep")
            logger.warning("2. Boot into a Windows environment (WinPE or full OS)")
            logger.warning("3. Run downgrade.bat as Administrator")
            self._emit(ReversionState.FAILED, "Linux not supported for automated downgrade. Please use Windows.", 0.0)
            raise BIOSReversionError("Windows required for IVprep.")

        manager = IVPrepManager(model=self.model, target_version=self.target_bios)
        manager.verify_prerequisites()

        self._emit(ReversionState.DOWNLOADING, "Downloading and verifying IVprep...", 40.0)
        manager.download()
        
        self._emit(ReversionState.FLASHING, "Executing flash downgrade...", 70.0)
        manager.execute_downgrade()

    def _step_5_verify(self):
        self._emit(ReversionState.VERIFYING, "Verifying downgrade...", 90.0)
        manager = IVPrepManager(model=self.model, target_version=self.target_bios)
        if manager.verify_downgrade_success():
            self._emit(ReversionState.COMPLETE, "Reversion completed successfully! Please reboot.", 100.0)
        else:
            self._emit(ReversionState.FAILED, "Verification failed. BIOS version unchanged.", 90.0)
            raise BIOSReversionError("Verification failed. BIOS version unchanged.")
