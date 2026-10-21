"""Welcome Screen module."""
import logging
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Button, Header, Footer
from textual.containers import Container, Center, Horizontal
from autoloader.config import VERSION

logger = logging.getLogger(__name__)

ASCII_LOGO = r"""
  _      _ _               _____               
 | |    (_) |             / ____|              
 | |     _| |__  _ __ ___| |     ___  _ __ ___ 
 | |    | | '_ \| '__/ _ \ |    / _ \| '__/ _ \
 | |____| | |_) | | |  __/ |___| (_) | | |  __/
 |______|_|_.__/|_|  \___|\_____\___/|_|  \___|
       AutoLoader - ThinkPad Firmware Tool      
"""

class WelcomeScreen(Screen[None]):
    """The initial welcome screen of the application."""
    
    def compose(self) -> ComposeResult:
        """Compose the welcome screen."""
        yield Header()
        with Container(classes="container"):
            with Center():
                yield Static(ASCII_LOGO, classes="header-text")
                yield Static(f"In development · Version {VERSION}", id="version")
                yield Static(
                    "This in-development tool provides ThinkPad detection and firmware workflow guidance.\n"
                    "Guarded X200/X220 ROM builds and write workflows require their evidence checks and explicit approval.\n"
                    "Physical-device validation remains pending.",
                    id="description",
                    classes="warning"
                )
                yield Static(
                    "Not run; choose Start Guided Setup to review device detection options.",
                    id="detection-status",
                    classes="warning",
                )
            
            with Horizontal(classes="button-container"):
                yield Button("Start Guided Setup", variant="primary", id="btn-start")
                yield Button("Advanced Options", id="btn-advanced")
                yield Button("About", id="btn-about")
        yield Footer()

    def on_mount(self) -> None:
        """Update hardware detection status on mount."""
        try:
            detected = getattr(self.app, "detected_model", None)
            bios = getattr(self.app, "detected_bios", None)
            status_widget = self.query_one("#detection-status", Static)
            if detected and detected.model_id:
                bios_str = f" | BIOS: {bios.version}" if (bios and bios.version) else ""
                status_widget.update(
                    f"Auto-detection: Found {detected.model_name} ({detected.model_id.upper()}){bios_str}"
                )
                status_widget.remove_class("warning")
                status_widget.add_class("success")
            else:
                status_widget.update("Not run; choose Start Guided Setup to review device detection options.")
        except Exception as e:
            logger.debug(f"Could not update welcome detection status: {e}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press events.
        
        Args:
            event: The button pressed event.
        """
        if event.button.id == "btn-start":
            self.app.push_screen("model_select")
        elif event.button.id == "btn-advanced":
            self.app.notify("Advanced Options not yet implemented.")
        elif event.button.id == "btn-about":
            self.app.notify(
                f"Libre_Core AutoLoader v{VERSION} (in development). Firmware workflows are evidence-gated; "
                "physical-device validation remains pending.\n"
                "Created for ThinkPad firmware workflow development."
            )
