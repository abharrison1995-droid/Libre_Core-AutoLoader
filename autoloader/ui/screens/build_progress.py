"""Explicit build request entry point without implicit work or fake progress."""
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Button, Footer, Header, Static

from autoloader.application_controller import ApplicationAction
from autoloader.ui.screens.controller_screen import ControllerScreen
from autoloader.ui.screens.x_workflow_action import XWorkflowActionScreen


class BuildProgressScreen(ControllerScreen):
    """Show no progress until an operator submits a profile-specific request."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(classes="container"):
            yield Static("Firmware Preparation Status", classes="header-text")
            yield Static("Build not started; no progress or success is claimed.", id="build-status", classes="warning")
            with Horizontal(classes="button-container"):
                yield Button("Enter Explicit Build Request", id="btn-build")
                yield Button("Hardware steps unavailable", id="btn-next", disabled=True)
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "btn-build":
            return
        if self.controller_app.controller.selected_profile_id not in ("x200-libreboot", "x220-coreboot"):
            self.query_one("#build-status", Static).update("Select the X200 Libreboot or X220 Coreboot profile first.")
            return
        self.app.push_screen(XWorkflowActionScreen(ApplicationAction.BUILD))
