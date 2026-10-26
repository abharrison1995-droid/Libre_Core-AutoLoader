"""Fake-only tests for read-only persisted device readiness review."""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import autoloader.workflow.journal as journal_module
import autoloader.workflow.write_approval as approval_module
from autoloader.application_controller import ApplicationAction, ApplicationController, ApplicationStatus
from autoloader.flasher.flashrom import FlashromRunner
from autoloader.flasher.spi import SPIProbeService
from autoloader.reversion.t430_handoff import T430HandoffCheckpointStore
from autoloader.workflow.device_readiness import DeviceReadinessReportError, DeviceReadinessReportService
from autoloader.workflow.journal import OperationJournal
from autoloader.workflow.types import OperationState
from tests.test_phase10b_write_service import _service_request
from tests.test_post_reboot_evidence import _make_consumed_t430_checkpoint
from tests import test_t430_handoff as t430_fixtures


@pytest.fixture
def synthetic_durability(monkeypatch):
    monkeypatch.setattr(approval_module, "_directory_fsync_supported", lambda: True)
    monkeypatch.setattr(approval_module, "_sync_directory", lambda _path: None)
    monkeypatch.setattr(journal_module, "_directory_fsync_supported", lambda: True)
    monkeypatch.setattr(journal_module, "_sync_directory", lambda _path: None)


def _completed_write(tmp_path, monkeypatch, *, x220: bool = False):
    service, request, writer, readback = _service_request(tmp_path, monkeypatch, x220=x220)
    result = service.execute(request)
    assert result.state is OperationState.WRITE_VERIFIED
    return request, result, writer, readback


def test_x220_report_reopens_artifact_readback_and_backup_bytes_without_hardware(
    tmp_path, monkeypatch, synthetic_durability
):
    request, result, writer, readback = _completed_write(tmp_path, monkeypatch, x220=True)
    checkpoint = Path(request.operation_journal_path)
    before_checkpoint = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    journal = OperationJournal.inspect_checkpoint(checkpoint)
    before_backups = {item.path: hashlib.sha256(Path(item.path).read_bytes()).hexdigest() for item in journal.backup_checksums}
    report = DeviceReadinessReportService.inspect("x220-coreboot", checkpoint)

    assert report.outcome == "REVIEW_INPUTS_COMPLETE"
    assert report.operation_id == result.operation_id
    assert report.hardware_ready is False and report.write_authorized is False
    assert {gate.name: gate.status for gate in report.gates}["artifact-readback"] == "PASS"
    assert {gate.name: gate.status for gate in report.gates}["backup-bytes"] == "PASS"
    assert {gate.name: gate.status for gate in report.gates}["recovery-manifests-and-volumes"] == "UNKNOWN"
    assert len(writer.calls) == 1 and len(readback.calls) == 1
    assert hashlib.sha256(checkpoint.read_bytes()).hexdigest() == before_checkpoint
    assert before_backups == {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in before_backups}


def test_x200_report_reuses_final_rom_mac_validator_and_keeps_hardware_unknown(
    tmp_path, monkeypatch, synthetic_durability
):
    request, _result, _writer, _readback = _completed_write(tmp_path, monkeypatch)
    report = DeviceReadinessReportService.inspect("x200-libreboot", request.operation_journal_path)

    gates = {gate.name: gate.status for gate in report.gates}
    assert report.outcome == "REVIEW_INPUTS_COMPLETE"
    assert gates["x200-mac-preservation"] == "PASS"
    assert gates["current-device-and-boot-state"] == "UNKNOWN"
    assert report.hardware_ready is False
    assert report.write_authorized is False


def test_t430_report_uses_only_consumed_return_and_does_not_claim_flash_success(tmp_path, monkeypatch):
    checkpoint = _make_consumed_t430_checkpoint(tmp_path, monkeypatch)
    payload = T430HandoffCheckpointStore.inspect_consumed_return(checkpoint)
    before = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    report = DeviceReadinessReportService.inspect("t430-1vyrain", checkpoint)

    assert report.operation_id == payload["operation_id"]
    assert report.outcome == "REVIEW_INPUTS_COMPLETE"
    assert {gate.name: gate.status for gate in report.gates}["consumed-t430-return"] == "PASS"
    assert {gate.name: gate.status for gate in report.gates}["firmware-command-and-acceptance"] == "UNKNOWN"
    assert hashlib.sha256(checkpoint.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("case", ("missing", "malformed", "wrong-profile", "stale"))
def test_report_rejects_missing_malformed_mismatched_and_stale_checkpoint(
    tmp_path, monkeypatch, synthetic_durability, case
):
    request, _result, _writer, _readback = _completed_write(tmp_path, monkeypatch, x220=True)
    checkpoint = Path(request.operation_journal_path)
    if case == "missing":
        checkpoint = tmp_path / "missing.json"
    elif case == "malformed":
        checkpoint.write_bytes(b"not-json")
    elif case == "stale":
        report_time = datetime.now(timezone.utc) + timedelta(days=2)
        with pytest.raises(DeviceReadinessReportError, match="recency limit"):
            DeviceReadinessReportService.inspect("x220-coreboot", checkpoint, now=report_time)
        return
    profile = "x200-libreboot" if case == "wrong-profile" else "x220-coreboot"
    with pytest.raises(DeviceReadinessReportError):
        DeviceReadinessReportService.inspect(profile, checkpoint)


def test_report_rejects_changed_backup_bytes_and_symlinked_checkpoint(
    tmp_path, monkeypatch, synthetic_durability
):
    request, _result, _writer, _readback = _completed_write(tmp_path, monkeypatch, x220=True)
    checkpoint = Path(request.operation_journal_path)
    journal = OperationJournal.inspect_checkpoint(checkpoint)
    Path(journal.backup_checksums[0].path).write_bytes(b"changed")
    with pytest.raises(DeviceReadinessReportError, match="backup copy changed"):
        DeviceReadinessReportService.inspect("x220-coreboot", checkpoint)

    link = tmp_path / "checkpoint-link.json"
    try:
        os.symlink(checkpoint, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this host")
    with pytest.raises(DeviceReadinessReportError, match="symlink or junction"):
        DeviceReadinessReportService.inspect("x220-coreboot", link)


def test_controller_routes_readiness_review_without_hardware_calls(
    tmp_path, monkeypatch, synthetic_durability
):
    request, _result, _writer, _readback = _completed_write(tmp_path, monkeypatch, x220=True)
    calls: list[str] = []

    def forbidden(*_args, **_kwargs):
        calls.append("hardware")
        raise AssertionError("readiness review must not probe, read or write hardware")

    monkeypatch.setattr(FlashromRunner, "write_rom", forbidden)
    monkeypatch.setattr(FlashromRunner, "read_rom", forbidden)
    monkeypatch.setattr(FlashromRunner, "run_command", forbidden)
    monkeypatch.setattr(SPIProbeService, "probe", forbidden)
    result = ApplicationController().request_action(
        ApplicationAction.REVIEW_READINESS,
        profile_id="x220-coreboot",
        checkpoint_path=request.operation_journal_path,
    )

    assert result.code == "REVIEW_INPUTS_COMPLETE"
    assert result.status is ApplicationStatus.UNKNOWN
    assert "does not establish current hardware state" in result.message
    assert calls == []
    assert OperationJournal.inspect_checkpoint(request.operation_journal_path).state is OperationState.WRITE_VERIFIED
