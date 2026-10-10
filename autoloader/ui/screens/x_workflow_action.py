"""Explicit request screen for the shared X200/X220 controller stages."""
from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, Static, TextArea
from textual.worker import Worker, get_current_worker

from autoloader.application_controller import (
    ApplicationAction,
    ApplicationActionResult,
    ApplicationController,
    ApplicationStatus,
)
from autoloader.ui.screens.controller_screen import ControllerScreen


_TITLES = {
    ApplicationAction.PREPARE: "Prepare X200/X220 Workflow",
    ApplicationAction.ACCEPT_BACKUP: "Capture and Verify Full-Chip Backup",
    ApplicationAction.BUILD: "Build Pinned Firmware Artifact",
    ApplicationAction.ACCEPT_ARTIFACT: "Reopen and Accept Completed Artifact",
    ApplicationAction.RESUME: "Resume X200/X220 Checkpoint",
}


class XWorkflowActionScreen(ControllerScreen):
    """Collect explicit typed request data, then invoke one facade action."""

    def __init__(self, action: ApplicationAction = ApplicationAction.PREPARE, **kwargs) -> None:
        super().__init__(**kwargs)
        if action not in _TITLES:
            raise ValueError("Unsupported X200/X220 screen action")
        self.action = action
        self.worker: Worker | None = None
        self._operation_active = False

    @property
    def operation_active(self) -> bool:
        return self._operation_active

    def guard_navigation(self, destination: str) -> bool:
        """Keep the result screen mounted until an explicit operation returns."""
        if not self._operation_active:
            return False
        self.query_one("#x-action-status", Static).update(
            f"This explicit backend action is still running; wait for its actual result before {destination}."
        )
        return True

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(classes="container"):
            yield Static(_TITLES[self.action], classes="header-text")
            yield Static("No operator request has been submitted.", id="x-action-status", classes="warning")
            with VerticalScroll():
                yield Static(
                    "This is an explicit operator action. Prepare may run local DMI and read-only SPI detection; "
                    "X220 also reads the original chip for its IFD map. Backup performs two full-chip reads. "
                    "Build invokes only the pinned profile recipe. No write, restore, read-back or boot action is available here.",
                    classes="warning",
                )
                yield Input(placeholder="Absolute workflow evidence checkpoint path", id="x-checkpoint")
                yield Static(
                    "Version 1 request JSON. Voltage, wiring and storage claims are human-reported with actor, UTC time, instrument and test point; the checksum is not authentication.",
                    classes="warning",
                )
                yield TextArea("{}", id="x-request", language="json", show_line_numbers=True)
            with Horizontal(classes="button-container"):
                yield Button("Run This Controller Action", id="x-submit", variant="primary")
                yield Button("Back", id="x-back")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "x-back":
            if self.guard_navigation("leaving this screen"):
                return
            self.app.pop_screen()
            return
        if event.button.id != "x-submit":
            return
        profile_id = self.controller_app.controller.selected_profile_id
        checkpoint = self.query_one("#x-checkpoint", Input).value.strip()
        request = self.query_one("#x-request", TextArea).text
        if profile_id not in ("x200-libreboot", "x220-coreboot"):
            self.query_one("#x-action-status", Static).update("Select the X200 Libreboot or X220 Coreboot profile first.")
            return
        if not checkpoint:
            self.query_one("#x-action-status", Static).update("WORKFLOW_CHECKPOINT_REQUIRED")
            return
        if request.strip() in ("", "{}"):
            self.query_one("#x-action-status", Static).update("VERSIONED_OPERATOR_REQUEST_REQUIRED")
            return
        self.query_one("#x-submit", Button).disabled = True
        self.query_one("#x-action-status", Static).update("Submitting explicit controller request…")
        self._operation_active = True
        self.worker = self.submit_request_worker(
            self.controller_app.controller, self.action, profile_id, checkpoint, request
        )

    @work(thread=True)
    def submit_request_worker(
        self,
        controller: ApplicationController,
        action: ApplicationAction,
        profile_id: str,
        checkpoint: str,
        request_document: str,
    ) -> None:
        """Do potentially slow backend work off-thread; no widget lookup here."""
        worker = get_current_worker()
        try:
            result = controller.request_action(
                action,
                profile_id=profile_id,
                checkpoint_path=checkpoint,
                request_document=request_document,
            )
        except Exception as exc:
            result = ApplicationActionResult(
                action,
                ApplicationStatus.UNKNOWN,
                "WORKFLOW_ACTION_FAILED",
                f"The controller action ended with an error: {exc}",
            )
        if self.is_mounted and not worker.is_cancelled:
            self.app.call_from_thread(self._show_result, result)

    def _show_result(self, result: ApplicationActionResult) -> None:
        self._operation_active = False
        self.worker = None
        self.query_one("#x-submit", Button).disabled = False
        self.query_one("#x-action-status", Static).update(f"{result.code}: {result.message}")

