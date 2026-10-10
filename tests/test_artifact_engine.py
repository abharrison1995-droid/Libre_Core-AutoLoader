from __future__ import annotations

import hashlib
import subprocess
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoloader.rom_builder.artifact_engine import (
    ARTIFACT_RECIPES,
    COREBOOT_2609,
    HANDOFF_RECIPES,
    LIBREBOOT_2601,
    ONEVYRAIN_HANDOFF,
    X200_4MB,
    X200_8MB,
    X220_COREBOOT,
    ArtifactEngine,
    ArtifactManifest,
    BuildRequest,
    BuildStatus,
    CBFSInspectionRecord,
    CommandRecord,
    PinnedFile,
    ReproducibilityRecord,
    SourcePin,
    compare_region_maps,
    derive_lbmk_output_path,
    _finalize_x220_operation_plan,
    lbmk_expected_output_paths,
    parse_cbfs_parseable_output,
    parse_ifdtool_regions,
    parse_kconfig,
    select_exact_lbmk_artifact,
    validate_recipe_config,
    validate_x220_region_preservation,
    validate_x220_backup_evidence,
)
from autoloader.flasher.backup_service import (
    BackupManifestStore,
    BackupSetResult,
    CapturedChipBackup,
    StorageEvidence,
)
from autoloader.workflow.types import (
    BackupEvidence,
    BackupPairEvidence,
    BuildIntentEvidence,
    ChipEvidence,
    ChipTopology,
    DeviceEvidence,
    FirmwareFamily,
    ProgrammerEvidence,
    ProgrammerKind,
    RegionEvidence,
    build_intent_record,
    build_intent_sha256,
)


def test_pins_are_full_and_recipe_allowlist_is_exact() -> None:
    import autoloader.rom_builder.artifact_engine as module

    assert COREBOOT_2609.revision == "8b0e34f90e08547cf19e550a43ed72f69c39d6ed"
    assert COREBOOT_2609.tree_id == "1cb70fc2c4a0229fa17991b19cf6916b71f75ba7"
    assert LIBREBOOT_2601.revision == "1cf3181537b8d1fe1df0e91681f700850a3d9bf6"
    assert LIBREBOOT_2601.tree_id == "0cb9451c6e2e265d3aa5b7e371752d45cab98f53"
    assert all(len(pin.revision) == 40 and len(pin.tree_id) == 40 for pin in (COREBOOT_2609, LIBREBOOT_2601))
    assert set(ARTIFACT_RECIPES) == {"x200-libreboot-4mb", "x200-libreboot-8mb", "x220-coreboot-8mb-seabios"}
    assert set(HANDOFF_RECIPES) == {"t430-1vyrain-upstream-handoff"}
    assert X200_4MB.expected_size_bytes == 4 * 1024 * 1024
    assert X200_8MB.expected_size_bytes == 8 * 1024 * 1024
    assert X220_COREBOOT.expected_size_bytes == 8 * 1024 * 1024
    assert ONEVYRAIN_HANDOFF.source.repository_url == "https://github.com/n4ru/1vyrain.git"
    assert {item.path for item in LIBREBOOT_2601.files} >= {
        "config/coreboot/default/target.cfg",
        "config/git/coreboot/pkg.cfg",
        "include/lib.sh",
    }
    assert {item.path for item in COREBOOT_2609.files} >= {
        "util/cbfstool/cbfs_image.c",
        "util/cbfstool/cbfstool.c",
        "src/Kconfig",
    }
    assert module._LBMK_COREBOOT_REVISION == "fcd716d9a272461da1fdbaf1c048eb5a52c7896b"
    assert module._LBMK_COREBOOT_TREE == "e16fe240567cb3fa08619c6a23e51c4af142f6ac"
    assert tuple(item.sha256 for item in module._LBMK_COREBOOT_CBFSTOOL_SOURCES) == (
        "fd6a56a8e20c15f4fd8f03511f05ce8264f120ad0a0dc11900e2e04f89a88dda",
        "0cfd0e5cfb4b169355508e270b1cff1f644010cda72bd36a9a8f07952377e3cb",
    )


_CBFS_FIXTURE = "\n".join((
    "Name\tOffset\tType\tMetadata Size\tData Size\tTotal Size",
    "fallback/romstage\t0x0\tstage\t0x20\t0x1000\t0x1020",
    "fallback/payload\t0x2000\tpayload\t0x20\t0x4000\t0x4020",
    "(empty)\t0x7000\tnull\t0x20\t0x1000\t0x1020",
))


