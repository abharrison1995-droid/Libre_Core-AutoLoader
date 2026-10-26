from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SUMS = ROOT / "SHA256SUMS"
SYMLINKS = ROOT / "SYMLINKS.txt"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def symlink_rows() -> list[str]:
    rows = []
    for path in ROOT.rglob("*"):
        if path.is_symlink():
            rows.append(f"{path.relative_to(ROOT).as_posix()} -> {os.readlink(path)}")
    return sorted(rows)


def files() -> list[Path]:
    result = []
    for path in ROOT.rglob("*"):
        if path == SUMS or path.is_symlink() or not path.is_file():
            continue
        result.append(path)
    return sorted(result, key=lambda item: item.relative_to(ROOT).as_posix())


def generate() -> None:
    SYMLINKS.write_text("\n".join(symlink_rows()) + ("\n" if symlink_rows() else ""), encoding="utf-8")
    lines = [f"{sha256(path)}  {path.relative_to(ROOT).as_posix()}" for path in files()]
    SUMS.write_text("\n".join(lines) + "\n", encoding="ascii")


def verify() -> None:
    expected_links = "\n".join(symlink_rows()) + ("\n" if symlink_rows() else "")
    if SYMLINKS.read_text(encoding="utf-8") != expected_links:
        raise SystemExit("SYMLINKS.txt does not match the retained symlink paths/targets")
    entries: dict[str, str] = {}
    for line in SUMS.read_text(encoding="ascii").splitlines():
        if len(line) < 67 or line[64:66] != "  ":
            raise SystemExit("malformed SHA256SUMS row")
        digest, relative = line[:64], line[66:]
        if relative in entries or len(digest) != 64:
            raise SystemExit(f"duplicate or malformed SHA256SUMS row: {relative}")
        entries[relative] = digest
    actual = {path.relative_to(ROOT).as_posix() for path in files()}
    if actual != set(entries):
        raise SystemExit(f"manifest file set mismatch: unlisted={sorted(actual - set(entries))}, missing={sorted(set(entries) - actual)}")
    for relative, expected in entries.items():
        path = ROOT / relative
        if sha256(path) != expected:
            raise SystemExit(f"SHA256 mismatch: {relative}")
    print(f"verified {len(entries)} files")


if __name__ == "__main__":
    if sys.argv[1:] == ["--verify"]:
        verify()
    elif not sys.argv[1:]:
        generate()
    else:
        raise SystemExit("usage: write_checksums.py [--verify]")
