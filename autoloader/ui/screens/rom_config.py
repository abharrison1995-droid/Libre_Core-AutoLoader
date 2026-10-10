"""Workflow preparation summary without generic firmware or payload choices."""
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Header, Static

from autoloader.application_controller import ApplicationAction, ApplicationController
from autoloader.ui.screens.controller_screen import ControllerScreen


class RomConfigScreen(ControllerScreen):
    """Display controller-owned profile evidence and fail-closed preparation."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(classes="container"):
            yield Static("V1 Workflow Preparation", classes="header-text")
            yield Static("No workflow profile selected", id="prepare-profile")
            with VerticalScroll():
                yield Static("", id="prepare-status")
                yield Static("", id="prepare-reasons")
                yield Static("", id="prepare-evidence")
                yield Static(
                    "There are no generic firmware-base, payload, custom-ROM, or EC-modification choices in V1.",
                    classes="warning",
                )
                yield Static(
                    "A staged artifact requires the accepted model-specific workflow, durable backup and recovery evidence, and a pinned validated artifact result.",
                    classes="warning",
                )
            with Horizontal(classes="button-container"):
                yield Button("Back", id="btn-back")
                yield Button("Request Preparation", id="btn-prepare", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_status()

    def refresh_status(self) -> None:
        controller: ApplicationController = self.controller_app.controller
        if not controller.selected_profile_id:
            return
        status = controller.profile_status()
        self.query_one("#prepare-profile", Static).update(
            f"{status.model_id.upper()} · {status.firmware_family} · {status.profile_id}"
        )
        self.query_one("#prepare-status", Static).update(f"{status.status.value} · {status.stage}")
        self.query_one("#prepare-reasons", Static).update("Reasons: " + "; ".join(status.reasons))
        self.query_one("#prepare-evidence", Static).update(
            "Evidence references: " + (", ".join(status.evidence_refs) if status.evidence_refs else "none")
        )
        self.query_one("#btn-prepare", Button).disabled = ApplicationAction.PREPARE not in status.allowed_next_actions

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-back":
            self.app.pop_screen()
        elif event.button.id == "btn-prepare":
            result = self.controller_app.controller.request_action(ApplicationAction.PREPARE)
            self.query_one("#prepare-status", Static).update(f"{result.code}: {result.message}")
            self.refresh_status()
