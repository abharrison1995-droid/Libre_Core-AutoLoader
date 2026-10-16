"""Fake-only tests for the terminal-checkpoint post-reboot intake."""
from __future__ import annotations

import hashlib
import inspect
import json
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import autoloader.workflow.journal as journal_module
import autoloader.workflow.write_approval as approval_module
from autoloader.application_controller import ApplicationAction, ApplicationController, ApplicationStatus
from autoloader.flasher.flashrom import FlashromRunner
from autoloader.flasher.spi import SPIProbeService
from autoloader.reversion.t430_handoff import T430DetectionCapture, T430HandoffCheckpointStore, T430HandoffSession
from autoloader.workflow.journal import OperationJournal
from autoloader.workflow.post_reboot import PostRebootEvidenceError, PostRebootEvidenceService
from autoloader.workflow.types import OperationState
from tests import test_t430_handoff as t430_fixtures
from tests.test_phase10b_write_service import _service_request

_SERIAL_FP = hashlib.sha256(b"SYNTHETIC-SERIAL").hexdigest()


@pytest.fixture
def synthetic_write_durability(monkeypatch):
    """Keep the prerequisite write-service fixture synthetic and side-effect-free."""
    monkeypatch.setattr(approval_module, "_directory_fsync_supported", lambda: True)
    monkeypatch.setattr(approval_module, "_sync_directory", lambda _path: None)
    monkeypatch.setattr(journal_module, "_directory_fsync_supported", lambda: True)
    monkeypatch.setattr(journal_module, "_sync_directory", lambda _path: None)


def _completed_x220(tmp_path, monkeypatch):
    service, request, writer, readback = _service_request(tmp_path, monkeypatch, x220=True)
    result = service.execute(request)
    assert result.state is OperationState.WRITE_VERIFIED
    assert len(writer.calls) == 1
    assert len(readback.calls) == 1
    return request, result


def _completed_x200(tmp_path, monkeypatch):
    service, request, writer, readback = _service_request(tmp_path, monkeypatch)
    result = service.execute(request)
    assert result.state is OperationState.WRITE_VERIFIED
    assert len(writer.calls) == 1
    assert len(readback.calls) == 1
    return request, result


def _local_capture(monkeypatch, device, observed_at, *, fingerprint=_SERIAL_FP, capture_id=""):
    capture = T430DetectionCapture(
        capture_id=capture_id or uuid.uuid4().hex,
        observed_at=observed_at,
        device=device,
        device_fingerprint_sha256=fingerprint,
    )

    def detect_now(cls, *, model_detector=None, bios_detector=None):
        return capture

    monkeypatch.setattr(T430DetectionCapture, "detect_now", classmethod(detect_now))
    return capture


def test_service_does_not_accept_a_publicly_constructible_capture_argument():
    assert "capture" not in inspect.signature(PostRebootEvidenceService.record).parameters


def _x220_request(operation_time: datetime, capture_time: datetime) -> dict[str, object]:
    reboot_time = operation_time + timedelta(milliseconds=100)
    check_time = operation_time + timedelta(milliseconds=200)
    return {
        "schema_version": 1,
        "operator": "Synthetic Test Operator",
        "boot_session_id": "synthetic-boot-session-1",
        "boot_session_unknown_reason": None,
        "human_reboot_attested": True,
        "human_reboot_evidence_ref": "synthetic-observation:reboot",
        "human_reboot_observed_at_utc": reboot_time.isoformat(),
        "checks": {
            name: {
                "outcome": "pass",
                "evidence_ref": f"synthetic-observation:{name}",
                "observed_at_utc": check_time.isoformat(),
            }
            for name in (
                "cold_boot", "display", "keyboard", "pointing_device", "storage", "sleep_resume", "ethernet"
            )
        },
    }


def _x200_request(operation_time: datetime, expected_mac: str) -> dict[str, object]:
    document = _x220_request(operation_time, operation_time + timedelta(seconds=1))
    document["checks"] = {
        name: {
            "outcome": "pass",
            "evidence_ref": f"synthetic-observation:{name}",
            "observed_at_utc": (operation_time + timedelta(milliseconds=200)).isoformat(),
        }
        for name in (
            "cold_boot", "display", "keyboard", "pointing_device", "storage", "sleep_resume"
        )
    }
    document["checks"]["ethernet_mac"] = {
        "observed_mac": expected_mac,
        "expected_mac": expected_mac,
        "matches_expected": True,
        "evidence_ref": "synthetic-observation:x200-mac",
        "observed_at_utc": (operation_time + timedelta(milliseconds=200)).isoformat(),
    }
    return document


