"""Unit tests for autoloader.orchestrator and TUI orchestration."""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from autoloader.orchestrator import Orchestrator, AppState
from autoloader.flasher.flashrom import ProgrammerType
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


def test_orchestrator_build_fails_closed_without_fake_completion(tmp_path):
    errors = []
    progress = []
    completed = []
    logs = []
    orch = Orchestrator(
        AppState(output_dir=tmp_path),
        on_error=errors.append,
        on_progress=lambda pct, label: progress.append((pct, label)),
        on_complete=lambda: completed.append(True),
        on_log=logs.append,
    )

    assert orch.run_build() is False
    assert len(errors) == 1
    assert "ROM_BUILD_UNAVAILABLE" in errors[0]
    assert "no rom was produced" in errors[0].lower()
    assert completed == []
    assert progress == [(0, "Build unavailable; no ROM was produced.")]
    assert not (tmp_path / "coreboot.rom").exists()
    assert not any("ROM image ready" in message for message in logs)


def test_orchestrator_flash_fails_closed_without_writer(tmp_path, monkeypatch):
    from autoloader.flasher.flashrom import FlashromRunner

    def unexpected_write(*args, **kwargs):
        raise AssertionError("the unavailable workflow must not reach flashrom")

    monkeypatch.setattr(FlashromRunner, "write_rom", unexpected_write)
    errors = []
    completed = []
    orch = Orchestrator(AppState(), on_error=errors.append, on_complete=lambda: completed.append(True))

    assert orch.run_flash(tmp_path / "candidate.rom") is False
    assert len(errors) == 1
    assert "no write was attempted" in errors[0].lower()
    assert "PHYSICAL_FLASH_UNAVAILABLE" in errors[0]
    assert completed == []


def test_orchestrator_run_backup_no_model():
    state = AppState(model_id=None)
    error_called = []

    orch = Orchestrator(state, on_error=lambda err: error_called.append(err))
    res = orch.run_backup()
    assert res is None
    assert len(error_called) == 1
    assert "No model selected" in error_called[0]


def test_orchestrator_run_backup_fails_closed_without_topology(tmp_path, monkeypatch):
    from autoloader.flasher.backup import BackupManager

    def unexpected_backup(*args, **kwargs):
        raise AssertionError("unobserved topology must not trigger a physical read")

    monkeypatch.setattr(BackupManager, "create_backup", unexpected_backup)
    errors = []
    progress = []
    completed = []
    state = AppState(model_id="x230", backup_dir=tmp_path)
    orch = Orchestrator(
        state,
        on_error=errors.append,
        on_progress=lambda pct, label: progress.append((pct, label)),
        on_complete=lambda: completed.append(True),
    )

    assert orch.run_backup() is None
    assert len(errors) == 1
    assert "BACKUP_UNAVAILABLE" in errors[0]
    assert "No hardware was read" in errors[0]
    assert progress == [(0, "Backup unavailable; no hardware was read.")]
    assert completed == []
    assert state.last_backup_result is None


def test_orchestrator_run_verify_is_quarantined_without_spi_read(tmp_path):
    rom_file = tmp_path / "test.rom"
    rom_file.write_bytes(b"coreboot-image-content" * 100)
    state = AppState(model_id="x230")
    errors = []
    progress = []
    completed = []
    with patch("autoloader.flasher.verify.verify_flash", side_effect=AssertionError("direct read attempted")):
        orch = Orchestrator(
            state,
            on_error=errors.append,
            on_progress=lambda pct, label: progress.append((pct, label)),
            on_complete=lambda: completed.append(True),
        )
        assert orch.run_verify(rom_file) is False
    assert len(errors) == 1 and "READBACK_NOT_PERFORMED" in errors[0]
    assert progress == [(0, "Read-back unavailable; not performed.")]
    assert completed == []


def test_app_controller_replaces_legacy_app_state_and_only_allows_v1_profiles():
    from autoloader.app import AutoLoaderApp

    app = AutoLoaderApp()
    app.set_selected_profile("x220-coreboot")
    assert app.controller.selected_profile_id == "x220-coreboot"
    with pytest.raises(ValueError):
        app.set_selected_model("x230")


def test_standalone_verify_flash_helper_is_importable():
    from autoloader.flasher.verify import FlashVerifier, verify_flash

    assert callable(verify_flash)
    assert callable(FlashVerifier.verify_flash)


def test_autoloader_app_state_and_theme_toggle():
    from autoloader.app import AutoLoaderApp

    app = AutoLoaderApp()
    assert hasattr(app, "controller")

    # Test model synchronization
    app.set_selected_model("t430")
    assert app.selected_profile_id == "t430-1vyrain"
    assert app.selected_model_id == "t430"

    # Test toggle dark
    initial_theme = app.theme
    app.action_toggle_dark()
    assert app.theme != initial_theme
