from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from autoloader.exceptions import BIOSReversionError, SafetyError, UnsupportedModelError
from autoloader.reversion.ivprep import (
    IVPREP_DOWNGRADE_SCRIPT_SHA256,
    IVPREP_README_SHA256,
    IVPREP_REVISION,
    IVPREP_TARGET_BIOS,
    IVPREP_TREE,
    IVPrepManager,
)
from autoloader.reversion.t430_handoff import (
    ONEVYRAIN_OPTION,
    ONEVYRAIN_README_SHA256,
    ONEVYRAIN_REV5_MD5,
    ONEVYRAIN_REV5_MEDIA_URL,
    ONEVYRAIN_START_SHA256,
    T430DetectionCapture,
    T430HandoffCheckpointStore,
    T430HandoffSession,
    T430HandoffStage,
    T430MediaEvidence,
    T430PreflightEvidence,
    assess_t430_handoff,
)
from autoloader.workflow.types import CompatibilityDecision, DeviceEvidence

BASE = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
DEVICE_FP = "a" * 64
OTHER_DEVICE_FP = "b" * 64


def t430_device(bios: str = "G1ET74WW (2.64 )", **overrides) -> DeviceEvidence:
    values = {
        "raw_dmi_product_name": "ThinkPad T430 2342",
        "raw_dmi_product_version": "2342CTO",
        "raw_dmi_system_family": "ThinkPad T430",
        "raw_mtm": "2342CTO",
        "raw_bios_version": bios,
        "raw_bios_vendor": "LENOVO",
        "stock_lenovo_bios": True,
    }
    values.update(overrides)
    return DeviceEvidence(**values)


def capture(
    device: DeviceEvidence,
    name: str,
    minutes: int,
    *,
    fingerprint: str = DEVICE_FP,
) -> T430DetectionCapture:
    return T430DetectionCapture(
        capture_id=name,
        observed_at=BASE + timedelta(minutes=minutes),
        device=device,
        device_fingerprint_sha256=fingerprint,
    )


def _valid_onevyrain_return_evidence():
    return {
        "selected_option": ONEVYRAIN_OPTION,
        "selected_option_evidence_ref": "operator-observation:1vyrain-option-1",
        "selected_option_observed_at": BASE + timedelta(minutes=3, seconds=30),
        "successful_reboot_evidence_ref": "operator-observation:post-install-reboot",
        "successful_reboot_observed_at": BASE + timedelta(minutes=5),
    }


def preflight(
    detection: T430DetectionCapture,
    *,
    actor: str = "operator",
    minutes_after_capture: int = 1,
    uefi: bool | None = True,
    **overrides,
) -> T430PreflightEvidence:
    values = {
        "capture_id": detection.capture_id,
        "device_fingerprint_sha256": detection.device_fingerprint_sha256,
        "attested_by": actor,
        "attested_at": detection.observed_at + timedelta(minutes=minutes_after_capture),
        "bios_passwords_removed": True,
        "end_user_bios_update_enabled": True,
        "secure_rollback_prevention_disabled": True,
        "ac_connected": True,
        "battery_percent": 80,
        "uefi_booted": uefi,
        "secure_boot_disabled": True,
        "will_boot_1vyrain_from_direct_onboard_usb_without_dock_or_hub": True,
    }
    values.update(overrides)
    return T430PreflightEvidence(**values)


def media_evidence(
    detection: T430DetectionCapture,
    *,
    actor: str = "operator",
    selected_at: datetime | None = None,
    fingerprint: str | None = None,
    image_md5: str | None = ONEVYRAIN_REV5_MD5,
    dd_mode: bool | None = True,
    image_source_url: str = ONEVYRAIN_REV5_MEDIA_URL,
) -> T430MediaEvidence:
    return T430MediaEvidence(
        capture_id=detection.capture_id,
        device_fingerprint_sha256=fingerprint or detection.device_fingerprint_sha256,
        user_reported_image_sha256="c" * 64,
        user_reported_image_md5=image_md5,
        image_size_bytes=2_000_000,
        image_source_url=image_source_url,
        user_reported_media_fingerprint_sha256="d" * 64,
        written_in_dd_mode=dd_mode,
        selected_at=selected_at or detection.observed_at + timedelta(minutes=2),
        selected_by=actor,
    )


