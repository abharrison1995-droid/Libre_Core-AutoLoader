
import pytest
import json
from pathlib import Path
from autoloader.database.models import FirmwareBase, PayloadType
from autoloader.rom_builder.configurator import ROMConfiguration, ROMConfigurator
from autoloader.exceptions import FirmwareNotSupportedError, PayloadNotSupportedError

def test_rom_configurator_valid_config():
    configurator = ROMConfigurator()
    config = ROMConfiguration(
        model_id="x230",
        firmware_base=FirmwareBase.COREBOOT,
        payload=PayloadType.SEABIOS,
        remove_wifi_whitelist=True,
        neuter_intel_me=True,
    )
    assert configurator.validate(config) is True
    summary = configurator.generate_summary(config)
    assert "x230" in summary.lower()
    assert "coreboot" in summary.lower()

def test_rom_configurator_unsupported_firmware_reject():
    configurator = ROMConfigurator()
    config = ROMConfiguration(
        model_id="x201",
        firmware_base=FirmwareBase.LIBREBOOT,
        payload=PayloadType.SEABIOS,
    )
    with pytest.raises(FirmwareNotSupportedError):
        configurator.validate(config)

def test_rom_config_json_export_import(tmp_path):
    configurator = ROMConfigurator()
    config = ROMConfiguration(
        model_id="t430",
        firmware_base=FirmwareBase.CANOEBOOT,
        payload=PayloadType.GRUB,
        remove_wifi_whitelist=True,
        neuter_intel_me=True,
    )
    json_path = tmp_path / "test_config.json"
    configurator.export_json(config, json_path)
    assert json_path.exists()

    imported = configurator.import_json(json_path)
    assert imported.model_id == "t430"
    assert imported.firmware_base == FirmwareBase.CANOEBOOT
    assert imported.payload == PayloadType.GRUB


def test_rom_config_json_import_value_format(tmp_path):
    configurator = ROMConfigurator()
    json_path = tmp_path / "test_config_value.json"
    json_path.write_text(json.dumps({
        "model_id": "X230",
        "firmware_base": "coreboot",
        "payload": "seabios",
        "remove_wifi_whitelist": True,
        "neuter_intel_me": True,
        "custom_bootsplash": None,
        "include_cpu_microcode": True,
        "use_native_vga_init": False,
        "ec_mod_keyboard": False,
        "ec_mod_battery": False,
        "me_cleaner_soft_disable": True,
        "me_cleaner_strip": True,
        "stock_bios_dump_path": None,
        "output_path": None,
    }), encoding="utf-8")

    imported = configurator.import_json(json_path)
    assert imported.model_id == "x230"
    assert imported.firmware_base == FirmwareBase.COREBOOT
    assert imported.payload == PayloadType.SEABIOS


def test_rom_config_uppercase_model_id_normalized():
    configurator = ROMConfigurator()
    config = ROMConfiguration(
        model_id="X230",
        firmware_base=FirmwareBase.COREBOOT,
        payload=PayloadType.SEABIOS,
    )
    assert configurator.validate(config) is True
    assert config.model_id == "x230"


def test_blob_extractor_conditional_me(tmp_path):
    from autoloader.rom_builder.blob_extractor import BlobExtractor, BlobSet
    extractor = BlobExtractor(workspace_dir=tmp_path)
    
    desc_file = tmp_path / "descriptor.bin"
    desc_file.write_bytes(b"\xFF" * 16 + b"\x5a\xa5\xf0\x0f" + b"\x00" * 4076)
    
    # BlobSet without ME
    blob_set_no_me = BlobSet(descriptor_path=desc_file, me_path=None)
    assert extractor.validate_blobs(blob_set_no_me, requires_me=False) is True
    assert extractor.validate_blobs(blob_set_no_me, requires_me=True) is False

    # BlobSet with valid ME
    me_file = tmp_path / "me.bin"
    me_file.write_bytes(b"INTEL_ME_IMAGE_DATA")
    blob_set_with_me = BlobSet(descriptor_path=desc_file, me_path=me_file)
    assert extractor.validate_blobs(blob_set_with_me, requires_me=True) is True


