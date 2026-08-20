"""ASCII art diagram viewer widget."""
import logging
from textual.app import ComposeResult
from textual.widgets import Static
from textual.containers import Center, Middle

logger = logging.getLogger(__name__)

class DiagramViewer(Center):
    """A widget that centers and frames ASCII art diagrams."""
    
    def __init__(self, diagram: str, **kwargs) -> None:
        """Initialize the diagram viewer.
        
        Args:
            diagram: ASCII art string.
            **kwargs: Additional kwargs.
        """
        super().__init__(**kwargs)
        self.diagram = diagram
        
    def compose(self) -> ComposeResult:
        """Compose the viewer."""
        with Middle():
            static = Static(self.diagram)
            static.styles.border = ("solid", "#ee1c25")
            static.styles.padding = (1, 2)
            yield static
            
    def update_diagram(self, new_diagram: str) -> None:
        """Update the displayed diagram.
        
        Args:
            new_diagram: New ASCII art string.
        """
        self.diagram = new_diagram
        static = self.query_one(Static)
        static.update(new_diagram)