@pytest.mark.parametrize(
    ("bios", "expected_stage", "expected_decision", "numeric"),
    [
        ("G1ET74WW (2.64 )", T430HandoffStage.ONEVYRAIN_READY, CompatibilityDecision.ALLOWED, 264),
        ("Version: G1ET75WW (2.65 )", T430HandoffStage.IVPREP_REQUIRED, CompatibilityDecision.BLOCKED, 265),
        ("2.64", T430HandoffStage.UNKNOWN, CompatibilityDecision.UNKNOWN, None),
        ("G1ET74WW (2.64)", T430HandoffStage.UNKNOWN, CompatibilityDecision.UNKNOWN, None),
        ("G1ET7XWW (2.64 )", T430HandoffStage.ONEVYRAIN_READY, CompatibilityDecision.ALLOWED, 264),
    ],
)
def test_t430_handoff_uses_pinned_upstream_bios_token(bios, expected_stage, expected_decision, numeric):
    assessment = assess_t430_handoff(t430_device(bios))
    assert assessment.stage is expected_stage
    assert assessment.decision is expected_decision
    assert assessment.numeric_bios == numeric


@pytest.mark.parametrize(
    "overrides",
    [
        {"raw_dmi_product_name": "ThinkPad T430s 2344"},
        {"raw_dmi_system_family": "ThinkPad X220"},
        {"raw_mtm": "9999CTO"},
        {"raw_bios_vendor": "Phoenix"},
        {"stock_lenovo_bios": False},
    ],
)
def test_t430_handoff_rejects_conflicting_or_unsupported_evidence(overrides):
    assert assess_t430_handoff(t430_device(**overrides)).stage is T430HandoffStage.BLOCKED


def test_t430_handoff_unknown_identity_or_vendor_fails_closed():
    no_mtm = t430_device(raw_mtm=None, raw_dmi_product_version=None, raw_dmi_product_name="ThinkPad T430")
    no_vendor = t430_device(raw_bios_vendor=None)
    assert assess_t430_handoff(no_mtm).stage is T430HandoffStage.UNKNOWN
    assert assess_t430_handoff(no_vendor).stage is T430HandoffStage.UNKNOWN


def test_ivprep_source_pin_and_manual_handoff_only():
    manager = IVPrepManager(model="t430", target_version="2.64")
    with pytest.raises(SafetyError):
        manager.create_handoff_descriptor(actor="operator", explicit_approval=False, approved_at=BASE)
    descriptor = manager.create_handoff_descriptor(
        actor="operator", explicit_approval=True, approved_at=BASE + timedelta(minutes=1)
    )
    assert descriptor.revision == IVPREP_REVISION == "7faecc72be8776be32f37e7ff00af22a25dfc3cf"
    assert descriptor.tree == IVPREP_TREE == "e5c684e3dc5b90d7e70122229a8b46bd4e4bd012"
    assert descriptor.target_bios_version == IVPREP_TARGET_BIOS == "2.64"
    assert descriptor.readme_sha256 == IVPREP_README_SHA256 == "e1b8bc1feea5d8b52e5bacc8fc520a98cb21b125824800a7662d6119fb8492e5"
    assert descriptor.downgrade_script_sha256 == IVPREP_DOWNGRADE_SCRIPT_SHA256 == "cb5528d6f1d001937b224f3d9b7f075a928c2692e12d8f74b24533dd12d2dd31"
    assert descriptor.required_platform == "Windows 64-bit"
    assert descriptor.power_loss_can_brick_device is True
    ivprep_instructions = " ".join(descriptor.instructions)
    assert "Windows 64-bit" in ivprep_instructions
    assert "Flash BIOS Updating by End-Users" in ivprep_instructions
    assert "Secure RollBack Prevention" in ivprep_instructions
    assert "Connect AC power" in ivprep_instructions
    assert "Do not run downgrade.bat as Administrator" in ivprep_instructions
    assert "WinFlash requests elevation" in ivprep_instructions
    assert "reboots" in ivprep_instructions and "BIOS region" in ivprep_instructions
    assert "Power loss" in ivprep_instructions and "brick" in ivprep_instructions
    assert descriptor.binaries_included is False
    assert descriptor.binary_assets_verified is False
    assert descriptor.executes_anything is False


def test_ivprep_handoff_rejects_other_models_and_targets():
    with pytest.raises(UnsupportedModelError):
        IVPrepManager(model="x230").create_handoff_descriptor(
            actor="operator", explicit_approval=True, approved_at=BASE
        )
    with pytest.raises(SafetyError):
        IVPrepManager(model="t430", target_version="2.65").create_handoff_descriptor(
            actor="operator", explicit_approval=True, approved_at=BASE
        )


