"""Unit and parity tests for the controller-driven CLI and Textual interface."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from click.testing import CliRunner
from textual.screen import Screen
from textual.widgets import Button, Input, RadioButton, Static, TextArea

from autoloader.app import AutoLoaderApp
from autoloader.application_controller import ApplicationAction, ApplicationController, ApplicationStatus
from autoloader.config import VERSION
from autoloader.ui.screens.backup_screen import BackupScreen
from autoloader.ui.screens.build_progress import BuildProgressScreen
from autoloader.ui.screens.evidence_import import EvidenceImportScreen
from autoloader.ui.screens.flash_guide import FlashGuideScreen
from autoloader.ui.screens.model_select import ModelSelectScreen
from autoloader.ui.screens.pathway_select import PathwaySelectScreen
from autoloader.ui.screens.rom_config import RomConfigScreen
from autoloader.ui.screens.verify_screen import VerifyScreen
from autoloader.__main__ import cli


def test_app_registers_only_v1_workflow_screens():
    app = AutoLoaderApp()
    expected = {
        "welcome", "model_select", "pathway_select", "rom_config", "build_progress",
        "flash_guide", "backup_screen", "verify_screen", "evidence_import",
    }
    assert set(app.SCREENS) == expected
    for screen in app.SCREENS.values():
        assert isinstance(screen(), Screen)
    assert "ec_config" not in app.SCREENS


def test_screens_mount_without_hardware_input_or_false_success():
    async def runner():
        controller = ApplicationController()
        app = AutoLoaderApp(controller=controller)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert not hasattr(controller, "capture") or controller.capture is None
            assert "not run" in str(app.screen.query_one("#detection-status", Static).render()).lower()

            app.set_selected_profile("x200-libreboot")
            for name in ("model_select", "pathway_select", "rom_config", "build_progress", "flash_guide", "backup_screen", "verify_screen", "evidence_import"):
                await app.push_screen(name)
                await pilot.pause()
                assert isinstance(app.screen, app.SCREENS[name])
                if name == "build_progress":
                    assert app.screen.query_one("#btn-next", Button).disabled
                    assert "unavailable" in app.screen.query_one("#build-status", Static).render().plain.lower()
                if name == "flash_guide":
                    assert app.screen.query_one("#btn-flash", Button).disabled
                    assert app.screen.query_one("#btn-verify", Button).disabled
                if name == "backup_screen":
                    assert app.screen.query_one("#btn-create", Button).disabled
                    assert app.screen.query_one("#btn-restore", Button).disabled
                if name == "verify_screen":
                    report = " ".join(widget.render().plain for widget in app.screen.query(Static))
                    assert "NOT_PERFORMED" in report
                    assert "unavailable" in report.lower()
                app.pop_screen()
                await pilot.pause()

    asyncio.run(runner())


def test_profile_selection_ui_contains_only_the_three_v1_profiles():
    async def runner():
        app = AutoLoaderApp()
        async with app.run_test() as pilot:
            await app.push_screen("model_select")
            await pilot.pause()
            labels = [str(button.label) for button in app.screen.query(RadioButton)]
            assert len(labels) == 3
            assert any("T430" in label and "1vyrain" in label for label in labels)
            assert any("X200" in label and "Libreboot" in label for label in labels)
            assert any("X220" in label and "Coreboot" in label for label in labels)
            assert not any("X200s" in label or "Tablet" in label for label in labels)
            assert not app.controller.capture

    asyncio.run(runner())


def test_generic_rom_base_payload_and_ec_chooser_paths_are_absent():
    async def runner():
        app = AutoLoaderApp()
        app.set_selected_profile("x200-libreboot")
        async with app.run_test() as pilot:
            await app.push_screen("rom_config")
            await pilot.pause()
            text = " ".join(widget.render().plain for widget in app.screen.query(Static)).lower()
            assert "no generic firmware-base, payload" in text
            assert "canoeboot" not in text
            ids = {widget.id for widget in app.screen.query("*")}
            assert "fw-base" not in ids
            assert "payload" not in ids
            assert app.screen.query_one("#btn-prepare", Button).disabled

    asyncio.run(runner())


def test_pathway_actions_and_status_come_from_controller():
    async def runner():
        app = AutoLoaderApp()
        app.set_selected_profile("x220-coreboot")
        async with app.run_test() as pilot:
            await app.push_screen("pathway_select")
            await pilot.pause()
            screen = app.screen
            assert "UNKNOWN" in screen.query_one("#profile-status", Static).render().plain
            assert "detect-device" in screen.query_one("#profile-next-actions", Static).render().plain
            assert screen.query_one("#action-detect", Button).disabled is False
            assert screen.query_one("#action-import", Button).disabled is False
            for widget_id in ("action-backup", "action-build", "action-flash", "action-restore", "action-readback", "action-boot"):
                assert screen.query_one(f"#{widget_id}", Button).disabled
            await pilot.click("#action-import")
            await pilot.pause()
            assert isinstance(app.screen, EvidenceImportScreen)

    asyncio.run(runner())


def test_evidence_import_screen_is_candidate_only_and_uses_controller():
    async def runner():
        controller = ApplicationController()
        controller.select_profile("x200-libreboot")
        app = AutoLoaderApp(controller=controller)
        async with app.run_test() as pilot:
            await app.push_screen("evidence_import")
            await pilot.pause()
            text = " ".join(widget.render().plain for widget in app.screen.query(Static)).lower()
            assert "unauthenticated candidate" in text
            assert "compatibility approval" in text
            assert app.controller.profile_status().decision.value == "unknown"

    asyncio.run(runner())


def test_evidence_import_screen_persists_an_explicit_candidate_record(tmp_path):
    async def runner():
        app = AutoLoaderApp()
        app.set_selected_profile("x200-libreboot")
        store_path = tmp_path / "tui-candidate.json"
        async with app.run_test() as pilot:
            await app.push_screen("evidence_import")
            await pilot.pause()
            screen = app.screen
            screen.query_one("#evidence-ref", Input).value = "inventory:ui"
            screen.query_one("#save-path", Input).value = str(store_path)
            screen.query_one("#evidence-json", TextArea).text = (
                '{"raw_dmi_product_name":"ThinkPad X200","raw_mtm":"7455-ABC"}'
            )
            screen.on_button_pressed(SimpleNamespace(button=screen.query_one("#btn-import", Button)))
            await pilot.pause()
            assert store_path.is_file()
            assert "saved" in screen.query_one("#evidence-result", Static).render().plain.lower()
            assert app.controller.profile_status().decision.value == "unknown"

    asyncio.run(runner())


def test_t430_resume_control_routes_a_checkpoint_path_through_controller(monkeypatch, tmp_path):
    from autoloader.reversion.t430_handoff import T430Assessment, T430HandoffSession, T430HandoffStage
    from autoloader.workflow.types import CompatibilityDecision

    fake_session = SimpleNamespace(
        operation_id="a" * 32,
        stage=T430HandoffStage.AWAITING_IVPREP_REDETECTION,
        assessment=T430Assessment(T430HandoffStage.IVPREP_REQUIRED, CompatibilityDecision.BLOCKED, 264, ("ivprep required",)),
        checkpoint_path=tmp_path / "pending.json",
        _device_fingerprint_sha256="b" * 64,
        _capture_id="capture-1",
        _observed_at=datetime.now(timezone.utc),
        _preflight=None,
        _media=None,
        _approved_actor="operator",
        _approval_timestamp=None,
    )
    monkeypatch.setattr(T430HandoffSession, "resume_from_checkpoint", lambda path, validator=None: fake_session)

    async def runner():
        app = AutoLoaderApp()
        app.set_selected_profile("t430-1vyrain")
        async with app.run_test() as pilot:
            await app.push_screen("pathway_select")
            await pilot.pause()
            resume = app.screen.query_one("#action-resume", Button)
            assert resume.disabled is False
            await pilot.click("#action-resume")
            await pilot.pause()
            assert isinstance(app.screen, EvidenceImportScreen)
            app.screen.query_one("#checkpoint-path", Input).value = str(tmp_path / "pending.json")
            app.screen.on_button_pressed(SimpleNamespace(button=app.screen.query_one("#btn-resume", Button)))
            await pilot.pause()
            result = app.screen.query_one("#evidence-result", Static).render().plain
            assert "T430_CHECKPOINT_RESUMED" in result
            assert app.controller.profile_status().operation_id == "a" * 32

    asyncio.run(runner())


def test_welcome_and_app_do_not_detect_implicitly(monkeypatch):
    from autoloader.detection.model import ModelDetector
    from autoloader.detection.bios import BIOSDetector

    def unexpected(*args, **kwargs):
        raise AssertionError("startup must not read hardware")

    monkeypatch.setattr(ModelDetector, "detect", unexpected)
    monkeypatch.setattr(BIOSDetector, "detect", unexpected)

    async def runner():
        app = AutoLoaderApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert "in development" in app.screen.query_one("#version", Static).render().plain.lower()
            assert VERSION in app.screen.query_one("#version", Static).render().plain
            assert "unavailable" in app.screen.query_one("#description", Static).render().plain.lower()
            assert "not run" in app.screen.query_one("#detection-status", Static).render().plain.lower()
            assert "detect device" in app.screen.query_one("#detection-status", Static).render().plain.lower()

    asyncio.run(runner())


def test_cli_commands_use_v1_profiles_and_remove_generic_build_options(tmp_path):
    help_result = CliRunner().invoke(cli, ["--help"])
    assert help_result.exit_code == 0
    for command in ("run", "detect", "import-evidence", "status", "resume-checkpoint", "inspect-checkpoint", "build-rom", "usb", "backup", "verify", "flash"):
        assert command in help_result.output

    result = CliRunner().invoke(cli, ["build-rom", "--profile", "x200-libreboot"])
    assert result.exit_code != 0
    assert "BUILD_UNAVAILABLE" in result.output
    assert "no firmware build was invoked" in result.output.lower()
    generic = CliRunner().invoke(cli, ["build-rom", "--profile", "x200-libreboot", "--base", "coreboot"])
    assert generic.exit_code != 0
    assert "no such option" in generic.output.lower()
    unsupported = CliRunner().invoke(cli, ["build-rom", "--profile", "x230"])
    assert unsupported.exit_code != 0
    assert "invalid value" in unsupported.output.lower()


def test_cli_readback_and_hardware_commands_fail_closed_with_shared_codes(monkeypatch, tmp_path):
    from autoloader.flasher.flashrom import FlashromRunner
    from autoloader.rom_builder.artifact_engine import ArtifactEngine
    from autoloader.flasher.backup_service import BackupSetService

    calls = []

    def unexpected(*args, **kwargs):
        calls.append("called")
        raise AssertionError("quarantined operation was reached")

    monkeypatch.setattr(FlashromRunner, "run_command", unexpected)
    monkeypatch.setattr(FlashromRunner, "read_rom", unexpected)
    monkeypatch.setattr(FlashromRunner, "write_rom", unexpected)
    monkeypatch.setattr(BackupSetService, "capture", unexpected)
    monkeypatch.setattr(ArtifactEngine, "build", unexpected)
    rom = tmp_path / "candidate.rom"
    rom.write_bytes(b"candidate only")

    verify = CliRunner().invoke(cli, ["verify", "--profile", "x200-libreboot"])
    assert verify.exit_code != 0 and "READBACK_NOT_PERFORMED" in verify.output
    flash = CliRunner().invoke(cli, ["flash", "--profile", "x200-libreboot", "--rom", str(rom)])
    assert flash.exit_code != 0 and "FLASH_UNAVAILABLE" in flash.output
    backup = CliRunner().invoke(cli, ["backup", "--profile", "x200-libreboot"])
    assert backup.exit_code != 0 and "BACKUP_UNAVAILABLE" in backup.output
    assert calls == []


def test_cli_and_tui_share_action_codes_and_authoritative_profile_status(monkeypatch):
    from autoloader import __main__ as entrypoint

    real_controller = ApplicationController()
    observed = []
    original_request = ApplicationController.request_action

    def record_request(self, action, **kwargs):
        observed.append(action)
        return original_request(self, action, **kwargs)

    monkeypatch.setattr(ApplicationController, "request_action", record_request)
    result = CliRunner().invoke(entrypoint.cli, ["verify", "--profile", "x220-coreboot"])
    assert result.exit_code != 0
    assert "READBACK_NOT_PERFORMED" in result.output
    assert observed == [ApplicationAction.READBACK]
    cli_status = real_controller.select_profile("x220-coreboot")
    assert cli_status.allowed_next_actions == real_controller.profile_status().allowed_next_actions


def test_cli_candidate_evidence_import_remains_unknown_and_strict(tmp_path):
    document = '{"raw_dmi_product_name":"ThinkPad X200","raw_mtm":"7455-ABC"}'
    evidence_file = tmp_path / "device.json"
    evidence_file.write_text(document, encoding="utf-8")
    store_file = tmp_path / "persisted-candidate.json"
    result = CliRunner().invoke(cli, [
        "import-evidence", "--profile", "x200-libreboot", "--file", str(evidence_file),
        "--save-to", str(store_file),
    ])
    assert result.exit_code == 0
    assert "candidate evidence" in result.output.lower()
    assert "does not authenticate" in result.output.lower()
    assert store_file.is_file()

    loaded = CliRunner().invoke(cli, [
        "status", "--profile", "x200-libreboot", "--evidence-file", str(store_file)
    ])
    assert loaded.exit_code == 0
    assert "unknown" in loaded.output.lower()
    assert "imported-device-evidence:sha256:" in loaded.output
    mismatch = CliRunner().invoke(cli, [
        "status", "--profile", "x220-coreboot", "--evidence-file", str(store_file)
    ])
    assert mismatch.exit_code != 0
    assert "does not match selected profile" in mismatch.output.lower()

    duplicate = CliRunner().invoke(cli, [
        "import-evidence", "--profile", "x200-libreboot", "--file", str(evidence_file),
        "--save-to", str(store_file),
    ])
    assert duplicate.exit_code != 0
    assert "refusing to overwrite" in duplicate.output.lower()

    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"is_compatible":true}', encoding="utf-8")
    bad_result = CliRunner().invoke(cli, [
        "import-evidence", "--profile", "x200-libreboot", "--file", str(invalid),
        "--save-to", str(tmp_path / "invalid-candidate.json"),
    ])
    assert bad_result.exit_code != 0
    assert "unsupported fields" in bad_result.output.lower()


def test_cli_t430_resume_uses_the_shared_controller_checkpoint_route(monkeypatch, tmp_path):
    from autoloader.reversion.t430_handoff import T430Assessment, T430HandoffSession, T430HandoffStage
    from autoloader.workflow.types import CompatibilityDecision

    fake_session = SimpleNamespace(
        operation_id="e" * 32,
        stage=T430HandoffStage.AWAITING_IVPREP_REDETECTION,
        assessment=T430Assessment(T430HandoffStage.IVPREP_REQUIRED, CompatibilityDecision.BLOCKED, 264, ("ivprep required",)),
        checkpoint_path=tmp_path / "pending.json",
        _device_fingerprint_sha256="f" * 64,
        _capture_id="capture-1",
        _observed_at=datetime.now(timezone.utc),
        _preflight=None,
        _media=None,
        _approved_actor="operator",
        _approval_timestamp=None,
    )
    monkeypatch.setattr(T430HandoffSession, "resume_from_checkpoint", lambda path, validator=None: fake_session)
    checkpoint = tmp_path / "pending.json"
    checkpoint.write_text("placeholder", encoding="utf-8")
    result = CliRunner().invoke(cli, [
        "resume-checkpoint", "--profile", "t430-1vyrain", "--checkpoint", str(checkpoint)
    ])
    assert result.exit_code == 0
    assert "T430_CHECKPOINT_RESUMED" in result.output
    assert "No hardware read, write" in result.output


def test_app_profile_selection_routes_through_controller_and_rejects_non_v1():
    app = AutoLoaderApp()
    app.set_selected_profile("x220-coreboot")
    assert app.controller.selected_profile_id == "x220-coreboot"
    assert app.selected_model_id == "x220"
    try:
        app.set_selected_model("x230")
    except ValueError:
        pass
    else:
        raise AssertionError("non-V1 model selection should fail")
