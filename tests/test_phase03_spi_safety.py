"""Phase 03 software-only SPI, backup, readback, and recovery tests."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

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
    ProgrammerEvidence,
    ProgrammerKind,
    DeviceEvidence,
    FirmwareFamily,
    OperationPlan,
    RegionEvidence,
    VerificationStatus,
)
from tests.test_workflow_safety import MIB, make_t430_plan, make_x200_plan


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _payload(size: int, salt: int = 3) -> bytes:
    return bytes((index * 37 + salt) % 251 for index in range(size))


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
    def __init__(self, probes, read_payloads, *, mismatch_second=False, fail_read=None):
        self.identity = FlashToolIdentity(
            name="flashrom",
            executable=str(Path(__file__).resolve()),
            version="1.4.0",
            raw_version="flashrom v1.4.0 fake build",
            observed_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        self.probes = list(probes)
        self.read_payloads = dict(read_payloads)
        self.mismatch_second = mismatch_second
        self.fail_read = fail_read
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
    assert readiness.recovery_write_enabled is False
    assert readiness.hardware_recovery_tested is False


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
    intent = BuildIntentEvidence(
        profile_id=plan.profile_id,
        requested_model_id=plan.requested_model_id,
        firmware_family=plan.firmware_family,
        device=plan.device,
        topology=plan.topology,
        programmer=plan.programmer,
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


def test_flashrom_runner_allows_only_exact_read_only_command_forms(monkeypatch):
    from types import SimpleNamespace

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


def test_filesystem_storage_preflight_rejects_unknown_or_transient_storage(tmp_path):
    evidence = FilesystemStorageInspector().inspect(
        tmp_path,
        1024,
        persistence_confirmed=True,
    )
    # The test temp area is not accepted as a durable recovery location.
    assert evidence.persistence_confirmed is False
    assert evidence.usable is False
