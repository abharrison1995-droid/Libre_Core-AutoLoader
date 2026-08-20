import pytest
import hashlib
from pathlib import Path
from autoloader.flasher.backup import BackupManager

def test_backup_verification_matching_hashes(tmp_path):
    backup_mgr = BackupManager(backup_dir=tmp_path)

    file1 = tmp_path / "dump1.rom"
    file2 = tmp_path / "dump2.rom"
    data = b"\xFF" * 8192 + b"BIOS_SAMPLE_DATA"
    file1.write_bytes(data)
    file2.write_bytes(data)

    is_match, sha256_hash = backup_mgr.verify_backup_pair(file1, file2)
    assert is_match is True
    assert sha256_hash == hashlib.sha256(data).hexdigest()

def test_backup_verification_mismatch_fails(tmp_path):
    backup_mgr = BackupManager(backup_dir=tmp_path)

    file1 = tmp_path / "dump1.rom"
    file2 = tmp_path / "dump2.rom"
    file1.write_bytes(b"DATA_ONE")
    file2.write_bytes(b"DATA_TWO_CORRUPTED")

    is_match, _ = backup_mgr.verify_backup_pair(file1, file2)
    assert is_match is False


def test_backup_verification_degenerate_blank_fails(tmp_path):
    """Test that all-0xFF or all-0x00 matching backups are rejected as degenerate."""
    backup_mgr = BackupManager(backup_dir=tmp_path)

    file1 = tmp_path / "blank1.rom"
    file2 = tmp_path / "blank2.rom"
    file1.write_bytes(b"\xFF" * 16384)
    file2.write_bytes(b"\xFF" * 16384)

    is_match, _ = backup_mgr.verify_backup_pair(file1, file2)
    assert is_match is False

    zero1 = tmp_path / "zero1.rom"
    zero2 = tmp_path / "zero2.rom"
    zero1.write_bytes(b"\x00" * 16384)
    zero2.write_bytes(b"\x00" * 16384)

    is_match, _ = backup_mgr.verify_backup_pair(zero1, zero2)
    assert is_match is False


def test_flash_verifier_structure(tmp_path):
    from autoloader.flasher.verify import FlashVerifier, IFD_MAGIC
    verifier = FlashVerifier()

    # Valid IFD structure
    valid_ifd_rom = tmp_path / "valid_ifd.rom"
    ifd_data = (b"\xFF" * 16) + IFD_MAGIC + (b"VALID_DESCRIPTOR_PAYLOAD" * 300)
    valid_ifd_rom.write_bytes(ifd_data)
    assert verifier.check_rom_structure(valid_ifd_rom) is True

    # Degenerate blank rom
    blank_rom = tmp_path / "blank.rom"
    blank_rom.write_bytes(b"\xFF" * 8192)
    assert verifier.check_rom_structure(blank_rom) is False

    # Degenerate zero rom
    zero_rom = tmp_path / "zero.rom"
    zero_rom.write_bytes(b"\x00" * 8192)
    assert verifier.check_rom_structure(zero_rom) is False


def test_onevy_supported_models():
    from autoloader.flasher.onevy import OnevyRainManager
    mgr = OnevyRainManager()
    assert mgr.verify_prerequisites("x230", "2.60") is True
    assert mgr.verify_prerequisites("X230", "2.60") is True
    assert mgr.verify_prerequisites("T430", "2.64") is True
    assert mgr.verify_prerequisites("T480", "1.00") is False


def test_ec_mods_battery_keyboard_compatibility():
    from autoloader.ec_mod.battery import BatteryWhitelistMod
    from autoloader.ec_mod.keyboard import ClassicKeyboardMod

    bat_x230 = BatteryWhitelistMod("X230")
    assert bat_x230.is_compatible() is True
    assert bat_x230.configure_patch() == "make patch_enable_battery"

    bat_t400 = BatteryWhitelistMod("t400")
    assert bat_t400.is_compatible() is False

    kbd_x230 = ClassicKeyboardMod("x230")
    assert kbd_x230.is_compatible() is True
    assert kbd_x230.configure_patch() == "make patch_enable_keyboard"

    kbd_t480 = ClassicKeyboardMod("t480")
    assert kbd_t480.is_compatible() is False


def test_winflash_reversion_error_alias():
    from autoloader.reversion.winflash import ReversionError
    from autoloader.exceptions import BIOSReversionError

    assert issubclass(ReversionError, BIOSReversionError)
    assert ReversionError is BIOSReversionError


