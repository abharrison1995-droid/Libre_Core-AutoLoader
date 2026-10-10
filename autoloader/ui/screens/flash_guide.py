"""Interactive Flashing Wizard Screen module."""
import logging
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Button, Header, Footer, RadioSet, RadioButton
from textual.containers import Container, VerticalScroll, Horizontal

from autoloader.ui.widgets.diagram_viewer import DiagramViewer
from autoloader.ui.widgets.log_panel import LogPanel
from autoloader.flasher.flashrom import ProgrammerType
from autoloader.orchestrator import AppState

logger = logging.getLogger(__name__)

CHIP_DIAGRAM = r"""
     Top of Motherboard
       .-------.
  CS --| 1   8 |-- VCC
MISO --| 2   7 |-- HOLD
  WP --| 3   6 |-- CLK
 GND --| 4   5 |-- MOSI
       '-------'
     SPI Flash Chip
"""

class FlashGuideScreen(Screen[None]):
    """Interactive wizard for hardware flashing."""
    
    def compose(self) -> ComposeResult:
        """Compose the flashing guide screen."""
        yield Header()
        with Container(classes="container"):
            yield Static("Interactive Flashing Guide", classes="header-text")
            yield Static(
                "Physical writes are unavailable until model-specific compatibility, backup, "
                "and human authorization checks are connected. No flash operation has been run.",
                classes="error",
                id="flash-unavailable",
            )
            
            with VerticalScroll(id="flash-steps"):
                yield Static("Select Programmer:", classes="success")
                with RadioSet(id="programmer-select"):
                    yield RadioButton("CH341A", value=True, id="prog-ch341a")
                    yield RadioButton("Raspberry Pi", id="prog-rpi")
                    yield RadioButton("Bus Pirate", id="prog-buspirate")
                    yield RadioButton("Internal (1vyrain/flashrom)", id="prog-internal")
                
                yield Static(
                    "WARNING: Improper flashing can BRICK your device. Ensure clip is firmly attached and 3.3V power is verified.",
                    classes="error"
                )
                
                yield DiagramViewer(diagram=CHIP_DIAGRAM)
                
                self.log_panel = LogPanel()
                yield self.log_panel
                
            with Horizontal(classes="button-container"):
                yield Button("Back", id="btn-back")
                self.btn_flash = Button("Write unavailable", id="btn-flash", disabled=True)
                yield self.btn_flash
                self.btn_verify = Button("Verification unavailable", id="btn-verify", disabled=True)
                yield self.btn_verify
        yield Footer()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        """Sync programmer selection to AppState."""
        prog_id = event.pressed.id if event.pressed else ""
        prog_map = {
            "prog-ch341a": ProgrammerType.CH341A,
            "prog-rpi": ProgrammerType.RASPBERRY_PI,
            "prog-buspirate": ProgrammerType.BUS_PIRATE,
            "prog-internal": ProgrammerType.INTERNAL,
        }
        if prog_id in prog_map and hasattr(self.app, "state"):
            self.app.state.programmer = prog_map[prog_id]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press events."""
        if event.button.id == "btn-back":
            self.app.pop_screen()
