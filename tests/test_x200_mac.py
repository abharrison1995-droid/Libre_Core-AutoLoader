"""Synthetic X200 GbE NVM/MAC policy tests; no physical ROM is used."""
from __future__ import annotations

import hashlib
import json
import struct
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autoloader.workflow.x200_mac import (
    X200MacError,
    inspect_x200_gbe_nvm,
    prepare_x200_preserved_mac_image,
    validate_x200_postboot_mac,
)

MIB = 1024 * 1024
ORIGINAL_MAC = bytes.fromhex("00262d123456")
GENERIC_MAC = bytes.fromhex("020000000001")


def _nvm_record(mac: bytes) -> bytes:
    record = bytearray(128)
    record[:6] = mac
    words = struct.unpack("<63H", bytes(record[:126]))
    struct.pack_into("<H", record, 126, (0xBABA - sum(words)) & 0xFFFF)
    return bytes(record)


def _rom(mac: bytes, *, total_size: int = 8192, second_mac: bytes | None = None) -> bytes:
    # Repeating byte values keep this synthetic dump non-degenerate for the
    # backup validator while remaining obviously test-only content.
    image = bytearray(bytes(range(256)) * (4 * MIB // 256))
    image[0x10:0x14] = b"\x5a\xa5\xf0\x0f"
    struct.pack_into("<I", image, 0x14, 0x00040000)  # FRBA field 4 * 16 = 0x40.
    struct.pack_into("<I", image, 0x40, 0)  # IFD descriptor starts at zero and is 4 KiB.
    start = 0x10000
    half = total_size // 2
    blocks = total_size // 4096
    base = start // 4096
    limit = base + blocks - 1
    struct.pack_into("<I", image, 0x4C, base | (limit << 16))
    image[start : start + 128] = _nvm_record(mac)
    image[start + half : start + half + 128] = _nvm_record(second_mac or mac)
    return bytes(image)


def _write_pair(tmp_path: Path, original: bytes, selected: bytes) -> tuple[Path, Path, Path]:
    source = tmp_path / "selected.rom"
    first = tmp_path / "backup-a.bin"
    second = tmp_path / "backup-b.bin"
    source.write_bytes(selected)
    first.write_bytes(original)
    second.write_bytes(original)
    return source, first, second


@pytest.mark.parametrize("gbe_size", [8192, 16384, 131072])
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="successful durable MAC artifact publication requires POSIX directory fsync; production remains fail-closed on Windows",
)
def test_x200_preservation_copies_original_dual_nvm_region_and_binds_output(tmp_path, gbe_size):
    original = _rom(ORIGINAL_MAC, total_size=gbe_size)
    selected = _rom(GENERIC_MAC, total_size=gbe_size)
    source, first, second = _write_pair(tmp_path, original, selected)
    evidence = prepare_x200_preserved_mac_image(
        operation_id=str(uuid.uuid4()),
        selected_artifact_path=source,
        selected_artifact_sha256=hashlib.sha256(selected).hexdigest(),
        backup_first_path=first,
        backup_first_sha256=hashlib.sha256(original).hexdigest(),
        backup_second_path=second,
        backup_second_sha256=hashlib.sha256(original).hexdigest(),
    )
    derived = Path(evidence.write_artifact_path).read_bytes()
    manifest_raw = Path(evidence.manifest_path).read_bytes()
    manifest = json.loads(manifest_raw)
    assert evidence.original_mac == "00:26:2d:12:34:56"
    assert evidence.selected_mac_before == "02:00:00:00:00:01"
    assert evidence.selected_mac_after == evidence.original_mac
    assert evidence.region_end - evidence.region_start == gbe_size
    assert derived != selected
    assert inspect_x200_gbe_nvm(derived).mac_address == evidence.original_mac
    assert hashlib.sha256(derived).hexdigest() == evidence.write_artifact_sha256
    assert hashlib.sha256(manifest_raw).hexdigest() == evidence.manifest_sha256
    assert manifest["source_artifact_sha256"] == hashlib.sha256(selected).hexdigest()
    assert manifest["selected_mac_before"] == "02:00:00:00:00:01"
    assert manifest["write_artifact_sha256"] == evidence.write_artifact_sha256
    assert source.read_bytes() == selected


@pytest.mark.parametrize(
    ("mac", "expected"),
    [
        (bytes(6), "nonzero"),
        (b"\xff" * 6, "nonzero"),
        (bytes.fromhex("010203040506"), "unicast"),
    ],
)
def test_x200_rejects_unusable_mac_addresses(tmp_path, mac, expected):
    with pytest.raises(X200MacError, match=expected):
        inspect_x200_gbe_nvm(_rom(mac))


def test_x200_rejects_bad_checksum_and_disagreeing_redundant_parts():
    damaged = bytearray(_rom(ORIGINAL_MAC))
    damaged[0x10000 + 9] ^= 1
    with pytest.raises(X200MacError, match="checksum"):
        inspect_x200_gbe_nvm(bytes(damaged))
    with pytest.raises(X200MacError, match="different MAC"):
        inspect_x200_gbe_nvm(_rom(ORIGINAL_MAC, second_mac=bytes.fromhex("00262d654321")))


def test_x200_rejects_unknown_gbe_layout_or_bad_descriptor():
    with pytest.raises(X200MacError, match="bounds/size"):
        inspect_x200_gbe_nvm(_rom(ORIGINAL_MAC, total_size=12288))
    invalid = bytearray(_rom(ORIGINAL_MAC))
    invalid[0x10:0x14] = b"\0\0\0\0"
    with pytest.raises(X200MacError, match="signature"):
        inspect_x200_gbe_nvm(bytes(invalid))
    wrong_frba = bytearray(_rom(ORIGINAL_MAC))
    struct.pack_into("<I", wrong_frba, 0x14, 0x00050000)
    with pytest.raises(X200MacError, match="FRBA"):
        inspect_x200_gbe_nvm(bytes(wrong_frba))


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="the final positive MAC artifact publication requires POSIX directory fsync; production remains fail-closed on Windows",
)
def test_x200_preservation_rejects_backup_mismatch_and_output_tamper(tmp_path):
    original = _rom(ORIGINAL_MAC)
    selected = _rom(GENERIC_MAC)
    source, first, second = _write_pair(tmp_path, original, selected)
    with pytest.raises(X200MacError, match="independent full-chip backups differ"):
        prepare_x200_preserved_mac_image(
            operation_id=str(uuid.uuid4()), selected_artifact_path=source,
            selected_artifact_sha256=hashlib.sha256(selected).hexdigest(),
            backup_first_path=first, backup_first_sha256=hashlib.sha256(original).hexdigest(),
            backup_second_path=second, backup_second_sha256="b" * 64,
        )
    second.write_bytes(_rom(ORIGINAL_MAC, second_mac=bytes.fromhex("00262d654321")))
    operation_id = str(uuid.uuid4())
    with pytest.raises(X200MacError, match="changed or no longer match"):
        prepare_x200_preserved_mac_image(
            operation_id=operation_id, selected_artifact_path=source,
            selected_artifact_sha256=hashlib.sha256(selected).hexdigest(),
            backup_first_path=first, backup_first_sha256=hashlib.sha256(original).hexdigest(),
            backup_second_path=second, backup_second_sha256=hashlib.sha256(original).hexdigest(),
        )

    second.write_bytes(original)
    evidence = prepare_x200_preserved_mac_image(
        operation_id=operation_id, selected_artifact_path=source,
        selected_artifact_sha256=hashlib.sha256(selected).hexdigest(),
        backup_first_path=first, backup_first_sha256=hashlib.sha256(original).hexdigest(),
        backup_second_path=second, backup_second_sha256=hashlib.sha256(original).hexdigest(),
    )
    derived = Path(evidence.write_artifact_path)
    derived.write_bytes(selected)
    with pytest.raises(X200MacError, match="differs from the reopened evidence"):
        prepare_x200_preserved_mac_image(
            operation_id=operation_id, selected_artifact_path=source,
            selected_artifact_sha256=hashlib.sha256(selected).hexdigest(),
            backup_first_path=first, backup_first_sha256=hashlib.sha256(original).hexdigest(),
            backup_second_path=second, backup_second_sha256=hashlib.sha256(original).hexdigest(),
        )


def test_x200_postboot_mac_check_binds_final_rom_and_requires_match():
    now = datetime.now(timezone.utc).isoformat()
    digest = "a" * 64
    result = validate_x200_postboot_mac(
        expected_mac="00:26:2d:12:34:56", observed_mac="00:26:2d:12:34:56",
        artifact_sha256=digest, expected_artifact_sha256=digest,
        actor="Operator", observed_at_utc=now, evidence_ref="observation:nic-mac",
    )
    assert result.matches is True
    with pytest.raises(X200MacError, match="differs"):
        validate_x200_postboot_mac(
            expected_mac="00:26:2d:12:34:56", observed_mac="00:26:2d:65:43:21",
            artifact_sha256=digest, expected_artifact_sha256=digest,
            actor="Operator", observed_at_utc=now, evidence_ref="observation:nic-mac",
        )
    with pytest.raises(X200MacError, match="approved final ROM hash"):
        validate_x200_postboot_mac(
            expected_mac="00:26:2d:12:34:56", observed_mac="00:26:2d:12:34:56",
            artifact_sha256="b" * 64, expected_artifact_sha256=digest,
            actor="Operator", observed_at_utc=now, evidence_ref="observation:nic-mac",
        )
