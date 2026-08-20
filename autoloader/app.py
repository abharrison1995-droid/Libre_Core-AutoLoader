"""Main Textual Application module."""
import logging
from textual.app import App
from textual.binding import Binding
from textual.widgets import Header, Footer

from autoloader.ui.theme import AUTOLOADER_CSS
from autoloader.ui.screens.welcome import WelcomeScreen
from autoloader.ui.screens.model_select import ModelSelectScreen
from autoloader.ui.screens.pathway_select import PathwaySelectScreen
from autoloader.ui.screens.rom_config import RomConfigScreen
from autoloader.ui.screens.ec_config import EcConfigScreen
from autoloader.ui.screens.build_progress import BuildProgressScreen
from autoloader.ui.screens.flash_guide import FlashGuideScreen
from autoloader.ui.screens.backup_screen import BackupScreen
from autoloader.ui.screens.verify_screen import VerifyScreen
from autoloader.detection.model import ModelDetector, DetectionResult
from autoloader.detection.bios import BIOSDetector, BIOSInfo
from autoloader.rom_builder.configurator import ROMConfiguration

from autoloader.orchestrator import AppState
from autoloader.flasher.flashrom import ProgrammerType

logger = logging.getLogger(__name__)

class AutoLoaderApp(App[None]):
    """Main application class for Libre_Core AutoLoader."""
    
    TITLE = "Libre_Core AutoLoader"
    CSS = AUTOLOADER_CSS
    
    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("question_mark", "help", "Help", show=True),
        Binding("b", "back", "Back", show=True),
        Binding("d", "toggle_dark", "Theme", show=True),
    ]

    SCREENS = {
        "welcome": WelcomeScreen,
        "model_select": ModelSelectScreen,
        "pathway_select": PathwaySelectScreen,
        "rom_config": RomConfigScreen,
        "ec_config": EcConfigScreen,
        "build_progress": BuildProgressScreen,
        "flash_guide": FlashGuideScreen,
        "backup_screen": BackupScreen,
        "verify_screen": VerifyScreen,
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.state = AppState()
        self.selected_model_id: str | None = None
        self.rom_config: ROMConfiguration | None = None
        self.detected_model: DetectionResult | None = None
        self.detected_bios: BIOSInfo | None = None

    def on_mount(self) -> None:
        """Called when app starts."""
        try:
            self.detected_model = ModelDetector().detect()
            self.detected_bios = BIOSDetector().detect()
            if self.detected_model and self.detected_model.model_id:
                self.selected_model_id = self.detected_model.model_id
                self.state.model_id = self.detected_model.model_id
                self.state.detected = self.detected_model
        except Exception as e:
            logger.warning(f"Hardware detection failed on startup: {e}")
        self.push_screen("welcome")

    def set_selected_model(self, model_id: str) -> None:
        """Updates selected model across legacy property and AppState."""
        self.selected_model_id = model_id
        self.state.model_id = model_id

    def action_toggle_dark(self) -> None:
        """Toggle dark/light mode."""
        self.theme = "textual-light" if self.theme == "textual-dark" else "textual-dark"
        self.notify(f"Theme switched to {self.theme}", title="Theme")

    def action_help(self) -> None:
        """Show help information and keyboard shortcuts."""
        help_text = (
            "Shortcuts: [q] Quit  [b] Go Back  [d] Toggle Theme  [?] Show Help\n"
            "Workflow: Detect Model -> Select Pathway -> Configure/Guide -> Verify"
        )
        self.notify(help_text, title="Libre_Core AutoLoader Help", timeout=6.0)

    def action_back(self) -> None:
        """Go back to previous screen."""
        if len(self.screen_stack) > 2:
            self.pop_screen()
        else:
            self.notify("Cannot go back any further.", title="Back")

if __name__ == "__main__":
    app = AutoLoaderApp()
    app.run()
