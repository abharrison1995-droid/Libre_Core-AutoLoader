import logging
from typing import Optional

from .model import ModelDetector, DetectionResult, detect_model
from .bios import BIOSDetector, BIOSInfo, VersionComparison, detect_bios_version
from .ec import ECDetector, ECInfo, detect_ec_version

__all__ = [
    "ModelDetector",
    "DetectionResult",
    "detect_model",
    "BIOSDetector",
    "BIOSInfo",
    "VersionComparison",
    "detect_bios_version",
    "ECDetector",
    "ECInfo",
    "detect_ec_version",
]
