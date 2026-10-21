from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


repo = Path("/mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader")
evidence = repo / "docs/v1/evidence/current-source-wheel-x200-null-overlap-20261021"
scratch = Path(tempfile.mkdtemp(prefix="librecore-wheel-schema4-"))
src = scratch / "src"
src.mkdir()
for name in ("README.md", "pyproject.toml", "autoloader", "usb_builder"):
    path = repo / name
    target = src / name
    if path.is_dir():
        shutil.copytree(path, target)
    else:
        shutil.copy2(path, target)


def tree_digest(root: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode()
        data = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        count += 1
    return digest.hexdigest(), count


source_sha, source_files = tree_digest(src)
os.chdir(src)
from setuptools.build_meta import build_wheel

wheel_dir = scratch / "dist"
wheel_dir.mkdir()
with contextlib.redirect_stdout(io.StringIO()) as build_output:
    wheel_name = build_wheel(str(wheel_dir))
build_log = evidence / "build.log"
build_log.write_text(build_output.getvalue(), encoding="utf-8")
wheel = wheel_dir / wheel_name
wheel_sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
preserved_wheel = evidence / wheel_name
shutil.copy2(wheel, preserved_wheel)

with zipfile.ZipFile(wheel) as archive:
    entries = set(archive.namelist())
    patches = sorted(name for name in entries if name.endswith(".patch"))
    required = {
        "autoloader/__main__.py",
        "autoloader/rom_builder/artifact_engine.py",
        "usb_builder/build_usb.py",
        "autoloader/rom_builder/patches/0009-x86-use-output-target-for-efi.patch",
        "autoloader/rom_builder/patches/0010-lbmk-grub-explicit-unifont.patch",
        "autoloader/rom_builder/patches/0011-lbmk-vboot-cbfstool-keep-strchr-result-const.patch",
        "usb_builder/patches/arch-install-scripts-31-2-pacstrap-status.patch",
    }
    missing = sorted(required - entries)
    if missing:
        raise RuntimeError(f"missing required wheel entries: {missing}")

target = scratch / "target"
target.mkdir()
with zipfile.ZipFile(wheel) as archive:
    archive.extractall(target)
env = os.environ.copy()
env["PYTHONPATH"] = str(target)
probe = (
    "import autoloader, usb_builder, json; "
    "print(json.dumps({'autoloader': autoloader.__file__, 'usb_builder': usb_builder.__file__}))"
)
check = subprocess.run(
    [sys.executable, "-c", probe], env=env, cwd=str(scratch), capture_output=True, text=True
)
if check.returncode:
    raise RuntimeError(f"isolated extracted-wheel imports failed: {check.stderr}")
import_paths = json.loads(check.stdout)
target_resolved = target.resolve()
for module_path in import_paths.values():
    if not Path(module_path).resolve().is_relative_to(target_resolved):
        raise RuntimeError(f"import resolved outside extracted wheel target: {module_path}")

cli_results = {}
for arguments in (("--help",), ("flash", "--help")):
    result = subprocess.run(
        [sys.executable, "-m", "autoloader", *arguments],
        env=env,
        cwd=str(scratch),
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(f"extracted-wheel CLI {arguments!r} failed: {result.stderr}")
    cli_results[" ".join(arguments)] = {"exit_code": result.returncode, "first_line": result.stdout.splitlines()[0]}

run = {
    "result": "PASS",
    "environment": "Ubuntu 26.04 WSL; Python " + sys.version.split()[0],
    "source_snapshot_sha256": source_sha,
    "source_file_count": source_files,
    "wheel": wheel_name,
    "wheel_path": str(preserved_wheel),
    "wheel_sha256": wheel_sha,
    "wheel_bytes": wheel.stat().st_size,
    "packaged_patch_resources": patches,
    "required_patch_resources_present": True,
    "extracted_import_target": str(target_resolved),
    "extracted_import_module_paths": import_paths,
    "extracted_wheel_cli": cli_results,
    "build_log": str(build_log),
}
(evidence / "run.json").write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
print(json.dumps(run, indent=2))