def test_cbfs_parseable_inventory_requires_pinned_header_bounds_and_payload() -> None:
    inventory = parse_cbfs_parseable_output(
        _CBFS_FIXTURE,
        required_payload="fallback/payload",
        region_size_bytes=0x9000,
    )
    assert tuple(item.name for item in inventory) == (
        "fallback/romstage", "fallback/payload", "(empty)"
    )
    assert inventory[1].entry_type == "payload"
    assert inventory[1].total_size_bytes == 0x4020

    invalid = (
        _CBFS_FIXTURE.replace("\t0x4020\n", "\t0x4021\n"),
        _CBFS_FIXTURE.replace("\tpayload\t", "\traw\t"),
        _CBFS_FIXTURE.replace("fallback/payload", "fallback/romstage"),
        _CBFS_FIXTURE.replace("0x7000", "0x9000"),
        _CBFS_FIXTURE.replace("\t0x1000\t0x1020\n", "\t0x1000\n"),
    )
    for output in invalid:
        with pytest.raises(ValueError):
            parse_cbfs_parseable_output(
                output,
                required_payload="fallback/payload",
                region_size_bytes=0x9000,
            )


def test_cbfs_parseable_inventory_accepts_pinned_nonverbose_region_output() -> None:
    # Pinned cbfstool `print -r COREBOOT -k` has the header directly; unlike
    # verbose output it does not include an FMAP marker.
    inventory = parse_cbfs_parseable_output(
        _CBFS_FIXTURE,
        required_payload="fallback/payload",
        region_size_bytes=0x9000,
        expected_region="COREBOOT",
    )
    assert inventory[1].name == "fallback/payload"

    for output, expected_region in (
        ("[FMAP REGION]\tBIOS\n" + _CBFS_FIXTURE, "COREBOOT"),
        ("[FMAP REGION]\tCOREBOOT\n" + _CBFS_FIXTURE, None),
    ):
        with pytest.raises(ValueError):
            parse_cbfs_parseable_output(
                output,
                required_payload="fallback/payload",
                region_size_bytes=0x9000,
                expected_region=expected_region,
            )


def test_cbfs_inspection_records_exact_pinned_tool_invocations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import autoloader.rom_builder.artifact_engine as module

    engine = ArtifactEngine()
    source = tmp_path / "source"
    coreboot_tool = source / "util" / "cbfstool" / "cbfstool"
    coreboot_tool.parent.mkdir(parents=True)
    coreboot_tool.write_bytes(b"fake coreboot cbfstool")
    rom = tmp_path / "coreboot.rom"
    rom.write_bytes(b"fake rom")
    logs = tmp_path / "logs"
    logs.mkdir()
    invocations: list[tuple[str, ...]] = []

    def fake_readonly(argv, _cwd, _logs, commands):
        invocations.append(tuple(argv))
        if "-r" in argv:
            return _CBFS_FIXTURE
        return _CBFS_FIXTURE

    monkeypatch.setattr(engine, "_run_readonly", fake_readonly)
    records: list[CommandRecord] = []
    record = engine._inspect_cbfs(X220_COREBOOT, source, rom, logs, records)
    assert invocations == [(str(coreboot_tool), str(rom), "print", "-r", "COREBOOT", "-k")]
    assert isinstance(record, CBFSInspectionRecord)
    assert record.source_revision == COREBOOT_2609.revision
    assert record.region == "COREBOOT"
    assert record.tool_sha256 == hashlib.sha256(coreboot_tool.read_bytes()).hexdigest()
    assert record.source_file_hashes == (
        ("util/cbfstool/cbfs_image.c", "2526616afa482cdd2552788e7326f8b0dd840c218c17f1757081f5df516b6ed7"),
        ("util/cbfstool/cbfstool.c", "ebdb9caae36be926fae39af2ae3a97d02a18c796ab9c963f68e5b7ea3decc00a"),
    )

    lbmk_recipe = X200_4MB
    lbmk_tool = source / "elf" / "coreboot" / "default" / "cbfstool"
    lbmk_tool.parent.mkdir(parents=True)
    lbmk_tool.write_bytes(b"fake pinned nested cbfstool")
    monkeypatch.setattr(
        engine,
        "_validate_lbmk_nested_coreboot",
        lambda _path: (
            "https://review.coreboot.org/coreboot",
            "fcd716d9a272461da1fdbaf1c048eb5a52c7896b",
            "e16fe240567cb3fa08619c6a23e51c4af142f6ac",
            (
                ("util/cbfstool/cbfs_image.c", "a" * 64),
                ("util/cbfstool/cbfstool.c", "b" * 64),
            ),
        ),
    )
    invocations.clear()
    engine._inspect_cbfs(lbmk_recipe, source, rom, logs, records)
    assert invocations == [(str(lbmk_tool), str(rom), "print", "-k")]


