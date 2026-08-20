from dataclasses import dataclass
from typing import Optional

@dataclass
class FlashChipSpec:
    manufacturer: str
    model: str
    size_mb: int
    voltage: float
    supported_by_flashrom: bool

    @property
    def model_number(self) -> str:
        return self.model

COMMON_CHIPS = {
    # Winbond
    "W25Q32FV": FlashChipSpec("Winbond", "W25Q32FV", 4, 3.3, True),
    "W25Q64FV": FlashChipSpec("Winbond", "W25Q64FV", 8, 3.3, True),
    "W25Q64CV": FlashChipSpec("Winbond", "W25Q64CV", 8, 3.3, True),
    "W25Q64BV": FlashChipSpec("Winbond", "W25Q64BV", 8, 3.3, True),
    "W25Q128JV": FlashChipSpec("Winbond", "W25Q128JV", 16, 3.3, True),
    "W25Q80DV": FlashChipSpec("Winbond", "W25Q80DV", 1, 3.3, True),
    
    # Macronix
    "MX25L3205D": FlashChipSpec("Macronix", "MX25L3205D", 4, 3.3, True),
    "MX25L6405D": FlashChipSpec("Macronix", "MX25L6405D", 8, 3.3, True),
    "MX25L3206E": FlashChipSpec("Macronix", "MX25L3206E", 4, 3.3, True),
    "MX25L6406E": FlashChipSpec("Macronix", "MX25L6406E", 8, 3.3, True),
}

def identify_chip_from_flashrom(output: str) -> Optional[FlashChipSpec]:
    """Identify a flash chip from flashrom output.
    
    Args:
        output: Raw output string from flashrom.
        
    Returns:
        The matched FlashChipSpec, or None if no known chip is found.
    """
    for model, spec in COMMON_CHIPS.items():
        if model in output:
            return spec
            
    # Sometimes flashrom reports partial matches or generic names
    if "W25Q32" in output:
        return COMMON_CHIPS["W25Q32FV"]
    if "W25Q64" in output:
        return COMMON_CHIPS["W25Q64FV"]
    if "MX25L32" in output:
        return COMMON_CHIPS["MX25L3205D"]
    if "MX25L64" in output:
        return COMMON_CHIPS["MX25L6405D"]
        
    return None


# Aliases for compatibility
identify_chip_from_output = identify_chip_from_flashrom
COMMON_FLASH_CHIPS = COMMON_CHIPS
