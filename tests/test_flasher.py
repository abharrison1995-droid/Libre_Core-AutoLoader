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


def test_degenerate_dump_check_does_not_alias_periodic_firmware_data():
    from autoloader.flasher.backup import is_degenerate_dump

    # The former sampled entropy implementation selected one byte from each
    # 64-byte period and falsely classified this valid nonblank pattern as blank.
    periodic_firmware = bytes(range(64)) * 131_072
    assert is_degenerate_dump(periodic_firmware) is False


def test_legacy_backup_manifest_corruption_is_not_reset(tmp_path):
    from autoloader.exceptions import BackupVerificationError
    from autoloader.flasher.backup import BackupManager

    manifest = tmp_path / "manifest.json"
    manifest.write_text("{broken", encoding="utf-8")
    manager = BackupManager(backup_dir=tmp_path)
    with pytest.raises(BackupVerificationError, match="refusing to reset or trust it"):
        manager.list_backups()


def test_flashrom_missing_rom_reports_failure_code_without_command(tmp_path, monkeypatch):
    from autoloader.flasher.flashrom import FlashromRunner, ProgrammerType

    runner = FlashromRunner()
    monkeypatch.setattr(runner, "run_command", lambda *args, **kwargs: pytest.fail("must not invoke tool"))
    result = runner.write_rom(ProgrammerType.CH341A, str(tmp_path / "missing.rom"))
    assert result.success is False
    assert result.code == "ROM_NOT_FOUND"


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


def test_onevy_prerequisites_fail_closed_without_pinned_bios_rules():
    from autoloader.exceptions import OnevyUnavailableError
    from autoloader.flasher.onevy import ExploitState, OnevyRainManager

    mgr = OnevyRainManager()
    assert mgr.verify_prerequisites("T480", "1.00") is False

    for model, version in (("x230", "2.60"), ("X230", "unknown"), ("T430", "2.64"), ("T430", "2.65")):
        with pytest.raises(OnevyUnavailableError, match="compatibility was not verified"):
            mgr.verify_prerequisites(model, version)
        assert mgr.state is ExploitState.UNAVAILABLE


def test_onevy_actions_never_simulate_unlock_or_flash():
    from autoloader.exceptions import OnevyUnavailableError
    from autoloader.flasher.onevy import ExploitState, OnevyRainManager

    mgr = OnevyRainManager()
    messages = []
    with pytest.raises(OnevyUnavailableError, match="did not execute an exploit"):
        mgr.execute_exploit(messages.append)
    assert messages and "unavailable" in messages[0].lower()
    assert "successfully" not in messages[0].lower()
    assert mgr.state is ExploitState.UNAVAILABLE

    for action in (mgr.prepare_exploit, mgr._run_exploit_binary, mgr.verify_unlock):
        with pytest.raises(OnevyUnavailableError):
            action()
        assert mgr.state is ExploitState.UNAVAILABLE

    # Even a stale or externally assigned UNLOCKED state cannot reach a writer.
    mgr.state = ExploitState.UNLOCKED
    with pytest.raises(OnevyUnavailableError, match="no write was attempted"):
        mgr.flash_rom("candidate.rom")
    assert mgr.state is ExploitState.UNAVAILABLE
    assert mgr.state is not ExploitState.COMPLETE


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


def test_restore_from_backup_is_quarantined_even_with_legacy_arguments(tmp_path, monkeypatch):
    from autoloader.flasher.backup import BackupManager
    from autoloader.flasher.flashrom import ProgrammerType

    mgr = BackupManager(backup_dir=tmp_path)
    backup_file = tmp_path / "test_backup.rom"
    backup_file.write_bytes(b"\x5a\xa5\xf0\x0f" * 1024 * 1024)

    def unexpected_write(*args, **kwargs):
        raise AssertionError("restore must not call any physical writer")
    monkeypatch.setattr(mgr.runner, "write_rom", unexpected_write)
    monkeypatch.setattr(mgr.runner, "run_command", unexpected_write)
    res = mgr.restore_from_backup(
        str(backup_file),
        ProgrammerType.INTERNAL,
        chip_name="W25Q64FV",
        extra_args=["-w", str(backup_file), "--write", str(backup_file)],
    )
    assert res is False


