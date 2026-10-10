"""Immutable evidence, plan, compatibility and workflow value types."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoloader.detection.bios import BIOSInfo
    from autoloader.detection.model import DetectionResult


class FirmwareFamily(str, Enum):
    ONEVYRAIN = "1vyrain"
    LIBREBOOT = "libreboot"
    COREBOOT = "coreboot"


class ProgrammerKind(str, Enum):
    INTERNAL_ONEVYRAIN = "internal-onevyrain"
    EXTERNAL_SPI = "external-spi"
    SIMULATION = "simulation"


class CompatibilityDecision(str, Enum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class EvidenceConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    FAILED = "failed"
    UNKNOWN = "unknown"
    NOT_PERFORMED = "not-performed"


class OperationState(str, Enum):
    DRAFT = "draft"
    VALIDATED = "validated"
    BACKUP_VERIFIED = "backup-verified"
    AUTHORIZED = "authorized"
    WRITE_BLOCKED = "write-blocked"
    BLOCKED = "blocked"
    FAILED = "failed"
    READBACK_VERIFIED = "readback-verified"
    COMPLETE = "complete"


@dataclass(frozen=True)
class DeviceEvidence:
    """Raw DMI/MTM/BIOS observations; manual selection is only a candidate."""

    raw_dmi_product_name: str | None = None
    raw_dmi_product_version: str | None = None
    raw_dmi_system_family: str | None = None
    raw_mtm: str | None = None
    raw_bios_version: str | None = None
    raw_bios_vendor: str | None = None
    stock_lenovo_bios: bool | None = None
    manual_model_candidate: str | None = None
    internal_flash_unlock_status: bool | None = None
    internal_flash_unlock_observed: bool = False

    @classmethod
    def from_detection(
        cls,
        model: DetectionResult,
        bios: BIOSInfo,
        *,
        manual_model_candidate: str | None = None,
    ) -> DeviceEvidence:
        """Preserve detector-provided raw fields without elevating manual input."""
        return cls(
            raw_dmi_product_name=getattr(model, "raw_product_name", None),
            raw_dmi_product_version=getattr(model, "raw_product_version", None),
            raw_dmi_system_family=getattr(model, "raw_system_family", None),
            raw_mtm=getattr(model, "raw_mtm", None),
            raw_bios_version=getattr(bios, "raw_version", None),
            raw_bios_vendor=bios.vendor,
            stock_lenovo_bios=bios.is_stock_lenovo,
            manual_model_candidate=manual_model_candidate,
        )


@dataclass(frozen=True)
class ChipEvidence:
    chip_id: str
    capacity_bytes: int
    observed: bool
    physical_label: str = ""
    voltage_mv: int | None = None
    manufacturer: str | None = None
    model_number: str | None = None


@dataclass(frozen=True)
class RegionEvidence:
    name: str
    start_byte: int
    end_byte: int

    @property
    def size_bytes(self) -> int:
        return self.end_byte - self.start_byte


@dataclass(frozen=True)
class ChipTopology:
    observed: bool
    chips: tuple[ChipEvidence, ...] = ()
    regions: tuple[RegionEvidence, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "chips", tuple(self.chips))
        object.__setattr__(self, "regions", tuple(self.regions))


@dataclass(frozen=True)
class ProgrammerEvidence:
    kind: ProgrammerKind
    observed: bool
    interface_verified: bool | None = None
    wiring_verified: bool | None = None
    voltage_mv: int | None = None


@dataclass(frozen=True)
class ArtifactEvidence:
    path: str
    model_id: str
    firmware_family: FirmwareFamily
    expected_sha256: str
    expected_size_bytes: int
    source_url: str
    source_revision: str
    regions: tuple[RegionEvidence, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "regions", tuple(self.regions))


@dataclass(frozen=True)
class BackupPairEvidence:
    chip_id: str
    first_path: str
    second_path: str
    first_sha256: str
    second_sha256: str
    first_capture_id: str = ""
    second_capture_id: str = ""
    transient: bool = False


@dataclass(frozen=True)
class BackupEvidence:
    pairs: tuple[BackupPairEvidence, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "pairs", tuple(self.pairs))


@dataclass(frozen=True)
class OperationPlan:
    profile_id: str
    requested_model_id: str
    firmware_family: FirmwareFamily
    device: DeviceEvidence
    topology: ChipTopology
    programmer: ProgrammerEvidence
    artifact: ArtifactEvidence
    backup: BackupEvidence = field(default_factory=BackupEvidence)


@dataclass(frozen=True)
class CompatibilityResult:
    decision: CompatibilityDecision
    profile_id: str
    confidence: EvidenceConfidence
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "reasons", tuple(self.reasons))


@dataclass(frozen=True)
class OperationResult:
    state: OperationState
    decision: CompatibilityDecision
    code: str
    message: str


@dataclass(frozen=True)
class VerificationEvidence:
    """Immutable record of an observed or explicitly unperformed check."""

    kind: str
    status: VerificationStatus
    expected_sha256: str | None = None
    observed_sha256: str | None = None
    capture_ids: tuple[str, ...] = ()
    details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "capture_ids", tuple(self.capture_ids))
        object.__setattr__(self, "details", tuple(self.details))


@dataclass(frozen=True)
class PlanDigests:
    plan_sha256: str
    device_sha256: str
    artifact_sha256: str
    topology_sha256: str
    backup_sha256: str


def normalized_path(value: str) -> str:
    """Stable path representation for digesting; does not resolve/read the path."""
    return str(Path(value).expanduser().resolve(strict=False))
