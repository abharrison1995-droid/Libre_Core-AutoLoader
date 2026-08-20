"""
Disassembly guides package mapping model IDs to guide functions.
"""
from typing import Callable, Optional
from autoloader.guides.guide_engine import Guide

from . import x200, x201, x220, x230, t400, t420, t430, t440p, t480, t500

GUIDES_REGISTRY = {
    "x200": x200.get_guide,
    "x201": x201.get_guide,
    "x220": x220.get_guide,
    "x230": x230.get_guide,
    "t400": t400.get_guide,
    "t420": t420.get_guide,
    "t430": t430.get_guide,
    "t440p": t440p.get_guide,
    "t480": t480.get_guide,
    "t500": t500.get_guide,
}

def get_disassembly_guide(model_id: str) -> Optional[Guide]:
    """Retrieve the disassembly guide for a given model ID."""
    factory = GUIDES_REGISTRY.get(model_id.lower())
    if factory:
        return factory()
    return None
