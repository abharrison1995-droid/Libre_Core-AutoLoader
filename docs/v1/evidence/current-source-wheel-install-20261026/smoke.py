from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path


REPO = Path(__file__).resolve().parents[4]
EVIDENCE = Path(__file__).resolve().parent
INPUTS = ("README.md", "pyproject.toml", "autoloader", "usb_builder")
REQUIRED_WHEEL_ENTRIES = {
    "autoloader/__main__.py",
    "autoloader/rom_builder/artifact_engine.py",
    "usb_builder/build_usb.py",
    "usb_builder/offline_vm_build.py",
    "usb_builder/offline_vm_build_service.py",
    "usb_builder/offline_vm_evidence.py",
    "usb_builder/offline_vm_guest_worker.py",
    "usb_builder/offline_vm_image.py",
    "usb_builder/offline_vm_package_closure.py",
    "usb_builder/offline_vm_worker.py",
    "usb_builder/snapshot.lock.json",
    "usb_builder/systemd/librecore-offline-build.service",
    "usb_builder/systemd/librecore-offline-build.path",
    "usb_builder/systemd/librecore-offline-preflight.service",
    "usb_builder/systemd/librecore-offline-preflight.path",
    "autoloader/rom_builder/patches/0009-x86-use-output-target-for-efi.patch",
    "autoloader/rom_builder/patches/0010-lbmk-grub-explicit-unifont.patch",
    "autoloader/rom_builder/patches/0011-lbmk-vboot-cbfstool-keep-strchr-result-const.patch",
    "usb_builder/patches/arch-install-scripts-31-2-pacstrap-status.patch",
}
WORKER_MODULES = (
    "usb_builder.offline_vm_build",
    "usb_builder.offline_vm_build_service",
    "usb_builder.offline_vm_evidence",
    "usb_builder.offline_vm_guest_worker",
    "usb_builder.offline_vm_image",
    "usb_builder.offline_vm_package_closure",
    "usb_builder.offline_vm_worker",
)
INSTALLED_RESOURCE_PATHS = (
    "usb_builder/snapshot.lock.json",
    "usb_builder/systemd/librecore-offline-build.service",
    "usb_builder/systemd/librecore-offline-build.path",
    "usb_builder/systemd/librecore-offline-preflight.service",
    "usb_builder/systemd/librecore-offline-preflight.path",
)


def selected_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for name in INPUTS:
        item = root / name
        if item.is_file():
            files.append(item)
        elif item.is_dir():
            files.extend(
                path
                for path in item.rglob("*")
                if path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix != ".pyc"
            )
        else:
            raise FileNotFoundError(item)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def manifest(root: Path) -> tuple[str, int, str]:
    digest = hashlib.sha256()
    rows: list[str] = []
    files = selected_files(root)
    for path in files:
        relative = path.relative_to(root).as_posix()
        relative_bytes = relative.encode("utf-8")
        data = path.read_bytes()
        digest.update(len(relative_bytes).to_bytes(4, "big"))
        digest.update(relative_bytes)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        rows.append(f"{hashlib.sha256(data).hexdigest()}  {relative}")
    return digest.hexdigest(), len(files), "\n".join(rows) + "\n"


def normalize_requirement_metadata(value: str) -> str:
    return "".join(value.casefold().split())


def save_process(name: str, argv: list[str], *, cwd: Path, env: dict[str, str]) -> dict[str, object]:
    result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    (EVIDENCE / f"{name}.stdout.txt").write_text(result.stdout, encoding="utf-8")
    (EVIDENCE / f"{name}.stderr.txt").write_text(result.stderr, encoding="utf-8")
    return {"argv": argv, "cwd": str(cwd), "exit_code": result.returncode}


