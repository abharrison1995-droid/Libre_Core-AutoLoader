"""
Programmer setup guides package.
"""
from typing import Optional
from autoloader.guides.guide_engine import Guide
from . import ch341a, raspberry_pi, bus_pirate

PROGRAMMER_GUIDES_REGISTRY = {
    "ch341a": ch341a.get_guide,
    "raspberry_pi": raspberry_pi.get_guide,
    "bus_pirate": bus_pirate.get_guide,
}

def get_programmer_guide(programmer_id: str) -> Optional[Guide]:
    """Retrieve the programmer setup guide for a given programmer type."""
    factory = PROGRAMMER_GUIDES_REGISTRY.get(programmer_id.lower())
    if factory:
        return factory()
    return None

__all__ = ["get_programmer_guide", "PROGRAMMER_GUIDES_REGISTRY"]
