"""Phase 10B fake-only tests for the external SPI write/read-back boundary."""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

import autoloader.workflow.journal as journal_module
import autoloader.workflow.write_approval as approval_module
import autoloader.workflow.write_service as write_service_module
from autoloader.flasher.backup_service import StorageEvidence
from autoloader.flasher.readback import ReadbackComparator
from autoloader.flasher.spi import ChipProbeEvidence, SPITransportResult
from autoloader.workflow.engine import WorkflowSession
from autoloader.workflow.journal import OperationJournal
from autoloader.workflow.types import OperationState, VerificationStatus
from autoloader.workflow.write_approval import (
    DeviceCaptureEvidence,
    WriteApprovalLedger,
    WriteIntentFactory,
)
from autoloader.workflow.write_service import (
    ExternalSPIWriteService,
    FreshWriteEvidence,
    HardwareSetupAttestation,
    SubprocessWriteCommandRunner,
    WriteExecutionRequest,
    WriteExecutionStatus,
)
from tests.test_phase03_spi_safety import MIB
from tests.test_phase10a_write_approval import (
    _write_intent_factory,
    _prepared_write,
    _prepared_x220_write,
    _iso_now,
)


@pytest.fixture(autouse=True)
def _fake_durable_host(monkeypatch):
    """Model POSIX durable fsync in unit fixtures; never touches hardware."""
    monkeypatch.setattr(approval_module, "_directory_fsync_supported", lambda: True)
    monkeypatch.setattr(approval_module, "_sync_directory", lambda _path: None)
    monkeypatch.setattr(journal_module, "_directory_fsync_supported", lambda: True)
    monkeypatch.setattr(journal_module, "_sync_directory", lambda _path: None)


class _TTYInput(io.StringIO):
    def fileno(self) -> int:
        return 91


class _TTYOutput(io.StringIO):
    def fileno(self) -> int:
        return 92


class _QueueEvidence:
    def __init__(self, plan, probe, *, second_probe=None, mutate=None):
        self.plan = plan
        self.probe = probe
        self.second_probe = second_probe
        self.mutate = mutate
        self.calls = 0

    def collect(self, _plan):
        self.calls += 1
        if self.mutate is not None and self.calls == 2:
            self.mutate()
        source_probe = self.second_probe if self.calls == 2 and self.second_probe is not None else self.probe
        probe = replace(
            source_probe,
            probe_id=str(uuid.uuid4()),
            observed_at_utc=_iso_now(),
        )
        capture = DeviceCaptureEvidence(
            str(uuid.uuid4()), _iso_now(), self.plan.device,
            device_fingerprint_sha256=hashlib.sha256(b"SYNTHETIC-SERIAL").hexdigest(),
        )
        programmer = self.plan.programmer
        setup = HardwareSetupAttestation(
            actor=programmer.attested_by or "",
            instrument_id=programmer.instrument_id or "",
            test_point=programmer.measurement_test_point or "",
            measured_at_utc=_iso_now(),
            programmer_voltage_mv=programmer.voltage_mv or 0,
            chip_voltage_mv=self.plan.topology.chips[0].voltage_mv or 0,
            interface_verified=True,
            wiring_verified=True,
            target_powered_off=True,
            ac_disconnected=True,
            removable_battery_removed=True,
            chip_pin1_orientation_verified=True,
            programmer_disconnect_before_repower=True,
            ec_update_decision="waived",
            ec_waiver_reason="Operator accepts loss of later OEM EC update route.",
        )
        return FreshWriteEvidence(capture, probe, setup)


class _FakeWriter:
    def __init__(self):
        self.calls: list[tuple[str, tuple[str, ...], int]] = []
        self.image: bytes | None = None
        self.result = subprocess.CompletedProcess([], 0, "write complete", "")
        self.exception: BaseException | None = None

    def run(self, capability: object, timeout_seconds: int):
        command = getattr(capability, "command")
        executable = command.executable
        argv = command.argv
        self.calls.append((executable, argv, timeout_seconds))
        if self.exception is not None:
            raise self.exception
        if self.result.returncode == 0:
            self.image = Path(argv[-1]).read_bytes()
        return self.result


