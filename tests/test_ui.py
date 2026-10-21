"""Unit and parity tests for the controller-driven CLI and Textual interface."""
import asyncio
import json
import sys
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Input, RadioButton, Select, Static, TextArea

from autoloader.app import AutoLoaderApp
from autoloader.application_controller import ApplicationAction, ApplicationController, ApplicationStatus
from autoloader.config import VERSION
from autoloader.ui.screens.backup_screen import BackupScreen
from autoloader.ui.screens.build_progress import BuildProgressScreen
from autoloader.ui.screens.evidence_import import EvidenceImportScreen
from autoloader.ui.screens.flash_guide import FlashGuideScreen
from autoloader.ui.screens.model_select import ModelSelectScreen
from autoloader.ui.screens.pathway_select import PathwaySelectScreen
from autoloader.ui.screens.post_reboot_evidence import PostRebootEvidenceScreen
from autoloader.ui.screens.rom_config import RomConfigScreen
from autoloader.ui.screens.verify_screen import VerifyScreen
from autoloader.ui.screens.x_workflow_action import XWorkflowActionScreen
from autoloader.ui.screens.write_confirmation import WriteConfirmationScreen
from autoloader.__main__ import cli


def test_app_registers_only_v1_workflow_screens():
    app = AutoLoaderApp()
    expected = {
        "welcome", "model_select", "pathway_select", "rom_config", "build_progress",
        "flash_guide", "backup_screen", "verify_screen", "evidence_import", "t430_handoff", "x_workflow_action",
        "write_confirmation", "post_reboot_evidence", "readiness_review",
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
            for name in ("model_select", "pathway_select", "rom_config", "build_progress", "flash_guide", "backup_screen", "verify_screen", "evidence_import", "t430_handoff", "write_confirmation", "post_reboot_evidence"):
                await app.push_screen(name)
                await pilot.pause()
                assert isinstance(app.screen, app.SCREENS[name])
                if name == "build_progress":
                    assert app.screen.query_one("#btn-next", Button).disabled
                    assert "not started" in app.screen.query_one("#build-status", Static).render().plain.lower()
                if name == "flash_guide":
                    assert app.screen.query_one("#btn-flash", Button).disabled
                    assert app.screen.query_one("#btn-verify", Button).disabled
                    flash_status = app.screen.query_one("#flash-status", Static).render().plain.lower()
                    assert "this screen does not perform" in flash_status
                    assert "inspect an operation journal" in flash_status
                    assert "has been performed" not in flash_status
                if name == "backup_screen":
                    assert app.screen.query_one("#btn-create", Button).disabled
                    assert app.screen.query_one("#btn-restore", Button).disabled
                if name == "verify_screen":
                    report = " ".join(widget.render().plain for widget in app.screen.query(Static))
                    assert "NOT_PERFORMED" in report
                    assert "unavailable" in report.lower()
                if name == "write_confirmation":
                    assert app.screen.query_one("#write-submit", Button).disabled
                    assert "no validated staged" in app.screen.query_one("#write-screen-status", Static).render().plain.lower()
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
            for widget_id in ("action-accept-backup", "action-build", "action-accept-artifact", "action-flash", "action-restore", "action-readback"):
                assert screen.query_one(f"#{widget_id}", Button).disabled
            assert screen.query_one("#action-boot", Button).disabled is False
            await pilot.click("#action-boot")
            await pilot.pause()
            assert isinstance(app.screen, PostRebootEvidenceScreen)
            app.pop_screen()
            await pilot.pause()
            await pilot.click("#action-import")
            await pilot.pause()
            assert isinstance(app.screen, EvidenceImportScreen)

    asyncio.run(runner())


def test_post_reboot_screen_submits_observations_through_shared_controller(monkeypatch, tmp_path):
    calls = []

    def fake_request(action, **kwargs):
        from autoloader.application_controller import ApplicationActionResult

        calls.append((action, kwargs))
        return ApplicationActionResult(
            action,
            ApplicationStatus.PENDING_HUMAN_REVIEW,
            "POST_REBOOT_EVIDENCE_PENDING_REVIEW",
            "Synthetic pending response only.",
            evidence_refs=("synthetic-report",),
        )

    controller = ApplicationController()
    controller.select_profile("x220-coreboot")
    monkeypatch.setattr(controller, "request_action", fake_request)

    async def runner():
        app = AutoLoaderApp(controller=controller)
        async with app.run_test() as pilot:
            await app.push_screen("post_reboot_evidence")
            await pilot.pause()
            app.screen.query_one("#post-checkpoint", Input).value = str(tmp_path / "write.json")
            app.screen.query_one("#post-operator", Input).value = "Test Operator"
            app.screen.query_one("#post-boot-session", Input).value = "boot-session-1"
            app.screen.query_one("#post-reboot-attested", Checkbox).value = True
            app.screen.query_one("#post-reboot-ref", Input).value = "reboot-observation-1"
            app.screen.query_one("#post-reboot-time", Input).value = datetime.now(timezone.utc).isoformat()
            for check_id in (
                "cold_boot", "display", "keyboard", "pointing_device", "storage", "sleep_resume", "ethernet"
            ):
                app.screen.query_one(f"#post-check-{check_id}-outcome", Select).value = "pass"
                app.screen.query_one(f"#post-check-{check_id}-evidence", Input).value = f"evidence-{check_id}"
                app.screen.query_one(f"#post-check-{check_id}-time", Input).value = datetime.now(
                    timezone.utc
                ).isoformat()
            await pilot.click("#post-submit")
            await pilot.pause(0.2)
            assert calls
            assert calls[0][0] is ApplicationAction.BOOT_CHECK
            assert calls[0][1]["profile_id"] == "x220-coreboot"
            assert calls[0][1]["checkpoint_path"] == str(tmp_path / "write.json")
            request = json.loads(calls[0][1]["request_document"])
            assert request["operator"] == "Test Operator"
            assert request["boot_session_id"] == "boot-session-1"
            assert request["human_reboot_attested"] is True
            assert request["human_reboot_evidence_ref"] == "reboot-observation-1"
            assert request["checks"]["ethernet"]["outcome"] == "pass"
            assert request["checks"]["cold_boot"]["evidence_ref"] == "evidence-cold_boot"
            assert request["checks"]["cold_boot"]["observed_at_utc"].endswith("+00:00")
            assert request["checks"]["ethernet"]["observed_at_utc"].endswith("+00:00")
            assert request["checks"]["cold_boot"]["observed_at_utc"] != ""
            assert len(app.screen.query("#post-request")) == 0
            assert len(app.screen.query("#post-checks-time")) == 0
            assert "POST_REBOOT_EVIDENCE_PENDING_REVIEW" in app.screen.query_one(
                "#post-status", Static
            ).render().plain

    asyncio.run(runner())


def test_readiness_review_screen_routes_checkpoint_through_shared_controller(monkeypatch, tmp_path):
    calls = []

    def fake_request(action, **kwargs):
        from autoloader.application_controller import ApplicationActionResult

        calls.append((action, kwargs))
        return ApplicationActionResult(
            action,
            ApplicationStatus.UNKNOWN,
            "REVIEW_INPUTS_COMPLETE",
            "Persisted inputs only; hardware readiness is not assessed.",
            reasons=("UNKNOWN: current device state",),
            evidence_refs=("checkpoint-sha256:synthetic",),
        )

    controller = ApplicationController()
    controller.select_profile("x220-coreboot")
    monkeypatch.setattr(controller, "request_action", fake_request)

    async def runner():
        app = AutoLoaderApp(controller=controller)
        async with app.run_test() as pilot:
            await app.push_screen("readiness_review")
            app.screen.query_one("#readiness-checkpoint", Input).value = str(tmp_path / "terminal.json")
            await pilot.click("#readiness-submit")
            await pilot.pause()
            assert calls == [(
                ApplicationAction.REVIEW_READINESS,
                {"profile_id": "x220-coreboot", "checkpoint_path": str(tmp_path / "terminal.json")},
            )]
            assert "REVIEW_INPUTS_COMPLETE" in app.screen.query_one("#readiness-status", Static).render().plain
            assert "hardware readiness is not assessed" in app.screen.query_one("#readiness-status", Static).render().plain

    asyncio.run(runner())


@pytest.mark.parametrize(
    ("profile_id", "required_checks", "has_mac_fields"),
    [
        (
            "x200-libreboot",
            ("cold_boot", "display", "keyboard", "pointing_device", "storage", "sleep_resume"),
            True,
        ),
        (
            "x220-coreboot",
            ("cold_boot", "display", "keyboard", "pointing_device", "storage", "sleep_resume", "ethernet"),
            False,
        ),
        (
            "t430-1vyrain",
            (
                "cold_boot", "display", "keyboard", "pointing_device", "storage", "sleep_resume",
                "advanced_bios_menu", "option_1_default_lenovo_bios", "bios_identity",
            ),
            False,
        ),
    ],
)
def test_post_reboot_screen_guides_model_specific_timestamped_observations(
    profile_id, required_checks, has_mac_fields
):
    async def runner():
        controller = ApplicationController()
        controller.select_profile(profile_id)
        app = AutoLoaderApp(controller=controller)
        async with app.run_test() as pilot:
            await app.push_screen("post_reboot_evidence")
            await pilot.pause()
            for check_id in required_checks:
                assert len(app.screen.query(f"#post-check-{check_id}-outcome")) == 1
                assert len(app.screen.query(f"#post-check-{check_id}-evidence")) == 1
                assert len(app.screen.query(f"#post-check-{check_id}-time")) == 1
            assert (len(app.screen.query("#post-x200-mac-time")) == 1) is has_mac_fields
            assert (len(app.screen.query("#post-x200-observed-mac")) == 1) is has_mac_fields
            assert "No post-reboot report has been recorded." in app.screen.query_one(
                "#post-status", Static
            ).render().plain

    asyncio.run(runner())


def test_post_reboot_cli_uses_shared_boot_check_controller(monkeypatch, tmp_path):
    import autoloader.__main__ as entrypoint

    calls = []

    class FakeController:
        def select_profile(self, profile_id):
            assert profile_id == "x220-coreboot"

        def request_action(self, action, **kwargs):
            from autoloader.application_controller import ApplicationActionResult

            calls.append((action, kwargs))
            return ApplicationActionResult(
                action,
                ApplicationStatus.PENDING_HUMAN_REVIEW,
                "POST_REBOOT_EVIDENCE_PENDING_REVIEW",
                "Synthetic pending response only.",
                evidence_refs=("synthetic-report",),
            )

    monkeypatch.setattr(entrypoint, "ApplicationController", FakeController)
    checkpoint = tmp_path / "terminal.json"
    checkpoint.write_text("synthetic checkpoint", encoding="utf-8")
    observations = tmp_path / "observations.json"
    observations.write_text('{"operator":"synthetic"}', encoding="utf-8")
    result = CliRunner().invoke(cli, [
        "post-reboot-check", "--profile", "x220-coreboot",
        "--checkpoint", str(checkpoint), "--observations", str(observations),
    ])

    assert result.exit_code == 0, result.output
    assert calls[0][0] is ApplicationAction.BOOT_CHECK
    assert calls[0][1]["profile_id"] == "x220-coreboot"
    assert calls[0][1]["checkpoint_path"] == checkpoint
    assert calls[0][1]["request_document"] == '{"operator":"synthetic"}'
    assert "POST_REBOOT_EVIDENCE_PENDING_REVIEW" in result.output


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
    for command in ("run", "detect", "import-evidence", "status", "resume-checkpoint", "inspect-checkpoint", "post-reboot-check", "review-readiness", "build-rom", "usb", "build-usb", "backup", "verify", "flash"):
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
    checkpoint = tmp_path / "workflow.json"
    checkpoint.write_text("{}", encoding="utf-8")
    request_file = tmp_path / "request.json"
    request_file.write_text("{}", encoding="utf-8")
    flash = CliRunner().invoke(cli, [
        "flash", "--profile", "x200-libreboot", "--checkpoint", str(checkpoint), "--request", str(request_file),
    ])
    assert flash.exit_code != 0 and "TTY_REQUIRED" in flash.output
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


def test_cli_flash_collects_live_tty_fields_and_routes_through_controller(tmp_path, monkeypatch):
    from autoloader import __main__ as entrypoint
    from autoloader.application_controller import ApplicationActionResult

    checkpoint = tmp_path / "staged.json"
    checkpoint.write_text("synthetic test checkpoint", encoding="utf-8")
    request_path = tmp_path / "request.json"
    request_path.write_text('{"version":1}', encoding="utf-8")
    calls = []

    class FakeController:
        def select_profile(self, profile):
            self.profile = profile

        def request_action(self, action, **kwargs):
            calls.append((action, kwargs))
            return ApplicationActionResult(
                action, ApplicationStatus.WRITE_VERIFIED, "WRITE_VERIFIED",
                "synthetic fake-controller result", evidence_refs=("synthetic-journal",),
            )

    monkeypatch.setattr(entrypoint, "_require_live_write_tty", lambda: None)
    monkeypatch.setattr(entrypoint, "ApplicationController", FakeController)
    result = CliRunner().invoke(cli, [
        "flash", "--profile", "x200-libreboot", "--checkpoint", str(checkpoint), "--request", str(request_path),
    ], input=(
        "Test Operator\nDMM-1\nboard VCC test point\n3300\n3300\n"
        "y\ny\ny\ny\ny\ny\ny\n\nOperator accepts loss of later OEM EC update route.\n"
    ))

    assert result.exit_code == 0, result.output
    assert "synthetic-journal" in result.output
    assert "Boot validation: NOT_PERFORMED" in result.output
    assert len(calls) == 1
    action, kwargs = calls[0]
    assert action is ApplicationAction.FLASH
    assert kwargs["profile_id"] == "x200-libreboot"
    assert kwargs["checkpoint_path"] == checkpoint
    assert kwargs["request_document"] == '{"version":1}'
    assert kwargs["frontend"] == "cli-tty"
    assert kwargs["hardware_setup"].actor == "Test Operator"
    assert kwargs["hardware_setup"].programmer_voltage_mv == 3300
    assert kwargs["hardware_setup"].chip_voltage_mv == 3300


def test_cli_t430_flash_rejected_before_external_spi_prompts(tmp_path, monkeypatch):
    from autoloader import __main__ as entrypoint

    checkpoint = tmp_path / "staged.json"
    request = tmp_path / "request.json"
    checkpoint.write_text("{}", encoding="utf-8")
    request.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(entrypoint, "_collect_live_write_setup", lambda: (_ for _ in ()).throw(AssertionError("T430 must not get SPI setup prompts")))
    result = CliRunner().invoke(cli, ["flash", "--profile", "t430-1vyrain", "--checkpoint", str(checkpoint), "--request", str(request)])
    assert result.exit_code != 0
    assert "PROFILE_WRITE_UNAVAILABLE" in result.output


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


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="positive approval/journal persistence requires POSIX directory fsync; production remains fail-closed on Windows",
)
def test_dedicated_tui_write_challenge_is_screen_bound_one_use_and_routes_shared_controller(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import autoloader.application_controller as controller_module
    import autoloader.workflow.write_approval as approval_module
    from autoloader.application_controller import ProfileStatus
    from autoloader.workflow.write_approval import ConfirmationChannel, create_write_approval_challenge
    from autoloader.workflow.write_service import WriteExecutionResult, WriteExecutionStatus
    from autoloader.workflow.x200_libreboot import X200ExternalSPIPlan
    from autoloader.workflow.types import CompatibilityDecision, OperationState, ProgrammerKind, VerificationStatus
    from tests.test_phase10a_write_approval import _prepared_write, _setup_for_intent

    plan, intent, _backup_service, _probe, backup, recovery, _transport, paths, operation_id = _prepared_write(tmp_path / "prepared")
    challenge = create_write_approval_challenge(intent, setup_attestation=_setup_for_intent(intent))
    checkpoint = tmp_path / "workflow.json"
    checkpoint.write_text("synthetic test workflow checkpoint", encoding="utf-8")
    proposal = X200ExternalSPIPlan(
        operation_id=operation_id,
        profile_id="x200-libreboot",
        chip_id=plan.topology.chips[0].chip_id,
        chip_name=backup.chips[0].observed_chip_name,
        capacity_bytes=plan.topology.chips[0].capacity_bytes,
        voltage_mv=3300,
        programmer_kind=ProgrammerKind.EXTERNAL_SPI,
        artifact_path=plan.artifact.path,
        artifact_sha256=plan.artifact.expected_sha256,
        artifact_manifest_path=paths["artifact_manifest_path"],
        artifact_manifest_sha256="a" * 64,
        backup_operation_id=backup.operation_id,
        recovery_record_id=recovery.record_id,
        operation_journal_path=paths["operation_journal_path"],
    )
    workflow = SimpleNamespace(
        result=SimpleNamespace(status="STAGED/HARDWARE_PENDING"),
        operation_plan=plan,
        backup_set=backup,
        recovery_readiness=recovery,
        proposed_external_spi_plan=proposal,
        operation_journal_path=paths["operation_journal_path"],
        operation_id=operation_id,
    )
    controller = ApplicationController()
    controller.select_profile("x200-libreboot")
    staged_status = ProfileStatus(
        profile_id="x200-libreboot",
        model_id="x200",
        firmware_family="libreboot",
        status=ApplicationStatus.STAGED_HARDWARE_PENDING,
        decision=CompatibilityDecision.ALLOWED,
        stage="STAGED",
        reasons=("synthetic staged fixture",),
        evidence_refs=("synthetic-only",),
        allowed_next_actions=(ApplicationAction.VIEW_EVIDENCE, ApplicationAction.FLASH),
        operation_id=operation_id,
        checkpoint_ref=str(checkpoint),
        operation_journal_ref=paths["operation_journal_path"],
    )
    monkeypatch.setattr(controller, "profile_status", lambda _profile_id=None: staged_status)
    monkeypatch.setattr(controller, "_reconstruct_x_workflow", lambda *_args, **_kwargs: workflow)
    monkeypatch.setattr(controller, "_write_approval_directory", lambda: tmp_path / "approvals")
    monkeypatch.setattr(approval_module, "_directory_fsync_supported", lambda: True)

    class FakeLedger:
        def __init__(self, _directory):
            pass

    receipts = []
    bridges = []
    write_started = threading.Event()
    release_service = threading.Event()

    class FakeWriteService:
        def __init__(self, **_kwargs):
            pass

        def execute(self, request, *, _tui_bridge=None):
            assert request.operation_id == operation_id
            assert _tui_bridge is not None
            bridges.append(_tui_bridge)
            confirmation = _tui_bridge.collect(challenge, plan.programmer.attested_by or "")
            receipts.append(confirmation)
            approval_module._consume_frontend_confirmation(confirmation)
            # Synthetic state-machine event only: no writer object or process
            # is called by this test. Hold the worker after WRITE_STARTED to
            # exercise app navigation while the receipt is already consumed.
            from autoloader.workflow.journal import OperationJournal
            journal = OperationJournal.load_checkpoint(request.operation_journal_path, expected_plan=request.plan)
            journal.append(
                action="synthetic-test-boundary",
                from_state=OperationState.BACKUP_VERIFIED,
                to_state=OperationState.WRITE_STARTED,
                code="WRITE_STARTED",
                message="synthetic UI lifecycle fixture; no writer call",
            )
            write_started.set()
            release_service.wait(timeout=8)
            journal.append(
                action="synthetic-test-boundary",
                from_state=OperationState.WRITE_STARTED,
                to_state=OperationState.WRITE_UNCERTAIN,
                code="SYNTHETIC_WRITE_UNCERTAIN",
                message="synthetic UI lifecycle fixture; no writer call",
            )
            return WriteExecutionResult(
                WriteExecutionStatus.UNCERTAIN,
                "SYNTHETIC_WRITE_UNCERTAIN",
                request.operation_id,
                "synthetic fake route; no hardware operation or writer call",
                OperationState.WRITE_UNCERTAIN,
                artifact_sha256=request.plan.artifact.expected_sha256,
                boot_status=VerificationStatus.NOT_PERFORMED,
            )

    monkeypatch.setattr(controller_module, "WriteApprovalLedger", FakeLedger)
    monkeypatch.setattr(controller_module, "ExternalSPIWriteService", FakeWriteService)
    app = AutoLoaderApp(controller=controller)

    async def runner():
        async with app.run_test(size=(100, 40)) as pilot:
            await app.push_screen(WriteConfirmationScreen())
            screen = app.screen
            screen.query_one("#write-checkpoint", Input).value = str(checkpoint)
            screen.query_one("#write-request", TextArea).load_text('{"version":1}')
            screen.query_one("#write-actor", Input).value = plan.programmer.attested_by or ""
            screen.query_one("#write-instrument", Input).value = plan.programmer.instrument_id or ""
            screen.query_one("#write-test-point", Input).value = plan.programmer.measurement_test_point or ""
            screen.query_one("#write-programmer-mv", Input).value = "3300"
            screen.query_one("#write-chip-mv", Input).value = "3300"
            screen.query_one("#write-interface-verified", Checkbox).value = True
            screen.query_one("#write-wiring-verified", Checkbox).value = True
            screen.query_one("#write-target-off", Checkbox).value = True
            screen.query_one("#write-ac-disconnected", Checkbox).value = True
            screen.query_one("#write-battery-removed", Checkbox).value = True
            screen.query_one("#write-pin1-verified", Checkbox).value = True
            screen.query_one("#write-disconnect-before-repower", Checkbox).value = True
            screen.query_one("#write-ec-waiver", Input).value = "Operator accepts loss of later OEM EC update route."
            await pilot.click("#write-submit")
            for _ in range(80):
                await pilot.pause(0.05)
                if challenge.exact_phrase in screen.query_one("#write-challenge-details", Static).render().plain:
                    break
            assert challenge.exact_phrase in screen.query_one("#write-challenge-details", Static).render().plain, (
                f"screen status={screen.query_one('#write-screen-status', Static).render().plain!r}; "
                f"challenge status={screen.query_one('#write-challenge-status', Static).render().plain!r}; "
                f"worker={screen.worker!r}"
            )
            preview = screen.query_one("#write-challenge-details", Static).render().plain
            assert challenge.artifact_sha256 in preview
            assert challenge.artifact_manifest_sha256 in preview
            assert challenge.setup_actor in preview
            assert challenge.setup_instrument_id in preview
            assert challenge.setup_test_point in preview
            assert f"EC decision={challenge.ec_update_decision}" in preview
            assert "waiver recorded at setup time" in preview
            assert f"programmer {challenge.programmer_voltage_mv} mV" in preview
            assert f"chip {challenge.chip_voltage_mv} mV" in preview
            assert f"interface verified {challenge.interface_verified}" in preview
            assert f"wiring verified {challenge.wiring_verified}" in preview
            assert screen.query_one("#write-actor", Input).disabled
            assert screen.query_one("#write-instrument", Input).disabled
            assert screen.query_one("#write-programmer-mv", Input).disabled
            bridge = bridges[0]
            with pytest.raises(approval_module.WriteApprovalError, match="different screen instance"):
                bridge._confirm_from_screen(object(), screen.mount_nonce, challenge.exact_phrase)
            await pilot.press("b")
            await pilot.click("#write-back")
            await pilot.press("q")
            await pilot.pause()
            assert app.screen is screen and screen.operation_active
            screen.query_one("#write-challenge-phrase", Input).value = "FLASH X200 incorrect"
            screen._confirm()
            assert "does not exactly match" in screen.query_one("#write-challenge-status", Static).render().plain
            screen.query_one("#write-challenge-phrase", Input).value = challenge.exact_phrase
            assert screen.query_one("#write-challenge-phrase", Input).value == challenge.exact_phrase
            assert not screen.query_one("#write-confirm", Button).disabled
            await pilot.pause()
            await pilot.click("#write-confirm")
            for _ in range(80):
                await pilot.pause(0.05)
                if write_started.is_set():
                    break
            assert write_started.is_set(), (
                f"screen status={screen.query_one('#write-screen-status', Static).render().plain!r}; "
                f"challenge status={screen.query_one('#write-challenge-status', Static).render().plain!r}; "
                f"receipts={len(receipts)}; bridge errors={bridges[0]._error!r}; "
                f"phrase={screen.query_one('#write-challenge-phrase', Input).value!r}; "
                f"expected={challenge.exact_phrase!r}; confirm disabled={screen.query_one('#write-confirm', Button).disabled}"
            )
            assert screen.operation_active
            await pilot.press("b")
            await pilot.press("q")
            await pilot.click("#write-back")
            assert app.screen is screen and screen.operation_active
            release_service.set()
            for _ in range(80):
                await pilot.pause(0.05)
                if not screen.operation_active:
                    break
            assert "SYNTHETIC_WRITE_UNCERTAIN" in screen.query_one("#write-screen-status", Static).render().plain
            assert len(receipts) == 1
            assert receipts[0].channel is ConfirmationChannel.TUI_EXPLICIT_ACTION
            with pytest.raises(approval_module.WriteApprovalError, match="already submitted"):
                bridge._confirm_from_screen(screen, screen.mount_nonce, challenge.exact_phrase)

    asyncio.run(runner())


def test_tui_cancel_or_unmount_wakes_actual_service_worker_without_writer_call(tmp_path, monkeypatch):
    import threading

    import autoloader.workflow.journal as journal_module
    import autoloader.workflow.write_approval as approval_module
    from tests.test_phase10b_write_service import _service_request

    monkeypatch.setattr(approval_module, "_directory_fsync_supported", lambda: True)
    monkeypatch.setattr(approval_module, "_sync_directory", lambda _path: None)
    monkeypatch.setattr(journal_module, "_directory_fsync_supported", lambda: True)
    monkeypatch.setattr(journal_module, "_sync_directory", lambda _path: None)
    service, request, writer, _readback = _service_request(tmp_path / "service", monkeypatch)

    async def runner():
        app = AutoLoaderApp()
        async with app.run_test() as pilot:
            for action in ("cancel", "unmount"):
                await app.push_screen(WriteConfirmationScreen())
                screen = app.screen
                bridge = approval_module._create_mounted_tui_confirmation_bridge(
                    screen, mount_nonce=screen.mount_nonce
                )
                errors = []

                def run_service():
                    try:
                        service.execute(request, _tui_bridge=bridge)
                    except Exception as exc:
                        errors.append(exc)

                thread = threading.Thread(target=run_service, daemon=True)
                thread.start()
                for _ in range(100):
                    await pilot.pause(0.05)
                    if "Type exactly:" in screen.query_one("#write-challenge-details", Static).render().plain:
                        break
                assert "Type exactly:" in screen.query_one("#write-challenge-details", Static).render().plain, (
                    f"screen status={screen.query_one('#write-screen-status', Static).render().plain!r}; "
                    f"challenge status={screen.query_one('#write-challenge-status', Static).render().plain!r}; "
                    f"worker alive={thread.is_alive()} errors={errors!r}"
                )
                if action == "cancel":
                    await pilot.click("#write-cancel")
                else:
                    app.pop_screen()
                    await pilot.pause(0.2)
                thread.join(timeout=4)
                assert not thread.is_alive()
                assert len(errors) == 1
                assert isinstance(errors[0], approval_module.WriteApprovalError)
                assert writer.calls == []

    asyncio.run(runner())


def test_tui_cancel_or_unmount_revokes_minted_receipt_before_service_consumption(tmp_path, monkeypatch):
    """A phrase click cannot race an unmount into consuming approval later."""
    import threading

    import autoloader.workflow.journal as journal_module
    import autoloader.workflow.write_approval as approval_module
    from tests.test_phase10b_write_service import _service_request

    monkeypatch.setattr(approval_module, "_directory_fsync_supported", lambda: True)
    monkeypatch.setattr(approval_module, "_sync_directory", lambda _path: None)
    monkeypatch.setattr(journal_module, "_directory_fsync_supported", lambda: True)
    monkeypatch.setattr(journal_module, "_sync_directory", lambda _path: None)
    service, request, writer, _readback = _service_request(tmp_path / "service", monkeypatch)
    original_consume = approval_module._consume_frontend_confirmation
    consume_entered = threading.Event()
    release_consume = threading.Event()

    def gated_consume(confirmation):
        if confirmation.channel.value == "tui-explicit-action":
            consume_entered.set()
            if not release_consume.wait(timeout=5):
                raise AssertionError("test did not release the receipt-consumption gate")
        original_consume(confirmation)

    monkeypatch.setattr(approval_module, "_consume_frontend_confirmation", gated_consume)

    async def runner():
        app = AutoLoaderApp()
        async with app.run_test(size=(100, 40)) as pilot:
            for action in ("cancel", "unmount"):
                await app.push_screen(WriteConfirmationScreen())
                screen = app.screen
                bridge = approval_module._create_mounted_tui_confirmation_bridge(
                    screen, mount_nonce=screen.mount_nonce
                )
                errors = []

                def run_service():
                    try:
                        service.execute(request, _tui_bridge=bridge)
                    except Exception as exc:
                        errors.append(exc)

                thread = threading.Thread(target=run_service, daemon=True)
                thread.start()
                for _ in range(100):
                    await pilot.pause(0.05)
                    if "Type exactly:" in screen.query_one("#write-challenge-details", Static).render().plain:
                        break
                assert "Type exactly:" in screen.query_one("#write-challenge-details", Static).render().plain
                challenge = screen._challenge
                assert challenge is not None
                screen.query_one("#write-challenge-phrase", Input).value = challenge.exact_phrase
                await pilot.click("#write-confirm")
                for _ in range(100):
                    await pilot.pause(0.05)
                    if consume_entered.is_set():
                        break
                assert consume_entered.is_set(), "service did not reach receipt consumption"
                if action == "cancel":
                    await pilot.click("#write-cancel")
                else:
                    app.pop_screen()
                    await pilot.pause(0.1)
                release_consume.set()
                thread.join(timeout=4)
                assert not thread.is_alive()
                assert len(errors) == 1
                assert isinstance(errors[0], approval_module.WriteApprovalError)
                assert writer.calls == []
                consume_entered.clear()
                release_consume.clear()

    asyncio.run(runner())


def test_staged_flash_action_opens_dedicated_confirmation_screen(monkeypatch):
    from autoloader.application_controller import ProfileStatus
    from autoloader.workflow.types import CompatibilityDecision

    controller = ApplicationController()
    controller.select_profile("x200-libreboot")
    status = ProfileStatus(
        profile_id="x200-libreboot",
        model_id="x200",
        firmware_family="libreboot",
        status=ApplicationStatus.STAGED_HARDWARE_PENDING,
        decision=CompatibilityDecision.ALLOWED,
        stage="STAGED",
        reasons=("synthetic staged test status",),
        evidence_refs=("synthetic fixture",),
        allowed_next_actions=(ApplicationAction.VIEW_EVIDENCE, ApplicationAction.FLASH),
        operation_id="synthetic-operation",
        checkpoint_ref="C:/synthetic/workflow.json",
        operation_journal_ref="C:/synthetic/operation.json",
    )
    monkeypatch.setattr(controller, "profile_status", lambda _profile_id=None: status)

    async def runner():
        app = AutoLoaderApp(controller=controller)
        async with app.run_test() as pilot:
            await app.push_screen("pathway_select")
            await pilot.pause()
            assert app.screen.query_one("#action-flash", Button).disabled is False
            await pilot.click("#action-flash")
            await pilot.pause()
            assert isinstance(app.screen, WriteConfirmationScreen)
            assert app.screen.query_one("#write-submit", Button).disabled is False

    asyncio.run(runner())