def test_ivprep_approval_requires_fresh_preflight_and_timestamp_then_redetection(tmp_path):
    initial = capture(t430_device("G1ET75WW (2.65 )"), "initial", 0)
    checkpoint = tmp_path / "ivprep-pending.json"
    session = T430HandoffSession(initial, checkpoint_path=checkpoint)
    assert session.stage is T430HandoffStage.IVPREP_REQUIRED
    with pytest.raises(SafetyError, match="preflight"):
        session.approve_ivprep_handoff(actor="operator", explicit_approval=True, approved_at=BASE + timedelta(minutes=2))
    session.record_preflight(preflight(initial, uefi=None))
    with pytest.raises(SafetyError, match="timestamp"):
        session.approve_ivprep_handoff(actor="operator", explicit_approval=True, approved_at=BASE + timedelta(minutes=1))
    descriptor = session.approve_ivprep_handoff(
        actor="operator", explicit_approval=True, approved_at=BASE + timedelta(minutes=2)
    )
    assert descriptor.approved_at == BASE + timedelta(minutes=2)
    assert descriptor.executes_anything is False
    assert session.stage is T430HandoffStage.AWAITING_IVPREP_REDETECTION
    resumed = T430HandoffSession.resume_from_checkpoint(checkpoint)
    assert resumed.operation_id == session.operation_id
    assert resumed.stage is T430HandoffStage.AWAITING_IVPREP_REDETECTION
    assert resumed._approval_timestamp == BASE + timedelta(minutes=2)

    with pytest.raises(SafetyError, match="Stale detection"):
        resumed.record_ivprep_return(initial, reported_by="operator", user_reported_handoff_complete=True)
    with pytest.raises(SafetyError, match="strictly later"):
        resumed.record_ivprep_return(
            capture(t430_device("G1ET74WW (2.64 )"), "before-approval", 1),
            reported_by="operator", user_reported_handoff_complete=True,
        )
    with pytest.raises(SafetyError, match="approving human"):
        resumed.record_ivprep_return(
            capture(t430_device("G1ET74WW (2.64 )"), "wrong-actor", 3),
            reported_by="other-operator", user_reported_handoff_complete=True,
        )
    result = resumed.record_ivprep_return(
        capture(t430_device("G1ET74WW (2.64 )"), "after-ivprep", 4),
        reported_by="operator", user_reported_handoff_complete=True,
    )
    assert result.stage is T430HandoffStage.ONEVYRAIN_READY
    assert resumed.stage is T430HandoffStage.ONEVYRAIN_READY
    with pytest.raises(SafetyError, match="Consumed"):
        T430HandoffSession.resume_from_checkpoint(checkpoint)
    terminal = json.loads(checkpoint.read_text(encoding="utf-8"))["payload"]
    assert terminal["checkpoint_status"] == "consumed"
    assert terminal["result_code"] == "ivprep-compatible"
    current_capture = capture(t430_device("G1ET74WW (2.64 )"), "after-ivprep", 4)
    assert resumed._capture_id == current_capture.capture_id
    resumed.record_preflight(preflight(current_capture, uefi=True))
    resumed.record_onevyrain_media(
        media_evidence(current_capture, selected_at=BASE + timedelta(minutes=6))
    )
    onevyrain = resumed.approve_onevyrain_handoff(
        actor="operator", explicit_approval=True, approved_at=BASE + timedelta(minutes=7)
    )
    assert onevyrain.approved_at == BASE + timedelta(minutes=7)
    assert resumed.stage is T430HandoffStage.AWAITING_POST_INSTALL_REDETECTION
    assert list(tmp_path.glob("*.ivprep-compatible.consumed"))


def test_ivprep_return_with_changed_device_identity_blocks(tmp_path):
    initial = capture(t430_device("G1ET75WW (2.65 )"), "initial", 0)
    session = T430HandoffSession(initial, checkpoint_path=tmp_path / "ivprep-pending.json")
    session.record_preflight(preflight(initial, uefi=None))
    session.approve_ivprep_handoff(
        actor="operator", explicit_approval=True, approved_at=BASE + timedelta(minutes=2)
    )
    with pytest.raises(SafetyError, match="fingerprint"):
        session.record_ivprep_return(
            capture(t430_device("G1ET74WW (2.64 )"), "changed-device", 3, fingerprint=OTHER_DEVICE_FP),
            reported_by="operator", user_reported_handoff_complete=True,
        )


