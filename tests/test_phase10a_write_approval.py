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

from autoloader.flasher.flashrom import ProgrammerType
from autoloader.flasher.recovery import RecoveryReadinessService
from autoloader.workflow.authorization import AuthorizationReusedError, AuthorizationStaleError
from autoloader.workflow.types import ChipEvidence, ProgrammerEvidence
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
    plan = replace(base, topology=replace(base.topology, chips=(chip,)), programmer=programmer)
    payload = _payload(4 * MIB, 47)
    transport = FakeTransport(
        ['Found Winbond flash chip "W25Q32.V" (4096 kB, SPI) on ch341a_spi.\n'],
        {4 * MIB: payload},
    )
    probes = _probe(transport)
    backup_service, primary, recovery = _make_service(tmp_path, transport)
    backup = _capture(backup_service, plan, probes, primary, recovery)
    bound_plan = replace(plan, backup=backup.backup)
    loaded = backup_service.load_verified_result(
        bound_plan,
        operation_id=backup.operation_id,
        primary_manifest=backup.primary_manifest,
        recovery_manifest=backup.recovery_manifest,
        primary_manifest_sha256=backup.primary_manifest_sha256,
        recovery_manifest_sha256=backup.recovery_manifest_sha256,
        expected_tool=probes[0].tool,
    )
    readiness = RecoveryReadinessService().evaluate(bound_plan, loaded)
    assert readiness.status == "ready", readiness.reasons
    capture = DeviceCaptureEvidence(
        capture_id=str(uuid.uuid4()),
        observed_at_utc=_iso_now(),
        device=bound_plan.device,
    )
    intent = WriteIntentFactory(backup_service).create(
        bound_plan,
        operation_id=str(uuid.uuid4()),
        capture=capture,
        probe=probes[0],
        backup_set=loaded,
        recovery_readiness=readiness,
    )
    return bound_plan, intent, backup_service, probes[0], loaded, readiness, transport


def _prepared_x220_write(tmp_path: Path):
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
    plan = replace(base, topology=replace(base.topology, chips=(chip,)), programmer=programmer)
    payload = _payload(8 * MIB, 61)
    transport = FakeTransport(
        ['Found Winbond flash chip "W25Q64.V" (8192 kB, SPI) on ch341a_spi.\n'],
        {8 * MIB: payload},
    )
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)
    backup = _capture(service, plan, probes, primary, recovery)
    bound_plan = replace(plan, backup=backup.backup)
    loaded = service.load_verified_result(
        bound_plan,
        operation_id=backup.operation_id,
        primary_manifest=backup.primary_manifest,
        recovery_manifest=backup.recovery_manifest,
        primary_manifest_sha256=backup.primary_manifest_sha256,
        recovery_manifest_sha256=backup.recovery_manifest_sha256,
        expected_tool=probes[0].tool,
    )
    readiness = RecoveryReadinessService().evaluate(bound_plan, loaded)
    assert readiness.status == "ready", readiness.reasons
    capture = DeviceCaptureEvidence(str(uuid.uuid4()), _iso_now(), bound_plan.device)
    intent = WriteIntentFactory(service).create(
        bound_plan,
        operation_id=str(uuid.uuid4()),
        capture=capture,
        probe=probes[0],
        backup_set=loaded,
        recovery_readiness=readiness,
    )
    return bound_plan, intent, service, probes[0], loaded, readiness, transport


class _TTYInput(io.StringIO):
    def isatty(self) -> bool:
        return True


class _TTYOutput(io.StringIO):
    def isatty(self) -> bool:
        return True


def _confirmation(intent):
    challenge = create_write_approval_challenge(intent)
    typed = _TTYInput(challenge.exact_phrase + "\n")
    displayed = _TTYOutput()
    evidence = collect_cli_confirmation(
        challenge,
        actor="Test Operator",
        input_stream=typed,
        output_stream=displayed,
    )
    return challenge, evidence, displayed.getvalue()


def test_x200_intent_binds_fresh_probe_artifact_backup_recovery_and_fixed_command(tmp_path):
    plan, intent, _service, probe, backup, readiness, _transport = _prepared_write(tmp_path)
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


def test_x220_intent_binds_full_chip_coreboot_regions_and_backup(tmp_path):
    plan, intent, _service, probe, backup, _readiness, _transport = _prepared_x220_write(tmp_path)
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


