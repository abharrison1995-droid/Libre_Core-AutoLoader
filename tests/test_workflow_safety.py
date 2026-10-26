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
    JournalConflictError,
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


def _reseal_journal_document(document: dict[str, object]) -> None:
    """Recompute journal checksums after deliberate, schema-valid test tampering."""
    from autoloader.workflow.journal import _checksum

    entries = document["entries"]
    assert isinstance(entries, list)
    for entry in entries:
        assert isinstance(entry, dict)
        event = {key: value for key, value in entry.items() if key != "checksum"}
        entry["checksum"] = _checksum({
            "previous_checksum": entry["previous_checksum"],
            "event": event,
        })
    body = {key: value for key, value in document.items() if key != "checkpoint_checksum"}
    document["checkpoint_checksum"] = _checksum(body)


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
    conflicting_tokens_in_one_raw_field = replace(
        base,
        device=replace(base.device, raw_dmi_product_name="ThinkPad T430 X220"),
    )

    assert _assert_decision(conflicting, CompatibilityDecision.BLOCKED)
    assert _assert_decision(conflicting_version, CompatibilityDecision.BLOCKED)
    assert _assert_decision(alias, CompatibilityDecision.BLOCKED)
    assert _assert_decision(manual_only, CompatibilityDecision.UNKNOWN)
    assert _assert_decision(manual_conflict, CompatibilityDecision.BLOCKED)
    within_field_reasons = _assert_decision(conflicting_tokens_in_one_raw_field, CompatibilityDecision.BLOCKED)
    assert any("conflicting model tokens" in reason for reason in within_field_reasons)


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


def test_t430_internal_flash_unlock_must_be_observed_available(tmp_path: Path) -> None:
    artifact = tmp_path / "t430.rom"
    artifact.write_bytes(b"R" * (4 * MIB))
    base = make_t430_plan(artifact, "G1ET74WW (2.64 )")
    observed_locked = replace(
        base,
        device=replace(
            base.device,
            internal_flash_unlock_observed=True,
            internal_flash_unlock_status=False,
        ),
    )
    unobserved = replace(
        base,
        device=replace(
            base.device,
            internal_flash_unlock_observed=False,
            internal_flash_unlock_status=True,
        ),
    )

    locked_reasons = _assert_decision(observed_locked, CompatibilityDecision.BLOCKED)
    unknown_reasons = _assert_decision(unobserved, CompatibilityDecision.UNKNOWN)
    assert any("observed as unavailable" in reason for reason in locked_reasons)
    assert any("not been independently observed" in reason for reason in unknown_reasons)


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


@pytest.mark.parametrize(
    ("case", "expected", "reason_fragment"),
    [
        ("programmer-unobserved", CompatibilityDecision.UNKNOWN, "not been physically observed"),
        ("interface-failed", CompatibilityDecision.BLOCKED, "interface or wiring verification failed"),
        ("interface-unknown", CompatibilityDecision.UNKNOWN, "interface and wiring must be verified"),
        ("voltage-unknown", CompatibilityDecision.UNKNOWN, "output voltage is unknown"),
        ("voltage-mismatch", CompatibilityDecision.BLOCKED, "exactly 3300 mV"),
        ("topology-unobserved", CompatibilityDecision.UNKNOWN, "physical flash chip topology has not been observed"),
        ("chip-unobserved", CompatibilityDecision.UNKNOWN, "one or more listed flash chips are not physically observed"),
        ("duplicate-chip-id", CompatibilityDecision.BLOCKED, "duplicate chip identifiers"),
    ],
)
def test_programmer_and_topology_uncertainty_never_becomes_write_compatibility(
    x200_files: dict[str, Path],
    case: str,
    expected: CompatibilityDecision,
    reason_fragment: str,
) -> None:
    plan = make_x200_plan(x200_files)
    if case == "programmer-unobserved":
        plan = replace(plan, programmer=replace(plan.programmer, observed=False))
    elif case == "interface-failed":
        plan = replace(plan, programmer=replace(plan.programmer, interface_verified=False))
    elif case == "interface-unknown":
        plan = replace(plan, programmer=replace(plan.programmer, interface_verified=None))
    elif case == "voltage-unknown":
        plan = replace(plan, programmer=replace(plan.programmer, voltage_mv=None))
    elif case == "voltage-mismatch":
        plan = replace(plan, programmer=replace(plan.programmer, voltage_mv=1800))
    elif case == "topology-unobserved":
        plan = replace(plan, topology=replace(plan.topology, observed=False))
    elif case == "chip-unobserved":
        plan = replace(
            plan,
            topology=replace(plan.topology, chips=(replace(plan.topology.chips[0], observed=False),)),
        )
    elif case == "duplicate-chip-id":
        original = plan.topology.chips[0]
        duplicate = replace(original, physical_label="secondary")
        plan = replace(plan, topology=replace(plan.topology, chips=(original, duplicate)))

    result = CompatibilityValidator().validate(plan)
    assert result.decision is expected, result.reasons
    assert any(reason_fragment in reason for reason in result.reasons), result.reasons


