from __future__ import annotations

import hashlib
import lzma
from pathlib import Path

import pytest

from verify_bundle import (
    EvidenceError,
    find_package_record,
    parse_deb822_paragraphs,
    verify_archive_file,
    verify_packages_xz,
)


def _inrelease_for(path: str, compressed: bytes) -> bytes:
    digest = hashlib.sha256(compressed).hexdigest()
    payload = (
        "Origin: Ubuntu\n"
        "Label: Ubuntu\n"
        "SHA256:\n"
        f" {digest} {len(compressed)} {path}\n"
    )
    return (
        "-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\n"
        + payload
        + "-----BEGIN PGP SIGNATURE-----\nsynthetic\n-----END PGP SIGNATURE-----\n"
    ).encode()


def test_signed_release_row_binds_compressed_index_bytes() -> None:
    path = "main/binary-amd64/Packages.xz"
    compressed = lzma.compress(b"Package: demo\nVersion: 1\nArchitecture: amd64\n\n")
    inrelease = _inrelease_for(path, compressed)
    assert b"Package: demo" in verify_packages_xz(inrelease, compressed, path)
    with pytest.raises(EvidenceError, match="does not match"):
        verify_packages_xz(inrelease, compressed + b"x", path)


def test_deb822_rejects_duplicate_fields_and_orphan_continuations() -> None:
    with pytest.raises(EvidenceError, match="duplicate"):
        parse_deb822_paragraphs(b"Package: a\nPackage: b\n\n")
    with pytest.raises(EvidenceError, match="orphan"):
        parse_deb822_paragraphs(b" continuation\n\n")


def test_package_record_binds_identity_and_safe_archive_name() -> None:
    raw = (
        "Package: demo\nVersion: 1:2-3\nArchitecture: amd64\n"
        "Filename: pool/main/d/demo/demo_1%3a2-3_amd64.deb\n"
        "Size: 4\nSHA256: " + hashlib.sha256(b"data").hexdigest() + "\n\n"
    ).encode()
    record = find_package_record(raw, name="demo", version="1:2-3", architecture="amd64")
    assert record["Size"] == "4"
    bad = raw.replace(b"pool/main/d/demo/demo_1%3a2-3_amd64.deb", b"pool/main/d/demo/other.deb")
    with pytest.raises(EvidenceError, match="archive path"):
        find_package_record(bad, name="demo", version="1:2-3", architecture="amd64")
    traversal = raw.replace(b"pool/main/d/demo/", b"pool/main/d/../")
    with pytest.raises(EvidenceError, match="archive path"):
        find_package_record(traversal, name="demo", version="1:2-3", architecture="amd64")


def test_archive_file_checks_hash_size_and_regular_single_link(tmp_path: Path) -> None:
    archive = tmp_path / "demo.deb"
    archive.write_bytes(b"data")
    record = {"Size": "4", "SHA256": hashlib.sha256(b"data").hexdigest()}
    assert verify_archive_file(archive, record) == record["SHA256"]
    archive.write_bytes(b"evil")
    with pytest.raises(EvidenceError, match="SHA256"):
        verify_archive_file(archive, record)


def test_archive_file_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.deb"
    target.write_bytes(b"data")
    link = tmp_path / "linked.deb"
    link.symlink_to(target)
    record = {"Size": "4", "SHA256": hashlib.sha256(b"data").hexdigest()}
    with pytest.raises(EvidenceError, match="bounded single-link"):
        verify_archive_file(link, record)
