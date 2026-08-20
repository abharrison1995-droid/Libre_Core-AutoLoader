
import pytest
from autoloader.detection.model import ModelDetector
from autoloader.detection.bios import BIOSDetector, VersionComparison

def test_model_detector_machine_type_lookup():
    detector = ModelDetector()
    model_id = detector.machine_type_to_model("2325B11")
    assert model_id == "x230"

    model_id = detector.machine_type_to_model("20AN001KUS")
    assert model_id == "t440p"

    model_id = detector.machine_type_to_model("20L5CTO1WW")
    assert model_id == "t480"

def test_model_detector_product_name_parse():
    detector = ModelDetector()
    assert detector.parse_product_name("ThinkPad X230") == "x230"
    assert detector.parse_product_name("LENOVO ThinkPad T480 20L5") == "t480"
    assert detector.parse_product_name("ThinkPad T440p") == "t440p"

def test_bios_detector_compare():
    detector = BIOSDetector()
    assert detector.compare_version("2.60", "2.60") == VersionComparison.MATCHES
    assert detector.compare_version("2.77", "2.60") == VersionComparison.NEWER
    assert detector.compare_version("2.50", "2.60") == VersionComparison.OLDER

    # Test messy/unparsed strings
    assert detector.compare_version("G2ETB4WW (2.74)", "2.60") == VersionComparison.NEWER
    assert detector.compare_version("G2ET64WW (2.60)", "2.60") == VersionComparison.MATCHES
    assert detector.compare_version("G1ET73WW (1.43)", "2.60") == VersionComparison.OLDER

def test_bios_detector_1vyrain_check():
    detector = BIOSDetector()
    assert detector.check_1vyrain_compatible("x230", "2.60") is True
    assert detector.check_1vyrain_compatible("x230", "2.77") is False
    assert detector.needs_downgrade("x230", "2.77") is True
    assert detector.needs_downgrade("x230", "2.60") is False


def test_ec_detector_is_ec_mod_needed():
    from autoloader.detection.ec import ECDetector
    detector = ECDetector()
    assert detector.is_ec_mod_needed("x230", ["classic_keyboard"]) is True
    assert detector.is_ec_mod_needed("X230", ["battery_whitelist"]) is True
    assert detector.is_ec_mod_needed("t400", ["classic_keyboard"]) is False
    assert detector.is_ec_mod_needed("unknown_model", ["classic_keyboard"]) is False


def test_bios_detector_caching():
    detector = BIOSDetector()
    res1 = detector.detect()
    res2 = detector.detect()
    assert res1 is res2  # Returned cached instance

    res_refreshed = detector.detect(force_refresh=True)
    assert res_refreshed is not None


def test_bios_detector_compare_non_decimal_fail_closed():
    detector = BIOSDetector()
    # Raw IDs without parentheses or decimal formats fail closed with UNKNOWN
    assert detector.compare_version("G2ET64WW", "2.60") == VersionComparison.UNKNOWN
    assert detector.compare_version("Unknown", "2.60") == VersionComparison.UNKNOWN
    assert detector.compare_version(None, "2.60") == VersionComparison.UNKNOWN


def test_detection_subprocess_timeout_handling(monkeypatch):
    import subprocess
    from autoloader.detection.bios import BIOSDetector
    from autoloader.detection.ec import ECDetector
    from autoloader.detection.model import ModelDetector

    def mock_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 15))

    monkeypatch.setattr(subprocess, "run", mock_timeout)

    bios_det = BIOSDetector()
    assert bios_det._run_subprocess(["dmidecode"]) == ""

    ec_det = ECDetector()
    assert ec_det._run_subprocess(["dmidecode"]) == ""

    model_det = ModelDetector()
    assert model_det._run_subprocess(["wmic"]) == ""


def test_model_detector_tablet_product_name():
    detector = ModelDetector()
    assert detector.parse_product_name("ThinkPad X230 Tablet") == "x230t"
    assert detector.parse_product_name("LENOVO ThinkPad X230t") == "x230t"


def test_bios_detector_windows_wmic_parsing(monkeypatch):
    detector = BIOSDetector()

    # Mock WMIC /format:list output
    mock_wmic_list = (
        "Manufacturer=LENOVO\n"
        "ReleaseDate=20190822000000.000000+000\n"
        "SMBIOSBIOSVersion=G2ETB4WW (2.74 )\n"
    )
    monkeypatch.setattr(detector, "_run_subprocess", lambda cmd: mock_wmic_list)
    info = detector.detect_windows()
    assert info.vendor == "LENOVO"
    assert info.version == "2.74"
    assert info.date == "20190822000000.000000+000"
    assert info.is_stock_lenovo is True


def test_database_bios_versions_helpers():
    from autoloader.database.bios_versions import downgrade_needed, is_1vyrain_compatible

    assert is_1vyrain_compatible("x230", "2.60") is True
    assert is_1vyrain_compatible("x230", "G2ET64WW (2.60)") is True
    assert is_1vyrain_compatible("x230", "2.77") is False

    assert downgrade_needed("x230", "2.77") is True
    assert downgrade_needed("x230", "G2ETA6WW (2.77)") is True
    assert downgrade_needed("x230", "2.60") is False
    assert downgrade_needed("x230", "2.50") is False
    # Fail closed on unknown
    assert downgrade_needed("x230", "Unknown") is True


