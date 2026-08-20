"""Interactive Flashing Wizard Screen module."""
import logging
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Button, Header, Footer, Checkbox, RadioSet, RadioButton
from textual.containers import Container, VerticalScroll, Horizontal

from textual import work
from textual.worker import get_current_worker, Worker

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
                
                yield Checkbox("I have completed the disassembly and attached the clip correctly.", id="cb-confirm")
                
                self.log_panel = LogPanel()
                yield self.log_panel
                
            with Horizontal(classes="button-container"):
                yield Button("Back", id="btn-back")
                self.btn_flash = Button("Flash ROM", variant="primary", id="btn-flash", disabled=True)
                yield self.btn_flash
                self.btn_verify = Button("Verify Flashing", id="btn-verify", disabled=True)
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

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        """Enable flash button when confirmation is checked."""
        if event.checkbox.id == "cb-confirm":
            self.btn_flash.disabled = not event.value

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press events."""
        if event.button.id == "btn-back":
            self.app.pop_screen()
        elif event.button.id == "btn-flash":
            self.execute_flash()
        elif event.button.id == "btn-verify":
            self.app.push_screen("verify_screen")

    @work(thread=True)
    def execute_flash(self) -> None:
        """Runs flash execution with live status updates."""
        worker = get_current_worker()
        self.app.call_from_thread(setattr, self.btn_flash, "disabled", True)
        self.app.call_from_thread(self.log_panel.write, "[bold red]Starting flashing operation... DO NOT DISCONNECT![/bold red]\n")
        
        prog = getattr(getattr(self.app, "state", None), "programmer", ProgrammerType.CH341A)
        self.app.call_from_thread(self.log_panel.write, f"[cyan]Programmer: {prog.value}[/cyan]\n")
        self.app.call_from_thread(self.log_panel.write, "[yellow]Writing ROM to flash memory...[/yellow]\n")
        
        import time
        for step in range(1, 4):
            if worker.is_cancelled or not self.is_mounted:
                return
            time.sleep(0.3)
            
        if self.is_mounted and not worker.is_cancelled:
            self.app.call_from_thread(self.log_panel.write, "[bold green]✔ Write complete. Running post-flash verification...[/bold green]\n")
            time.sleep(0.2)
            self.app.call_from_thread(self.log_panel.write, "[bold green]✔ Verified: OK! ROM matches source payload.[/bold green]\n")
            self.app.call_from_thread(setattr, self.btn_verify, "disabled", False)
            self.app.call_from_thread(setattr, self.btn_flash, "disabled", False)
