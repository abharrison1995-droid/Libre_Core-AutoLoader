"""Phase 08 tests for the shared, fail-closed application facade."""
from __future__ import annotations

import json
import hashlib

import pytest

from autoloader.application_controller import (
    ApplicationAction,
    ApplicationController,
    ApplicationStatus,
)
from autoloader.detection.bios import BIOSInfo
from autoloader.detection.model import DetectionResult
from autoloader.flasher.backup_service import BackupSetService
from autoloader.flasher.flashrom import FlashromRunner
from autoloader.rom_builder.artifact_engine import ArtifactEngine
from autoloader.workflow.types import CompatibilityDecision, DeviceEvidence


class _ModelDetector:
    def detect(self):
        return DetectionResult(
            "t430", "ThinkPad T430", "2342", "SERIAL-1", "high",
            raw_product_name="ThinkPad T430",
            raw_product_version="2342-ABC",
            raw_system_family="ThinkPad T430",
            raw_mtm="2342-ABC",
        )


class _BIOSDetector:
    def detect(self, *, force_refresh=False):
        return BIOSInfo("2.64", None, "LENOVO", True, "G1ET74WW (2.64 )")


def _valid_t430_candidate() -> DeviceEvidence:
    return DeviceEvidence(
        raw_dmi_product_name="ThinkPad T430",
        raw_dmi_product_version="2342-ABC",
        raw_dmi_system_family="ThinkPad T430",
        raw_mtm="2342-ABC",
        raw_bios_version="G1ET74WW (2.64 )",
        raw_bios_vendor="LENOVO",
        stock_lenovo_bios=True,
    )


def test_controller_exposes_only_the_three_v1_profiles_and_rejects_generic_models():
    controller = ApplicationController()
    assert controller.profile_ids == ("t430-1vyrain", "x200-libreboot", "x220-coreboot")
    for generic in ("x230", "x200s", "canoeboot", "custom"):
        with pytest.raises(ValueError):
            controller.select_profile(generic)


def test_imported_t430_candidate_never_becomes_handoff_ready_without_fresh_capture():
    controller = ApplicationController()
    status = controller.import_device_evidence(
        "t430-1vyrain", _valid_t430_candidate(), evidence_ref="inventory:captured-by-user"
    )
    assert status.status is ApplicationStatus.UNKNOWN
    assert status.decision is CompatibilityDecision.UNKNOWN
    assert status.stage == "fresh-capture-required"
    assert ApplicationAction.HANDOFF_GUIDANCE not in status.allowed_next_actions
    outcome = controller.request_action(ApplicationAction.HANDOFF_GUIDANCE, profile_id="t430-1vyrain")
    assert outcome.code == "T430_HANDOFF_NOT_READY"
    assert outcome.status is ApplicationStatus.NOT_PERFORMED


def test_fresh_t430_capture_reports_predicate_and_guidance_only():
    controller = ApplicationController(model_detector=_ModelDetector(), bios_detector=_BIOSDetector())
    capture, status = controller.detect_device()
    assert capture.device_fingerprint_sha256
    assert status is not None
    assert status.profile_id == "t430-1vyrain"
    assert status.decision is CompatibilityDecision.ALLOWED
    assert status.status is ApplicationStatus.BIOS_PREDICATE_READY
    assert ApplicationAction.HANDOFF_GUIDANCE in status.allowed_next_actions
    guidance = controller.request_action(ApplicationAction.HANDOFF_GUIDANCE)
    assert guidance.code == "HUMAN_HANDOFF_GUIDANCE_ONLY"
    assert "does not download, execute, write media, approve, or perform" in guidance.message
    assert guidance.progress_percent is None


