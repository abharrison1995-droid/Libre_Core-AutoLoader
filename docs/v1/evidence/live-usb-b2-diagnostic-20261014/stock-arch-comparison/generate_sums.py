from pathlib import Path
import hashlib

base = Path(__file__).resolve().parent
for root in (base, base.parent):
    manifest = root / "SHA256SUMS"
    rows = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path == manifest or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(f"{digest}  {rel}")
    manifest.write_text("\n".join(rows) + "\n", encoding="ascii")
    print(f"{manifest}: {len(rows)} entries")
