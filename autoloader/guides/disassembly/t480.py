"""
ThinkPad T480 disassembly guide.
"""
from autoloader.guides.guide_engine import Guide, GuideStep

def get_guide() -> Guide:
    return Guide(
        title="ThinkPad T480 Disassembly Guide",
        description="Step-by-step guide to access the flash chips on the Lenovo ThinkPad T480. WARNING: Boot Guard active.",
        total_estimated_time=45,
        difficulty="hard",
        tools_required=["Phillips #0 Screwdriver", "Spudger/Prying Tool", "Deguard Bypass Tool"],
        prerequisites=["Laptop powered off", "Thunderbolt firmware updated first"],
        steps=[
            GuideStep(
                step_number=1,
                title="Crucial Pre-requisite",
                description="Update Thunderbolt firmware before proceeding.",
                substeps=[
                    "You MUST update the Thunderbolt firmware in the original OS before flashing.",
                    "Failure to do so can result in a bricked Thunderbolt controller."
                ],
                warning="EXPERIMENTAL: Intel Boot Guard makes this process extremely risky. Proceed at your own risk.",
                estimated_time_minutes=0
            ),
            GuideStep(
                step_number=2,
                title="Power Disconnect",
                description="Completely disconnect all power sources from the laptop.",
                substeps=[
                    "Ensure the laptop is powered off.",
                    "Unplug the AC adapter.",
                    "Disable the internal battery in BIOS.",
                    "Remove the external hot-swappable battery."
                ],
                estimated_time_minutes=2
            ),
            GuideStep(
                step_number=3,
                title="Remove Bottom Cover",
                description="Remove the bottom cover to access the motherboard.",
                substeps=[
                    "Loosen the captive Phillips screws on the bottom cover.",
                    "Carefully pry the bottom cover starting from the rear near the hinges.",
                    "Lift the bottom cover off."
                ],
                reference="Lenovo T480 HMM",
                estimated_time_minutes=5
            ),
            GuideStep(
                step_number=4,
                title="Locate Flash Chips",
                description="Find the 16MB system chip and 1MB Thunderbolt chip.",
                substeps=[
                    "The 16MB system chip is located near the middle of the motherboard.",
                    "The 1MB Thunderbolt chip is located in the top-right area."
                ],
                diagram=(
                    "    ThinkPad T480 Motherboard (Bottom View)\n"
                    "    ┌──────────────────────────────────────┐\n"
                    "    │                            ┌──────┐  │\n"
                    "    │                            │1MB   │◄─ Thunderbolt Chip\n"
                    "    │  ┌─────────┐               │SOIC-8│  │ (Top-right)\n"
                    "    │  │ FAN     │               └──────┘  │\n"
                    "    │  └─────────┘                         │\n"
                    "    │                                      │\n"
                    "    │               ┌──────┐               │\n"
                    "    │               │16MB  │◄── System Chip │\n"
                    "    │               │SOIC-8│    (Middle)   │\n"
                    "    │               └──────┘               │\n"
                    "    │                                      │\n"
                    "    │                                      │\n"
                    "    └──────────────────────────────────────┘"
                ),
                tip="A deguard bypass tool is required due to Intel Boot Guard on this model.",
                reference="Lenovo T480 HMM",
                estimated_time_minutes=5
            )
        ]
    )
