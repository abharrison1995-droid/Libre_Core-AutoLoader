"""Allowlisted V1 workflow profile selection."""
import logging

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Header, RadioButton, RadioSet, Static

from autoloader.application_controller import ApplicationAction, ApplicationController
from autoloader.ui.screens.controller_screen import ControllerScreen

logger = logging.getLogger(__name__)

PROFILE_LABELS = {
    "t430-1vyrain": "ThinkPad T430 · 1vyrain human handoff",
    "x200-libreboot": "ThinkPad X200 · Libreboot external SPI",
    "x220-coreboot": "ThinkPad X220 · Coreboot external SPI",
}


class ModelSelectScreen(ControllerScreen):
    """Choose one of the three reviewed V1 profiles."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(classes="container"):
            yield Static("Select a V1 ThinkPad Workflow", classes="header-text")
            yield Static(
                "Only T430/1vyrain, regular X200/Libreboot, and X220/Coreboot are in scope. "
                "A selection is a candidate, not a compatibility decision.",
                classes="warning",
                id="profile-warning",
            )
            with VerticalScroll():
                with RadioSet(id="profile-select"):
                    for profile_id, label in PROFILE_LABELS.items():
                        yield RadioButton(label, id=f"profile-{profile_id}")
            yield Static("Device evidence: not captured", id="profile-evidence")
            with Horizontal(classes="button-container"):
                yield Button("Detect Device", id="btn-detect")
                yield Button("Cancel", id="btn-cancel")
                yield Button("Continue", variant="primary", id="btn-continue")
        yield Footer()

    def on_mount(self) -> None:
        controller: ApplicationController = self.controller_app.controller
        selected = controller.selected_profile_id
        if selected:
            self.query_one(f"#profile-{selected}", RadioButton).value = True
        self.query_one("#profile-evidence", Static).update(self._evidence_summary())

    def _evidence_summary(self) -> str:
        controller: ApplicationController = self.controller_app.controller
        if controller.selected_profile_id:
            status = controller.profile_status()
            return f"{status.status.value} · {status.stage}: " + "; ".join(status.reasons)
        return "Device evidence: not captured"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-detect":
            result = self.controller_app.controller.request_action(ApplicationAction.DETECT_DEVICE)
            self.query_one("#profile-evidence", Static).update(f"{result.code} · {result.message}")
            return
        if event.button.id == "btn-cancel":
            self.app.pop_screen()
            return
        if event.button.id != "btn-continue":
            return
        chosen = self.query_one(RadioSet).pressed_button
        if chosen is None or not chosen.id or not chosen.id.startswith("profile-"):
            self.app.notify("Select one V1 workflow profile first.", severity="error")
            return
        profile_id = chosen.id.removeprefix("profile-")
        try:
            self.controller_app.set_selected_profile(profile_id)
        except ValueError as exc:
            logger.warning("Rejected unsupported workflow profile: %s", exc)
            self.app.notify(str(exc), severity="error")
            return
        self.app.push_screen("pathway_select")