def test_t430_external_handoff_approval_requires_durable_checkpoint(tmp_path):
    initial = capture(t430_device("G1ET75WW (2.65 )"), "initial", 0)
    session = T430HandoffSession(initial)
    session.record_preflight(preflight(initial, uefi=None))
    with pytest.raises(SafetyError, match="durable checkpoint"):
        session.approve_ivprep_handoff(
            actor="operator", explicit_approval=True, approved_at=BASE + timedelta(minutes=2)
        )
    assert session.stage is T430HandoffStage.IVPREP_REQUIRED
    assert session._handoff_approved is False
    assert not list(tmp_path.iterdir())


def test_t430_checkpoint_corruption_is_rejected(tmp_path):
    initial = capture(t430_device("G1ET75WW (2.65 )"), "initial", 0)
    checkpoint = tmp_path / "ivprep-pending.json"
    session = T430HandoffSession(initial, checkpoint_path=checkpoint)
    session.record_preflight(preflight(initial, uefi=None))
    session.approve_ivprep_handoff(
        actor="operator", explicit_approval=True, approved_at=BASE + timedelta(minutes=2)
    )
    checkpoint.write_text(checkpoint.read_text(encoding="utf-8").replace('"stage":"awaiting-ivprep-redetection"', '"stage":"complete"'), encoding="utf-8")
    with pytest.raises(SafetyError, match="checksum mismatch"):
        T430HandoffSession.resume_from_checkpoint(checkpoint)


def test_t430_v1_checkpoint_with_valid_checksum_is_rejected(tmp_path):
    initial = capture(t430_device("G1ET75WW (2.65 )"), "initial", 0)
    checkpoint = tmp_path / "ivprep-v1-pending.json"
    session = T430HandoffSession(initial, checkpoint_path=checkpoint)
    session.record_preflight(preflight(initial, uefi=None))
    session.approve_ivprep_handoff(
        actor="operator", explicit_approval=True, approved_at=BASE + timedelta(minutes=2)
    )

    payload = json.loads(checkpoint.read_text(encoding="utf-8"))["payload"]
    payload["version"] = 1
    payload["preflight"].pop("secure_boot_disabled")
    payload["preflight"].pop("will_boot_1vyrain_from_direct_onboard_usb_without_dock_or_hub")
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    envelope = {"payload": payload, "sha256": hashlib.sha256(canonical).hexdigest()}
    checkpoint.write_text(
        json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    assert T430HandoffCheckpointStore._read_verified_payload(checkpoint) == payload
    with pytest.raises(SafetyError, match="unsupported schema/version"):
        T430HandoffSession.resume_from_checkpoint(checkpoint)


def test_ivprep_return_that_did_not_reach_compatible_bios_is_not_success(tmp_path):
    initial = capture(t430_device("G1ET75WW (2.65 )"), "initial", 0)
    session = T430HandoffSession(initial, checkpoint_path=tmp_path / "ivprep-pending.json")
    session.record_preflight(preflight(initial, uefi=None))
    session.approve_ivprep_handoff(
        actor="operator", explicit_approval=True, approved_at=BASE + timedelta(minutes=2)
    )
    with pytest.raises(BIOSReversionError, match="does not establish a compatible"):
        session.record_ivprep_return(
            capture(t430_device("G1ET75WW (2.65 )"), "after-ivprep-still-high", 3),
            reported_by="operator", user_reported_handoff_complete=True,
        )
    assert session.stage is T430HandoffStage.IVPREP_REQUIRED
    terminal = json.loads((tmp_path / "ivprep-pending.json").read_text(encoding="utf-8"))["payload"]
    assert terminal["checkpoint_status"] == "consumed"
    assert terminal["result_code"] == "ivprep-recheck-required"
    with pytest.raises(SafetyError, match="Consumed"):
        T430HandoffSession.resume_from_checkpoint(tmp_path / "ivprep-pending.json")
    with pytest.raises(SafetyError, match="preflight"):
        session.approve_ivprep_handoff(
            actor="operator", explicit_approval=True, approved_at=BASE + timedelta(minutes=5)
        )


def test_onevyrain_requires_fresh_preflight_media_and_same_human():
    initial = capture(t430_device(), "initial", 0)
    session = T430HandoffSession(initial)
    with pytest.raises(SafetyError, match="preflight"):
        session.approve_onevyrain_handoff(
            actor="operator", explicit_approval=True, approved_at=BASE + timedelta(minutes=4)
        )
    with pytest.raises(SafetyError, match="UEFI"):
        session.record_preflight(preflight(initial, uefi=None))
    session.record_preflight(preflight(initial, actor="operator", uefi=True))
    with pytest.raises(SafetyError, match="human who attested"):
        session.record_onevyrain_media(media_evidence(initial, actor="other-operator"))
    with pytest.raises(SafetyError, match="different device"):
        session.record_onevyrain_media(media_evidence(initial, fingerprint=OTHER_DEVICE_FP))
    session.record_onevyrain_media(media_evidence(initial))
    with pytest.raises(SafetyError, match="timestamp"):
        session.approve_onevyrain_handoff(
            actor="operator", explicit_approval=True, approved_at=BASE + timedelta(minutes=1)
        )
    with pytest.raises(SafetyError, match="follow image/media selection"):
        session.approve_onevyrain_handoff(
            actor="operator", explicit_approval=True, approved_at=BASE + timedelta(minutes=2)
        )
    with pytest.raises(SafetyError, match="human who attested"):
        session.approve_onevyrain_handoff(
            actor="another-operator", explicit_approval=True, approved_at=BASE + timedelta(minutes=4)
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"bios_passwords_removed": None},
        {"end_user_bios_update_enabled": False},
        {"secure_rollback_prevention_disabled": None},
        {"ac_connected": False},
        {"battery_percent": None},
        {"battery_percent": 29},
        {"secure_boot_disabled": None},
        {"secure_boot_disabled": False},
        {"will_boot_1vyrain_from_direct_onboard_usb_without_dock_or_hub": None},
        {"will_boot_1vyrain_from_direct_onboard_usb_without_dock_or_hub": False},
    ],
)
def test_t430_handoff_preflight_unknown_or_failed_values_block(overrides):
    initial = capture(t430_device(), "initial", 0)
    session = T430HandoffSession(initial)
    with pytest.raises(SafetyError):
        session.record_preflight(preflight(initial, **overrides))
    with pytest.raises(SafetyError, match="preflight"):
        session.approve_onevyrain_handoff(
            actor="operator", explicit_approval=True, approved_at=BASE + timedelta(minutes=4)
        )


