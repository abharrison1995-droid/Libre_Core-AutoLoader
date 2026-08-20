"""
Middleton BIOS Database & Reference.
Information on SATA-II 3.0Gbps unlock, mini-PCIe whitelist removal, and Fn/Ctrl swap for classic ThinkPads.
"""
from dataclasses import dataclass
from typing import Dict, List

@dataclass
class MiddletonModInfo:
    model: str
    target_generation: str
    features: List[str]
    download_url: str
    flash_method: str
    notes: str

MIDDLETON_MODS: Dict[str, MiddletonModInfo] = {
    "t500": MiddletonModInfo(
        model="ThinkPad T500 / W500",
        target_generation="Core 2 Duo / GM45",
        features=[
            "SATA-II (3.0 Gbps) full bus speed unlock (doubles SSD throughput from SATA-I)",
            "Wi-Fi / WWAN Mini-PCIe Whitelist completely removed",
            "Thermal fan table optimizations",
            "Optional Ctrl-Fn key swap",
            "SLIC 2.1 table injection"
        ],
        download_url="https://web.archive.org/web/middleton-bios/t500",
        flash_method="Bootable ISO / CD or Windows 32-bit WinPhlash utility",
        notes="Middleton BIOS is a patched Lenovo Phoenix BIOS for Penryn-generation ThinkPads. Does not require external hardware programmer."
    ),
    "x200": MiddletonModInfo(
        model="ThinkPad X200 / X200s",
        target_generation="Core 2 Duo / GM45",
        features=[
            "SATA-II 3.0 Gbps speed enabled natively",
            "Mini-PCIe WLAN whitelist removal (allows modern 802.11ac cards)",
            "Dual-IDA (Dynamic Acceleration) support on Core 2 Duo",
            "Thermal threshold adjustments"
        ],
        download_url="https://web.archive.org/web/middleton-bios/x200",
        flash_method="Bootable DOS ISO or WinPhlash32",
        notes="For X200, Libreboot/Coreboot is generally recommended over Middleton BIOS for 100% blob-free open source operation."
    ),
    "t400": MiddletonModInfo(
        model="ThinkPad T400 / R400",
        target_generation="Core 2 Duo / GM45",
        features=[
            "SATA-II 3.0 Gbps speed enabled",
            "Wi-Fi whitelist removal",
            "Optional Fn/Ctrl key swap",
            "SLIC 2.1 support"
        ],
        download_url="https://web.archive.org/web/middleton-bios/t400",
        flash_method="Bootable ISO or 32-bit Windows flasher",
        notes="Middleton mod operates within stock Phoenix BIOS framework. High stability and ease of installation."
    )
}

def get_middleton_info(model_id: str) -> MiddletonModInfo | None:
    return MIDDLETON_MODS.get(model_id.lower())