def _rewrite_report_with_valid_checksum(report_path: Path, mutate) -> None:
    envelope = json.loads(report_path.read_text(encoding="utf-8"))
    payload = envelope["payload"]
    mutate(payload)
    canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    report_path.write_text(
        json.dumps(
            {"payload": payload, "sha256": hashlib.sha256(canonical_payload).hexdigest()},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ) + "\n",
        encoding="utf-8",
    )


def _t430_request(checkpoint_payload: dict, operation_time: datetime, capture_time: datetime) -> dict[str, object]:
    evidence = checkpoint_payload["return_evidence"]
    checks = {
        name: {
            "outcome": "pass",
            "evidence_ref": f"synthetic-observation:{name}",
            "observed_at_utc": (operation_time + timedelta(minutes=3)).isoformat(),
        }
        for name in ("cold_boot", "display", "keyboard", "pointing_device", "storage", "sleep_resume")
    }
    checks.update({
        name: {
            "outcome": "pass",
            "evidence_ref": f"synthetic-observation:{name}",
            "observed_at_utc": (operation_time + timedelta(minutes=3)).isoformat(),
        }
        for name in ("advanced_bios_menu", "option_1_default_lenovo_bios", "bios_identity")
    })
    return {
        "schema_version": 1,
        "operator": "operator",
        "boot_session_id": "synthetic-t430-return-session",
        "boot_session_unknown_reason": None,
        "human_reboot_attested": True,
        "human_reboot_evidence_ref": evidence["successful_reboot_evidence_ref"],
        "human_reboot_observed_at_utc": evidence["successful_reboot_observed_at"],
        "checks": checks,
    }


def _make_consumed_t430_checkpoint(tmp_path: Path, monkeypatch) -> Path:
    base = datetime.now(timezone.utc) - timedelta(minutes=10)
    monkeypatch.setattr(t430_fixtures, "BASE", base)
    initial = t430_fixtures.capture(t430_fixtures.t430_device(), "initial", 0)
    checkpoint = tmp_path / "t430-return.json"
    session = T430HandoffSession(initial, checkpoint_path=checkpoint)
    session.record_preflight(t430_fixtures.preflight(initial, actor="operator", uefi=True))
    session.record_onevyrain_media(t430_fixtures.media_evidence(initial, selected_at=base + timedelta(minutes=2)))
    session.approve_onevyrain_handoff(
        actor="operator", explicit_approval=True, approved_at=base + timedelta(minutes=3)
    )
    session.record_post_install_return(
        t430_fixtures.capture(t430_fixtures.t430_device(), "t430-return", 6),
        reported_by="operator",
        user_reported_handoff_complete=True,
        user_reported_successful_reboot=True,
        user_reported_boot_test_passed=True,
        user_reported_1vyrain_validation_passed=True,
        selected_option=t430_fixtures.ONEVYRAIN_OPTION,
        selected_option_evidence_ref="synthetic-observation:option-1",
        selected_option_observed_at=base + timedelta(minutes=4),
        successful_reboot_evidence_ref="synthetic-observation:reboot",
        successful_reboot_observed_at=base + timedelta(minutes=5),
        advanced_bios_menu_observed=True,
        advanced_bios_evidence_ref="synthetic-observation:advanced-bios",
        advanced_bios_observed_at=base + timedelta(minutes=5, seconds=30),
    )
    payload = T430HandoffCheckpointStore.inspect_consumed_return(checkpoint)
    assert payload["checkpoint_status"] == "consumed"
    return checkpoint


@pytest.mark.skipif(sys.platform == "win32", reason="production sidecar durability requires POSIX directory fsync")
def test_x200_pending_report_binds_verified_mac_and_keeps_checkpoint_terminal(
    tmp_path, monkeypatch, synthetic_write_durability
):
    request, result = _completed_x200(tmp_path, monkeypatch)
    checkpoint = Path(request.operation_journal_path)
    operation = PostRebootEvidenceService._open_checkpoint("x200-libreboot", checkpoint)
    captured_at = operation["operation_time"] + timedelta(seconds=2)
    now = captured_at + timedelta(seconds=1)
    capture = _local_capture(
        monkeypatch,
        request.plan.device,
        captured_at,
    )
    expected_mac = operation["expected_x200_mac"]
    document = json.dumps(_x200_request(operation["operation_time"], expected_mac))

    report_path, report = PostRebootEvidenceService.record(
        checkpoint,
        profile_id="x200-libreboot",
        request_document=document,
        now=now,
    )

    assert report["operation_id"] == result.operation_id
    assert report["report_status"] == "PENDING_HUMAN_REVIEW"
    assert report["hardware_success_claimed"] is False
    assert report["local_capture"]["capture_id"] == capture.capture_id
    assert report["checks"]["ethernet_mac"]["matches_expected"] is True
    assert PostRebootEvidenceService.verify_report(
        report_path,
        expected_checkpoint_hash=hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        expected_operation_id=result.operation_id,
    )["acceptance_status"] == "NOT_ASSESSED"
    assert OperationJournal.inspect_checkpoint(checkpoint).state is OperationState.WRITE_VERIFIED


