
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


def test_coreboot_builder_kconfig_sanitization(tmp_path, monkeypatch):
    from autoloader.rom_builder.coreboot_builder import CorebootBuilder
    from autoloader.rom_builder.blob_extractor import BlobSet

    builder = CorebootBuilder(workspace_dir=tmp_path)
    src_dir = tmp_path / "coreboot"
    src_dir.mkdir(parents=True, exist_ok=True)
    builder.source_dir = src_dir
    monkeypatch.setattr(builder, "_run_cmd", lambda cmd, cwd=None: None)

    config = ROMConfiguration(
        model_id="x230",
        firmware_base=FirmwareBase.COREBOOT,
        payload=PayloadType.SEABIOS,
    )

    desc_file = tmp_path / "desc.bin"
    desc_file.write_bytes(b"IFD")
    blob_set = BlobSet(descriptor_path=desc_file)

    builder.generate_config(config, blob_set)
    config_file = src_dir / ".config"
    assert config_file.exists()
    content = config_file.read_text(encoding="utf-8")
    assert "CONFIG_BOARD_LENOVO_X230=y" in content
    assert "CONFIG_HAVE_IFD_BIN=y" in content


def test_blob_extractor_invalid_ifd_rejection(tmp_path):
    from autoloader.rom_builder.blob_extractor import BlobExtractor, BlobSet
    extractor = BlobExtractor(workspace_dir=tmp_path)

    corrupt_desc = tmp_path / "corrupt_descriptor.bin"
    corrupt_desc.write_bytes(b"\x00" * 4096)  # Missing 0x5aa5f00f magic

    blob_set = BlobSet(descriptor_path=corrupt_desc, me_path=None)
    assert extractor.validate_blobs(blob_set, requires_me=False) is False


