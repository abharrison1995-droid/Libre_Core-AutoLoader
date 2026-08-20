"""
ThinkPad T500 disassembly guide.
"""
from autoloader.guides.guide_engine import Guide, GuideStep

def get_guide() -> Guide:
    return Guide(
        title="ThinkPad T500 Disassembly Guide",
        description="Step-by-step guide to access the flash chip on the Lenovo ThinkPad T500.",
        total_estimated_time=45,
        difficulty="hard",
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
                title="Full Chassis Teardown",
                description="Near-complete teardown is required to access the chip.",
                substeps=[
                    "Remove the keyboard and palmrest.",
                    "Remove the display assembly.",
                    "Remove the magnesium frame.",
                    "Extract the motherboard completely from the chassis."
                ],
                tip="Keep track of screws carefully; the T500 has many different screw types.",
                reference="Lenovo T500 HMM",
                estimated_time_minutes=30
            ),
            GuideStep(
                step_number=3,
                title="Locate Flash Chip",
                description="Find the SPI flash chip on the UNDERSIDE of the motherboard.",
                substeps=[
                    "Examine the underside of the extracted motherboard.",
                    "Locate the 8-pin SOIC chip.",
                    "Note the dimple on the chip which indicates Pin 1."
                ],
                diagram=(
                    "    ThinkPad T500 Motherboard (Underside View)\n"
                    "    ┌──────────────────────────────────────┐\n"
                    "    │                                      │\n"
                    "    │             ┌─────────┐              │\n"
                    "    │             │Docking  │              │\n"
                    "    │             │Connector│              │\n"
                    "    │             └─────────┘              │\n"
                    "    │                                      │\n"
                    "    │                                      │\n"
                    "    │                  ┌──────┐            │\n"
                    "    │                  │4/8MB │◄── Flash Chip\n"
                    "    │                  │SOIC-8│   (Bottom side)\n"
                    "    │                  └──────┘            │\n"
                    "    │                                      │\n"
                    "    │                                      │\n"
                    "    └──────────────────────────────────────┘"
                ),
                reference="Lenovo T500 HMM",
                estimated_time_minutes=5
            )
        ]
    )
