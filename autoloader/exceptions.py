"""Custom exceptions for the Libre_Core AutoLoader."""


class AutoLoaderError(Exception):
    """Base exception for AutoLoader."""


class OperationUnavailableError(AutoLoaderError):
    """Raised when a user requests a workflow that is not implemented safely."""

    code = "OPERATION_UNAVAILABLE"


class BackupUnavailableError(OperationUnavailableError):
    """Raised when backup completeness cannot be established safely."""

    code = "BACKUP_UNAVAILABLE"


class ROMBuildUnavailableError(OperationUnavailableError):
    """Raised when no validated ROM builder is connected."""

    code = "ROM_BUILD_UNAVAILABLE"


class PhysicalFlashUnavailableError(OperationUnavailableError):
    """Raised when no validated and authorized physical flash backend exists."""

    code = "PHYSICAL_FLASH_UNAVAILABLE"


class DetectionError(AutoLoaderError):
    """Raised when hardware detection fails."""


class UnsupportedModelError(DetectionError):
    """Raised when an unsupported model is detected."""


class BIOSReversionError(AutoLoaderError):
    """Raised when BIOS reversion fails."""


class VersionMismatchError(BIOSReversionError):
    """Raised when BIOS version does not match expected version."""


class ROMBuildError(AutoLoaderError):
    """Raised when ROM building fails."""


class BlobExtractionError(ROMBuildError):
    """Raised when extracting binary blobs fails."""


class CompilationError(ROMBuildError):
    """Raised when compiling the ROM fails."""


class FlashError(AutoLoaderError):
    """Raised when flashing the ROM fails."""


class BackupVerificationError(FlashError):
    """Raised when backup verification fails."""


class FlashVerificationError(FlashError):
    """Raised when flash verification fails."""


class ECModError(AutoLoaderError):
    """Raised when EC modification fails."""


class USBBuildError(AutoLoaderError):
    """Raised when building the USB image fails."""


class SafetyError(AutoLoaderError):
    """Raised when safety checks fail."""


class LowBatteryError(SafetyError):
    """Raised when the battery level is too low."""


class InsufficientSpaceError(SafetyError):
    """Raised when there is insufficient disk space."""


# ---------------------------------------------------------------------------
# Aliases used by submodules (reconciling independent agent naming)
# ---------------------------------------------------------------------------

# detection/ modules import this name
HardwareDetectionError = DetectionError

# rom_builder/ modules import these names
BuildError = ROMBuildError
SystemCommandError = CompilationError
ConfigurationError = ROMBuildError
FirmwareNotSupportedError = UnsupportedModelError
PayloadNotSupportedError = ROMBuildError