@pytest.mark.parametrize("profile", ["x200", "t430"])
def test_observed_chip_capacity_mismatch_blocks_each_fixed_v1_profile(
    x200_files: dict[str, Path], tmp_path: Path, profile: str
) -> None:
    if profile == "x200":
        plan = make_x200_plan(x200_files)
        changed_chip = replace(plan.topology.chips[0], capacity_bytes=2 * MIB)
        changed_topology = replace(plan.topology, chips=(changed_chip,))
        expected_reason = "observed flash chip capacity is not allowlisted"
    else:
        artifact = tmp_path / "t430-original.rom"
        artifact.write_bytes(b"T" * (4 * MIB))
        plan = make_t430_plan(artifact, "G1ET74WW (2.64 )")
        changed_chips = (plan.topology.chips[0], replace(plan.topology.chips[1], capacity_bytes=8 * MIB))
        changed_topology = replace(plan.topology, chips=changed_chips)
        expected_reason = "top flash chip capacity does not match the V1 profile"

    result = CompatibilityValidator().validate(replace(plan, topology=changed_topology))
    assert result.decision is CompatibilityDecision.BLOCKED, result.reasons
    assert any(expected_reason in reason for reason in result.reasons), result.reasons


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
    out_of_bounds_regions = profile_regions[:-1] + (
        RegionEvidence("bios", 5 * MIB, 8 * MIB + 1),
    )
    out_of_bounds = replace(
        with_regions,
        topology=replace(with_regions.topology, regions=out_of_bounds_regions),
        artifact=replace(with_regions.artifact, regions=out_of_bounds_regions),
    )
    reasons = _assert_decision(out_of_bounds, CompatibilityDecision.BLOCKED)
    assert any("exceeds the observed 8 MiB chip bounds" in reason for reason in reasons)


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


@pytest.mark.parametrize(
    ("case", "expected", "reason"),
    [
        ("topology-unobserved", CompatibilityDecision.UNKNOWN, "complete observed chip topology"),
        ("chip-unobserved", CompatibilityDecision.BLOCKED, "cannot skip an unobserved flash chip"),
        ("no-chip-identifiers", CompatibilityDecision.BLOCKED, "no observed chip identifiers"),
    ],
)
def test_backup_validator_keeps_incomplete_physical_topology_unusable(
    x200_files: dict[str, Path], case: str, expected: CompatibilityDecision, reason: str
) -> None:
    plan = make_x200_plan(x200_files)
    if case == "topology-unobserved":
        plan = replace(plan, topology=replace(plan.topology, observed=False))
    elif case == "chip-unobserved":
        plan = replace(
            plan,
            topology=replace(
                plan.topology,
                chips=(replace(plan.topology.chips[0], observed=False),),
            ),
        )
    elif case == "no-chip-identifiers":
        plan = replace(plan, topology=replace(plan.topology, chips=()))

    result = CompatibilityValidator().validate_backup(plan)
    assert result.decision is expected, result.reasons
    assert any(reason in item for item in result.reasons), result.reasons


def test_backup_validator_rejects_profile_outside_explicit_allowlist(x200_files: dict[str, Path]) -> None:
    plan = replace(make_x200_plan(x200_files), profile_id="unlisted-device-profile")
    result = CompatibilityValidator().validate_backup(plan)
    assert result.decision is CompatibilityDecision.BLOCKED
    assert any("explicit V1 allowlist" in reason for reason in result.reasons)


