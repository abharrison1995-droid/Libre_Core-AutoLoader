"""Append-only, pending human post-reboot observations.

This module never probes SPI or upgrades a write/handoff checkpoint. It only
reopens an already terminal operation record, compares a fresh local identity
capture to that record, and writes one durable sidecar whose status is always
``PENDING_HUMAN_REVIEW``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from autoloader.exceptions import SafetyError
from autoloader.detection.bios import BIOSDetector
from autoloader.detection.model import ModelDetector
from autoloader.reversion.t430_handoff import (
    ONEVYRAIN_OPTION,
    T430DetectionCapture,
    T430HandoffCheckpointStore,
)
from autoloader.workflow.journal import JournalError, OperationJournal
from autoloader.workflow.types import OperationState
from autoloader.workflow.write_approval import _directory_fsync_supported, _sync_directory

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAC = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$")
_COMMON_CHECKS = ("cold_boot", "display", "keyboard", "pointing_device", "storage", "sleep_resume")
_MAX_REPORT_AGE = timedelta(hours=24)
_MAX_FUTURE_SKEW = timedelta(seconds=30)


class PostRebootEvidenceError(ValueError):
    """A terminal checkpoint or human observation cannot be safely recorded."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _parse_utc(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise PostRebootEvidenceError(f"{label} must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PostRebootEvidenceError(f"{label} must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise PostRebootEvidenceError(f"{label} must include a UTC timezone")
    return parsed.astimezone(timezone.utc)


def _safe_regular_file(path: Path) -> Path:
    candidate = Path(os.path.abspath(path.expanduser()))
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except OSError as exc:
            raise PostRebootEvidenceError(f"checkpoint path is missing or inaccessible: {current}") from exc
        if stat.S_ISLNK(mode):
            raise PostRebootEvidenceError(f"checkpoint path contains a symlink: {current}")
        if current != candidate and not stat.S_ISDIR(mode):
            raise PostRebootEvidenceError(f"checkpoint path has a non-directory parent: {current}")
        if current == candidate and not stat.S_ISREG(mode):
            raise PostRebootEvidenceError("checkpoint must be a regular non-symlink file")
    return candidate


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _parse_json_object(text: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PostRebootEvidenceError(f"{label} is malformed JSON") from exc
    if not isinstance(value, dict):
        raise PostRebootEvidenceError(f"{label} must be a JSON object")
    return value


def _usable_unicast_mac(value: str) -> bool:
    try:
        octets = bytes.fromhex(value.replace(":", ""))
    except ValueError:
        return False
    return len(octets) == 6 and octets not in (b"\x00" * 6, b"\xff" * 6) and not (octets[0] & 1)


def _validate_observations(
    profile_id: str,
    raw: object,
    *,
    checkpoint_time: datetime,
    capture_time: datetime,
    now: datetime,
    expected_x200_mac: str | None,
    expected_reboot_evidence_ref: str | None = None,
    expected_reboot_observed_at_utc: str | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PostRebootEvidenceError("post-reboot request must be a JSON object")
    common = {
        "schema_version", "operator", "boot_session_id", "boot_session_unknown_reason",
        "human_reboot_attested", "human_reboot_evidence_ref", "human_reboot_observed_at_utc", "checks",
    }
    if set(raw) != common or raw.get("schema_version") != 1:
        raise PostRebootEvidenceError("post-reboot request has an unsupported or incomplete schema")
    operator = raw.get("operator")
    if not isinstance(operator, str) or not operator.strip() or len(operator.strip()) > 120:
        raise PostRebootEvidenceError("a named operator is required")
    boot_session = raw.get("boot_session_id")
    unknown_reason = raw.get("boot_session_unknown_reason")
    if boot_session is not None:
        if not isinstance(boot_session, str) or not boot_session.strip() or len(boot_session.strip()) > 160:
            raise PostRebootEvidenceError("boot session ID must be non-empty text or null")
        if unknown_reason is not None:
            raise PostRebootEvidenceError("boot session unknown reason must be null when a session ID is supplied")
    elif not isinstance(unknown_reason, str) or not unknown_reason.strip():
        raise PostRebootEvidenceError("when boot session ID is unavailable, explain why")
    if raw.get("human_reboot_attested") is not True:
        raise PostRebootEvidenceError("operator must explicitly attest a post-write/return reboot")
    reboot_ref = _required_ref(raw.get("human_reboot_evidence_ref"), "human reboot")
    reboot_time = _parse_utc(raw.get("human_reboot_observed_at_utc"), "human reboot observation time")
    if expected_reboot_evidence_ref is not None and reboot_ref != expected_reboot_evidence_ref:
        raise PostRebootEvidenceError("T430 reboot evidence reference must match the consumed return checkpoint")
    if expected_reboot_observed_at_utc is not None and reboot_time != _parse_utc(expected_reboot_observed_at_utc, "T430 checkpoint reboot time"):
        raise PostRebootEvidenceError("T430 reboot time must match the consumed return checkpoint")
    if reboot_time <= checkpoint_time or reboot_time > now + _MAX_FUTURE_SKEW:
        raise PostRebootEvidenceError("human reboot observation must follow the operation and not be future-dated")
    if reboot_time > capture_time + _MAX_FUTURE_SKEW:
        raise PostRebootEvidenceError("human reboot observation is later than the fresh DMI/BIOS capture")
    if now - reboot_time > _MAX_REPORT_AGE:
        raise PostRebootEvidenceError("human reboot observation is stale")

    required_checks = set(_COMMON_CHECKS)
    if profile_id == "x200-libreboot":
        required_checks.add("ethernet_mac")
    elif profile_id == "x220-coreboot":
        required_checks.add("ethernet")
    elif profile_id == "t430-1vyrain":
        required_checks.update(("advanced_bios_menu", "option_1_default_lenovo_bios", "bios_identity"))
    checks = raw.get("checks")
    if not isinstance(checks, dict) or set(checks) != required_checks:
        raise PostRebootEvidenceError("post-reboot checks are incomplete or contain unsupported fields")

    normalized: dict[str, Any] = {}
    for name in sorted(required_checks):
        value = checks[name]
        if name == "ethernet_mac":
            if not isinstance(value, dict) or set(value) != {
                "observed_mac", "expected_mac", "matches_expected", "evidence_ref", "observed_at_utc"
            }:
                raise PostRebootEvidenceError("X200 Ethernet MAC observation is incomplete")
            observed_mac = value.get("observed_mac")
            expected_mac = value.get("expected_mac")
            if not isinstance(observed_mac, str) or not _MAC.fullmatch(observed_mac.lower()):
                raise PostRebootEvidenceError("X200 observed Ethernet MAC must be a colon-separated address")
            if not isinstance(expected_mac, str) or expected_mac.lower() != (expected_x200_mac or "").lower():
                raise PostRebootEvidenceError("X200 expected MAC does not match the verified write intent")
            if not _usable_unicast_mac(expected_mac):
                raise PostRebootEvidenceError("X200 expected MAC is not a usable unicast address")
            if not _usable_unicast_mac(observed_mac):
                raise PostRebootEvidenceError("X200 observed MAC is not a usable unicast address")
            if value.get("matches_expected") is not (observed_mac.lower() == expected_mac.lower()):
                raise PostRebootEvidenceError("X200 MAC match result contradicts the reported addresses")
            normalized[name] = {
                "observed_mac": observed_mac.lower(),
                "expected_mac": expected_mac.lower(),
                "matches_expected": value["matches_expected"],
                "evidence_ref": _required_ref(value.get("evidence_ref"), name),
                "observed_at_utc": value.get("observed_at_utc"),
            }
            timestamp_value = value.get("observed_at_utc")
        else:
            if not isinstance(value, dict) or set(value) != {"outcome", "evidence_ref", "observed_at_utc"}:
                raise PostRebootEvidenceError(f"{name} observation is incomplete")
            outcome = value.get("outcome")
            if outcome not in {"pass", "fail", "unknown"}:
                raise PostRebootEvidenceError(f"{name} outcome must be pass, fail, or unknown")
            normalized[name] = {
                "outcome": outcome,
                "evidence_ref": _required_ref(value.get("evidence_ref"), name),
                "observed_at_utc": value.get("observed_at_utc"),
            }
            timestamp_value = value.get("observed_at_utc")
        observed_at = _parse_utc(timestamp_value, f"{name} observation time")
        if observed_at <= checkpoint_time or observed_at > now + _MAX_FUTURE_SKEW:
            raise PostRebootEvidenceError(f"{name} observation must follow the operation and not be future-dated")
        if observed_at < reboot_time:
            raise PostRebootEvidenceError(f"{name} observation must follow the reported reboot")
        if now - observed_at > _MAX_REPORT_AGE:
            raise PostRebootEvidenceError(f"{name} observation is stale")
        if observed_at > capture_time + _MAX_FUTURE_SKEW:
            raise PostRebootEvidenceError(f"{name} observation is later than the fresh DMI/BIOS capture")
    return {
        "operator": operator.strip(),
        "boot_session_id": boot_session.strip() if isinstance(boot_session, str) else None,
        "boot_session_unknown_reason": unknown_reason.strip() if isinstance(unknown_reason, str) else None,
        "human_reboot_attested": True,
        "human_reboot_evidence_ref": reboot_ref,
        "human_reboot_observed_at_utc": reboot_time.isoformat(),
        "checks": normalized,
    }


def _required_ref(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 500:
        raise PostRebootEvidenceError(f"{label} requires a bounded evidence reference")
    return value.strip()


class PostRebootEvidenceService:
    """Validate a terminal checkpoint and append exactly one pending sidecar."""

    @classmethod
    def record(
        cls,
        checkpoint_path: str | Path,
        *,
        profile_id: str,
        request_document: str,
        model_detector: ModelDetector | None = None,
        bios_detector: BIOSDetector | None = None,
        now: datetime | None = None,
    ) -> tuple[Path, dict[str, Any]]:
        if not _directory_fsync_supported():
            raise PostRebootEvidenceError("durable post-reboot records require POSIX directory fsync support")
        if profile_id not in {"x200-libreboot", "x220-coreboot", "t430-1vyrain"}:
            raise PostRebootEvidenceError("an allowlisted V1 profile is required")
        path = _safe_regular_file(Path(checkpoint_path))
        before_hash, _ = _sha256_file(path)
        now_utc = now or datetime.now(timezone.utc)
        if now_utc.tzinfo is None or now_utc.utcoffset() != timedelta(0):
            raise PostRebootEvidenceError("current time must be timezone-aware UTC")
        # Keep capture acquisition inside the service boundary. Callers can
        # submit observations, but cannot supply/import a capture object and
        # have it labeled as locally detected evidence.
        capture = T430DetectionCapture.detect_now(
            model_detector=model_detector,
            bios_detector=bios_detector,
        )
        if type(capture) is not T430DetectionCapture or not capture.device_fingerprint_sha256:
            raise PostRebootEvidenceError("fresh local DMI/BIOS capture with serial fingerprint is required")
        try:
            UUID(capture.capture_id)
        except ValueError as exc:
            raise PostRebootEvidenceError("local DMI/BIOS capture ID must be a generated UUID") from exc
        capture_time = capture.observed_at.astimezone(timezone.utc)
        if capture_time > now_utc + _MAX_FUTURE_SKEW or now_utc - capture_time > _MAX_REPORT_AGE:
            raise PostRebootEvidenceError("fresh local DMI/BIOS capture is stale or future-dated")
        model = cls._capture_model(capture)
        expected_model = {"x200-libreboot": "x200", "x220-coreboot": "x220", "t430-1vyrain": "t430"}[profile_id]
        if model != expected_model:
            raise PostRebootEvidenceError("fresh local DMI/BIOS capture does not match the selected V1 profile")

        try:
            checkpoint = cls._open_checkpoint(profile_id, path)
        except PostRebootEvidenceError:
            raise
        except (JournalError, SafetyError, OSError, ValueError) as exc:
            raise PostRebootEvidenceError(f"terminal checkpoint validation failed: {exc}") from exc
        if capture.device_fingerprint_sha256 != checkpoint["device_fingerprint_sha256"]:
            raise PostRebootEvidenceError("fresh local device fingerprint does not match the terminal checkpoint")
        if capture.capture_id in checkpoint["prior_capture_ids"]:
            raise PostRebootEvidenceError("fresh local DMI/BIOS capture ID is already present in the terminal checkpoint")
        checkpoint_time = checkpoint["operation_time"]
        return_capture_time = checkpoint.get("return_capture_time")
        if isinstance(return_capture_time, datetime) and capture_time <= return_capture_time:
            raise PostRebootEvidenceError("fresh local DMI/BIOS capture must follow the T430 return capture")
        if capture_time <= checkpoint_time:
            raise PostRebootEvidenceError("fresh local DMI/BIOS capture must follow the completed write/return")
        raw = _parse_json_object(request_document, "post-reboot request")
        checks = _validate_observations(
            profile_id,
            raw,
            checkpoint_time=checkpoint_time,
            capture_time=capture_time,
            now=now_utc,
            expected_x200_mac=checkpoint.get("expected_x200_mac"),
            expected_reboot_evidence_ref=checkpoint.get("reboot_evidence_ref"),
            expected_reboot_observed_at_utc=checkpoint.get("reboot_observed_at_utc"),
        )
        after_hash, _ = _sha256_file(path)
        if after_hash != before_hash:
            raise PostRebootEvidenceError("terminal checkpoint changed while post-reboot evidence was being validated")

        operation_id = checkpoint["operation_id"]
        target = path.with_name(path.name + ".post-reboot-pending.json")
        lock = path.with_name(path.name + ".post-reboot.lock")
        payload = {
            "schema_version": 1,
            "report_status": "PENDING_HUMAN_REVIEW",
            "observation_semantics": "operator-reported observations; not authenticated measurements or hardware verification",
            "profile_id": profile_id,
            "operation_id": operation_id,
            "checkpoint_path": str(path),
            "checkpoint_sha256": before_hash,
            "checkpoint_operation_fingerprint": checkpoint["checkpoint_binding_sha256"],
            "device_fingerprint_sha256": capture.device_fingerprint_sha256,
            "local_capture": {
                "capture_id": capture.capture_id,
                "observed_at_utc": capture.observed_at.isoformat(),
                "source": "local-read-only-dmi-bios-detector",
                "raw_device_evidence": {
                    "raw_dmi_product_name": capture.device.raw_dmi_product_name,
                    "raw_dmi_product_version": capture.device.raw_dmi_product_version,
                    "raw_dmi_system_family": capture.device.raw_dmi_system_family,
                    "raw_mtm": capture.device.raw_mtm,
                    "raw_bios_version": capture.device.raw_bios_version,
                    "raw_bios_vendor": capture.device.raw_bios_vendor,
                },
            },
            "operation_completed_at_utc": checkpoint_time.isoformat(),
            "return_capture_at_utc": return_capture_time.isoformat() if isinstance(return_capture_time, datetime) else None,
            "recorded_at_utc": now_utc.isoformat(),
            **checks,
            "acceptance_status": "NOT_ASSESSED",
            "hardware_success_claimed": False,
        }
        digest = hashlib.sha256(_canonical(payload)).hexdigest()
        envelope = _canonical({"payload": payload, "sha256": digest}) + b"\n"
        final_checkpoint_hash, _ = _sha256_file(path)
        if final_checkpoint_hash != before_hash:
            raise PostRebootEvidenceError("terminal checkpoint changed before pending report publication")
        cls._write_once(target, lock, envelope)
        cls.verify_report(target, expected_checkpoint_hash=before_hash, expected_operation_id=operation_id)
        return target, payload

    @staticmethod
    def _capture_model(capture: T430DetectionCapture) -> str | None:
        text = " ".join((
            capture.device.raw_dmi_product_name or "",
            capture.device.raw_dmi_product_version or "",
            capture.device.raw_dmi_system_family or "",
            capture.device.raw_mtm or "",
        )).casefold()
        matches = [model for model in ("x200", "x220", "t430") if re.search(rf"(?<![a-z0-9]){model}(?![a-z0-9])", text)]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _mapping_model(value: object) -> str | None:
        if not isinstance(value, dict):
            return None
        text = " ".join(str(value.get(key) or "") for key in (
            "raw_dmi_product_name", "raw_dmi_product_version", "raw_dmi_system_family", "raw_mtm"
        )).casefold()
        matches = [model for model in ("x200", "x220", "t430") if re.search(rf"(?<![a-z0-9]){model}(?![a-z0-9])", text)]
        return matches[0] if len(matches) == 1 else None

    @classmethod
    def _open_checkpoint(cls, profile_id: str, path: Path) -> dict[str, Any]:
        if profile_id == "t430-1vyrain":
            payload = T430HandoffCheckpointStore.inspect_consumed_return(path)
            if payload.get("checkpoint_status") != "consumed" or payload.get("stage") != "consumed":
                raise PostRebootEvidenceError("T430 post-reboot evidence requires an already consumed return checkpoint")
            if payload.get("result_code") != "1vyrain-reported-validation-pending-acceptance":
                raise PostRebootEvidenceError("T430 checkpoint does not record the completed option-1 return report")
            return_capture = payload.get("return_capture")
            evidence = payload.get("return_evidence")
            initial_capture = payload.get("capture")
            if not isinstance(return_capture, dict) or not isinstance(evidence, dict) or not isinstance(initial_capture, dict):
                raise PostRebootEvidenceError("T430 consumed return evidence is incomplete")
            fingerprint = return_capture.get("device_fingerprint_sha256")
            if not isinstance(fingerprint, str) or not _SHA256.fullmatch(fingerprint):
                raise PostRebootEvidenceError("T430 return capture has no verified device fingerprint")
            if fingerprint != initial_capture.get("device_fingerprint_sha256"):
                raise PostRebootEvidenceError("T430 consumed return capture belongs to a different device")
            if cls._mapping_model(return_capture.get("device")) != "t430" or cls._mapping_model(initial_capture.get("device")) != "t430":
                raise PostRebootEvidenceError("T430 consumed checkpoint contains a different or ambiguous model identity")
            if evidence.get("selected_option") != ONEVYRAIN_OPTION:
                raise PostRebootEvidenceError("T430 return did not record observed upstream option 1")
            for field in (
                "selected_option_evidence_ref", "successful_reboot_evidence_ref", "advanced_bios_evidence_ref"
            ):
                _required_ref(evidence.get(field), f"T430 {field}")
            for field in (
                "user_reported_successful_reboot", "user_reported_boot_test_passed",
                "user_reported_1vyrain_validation_passed", "advanced_bios_menu_observed",
            ):
                if evidence.get(field) is not True:
                    raise PostRebootEvidenceError(f"T430 return checkpoint is missing {field}")
            return_time = _parse_utc(return_capture.get("observed_at"), "T430 return capture time")
            consumed_at = _parse_utc(payload.get("consumed_at_utc"), "T430 checkpoint consumption time")
            reboot_at = _parse_utc(evidence.get("successful_reboot_observed_at"), "T430 reported reboot time")
            advanced_at = _parse_utc(evidence.get("advanced_bios_observed_at"), "T430 Advanced BIOS observation time")
            option_at = _parse_utc(evidence.get("selected_option_observed_at"), "T430 option selection time")
            approved_at = _parse_utc(payload.get("approved_at"), "T430 approved handoff time")
            return_evidence_ref = _required_ref(evidence.get("successful_reboot_evidence_ref"), "T430 reboot evidence")
            if payload.get("reported_by") != payload.get("approved_by"):
                raise PostRebootEvidenceError("T430 return evidence reporter differs from the approved handoff actor")
            if consumed_at < return_time:
                raise PostRebootEvidenceError("T430 consumed checkpoint predates its return capture")
            if evidence.get("user_reported_handoff_complete") is not True:
                raise PostRebootEvidenceError("T430 consumed checkpoint lacks a completed handoff attestation")
            return_time = _parse_utc(return_capture.get("observed_at"), "T430 return capture time")
            if option_at <= approved_at or option_at > return_time + _MAX_FUTURE_SKEW:
                raise PostRebootEvidenceError("T430 option-1 observation is outside the approved handoff and return window")
            if reboot_at <= option_at or reboot_at > return_time + _MAX_FUTURE_SKEW:
                raise PostRebootEvidenceError("T430 reboot observation is outside the option-1 and return window")
            if advanced_at < reboot_at or advanced_at > return_time + _MAX_FUTURE_SKEW or return_time - advanced_at > timedelta(minutes=30):
                raise PostRebootEvidenceError("T430 Advanced BIOS observation is outside the reboot and return window")
            operation_id = payload.get("operation_id")
            try:
                UUID(str(operation_id))
            except ValueError as exc:
                raise PostRebootEvidenceError("T430 operation ID is invalid") from exc
            binding = hashlib.sha256(_canonical({
                "operation_id": operation_id,
                "checkpoint_sha256": _sha256_file(path)[0],
                "return_capture_id": return_capture.get("capture_id"),
                "return_fingerprint": fingerprint,
                "result_code": payload.get("result_code"),
            })).hexdigest()
            return {
                "operation_id": operation_id,
                "device_fingerprint_sha256": fingerprint,
                # The external handoff begins at approval. The consumed return
                # checkpoint separately requires the new local capture to be
                # after return; persistence time is not misrepresented as a
                # device operation or as evidence of another reboot.
                "operation_time": approved_at,
                "return_capture_time": return_time,
                "reboot_evidence_ref": return_evidence_ref,
                "reboot_observed_at_utc": reboot_at.isoformat(),
                "prior_capture_ids": (initial_capture.get("capture_id"), return_capture.get("capture_id")),
                "checkpoint_binding_sha256": binding,
            }

        journal = OperationJournal.inspect_checkpoint(path)
        if journal.state is not OperationState.WRITE_VERIFIED or len(journal.entries) < 2:
            raise PostRebootEvidenceError("X200/X220 post-reboot evidence requires a terminal WRITE_VERIFIED journal")
        write_started = [entry for entry in journal.entries if entry.to_state is OperationState.WRITE_STARTED]
        verified = [entry for entry in journal.entries if entry.to_state is OperationState.WRITE_VERIFIED]
        if len(write_started) != 1 or len(verified) != 1:
            raise PostRebootEvidenceError("terminal journal must contain exactly one write start and one verification")
        start, final = write_started[0], verified[0]
        start_data = _parse_json_object(start.message, "WRITE_STARTED evidence")
        final_data = _parse_json_object(final.message, "WRITE_VERIFIED evidence")
        if final.code != "WRITE_VERIFIED" or start.code != "WRITE_STARTED":
            raise PostRebootEvidenceError("terminal journal write transition codes are inconsistent")
        if start_data.get("operation_id") != journal.operation_id or final_data.get("operation_id") != journal.operation_id:
            raise PostRebootEvidenceError("write journal operation identity is incomplete")
        fingerprint = start_data.get("device_fingerprint_sha256")
        if not isinstance(fingerprint, str) or not _SHA256.fullmatch(fingerprint) or final_data.get("device_fingerprint_sha256") != fingerprint:
            raise PostRebootEvidenceError("write journal lacks a consistent serial-derived device fingerprint")
        intent_digest = start_data.get("intent_sha256")
        artifact_sha = start_data.get("artifact_sha256")
        readback_sha = final_data.get("readback_sha256")
        if not all(isinstance(item, str) and _SHA256.fullmatch(item) for item in (intent_digest, artifact_sha, readback_sha)):
            raise PostRebootEvidenceError("write journal digest binding is incomplete")
        if final_data.get("intent_sha256") != intent_digest or final_data.get("artifact_sha256") != artifact_sha:
            raise PostRebootEvidenceError("verified journal does not match the approved write intent")
        if final_data.get("comparison_status") != "verified" or final_data.get("boot_status") != "not-performed":
            raise PostRebootEvidenceError("journal lacks an exact verified full-chip comparison with boot still pending")
        metadata = journal.artifact_metadata
        if metadata is None:
            raise PostRebootEvidenceError("terminal journal lacks pinned artifact metadata")
        expected_model, expected_family = {
            "x200-libreboot": ("x200", "libreboot"),
            "x220-coreboot": ("x220", "coreboot"),
        }[profile_id]
        if metadata.model_id.casefold() != expected_model or metadata.firmware_family.casefold() != expected_family:
            raise PostRebootEvidenceError("terminal journal artifact does not match the selected X200/X220 profile")
        artifact_path = _safe_regular_file(Path(str(final_data.get("artifact_path", ""))))
        readback_path = _safe_regular_file(Path(str(final_data.get("readback_path", ""))))
        artifact_hash, artifact_size = _sha256_file(artifact_path)
        readback_hash, readback_size = _sha256_file(readback_path)
        if (
            artifact_hash != artifact_sha or readback_hash != readback_sha or artifact_hash != readback_hash
            or artifact_size != final_data.get("artifact_size_bytes")
            or readback_size != final_data.get("readback_size_bytes")
            or artifact_size != readback_size or artifact_size != metadata.expected_size_bytes
            or metadata.actual_size_bytes != metadata.expected_size_bytes
        ):
            raise PostRebootEvidenceError("artifact/read-back files no longer match the verified journal hashes and sizes")
        approved_path = _safe_regular_file(Path(str(start_data.get("artifact_path", ""))))
        if str(artifact_path.resolve(strict=True)) != str(approved_path.resolve(strict=True)):
            raise PostRebootEvidenceError("verified artifact path differs from the approved start record")
        if profile_id == "x200-libreboot":
            # X200 writes a derived ROM with the original GbE region copied in;
            # journal metadata names the pinned COMPLETE source artifact while
            # WRITE_STARTED/WRITE_VERIFIED bind the final derived bytes.
            mac_binding = start_data.get("x200_mac_preservation")
            if not isinstance(mac_binding, dict):
                raise PostRebootEvidenceError("X200 write journal lacks MAC-preservation provenance")
            source_path = _safe_regular_file(Path(metadata.path))
            source_hash, source_size = _sha256_file(source_path)
            if (
                source_hash != metadata.expected_sha256
                or source_hash != metadata.actual_sha256
                or source_size != metadata.expected_size_bytes
                or mac_binding.get("source_artifact_sha256") != source_hash
                or mac_binding.get("write_artifact_sha256") != artifact_sha
            ):
                raise PostRebootEvidenceError("X200 source artifact or MAC-derived write digest changed")
            manifest_path = _safe_regular_file(Path(str(mac_binding.get("preservation_manifest_path", ""))))
            manifest_hash, _manifest_size = _sha256_file(manifest_path)
            expected_manifest_hash = mac_binding.get("preservation_manifest_sha256")
            if not isinstance(expected_manifest_hash, str) or manifest_hash != expected_manifest_hash:
                raise PostRebootEvidenceError("X200 MAC-preservation manifest digest changed")
            manifest = _parse_json_object(manifest_path.read_text(encoding="utf-8"), "X200 MAC-preservation manifest")
            if (
                manifest.get("schema_version") != 1
                or manifest.get("operation_id") != journal.operation_id
                or manifest.get("policy") != "preserve-original-gbe-region"
                or manifest.get("source_artifact_sha256") != source_hash
                or str(Path(str(manifest.get("source_artifact_path", ""))).resolve(strict=True))
                != str(source_path.resolve(strict=True))
                or manifest.get("write_artifact_sha256") != artifact_sha
                or str(Path(str(manifest.get("write_artifact_path", ""))).resolve(strict=True))
                != str(artifact_path.resolve(strict=True))
                or manifest.get("write_artifact_size_bytes") != artifact_size
                or manifest.get("selected_mac_after") != mac_binding.get("final_mac")
            ):
                raise PostRebootEvidenceError("X200 MAC-preservation manifest does not bind the approved final ROM")
        elif (
            metadata.expected_sha256 != artifact_sha
            or metadata.actual_sha256 != artifact_sha
            or str(artifact_path.resolve(strict=True)) != str(Path(metadata.path).resolve(strict=True))
        ):
            raise PostRebootEvidenceError("X220 verified artifact differs from terminal journal metadata")
        operation_id = journal.operation_id
        try:
            UUID(operation_id)
        except ValueError as exc:
            raise PostRebootEvidenceError("X200/X220 operation ID is invalid") from exc
        try:
            operation_time = _parse_utc(final.timestamp_utc, "WRITE_VERIFIED timestamp")
        except PostRebootEvidenceError:
            raise
        binding = hashlib.sha256(_canonical({
            "operation_id": operation_id,
            "journal_entry_checksum": final.checksum,
            "intent_sha256": intent_digest,
            "artifact_sha256": artifact_hash,
            "artifact_size_bytes": artifact_size,
            "readback_sha256": readback_hash,
            "readback_size_bytes": readback_size,
            "device_fingerprint_sha256": fingerprint,
        })).hexdigest()
        result: dict[str, Any] = {
            "operation_id": operation_id,
            "device_fingerprint_sha256": fingerprint,
            "operation_time": operation_time,
            "prior_capture_ids": (start_data.get("initial_capture_id"), start_data.get("revalidated_capture_id")),
            "checkpoint_binding_sha256": binding,
        }
        if profile_id == "x200-libreboot":
            mac = start_data.get("x200_mac_preservation")
            if not isinstance(mac, dict) or mac.get("write_artifact_sha256") != artifact_sha:
                raise PostRebootEvidenceError("X200 MAC preservation evidence is missing or bound to another artifact")
            final_mac = mac.get("final_mac")
            if not isinstance(final_mac, str) or not _MAC.fullmatch(final_mac.lower()):
                raise PostRebootEvidenceError("X200 final GbE MAC is missing from the approved write record")
            result["expected_x200_mac"] = final_mac.lower()
        return result

    @staticmethod
    def _write_once(target: Path, lock: Path, envelope: bytes) -> None:
        parent = target.parent
        if not parent.is_dir() or parent.is_symlink() or target.exists() or target.is_symlink():
            raise PostRebootEvidenceError("post-reboot report location is not new and operation-owned")
        lock_fd: int | None = None
        try:
            lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            lock_data = memoryview(b"post-reboot-report-v1\n")
            while lock_data:
                written = os.write(lock_fd, lock_data)
                if written <= 0:
                    raise OSError("short write while acquiring post-reboot lock")
                lock_data = lock_data[written:]
            os.fsync(lock_fd)
            _sync_directory(parent)
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
            report_fd = os.open(target, flags, 0o600)
            try:
                view = memoryview(envelope)
                while view:
                    written = os.write(report_fd, view)
                    if written <= 0:
                        raise OSError("short write while recording post-reboot report")
                    view = view[written:]
                os.fsync(report_fd)
            finally:
                os.close(report_fd)
            _sync_directory(parent)
        except Exception as exc:
            raise PostRebootEvidenceError(f"durable one-use post-reboot report could not be recorded: {exc}") from exc
        finally:
            if lock_fd is not None:
                os.close(lock_fd)
                # A leftover lock after a crash blocks replays safely. Normal
                # completion removes it only after the report and parent are synced.
                try:
                    lock.unlink()
                    _sync_directory(parent)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass

    @staticmethod
    def verify_report(
        path: str | Path,
        *,
        expected_checkpoint_hash: str | None = None,
        expected_operation_id: str | None = None,
    ) -> dict[str, Any]:
        report_path = _safe_regular_file(Path(path))
        try:
            envelope = _parse_json_object(report_path.read_text(encoding="utf-8"), "pending report")
        except OSError as exc:
            raise PostRebootEvidenceError("pending report is unreadable") from exc
        if set(envelope) != {"payload", "sha256"} or not isinstance(envelope["payload"], dict):
            raise PostRebootEvidenceError("pending report envelope is invalid")
        payload = envelope["payload"]
        digest = envelope["sha256"]
        if not isinstance(digest, str) or hashlib.sha256(_canonical(payload)).hexdigest() != digest:
            raise PostRebootEvidenceError("pending report checksum is invalid")
        expected_payload_keys = {
            "schema_version", "report_status", "observation_semantics", "profile_id", "operation_id",
            "checkpoint_path", "checkpoint_sha256", "checkpoint_operation_fingerprint",
            "device_fingerprint_sha256", "local_capture", "operation_completed_at_utc",
            "return_capture_at_utc", "recorded_at_utc", "operator", "boot_session_id",
            "boot_session_unknown_reason", "human_reboot_attested", "human_reboot_evidence_ref",
            "human_reboot_observed_at_utc", "checks", "acceptance_status", "hardware_success_claimed",
        }
        if set(payload) != expected_payload_keys or payload.get("schema_version") != 1:
            raise PostRebootEvidenceError("pending report payload has an unsupported or incomplete schema")
        if payload.get("report_status") != "PENDING_HUMAN_REVIEW" or payload.get("hardware_success_claimed") is not False:
            raise PostRebootEvidenceError("post-reboot report must remain pending and cannot claim hardware success")
        if payload.get("observation_semantics") != "operator-reported observations; not authenticated measurements or hardware verification":
            raise PostRebootEvidenceError("post-reboot report observation semantics are missing")
        profile_id = payload.get("profile_id")
        if profile_id not in {"x200-libreboot", "x220-coreboot", "t430-1vyrain"}:
            raise PostRebootEvidenceError("pending report profile is unsupported")
        checkpoint_value = payload.get("checkpoint_path")
        if not isinstance(checkpoint_value, str):
            raise PostRebootEvidenceError("pending report checkpoint path is missing")
        checkpoint_path = _safe_regular_file(Path(checkpoint_value))
        if checkpoint_value != str(checkpoint_path):
            raise PostRebootEvidenceError("pending report checkpoint path is not canonical")
        expected_report_path = checkpoint_path.with_name(checkpoint_path.name + ".post-reboot-pending.json")
        if report_path != expected_report_path:
            raise PostRebootEvidenceError("pending report is not at its exact operation-owned sidecar path")
        checkpoint_hash, _ = _sha256_file(checkpoint_path)
        if checkpoint_hash != payload.get("checkpoint_sha256"):
            raise PostRebootEvidenceError("terminal checkpoint changed after pending report publication")
        try:
            checkpoint = PostRebootEvidenceService._open_checkpoint(profile_id, checkpoint_path)
        except PostRebootEvidenceError:
            raise
        except (JournalError, SafetyError, OSError, ValueError) as exc:
            raise PostRebootEvidenceError(f"pending report terminal checkpoint validation failed: {exc}") from exc
        if (
            payload.get("operation_id") != checkpoint.get("operation_id")
            or payload.get("device_fingerprint_sha256") != checkpoint.get("device_fingerprint_sha256")
            or payload.get("checkpoint_operation_fingerprint") != checkpoint.get("checkpoint_binding_sha256")
        ):
            raise PostRebootEvidenceError("pending report identity no longer matches the terminal checkpoint")
        local_capture = payload.get("local_capture")
        if not isinstance(local_capture, dict) or local_capture.get("source") != "local-read-only-dmi-bios-detector":
            raise PostRebootEvidenceError("pending report lacks a locally captured read-only DMI/BIOS identity")
        raw_device = local_capture.get("raw_device_evidence")
        if PostRebootEvidenceService._mapping_model(raw_device) != {
            "x200-libreboot": "x200", "x220-coreboot": "x220", "t430-1vyrain": "t430"
        }[profile_id]:
            raise PostRebootEvidenceError("pending report local identity does not match its profile")
        if (
            local_capture.get("capture_id") in checkpoint.get("prior_capture_ids", ())
            or local_capture.get("observed_at_utc") is None
            or payload.get("device_fingerprint_sha256") != checkpoint.get("device_fingerprint_sha256")
        ):
            raise PostRebootEvidenceError("pending report capture is replayed or bound to another device")
        capture_time = _parse_utc(local_capture.get("observed_at_utc"), "pending report local capture time")
        operation_time = checkpoint.get("operation_time")
        recorded_at = _parse_utc(payload.get("recorded_at_utc"), "pending report recording time")
        if not isinstance(operation_time, datetime) or capture_time <= operation_time:
            raise PostRebootEvidenceError("pending report local capture does not follow its terminal checkpoint")
        if _parse_utc(payload.get("operation_completed_at_utc"), "pending operation completion time") != operation_time:
            raise PostRebootEvidenceError("pending report operation time differs from its terminal checkpoint")
        expected_return_time = checkpoint.get("return_capture_time")
        reported_return_time = payload.get("return_capture_at_utc")
        if isinstance(expected_return_time, datetime):
            if _parse_utc(reported_return_time, "pending T430 return capture time") != expected_return_time:
                raise PostRebootEvidenceError("pending T430 return capture time differs from the consumed checkpoint")
        elif reported_return_time is not None:
            raise PostRebootEvidenceError("non-T430 report must not contain a T430 return capture time")
        if capture_time > recorded_at + _MAX_FUTURE_SKEW or recorded_at - capture_time > _MAX_REPORT_AGE:
            raise PostRebootEvidenceError("pending report local capture timestamp is stale or future-dated")
        if not isinstance(payload.get("operator"), str) or not payload["operator"].strip():
            raise PostRebootEvidenceError("pending report operator identity is missing")
        if not isinstance(payload.get("checks"), dict) or payload.get("acceptance_status") != "NOT_ASSESSED":
            raise PostRebootEvidenceError("pending report observations or unassessed status are missing")
        normalized_observations = _validate_observations(
            profile_id,
            {
                "schema_version": 1,
                "operator": payload.get("operator"),
                "boot_session_id": payload.get("boot_session_id"),
                "boot_session_unknown_reason": payload.get("boot_session_unknown_reason"),
                "human_reboot_attested": payload.get("human_reboot_attested"),
                "human_reboot_evidence_ref": payload.get("human_reboot_evidence_ref"),
                "human_reboot_observed_at_utc": payload.get("human_reboot_observed_at_utc"),
                "checks": payload.get("checks"),
            },
            checkpoint_time=operation_time,
            capture_time=capture_time,
            now=recorded_at,
            expected_x200_mac=checkpoint.get("expected_x200_mac"),
            expected_reboot_evidence_ref=checkpoint.get("reboot_evidence_ref"),
            expected_reboot_observed_at_utc=checkpoint.get("reboot_observed_at_utc"),
        )
        if (
            normalized_observations["operator"] != payload.get("operator")
            or normalized_observations["boot_session_id"] != payload.get("boot_session_id")
            or normalized_observations["boot_session_unknown_reason"] != payload.get("boot_session_unknown_reason")
            or normalized_observations["human_reboot_attested"] is not payload.get("human_reboot_attested")
            or normalized_observations["human_reboot_evidence_ref"] != payload.get("human_reboot_evidence_ref")
            or normalized_observations["human_reboot_observed_at_utc"] != payload.get("human_reboot_observed_at_utc")
            or _canonical(normalized_observations["checks"]) != _canonical(payload.get("checks"))
        ):
            raise PostRebootEvidenceError("pending report observations do not match the validated profile-specific schema")
        if expected_checkpoint_hash is not None and payload.get("checkpoint_sha256") != expected_checkpoint_hash:
            raise PostRebootEvidenceError("pending report is bound to a different checkpoint")
        if expected_operation_id is not None and payload.get("operation_id") != expected_operation_id:
            raise PostRebootEvidenceError("pending report is bound to a different operation")
        return payload
