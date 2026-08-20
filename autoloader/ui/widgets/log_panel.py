"""Scrollable log output panel widget."""
import logging
from textual.app import ComposeResult
from textual.widgets import RichLog
from textual.containers import VerticalScroll

logger = logging.getLogger(__name__)

class LogPanel(VerticalScroll):
    """A scrollable log panel for command outputs."""
    
    def __init__(self, **kwargs) -> None:
        """Initialize the log panel."""
        super().__init__(**kwargs)
        self._log = RichLog(markup=True, wrap=True)
        self.styles.border = ("solid", "#333333")
        self.styles.height = "1fr"
        
    def compose(self) -> ComposeResult:
        """Compose the log panel."""
        yield self._log
        
    def write(self, text: str) -> None:
        """Write text to the log panel.
        
        Args:
            text: Text to write.
        """
        self._log.write(text)
        
    def clear(self) -> None:
        """Clear the log panel."""
        self._log.clear()
