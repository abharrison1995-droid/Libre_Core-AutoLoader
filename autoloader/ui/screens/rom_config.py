"""ROM Configuration Wizard Screen module."""
import logging
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Button, Header, Footer, RadioSet, RadioButton, Checkbox
from textual.containers import Container, VerticalScroll, Horizontal

logger = logging.getLogger(__name__)

class RomConfigScreen(Screen[None]):
    """Multi-step wizard for configuring ROM options."""
    
    def compose(self) -> ComposeResult:
        """Compose the ROM config screen."""
        yield Header()
        with Container(classes="container"):
            yield Static("ROM Configuration Wizard", classes="header-text")
            yield Static("Step 1-4: Select Firmware Options", id="step-indicator")
            
            with VerticalScroll(id="config-options"):
                yield Static("Firmware Base:", classes="success")
                with RadioSet(id="fw-base"):
                    yield RadioButton("Coreboot", value=True)
                    yield RadioButton("Libreboot")
                    yield RadioButton("Canoeboot")
                
                yield Static("Payload:", classes="success")
                with RadioSet(id="payload"):
                    yield RadioButton("SeaBIOS", value=True)
                    yield RadioButton("GRUB")
                    yield RadioButton("TianoCore")
                
                yield Static("Customizations:", classes="success")
                yield Checkbox("Remove WiFi whitelist", value=True)
                yield Checkbox("Neuter Intel ME", value=True)
                yield Checkbox("Custom bootsplash image")
                yield Checkbox("EC mod: Classic keyboard remap (xx30 only)")
                yield Checkbox("EC mod: Battery whitelist bypass (xx30 only)")
                yield Checkbox("Include CPU microcode updates", value=True)
                yield Checkbox("Use native libgfxinit")
                
                yield Static("Build Target:", classes="success")
                with RadioSet(id="build-target"):
                    yield RadioButton("Local build", value=True)
                    yield RadioButton("Export config for remote build")
                
            with Horizontal(classes="button-container"):
                yield Button("Back", id="btn-back")
                yield Button("Summary & Build", variant="primary", id="btn-build")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press events."""
        if event.button.id == "btn-back":
            self.app.pop_screen()
        elif event.button.id == "btn-build":
            self.app.push_screen("build_progress")
