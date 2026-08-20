"""
ThinkPad X200 disassembly guide.
"""
from autoloader.guides.guide_engine import Guide, GuideStep

def get_guide() -> Guide:
    return Guide(
        title="ThinkPad X200 Disassembly Guide",
        description="Step-by-step guide to access the flash chip on the Lenovo ThinkPad X200.",
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
                    "Slide the battery release latches and remove the main battery.",
                    "Remove the CMOS battery if accessible (often under keyboard/palmrest)."
                ],
                warning="Always remove all power before disassembling to avoid short circuits.",
                estimated_time_minutes=2
            ),
            GuideStep(
                step_number=2,
                title="Remove Keyboard",
                description="Remove the keyboard to access the palmrest screws.",
                substeps=[
                    "Remove the screws on the bottom of the laptop marked with a keyboard icon.",
                    "Flip the laptop over and open the lid.",
                    "Push the keyboard toward the screen (latches on back edge, no screws on top).",
                    "Lift the bottom edge of the keyboard up.",
                    "Gently disconnect the ribbon cable from the motherboard."
                ],
                reference="Lenovo X200 HMM Chapter 9",
                estimated_time_minutes=5
            ),
            GuideStep(
                step_number=3,
                title="Remove Palmrest",
                description="Remove the palmrest to expose the motherboard.",
                substeps=[
                    "Remove the screws on the bottom of the laptop marked with a square/touchpad icon.",
                    "Pry the palmrest up gently from the edges.",
                    "Disconnect the trackpad/fingerprint ribbon cable from the motherboard.",
                    "Lift the palmrest away completely."
                ],
                tip="Keep track of which screws go where; they might have different lengths.",
                estimated_time_minutes=5
            ),
            GuideStep(
                step_number=4,
                title="Locate Flash Chip",
                description="Find the SPI flash chip on the motherboard.",
                substeps=[
                    "Look in the area previously covered by the right side of the palmrest.",
                    "Identify the 8-pin SOIC chip. Common markings include Macronix (MX) or Winbond (W).",
                    "Note the dimple on the chip which indicates Pin 1."
                ],
                diagram=(
                    "    ThinkPad X200 Motherboard (Top View)\n"
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
                    "    │                                      │\n"
                    "    │                           ┌──────┐   │\n"
                    "    │                           │4/8MB │◄── Flash Chip\n"
                    "    │                           │SOIC-8│   (Under Palmrest)\n"
                    "    │                           └──────┘   │\n"
                    "    └──────────────────────────────────────┘"
                ),
                reference="Lenovo X200 HMM Chapter 9",
                estimated_time_minutes=3
            )
        ]
    )