def test_import_document_is_strict_candidate_data_and_never_compatibility_approval():
    controller = ApplicationController()
    document = json.dumps({
        "raw_dmi_product_name": "ThinkPad X200",
        "raw_mtm": "7455-ABC",
        "raw_bios_vendor": "LENOVO",
        "stock_lenovo_bios": True,
    })
    ref = controller.evidence_document_reference(document)
    status = controller.import_device_evidence_document("x200-libreboot", document, evidence_ref=ref)
    assert status.status is ApplicationStatus.UNKNOWN
    assert status.decision is CompatibilityDecision.UNKNOWN
    assert status.evidence_refs == (ref,)
    with pytest.raises(ValueError, match="unsupported fields"):
        controller.import_device_evidence_document(
            "x200-libreboot", '{"raw_mtm":"7455","verified":true}', evidence_ref="source:1"
        )
    with pytest.raises(ValueError, match="boolean"):
        controller.import_device_evidence_document(
            "x200-libreboot", '{"stock_lenovo_bios":"yes"}', evidence_ref="source:2"
        )


def test_candidate_store_persists_candidate_only_and_rejects_wrong_profile(tmp_path):
    from autoloader.workflow.candidate_evidence import CandidateEvidenceStore

    path = tmp_path / "candidate.json"
    CandidateEvidenceStore.save(
        path, profile_id="x200-libreboot", evidence=_valid_t430_candidate(), evidence_ref="inventory:one"
    )
    controller = ApplicationController()
    with pytest.raises(ValueError, match="does not match selected profile"):
        controller.load_candidate_evidence(path, expected_profile="x220-coreboot")
    status = controller.load_candidate_evidence(path, expected_profile="x200-libreboot")
    assert status.decision is CompatibilityDecision.UNKNOWN
    assert status.status is ApplicationStatus.UNKNOWN
    assert status.evidence_refs == ("inventory:one",)
    assert "candidate" in path.read_text(encoding="utf-8")


