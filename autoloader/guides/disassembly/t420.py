"""
ThinkPad T420 disassembly guide.
"""
from autoloader.guides.guide_engine import Guide, GuideStep

def get_guide() -> Guide:
    return Guide(
        title="ThinkPad T420 Disassembly Guide",
        description="Step-by-step guide to access the flash chip on the Lenovo ThinkPad T420.",
        total_estimated_time=40,
        difficulty="moderate-hard",
        tools_required=["Phillips #0 Screwdriver", "Spudger/Prying Tool", "Thermal Paste (for reassembly)"],
        prerequisites=["Laptop powered off", "Workspace cleared", "Screw organizer"],
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
                    "Remove the 7 screws on the bottom marked with a keyboard icon.",
                    "Push keyboard toward screen, lift and disconnect the ribbon cable."
                ],
                reference="Lenovo T420 HMM",
                estimated_time_minutes=5
            ),
            GuideStep(
                step_number=3,
                title="Remove Palmrest",
                description="Remove the palmrest.",
                substeps=[
                    "Remove screws marked with a palmrest icon from the bottom.",
                    "Pry up palmrest and disconnect trackpad cable."
                ],
                estimated_time_minutes=5
            ),
            GuideStep(
                step_number=4,
                title="Extract Motherboard",
                description="Full motherboard removal from the chassis is required.",
                substeps=[
                    "Disconnect all visible ribbon cables and antennas.",
                    "Remove the display assembly if necessary for clearance.",
                    "Remove the screws holding the motherboard in the base.",
                    "Lift the motherboard out of the chassis and flip it over."
                ],
                tip="Take photos of the cable routing before removal to aid in reassembly.",
                reference="Lenovo T420 HMM",
                estimated_time_minutes=20
            ),
            GuideStep(
                step_number=5,
                title="Locate Flash Chip",
                description="Find the SPI flash chip on the UNDERSIDE of the motherboard.",
                substeps=[
                    "Examine the underside of the extracted motherboard.",
                    "Locate the 8-pin SOIC chip near the bottom center.",
                    "Note the dimple on the chip which indicates Pin 1."
                ],
                diagram=(
                    "    ThinkPad T420 Motherboard (Underside View)\n"
                    "    ┌──────────────────────────────────────┐\n"
                    "    │                                      │\n"
                    "    │             ┌─────────┐              │\n"
                    "    │             │Docking  │              │\n"
                    "    │             │Connector│              │\n"
                    "    │             └─────────┘              │\n"
                    "    │                                      │\n"
                    "    │                                      │\n"
                    "    │                  ┌──────┐            │\n"
                    "    │                  │8MB   │◄── Flash Chip\n"
                    "    │                  │SOIC-8│   (Bottom center)\n"
                    "    │                  └──────┘            │\n"
                    "    │                                      │\n"
                    "    └──────────────────────────────────────┘"
                ),
                reference="Lenovo T420 HMM",
                estimated_time_minutes=5
            )
        ]
    )
