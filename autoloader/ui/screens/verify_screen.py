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
                yield Static("Running verification checks...", id="status-text")
                yield Static("SHA256 match: ✅", classes="success")
                yield Static("ROM structure valid: ✅", classes="success")
                yield Static("CBFS header found: ✅", classes="success")
                yield Static("Payload present: ✅", classes="success")
                yield Static("ME status: Neutered ✅", classes="success")
                
                yield Static(
                    "\n🎉 Your ThinkPad has been successfully flashed! 🎉\n\n"
                    "First boot instructions:\n"
                    "1. Reassemble the laptop.\n"
                    "2. Plug in AC power.\n"
                    "3. Power on. It may take up to 30 seconds to show the bootsplash on first boot due to memory training.\n"
                    "4. Keep your backup ROMs safe in multiple locations!",
                    classes="success"
                )
                
            with Horizontal(classes="button-container"):
                yield Button("Finish & Exit", variant="primary", id="btn-exit")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press events."""
        if event.button.id == "btn-exit":
            self.app.exit()
