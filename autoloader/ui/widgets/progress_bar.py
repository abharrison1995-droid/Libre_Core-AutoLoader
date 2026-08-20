"""Custom progress bar widget."""
import logging
from typing import Optional
from textual.app import ComposeResult
from textual.widgets import ProgressBar as TextualProgressBar, Static
from textual.containers import Vertical

logger = logging.getLogger(__name__)

class CustomProgressBar(Vertical):
    """A composite widget containing a label and a textual progress bar."""
    
    def __init__(self, label: str = "Progress", total: int = 100, **kwargs) -> None:
        """Initialize the progress bar.
        
        Args:
            label: Text label for the progress bar.
            total: Total steps/percentage.
            **kwargs: Additional kwargs for the container.
        """
        super().__init__(**kwargs)
        self.label_text = label
        self.total = total
        self._label_widget = Static(label)
        self._bar_widget = TextualProgressBar(total=total, show_eta=True)
        
    def compose(self) -> ComposeResult:
        """Compose the progress bar widgets."""
        yield self._label_widget
        yield self._bar_widget
        
    def update_progress(self, advance: int = 1) -> None:
        """Advance the progress bar by a specific amount."""
        self._bar_widget.advance(advance)
        
    def update_label(self, new_label: str) -> None:
        """Update the label text."""
        self.label_text = new_label
        self._label_widget.update(new_label)
        
    def set_progress(self, progress: int) -> None:
        """Set progress to an absolute value."""
        self._bar_widget.progress = progress
