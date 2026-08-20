"""
ThinkPad X230 disassembly guide.
"""
from autoloader.guides.guide_engine import Guide, GuideStep

def get_guide() -> Guide:
    return Guide(
        title="ThinkPad X230 Disassembly Guide",
        description="Step-by-step guide to access the TWO flash chips on the Lenovo ThinkPad X230.",
        total_estimated_time=20,
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
                    "Slide the battery release latches and remove the main battery.",
                    "Remove the CMOS battery if accessible."
                ],
                warning="Always remove all power before disassembling to avoid short circuits.",
                estimated_time_minutes=2
            ),
            GuideStep(
                step_number=2,
                title="Remove Keyboard",
                description="Remove the keyboard to access internal components.",
                substeps=[
                    "Remove the 7 screws on the bottom marked with a keyboard icon.",
                    "Flip the laptop over and open the lid.",
                    "Push the keyboard forward from the rear notches.",
                    "Lift the keyboard up and disconnect the ribbon cable from the motherboard."
                ],
                reference="Lenovo X230 HMM",
                estimated_time_minutes=5
            ),
            GuideStep(
                step_number=3,
                title="Remove Palmrest",
                description="Remove the palmrest to expose the motherboard.",
                substeps=[
                    "Remove the 5 palmrest screws from the bottom of the chassis.",
                    "Lift the palmrest gently.",
                    "Disconnect the trackpad ribbon cable from the motherboard.",
                    "Remove the palmrest completely."
                ],
                estimated_time_minutes=5
            ),
            GuideStep(
                step_number=4,
                title="Locate Flash Chips",
                description="Find the TWO SPI flash chips on the motherboard.",
                substeps=[
                    "The X230 has two chips: a 4MB chip and an 8MB chip.",
                    "Locate the 4MB chip (top) near the RAM slot area.",
                    "Locate the 8MB chip (bottom) near the ExpressCard slot.",
                    "Both chips must be flashed for full coreboot functionality."
                ],
                diagram=(
                    "    ThinkPad X230 Motherboard (Top View)\n"
                    "    ┌──────────────────────────────────────┐\n"
                    "    │  ┌─────┐                             │\n"
                    "    │  │ RAM │      ┌──────┐               │\n"
                    "    │  │SLOT │      │4MB   │◄── Flash Chip 1│\n"
                    "    │  └─────┘      │SOIC-8│    (Top)       │\n"
                    "    │               └──────┘               │\n"
                    "    │                                      │\n"
                    "    │    ┌──────────────┐                  │\n"
                    "    │    │    CPU       │                  │\n"
                    "    │    │  (BGA1023)   │                  │\n"
                    "    │    └──────────────┘                  │\n"
                    "    │                                      │\n"
                    "    │               ┌──────┐               │\n"
                    "    │               │8MB   │◄── Flash Chip 2│\n"
                    "    │  ┌─────────┐  │SOIC-8│    (Bottom)   │\n"
                    "    │  │ExpressC.│  └──────┘               │\n"
                    "    │  └─────────┘                         │\n"
                    "    └──────────────────────────────────────┘"
                ),
                tip="Make sure you backup and flash the correct image to each respective chip. They are distinct sizes (4MB and 8MB).",
                reference="Lenovo X230 HMM",
                estimated_time_minutes=5
            )
        ]
    )
