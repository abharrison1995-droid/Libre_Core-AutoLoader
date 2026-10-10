"""Immutable, region-aware comparison of an intended image and a readback."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from autoloader.workflow.types import VerificationStatus


@dataclass(frozen=True)
class RegionReadbackSpec:
    name: str
    flash_start_byte: int
    flash_end_byte: int
    artifact_start_byte: int
    artifact_end_byte: int


@dataclass(frozen=True)
class RegionComparison:
    name: str
    expected_sha256: str
    observed_sha256: str
    matches: bool


@dataclass(frozen=True)
class ReadbackComparison:
    status: VerificationStatus
    expected_sha256: str | None
    observed_sha256: str | None
    regions: tuple[RegionComparison, ...]
    details: tuple[str, ...]
    automatic_retry: bool = False
    automatic_reboot: bool = False


class ReadbackComparator:
    """Compare full-chip bytes or explicitly mapped image regions.

    Inputs are retained as evidence. A mismatch never removes either file and
    never triggers a retry or reboot.
    """

    def compare(
        self,
        expected_path: str | Path,
        readback_path: str | Path,
        *,
        chip_capacity_bytes: int,
        regions: Sequence[RegionReadbackSpec] = (),
    ) -> ReadbackComparison:
        expected = Path(expected_path)
        readback = Path(readback_path)
        if chip_capacity_bytes <= 0:
            return _unknown("chip capacity must be positive")
        if not expected.is_file() or not readback.is_file():
            return _unknown("expected image and readback files must both exist")
        try:
            expected_bytes = expected.read_bytes()
            observed_bytes = readback.read_bytes()
        except OSError as exc:
            return _unknown(f"comparison inputs could not be read: {exc}")
        if len(observed_bytes) != chip_capacity_bytes:
            return _unknown(
                f"readback length {len(observed_bytes)} does not equal observed chip capacity {chip_capacity_bytes}"
            )
        expected_hash = _digest(expected_bytes)
        observed_hash = _digest(observed_bytes)
        if not regions:
            if len(expected_bytes) != chip_capacity_bytes:
                return _unknown(
                    f"full-chip image length {len(expected_bytes)} does not equal chip capacity {chip_capacity_bytes}"
                )
            matches = expected_hash == observed_hash
            return ReadbackComparison(
                VerificationStatus.VERIFIED if matches else VerificationStatus.FAILED,
                expected_hash,
                observed_hash,
                (),
                ("full-chip readback matches exact bytes" if matches else "full-chip readback differs; preserve both files",),
            )

        problem = _validate_region_specs(regions, len(expected_bytes), chip_capacity_bytes)
        if problem:
            return _unknown(problem, expected_hash, observed_hash)
        comparisons: list[RegionComparison] = []
        for spec in regions:
            intended = expected_bytes[spec.artifact_start_byte:spec.artifact_end_byte]
            actual = observed_bytes[spec.flash_start_byte:spec.flash_end_byte]
            intended_hash = _digest(intended)
            actual_hash = _digest(actual)
            comparisons.append(RegionComparison(
                name=spec.name,
                expected_sha256=intended_hash,
                observed_sha256=actual_hash,
                matches=intended_hash == actual_hash,
            ))
        failures = tuple(item.name for item in comparisons if not item.matches)
        status = VerificationStatus.FAILED if failures else VerificationStatus.VERIFIED
        details = (
            (f"readback differs in regions: {', '.join(failures)}; preserve both files",)
            if failures
            else ("all explicitly mapped regions match; unrelated chip regions were not compared",)
        )
        return ReadbackComparison(status, expected_hash, observed_hash, tuple(comparisons), details)


def _validate_region_specs(
    regions: Sequence[RegionReadbackSpec],
    artifact_size: int,
    chip_size: int,
) -> str | None:
    names: set[str] = set()
    flash_ranges: list[tuple[int, int]] = []
    artifact_ranges: list[tuple[int, int]] = []
    for region in regions:
        name = region.name.strip().casefold()
        if not name or name in names:
            return "region comparison names must be non-empty and unique"
        names.add(name)
        if (
            region.flash_start_byte < 0
            or region.flash_end_byte <= region.flash_start_byte
            or region.flash_end_byte > chip_size
            or region.artifact_start_byte < 0
            or region.artifact_end_byte <= region.artifact_start_byte
            or region.artifact_end_byte > artifact_size
        ):
            return f"region {region.name} has invalid or out-of-bounds comparison ranges"
        if region.flash_end_byte - region.flash_start_byte != region.artifact_end_byte - region.artifact_start_byte:
            return f"region {region.name} maps different byte lengths in artifact and chip"
        flash_ranges.append((region.flash_start_byte, region.flash_end_byte))
        artifact_ranges.append((region.artifact_start_byte, region.artifact_end_byte))
    for ranges, label in ((flash_ranges, "chip"), (artifact_ranges, "artifact")):
        ordered = sorted(ranges)
        if any(left[1] > right[0] for left, right in zip(ordered, ordered[1:])):
            return f"region comparison ranges overlap in the {label} image"
    return None


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unknown(
    detail: str,
    expected_hash: str | None = None,
    observed_hash: str | None = None,
) -> ReadbackComparison:
    return ReadbackComparison(
        VerificationStatus.UNKNOWN,
        expected_hash,
        observed_hash,
        (),
        (detail,),
    )
