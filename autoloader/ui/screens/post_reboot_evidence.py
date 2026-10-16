"""Guided human observation intake routed through the shared controller."""
from __future__ import annotations

import json

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import Button, Checkbox, Footer, Header, Input, Select, Static
from textual.worker import Worker, get_current_worker

from autoloader.application_controller import (
    ApplicationAction,
    ApplicationActionResult,
    ApplicationController,
    ApplicationStatus,
)
from autoloader.ui.screens.controller_screen import ControllerScreen

_PROFILE_CHECKS: dict[str, tuple[tuple[str, str], ...]] = {
    "x200-libreboot": (
        ("cold_boot", "Cold boot"),
        ("display", "Display"),
        ("keyboard", "Keyboard"),
        ("pointing_device", "Pointing device"),
        ("storage", "Storage"),
        ("sleep_resume", "Sleep and resume"),
    ),
    "x220-coreboot": (
        ("cold_boot", "Cold boot"),
        ("display", "Display"),
        ("keyboard", "Keyboard"),
        ("pointing_device", "Pointing device"),
        ("storage", "Storage"),
        ("sleep_resume", "Sleep and resume"),
        ("ethernet", "Ethernet"),
    ),
    "t430-1vyrain": (
        ("cold_boot", "Cold boot"),
        ("display", "Display"),
        ("keyboard", "Keyboard"),
        ("pointing_device", "Pointing device"),
        ("storage", "Storage"),
        ("sleep_resume", "Sleep and resume"),
        ("advanced_bios_menu", "Advanced BIOS menu"),
        ("option_1_default_lenovo_bios", "Option 1 returned to the default Lenovo BIOS"),
        ("bios_identity", "BIOS identity"),
    ),
}


