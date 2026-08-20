"""
CH341A programmer setup guide.
"""
from autoloader.guides.guide_engine import Guide, GuideStep

def get_guide() -> Guide:
    return Guide(
        title="CH341A Programmer Setup Guide",
        description="Step-by-step guide to setting up and modifying the CH341A SPI programmer.",
        total_estimated_time=30,
        difficulty="moderate",
        tools_required=["Soldering Iron (for 3.3V mod)", "Pomona SOIC-8 Clip", "Jumper wires"],
        prerequisites=["CH341A Black Edition (or green version)"],
        steps=[
            GuideStep(
                step_number=1,
                title="The 5V Problem and 3.3V Mod",
                description="Fixing the voltage issue on the black CH341A.",
                substeps=[
                    "The black CH341A outputs 5V on the data lines, which can damage 3.3V SPI flash chips.",
                    "Step 1: Use a soldering iron to carefully lift Pin 28 (VCC) of the CH341A chip.",
                    "Step 2: Solder a wire from the lifted Pin 28 to the middle pin of the 3.3V voltage regulator (AMS1117-3.3).",
                    "Alternatively, purchase a green PCB version which often has a built-in voltage selection jumper."
                ],
                diagram=(
                    "    CH341A Black PCB (Top View)\n"
                    "    ┌──────────────────────────────────────┐\n"
                    "    │  USB    ┌─────────┐   ┌───┐          │\n"
                    "    │ ├───┤   │ CH341A  │   │Reg│◄─ 3.3V   │\n"
                    "    │ ├───┤   │         │   └───┘   output │\n"
                    "    │         └─┬───────┘                  │\n"
                    "    │        Lift Pin 28 ──> Wire ─────────┘\n"
                    "    │                                      │\n"
                    "    │  ┌─────────────────────────┐         │\n"
                    "    │  │ ZIF SOCKET              │         │\n"
                    "    │  └─────────────────────────┘         │\n"
                    "    └──────────────────────────────────────┘"
                ),
                warning="Skipping the 3.3V mod can permanently damage your laptop's flash chip and motherboard.",
                estimated_time_minutes=20
            ),
            GuideStep(
                step_number=2,
                title="Driver Installation",
                description="Install necessary drivers for the CH341A.",
                substeps=[
                    "Windows: Download and run CH341SER.EXE to install the serial driver.",
                    "Linux: Drivers are usually built into modern kernels. No extra installation needed."
                ],
                estimated_time_minutes=5
            ),
            GuideStep(
                step_number=3,
                title="Connecting the Pomona Clip",
                description="Wire the SPI clip to the CH341A.",
                substeps=[
                    "Connect the jumper wires from the Pomona clip to the ZIF socket or breakout pins.",
                    "Match the pins according to the SPI pinout."
                ],
                diagram=(
                    "    CH341A SPI Pinout (ZIF Socket)\n"
                    "    ┌───────────────────┐\n"
                    "    │ 1 CS    │ VCC 8   │\n"
                    "    │ 2 MISO  │ HOLD 7  │\n"
                    "    │ 3 WP    │ SCK 6   │\n"
                    "    │ 4 GND   │ MOSI 5  │\n"
                    "    └───────────────────┘\n\n"
                    "    Flash Chip (SOIC-8)\n"
                    "    ┌───────────────────┐\n"
                    "    │ 1 CS    │ VCC 8   │\n"
                    "    │ 2 MISO  │ HOLD 7  │\n"
                    "    │ 3 WP    │ SCK 6   │\n"
                    "    │ 4 GND   │ MOSI 5  │\n"
                    "    └───────────────────┘"
                ),
                tip="Always align Pin 1 (CS) on the programmer with Pin 1 (dimple) on the flash chip.",
                estimated_time_minutes=5
            )
        ]
    )