class _MutatingWriter(_FakeWriter):
    def run(self, capability: object, timeout_seconds: int):
        command = getattr(capability, "command")
        result = super().run(capability, timeout_seconds)
        if result.returncode == 0:
            target = Path(command.argv[-1])
            data = bytearray(target.read_bytes())
            data[0] ^= 1
            target.write_bytes(data)
            self.image = bytes(data)
        return result


class _FakeReadback:
    def __init__(self, writer: _FakeWriter, *, corrupt: bool = False, fail: bool = False):
        self.writer = writer
        self.corrupt = corrupt
        self.fail = fail
        self.calls = []

    def read_chip(self, programmer, probe, destination):
        self.calls.append((programmer, probe.probe_id, destination))
        if self.fail or self.writer.image is None:
            return SPITransportResult(False, "synthetic read failure", 0.01)
        payload = self.writer.image
        if self.corrupt:
            payload = payload[:-1] + bytes([payload[-1] ^ 1])
        Path(destination).write_bytes(payload)
        return SPITransportResult(True, "synthetic full-chip read", 0.01)


class _StorageInspector:
    def __init__(self, reference: StorageEvidence, *, filesystem_type: str = "ext4", enough: bool = True):
        self.reference = reference
        self.filesystem_type = filesystem_type
        self.enough = enough
        self.calls = []

    def inspect(self, directory, required_bytes, *, persistence_confirmed):
        path = Path(directory).resolve(strict=True)
        self.calls.append((path, required_bytes, persistence_confirmed))
        available = required_bytes + 1024 if self.enough else max(0, required_bytes - 1)
        return StorageEvidence(
            path=str(path),
            volume_id=self.reference.volume_id,
            filesystem_type=self.filesystem_type,
            available_bytes=available,
            required_bytes=required_bytes,
            persistence_confirmed=persistence_confirmed,
            scratch_readback_verified=persistence_confirmed,
            observed_at_utc=_iso_now(),
        )


def _real_cli_confirmation(monkeypatch):
    def collect(challenge, actor):
        typed = _TTYInput(challenge.exact_phrase + "\n")
        displayed = _TTYOutput()
        monkeypatch.setattr(approval_module.sys, "stdin", typed)
        monkeypatch.setattr(approval_module.sys, "stdout", displayed)
        monkeypatch.setattr(
            approval_module,
            "_require_tty_file_descriptor",
            lambda _stream, label: 91 if label == "input" else 92,
        )
        monkeypatch.setattr(approval_module.os, "isatty", lambda descriptor: descriptor in {91, 92})
        return approval_module.collect_cli_confirmation(challenge, actor=actor)

    return collect


def _service_request(
    tmp_path, monkeypatch, *, x220=False, provider=None, provider_builder=None, writer=None, readback=None,
    storage_inspector=None,
):
    import autoloader.workflow.write_service as write_service_module

    if x220:
        plan, _intent, backup_service, probe, backup, readiness, _transport, paths, operation_id = _prepared_x220_write(tmp_path, monkeypatch)
    else:
        plan, _intent, backup_service, probe, backup, readiness, _transport, paths, operation_id = _prepared_write(tmp_path)
    evidence = provider or (provider_builder(plan, probe) if provider_builder is not None else _QueueEvidence(plan, probe))
    fake_writer = writer or _FakeWriter()
    fake_readback = readback or _FakeReadback(fake_writer)
    inspector = (
        storage_inspector(backup.primary_storage)
        if callable(storage_inspector)
        else storage_inspector or _StorageInspector(backup.primary_storage)
    )
    monkeypatch.setattr(write_service_module, "collect_cli_confirmation", _real_cli_confirmation(monkeypatch))
    ledger = WriteApprovalLedger(tmp_path / "approval-ledger")
    service = ExternalSPIWriteService(
        intent_factory=_write_intent_factory(backup_service),
        approval_ledger=ledger,
        evidence_provider=evidence,
        read_transport=fake_readback,
        writer=fake_writer,
        storage_inspector=inspector,
        durability_supported=lambda: True,
    )
    request = WriteExecutionRequest(
        plan=plan,
        operation_id=operation_id,
        backup_set=backup,
        recovery_readiness=readiness,
        operation_journal_path=paths["operation_journal_path"],
        artifact_manifest_path=paths["artifact_manifest_path"],
    )
    return service, request, fake_writer, fake_readback