@pytest.mark.parametrize(
    ("image_md5", "dd_mode", "source_url", "expected_message"),
    [
        (None, True, ONEVYRAIN_REV5_MEDIA_URL, "MD5 is required"),
        ("0" * 32, True, ONEVYRAIN_REV5_MEDIA_URL, "does not match"),
        (ONEVYRAIN_REV5_MD5.upper(), None, ONEVYRAIN_REV5_MEDIA_URL, "DD mode"),
        (ONEVYRAIN_REV5_MD5, False, ONEVYRAIN_REV5_MEDIA_URL, "DD mode"),
        (ONEVYRAIN_REV5_MD5, True, "https://example.invalid/image.iso", "pinned upstream Rev 5 link"),
    ],
)
def test_onevyrain_media_requires_pinned_legacy_md5_source_and_dd_mode(
    image_md5, dd_mode, source_url, expected_message
):
    initial = capture(t430_device(), "initial", 0)
    session = T430HandoffSession(initial)
    session.record_preflight(preflight(initial, uefi=True))
    with pytest.raises(SafetyError, match=expected_message):
        session.record_onevyrain_media(
            media_evidence(
                initial,
                image_md5=image_md5,
                dd_mode=dd_mode,
                image_source_url=source_url,
            )
        )
    assert session._media is None


@pytest.mark.parametrize(
    ("preflight_field", "bad_value", "media_field", "expected_message"),
    [
        ("secure_boot_disabled", None, None, "Secure Boot disabled"),
        ("secure_boot_disabled", False, None, "Secure Boot disabled"),
        (
            "will_boot_1vyrain_from_direct_onboard_usb_without_dock_or_hub",
            None,
            None,
            "direct onboard USB boot",
        ),
        (
            "will_boot_1vyrain_from_direct_onboard_usb_without_dock_or_hub",
            False,
            None,
            "direct onboard USB boot",
        ),
        (None, None, "user_reported_image_md5", "Rev 5 MD5"),
        (None, None, "written_in_dd_mode", "DD-mode"),
    ],
)
def test_onevyrain_approval_rechecks_required_human_attestations(
    preflight_field, bad_value, media_field, expected_message
):
    initial = capture(t430_device(), "initial", 0)
    session = T430HandoffSession(initial)
    session.record_preflight(preflight(initial, uefi=True))
    session.record_onevyrain_media(media_evidence(initial))
    if preflight_field is not None:
        setattr(session, "_preflight", preflight(initial, uefi=True, **{preflight_field: bad_value}))
    if media_field == "user_reported_image_md5":
        setattr(session, "_media", media_evidence(initial, image_md5="0" * 32))
    elif media_field == "written_in_dd_mode":
        setattr(session, "_media", media_evidence(initial, dd_mode=None))
    with pytest.raises(SafetyError, match=expected_message):
        session.approve_onevyrain_handoff(
            actor="operator", explicit_approval=True, approved_at=BASE + timedelta(minutes=4)
        )
    assert session._handoff_approved is False