@pytest.mark.parametrize(
    ("case", "expected", "reason"),
    [
        ("unobserved-topology", CompatibilityDecision.UNKNOWN, "complete observed chip topology"),
        ("duplicate-pair", CompatibilityDecision.BLOCKED, "exactly one pair"),
        ("partial-path", CompatibilityDecision.BLOCKED, "temporary or partial"),
        ("same-path", CompatibilityDecision.BLOCKED, "separate files"),
        ("malformed-hash", CompatibilityDecision.BLOCKED, "SHA-256 declaration is malformed"),
        ("missing-file", CompatibilityDecision.UNKNOWN, "not available for verification"),
        ("wrong-size", CompatibilityDecision.BLOCKED, "exact observed chip capacity"),
        ("changed-file", CompatibilityDecision.BLOCKED, "content differs from a declared SHA-256"),
    ],
)
def test_backup_validator_rejects_untrusted_read_pair_variants(
    x200_files: dict[str, Path],
    tmp_path: Path,
    case: str,
    expected: CompatibilityDecision,
    reason: str,
) -> None:
    plan = make_x200_plan(x200_files)
    pair = plan.backup.pairs[0]
    if case == "unobserved-topology":
        plan = replace(plan, topology=replace(plan.topology, observed=False))
    elif case == "duplicate-pair":
        plan = replace(plan, backup=BackupEvidence((pair, pair)))
    elif case == "partial-path":
        plan = replace(plan, backup=BackupEvidence((replace(pair, first_path=pair.first_path + ".partial"),)))
    elif case == "same-path":
        plan = replace(plan, backup=BackupEvidence((replace(pair, second_path=pair.first_path),)))
    elif case == "malformed-hash":
        plan = replace(plan, backup=BackupEvidence((replace(pair, first_sha256="not-a-sha256"),)))
    elif case == "missing-file":
        plan = replace(plan, backup=BackupEvidence((replace(pair, first_path=str(tmp_path / "missing.rom")),)))
    elif case == "wrong-size":
        short = tmp_path / "short-backup.rom"
        short.write_bytes(b"short")
        plan = replace(plan, backup=BackupEvidence((replace(
            pair, first_path=str(short), first_sha256=_hash(short)
        ),)))
    elif case == "changed-file":
        changed = tmp_path / "changed-backup.rom"
        changed.write_bytes(b"\x73" * (4 * MIB))
        plan = replace(plan, backup=BackupEvidence((replace(pair, first_path=str(changed)),)))

    result = CompatibilityValidator().validate_backup(plan)
    assert result.decision is expected, result.reasons
    assert any(reason in item for item in result.reasons), result.reasons


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


@pytest.mark.parametrize(
    ("change", "expected_message"),
    [
        ("compatibility", "plan evidence no longer passes compatibility validation"),
        ("backup", "backup evidence no longer passes verification"),
    ],
)
def test_authorization_issue_revalidates_live_plan_and_backup_before_token_creation(
    x200_files: dict[str, Path], change: str, expected_message: str
) -> None:
    plan = make_x200_plan(x200_files)
    validator = CompatibilityValidator()
    approved_compatibility = validator.validate(plan)
    approved_backup = validator.validate_backup(plan)
    assert approved_compatibility.decision is CompatibilityDecision.ALLOWED
    assert approved_backup.decision is CompatibilityDecision.ALLOWED
    ledger = AuthorizationLedger(validator)

    if change == "compatibility":
        current_plan = replace(plan, programmer=replace(plan.programmer, kind=ProgrammerKind.SIMULATION))
        backup_path = None
        original_backup = None
    else:
        current_plan = plan
        backup_path = Path(plan.backup.pairs[0].second_path)
        original_backup = backup_path.read_bytes()
        backup_path.write_bytes(b"changed after old validation")

    try:
        with pytest.raises(AuthorizationError, match=expected_message):
            ledger.issue(
                current_plan,
                actor="Synthetic safety test",
                explicit_approval=True,
                compatibility=approved_compatibility,
                backup_verification=approved_backup,
            )
        assert ledger._entries == {}
    finally:
        if backup_path is not None and original_backup is not None:
            backup_path.write_bytes(original_backup)