def test_prebuilt_manager_verification(tmp_path):
    import hashlib
    from autoloader.rom_builder.prebuilt import PrebuiltManager
    manager = PrebuiltManager(storage_dir=tmp_path)
    
    sample_rom = tmp_path / "sample.rom"
    sample_data = b"PREBUILT_ROM_DATA"
    sample_rom.write_bytes(sample_data)
    expected_hash = hashlib.sha256(sample_data).hexdigest()
    
    assert manager.verify_rom(sample_rom, expected_hash) is True
    assert manager.verify_rom(sample_rom, "wronghash123") is False
    assert manager.verify_rom(sample_rom, "a" * 63) is False
    assert not (tmp_path / "catalog-created-by-constructor").exists()


def test_prebuilt_manager_has_no_catalog_or_mutating_operations(tmp_path):
    from autoloader.rom_builder.prebuilt import PrebuiltManager
    from autoloader.exceptions import ROMBuildUnavailableError

    storage = tmp_path / "not-created"
    manager = PrebuiltManager(storage_dir=storage)
    assert not storage.exists()
    with pytest.raises(ROMBuildUnavailableError):
        manager.list_available("x200", "libreboot")
    with pytest.raises(ROMBuildUnavailableError):
        manager.download_rom("https://example.invalid/rom.rom", "0" * 64)
    with pytest.raises(ROMBuildUnavailableError):
        manager.inject_blobs(tmp_path, tmp_path / "rom.tar.xz")
    assert not storage.exists()


def test_config_atomic_save(tmp_path):
    from autoloader.config import Config
    cfg = Config()
    cfg.verbose = True
    save_path = tmp_path / "saved_config.json"
    cfg.save(save_path)
    assert save_path.exists()
    assert not save_path.with_suffix(".tmp").exists()

    loaded = json.loads(save_path.read_text(encoding="utf-8"))
    assert loaded["verbose"] is True


def test_coreboot_builder_generic_kconfig_is_quarantined(tmp_path):
    from autoloader.rom_builder.coreboot_builder import CorebootBuilder
    from autoloader.rom_builder.blob_extractor import BlobSet
    from autoloader.exceptions import ROMBuildUnavailableError

    builder = CorebootBuilder(workspace_dir=tmp_path)
    src_dir = tmp_path / "coreboot"
    src_dir.mkdir(parents=True, exist_ok=True)
    builder.source_dir = src_dir
    config = ROMConfiguration(
        model_id="x230",
        firmware_base=FirmwareBase.COREBOOT,
        payload=PayloadType.SEABIOS,
    )

    desc_file = tmp_path / "desc.bin"
    desc_file.write_bytes(b"IFD")
    blob_set = BlobSet(descriptor_path=desc_file)

    with pytest.raises(ROMBuildUnavailableError):
        builder.generate_config(config, blob_set)
    assert not (src_dir / ".config").exists()


def test_canoeboot_legacy_build_paths_are_quarantined(tmp_path, monkeypatch):
    from autoloader.rom_builder.canoeboot_builder import CanoebootBuilder
    from autoloader.exceptions import ROMBuildUnavailableError
    import subprocess

    builder = CanoebootBuilder(workspace_dir=tmp_path)
    config = ROMConfiguration(
        model_id="t430",
        firmware_base=FirmwareBase.CANOEBOOT,
        payload=PayloadType.GRUB,
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no subprocess allowed")),
    )
    for operation in (
        lambda: builder.clone_source(),
        lambda: builder.configure_build(config),
        lambda: builder.inject_blobs(config),
        lambda: builder.compile(config),
        lambda: builder.get_rom_path(config),
    ):
        with pytest.raises(ROMBuildUnavailableError):
            operation()
    assert not builder.source_dir.exists()


def test_blob_extractor_invalid_ifd_rejection(tmp_path):
    from autoloader.rom_builder.blob_extractor import BlobExtractor, BlobSet
    extractor = BlobExtractor(workspace_dir=tmp_path)

    corrupt_desc = tmp_path / "corrupt_descriptor.bin"
    corrupt_desc.write_bytes(b"\x00" * 4096)  # Missing 0x5aa5f00f magic

    blob_set = BlobSet(descriptor_path=corrupt_desc, me_path=None)
    assert extractor.validate_blobs(blob_set, requires_me=False) is False


