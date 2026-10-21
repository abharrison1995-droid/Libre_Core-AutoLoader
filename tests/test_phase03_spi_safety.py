"""Phase 03 software-only SPI, backup, readback, and recovery tests."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path

import pytest

from autoloader.exceptions import BackupUnavailableError
from autoloader.flasher import backup_service, recovery as recovery_module
from autoloader.flasher.backup import BackupManager
from autoloader.flasher.backup_service import (
    BackupCaptureError,
    BackupManifestError,
    BackupManifestStore,
    BackupSetService,
    FilesystemStorageInspector,
    StorageEvidence,
    StoragePreflightError,
)
from autoloader.flasher.flashrom import (
    FlashToolIdentity,
    FlashromRunner,
    ProgrammerType,
    ProbeOutputError,
    parse_probe_output,
)
from autoloader.flasher.readback import ReadbackComparator, RegionReadbackSpec
from autoloader.flasher.recovery import RecoveryReadinessService
from autoloader.flasher.spi import (
    ChipProbeEvidence,
    SPIOperationError,
    SPIProbeService,
    SPITransportResult,
)
from autoloader.workflow.types import (
    ArtifactEvidence,
    BackupEvidence,
    ChipEvidence,
    ChipTopology,
    BuildIntentEvidence,
    CompatibilityDecision,
    ProgrammerEvidence,
    ProgrammerKind,
    DeviceEvidence,
    FirmwareFamily,
    OperationPlan,
    RegionEvidence,
    CompatibilityResult,
    EvidenceConfidence,
    VerificationStatus,
)
from tests.test_workflow_safety import MIB, make_t430_plan, make_x200_plan


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rewrite_backup_manifests(result, mutation) -> tuple[str, str]:
    """Reseal both synthetic manifest chains after an intentional tamper."""
    hashes = []
    for raw_path in (result.primary_manifest, result.recovery_manifest):
        path = Path(raw_path)
        document = json.loads(path.read_text(encoding="utf-8"))
        mutation(document["events"][-1])
        previous = "0" * 64
        for index, event in enumerate(document["events"], start=1):
            event["sequence"] = index
            event["previous_sha256"] = previous
            payload = {key: value for key, value in event.items() if key != "event_sha256"}
            event["event_sha256"] = backup_service._sha256(backup_service._canonical(payload))
            previous = event["event_sha256"]
        body = {key: value for key, value in document.items() if key != "manifest_sha256"}
        document["manifest_sha256"] = backup_service._sha256(backup_service._canonical(body))
        path.write_bytes(backup_service._canonical(document))
        hashes.append(document["manifest_sha256"])
    return hashes[0], hashes[1]


def _payload(size: int, salt: int = 3) -> bytes:
    if size <= 0:
        return b""
    # Adding 251 to index adds 37 * 251, which is zero modulo 251, so this
    # exact 251-byte cycle reproduces the original formula for every salt.
    cycle = bytes((index * 37 + salt) % 251 for index in range(251))
    full_cycles, remainder = divmod(size, len(cycle))
    return cycle * full_cycles + cycle[:remainder]


def test_payload_cycle_matches_original_formula_for_boundaries_and_salts() -> None:
    boundary_sizes = (-1, 0, 1, 249, 250, 251, 252, 500, 501, 502, 503, 504)
    # Cover every salt residue repeatedly, including negative salts.
    for salt in range(-251, 502):
        for size in boundary_sizes:
            expected = bytes((index * 37 + salt) % 251 for index in range(size))
            assert _payload(size, salt) == expected

    # Also compare a multi-megabyte value through the same original formula.
    size = 4 * MIB + 7
    salt = -999
    expected = bytes((index * 37 + salt) % 251 for index in range(size))
    assert _payload(size, salt) == expected


class FakeStorageInspector:
    def __init__(self, *, same_volume: bool = False, persistent: bool = True) -> None:
        self.same_volume = same_volume
        self.persistent = persistent
        self.calls: list[tuple[str, int]] = []

    def inspect(self, directory, required_bytes, *, persistence_confirmed):
        path = Path(directory).resolve()
        self.calls.append((str(path), required_bytes))
        volume_id = "fake-volume-primary"
        if not self.same_volume and path.name == "recovery":
            volume_id = "fake-volume-recovery"
        return StorageEvidence(
            path=str(path),
            volume_id=volume_id,
            filesystem_type="fake-persistent-fs",
            available_bytes=required_bytes + 1_000_000,
            required_bytes=required_bytes,
            persistence_confirmed=self.persistent and persistence_confirmed,
            scratch_readback_verified=self.persistent,
            observed_at_utc=datetime.now(timezone.utc).isoformat(),
        )


class FakeTransport:
    def __init__(self, probes, read_payloads, *, mismatch_second=False, fail_read=None, short_read=None):
        self.identity = FlashToolIdentity(
            name="flashrom",
            executable=str(Path(__file__).resolve()),
            version="1.4.0",
            raw_version="flashrom v1.4.0 fake build",
            observed_at_utc=datetime.now(timezone.utc).isoformat(),
            sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            package_owner="pacman: test/flashrom 1.4.0-1",
        )
        self.probes = list(probes)
        self.read_payloads = dict(read_payloads)
        self.mismatch_second = mismatch_second
        self.fail_read = fail_read
        self.short_read = short_read
        self.probe_calls = 0
        self.read_calls = []
        self.count_by_capacity: dict[int, int] = {}

    def inspect_tool(self):
        return self.identity

    def probe(self, programmer):
        self.probe_calls += 1
        if not self.probes:
            return SPITransportResult(False, "no fake probe remaining", 0.0)
        return SPITransportResult(True, self.probes.pop(0), 0.0)

    def read_chip(self, programmer, probe, destination):
        self.read_calls.append((programmer, probe.chip_name, Path(destination)))
        count = self.count_by_capacity.get(probe.capacity_bytes, 0) + 1
        self.count_by_capacity[probe.capacity_bytes] = count
        path = Path(destination)
        payload = self.read_payloads[probe.capacity_bytes]
        if self.mismatch_second and count == 2:
            payload = _payload(len(payload), salt=19)
        if self.short_read == count:
            payload = payload[:-1]
        if self.fail_read == count:
            path.write_bytes(payload[: max(1, len(payload) // 2)])
            return SPITransportResult(False, "injected fake read failure", 0.0)
        path.write_bytes(payload)
        return SPITransportResult(True, "fake full-chip read", 0.0)


def _probe_line(chip: str, capacity: int, programmer: str = "ch341a_spi") -> str:
    return f'Found Winbond flash chip "{chip}" ({capacity // 1024} kB, SPI) on {programmer}.\n'


def _make_x200(tmp_path: Path):
    artifact = tmp_path / "artifact.rom"
    artifact.write_bytes(_payload(4 * MIB))
    old_a = tmp_path / "old-a.rom"
    old_b = tmp_path / "old-b.rom"
    old_a.write_bytes(_payload(4 * MIB))
    old_b.write_bytes(_payload(4 * MIB))
    plan = make_x200_plan({"libreboot.rom": artifact, "backup-a.bin": old_a, "backup-b.bin": old_b})
    return replace(plan, backup=BackupEvidence())


def _make_t430(tmp_path: Path):
    artifact = tmp_path / "t430-original.rom"
    artifact.write_bytes(_payload(4 * MIB, 11))
    plan = make_t430_plan(artifact, "G1ET74WW (2.64 )")
    topology = ChipTopology(
        observed=True,
        chips=(
            ChipEvidence("bottom", 8 * MIB, True, physical_label="bottom", voltage_mv=3300),
            ChipEvidence("top", 4 * MIB, True, physical_label="top", voltage_mv=3300),
        ),
    )
    return replace(plan, topology=topology, backup=BackupEvidence())


def _make_x220(tmp_path: Path):
    artifact_path = tmp_path / "x220-coreboot.rom"
    content = _payload(8 * MIB, 23)
    artifact_path.write_bytes(content)
    regions = (
        RegionEvidence("ifd", 0, 4096),
        RegionEvidence("gbe", 4096, 512 * 1024),
        RegionEvidence("me", 512 * 1024, 5 * MIB),
        RegionEvidence("bios", 5 * MIB, 8 * MIB),
    )
    return OperationPlan(
        profile_id="x220-coreboot",
        requested_model_id="x220",
        firmware_family=FirmwareFamily.COREBOOT,
        device=DeviceEvidence(
            raw_dmi_product_name="ThinkPad X220",
            raw_dmi_product_version="4291CTO",
            raw_dmi_system_family="ThinkPad X220",
            raw_mtm="4291CTO",
        ),
        topology=ChipTopology(
            observed=True,
            chips=(ChipEvidence("main", 8 * MIB, True, physical_label="main", voltage_mv=3300),),
            regions=regions,
        ),
        programmer=ProgrammerEvidence(
            kind=ProgrammerKind.EXTERNAL_SPI,
            observed=True,
            interface_verified=True,
            wiring_verified=True,
            voltage_mv=3300,
        ),
        artifact=ArtifactEvidence(
            path=str(artifact_path),
            model_id="x220",
            firmware_family=FirmwareFamily.COREBOOT,
            expected_sha256=hashlib.sha256(content).hexdigest(),
            expected_size_bytes=len(content),
            source_url="https://doc.coreboot.org/mainboard/lenovo/Sandy_Bridge_series.html",
            source_revision="a3427b34102f8f9887acf65e23d7fd5900fe1d40",
            regions=regions,
        ),
    )


def _make_service(tmp_path: Path, transport: FakeTransport, *, same_volume=False, persistent=True):
    primary = tmp_path / "primary"
    recovery = tmp_path / "recovery"
    primary.mkdir(exist_ok=True)
    recovery.mkdir(exist_ok=True)
    service = BackupSetService(
        transport,
        storage_inspector=FakeStorageInspector(same_volume=same_volume, persistent=persistent),
    )
    return service, primary, recovery


def _probe(transport: FakeTransport, count: int = 1):
    service = SPIProbeService(transport)
    return tuple(service.probe(ProgrammerType.CH341A) for _ in range(count))


def _capture(service, plan, probes, primary, recovery, *, programmer_evidence=None):
    return service.capture(
        plan,
        probes,
        programmer=ProgrammerType.CH341A,
        programmer_evidence=programmer_evidence,
        primary_directory=primary,
        recovery_directory=recovery,
        primary_persistent_confirmed=True,
        recovery_persistent_confirmed=True,
    )


def test_probe_parser_requires_one_exact_unforced_chip_result():
    output = _probe_line("W25Q32.V", 4 * MIB)
    assert parse_probe_output(output) == ("W25Q32.V", 4 * MIB, "ch341a_spi")
    with pytest.raises(ProbeOutputError, match="exactly one"):
        parse_probe_output(output + output)
    with pytest.raises(ProbeOutputError, match="exactly one"):
        parse_probe_output("No EEPROM/flash device found.\n")
    with pytest.raises(ProbeOutputError, match="malformed"):
        parse_probe_output(output + "Found unsupported flash chip output\n")


def test_legacy_single_chip_backup_and_restore_paths_remain_disabled_without_transport_calls(tmp_path):
    class NeverRunner:
        def __getattr__(self, name):
            pytest.fail(f"legacy backup must not call transport method {name}")

    manager = BackupManager(tmp_path / "legacy", runner=NeverRunner())
    with pytest.raises(BackupUnavailableError, match="Single-chip backup capture is disabled"):
        manager.create_backup("x200", ProgrammerType.CH341A, "chip")

    assert manager.restore_from_backup(
        str(tmp_path / "original.rom"), ProgrammerType.CH341A
    ) is False
    assert manager.get_manifest() == []


@pytest.mark.parametrize(
    ("first_bytes", "second_bytes", "expected_match", "expected_hash"),
    [
        (None, b"valid", False, ""),
        (b"", b"valid", False, ""),
        (b"\xff" * 1024, b"valid", False, ""),
        (b"\x00" * 1024, b"valid", False, ""),
        (b"\xff" * 9990 + bytes(range(10)), b"valid", False, ""),
        (b"first non-degenerate read", b"different read", False, None),
        (b"same non-degenerate read", b"same non-degenerate read", True, None),
    ],
    ids=[
        "missing-first",
        "empty-first",
        "all-ff-first",
        "all-zero-first",
        "near-blank-first",
        "different-reads",
        "matching-reads",
    ],
)
def test_legacy_pair_verification_never_accepts_missing_degenerate_or_mismatched_reads(
    tmp_path, first_bytes, second_bytes, expected_match, expected_hash
):
    manager = BackupManager(tmp_path / "legacy")
    first = tmp_path / "first.rom"
    second = tmp_path / "second.rom"
    if first_bytes is not None:
        first.write_bytes(first_bytes)
    if second_bytes is not None:
        second.write_bytes(second_bytes)

    matched, digest = manager.verify_backup_pair(first, second)

    assert matched is expected_match
    if expected_hash is None:
        assert digest == hashlib.sha256(first_bytes).hexdigest()
    else:
        assert digest == expected_hash


def test_runner_rejects_caller_chip_override_without_running_probe(monkeypatch):
    runner = FlashromRunner()
    monkeypatch.setattr(runner, "run_command", lambda *args, **kwargs: pytest.fail("must not probe"))
    result = runner.probe_chip(ProgrammerType.CH341A, chip_name="W25Q32.V")
    assert result.success is False
    assert result.code == "PROBE_OVERRIDE_REJECTED"


def test_probe_evidence_rejects_reported_programmer_mismatch():
    fake = FakeTransport([_probe_line("W25Q32.V", 4 * MIB, "buspirate_spi")], {})
    with pytest.raises(SPIOperationError, match="different programmer"):
        SPIProbeService(fake).probe(ProgrammerType.CH341A)


def test_backup_probe_matcher_requires_observed_topology_and_selected_programmer(tmp_path):
    from autoloader.exceptions import BackupUnavailableError
    from autoloader.flasher.backup_service import _match_probes_to_topology

    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB, 5)
    transport = FakeTransport([_probe_line("W25Q32.V", len(content))], {len(content): content})
    probes = _probe(transport)
    with pytest.raises(BackupUnavailableError, match="complete observed chip topology"):
        _match_probes_to_topology(
            replace(plan, topology=replace(plan.topology, observed=False)),
            probes,
            ProgrammerType.CH341A,
        )

    buspirate_probe = ChipProbeEvidence.from_output(
        programmer=ProgrammerType.BUS_PIRATE,
        tool=transport.identity,
        output=_probe_line("W25Q32.V", len(content), "buspirate_spi"),
    )
    with pytest.raises(BackupUnavailableError, match="differs from the selected backup programmer"):
        _match_probes_to_topology(plan, (buspirate_probe,), ProgrammerType.CH341A)
    assert transport.read_calls == []


def test_backup_programmer_voltage_must_match_each_observed_chip(tmp_path):
    from autoloader.exceptions import BackupUnavailableError
    from autoloader.flasher.backup_service import _validate_external_backup_programmer

    plan = _make_x200(tmp_path)
    evidence = ProgrammerEvidence(
        kind=ProgrammerKind.EXTERNAL_SPI,
        observed=True,
        interface_verified=True,
        wiring_verified=True,
        voltage_mv=3300,
    )
    wrong_chip_voltage_plan = replace(
        plan,
        topology=replace(
            plan.topology,
            chips=(replace(plan.topology.chips[0], voltage_mv=1800),),
        ),
    )
    with pytest.raises(BackupUnavailableError, match="observed voltage matching the programmer"):
        _validate_external_backup_programmer(
            wrong_chip_voltage_plan, ProgrammerType.CH341A, evidence
        )


def test_backup_service_captures_complete_persistent_x200_set_and_recovery_readiness(tmp_path):
    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB)
    transport = FakeTransport([_probe_line("W25Q32.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)

    result = _capture(service, plan, probes, primary, recovery)

    assert len(transport.read_calls) == 2
    assert result.backup.pairs[0].first_sha256 == result.backup.pairs[0].second_sha256
    assert Path(result.chips[0].first_path).is_file()
    assert Path(result.chips[0].second_path).is_file()
    assert result.primary_storage.volume_id != result.recovery_storage.volume_id
    primary_events = BackupManifestStore(primary).read_events()
    recovery_events = BackupManifestStore(recovery).read_events()
    assert [event["status"] for event in primary_events] == ["in-progress", "verified"]
    assert primary_events[-1]["chips"][0]["capacity_bytes"] == 4 * MIB
    assert primary_events[-1]["tool"]["version"] == "1.4.0"
    assert recovery_events[-1]["event_sha256"] == primary_events[-1]["event_sha256"]

    readiness = RecoveryReadinessService().evaluate(plan, result)
    assert readiness.status == "ready", readiness.reasons
    assert readiness.runbook == str(
        Path(recovery_module.__file__).resolve().parent / "data" / "RECOVERY_RUNBOOK.md"
    )
    assert Path(readiness.runbook).is_file()
    assert Path(readiness.runbook).read_bytes() == files("autoloader.flasher").joinpath(
        "data", "RECOVERY_RUNBOOK.md"
    ).read_bytes()
    assert readiness.recovery_write_enabled is False
    assert readiness.hardware_recovery_tested is False


def test_recovery_runbook_is_bundled_and_matches_reviewed_v1_document():
    source = Path(__file__).resolve().parents[1] / "docs" / "v1" / "RECOVERY_RUNBOOK.md"
    bundled = files("autoloader.flasher").joinpath("data", "RECOVERY_RUNBOOK.md")

    assert bundled.is_file()
    assert bundled.read_bytes() == source.read_bytes()


@pytest.mark.parametrize("runbook_bytes", [None, b"changed recovery guidance\n"])
def test_recovery_readiness_blocks_missing_or_changed_installed_runbook(
    tmp_path, monkeypatch, runbook_bytes
):
    resource_path = tmp_path / "installed-package" / "autoloader" / "flasher" / "data" / "RECOVERY_RUNBOOK.md"
    if runbook_bytes is not None:
        resource_path.parent.mkdir(parents=True)
        resource_path.write_bytes(runbook_bytes)
    monkeypatch.setattr(recovery_module, "_installed_recovery_runbook_path", lambda: resource_path)
    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB, 31)
    transport = FakeTransport([_probe_line("W25Q32.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery_store = _make_service(tmp_path, transport)
    result = _capture(service, plan, probes, primary, recovery_store)

    readiness = RecoveryReadinessService().evaluate(plan, result)

    assert readiness.status == "blocked"
    assert readiness.runbook == str(resource_path)
    assert any("recovery runbook is missing from the installation" in reason for reason in readiness.reasons)


def test_backup_reopen_rejects_operation_artifact_drift_before_reusing_backup(tmp_path):
    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB, 29)
    transport = FakeTransport([_probe_line("W25Q32.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)
    result = _capture(service, plan, probes, primary, recovery)
    reads_before_reopen = list(transport.read_calls)
    changed_plan = replace(
        plan,
        artifact=replace(plan.artifact, expected_sha256="f" * 64),
    )

    with pytest.raises(BackupManifestError, match="current exact operation artifact"):
        service.load_verified_result(
            changed_plan,
            operation_id=result.operation_id,
            primary_manifest=result.primary_manifest,
            recovery_manifest=result.recovery_manifest,
            primary_manifest_sha256=result.primary_manifest_sha256,
            recovery_manifest_sha256=result.recovery_manifest_sha256,
            expected_tool=transport.identity,
        )
    assert transport.read_calls == reads_before_reopen


def test_recovery_readiness_blocks_changed_plan_and_later_failure_event(tmp_path):
    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB)
    transport = FakeTransport([_probe_line("W25Q32.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)
    result = _capture(service, plan, probes, primary, recovery)
    readiness = RecoveryReadinessService()

    altered_chip = replace(plan.topology.chips[0], physical_label="different physical chip")
    altered_plan = replace(plan, topology=replace(plan.topology, chips=(altered_chip,)))
    changed = readiness.evaluate(altered_plan, result)
    assert changed.status == "blocked"
    assert any("manifest chip topology" in reason for reason in changed.reasons)

    failure = {"operation_id": result.operation_id, "status": "failed", "failure": "injected audit failure"}
    BackupManifestStore(primary).append(failure)
    BackupManifestStore(recovery).append(failure)
    terminal_failure = readiness.evaluate(plan, result)
    assert terminal_failure.status == "blocked"
    assert any("does not end with a verified event" in reason for reason in terminal_failure.reasons)


def test_backup_service_captures_each_observed_t430_chip_twice(tmp_path):
    plan = _make_t430(tmp_path)
    content4 = _payload(4 * MIB, 9)
    content8 = _payload(8 * MIB, 17)
    transport = FakeTransport(
        [
            _probe_line("W25Q64.V", 8 * MIB),
            _probe_line("W25Q32.V", 4 * MIB),
        ],
        {8 * MIB: content8, 4 * MIB: content4},
    )
    probes = _probe(transport, count=2)
    service, primary, recovery = _make_service(tmp_path, transport)
    external = ProgrammerEvidence(
        kind=ProgrammerKind.EXTERNAL_SPI,
        observed=True,
        interface_verified=True,
        wiring_verified=True,
        voltage_mv=3300,
    )

    result = _capture(service, plan, probes, primary, recovery, programmer_evidence=external)

    assert len(transport.read_calls) == 4
    assert {item.chip_id for item in result.chips} == {"bottom", "top"}
    assert {item.capacity_bytes for item in result.chips} == {4 * MIB, 8 * MIB}


def test_backup_service_preserves_x220_ifd_gbe_me_bios_map(tmp_path):
    plan = _make_x220(tmp_path)
    content = _payload(8 * MIB, 31)
    transport = FakeTransport([_probe_line("W25Q64.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)

    result = _capture(service, plan, probes, primary, recovery)

    assert len(transport.read_calls) == 2
    event = BackupManifestStore(primary).read_events()[-1]
    assert event["region_map"] == [
        {"name": "ifd", "start_byte": 0, "end_byte": 4096},
        {"name": "gbe", "start_byte": 4096, "end_byte": 512 * 1024},
        {"name": "me", "start_byte": 512 * 1024, "end_byte": 5 * MIB},
        {"name": "bios", "start_byte": 5 * MIB, "end_byte": 8 * MIB},
    ]
    assert RecoveryReadinessService().evaluate(plan, result).status == "ready"


def test_x220_backup_capture_binds_prebuild_intent_without_future_artifact(tmp_path):
    from autoloader.rom_builder.artifact_engine import COREBOOT_2609, X220_COREBOOT

    plan = _make_x220(tmp_path)
    intent_programmer = replace(
        plan.programmer,
        evidence_source="synthetic test attestation",
        attested_by="test operator",
        attested_at=datetime.now(timezone.utc).isoformat(),
        instrument_id="synthetic meter",
        measurement_test_point="synthetic point",
    )
    intent = BuildIntentEvidence(
        profile_id=plan.profile_id,
        requested_model_id=plan.requested_model_id,
        firmware_family=plan.firmware_family,
        device=plan.device,
        topology=plan.topology,
        programmer=intent_programmer,
        recipe_id=X220_COREBOOT.recipe_id,
        source_url=COREBOOT_2609.repository_url,
        source_revision=COREBOOT_2609.revision,
        source_tree_id=COREBOOT_2609.tree_id,
        configuration_sha256=X220_COREBOOT.expected_config_sha256,
        expected_size_bytes=8 * MIB,
    )
    content = _payload(8 * MIB, 43)
    transport = FakeTransport([_probe_line("W25Q64.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)

    result = _capture(service, intent, probes, primary, recovery)

    event = BackupManifestStore(primary).read_events()[-1]
    assert event["source_artifact"] is None
    assert event["build_intent"]["recipe_id"] == X220_COREBOOT.recipe_id
    assert event["build_intent_sha256"]
    assert RecoveryReadinessService().evaluate(intent, result).status == "ready"
    reopened = service.load_verified_result(
        intent,
        operation_id=result.operation_id,
        primary_manifest=result.primary_manifest,
        recovery_manifest=result.recovery_manifest,
        primary_manifest_sha256=result.primary_manifest_sha256,
        recovery_manifest_sha256=result.recovery_manifest_sha256,
        expected_tool=transport.identity,
    )
    assert reopened.operation_id == result.operation_id
    changed_intent = replace(intent, source_revision="f" * 40)
    with pytest.raises(BackupManifestError, match="current exact build intent"):
        service.load_verified_result(
            changed_intent,
            operation_id=result.operation_id,
            primary_manifest=result.primary_manifest,
            recovery_manifest=result.recovery_manifest,
            primary_manifest_sha256=result.primary_manifest_sha256,
            recovery_manifest_sha256=result.recovery_manifest_sha256,
            expected_tool=transport.identity,
        )


def test_backup_service_blocks_invalid_x220_region_map_before_reads(tmp_path):
    from autoloader.exceptions import BackupUnavailableError

    plan = _make_x220(tmp_path)
    invalid_regions = tuple(
        replace(region, end_byte=7 * MIB) if region.name == "bios" else region
        for region in plan.topology.regions
    )
    invalid_plan = replace(plan, topology=replace(plan.topology, regions=invalid_regions))
    content = _payload(8 * MIB, 37)
    transport = FakeTransport([_probe_line("W25Q64.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)

    with pytest.raises(BackupUnavailableError, match="not fully compatible"):
        _capture(service, invalid_plan, probes, primary, recovery)
    assert transport.read_calls == []


def test_backup_service_blocks_incomplete_probe_or_transient_storage_before_reads(tmp_path):
    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB)
    transport = FakeTransport([_probe_line("W25Q32.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport, persistent=False)
    with pytest.raises(StoragePreflightError, match="persistent"):
        _capture(service, plan, probes, primary, recovery)
    assert transport.read_calls == []


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("relative-primary", "primary backup destination must be absolute and link-free"),
        ("same-destination", "stored at different destinations"),
    ],
)
def test_backup_storage_identity_failure_stops_before_manifest_or_read(
    tmp_path, case: str, message: str
) -> None:
    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB, 41)
    transport = FakeTransport([_probe_line("W25Q32.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)
    original_inspect = service.storage_inspector.inspect

    def inspect(path, required_bytes, *, persistence_confirmed):
        evidence = original_inspect(path, required_bytes, persistence_confirmed=persistence_confirmed)
        if case == "relative-primary" and Path(path) == primary:
            return replace(evidence, path="relative-backup")
        if case == "same-destination" and Path(path) == recovery:
            return replace(evidence, path=str(primary), volume_id="different-claimed-volume")
        return evidence

    service.storage_inspector.inspect = inspect
    with pytest.raises(StoragePreflightError, match=message):
        _capture(service, plan, probes, primary, recovery)
    assert transport.read_calls == []
    assert not (primary / "backup-manifest.json").exists()
    assert not (recovery / "backup-manifest.json").exists()


@pytest.mark.parametrize(
    ("case", "message", "expected_reads"),
    [
        ("missing-dump", "did not create a file", 1),
        ("short-dump", "expected exactly", 1),
        ("blank-dump", "blank or degenerate", 1),
    ],
)
def test_backup_capture_rejects_missing_short_and_blank_dump_outputs(
    tmp_path, case: str, message: str, expected_reads: int
) -> None:
    plan = _make_x200(tmp_path)
    content = b"\x00" * (4 * MIB) if case == "blank-dump" else _payload(4 * MIB, 47)

    class MissingDumpTransport(FakeTransport):
        def read_chip(self, programmer, probe, destination):
            self.read_calls.append((programmer, probe.chip_name, Path(destination)))
            return SPITransportResult(True, "fake success without an output file", 0.0)

    if case == "missing-dump":
        transport = MissingDumpTransport([_probe_line("W25Q32.V", 4 * MIB)], {4 * MIB: content})
    else:
        transport = FakeTransport(
            [_probe_line("W25Q32.V", 4 * MIB)],
            {4 * MIB: content},
            short_read=1 if case == "short-dump" else None,
        )
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)
    with pytest.raises(BackupCaptureError, match=message):
        _capture(service, plan, probes, primary, recovery)
    assert len(transport.read_calls) == expected_reads
    assert [event["status"] for event in BackupManifestStore(primary).read_events()] == ["in-progress", "failed"]
    assert [event["status"] for event in BackupManifestStore(recovery).read_events()] == ["in-progress", "failed"]
    assert not any(event["status"] == "verified" for event in BackupManifestStore(primary).read_events())


def test_backup_service_rechecks_exact_tool_identity_before_reads(tmp_path):
    from autoloader.exceptions import BackupUnavailableError

    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB)
    transport = FakeTransport([_probe_line("W25Q32.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)
    transport.identity = replace(transport.identity, executable=str(Path(__file__).resolve().parent))

    with pytest.raises(BackupUnavailableError, match="differs from the exact probe evidence"):
        _capture(service, plan, probes, primary, recovery)
    assert transport.read_calls == []


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("kind", "independently observed external SPI programmer"),
        ("not-observed", "independently observed external SPI programmer"),
        ("interface", "verified programmer interface and wiring"),
        ("wiring", "verified programmer interface and wiring"),
        ("voltage-unknown", "exactly 3300 mV"),
        ("voltage-mismatch", "exactly 3300 mV"),
        ("internal-selected", "generic internal programmer is not an allowed"),
    ],
)
def test_backup_capture_rejects_untrusted_programmer_attestation_before_reads(
    tmp_path, case: str, message: str
) -> None:
    from autoloader.exceptions import BackupUnavailableError

    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB)
    transport = FakeTransport([_probe_line("W25Q32.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)
    evidence = plan.programmer
    selected = ProgrammerType.CH341A
    if case == "kind":
        evidence = replace(evidence, kind=ProgrammerKind.SIMULATION)
    elif case == "not-observed":
        evidence = replace(evidence, observed=False)
    elif case == "interface":
        evidence = replace(evidence, interface_verified=False)
    elif case == "wiring":
        evidence = replace(evidence, wiring_verified=None)
    elif case == "voltage-unknown":
        evidence = replace(evidence, voltage_mv=None)
    elif case == "voltage-mismatch":
        evidence = replace(evidence, voltage_mv=1800)
    elif case == "internal-selected":
        selected = ProgrammerType.INTERNAL

    with pytest.raises(BackupUnavailableError, match=message):
        service.capture(
            plan,
            probes,
            programmer=selected,
            programmer_evidence=evidence,
            primary_directory=primary,
            recovery_directory=recovery,
            primary_persistent_confirmed=True,
            recovery_persistent_confirmed=True,
        )
    assert transport.read_calls == []
    assert not (primary / "backup-manifest.json").exists()
    assert not (recovery / "backup-manifest.json").exists()


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("probe-count", "exactly every chip"),
        ("duplicate-probe-id", "distinct raw probe record"),
        ("duplicate-capacity", "mapping is ambiguous"),
        ("capacity-mismatch", "capacities do not match"),
        ("different-tool", "same exact tool executable and version"),
        ("changed-probe-digest", "evidence fields do not match"),
    ],
)
def test_t430_backup_capture_rejects_probe_topology_drift_before_reads(
    tmp_path, case: str, message: str
) -> None:
    from autoloader.exceptions import BackupUnavailableError

    plan = _make_t430(tmp_path)
    content4 = _payload(4 * MIB, 3)
    content8 = _payload(8 * MIB, 7)
    probe_outputs = [_probe_line("W25Q64.V", 8 * MIB), _probe_line("W25Q32.V", 4 * MIB)]
    if case == "capacity-mismatch":
        probe_outputs[1] = _probe_line("W25Q128.V", 16 * MIB)
    transport = FakeTransport(probe_outputs, {4 * MIB: content4, 8 * MIB: content8, 16 * MIB: _payload(16 * MIB, 13)})
    probes = _probe(transport, count=2)
    service, primary, recovery = _make_service(tmp_path, transport)
    external = ProgrammerEvidence(
        kind=ProgrammerKind.EXTERNAL_SPI,
        observed=True,
        interface_verified=True,
        wiring_verified=True,
        voltage_mv=3300,
    )

    if case == "probe-count":
        probes = probes[:1]
    elif case == "duplicate-probe-id":
        probes = (probes[0], replace(probes[1], probe_id=probes[0].probe_id))
    elif case == "duplicate-capacity":
        probes = (probes[0], replace(
            probes[1],
            chip_name=probes[0].chip_name,
            capacity_bytes=probes[0].capacity_bytes,
            raw_output=probes[0].raw_output,
            raw_output_sha256=probes[0].raw_output_sha256,
            reported_programmer=probes[0].reported_programmer,
        ))
    elif case == "different-tool":
        other_tool = replace(
            probes[1].tool,
            version="1.5.0",
            raw_version="flashrom v1.5.0 synthetic build",
        )
        probes = (probes[0], replace(probes[1], tool=other_tool))
    elif case == "changed-probe-digest":
        probes = (replace(probes[0], raw_output_sha256="0" * 64), probes[1])

    with pytest.raises((BackupUnavailableError, SPIOperationError), match=message):
        _capture(service, plan, probes, primary, recovery, programmer_evidence=external)
    assert transport.read_calls == []
    assert not (primary / "backup-manifest.json").exists()
    assert not (recovery / "backup-manifest.json").exists()


def test_backup_service_requires_separate_storage_volumes(tmp_path):
    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB)
    transport = FakeTransport([_probe_line("W25Q32.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport, same_volume=True)
    with pytest.raises(StoragePreflightError, match="separately identified"):
        _capture(service, plan, probes, primary, recovery)
    assert transport.read_calls == []


def test_backup_service_rejects_corrupt_manifest_before_hardware_reads(tmp_path):
    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB)
    transport = FakeTransport([_probe_line("W25Q32.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)
    (recovery / "backup-manifest.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(BackupManifestError):
        _capture(service, plan, probes, primary, recovery)
    assert transport.read_calls == []


@pytest.mark.parametrize(
    ("tamper", "expected_message"),
    [
        ("schema", "unsupported schema"),
        ("events-type", "events are malformed"),
        ("event-type", "invalid sequence data"),
        ("sequence", "invalid sequence data"),
        ("previous-hash", "breaks its checksum chain"),
        ("event-hash", "event .* checksum is invalid"),
        ("manifest-hash", "manifest checksum is invalid"),
    ],
)
def test_backup_manifest_chain_tampering_blocks_before_spi_reads(
    tmp_path, tamper, expected_message
):
    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB)
    transport = FakeTransport([_probe_line("W25Q32.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)
    recovery_store = BackupManifestStore(recovery)
    recovery_store.append({"status": "synthetic seed for integrity validation"})
    manifest_path = recovery / "backup-manifest.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    if tamper == "schema":
        document["schema_version"] = 999
    elif tamper == "events-type":
        document["events"] = {"not": "a list"}
    elif tamper == "event-type":
        document["events"][0] = None
    elif tamper == "sequence":
        document["events"][0]["sequence"] = 2
    elif tamper == "previous-hash":
        document["events"][0]["previous_sha256"] = "f" * 64
    elif tamper == "event-hash":
        document["events"][0]["status"] = "altered without resealing"
    elif tamper == "manifest-hash":
        document["manifest_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(BackupManifestError, match=expected_message):
        _capture(service, plan, probes, primary, recovery)
    assert transport.read_calls == []
    assert not list(primary.rglob("*.partial")) and not list(recovery.rglob("*.partial"))


def test_recovery_manifest_start_failure_blocks_before_any_spi_read(tmp_path, monkeypatch):
    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB)
    transport = FakeTransport([_probe_line("W25Q32.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)
    original_append = BackupManifestStore.append
    injected = False

    def fail_recovery_start_once(store, event):
        nonlocal injected
        if store.path.parent == recovery and event.get("status") == "in-progress" and not injected:
            injected = True
            raise BackupManifestError("injected recovery manifest start failure")
        return original_append(store, event)

    monkeypatch.setattr(BackupManifestStore, "append", fail_recovery_start_once)

    with pytest.raises(BackupManifestError, match="injected recovery manifest start failure"):
        _capture(service, plan, probes, primary, recovery)

    assert injected is True
    assert transport.read_calls == []
    # The primary write-ahead marker exists, but the recovery marker does not;
    # the operation stops before filesystem staging or any programmer read.
    assert [event["status"] for event in BackupManifestStore(primary).read_events()] == ["in-progress"]
    assert BackupManifestStore(recovery).read_events() == []
    assert not list(primary.glob("*/")) and not list(recovery.glob("*/"))
    assert not list(primary.rglob("*.rom")) and not list(recovery.rglob("*.rom"))


def test_backup_mismatch_preserves_both_reads_and_never_retries(tmp_path):
    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB)
    transport = FakeTransport(
        [_probe_line("W25Q32.V", len(content))],
        {len(content): content},
        mismatch_second=True,
    )
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)
    with pytest.raises(BackupCaptureError, match="independent reads differ"):
        _capture(service, plan, probes, primary, recovery)
    assert len(transport.read_calls) == 2
    assert len(list(primary.rglob("*.partial"))) == 1
    assert len(list(recovery.rglob("*.partial"))) == 1
    events = BackupManifestStore(primary).read_events()
    assert events[-1]["status"] == "failed"
    assert events[-1]["automatic_retry"] is False
    assert events[-1]["automatic_reboot"] is False


def test_backup_detects_first_dump_mutation_after_initial_hash_and_preserves_failure(tmp_path, monkeypatch):
    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB)
    transport = FakeTransport([_probe_line("W25Q32.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)
    original_hash = backup_service._hash_file
    mutated = False

    def hash_then_mutate_first_partial(path):
        nonlocal mutated
        digest = original_hash(path)
        if path.name.endswith("read-1.partial") and not mutated:
            path.write_bytes(_payload(len(content), salt=71))
            mutated = True
        return digest

    monkeypatch.setattr(backup_service, "_hash_file", hash_then_mutate_first_partial)
    with pytest.raises(BackupCaptureError, match="persisted backup changed after read"):
        _capture(service, plan, probes, primary, recovery)

    assert mutated is True
    assert len(transport.read_calls) == 2
    primary_events = BackupManifestStore(primary).read_events()
    recovery_events = BackupManifestStore(recovery).read_events()
    assert [event["status"] for event in primary_events] == ["in-progress", "failed"]
    assert [event["status"] for event in recovery_events] == ["in-progress", "failed"]
    assert primary_events[-1]["automatic_retry"] is False
    assert recovery_events[-1]["automatic_reboot"] is False
    assert len(list(primary.rglob("*.rom"))) == 1
    assert len(list(recovery.rglob("*.rom"))) == 1


def test_backup_policy_failure_after_reads_never_publishes_verified_result(tmp_path, monkeypatch):
    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB)
    transport = FakeTransport([_probe_line("W25Q32.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)

    def reject_saved_pair(_plan):
        return CompatibilityResult(
            CompatibilityDecision.BLOCKED,
            "x200",
            EvidenceConfidence.LOW,
            ("injected independent backup policy rejection",),
        )

    monkeypatch.setattr(service.validator, "validate_backup", reject_saved_pair)
    with pytest.raises(BackupCaptureError, match="failed independent policy validation"):
        _capture(service, plan, probes, primary, recovery)

    assert len(transport.read_calls) == 2
    for directory in (primary, recovery):
        events = BackupManifestStore(directory).read_events()
        assert [event["status"] for event in events] == ["in-progress", "failed"]
        assert events[-1]["failure"]
        assert events[-1]["automatic_retry"] is False
        assert events[-1]["automatic_reboot"] is False
        assert len(list(directory.rglob("*.rom"))) == 1


@pytest.mark.parametrize(
    ("tamper", "expected_message"),
    [
        ("missing-chip", "does not cover every expected chip"),
        ("malformed-chip", "chip record is malformed"),
        ("empty-capture-id", "chip record is malformed"),
        ("invalid-storage-capacity", "storage record is malformed"),
        ("unsupported-programmer", "unsupported programmer"),
        ("malformed-topology", "duplicate or malformed chip topology"),
    ],
)
def test_backup_reopen_rejects_resealed_semantically_invalid_manifest(
    tmp_path, tamper, expected_message
):
    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB)
    transport = FakeTransport([_probe_line("W25Q32.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)
    result = _capture(service, plan, probes, primary, recovery)
    read_calls_before_reopen = len(transport.read_calls)

    def mutate(event):
        if tamper == "missing-chip":
            event["chips"] = []
        elif tamper == "malformed-chip":
            event["chips"][0] = None
        elif tamper == "empty-capture-id":
            event["chips"][0]["first_capture_id"] = ""
        elif tamper == "invalid-storage-capacity":
            event["primary_storage"]["available_bytes"] = 0
        elif tamper == "unsupported-programmer":
            event["programmer"] = "unsupported-programmer"
        elif tamper == "malformed-topology":
            event["topology"] = [None]

    manifest_hashes = _rewrite_backup_manifests(result, mutate)
    with pytest.raises(BackupManifestError, match=expected_message):
        service.load_verified_result(
            plan,
            operation_id=result.operation_id,
            primary_manifest=result.primary_manifest,
            recovery_manifest=result.recovery_manifest,
            primary_manifest_sha256=manifest_hashes[0],
            recovery_manifest_sha256=manifest_hashes[1],
            expected_tool=transport.identity,
        )
    assert len(transport.read_calls) == read_calls_before_reopen == 2


@pytest.mark.parametrize(
    ("failure_kind", "failure_read", "expected_reads", "message"),
    [
        ("transport", 1, 1, "first independent read failed"),
        ("transport", 2, 2, "second independent read failed"),
        ("short-success", 1, 1, "first read for chip .* has .* bytes; expected exactly"),
    ],
)
def test_backup_read_failure_is_replicated_and_preserves_partial_evidence_without_retry(
    tmp_path, failure_kind, failure_read, expected_reads, message
):
    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB)
    transport = FakeTransport(
        [_probe_line("W25Q32.V", len(content))],
        {len(content): content},
        fail_read=failure_read if failure_kind == "transport" else None,
        short_read=failure_read if failure_kind == "short-success" else None,
    )
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)

    with pytest.raises(BackupCaptureError, match=message):
        _capture(service, plan, probes, primary, recovery)

    assert len(transport.read_calls) == expected_reads
    primary_events = BackupManifestStore(primary).read_events()
    recovery_events = BackupManifestStore(recovery).read_events()
    assert [event["status"] for event in primary_events] == ["in-progress", "failed"]
    assert [event["status"] for event in recovery_events] == ["in-progress", "failed"]
    failed_primary = primary_events[-1]
    failed_recovery = recovery_events[-1]
    assert failed_primary["event_sha256"] == failed_recovery["event_sha256"]
    assert failed_primary["automatic_retry"] is False
    assert failed_primary["automatic_reboot"] is False
    assert failed_primary["failure"]
    preserved = [Path(value) for value in failed_primary["preserved_paths"]]
    assert len(preserved) == expected_reads
    assert all(path.is_file() for path in preserved)
    assert all(path.suffix == ".partial" for path in preserved)
    assert not list(primary.rglob("*.rom"))
    assert not list(recovery.rglob("*.rom"))


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink creation depends on account privilege")
def test_backup_reopen_rejects_symlinked_full_chip_copy(tmp_path):
    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB)
    transport = FakeTransport([_probe_line("W25Q32.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)
    result = _capture(service, plan, probes, primary, recovery)

    captured_path = Path(result.chips[0].first_path)
    retained_target = captured_path.with_name(captured_path.name + ".retained")
    captured_path.replace(retained_target)
    captured_path.symlink_to(retained_target.name)

    with pytest.raises(BackupManifestError, match="primary copy .* missing or unsafe"):
        service.load_verified_result(
            plan,
            operation_id=result.operation_id,
            primary_manifest=result.primary_manifest,
            recovery_manifest=result.recovery_manifest,
            primary_manifest_sha256=result.primary_manifest_sha256,
            recovery_manifest_sha256=result.recovery_manifest_sha256,
            expected_tool=transport.identity,
        )


def test_backup_reopen_rejects_manifest_bound_to_different_observed_chip_topology(tmp_path):
    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB)
    transport = FakeTransport([_probe_line("W25Q32.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)
    result = _capture(service, plan, probes, primary, recovery)
    changed_chip = replace(plan.topology.chips[0], physical_label="different observed chip")
    changed_plan = replace(plan, topology=replace(plan.topology, chips=(changed_chip,)))

    with pytest.raises(BackupManifestError, match="chip evidence differs from the current observed topology"):
        service.load_verified_result(
            changed_plan,
            operation_id=result.operation_id,
            primary_manifest=result.primary_manifest,
            recovery_manifest=result.recovery_manifest,
            primary_manifest_sha256=result.primary_manifest_sha256,
            recovery_manifest_sha256=result.recovery_manifest_sha256,
            expected_tool=transport.identity,
        )


@pytest.mark.parametrize(("copy_label", "copy_attribute"), [("primary", "first_path"), ("recovery", "second_path")])
def test_backup_reopen_rehashes_both_full_chip_copies(tmp_path, copy_label, copy_attribute):
    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB)
    transport = FakeTransport([_probe_line("W25Q32.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)
    result = _capture(service, plan, probes, primary, recovery)

    changed_copy = Path(getattr(result.chips[0], copy_attribute))
    changed = bytearray(changed_copy.read_bytes())
    changed[0] ^= 1
    changed_copy.write_bytes(changed)

    with pytest.raises(BackupManifestError, match=f"{copy_label} copy for .* changed since capture"):
        service.load_verified_result(
            plan,
            operation_id=result.operation_id,
            primary_manifest=result.primary_manifest,
            recovery_manifest=result.recovery_manifest,
            primary_manifest_sha256=result.primary_manifest_sha256,
            recovery_manifest_sha256=result.recovery_manifest_sha256,
            expected_tool=transport.identity,
        )


def test_backup_reopen_rejects_rechecksummed_same_volume_claims(tmp_path):
    from autoloader.flasher.backup_service import _canonical, _sha256

    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB)
    transport = FakeTransport([_probe_line("W25Q32.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)
    result = _capture(service, plan, probes, primary, recovery)
    updated_manifest_hashes = []

    for manifest_path in (Path(result.primary_manifest), Path(result.recovery_manifest)):
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        verified = document["events"][-1]
        verified["recovery_storage"]["volume_id"] = verified["primary_storage"]["volume_id"]
        event_body = {key: value for key, value in verified.items() if key != "event_sha256"}
        verified["event_sha256"] = _sha256(_canonical(event_body))
        manifest_body = {key: value for key, value in document.items() if key != "manifest_sha256"}
        document["manifest_sha256"] = _sha256(_canonical(manifest_body))
        manifest_path.write_bytes(_canonical(document))
        updated_manifest_hashes.append(document["manifest_sha256"])

    with pytest.raises(StoragePreflightError, match="separately identified persistent volumes"):
        service.load_verified_result(
            plan,
            operation_id=result.operation_id,
            primary_manifest=result.primary_manifest,
            recovery_manifest=result.recovery_manifest,
            primary_manifest_sha256=updated_manifest_hashes[0],
            recovery_manifest_sha256=updated_manifest_hashes[1],
            expected_tool=transport.identity,
        )


def test_backup_reopen_rejects_replayed_verified_event(tmp_path):
    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB)
    transport = FakeTransport([_probe_line("W25Q32.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)
    result = _capture(service, plan, probes, primary, recovery)

    for directory in (primary, recovery):
        store = BackupManifestStore(directory)
        verified = store.read_events()[-1]
        replay = {
            key: value
            for key, value in verified.items()
            if key not in {"sequence", "previous_sha256", "event_sha256"}
        }
        store.append(replay)

    with pytest.raises(BackupManifestError, match="exactly one verified event"):
        service.load_verified_result(
            plan,
            operation_id=result.operation_id,
            primary_manifest=result.primary_manifest,
            recovery_manifest=result.recovery_manifest,
            primary_manifest_sha256=result.primary_manifest_sha256,
            recovery_manifest_sha256=result.recovery_manifest_sha256,
            expected_tool=transport.identity,
        )


def test_backup_reopen_rejects_validly_rechecksummed_primary_recovery_divergence(tmp_path):
    from autoloader.flasher.backup_service import _canonical, _sha256

    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB)
    transport = FakeTransport([_probe_line("W25Q32.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)
    result = _capture(service, plan, probes, primary, recovery)

    recovery_manifest = Path(result.recovery_manifest)
    document = json.loads(recovery_manifest.read_text(encoding="utf-8"))
    verified = document["events"][-1]
    verified["tool"]["version"] = "different-tool-version"
    event_body = {key: value for key, value in verified.items() if key != "event_sha256"}
    verified["event_sha256"] = _sha256(_canonical(event_body))
    manifest_body = {key: value for key, value in document.items() if key != "manifest_sha256"}
    document["manifest_sha256"] = _sha256(_canonical(manifest_body))
    recovery_manifest.write_bytes(_canonical(document))

    with pytest.raises(BackupManifestError, match="primary and recovery backup events disagree"):
        service.load_verified_result(
            plan,
            operation_id=result.operation_id,
            primary_manifest=result.primary_manifest,
            recovery_manifest=result.recovery_manifest,
            primary_manifest_sha256=result.primary_manifest_sha256,
            recovery_manifest_sha256=_sha256(_canonical(manifest_body)),
            expected_tool=transport.identity,
        )


@pytest.mark.parametrize(
    ("tamper", "expected_message"),
    [
        ("chip-id", "differs from current observed topology"),
        ("chip-capacity", "differs from current observed topology"),
        ("tool-version", "backup tool identity differs from the fresh local probe"),
        ("tool-sha256", "backup tool identity differs from the fresh local probe"),
        ("tool-package-owner", "backup tool identity differs from the fresh local probe"),
        ("device-identity", "device identity differs from the current plan"),
        ("region-map", "region map differs from the current topology"),
        ("chip-voltage-evidence", "chip evidence differs from the current observed topology"),
        ("programmer-evidence", "programmer setup provenance differs from current attestation"),
        ("internal-programmer", "cannot use the internal programmer"),
    ],
)
def test_verified_backup_reopen_rejects_rechecksummed_identity_and_setup_tampering(
    tmp_path, tamper, expected_message
):
    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB)
    transport = FakeTransport([_probe_line("W25Q32.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)
    result = _capture(service, plan, probes, primary, recovery)

    if tamper == "chip-capacity":
        smaller = _payload(2 * MIB, 19)
        for copy_path in (result.chips[0].first_path, result.chips[0].second_path):
            Path(copy_path).write_bytes(smaller)
        smaller_hash = hashlib.sha256(smaller).hexdigest()

    def mutate(event):
        if tamper == "chip-id":
            event["chips"][0]["chip_id"] = "unobserved-chip"
        elif tamper == "chip-capacity":
            chip = event["chips"][0]
            chip["capacity_bytes"] = 2 * MIB
            chip["first_size_bytes"] = chip["second_size_bytes"] = 2 * MIB
            chip["first_sha256"] = chip["second_sha256"] = smaller_hash
        elif tamper == "tool-version":
            event["tool"]["version"] = "different-pinned-version"
        elif tamper == "tool-sha256":
            event["tool"]["sha256"] = "f" * 64
        elif tamper == "tool-package-owner":
            event["tool"]["package_owner"] = "dpkg: other-package: /usr/bin/flashrom"
        elif tamper == "device-identity":
            event["device_identifiers"]["raw_dmi_product_name"] = "ThinkPad X220"
        elif tamper == "region-map":
            event["regions"] = [{"name": "unexpected", "start_byte": 0, "end_byte": 1}]
        elif tamper == "chip-voltage-evidence":
            event["topology"][0]["voltage_evidence_source"] = "unrecorded-source"
        elif tamper == "programmer-evidence":
            event["programmer_evidence"]["interface_verified"] = False
        elif tamper == "internal-programmer":
            event["programmer"] = ProgrammerType.INTERNAL.value

    manifest_hashes = _rewrite_backup_manifests(result, mutate)
    read_count = len(transport.read_calls)
    with pytest.raises(BackupManifestError, match=expected_message):
        service.load_verified_result(
            plan,
            operation_id=result.operation_id,
            primary_manifest=result.primary_manifest,
            recovery_manifest=result.recovery_manifest,
            primary_manifest_sha256=manifest_hashes[0],
            recovery_manifest_sha256=manifest_hashes[1],
            expected_tool=transport.identity,
        )
    assert len(transport.read_calls) == read_count == 2


def test_verified_backup_reopen_rejects_primary_recovery_hardlink_alias(tmp_path):
    plan = _make_x200(tmp_path)
    content = _payload(4 * MIB)
    transport = FakeTransport([_probe_line("W25Q32.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(tmp_path, transport)
    result = _capture(service, plan, probes, primary, recovery)
    first_path = Path(result.chips[0].first_path)
    second_path = Path(result.chips[0].second_path)
    alias_path = second_path.with_name("recovery-hardlink.rom")
    try:
        os.link(first_path, alias_path)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"host does not support creating a local hard link: {exc}")

    def alias_recovery_copy(event):
        event["chips"][0]["second_path"] = str(alias_path)

    manifest_hashes = _rewrite_backup_manifests(result, alias_recovery_copy)
    with pytest.raises(BackupManifestError, match="copies must be distinct files"):
        service.load_verified_result(
            plan,
            operation_id=result.operation_id,
            primary_manifest=result.primary_manifest,
            recovery_manifest=result.recovery_manifest,
            primary_manifest_sha256=manifest_hashes[0],
            recovery_manifest_sha256=manifest_hashes[1],
            expected_tool=transport.identity,
        )
    assert len(transport.read_calls) == 2


def test_readback_comparison_is_region_aware_and_preserves_mismatch(tmp_path):
    chip = bytearray(_payload(8192))
    intended_region = bytes((index * 13 + 5) % 251 for index in range(2048))
    chip[4096:6144] = intended_region
    expected = tmp_path / "bios-region.bin"
    readback = tmp_path / "chip-readback.bin"
    expected.write_bytes(intended_region)
    readback.write_bytes(chip)
    comparator = ReadbackComparator()
    spec = RegionReadbackSpec("bios", 4096, 6144, 0, 2048)

    verified = comparator.compare(expected, readback, chip_capacity_bytes=8192, regions=(spec,))
    assert verified.status is VerificationStatus.VERIFIED
    assert verified.automatic_retry is False and verified.automatic_reboot is False

    chip[4096] ^= 0xFF
    readback.write_bytes(chip)
    failed = comparator.compare(expected, readback, chip_capacity_bytes=8192, regions=(spec,))
    assert failed.status is VerificationStatus.FAILED
    assert "bios" in failed.details[0]
    assert expected.exists() and readback.exists()


def test_readback_rejects_invalid_or_incomplete_region_map(tmp_path):
    expected = tmp_path / "expected.bin"
    readback = tmp_path / "readback.bin"
    expected.write_bytes(b"ABCD")
    readback.write_bytes(b"x" * 8192)
    result = ReadbackComparator().compare(
        expected,
        readback,
        chip_capacity_bytes=8192,
        regions=(RegionReadbackSpec("bad", 0, 8, 0, 4),),
    )
    assert result.status is VerificationStatus.UNKNOWN
    assert "different byte lengths" in result.details[0]


def test_flashrom_runner_allows_only_exact_read_only_command_forms(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import autoloader.flasher.flashrom as flashrom_module

    fake_executable = tmp_path / "flashrom"
    fake_executable.write_bytes(b"synthetic executable")
    resolved = str(fake_executable.resolve())
    info = fake_executable.stat()
    provenance = flashrom_module._TrustedExecutable(
        file=flashrom_module._FileFingerprint(
            path=resolved,
            sha256=hashlib.sha256(fake_executable.read_bytes()).hexdigest(),
            device=info.st_dev,
            inode=info.st_ino,
            size=info.st_size,
            mtime_ns=info.st_mtime_ns,
            ctime_ns=info.st_ctime_ns,
            mode=info.st_mode,
            uid=info.st_uid,
            gid=info.st_gid,
        ),
        package_owner="pacman: test/flashrom 1.4.0-1",
    )
    monkeypatch.setattr(flashrom_module.shutil, "which", lambda name: resolved if name == "flashrom" else None)
    monkeypatch.setattr(flashrom_module, "_trusted_executable_provenance", lambda _path: provenance)
    runner = FlashromRunner()
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="read-only fake", stderr="")

    monkeypatch.setattr("autoloader.flasher.flashrom.subprocess.run", fake_run)
    for args in (
        ["--version"],
        ["-p", "internal"],
        ["-p", ProgrammerType.CH341A.value, "-r", "dump.rom"],
        ["-p", ProgrammerType.CH341A.value, "-c", "W25Q64.V", "--ifd", "-i", "bios", "-v", "image.rom"],
    ):
        success, _output, _duration = runner.run_command(args)
        assert success is True
    assert len(calls) == 4


def test_flashrom_runner_rejects_mutating_aliases_and_injected_options_without_subprocess(monkeypatch, tmp_path):
    runner = FlashromRunner()
    monkeypatch.setattr("autoloader.flasher.flashrom.subprocess.run", lambda *args, **kwargs: pytest.fail("must not execute"))
    dangerous = (
        ["-w", "image.rom"],
        ["--write", "image.rom"],
        ["--write=image.rom"],
        ["-e"],
        ["-E"],
        ["--erase"],
        ["--wp-enable"],
        ["--WP-ENABLE"],
        ["--wp-enable=0x0-0xff"],
        ["--wp-disable"],
        ["--wp-set", "0x0"],
        ["--wp-clear"],
        ["--chipset-enable"],
        ["-P", ProgrammerType.CH341A.value],
        ["-p", ProgrammerType.CH341A.value, "--wp-enable"],
        ["-p", ProgrammerType.CH341A.value, "-r", "dump.rom", "--wp-enable"],
        ["-p", ProgrammerType.CH341A.value, "-v", "image.rom", "--wp-enable"],
        ["-p", ProgrammerType.CH341A.value, "--unknown-option"],
    )
    for args in dangerous:
        success, output, _duration = runner.run_command(list(args))
        assert success is False
        assert "rejected" in output.lower()

    image = tmp_path / "candidate.rom"
    image.write_bytes(b"test image")
    probe = runner.probe_chip(ProgrammerType.CH341A, chip_name="--wp-enable")
    read = runner.read_rom(
        ProgrammerType.CH341A,
        str(tmp_path / "dump.rom"),
        extra_args=["--wp-enable"],
    )
    verify = runner.verify_rom(
        ProgrammerType.CH341A,
        str(image),
        extra_args=["--wp-enable"],
    )
    assert probe.code == "PROBE_OVERRIDE_REJECTED"
    assert read.code == "UNSUPPORTED_ARGUMENTS"
    assert verify.code == "UNSUPPORTED_ARGUMENTS"


@pytest.mark.parametrize("filesystem_type", [None, "tmpfs"], ids=["unknown", "transient"])
def test_filesystem_storage_preflight_rejects_unknown_or_transient_storage(
    tmp_path, monkeypatch, filesystem_type
):
    # Exercise the POSIX durability policy on every host, with a destination
    # outside the configured system-temp path so the mount classification is
    # the reason this storage is rejected.
    real_os = backup_service.os

    class PosixOSProxy:
        name = "posix"

        def __getattr__(self, name):
            return getattr(real_os, name)

    monkeypatch.setattr(backup_service, "os", PosixOSProxy())
    monkeypatch.setattr(
        backup_service.tempfile,
        "gettempdir",
        lambda: str(tmp_path.parent / "unrelated-system-temp"),
    )
    monkeypatch.setattr(backup_service, "_mount_type", lambda _path: filesystem_type)

    def scratch_readback_must_not_run(_directory):
        pytest.fail("unknown/transient storage must be rejected before scratch writes")

    monkeypatch.setattr(
        FilesystemStorageInspector,
        "_scratch_readback",
        staticmethod(scratch_readback_must_not_run),
    )
    evidence = FilesystemStorageInspector().inspect(
        tmp_path,
        1024,
        persistence_confirmed=True,
    )
    assert evidence.filesystem_type == filesystem_type
    assert evidence.persistence_confirmed is False
    assert evidence.scratch_readback_verified is False
    assert evidence.usable is False


def test_filesystem_storage_preflight_rejects_posix_system_temp_even_on_persistent_fs(tmp_path, monkeypatch):
    system_temp = tmp_path / "system-temp"
    destination = system_temp / "recovery"
    destination.mkdir(parents=True)
    monkeypatch.setattr(backup_service.tempfile, "gettempdir", lambda: str(system_temp))
    real_os = backup_service.os

    class PosixOSProxy:
        name = "posix"

        def __getattr__(self, name):
            return getattr(real_os, name)

    monkeypatch.setattr(backup_service, "os", PosixOSProxy())
    monkeypatch.setattr(backup_service, "_mount_type", lambda _path: "ext4")

    def scratch_readback_must_not_run(_directory):
        pytest.fail("system-temp recovery path must be rejected before scratch writes")

    monkeypatch.setattr(FilesystemStorageInspector, "_scratch_readback", staticmethod(scratch_readback_must_not_run))
    evidence = FilesystemStorageInspector().inspect(
        destination,
        1024,
        persistence_confirmed=True,
    )
    assert evidence.filesystem_type == "ext4"
    assert evidence.persistence_confirmed is False
    assert evidence.scratch_readback_verified is False
    assert evidence.usable is False
