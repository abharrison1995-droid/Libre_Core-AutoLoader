"""Model Selection Screen module."""
import logging
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Button, Header, Footer, RadioSet, RadioButton
from textual.containers import Container, VerticalScroll, Horizontal

from autoloader.database.models import get_all_models

logger = logging.getLogger(__name__)

class ModelSelectScreen(Screen[None]):
    """Screen for selecting the target ThinkPad model."""
    
    def compose(self) -> ComposeResult:
        """Compose the model selection screen."""
        yield Header()
        with Container(classes="container"):
            yield Static("Select Target ThinkPad Model", classes="header-text")
            yield Static("Current BIOS version: Unknown", classes="success", id="bios-info")
            
            with VerticalScroll():
                with RadioSet(id="model-select"):
                    for model in get_all_models():
                        desc = f"{model.name} ({model.generation.value}) — {model.flash_chips[0].size_mb}MB SPI"
                        if model.supports_1vyrain:
                            desc += " [1vyrain]"
                        if not model.libreboot_support:
                            desc += " [No Libreboot]"
                        yield RadioButton(desc, id=f"rb-{model.model_id}")
                
                yield Static(
                    "Warning: T480 is experimental. X201 lacks Libreboot support.",
                    classes="warning",
                    id="model-warning"
                )
                
            with Horizontal(classes="button-container"):
                yield Button("Cancel", id="btn-cancel")
                yield Button("Continue", variant="primary", id="btn-continue")
        yield Footer()

    def on_mount(self) -> None:
        """Update BIOS information and pre-select detected model on mount."""
        try:
            bios = getattr(self.app, "detected_bios", None)
            if bios and bios.version:
                bios_widget = self.query_one("#bios-info", Static)
                vendor_str = f" ({bios.vendor})" if bios.vendor else ""
                bios_widget.update(f"Current BIOS version: {bios.version}{vendor_str}")
            
            selected_model = getattr(self.app, "selected_model_id", None)
            if selected_model:
                rb = self.query_one(f"#rb-{selected_model}", RadioButton)
                rb.value = True
        except Exception as e:
            logger.debug(f"Could not initialize model_select screen: {e}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press events."""
        if event.button.id == "btn-cancel":
            self.app.pop_screen()
        elif event.button.id == "btn-continue":
            radio_set = self.query_one(RadioSet)
            if radio_set.pressed_button is not None:
                btn_id = radio_set.pressed_button.id
                if btn_id and btn_id.startswith("rb-"):
                    model_id = btn_id[3:]
                    if hasattr(self.app, "set_selected_model"):
                        self.app.set_selected_model(model_id)
                    else:
                        self.app.selected_model_id = model_id
                self.app.push_screen("pathway_select")
            else:
                self.app.notify("Please select a model first.", severity="error")
