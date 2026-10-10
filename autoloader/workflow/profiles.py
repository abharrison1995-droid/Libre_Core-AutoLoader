"""Explicit, conservative V1 workflow allowlist."""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .types import FirmwareFamily, ProgrammerKind

MIB = 1024 * 1024
ONEVYRAIN_REVISION = "a3427b34102f8f9887acf65e23d7fd5900fe1d40"


@dataclass(frozen=True)
class V1Profile:
    profile_id: str
    model_id: str
    firmware_family: FirmwareFamily
    programmer_kind: ProgrammerKind
    accepted_mtm_prefixes: tuple[str, ...]
    fixed_chip_layout: tuple[tuple[str, int], ...] = ()
    single_chip_capacities: tuple[int, ...] = ()
    expected_artifact_size_bytes: int | None = None
    artifact_size_matches_chip: bool = False
    required_region_names: tuple[str, ...] = ()
    required_bios_region_bytes: int | None = None
    bios_numeric_threshold_exclusive: int | None = None
    bios_compatibility_source: str | None = None
    source_references: tuple[str, ...] = ()


V1_PROFILES: Mapping[str, V1Profile] = MappingProxyType({
    "t430-1vyrain": V1Profile(
        profile_id="t430-1vyrain",
        model_id="t430",
        firmware_family=FirmwareFamily.ONEVYRAIN,
        programmer_kind=ProgrammerKind.INTERNAL_ONEVYRAIN,
        accepted_mtm_prefixes=("2342", "2344", "2347", "2349", "2350", "2351"),
        fixed_chip_layout=(("bottom", 8 * MIB), ("top", 4 * MIB)),
        expected_artifact_size_bytes=4 * MIB,
        bios_numeric_threshold_exclusive=265,
        bios_compatibility_source=(
            "https://github.com/n4ru/1vyrain/blob/"
            f"{ONEVYRAIN_REVISION}/start.sh"
        ),
        source_references=(
            "https://github.com/n4ru/1vyrain",
            "https://github.com/n4ru/IVprep",
        ),
    ),
    "x200-libreboot": V1Profile(
        profile_id="x200-libreboot",
        model_id="x200",
        firmware_family=FirmwareFamily.LIBREBOOT,
        programmer_kind=ProgrammerKind.EXTERNAL_SPI,
        accepted_mtm_prefixes=("7454", "7455", "7457", "7458", "7459", "2023", "2024"),
        single_chip_capacities=(4 * MIB, 8 * MIB),
        artifact_size_matches_chip=True,
        source_references=(
            "https://libreboot.org/docs/install/x200.html",
        ),
    ),
    "x220-coreboot": V1Profile(
        profile_id="x220-coreboot",
        model_id="x220",
        firmware_family=FirmwareFamily.COREBOOT,
        programmer_kind=ProgrammerKind.EXTERNAL_SPI,
        accepted_mtm_prefixes=("4286", "4287", "4289", "4290", "4291", "4292", "4293"),
        fixed_chip_layout=(("main", 8 * MIB),),
        expected_artifact_size_bytes=8 * MIB,
        required_region_names=("ifd", "gbe", "me", "bios"),
        required_bios_region_bytes=3 * MIB,
        source_references=(
            "https://doc.coreboot.org/mainboard/lenovo/Sandy_Bridge_series.html",
        ),
    ),
})

