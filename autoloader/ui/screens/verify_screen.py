"""Post-Flash Verification Screen module."""
import logging
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Button, Header, Footer
from textual.containers import Container, Vertical, Horizontal

logger = logging.getLogger(__name__)

class VerifyScreen(Screen[None]):
    """Screen for post-flash verification and wrap-up."""
    
    def compose(self) -> ComposeResult:
        """Compose the verify screen."""
        yield Header()
        with Container(classes="container"):
            yield Static("Post-Flash Verification Report", classes="header-text")
            
            with Vertical(classes="report"):
                yield Static("Verification status: NOT PERFORMED", id="status-text", classes="error")
                yield Static("No flash operation is connected to this screen.")
                yield Static("A read-back comparison and hardware boot check are required before reporting success.")
                
                yield Static(
                    "\nNo hardware validation has been recorded. Keep verified backups and "
                    "use the recovery procedure documented for the exact model and board."
                )
                
            with Horizontal(classes="button-container"):
                yield Button("Finish & Exit", variant="primary", id="btn-exit")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press events."""
        if event.button.id == "btn-exit":
            self.app.exit()
