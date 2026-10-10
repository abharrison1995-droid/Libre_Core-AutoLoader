"""Build request status screen; unavailable work never reports fake progress."""
import logging

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Button, Footer, Header, Static
from textual.worker import Worker, get_current_worker

from autoloader.application_controller import ApplicationAction, ApplicationStatus
from autoloader.ui.widgets.log_panel import LogPanel
from autoloader.ui.widgets.progress_bar import CustomProgressBar
from autoloader.ui.screens.controller_screen import ControllerScreen

logger = logging.getLogger(__name__)


class BuildProgressScreen(ControllerScreen):
    """Submit build requests only through the shared controller facade."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(classes="container"):
            yield Static("Firmware Preparation Status", classes="header-text")
            yield Static("Build not started", id="build-status", classes="warning")
            self.progress_bar = CustomProgressBar(label="No build progress reported", total=100)
            yield self.progress_bar
            self.log_panel = LogPanel()
            yield self.log_panel
            with Horizontal(classes="button-container"):
                yield Button("Cancel", id="btn-cancel")
                yield Button("Hardware steps unavailable", id="btn-next", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self.worker: Worker = self.request_build()

    @work(thread=True)
    def request_build(self) -> None:
        worker = get_current_worker()
        result = self.controller_app.controller.request_action(ApplicationAction.BUILD)
        if self.is_mounted and not worker.is_cancelled:
            self.app.call_from_thread(self._show_result, result)
        if result.status is ApplicationStatus.STAGED_HARDWARE_PENDING:
            logger.error("Build request cannot make an artifact staged through the UI action gate")

    def _show_result(self, result) -> None:
        """Render the controller's actual outcome on the UI thread."""
        self.query_one("#build-status", Static).update(f"{result.status.value} · {result.code}: {result.message}")
        self.log_panel.write(f"{result.message}\n")
        self.progress_bar.update_label("Unavailable; no build progress reported")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.worker.cancel()
            self.app.pop_screen()
