"""
ThinkPad T430 disassembly guide.
"""
from autoloader.guides.guide_engine import Guide, GuideStep

def get_guide() -> Guide:
    return Guide(
        title="ThinkPad T430 Disassembly Guide",
        description="Step-by-step guide to access the TWO flash chips on the Lenovo ThinkPad T430.",
        total_estimated_time=25,
        difficulty="moderate",
        tools_required=["Phillips #0 Screwdriver", "Spudger/Prying Tool"],
        prerequisites=["Laptop powered off", "Workspace cleared"],
        steps=[
            GuideStep(
                step_number=1,
                title="Power Disconnect",
                description="Completely disconnect all power sources from the laptop.",
                substeps=[
                    "Ensure the laptop is powered off.",
                    "Unplug the AC adapter.",
                    "Remove the main battery.",
                    "Remove the CMOS battery."
                ],
                warning="Always remove all power before disassembling to avoid short circuits.",
                estimated_time_minutes=2
            ),
            GuideStep(
                step_number=2,
                title="Remove Keyboard",
                description="Remove the keyboard to access internal components.",
                substeps=[
                    "Remove the keyboard screws from the bottom.",
                    "Flip laptop, push keyboard forward, lift and disconnect."
                ],
                reference="Lenovo T430 HMM",
                estimated_time_minutes=5
            ),
            GuideStep(
                step_number=3,
                title="Remove Palmrest",
                description="Remove the palmrest to expose the motherboard.",
                substeps=[
                    "Remove the palmrest screws from the bottom of the chassis.",
                    "Pry the palmrest up gently.",
                    "Disconnect the trackpad ribbon cable from the motherboard.",
                    "Remove the palmrest completely."
                ],
                estimated_time_minutes=8
            ),
            GuideStep(
                step_number=4,
                title="Locate Flash Chips",
                description="Find the TWO SPI flash chips on the motherboard.",
                substeps=[
                    "The T430 has two chips: a 4MB chip and an 8MB chip, similar to the X230.",
                    "Locate the chips on the exposed motherboard.",
                    "Both chips must be flashed for full coreboot functionality."
                ],
                diagram=(
                    "    ThinkPad T430 Motherboard (Top View)\n"
                    "    ┌──────────────────────────────────────┐\n"
                    "    │  ┌─────┐                             │\n"
                    "    │  │ RAM │      ┌──────┐               │\n"
                    "    │  │SLOT │      │4MB   │◄── Flash Chip 1│\n"
                    "    │  └─────┘      │SOIC-8│    (Top)       │\n"
                    "    │               └──────┘               │\n"
                    "    │                                      │\n"
                    "    │    ┌──────────────┐                  │\n"
                    "    │    │    CPU       │                  │\n"
                    "    │    │              │                  │\n"
                    "    │    └──────────────┘                  │\n"
                    "    │                                      │\n"
                    "    │               ┌──────┐               │\n"
                    "    │               │8MB   │◄── Flash Chip 2│\n"
                    "    │               │SOIC-8│    (Bottom)   │\n"
                    "    │               └──────┘               │\n"
                    "    │                                      │\n"
                    "    └──────────────────────────────────────┘"
                ),
                tip="Make sure you backup and flash the correct image to each respective chip. They are distinct sizes (4MB and 8MB).",
                reference="Lenovo T430 HMM",
                estimated_time_minutes=10
            )
        ]
    )
