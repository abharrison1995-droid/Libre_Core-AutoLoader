"""Build Progress Screen module."""
import logging
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Button, Header, Footer
from textual.containers import Container, VerticalScroll, Horizontal
from textual import work

from textual.worker import get_current_worker, Worker
from autoloader.ui.widgets.progress_bar import CustomProgressBar
from autoloader.ui.widgets.log_panel import LogPanel
from autoloader.orchestrator import Orchestrator, AppState

logger = logging.getLogger(__name__)

class BuildProgressScreen(Screen[None]):
    """Screen displaying real-time ROM build progress."""
    
    def compose(self) -> ComposeResult:
        """Compose the build progress screen."""
        yield Header()
        with Container(classes="container"):
            yield Static("ROM Build Status", classes="header-text")
            yield Static(
                "No validated firmware builder is connected. No ROM will be produced by this workflow.",
                classes="error",
                id="build-unavailable",
            )
            
            self.progress_bar = CustomProgressBar(label="Build unavailable", total=100)
            yield self.progress_bar
            
            self.log_panel = LogPanel()
            yield self.log_panel
            
            with Horizontal(classes="button-container"):
                yield Button("Cancel", id="btn-cancel")
                self.btn_next = Button("Continue to Flashing", variant="primary", id="btn-next", disabled=True)
                yield self.btn_next
        yield Footer()

    def on_mount(self) -> None:
        """Start the build process worker on mount."""
        self.orchestrator: Orchestrator | None = None
        self.worker: Worker = self.execute_build()

    @work(thread=True)
    def execute_build(self) -> None:
        """Execute the build pipeline in a background thread."""
        worker = get_current_worker()
        app_state = getattr(self.app, "state", None) or AppState(
            model_id=getattr(self.app, "selected_model_id", None)
        )
        
        def on_log(msg: str) -> None:
            if self.is_mounted and not worker.is_cancelled:
                self.app.call_from_thread(self.log_panel.write, f"{msg}\n")
                
        def on_progress(pct: int, label: str) -> None:
            if self.is_mounted and not worker.is_cancelled:
                self.app.call_from_thread(self.progress_bar.set_progress, pct)
                self.app.call_from_thread(self.progress_bar.update_label, label)
                
        def on_complete() -> None:
            if self.is_mounted and not worker.is_cancelled:
                self.app.call_from_thread(setattr, self.btn_next, "disabled", False)
                
        def on_error(err_msg: str) -> None:
            if self.is_mounted and not worker.is_cancelled:
                self.app.call_from_thread(self.log_panel.write, f"{err_msg}\n")

        self.orchestrator = Orchestrator(
            app_state,
            on_log=on_log,
            on_progress=on_progress,
            on_complete=on_complete,
            on_error=on_error,
        )
        self.orchestrator.run_build()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press events."""
        if event.button.id == "btn-cancel":
            if hasattr(self, "orchestrator") and self.orchestrator:
                self.orchestrator.cancel()
            if hasattr(self, "worker") and self.worker:
                self.worker.cancel()
            self.app.pop_screen()
        elif event.button.id == "btn-next":
            self.app.push_screen("flash_guide")
