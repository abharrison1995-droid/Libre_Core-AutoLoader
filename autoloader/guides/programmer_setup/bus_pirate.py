"""
Bus Pirate programmer setup guide.
"""
from autoloader.guides.guide_engine import Guide, GuideStep

def get_guide() -> Guide:
    return Guide(
        title="Bus Pirate Setup Guide",
        description="Step-by-step guide to using the Bus Pirate as an SPI flasher.",
        total_estimated_time=15,
        difficulty="moderate",
        tools_required=["Bus Pirate", "Pomona SOIC-8 Clip", "Jumper wires"],
        prerequisites=["Bus Pirate connected to PC"],
        steps=[
            GuideStep(
                step_number=1,
                title="Firmware Check",
                description="Ensure your Bus Pirate has the latest firmware.",
                substeps=[
                    "It is highly recommended to update your Bus Pirate firmware to the latest community version for stability.",
                    "Older firmware might have SPI bugs or be significantly slower."
                ],
                estimated_time_minutes=5
            ),
            GuideStep(
                step_number=2,
                title="Wiring the SPI Clip",
                description="Connect the Pomona clip to the Bus Pirate.",
                substeps=[
                    "Match the SPI pins (CS, MISO, MOSI, SCK, VCC, GND) between the Bus Pirate header and the SOIC clip.",
                    "Ensure you use the 3.3V VCC output, NOT 5V."
                ],
                diagram=(
                    "    Bus Pirate v3.6 Pinout\n"
                    "    ┌───────────────────────────────────┐\n"
                    "    │ MISO  | CS    | MISO  | MOSI      │\n"
                    "    │ GND   | 3.3V  | 5.0V  | ADC       │\n"
                    "    └───────────────────────────────────┘\n"
                    "    (Check your specific Bus Pirate version's reference card)"
                ),
                warning="The Bus Pirate is considerably slower than the CH341A or Raspberry Pi. Reading an 8MB chip can take 15-30 minutes.",
                estimated_time_minutes=10
            ),
            GuideStep(
                step_number=3,
                title="Testing the Connection",
                description="Verify the SPI connection with flashrom.",
                substeps=[
                    "Attach the clip to the flash chip, ensuring Pin 1 aligns correctly.",
                    "Run command: 'flashrom -p buspirate_spi:dev=/dev/ttyUSB0'",
                    "(Adjust /dev/ttyUSB0 to match your system's serial port, e.g., COM3 on Windows)"
                ],
                estimated_time_minutes=0
            )
        ]
    )
