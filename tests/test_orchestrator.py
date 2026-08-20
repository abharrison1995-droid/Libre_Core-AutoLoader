"""Unit tests for autoloader.orchestrator and TUI orchestration."""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from autoloader.orchestrator import Orchestrator, AppState
from autoloader.flasher.flashrom import ProgrammerType
from autoloader.flasher.backup import BackupResult
from autoloader.flasher.verify import VerificationResult
from autoloader.detection.model import DetectionResult


def test_app_state_defaults():
    state = AppState()
    assert state.model_id is None
    assert state.detected is None
    assert state.programmer == ProgrammerType.INTERNAL
    assert state.backup_dir == Path("backups")
    assert state.output_dir == Path("out")
    assert state.last_backup_result is None


def test_orchestrator_cancellation():
    state = AppState(model_id="x230")
    orch = Orchestrator(state)
    assert not orch.is_cancelled
    orch.cancel()
    assert orch.is_cancelled


def test_orchestrator_run_backup_no_model():
    state = AppState(model_id=None)
    error_called = []

    orch = Orchestrator(state, on_error=lambda err: error_called.append(err))
    res = orch.run_backup()
    assert res is None
    assert len(error_called) == 1
    assert "No model selected" in error_called[0]


def test_orchestrator_run_backup_success(tmp_path):
    state = AppState(model_id="x230", backup_dir=tmp_path)
    logs = []
    progress_records = []
    completed = []

    mock_result = BackupResult(
        path1=str(tmp_path / "b1.rom"),
        path2=str(tmp_path / "b2.rom"),
        sha256="abcd1234ef567890" * 4,
        match=True,
        chip_info="MX25L6405D (8MB, bottom)"
    )

    with patch("autoloader.orchestrator.BackupManager") as mock_bm_cls:
        mock_bm = mock_bm_cls.return_value
        mock_bm.create_backup.return_value = mock_result

        orch = Orchestrator(
            state,
            on_log=lambda msg: logs.append(msg),
            on_progress=lambda pct, lbl: progress_records.append((pct, lbl)),
            on_complete=lambda: completed.append(True)
        )

        res = orch.run_backup()
        assert res is not None
        assert res.match is True
        assert state.last_backup_result == mock_result
        assert len(completed) == 1
        assert any("Backup verified" in log for log in logs)


def test_orchestrator_run_backup_mismatch(tmp_path):
    state = AppState(model_id="x230", backup_dir=tmp_path)
    error_called = []

    mock_result = BackupResult(
        path1=str(tmp_path / "b1.rom"),
        path2=str(tmp_path / "b2.rom"),
        sha256="diff",
        match=False,
        chip_info="MX25L6405D (8MB, bottom)"
    )

    with patch("autoloader.orchestrator.BackupManager") as mock_bm_cls:
        mock_bm = mock_bm_cls.return_value
        mock_bm.create_backup.return_value = mock_result

        orch = Orchestrator(
            state,
            on_error=lambda err: error_called.append(err)
        )

        res = orch.run_backup()
        assert res is not None
        assert len(error_called) == 1
        assert "mismatch" in error_called[0].lower()


def test_orchestrator_run_verify_success(tmp_path):
    rom_file = tmp_path / "test.rom"
    rom_file.write_bytes(b"coreboot-image-content" * 100)
    state = AppState(model_id="x230")
    completed = []

    mock_verify_res = VerificationResult(
        verified=True,
        sha256_match=True,
        structure_valid=True,
        details="Verification successful"
    )

    with patch("autoloader.orchestrator.verify_flash", return_value=mock_verify_res):
        orch = Orchestrator(state, on_complete=lambda: completed.append(True))
        ok = orch.run_verify(rom_file)
        assert ok is True
        assert len(completed) == 1


def test_orchestrator_run_verify_failure(tmp_path):
    rom_file = tmp_path / "test.rom"
    rom_file.write_bytes(b"bad-rom-content" * 100)
    state = AppState(model_id="x230")
    errors = []

    mock_verify_res = VerificationResult(
        verified=False,
        sha256_match=False,
        structure_valid=False,
        details="Hash mismatch"
    )

    with patch("autoloader.orchestrator.verify_flash", return_value=mock_verify_res):
        orch = Orchestrator(state, on_error=lambda err: errors.append(err))
        ok = orch.run_verify(rom_file)
        assert ok is False
        assert len(errors) == 1
        assert "FAILED" in errors[0]


def test_autoloader_app_state_and_theme_toggle():
    from autoloader.app import AutoLoaderApp

    app = AutoLoaderApp()
    assert hasattr(app, "state")
    assert isinstance(app.state, AppState)

    # Test model synchronization
    app.set_selected_model("t430s")
    assert app.selected_model_id == "t430s"
    assert app.state.model_id == "t430s"

    # Test toggle dark
    initial_theme = app.theme
    app.action_toggle_dark()
    assert app.theme != initial_theme
