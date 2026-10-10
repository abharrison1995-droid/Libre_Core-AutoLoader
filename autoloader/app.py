"""Main Textual Application module."""
from textual.app import App
from textual.binding import Binding
from textual.widgets import Header, Footer

from autoloader.ui.theme import AUTOLOADER_CSS
from autoloader.ui.screens.welcome import WelcomeScreen
from autoloader.ui.screens.model_select import ModelSelectScreen
from autoloader.ui.screens.pathway_select import PathwaySelectScreen
from autoloader.ui.screens.rom_config import RomConfigScreen
from autoloader.ui.screens.build_progress import BuildProgressScreen
from autoloader.ui.screens.flash_guide import FlashGuideScreen
from autoloader.ui.screens.backup_screen import BackupScreen
from autoloader.ui.screens.verify_screen import VerifyScreen
from autoloader.ui.screens.evidence_import import EvidenceImportScreen
from autoloader.ui.screens.t430_handoff import T430HandoffScreen
from autoloader.ui.screens.x_workflow_action import XWorkflowActionScreen
from autoloader.ui.screens.write_confirmation import WriteConfirmationScreen
from autoloader.application_controller import ApplicationController

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
        "build_progress": BuildProgressScreen,
        "flash_guide": FlashGuideScreen,
        "backup_screen": BackupScreen,
        "verify_screen": VerifyScreen,
        "evidence_import": EvidenceImportScreen,
        "t430_handoff": T430HandoffScreen,
        "x_workflow_action": XWorkflowActionScreen,
        "write_confirmation": WriteConfirmationScreen,
    }

    def __init__(self, *, controller: ApplicationController | None = None, **kwargs):
        super().__init__(**kwargs)
        self.controller = controller or ApplicationController()
        self.selected_model_id: str | None = None
        self.selected_profile_id: str | None = None
        self.detected_model = None
        self.detected_bios = None

    def on_mount(self) -> None:
        """Called when app starts."""
        self.push_screen("welcome")

    def set_selected_profile(self, profile_id: str) -> None:
        """Select an allowlisted V1 workflow through the shared controller."""
        profile = self.controller.select_profile(profile_id)
        self.selected_profile_id = profile.profile_id
        self.selected_model_id = profile.model_id

    def set_selected_model(self, model_id: str) -> None:
        """Compatibility adapter for internal callers; only V1 model IDs map."""
        profile_by_model = {"t430": "t430-1vyrain", "x200": "x200-libreboot", "x220": "x220-coreboot"}
        profile_id = profile_by_model.get(model_id.lower())
        if profile_id is None:
            raise ValueError(f"Unsupported V1 model: {model_id}")
        self.set_selected_profile(profile_id)

    def action_toggle_dark(self) -> None:
        """Toggle dark/light mode."""
        self.theme = "textual-light" if self.theme == "textual-dark" else "textual-dark"
        self.notify(f"Theme switched to {self.theme}", title="Theme")

    def action_help(self) -> None:
        """Show help information and keyboard shortcuts."""
        help_text = (
            "Shortcuts: [q] Quit  [b] Go Back  [d] Toggle Theme  [?] Show Help\n"
            "Workflow screens show status only for operations that have a connected backend."
        )
        self.notify(help_text, title="Libre_Core AutoLoader Help", timeout=6.0)

    async def action_back(self) -> None:
        """Go back to previous screen."""
        if isinstance(self.screen, XWorkflowActionScreen) and self.screen.guard_navigation("going back"):
            return
        if isinstance(self.screen, WriteConfirmationScreen) and self.screen.guard_navigation("going back"):
            return
        if len(self.screen_stack) > 2:
            self.pop_screen()
        else:
            self.notify("Cannot go back any further.", title="Back")

    async def action_quit(self) -> None:
        """Keep explicit workflow requests visible until their worker returns."""
        if isinstance(self.screen, XWorkflowActionScreen) and self.screen.guard_navigation("quitting"):
            return
        if isinstance(self.screen, WriteConfirmationScreen) and self.screen.guard_navigation("quitting"):
            return
        await super().action_quit()

if __name__ == "__main__":
    app = AutoLoaderApp()
    app.run()
