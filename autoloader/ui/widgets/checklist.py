"""Interactive checklist widget."""
import logging
from textual.app import ComposeResult
from textual.widgets import Checkbox, Static
from textual.containers import Vertical

logger = logging.getLogger(__name__)

class InteractiveChecklist(Vertical):
    """A widget for displaying a list of checkboxes."""
    
    def __init__(self, items: list[str], title: str = "Checklist", **kwargs) -> None:
        """Initialize the checklist.
        
        Args:
            items: List of labels for each checkbox.
            title: Title of the checklist.
            **kwargs: Additional kwargs.
        """
        super().__init__(**kwargs)
        self.items = items
        self.title = title
        
    def compose(self) -> ComposeResult:
        """Compose checklist widgets."""
        yield Static(self.title, classes="header-text")
        for item in self.items:
            yield Checkbox(item)
            
    def get_selected(self) -> list[str]:
        """Get the labels of all selected checkboxes.
        
        Returns:
            A list of selected item labels.
        """
        return [cb.label.plain for cb in self.query(Checkbox) if cb.value]