def test_onevyrain_requires_fresh_reboot_evidence_and_remains_pending(tmp_path):
    initial = capture(t430_device(), "initial", 0)
    checkpoint = tmp_path / "1vyrain-pending.json"
    session = T430HandoffSession(initial, checkpoint_path=checkpoint)
    session.record_preflight(preflight(initial, actor="operator", uefi=True))
    session.record_onevyrain_media(media_evidence(initial, selected_at=BASE + timedelta(minutes=2)))
    descriptor = session.approve_onevyrain_handoff(
        actor="operator", explicit_approval=True, approved_at=BASE + timedelta(minutes=3)
    )
    assert descriptor.revision
    assert descriptor.readme_sha256 == ONEVYRAIN_README_SHA256 == "5af0f463e56e8fcfbc6d3b57224859e755e701d4e04087167c9e335b14e8c33a"
    assert descriptor.start_script_sha256 == ONEVYRAIN_START_SHA256 == "d00089253cb56a8967e242a9e73496c413a7c3d378044968acbc6278b31e873a"
    assert descriptor.upstream_sha256_verified is False
    assert descriptor.image_integrity_claim == "user-reported local SHA-256 fingerprint only; upstream authenticity is not verified"
    assert descriptor.user_reported_image_sha256 == "c" * 64
    assert descriptor.image_size_bytes == 2_000_000
    assert descriptor.user_reported_image_source_url == ONEVYRAIN_REV5_MEDIA_URL
    assert descriptor.selected_option == "option-1-default-modified-lenovo-bios"
    with pytest.raises(ValueError, match="option 1/default"):
        replace(descriptor, selected_option="custom-bios-url")
    assert descriptor.user_reported_media_fingerprint_sha256 == "d" * 64
    assert descriptor.upstream_rev5_media_url == ONEVYRAIN_REV5_MEDIA_URL
    assert descriptor.upstream_rev5_published_md5 == ONEVYRAIN_REV5_MD5
    assert descriptor.user_reported_image_md5 == ONEVYRAIN_REV5_MD5
    assert descriptor.md5_integrity_claim == "user-reported MD5 matches the pinned README's legacy checksum; weak checksum, not authentication"
    assert descriptor.image_authenticated is False
    assert descriptor.written_in_dd_mode is True
    instructions = " ".join(descriptor.instructions)
    assert "UEFI" in instructions and "Secure Boot" in instructions
    assert "onboard USB" in instructions and "dock, ultrabase" in instructions
    assert "DD mode" in instructions
    assert "AC power" in instructions and "BIOS passwords" in instructions
    assert ONEVYRAIN_REV5_MEDIA_URL in instructions and ONEVYRAIN_REV5_MD5 in instructions
    assert "not authenticate" in instructions
    assert descriptor.binaries_included is False
    assert descriptor.binaries_verified is False
    assert descriptor.executes_anything is False
    assert descriptor.writes_firmware is False
    resumed = T430HandoffSession.resume_from_checkpoint(checkpoint)
    assert resumed.operation_id == session.operation_id
    assert resumed.stage is T430HandoffStage.AWAITING_POST_INSTALL_REDETECTION
    assert resumed._preflight == session._preflight
    assert resumed._preflight is not None
    assert resumed._preflight.secure_boot_disabled is True
    assert resumed._preflight.will_boot_1vyrain_from_direct_onboard_usb_without_dock_or_hub is True
    assert resumed._media == session._media
    assert resumed._media is not None
    assert resumed._media.user_reported_image_md5 == ONEVYRAIN_REV5_MD5
    assert resumed._media.written_in_dd_mode is True
    assert json.loads(checkpoint.read_text(encoding="utf-8"))["payload"]["version"] == 2

    with pytest.raises(SafetyError, match="Stale detection"):
        resumed.record_post_install_return(
            capture(t430_device(), "initial", 4), reported_by="operator",
            user_reported_handoff_complete=True, user_reported_successful_reboot=True,
            user_reported_boot_test_passed=True, user_reported_1vyrain_validation_passed=True,
        )
    assert T430HandoffSession.resume_from_checkpoint(checkpoint).stage is T430HandoffStage.AWAITING_POST_INSTALL_REDETECTION
    with pytest.raises(SafetyError, match="strictly later"):
        resumed.record_post_install_return(
            capture(t430_device(), "before-approval", 2), reported_by="operator",
            user_reported_handoff_complete=True, user_reported_successful_reboot=True,
            user_reported_boot_test_passed=True, user_reported_1vyrain_validation_passed=True,
        )
    with pytest.raises(BIOSReversionError, match="evidence are incomplete"):
        resumed.record_post_install_return(
            capture(t430_device(), "post-install-incomplete", 4), reported_by="operator",
            user_reported_handoff_complete=True, user_reported_successful_reboot=True,
            user_reported_boot_test_passed=False, user_reported_1vyrain_validation_passed=True,
        )
    with pytest.raises(SafetyError, match="fingerprint"):
        resumed.record_post_install_return(
            capture(t430_device(), "changed-device", 5, fingerprint=OTHER_DEVICE_FP),
            reported_by="operator", user_reported_handoff_complete=True,
            user_reported_successful_reboot=True, user_reported_boot_test_passed=True,
            user_reported_1vyrain_validation_passed=True,
        )
    post_capture = capture(t430_device("G1ET74WW (2.64 )"), "post-install-valid", 6)
    with pytest.raises(BIOSReversionError, match="Advanced BIOS"):
        resumed.record_post_install_return(
            post_capture,
            reported_by="operator", user_reported_handoff_complete=True,
            user_reported_successful_reboot=True, user_reported_boot_test_passed=True,
            user_reported_1vyrain_validation_passed=True,
            **_valid_onevyrain_return_evidence(),
        )
    with pytest.raises(BIOSReversionError, match="requires both"):
        resumed.record_post_install_return(
            post_capture,
            reported_by="operator", user_reported_handoff_complete=True,
            user_reported_successful_reboot=True, user_reported_boot_test_passed=True,
            user_reported_1vyrain_validation_passed=True,
            advanced_bios_menu_observed=True,
            advanced_bios_evidence_ref="observation:advanced-bios-menu",
            advanced_bios_observed_at=BASE + timedelta(minutes=5, seconds=30),
            flash_command_evidence_ref="observation:flash-command",
            **_valid_onevyrain_return_evidence(),
        )
    for rejected_option in ("option-2-custom-bios", "custom-bios-url", "unknown"):
        wrong_option = _valid_onevyrain_return_evidence()
        wrong_option["selected_option"] = rejected_option
        with pytest.raises(SafetyError, match="actual upstream option 1"):
            resumed.record_post_install_return(
                post_capture,
                reported_by="operator", user_reported_handoff_complete=True,
                user_reported_successful_reboot=True, user_reported_boot_test_passed=True,
                user_reported_1vyrain_validation_passed=True,
                advanced_bios_menu_observed=True,
                advanced_bios_evidence_ref="observation:advanced-bios-menu",
                advanced_bios_observed_at=BASE + timedelta(minutes=5, seconds=30),
                **wrong_option,
            )
    missing_option_evidence = _valid_onevyrain_return_evidence()
    missing_option_evidence["selected_option_evidence_ref"] = ""
    with pytest.raises(SafetyError, match="selection requires a named evidence reference"):
        resumed.record_post_install_return(
            post_capture,
            reported_by="operator", user_reported_handoff_complete=True,
            user_reported_successful_reboot=True, user_reported_boot_test_passed=True,
            user_reported_1vyrain_validation_passed=True,
            advanced_bios_menu_observed=True,
            advanced_bios_evidence_ref="observation:advanced-bios-menu",
            advanced_bios_observed_at=BASE + timedelta(minutes=5, seconds=30),
            **missing_option_evidence,
        )
    stale_option = _valid_onevyrain_return_evidence()
    stale_option["selected_option_observed_at"] = BASE + timedelta(minutes=2, seconds=59)
    with pytest.raises(SafetyError, match="after the approved handoff"):
        resumed.record_post_install_return(
            post_capture,
            reported_by="operator", user_reported_handoff_complete=True,
            user_reported_successful_reboot=True, user_reported_boot_test_passed=True,
            user_reported_1vyrain_validation_passed=True,
            advanced_bios_menu_observed=True,
            advanced_bios_evidence_ref="observation:advanced-bios-menu",
            advanced_bios_observed_at=BASE + timedelta(minutes=5, seconds=30),
            **stale_option,
        )
    with pytest.raises(BIOSReversionError, match="after the operator-reported successful reboot"):
        resumed.record_post_install_return(
            post_capture,
            reported_by="operator", user_reported_handoff_complete=True,
            user_reported_successful_reboot=True, user_reported_boot_test_passed=True,
            user_reported_1vyrain_validation_passed=True,
            advanced_bios_menu_observed=True,
            advanced_bios_evidence_ref="observation:advanced-bios-menu",
            advanced_bios_observed_at=BASE + timedelta(minutes=4, seconds=30),
            **_valid_onevyrain_return_evidence(),
        )
    report = resumed.record_post_install_return(
        post_capture,
        reported_by="operator", user_reported_handoff_complete=True,
        user_reported_successful_reboot=True, user_reported_boot_test_passed=True,
        user_reported_1vyrain_validation_passed=True,
        advanced_bios_menu_observed=True,
        advanced_bios_evidence_ref="observation:advanced-bios-menu",
        advanced_bios_observed_at=BASE + timedelta(minutes=5, seconds=30),
        **_valid_onevyrain_return_evidence(),
    )
    assert report.status is T430HandoffStage.POST_INSTALL_EVIDENCE_PENDING_ACCEPTANCE
    assert report.device_fingerprint_sha256 == DEVICE_FP
    assert report.onevy_approved_at == BASE + timedelta(minutes=3)
    assert report.preflight.capture_id == initial.capture_id
    assert report.media.capture_id == initial.capture_id
    assert report.flash_status == "UNKNOWN"
    assert report.advanced_bios_menu_observed is True
    assert report.selected_option == ONEVYRAIN_OPTION
    assert report.selected_option_evidence_ref == "operator-observation:1vyrain-option-1"
    assert report.successful_reboot_observed_at == BASE + timedelta(minutes=5)
    assert resumed.stage is T430HandoffStage.POST_INSTALL_EVIDENCE_PENDING_ACCEPTANCE
    terminal = json.loads(checkpoint.read_text(encoding="utf-8"))["payload"]
    assert terminal["checkpoint_status"] == "consumed"
    assert terminal["result_code"] == "1vyrain-reported-validation-pending-acceptance"
    with pytest.raises(SafetyError, match="Consumed"):
        T430HandoffSession.resume_from_checkpoint(checkpoint)