def test_x200_write_journals_before_one_fixed_call_then_exact_full_chip_readback(tmp_path, monkeypatch):
    service, request, writer, readback = _service_request(tmp_path, monkeypatch)
    result = service.execute(request)

    assert result.status == WriteExecutionStatus.WRITE_VERIFIED
    assert result.state is OperationState.WRITE_VERIFIED
    assert result.boot_status is VerificationStatus.NOT_PERFORMED
    assert len(writer.calls) == 1
    executable, argv, timeout = writer.calls[0]
    assert Path(executable).is_file()
    assert argv[:4] == ("-p", "ch341a_spi", "-c", "W25Q32.V")
    assert argv[4] == "-w" and len(argv) == 6
    assert Path(argv[-1]).parent.name == "x200-write-ready"
    written_sha256 = hashlib.sha256(writer.image or b"").hexdigest()
    assert written_sha256 != request.plan.artifact.expected_sha256
    assert written_sha256 == result.artifact_sha256
    assert timeout > 0
    assert len(readback.calls) == 1
    journal = OperationJournal.inspect_checkpoint(request.operation_journal_path)
    assert journal.state is OperationState.WRITE_VERIFIED
    start = next(entry for entry in journal.entries if entry.to_state is OperationState.WRITE_STARTED)
    assert "approval_id" in start.message
    assert "fixed_command_sha256" in start.message
    start_payload = json.loads(start.message)
    assert start_payload["human_setup_attestations"]["semantics"] == "human-reported attestations; not measured by AutoLoader"
    assert start_payload["ec_readiness"]["automatic_update_performed"] is False
    assert start_payload["x200_mac_preservation"]["original_mac"] == "00:26:2d:12:34:56"
    assert start_payload["x200_mac_preservation"]["selected_mac_before"] == "02:00:00:00:00:01"
    verified = next(entry for entry in journal.entries if entry.to_state is OperationState.WRITE_VERIFIED)
    verified_payload = json.loads(verified.message)
    assert verified_payload["artifact_sha256"] == written_sha256
    assert verified_payload["readback_sha256"] == written_sha256
    assert result.readback_sha256 == written_sha256
    assert "x200_mac_preservation" in start.message


@pytest.mark.parametrize(
    "field",
    [
        "target_powered_off",
        "ac_disconnected",
        "removable_battery_removed",
        "chip_pin1_orientation_verified",
        "programmer_disconnect_before_repower",
    ],
)
def test_missing_physical_setup_attestation_blocks_before_confirmation_or_writer(tmp_path, monkeypatch, field):
    service, request, writer, readback = _service_request(tmp_path, monkeypatch)
    original_provider = service.evidence_provider

    class MissingAttestation:
        def collect(self, plan):
            evidence = original_provider.collect(plan)
            return replace(evidence, setup=replace(evidence.setup, **{field: False}))

    service.evidence_provider = MissingAttestation()
    with pytest.raises(write_service_module.WriteExecutionError, match="human setup attestation"):
        service.execute(request)
    assert writer.calls == []
    assert readback.calls == []


