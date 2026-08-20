import pytest
from autoloader.database.models import (
    THINKPAD_MODELS,
    Generation,
    FirmwareBase,
    PayloadType,
    MEStatus,
    get_model,
    get_all_models,
)
from autoloader.database.bios_versions import (
    ONEVY_REQUIRED_VERSIONS,
    is_1vyrain_compatible,
    needs_bios_downgrade,
)
from autoloader.database.flash_chips import identify_chip_from_output
from autoloader.database.compatibility import (
    is_firmware_supported,
    is_payload_supported,
    validate_configuration,
)

def test_database_all_models_present():
    expected_models = [
        "x200", "x201", "x220", "x230", "x230t",
        "t400", "t420", "t430", "t430s", "t530", "w530", "t440p", "t480", "t500"
    ]
    all_models = get_all_models()
    assert len(all_models) == 14
    for model_id in expected_models:
        model = get_model(model_id)
        assert model is not None
        assert model.model_id == model_id
        assert model.name.startswith("ThinkPad")
        assert len(model.flash_chips) > 0

def test_xx30_1vyrain_compatibility():
    assert ONEVY_REQUIRED_VERSIONS.get("x230") == "2.60"
    assert ONEVY_REQUIRED_VERSIONS.get("t430") == "2.64"
    assert ONEVY_REQUIRED_VERSIONS.get("w530") == "2.58"
    assert ONEVY_REQUIRED_VERSIONS.get("t530") == "2.60"
    assert ONEVY_REQUIRED_VERSIONS.get("t430s") == "2.59"
    assert ONEVY_REQUIRED_VERSIONS.get("x230t") == "2.58"

    assert is_1vyrain_compatible("x230", "2.60") is True
    assert is_1vyrain_compatible("x230", "2.77") is False
    assert needs_bios_downgrade("x230", "2.77") is True
    assert needs_bios_downgrade("x230", "2.60") is False

def test_libreboot_and_canoeboot_rules():
    x201 = get_model("x201")
    assert x201.libreboot_support is False
    assert x201.coreboot_support is True

    t480 = get_model("t480")
    assert t480.canoeboot_support is False
    assert t480.libreboot_support is True
    assert t480.me_status == MEStatus.BOOT_GUARD

def test_chip_identification():
    flashrom_sample = 'Found Winbond flash chip "W25Q64FV" (8192 kB, SPI) on ch341a_spi.'
    chip = identify_chip_from_output(flashrom_sample)
    assert chip is not None
    assert chip.model_number == "W25Q64FV"
    assert chip.size_mb == 8

def test_compatibility_validator():
    errors = validate_configuration(
        model_id="x230",
        firmware=FirmwareBase.COREBOOT,
        payload=PayloadType.SEABIOS,
    )
    assert len(errors) == 0

    errors = validate_configuration(
        model_id="x201",
        firmware=FirmwareBase.LIBREBOOT,
        payload=PayloadType.SEABIOS,
    )
    assert len(errors) > 0
    assert any("libreboot" in err.lower() or "x201" in err.lower() for err in errors)


def test_model_alias_resolution():
    """Verify that variants and aliases correctly map to dedicated or base models."""
    assert get_model("x230t").model_id == "x230t"
    assert get_model("X230T").model_id == "x230t"
    assert get_model("x230i").model_id == "x230"
    assert get_model("t430s").model_id == "t430s"
    assert get_model("T430S").model_id == "t430s"
    assert get_model("t430i").model_id == "t430"
    assert get_model("t530").model_id == "t530"
    assert get_model("w530").model_id == "w530"
    assert get_model("x201s").model_id == "x201"
    assert get_model("x220t").model_id == "x220"

    with pytest.raises(KeyError):
        get_model("nonexistent_model_xyz")


def test_cached_all_models():
    """Verify that get_all_models returns the cached list."""
    from autoloader.database.models import _ALL_MODELS_CACHE
    models1 = get_all_models()
    models2 = get_all_models()
    assert models1 is _ALL_MODELS_CACHE
    assert models2 is _ALL_MODELS_CACHE
    assert len(models1) == 14


def test_middleton_database_lookup():
    from autoloader.database import get_middleton_info, MIDDLETON_MODS
    t500_mod = get_middleton_info("t500")
    assert t500_mod is not None
    assert "SATA-II" in t500_mod.features[0]
    assert get_middleton_info("x230") is None
