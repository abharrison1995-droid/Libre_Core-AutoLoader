from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


class Generation(Enum):
    CORE2_DUO = "core2_duo"      # X200, T400, T500
    ARRANDALE = "arrandale"       # X201
    SANDY_BRIDGE = "sandy_bridge" # X220, T420
    IVY_BRIDGE = "ivy_bridge"     # X230, T430
    HASWELL = "haswell"           # T440p
    KABY_LAKE = "kaby_lake"       # T480


class ChipPackage(Enum):
    SOIC_8 = "SOIC-8"
    SOIC_16 = "SOIC-16"


class PayloadType(Enum):
    SEABIOS = "seabios"
    GRUB = "grub"
    TIANOCORE = "tianocore"


class FirmwareBase(Enum):
    COREBOOT = "coreboot"
    LIBREBOOT = "libreboot"
    CANOEBOOT = "canoeboot"


class MEStatus(Enum):
    REMOVABLE = "removable"        # Can run with NO ME (X200, T400, T500)
    NEUTERABLE = "neuterable"      # Must keep minimal ME but can neuter (X220, X230, T420, T430)
    REQUIRED = "required"          # ME required, limited modification (T440p)
    BOOT_GUARD = "boot_guard"      # ME locked by Boot Guard (T480)


@dataclass
class FlashChip:
    size_mb: int
    package: ChipPackage
    label: str  # e.g., "top", "bottom", "main", "thunderbolt"
    model_number: str  # e.g., "W25Q64FV", "MX25L6405D"
    voltage: float = 3.3


@dataclass
class DisassemblyInfo:
    difficulty: str  # "easy", "moderate", "hard"
    estimated_time_minutes: int
    tools_required: list[str]
    chip_access_description: str
    hmm_url: str
    ifixit_url: str
    screw_count: int  # screws to remove to access flash chip


@dataclass
class ThinkPadModel:
    model_id: str           # e.g., "x230"
    name: str               # e.g., "ThinkPad X230"
    generation: Generation
    chipset: str            # e.g., "QM77"
    cpu_socket: str         # e.g., "BGA1023" or "rPGA947" (socketed)
    flash_chips: list[FlashChip]
    total_flash_mb: int
    
    # Firmware support
    coreboot_board: str     # e.g., "lenovo/x230"
    coreboot_support: bool
    libreboot_support: bool
    canoeboot_support: bool
    supported_payloads: list[PayloadType]
    
    # 1vyrain support
    supports_1vyrain: bool
    required_bios_version: Optional[str]  # version needed for 1vyrain
    
    # ME status
    me_status: MEStatus
    
    # Flashing
    internal_flash_possible: bool
    internal_flash_condition: str  # e.g., "After 1vyrain", "After initial coreboot flash"
    
    # EC
    ec_moddable: bool
    ec_mods_available: list[str]  # e.g., ["classic_keyboard", "battery_whitelist"]
    
    # Disassembly
    disassembly: DisassemblyInfo
    
    # Required blobs
    requires_me_blob: bool
    requires_descriptor_blob: bool
    requires_gbe_blob: bool
    requires_mrc_blob: bool
    supports_native_vga_init: bool  # Can use libgfxinit instead of VGA BIOS
    
    # Warnings/notes
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def is_dual_chip(self) -> bool:
        """Returns True if this model uses two physically distinct SPI chips (bottom + top)."""
        labels = {c.label.lower() for c in self.flash_chips}
        return "bottom" in labels and "top" in labels