@pytest.mark.parametrize(
    ("ec_version", "ec_ref", "ec_time", "decision", "waiver", "valid"),
    [
        ("", "", "", "", "", False),
        ("1.2", "ec-observation:1", _iso_now(), "waived", "Operator waives possible loss of later OEM EC updates.", False),
        ("1.2", "", "", "waived", "Operator waives possible loss of later OEM EC updates.", False),
        ("", "ec-observation:1", "", "waived", "Operator waives possible loss of later OEM EC updates.", False),
        ("", "", _iso_now(), "waived", "Operator waives possible loss of later OEM EC updates.", False),
        ("1.2", "ec-observation:1", "", "waived", "Operator waives possible loss of later OEM EC updates.", False),
        ("1.2", "", _iso_now(), "waived", "Operator waives possible loss of later OEM EC updates.", False),
        ("", "ec-observation:1", _iso_now(), "waived", "Operator waives possible loss of later OEM EC updates.", False),
        ("1.2", "", _iso_now(), "", "", False),
        ("", "ec-observation:1", "", "waived", "Operator waives possible loss of later OEM EC updates.", False),
        ("", "", _iso_now(), "waived", "Operator waives possible loss of later OEM EC updates.", False),
        ("1.2", "ec-observation:1", _iso_now(), "", "", False),
        ("1.2", "ec-observation:1", _iso_now(), "already-current", "", True),
        ("", "", "", "waived", "Operator waives possible loss of later OEM EC updates.", True),
    ],
)
def test_ec_readiness_requires_observed_version_or_explicit_waiver_before_writer(
    tmp_path, monkeypatch, ec_version, ec_ref, ec_time, decision, waiver, valid
):
    service, request, writer, readback = _service_request(tmp_path, monkeypatch)
    if valid and ec_version:
        # Freshness is evaluated at test execution, not collection time.
        ec_time = _iso_now()
    original_provider = service.evidence_provider

    class ECProvider:
        def collect(self, plan):
            evidence = original_provider.collect(plan)
            return replace(
                evidence,
                setup=replace(
                    evidence.setup,
                    ec_version=ec_version,
                    ec_evidence_ref=ec_ref,
                    ec_observed_at_utc=ec_time,
                    ec_update_decision=decision,
                    ec_waiver_reason=waiver,
                ),
            )

    service.evidence_provider = ECProvider()
    if valid:
        result = service.execute(request)
        assert result.status is WriteExecutionStatus.WRITE_VERIFIED
        assert len(writer.calls) == 1
    else:
        with pytest.raises(write_service_module.WriteExecutionError):
            service.execute(request)
        assert writer.calls == []
        assert readback.calls == []
    if valid:
        with pytest.raises(Exception):
            WorkflowSession.resume(request.plan, request.operation_journal_path)
    else:
        assert WorkflowSession.resume(request.plan, request.operation_journal_path).state is OperationState.BACKUP_VERIFIED


def test_x220_write_uses_same_service_profile_allowlist_and_full_chip_readback(tmp_path, monkeypatch):
    service, request, writer, readback = _service_request(tmp_path, monkeypatch, x220=True)
    result = service.execute(request)
    assert result.status == WriteExecutionStatus.WRITE_VERIFIED
    assert request.plan.profile_id == "x220-coreboot"
    assert request.plan.artifact.expected_size_bytes == 8 * MIB
    assert len(writer.calls) == len(readback.calls) == 1


def test_changed_probe_after_approval_burns_token_without_journal_or_writer(tmp_path, monkeypatch):
    changed_output = 'Found Winbond flash chip "W25Q64.V" (8192 kB, SPI) on ch341a_spi.\n'
    def build_provider(plan, probe):
        changed_probe = replace(
            probe,
            chip_name="W25Q64.V",
            capacity_bytes=8 * MIB,
            raw_output=changed_output,
            raw_output_sha256=hashlib.sha256(changed_output.encode()).hexdigest(),
            reported_programmer="ch341a_spi",
        )
        return _QueueEvidence(plan, probe, second_probe=changed_probe)

    service, request, writer, readback = _service_request(
        tmp_path, monkeypatch, provider_builder=build_provider
    )
    with pytest.raises(Exception, match="changed after approval|capacity"):
        service.execute(request)
    assert writer.calls == []
    assert readback.calls == []
    assert OperationJournal.load_checkpoint(request.operation_journal_path, expected_plan=request.plan).state is OperationState.BACKUP_VERIFIED