def test_candidate_store_rejects_wrong_typed_values_even_with_recomputed_checksum(tmp_path):
    from autoloader.workflow.candidate_evidence import CandidateEvidenceError, CandidateEvidenceStore

    path = tmp_path / "candidate.json"
    CandidateEvidenceStore.save(
        path, profile_id="x200-libreboot", evidence=DeviceEvidence(raw_mtm="7455-ABC"), evidence_ref="inventory:one"
    )
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["payload"]["evidence"]["raw_mtm"] = 42
    encoded_payload = json.dumps(
        envelope["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    envelope["payload_sha256"] = hashlib.sha256(encoded_payload).hexdigest()
    path.write_text(json.dumps(envelope, sort_keys=True, separators=(",", ":")), encoding="utf-8")

    with pytest.raises(CandidateEvidenceError, match="raw_mtm must be a string"):
        CandidateEvidenceStore.load(path)


def test_controller_inspects_operation_journal_read_only(tmp_path):
    from autoloader.workflow.journal import OperationJournal
    from autoloader.workflow.types import (
        ArtifactEvidence, ChipEvidence, ChipTopology, FirmwareFamily, OperationPlan,
        ProgrammerEvidence, ProgrammerKind, RegionEvidence,
    )

    plan = OperationPlan(
        profile_id="x200-libreboot",
        requested_model_id="x200",
        firmware_family=FirmwareFamily.LIBREBOOT,
        device=DeviceEvidence(raw_mtm="7455-ABC"),
        topology=ChipTopology(True, (ChipEvidence("spi0", 4 * 1024 * 1024, True, voltage_mv=3300),), ()),
        programmer=ProgrammerEvidence(ProgrammerKind.EXTERNAL_SPI, True, True, True, 3300),
        artifact=ArtifactEvidence(
            "C:/staged/coreboot.rom", "x200", FirmwareFamily.LIBREBOOT, "a" * 64,
            4 * 1024 * 1024, "https://example.test/source", "1" * 40,
            (RegionEvidence("COREBOOT", 0, 4 * 1024 * 1024),),
        ),
    )
    path = tmp_path / "operation.json"
    OperationJournal(path, plan=plan)
    result = ApplicationController().request_action(
        ApplicationAction.INSPECT_CHECKPOINT, checkpoint_path=path
    )
    assert result.code == "CHECKPOINT_INSPECTED_READ_ONLY"
    assert "State: draft" in result.message
    assert any(ref.startswith("operation:") for ref in result.evidence_refs)


def test_attached_staged_workflow_exposes_operation_summary_and_checkpoint_action(tmp_path):
    from types import SimpleNamespace
    from autoloader.workflow.journal import OperationJournal
    from autoloader.workflow.types import (
        ArtifactEvidence, ChipEvidence, ChipTopology, FirmwareFamily, OperationPlan,
        ProgrammerEvidence, ProgrammerKind, RegionEvidence,
    )

    journal_path = tmp_path / "x200.json"
    plan = OperationPlan(
        profile_id="x200-libreboot",
        requested_model_id="x200",
        firmware_family=FirmwareFamily.LIBREBOOT,
        device=DeviceEvidence(raw_dmi_product_name="ThinkPad X200", raw_mtm="7455-ABC"),
        topology=ChipTopology(True, (ChipEvidence("spi0", 4 * 1024 * 1024, True, voltage_mv=3300),), (
            RegionEvidence("COREBOOT", 0, 4 * 1024 * 1024),
        )),
        programmer=ProgrammerEvidence(ProgrammerKind.EXTERNAL_SPI, True, True, True, 3300),
        artifact=ArtifactEvidence(
            "C:/staged/coreboot.rom", "x200", FirmwareFamily.LIBREBOOT, "a" * 64,
            4 * 1024 * 1024, "https://example.test/source", "1" * 40,
            (RegionEvidence("COREBOOT", 0, 4 * 1024 * 1024),),
        ),
    )
    journal = OperationJournal(journal_path, plan=plan)
    intent = SimpleNamespace(
        profile_id="x200-libreboot",
        device=plan.device,
        programmer=plan.programmer,
        topology=plan.topology,
    )
    workflow = SimpleNamespace(
        intent=intent,
        operation_id=journal.operation_id,
        operation_journal_path=str(journal_path),
        result=SimpleNamespace(
            status="STAGED/HARDWARE_PENDING",
            stage=SimpleNamespace(value="staged"),
            reasons=(),
            operation_id=journal.operation_id,
            compatibility=SimpleNamespace(decision=CompatibilityDecision.ALLOWED),
        ),
        backup_set=None,
        recovery_readiness=None,
        proposed_external_spi_plan=None,
    )
    status = ApplicationController().attach_x200_workflow(workflow)
    assert status.operation_id == journal.operation_id
    assert status.checkpoint_ref == str(journal_path)
    assert ApplicationAction.INSPECT_CHECKPOINT in status.allowed_next_actions
    assert any("3300 mV" in item for item in status.evidence_summary)
    assert any("Observed region COREBOOT" in item for item in status.evidence_summary)


def test_resume_action_is_t430_only_and_uses_validated_session_loader(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from autoloader.reversion.t430_handoff import T430HandoffSession, T430HandoffStage
    from autoloader.reversion.t430_handoff import T430Assessment

    fake_session = SimpleNamespace(
        operation_id="a" * 32,
        stage=T430HandoffStage.AWAITING_IVPREP_REDETECTION,
        assessment=T430Assessment(T430HandoffStage.IVPREP_REQUIRED, CompatibilityDecision.BLOCKED, 264, ("ivprep required",)),
        checkpoint_path=tmp_path / "pending.json",
        _device_fingerprint_sha256="b" * 64,
        _capture_id="capture-1",
        _observed_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        _preflight=None,
        _media=None,
        _approved_actor="operator",
        _approval_timestamp=None,
    )
    monkeypatch.setattr(T430HandoffSession, "resume_from_checkpoint", lambda path, validator=None: fake_session)
    controller = ApplicationController()
    result = controller.request_action(
        ApplicationAction.RESUME, profile_id="t430-1vyrain", checkpoint_path=tmp_path / "pending.json"
    )
    assert result.code == "T430_CHECKPOINT_RESUMED"
    assert controller.profile_status().operation_id == "a" * 32

    x200 = ApplicationController()
    x200.select_profile("x200-libreboot")
    blocked = x200.request_action(ApplicationAction.RESUME, checkpoint_path=tmp_path / "pending.json")
    assert blocked.code == "SAFE_RESUME_UNAVAILABLE"
    assert ApplicationAction.RESUME not in x200.profile_status().allowed_next_actions


def test_detection_during_t430_handoff_is_candidate_only_and_does_not_advance(monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from autoloader.reversion.t430_handoff import T430Assessment, T430HandoffStage

    session = SimpleNamespace(
        operation_id="c" * 32,
        stage=T430HandoffStage.AWAITING_IVPREP_REDETECTION,
        assessment=T430Assessment(T430HandoffStage.IVPREP_REQUIRED, CompatibilityDecision.BLOCKED, 264, ("waiting for IVprep return",)),
        checkpoint_path=None,
        _device_fingerprint_sha256="d" * 64,
        _capture_id="old-capture",
        _observed_at=datetime.now(timezone.utc),
        _preflight=None,
        _media=None,
        _approved_actor="operator",
        _approval_timestamp=None,
    )
    controller = ApplicationController(model_detector=_ModelDetector(), bios_detector=_BIOSDetector())
    controller.attach_t430_session(session)
    outcome = controller.request_action(ApplicationAction.DETECT_DEVICE, profile_id="t430-1vyrain")
    assert outcome.code == "T430_RETURN_CAPTURE_CANDIDATE_ONLY"
    assert controller.capture is not None
    assert controller.capture.capture_id != session._capture_id
    assert session.stage is T430HandoffStage.AWAITING_IVPREP_REDETECTION
    assert controller.profile_status().stage == T430HandoffStage.AWAITING_IVPREP_REDETECTION.value


def test_controller_advertises_only_routed_safe_actions_without_active_workflow():
    controller = ApplicationController()
    for profile_id in controller.profile_ids:
        status = controller.select_profile(profile_id)
        expected = (ApplicationAction.DETECT_DEVICE, ApplicationAction.IMPORT_EVIDENCE)
        if profile_id == "t430-1vyrain":
            expected += (ApplicationAction.RESUME,)
        assert status.allowed_next_actions == expected
        assert controller.request_action(ApplicationAction.VIEW_EVIDENCE).code == "PROFILE_STATUS"
        missing = controller.request_action(ApplicationAction.IMPORT_EVIDENCE)
        assert missing.code == "TYPED_EVIDENCE_REQUIRED"


def test_mutating_or_hardware_actions_fail_closed_with_no_writer_build_or_spi_calls(monkeypatch):
    controller = ApplicationController()
    controller.select_profile("x200-libreboot")
    calls: list[str] = []

    def unexpected(*args, **kwargs):
        calls.append("called")
        raise AssertionError("controller action crossed a quarantined boundary")

    monkeypatch.setattr(FlashromRunner, "run_command", unexpected)
    monkeypatch.setattr(FlashromRunner, "read_rom", unexpected)
    monkeypatch.setattr(FlashromRunner, "write_rom", unexpected)
    monkeypatch.setattr(BackupSetService, "capture", unexpected)
    monkeypatch.setattr(ArtifactEngine, "build", unexpected)

    expected_codes = {
        ApplicationAction.BACKUP: "BACKUP_UNAVAILABLE",
        ApplicationAction.BUILD: "BUILD_UNAVAILABLE",
        ApplicationAction.FLASH: "FLASH_UNAVAILABLE",
        ApplicationAction.RESTORE: "RESTORE_UNAVAILABLE",
        ApplicationAction.READBACK: "READBACK_NOT_PERFORMED",
        ApplicationAction.BOOT_CHECK: "BOOT_CHECK_NOT_PERFORMED",
    }
    for action, expected_code in expected_codes.items():
        result = controller.request_action(action)
        assert result.code == expected_code
        assert result.status is ApplicationStatus.NOT_PERFORMED
        assert result.progress_percent is None
    assert calls == []


def test_backup_manifest_verification_does_not_create_a_default_manifest(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    controller = ApplicationController()
    outcome = controller.request_action(ApplicationAction.VERIFY_BACKUPS)
    assert outcome.code == "BACKUP_MANIFEST_NOT_CONFIGURED"
    assert not (tmp_path / "backups").exists()