def test_flashrom_missing_file_validation(tmp_path):
    from autoloader.flasher.flashrom import FlashromRunner, ProgrammerType
    runner = FlashromRunner()
    
    non_existent = tmp_path / "non_existent_bios.rom"
    write_res = runner.write_rom(ProgrammerType.INTERNAL, str(non_existent))
    assert write_res.success is False
    assert "not found" in write_res.output.lower()

    verify_res = runner.verify_rom(ProgrammerType.INTERNAL, str(non_existent))
    assert verify_res.success is False
    assert "not found" in verify_res.output.lower()


def test_is_admin_check():
    from autoloader.utils import is_admin
    # is_admin returns a boolean without throwing an unhandled exception
    res = is_admin()
    assert isinstance(res, bool)


def test_internal_flasher_check_prerequisites(monkeypatch):
    from autoloader.flasher.internal import InternalFlasher

    flasher = InternalFlasher()
    
    # Non-admin fails prerequisites
    monkeypatch.setattr("autoloader.flasher.internal.is_admin", lambda: False)
    assert flasher.check_prerequisites() is False

    # Admin passes prerequisites
    monkeypatch.setattr("autoloader.flasher.internal.is_admin", lambda: True)
    assert flasher.check_prerequisites() is True


def test_flashrom_runner_timeout_handling(monkeypatch):
    import subprocess
    from autoloader.flasher.flashrom import FlashromRunner
    runner = FlashromRunner()

    def mock_run_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=600)

    monkeypatch.setattr(subprocess, "run", mock_run_timeout)
    success, log, duration = runner.run_command(["flashrom", "-p", "internal"])
    assert success is False
    assert "timed out" in log.lower()


def test_ivprep_sha256_verification(tmp_path, monkeypatch):
    import hashlib
    import zipfile
    from autoloader.reversion.ivprep import IVPrepManager
    from autoloader.exceptions import BIOSReversionError

    # Create mock zip
    zip_file = tmp_path / "mock_ivprep.zip"
    with zipfile.ZipFile(zip_file, "w") as zf:
        zf.writestr("IVprep-master/downgrade.bat", "echo Downgrade")

    zip_bytes = zip_file.read_bytes()
    real_hash = hashlib.sha256(zip_bytes).hexdigest()

    class MockResponse:
        def __init__(self, data):
            self.data = data
            self.offset = 0
        def read(self, n=8192):
            chunk = self.data[self.offset:self.offset+n]
            self.offset += len(chunk)
            return chunk
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: MockResponse(zip_bytes))

    # Correct hash passes
    mgr_valid = IVPrepManager(model="x230", target_version="2.60", expected_sha256=real_hash)
    mgr_valid.config.working_dir = tmp_path / "work_valid"
    extracted = mgr_valid.download()
    assert extracted.exists()

    # Mismatched hash raises error
    mgr_invalid = IVPrepManager(model="x230", target_version="2.60", expected_sha256="0000000000000000000000000000000000000000000000000000000000000000")
    mgr_invalid.config.working_dir = tmp_path / "work_invalid"
    with pytest.raises(BIOSReversionError, match="SHA256 mismatch"):
        mgr_invalid.download()


def test_reversion_chain_model_forwarding():
    from autoloader.reversion.chains import ReversionChain, ReversionState
    
    chain = ReversionChain()
    chain.model = "t430"
    chain.current_bios = "2.81"
    chain.target_bios = "2.64"

    assert chain.model == "t430"
    assert chain.target_bios == "2.64"
    assert chain.state == ReversionState.DETECTING


def test_restore_from_backup_with_chip_name(tmp_path, monkeypatch):
    from autoloader.flasher.backup import BackupManager
    from autoloader.flasher.flashrom import FlashromRunner, ProgrammerType, FlashResult

    mgr = BackupManager(backup_dir=tmp_path)
    backup_file = tmp_path / "test_backup.rom"
    backup_file.write_bytes(b"\x5a\xa5\xf0\x0f" * 1024 * 1024)

    recorded_chip = []
    def mock_write(prog, path, chip_name=None, layout_region=None, extra_args=None):
        recorded_chip.append(chip_name)
        return FlashResult(True, chip_name, 8 * 1024 * 1024, 1.0, "Write success")

    def mock_verify(prog, path, chip_name=None, layout_region=None, extra_args=None):
        return FlashResult(True, chip_name, 8 * 1024 * 1024, 1.0, "Verify success")

    monkeypatch.setattr(mgr.runner, "write_rom", mock_write)
    monkeypatch.setattr(mgr.runner, "verify_rom", mock_verify)

    res = mgr.restore_from_backup(str(backup_file), ProgrammerType.INTERNAL, chip_name="W25Q64FV")
    assert res is True
    assert recorded_chip == ["W25Q64FV"]