@pytest.mark.parametrize(
    ("exception", "expected_state", "expected_status"),
    [
        (subprocess.TimeoutExpired("flashrom", 1), OperationState.WRITE_UNCERTAIN, WriteExecutionStatus.UNCERTAIN),
        (KeyboardInterrupt(), OperationState.WRITE_UNCERTAIN, WriteExecutionStatus.UNCERTAIN),
        (OSError("synthetic spawn failure"), OperationState.WRITE_FAILED, WriteExecutionStatus.FAILED),
    ],
)
def test_timeout_interruption_and_spawn_failure_never_retry_or_readback(
    tmp_path, monkeypatch, exception, expected_state, expected_status
):
    writer = _FakeWriter()
    writer.exception = exception
    service, request, _writer, readback = _service_request(tmp_path, monkeypatch, writer=writer)
    result = service.execute(request)
    assert result.state is expected_state
    assert result.status == expected_status
    assert len(writer.calls) == 1
    assert readback.calls == []
    assert OperationJournal.inspect_checkpoint(request.operation_journal_path).state is expected_state


def test_nonzero_writer_and_readback_mismatch_are_terminal_without_retry(tmp_path, monkeypatch):
    writer = _FakeWriter()
    writer.result = subprocess.CompletedProcess([], 7, "", "synthetic nonzero")
    service, request, _writer, readback = _service_request(tmp_path, monkeypatch, writer=writer)
    result = service.execute(request)
    assert result.state is OperationState.WRITE_UNCERTAIN
    assert result.status is WriteExecutionStatus.UNCERTAIN
    assert readback.calls == []
    assert len(writer.calls) == 1

    mismatch_writer = _FakeWriter()
    mismatch_readback = _FakeReadback(mismatch_writer, corrupt=True)
    service2, request2, _writer2, readback2 = _service_request(
        tmp_path / "mismatch", monkeypatch, writer=mismatch_writer, readback=mismatch_readback
    )
    mismatch = service2.execute(request2)
    assert mismatch.state is OperationState.WRITE_UNCERTAIN
    assert mismatch.comparison is not None and mismatch.comparison.status is VerificationStatus.FAILED
    assert len(mismatch_writer.calls) == len(readback2.calls) == 1


def test_mutated_artifact_cannot_be_accepted_even_if_readback_matches_mutation(tmp_path, monkeypatch):
    writer = _MutatingWriter()
    service, request, _writer, readback = _service_request(tmp_path, monkeypatch, writer=writer)

    result = service.execute(request)

    assert result.state is OperationState.WRITE_UNCERTAIN
    assert result.code == "READBACK_FAILED"
    assert len(writer.calls) == len(readback.calls) == 1
    assert OperationJournal.inspect_checkpoint(request.operation_journal_path).state is OperationState.WRITE_UNCERTAIN


def test_readback_mutation_after_comparison_cannot_be_marked_verified(tmp_path, monkeypatch):
    class MutatingComparator:
        def compare(self, expected_path, readback_path, *, chip_capacity_bytes, regions=()):
            comparison = ReadbackComparator().compare(
                expected_path,
                readback_path,
                chip_capacity_bytes=chip_capacity_bytes,
                regions=regions,
            )
            target = Path(readback_path)
            data = bytearray(target.read_bytes())
            data[-1] ^= 1
            target.write_bytes(data)
            return comparison

    writer = _FakeWriter()
    service, request, _writer, readback = _service_request(tmp_path, monkeypatch, writer=writer)
    service.comparator = MutatingComparator()

    result = service.execute(request)

    assert result.state is OperationState.WRITE_UNCERTAIN
    assert result.code == "READBACK_FAILED"
    assert len(writer.calls) == len(readback.calls) == 1


def test_readback_transport_failure_preserves_started_operation_as_uncertain(tmp_path, monkeypatch):
    writer = _FakeWriter()
    failed_readback = _FakeReadback(writer, fail=True)
    service, request, _writer, _readback = _service_request(
        tmp_path, monkeypatch, writer=writer, readback=failed_readback
    )
    result = service.execute(request)
    assert result.state is OperationState.WRITE_UNCERTAIN
    assert result.code == "READBACK_FAILED"
    assert len(writer.calls) == 1 and len(failed_readback.calls) == 1