def run() -> dict[str, object]:
    evidence_record: dict[str, object] = {
        "result": "FAIL",
        "source_root": str(REPO),
        "source_inputs": list(INPUTS),
        "python": sys.version,
        "platform": platform.platform(),
        "python_executable": sys.executable,
        "network_policy": "pip --no-index; --no-deps; no build isolation; no cache",
        "hardware_accessed": False,
        "firmware_accessed": False,
    }
    before_sha, before_count, before_text = manifest(REPO)
    (EVIDENCE / "source-manifest.sha256").write_text(before_text, encoding="utf-8")
    evidence_record["source_snapshot_sha256"] = before_sha
    evidence_record["source_file_count"] = before_count

    import setuptools
    import wheel

    evidence_record["build_backend_versions"] = {
        "setuptools": setuptools.__version__,
        "wheel": wheel.__version__,
        "pip": importlib.metadata.version("pip"),
    }
    evidence_record["build_backend_module_paths"] = {
        "setuptools": str(Path(setuptools.__file__).resolve()),
        "wheel": str(Path(wheel.__file__).resolve()),
    }

    venv_site = REPO / ".venv" / "Lib" / "site-packages"
    if not venv_site.is_dir():
        raise RuntimeError(f"existing project dependency site-packages not found: {venv_site}")

    with tempfile.TemporaryDirectory(prefix="lca-current-wheel-install-") as temporary:
        scratch = Path(temporary)
        source = scratch / "source"
        source.mkdir()
        for name in INPUTS:
            original = REPO / name
            target = source / name
            if original.is_dir():
                shutil.copytree(
                    original,
                    target,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
            else:
                shutil.copy2(original, target)

        snapshot_sha, snapshot_count, snapshot_text = manifest(source)
        if (snapshot_sha, snapshot_count, snapshot_text) != (before_sha, before_count, before_text):
            raise RuntimeError("copied source snapshot did not match the working tree manifest")

        dist = scratch / "dist"
        dist.mkdir()
        env = os.environ.copy()
        env.update(
            {
                "PIP_NO_INDEX": "1",
                "PIP_NO_CACHE_DIR": "1",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        build = save_process(
            "build",
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-index",
                "--no-deps",
                "--no-build-isolation",
                "--no-cache-dir",
                "--wheel-dir",
                str(dist),
                str(source),
            ],
            cwd=source,
            env=env,
        )
        evidence_record["wheel_build"] = build
        if build["exit_code"] != 0:
            raise RuntimeError("no-index wheel build failed")

        wheels = sorted(dist.glob("*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"expected exactly one wheel; found {len(wheels)}")
        built_wheel = wheels[0]
        preserved_wheel = EVIDENCE / built_wheel.name
        shutil.copy2(built_wheel, preserved_wheel)
        wheel_bytes = preserved_wheel.read_bytes()
        evidence_record["wheel"] = {
            "filename": built_wheel.name,
            "bytes": len(wheel_bytes),
            "sha256": hashlib.sha256(wheel_bytes).hexdigest(),
        }

        with zipfile.ZipFile(preserved_wheel) as archive:
            entries = set(archive.namelist())
            metadata_paths = sorted(name for name in entries if name.endswith(".dist-info/METADATA"))
            if len(metadata_paths) != 1:
                raise RuntimeError(f"expected exactly one wheel METADATA file; found {metadata_paths}")
            wheel_metadata = Parser().parsestr(archive.read(metadata_paths[0]).decode("utf-8"))
        missing = sorted(REQUIRED_WHEEL_ENTRIES - entries)
        if missing:
            raise RuntimeError(f"required package resources missing from wheel: {missing}")
        evidence_record["required_wheel_entries_present"] = True
        project_metadata = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        expected_runtime_dependencies = sorted(
            normalize_requirement_metadata(requirement)
            for requirement in project_metadata.get("dependencies", [])
        )
        if any(";" in requirement for requirement in expected_runtime_dependencies):
            raise RuntimeError("wheel smoke does not accept environment-marked runtime dependencies")
        optional_dependencies = project_metadata.get("optional-dependencies", {})
        if any(
            ";" in requirement
            for requirements in optional_dependencies.values()
            for requirement in requirements
        ):
            raise RuntimeError("wheel smoke does not accept pre-marked optional dependencies")
        expected_requires_dist = list(expected_runtime_dependencies)
        for extra, requirements in optional_dependencies.items():
            expected_requires_dist.extend(
                normalize_requirement_metadata(f'{requirement}; extra == "{extra}"')
                for requirement in requirements
            )
        expected_requires_dist.sort()
        actual_requires_dist = sorted(
            normalize_requirement_metadata(requirement)
            for requirement in (wheel_metadata.get_all("Requires-Dist") or [])
        )
        if actual_requires_dist != expected_requires_dist:
            raise RuntimeError(
                "wheel Requires-Dist metadata does not match pyproject.toml: "
                f"expected {expected_requires_dist}, found {actual_requires_dist}"
            )
        actual_runtime_dependencies = sorted(
            requirement for requirement in actual_requires_dist if ";" not in requirement
        )
        if actual_runtime_dependencies != expected_runtime_dependencies:
            raise RuntimeError(
                "wheel runtime dependencies do not match pyproject.toml: "
                f"expected {expected_runtime_dependencies}, found {actual_runtime_dependencies}"
            )
        expected_extras = sorted(extra.casefold() for extra in optional_dependencies)
        actual_extras = sorted((extra.casefold() for extra in (wheel_metadata.get_all("Provides-Extra") or [])))
        if actual_extras != expected_extras:
            raise RuntimeError(
                "wheel optional extras do not match pyproject.toml: "
                f"expected {expected_extras}, found {actual_extras}"
            )
        if wheel_metadata.get("Requires-Python") != project_metadata.get("requires-python"):
            raise RuntimeError("wheel Python requirement does not match pyproject.toml")
        evidence_record["wheel_runtime_dependencies_match"] = True
        evidence_record["wheel_runtime_dependencies"] = actual_runtime_dependencies
        evidence_record["wheel_requires_dist_match"] = True
        evidence_record["wheel_requires_dist"] = actual_requires_dist
        evidence_record["wheel_extras_match"] = True
        evidence_record["wheel_extras"] = actual_extras
        evidence_record["wheel_requires_python"] = wheel_metadata.get("Requires-Python")
        evidence_record["wheel_patch_entries"] = sorted(name for name in entries if name.endswith(".patch"))
        evidence_record["required_worker_module_entries_present"] = all(
            module.replace(".", "/") + ".py" in entries for module in WORKER_MODULES
        )
        evidence_record["required_installed_resource_entries_present"] = all(
            relative in entries for relative in INSTALLED_RESOURCE_PATHS
        )

        install_target = scratch / "installed"
        install_target.mkdir()
        install = save_process(
            "install",
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                "--no-cache-dir",
                "--target",
                str(install_target),
                str(preserved_wheel),
            ],
            cwd=scratch,
            env=env,
        )
        evidence_record["wheel_install"] = install
        if install["exit_code"] != 0:
            raise RuntimeError("no-index local wheel install failed")

        probe_env = env.copy()
        probe_env["PYTHONPATH"] = os.pathsep.join((str(install_target), str(venv_site)))
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "import importlib, json; "
                f"modules = {WORKER_MODULES!r}; "
                "print(json.dumps({name: importlib.import_module(name).__file__ for name in modules}))",
            ],
            cwd=scratch,
            env=probe_env,
            capture_output=True,
            text=True,
            check=False,
        )
        (EVIDENCE / "installed-imports.stdout.txt").write_text(probe.stdout, encoding="utf-8")
        (EVIDENCE / "installed-imports.stderr.txt").write_text(probe.stderr, encoding="utf-8")
        if probe.returncode:
            raise RuntimeError("imports from the no-index installed target failed")
        module_paths = json.loads(probe.stdout)
        target_resolved = install_target.resolve()
        for module_path in module_paths.values():
            if not Path(module_path).resolve().is_relative_to(target_resolved):
                raise RuntimeError(f"import resolved outside installed wheel target: {module_path}")
        evidence_record["installed_import_module_paths"] = module_paths
        evidence_record["runtime_dependencies_source"] = str(venv_site)

        installed_resources: dict[str, dict[str, object]] = {}
        for relative in INSTALLED_RESOURCE_PATHS:
            snapshot_resource = source / relative
            installed_resource = install_target / relative
            if not installed_resource.is_file():
                raise RuntimeError(f"required installed wheel resource is missing: {relative}")
            snapshot_hash = hashlib.sha256(snapshot_resource.read_bytes()).hexdigest()
            installed_hash = hashlib.sha256(installed_resource.read_bytes()).hexdigest()
            if installed_hash != snapshot_hash:
                raise RuntimeError(f"installed wheel resource differs from source snapshot: {relative}")
            installed_resources[relative] = {
                "snapshot_sha256": snapshot_hash,
                "installed_sha256": installed_hash,
                "matches_snapshot": True,
            }
        evidence_record["installed_snapshot_resources"] = installed_resources

        cli_runs: dict[str, object] = {}
        for name, arguments in (
            ("cli-help", ["-m", "autoloader", "--help"]),
            ("flash-help", ["-m", "autoloader", "flash", "--help"]),
        ):
            result = subprocess.run(
                [sys.executable, *arguments],
                cwd=scratch,
                env=probe_env,
                capture_output=True,
                text=True,
                check=False,
            )
            (EVIDENCE / f"{name}.stdout.txt").write_text(result.stdout, encoding="utf-8")
            (EVIDENCE / f"{name}.stderr.txt").write_text(result.stderr, encoding="utf-8")
            cli_runs[name] = {"argv": [sys.executable, *arguments], "exit_code": result.returncode}
            if result.returncode:
                raise RuntimeError(f"installed-wheel CLI {name} failed")
        evidence_record["cli_help"] = cli_runs
        evidence_record["result"] = "PASS"

    after_sha, after_count, after_text = manifest(REPO)
    (EVIDENCE / "source-manifest-postrun.sha256").write_text(after_text, encoding="utf-8")
    evidence_record["source_after_sha256"] = after_sha
    evidence_record["source_after_file_count"] = after_count
    evidence_record["source_unchanged"] = (before_sha, before_count, before_text) == (
        after_sha,
        after_count,
        after_text,
    )
    if not evidence_record["source_unchanged"]:
        evidence_record["result"] = "FAIL"
        raise RuntimeError("repository package inputs changed during the smoke")

    (EVIDENCE / "run.json").write_text(json.dumps(evidence_record, indent=2) + "\n", encoding="utf-8")
    return evidence_record


if __name__ == "__main__":
    try:
        result = run()
    except Exception as error:
        record = {"result": "FAIL", "error": f"{type(error).__name__}: {error}"}
        run_file = EVIDENCE / "run.json"
        if run_file.exists():
            try:
                record = json.loads(run_file.read_text(encoding="utf-8")) | record
            except (OSError, json.JSONDecodeError):
                pass
        run_file.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        raise
    print(json.dumps(result, indent=2))
