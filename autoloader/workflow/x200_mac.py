"""Fail-closed X200 GbE NVM MAC preservation and post-boot comparison.

The parser accepts only Intel flash descriptors with a usable GbE region and
the documented dual NVM layout. The two 128-byte NVM records are checked
independently; each is 64 little-endian words whose sum modulo 2**16 must be
0xBABA. Only verified original-backup GbE bytes are copied into the selected
build. The resulting image is a derived write input, not an ArtifactEngine
COMPLETE output.
"""
from __future__ import annotations

import hashlib
import json
import re
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from autoloader.workflow.write_approval import _has_link_component, _write_exclusive

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IFD_SIGNATURE = b"\x5a\xa5\xf0\x0f"
_ALLOWED_ROM_SIZES = {4 * 1024 * 1024, 8 * 1024 * 1024}
_ALLOWED_GBE_SIZES = {8 * 1024, 16 * 1024, 128 * 1024}
_NVM_BYTES = 128
_NVM_CHECKSUM = 0xBABA


class X200MacError(ValueError):
    """X200 GbE NVM or MAC provenance is invalid or incomplete."""


@dataclass(frozen=True)
class GbeNvmEvidence:
    region_start: int
    region_end: int
    region_size: int
    part0_sha256: str
    part1_sha256: str
    part0_checksum: int
    part1_checksum: int
    part0_mac: str
    part1_mac: str
    mac_address: str


@dataclass(frozen=True)
class X200MacPreservationEvidence:
    operation_id: str
    policy: str
    source_artifact_path: str
    source_artifact_sha256: str
    original_backup_path: str
    original_backup_sha256: str
    independent_backup_path: str
    independent_backup_sha256: str
    region_start: int
    region_end: int
    original_mac: str
    selected_mac_before: str
    selected_mac_after: str
    write_artifact_path: str
    write_artifact_sha256: str
    write_artifact_size_bytes: int
    manifest_path: str
    manifest_sha256: str


@dataclass(frozen=True)
class X200PostBootMacEvidence:
    artifact_sha256: str
    expected_mac: str
    observed_mac: str
    actor: str
    observed_at_utc: str
    evidence_ref: str
    matches: bool


def inspect_x200_gbe_nvm(image: bytes) -> GbeNvmEvidence:
    """Read IFD GbE bounds and validate both redundant NVM records."""
    if len(image) not in _ALLOWED_ROM_SIZES:
        raise X200MacError("X200 ROM must be an exact supported 4 MiB or 8 MiB full-chip image")
    if image[0x10:0x14] != _IFD_SIGNATURE:
        raise X200MacError("X200 full-chip image lacks the expected Intel flash descriptor signature")
    if len(image) < 0x50:
        raise X200MacError("X200 flash descriptor is truncated")
    flmap0 = struct.unpack_from("<I", image, 0x14)[0]
    frba = ((flmap0 >> 16) & 0xFF) << 4
    # V1's pinned ifdtool-compatible parser intentionally supports the standard
    # 0x40 descriptor-register table only. Reject unusual FRBA values instead
    # of interpreting fixed offsets as FLREG0/FLREG3 without checking FLMAP0.
    if frba != 0x40 or len(image) < frba + 16:
        raise X200MacError("X200 descriptor FLMAP0 FRBA is not the validated 0x40 register table")
    descriptor_register = struct.unpack_from("<I", image, frba)[0]
    ifd_end = (((descriptor_register >> 16) & 0x7FFF) + 1) << 12
    ifd_start = (descriptor_register & 0x7FFF) << 12
    if ifd_start != 0 or ifd_end <= ifd_start:
        raise X200MacError("X200 descriptor region bounds are disabled or malformed")
    gbe_register = struct.unpack_from("<I", image, frba + 12)[0]
    base = gbe_register & 0x7FFF
    limit = (gbe_register >> 16) & 0x7FFF
    if base == 0x7FFF or limit == 0x7FFF or limit < base:
        raise X200MacError("X200 descriptor has a disabled or malformed GbE region")
    region_start = base << 12
    region_end = (limit + 1) << 12
    region_size = region_end - region_start
    if (
        region_size not in _ALLOWED_GBE_SIZES
        or region_start < ifd_end
        or region_end > len(image)
        or region_start % 4096
        or region_end % 4096
    ):
        raise X200MacError("X200 GbE region bounds/size are outside the validated 8/16/128 KiB layouts")
    half = region_size // 2
    part0 = image[region_start : region_start + _NVM_BYTES]
    part1 = image[region_start + half : region_start + half + _NVM_BYTES]
    if len(part0) != _NVM_BYTES or len(part1) != _NVM_BYTES:
        raise X200MacError("X200 redundant GbE NVM record is truncated")
    checksum0, mac0 = _validate_nvm_record(part0, "part 0")
    checksum1, mac1 = _validate_nvm_record(part1, "part 1")
    if mac0 != mac1:
        raise X200MacError("X200 redundant GbE NVM parts contain different MAC addresses")
    return GbeNvmEvidence(
        region_start=region_start,
        region_end=region_end,
        region_size=region_size,
        part0_sha256=_sha256(part0),
        part1_sha256=_sha256(part1),
        part0_checksum=checksum0,
        part1_checksum=checksum1,
        part0_mac=mac0,
        part1_mac=mac1,
        mac_address=mac0,
    )


