"""Phase 10A fake-only tests for evidence-bound external SPI approvals."""
from __future__ import annotations

import hashlib
import io
import json
import pickle
import subprocess
import threading
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import autoloader.workflow.write_approval as approval_module
from autoloader.flasher.flashrom import ProgrammerType
from autoloader.flasher.recovery import RecoveryReadinessService
from autoloader.workflow.authorization import AuthorizationReusedError, AuthorizationStaleError
from autoloader.workflow.types import ChipEvidence, ProgrammerEvidence, RegionEvidence
from autoloader.workflow.write_approval import (
    ConfirmationChannel,
    DeviceCaptureEvidence,
    HumanActionConfirmation,
    TUIConfirmationEvent,
    WriteAction,
    WriteApprovalError,
    WriteApprovalLedger,
    WriteIntentFactory,
    accept_tui_confirmation,
    collect_cli_confirmation,
    create_write_approval_challenge,
)
from tests.test_phase03_spi_safety import (
    FakeTransport,
    MIB,
    _capture,
    _make_service,
    _make_x200,
    _make_x220,
    _payload,
    _probe,
)


@pytest.fixture(autouse=True)
def _fake_durable_directory_sync(monkeypatch):
    """The approval persistence fixtures model a POSIX directory-fsync host."""
    monkeypatch.setattr(approval_module, "_directory_fsync_supported", lambda: True)
    monkeypatch.setattr(approval_module, "_sync_directory", lambda _directory: None)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prepared_write(tmp_path: Path):
    """Create synthetic local evidence; all SPI reads use a fake transport."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    base = _make_x200(tmp_path)
    now = _iso_now()
    chip = replace(
        base.topology.chips[0],
        voltage_evidence_source="operator DMM measurement",
        voltage_attested_by="Test Operator",
        voltage_attested_at=now,
        voltage_instrument_id="DMM-SYNTHETIC-01",
        voltage_test_point="programmer VCC test point",
    )
    programmer = replace(
        base.programmer,
        evidence_source="operator physical setup attestation",
        attested_by="Test Operator",
        attested_at=now,
        instrument_id="DMM-SYNTHETIC-01",
        measurement_test_point="programmer VCC test point",
    )
    topology = replace(base.topology, chips=(chip,))
    payload = _payload(4 * MIB, 47)
    transport = FakeTransport(
        ['Found Winbond flash chip "W25Q32.V" (4096 kB, SPI) on ch341a_spi.\n'],
        {4 * MIB: payload},
    )
    probes = _probe(transport)
    from autoloader.workflow.x200_libreboot import X200LibrebootWorkflow, create_x200_build_intent
    from tests.test_x200_libreboot_workflow import _fake_complete_result

    build_intent = create_x200_build_intent(device=base.device, topology=topology, programmer=programmer)
    backup_service, primary, recovery = _make_service(tmp_path, transport)
    workflow = X200LibrebootWorkflow(build_intent, journal_path=tmp_path / "operation.json")
    assert workflow.validate_build_intent().decision.value == "allowed"
    workflow.capture_backup(
        backup_service,
        probes,
        programmer=ProgrammerType.CH341A,
        primary_directory=primary,
        recovery_directory=recovery,
        primary_persistent_confirmed=True,
        recovery_persistent_confirmed=True,
    )
    loaded = workflow.backup_set
    readiness = workflow.recovery_readiness
    assert loaded is not None and readiness is not None and readiness.status == "ready"
    synthetic_artifact = _fake_complete_result(tmp_path / "artifact", workflow.intent)
    staged = workflow.accept_artifact_result(synthetic_artifact)
    assert staged.status == "STAGED/HARDWARE_PENDING", staged.reasons
    assert staged.operation_plan is not None and staged.proposed_external_spi_plan is not None
    bound_plan = staged.operation_plan
    operation_id = staged.operation_id
    accepted_paths = {
        "operation_journal_path": staged.proposed_external_spi_plan.operation_journal_path,
        "artifact_manifest_path": staged.proposed_external_spi_plan.artifact_manifest_path,
    }
    capture = DeviceCaptureEvidence(
        capture_id=str(uuid.uuid4()),
        observed_at_utc=_iso_now(),
        device=bound_plan.device,
    )
    intent = WriteIntentFactory(backup_service).create(
        bound_plan,
        operation_id=operation_id,
        capture=capture,
        probe=probes[0],
        backup_set=loaded,
        recovery_readiness=readiness,
        **accepted_paths,
    )
    return bound_plan, intent, backup_service, probes[0], loaded, readiness, transport, accepted_paths, operation_id


def _prepared_x220_write(tmp_path: Path, monkeypatch):
    tmp_path.mkdir(parents=True, exist_ok=True)
    base = _make_x220(tmp_path)
    now = _iso_now()
    chip = replace(
        base.topology.chips[0],
        voltage_evidence_source="operator DMM measurement",
        voltage_attested_by="Test Operator",
        voltage_attested_at=now,
        voltage_instrument_id="DMM-SYNTHETIC-01",
        voltage_test_point="programmer VCC test point",
    )
    programmer = replace(
        base.programmer,
        evidence_source="operator physical setup attestation",
        attested_by="Test Operator",
        attested_at=now,
        instrument_id="DMM-SYNTHETIC-01",
        measurement_test_point="programmer VCC test point",
    )
    regions = (
        RegionEvidence("ifd", 0, 0x1000),
        RegionEvidence("gbe", 0x1000, 0x3000),
        RegionEvidence("me", 0x3000, 0x500000),
        RegionEvidence("bios", 0x500000, 8 * MIB),
    )
    topology = replace(base.topology, chips=(chip,), regions=regions)
    payload = _payload(8 * MIB, 61)
    transport = FakeTransport(
        ['Found Winbond flash chip "W25Q64.V" (8192 kB, SPI) on ch341a_spi.\n'],
        {8 * MIB: payload},
    )
    probes = _probe(transport)
    import autoloader.workflow.x220_coreboot as workflow_module
    from autoloader.rom_builder.artifact_engine import COREBOOT_2609, X220_COREBOOT, PinnedFile
    from autoloader.workflow.x220_coreboot import X220CorebootWorkflow, create_x220_build_intent
    from tests.test_x220_coreboot_workflow import _fake_result

    fake_files = tuple(
        PinnedFile(item.path, hashlib.sha256(("synthetic pinned source fixture:" + item.path).encode()).hexdigest())
        for item in COREBOOT_2609.files
    )
    fake_pin = replace(COREBOOT_2609, files=fake_files)
    monkeypatch.setattr(workflow_module, "COREBOOT_2609", fake_pin)
    monkeypatch.setattr(workflow_module, "X220_COREBOOT", replace(X220_COREBOOT, source=fake_pin))
    build_intent = create_x220_build_intent(device=base.device, topology=topology, programmer=programmer)
    service, primary, recovery = _make_service(tmp_path, transport)
    workflow = X220CorebootWorkflow(build_intent, journal_path=tmp_path / "operation.json")
    assert workflow.validate_build_intent().decision.value == "allowed"
    workflow.capture_backup(
        service,
        probes,
        programmer=ProgrammerType.CH341A,
        primary_directory=primary,
        recovery_directory=recovery,
        primary_persistent_confirmed=True,
        recovery_persistent_confirmed=True,
    )
    loaded = workflow.backup_set
    readiness = workflow.recovery_readiness
    assert loaded is not None and readiness is not None and readiness.status == "ready"
    synthetic_artifact = _fake_result(
        tmp_path / "artifact", workflow, fake_pin,
        {item.path: ("synthetic pinned source fixture:" + item.path).encode() for item in fake_pin.files},
    )
    staged = workflow.accept_artifact_result(synthetic_artifact)
    assert staged.status == "STAGED/HARDWARE_PENDING", staged.reasons
    assert staged.operation_plan is not None and staged.proposed_external_spi_plan is not None
    bound_plan = staged.operation_plan
    operation_id = staged.operation_id
    accepted_paths = {
        "operation_journal_path": staged.proposed_external_spi_plan.operation_journal_path,
        "artifact_manifest_path": staged.proposed_external_spi_plan.artifact_manifest_path,
    }
    capture = DeviceCaptureEvidence(str(uuid.uuid4()), _iso_now(), bound_plan.device)
    intent = WriteIntentFactory(service).create(
        bound_plan,
        operation_id=operation_id,
        capture=capture,
        probe=probes[0],
        backup_set=loaded,
        recovery_readiness=readiness,
        **accepted_paths,
    )
    return bound_plan, intent, service, probes[0], loaded, readiness, transport, accepted_paths, operation_id


class _TTYInput(io.StringIO):
    def fileno(self) -> int:
        return 91

    def isatty(self) -> bool:
        return False


class _TTYOutput(io.StringIO):
    def fileno(self) -> int:
        return 92

    def isatty(self) -> bool:
        return False


def _confirmation(intent):
    challenge = create_write_approval_challenge(intent)
    typed = _TTYInput(challenge.exact_phrase + "\n")
    displayed = _TTYOutput()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(approval_module.sys, "stdin", typed)
        patch.setattr(approval_module.sys, "stdout", displayed)
        # Explicit unit-test adapter for the otherwise real OS-handle gate.
        # Production callers cannot pass a stream or select this adapter.
        patch.setattr(
            approval_module,
            "_require_tty_file_descriptor",
            lambda _stream, label: 91 if label == "input" else 92,
        )
        patch.setattr(approval_module.os, "isatty", lambda descriptor: descriptor in {91, 92})
        evidence = collect_cli_confirmation(challenge, actor="Test Operator")
    return challenge, evidence, displayed.getvalue()


def test_x200_intent_binds_fresh_probe_artifact_backup_recovery_and_fixed_command(tmp_path):
    plan, intent, _service, probe, backup, readiness, _transport, _paths, _operation_id = _prepared_write(tmp_path)
    intent.validate_integrity()
    assert intent.action is WriteAction.FLASH
    assert intent.profile_id == "x200-libreboot"
    assert intent.model_id == "x200"
    assert intent.probe_id == probe.probe_id
    assert intent.chip_name == "W25Q32.V"
    assert intent.chip_capacity_bytes == 4 * MIB
    assert intent.artifact_sha256 == plan.artifact.expected_sha256
    assert intent.backup_operation_id == backup.operation_id
    assert intent.primary_manifest_sha256 == backup.primary_manifest_sha256
    assert intent.recovery_manifest_sha256 == backup.recovery_manifest_sha256
    assert intent.recovery_record_id == readiness.record_id
    assert intent.command.argv == (
        "-p", ProgrammerType.CH341A.value, "-c", "W25Q32.V", "-w", intent.artifact_path,
    )
    assert "-w" not in intent.command.executable
    assert intent.command.executable_sha256 == hashlib.sha256(Path(intent.command.executable).read_bytes()).hexdigest()


def test_x220_intent_binds_full_chip_coreboot_regions_and_backup(tmp_path, monkeypatch):
    plan, intent, _service, probe, backup, _readiness, _transport, _paths, _operation_id = _prepared_x220_write(tmp_path, monkeypatch)
    intent.validate_integrity()
    assert intent.profile_id == "x220-coreboot"
    assert intent.model_id == "x220"
    assert intent.chip_capacity_bytes == 8 * MIB
    assert intent.artifact_size_bytes == 8 * MIB
    assert intent.artifact_regions == plan.topology.regions
    assert intent.probe_id == probe.probe_id
    assert intent.backup_operation_id == backup.operation_id
    assert intent.command.argv == (
        "-p", ProgrammerType.CH341A.value, "-c", "W25Q64.V", "-w", intent.artifact_path,
    )


def test_cli_confirmation_requires_real_os_tty_and_exact_phrase(tmp_path, monkeypatch):
    _plan, intent, *_ = _prepared_write(tmp_path)
    challenge = create_write_approval_challenge(intent)
    with pytest.raises(WriteApprovalError, match="real process stdin/stdout"):
        collect_cli_confirmation(
            challenge, actor="Operator", input_stream=_TTYInput(challenge.exact_phrase), output_stream=_TTYOutput()
        )
    wrapped_input = _TTYInput(challenge.exact_phrase + "\n")
    wrapped_output = _TTYOutput()
    monkeypatch.setattr(approval_module.sys, "stdin", wrapped_input)
    monkeypatch.setattr(approval_module.sys, "stdout", wrapped_output)
    monkeypatch.setattr(approval_module.os, "isatty", lambda _descriptor: False)
    with pytest.raises(WriteApprovalError, match="OS TTY input"):
        collect_cli_confirmation(challenge, actor="Operator")
    monkeypatch.setattr(
        approval_module,
        "_require_tty_file_descriptor",
        lambda _stream, label: 91 if label == "input" else 92,
    )
    monkeypatch.setattr(approval_module.os, "isatty", lambda descriptor: descriptor in {91, 92})
    monkeypatch.setattr(approval_module.sys, "stdin", _TTYInput("FLASH X200 WRONG\n"))
    with pytest.raises(WriteApprovalError, match="exact write challenge"):
        collect_cli_confirmation(challenge, actor="Operator")
    monkeypatch.setattr(approval_module.sys, "stdin", _TTYInput(challenge.exact_phrase + "\n"))
    evidence = collect_cli_confirmation(challenge, actor="Operator")
    assert evidence.channel is ConfirmationChannel.CLI_TTY
    assert challenge.exact_phrase in create_write_approval_challenge(intent).exact_phrase


def test_programmatic_boolean_json_and_fabricated_confirmation_cannot_issue(tmp_path):
    _plan, intent, *_ = _prepared_write(tmp_path)
    ledger = WriteApprovalLedger(tmp_path / "approvals")
    with pytest.raises(WriteApprovalError, match="not produced"):
        create_write_approval_challenge(replace(intent))
    with pytest.raises(WriteApprovalError, match="confirmation evidence"):
        HumanActionConfirmation(  # type: ignore[call-arg]
            challenge_sha256="0" * 64,
            exact_phrase="FLASH X200",
            actor="Operator",
            confirmed_at_utc=_iso_now(),
            channel=ConfirmationChannel.CLI_TTY,
        )
    with pytest.raises(WriteApprovalError, match="typed write intent"):
        ledger.issue(intent, True)  # type: ignore[arg-type]
    with pytest.raises(WriteApprovalError, match="typed write intent"):
        ledger.issue(intent, {"approved": True})  # type: ignore[arg-type]


def test_approval_is_one_use_durable_and_does_not_call_writer_or_process(tmp_path, monkeypatch):
    _plan, intent, *_ = _prepared_write(tmp_path)
    ledger = WriteApprovalLedger(tmp_path / "approvals")
    challenge, confirmation, display = _confirmation(intent)
    assert "PHYSICAL FIRMWARE WRITE" in display
    assert intent.chip_name in display
    token = ledger.issue(intent, confirmation)

    def no_process(*args, **kwargs):
        pytest.fail("Phase 10A approval must not spawn a process")

    monkeypatch.setattr(subprocess, "run", no_process)
    from autoloader.flasher.flashrom import FlashromRunner
    monkeypatch.setattr(FlashromRunner, "write_rom", no_process)
    receipt = ledger.consume(token, intent)
    assert receipt.intent_sha256 == intent.intent_sha256
    assert (tmp_path / "approvals" / f"{token.approval_id}.consumed").is_file()
    with pytest.raises(AuthorizationReusedError):
        ledger.consume(token, intent)
    marker = tmp_path / "approvals" / f"{token.approval_id}.consumed"
    marker.unlink()
    with pytest.raises(AuthorizationReusedError, match="process"):
        ledger.consume(token, intent)
    record_text = (tmp_path / "approvals" / f"{token.approval_id}.approval.json").read_text(encoding="utf-8")
    assert token.secret not in record_text
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(token)
    with pytest.raises(WriteApprovalError, match="already used"):
        ledger.issue(intent, confirmation)
    assert challenge.intent_sha256 == intent.intent_sha256


def test_stale_intent_is_consumed_and_cannot_be_replayed(tmp_path):
    plan, intent, service, probe, backup, readiness, _transport, paths, operation_id = _prepared_write(tmp_path)
    ledger = WriteApprovalLedger(tmp_path / "approvals")
    _challenge, confirmation, _display = _confirmation(intent)
    token = ledger.issue(intent, confirmation)
    changed = WriteIntentFactory(service).create(
        plan,
        operation_id=operation_id,
        capture=DeviceCaptureEvidence(str(uuid.uuid4()), _iso_now(), plan.device),
        probe=probe,
        backup_set=backup,
        recovery_readiness=readiness,
        **paths,
    )
    with pytest.raises(AuthorizationStaleError):
        ledger.consume(token, changed)
    with pytest.raises(AuthorizationReusedError):
        ledger.consume(token, intent)


def test_expired_approval_is_consumed_and_cannot_be_replayed(tmp_path):
    _plan, intent, *_ = _prepared_write(tmp_path)
    ledger = WriteApprovalLedger(tmp_path / "approvals")
    _challenge, confirmation, _display = _confirmation(intent)
    token = ledger.issue(intent, confirmation)
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
    record_path = tmp_path / "approvals" / f"{token.approval_id}.approval.json"
    document = json.loads(record_path.read_text(encoding="utf-8"))
    document["issued_at_utc"] = expired_at
    payload = {key: value for key, value in document.items() if key != "record_sha256"}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    document["record_sha256"] = digest
    record_path.write_text(json.dumps(document), encoding="utf-8")
    expired_token = replace(token, issued_at_utc=expired_at, record_sha256=digest)
    with pytest.raises(AuthorizationStaleError, match="expired"):
        ledger.consume(expired_token, intent)
    with pytest.raises(AuthorizationReusedError):
        ledger.consume(expired_token, intent)


def test_atomic_consumption_allows_only_one_concurrent_consumer(tmp_path):
    _plan, intent, *_ = _prepared_write(tmp_path)
    ledger = WriteApprovalLedger(tmp_path / "approvals")
    second_ledger = WriteApprovalLedger(tmp_path / "approvals")
    _challenge, confirmation, _display = _confirmation(intent)
    token = ledger.issue(intent, confirmation)
    barrier = threading.Barrier(3)
    outcomes: list[str] = []

    def consume(selected_ledger: WriteApprovalLedger) -> None:
        barrier.wait()
        try:
            selected_ledger.consume(token, intent)
            outcomes.append("consumed")
        except AuthorizationReusedError:
            outcomes.append("reused")

    threads = [threading.Thread(target=consume, args=(selected,)) for selected in (ledger, second_ledger)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)
    assert sorted(outcomes) == ["consumed", "reused"]


def test_t430_candidate_and_internal_programmer_are_rejected_before_approval(tmp_path):
    _plan, intent, service, probe, backup, readiness, _transport, paths, operation_id = _prepared_write(tmp_path)
    t430 = replace(intent, profile_id="t430-1vyrain", model_id="t430")
    from autoloader.workflow.write_approval import _with_intent_checksum
    with pytest.raises(WriteApprovalError, match="outside the external SPI allowlist"):
        _with_intent_checksum(t430).validate_integrity()

    plan, *_ = _prepared_write(tmp_path / "candidate")
    candidate = replace(plan, device=replace(plan.device, manual_model_candidate="x200"))
    capture = DeviceCaptureEvidence(str(uuid.uuid4()), _iso_now(), candidate.device)
    with pytest.raises(WriteApprovalError, match="candidate-only"):
        WriteIntentFactory(service).create(
            candidate,
            operation_id=operation_id, capture=capture, probe=probe,
            backup_set=backup, recovery_readiness=readiness,
            **paths,
        )


def test_stale_probe_or_wrong_chip_capacity_blocks_intent(tmp_path):
    plan, _intent, service, probe, backup, readiness, _transport, paths, operation_id = _prepared_write(tmp_path)
    old_probe = replace(probe, observed_at_utc=(datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat())
    with pytest.raises(WriteApprovalError, match="stale"):
        WriteIntentFactory(service).create(
            plan,
            operation_id=operation_id,
            capture=DeviceCaptureEvidence(str(uuid.uuid4()), _iso_now(), plan.device),
            probe=old_probe,
            backup_set=backup,
            recovery_readiness=readiness,
            **paths,
        )
    raw_8mb = 'Found Winbond flash chip "W25Q64.V" (8192 kB, SPI) on ch341a_spi.\n'
    wrong_capacity = replace(
        probe,
        chip_name="W25Q64.V",
        capacity_bytes=8 * MIB,
        raw_output=raw_8mb,
        raw_output_sha256=hashlib.sha256(raw_8mb.encode()).hexdigest(),
        reported_programmer="ch341a_spi",
    )
    with pytest.raises(WriteApprovalError, match="capacity"):
        WriteIntentFactory(service).create(
            plan,
            operation_id=operation_id,
            capture=DeviceCaptureEvidence(str(uuid.uuid4()), _iso_now(), plan.device),
            probe=wrong_capacity,
            backup_set=backup,
            recovery_readiness=readiness,
            **paths,
        )


def test_factory_reopens_only_accepted_journal_and_pinned_artifact_manifest(tmp_path):
    plan, _intent, service, probe, backup, readiness, _transport, paths, operation_id = _prepared_write(tmp_path)
    journal_path = Path(paths["operation_journal_path"])
    journal_original = journal_path.read_bytes()
    from autoloader.workflow.journal import OperationJournal
    from autoloader.workflow.types import OperationState

    journal = OperationJournal.load_checkpoint(journal_path, expected_plan=plan)
    journal.append(
        action="authorize",
        from_state=OperationState.BACKUP_VERIFIED,
        to_state=OperationState.AUTHORIZED,
        code="TEST_ONLY_AUTHORIZED_STATE",
        message="Synthetic invalid pre-write checkpoint fixture.",
    )
    with pytest.raises(WriteApprovalError, match="checkpoint"):
        WriteIntentFactory(service).create(
            plan,
            operation_id=operation_id,
            capture=DeviceCaptureEvidence(str(uuid.uuid4()), _iso_now(), plan.device),
            probe=probe,
            backup_set=backup,
            recovery_readiness=readiness,
            **paths,
        )

    # Restore the accepted checkpoint, then change only the pinned source
    # revision and recompute the non-authenticating sidecar so the model
    # workflow's recipe/source checks—not a checksum error—must reject it.
    journal_path.write_bytes(journal_original)
    manifest_path = Path(paths["artifact_manifest_path"])
    manifest_document = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_document["source_revision"] = "f" * 40
    manifest_bytes = (json.dumps(manifest_document, sort_keys=True, indent=2) + "\n").encode("utf-8")
    manifest_path.write_bytes(manifest_bytes)
    manifest_path.with_name("manifest.sha256").write_text(
        f"{hashlib.sha256(manifest_bytes).hexdigest()}  manifest.json\n", encoding="ascii"
    )
    with pytest.raises(WriteApprovalError, match="artifact evidence failed revalidation"):
        WriteIntentFactory(service).create(
            plan,
            operation_id=operation_id,
            capture=DeviceCaptureEvidence(str(uuid.uuid4()), _iso_now(), plan.device),
            probe=probe,
            backup_set=backup,
            recovery_readiness=readiness,
            **paths,
        )


def test_unknown_or_changed_voltage_provenance_blocks_intent(tmp_path):
    plan, _intent, service, probe, backup, readiness, _transport, paths, operation_id = _prepared_write(tmp_path)
    unknown = replace(plan, topology=replace(plan.topology, chips=(replace(plan.topology.chips[0], voltage_mv=None),)))
    with pytest.raises(WriteApprovalError, match="3300 mV"):
        WriteIntentFactory(service).create(
            unknown, operation_id=operation_id,
            capture=DeviceCaptureEvidence(str(uuid.uuid4()), _iso_now(), unknown.device), probe=probe,
            backup_set=backup, recovery_readiness=readiness,
            **paths,
        )
    changed_provenance = replace(
        plan,
        programmer=replace(plan.programmer, attested_by="Other Operator"),
    )
    with pytest.raises(WriteApprovalError, match="same operator"):
        WriteIntentFactory(service).create(
            changed_provenance, operation_id=operation_id,
            capture=DeviceCaptureEvidence(str(uuid.uuid4()), _iso_now(), changed_provenance.device), probe=probe,
            backup_set=backup, recovery_readiness=readiness,
            **paths,
        )


def test_backup_tamper_and_recovery_mismatch_block_intent(tmp_path):
    plan, _intent, service, probe, backup, readiness, _transport, paths, operation_id = _prepared_write(tmp_path)
    Path(backup.chips[0].first_path).write_bytes(b"tampered")
    with pytest.raises(WriteApprovalError, match="backup"):
        WriteIntentFactory(service).create(
            plan, operation_id=operation_id,
            capture=DeviceCaptureEvidence(str(uuid.uuid4()), _iso_now(), plan.device), probe=probe,
            backup_set=backup, recovery_readiness=readiness,
            **paths,
        )

    # A separately generated intent with valid backup files but a modified
    # recovery record is rejected before approval can be minted.
    plan2, _intent2, service2, probe2, backup2, readiness2, _transport2, paths2, operation_id2 = _prepared_write(tmp_path / "second")
    forged_readiness = replace(readiness2, primary_volume_id=readiness2.recovery_volume_id)
    with pytest.raises(WriteApprovalError, match="readiness evidence changed"):
        WriteIntentFactory(service2).create(
            plan2, operation_id=operation_id2,
            capture=DeviceCaptureEvidence(str(uuid.uuid4()), _iso_now(), plan2.device), probe=probe2,
            backup_set=backup2, recovery_readiness=forged_readiness,
            **paths2,
        )


def test_approval_ledger_corruption_and_restore_are_fail_closed(tmp_path):
    _plan, intent, *_ = _prepared_write(tmp_path)
    ledger = WriteApprovalLedger(tmp_path / "approvals")
    _challenge, confirmation, _display = _confirmation(intent)
    token = ledger.issue(intent, confirmation)
    record = tmp_path / "approvals" / f"{token.approval_id}.approval.json"
    document = json.loads(record.read_text(encoding="utf-8"))
    document["intent_sha256"] = "0" * 64
    record.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(WriteApprovalError, match="checksum"):
        ledger.consume(token, intent)

    restore_intent = replace(intent, action=WriteAction.RESTORE)
    from autoloader.workflow.write_approval import _with_intent_checksum
    with pytest.raises(WriteApprovalError, match="profile/action/programmer"):
        _with_intent_checksum(restore_intent).validate_integrity()


def test_command_argv_or_artifact_path_tamper_is_rejected(tmp_path):
    _plan, intent, *_ = _prepared_write(tmp_path)
    from autoloader.workflow.write_approval import _with_intent_checksum
    changed_argv = replace(intent, command=replace(intent.command, argv=("-p", "internal", "-w", intent.artifact_path)))
    with pytest.raises(WriteApprovalError, match="fixed model-specific argv"):
        _with_intent_checksum(changed_argv).validate_integrity()

    alternate = str((tmp_path / "elsewhere.rom").resolve())
    changed_path = replace(
        intent,
        artifact_path=alternate,
        command=replace(intent.command, argv=("-p", intent.programmer, "-c", intent.chip_name, "-w", alternate)),
    )
    changed_path = _with_intent_checksum(changed_path)
    with pytest.raises(WriteApprovalError, match="not produced by"):
        create_write_approval_challenge(changed_path)


def test_tui_confirmation_requires_the_matching_explicit_action(tmp_path):
    _plan, intent, *_ = _prepared_write(tmp_path)
    challenge = create_write_approval_challenge(intent)
    bad = TUIConfirmationEvent("pathway", "confirm-write", str(uuid.uuid4()), challenge.challenge_sha256, _iso_now())
    with pytest.raises(WriteApprovalError, match="live dedicated screen bridge"):
        accept_tui_confirmation(challenge, actor="Operator", event=bad)
    good = TUIConfirmationEvent("write-confirmation", "confirm-write", str(uuid.uuid4()), challenge.challenge_sha256, _iso_now())
    with pytest.raises(WriteApprovalError, match="live dedicated screen bridge"):
        confirmation = accept_tui_confirmation(challenge, actor="Operator", event=good)
    forged = object.__new__(HumanActionConfirmation)
    object.__setattr__(forged, "evidence_id", "forged")
    object.__setattr__(forged, "challenge_sha256", challenge.challenge_sha256)
    object.__setattr__(forged, "exact_phrase", challenge.exact_phrase)
    object.__setattr__(forged, "actor", "Operator")
    object.__setattr__(forged, "confirmed_at_utc", _iso_now())
    object.__setattr__(forged, "channel", ConfirmationChannel.TUI_EXPLICIT_ACTION)
    with pytest.raises(WriteApprovalError, match="fabricated, expired, revoked, or already used"):
        WriteApprovalLedger(tmp_path / "approvals").issue(intent, forged)


def test_approval_fails_closed_when_directory_fsync_fails(tmp_path, monkeypatch):
    _plan, intent, *_ = _prepared_write(tmp_path)
    challenge, confirmation, _display = _confirmation(intent)
    ledger = WriteApprovalLedger(tmp_path / "approvals")

    def fail_directory_sync(_path):
        raise OSError("simulated directory fsync failure")

    monkeypatch.setattr(approval_module, "_sync_directory", fail_directory_sync)
    with pytest.raises(WriteApprovalError, match="parent directory could not be durably synced"):
        ledger.issue(intent, confirmation)
    assert not list((tmp_path / "approvals").glob("*.approval.json"))
    assert challenge.challenge_sha256 in next((tmp_path / "approvals").glob("*.confirmed")).name


def test_approval_platform_without_directory_fsync_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(approval_module, "_directory_fsync_supported", lambda: False)
    with pytest.raises(WriteApprovalError, match="directory fsync is unsupported"):
        WriteApprovalLedger(tmp_path / "approvals")
    assert not (tmp_path / "approvals").exists()