@pytest.mark.parametrize(
    ("case", "expected_message"),
    [
        ("no-explicit-approval", "explicit human approval is required"),
        ("missing-actor", "approval actor must be recorded"),
        ("compatibility-not-allowed", "compatibility evidence is not fully allowed"),
        ("backup-not-allowed", "complete verified backup evidence is required"),
    ],
)
def test_authorization_missing_or_denied_inputs_create_no_receipt(
    x200_files: dict[str, Path], case: str, expected_message: str
) -> None:
    plan = make_x200_plan(x200_files)
    validator = CompatibilityValidator()
    compatibility = validator.validate(plan)
    backup = validator.validate_backup(plan)
    ledger = AuthorizationLedger(validator)
    explicit_approval = True
    actor = "Synthetic safety test"
    if case == "no-explicit-approval":
        explicit_approval = False
    elif case == "missing-actor":
        actor = "   "
    elif case == "compatibility-not-allowed":
        compatibility = replace(compatibility, decision=CompatibilityDecision.BLOCKED)
    elif case == "backup-not-allowed":
        backup = replace(backup, decision=CompatibilityDecision.BLOCKED)

    with pytest.raises(AuthorizationError, match=expected_message):
        ledger.issue(
            plan,
            actor=actor,
            explicit_approval=explicit_approval,
            compatibility=compatibility,
            backup_verification=backup,
        )
    assert ledger._entries == {}


def test_unknown_approval_token_does_not_consume_a_valid_token(x200_files: dict[str, Path]) -> None:
    plan = make_x200_plan(x200_files)
    validator = CompatibilityValidator()
    ledger = AuthorizationLedger(validator)
    token = ledger.issue(
        plan,
        actor="Synthetic safety test",
        explicit_approval=True,
        compatibility=validator.validate(plan),
        backup_verification=validator.validate_backup(plan),
    )
    forged = replace(token, token_id="not-present-in-ledger")

    with pytest.raises(AuthorizationError, match="unknown or invalid"):
        ledger.consume(forged, plan)

    assert ledger.consume(token, plan) == token.digests


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


@pytest.mark.parametrize(
    ("tamper", "expected_message"),
    [
        ("sequence", "invalid sequence or operation ID"),
        ("operation-id", "invalid sequence or operation ID"),
        ("stale-time", "non-monotonic timestamp"),
        ("illegal-transition", "illegal state transition"),
        ("device-fingerprint", "changes device or artifact identity"),
        ("previous-checksum", "breaks the checksum chain"),
        ("failure-flag", "inconsistent failure fields"),
        ("unexpected-failure-code", "unexpected failure details"),
    ],
)
def test_rechecksummed_journal_event_tampering_stays_corrupt(
    x200_files: dict[str, Path],
    tmp_path: Path,
    tamper: str,
    expected_message: str,
) -> None:
    plan = make_x200_plan(x200_files)
    checkpoint = tmp_path / f"tampered-{tamper}.json"
    session = WorkflowSession(plan, journal_path=checkpoint)
    assert session.validate().decision is CompatibilityDecision.ALLOWED
    document = json.loads(checkpoint.read_text(encoding="utf-8"))
    entry = document["entries"][0]
    if tamper == "sequence":
        entry["sequence"] = 2
    elif tamper == "operation-id":
        entry["operation_id"] = "00000000-0000-4000-8000-000000000000"
    elif tamper == "stale-time":
        entry["timestamp_utc"] = "2000-01-01T00:00:00+00:00"
    elif tamper == "illegal-transition":
        entry["to_state"] = OperationState.WRITE_STARTED.value
    elif tamper == "device-fingerprint":
        entry["device_fingerprint"] = "0" * 64
    elif tamper == "previous-checksum":
        entry["previous_checksum"] = "f" * 64
    elif tamper == "failure-flag":
        entry["failure"] = True
    elif tamper == "unexpected-failure-code":
        entry["failure_code"] = "SYNTHETIC_UNEXPECTED_FAILURE"
    _reseal_journal_document(document)
    checkpoint.write_text(json.dumps(document), encoding="utf-8")
    tampered_bytes = checkpoint.read_bytes()

    with pytest.raises(JournalCorruptionError, match=expected_message):
        OperationJournal.load_checkpoint(checkpoint, expected_plan=plan)
    assert checkpoint.read_bytes() == tampered_bytes