def test_cli_confirmation_requires_tty_and_exact_phrase(tmp_path):
    _plan, intent, *_ = _prepared_write(tmp_path)
    challenge = create_write_approval_challenge(intent)
    with pytest.raises(WriteApprovalError, match="TTY input"):
        collect_cli_confirmation(
            challenge, actor="Operator", input_stream=io.StringIO(challenge.exact_phrase), output_stream=_TTYOutput()
        )
    with pytest.raises(WriteApprovalError, match="exact write challenge"):
        collect_cli_confirmation(
            challenge, actor="Operator", input_stream=_TTYInput("FLASH X200 WRONG\n"), output_stream=_TTYOutput()
        )
    evidence = collect_cli_confirmation(
        challenge,
        actor="Operator",
        input_stream=_TTYInput(challenge.exact_phrase + "\n"),
        output_stream=_TTYOutput(),
    )
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
    plan, intent, service, probe, backup, readiness, _transport = _prepared_write(tmp_path)
    ledger = WriteApprovalLedger(tmp_path / "approvals")
    _challenge, confirmation, _display = _confirmation(intent)
    token = ledger.issue(intent, confirmation)
    changed = WriteIntentFactory(service).create(
        plan,
        operation_id=str(uuid.uuid4()),
        capture=DeviceCaptureEvidence(str(uuid.uuid4()), _iso_now(), plan.device),
        probe=probe,
        backup_set=backup,
        recovery_readiness=readiness,
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
    _plan, intent, service, probe, backup, readiness, _transport = _prepared_write(tmp_path)
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
            operation_id=str(uuid.uuid4()), capture=capture, probe=probe,
            backup_set=backup, recovery_readiness=readiness,
        )


def test_stale_probe_or_wrong_chip_capacity_blocks_intent(tmp_path):
    plan, _intent, service, probe, backup, readiness, _transport = _prepared_write(tmp_path)
    old_probe = replace(probe, observed_at_utc=(datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat())
    with pytest.raises(WriteApprovalError, match="stale"):
        WriteIntentFactory(service).create(
            plan,
            operation_id=str(uuid.uuid4()),
            capture=DeviceCaptureEvidence(str(uuid.uuid4()), _iso_now(), plan.device),
            probe=old_probe,
            backup_set=backup,
            recovery_readiness=readiness,
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
            operation_id=str(uuid.uuid4()),
            capture=DeviceCaptureEvidence(str(uuid.uuid4()), _iso_now(), plan.device),
            probe=wrong_capacity,
            backup_set=backup,
            recovery_readiness=readiness,
        )


def test_unknown_or_changed_voltage_provenance_blocks_intent(tmp_path):
    plan, _intent, service, probe, backup, readiness, _transport = _prepared_write(tmp_path)
    unknown = replace(plan, topology=replace(plan.topology, chips=(replace(plan.topology.chips[0], voltage_mv=None),)))
    with pytest.raises(WriteApprovalError, match="3300 mV"):
        WriteIntentFactory(service).create(
            unknown, operation_id=str(uuid.uuid4()),
            capture=DeviceCaptureEvidence(str(uuid.uuid4()), _iso_now(), unknown.device), probe=probe,
            backup_set=backup, recovery_readiness=readiness,
        )
    changed_provenance = replace(
        plan,
        programmer=replace(plan.programmer, attested_by="Other Operator"),
    )
    with pytest.raises(WriteApprovalError, match="same operator"):
        WriteIntentFactory(service).create(
            changed_provenance, operation_id=str(uuid.uuid4()),
            capture=DeviceCaptureEvidence(str(uuid.uuid4()), _iso_now(), changed_provenance.device), probe=probe,
            backup_set=backup, recovery_readiness=readiness,
        )


def test_backup_tamper_and_recovery_mismatch_block_intent(tmp_path):
    plan, _intent, service, probe, backup, readiness, _transport = _prepared_write(tmp_path)
    Path(backup.chips[0].first_path).write_bytes(b"tampered")
    with pytest.raises(WriteApprovalError, match="backup"):
        WriteIntentFactory(service).create(
            plan, operation_id=str(uuid.uuid4()),
            capture=DeviceCaptureEvidence(str(uuid.uuid4()), _iso_now(), plan.device), probe=probe,
            backup_set=backup, recovery_readiness=readiness,
        )

    # A separately generated intent with valid backup files but a modified
    # recovery record is rejected before approval can be minted.
    plan2, _intent2, service2, probe2, backup2, readiness2, _transport2 = _prepared_write(tmp_path / "second")
    forged_readiness = replace(readiness2, primary_volume_id=readiness2.recovery_volume_id)
    with pytest.raises(WriteApprovalError, match="readiness evidence changed"):
        WriteIntentFactory(service2).create(
            plan2, operation_id=str(uuid.uuid4()),
            capture=DeviceCaptureEvidence(str(uuid.uuid4()), _iso_now(), plan2.device), probe=probe2,
            backup_set=backup2, recovery_readiness=forged_readiness,
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
    with pytest.raises(WriteApprovalError, match="does not confirm"):
        accept_tui_confirmation(challenge, actor="Operator", event=bad)
    good = TUIConfirmationEvent("write-confirmation", "confirm-write", str(uuid.uuid4()), challenge.challenge_sha256, _iso_now())
    confirmation = accept_tui_confirmation(challenge, actor="Operator", event=good)
    ledger = WriteApprovalLedger(tmp_path / "approvals")
    token = ledger.issue(intent, confirmation)
    assert token.channel is ConfirmationChannel.TUI_EXPLICIT_ACTION
