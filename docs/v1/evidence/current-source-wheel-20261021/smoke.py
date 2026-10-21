from __future__ import annotations
import contextlib, hashlib, io, json, os, shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path

repo = Path('/mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader')
scratch = Path(tempfile.mkdtemp(prefix='librecore-current-wheel-'))
src = scratch / 'src'
src.mkdir()
for name in ('README.md', 'pyproject.toml', 'autoloader', 'usb_builder'):
    path = repo / name
    target = src / name
    if path.is_dir():
        shutil.copytree(path, target)
    else:
        shutil.copy2(path, target)

def tree_digest(root: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    for path in sorted(item for item in root.rglob('*') if item.is_file()):
        rel = path.relative_to(root).as_posix().encode()
        digest.update(len(rel).to_bytes(4, 'big'))
        digest.update(rel)
        data = path.read_bytes()
        digest.update(len(data).to_bytes(8, 'big'))
        digest.update(data)
        count += 1
    return digest.hexdigest(), count

source_sha, source_files = tree_digest(src)
os.chdir(src)
from setuptools.build_meta import build_wheel
wheel_dir = scratch / 'dist'
wheel_dir.mkdir()
with contextlib.redirect_stdout(io.StringIO()) as build_output:
    wheel_name = build_wheel(str(wheel_dir))
(scratch / 'build.log').write_text(build_output.getvalue(), encoding='utf-8')
wheel = wheel_dir / wheel_name
wheel_sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
with zipfile.ZipFile(wheel) as archive:
    entries = set(archive.namelist())
    patch_files = sorted(name for name in entries if name.endswith('.patch'))
    required = {'autoloader/__main__.py', 'autoloader/rom_builder/artifact_engine.py', 'usb_builder/build_usb.py'}
    missing = sorted(required - entries)
    if missing:
        raise RuntimeError(f'missing required wheel entries: {missing}')
    if not patch_files:
        raise RuntimeError('wheel contains no packaged pinned patch resources')
target = scratch / 'target'
target.mkdir()
with zipfile.ZipFile(wheel) as archive:
    archive.extractall(target)
env = os.environ.copy()
env['PYTHONPATH'] = str(target)
probe = 'import autoloader, usb_builder, json; print(json.dumps({"autoloader": autoloader.__file__, "usb_builder": usb_builder.__file__}))'
check = subprocess.run([sys.executable, '-c', probe], env=env, cwd=str(scratch), capture_output=True, text=True)
if check.returncode:
    raise RuntimeError(f'isolated extracted-wheel import failed: {check.stderr}')
import_paths = json.loads(check.stdout)
target_resolved = target.resolve()
for module_path in import_paths.values():
    if not Path(module_path).resolve().is_relative_to(target_resolved):
        raise RuntimeError(f'import resolved outside extracted wheel target: {module_path}')
print(json.dumps({'result':'PASS','source_snapshot_sha256':source_sha,'source_file_count':source_files,'wheel':wheel_name,'wheel_sha256':wheel_sha,'wheel_bytes':wheel.stat().st_size,'packaged_patch_resources':patch_files,'import_target':str(target_resolved),'import_module_paths':import_paths,'build_log_path':str(scratch / 'build.log'),'scratch':str(scratch)}, indent=2))
