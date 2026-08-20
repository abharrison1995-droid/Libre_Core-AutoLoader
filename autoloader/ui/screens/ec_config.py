"""EC Modification Screen module."""
import logging
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Button, Header, Footer, Checkbox
from textual.containers import Container, Vertical, Horizontal

logger = logging.getLogger(__name__)

class EcConfigScreen(Screen[None]):
    """Screen for configuring Embedded Controller mods."""
    
    def compose(self) -> ComposeResult:
        """Compose the EC config screen."""
        yield Header()
        with Container(classes="container"):
            yield Static("Embedded Controller (EC) Modification", classes="header-text")
            
            with Vertical():
                yield Static(
                    "EC mods alter the Embedded Controller firmware to support classic keyboards "
                    "or remove battery checks. These must be done BEFORE flashing Coreboot.",
                    classes="success"
                )
                yield Static(
                    "WARNING: Requires downgraded BIOS! May be affected by CVE-2019-6171.",
                    classes="error"
                )
                
                yield Checkbox("Classic 7-row Keyboard Remap", value=True)
                yield Checkbox("Battery Whitelist Bypass", value=True)
                
            with Horizontal(classes="button-container"):
                yield Button("Back", id="btn-back")
                yield Button("Apply EC Mods", variant="primary", id="btn-apply")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press events."""
        if event.button.id == "btn-back":
            self.app.pop_screen()
        elif event.button.id == "btn-apply":
            self.app.notify("Applying EC Mods is not yet fully implemented.", severity="warning")
            self.app.push_screen("flash_guide")
