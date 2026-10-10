"""Fail-closed validation for the three explicit V1 workflow profiles.

This module intentionally does not consult the broad model database. A database
entry describes a candidate; only the independent evidence checked here can
produce an ALLOWED result.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Mapping

from .profiles import V1Profile, V1_PROFILES
from .types import (
    ArtifactEvidence,
    BackupEvidence,
    ChipEvidence,
    CompatibilityDecision,
    CompatibilityResult,
    DeviceEvidence,
    EvidenceConfidence,
    FirmwareFamily,
    OperationPlan,
    ProgrammerKind,
    RegionEvidence,
)

_MODEL_TOKEN = re.compile(r"(?<![a-z0-9])(t430|x200|x220)(?![a-z0-9])", re.I)
_UNSUPPORTED_MODEL_ALIAS = re.compile(
    r"(?<![a-z0-9])(?:t430s|x200s(?:i)?|x200\s+tablet(?:\s+pc)?|x220i)(?![a-z0-9])",
    re.I,
)
_MTM_TOKEN = re.compile(r"^([0-9]{4})[0-9A-Z-]*$", re.I)
_EMBEDDED_MTM_TOKEN = re.compile(r"(?<![A-Z0-9])([0-9]{4}[A-Z0-9-]*)(?![A-Z0-9])", re.I)
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.I)
_PINNED_REVISION = re.compile(r"^[0-9a-f]{40}$", re.I)
_T430_BIOS_TOKEN = re.compile(r"\(\s*(\d{1,2}\.\d{2})\s+\)")


class CompatibilityValidator:
    """Evaluate evidence against the tightly scoped V1 profile allowlist."""

    def __init__(self, profiles: Mapping[str, V1Profile] | None = None) -> None:
        self.profiles = dict(profiles or V1_PROFILES)

    @staticmethod
    def _result(
        profile_id: str,
        blocked: list[str],
        unknown: list[str],
        *,
        high_identity: bool,
    ) -> CompatibilityResult:
        if blocked:
            return CompatibilityResult(
                CompatibilityDecision.BLOCKED,
                profile_id,
                EvidenceConfidence.LOW,
                tuple(blocked + unknown),
            )
        if unknown:
            return CompatibilityResult(
                CompatibilityDecision.UNKNOWN,
                profile_id,
                EvidenceConfidence.LOW,
                tuple(unknown),
            )
        return CompatibilityResult(
            CompatibilityDecision.ALLOWED,
            profile_id,
            EvidenceConfidence.HIGH if high_identity else EvidenceConfidence.LOW,
            ("all required V1 evidence independently verified",),
        )

    @staticmethod
    def _explicit_model(value: str | None) -> tuple[str | None, bool]:
        """Return an exact model token and whether a nearby alias was rejected."""
        if not value:
            return None, False
        folded = value.strip().lower()
        # Reject common adjacent aliases instead of allowing a substring match.
        if _UNSUPPORTED_MODEL_ALIAS.search(folded):
            return None, True
        tokens = {match.group(1).lower() for match in _MODEL_TOKEN.finditer(folded)}
        if len(tokens) > 1:
            return None, True
        return (next(iter(tokens)), False) if tokens else (None, False)

    @staticmethod
    def _mtm_prefix(raw_mtm: str | None) -> str | None:
        if not raw_mtm:
            return None
        match = _MTM_TOKEN.fullmatch(raw_mtm.strip())
        return match.group(1).upper() if match else None

    @staticmethod
    def _pinned_family_token(raw_family: str | None) -> str | None:
        """Apply upstream's ``awk '{print $3$4}'`` to a Family line.

        Detection records the DMI value without its label on most platforms,
        so add the ``Family:`` prefix before applying the same field selection.
        Unsupported formatting is left unknown instead of being accepted by a
        broader model-name search.
        """
        if not raw_family or not raw_family.strip():
            return None
        line = raw_family.strip()
        if not line.casefold().startswith("family:"):
            line = f"Family: {line}"
        fields = line.split()
        if len(fields) < 3 or fields[0].casefold() != "family:":
            return None
        return "".join(fields[2:4]).casefold()

    @staticmethod
    def _mtm_prefixes(device: DeviceEvidence, blocked: list[str], unknown: list[str]) -> set[str]:
        found: dict[str, set[str]] = {}
        fields = {
            "raw MTM": device.raw_mtm,
            "DMI product version": device.raw_dmi_product_version,
            "DMI product name": device.raw_dmi_product_name,
        }
        for label, raw in fields.items():
            if not raw:
                continue
            tokens = {match.group(1)[:4].upper() for match in _EMBEDDED_MTM_TOKEN.finditer(raw)}
            if label == "raw MTM" and not CompatibilityValidator._mtm_prefix(raw):
                unknown.append("raw machine type/MTM evidence is malformed")
            if len(tokens) > 1:
                blocked.append(f"{label} contains contradictory MTM prefixes")
            if tokens:
                found[label] = tokens
                for prefix in tokens:
                    # Unsupported machine types must be a hard mismatch even
                    # when another DMI field happens to name an allowlisted one.
                    if prefix not in {candidate for profile in V1_PROFILES.values() for candidate in profile.accepted_mtm_prefixes}:
                        blocked.append(f"{label} contains unsupported MTM prefix {prefix}")
        all_prefixes = {prefix for prefixes in found.values() for prefix in prefixes}
        if len(all_prefixes) > 1:
            blocked.append("raw MTM, product version and product name contain conflicting MTM prefixes")
        return all_prefixes

    def _validate_identity(
        self, profile: V1Profile, device: DeviceEvidence, blocked: list[str], unknown: list[str]
    ) -> bool:
        product_model, product_alias = self._explicit_model(device.raw_dmi_product_name)
        family_model, family_alias = self._explicit_model(device.raw_dmi_system_family)
        version_model, version_alias = self._explicit_model(device.raw_dmi_product_version)
        if product_alias or family_alias or version_alias:
            blocked.append("DMI names an unsupported model alias or conflicting model tokens")
            return False
        if product_model and product_model != profile.model_id:
            blocked.append(f"DMI product name identifies {product_model}, not {profile.model_id}")
        if family_model and family_model != profile.model_id:
            blocked.append(f"DMI system family identifies {family_model}, not {profile.model_id}")
        if version_model and version_model != profile.model_id:
            blocked.append(f"DMI product version identifies {version_model}, not {profile.model_id}")
        explicit_models = {model for model in (product_model, family_model, version_model) if model}
        if len(explicit_models) > 1:
            blocked.append("independent DMI model fields conflict")

        mtm_prefixes = self._mtm_prefixes(device, blocked, unknown)
        if not mtm_prefixes:
            unknown.append("raw machine type/MTM evidence is missing or malformed")
        elif len(mtm_prefixes) == 1:
            mtm = next(iter(mtm_prefixes))
            if mtm not in profile.accepted_mtm_prefixes:
                blocked.append(f"MTM prefix {mtm} is not allowlisted for {profile.model_id}")

        if profile.bios_numeric_threshold_exclusive is not None:
            pinned_family = self._pinned_family_token(device.raw_dmi_system_family)
            if pinned_family is None:
                unknown.append("raw DMI system-family format is unsupported by the pinned upstream parser")
            elif pinned_family != profile.model_id:
                if family_model and family_model != profile.model_id:
                    blocked.append(f"pinned DMI family token identifies {pinned_family}, not {profile.model_id}")
                else:
                    unknown.append("raw DMI system-family does not match the pinned upstream token format")
        else:
            pinned_family = family_model

        if product_model is None:
            unknown.append("raw DMI product name does not independently identify an allowlisted model")
        if family_model is None:
            unknown.append("raw DMI system family does not independently identify an allowlisted model")

        if device.manual_model_candidate:
            candidate, alias = self._explicit_model(device.manual_model_candidate)
            if alias or candidate != profile.model_id:
                blocked.append("manual model candidate conflicts with the selected V1 profile")
        return bool(
            product_model == profile.model_id
            and family_model == profile.model_id
            and mtm_prefixes == {next(iter(mtm_prefixes), None)}
            and bool(mtm_prefixes & set(profile.accepted_mtm_prefixes))
            and pinned_family == profile.model_id
        )

    @staticmethod
    def _validate_bios(profile: V1Profile, device: DeviceEvidence, blocked: list[str], unknown: list[str]) -> None:
        if profile.bios_numeric_threshold_exclusive is None:
            return
        if device.stock_lenovo_bios is False:
            blocked.append("1vyrain V1 requires the supported stock Lenovo BIOS state")
        elif device.stock_lenovo_bios is None:
            unknown.append("stock Lenovo BIOS status is unknown")
        vendor = (device.raw_bios_vendor or "").strip().casefold()
        if not vendor:
            unknown.append("raw BIOS vendor evidence is missing")
        elif device.stock_lenovo_bios is not None:
            raw_vendor_is_lenovo = "lenovo" in vendor or "ibm" in vendor
            if raw_vendor_is_lenovo is not device.stock_lenovo_bios:
                blocked.append("raw BIOS vendor contradicts the stock Lenovo BIOS classification")
        raw = (device.raw_bios_version or "").strip()
        parenthesized = _T430_BIOS_TOKEN.findall(raw)
        expected_format = re.fullmatch(r"\S+\s+\(\s*\d{1,2}\.\d{2}\s+\)", raw)
        if len(parenthesized) == 1 and raw.count("(") == 1 and raw.count(")") == 1 and expected_format:
            token = parenthesized[0]
        else:
            unknown.append("T430 BIOS version is missing, ambiguous, or not in the pinned upstream DMI format")
            return
        # Upstream start.sh removes the decimal point and compares the numeric token.
        numeric_version = int(token.replace(".", ""))
        if numeric_version >= profile.bios_numeric_threshold_exclusive:
            blocked.append(
                f"T430 BIOS numeric version {numeric_version} is not below the upstream threshold "
                f"{profile.bios_numeric_threshold_exclusive}; IVprep handoff may be required"
            )

    @staticmethod
    def _validate_programmer(profile: V1Profile, plan: OperationPlan, blocked: list[str], unknown: list[str]) -> None:
        programmer = plan.programmer
        if programmer.kind is ProgrammerKind.SIMULATION:
            blocked.append("simulation programmer evidence can never authorize an operation")
            return
        if programmer.kind is not profile.programmer_kind:
            blocked.append("programmer method does not match the selected V1 profile")
            return
        if not programmer.observed:
            unknown.append("programmer method has not been physically observed")
            return
        if programmer.kind is ProgrammerKind.EXTERNAL_SPI:
            if programmer.interface_verified is False or programmer.wiring_verified is False:
                blocked.append("external programmer interface or wiring verification failed")
            elif programmer.interface_verified is not True or programmer.wiring_verified is not True:
                unknown.append("external programmer interface and wiring must be verified")
            if programmer.voltage_mv is None:
                unknown.append("external programmer output voltage is unknown")
            elif programmer.voltage_mv != 3300:
                blocked.append("external programmer must be verified at exactly 3300 mV for this profile")
            if not plan.topology.observed or not plan.topology.chips:
                unknown.append("observed chip voltage is required for every external SPI target")
            else:
                for chip in plan.topology.chips:
                    if not chip.observed:
                        unknown.append(f"chip {chip.chip_id} is not observed for voltage matching")
                    elif chip.voltage_mv is None:
                        unknown.append(f"chip {chip.chip_id} voltage is unknown")
                    elif programmer.voltage_mv is not None and chip.voltage_mv != programmer.voltage_mv:
                        blocked.append(
                            f"chip {chip.chip_id} voltage {chip.voltage_mv} mV does not match "
                            f"programmer output {programmer.voltage_mv} mV"
                        )
        elif programmer.kind is ProgrammerKind.INTERNAL_ONEVYRAIN:
            if plan.device.internal_flash_unlock_status is False:
                blocked.append("internal flash unlock was observed as unavailable")
            elif not plan.device.internal_flash_unlock_observed or plan.device.internal_flash_unlock_status is not True:
                unknown.append("internal flash unlock has not been independently observed")

    @staticmethod
    def _validate_topology(profile: V1Profile, plan: OperationPlan, blocked: list[str], unknown: list[str]) -> dict[str, ChipEvidence]:
        topology = plan.topology
        if not topology.observed:
            unknown.append("physical flash chip topology has not been observed")
            return {}
        if any(not chip.observed for chip in topology.chips):
            unknown.append("one or more listed flash chips are not physically observed")
            return {}
        if len({chip.chip_id for chip in topology.chips}) != len(topology.chips):
            blocked.append("flash topology contains duplicate chip identifiers")
            return {}
        chips = {chip.chip_id: chip for chip in topology.chips}
        if profile.fixed_chip_layout:
            expected = {label: size for label, size in profile.fixed_chip_layout}
            observed: dict[str, int] = {}
            for chip in topology.chips:
                label = (chip.physical_label or chip.chip_id).strip().lower()
                if label in observed:
                    blocked.append("flash topology contains duplicate physical chip labels")
                    continue
                observed[label] = chip.capacity_bytes
            if set(observed) != set(expected):
                unknown.append(f"observed chip labels must exactly identify {', '.join(expected)}")
            for label, expected_size in expected.items():
                if label in observed and observed[label] != expected_size:
                    blocked.append(f"{label} flash chip capacity does not match the V1 profile")
        elif profile.single_chip_capacities:
            if len(topology.chips) != 1:
                blocked.append("this profile requires exactly one observed flash chip")
            elif topology.chips[0].capacity_bytes not in profile.single_chip_capacities:
                blocked.append("observed flash chip capacity is not allowlisted for this profile")

        if profile.expected_artifact_size_bytes is not None and not profile.artifact_size_matches_chip:
            if plan.artifact.expected_size_bytes != profile.expected_artifact_size_bytes:
                blocked.append("artifact size does not match the V1 profile's expected size")
        if profile.artifact_size_matches_chip and len(topology.chips) == 1:
            if plan.artifact.expected_size_bytes != topology.chips[0].capacity_bytes:
                blocked.append("artifact size does not match the exact observed X200 chip capacity")

        if profile.required_region_names:
            regions_ok = CompatibilityValidator._validate_regions(
                profile, topology.regions, blocked, unknown, source="observed topology"
            )
            artifact_regions_ok = CompatibilityValidator._validate_regions(
                profile, plan.artifact.regions, blocked, unknown, source="firmware artifact"
            )
            if regions_ok and artifact_regions_ok:
                observed_regions = {r.name.lower(): (r.start_byte, r.end_byte) for r in topology.regions}
                artifact_regions = {r.name.lower(): (r.start_byte, r.end_byte) for r in plan.artifact.regions}
                if observed_regions != artifact_regions:
                    blocked.append("X220 artifact region layout differs from the independently observed flash layout")
        return chips

    @staticmethod
    def _validate_regions(
        profile: V1Profile,
        regions: tuple[RegionEvidence, ...],
        blocked: list[str],
        unknown: list[str],
        *,
        source: str,
    ) -> bool:
        if not regions:
            unknown.append(f"{source} lacks required X220 IFD/GBE/ME/BIOS region evidence")
            return False
        normalized: dict[str, RegionEvidence] = {}
        for region in regions:
            key = region.name.strip().lower()
            if not key or key in normalized:
                blocked.append(f"{source} has duplicate or empty firmware region names")
                continue
            if region.start_byte < 0 or region.end_byte <= region.start_byte:
                blocked.append(f"{source} has invalid region bounds")
            normalized[key] = region
        required = set(profile.required_region_names)
        if set(normalized) != required:
            missing = sorted(required - set(normalized))
            extra = sorted(set(normalized) - required)
            unknown.append(f"{source} region map incomplete or unexpected (missing={missing}, extra={extra})")
            return False
        ordered = sorted(normalized.values(), key=lambda region: region.start_byte)
        chip_size = profile.fixed_chip_layout[0][1]
        if any(region.end_byte > chip_size for region in ordered):
            blocked.append(f"{source} region exceeds the observed 8 MiB chip bounds")
        if any(left.end_byte > right.start_byte for left, right in zip(ordered, ordered[1:])):
            blocked.append(f"{source} regions overlap")
        bios = normalized.get("bios")
        if bios and bios.size_bytes != profile.required_bios_region_bytes:
            blocked.append("X220 BIOS region must be exactly 3 MiB")
        return True

    @staticmethod
    def _validate_artifact(profile: V1Profile, artifact: ArtifactEvidence, blocked: list[str], unknown: list[str]) -> None:
        if artifact.model_id != profile.model_id:
            blocked.append("artifact model does not match the selected V1 profile")
        if artifact.firmware_family is not profile.firmware_family:
            blocked.append("artifact firmware family does not match the selected V1 profile")
        if not artifact.source_url.startswith("https://"):
            blocked.append("artifact provenance must use an HTTPS source URL")
        if not _PINNED_REVISION.fullmatch(artifact.source_revision.strip()):
            blocked.append("artifact provenance must name a full immutable 40-hex source commit")
        if artifact.expected_size_bytes <= 0:
            blocked.append("artifact expected size must be positive")
        if not _SHA256.fullmatch(artifact.expected_sha256):
            blocked.append("artifact SHA-256 declaration is malformed")
            return
        path = Path(artifact.path)
        if not path.is_file():
            unknown.append("firmware artifact file is not available for verification")
            return
        if path.stat().st_size != artifact.expected_size_bytes:
            blocked.append("firmware artifact file size differs from its declaration")
            return
        try:
            digest = CompatibilityValidator._file_sha256(path)
        except OSError:
            unknown.append("firmware artifact could not be read for SHA-256 verification")
            return
        if digest.lower() != artifact.expected_sha256.lower():
            blocked.append("firmware artifact content does not match its declared SHA-256")

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _is_uniform_blank(path: Path) -> bool:
        """Reject obviously erased/zero-filled dumps as recovery evidence."""
        first_byte: int | None = None
        uniform = True
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                if not chunk:
                    continue
                if first_byte is None:
                    first_byte = chunk[0]
                if chunk[0] != first_byte or any(value != first_byte for value in chunk):
                    uniform = False
                    break
        return uniform and first_byte in (0x00, 0xFF)

    def validate(self, plan: OperationPlan) -> CompatibilityResult:
        blocked: list[str] = []
        unknown: list[str] = []
        profile = self.profiles.get(plan.profile_id)
        if profile is None:
            return CompatibilityResult(
                CompatibilityDecision.BLOCKED,
                plan.profile_id,
                EvidenceConfidence.LOW,
                ("workflow profile is not in the explicit V1 allowlist",),
            )
        if plan.requested_model_id != profile.model_id:
            blocked.append("requested model does not match the selected V1 profile")
        if plan.firmware_family is not profile.firmware_family:
            blocked.append("requested firmware family does not match the selected V1 profile")
        high_identity = self._validate_identity(profile, plan.device, blocked, unknown)
        self._validate_bios(profile, plan.device, blocked, unknown)
        self._validate_programmer(profile, plan, blocked, unknown)
        self._validate_topology(profile, plan, blocked, unknown)
        self._validate_artifact(profile, plan.artifact, blocked, unknown)
        return self._result(profile.profile_id, blocked, unknown, high_identity=high_identity)

    def validate_backup(self, plan: OperationPlan) -> CompatibilityResult:
        """Verify two stable, matching full-chip reads for every observed chip."""
        blocked: list[str] = []
        unknown: list[str] = []
        profile = self.profiles.get(plan.profile_id)
        if profile is None:
            return CompatibilityResult(
                CompatibilityDecision.BLOCKED,
                plan.profile_id,
                EvidenceConfidence.LOW,
                ("workflow profile is not in the explicit V1 allowlist",),
            )
        if not plan.topology.observed or not plan.topology.chips:
            unknown.append("complete observed chip topology is required before backup verification")
        if any(not chip.observed for chip in plan.topology.chips):
            unknown.append("backup verification cannot skip an unobserved flash chip")
        expected_ids = {chip.chip_id: chip.capacity_bytes for chip in plan.topology.chips if chip.observed}
        pairs_by_id: dict[str, list] = {}
        for pair in plan.backup.pairs:
            pairs_by_id.setdefault(pair.chip_id, []).append(pair)
        if not expected_ids:
            unknown.append("no observed chip identifiers are available for backup verification")
        if set(pairs_by_id) != set(expected_ids):
            blocked.append("backup set does not cover exactly the observed flash chip identifiers")
        for chip_id, capacity in expected_ids.items():
            pairs = pairs_by_id.get(chip_id, [])
            if len(pairs) != 1:
                blocked.append(f"chip {chip_id} must have exactly one pair of independent backup reads")
                continue
            pair = pairs[0]
            if pair.transient:
                blocked.append(f"chip {chip_id} backup evidence is marked transient/incomplete")
            if (
                not pair.first_capture_id.strip()
                or not pair.second_capture_id.strip()
                or pair.first_capture_id.strip().casefold() == pair.second_capture_id.strip().casefold()
            ):
                blocked.append(f"chip {chip_id} requires two separately identified backup captures")
            if Path(pair.first_path).name.lower().endswith((".tmp", ".partial", ".part")) or Path(pair.second_path).name.lower().endswith((".tmp", ".partial", ".part")):
                blocked.append(f"chip {chip_id} backup path is marked temporary or partial")
            if Path(pair.first_path).resolve(strict=False) == Path(pair.second_path).resolve(strict=False):
                blocked.append(f"chip {chip_id} backup reads must be separate files")
            if not _SHA256.fullmatch(pair.first_sha256) or not _SHA256.fullmatch(pair.second_sha256):
                blocked.append(f"chip {chip_id} backup SHA-256 declaration is malformed")
                continue
            paths = (Path(pair.first_path), Path(pair.second_path))
            if any(not path.is_file() for path in paths):
                unknown.append(f"chip {chip_id} backup file is not available for verification")
                continue
            try:
                if any(path.stat().st_size != capacity for path in paths):
                    blocked.append(f"chip {chip_id} backup file does not cover its exact observed chip capacity")
                    continue
                if any(self._is_uniform_blank(path) for path in paths):
                    blocked.append(f"chip {chip_id} backup contains an all-zero or all-0xFF image")
                observed_hashes = tuple(self._file_sha256(path) for path in paths)
            except OSError:
                unknown.append(f"chip {chip_id} backup files could not be read for verification")
                continue
            try:
                if os.path.samefile(paths[0], paths[1]):
                    blocked.append(f"chip {chip_id} backup reads refer to the same underlying file")
            except OSError:
                unknown.append(f"chip {chip_id} backup file identity could not be verified")
            declared_hashes = (pair.first_sha256.lower(), pair.second_sha256.lower())
            if observed_hashes != declared_hashes:
                blocked.append(f"chip {chip_id} backup content differs from a declared SHA-256")
            if observed_hashes[0] != observed_hashes[1]:
                blocked.append(f"chip {chip_id} independent backup reads do not match")
        return self._result(profile.profile_id, blocked, unknown, high_identity=False)
