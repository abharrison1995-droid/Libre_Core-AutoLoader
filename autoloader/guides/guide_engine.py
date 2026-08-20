"""
Step-by-step guide renderer.
"""
from dataclasses import dataclass
from typing import Optional, List

@dataclass
class GuideStep:
    """A single step in a guide."""
    step_number: int
    title: str
    description: str
    substeps: List[str]
    diagram: Optional[str] = None
    warning: Optional[str] = None
    tip: Optional[str] = None
    reference: Optional[str] = None
    requires_confirmation: bool = True
    estimated_time_minutes: int = 0

@dataclass
class Guide:
    """A complete interactive guide."""
    title: str
    description: str
    total_estimated_time: int
    difficulty: str
    tools_required: List[str]
    prerequisites: List[str]
    steps: List[GuideStep]

class GuideEngine:
    """Engine for rendering and navigating guides."""
    
    def __init__(self, guide: Guide):
        """Initialize with a guide."""
        self.guide = guide
        self.current_step_index = 0
        
    def render_step(self) -> GuideStep:
        """Render the current step."""
        return self.guide.steps[self.current_step_index]
        
    def render_guide_overview(self) -> dict:
        """Return an overview of the guide."""
        return {
            "title": self.guide.title,
            "description": self.guide.description,
            "total_estimated_time": self.guide.total_estimated_time,
            "difficulty": self.guide.difficulty,
            "tools_required": self.guide.tools_required,
            "prerequisites": self.guide.prerequisites,
            "total_steps": len(self.guide.steps)
        }
        
    def get_progress(self) -> tuple[int, int]:
        """Return current step and total steps."""
        return self.current_step_index + 1, len(self.guide.steps)
        
    def next_step(self) -> bool:
        """Advance to the next step. Returns True if advanced, False if at end."""
        if self.current_step_index < len(self.guide.steps) - 1:
            self.current_step_index += 1
            return True
        return False
        
    def prev_step(self) -> bool:
        """Go back to the previous step. Returns True if went back, False if at start."""
        if self.current_step_index > 0:
            self.current_step_index -= 1
            return True
        return False

    def is_last_step(self) -> bool:
        """Check if currently at the final step."""
        return self.current_step_index == len(self.guide.steps) - 1

    def get_current_step(self) -> GuideStep:
        """Return the current step object."""
        return self.guide.steps[self.current_step_index]