@pytest.mark.parametrize(
    ("post_device", "expected_result"),
    [
        (t430_device(raw_dmi_product_name="ThinkPad X220 4286"), "1vyrain-return-identity-not-verified"),
        (t430_device(raw_bios_version=None), "1vyrain-return-bios-unknown"),
    ],
)
def test_unverified_postinstall_capture_consumes_approval_without_success(tmp_path, post_device, expected_result):
    initial = capture(t430_device(), "initial", 0)
    checkpoint = tmp_path / "1vyrain-pending.json"
    session = T430HandoffSession(initial, checkpoint_path=checkpoint)
    session.record_preflight(preflight(initial, uefi=True))
    session.record_onevyrain_media(media_evidence(initial, selected_at=BASE + timedelta(minutes=2)))
    session.approve_onevyrain_handoff(
        actor="operator", explicit_approval=True, approved_at=BASE + timedelta(minutes=3)
    )
    with pytest.raises(BIOSReversionError):
        session.record_post_install_return(
            capture(post_device, "post-install-invalid", 4),
            reported_by="operator", user_reported_handoff_complete=True,
            user_reported_successful_reboot=True, user_reported_boot_test_passed=True,
            user_reported_1vyrain_validation_passed=True,
        )
    terminal = json.loads(checkpoint.read_text(encoding="utf-8"))["payload"]
    assert terminal["checkpoint_status"] == "consumed"
    assert terminal["result_code"] == expected_result
    assert session.stage in {T430HandoffStage.BLOCKED, T430HandoffStage.UNKNOWN}
    with pytest.raises(SafetyError, match="Consumed"):
        T430HandoffSession.resume_from_checkpoint(checkpoint)
    assert "COMPLETE" not in {stage.name for stage in T430HandoffStage}
