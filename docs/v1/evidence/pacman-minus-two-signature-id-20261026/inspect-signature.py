"""Parse OpenPGP packet metadata without verifying or executing the signature."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def packet_payload(raw: bytes) -> tuple[int, bytes]:
    if len(raw) < 2 or raw[0] & 0x80 == 0:
        raise ValueError("missing OpenPGP packet header")
    first = raw[0]
    if first & 0x40:
        tag = first & 0x3F
        octet = raw[1]
        if octet < 192:
            length, offset = octet, 2
        elif octet < 224:
            if len(raw) < 3:
                raise ValueError("short two-octet packet length")
            length, offset = ((octet - 192) << 8) + raw[2] + 192, 3
        elif octet == 255:
            if len(raw) < 6:
                raise ValueError("short five-octet packet length")
            length, offset = int.from_bytes(raw[2:6], "big"), 6
        else:
            raise ValueError("partial packet lengths are outside this parser")
    else:
        tag = (first >> 2) & 0x0F
        kind = first & 0x03
        widths = (1, 2, 4)
        if kind == 3:
            length, offset = len(raw) - 1, 1
        else:
            width = widths[kind]
            if len(raw) < 1 + width:
                raise ValueError("short old-format packet length")
            length = int.from_bytes(raw[1 : 1 + width], "big")
            offset = 1 + width
    if offset + length != len(raw):
        raise ValueError("packet length does not consume exact input")
    return tag, raw[offset:]


def subpackets(raw: bytes) -> list[tuple[int, bytes]]:
    result: list[tuple[int, bytes]] = []
    offset = 0
    while offset < len(raw):
        first = raw[offset]
        offset += 1
        if first < 192:
            length = first
        elif first < 255:
            if offset >= len(raw):
                raise ValueError("short two-octet subpacket length")
            length = ((first - 192) << 8) + raw[offset] + 192
            offset += 1
        else:
            if offset + 4 > len(raw):
                raise ValueError("short five-octet subpacket length")
            length = int.from_bytes(raw[offset : offset + 4], "big")
            offset += 4
        if length < 1 or offset + length > len(raw):
            raise ValueError("invalid subpacket length")
        kind = raw[offset] & 0x7F
        result.append((kind, raw[offset + 1 : offset + length]))
        offset += length
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("signature", type=Path)
    parser.add_argument("--expected-sha256", required=True)
    args = parser.parse_args()
    raw = args.signature.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != args.expected_sha256:
        raise ValueError("signature bytes do not match pinned PGPSIG digest")

    tag, body = packet_payload(raw)
    if tag != 2 or len(body) < 8 or body[0] != 4:
        raise ValueError("expected one version-4 signature packet")
    hashed_size = int.from_bytes(body[4:6], "big")
    hashed_end = 6 + hashed_size
    if hashed_end + 2 > len(body):
        raise ValueError("truncated hashed signature subpackets")
    hashed = subpackets(body[6:hashed_end])
    unhashed_size = int.from_bytes(body[hashed_end : hashed_end + 2], "big")
    unhashed_start = hashed_end + 2
    unhashed_end = unhashed_start + unhashed_size
    if unhashed_end + 2 > len(body):
        raise ValueError("truncated unhashed signature subpackets")
    unhashed = subpackets(body[unhashed_start:unhashed_end])

    issuer = next((data.hex() for kind, data in hashed + unhashed if kind == 16 and len(data) == 8), None)
    fingerprint_data = next((data for kind, data in hashed + unhashed if kind == 33 and len(data) >= 2), None)
    fingerprint = None
    fingerprint_version = None
    if fingerprint_data is not None:
        fingerprint_version = fingerprint_data[0]
        fingerprint = fingerprint_data[1:].hex()
        if fingerprint_version != 4 or len(fingerprint_data[1:]) != 20:
            raise ValueError("unexpected issuer fingerprint version or size")

    print(
        json.dumps(
            {
                "packet_tag": tag,
                "signature_version": body[0],
                "signature_type": body[1],
                "public_key_algorithm": body[2],
                "hash_algorithm": body[3],
                "signature_sha256": digest,
                "issuer_fingerprint_version": fingerprint_version,
                "issuer_fingerprint": fingerprint,
                "issuer_key_id": issuer or (fingerprint[-16:] if fingerprint else None),
                "signature_verified": False,
                "package_bytes_fetched": False,
                "key_bytes_fetched": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
