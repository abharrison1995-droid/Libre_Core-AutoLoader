"""
ThinkPad X220 disassembly guide.
"""
from autoloader.guides.guide_engine import Guide, GuideStep

def get_guide() -> Guide:
    return Guide(
        title="ThinkPad X220 Disassembly Guide",
        description="Step-by-step guide to access the flash chip on the Lenovo ThinkPad X220.",
        total_estimated_time=15,
        difficulty="easy",
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
                    "Slide the battery release latches and remove the main battery."
                ],
                warning="Always remove all power before disassembling to avoid short circuits.",
                estimated_time_minutes=2
            ),
            GuideStep(
                step_number=2,
                title="Remove Keyboard",
                description="Remove the keyboard to access internal components.",
                substeps=[
                    "Remove the 7 screws on the bottom of the laptop marked with a keyboard icon.",
                    "Flip the laptop over and open the lid.",
                    "Slide the keyboard slightly forward to release it.",
                    "Lift the keyboard from the rear.",
                    "Gently disconnect the ribbon cable from the motherboard."
                ],
                reference="Lenovo X220 HMM",
                estimated_time_minutes=5
            ),
            GuideStep(
                step_number=3,
                title="Remove Palmrest",
                description="Remove the palmrest to expose the motherboard.",
                substeps=[
                    "Remove the palmrest screws from the bottom of the chassis.",
                    "Pry the palmrest up gently from the edges.",
                    "Disconnect the trackpad ribbon cable from the motherboard.",
                    "Lift the palmrest away completely."
                ],
                tip="Be careful with the thin plastic clips on the edges of the palmrest.",
                estimated_time_minutes=5
            ),
            GuideStep(
                step_number=4,
                title="Locate Flash Chip",
                description="Find the SPI flash chip on the motherboard.",
                substeps=[
                    "Look at the bottom-left corner of the motherboard under the keyboard area.",
                    "Identify the 8-pin SOIC chip (usually an 8MB chip).",
                    "Note the dimple on the chip which indicates Pin 1."
                ],
                diagram=(
                    "    ThinkPad X220 Motherboard (Top View)\n"
                    "    ┌──────────────────────────────────────┐\n"
                    "    │  ┌─────────┐                         │\n"
                    "    │  │ FAN     │                         │\n"
                    "    │  │         │                         │\n"
                    "    │  └─────────┘                         │\n"
                    "    │                                      │\n"
                    "    │    ┌──────────────┐                  │\n"
                    "    │    │    CPU       │                  │\n"
                    "    │    │              │                  │\n"
                    "    │    └──────────────┘                  │\n"
                    "    │  ┌──────┐                            │\n"
                    "    │  │8MB   │◄── Flash Chip              │\n"
                    "    │  │SOIC-8│    (Bottom-left corner)    │\n"
                    "    │  └──────┘                            │\n"
                    "    └──────────────────────────────────────┘"
                ),
                reference="Lenovo X220 HMM",
                estimated_time_minutes=3
            )
        ]
    )