def test_internal_flasher_layout_flags(tmp_path, monkeypatch):
    from autoloader.flasher.internal import InternalFlasher
    from autoloader.flasher.flashrom import ProgrammerType, FlashResult

    flasher = InternalFlasher()
    test_rom = tmp_path / "test_bios.rom"
    test_rom.write_bytes(b"BIOS_DATA")

    recorded_layout = []
    def mock_write(prog, path, chip_name=None, layout_region=None, extra_args=None):
        recorded_layout.append(layout_region)
        return FlashResult(True, "N25Q128", 16 * 1024 * 1024, 1.0, "Write OK")

    def mock_verify(prog, path, chip_name=None, layout_region=None, extra_args=None):
        recorded_layout.append(layout_region)
        return FlashResult(True, "N25Q128", 16 * 1024 * 1024, 1.0, "Verify OK")

    monkeypatch.setattr(flasher, "check_prerequisites", lambda: True)
    monkeypatch.setattr(flasher, "is_flash_unlocked", lambda: True)
    monkeypatch.setattr(flasher.runner, "write_rom", mock_write)
    monkeypatch.setattr(flasher.runner, "verify_rom", mock_verify)

    res = flasher.flash_internal(str(test_rom), layout_region="bios")
    assert res.success is True
    assert recorded_layout == ["bios"]

    v_res = flasher.verify_internal(str(test_rom), layout_region="bios")
    assert v_res.success is True
    assert recorded_layout == ["bios", "bios"]


def test_cli_programmer_buspirate_mapping(tmp_path):
    from click.testing import CliRunner
    from autoloader.__main__ import cli
    from autoloader.flasher.verify import IFD_MAGIC

    valid_rom = tmp_path / "valid.rom"
    valid_data = (b"\xFF" * 16) + IFD_MAGIC + (b"VALID_PAYLOAD_DESCRIPTOR" * 200) + (bytes(range(256)) * 50)
    valid_rom.write_bytes(valid_data)

    runner = CliRunner()
    result = runner.invoke(cli, ["flash", "--programmer", "buspirate_spi", "--rom", str(valid_rom)])
    assert "ValueError" not in (result.output or "")
    assert "buspirate_spi:dev=/dev/ttyUSB0" in (result.output or "")
    assert "Dry-run check complete" in (result.output or "")


def test_entropy_calculation_accuracy_and_speed():
    from autoloader.flasher.verify import calculate_entropy
    import math

    # Uniform random-like distribution across 256 bytes has theoretical max entropy of 8.0
    uniform_data = bytes(range(256)) * 1000
    entropy = calculate_entropy(uniform_data)
    assert math.isclose(entropy, 8.0, rel_tol=1e-3)

    # Monolithic single byte has entropy 0.0
    zero_data = b"\x00" * 5000
    assert calculate_entropy(zero_data) == 0.0


def test_thinkpad_ec_manager_model_resolution(monkeypatch, tmp_path):
    from autoloader.ec_mod.thinkpad_ec import ThinkPadECManager
    from autoloader.detection.model import DetectionResult
    from autoloader.detection.bios import BIOSInfo
    from autoloader.exceptions import SafetyError
    import pytest

    # Explicit model
    mgr = ThinkPadECManager(model="t430", working_dir=tmp_path)
    assert mgr.model == "t430"

    # Auto-detected model
    monkeypatch.setattr("autoloader.detection.model.ModelDetector.detect", lambda self: DetectionResult("x230", "ThinkPad X230", "2325", "PF12345", "high"))
    monkeypatch.setattr("autoloader.detection.bios.BIOSDetector.detect", lambda self, force_refresh=False: BIOSInfo("2.60", "2013-05-10", "Lenovo", True))
    mgr2 = ThinkPadECManager(working_dir=tmp_path)
    assert mgr2.model == "x230"

    # Unknown model raises SafetyError (no silent default)
    monkeypatch.setattr("autoloader.detection.model.ModelDetector.detect", lambda self: DetectionResult(None, None, None, None, "low"))
    with pytest.raises(SafetyError, match="Could not detect hardware model"):
        ThinkPadECManager(working_dir=tmp_path)