def test_write_started_journal_fsync_failure_makes_zero_writer_calls(tmp_path, monkeypatch):
    service, request, writer, readback = _service_request(tmp_path, monkeypatch)
    monkeypatch.setattr(journal_module, "_sync_directory", lambda _path: (_ for _ in ()).throw(OSError("sync failed")))
    with pytest.raises(Exception, match="durable WRITE_STARTED"):
        service.execute(request)
    assert writer.calls == [] and readback.calls == []
    # The rename may have reached the page cache before fsync failed; this state
    # is terminal/read-only and cannot be resumed into another write.
    inspected = OperationJournal.inspect_checkpoint(request.operation_journal_path)
    assert inspected.state in {OperationState.BACKUP_VERIFIED, OperationState.WRITE_STARTED}
    with pytest.raises(Exception):
        WorkflowSession.resume(request.plan, request.operation_journal_path)


def test_readback_destination_is_reserved_before_write_and_existing_target_blocks(tmp_path, monkeypatch):
    service, request, writer, readback = _service_request(tmp_path, monkeypatch)
    target = service._readback_path(request)
    target.write_bytes(b"pre-existing operator data")

    with pytest.raises(Exception, match="read-back parent or new destination"):
        service.execute(request)

    assert writer.calls == [] and readback.calls == []
    assert target.read_bytes() == b"pre-existing operator data"
    assert OperationJournal.load_checkpoint(
        request.operation_journal_path, expected_plan=request.plan
    ).state is OperationState.BACKUP_VERIFIED


def test_readback_storage_capacity_failure_blocks_before_writer(tmp_path, monkeypatch):
    service, request, writer, readback = _service_request(
        tmp_path,
        monkeypatch,
        storage_inspector=lambda primary_storage: _StorageInspector(primary_storage, enough=False),
    )

    with pytest.raises(Exception, match="does not match the verified persistent primary backup volume"):
        service.execute(request)

    assert writer.calls == [] and readback.calls == []
    assert not service._readback_path(request).exists()


def test_volatile_readback_volume_is_rejected_before_writer(tmp_path, monkeypatch):
    service, request, writer, readback = _service_request(
        tmp_path,
        monkeypatch,
        storage_inspector=lambda primary_storage: _StorageInspector(primary_storage, filesystem_type="overlay"),
    )

    with pytest.raises(Exception, match="does not match the verified persistent primary backup volume"):
        service.execute(request)

    assert writer.calls == [] and readback.calls == []


def test_lock_release_fsync_failure_returns_uncertain_without_losing_terminal_result(tmp_path, monkeypatch):
    service, request, writer, readback = _service_request(tmp_path, monkeypatch)
    real_sync = approval_module._sync_directory
    journal_parent = Path(request.operation_journal_path).parent
    lock_path = service._lock_path(request.operation_journal_path)

    def fail_on_release(directory):
        if Path(directory) == journal_parent and not lock_path.exists():
            raise OSError("synthetic lock release fsync failure")
        real_sync(directory)

    monkeypatch.setattr(approval_module, "_sync_directory", fail_on_release)
    result = service.execute(request)

    assert result.status is WriteExecutionStatus.UNCERTAIN
    assert result.code == "LOCK_RELEASE_UNCERTAIN"
    assert result.state is OperationState.WRITE_VERIFIED
    assert result.readback_path is not None and result.readback_sha256 is not None
    assert len(writer.calls) == len(readback.calls) == 1
    assert OperationJournal.inspect_checkpoint(request.operation_journal_path).state is OperationState.WRITE_VERIFIED


