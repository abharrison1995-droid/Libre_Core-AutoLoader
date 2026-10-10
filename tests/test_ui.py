"""Unit and parity tests for the controller-driven CLI and Textual interface."""
import asyncio
import json
import threading
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
from autoloader.ui.screens.x_workflow_action import XWorkflowActionScreen
from autoloader.__main__ import cli


def test_app_registers_only_v1_workflow_screens():
    app = AutoLoaderApp()
    expected = {
        "welcome", "model_select", "pathway_select", "rom_config", "build_progress",
        "flash_guide", "backup_screen", "verify_screen", "evidence_import", "t430_handoff", "x_workflow_action",
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
            for name in ("model_select", "pathway_select", "rom_config", "build_progress", "flash_guide", "backup_screen", "verify_screen", "evidence_import", "t430_handoff"):
                await app.push_screen(name)
                await pilot.pause()
                assert isinstance(app.screen, app.SCREENS[name])
                if name == "build_progress":
                    assert app.screen.query_one("#btn-next", Button).disabled
                    assert "not started" in app.screen.query_one("#build-status", Static).render().plain.lower()
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
            assert app.screen.query_one("#btn-prepare", Button).disabled is False
            await pilot.click("#btn-prepare")
            await pilot.pause()
            assert isinstance(app.screen, XWorkflowActionScreen)
            assert "no operator request has been submitted" in app.screen.query_one("#x-action-status", Static).render().plain.lower()

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
            for widget_id in ("action-accept-backup", "action-build", "action-accept-artifact", "action-flash", "action-restore", "action-readback", "action-boot"):
                assert screen.query_one(f"#{widget_id}", Button).disabled
            await pilot.click("#action-import")
            await pilot.pause()
            assert isinstance(app.screen, EvidenceImportScreen)

    asyncio.run(runner())


def test_pathway_refreshes_controller_status_after_child_workflow_screen_returns():
    from autoloader.workflow.types import DeviceEvidence

    async def runner():
        app = AutoLoaderApp()
        app.set_selected_profile("x200-libreboot")
        async with app.run_test() as pilot:
            await app.push_screen("pathway_select")
            await pilot.pause()
            pathway = app.screen
            assert "none" in pathway.query_one("#profile-evidence", Static).render().plain.lower()
            await pilot.click("#action-prepare")
            await pilot.pause()
            assert isinstance(app.screen, XWorkflowActionScreen)

            # Simulate a controller state change from the child action without
            # invoking any hardware adapter, then return to the parent screen.
            app.controller.import_device_evidence(
                "x200-libreboot",
                DeviceEvidence(raw_dmi_product_name="ThinkPad X200", raw_mtm="7455-ABC"),
                evidence_ref="candidate-after-child-action",
            )
            app.pop_screen()
            await pilot.pause()
            assert "candidate-after-child-action" in pathway.query_one(
                "#profile-evidence", Static
            ).render().plain

    asyncio.run(runner())


def test_xworkflow_screen_submits_explicit_typed_request_through_shared_controller(monkeypatch, tmp_path):
    from autoloader.application_controller import ApplicationActionResult

    app_controller = ApplicationController()
    app_controller.select_profile("x200-libreboot")
    calls = []

    def fake_request(action, **kwargs):
        calls.append((action, kwargs))
        return ApplicationActionResult(action, ApplicationStatus.NOT_PERFORMED, "FAKE_BLOCKED", "fake controller response")

    monkeypatch.setattr(app_controller, "request_action", fake_request)

    async def runner():
        app = AutoLoaderApp(controller=app_controller)
        async with app.run_test() as pilot:
            await app.push_screen(XWorkflowActionScreen(ApplicationAction.PREPARE))
            await pilot.pause()
            app.screen.query_one("#x-checkpoint", Input).value = str(tmp_path / "new-session.json")
            app.screen.query_one("#x-request", TextArea).text = '{"version":1,"purpose":"test fixture"}'
            await pilot.click("#x-submit")
            await pilot.pause(0.2)
            assert calls and calls[0][0] is ApplicationAction.PREPARE
            assert calls[0][1]["checkpoint_path"] == str(tmp_path / "new-session.json")
            assert calls[0][1]["request_document"] == '{"version":1,"purpose":"test fixture"}'
            assert "FAKE_BLOCKED" in app.screen.query_one("#x-action-status", Static).render().plain

    asyncio.run(runner())


def test_xworkflow_active_worker_blocks_back_key_and_quit_until_result_is_visible(monkeypatch, tmp_path):
    from autoloader.application_controller import ApplicationActionResult

    app_controller = ApplicationController()
    app_controller.select_profile("x200-libreboot")
    started = threading.Event()
    release = threading.Event()

    def slow_request(action, **kwargs):
        started.set()
        release.wait(timeout=10)
        return ApplicationActionResult(
            action, ApplicationStatus.NOT_PERFORMED, "FAKE_COMPLETE", "fake request result is visible"
        )

    monkeypatch.setattr(app_controller, "request_action", slow_request)

    async def runner():
        app = AutoLoaderApp(controller=app_controller)
        async with app.run_test() as pilot:
            await app.push_screen(XWorkflowActionScreen(ApplicationAction.PREPARE))
            await pilot.pause()
            screen = app.screen
            screen.query_one("#x-checkpoint", Input).value = str(tmp_path / "new-session.json")
            screen.query_one("#x-request", TextArea).text = '{"version":1,"purpose":"test fixture"}'
            await pilot.click("#x-submit")
            for _ in range(20):
                await pilot.pause(0.05)
                if started.is_set():
                    break
            assert started.is_set()
            assert screen.operation_active
            assert screen.worker is not None

            # Exercise the screen button and both app-level navigation bindings
            # while the controller request is deliberately held in its worker.
            await pilot.click("#x-back")
            assert app.screen is screen
            assert "still running" in screen.query_one("#x-action-status", Static).render().plain.lower()
            await pilot.press("b")
            assert app.screen is screen
            assert "still running" in screen.query_one("#x-action-status", Static).render().plain.lower()
            await pilot.press("q")
            assert app.is_running
            assert app.screen is screen
            assert "still running" in screen.query_one("#x-action-status", Static).render().plain.lower()

            release.set()
            for _ in range(40):
                await pilot.pause(0.05)
                if not screen.operation_active:
                    break
            assert not screen.operation_active
            assert screen.worker is None
            assert "FAKE_COMPLETE" in screen.query_one("#x-action-status", Static).render().plain

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


def test_t430_stage_tui_and_cli_submit_same_controller_action(monkeypatch, tmp_path):
    from autoloader.reversion.t430_handoff import T430DetectionCapture
    from autoloader.workflow.types import DeviceEvidence

    capture = T430DetectionCapture(
        "capture-ui-cli",
        datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc),
        DeviceEvidence(
            raw_dmi_product_name="ThinkPad T430",
            raw_dmi_product_version="2342-ABC",
            raw_dmi_system_family="ThinkPad T430",
            raw_mtm="2342-ABC",
            raw_bios_version="Version: G1ET75WW (2.65 )",
            raw_bios_vendor="LENOVO",
            stock_lenovo_bios=True,
        ),
        "a" * 64,
    )
    monkeypatch.setattr(T430DetectionCapture, "detect_now", classmethod(lambda cls, **kwargs: capture))
    document = {
        "version": 1,
        "profile_id": "t430-1vyrain",
        "action": "approve-current-handoff",
        "actor": "operator",
        "explicit_approval": True,
        "approved_at": "2026-10-09T12:10:00+00:00",
        "preflight": {
            "attested_at": "2026-10-09T12:05:00+00:00",
            "bios_passwords_removed": True,
            "end_user_bios_update_enabled": True,
            "secure_rollback_prevention_disabled": True,
            "ac_connected": True,
            "battery_percent": 100,
            "uefi_booted": None,
            "secure_boot_disabled": None,
            "will_boot_1vyrain_from_direct_onboard_usb_without_dock_or_hub": None,
        },
    }
    text = json.dumps(document)

    async def runner():
        app = AutoLoaderApp(controller=ApplicationController())
        app.set_selected_profile("t430-1vyrain")
        tui_checkpoint = tmp_path / "tui-ivprep.json"
        async with app.run_test() as pilot:
            await app.push_screen("pathway_select")
            await pilot.pause()
            assert not app.screen.query_one("#action-t430-stage", Button).disabled
            app.screen.on_button_pressed(SimpleNamespace(button=app.screen.query_one("#action-t430-stage", Button)))
            await pilot.pause()
            assert app.screen.__class__.__name__ == "T430HandoffScreen"
            app.screen.query_one("#t430-checkpoint", Input).value = str(tui_checkpoint)
            app.screen.query_one("#t430-request", TextArea).text = text
            app.screen.on_button_pressed(SimpleNamespace(button=app.screen.query_one("#t430-submit", Button)))
            await pilot.pause()
            tui_result = app.screen.query_one("#t430-result", Static).render().plain
            assert "T430_IVPREP_HANDOFF_PENDING_RETURN" in tui_result
            assert tui_checkpoint.is_file()

    asyncio.run(runner())

    from autoloader.__main__ import cli
    request_file = tmp_path / "t430-request.json"
    request_file.write_text(text, encoding="utf-8")
    cli_checkpoint = tmp_path / "cli-ivprep.json"
    cli_result = CliRunner().invoke(cli, [
        "t430-stage", "--request", str(request_file), "--checkpoint", str(cli_checkpoint)
    ])
    assert cli_result.exit_code == 0, cli_result.output
    assert "T430_IVPREP_HANDOFF_PENDING_RETURN" in cli_result.output
    assert cli_checkpoint.is_file()


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
    assert "Missing option '--checkpoint'" in result.output
    build_help = CliRunner().invoke(cli, ["build-rom", "--help"])
    assert build_help.exit_code == 0
    assert "--request" in build_help.output
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
    checkpoint = tmp_path / "workflow.json"
    checkpoint.write_text("{}", encoding="utf-8")
    malformed_request = tmp_path / "request.json"
    malformed_request.write_text('{"version": 2}', encoding="utf-8")
    backup = CliRunner().invoke(cli, [
        "backup", "--profile", "x200-libreboot", "--checkpoint", str(checkpoint),
        "--request", str(malformed_request),
    ])
    assert backup.exit_code != 0 and "VERIFIED_BACKUP_REJECTED" in backup.output
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
