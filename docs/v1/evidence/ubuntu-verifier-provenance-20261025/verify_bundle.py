"""Offline validation for the retained, non-installing Ubuntu APT evidence.

This utility verifies Release hashes, package stanzas and archive bytes from a
captured APT bundle. It does not install packages or execute gpg/gpgv from an
extracted package. Signature checks are explicitly relative to the Ubuntu
archive keyring already installed in this WSL image; this tool does not
independently authenticate that root keyring's provisioning.
"""

from __future__ import annotations

import hashlib
import lzma
import os
import re
import stat
from pathlib import Path
from typing import Mapping


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_INDEX_COMPRESSED = 32 * 1024 * 1024
_MAX_INDEX_EXPANDED = 128 * 1024 * 1024


class EvidenceError(ValueError):
    """An evidence input is missing, malformed or inconsistent."""


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_regular(path: Path, limit: int) -> bytes:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise EvidenceError(f"input is not a bounded single-link regular file: {path.name}")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if _stat_identity(info) != _stat_identity(opened):
                raise EvidenceError(f"input changed between stat and open: {path.name}")
            raw = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
        current = path.lstat()
    except OSError as exc:
        raise EvidenceError(f"cannot read evidence input {path.name}: {exc}") from exc
    if len(raw) > limit or len(raw) != opened.st_size:
        raise EvidenceError(f"input size changed or exceeds limit: {path.name}")
    identity = _stat_identity(opened)
    if identity != _stat_identity(after):
        raise EvidenceError(f"input mutated while read: {path.name}")
    if identity != _stat_identity(current):
        raise EvidenceError(f"input path changed while read: {path.name}")
    return raw


def _stat_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_mode,
        info.st_nlink,
        info.st_uid,
        info.st_gid,
    )


def release_payload(inrelease: bytes) -> str:
    try:
        text = inrelease.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise EvidenceError("InRelease is not strict UTF-8") from exc
    try:
        _header, signed = text.split("\n\n", 1)
        payload = signed.split("\n-----BEGIN PGP SIGNATURE-----", 1)[0]
    except ValueError as exc:
        raise EvidenceError("InRelease cleartext/signature framing is malformed") from exc
    if not payload.startswith("Origin: ") or "\nSHA256:\n" not in payload:
        raise EvidenceError("InRelease payload lacks Origin or SHA256 section")
    return payload + "\n"


def release_sha256_entries(inrelease: bytes) -> dict[str, tuple[str, int]]:
    payload = release_payload(inrelease)
    lines = payload.splitlines()
    try:
        start = lines.index("SHA256:") + 1
    except ValueError as exc:
        raise EvidenceError("InRelease lacks SHA256 section") from exc
    entries: dict[str, tuple[str, int]] = {}
    for line in lines[start:]:
        if line and not line[0].isspace() and ":" in line:
            break
        fields = line.split()
        if not fields:
            continue
        if len(fields) != 3 or not _SHA256.fullmatch(fields[0]) or not fields[1].isdigit():
            raise EvidenceError("InRelease SHA256 row is malformed")
        relative = fields[2]
        if relative in entries:
            raise EvidenceError("InRelease repeats a SHA256 path")
        entries[relative] = (fields[0], int(fields[1]))
    if not entries:
        raise EvidenceError("InRelease SHA256 section is empty")
    return entries


def verify_packages_xz(inrelease: bytes, compressed_packages: bytes, relative_path: str) -> bytes:
    if len(compressed_packages) > _MAX_INDEX_COMPRESSED:
        raise EvidenceError("compressed Packages index exceeds fixed bound")
    entry = release_sha256_entries(inrelease).get(relative_path)
    if entry is None:
        raise EvidenceError("InRelease does not bind the requested Packages index")
    expected_digest, expected_size = entry
    if len(compressed_packages) != expected_size or sha256_bytes(compressed_packages) != expected_digest:
        raise EvidenceError("Packages index does not match the signed InRelease SHA256/size")
    decompressor = lzma.LZMADecompressor()
    try:
        expanded = decompressor.decompress(compressed_packages, max_length=_MAX_INDEX_EXPANDED + 1)
    except lzma.LZMAError as exc:
        raise EvidenceError("Packages index XZ stream is invalid") from exc
    if len(expanded) > _MAX_INDEX_EXPANDED or not decompressor.eof or decompressor.unused_data:
        raise EvidenceError("Packages index expansion is oversized or has trailing data")
    return expanded


def parse_deb822_paragraphs(raw: bytes) -> tuple[dict[str, str], ...]:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise EvidenceError("Packages index is not strict UTF-8") from exc
    paragraphs: list[dict[str, str]] = []
    for block in re.split(r"\n\s*\n", text.strip("\n")):
        fields: dict[str, str] = {}
        previous: str | None = None
        for line in block.splitlines():
            if line[:1] in {" ", "\t"}:
                if previous is None:
                    raise EvidenceError("Packages index has an orphan continuation line")
                fields[previous] += "\n" + line[1:]
                continue
            if ":" not in line:
                raise EvidenceError("Packages index field is malformed")
            name, value = line.split(":", 1)
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", name) or name in fields:
                raise EvidenceError("Packages index has an invalid or duplicate field")
            fields[name] = value.lstrip()
            previous = name
        if fields:
            paragraphs.append(fields)
    return tuple(paragraphs)


def find_package_record(
    packages_index: bytes,
    *,
    name: str,
    version: str,
    architecture: str,
) -> Mapping[str, str]:
    matches = [
        paragraph
        for paragraph in parse_deb822_paragraphs(packages_index)
        if paragraph.get("Package") == name
        and paragraph.get("Version") == version
        and paragraph.get("Architecture") == architecture
    ]
    if len(matches) != 1:
        raise EvidenceError(f"expected one exact package stanza for {name}={version}:{architecture}")
    record = matches[0]
    filename = record.get("Filename", "")
    path_parts = filename.split("/")
    expected_filename = f"{name}_{version.replace(':', '%3a')}_{architecture}.deb"
    if (
        not filename.startswith("pool/main/")
        or len(path_parts) < 4
        or any(part in {"", ".", ".."} for part in path_parts)
        or path_parts[-1] != expected_filename
        or not record.get("Size", "").isdigit()
        or not _SHA256.fullmatch(record.get("SHA256", ""))
    ):
        raise EvidenceError(f"package stanza lacks a valid archive path/size/SHA256: {name}")
    return record


def verify_archive_file(path: Path, record: Mapping[str, str]) -> str:
    try:
        expected_size = int(record["Size"])
    except (KeyError, ValueError) as exc:
        raise EvidenceError(f"invalid expected archive size: {path.name}") from exc
    raw = _read_regular(path, expected_size)
    actual = sha256_bytes(raw)
    if actual != record["SHA256"]:
        raise EvidenceError(f"package archive SHA256 differs from Packages record: {path.name}")
    return actual
