from __future__ import annotations

import lzma
from pathlib import Path

from verify_bundle import parse_deb822_paragraphs


ROOT = Path(__file__).resolve().parent
LISTS = ROOT / "apt-state" / "lists"
INDEXES = ROOT / "indices"
WANTED = {
    "gpgv": "2.4.8-4ubuntu3.1",
    "gpg": "2.4.8-4ubuntu3.1",
    "ubuntu-keyring": "2023.11.28.1build1",
}

for name, version in WANTED.items():
    print(f"PACKAGE {name} {version}")
    seen = 0
    for index_path in sorted(INDEXES.glob("*.xz")):
        rows = parse_deb822_paragraphs(lzma.decompress(index_path.read_bytes()))
        for row in rows:
            if row.get("Package") == name and row.get("Version") == version and row.get("Architecture") in {"amd64", "all"}:
                seen += 1
                print(f"  index={index_path.name}")
                for key in ("Package", "Version", "Architecture", "Filename", "Size", "SHA256"):
                    print(f"  {key}={row.get(key, '')}")
    print(f"  matching_stanzas={seen}")
