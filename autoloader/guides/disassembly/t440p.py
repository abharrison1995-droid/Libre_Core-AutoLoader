"""
ThinkPad T440p disassembly guide.
"""
from autoloader.guides.guide_engine import Guide, GuideStep

def get_guide() -> Guide:
    return Guide(
        title="ThinkPad T440p Disassembly Guide",
        description="Step-by-step guide to access the TWO flash chips on the Lenovo ThinkPad T440p.",
        total_estimated_time=30,
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
                    "Remove the main battery."
                ],
                warning="Always remove all power before disassembling to avoid short circuits.",
                estimated_time_minutes=2
            ),
            GuideStep(
                step_number=2,
                title="Access 4MB Chip (EASY)",
                description="The 4MB chip is easily accessible under the main bottom door.",
                substeps=[
                    "Remove the two captive screws holding the large bottom door (RAM/HDD panel).",
                    "Slide the panel off.",
                    "Locate the 4MB chip on the exposed section of the motherboard."
                ],
                diagram=(
                    "    ThinkPad T440p Bottom Access (Door Removed)\n"
                    "    ┌──────────────────────────────────────┐\n"
                    "    │          ┌──────────────┐            │\n"
                    "    │          │    HDD       │            │\n"
                    "    │          └──────────────┘            │\n"
                    "    │                                      │\n"
                    "    │  ┌─────┐                             │\n"
                    "    │  │ RAM │                             │\n"
                    "    │  └─────┘          ┌──────┐           │\n"
                    "    │                   │4MB   │◄── Flash Chip 1\n"
                    "    │                   │SOIC-8│    (Easy Access)\n"
                    "    │                   └──────┘           │\n"
                    "    │                                      │\n"
                    "    └──────────────────────────────────────┘"
                ),
                tip="The T440p is the last ThinkPad with a socketed CPU. Consider a CPU upgrade while you have it open!",
                reference="Lenovo T440p HMM",
                estimated_time_minutes=5
            ),
            GuideStep(
                step_number=3,
                title="Access 8MB Chip (DEEPER)",
                description="The 8MB chip requires removing the base cover.",
                substeps=[
                    "Remove all remaining screws from the bottom of the laptop.",
                    "Carefully pry off the entire base cover.",
                    "Locate the 8MB chip on the motherboard."
                ],
                diagram=(
                    "    ThinkPad T440p Motherboard (Base Cover Removed)\n"
                    "    ┌──────────────────────────────────────┐\n"
                    "    │                                      │\n"
                    "    │    ┌──────────────┐                  │\n"
                    "    │    │  CPU Socket  │                  │\n"
                    "    │    └──────────────┘                  │\n"
                    "    │                                      │\n"
                    "    │                           ┌──────┐   │\n"
                    "    │                           │8MB   │◄── Flash Chip 2\n"
                    "    │                           │SOIC-8│    (Requires Teardown)\n"
                    "    │                           └──────┘   │\n"
                    "    │                                      │\n"
                    "    └──────────────────────────────────────┘"
                ),
                reference="Lenovo T440p HMM",
                estimated_time_minutes=20
            )
        ]
    )