@pytest.mark.parametrize(
    ("tamper", "expected_message"),
    [
        ("schema", "schema version is unsupported"),
        ("updated-before-created", "checkpoint metadata is malformed"),
        ("short-fingerprint", "checkpoint metadata is malformed"),
        ("entries-not-list", "checkpoint metadata is malformed"),
        ("non-object-event", "journal event is not an object"),
        ("event-checksum", "event 1 checksum is invalid"),
        ("backup-header-mismatch", "checkpoint backup evidence does not match"),
    ],
)
def test_rechecksummed_journal_header_and_event_shapes_remain_corrupt(
    x200_files: dict[str, Path],
    tmp_path: Path,
    tamper: str,
    expected_message: str,
) -> None:
    from autoloader.workflow.journal import _checksum

    plan = make_x200_plan(x200_files)
    checkpoint = tmp_path / f"rechecksummed-{tamper}.json"
    session = WorkflowSession(plan, journal_path=checkpoint)
    assert session.validate().decision is CompatibilityDecision.ALLOWED
    document = json.loads(checkpoint.read_text(encoding="utf-8"))
    if tamper == "schema":
        document["schema_version"] = 999
    elif tamper == "updated-before-created":
        document["updated_at_utc"] = "2000-01-01T00:00:00+00:00"
    elif tamper == "short-fingerprint":
        document["device_fingerprint"] = "bad"
    elif tamper == "entries-not-list":
        document["entries"] = {"unexpected": "object"}
    elif tamper == "non-object-event":
        document["entries"][0] = None
    elif tamper == "event-checksum":
        document["entries"][0]["message"] = "changed without event reseal"
    elif tamper == "backup-header-mismatch":
        document["backup_checksums"] = [{
            "chip_id": "main",
            "capture_id": "synthetic-capture",
            "path": str(tmp_path / "synthetic.rom"),
            "declared_sha256": "a" * 64,
            "actual_size_bytes": 4,
            "actual_sha256": "a" * 64,
        }]
    body = {key: value for key, value in document.items() if key != "checkpoint_checksum"}
    document["checkpoint_checksum"] = _checksum(body)
    checkpoint.write_text(json.dumps(document), encoding="utf-8")
    tampered_bytes = checkpoint.read_bytes()

    with pytest.raises(JournalCorruptionError, match=expected_message):
        OperationJournal.inspect_checkpoint(checkpoint, expected_plan=plan)
    assert checkpoint.read_bytes() == tampered_bytes
    assert session.state is OperationState.VALIDATED


def test_journal_rejects_backup_metadata_changed_outside_verify_transition(
    x200_files: dict[str, Path], tmp_path: Path
) -> None:
    plan = make_x200_plan(x200_files)
    checkpoint = tmp_path / "backup-metadata-tamper.json"
    session = WorkflowSession(plan, journal_path=checkpoint)
    session.validate()
    session.journal.append(
        action="synthetic-block",
        from_state=OperationState.VALIDATED,
        to_state=OperationState.BLOCKED,
        code="SYNTHETIC_BLOCK",
        message="synthetic terminal event for metadata validation only",
    )
    document = json.loads(checkpoint.read_text(encoding="utf-8"))
    document["entries"][1]["backup_checksums"] = []
    _reseal_journal_document(document)
    checkpoint.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(JournalCorruptionError, match="changes backup evidence outside verification"):
        OperationJournal.inspect_checkpoint(checkpoint, expected_plan=plan)


def test_journal_append_conflict_and_removal_preserve_checkpoint_and_state(
    x200_files: dict[str, Path], tmp_path: Path
) -> None:
    plan = make_x200_plan(x200_files)
    checkpoint = tmp_path / "concurrent-journal.json"
    first = OperationJournal(checkpoint, plan=plan)
    assert first.append(
        action="validate",
        from_state=OperationState.DRAFT,
        to_state=OperationState.VALIDATED,
        code="PLAN_VALIDATED",
        message="synthetic validated checkpoint",
    ).to_state is OperationState.VALIDATED
    stale = OperationJournal.load_checkpoint(checkpoint, expected_plan=plan)
    winner = OperationJournal.load_checkpoint(checkpoint, expected_plan=plan)
    winner.append(
        action="synthetic-block",
        from_state=OperationState.VALIDATED,
        to_state=OperationState.BLOCKED,
        code="SYNTHETIC_BLOCK",
        message="first writer wins the checkpoint compare-and-swap",
    )
    committed = checkpoint.read_bytes()

    with pytest.raises(JournalConflictError, match="changed since this journal was loaded"):
        stale.append(
            action="synthetic-block",
            from_state=OperationState.VALIDATED,
            to_state=OperationState.BLOCKED,
            code="SYNTHETIC_BLOCK",
            message="stale writer must not replace the checkpoint",
        )
    assert stale.state is OperationState.VALIDATED
    assert checkpoint.read_bytes() == committed

    removed_path = tmp_path / "removed-journal.json"
    original = OperationJournal(removed_path, plan=plan)
    loaded = OperationJournal.load_checkpoint(removed_path, expected_plan=plan)
    removed_path.unlink()
    with pytest.raises(JournalConflictError, match="was removed since it was loaded"):
        loaded.append(
            action="validate",
            from_state=OperationState.DRAFT,
            to_state=OperationState.VALIDATED,
            code="PLAN_VALIDATED",
            message="must not recreate a removed checkpoint",
        )
    assert loaded.state is OperationState.DRAFT
    assert not removed_path.exists()
    assert original.state is OperationState.DRAFT


