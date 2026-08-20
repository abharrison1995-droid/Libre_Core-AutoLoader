
import pytest
from autoloader.guides.disassembly import get_disassembly_guide
from autoloader.guides.programmer_setup import get_programmer_guide
from autoloader.guides.guide_engine import GuideEngine

def test_disassembly_guides_all_models():
    models = ["x200", "x201", "x220", "x230", "t400", "t420", "t430", "t440p", "t480", "t500"]
    for m in models:
        guide = get_disassembly_guide(m)
        assert guide is not None
        assert len(guide.steps) >= 3
        assert guide.title is not None
        has_diagram = any(step.diagram is not None for step in guide.steps)
        assert has_diagram is True, f"Model {m} guide missing diagram"

def test_programmer_guides():
    programmers = ["ch341a", "raspberry_pi", "bus_pirate"]
    for prog in programmers:
        guide = get_programmer_guide(prog)
        assert guide is not None
        assert len(guide.steps) >= 2

def test_guide_engine_navigation():
    guide = get_disassembly_guide("x230")
    engine = GuideEngine(guide)
    assert engine.current_step_index == 0
    assert engine.is_last_step() is False

    step1 = engine.get_current_step()
    assert step1.step_number == 1

    engine.next_step()
    assert engine.current_step_index == 1
    engine.prev_step()
    assert engine.current_step_index == 0