def _make_x220_backup_fixture(tmp_path: Path):
    from autoloader.rom_builder.artifact_engine import BlobInput

    mib = 1024 * 1024
    size = 8 * mib
    original = (bytes(range(251)) * ((size // 251) + 1))[:size]
    regions = (
        RegionEvidence("ifd", 0, 0x1000),
        RegionEvidence("gbe", 0x1000, 0x3000),
        RegionEvidence("me", 0x3000, 0x500000),
        RegionEvidence("bios", 0x500000, size),
    )
    intent = BuildIntentEvidence(
        profile_id="x220-coreboot",
        requested_model_id="x220",
        firmware_family=FirmwareFamily.COREBOOT,
        device=DeviceEvidence(
            raw_dmi_product_name="ThinkPad X220",
            raw_dmi_product_version="4291CTO",
            raw_dmi_system_family="ThinkPad X220",
            raw_mtm="4291CTO",
            raw_bios_version="G1ET99WW (9.99)",
        ),
        topology=ChipTopology(
            observed=True,
            chips=(ChipEvidence("main", size, True, physical_label="main", voltage_mv=3300),),
            regions=regions,
        ),
        programmer=ProgrammerEvidence(
            kind=ProgrammerKind.EXTERNAL_SPI,
            observed=True,
            interface_verified=True,
            wiring_verified=True,
            voltage_mv=3300,
        ),
        recipe_id=X220_COREBOOT.recipe_id,
        source_url=COREBOOT_2609.repository_url,
        source_revision=COREBOOT_2609.revision,
        source_tree_id=COREBOOT_2609.tree_id,
        configuration_sha256=X220_COREBOOT.expected_config_sha256,
        expected_size_bytes=size,
    )
    primary_dir, recovery_dir = tmp_path / "primary", tmp_path / "recovery"
    primary_dir.mkdir()
    recovery_dir.mkdir()
    primary_path, recovery_path = primary_dir / "chip-main.rom", recovery_dir / "chip-main.rom"
    primary_path.write_bytes(original)
    recovery_path.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()
    captured = CapturedChipBackup(
        chip_id="main",
        observed_chip_name="W25Q64.V",
        capacity_bytes=size,
        first_path=str(primary_path),
        second_path=str(recovery_path),
        first_sha256=digest,
        second_sha256=digest,
        first_capture_id="capture-original-1",
        second_capture_id="capture-original-2",
    )
    pair = BackupPairEvidence(
        chip_id="main",
        first_path=str(primary_path),
        second_path=str(recovery_path),
        first_sha256=digest,
        second_sha256=digest,
        first_capture_id=captured.first_capture_id,
        second_capture_id=captured.second_capture_id,
        transient=False,
    )
    intent = replace(intent, backup=BackupEvidence((pair,)))
    event = {
        "operation_id": "backup-operation-x220",
        "status": "verified",
        "profile_id": intent.profile_id,
        "requested_model_id": intent.requested_model_id,
        "programmer": "ch341a_spi",
        "tool": {
            "name": "flashrom",
            "executable": "fixture-flashrom",
            "version": "fixture-tool",
        },
        "device_identifiers": {
            "raw_dmi_product_name": intent.device.raw_dmi_product_name,
            "raw_mtm": intent.device.raw_mtm,
            "raw_bios_version": intent.device.raw_bios_version,
        },
        "source_artifact": None,
        "build_intent": build_intent_record(intent),
        "build_intent_sha256": build_intent_sha256(intent),
        "topology": [{
            "chip_id": "main",
            "physical_label": "main",
            "capacity_bytes": size,
            "voltage_mv": 3300,
            "observed_chip_name": "W25Q64.V",
        }],
        "region_map": [
            {"name": region.name, "start_byte": region.start_byte, "end_byte": region.end_byte}
            for region in regions
        ],
        "primary_storage": {
            "path": str(primary_dir),
            "volume_id": "primary-volume",
            "filesystem_type": "ext4",
            "available_bytes": 2 * size,
            "required_bytes": size,
            "persistent_confirmed": True,
            "scratch_readback_verified": True,
            "observed_at_utc": "2026-10-10T00:00:00+00:00",
        },
        "recovery_storage": {
            "path": str(recovery_dir),
            "volume_id": "recovery-volume",
            "filesystem_type": "ext4",
            "available_bytes": 2 * size,
            "required_bytes": size,
            "persistent_confirmed": True,
            "scratch_readback_verified": True,
            "observed_at_utc": "2026-10-10T00:00:00+00:00",
        },
        "chips": [{
            "chip_id": "main",
            "capacity_bytes": size,
            "first_path": str(primary_path),
            "second_path": str(recovery_path),
            "first_size_bytes": size,
            "second_size_bytes": size,
            "first_sha256": digest,
            "second_sha256": digest,
            "first_capture_id": captured.first_capture_id,
            "second_capture_id": captured.second_capture_id,
        }],
    }
    primary_manifest_hash = BackupManifestStore(primary_dir).append(event)
    recovery_manifest_hash = BackupManifestStore(recovery_dir).append(event)

    def storage(path: Path, volume: str) -> StorageEvidence:
        return StorageEvidence(
            path=str(path),
            volume_id=volume,
            filesystem_type="ext4",
            available_bytes=2 * size,
            required_bytes=size,
            persistence_confirmed=True,
            scratch_readback_verified=True,
            observed_at_utc="2026-10-10T00:00:00+00:00",
        )

    backup = BackupSetResult(
        operation_id="backup-operation-x220",
        profile_id=intent.profile_id,
        created_at_utc="2026-10-10T00:00:00+00:00",
        tool_name="flashrom",
        tool_version="fixture-tool",
        programmer="ch341a_spi",
        backup=BackupEvidence((pair,)),
        chips=(captured,),
        primary_manifest=str(primary_dir / "backup-manifest.json"),
        recovery_manifest=str(recovery_dir / "backup-manifest.json"),
        primary_manifest_sha256=primary_manifest_hash,
        recovery_manifest_sha256=recovery_manifest_hash,
        primary_storage=storage(primary_dir, "primary-volume"),
        recovery_storage=storage(recovery_dir, "recovery-volume"),
    )
    blob_inputs = []
    for name, region_name in (("descriptor.bin", "ifd"), ("me.bin", "me"), ("gbe.bin", "gbe")):
        region = next(item for item in regions if item.name == region_name)
        data = original[region.start_byte:region.end_byte]
        path = tmp_path / name
        path.write_bytes(data)
        blob_inputs.append(BlobInput(name, path, hashlib.sha256(data).hexdigest()))
    return intent, backup, regions, tuple(blob_inputs), original


def test_x220_backup_evidence_binds_blobs_to_matching_full_chip_backup(tmp_path: Path) -> None:
    intent, backup, regions, blob_inputs, original = _make_x220_backup_fixture(tmp_path)
    observed, provenance = validate_x220_backup_evidence(intent, backup, regions, blob_inputs)
    assert observed == original
    assert provenance.operation_id == backup.operation_id
    assert provenance.original_chip_sha256 == backup.chips[0].first_sha256
    assert provenance.preserved_regions == ("gbe", "ifd", "me")


def test_fresh_x220_output_hash_is_bound_only_after_generation(tmp_path: Path) -> None:
    from autoloader.workflow.compatibility import CompatibilityValidator
    from autoloader.workflow.types import CompatibilityDecision

    intent, backup, regions, _blob_inputs, _original = _make_x220_backup_fixture(tmp_path)
    generated = tmp_path / "newly-generated-coreboot.rom"
    fresh_bytes = (bytes(range(239)) * ((X220_COREBOOT.expected_size_bytes // 239) + 1))[
        :X220_COREBOOT.expected_size_bytes
    ]
    generated.write_bytes(fresh_bytes)
    observed_digest = hashlib.sha256(fresh_bytes).hexdigest()

    final_plan = _finalize_x220_operation_plan(intent, backup, generated, regions)

    assert final_plan.artifact.expected_sha256 == observed_digest
    assert final_plan.artifact.path == str(generated.resolve())
    assert CompatibilityValidator().validate(final_plan).decision is CompatibilityDecision.ALLOWED
    with pytest.raises(ValueError, match="hash differs"):
        _finalize_x220_operation_plan(intent, backup, generated, regions, expected_sha256="0" * 64)

    generated.write_bytes(bytes((fresh_bytes[0] ^ 0xFF,)) + fresh_bytes[1:])
    result = CompatibilityValidator().validate(final_plan)
    assert result.decision is CompatibilityDecision.BLOCKED
    assert any("content does not match" in reason for reason in result.reasons)


def test_x220_backup_evidence_blocks_missing_or_unbound_inputs(tmp_path: Path) -> None:
    intent, backup, regions, blob_inputs, _original = _make_x220_backup_fixture(tmp_path)
    with pytest.raises(ValueError, match="complete original backup"):
        validate_x220_backup_evidence(intent, None, regions, blob_inputs)

    me_input = next(item for item in blob_inputs if item.name == "me.bin")
    me_input.path.write_bytes(b"incorrect ME blob")
    wrong_blob = replace(me_input, sha256=hashlib.sha256(me_input.path.read_bytes()).hexdigest())
    inputs = tuple(wrong_blob if item.name == me_input.name else item for item in blob_inputs)
    with pytest.raises(ValueError, match="exact matching slice"):
        validate_x220_backup_evidence(intent, backup, regions, inputs)


def test_x220_backup_evidence_rejects_changed_backup_or_wrong_region_map(tmp_path: Path) -> None:
    intent, backup, regions, blob_inputs, _original = _make_x220_backup_fixture(tmp_path)
    primary = Path(backup.chips[0].first_path)
    primary.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="exact observed chip capacity"):
        validate_x220_backup_evidence(intent, backup, regions, blob_inputs)

    second_root = tmp_path / "second"
    second_root.mkdir()
    intent, backup, regions, blob_inputs, _original = _make_x220_backup_fixture(second_root)
    changed = regions[:-1] + (RegionEvidence("bios", 0x510000, 8 * 1024 * 1024),)
    with pytest.raises(ValueError, match="differ from the evidence-bound"):
        validate_x220_backup_evidence(intent, backup, changed, blob_inputs)


def test_pinned_lbmk_output_name_is_derived_and_allowlisted() -> None:
    assert derive_lbmk_output_path("x200_4mb", "libgfxinit_corebootfb", "seabios") == (
        "bin/x200_4mb/seabios_x200_4mb_libgfxinit_corebootfb.rom"
    )
    assert derive_lbmk_output_path("x200_8mb", "libgfxinit_txtmode", "seabios") == (
        "bin/x200_8mb/seabios_x200_8mb_libgfxinit_txtmode.rom"
    )
    for args in (
        ("x200s_4mb", "libgfxinit_corebootfb", "seabios"),
        ("x200_4mb", "unknown", "seabios"),
        ("x200_4mb", "libgfxinit_corebootfb", "grub"),
    ):
        with pytest.raises(ValueError):
            derive_lbmk_output_path(*args)


def test_lbmk_output_inventory_matches_pinned_cprom_and_target_inputs(tmp_path: Path) -> None:
    assert any(item.path == "include/rom.sh" for item in LIBREBOOT_2601.files)
    assert {item.path for item in LIBREBOOT_2601.files} >= {
        "config/coreboot/x200_4mb/target.cfg",
        "config/coreboot/x200_4mb/config/libgfxinit_corebootfb",
        "config/coreboot/x200_4mb/config/libgfxinit_txtmode",
        "config/coreboot/x200_8mb/target.cfg",
        "config/coreboot/x200_8mb/config/libgfxinit_corebootfb",
        "config/coreboot/x200_8mb/config/libgfxinit_txtmode",
    }
    # The pinned include/rom.sh cprom() at lines 448-473 derives ROM names
    # from payload, target, init/display mode and .gkb keymap. Its
    # mkcorebootbin_real() iterates both target configs; target.cfg enables
    # SeaBIOS, GRUB, memtest and amd64 U-Boot. Therefore both target sizes
    # produce the same 31 distinct names, including one canonical SeaBIOS
    # corebootfb variant selected by the recipe.
    source = tmp_path / "pinned-lbmk"
    source.mkdir()
    outputs = _populate_lbmk_output_fixture(source, X200_4MB)
    relative_paths = tuple(path.relative_to(source).as_posix() for path in outputs)
    assert len(relative_paths) == 31
    assert X200_4MB.output_relative_path in relative_paths
    assert "bin/x200_4mb/seauboot_x200_4mb_libgfxinit_corebootfb.rom" in relative_paths
    assert "bin/x200_4mb/seagrub_x200_4mb_libgfxinit_txtmode_usqwerty.rom" in relative_paths
    assert not any("seauboot" in path and "txtmode" in path for path in relative_paths)


def test_kconfig_parser_rejects_duplicate_and_recipe_mismatch() -> None:
    assert parse_kconfig("CONFIG_BOARD_LENOVO_X220=y\n# CONFIG_PAYLOAD_NONE is not set\n") == {
        "BOARD_LENOVO_X220": "y",
        "PAYLOAD_NONE": "n",
    }
    with pytest.raises(ValueError, match="Duplicate"):
        parse_kconfig("CONFIG_ROM_SIZE=0x800000\nCONFIG_ROM_SIZE=0x800000\n")

    expected = "\n".join(f"{key}={value}" for key, value in X220_COREBOOT.config_expectations)
    digest = validate_recipe_config(X220_COREBOOT, expected)
    assert digest == hashlib.sha256(expected.encode()).hexdigest()
    bad = expected.replace("CONFIG_BOARD_LENOVO_X220=y", "CONFIG_BOARD_LENOVO_X220I=y")
    with pytest.raises(ValueError):
        validate_recipe_config(X220_COREBOOT, bad)


def test_lbmk_resolved_config_must_match_exact_target() -> None:
    recipe = X200_4MB
    valid = "\n".join(f"{key}={value}" for key, value in recipe.config_expectations)
    assert validate_recipe_config(recipe, valid)
    with pytest.raises(ValueError):
        validate_recipe_config(recipe, valid.replace("CONFIG_ROM_SIZE=0x400000", "CONFIG_ROM_SIZE=0x800000"))
    with pytest.raises(ValueError):
        validate_recipe_config(recipe, valid.replace("CONFIG_BOARD_LENOVO_X200=y", "CONFIG_BOARD_LENOVO_X200S=y"))


def test_x220_ifd_map_requires_exact_contiguous_complete_layout() -> None:
    output = "\n".join(
        (
            "Flash Region 0 (Flash Descriptor): 00000000 - 00000fff",
            "Flash Region 1 (GbE): 00001000 - 00002fff",
            "Flash Region 2 (Intel ME): 00003000 - 004fffff",
            "Flash Region 3 (BIOS): 00500000 - 007fffff",
        )
    )
    regions = parse_ifdtool_regions(output, 8 * 1024 * 1024)
    assert [(r.name, r.start_byte, r.end_byte) for r in regions] == [
        ("ifd", 0, 0x1000),
        ("gbe", 0x1000, 0x3000),
        ("me", 0x3000, 0x500000),
        ("bios", 0x500000, 0x800000),
    ]
    compare_region_maps(regions, regions)
    with pytest.raises(ValueError):
        compare_region_maps(regions[:-1], regions)
    with pytest.raises(ValueError, match="gap"):
        parse_ifdtool_regions(output.replace("00001000", "00002000"), 8 * 1024 * 1024)
    with pytest.raises(ValueError, match="3 MiB"):
        parse_ifdtool_regions(
            output.replace("004fffff", "005fffff").replace("00500000", "00600000"),
            8 * 1024 * 1024,
        )


def test_x220_full_chip_preservation_requires_exact_original_non_bios_regions() -> None:
    size = 8 * 1024 * 1024
    regions = (
        RegionEvidence("ifd", 0, 0x1000),
        RegionEvidence("gbe", 0x1000, 0x3000),
        RegionEvidence("me", 0x3000, 0x500000),
        RegionEvidence("bios", 0x500000, size),
    )
    original = bytes((index % 251 for index in range(size)))
    generated = bytearray(original)
    generated[0x500000:] = b"\xA5" * (size - 0x500000)
    assert validate_x220_region_preservation(original, bytes(generated), regions) == ("gbe", "ifd", "me")

    generated[0x3000] ^= 0x01
    with pytest.raises(ValueError, match="me region differs"):
        validate_x220_region_preservation(original, bytes(generated), regions)


def test_windows_build_fails_before_subprocess_or_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import autoloader.rom_builder.artifact_engine as module

    def forbidden(*args, **kwargs):
        raise AssertionError("Windows guard must not spawn a subprocess")

    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(module.subprocess, "run", forbidden)
    output = tmp_path / "artifacts"
    result = ArtifactEngine().build(
        BuildRequest("x200-libreboot-4mb", tmp_path / "not-a-checkout", output)
    )
    assert result.status is BuildStatus.BLOCKED
    assert result.code == "LINUX_BUILD_ENV_REQUIRED"
    assert "No command was run" in result.message
    assert result.artifacts == ()
    assert not output.exists()
    assert result.manifest is None


def test_source_inspection_requires_exact_commit_tree_cleanliness_and_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoloader.rom_builder.artifact_engine as module

    source = tmp_path / "source"
    source.mkdir()
    file_data = {"target.cfg": b"payload_seabios=\"y\"\n", "config": b"pinned-mode-config\n"}
    pinned_files = tuple(
        PinnedFile(name, hashlib.sha256(data).hexdigest())
        for name, data in file_data.items()
    )
    for name, data in file_data.items():
        (source / name).write_bytes(data)
    pin = SourcePin(
        name="test-pin",
        repository_url="https://example.invalid/lbmk.git",
        revision="a" * 40,
        tree_id="b" * 40,
        files=pinned_files,
    )
    recipe = replace(X200_4MB, source=pin)
    monkeypatch.setattr(module, "ARTIFACT_RECIPES", {recipe.recipe_id: recipe})
    git_values = {
        ("remote", "get-url", "origin"): pin.repository_url,
        ("rev-parse", "HEAD"): pin.revision,
        ("rev-parse", "HEAD^{tree}"): pin.tree_id,
        ("status", "--porcelain=v1", "--untracked-files=all"): "",
    }
    engine = ArtifactEngine()
    monkeypatch.setattr(engine, "_git_value", lambda _source, *args: git_values[args])

    audit = engine.inspect_source(recipe.recipe_id, source)
    assert audit.clean is True
    assert audit.revision == pin.revision
    assert audit.tree_id == pin.tree_id
    assert audit.pinned_file_hashes == tuple((item.path, item.sha256) for item in pinned_files)

    git_values[("remote", "get-url", "origin")] = "https://example.invalid/attacker.git"
    with pytest.raises(ValueError, match="origin"):
        engine.inspect_source(recipe.recipe_id, source)
    git_values[("remote", "get-url", "origin")] = pin.repository_url
    git_values[("rev-parse", "HEAD")] = "c" * 40
    with pytest.raises(ValueError, match="revision/tree"):
        engine.inspect_source(recipe.recipe_id, source)
    git_values[("rev-parse", "HEAD")] = pin.revision
    git_values[("status", "--porcelain=v1", "--untracked-files=all")] = "?? local-change"
    with pytest.raises(ValueError, match="dirty"):
        engine.inspect_source(recipe.recipe_id, source)
    git_values[("status", "--porcelain=v1", "--untracked-files=all")] = ""
    (source / "config").write_bytes(b"changed config\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        engine.inspect_source(recipe.recipe_id, source)


def _populate_lbmk_output_fixture(source: Path, recipe) -> tuple[Path, ...]:
    target_config = source / "config" / "coreboot" / recipe.target
    config_dir = target_config / "config"
    config_dir.mkdir(parents=True)
    for stem in ("libgfxinit_corebootfb", "libgfxinit_txtmode"):
        (config_dir / stem).write_text("CONFIG_PAYLOAD_NONE=y\n", encoding="utf-8")
    keymap_dir = source / "config" / "data" / "grub" / "keymap"
    keymap_dir.mkdir(parents=True)
    from autoloader.rom_builder.artifact_engine import _PINNED_LBMK_KEYMAPS

    for name in _PINNED_LBMK_KEYMAPS:
        (keymap_dir / f"{name}.gkb").write_bytes(b"fixture keymap")
    target_dir = source / "bin" / recipe.target
    target_dir.mkdir(parents=True)
    outputs = lbmk_expected_output_paths(source, recipe)
    for relative in outputs:
        (source / relative).write_bytes(b"rom")
    return tuple(source / relative for relative in outputs)


def test_lbmk_validates_normal_multi_output_and_selects_only_exact_seabios(tmp_path: Path) -> None:
    recipe = replace(X200_4MB, expected_size_bytes=3)
    source = tmp_path / "lbmk"
    source.mkdir()
    engine = ArtifactEngine()
    engine._assert_no_stale_outputs(recipe, source)
    (source / "bin").mkdir()
    with pytest.raises(ValueError, match="Stale"):
        engine._assert_no_stale_outputs(recipe, source)
    (source / "bin").rmdir()

    outputs = _populate_lbmk_output_fixture(source, recipe)
    assert len(outputs) == 31
    assert len({path.name for path in outputs}) == 31
    assert select_exact_lbmk_artifact(source, recipe) == source / recipe.output_relative_path

    (source / "bin" / recipe.target / "unexpected.rom").write_bytes(b"rom")
    with pytest.raises(ValueError, match="inventory mismatch"):
        select_exact_lbmk_artifact(source, recipe)
    (source / "bin" / recipe.target / "unexpected.rom").unlink()

    selected = source / recipe.output_relative_path
    selected.unlink()
    with pytest.raises(ValueError, match="inventory mismatch"):
        select_exact_lbmk_artifact(source, recipe)
    selected.write_bytes(b"rom-too-large")
    with pytest.raises(ValueError, match="chip capacity"):
        select_exact_lbmk_artifact(source, recipe)


def test_lbmk_rejects_casefolded_duplicate_output_candidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recipe = replace(X200_4MB, expected_size_bytes=3)
    source = tmp_path / "lbmk"
    source.mkdir()
    outputs = _populate_lbmk_output_fixture(source, recipe)
    target_dir = source / "bin" / recipe.target
    selected = source / recipe.output_relative_path
    original_iterdir = Path.iterdir

    def iter_with_collision(path: Path):
        entries = list(original_iterdir(path))
        if path == target_dir:
            entries.append(selected.with_name(selected.name.upper()))
        return iter(entries)

    monkeypatch.setattr(Path, "iterdir", iter_with_collision)
    with pytest.raises(ValueError, match="colliding ROM filenames"):
        select_exact_lbmk_artifact(source, recipe)
    assert len(outputs) == 31


def test_lbmk_rejects_symlinked_expected_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recipe = replace(X200_4MB, expected_size_bytes=3)
    source = tmp_path / "lbmk"
    source.mkdir()
    _populate_lbmk_output_fixture(source, recipe)
    selected = source / recipe.output_relative_path
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == selected or original_is_symlink(path),
    )
    with pytest.raises(ValueError, match="regular non-symlink"):
        select_exact_lbmk_artifact(source, recipe)


def test_source_and_output_roots_must_not_overlap(tmp_path: Path) -> None:
    from autoloader.rom_builder.artifact_engine import _require_disjoint_paths

    source = tmp_path / "checkout"
    nested_output = source / "artifacts"
    source.mkdir()
    with pytest.raises(ValueError, match="separate"):
        _require_disjoint_paths(source, nested_output)
    with pytest.raises(ValueError, match="separate"):
        _require_disjoint_paths(source / "nested", source)
    _require_disjoint_paths(source, tmp_path / "external-output")


def test_t430_build_request_is_official_handoff_only(tmp_path: Path) -> None:
    result = ArtifactEngine().build(
        BuildRequest("t430-1vyrain-upstream-handoff", tmp_path / "none", tmp_path / "output")
    )
    assert result.status is BuildStatus.HANDOFF_REQUIRED
    assert result.artifacts == ()
    assert result.manifest is None
    assert "official" in result.message.lower()
    assert not (tmp_path / "output").exists()


def test_unknown_recipe_never_selects_a_firmware_family(tmp_path: Path) -> None:
    result = ArtifactEngine().build(BuildRequest("coreboot-master-x230", tmp_path, tmp_path / "out"))
    assert result.status is BuildStatus.BLOCKED
    assert result.code == "UNKNOWN_ARTIFACT_RECIPE"
    assert not (tmp_path / "out").exists()


def test_manifest_is_immutable_and_hashes_canonical_content() -> None:
    manifest = ArtifactManifest(
        schema_version=1,
        operation_id="operation-1",
        created_at_utc="2026-10-10T00:00:00+00:00",
        recipe_id="x220-coreboot-8mb-seabios",
        workflow_id="x220-coreboot",
        status="failed",
        source_repository_url=COREBOOT_2609.repository_url,
        source_revision=COREBOOT_2609.revision,
        source_tree_id=COREBOOT_2609.tree_id,
        source_files=(),
        commands=(CommandRecord(("make", "olddefconfig"), 1, "logs/01.log", "a" * 64),),
        host_platform="test host",
        toolchain=(("make", "GNU Make"),),
        build_environment=(("LC_ALL", "C"),),
        container_image_digest=None,
        configuration_sha256="b" * 64,
        resolved_configuration_sha256=None,
        observed_chip_capacity_bytes=8 * 1024 * 1024,
        expected_regions=(),
        blob_sha256=(),
        artifacts=(),
        selected_artifact_relative_path=None,
        selected_artifact_sha256=None,
        failure_code="TEST_FAILURE",
        failure_detail="Fake command failure; no firmware build performed.",
        reproducibility=ReproducibilityRecord("not_attempted", None, None, "No compile run."),
    )
    assert len(manifest.sha256) == 64
    assert manifest.sha256 == hashlib.sha256(manifest.canonical_json().encode()).hexdigest()
    with pytest.raises(FrozenInstanceError):
        manifest.status = "complete"  # type: ignore[misc]
    with pytest.raises(ValueError, match="selected artifact"):
        replace(manifest, status="complete", failure_code=None, artifacts=())


def test_fake_command_failure_is_logged_and_never_returns_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import autoloader.rom_builder.artifact_engine as module

    run_dir = tmp_path / "run"
    logs = run_dir / "logs"
    logs.mkdir(parents=True)
    observed: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        observed.update(kwargs)
        return SimpleNamespace(returncode=2, stdout="", stderr="fake failure")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    commands: list[CommandRecord] = []
    with pytest.raises(RuntimeError, match="exit code 2"):
        ArtifactEngine()._run_logged(("fake-tool", "build"), tmp_path, logs, commands)
    assert len(commands) == 1
    assert commands[0].exit_code == 2
    assert Path(logs.parent / commands[0].log_relative_path).is_file()
    env = observed["env"]
    assert isinstance(env, dict)
    assert "MAKEFLAGS" not in env
    assert env["HOME"] == str(run_dir)


def test_fake_command_timeout_is_recorded_as_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import autoloader.rom_builder.artifact_engine as module

    logs = tmp_path / "run" / "logs"
    logs.mkdir(parents=True)

    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1, output="timeout")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    commands: list[CommandRecord] = []
    with pytest.raises(RuntimeError, match="timed out"):
        ArtifactEngine()._run_logged(("fake-tool", "build"), tmp_path, logs, commands)
    assert len(commands) == 1
    assert commands[0].exit_code == -1
