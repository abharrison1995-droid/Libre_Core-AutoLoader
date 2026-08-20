"""
ThinkPad T400 disassembly guide.
"""
from autoloader.guides.guide_engine import Guide, GuideStep

def get_guide() -> Guide:
    return Guide(
        title="ThinkPad T400 Disassembly Guide",
        description="Step-by-step guide to access the flash chip on the Lenovo ThinkPad T400.",
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
                title="Remove Keyboard and Palmrest",
                description="Remove the keyboard and palmrest as standard teardown steps.",
                substeps=[
                    "Remove screws marked with keyboard and palmrest icons from the bottom.",
                    "Push keyboard toward screen, lift and disconnect.",
                    "Pry up palmrest and disconnect trackpad cable."
                ],
                estimated_time_minutes=8
            ),
            GuideStep(
                step_number=3,
                title="Remove Display Assembly",
                description="The display assembly must be removed to access the motherboard.",
                substeps=[
                    "Disconnect the display cable from the motherboard.",
                    "Disconnect the antenna cables from the Wi-Fi card.",
                    "Remove the hinge screws from the rear and corners.",
                    "Lift the display assembly off the base."
                ],
                reference="Lenovo T400 HMM",
                estimated_time_minutes=10
            ),
            GuideStep(
                step_number=4,
                title="Remove Magnesium Frame and Motherboard",
                description="Extract the motherboard to access its underside.",
                substeps=[
                    "Remove all remaining structural screws holding the magnesium roll cage.",
                    "Carefully lift the roll cage.",
                    "Remove screws securing the motherboard to the base.",
                    "Lift the motherboard out and flip it over."
                ],
                warning="Keep track of screws carefully; the T400 has many different screw types.",
                estimated_time_minutes=15
            ),
            GuideStep(
                step_number=5,
                title="Locate Flash Chip",
                description="Find the SPI flash chip on the UNDERSIDE of the motherboard.",
                substeps=[
                    "Examine the underside of the extracted motherboard.",
                    "Locate the 8-pin SOIC chip near the middle/rear section.",
                    "Note the dimple on the chip which indicates Pin 1."
                ],
                diagram=(
                    "    ThinkPad T400 Motherboard (Underside View)\n"
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
                reference="Lenovo T400 HMM",
                estimated_time_minutes=10
            )
        ]
    )