@pytest.mark.skipif(sys.platform == "win32", reason="production sidecar durability requires POSIX directory fsync")
def test_x220_pending_report_reopens_exact_write_checkpoint_and_keeps_it_terminal(
    tmp_path, monkeypatch, synthetic_write_durability
):
    request, result = _completed_x220(tmp_path, monkeypatch)
    checkpoint = Path(request.operation_journal_path)
    operation = PostRebootEvidenceService._open_checkpoint("x220-coreboot", checkpoint)
    captured_at = operation["operation_time"] + timedelta(seconds=2)
    now = captured_at + timedelta(seconds=1)
    capture = _local_capture(monkeypatch, request.plan.device, captured_at)
    document = json.dumps(_x220_request(operation["operation_time"], captured_at))

    report_path, report = PostRebootEvidenceService.record(
        checkpoint,
        profile_id="x220-coreboot",
        request_document=document,
        now=now,
    )

    assert report_path == checkpoint.with_name(checkpoint.name + ".post-reboot-pending.json")
    assert report["report_status"] == "PENDING_HUMAN_REVIEW"
    assert report["hardware_success_claimed"] is False
    assert report["local_capture"]["capture_id"] == capture.capture_id
    assert PostRebootEvidenceService.verify_report(
        report_path,
        expected_checkpoint_hash=hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        expected_operation_id=result.operation_id,
    )["acceptance_status"] == "NOT_ASSESSED"
    assert OperationJournal.inspect_checkpoint(checkpoint).state is OperationState.WRITE_VERIFIED
    report_path.write_bytes(report_path.read_bytes() + b"tamper")
    with pytest.raises(PostRebootEvidenceError, match="malformed JSON|checksum"):
        PostRebootEvidenceService.verify_report(report_path)
    with pytest.raises(PostRebootEvidenceError, match="not new"):
        PostRebootEvidenceService.record(
            checkpoint, profile_id="x220-coreboot", request_document=document, now=now
        )


@pytest.mark.skipif(sys.platform == "win32", reason="production sidecar durability requires POSIX directory fsync")
def test_reopen_revalidates_profile_specific_observation_schema_after_checksum_recomputed(
    tmp_path, monkeypatch, synthetic_write_durability
):
    request, _result = _completed_x220(tmp_path, monkeypatch)
    checkpoint = Path(request.operation_journal_path)
    operation = PostRebootEvidenceService._open_checkpoint("x220-coreboot", checkpoint)
    captured_at = operation["operation_time"] + timedelta(seconds=2)
    now = captured_at + timedelta(seconds=1)
    _local_capture(monkeypatch, request.plan.device, captured_at)
    report_path, _report = PostRebootEvidenceService.record(
        checkpoint,
        profile_id="x220-coreboot",
        request_document=json.dumps(_x220_request(operation["operation_time"], captured_at)),
        now=now,
    )

    _rewrite_report_with_valid_checksum(report_path, lambda payload: payload.update(checks={"cold_boot": {"outcome": "pass"}}))
    with pytest.raises(PostRebootEvidenceError, match="checks are incomplete"):
        PostRebootEvidenceService.verify_report(report_path)


