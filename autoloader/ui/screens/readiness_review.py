"""Read-only review of an existing terminal operation checkpoint."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Button, Footer, Header, Input, Static

from autoloader.application_controller import ApplicationAction, ApplicationController, ApplicationActionResult
from autoloader.ui.screens.controller_screen import ControllerScreen


class ReadinessReviewScreen(ControllerScreen):
    """Show persisted-input review without probing or changing operation state."""

    def compose(self) -> ComposeResult:
        profile = self.controller_app.controller.selected_profile_id
        yield Header()
        with Container(classes="container"):
            yield Static("Review Persisted Firmware Inputs", classes="header-text")
            yield Static(
                "This checks an existing terminal checkpoint and referenced files only. "
                "It does not detect current hardware, approve work, or declare firmware ready.",
                classes="warning",
            )
            yield Static(f"Selected profile: {profile or 'none'}")
            yield Input(placeholder="Absolute path to terminal checkpoint", id="readiness-checkpoint")
            yield Static("No report has been created.", id="readiness-status")
            with Horizontal(classes="button-container"):
                yield Button("Review Persisted Inputs", id="readiness-submit", variant="primary")
                yield Button("Back", id="readiness-back")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "readiness-back":
            self.app.pop_screen()
            return
        if event.button.id != "readiness-submit":
            return
        controller: ApplicationController = self.controller_app.controller
        profile = controller.selected_profile_id
        checkpoint = self.query_one("#readiness-checkpoint", Input).value.strip()
        if profile is None:
            self.query_one("#readiness-status", Static).update("Select a V1 device profile first.")
            return
        if not checkpoint:
            self.query_one("#readiness-status", Static).update("Enter the terminal checkpoint path.")
            return
        result = controller.request_action(
            ApplicationAction.REVIEW_READINESS,
            profile_id=profile,
            checkpoint_path=checkpoint,
        )
        self.show_result(result)

    def show_result(self, result: ApplicationActionResult) -> None:
        details = "\n".join(result.reasons)
        evidence = "\n".join(result.evidence_refs)
        self.query_one("#readiness-status", Static).update(
            f"{result.code}: {result.message}\n{details}\n{evidence}"
        )