def test_legacy_single_chip_backup_is_disabled_without_hardware_reads(tmp_path, monkeypatch):
    from autoloader.exceptions import BackupUnavailableError
    from autoloader.flasher.backup import BackupManager
    from autoloader.flasher.flashrom import ProgrammerType

    mgr = BackupManager(backup_dir=tmp_path)

    def unexpected_read(*args, **kwargs):
        raise AssertionError("legacy backup must not read unobserved hardware")

    monkeypatch.setattr(mgr.runner, "read_rom", unexpected_read)
    with pytest.raises(BackupUnavailableError, match="no hardware was read"):
        mgr.create_backup("x200", ProgrammerType.CH341A, "bios", chip_name="W25Q32.V")


def test_internal_flasher_write_is_quarantined_before_probe_or_write(tmp_path, monkeypatch):
    from autoloader.flasher.internal import InternalFlasher

    flasher = InternalFlasher()
    test_rom = tmp_path / "test_bios.rom"
    test_rom.write_bytes(b"BIOS_DATA")

    def unexpected_probe(*args, **kwargs):
        raise AssertionError("quarantined internal writer must not probe hardware")
    def unexpected_write(*args, **kwargs):
        raise AssertionError("quarantined internal writer must not call a writer")
    monkeypatch.setattr(flasher.runner, "probe_chip", unexpected_probe)
    monkeypatch.setattr(flasher.runner, "write_rom", unexpected_write)
    monkeypatch.setattr(flasher.runner, "run_command", unexpected_write)
    assert flasher.is_flash_unlocked() is False
    res = flasher.flash_internal(str(test_rom), layout_region="bios")
    assert res.success is False
    assert res.code == "PHYSICAL_WRITES_DISABLED"

    # This API intentionally does not expose arbitrary command arguments.
    with pytest.raises(TypeError):
        flasher.flash_internal(str(test_rom), extra_args=["-w", str(test_rom)])  # type: ignore[call-arg]


def test_flashrom_writer_and_arbitrary_command_reject_write_flags(tmp_path, monkeypatch):
    from autoloader.flasher.flashrom import FlashromRunner, ProgrammerType
    runner = FlashromRunner()
    rom = tmp_path / "candidate.rom"
    rom.write_bytes(b"firmware")
    calls = []
    monkeypatch.setattr(runner, "run_command", lambda args, timeout=600: calls.append(args))

    result = runner.write_rom(
        ProgrammerType.CH341A,
        str(rom),
        extra_args=["--write", str(rom)],
    )
    assert result.success is False
    assert result.code == "PHYSICAL_WRITES_DISABLED"
    assert calls == []

    monkeypatch.undo()
    result = runner.run_command(["-p", ProgrammerType.CH341A.value, "-w", str(rom)])
    assert result[0] is False
    assert "disabled" in result[1].lower()


def test_cli_flash_command_is_quarantined(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from autoloader.__main__ import cli
    from autoloader.flasher.flashrom import FlashromRunner

    rom = tmp_path / "candidate.rom"
    rom.write_bytes(b"candidate firmware")

    def unexpected_write(*args, **kwargs):
        raise AssertionError("CLI flash must not call the physical writer")

    monkeypatch.setattr(FlashromRunner, "write_rom", unexpected_write)

    runner = CliRunner()
    result = runner.invoke(cli, ["flash", "--rom", str(rom)])
    assert result.exit_code != 0
    assert "PHYSICAL_FLASH_UNAVAILABLE" in (result.output or "")
    assert "no write was attempted" in (result.output or "")


def test_cli_backup_command_is_registered_but_unavailable_without_topology(monkeypatch):
    from click.testing import CliRunner
    from autoloader.__main__ import cli
    from autoloader.flasher.backup import BackupManager

    def unexpected_backup(*args, **kwargs):
        raise AssertionError("CLI backup must not read unobserved hardware")

    monkeypatch.setattr(BackupManager, "create_backup", unexpected_backup)

    result = CliRunner().invoke(cli, ["backup", "--model", "x200"])
    assert result.exit_code != 0
    assert "BACKUP_UNAVAILABLE" in (result.output or "")
    assert "No hardware was read" in (result.output or "")


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
