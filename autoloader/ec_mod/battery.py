import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

class BatteryWhitelistMod:
    """
    Manages the Battery Authentication Bypass modification.
    """
    
    def __init__(self, model: str):
        self.model = model

    def explain(self):
        """Explains what the mod does."""
        logger.info("=== Battery Whitelist Bypass ===")
        logger.info("Lenovo introduced a hardware authentication check for batteries starting with the xx30 series.")
        logger.info("Unauthenticated (3rd party or older) batteries will not charge.")
        logger.info("This mod patches the EC firmware to bypass this check, allowing any compatible battery to charge.")

    def is_compatible(self) -> bool:
        """Checks model compatibility."""
        logger.info(f"Checking battery mod compatibility for {self.model}...")
        
        compatible = self.model.lower() in ["x230", "x230t", "t430", "t430s", "t530", "w530"]
            
        if compatible:
            logger.info("Model is compatible with Battery Whitelist Bypass.")
        else:
            logger.warning("Model may not be natively supported by the standard thinkpad-ec patch.")
            self._warn_specific_models()
            
        return compatible
        
    def _warn_specific_models(self):
        """Warns about specific battery issues on other platforms."""
        if self.model.lower() in ["t440p", "t480", "x240", "x280"]:
            logger.warning(f"Note on {self.model}: Battery authentication is handled at the EC level.")
            logger.warning("Coreboot/BIOS mods alone do NOT bypass the battery check on these models.")
            logger.warning("You may need specialized EC hardware flashing to bypass the battery check on this model.")

    def configure_patch(self) -> str:
        """Returns the thinkpad-ec build command for this patch."""
        if not self.is_compatible():
            logger.warning("Proceeding with patch configuration despite compatibility warnings.")
            
        logger.info("Configuring thinkpad-ec for battery patch...")
        return "make patch_enable_battery"

    def get_battery_info(self) -> str:
        """Provides information about battery handling post-mod."""
        info = """
Post-Mod Battery Information:
- The system will no longer halt on boot with a "Genuine battery not attached" error.
- 3rd party batteries will charge normally.
- NOTE: The BIOS/EC may still report the battery as non-genuine in diagnostic menus, 
  but the charging functionality will be fully restored.
"""
        logger.info(info)
        return info