def prepare_x200_preserved_mac_image(
    *,
    operation_id: str,
    selected_artifact_path: str | Path,
    selected_artifact_sha256: str,
    backup_first_path: str | Path,
    backup_first_sha256: str,
    backup_second_path: str | Path,
    backup_second_sha256: str,
) -> X200MacPreservationEvidence:
    """Create or reopen a durable derived ROM whose GbE region is from backup.

    The sidecar is created after the ROM using exclusive creation. A crash
    between those operations leaves an unusable orphan that fails closed on
    subsequent attempts; it is never silently overwritten or repaired.
    """
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", operation_id):
        raise X200MacError("X200 MAC preservation requires the exact workflow operation UUID")
    if not _SHA256.fullmatch(selected_artifact_sha256) or not _SHA256.fullmatch(backup_first_sha256) or not _SHA256.fullmatch(backup_second_sha256):
        raise X200MacError("X200 MAC preservation requires valid source and backup SHA-256 bindings")
    if backup_first_sha256 != backup_second_sha256:
        raise X200MacError("X200 independent full-chip backups differ")
    selected_path = _safe_regular_file(selected_artifact_path, "selected X200 artifact")
    backup_a = _safe_regular_file(backup_first_path, "first verified X200 backup")
    backup_b = _safe_regular_file(backup_second_path, "independent verified X200 backup")
    selected = selected_path.read_bytes()
    original = backup_a.read_bytes()
    second = backup_b.read_bytes()
    if _sha256(selected) != selected_artifact_sha256:
        raise X200MacError("Selected X200 artifact changed before MAC preservation")
    if _sha256(original) != backup_first_sha256 or _sha256(second) != backup_second_sha256 or original != second:
        raise X200MacError("Verified X200 backup copies changed or no longer match each other")
    original_nvm = inspect_x200_gbe_nvm(original)
    selected_nvm = inspect_x200_gbe_nvm(selected)
    if (original_nvm.region_start, original_nvm.region_end) != (selected_nvm.region_start, selected_nvm.region_end):
        raise X200MacError("Selected X200 artifact GbE layout differs from the verified original backup")
    result = bytearray(selected)
    result[original_nvm.region_start : original_nvm.region_end] = original[
        original_nvm.region_start : original_nvm.region_end
    ]
    output = bytes(result)
    final_nvm = inspect_x200_gbe_nvm(output)
    if final_nvm.mac_address != original_nvm.mac_address:
        raise X200MacError("Derived X200 artifact failed original GbE MAC preservation validation")
    output_sha = _sha256(output)
    output_dir = selected_path.parent / "x200-write-ready"
    if _has_link_component(output_dir):
        raise X200MacError("X200 write-ready output directory crosses a symlink or junction")
    output_dir.mkdir(mode=0o700, parents=False, exist_ok=True)
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise X200MacError("X200 write-ready output directory is unsafe")
    rom_path = output_dir / f"{operation_id}.rom"
    manifest_path = output_dir / f"{operation_id}.mac-preservation.json"
    manifest = {
        "schema_version": 1,
        "operation_id": operation_id,
        "policy": "preserve-original-gbe-region",
        "source_artifact_path": str(selected_path),
        "source_artifact_sha256": selected_artifact_sha256,
        "original_backup_path": str(backup_a),
        "original_backup_sha256": backup_first_sha256,
        "independent_backup_path": str(backup_b),
        "independent_backup_sha256": backup_second_sha256,
        "region_start": original_nvm.region_start,
        "region_end": original_nvm.region_end,
        "original_mac": original_nvm.mac_address,
        "selected_mac_before": selected_nvm.mac_address,
        "selected_mac_after": final_nvm.mac_address,
        "part0_sha256": final_nvm.part0_sha256,
        "part1_sha256": final_nvm.part1_sha256,
        "part0_checksum": final_nvm.part0_checksum,
        "part1_checksum": final_nvm.part1_checksum,
        "write_artifact_path": str(rom_path),
        "write_artifact_sha256": output_sha,
        "write_artifact_size_bytes": len(output),
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    exists = (rom_path.exists(), manifest_path.exists())
    if exists != (False, False):
        if exists != (True, True) or rom_path.is_symlink() or manifest_path.is_symlink():
            raise X200MacError("Partial or linked X200 MAC-derived output exists; refusing to overwrite it")
        if rom_path.read_bytes() != output or manifest_path.read_bytes() != manifest_bytes:
            raise X200MacError("Existing X200 MAC-derived ROM/manifest differs from the reopened evidence")
    else:
        _write_exclusive(rom_path, output)
        _write_exclusive(manifest_path, manifest_bytes)
    if _sha256(rom_path.read_bytes()) != output_sha:
        raise X200MacError("Durable X200 MAC-derived ROM hash changed after publication")
    return X200MacPreservationEvidence(
        operation_id=operation_id,
        policy="preserve-original-gbe-region",
        source_artifact_path=str(selected_path),
        source_artifact_sha256=selected_artifact_sha256,
        original_backup_path=str(backup_a),
        original_backup_sha256=backup_first_sha256,
        independent_backup_path=str(backup_b),
        independent_backup_sha256=backup_second_sha256,
        region_start=original_nvm.region_start,
        region_end=original_nvm.region_end,
        original_mac=original_nvm.mac_address,
        selected_mac_before=selected_nvm.mac_address,
        selected_mac_after=final_nvm.mac_address,
        write_artifact_path=str(rom_path),
        write_artifact_sha256=output_sha,
        write_artifact_size_bytes=len(output),
        manifest_path=str(manifest_path),
        manifest_sha256=_sha256(manifest_bytes),
    )


def validate_x200_postboot_mac(
    *,
    expected_mac: str,
    observed_mac: str,
    artifact_sha256: str,
    expected_artifact_sha256: str,
    actor: str,
    observed_at_utc: str,
    evidence_ref: str,
) -> X200PostBootMacEvidence:
    """Compare a human-collected post-boot NIC address to the approved image."""
    expected = _usable_mac(expected_mac)
    observed = _usable_mac(observed_mac)
    if not _SHA256.fullmatch(artifact_sha256) or artifact_sha256 != expected_artifact_sha256:
        raise X200MacError("Post-boot MAC evidence is not bound to the approved final ROM hash")
    if not actor.strip() or not evidence_ref.strip():
        raise X200MacError("Post-boot MAC check requires a named observer and evidence reference")
    observed_at = datetime.fromisoformat(observed_at_utc.replace("Z", "+00:00"))
    if observed_at.tzinfo is None or observed_at.utcoffset() != timezone.utc.utcoffset(observed_at):
        raise X200MacError("Post-boot MAC observation time must be timezone-aware UTC")
    now = datetime.now(timezone.utc)
    if observed_at > now + timedelta(seconds=30) or now - observed_at > timedelta(days=1):
        raise X200MacError("Post-boot MAC evidence is future-dated or stale")
    if expected != observed:
        raise X200MacError("Observed post-boot NIC MAC differs from the preserved original MAC")
    return X200PostBootMacEvidence(
        artifact_sha256=artifact_sha256,
        expected_mac=expected,
        observed_mac=observed,
        actor=actor.strip(),
        observed_at_utc=observed_at.isoformat(),
        evidence_ref=evidence_ref.strip(),
        matches=True,
    )


def _validate_nvm_record(record: bytes, label: str) -> tuple[int, str]:
    words = struct.unpack("<64H", record)
    checksum = sum(words) & 0xFFFF
    if checksum != _NVM_CHECKSUM:
        raise X200MacError(f"X200 GbE NVM {label} checksum is invalid")
    mac = _usable_mac(record[:6].hex(":"))
    return checksum, mac


def _usable_mac(value: str) -> str:
    try:
        raw = bytes.fromhex(value.replace(":", "").replace("-", ""))
    except ValueError as exc:
        raise X200MacError("MAC address is not hexadecimal") from exc
    if len(raw) != 6 or raw == bytes(6) or raw == b"\xff" * 6 or raw[0] & 1:
        raise X200MacError("MAC address must be a nonzero, nonbroadcast unicast address")
    return ":".join(f"{item:02x}" for item in raw)


def _safe_regular_file(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or _has_link_component(path) or not path.is_file():
        raise X200MacError(f"{label} must be an absolute, regular, link-free file")
    return path.resolve(strict=True)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