@pytest.mark.skipif(sys.platform == "win32", reason="production sidecar durability requires POSIX directory fsync")
def test_reopen_rejects_recomputed_checksum_with_x200_mac_for_another_rom(
    tmp_path, monkeypatch, synthetic_write_durability
):
    request, _result = _completed_x200(tmp_path, monkeypatch)
    checkpoint = Path(request.operation_journal_path)
    operation = PostRebootEvidenceService._open_checkpoint("x200-libreboot", checkpoint)
    captured_at = operation["operation_time"] + timedelta(seconds=2)
    now = captured_at + timedelta(seconds=1)
    _local_capture(monkeypatch, request.plan.device, captured_at)
    report_path, _report = PostRebootEvidenceService.record(
        checkpoint,
        profile_id="x200-libreboot",
        request_document=json.dumps(_x200_request(operation["operation_time"], operation["expected_x200_mac"])),
        now=now,
    )

    def alter_mac(payload):
        payload["checks"]["ethernet_mac"]["observed_mac"] = "02:00:00:00:00:01"
        payload["checks"]["ethernet_mac"]["expected_mac"] = "02:00:00:00:00:01"

    _rewrite_report_with_valid_checksum(report_path, alter_mac)
    with pytest.raises(PostRebootEvidenceError, match="expected MAC does not match"):
        PostRebootEvidenceService.verify_report(report_path)


@pytest.mark.skipif(sys.platform == "win32", reason="production sidecar durability requires POSIX directory fsync")
@pytest.mark.parametrize("failure", ("stale", "cross-device", "replayed-capture", "incomplete", "wrong-profile"))
def test_x220_post_reboot_intake_rejects_stale_replay_cross_device_and_incomplete(
    tmp_path, monkeypatch, synthetic_write_durability, failure
):
    request, _result = _completed_x220(tmp_path, monkeypatch)
    checkpoint = Path(request.operation_journal_path)
    operation = PostRebootEvidenceService._open_checkpoint("x220-coreboot", checkpoint)
    captured_at = operation["operation_time"] + timedelta(seconds=2)
    now = captured_at + timedelta(seconds=1)
    capture_id = uuid.uuid4().hex
    fingerprint = _SERIAL_FP
    if failure == "stale":
        captured_at = operation["operation_time"] - timedelta(seconds=1)
        now = operation["operation_time"] + timedelta(seconds=1)
    elif failure == "cross-device":
        fingerprint = hashlib.sha256(b"OTHER-DEVICE").hexdigest()
    elif failure == "replayed-capture":
        journal = OperationJournal.inspect_checkpoint(checkpoint)
        start = next(entry for entry in journal.entries if entry.to_state is OperationState.WRITE_STARTED)
        capture_id = json.loads(start.message)["initial_capture_id"]
    capture = _local_capture(monkeypatch, request.plan.device, captured_at, fingerprint=fingerprint, capture_id=capture_id)
    document = _x220_request(operation["operation_time"], captured_at)
    profile = "x220-coreboot"
    if failure == "incomplete":
        document["checks"] = {"cold_boot": {"outcome": "pass"}}
    elif failure == "wrong-profile":
        profile = "x200-libreboot"

    with pytest.raises((PostRebootEvidenceError, ValueError)):
        PostRebootEvidenceService.record(checkpoint, profile_id=profile, request_document=json.dumps(document), now=now)
    assert not checkpoint.with_name(checkpoint.name + ".post-reboot-pending.json").exists()
    assert capture.capture_id
    assert OperationJournal.inspect_checkpoint(checkpoint).state is OperationState.WRITE_VERIFIED


@pytest.mark.skipif(sys.platform == "win32", reason="production sidecar durability requires POSIX directory fsync")
def test_t430_sidecar_binds_consumed_return_and_reuses_checkpointed_reboot_evidence(
    tmp_path, monkeypatch
):
    checkpoint = _make_consumed_t430_checkpoint(tmp_path, monkeypatch)
    payload = T430HandoffCheckpointStore.inspect_consumed_return(checkpoint)
    operation = PostRebootEvidenceService._open_checkpoint("t430-1vyrain", checkpoint)
    capture_time = operation["return_capture_time"] + timedelta(minutes=1)
    now = capture_time + timedelta(seconds=1)
    capture = _local_capture(
        monkeypatch,
        t430_fixtures.t430_device(),
        capture_time,
        fingerprint=t430_fixtures.DEVICE_FP,
        capture_id=uuid.uuid4().hex,
    )
    document = _t430_request(payload, operation["operation_time"], capture_time)

    report_path, report = PostRebootEvidenceService.record(
        checkpoint, profile_id="t430-1vyrain", request_document=json.dumps(document), now=now
    )

    assert report["profile_id"] == "t430-1vyrain"
    assert report["report_status"] == "PENDING_HUMAN_REVIEW"
    assert report["hardware_success_claimed"] is False
    assert report["local_capture"]["capture_id"] == capture.capture_id
    assert report["human_reboot_evidence_ref"] == payload["return_evidence"]["successful_reboot_evidence_ref"]
    reopened = PostRebootEvidenceService.verify_report(report_path)
    assert reopened["checkpoint_operation_fingerprint"] == operation["checkpoint_binding_sha256"]


