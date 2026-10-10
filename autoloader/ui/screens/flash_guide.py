"""Hardware status screen with all writer-related actions quarantined."""
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Header, Static

from autoloader.application_controller import ApplicationAction, ApplicationController
from autoloader.ui.screens.controller_screen import ControllerScreen


class FlashGuideScreen(ControllerScreen):
    """Display controller status; never offers programmer or risk-checkbox bypasses."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(classes="container"):
            yield Static("Hardware Operation Status", classes="header-text")
            yield Static("Physical firmware writes are disabled in this release candidate.", classes="error", id="flash-unavailable")
            with VerticalScroll():
                yield Static("", id="flash-profile")
                yield Static("No write, restore, SPI probe, read-back, or hardware boot check has been performed.", id="flash-status")
                yield Static("", id="flash-evidence")
                yield Static("A checkbox or manual programmer choice cannot authorize a physical firmware operation.", classes="warning")
            with Horizontal(classes="button-container"):
                yield Button("Back", id="btn-back")
                yield Button("Write", id="btn-flash", disabled=True)
                yield Button("Restore", id="btn-restore", disabled=True)
                yield Button("Read-back", id="btn-verify", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        controller: ApplicationController = self.controller_app.controller
        if controller.selected_profile_id:
            status = controller.profile_status()
            self.query_one("#flash-profile", Static).update(f"{status.profile_id} · {status.status.value} · {status.stage}")
            self.query_one("#flash-evidence", Static).update(
                "Evidence references: " + (", ".join(status.evidence_refs) if status.evidence_refs else "none")
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-back":
            self.app.pop_screen()
            return
        action_by_id = {
            "btn-flash": ApplicationAction.FLASH,
            "btn-restore": ApplicationAction.RESTORE,
            "btn-verify": ApplicationAction.READBACK,
        }
        action = action_by_id.get(event.button.id or "")
        if action is None:
            return
        result = self.controller_app.controller.request_action(action)
        self.query_one("#flash-status", Static).update(f"{result.code}: {result.message}")