THINKPAD_MODELS = {
    "x200": ThinkPadModel(
        model_id="x200",
        name="ThinkPad X200",
        generation=Generation.CORE2_DUO,
        chipset="ICH9-M",
        cpu_socket="BGA479",
        flash_chips=[
            FlashChip(size_mb=4, package=ChipPackage.SOIC_8, label="main", model_number="MX25L3205D"),
            FlashChip(size_mb=8, package=ChipPackage.SOIC_16, label="main", model_number="MX25L6405D")
        ],
        total_flash_mb=8,  # Can vary, using max common
        coreboot_board="lenovo/x200",
        coreboot_support=True,
        libreboot_support=True,
        canoeboot_support=True,
        supported_payloads=[PayloadType.SEABIOS, PayloadType.GRUB, PayloadType.TIANOCORE],
        supports_1vyrain=False,
        required_bios_version=None,
        me_status=MEStatus.REMOVABLE,
        internal_flash_possible=False,
        internal_flash_condition="External flash only",
        ec_moddable=False,
        ec_mods_available=[],
        disassembly=DisassemblyInfo(
            difficulty="hard",
            estimated_time_minutes=60,
            tools_required=["Phillips #1 screwdriver", "Phillips #0 screwdriver", "Spudger"],
            chip_access_description="Near-complete teardown required. Motherboard must be removed to access the chip on the bottom.",
            hmm_url="https://download.lenovo.com/ibmdl/pub/pc/pccbbs/mobiles_pdf/x200_x200s_x200si_x201_x201i_x201s_hmm_en_43y6632_11.pdf",
            ifixit_url="https://www.ifixit.com/Device/Lenovo_ThinkPad_X200",
            screw_count=35
        ),
        requires_me_blob=False,
        requires_descriptor_blob=False,
        requires_gbe_blob=True,
        requires_mrc_blob=False,
        supports_native_vga_init=True,
        warnings=[],
        notes=["ME is fully removable", "Flash chip size and package vary (4MB or 8MB, SOIC-8 or SOIC-16)"]
    ),
    "x201": ThinkPadModel(
        model_id="x201",
        name="ThinkPad X201",
        generation=Generation.ARRANDALE,
        chipset="QM57/HM55",
        cpu_socket="BGA1288",
        flash_chips=[
            FlashChip(size_mb=8, package=ChipPackage.SOIC_8, label="main", model_number="W25Q64BV")
        ],
        total_flash_mb=8,
        coreboot_board="lenovo/x201",
        coreboot_support=True,
        libreboot_support=False,
        canoeboot_support=True,
        supported_payloads=[PayloadType.SEABIOS, PayloadType.GRUB, PayloadType.TIANOCORE],
        supports_1vyrain=False,
        required_bios_version=None,
        me_status=MEStatus.NEUTERABLE,
        internal_flash_possible=False,
        internal_flash_condition="External flash only",
        ec_moddable=False,
        ec_mods_available=[],
        disassembly=DisassemblyInfo(
            difficulty="hard",
            estimated_time_minutes=60,
            tools_required=["Phillips #1 screwdriver", "Phillips #0 screwdriver", "Spudger"],
            chip_access_description="Near-complete teardown required to remove motherboard and access flash chip.",
            hmm_url="https://download.lenovo.com/ibmdl/pub/pc/pccbbs/mobiles_pdf/x200_x200s_x200si_x201_x201i_x201s_hmm_en_43y6632_11.pdf",
            ifixit_url="https://www.ifixit.com/Device/Lenovo_ThinkPad_X201",
            screw_count=35
        ),
        requires_me_blob=True,
        requires_descriptor_blob=True,
        requires_gbe_blob=True,
        requires_mrc_blob=True,
        supports_native_vga_init=True,
        warnings=["Libreboot dropped support due to fan failures from ME neutering."],
        notes=[]
    ),
    "x220": ThinkPadModel(
        model_id="x220",
        name="ThinkPad X220",
        generation=Generation.SANDY_BRIDGE,
        chipset="QM67",
        cpu_socket="BGA1023",
        flash_chips=[
            FlashChip(size_mb=8, package=ChipPackage.SOIC_8, label="main", model_number="W25Q64CV")
        ],
        total_flash_mb=8,
        coreboot_board="lenovo/x220",
        coreboot_support=True,
        libreboot_support=True,
        canoeboot_support=True,
        supported_payloads=[PayloadType.SEABIOS, PayloadType.GRUB, PayloadType.TIANOCORE],
        supports_1vyrain=False,
        required_bios_version=None,
        me_status=MEStatus.NEUTERABLE,
        internal_flash_possible=True,
        internal_flash_condition="After initial coreboot external flash",
        ec_moddable=False,
        ec_mods_available=[],
        disassembly=DisassemblyInfo(
            difficulty="moderate",
            estimated_time_minutes=45,
            tools_required=["Phillips #1 screwdriver", "Phillips #0 screwdriver", "Spudger"],
            chip_access_description="Requires removing keyboard, palmrest, and motherboard to access the flash chip.",
            hmm_url="https://download.lenovo.com/ibmdl/pub/pc/pccbbs/mobiles_pdf/0a60739.pdf",
            ifixit_url="https://www.ifixit.com/Device/Lenovo_ThinkPad_X220",
            screw_count=25
        ),
        requires_me_blob=True,
        requires_descriptor_blob=True,
        requires_gbe_blob=True,
        requires_mrc_blob=True,
        supports_native_vga_init=True,
        warnings=[],
        notes=["Single 8MB flash chip"]
    ),
    "x230": ThinkPadModel(
        model_id="x230",
        name="ThinkPad X230",
        generation=Generation.IVY_BRIDGE,
        chipset="QM77",
        cpu_socket="BGA1023",
        flash_chips=[
            FlashChip(size_mb=8, package=ChipPackage.SOIC_8, label="bottom", model_number="W25Q64FV"),
            FlashChip(size_mb=4, package=ChipPackage.SOIC_8, label="top", model_number="W25Q32FV")
        ],
        total_flash_mb=12,
        coreboot_board="lenovo/x230",
        coreboot_support=True,
        libreboot_support=True,
        canoeboot_support=True,
        supported_payloads=[PayloadType.SEABIOS, PayloadType.GRUB, PayloadType.TIANOCORE],
        supports_1vyrain=True,
        required_bios_version="2.60",
        me_status=MEStatus.NEUTERABLE,
        internal_flash_possible=True,
        internal_flash_condition="Internal via 1vyrain (BIOS region) or after external flash of descriptors",
        ec_moddable=True,
        ec_mods_available=["classic_keyboard", "battery_whitelist"],
        disassembly=DisassemblyInfo(
            difficulty="moderate",
            estimated_time_minutes=30,
            tools_required=["Phillips #1 screwdriver", "Spudger"],
            chip_access_description="Remove keyboard and palmrest. Chips are located under the palmrest. Motherboard removal not strictly required for flashing.",
            hmm_url="https://download.lenovo.com/ibmdl/pub/pc/pccbbs/mobiles_pdf/x230_x230i_hmm_en_0b48666_01.pdf",
            ifixit_url="https://www.ifixit.com/Device/Lenovo_ThinkPad_X230",
            screw_count=14
        ),
        requires_me_blob=True,
        requires_descriptor_blob=True,
        requires_gbe_blob=True,
        requires_mrc_blob=True,
        supports_native_vga_init=True,
        warnings=[],
        notes=["Dual flash chips (4MB + 8MB). Top is 4MB (BIOS), bottom is 8MB (ME + Descriptor + GbE)."]
    ),
    "t400": ThinkPadModel(
        model_id="t400",
        name="ThinkPad T400",
        generation=Generation.CORE2_DUO,
        chipset="GM45/ICH9-M",
        cpu_socket="PGA478",
        flash_chips=[
            FlashChip(size_mb=4, package=ChipPackage.SOIC_8, label="main", model_number="MX25L3205D"),
            FlashChip(size_mb=8, package=ChipPackage.SOIC_16, label="main", model_number="MX25L6405D")
        ],
        total_flash_mb=8,
        coreboot_board="lenovo/t400",
        coreboot_support=True,
        libreboot_support=True,
        canoeboot_support=True,
        supported_payloads=[PayloadType.SEABIOS, PayloadType.GRUB, PayloadType.TIANOCORE],
        supports_1vyrain=False,
        required_bios_version=None,
        me_status=MEStatus.REMOVABLE,
        internal_flash_possible=False,
        internal_flash_condition="External flash only",
        ec_moddable=False,
        ec_mods_available=[],
        disassembly=DisassemblyInfo(
            difficulty="hard",
            estimated_time_minutes=60,
            tools_required=["Phillips #1 screwdriver", "Phillips #0 screwdriver", "Spudger"],
            chip_access_description="Near-complete teardown required. Motherboard must be removed to access the chip.",
            hmm_url="https://download.lenovo.com/ibmdl/pub/pc/pccbbs/mobiles_pdf/43y6629_05.pdf",
            ifixit_url="https://www.ifixit.com/Device/Lenovo_ThinkPad_T400",
            screw_count=40
        ),
        requires_me_blob=False,
        requires_descriptor_blob=False,
        requires_gbe_blob=True,
        requires_mrc_blob=False,
        supports_native_vga_init=True,
        warnings=[],
        notes=["ME is fully removable", "Flash chip size and package vary (4MB or 8MB, SOIC-8 or SOIC-16)"]
    ),
    "t420": ThinkPadModel(
        model_id="t420",
        name="ThinkPad T420",
        generation=Generation.SANDY_BRIDGE,
        chipset="QM67",
        cpu_socket="rPGA988B",
        flash_chips=[
            FlashChip(size_mb=8, package=ChipPackage.SOIC_8, label="main", model_number="W25Q64CV")
        ],
        total_flash_mb=8,
        coreboot_board="lenovo/t420",
        coreboot_support=True,
        libreboot_support=True,
        canoeboot_support=True,
        supported_payloads=[PayloadType.SEABIOS, PayloadType.GRUB, PayloadType.TIANOCORE],
        supports_1vyrain=False,
        required_bios_version=None,
        me_status=MEStatus.NEUTERABLE,
        internal_flash_possible=True,
        internal_flash_condition="After initial coreboot external flash",
        ec_moddable=False,
        ec_mods_available=[],
        disassembly=DisassemblyInfo(
            difficulty="moderate",
            estimated_time_minutes=45,
            tools_required=["Phillips #1 screwdriver", "Phillips #0 screwdriver", "Spudger"],
            chip_access_description="Full motherboard removal required to access the flash chip on the bottom of the board.",
            hmm_url="https://download.lenovo.com/ibmdl/pub/pc/pccbbs/mobiles_pdf/t420_t420i_hmm.pdf",
            ifixit_url="https://www.ifixit.com/Device/Lenovo_ThinkPad_T420",
            screw_count=30
        ),
        requires_me_blob=True,
        requires_descriptor_blob=True,
        requires_gbe_blob=True,
        requires_mrc_blob=True,
        supports_native_vga_init=True,
        warnings=[],
        notes=[]
    ),
    "t430": ThinkPadModel(
        model_id="t430",
        name="ThinkPad T430",
        generation=Generation.IVY_BRIDGE,
        chipset="QM77",
        cpu_socket="rPGA988B",
        flash_chips=[
            FlashChip(size_mb=8, package=ChipPackage.SOIC_8, label="bottom", model_number="W25Q64FV"),
            FlashChip(size_mb=4, package=ChipPackage.SOIC_8, label="top", model_number="W25Q32FV")
        ],
        total_flash_mb=12,
        coreboot_board="lenovo/t430",
        coreboot_support=True,
        libreboot_support=True,
        canoeboot_support=True,
        supported_payloads=[PayloadType.SEABIOS, PayloadType.GRUB, PayloadType.TIANOCORE],
        supports_1vyrain=True,
        required_bios_version="2.64",
        me_status=MEStatus.NEUTERABLE,
        internal_flash_possible=True,
        internal_flash_condition="Internal via 1vyrain (BIOS region)",
        ec_moddable=True,
        ec_mods_available=["classic_keyboard", "battery_whitelist"],
        disassembly=DisassemblyInfo(
            difficulty="moderate",
            estimated_time_minutes=45,
            tools_required=["Phillips #1 screwdriver", "Spudger"],
            chip_access_description="Requires removing keyboard, palmrest, and magnesium rollcage structure. Motherboard removal not strictly required.",
            hmm_url="https://download.lenovo.com/ibmdl/pub/pc/pccbbs/mobiles_pdf/t430_t430i_hmm_en_0b48304_03.pdf",
            ifixit_url="https://www.ifixit.com/Device/Lenovo_ThinkPad_T430",
            screw_count=20
        ),
        requires_me_blob=True,
        requires_descriptor_blob=True,
        requires_gbe_blob=True,
        requires_mrc_blob=True,
        supports_native_vga_init=True,
        warnings=[],
        notes=["Dual flash chips (4MB + 8MB)."]
    ),
    "t440p": ThinkPadModel(
        model_id="t440p",
        name="ThinkPad T440p",
        generation=Generation.HASWELL,
        chipset="QM87/HM86",
        cpu_socket="rPGA947",
        flash_chips=[
            FlashChip(size_mb=8, package=ChipPackage.SOIC_8, label="bottom", model_number="W25Q64FV"),
            FlashChip(size_mb=4, package=ChipPackage.SOIC_8, label="top", model_number="W25Q32FV")
        ],
        total_flash_mb=12,
        coreboot_board="lenovo/t440p",
        coreboot_support=True,
        libreboot_support=True,
        canoeboot_support=True,
        supported_payloads=[PayloadType.SEABIOS, PayloadType.GRUB, PayloadType.TIANOCORE],
        supports_1vyrain=False,
        required_bios_version=None,
        me_status=MEStatus.REQUIRED,
        internal_flash_possible=False,
        internal_flash_condition="External flash required",
        ec_moddable=False,
        ec_mods_available=[],
        disassembly=DisassemblyInfo(
            difficulty="easy",
            estimated_time_minutes=15,
            tools_required=["Phillips #1 screwdriver", "Spudger"],
            chip_access_description="Easy access to the 4MB chip under the main bottom door. 8MB chip requires some disassembly.",
            hmm_url="https://download.lenovo.com/ibmdl/pub/pc/pccbbs/mobiles_pdf/t440p_hmm_en_sp40a25467_04.pdf",
            ifixit_url="https://www.ifixit.com/Device/Lenovo_ThinkPad_T440p",
            screw_count=4
        ),
        requires_me_blob=True,
        requires_descriptor_blob=True,
        requires_gbe_blob=True,
        requires_mrc_blob=True,
        supports_native_vga_init=True,
        warnings=["ME is strictly required, limited modification possible."],
        notes=["Last socketed CPU ThinkPad."]
    ),
    "t480": ThinkPadModel(
        model_id="t480",
        name="ThinkPad T480",
        generation=Generation.KABY_LAKE,
        chipset="100-series",
        cpu_socket="BGA1356",
        flash_chips=[
            FlashChip(size_mb=16, package=ChipPackage.SOIC_8, label="main", model_number="W25Q128JV"),
            FlashChip(size_mb=1, package=ChipPackage.SOIC_8, label="thunderbolt", model_number="W25Q80DV")
        ],
        total_flash_mb=16,
        coreboot_board="lenovo/t480",
        coreboot_support=True,
        libreboot_support=True,
        canoeboot_support=False,
        supported_payloads=[PayloadType.SEABIOS, PayloadType.TIANOCORE],
        supports_1vyrain=False,
        required_bios_version=None,
        me_status=MEStatus.BOOT_GUARD,
        internal_flash_possible=False,
        internal_flash_condition="External flash required (deguard bypass)",
        ec_moddable=False,
        ec_mods_available=[],
        disassembly=DisassemblyInfo(
            difficulty="moderate",
            estimated_time_minutes=30,
            tools_required=["Phillips #1 screwdriver", "Spudger", "Prying tool"],
            chip_access_description="Remove bottom cover and internal battery to access main flash chip. Thunderbolt chip requires further disassembly.",
            hmm_url="https://download.lenovo.com/pccbbs/mobiles_pdf/t480_hmm_en.pdf",
            ifixit_url="https://www.ifixit.com/Device/Lenovo_ThinkPad_T480",
            screw_count=8
        ),
        requires_me_blob=True,
        requires_descriptor_blob=True,
        requires_gbe_blob=True,
        requires_mrc_blob=True,
        supports_native_vga_init=True,
        warnings=["EXPERIMENTAL", "Requires deguard bypass", "Thunderbolt FW must be updated first"],
        notes=["ME is boot guard locked. Special bypass required."]
    ),
    "t500": ThinkPadModel(
        model_id="t500",
        name="ThinkPad T500",
        generation=Generation.CORE2_DUO,
        chipset="GM45/ICH9-M",
        cpu_socket="PGA478",
        flash_chips=[
            FlashChip(size_mb=4, package=ChipPackage.SOIC_8, label="main", model_number="MX25L3205D"),
            FlashChip(size_mb=8, package=ChipPackage.SOIC_16, label="main", model_number="MX25L6405D")
        ],
        total_flash_mb=8,
        coreboot_board="lenovo/t500",
        coreboot_support=True,
        libreboot_support=True,
        canoeboot_support=True,
        supported_payloads=[PayloadType.SEABIOS, PayloadType.GRUB, PayloadType.TIANOCORE],
        supports_1vyrain=False,
        required_bios_version=None,
        me_status=MEStatus.REMOVABLE,
        internal_flash_possible=False,
        internal_flash_condition="External flash only",
        ec_moddable=False,
        ec_mods_available=[],
        disassembly=DisassemblyInfo(
            difficulty="hard",
            estimated_time_minutes=60,
            tools_required=["Phillips #1 screwdriver", "Phillips #0 screwdriver", "Spudger"],
            chip_access_description="Near-complete teardown required. Similar to T400.",
            hmm_url="https://download.lenovo.com/ibmdl/pub/pc/pccbbs/mobiles_pdf/43y6630_04.pdf",
            ifixit_url="https://www.ifixit.com/Device/Lenovo_ThinkPad_T500",
            screw_count=40
        ),
        requires_me_blob=False,
        requires_descriptor_blob=False,
        requires_gbe_blob=True,
        requires_mrc_blob=False,
        supports_native_vga_init=True,
        warnings=[],
        notes=["ME is fully removable"]
    ),
    "w530": ThinkPadModel(
        model_id="w530",
        name="ThinkPad W530",
        generation=Generation.IVY_BRIDGE,
        chipset="QM77",
        cpu_socket="PGA988",
        flash_chips=[
            FlashChip(size_mb=8, package=ChipPackage.SOIC_8, label="bottom", model_number="MX25L6405D"),
            FlashChip(size_mb=4, package=ChipPackage.SOIC_8, label="top", model_number="MX25L3205D")
        ],
        total_flash_mb=12,
        coreboot_board="lenovo/w530",
        coreboot_support=True,
        libreboot_support=True,
        canoeboot_support=True,
        supported_payloads=[PayloadType.SEABIOS, PayloadType.GRUB, PayloadType.TIANOCORE],
        supports_1vyrain=True,
        required_bios_version="2.58",
        me_status=MEStatus.NEUTERABLE,
        internal_flash_possible=True,
        internal_flash_condition="Via 1vyrain on BIOS 2.58",
        ec_moddable=True,
        ec_mods_available=["classic_keyboard", "battery_whitelist"],
        disassembly=DisassemblyInfo(
            difficulty="medium",
            estimated_time_minutes=25,
            tools_required=["Phillips #0 screwdriver", "Phillips #1 screwdriver", "Spudger"],
            chip_access_description="Keyboard and palmrest removal gives access to top chip; bottom chip under magnesium rollcage.",
            hmm_url="https://download.lenovo.com/ibmdl/pub/pc/pccbbs/mobiles_pdf/0b48474.pdf",
            ifixit_url="https://www.ifixit.com/Device/Lenovo_ThinkPad_W530",
            screw_count=7
        ),
        requires_me_blob=True,
        requires_descriptor_blob=True,
        requires_gbe_blob=True,
        requires_mrc_blob=True,
        supports_native_vga_init=True,
        warnings=["Nvidia Optimus dual-GPU - SeaBIOS text mode or native init recommended"],
        notes=["Requires dual chip flashing (8MB bottom + 4MB top) or 1vyrain software flash on BIOS 2.58"]
    ),
    "t530": ThinkPadModel(
        model_id="t530",
        name="ThinkPad T530",
        generation=Generation.IVY_BRIDGE,
        chipset="QM77",
        cpu_socket="PGA988",
        flash_chips=[
            FlashChip(size_mb=8, package=ChipPackage.SOIC_8, label="bottom", model_number="MX25L6405D"),
            FlashChip(size_mb=4, package=ChipPackage.SOIC_8, label="top", model_number="MX25L3205D")
        ],
        total_flash_mb=12,
        coreboot_board="lenovo/t530",
        coreboot_support=True,
        libreboot_support=True,
        canoeboot_support=True,
        supported_payloads=[PayloadType.SEABIOS, PayloadType.GRUB, PayloadType.TIANOCORE],
        supports_1vyrain=True,
        required_bios_version="2.60",
        me_status=MEStatus.NEUTERABLE,
        internal_flash_possible=True,
        internal_flash_condition="Via 1vyrain on BIOS 2.60",
        ec_moddable=True,
        ec_mods_available=["classic_keyboard", "battery_whitelist"],
        disassembly=DisassemblyInfo(
            difficulty="medium",
            estimated_time_minutes=25,
            tools_required=["Phillips #0 screwdriver", "Phillips #1 screwdriver", "Spudger"],
            chip_access_description="Keyboard and palmrest removal gives access to top chip; bottom chip under magnesium rollcage.",
            hmm_url="https://download.lenovo.com/ibmdl/pub/pc/pccbbs/mobiles_pdf/0b48474.pdf",
            ifixit_url="https://www.ifixit.com/Device/Lenovo_ThinkPad_T530",
            screw_count=7
        ),
        requires_me_blob=True,
        requires_descriptor_blob=True,
        requires_gbe_blob=True,
        requires_mrc_blob=True,
        supports_native_vga_init=True,
        warnings=[],
        notes=["Requires dual chip flashing (8MB bottom + 4MB top) or 1vyrain software flash on BIOS 2.60"]
    ),
    "t430s": ThinkPadModel(
        model_id="t430s",
        name="ThinkPad T430s",
        generation=Generation.IVY_BRIDGE,
        chipset="QM77",
        cpu_socket="BGA1023 (soldered)",
        flash_chips=[
            FlashChip(size_mb=8, package=ChipPackage.SOIC_8, label="bottom", model_number="MX25L6405D"),
            FlashChip(size_mb=8, package=ChipPackage.SOIC_8, label="top", model_number="MX25L6405D")
        ],
        total_flash_mb=16,
        coreboot_board="lenovo/t430s",
        coreboot_support=True,
        libreboot_support=True,
        canoeboot_support=True,
        supported_payloads=[PayloadType.SEABIOS, PayloadType.GRUB, PayloadType.TIANOCORE],
        supports_1vyrain=True,
        required_bios_version="2.59",
        me_status=MEStatus.NEUTERABLE,
        internal_flash_possible=True,
        internal_flash_condition="Via 1vyrain on BIOS 2.59",
        ec_moddable=True,
        ec_mods_available=["classic_keyboard", "battery_whitelist"],
        disassembly=DisassemblyInfo(
            difficulty="medium",
            estimated_time_minutes=20,
            tools_required=["Phillips #0 screwdriver", "Phillips #1 screwdriver", "Spudger"],
            chip_access_description="Access SPI chips via bottom RAM door and palmrest removal.",
            hmm_url="https://download.lenovo.com/ibmdl/pub/pc/pccbbs/mobiles_pdf/0b48475.pdf",
            ifixit_url="https://www.ifixit.com/Device/Lenovo_ThinkPad_T430s",
            screw_count=8
        ),
        requires_me_blob=True,
        requires_descriptor_blob=True,
        requires_gbe_blob=True,
        requires_mrc_blob=True,
        supports_native_vga_init=True,
        warnings=[],
        notes=["Requires dual 8MB chip flashing or 1vyrain software flash on BIOS 2.59"]
    ),
    "x230t": ThinkPadModel(
        model_id="x230t",
        name="ThinkPad X230 Tablet",
        generation=Generation.IVY_BRIDGE,
        chipset="QM77",
        cpu_socket="BGA1023 (soldered)",
        flash_chips=[
            FlashChip(size_mb=8, package=ChipPackage.SOIC_8, label="bottom", model_number="MX25L6405D"),
            FlashChip(size_mb=4, package=ChipPackage.SOIC_8, label="top", model_number="MX25L3205D")
        ],
        total_flash_mb=12,
        coreboot_board="lenovo/x230t",
        coreboot_support=True,
        libreboot_support=True,
        canoeboot_support=True,
        supported_payloads=[PayloadType.SEABIOS, PayloadType.GRUB, PayloadType.TIANOCORE],
        supports_1vyrain=True,
        required_bios_version="2.58",
        me_status=MEStatus.NEUTERABLE,
        internal_flash_possible=True,
        internal_flash_condition="Via 1vyrain on BIOS 2.58",
        ec_moddable=True,
        ec_mods_available=["classic_keyboard", "battery_whitelist"],
        disassembly=DisassemblyInfo(
            difficulty="medium",
            estimated_time_minutes=20,
            tools_required=["Phillips #0 screwdriver", "Phillips #1 screwdriver", "Spudger"],
            chip_access_description="Remove keyboard and palmrest to access top chip near touchpad connector.",
            hmm_url="https://download.lenovo.com/ibmdl/pub/pc/pccbbs/mobiles_pdf/0b48480.pdf",
            ifixit_url="https://www.ifixit.com/Device/Lenovo_ThinkPad_X230_Tablet",
            screw_count=7
        ),
        requires_me_blob=True,
        requires_descriptor_blob=True,
        requires_gbe_blob=True,
        requires_mrc_blob=True,
        supports_native_vga_init=True,
        warnings=["Tablet digitizer and swivel hinge display"],
        notes=["Requires dual chip flashing (8MB bottom + 4MB top) or 1vyrain software flash on BIOS 2.58"]
    )
}

