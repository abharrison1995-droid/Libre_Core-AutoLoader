import json
import logging
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Dict, Any, List

from autoloader.database.models import FirmwareBase, PayloadType, THINKPAD_MODELS, get_model
from autoloader.exceptions import FirmwareNotSupportedError, PayloadNotSupportedError, ConfigurationError

logger = logging.getLogger(__name__)

@dataclass
class ROMConfiguration:
    model_id: str                    # e.g., "x230"
    firmware_base: FirmwareBase      # COREBOOT, LIBREBOOT, CANOEBOOT
    payload: PayloadType             # SEABIOS, GRUB, TIANOCORE
    remove_wifi_whitelist: bool = True
    neuter_intel_me: bool = True
    custom_bootsplash: Optional[Path] = None
    include_cpu_microcode: bool = True
    use_native_vga_init: bool = False
    ec_mod_keyboard: bool = False
    ec_mod_battery: bool = False
    me_cleaner_soft_disable: bool = True  # Set HAP bit
    me_cleaner_strip: bool = True         # Remove non-essential partitions
    stock_bios_dump_path: Optional[Path] = None  # For blob extraction
    output_path: Optional[Path] = None

class ROMConfigurator:
    """ROM Configuration Engine."""

    def __init__(self) -> None:
        self.models = THINKPAD_MODELS

    def validate(self, config: ROMConfiguration) -> bool:
        """Validate configuration against the compatibility database."""
        config.model_id = config.model_id.lower()
        try:
            model = get_model(config.model_id)
        except KeyError:
            model = None

        if not model:
            raise ConfigurationError(f"Model {config.model_id} not found in database.")

        firmware_support = {
            FirmwareBase.COREBOOT: model.coreboot_support,
            FirmwareBase.LIBREBOOT: model.libreboot_support,
            FirmwareBase.CANOEBOOT: model.canoeboot_support,
        }
        if not firmware_support.get(config.firmware_base, False):
            raise FirmwareNotSupportedError(
                f"Firmware {config.firmware_base.value} not supported on {config.model_id}"
            )

        if config.payload not in model.supported_payloads:
            raise PayloadNotSupportedError(f"Payload {config.payload.value} not supported on {config.model_id}")

        self._warn_specific_issues(config)
        return True

    def _warn_specific_issues(self, config: ROMConfiguration) -> None:
        """Warn about model-specific issues."""
        if config.model_id.lower() == "x201" and config.firmware_base == FirmwareBase.LIBREBOOT:
            logger.warning("X201 has known fan control issues with Libreboot.")
        if config.model_id.lower() == "t480":
            logger.warning("T480 uses Intel Boot Guard. Ensure you are flashing with appropriate methods and neutralizing ME carefully.")

    def generate_summary(self, config: ROMConfiguration) -> str:
        """Generate a summary of what will be built."""
        lines = [
            f"ROM Build Summary for {config.model_id.upper()}",
            f"----------------------------------------",
            f"Firmware Base: {config.firmware_base.name}",
            f"Payload: {config.payload.name}",
            f"Remove Wi-Fi Whitelist: {'Yes' if config.remove_wifi_whitelist else 'No'}",
            f"Neuter Intel ME: {'Yes' if config.neuter_intel_me else 'No'}",
            f"Custom Bootsplash: {config.custom_bootsplash if config.custom_bootsplash else 'Default'}",
            f"CPU Microcode: {'Included' if config.include_cpu_microcode else 'Excluded'}",
            f"VGA Init: {'Native' if config.use_native_vga_init else 'Option ROM'}",
            f"EC Mods: Keyboard {'Yes' if config.ec_mod_keyboard else 'No'}, Battery {'Yes' if config.ec_mod_battery else 'No'}",
            f"Stock BIOS Path: {config.stock_bios_dump_path}",
            f"Output Path: {config.output_path}"
        ]
        return "\n".join(lines)

    def export_json(self, config: ROMConfiguration, path: Path) -> None:
        """Export config as JSON for the two-stage workflow."""
        data = asdict(config)
        
        # Convert Path objects to strings for serialization
        for key in ['custom_bootsplash', 'stock_bios_dump_path', 'output_path']:
            if data[key]:
                data[key] = str(data[key])
            
        data['firmware_base'] = data['firmware_base'].name
        data['payload'] = data['payload'].name
            
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = path.with_suffix(".tmp")
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
        os.replace(tmp_file, path)
        logger.info(f"Configuration exported to {path}")

    def import_json(self, path: Path) -> ROMConfiguration:
        """Import config from JSON."""
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        # Normalize model_id
        if 'model_id' in data and isinstance(data['model_id'], str):
            data['model_id'] = data['model_id'].lower()

        # Deserialize enums (supporting both .name and .value)
        if 'firmware_base' in data:
            fb_val = data['firmware_base']
            if isinstance(fb_val, str):
                try:
                    data['firmware_base'] = FirmwareBase[fb_val.upper()]
                except KeyError:
                    data['firmware_base'] = FirmwareBase(fb_val.lower())
            elif isinstance(fb_val, FirmwareBase):
                data['firmware_base'] = fb_val

        if 'payload' in data:
            pl_val = data['payload']
            if isinstance(pl_val, str):
                try:
                    data['payload'] = PayloadType[pl_val.upper()]
                except KeyError:
                    data['payload'] = PayloadType(pl_val.lower())
            elif isinstance(pl_val, PayloadType):
                data['payload'] = pl_val
        
        # Deserialize Paths
        for key in ['custom_bootsplash', 'stock_bios_dump_path', 'output_path']:
            if data.get(key):
                data[key] = Path(data[key])
            
        config = ROMConfiguration(**data)
        logger.info(f"Configuration imported from {path}")
        return config
