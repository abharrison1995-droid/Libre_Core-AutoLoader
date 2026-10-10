"""Versioned human attestations for the T430 handoff controller."""
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Button, Footer, Header, Input, Static, TextArea

from autoloader.application_controller import ApplicationAction, ApplicationController
from autoloader.ui.screens.controller_screen import ControllerScreen


class T430HandoffScreen(ControllerScreen):
    """Submit human stage evidence; the controller owns fresh local capture and binding."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(classes="container"):
            yield Static("T430 Human Handoff Stage", classes="header-text")
            yield Static(
                "Paste a version 1 JSON request. On Submit, the shared controller captures fresh local DMI/BIOS and binds the attestations to that capture. This screen does not download, execute, write media, flash, or confirm hardware success.",
                classes="warning",
            )
            yield Input(placeholder="Absolute new checkpoint path for approval; existing checkpoint for return", id="t430-checkpoint")
            yield TextArea("{}", id="t430-request", language="json", show_line_numbers=True)
            yield Static("", id="t430-result")
            with Horizontal(classes="button-container"):
                yield Button("Capture and Submit Stage", variant="primary", id="t430-submit")
                yield Button("Back", id="t430-back")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "t430-back":
            self.app.pop_screen()
            return
        if event.button.id != "t430-submit":
            return
        checkpoint = self.query_one("#t430-checkpoint", Input).value.strip()
        document = self.query_one("#t430-request", TextArea).text
        if not checkpoint:
            self.query_one("#t430-result", Static).update("T430_STAGE_REQUEST_REJECTED: checkpoint path required")
            return
        result = self.controller_app.controller.request_action(
            ApplicationAction.T430_HANDOFF_STEP,
            profile_id="t430-1vyrain",
            checkpoint_path=checkpoint,
            request_document=document,
        )
        self.query_one("#t430-result", Static).update(f"{result.code}: {result.message}")