def test_final_readback_digest_mutation_is_not_journaled_as_verified(tmp_path, monkeypatch):
    service, request, writer, readback = _service_request(tmp_path, monkeypatch)
    target = service._readback_path(request)
    original_hash = write_service_module._hash_file
    readback_hash_calls = 0

    def mutate_at_final_digest(path):
        nonlocal readback_hash_calls
        if Path(path) == target:
            readback_hash_calls += 1
            if readback_hash_calls == 2:
                payload = target.read_bytes()
                target.write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
        return original_hash(path)

    monkeypatch.setattr(write_service_module, "_hash_file", mutate_at_final_digest)
    result = service.execute(request)

    assert result.status is WriteExecutionStatus.UNCERTAIN
    assert result.code == "FINAL_VERIFICATION_DIGEST_CHANGED"
    assert result.state is OperationState.WRITE_UNCERTAIN
    assert len(writer.calls) == len(readback.calls) == 1
    assert OperationJournal.inspect_checkpoint(request.operation_journal_path).state is OperationState.WRITE_UNCERTAIN
    verified_entries = [
        entry for entry in OperationJournal.inspect_checkpoint(request.operation_journal_path).entries
        if entry.to_state is OperationState.WRITE_VERIFIED
    ]
    assert verified_entries == []


def test_process_level_operation_lock_rejects_second_writer(tmp_path, monkeypatch):
    service, request, writer, readback = _service_request(tmp_path, monkeypatch)
    lock_path = service._lock_path(request.operation_journal_path)
    with service._operation_lock(lock_path, request.operation_id):
        with pytest.raises(Exception, match="another write is active"):
            with service._operation_lock(lock_path, request.operation_id):
                pytest.fail("second process entered active write lock")
    assert writer.calls == [] and readback.calls == []


def test_lock_acquisition_failure_consumes_approval_without_writer(tmp_path, monkeypatch):
    service, request, writer, readback = _service_request(tmp_path, monkeypatch)
    lock_path = service._lock_path(request.operation_journal_path)
    with service._operation_lock(lock_path, request.operation_id):
        with pytest.raises(Exception, match="another write is active"):
            service.execute(request)

    assert list(service.approval_ledger.directory.glob("*.consumed"))
    assert writer.calls == [] and readback.calls == []


def test_t430_wrong_profile_is_rejected_before_evidence_confirmation_or_writer(tmp_path, monkeypatch):
    service, request, writer, readback = _service_request(tmp_path, monkeypatch)
    changed_plan = replace(request.plan, profile_id="t430-1vyrain", requested_model_id="t430")
    changed = replace(request, plan=changed_plan)
    with pytest.raises(Exception, match="only the exact X200 Libreboot and X220 Coreboot"):
        service.execute(changed)
    assert writer.calls == [] and readback.calls == []


def test_public_subprocess_writer_rejects_arbitrary_command_without_spawn(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    runner = SubprocessWriteCommandRunner()

    with pytest.raises(Exception, match="private one-use service capability"):
        runner.run(("flashrom", "-w", "arbitrary.rom"), 5)

    assert calls == []


def test_capability_mint_failure_after_write_started_is_terminal_failed_without_writer(tmp_path, monkeypatch):
    service, request, writer, readback = _service_request(tmp_path, monkeypatch)

    def reject_mint(*_args, **_kwargs):
        raise RuntimeError("synthetic capability validation failure")

    monkeypatch.setattr(write_service_module, "_mint_approved_write_capability", reject_mint)
    result = service.execute(request)

    assert result.status is WriteExecutionStatus.FAILED
    assert result.code == "WRITER_CAPABILITY_REJECTED"
    assert result.state is OperationState.WRITE_FAILED
    assert writer.calls == [] and readback.calls == []
    assert OperationJournal.inspect_checkpoint(request.operation_journal_path).state is OperationState.WRITE_FAILED


def test_unexpected_writer_exception_is_terminal_uncertain(tmp_path, monkeypatch):
    writer = _FakeWriter()
    writer.exception = RuntimeError("synthetic post-invocation runner error")
    service, request, _writer, readback = _service_request(tmp_path, monkeypatch, writer=writer)

    result = service.execute(request)

    assert result.status is WriteExecutionStatus.UNCERTAIN
    assert result.code == "WRITER_RUNTIME_ERROR_STATE_UNCERTAIN"
    assert result.state is OperationState.WRITE_UNCERTAIN
    assert len(writer.calls) == 1 and readback.calls == []
    assert OperationJournal.inspect_checkpoint(request.operation_journal_path).state is OperationState.WRITE_UNCERTAIN
