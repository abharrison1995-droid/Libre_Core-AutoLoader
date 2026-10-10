"""Fail-closed IVprep source handoff; no download or downgrade execution."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from autoloader.exceptions import BIOSReversionError, SafetyError, UnsupportedModelError

IVPREP_REPOSITORY = "https://github.com/n4ru/IVprep"
IVPREP_REVISION = "7faecc72be8776be32f37e7ff00af22a25dfc3cf"
IVPREP_TREE = "e5c684e3dc5b90d7e70122229a8b46bd4e4bd012"
IVPREP_README_SHA256 = "e1b8bc1feea5d8b52e5bacc8fc520a98cb21b125824800a7662d6119fb8492e5"
IVPREP_DOWNGRADE_SCRIPT_SHA256 = "cb5528d6f1d001937b224f3d9b7f075a928c2692e12d8f74b24533dd12d2dd31"
IVPREP_TARGET_BIOS = "2.64"


@dataclass(frozen=True)
class IVprepConfig:
    """Narrow metadata for the only supported V1 IVprep handoff."""

    model: str
    target_bios_version: str = IVPREP_TARGET_BIOS


@dataclass(frozen=True)
class IVprepHandoffDescriptor:
    """Pinned instructions for a human-operated external Windows workflow."""

    model: str
    target_bios_version: str
    repository_url: str
    revision: str
    tree: str
    readme_url: str
    readme_sha256: str
    downgrade_script_sha256: str
    approved_by: str
    approved_at: datetime
    required_platform: str = "Windows 64-bit"
    requires_external_human_run: bool = True
    power_loss_can_brick_device: bool = True
    binaries_included: bool = False
    binary_assets_verified: bool = False
    executes_anything: bool = False


class IVPrepManager:
    """Legacy API adapter that exposes source instructions only.

    Upstream IVprep is Windows 64-bit-only and performs a BIOS-region flash.
    V1 does not download or execute its mutable archive or its batch script.
    """

    TARGET_VERSIONS = {"t430": IVPREP_TARGET_BIOS}

    def __init__(
        self,
        model: str | None = None,
        target_version: str | None = None,
        expected_sha256: str | None = None,
    ) -> None:
        # Retain the old signature to avoid breaking imports, but never accept
        # a caller-provided archive checksum as artifact verification.
        del expected_sha256
        self.model = (model or "").strip().lower()
        target = target_version or self.TARGET_VERSIONS.get(self.model, "")
        self.config = IVprepConfig(self.model, target)

    def create_handoff_descriptor(
        self, *, actor: str, explicit_approval: bool, approved_at: datetime
    ) -> IVprepHandoffDescriptor:
        if self.model != "t430":
            raise UnsupportedModelError("V1 IVprep handoff is limited to the exact T430 profile")
        if self.config.target_bios_version != IVPREP_TARGET_BIOS:
            raise SafetyError("V1 T430 IVprep target must be pinned at BIOS 2.64")
        if not actor.strip() or not explicit_approval:
            raise SafetyError("Explicit human approval is required before displaying the IVprep handoff")
        if approved_at.tzinfo is None or approved_at.utcoffset() != timezone.utc.utcoffset(approved_at):
            raise SafetyError("IVprep approval timestamp must be timezone-aware UTC")
        return IVprepHandoffDescriptor(
            model="t430",
            target_bios_version=IVPREP_TARGET_BIOS,
            repository_url=IVPREP_REPOSITORY,
            revision=IVPREP_REVISION,
            tree=IVPREP_TREE,
            readme_url=f"{IVPREP_REPOSITORY}/blob/{IVPREP_REVISION}/README.md",
            readme_sha256=IVPREP_README_SHA256,
            downgrade_script_sha256=IVPREP_DOWNGRADE_SCRIPT_SHA256,
            approved_by=actor.strip(),
            approved_at=approved_at,
        )

    @staticmethod
    def _unavailable(action: str) -> BIOSReversionError:
        return BIOSReversionError(
            f"IVPREP_{action}_DISABLED: V1 provides only a pinned human handoff; "
            "no download, script execution, or BIOS write is available"
        )

    def verify_prerequisites(self) -> bool:
        raise self._unavailable("AUTOMATION")

    def download(self):
        """Never download a moving upstream archive or firmware assets."""
        raise self._unavailable("DOWNLOAD")

    def execute_downgrade(self) -> bool:
        """Never launch downgrade.bat, WinFlash, or a BIOS writer."""
        raise self._unavailable("EXECUTION")

    def verify_downgrade_success(self) -> bool:
        """A version read without a fresh post-reboot handoff is not success."""
        raise self._unavailable("AUTOMATED_VERIFICATION")
