import logging
from typing import Optional
from autoloader.database.models import get_model

logger = logging.getLogger(__name__)

class ClassicKeyboardMod:
    """
    Manages the Classic Keyboard (7-row) modification for xx30 series.
    """
    
    def __init__(self, model: str) -> None:
        self.model = model

    def explain(self) -> str:
        """Explains what the mod does."""
        text = (
            "=== Classic Keyboard Mod (7-row) ===\n"
            "This mod alters the Embedded Controller (EC) firmware to remap\n"
            "the matrix of the xx30 series to accept keyboards from the xx20 series (e.g. X220, T420).\n"
            "This allows you to use the classic 7-row ThinkPad keyboard on newer hardware."
        )
        logger.info(text)
        return text

    def is_compatible(self) -> bool:
        """Checks model compatibility."""
        logger.info(f"Checking keyboard mod compatibility for {self.model}...")
        try:
            m = get_model(self.model)
            compatible = m.ec_moddable and "classic_keyboard" in m.ec_mods_available
        except Exception:
            compatible = (self.model or "").lower() in ["x230", "x230t", "t430", "t430s", "t530", "w530"]
            
        if compatible:
            logger.info("Model is compatible with Classic Keyboard mod.")
        else:
            logger.warning("Model is NOT compatible. Only xx30 series are supported.")
            
        return compatible

    def configure_patch(self) -> str:
        """Returns the thinkpad-ec build command for this patch."""
        if not self.is_compatible():
            raise ValueError(f"Cannot configure patch: {self.model} is not compatible.")
            
        logger.info("Configuring thinkpad-ec for keyboard patch...")
        return "make patch_enable_keyboard"

    def get_keyboard_swap_guide(self) -> str:
        """Provides step-by-step instructions for physical keyboard swap."""
        guide = """
Physical Keyboard Swap Instructions:
1. Power off the laptop, unplug AC power, and remove the battery.
2. PIN ISOLATION (MANDATORY FOR ALL XX30 MODELS: X230, X230t, T430, T430s, T530, W530):
   - Carefully apply Kapton tape over power/backlight pins (pins 27, 28, 29 / 25, 27, 29 on the keyboard ribbon connector).
   - WARNING: Failing to isolate these pins WILL short the 5V power rails, blowing motherboard fuses or damaging the EC.
3. Unscrew the keyboard retention screws from the bottom of the chassis.
4. Slide the keyboard forward towards the display and lift the bottom edge.
5. Disconnect the stock 6-row ribbon cable.
6. Connect the taped classic 7-row (xx20) keyboard ribbon cable securely.
7. Reassemble chassis screws, attach battery, and boot.
"""
        logger.info(guide)
        return guide
