"""Human-operated, source-pinned T430 BIOS/1vyrain handoff workflow.

This module only classifies raw evidence and records external handoff states.
It never downloads, executes, flashes, writes media, or declares a completed
hardware workflow. A fresh evidence capture is required after each reboot.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from uuid import uuid4

from autoloader.exceptions import BIOSReversionError, SafetyError
from autoloader.workflow.compatibility import CompatibilityValidator
from autoloader.workflow.profiles import ONEVYRAIN_REVISION
from autoloader.workflow.types import CompatibilityDecision, DeviceEvidence
from autoloader.detection.bios import BIOSDetector
from autoloader.detection.model import ModelDetector

from .ivprep import (
    IVPREP_TARGET_BIOS,
    IVPrepManager,
    IVprepHandoffDescriptor,
)

ONEVYRAIN_REPOSITORY = "https://github.com/n4ru/1vyrain"
ONEVYRAIN_TREE = "2298fa23e74c7112b7227d8354a0e51404fda4da"
ONEVYRAIN_README_SHA256 = "5af0f463e56e8fcfbc6d3b57224859e755e701d4e04087167c9e335b14e8c33a"
ONEVYRAIN_START_SHA256 = "d00089253cb56a8967e242a9e73496c413a7c3d378044968acbc6278b31e873a"
ONEVYRAIN_REV5_MEDIA_URL = "https://drive.google.com/open?id=1yusq98ja6NmI4G4txKVueFqY_ZEwaZvO"
ONEVYRAIN_REV5_MD5 = "dd30313a8665e870360920b00cec55c4"
_MD5 = re.compile(r"^[0-9a-f]{32}$", re.I)
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.I)


class T430HandoffStage(str, Enum):
    UNKNOWN = "unknown"
    BLOCKED = "blocked"
    IVPREP_REQUIRED = "ivprep-required"
    AWAITING_IVPREP_REDETECTION = "awaiting-ivprep-redetection"
    ONEVYRAIN_READY = "onevy-rain-ready"
    AWAITING_POST_INSTALL_REDETECTION = "awaiting-post-install-redetection"
    POST_INSTALL_EVIDENCE_PENDING_ACCEPTANCE = "post-install-evidence-pending-acceptance"


@dataclass(frozen=True)
class T430Assessment:
    stage: T430HandoffStage
    decision: CompatibilityDecision
    numeric_bios: int | None
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class T430DetectionCapture:
    """One read-only raw DMI/BIOS capture, with a unique ID and UTC time."""

    capture_id: str
    observed_at: datetime
    device: DeviceEvidence
    device_fingerprint_sha256: str | None

    def __post_init__(self) -> None:
        if not self.capture_id.strip():
            raise ValueError("A unique detection capture ID is required")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != timezone.utc.utcoffset(self.observed_at):
            raise ValueError("Detection timestamps must be timezone-aware UTC")
        if self.device_fingerprint_sha256 and not _SHA256.fullmatch(self.device_fingerprint_sha256):
            raise ValueError("Device fingerprint must be a SHA-256 digest")

    @classmethod
    def detect_now(
        cls,
        *,
        model_detector: ModelDetector | None = None,
        bios_detector: BIOSDetector | None = None,
    ) -> T430DetectionCapture:
        """Perform fresh read-only detection; never invokes a flash utility."""
        model = (model_detector or ModelDetector()).detect()
        bios = (bios_detector or BIOSDetector()).detect(force_refresh=True)
        serial = (model.serial_number or "").strip().upper()
        device_fingerprint = None
        if serial:
            device_fingerprint = hashlib.sha256(serial.encode("utf-8")).hexdigest()
        return cls(
            capture_id=uuid4().hex,
            observed_at=datetime.now(timezone.utc),
            device=DeviceEvidence.from_detection(model, bios),
            device_fingerprint_sha256=device_fingerprint,
        )


@dataclass(frozen=True)
class T430PreflightEvidence:
    """Human-attested BIOS, power, boot path, and identity checks for one capture."""

    capture_id: str
    device_fingerprint_sha256: str
    attested_by: str
    attested_at: datetime
    bios_passwords_removed: bool | None
    end_user_bios_update_enabled: bool | None
    secure_rollback_prevention_disabled: bool | None
    ac_connected: bool | None
    battery_percent: int | None
    uefi_booted: bool | None
    secure_boot_disabled: bool | None
    will_boot_1vyrain_from_direct_onboard_usb_without_dock_or_hub: bool | None

    def __post_init__(self) -> None:
        if not self.capture_id.strip() or not self.attested_by.strip():
            raise ValueError("Preflight evidence requires a capture ID and named human")
        if not _SHA256.fullmatch(self.device_fingerprint_sha256):
            raise ValueError("Preflight device fingerprint must be a SHA-256 digest")
        if self.attested_at.tzinfo is None or self.attested_at.utcoffset() != timezone.utc.utcoffset(self.attested_at):
            raise ValueError("Preflight attestation time must be timezone-aware UTC")


@dataclass(frozen=True)
class T430MediaEvidence:
    """User-reported external image/media identifiers; authenticity is unverified."""

    capture_id: str
    device_fingerprint_sha256: str
    user_reported_image_sha256: str
    user_reported_image_md5: str | None
    image_size_bytes: int
    image_source_url: str
    user_reported_media_fingerprint_sha256: str
    written_in_dd_mode: bool | None
    selected_at: datetime
    selected_by: str
    integrity_claim: str = "user-reported local SHA-256 fingerprint only; upstream authenticity is not verified"

    def __post_init__(self) -> None:
        if not self.capture_id.strip():
            raise ValueError("Media evidence must name the current detection capture")
        if not _SHA256.fullmatch(self.device_fingerprint_sha256):
            raise ValueError("Media evidence device fingerprint must be a SHA-256 digest")
        if not _SHA256.fullmatch(self.user_reported_image_sha256):
            raise ValueError("User-reported image SHA-256 must be 64 hexadecimal characters")
        if self.user_reported_image_md5 is not None and not _MD5.fullmatch(self.user_reported_image_md5):
            raise ValueError("User-reported image MD5 must be 32 hexadecimal characters when supplied")
        if not _SHA256.fullmatch(self.user_reported_media_fingerprint_sha256):
            raise ValueError("USB/media fingerprint must be a SHA-256 digest")
        if self.image_size_bytes <= 0:
            raise ValueError("Image size must be positive")
        if not self.image_source_url.strip() or not self.selected_by.strip():
            raise ValueError("Image source and selecting user are required")
        if self.integrity_claim != "user-reported local SHA-256 fingerprint only; upstream authenticity is not verified":
            raise ValueError("T430 media cannot claim upstream artifact authenticity")
        if self.selected_at.tzinfo is None or self.selected_at.utcoffset() != timezone.utc.utcoffset(self.selected_at):
            raise ValueError("Image/media selection time must be timezone-aware UTC")


@dataclass(frozen=True)
class OnevyRainHandoffDescriptor:
    model: str
    repository_url: str
    revision: str
    tree: str
    readme_url: str
    start_script_url: str
    readme_sha256: str
    start_script_sha256: str
    approved_by: str
    approved_at: datetime
    user_reported_image_sha256: str
    image_size_bytes: int
    user_reported_image_source_url: str
    user_reported_media_fingerprint_sha256: str
    user_reported_image_md5: str
    upstream_rev5_media_url: str = ONEVYRAIN_REV5_MEDIA_URL
    upstream_rev5_published_md5: str = ONEVYRAIN_REV5_MD5
    md5_integrity_claim: str = "user-reported MD5 matches the pinned README's legacy checksum; weak checksum, not authentication"
    image_authenticated: bool = False
    written_in_dd_mode: bool = True
    instructions: tuple[str, ...] = (
        "Use UEFI boot mode and disable Secure Boot before starting.",
        "Boot the media from a direct onboard USB port; do not use a dock, ultrabase, or external USB hub.",
        "Write the upstream image to the selected media in DD mode.",
        "Connect AC power, charge the battery, and remove BIOS passwords before running.",
        "Use the upstream Rev 5 image at https://drive.google.com/open?id=1yusq98ja6NmI4G4txKVueFqY_ZEwaZvO; compare its README-published MD5 dd30313a8665e870360920b00cec55c4.",
        "That MD5 is weak/legacy and does not authenticate the image; the user-reported SHA-256 is only a local fingerprint, and the application does not authenticate or execute the image.",
    )
    image_integrity_claim: str = "user-reported local SHA-256 fingerprint only; upstream authenticity is not verified"
    upstream_sha256_verified: bool = False
    requires_external_human_run: bool = True
    binaries_included: bool = False
    binaries_verified: bool = False
    executes_anything: bool = False
    writes_firmware: bool = False


@dataclass(frozen=True)
class T430PostInstallReport:
    capture_id: str
    observed_at: datetime
    raw_device_evidence: DeviceEvidence
    reported_by: str
    device_fingerprint_sha256: str
    onevy_approved_at: datetime
    preflight: T430PreflightEvidence
    media: T430MediaEvidence
    user_reported_successful_reboot: bool
    user_reported_boot_test_passed: bool
    user_reported_1vyrain_validation_passed: bool
    status: T430HandoffStage = T430HandoffStage.POST_INSTALL_EVIDENCE_PENDING_ACCEPTANCE


class T430HandoffCheckpointStore:
    """Atomic, checksummed persistence for an approved external handoff.

    Checkpoints contain human-entered evidence and raw DMI fields. They are
    intentionally limited to an outstanding IVprep or 1vyrain handoff and
    cannot resume into a firmware write or a completed state.
    """

    VERSION = 2
    RESUMABLE_STAGES = {
        T430HandoffStage.AWAITING_IVPREP_REDETECTION,
        T430HandoffStage.AWAITING_POST_INSTALL_REDETECTION,
    }

    @classmethod
    def save(cls, session: T430HandoffSession, path: str | Path) -> None:
        if session.stage not in cls.RESUMABLE_STAGES:
            raise SafetyError("Only an approved, outstanding T430 handoff can be checkpointed")
        if not session._handoff_approved or not session._approved_actor or not session._approval_timestamp:
            raise SafetyError("A durable T430 checkpoint requires explicit actor and approval time")
        if session._preflight is None:
            raise SafetyError("A durable T430 checkpoint requires passing preflight evidence")
        if session.stage is T430HandoffStage.AWAITING_POST_INSTALL_REDETECTION and session._media is None:
            raise SafetyError("A durable 1vyrain checkpoint requires user-reported image/media evidence")

        payload: dict[str, object] = {
            "version": cls.VERSION,
            "checkpoint_status": "pending",
            "operation_id": session.operation_id,
            "stage": session.stage.value,
            "capture": {
                "capture_id": session._capture_id,
                "observed_at": session._observed_at.isoformat(),
                "device": asdict(session._device),
                "device_fingerprint_sha256": session._device_fingerprint_sha256,
            },
            "approved_by": session._approved_actor,
            "approved_at": session._approval_timestamp.isoformat(),
            "preflight": cls._preflight_record(session._preflight),
            "media": cls._media_record(session._media) if session._media is not None else None,
        }
        canonical = cls._canonical(payload)
        envelope: dict[str, object] = {"payload": payload, "sha256": hashlib.sha256(canonical).hexdigest()}
        cls._archive_existing_checkpoint(path)
        cls._write_envelope(path, envelope)

    @classmethod
    def consume(
        cls,
        session: T430HandoffSession,
        *,
        result_code: str,
        return_capture: T430DetectionCapture,
        reported_by: str,
        reported_evidence: dict[str, object],
    ) -> None:
        """Atomically retire a pending approval after a valid fresh return."""
        if session.checkpoint_path is None:
            raise SafetyError("Approved T430 handoff has no durable checkpoint to consume")
        payload = cls.load(session.checkpoint_path)
        if payload.get("operation_id") != session.operation_id or payload.get("stage") != session.stage.value:
            raise SafetyError("T430 checkpoint does not match the outstanding handoff operation")
        terminal = dict(payload)
        terminal.update(
            {
                "checkpoint_status": "consumed",
                "stage": "consumed",
                "result_code": result_code,
                "reported_by": reported_by,
                "return_capture": {
                    "capture_id": return_capture.capture_id,
                    "observed_at": return_capture.observed_at.isoformat(),
                    "device": asdict(return_capture.device),
                    "device_fingerprint_sha256": return_capture.device_fingerprint_sha256,
                },
                "return_evidence": reported_evidence,
            }
        )
        envelope: dict[str, object] = {
            "payload": terminal,
            "sha256": hashlib.sha256(cls._canonical(terminal)).hexdigest(),
        }
        cls._write_envelope(session.checkpoint_path, envelope)

    @classmethod
    def _write_envelope(cls, path: str | Path, envelope: dict[str, object]) -> None:
        target = Path(path)
        if target.is_symlink():
            raise SafetyError("T430 checkpoint target cannot be a symlink")
        parent = target.parent.resolve(strict=True)
        if not parent.is_dir():
            raise SafetyError("T430 checkpoint parent must be an existing directory")
        temp = parent / f".{target.name}.{uuid4().hex}.tmp"
        try:
            with temp.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(envelope, stream, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, target)
        except Exception:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    @classmethod
    def load(cls, path: str | Path) -> dict[str, object]:
        payload = cls._read_verified_payload(path)
        required = {
            "version", "checkpoint_status", "operation_id", "stage", "capture",
            "approved_by", "approved_at", "preflight", "media",
        }
        if payload.get("checkpoint_status") == "consumed":
            raise SafetyError("Consumed T430 handoff authorization cannot be resumed or replayed")
        if set(payload) != required or payload.get("version") != cls.VERSION or payload.get("checkpoint_status") != "pending":
            raise SafetyError("T430 checkpoint payload has an unsupported schema/version")
        try:
            stage = T430HandoffStage(payload["stage"])
        except (ValueError, TypeError) as exc:
            raise SafetyError("T430 checkpoint stage is invalid") from exc
        if stage not in cls.RESUMABLE_STAGES:
            raise SafetyError("Terminal or non-handoff T430 records cannot be resumed")
        return payload

    @classmethod
    def _read_verified_payload(cls, path: str | Path) -> dict[str, object]:
        target = Path(path)
        if target.is_symlink() or not target.is_file():
            raise SafetyError("T430 checkpoint must be a regular, non-symlink file")
        try:
            envelope = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SafetyError("T430 checkpoint is unreadable or malformed") from exc
        if not isinstance(envelope, dict) or set(envelope) != {"payload", "sha256"}:
            raise SafetyError("T430 checkpoint envelope has an unsupported schema")
        payload = envelope["payload"]
        digest = envelope["sha256"]
        if not isinstance(payload, dict) or not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise SafetyError("T430 checkpoint envelope is invalid")
        actual = hashlib.sha256(cls._canonical(payload)).hexdigest()
        if actual != digest:
            raise SafetyError("T430 checkpoint checksum mismatch; refusing to resume")
        return payload

    @classmethod
    def _archive_existing_checkpoint(cls, path: str | Path) -> None:
        target = Path(path)
        if not target.exists() and not target.is_symlink():
            return
        payload = cls._read_verified_payload(target)
        if payload.get("checkpoint_status") != "consumed":
            raise SafetyError("An existing pending T430 checkpoint must be resumed or consumed before replacement")
        operation_id = payload.get("operation_id")
        result_code = payload.get("result_code")
        if not isinstance(operation_id, str) or not isinstance(result_code, str):
            raise SafetyError("Consumed T430 checkpoint is missing archival identifiers")
        history = target.with_name(f"{target.name}.{operation_id}.{result_code}.consumed")
        if history.exists() or history.is_symlink():
            raise SafetyError("A consumed T430 checkpoint with these identifiers already exists")
        os.replace(target, history)

    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    @staticmethod
    def _preflight_record(evidence: T430PreflightEvidence) -> dict[str, object]:
        record = asdict(evidence)
        record["attested_at"] = evidence.attested_at.isoformat()
        return record

    @staticmethod
    def _media_record(evidence: T430MediaEvidence) -> dict[str, object]:
        record = asdict(evidence)
        record["selected_at"] = evidence.selected_at.isoformat()
        return record


def _parse_utc_timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise SafetyError(f"T430 checkpoint {field_name} timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SafetyError(f"T430 checkpoint {field_name} timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise SafetyError(f"T430 checkpoint {field_name} timestamp must be UTC")
    return parsed


def assess_t430_handoff(
    device: DeviceEvidence,
    *,
    validator: CompatibilityValidator | None = None,
) -> T430Assessment:
    """Fail closed on identity/vendor/BIOS evidence using pinned V1 policy."""
    policy = validator or CompatibilityValidator()
    identity = policy.validate_t430_handoff_identity(device)
    if identity.decision is CompatibilityDecision.BLOCKED:
        return T430Assessment(T430HandoffStage.BLOCKED, identity.decision, None, identity.reasons)
    if identity.decision is CompatibilityDecision.UNKNOWN:
        return T430Assessment(T430HandoffStage.UNKNOWN, identity.decision, None, identity.reasons)

    if device.stock_lenovo_bios is False:
        return T430Assessment(
            T430HandoffStage.BLOCKED,
            CompatibilityDecision.BLOCKED,
            None,
            ("T430 IVprep/1vyrain handoff requires observed stock Lenovo BIOS evidence",),
        )
    if device.stock_lenovo_bios is None:
        return T430Assessment(
            T430HandoffStage.UNKNOWN,
            CompatibilityDecision.UNKNOWN,
            None,
            ("stock Lenovo BIOS evidence is missing",),
        )

    vendor = (device.raw_bios_vendor or "").strip().casefold()
    if not vendor:
        return T430Assessment(
            T430HandoffStage.UNKNOWN,
            CompatibilityDecision.UNKNOWN,
            None,
            ("raw BIOS vendor evidence is missing",),
        )
    if "lenovo" not in vendor and "ibm" not in vendor:
        return T430Assessment(
            T430HandoffStage.BLOCKED,
            CompatibilityDecision.BLOCKED,
            None,
            ("raw BIOS vendor is not Lenovo/IBM",),
        )

    numeric_bios = CompatibilityValidator.parse_t430_upstream_bios_numeric(device.raw_bios_version)
    if numeric_bios is None:
        return T430Assessment(
            T430HandoffStage.UNKNOWN,
            CompatibilityDecision.UNKNOWN,
            None,
            ("raw BIOS version is missing, malformed, ambiguous, or not accepted by pinned upstream parsing",),
        )
    if numeric_bios >= 265:
        return T430Assessment(
            T430HandoffStage.IVPREP_REQUIRED,
            CompatibilityDecision.BLOCKED,
            numeric_bios,
            ("pinned upstream T430 compatibility threshold is numeric BIOS < 265",),
        )
    return T430Assessment(
        T430HandoffStage.ONEVYRAIN_READY,
        CompatibilityDecision.ALLOWED,
        numeric_bios,
        ("T430 identity and pinned upstream BIOS threshold verified; external handoff still requires approval",),
    )


class T430HandoffSession:
    """Enforce approval and fresh post-reboot evidence around manual handoffs."""

    def __init__(
        self,
        capture: T430DetectionCapture,
        *,
        validator: CompatibilityValidator | None = None,
        checkpoint_path: str | Path | None = None,
        operation_id: str | None = None,
    ) -> None:
        self.validator = validator or CompatibilityValidator()
        self.stage = T430HandoffStage.UNKNOWN
        self.assessment = assess_t430_handoff(capture.device, validator=self.validator)
        self.stage = self.assessment.stage
        self._device = capture.device
        self._capture_id = self._require_capture_id(capture.capture_id)
        self._observed_at = self._require_utc(capture.observed_at)
        self.operation_id = operation_id or uuid4().hex
        if not re.fullmatch(r"[0-9a-f]{32}", self.operation_id):
            raise ValueError("T430 handoff operation ID must be a lowercase UUID hex value")
        self.checkpoint_path = Path(checkpoint_path) if checkpoint_path is not None else None
        self._used_capture_ids = {self._capture_id}
        self._device_fingerprint_sha256 = capture.device_fingerprint_sha256
        self._handoff_approved = False
        self._approved_actor: str | None = None
        self._approval_timestamp: datetime | None = None
        self._preflight: T430PreflightEvidence | None = None
        self._media: T430MediaEvidence | None = None
        if not self._device_fingerprint_sha256:
            self.assessment = T430Assessment(
                T430HandoffStage.UNKNOWN,
                CompatibilityDecision.UNKNOWN,
                self.assessment.numeric_bios,
                ("stable T430 device fingerprint is unavailable",),
            )
            self.stage = T430HandoffStage.UNKNOWN

    @staticmethod
    def _require_capture_id(capture_id: str) -> str:
        clean = capture_id.strip()
        if not clean:
            raise ValueError("A unique detection capture ID is required")
        return clean

    @staticmethod
    def _require_utc(observed_at: datetime) -> datetime:
        if observed_at.tzinfo is None or observed_at.utcoffset() != timezone.utc.utcoffset(observed_at):
            raise ValueError("Detection timestamps must be timezone-aware UTC")
        return observed_at

    def _check_fresh_capture(self, capture: T430DetectionCapture) -> str:
        new_capture_id = self._require_capture_id(capture.capture_id)
        timestamp = self._require_utc(capture.observed_at)
        if new_capture_id in self._used_capture_ids:
            raise SafetyError("Stale detection capture cannot resume a T430 handoff")
        if timestamp <= self._observed_at:
            raise SafetyError("Post-reboot evidence must be captured after the external handoff")
        if self._approval_timestamp is not None and timestamp <= self._approval_timestamp:
            raise SafetyError("Post-reboot detection must be strictly later than handoff approval")
        if capture.device_fingerprint_sha256 != self._device_fingerprint_sha256:
            raise SafetyError("Re-detected device fingerprint differs from the approved T430 device")
        return new_capture_id

    def record_preflight(self, evidence: T430PreflightEvidence) -> None:
        if self.stage not in {T430HandoffStage.IVPREP_REQUIRED, T430HandoffStage.ONEVYRAIN_READY}:
            raise BIOSReversionError("T430 preflight is only accepted at a validated handoff stage")
        if evidence.capture_id != self._capture_id:
            raise SafetyError("Preflight must refer to the current fresh detection capture")
        if evidence.device_fingerprint_sha256 != self._device_fingerprint_sha256:
            raise SafetyError("Preflight device fingerprint does not match the detected T430")
        if not evidence.attested_by.strip():
            raise SafetyError("Preflight requires a named human attestation")
        attested_at = self._require_utc(evidence.attested_at)
        if attested_at < self._observed_at:
            raise SafetyError("Preflight attestation cannot predate its device detection")
        required = {
            "BIOS passwords removed": evidence.bios_passwords_removed,
            "end-user BIOS update enabled": evidence.end_user_bios_update_enabled,
            "Secure RollBack Prevention disabled": evidence.secure_rollback_prevention_disabled,
            "AC power connected": evidence.ac_connected,
            "battery percentage observed": evidence.battery_percent is not None,
        }
        require_uefi = self.stage is T430HandoffStage.ONEVYRAIN_READY
        if require_uefi:
            required["UEFI boot confirmed"] = evidence.uefi_booted
            required["Secure Boot disabled"] = evidence.secure_boot_disabled
            required["will boot 1vyrain from direct onboard USB without dock/ultrabase/hub"] = (
                evidence.will_boot_1vyrain_from_direct_onboard_usb_without_dock_or_hub
            )
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise SafetyError("Preflight evidence is unknown: " + ", ".join(missing))
        failed = [name for name, value in required.items() if value is False]
        if failed:
            raise SafetyError("Preflight check failed: " + ", ".join(failed))
        if evidence.battery_percent is None or not 0 <= evidence.battery_percent <= 100:
            raise SafetyError("Observed battery percentage must be from 0 to 100")
        if evidence.battery_percent < 30:
            raise SafetyError("Battery level is below the Phase 05 30% minimum")
        self._preflight = evidence

    def record_onevyrain_media(self, evidence: T430MediaEvidence) -> None:
        if self.stage is not T430HandoffStage.ONEVYRAIN_READY:
            raise BIOSReversionError("External media evidence is only accepted before the T430 1vyrain handoff")
        if self._preflight is None or self._preflight.uefi_booted is not True:
            raise SafetyError("Fresh passing UEFI preflight is required before recording 1vyrain media")
        if self._preflight.secure_boot_disabled is not True:
            raise SafetyError("1vyrain requires current-capture human attestation that Secure Boot is disabled")
        if self._preflight.will_boot_1vyrain_from_direct_onboard_usb_without_dock_or_hub is not True:
            raise SafetyError("1vyrain requires current-capture attestation to use direct onboard USB without dock or hub")
        if evidence.capture_id != self._capture_id:
            raise SafetyError("Selected image/media must be tied to the current T430 detection capture")
        if evidence.device_fingerprint_sha256 != self._device_fingerprint_sha256:
            raise SafetyError("Selected image/media belongs to a different device fingerprint")
        if evidence.selected_by.strip() != self._preflight.attested_by.strip():
            raise SafetyError("Media must be selected by the human who attested the current preflight")
        selected_at = self._require_utc(evidence.selected_at)
        if selected_at < self._observed_at or selected_at < self._preflight.attested_at:
            raise SafetyError("Image/media selection must follow the current detection and preflight")
        if evidence.image_source_url != ONEVYRAIN_REV5_MEDIA_URL:
            raise SafetyError("1vyrain media source must be the pinned upstream Rev 5 link")
        if evidence.user_reported_image_md5 is None:
            raise SafetyError("User-reported Rev 5 MD5 is required; it is only a weak legacy checksum")
        if evidence.user_reported_image_md5.casefold() != ONEVYRAIN_REV5_MD5:
            raise SafetyError("User-reported image MD5 does not match the pinned README's Rev 5 value")
        if evidence.written_in_dd_mode is not True:
            raise SafetyError("1vyrain requires an explicit human attestation that media was written in DD mode")
        self._media = evidence

    def approve_ivprep_handoff(
        self, *, actor: str, explicit_approval: bool, approved_at: datetime
    ) -> IVprepHandoffDescriptor:
        if self.stage is not T430HandoffStage.IVPREP_REQUIRED:
            raise BIOSReversionError("IVprep handoff is not the current validated T430 stage")
        if not actor.strip() or not explicit_approval:
            raise SafetyError("Explicit human approval is required before IVprep handoff")
        preflight = self._require_preflight(actor, require_uefi=False)
        approval_time = self._validate_approval_time(approved_at, preflight)
        descriptor = IVPrepManager("t430", IVPREP_TARGET_BIOS).create_handoff_descriptor(
            actor=actor,
            explicit_approval=explicit_approval,
            approved_at=approval_time,
        )
        self._handoff_approved = True
        self._approved_actor = actor.strip()
        self._approval_timestamp = approval_time
        self.stage = T430HandoffStage.AWAITING_IVPREP_REDETECTION
        self._persist_approved_handoff()
        return descriptor

    def record_ivprep_return(
        self,
        capture: T430DetectionCapture,
        *,
        reported_by: str,
        user_reported_handoff_complete: bool,
    ) -> T430Assessment:
        if self.stage is not T430HandoffStage.AWAITING_IVPREP_REDETECTION or not self._handoff_approved:
            raise BIOSReversionError("No approved IVprep handoff is awaiting re-detection")
        new_capture_id = self._check_fresh_capture(capture)
        if not reported_by.strip() or reported_by.strip() != self._approved_actor:
            raise SafetyError("IVprep return evidence must be reported by the approving human")
        if not user_reported_handoff_complete:
            raise SafetyError("Record the external IVprep handoff result before re-detection")
        assessment = assess_t430_handoff(capture.device, validator=self.validator)
        T430HandoffCheckpointStore.consume(
            self,
            result_code=("ivprep-compatible" if assessment.stage is T430HandoffStage.ONEVYRAIN_READY else "ivprep-recheck-required"),
            return_capture=capture,
            reported_by=reported_by.strip(),
            reported_evidence={
                "user_reported_handoff_complete": user_reported_handoff_complete,
                "assessment_stage": assessment.stage.value,
                "decision": assessment.decision.value,
                "numeric_bios": assessment.numeric_bios,
            },
        )
        if assessment.stage is not T430HandoffStage.ONEVYRAIN_READY:
            self.stage = assessment.stage
            self.assessment = assessment
            self._used_capture_ids.add(new_capture_id)
            self._capture_id = new_capture_id
            self._device = capture.device
            self._observed_at = capture.observed_at
            self._handoff_approved = False
            self._approved_actor = None
            self._approval_timestamp = None
            self._preflight = None
            self._media = None
            raise BIOSReversionError(
                "Post-IVprep evidence does not establish a compatible T430 BIOS: "
                + "; ".join(assessment.reasons)
            )
        self._used_capture_ids.add(new_capture_id)
        self._capture_id = new_capture_id
        self._device = capture.device
        self._observed_at = capture.observed_at
        self.assessment = assessment
        self.stage = T430HandoffStage.ONEVYRAIN_READY
        self._handoff_approved = False
        self._approved_actor = None
        self._approval_timestamp = None
        self._preflight = None
        self._media = None
        return assessment

    def approve_onevyrain_handoff(
        self, *, actor: str, explicit_approval: bool, approved_at: datetime
    ) -> OnevyRainHandoffDescriptor:
        if self.stage is not T430HandoffStage.ONEVYRAIN_READY:
            raise BIOSReversionError("1vyrain handoff is not the current validated T430 stage")
        if not actor.strip() or not explicit_approval:
            raise SafetyError("Explicit human approval is required before 1vyrain handoff")
        preflight = self._require_preflight(actor, require_uefi=True)
        if preflight.secure_boot_disabled is not True:
            raise SafetyError("1vyrain approval requires explicit current-capture Secure Boot disabled attestation")
        if preflight.will_boot_1vyrain_from_direct_onboard_usb_without_dock_or_hub is not True:
            raise SafetyError("1vyrain approval requires explicit direct onboard USB/no dock or hub attestation")
        media = self._media
        if media is None:
            raise SafetyError("User-reported image digests/size, DD-mode attestation and external media identity are required")
        if media.capture_id != self._capture_id or media.device_fingerprint_sha256 != self._device_fingerprint_sha256:
            raise SafetyError("Image/media evidence is stale or belongs to a different T430")
        if media.selected_by.strip() != actor.strip():
            raise SafetyError("1vyrain approver must be the human who selected the image/media")
        if media.user_reported_image_md5 is None or media.user_reported_image_md5.casefold() != ONEVYRAIN_REV5_MD5:
            raise SafetyError("1vyrain approval requires the user-reported Rev 5 MD5 to match the pinned README")
        if media.written_in_dd_mode is not True:
            raise SafetyError("1vyrain approval requires explicit DD-mode media-write attestation")
        approval_time = self._validate_approval_time(approved_at, preflight)
        if approval_time <= media.selected_at:
            raise SafetyError("1vyrain approval must follow image/media selection")
        self._handoff_approved = True
        self._approved_actor = actor.strip()
        self._approval_timestamp = approval_time
        self.stage = T430HandoffStage.AWAITING_POST_INSTALL_REDETECTION
        self._persist_approved_handoff()
        return OnevyRainHandoffDescriptor(
            model="t430",
            repository_url=ONEVYRAIN_REPOSITORY,
            revision=ONEVYRAIN_REVISION,
            tree=ONEVYRAIN_TREE,
            readme_url=f"{ONEVYRAIN_REPOSITORY}/blob/{ONEVYRAIN_REVISION}/README.md",
            start_script_url=f"{ONEVYRAIN_REPOSITORY}/blob/{ONEVYRAIN_REVISION}/start.sh",
            readme_sha256=ONEVYRAIN_README_SHA256,
            start_script_sha256=ONEVYRAIN_START_SHA256,
            approved_by=actor.strip(),
            approved_at=approval_time,
            user_reported_image_sha256=media.user_reported_image_sha256,
            image_size_bytes=media.image_size_bytes,
            user_reported_image_source_url=media.image_source_url,
            user_reported_media_fingerprint_sha256=media.user_reported_media_fingerprint_sha256,
            user_reported_image_md5=media.user_reported_image_md5,
        )

    def record_post_install_return(
        self,
        capture: T430DetectionCapture,
        *,
        reported_by: str,
        user_reported_handoff_complete: bool,
        user_reported_successful_reboot: bool,
        user_reported_boot_test_passed: bool,
        user_reported_1vyrain_validation_passed: bool,
    ) -> T430PostInstallReport:
        if self.stage is not T430HandoffStage.AWAITING_POST_INSTALL_REDETECTION or not self._handoff_approved:
            raise BIOSReversionError("No approved 1vyrain handoff is awaiting post-install re-detection")
        new_capture_id = self._check_fresh_capture(capture)
        if not reported_by.strip() or reported_by.strip() != self._approved_actor:
            raise SafetyError("Post-install evidence must be reported by the approving human")
        if not user_reported_handoff_complete or not user_reported_successful_reboot:
            raise SafetyError("Explicit handoff and successful reboot evidence are required")
        device = capture.device
        identity = self.validator.validate_t430_handoff_identity(device)
        if identity.decision is not CompatibilityDecision.ALLOWED:
            rejected_stage = (
                T430HandoffStage.BLOCKED
                if identity.decision is CompatibilityDecision.BLOCKED
                else T430HandoffStage.UNKNOWN
            )
            rejected_assessment = T430Assessment(rejected_stage, identity.decision, None, identity.reasons)
            T430HandoffCheckpointStore.consume(
                self,
                result_code="1vyrain-return-identity-not-verified",
                return_capture=capture,
                reported_by=reported_by.strip(),
                reported_evidence={"identity_decision": identity.decision.value, "reasons": list(identity.reasons)},
            )
            self._used_capture_ids.add(new_capture_id)
            self._capture_id = new_capture_id
            self._device = device
            self._observed_at = capture.observed_at
            self.stage = rejected_stage
            self.assessment = rejected_assessment
            self._handoff_approved = False
            self._approved_actor = None
            self._approval_timestamp = None
            self._preflight = None
            self._media = None
            raise BIOSReversionError("Post-install DMI identity is not verified: " + "; ".join(identity.reasons))
        if not device.raw_bios_version or not device.raw_bios_version.strip():
            T430HandoffCheckpointStore.consume(
                self,
                result_code="1vyrain-return-bios-unknown",
                return_capture=capture,
                reported_by=reported_by.strip(),
                reported_evidence={"identity_decision": identity.decision.value, "raw_bios_version": device.raw_bios_version},
            )
            self._used_capture_ids.add(new_capture_id)
            self._capture_id = new_capture_id
            self._device = device
            self._observed_at = capture.observed_at
            self.stage = T430HandoffStage.UNKNOWN
            self.assessment = T430Assessment(self.stage, CompatibilityDecision.UNKNOWN, None, ("post-install BIOS evidence is missing",))
            self._handoff_approved = False
            self._approved_actor = None
            self._approval_timestamp = None
            self._preflight = None
            self._media = None
            raise BIOSReversionError("Post-install BIOS evidence is missing")
        if not user_reported_boot_test_passed or not user_reported_1vyrain_validation_passed:
            raise BIOSReversionError("Post-install boot and 1vyrain validation evidence are incomplete")
        preflight = self._preflight
        media = self._media
        approval_time = self._approval_timestamp
        if preflight is None or media is None or approval_time is None:
            raise SafetyError("Preflight, media, and approval evidence are required for the post-install record")
        T430HandoffCheckpointStore.consume(
            self,
            result_code="1vyrain-reported-validation-pending-acceptance",
            return_capture=capture,
            reported_by=reported_by.strip(),
            reported_evidence={
                "user_reported_handoff_complete": user_reported_handoff_complete,
                "user_reported_successful_reboot": user_reported_successful_reboot,
                "user_reported_boot_test_passed": user_reported_boot_test_passed,
                "user_reported_1vyrain_validation_passed": user_reported_1vyrain_validation_passed,
            },
        )
        self._used_capture_ids.add(new_capture_id)
        self._capture_id = new_capture_id
        self._device = device
        self._observed_at = capture.observed_at
        self.stage = T430HandoffStage.POST_INSTALL_EVIDENCE_PENDING_ACCEPTANCE
        self._handoff_approved = False
        self._approved_actor = None
        return T430PostInstallReport(
            capture_id=new_capture_id,
            observed_at=capture.observed_at,
            raw_device_evidence=device,
            reported_by=reported_by.strip(),
            device_fingerprint_sha256=self._device_fingerprint_sha256 or "",
            onevy_approved_at=approval_time,
            preflight=preflight,
            media=media,
            user_reported_successful_reboot=user_reported_successful_reboot,
            user_reported_boot_test_passed=user_reported_boot_test_passed,
            user_reported_1vyrain_validation_passed=user_reported_1vyrain_validation_passed,
        )

    def _require_preflight(self, actor: str, *, require_uefi: bool) -> T430PreflightEvidence:
        evidence = self._preflight
        if evidence is None or evidence.capture_id != self._capture_id:
            raise SafetyError("Fresh human-attested preflight for the current detection is required")
        if evidence.device_fingerprint_sha256 != self._device_fingerprint_sha256:
            raise SafetyError("Preflight device fingerprint does not match the current T430")
        if evidence.attested_by.strip() != actor.strip():
            raise SafetyError("Handoff approver must be the human who attested the current preflight")
        if require_uefi and evidence.uefi_booted is not True:
            raise SafetyError("1vyrain requires human-attested UEFI boot confirmation")
        if require_uefi and evidence.secure_boot_disabled is not True:
            raise SafetyError("1vyrain requires human-attested Secure Boot disabled confirmation")
        if require_uefi and evidence.will_boot_1vyrain_from_direct_onboard_usb_without_dock_or_hub is not True:
            raise SafetyError("1vyrain requires direct onboard USB boot without dock or hub")
        return evidence

    def _validate_approval_time(
        self, approved_at: datetime, preflight: T430PreflightEvidence
    ) -> datetime:
        approval_time = self._require_utc(approved_at)
        if approval_time <= self._observed_at or approval_time <= preflight.attested_at:
            raise SafetyError("Human approval timestamp must follow the current detection and preflight")
        return approval_time

    def _persist_approved_handoff(self) -> None:
        if self.checkpoint_path is None:
            self._handoff_approved = False
            self._approved_actor = None
            self._approval_timestamp = None
            self.stage = self.assessment.stage
            raise SafetyError("An explicit durable checkpoint path is required before external handoff approval")
        try:
            T430HandoffCheckpointStore.save(self, self.checkpoint_path)
        except Exception as exc:
            self._handoff_approved = False
            self._approved_actor = None
            self._approval_timestamp = None
            self.stage = self.assessment.stage
            raise SafetyError("Could not durably save T430 approval; handoff descriptor was not issued") from exc

    @classmethod
    def resume_from_checkpoint(
        cls,
        path: str | Path,
        *,
        validator: CompatibilityValidator | None = None,
    ) -> T430HandoffSession:
        """Restore only an approved pending external handoff, never a writer state."""
        payload = T430HandoffCheckpointStore.load(path)
        capture_record = payload.get("capture")
        if not isinstance(capture_record, dict) or set(capture_record) != {
            "capture_id", "observed_at", "device", "device_fingerprint_sha256"
        }:
            raise SafetyError("T430 checkpoint capture evidence is invalid")
        device_record = capture_record.get("device")
        device_fields = {item.name for item in fields(DeviceEvidence)}
        if not isinstance(device_record, dict) or set(device_record) != device_fields:
            raise SafetyError("T430 checkpoint raw device evidence is invalid")
        try:
            device = DeviceEvidence(**device_record)
            capture_id = capture_record["capture_id"]
            fingerprint = capture_record["device_fingerprint_sha256"]
            if not isinstance(capture_id, str) or not isinstance(fingerprint, str):
                raise ValueError("capture ID or fingerprint is not text")
            capture = T430DetectionCapture(
                capture_id=capture_id,
                observed_at=_parse_utc_timestamp(capture_record["observed_at"], "detection"),
                device=device,
                device_fingerprint_sha256=fingerprint,
            )
        except (TypeError, ValueError) as exc:
            raise SafetyError("T430 checkpoint detection evidence is invalid") from exc
        operation_id = payload.get("operation_id")
        if not isinstance(operation_id, str):
            raise SafetyError("T430 checkpoint operation ID is invalid")
        session = cls(
            capture,
            validator=validator,
            checkpoint_path=path,
            operation_id=operation_id,
        )
        try:
            saved_stage = T430HandoffStage(payload["stage"])
            expected_stage = (
                T430HandoffStage.AWAITING_IVPREP_REDETECTION
                if session.assessment.stage is T430HandoffStage.IVPREP_REQUIRED
                else T430HandoffStage.AWAITING_POST_INSTALL_REDETECTION
                if session.assessment.stage is T430HandoffStage.ONEVYRAIN_READY
                else None
            )
            if saved_stage is not expected_stage:
                raise SafetyError("Saved handoff does not match the revalidated T430 BIOS and identity evidence")
            preflight_record = payload.get("preflight")
            if not isinstance(preflight_record, dict) or set(preflight_record) != {
                item.name for item in fields(T430PreflightEvidence)
            }:
                raise SafetyError("T430 checkpoint preflight evidence is invalid")
            parsed_preflight = dict(preflight_record)
            parsed_preflight["attested_at"] = _parse_utc_timestamp(parsed_preflight.get("attested_at"), "preflight")
            preflight_evidence = T430PreflightEvidence(**parsed_preflight)
            session.record_preflight(preflight_evidence)

            media_record = payload.get("media")
            if saved_stage is T430HandoffStage.AWAITING_POST_INSTALL_REDETECTION:
                if not isinstance(media_record, dict) or set(media_record) != {
                    item.name for item in fields(T430MediaEvidence)
                }:
                    raise SafetyError("T430 checkpoint image/media evidence is invalid")
                parsed_media = dict(media_record)
                parsed_media["selected_at"] = _parse_utc_timestamp(parsed_media.get("selected_at"), "media selection")
                session.record_onevyrain_media(T430MediaEvidence(**parsed_media))
            elif media_record is not None:
                raise SafetyError("IVprep checkpoint must not contain 1vyrain image/media evidence")

            actor = payload.get("approved_by")
            if not isinstance(actor, str) or not actor.strip():
                raise SafetyError("T430 checkpoint approval actor is invalid")
            approved_at = _parse_utc_timestamp(payload.get("approved_at"), "approval")
            session._validate_approval_time(approved_at, preflight_evidence)
            if actor.strip() != preflight_evidence.attested_by.strip():
                raise SafetyError("T430 checkpoint approver differs from the preflight attestor")
            if saved_stage is T430HandoffStage.AWAITING_POST_INSTALL_REDETECTION:
                if session._media is None or session._media.selected_by.strip() != actor.strip():
                    raise SafetyError("T430 checkpoint approver differs from the media selector")
                if approved_at <= session._media.selected_at:
                    raise SafetyError("T430 checkpoint approval predates image/media selection")
            session.stage = saved_stage
            session._handoff_approved = True
            session._approved_actor = actor.strip()
            session._approval_timestamp = approved_at
        except (KeyError, TypeError, ValueError) as exc:
            raise SafetyError("T430 checkpoint evidence could not be validated") from exc
        return session