MODEL_ALIASES: dict[str, str] = {
    "x230i": "x230",
    "t430i": "t430",
    "x200s": "x200",
    "x201s": "x201",
    "x201i": "x201",
    "x201t": "x201",
    "x220i": "x220",
    "x220t": "x220",
    "t400s": "t400",
    "t420s": "t420",
    "t420i": "t420",
    "t500s": "t500",
    "w500": "t500",
}

_ALL_MODELS_CACHE: list[ThinkPadModel] = list(THINKPAD_MODELS.values())


def get_model(model_id: str) -> ThinkPadModel:
    """Retrieve a ThinkPadModel by its string ID or variant alias.
    
    Args:
        model_id: The string ID of the model (e.g., 'x230', 'x230t')
        
    Returns:
        ThinkPadModel instance.
        
    Raises:
        KeyError: If the model_id is not found in the database.
    """
    normalized_id = model_id.lower().strip()
    resolved_id = MODEL_ALIASES.get(normalized_id, normalized_id)
    if resolved_id not in THINKPAD_MODELS:
        raise KeyError(f"Model {model_id} not found in database.")
    return THINKPAD_MODELS[resolved_id]


def get_all_models() -> list[ThinkPadModel]:
    """Retrieve all defined ThinkPad models.
    
    Returns:
        List of all ThinkPadModel instances.
    """
    return _ALL_MODELS_CACHE

