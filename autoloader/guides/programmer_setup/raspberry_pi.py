"""
Raspberry Pi programmer setup guide.
"""
from autoloader.guides.guide_engine import Guide, GuideStep

def get_guide() -> Guide:
    return Guide(
        title="Raspberry Pi SPI Setup Guide",
        description="Step-by-step guide to using a Raspberry Pi as an SPI flasher.",
        total_estimated_time=15,
        difficulty="easy",
        tools_required=["Raspberry Pi (any model with GPIO)", "Pomona SOIC-8 Clip", "Jumper wires (F-F)"],
        prerequisites=["Raspbian/Raspberry Pi OS installed", "Network connection to Pi"],
        steps=[
            GuideStep(
                step_number=1,
                title="Enable SPI and Install Flashrom",
                description="Configure the Raspberry Pi software.",
                substeps=[
                    "Run 'sudo raspi-config' in the terminal.",
                    "Navigate to 'Interfacing Options' -> 'SPI' and enable it.",
                    "Install flashrom: 'sudo apt update && sudo apt install flashrom'."
                ],
                estimated_time_minutes=5
            ),
            GuideStep(
                step_number=2,
                title="Wiring the SPI Clip",
                description="Connect the Pomona clip to the Pi's GPIO pins.",
                substeps=[
                    "Connect GND to Pin 25.",
                    "Connect CS to Pin 24 (GPIO 8).",
                    "Connect SCK to Pin 23 (GPIO 11).",
                    "Connect MISO to Pin 21 (GPIO 9).",
                    "Connect MOSI to Pin 19 (GPIO 10).",
                    "Connect 3.3V VCC to Pin 17."
                ],
                diagram=(
                    "    Raspberry Pi GPIO Header (Partial)\n"
                    "    ┌───────────────────────────────────┐\n"
                    "    │ Pin 17 (3.3V VCC)  │ Pin 18       │\n"
                    "    │ Pin 19 (MOSI)      │ Pin 20 (GND) │\n"
                    "    │ Pin 21 (MISO)      │ Pin 22       │\n"
                    "    │ Pin 23 (SCK)       │ Pin 24 (CS)  │\n"
                    "    │ Pin 25 (GND)       │ Pin 26       │\n"
                    "    └───────────────────────────────────┘"
                ),
                tip="Use short jumper wires (less than 15cm) for better signal integrity.",
                estimated_time_minutes=10
            ),
            GuideStep(
                step_number=3,
                title="Testing the Connection",
                description="Verify the SPI connection with flashrom.",
                substeps=[
                    "Attach the clip to the flash chip, ensuring Pin 1 aligns correctly.",
                    "Run command: 'sudo flashrom -p linux_spi:dev=/dev/spidev0.0,spispeed=1000'",
                    "If successful, flashrom will detect the chip model."
                ],
                estimated_time_minutes=0
            )
        ]
    )