class PostRebootEvidenceScreen(ControllerScreen):
    """Collect operator-entered observations; local identity capture stays in the controller."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.worker: Worker | None = None
        self.operation_active = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(classes="container"):
            yield Static("Post-Reboot Evidence Intake", classes="header-text")
            yield Static(
                "Record what you observed after the operation. The shared controller captures fresh local DMI/BIOS. "
                "The report remains PENDING_HUMAN_REVIEW and does not prove a flash or authenticate observations.",
                classes="warning",
            )
            with VerticalScroll():
                yield Static("Terminal operation checkpoint", classes="section-title")
                yield Input(placeholder="Absolute path to terminal write/return checkpoint", id="post-checkpoint")
                yield Static("Operator and boot session", classes="section-title")
                yield Input(placeholder="Operator name", id="post-operator")
                yield Input(placeholder="Boot session ID (optional)", id="post-boot-session")
                yield Input(placeholder="If unavailable, explain why the boot session ID is unknown", id="post-boot-unknown")
                yield Static("Reboot confirmation", classes="section-title")
                yield Checkbox("I personally observed a reboot after the operation", id="post-reboot-attested")
                yield Input(placeholder="Reboot evidence reference", id="post-reboot-ref")
                yield Input(
                    placeholder="Reboot time in UTC (for example 2026-10-16T14:30:00Z)",
                    id="post-reboot-time",
                )
                profile_id = self.controller_app.controller.selected_profile_id
                if profile_id in _PROFILE_CHECKS:
                    if profile_id == "t430-1vyrain":
                        yield Static(
                            "T430: reboot evidence reference and UTC time must exactly match the consumed return checkpoint.",
                            classes="warning",
                        )
                    yield Static("Observed device checks", classes="section-title")
                    for check_id, label in _PROFILE_CHECKS[profile_id]:
                        yield Static(label, classes="post-check-label")
                        yield Select(
                            [("Pass", "pass"), ("Fail", "fail"), ("Unknown", "unknown")],
                            prompt="Choose result…",
                            allow_blank=True,
                            id=f"post-check-{check_id}-outcome",
                        )
                        yield Input(
                            placeholder=f"Evidence reference for {label.lower()}",
                            id=f"post-check-{check_id}-evidence",
                        )
                        yield Input(
                            placeholder=f"When you observed {label.lower()} (UTC, e.g. 2026-10-16T14:35:00Z)",
                            id=f"post-check-{check_id}-time",
                        )
                    if profile_id == "x200-libreboot":
                        yield Static("X200 Ethernet MAC preservation", classes="section-title")
                        yield Input(placeholder="Observed Ethernet MAC address", id="post-x200-observed-mac")
                        yield Input(
                            placeholder="Expected Ethernet MAC from the verified write record",
                            id="post-x200-expected-mac",
                        )
                        yield Input(placeholder="Evidence reference for the MAC observation", id="post-x200-mac-evidence")
                        yield Input(
                            placeholder="When you observed the MAC (UTC, e.g. 2026-10-16T14:35:00Z)",
                            id="post-x200-mac-time",
                        )
                else:
                    yield Static("Select an allowlisted V1 profile before recording observations.", classes="warning")
            yield Static("No post-reboot report has been recorded.", id="post-status")
            with Horizontal(classes="button-container"):
                yield Button("Capture Local Identity and Record Pending Observations", id="post-submit", variant="primary")
                yield Button("Back", id="post-back")
        yield Footer()

    def guard_navigation(self, destination: str) -> bool:
        if not self.operation_active:
            return False
        self.query_one("#post-status", Static).update(
            f"The local capture/report action is still running; wait before {destination}."
        )
        return True

    def _build_request_document(self, profile_id: str) -> str:
        operator = self.query_one("#post-operator", Input).value.strip()
        boot_session = self.query_one("#post-boot-session", Input).value.strip()
        unknown_reason = self.query_one("#post-boot-unknown", Input).value.strip()
        reboot_attested = self.query_one("#post-reboot-attested", Checkbox).value
        reboot_ref = self.query_one("#post-reboot-ref", Input).value.strip()
        reboot_time = self.query_one("#post-reboot-time", Input).value.strip()
        if not operator:
            raise ValueError("Enter the operator name.")
        if not boot_session and not unknown_reason:
            raise ValueError("Enter a boot session ID or explain why it is unavailable.")
        if not reboot_attested:
            raise ValueError("Confirm that you personally observed the post-operation reboot.")
        if not reboot_ref or not reboot_time:
            raise ValueError("Enter the reboot evidence reference and UTC time.")

        checks: dict[str, dict[str, object]] = {}
        for check_id, _label in _PROFILE_CHECKS[profile_id]:
            outcome = self.query_one(f"#post-check-{check_id}-outcome", Select).value
            evidence_ref = self.query_one(f"#post-check-{check_id}-evidence", Input).value.strip()
            observed_at = self.query_one(f"#post-check-{check_id}-time", Input).value.strip()
            if outcome not in {"pass", "fail", "unknown"}:
                raise ValueError(f"Choose a result for {_label.lower()}.")
            if not evidence_ref:
                raise ValueError(f"Enter an evidence reference for {_label.lower()}.")
            if not observed_at:
                raise ValueError(f"Enter when you observed {_label.lower()} in UTC.")
            checks[check_id] = {
                "outcome": outcome,
                "evidence_ref": evidence_ref,
                "observed_at_utc": observed_at,
            }

        if profile_id == "x200-libreboot":
            observed_mac = self.query_one("#post-x200-observed-mac", Input).value.strip()
            expected_mac = self.query_one("#post-x200-expected-mac", Input).value.strip()
            evidence_ref = self.query_one("#post-x200-mac-evidence", Input).value.strip()
            observed_at = self.query_one("#post-x200-mac-time", Input).value.strip()
            if not observed_mac or not expected_mac or not evidence_ref or not observed_at:
                raise ValueError("Enter both X200 Ethernet MAC addresses, the evidence reference, and UTC observation time.")
            checks["ethernet_mac"] = {
                "observed_mac": observed_mac,
                "expected_mac": expected_mac,
                "matches_expected": observed_mac.lower() == expected_mac.lower(),
                "evidence_ref": evidence_ref,
                "observed_at_utc": observed_at,
            }

        document = {
            "schema_version": 1,
            "operator": operator,
            "boot_session_id": boot_session or None,
            "boot_session_unknown_reason": None if boot_session else unknown_reason,
            "human_reboot_attested": True,
            "human_reboot_evidence_ref": reboot_ref,
            "human_reboot_observed_at_utc": reboot_time,
            "checks": checks,
        }
        return json.dumps(document, sort_keys=True, separators=(",", ":"))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "post-back":
            if self.guard_navigation("leaving this screen"):
                return
            self.app.pop_screen()
            return
        if event.button.id != "post-submit":
            return
        controller = self.controller_app.controller
        profile_id = controller.selected_profile_id
        checkpoint = self.query_one("#post-checkpoint", Input).value.strip()
        if profile_id not in _PROFILE_CHECKS:
            self.query_one("#post-status", Static).update("Select one allowlisted V1 profile first.")
            return
        if not checkpoint:
            self.query_one("#post-status", Static).update("Enter the terminal operation checkpoint path.")
            return
        try:
            request = self._build_request_document(profile_id)
        except ValueError as exc:
            self.query_one("#post-status", Static).update(str(exc))
            return
        self.operation_active = True
        self.query_one("#post-submit", Button).disabled = True
        self.query_one("#post-status", Static).update("Capturing local DMI/BIOS and validating the terminal checkpoint…")
        self.worker = self.submit_worker(controller, profile_id, checkpoint, request)

    @work(thread=True)
    def submit_worker(self, controller: ApplicationController, profile_id: str, checkpoint: str, request: str) -> None:
        worker = get_current_worker()
        try:
            result = controller.request_action(
                ApplicationAction.BOOT_CHECK,
                profile_id=profile_id,
                checkpoint_path=checkpoint,
                request_document=request,
            )
        except Exception as exc:
            result = ApplicationActionResult(
                ApplicationAction.BOOT_CHECK,
                ApplicationStatus.UNKNOWN,
                "POST_REBOOT_EVIDENCE_REJECTED",
                f"The controller rejected the intake: {exc}",
            )
        if self.is_mounted and not worker.is_cancelled:
            self.app.call_from_thread(self.show_result, result)

    def show_result(self, result: ApplicationActionResult) -> None:
        self.operation_active = False
        self.worker = None
        self.query_one("#post-submit", Button).disabled = False
        self.query_one("#post-status", Static).update(f"{result.code}: {result.message}\n" + "\n".join(result.evidence_refs))
