"""Backup status screen routed through the application controller."""
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Static

from autoloader.application_controller import ApplicationAction, ApplicationController
from autoloader.ui.screens.controller_screen import ControllerScreen


class BackupScreen(ControllerScreen):
    """No direct SPI capture, restore, or manifest trust from this screen."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(classes="container"):
            yield Static("Backup and Recovery Evidence", classes="header-text")
            with Vertical():
                yield Static("No validated backup set is attached to the selected profile.", id="backup-list")
                yield Static(
                    "Backup capture is unavailable here until complete observed chip topology, safe programmer evidence, and persistent recovery storage are validated.",
                    classes="warning",
                    id="backup-unavailable",
                )
                yield Static("", id="backup-result")
            with Horizontal(classes="button-container"):
                yield Button("Back", id="btn-back")
                yield Button("Capture Backup", id="btn-create", disabled=True)
                yield Button("Verify Recorded Backups", id="btn-verify", disabled=True)
                yield Button("Restore", id="btn-restore", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        controller: ApplicationController = self.controller_app.controller
        if controller.selected_profile_id:
            status = controller.profile_status()
            self.query_one("#backup-list", Static).update(
                f"{status.profile_id}: {status.status.value} · {status.stage}\n"
                "Evidence references: " + (", ".join(status.evidence_refs) if status.evidence_refs else "none")
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-back":
            self.app.pop_screen()
            return
        action_by_id = {
            "btn-create": ApplicationAction.BACKUP,
            "btn-verify": ApplicationAction.VERIFY_BACKUPS,
            "btn-restore": ApplicationAction.RESTORE,
        }
        action = action_by_id.get(event.button.id or "")
        if action is None:
            return
        result = self.controller_app.controller.request_action(action)
        self.query_one("#backup-result", Static).update(f"{result.code}: {result.message}")