@pytest.mark.skipif(sys.platform == "win32", reason="production sidecar durability requires POSIX directory fsync")
@pytest.mark.parametrize("failure", ("cross-device", "cross-profile", "altered-checkpoint"))
def test_t430_intake_rejects_checkpoint_mismatch_and_tampering(tmp_path, monkeypatch, failure):
    checkpoint = _make_consumed_t430_checkpoint(tmp_path, monkeypatch)
    payload = T430HandoffCheckpointStore.inspect_consumed_return(checkpoint)
    operation = PostRebootEvidenceService._open_checkpoint("t430-1vyrain", checkpoint)
    capture_time = operation["return_capture_time"] + timedelta(minutes=1)
    now = capture_time + timedelta(seconds=1)
    fingerprint = hashlib.sha256(b"OTHER-DEVICE").hexdigest() if failure == "cross-device" else t430_fixtures.DEVICE_FP
    _local_capture(monkeypatch, t430_fixtures.t430_device(), capture_time, fingerprint=fingerprint)
    profile = "x220-coreboot" if failure == "cross-profile" else "t430-1vyrain"
    document = _t430_request(payload, operation["operation_time"], capture_time)
    if failure == "altered-checkpoint":
        checkpoint.write_bytes(checkpoint.read_bytes() + b"tamper")
    with pytest.raises((PostRebootEvidenceError, ValueError)):
        PostRebootEvidenceService.record(checkpoint, profile_id=profile, request_document=json.dumps(document), now=now)
    assert not checkpoint.with_name(checkpoint.name + ".post-reboot-pending.json").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="production sidecar durability requires POSIX directory fsync")
def test_sidecar_o_excl_lock_allows_only_one_concurrent_report(tmp_path):
    target = tmp_path / "operation.post-reboot-pending.json"
    lock = tmp_path / "operation.post-reboot.lock"
    barrier = threading.Barrier(2)
    results: list[str] = []

    def writer(payload: bytes) -> None:
        barrier.wait()
        try:
            PostRebootEvidenceService._write_once(target, lock, payload)
            results.append("created")
        except PostRebootEvidenceError:
            results.append("rejected")

    workers = [threading.Thread(target=writer, args=(b"one",)), threading.Thread(target=writer, args=(b"two",))]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)
    assert all(not worker.is_alive() for worker in workers)
    assert sorted(results) == ["created", "rejected"]
    assert target.read_bytes() in {b"one", b"two"}


@pytest.mark.skipif(sys.platform == "win32", reason="production sidecar durability requires POSIX directory fsync")
def test_application_controller_boot_check_uses_local_capture_and_shared_pending_service(
    tmp_path, monkeypatch, synthetic_write_durability
):
    request, _result = _completed_x220(tmp_path, monkeypatch)
    checkpoint = Path(request.operation_journal_path)
    operation = PostRebootEvidenceService._open_checkpoint("x220-coreboot", checkpoint)
    captured_at = operation["operation_time"] + timedelta(seconds=2)
    now = captured_at + timedelta(seconds=1)
    capture = _local_capture(monkeypatch, request.plan.device, captured_at)
    document = json.dumps(_x220_request(operation["operation_time"], captured_at))
    calls: list[str] = []

    def forbidden(*_args, **_kwargs):
        calls.append("hardware")
        raise AssertionError("post-reboot intake must not call hardware services")

    monkeypatch.setattr(FlashromRunner, "write_rom", forbidden)
    monkeypatch.setattr(FlashromRunner, "read_rom", forbidden)
    monkeypatch.setattr(FlashromRunner, "run_command", forbidden)
    monkeypatch.setattr(SPIProbeService, "probe", forbidden)
    controller = ApplicationController()
    result = controller.request_action(
        ApplicationAction.BOOT_CHECK,
        profile_id="x220-coreboot",
        checkpoint_path=checkpoint,
        request_document=document,
    )
    assert result.code == "POST_REBOOT_EVIDENCE_PENDING_REVIEW"
    assert result.status is ApplicationStatus.PENDING_HUMAN_REVIEW
    assert any(capture.capture_id in ref for ref in result.evidence_refs)
    assert calls == []
    assert OperationJournal.inspect_checkpoint(checkpoint).state is OperationState.WRITE_VERIFIED