def test_journal_rejects_rechecksummed_terminal_state_that_disagrees_with_final_event(
    x200_files: dict[str, Path], tmp_path: Path
) -> None:
    from autoloader.workflow.journal import _checksum

    plan = make_x200_plan(x200_files)
    checkpoint = tmp_path / "terminal-state-tamper.json"
    session = WorkflowSession(replace(plan, backup=BackupEvidence()), journal_path=checkpoint)
    session.validate()
    assert session.verify_backup(plan.backup).decision is CompatibilityDecision.ALLOWED
    session.journal.append(
        action="external-spi-write",
        from_state=OperationState.BACKUP_VERIFIED,
        to_state=OperationState.WRITE_STARTED,
        code="WRITE_STARTED",
        message="synthetic write boundary for journal validation only",
    )
    session.journal.append(
        action="external-spi-write",
        from_state=OperationState.WRITE_STARTED,
        to_state=OperationState.WRITE_UNCERTAIN,
        code="WRITE_UNCERTAIN",
        message="synthetic uncertain terminal state",
    )

    document = json.loads(checkpoint.read_text(encoding="utf-8"))
    document["state"] = OperationState.WRITE_VERIFIED.value
    body = {key: value for key, value in document.items() if key != "checkpoint_checksum"}
    document["checkpoint_checksum"] = _checksum(body)
    checkpoint.write_text(json.dumps(document), encoding="utf-8")
    tampered_bytes = checkpoint.read_bytes()

    with pytest.raises(JournalCorruptionError, match="state does not match the final journal transition"):
        OperationJournal.inspect_checkpoint(checkpoint, expected_plan=plan)
    assert checkpoint.read_bytes() == tampered_bytes


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


@pytest.mark.parametrize(
    ("replacement", "resume_message", "append_message"),
    [
        (b"{", "missing, truncated or invalid JSON", "cannot be read before append"),
        (b"[]", "root must be a JSON object", "root is malformed before append"),
    ],
)
def test_loaded_journal_refuses_malformed_replacement_before_append_or_resume(
    x200_files: dict[str, Path],
    tmp_path: Path,
    replacement: bytes,
    resume_message: str,
    append_message: str,
) -> None:
    plan = make_x200_plan(x200_files)
    checkpoint = tmp_path / "malformed-after-load.json"
    original = WorkflowSession(plan, journal_path=checkpoint)
    assert original.validate().decision is CompatibilityDecision.ALLOWED
    loaded = OperationJournal.load_checkpoint(checkpoint, expected_plan=plan)
    resumed = WorkflowSession(plan, journal=loaded)
    state_before = resumed.state
    entries_before = resumed.journal.entries
    checkpoint.write_bytes(replacement)
    foreign_bytes = checkpoint.read_bytes()

    with pytest.raises(JournalCorruptionError, match=resume_message):
        WorkflowSession.resume(plan, checkpoint)
    with pytest.raises(JournalCorruptionError, match=append_message):
        resumed.verify_backup()

    assert resumed.state is state_before is OperationState.VALIDATED
    assert resumed.journal.entries == entries_before
    assert checkpoint.read_bytes() == foreign_bytes


def test_initial_journal_bind_refuses_existing_file_before_first_checkpoint(
    x200_files: dict[str, Path], tmp_path: Path
) -> None:
    plan = make_x200_plan(x200_files)
    checkpoint = tmp_path / "created-before-initial-persist.json"
    journal = OperationJournal(checkpoint)
    assert not checkpoint.exists()

    foreign_bytes = b'{"foreign":"checkpoint"}\n'
    checkpoint.write_bytes(foreign_bytes)

    with pytest.raises(FileExistsError, match="refusing to overwrite an existing operation journal"):
        journal.bind_plan(plan)

    assert checkpoint.read_bytes() == foreign_bytes
    assert journal.state is OperationState.DRAFT
    assert journal.entries == ()
    assert journal._snapshot_checksum is None
    # The failed initial bind cannot persist or advance the workflow journal.


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

