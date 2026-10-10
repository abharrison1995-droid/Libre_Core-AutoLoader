"""Unit tests for UI screens and application components."""
import asyncio
import pytest
from textual.screen import Screen
from textual.widgets import Button, Static

from autoloader.app import AutoLoaderApp
from autoloader.ui.screens.welcome import WelcomeScreen
from autoloader.ui.screens.model_select import ModelSelectScreen
from autoloader.ui.screens.pathway_select import PathwaySelectScreen
from autoloader.ui.screens.rom_config import RomConfigScreen
from autoloader.ui.screens.ec_config import EcConfigScreen
from autoloader.ui.screens.build_progress import BuildProgressScreen
from autoloader.ui.screens.flash_guide import FlashGuideScreen
from autoloader.ui.screens.backup_screen import BackupScreen
from autoloader.ui.screens.verify_screen import VerifyScreen


def test_all_nine_screens_instantiate_cleanly():
    """Verify that all 9 UI screens instantiate cleanly without error."""
    screens = [
        WelcomeScreen(),
        ModelSelectScreen(),
        PathwaySelectScreen(),
        RomConfigScreen(),
        EcConfigScreen(),
        BuildProgressScreen(),
        FlashGuideScreen(),
        BackupScreen(),
        VerifyScreen(),
    ]
    assert len(screens) == 9
    for s in screens:
        assert isinstance(s, Screen)


def test_app_screens_registry():
    """Verify that AutoLoaderApp registers all 9 required screens."""
    app = AutoLoaderApp()
    expected_screen_keys = {
        "welcome",
        "model_select",
        "pathway_select",
        "rom_config",
        "ec_config",
        "build_progress",
        "flash_guide",
        "backup_screen",
        "verify_screen",
    }
    assert set(app.SCREENS.keys()) == expected_screen_keys
    assert app.SCREENS["welcome"] is WelcomeScreen
    assert app.SCREENS["model_select"] is ModelSelectScreen
    assert app.SCREENS["pathway_select"] is PathwaySelectScreen
    assert app.SCREENS["rom_config"] is RomConfigScreen
    assert app.SCREENS["ec_config"] is EcConfigScreen
    assert app.SCREENS["build_progress"] is BuildProgressScreen
    assert app.SCREENS["flash_guide"] is FlashGuideScreen
    assert app.SCREENS["backup_screen"] is BackupScreen
    assert app.SCREENS["verify_screen"] is VerifyScreen


def test_all_screens_mount_and_compose():
    """Verify all 9 screens mount, compose widgets, and render inside the app lifecycle."""
    async def _runner():
        app = AutoLoaderApp()
        async with app.run_test() as pilot:
            for screen_name, screen_cls in app.SCREENS.items():
                await app.push_screen(screen_name)
                await pilot.pause()
                assert isinstance(app.screen, screen_cls)
                app.pop_screen()
                await pilot.pause()

    asyncio.run(_runner())


def test_hardware_screens_remain_fail_closed_without_operation_results():
    async def _runner():
        app = AutoLoaderApp()
        async with app.run_test() as pilot:
            await app.push_screen("build_progress")
            await pilot.pause()
            assert app.screen.query_one("#btn-next", Button).disabled

            await app.push_screen("backup_screen")
            await pilot.pause()
            assert app.screen.query_one("#btn-create", Button).disabled

            await app.push_screen("flash_guide")
            await pilot.pause()
            assert app.screen.query_one("#btn-flash", Button).disabled
            assert app.screen.query_one("#btn-verify", Button).disabled
            flash_status = str(app.screen.query_one("#flash-unavailable", Static).render())
            assert "No flash operation has been run" in flash_status

            await app.push_screen("verify_screen")
            await pilot.pause()
            report = " ".join(str(widget.render()) for widget in app.screen.query(Static))
            assert "NOT PERFORMED" in report
            assert "successfully flashed" not in report.lower()

    asyncio.run(_runner())


def test_app_state_initialization():
    app = AutoLoaderApp()
    assert hasattr(app, "selected_model_id")
    assert hasattr(app, "rom_config")
    assert hasattr(app, "detected_model")
    assert hasattr(app, "detected_bios")


def test_cli_detect_command():
    from click.testing import CliRunner
    from autoloader.__main__ import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["detect"])
    assert result.exit_code == 0
    assert "Hardware Detection" in result.output


def test_cli_registers_expected_commands():
    from click.testing import CliRunner
    from autoloader.__main__ import cli

    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    for command in ("run", "detect", "build-rom", "usb", "backup", "verify", "flash"):
        assert command in result.output


def test_cli_flash_does_not_accept_write_bypass(tmp_path):
    from click.testing import CliRunner
    from autoloader.__main__ import cli

    rom = tmp_path / "candidate.rom"
    rom.write_bytes(b"candidate firmware")
    result = CliRunner().invoke(cli, ["flash", "--rom", str(rom), "--write", "--yes"])
    assert result.exit_code != 0
    assert "No such option" in result.output


def test_cli_build_rom_validation():
    from click.testing import CliRunner
    from autoloader.__main__ import cli
    runner = CliRunner()
    # Test valid model config
    result = runner.invoke(cli, ["build-rom", "--model", "x230", "--base", "coreboot", "--payload", "seabios"])
    assert result.exit_code != 0
    assert "ROM Configuration" in result.output
    assert "ROM_BUILD_UNAVAILABLE" in result.output
    assert "no rom was produced" in result.output.lower()

    # Test unsupported model config
    bad_result = runner.invoke(cli, ["build-rom", "--model", "x201", "--base", "libreboot"])
    assert bad_result.exit_code != 0
    assert "Configuration Error" in bad_result.output


def test_cli_verify_delegates_to_shared_orchestrator(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from autoloader.__main__ import cli
    from autoloader.orchestrator import Orchestrator

    rom = tmp_path / "candidate.rom"
    rom.write_bytes(b"candidate firmware")
    calls = []
    monkeypatch.setattr(Orchestrator, "run_verify", lambda self, rom_path, programmer=None: calls.append(rom_path) or True)

    result = CliRunner().invoke(cli, ["verify", "--rom", str(rom)])
    assert result.exit_code == 0
    assert "Read-back comparison passed" in result.output
    assert calls == [rom]

