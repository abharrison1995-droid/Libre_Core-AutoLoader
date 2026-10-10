"""Software-only Phase 02 safety policy, evidence and state-machine tests."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from autoloader.detection.bios import BIOSInfo
from autoloader.detection.model import DetectionResult
from autoloader.workflow import (
    ArtifactEvidence,
    AuthorizationError,
    AuthorizationLedger,
    AuthorizationReusedError,
    AuthorizationStaleError,
    BackupEvidence,
    BackupPairEvidence,
    ChipEvidence,
    ChipTopology,
    CompatibilityDecision,
    CompatibilityValidator,
    DeviceEvidence,
    FirmwareFamily,
    JournalCorruptionError,
    JournalPlanMismatchError,
    OperationJournal,
    OperationPlan,
    OperationState,
    ProgrammerEvidence,
    ProgrammerKind,
    RegionEvidence,
    WorkflowSession,
    WorkflowTransitionError,
    UnsafeCheckpointError,
)

MIB = 1024 * 1024
PINNED_REVISION = "a3427b34102f8f9887acf65e23d7fd5900fe1d40"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def isolate_default_journals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "app-state"))


@pytest.fixture(scope="module")
def x200_files(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("phase02-x200")
    content = bytes((index * 17) % 251 for index in range(4 * MIB))
    paths = {name: root / name for name in ("libreboot.rom", "backup-a.bin", "backup-b.bin")}
    for path in paths.values():
        path.write_bytes(content)
    return paths


def make_x200_plan(files: dict[str, Path]) -> OperationPlan:
    artifact = ArtifactEvidence(
        path=str(files["libreboot.rom"]),
        model_id="x200",
        firmware_family=FirmwareFamily.LIBREBOOT,
        expected_sha256=_hash(files["libreboot.rom"]),
        expected_size_bytes=files["libreboot.rom"].stat().st_size,
        source_url="https://libreboot.org/docs/install/x200.html",
        source_revision=PINNED_REVISION,
    )
    backup = BackupEvidence((BackupPairEvidence(
        chip_id="flash0",
        first_path=str(files["backup-a.bin"]),
        second_path=str(files["backup-b.bin"]),
        first_sha256=_hash(files["backup-a.bin"]),
        second_sha256=_hash(files["backup-b.bin"]),
        first_capture_id="flashrom-read-001",
        second_capture_id="flashrom-read-002",
    ),))
    return OperationPlan(
        profile_id="x200-libreboot",
        requested_model_id="x200",
        firmware_family=FirmwareFamily.LIBREBOOT,
        device=DeviceEvidence(
            raw_dmi_product_name="ThinkPad X200",
            raw_dmi_product_version="7454CTO",
            raw_dmi_system_family="ThinkPad X200",
            raw_mtm="7454CTO",
        ),
        topology=ChipTopology(
            observed=True,
            chips=(ChipEvidence("flash0", 4 * MIB, True, physical_label="main", voltage_mv=3300),),
        ),
        programmer=ProgrammerEvidence(
            kind=ProgrammerKind.EXTERNAL_SPI,
            observed=True,
            interface_verified=True,
            wiring_verified=True,
            voltage_mv=3300,
        ),
        artifact=artifact,
        backup=backup,
    )


def make_t430_plan(
    artifact_path: Path,
    bios_version: str,
    *,
    manual_model_candidate: str | None = None,
    product_name: str = "ThinkPad T430",
    family: str = "ThinkPad T430",
    mtm: str = "2342CTO",
) -> OperationPlan:
    return OperationPlan(
        profile_id="t430-1vyrain",
        requested_model_id="t430",
        firmware_family=FirmwareFamily.ONEVYRAIN,
        device=DeviceEvidence(
            raw_dmi_product_name=product_name,
            raw_dmi_product_version=mtm,
            raw_dmi_system_family=family,
            raw_mtm=mtm,
            raw_bios_version=bios_version,
            raw_bios_vendor="LENOVO",
            stock_lenovo_bios=True,
            manual_model_candidate=manual_model_candidate,
            internal_flash_unlock_status=True,
            internal_flash_unlock_observed=True,
        ),
        topology=ChipTopology(
            observed=True,
            chips=(
                ChipEvidence("bottom", 8 * MIB, True, physical_label="bottom"),
                ChipEvidence("top", 4 * MIB, True, physical_label="top"),
            ),
        ),
        programmer=ProgrammerEvidence(
            kind=ProgrammerKind.INTERNAL_ONEVYRAIN,
            observed=True,
        ),
        artifact=ArtifactEvidence(
            path=str(artifact_path),
            model_id="t430",
            firmware_family=FirmwareFamily.ONEVYRAIN,
            expected_sha256=_hash(artifact_path),
            expected_size_bytes=artifact_path.stat().st_size,
            source_url="https://github.com/n4ru/1vyrain",
            source_revision=PINNED_REVISION,
        ),
    )


def _assert_decision(plan: OperationPlan, decision: CompatibilityDecision) -> tuple[str, ...]:
    result = CompatibilityValidator().validate(plan)
    assert result.decision is decision, result.reasons
    return result.reasons


def test_t430_bios_boundary_matches_pinned_upstream_and_unknown_fails_closed(tmp_path: Path) -> None:
    artifact = tmp_path / "t430.rom"
    artifact.write_bytes(b"R" * (4 * MIB))
    allowed = make_t430_plan(artifact, "G1ET74WW (2.64 )")
    whitespace = make_t430_plan(artifact, "G1ET74WW (2.64  )")
    unspaced = make_t430_plan(artifact, "G1ET74WW (2.64)")
    blocked = make_t430_plan(artifact, "G1ET75WW (2.65 )")
    unknown = make_t430_plan(artifact, "G1ET74WW")
    bare = make_t430_plan(artifact, "2.64")

    assert CompatibilityValidator().validate(allowed).decision is CompatibilityDecision.ALLOWED
    assert CompatibilityValidator().validate(whitespace).decision is CompatibilityDecision.ALLOWED
    assert _assert_decision(blocked, CompatibilityDecision.BLOCKED)
    assert any("threshold 265" in reason for reason in _assert_decision(blocked, CompatibilityDecision.BLOCKED))
    assert any("ambiguous" in reason for reason in _assert_decision(unknown, CompatibilityDecision.UNKNOWN))
    assert any("ambiguous" in reason for reason in _assert_decision(unspaced, CompatibilityDecision.UNKNOWN))
    assert any("ambiguous" in reason for reason in _assert_decision(bare, CompatibilityDecision.UNKNOWN))


def test_identity_conflict_alias_and_manual_candidate_do_not_authorize(tmp_path: Path) -> None:
    artifact = tmp_path / "t430.rom"
    artifact.write_bytes(b"R" * (4 * MIB))
    base = make_t430_plan(artifact, "G1ET74WW (2.64 )")
    conflicting = replace(base, device=replace(base.device, raw_dmi_system_family="ThinkPad X220"))
    conflicting_version = replace(
        base,
        device=replace(base.device, raw_dmi_product_version="ThinkPad X220"),
    )
    alias = replace(base, device=replace(base.device, raw_dmi_product_name="ThinkPad T430s"))
    manual_only = replace(base, device=replace(
        base.device,
        raw_dmi_product_name=None,
        raw_dmi_system_family=None,
        raw_mtm=None,
        manual_model_candidate="t430",
    ))
    manual_conflict = replace(base, device=replace(base.device, manual_model_candidate="x200"))

    assert _assert_decision(conflicting, CompatibilityDecision.BLOCKED)
    assert _assert_decision(conflicting_version, CompatibilityDecision.BLOCKED)
    assert _assert_decision(alias, CompatibilityDecision.BLOCKED)
    assert _assert_decision(manual_only, CompatibilityDecision.UNKNOWN)
    assert _assert_decision(manual_conflict, CompatibilityDecision.BLOCKED)


def test_t430_family_must_match_upstream_field_parsing(tmp_path: Path) -> None:
    artifact = tmp_path / "t430.rom"
    artifact.write_bytes(b"R" * (4 * MIB))
    valid = make_t430_plan(artifact, "G1ET74WW (2.64 )", family="Family: ThinkPad T430")
    unsupported_shape = make_t430_plan(
        artifact, "G1ET74WW (2.64 )", family="Lenovo ThinkPad T430"
    )

    assert CompatibilityValidator().validate(valid).decision is CompatibilityDecision.ALLOWED
    result = CompatibilityValidator().validate(unsupported_shape)
    assert result.decision is CompatibilityDecision.UNKNOWN
    assert any("pinned upstream" in reason for reason in result.reasons)


def test_t430_mtm_prefixes_are_cross_checked_across_raw_dmi_fields(tmp_path: Path) -> None:
    artifact = tmp_path / "t430.rom"
    artifact.write_bytes(b"R" * (4 * MIB))
    base = make_t430_plan(artifact, "G1ET74WW (2.64 )")
    product_version_conflict = replace(
        base, device=replace(base.device, raw_dmi_product_version="2344CTO")
    )
    product_name_conflict = replace(
        base, device=replace(base.device, raw_dmi_product_name="ThinkPad T430 2344CTO")
    )
    unsupported_product_version = replace(
        base, device=replace(base.device, raw_dmi_product_version="9999CTO")
    )
    unsupported_product_name = replace(
        base, device=replace(base.device, raw_dmi_product_name="ThinkPad T430 9999CTO")
    )

    for candidate in (
        product_version_conflict,
        product_name_conflict,
        unsupported_product_version,
        unsupported_product_name,
    ):
        assert _assert_decision(candidate, CompatibilityDecision.BLOCKED)


def test_t430_bios_vendor_must_agree_with_stock_classification(tmp_path: Path) -> None:
    artifact = tmp_path / "t430.rom"
    artifact.write_bytes(b"R" * (4 * MIB))
    base = make_t430_plan(artifact, "G1ET74WW (2.64 )")
    vendor_mismatch = replace(
        base, device=replace(base.device, raw_bios_vendor="American Megatrends")
    )
    missing_vendor = replace(base, device=replace(base.device, raw_bios_vendor=None))
    opposite_flag = replace(
        base,
        device=replace(base.device, raw_bios_vendor="LENOVO", stock_lenovo_bios=False),
    )

    assert _assert_decision(vendor_mismatch, CompatibilityDecision.BLOCKED)
    assert _assert_decision(opposite_flag, CompatibilityDecision.BLOCKED)
    assert _assert_decision(missing_vendor, CompatibilityDecision.UNKNOWN)


def test_model_family_and_firmware_mismatches_block(x200_files: dict[str, Path]) -> None:
    plan = make_x200_plan(x200_files)
    bad_model = replace(plan, requested_model_id="x220")
    bad_family = replace(plan, firmware_family=FirmwareFamily.COREBOOT)
    bad_artifact = replace(plan, artifact=replace(plan.artifact, firmware_family=FirmwareFamily.COREBOOT))
    unknown_chip_voltage = replace(
        plan,
        topology=replace(plan.topology, chips=(replace(plan.topology.chips[0], voltage_mv=None),)),
    )
    wrong_chip_voltage = replace(
        plan,
        topology=replace(plan.topology, chips=(replace(plan.topology.chips[0], voltage_mv=1800),)),
    )

    assert _assert_decision(bad_model, CompatibilityDecision.BLOCKED)
    assert _assert_decision(bad_family, CompatibilityDecision.BLOCKED)
    assert _assert_decision(bad_artifact, CompatibilityDecision.BLOCKED)
    assert _assert_decision(unknown_chip_voltage, CompatibilityDecision.UNKNOWN)
    assert _assert_decision(wrong_chip_voltage, CompatibilityDecision.BLOCKED)


def test_raw_dmi_and_bios_are_preserved_without_normalized_fallback() -> None:
    detection = DetectionResult(
        "t430", "ThinkPad T430", "2342", None, "high",
        raw_product_name="ThinkPad T430",
        raw_product_version="2342CTO",
        raw_system_family="ThinkPad T430",
        raw_mtm="2342CTO",
    )
    bios = BIOSInfo("2.64", None, "LENOVO", True, raw_version=None)
    evidence = DeviceEvidence.from_detection(detection, bios, manual_model_candidate="t430")

    assert evidence.raw_dmi_product_name == "ThinkPad T430"
    assert evidence.raw_dmi_product_version == "2342CTO"
    assert evidence.raw_dmi_system_family == "ThinkPad T430"
    assert evidence.raw_mtm == "2342CTO"
    assert evidence.raw_bios_version is None
    assert evidence.manual_model_candidate == "t430"


def test_x200_variable_capacity_requires_exact_observed_artifact_size(tmp_path: Path) -> None:
    artifact = tmp_path / "x200-8m.rom"
    content = b"L" * (8 * MIB)
    artifact.write_bytes(content)
    plan = make_x200_plan({
        "libreboot.rom": artifact,
        "backup-a.bin": artifact,
        "backup-b.bin": artifact,
    })
    plan = replace(
        plan,
        topology=ChipTopology(True, (ChipEvidence("flash0", 8 * MIB, True, voltage_mv=3300),)),
    )
    assert CompatibilityValidator().validate(plan).decision is CompatibilityDecision.ALLOWED

    wrong_size = replace(plan, artifact=replace(plan.artifact, expected_size_bytes=4 * MIB))
    assert _assert_decision(wrong_size, CompatibilityDecision.BLOCKED)


def test_x200_profile_rejects_tablet_and_x200s_variants_with_regular_mtm(
    x200_files: dict[str, Path],
) -> None:
    plan = make_x200_plan(x200_files)
    for variant in ("ThinkPad X200 Tablet", "ThinkPad X200s", "ThinkPad X200si"):
        candidate = replace(
            plan,
            device=replace(plan.device, raw_dmi_product_name=variant),
        )
        result = CompatibilityValidator().validate(candidate)
        assert result.decision is CompatibilityDecision.BLOCKED, (variant, result.reasons)
        assert any("unsupported model alias" in reason for reason in result.reasons)


def test_x220_unknown_topology_and_regions_never_pass(tmp_path: Path) -> None:
    rom = tmp_path / "x220.rom"
    rom.write_bytes(b"C" * (8 * MIB))
    sha = _hash(rom)
    profile_regions = (
        RegionEvidence("ifd", 0, 64 * 1024),
        RegionEvidence("gbe", 64 * 1024, 128 * 1024),
        RegionEvidence("me", 128 * 1024, 5 * MIB),
        RegionEvidence("bios", 5 * MIB, 8 * MIB),
    )
    plan = OperationPlan(
        "x220-coreboot", "x220", FirmwareFamily.COREBOOT,
        DeviceEvidence("ThinkPad X220", "4291CTO", "ThinkPad X220", "4291CTO"),
        ChipTopology(True, (ChipEvidence("main", 8 * MIB, True, "main", voltage_mv=3300),)),
        ProgrammerEvidence(ProgrammerKind.EXTERNAL_SPI, True, True, True, 3300),
        ArtifactEvidence(str(rom), "x220", FirmwareFamily.COREBOOT, sha, 8 * MIB,
                         "https://doc.coreboot.org/mainboard/lenovo/Sandy_Bridge_series.html",
                         PINNED_REVISION),
    )
    assert _assert_decision(plan, CompatibilityDecision.UNKNOWN)

    with_regions = replace(
        plan,
        topology=replace(plan.topology, regions=profile_regions),
        artifact=replace(plan.artifact, regions=profile_regions),
    )
    assert CompatibilityValidator().validate(with_regions).decision is CompatibilityDecision.ALLOWED
    bad_regions = tuple(region for region in profile_regions if region.name != "me")
    incomplete = replace(
        with_regions,
        topology=replace(with_regions.topology, regions=bad_regions),
    )
    assert _assert_decision(incomplete, CompatibilityDecision.UNKNOWN)
    overlapping = replace(
        with_regions,
        topology=replace(
            with_regions.topology,
            regions=profile_regions[:2] + (RegionEvidence("me", 100 * 1024, 5 * MIB),) + profile_regions[3:],
        ),
    )
    assert _assert_decision(overlapping, CompatibilityDecision.BLOCKED)


def test_corrupted_artifact_hash_is_blocked(x200_files: dict[str, Path]) -> None:
    plan = make_x200_plan(x200_files)
    corrupted = replace(plan, artifact=replace(plan.artifact, expected_sha256="0" * 64))
    moving_revision = replace(plan, artifact=replace(plan.artifact, source_revision="master"))
    assert any(
        "SHA-256" in reason
        for reason in _assert_decision(corrupted, CompatibilityDecision.BLOCKED)
    )
    assert any(
        "40-hex" in reason
        for reason in _assert_decision(moving_revision, CompatibilityDecision.BLOCKED)
    )


def test_backup_requires_complete_stable_matching_reads(x200_files: dict[str, Path]) -> None:
    validator = CompatibilityValidator()
    plan = make_x200_plan(x200_files)
    assert validator.validate_backup(plan).decision is CompatibilityDecision.ALLOWED

    incomplete = replace(plan, backup=BackupEvidence())
    assert validator.validate_backup(incomplete).decision is CompatibilityDecision.BLOCKED
    transient_pair = replace(plan.backup.pairs[0], transient=True)
    transient = replace(plan, backup=BackupEvidence((transient_pair,)))
    assert validator.validate_backup(transient).decision is CompatibilityDecision.BLOCKED
    missing_capture_ids = replace(
        plan,
        backup=BackupEvidence((replace(
            plan.backup.pairs[0], first_capture_id="", second_capture_id=""
        ),)),
    )
    assert validator.validate_backup(missing_capture_ids).decision is CompatibilityDecision.BLOCKED

    original = x200_files["backup-a.bin"].read_bytes()
    try:
        x200_files["backup-a.bin"].write_bytes(b"\x00" * (4 * MIB))
        blank_pair = replace(
            plan.backup.pairs[0],
            first_sha256=_hash(x200_files["backup-a.bin"]),
        )
        blank_plan = replace(plan, backup=BackupEvidence((blank_pair,)))
        assert validator.validate_backup(blank_plan).decision is CompatibilityDecision.BLOCKED
    finally:
        x200_files["backup-a.bin"].write_bytes(original)


def test_state_machine_blocks_write_and_never_invokes_injected_writer(x200_files: dict[str, Path]) -> None:
    complete_plan = make_x200_plan(x200_files)
    session = WorkflowSession(replace(complete_plan, backup=BackupEvidence()))
    assert session.validate().decision is CompatibilityDecision.ALLOWED
    assert session.state is OperationState.VALIDATED
    assert session.verify_backup(complete_plan.backup).decision is CompatibilityDecision.ALLOWED
    token = session.authorize(actor="test operator", explicit_approval=True)
    calls: list[OperationPlan] = []
    result = session.request_write(token, writer=lambda plan: calls.append(plan))

    assert result.code == "WRITES_DISABLED_PHASE_02"
    assert result.state is OperationState.WRITE_BLOCKED
    assert calls == []
    assert [entry.sequence for entry in session.journal.entries] == [1, 2, 3, 4]


def test_simulation_programmer_is_blocked_before_writer_can_be_reached(x200_files: dict[str, Path]) -> None:
    plan = make_x200_plan(x200_files)
    simulated = replace(plan, programmer=replace(plan.programmer, kind=ProgrammerKind.SIMULATION))
    session = WorkflowSession(simulated)
    calls: list[OperationPlan] = []
    result = session.validate()
    assert result.decision is CompatibilityDecision.BLOCKED
    with pytest.raises(WorkflowTransitionError):
        session.request_write(None, writer=lambda current: calls.append(current))  # type: ignore[arg-type]
    assert calls == []


def test_authorization_rejects_changed_evidence_and_reuse(x200_files: dict[str, Path]) -> None:
    plan = make_x200_plan(x200_files)
    validator = CompatibilityValidator()
    compatibility = validator.validate(plan)
    backup = validator.validate_backup(plan)
    ledger = AuthorizationLedger(validator)
    token = ledger.issue(
        plan,
        actor="test operator",
        explicit_approval=True,
        compatibility=compatibility,
        backup_verification=backup,
    )
    ledger.consume(token, plan)
    with pytest.raises(AuthorizationReusedError):
        ledger.consume(token, plan)

    changed_plan_ledger = AuthorizationLedger(validator)
    changed_plan_token = changed_plan_ledger.issue(
        plan,
        actor="test operator",
        explicit_approval=True,
        compatibility=compatibility,
        backup_verification=backup,
    )
    changed_device = replace(
        plan,
        device=replace(plan.device, raw_dmi_system_family="ThinkPad X220"),
    )
    with pytest.raises(AuthorizationStaleError):
        changed_plan_ledger.consume(changed_plan_token, changed_device)

    session = WorkflowSession(make_x200_plan(x200_files))
    session.validate()
    session.verify_backup()
    stale_token = session.authorize(actor="test operator", explicit_approval=True)
    original = x200_files["libreboot.rom"].read_bytes()
    x200_files["libreboot.rom"].write_bytes(bytes([original[0] ^ 1]) + original[1:])
    try:
        calls: list[OperationPlan] = []
        result = session.request_write(stale_token, writer=lambda current: calls.append(current))
        assert result.code == "APPROVAL_REJECTED"
        assert session.state is OperationState.BLOCKED
        assert calls == []
    finally:
        x200_files["libreboot.rom"].write_bytes(original)


def test_illegal_stale_transitions_and_no_human_approval_fail_closed(x200_files: dict[str, Path]) -> None:
    session = WorkflowSession(make_x200_plan(x200_files))
    with pytest.raises(WorkflowTransitionError):
        session.verify_backup()
    session.validate()
    with pytest.raises(WorkflowTransitionError):
        session.authorize(actor="test operator", explicit_approval=True)
    session.verify_backup()
    with pytest.raises(AuthorizationError, match="explicit human approval"):
        session.authorize(actor="test operator", explicit_approval=False)
    assert session.state is OperationState.BACKUP_VERIFIED


def test_durable_journal_resumes_only_pre_authorization_and_never_writes(
    x200_files: dict[str, Path], tmp_path: Path
) -> None:
    complete_plan = make_x200_plan(x200_files)
    start_plan = replace(complete_plan, backup=BackupEvidence())
    default_session = WorkflowSession(start_plan)
    assert default_session.journal.path.is_file()

    checkpoint = tmp_path / "operation.json"
    session = WorkflowSession(start_plan, journal_path=checkpoint)
    operation_id = session.journal.operation_id
    initial = json.loads(checkpoint.read_text(encoding="utf-8"))

    assert initial["operation_id"] == operation_id
    assert initial["device_fingerprint"]
    assert initial["artifact_metadata"]["actual_sha256"] == _hash(x200_files["libreboot.rom"])
    assert session.validate().decision is CompatibilityDecision.ALLOWED

    resumed = WorkflowSession.resume(start_plan, checkpoint)
    assert resumed.state is OperationState.VALIDATED
    assert resumed.compatibility is not None
    assert resumed.verify_backup(complete_plan.backup).decision is CompatibilityDecision.ALLOWED
    backup_checkpoint = WorkflowSession.resume(complete_plan, checkpoint)
    assert backup_checkpoint.state is OperationState.BACKUP_VERIFIED
    assert backup_checkpoint.backup_verification is not None

    token = backup_checkpoint.authorize(actor="test operator", explicit_approval=True)
    with pytest.raises(UnsafeCheckpointError, match="not a supported resume point"):
        OperationJournal.load_checkpoint(checkpoint, expected_plan=complete_plan)
    calls: list[OperationPlan] = []
    result = backup_checkpoint.request_write(token, writer=lambda plan: calls.append(plan))
    assert result.code == "WRITES_DISABLED_PHASE_02"
    assert calls == []

    terminal = OperationJournal.inspect_checkpoint(checkpoint, expected_plan=complete_plan)
    assert terminal.state is OperationState.WRITE_BLOCKED
    with pytest.raises(UnsafeCheckpointError, match="read-only"):
        terminal.append(
            action="attempt-resume",
            from_state=OperationState.WRITE_BLOCKED,
            to_state=OperationState.BLOCKED,
            code="NOOP",
            message="must remain read-only",
        )
    with pytest.raises(UnsafeCheckpointError, match="not a supported resume point"):
        WorkflowSession.resume(complete_plan, checkpoint)


def test_backup_evidence_and_transition_are_atomic_at_failure_boundary(
    x200_files: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    complete_plan = make_x200_plan(x200_files)
    start_plan = replace(complete_plan, backup=BackupEvidence())
    checkpoint = tmp_path / "atomic-backup.json"
    session = WorkflowSession(start_plan, journal_path=checkpoint)
    assert session.validate().decision is CompatibilityDecision.ALLOWED
    before = checkpoint.read_bytes()

    def fail_backup_validation(_: OperationPlan):
        raise RuntimeError("simulated interruption before transition append")

    monkeypatch.setattr(session.validator, "validate_backup", fail_backup_validation)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        session.verify_backup(complete_plan.backup)

    assert checkpoint.read_bytes() == before
    recovered = OperationJournal.load_checkpoint(checkpoint, expected_plan=start_plan)
    assert recovered.state is OperationState.VALIDATED
    assert recovered.backup_checksums == ()
    assert WorkflowSession.resume(start_plan, checkpoint).state is OperationState.VALIDATED


def test_journal_rejects_corruption_changed_plan_and_resumes_no_terminal_state(
    x200_files: dict[str, Path], tmp_path: Path
) -> None:
    plan = make_x200_plan(x200_files)
    checkpoint = tmp_path / "corrupt.json"
    session = WorkflowSession(plan, journal_path=checkpoint)
    assert session.validate().decision is CompatibilityDecision.ALLOWED
    document = json.loads(checkpoint.read_text(encoding="utf-8"))

    changed_plan = replace(plan, device=replace(plan.device, raw_dmi_system_family="ThinkPad X220"))
    with pytest.raises(JournalPlanMismatchError):
        WorkflowSession.resume(changed_plan, checkpoint)

    document["entries"][0]["message"] = "tampered"
    checkpoint.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(JournalCorruptionError, match="checksum"):
        OperationJournal.load_checkpoint(checkpoint, expected_plan=plan)


def test_journal_append_does_not_overwrite_corruption_after_resume(
    x200_files: dict[str, Path], tmp_path: Path
) -> None:
    plan = make_x200_plan(x200_files)
    checkpoint = tmp_path / "tampered-after-load.json"
    original = WorkflowSession(plan, journal_path=checkpoint)
    assert original.validate().decision is CompatibilityDecision.ALLOWED
    loaded = OperationJournal.load_checkpoint(checkpoint, expected_plan=plan)
    resumed = WorkflowSession(plan, journal=loaded)

    document = json.loads(checkpoint.read_text(encoding="utf-8"))
    document["entries"][0]["message"] = "changed after checkpoint validation"
    checkpoint.write_text(json.dumps(document), encoding="utf-8")
    corrupted_bytes = checkpoint.read_bytes()

    with pytest.raises(JournalCorruptionError, match="before append"):
        resumed.verify_backup()
    assert resumed.state is OperationState.VALIDATED
    assert checkpoint.read_bytes() == corrupted_bytes


def test_blocked_journal_persists_failure_details_for_inspection(
    x200_files: dict[str, Path], tmp_path: Path
) -> None:
    plan = make_x200_plan(x200_files)
    simulated = replace(plan, programmer=replace(plan.programmer, kind=ProgrammerKind.SIMULATION))
    checkpoint = tmp_path / "blocked.json"
    session = WorkflowSession(simulated, journal_path=checkpoint)
    assert session.validate().decision is CompatibilityDecision.BLOCKED

    inspected = OperationJournal.inspect_checkpoint(checkpoint, expected_plan=simulated)
    entry = inspected.entries[-1]
    assert entry.failure is True
    assert entry.failure_code == entry.code
    assert entry.failure_message == entry.message
    with pytest.raises(UnsafeCheckpointError, match="not a supported resume point"):
        OperationJournal.load_checkpoint(checkpoint, expected_plan=simulated)

