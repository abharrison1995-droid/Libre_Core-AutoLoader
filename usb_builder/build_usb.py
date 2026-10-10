"""Fail-closed Archiso build contract.

This module never installs host dependencies or invokes an implicit privilege
escalation. Candidate images use a fixed, signature-required package snapshot;
this builder never claims that a candidate boots or is release validated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = REPOSITORY_ROOT / "usb_builder" / "archiso_profile"
DEFAULT_SNAPSHOT = REPOSITORY_ROOT / "usb_builder" / "snapshot.lock.json"
WORK_PREFIX = "librecore-autoloader-"
REQUIRED_PACKAGES = frozenset(
    {
        "base",
        "linux",
        "linux-firmware",
        "python",
        "python-pip",
        "python-textual",
        "python-rich",
        "python-click",
        "python-psutil",
        "flashrom",
        "dmidecode",
        "usbutils",
        "pciutils",
    }
)
REQUIRED_MODES = frozenset(("online", "offline"))
REQUIRED_BOOT_MODES = frozenset(("bios.syslinux", "uefi.systemd-boot"))
EXPECTED_MKARCHISO_VERSION = "91-1"
EXPECTED_SNAPSHOT_DATE = "2026-09-29"
REQUIRED_RUNTIME_IMPORTS = ("autoloader", "click", "psutil", "rich", "textual")
OFFLINE_NETWORK_PROBE_MARKER = "AUTOLOADER_NETNS_ISOLATION_OK"


class BuildContractError(RuntimeError):
    """A build request does not satisfy the reviewed environment contract."""


@dataclass(frozen=True)
class BuildConfig:
    source_root: Path
    profile_dir: Path
    output_dir: Path
    work_parent: Path
    snapshot_lock: Path
    network_mode: str
    offline_repository: Path | None = None
    offline_manifest: Path | None = None


@dataclass(frozen=True)
class PreflightResult:
    snapshot: Mapping[str, Any]
    snapshot_sha256: str
    package_names: tuple[str, ...]
    required_boot_modes: tuple[str, ...]
    offline_repository: Path | None


@dataclass(frozen=True)
class BuildResult:
    iso_path: Path
    manifest_path: Path
    sha256: str
    manifest_sha256: str


@dataclass(frozen=True)
class NetworkIsolation:
    mode: str
    command_prefix: tuple[str, ...]
    privilege_mode: str
    unshare_path: str | None
    unshare_sha256: str | None
    probe_command: tuple[str, ...]
    probe_output: str


Runner = Callable[..., subprocess.CompletedProcess[str]]


def repository_root() -> Path:
    return REPOSITORY_ROOT


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _assert_no_link_components(path: Path, *, include_leaf: bool = True) -> None:
    absolute = Path(os.path.abspath(path))
    components = [absolute.anchor]
    current = Path(absolute.anchor)
    parts = absolute.parts[1:]
    if not include_leaf:
        parts = parts[:-1]
    for part in parts:
        current = current / part
        if _is_reparse_or_symlink(current):
            raise BuildContractError(f"path contains a symlink or junction component: {current}")


def _canonical_existing_dir(raw: Path, label: str, *, reject_root: bool = True) -> Path:
    if not raw.is_absolute():
        raise BuildContractError(f"{label} must be an absolute path: {raw}")
    _assert_no_link_components(raw)
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise BuildContractError(f"{label} must exist: {raw}") from exc
    if not resolved.is_dir():
        raise BuildContractError(f"{label} must be an existing directory: {resolved}")
    if reject_root and resolved == Path(resolved.anchor):
        raise BuildContractError(f"{label} cannot be a filesystem root")
    return resolved


def _canonical_existing_file(raw: Path, label: str) -> Path:
    if not raw.is_absolute():
        raise BuildContractError(f"{label} must be an absolute path: {raw}")
    _assert_no_link_components(raw)
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise BuildContractError(f"{label} must exist: {raw}") from exc
    if not resolved.is_file():
        raise BuildContractError(f"{label} must be a regular file: {resolved}")
    return resolved


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildContractError(f"{label} is not readable JSON: {path}") from exc
    if not isinstance(value, dict):
        raise BuildContractError(f"{label} must contain a JSON object")
    return value, raw


def _read_package_names(path: Path) -> tuple[str, ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise BuildContractError(f"package list is unreadable: {path}") from exc
    names = tuple(line.split("#", 1)[0].strip() for line in lines)
    names = tuple(name for name in names if name)
    if len(set(names)) != len(names):
        raise BuildContractError("package list contains duplicates")
    missing = REQUIRED_PACKAGES.difference(names)
    if missing:
        raise BuildContractError(f"package list is missing required closed runtime packages: {sorted(missing)}")
    return names


def _validate_package_list_pin(path: Path, snapshot: Mapping[str, Any]) -> None:
    expected = snapshot.get("packages_x86_64_sha256")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise BuildContractError("snapshot lock has no exact package-list SHA-256")
    if _sha256_file(path) != expected:
        raise BuildContractError("profile package list does not match the package list pinned by the snapshot")


def _profile_boot_modes(profile_dir: Path) -> tuple[str, ...]:
    profiledef = profile_dir / "profiledef.sh"
    try:
        content = profiledef.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise BuildContractError("profiledef.sh is unreadable") from exc
    if not re.search(r"(?m)^arch=['\"]x86_64['\"]\s*$", content):
        raise BuildContractError("Archiso profile must explicitly target x86_64")
    if not re.search(r"(?m)^buildmodes=\(['\"]iso['\"]\)\s*$", content):
        raise BuildContractError("Archiso profile must explicitly request ISO build mode")
    match = re.search(r"(?ms)^bootmodes=\((.*?)\)\s*$", content)
    if match is None:
        raise BuildContractError("profiledef.sh does not declare a parseable bootmodes array")
    modes = tuple(re.findall(r"['\"]([^'\"]+)['\"]", match.group(1)))
    if not REQUIRED_BOOT_MODES.issubset(modes):
        raise BuildContractError("profile does not declare the required BIOS and x86_64 UEFI modes")
    boot_files = (
        "syslinux/syslinux.cfg",
        "syslinux/archiso_sys.cfg",
        "syslinux/archiso_sys-linux.cfg",
        "efiboot/loader/loader.conf",
        "efiboot/loader/entries/01-autoloader.conf",
    )
    missing = [name for name in boot_files if not (profile_dir / name).is_file()]
    if missing:
        raise BuildContractError(f"profile is missing boot configuration required by Archiso 91: {missing}")
    syslinux = (profile_dir / "syslinux" / "archiso_sys-linux.cfg").read_text(encoding="utf-8")
    uefi = (profile_dir / "efiboot" / "loader" / "entries" / "01-autoloader.conf").read_text(encoding="utf-8")
    for config_text in (syslinux, uefi):
        if "%INSTALL_DIR%" not in config_text or "%ARCH%" not in config_text or "%KERNEL_PARAMS%" not in config_text:
            raise BuildContractError("BIOS/UEFI boot entry is missing required Archiso profile placeholders")
    return modes


def _audit_profile_tree(profile_dir: Path) -> None:
    for path in profile_dir.rglob("*"):
        if _is_reparse_or_symlink(path):
            raise BuildContractError(f"profile contains an unsupported symlink/junction: {path}")
        if not (path.is_dir() or path.is_file()):
            raise BuildContractError(f"profile contains a non-regular object: {path}")
    forbidden_suffixes = (".rom", ".rom.gz", ".bin", ".hex", ".dump", ".backup", ".pem", ".key", ".p12", ".pfx", ".kdbx")
    forbidden = [
        p for p in profile_dir.rglob("*") if p.is_file() and p.name.casefold().endswith(forbidden_suffixes)
    ]
    if forbidden:
        raise BuildContractError("firmware images/blobs must never be bundled into the Live USB profile")


def _validate_online_pacman_config(profile_dir: Path, snapshot: Mapping[str, Any]) -> None:
    pacman_conf = profile_dir / "pacman.conf"
    try:
        content = pacman_conf.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise BuildContractError("profile pacman.conf is unreadable") from exc
    base = snapshot.get("repository_base")
    if not isinstance(base, str) or not re.fullmatch(r"https://archive\.archlinux\.org/repos/\d{4}/\d{2}/\d{2}", base):
        raise BuildContractError("snapshot lock repository URL is not an exact dated official archive URL")
    if content.count(f"Server = {base}/$repo/os/$arch") != 2:
        raise BuildContractError("online pacman config must point core and extra only at the locked dated snapshot")
    if "Include =" in content or "mirrorlist" in content.casefold() or "[community]" in content:
        raise BuildContractError("online pacman config must not include moving mirrors or an obsolete repository")
    if re.search(r"(?m)^\s*SigLevel\s*=\s*Required\s+DatabaseRequired\s*$", content) is None:
        raise BuildContractError("snapshot package and database signatures must be required")


def _validate_offline_repository(
    repository: Path,
    manifest_path: Path,
    snapshot_lock_sha256: str,
    required_package_names: Sequence[str],
) -> Path:
    repo_root = _canonical_existing_dir(repository, "offline repository")
    manifest_path = _canonical_existing_file(manifest_path, "offline repository manifest")
    document, _ = _load_json(manifest_path, "offline repository manifest")
    if document.get("schema_version") != 1 or document.get("snapshot_lock_sha256") != snapshot_lock_sha256:
        raise BuildContractError("offline repository manifest must bind schema 1 to the exact snapshot lock")
    files = document.get("files")
    if not isinstance(files, dict) or not files:
        raise BuildContractError("offline repository manifest must enumerate every repository file and SHA-256")
    expected: dict[str, str] = {}
    for name, digest in files.items():
        if not isinstance(name, str) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise BuildContractError("offline repository manifest contains an invalid path or SHA-256")
        relative_path = Path(name)
        if relative_path.is_absolute() or ".." in relative_path.parts or "\\" in name:
            raise BuildContractError("offline repository file paths must be relative and traversal-free")
        expected[relative_path.as_posix()] = digest
    required_db = {
        "core/os/x86_64/core.db",
        "core/os/x86_64/core.db.sig",
        "extra/os/x86_64/extra.db",
        "extra/os/x86_64/extra.db.sig",
    }
    if not required_db.issubset(expected):
        raise BuildContractError("offline repository is missing signed core/extra package databases")
    package_entries = document.get("packages")
    if not isinstance(package_entries, dict) or not set(required_package_names).issubset(package_entries):
        raise BuildContractError("offline manifest must bind every image package to a local signed archive")
    for package_name in required_package_names:
        archive = package_entries.get(package_name)
        if not isinstance(archive, str) or archive not in expected or not archive.endswith(".pkg.tar.zst"):
            raise BuildContractError(f"offline manifest has no checked zstd archive for package {package_name}")
        if archive + ".sig" not in expected:
            raise BuildContractError(f"offline package has no enumerated signature file: {package_name}")
    actual_paths: set[str] = set()
    for item in repo_root.rglob("*"):
        if _is_reparse_or_symlink(item):
            raise BuildContractError(f"offline repository contains a symlink/junction: {item}")
        if item.is_file():
            relative_name = item.relative_to(repo_root).as_posix()
            actual_paths.add(relative_name)
            if relative_name not in expected or _sha256_file(item) != expected[relative_name]:
                raise BuildContractError(f"offline repository file is unlisted or has changed: {relative_name}")
        elif not item.is_dir():
            raise BuildContractError(f"offline repository contains a non-regular object: {item}")
    if actual_paths != set(expected):
        raise BuildContractError("offline repository does not exactly match its file/hash manifest")
    return repo_root


def preflight(config: BuildConfig, *, host_platform: str | None = None) -> PreflightResult:
    """Validate all paths and profile/snapshot inputs without launching tools."""
    if config.network_mode not in REQUIRED_MODES:
        raise BuildContractError("network mode must be explicitly set to online or offline")
    host_platform = host_platform or sys.platform
    if host_platform != "linux":
        raise BuildContractError("LINUX_BUILD_ENV_REQUIRED: Archiso images are not built on this host")

    source = _canonical_existing_dir(config.source_root, "source root")
    profile = _canonical_existing_dir(config.profile_dir, "profile")
    output = _canonical_existing_dir(config.output_dir, "output directory")
    work_parent = _canonical_existing_dir(config.work_parent, "work parent")
    snapshot_path = _canonical_existing_file(config.snapshot_lock, "snapshot lock")
    if profile != source / "usb_builder" / "archiso_profile":
        raise BuildContractError("profile path must be the reviewed archiso_profile inside the source root")
    if snapshot_path != source / "usb_builder" / "snapshot.lock.json":
        raise BuildContractError("snapshot lock must be the checked-in source lock")
    if not (source / ".git").exists():
        raise BuildContractError("source root must be a Git checkout for provenance recording")
    if any(output.iterdir()):
        raise BuildContractError("output directory must already exist and be empty; builder never overwrites outputs")
    if output == work_parent or _is_within(output, work_parent) or _is_within(work_parent, output):
        raise BuildContractError("output directory and work parent must be separate, non-nested paths")
    for protected in (source, profile, snapshot_path.parent):
        if _is_within(output, protected) or _is_within(work_parent, protected):
            raise BuildContractError("output/work paths may not be inside source, profile or snapshot directories")
    _audit_profile_tree(profile)
    package_names = _read_package_names(profile / "packages.x86_64")
    modes = _profile_boot_modes(profile)
    snapshot, raw_snapshot = _load_json(snapshot_path, "snapshot lock")
    snapshot_sha = _sha256_bytes(raw_snapshot)
    if snapshot.get("schema_version") != 1 or snapshot.get("architecture") != "x86_64":
        raise BuildContractError("snapshot lock schema or architecture is unsupported")
    if snapshot.get("archiso_package") != "archiso" or snapshot.get("archiso_version") != EXPECTED_MKARCHISO_VERSION:
        raise BuildContractError("snapshot lock does not pin the reviewed archiso package version")
    if snapshot.get("snapshot_date") != EXPECTED_SNAPSHOT_DATE:
        raise BuildContractError("snapshot lock date differs from the reviewed pinned repository snapshot")
    expected_repository_base = "https://archive.archlinux.org/repos/" + EXPECTED_SNAPSHOT_DATE.replace("-", "/")
    if snapshot.get("repository_base") != expected_repository_base:
        raise BuildContractError("snapshot lock URL must use the official YYYY/MM/DD archive path for its locked date")
    locked_modes = snapshot.get("required_boot_modes")
    if not isinstance(locked_modes, list) or set(locked_modes) != REQUIRED_BOOT_MODES or not REQUIRED_BOOT_MODES.issubset(modes):
        raise BuildContractError("profile BIOS/UEFI modes do not match the reviewed snapshot lock")
    if snapshot.get("snapshot_pin_status") != "fixed-candidate":
        raise BuildContractError("SNAPSHOT_NOT_PINNED: candidate build requires a fixed package snapshot")
    if snapshot.get("package_signature_policy") != "Required DatabaseRequired":
        raise BuildContractError("SNAPSHOT_SIGNATURE_POLICY_INVALID: package and repository database signatures must be required")
    if snapshot.get("wheel_build_backend") != {
        "backend": "setuptools.build_meta",
        "minimum_setuptools_version": "61.0",
        "required_tools": ["setuptools", "wheel"],
    }:
        raise BuildContractError("SNAPSHOT_WHEEL_BACKEND_INVALID: wheel backend/tool requirements are not the reviewed lock")
    if snapshot.get("release_validation_status") not in {"pending-linux-build-and-qemu", "verified"}:
        raise BuildContractError("SNAPSHOT_RELEASE_STATUS_INVALID: release status must be pending or reviewed")
    _validate_package_list_pin(profile / "packages.x86_64", snapshot)
    _validate_online_pacman_config(profile, snapshot)

    offline_root: Path | None = None
    if config.network_mode == "offline":
        if config.offline_repository is None or config.offline_manifest is None:
            raise BuildContractError("offline mode requires an explicit local repository and exact hash manifest")
        offline_root = _validate_offline_repository(
            config.offline_repository,
            config.offline_manifest,
            snapshot_sha,
            package_names,
        )
    elif config.offline_repository is not None or config.offline_manifest is not None:
        raise BuildContractError("offline repository inputs are invalid in online mode")
    return PreflightResult(snapshot, snapshot_sha, package_names, modes, offline_root)


def _source_hashes(source_root: Path, profile_dir: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(profile_dir.rglob("*")):
        if path.is_file():
            rel = path.relative_to(source_root).as_posix()
            result[rel] = _sha256_file(path)
    return result


def _build_backend_evidence(runner: Runner) -> dict[str, Any]:
    pip_version = _run_command((sys.executable, "-m", "pip", "--version"), runner=runner).stdout.strip()
    show_output = _run_command(
        (sys.executable, "-m", "pip", "show", "setuptools", "wheel"), runner=runner
    ).stdout
    package_versions: dict[str, str] = {}
    current_name: str | None = None
    for line in show_output.splitlines():
        if line.startswith("Name:"):
            current_name = line.partition(":")[2].strip().casefold()
        elif line.startswith("Version:") and current_name is not None:
            package_versions[current_name] = line.partition(":")[2].strip()
            current_name = None
    if not pip_version or not {"setuptools", "wheel"}.issubset(package_versions):
        raise BuildContractError("local wheel build requires installed pip, setuptools and wheel; no installer is run")
    version_text = package_versions["setuptools"]
    numeric = re.match(r"^(\d+)(?:\.(\d+))?", version_text)
    if numeric is None:
        raise BuildContractError("cannot parse the installed setuptools version")
    setuptools_version = (int(numeric.group(1)), int(numeric.group(2) or 0))
    if setuptools_version < (61, 0):
        raise BuildContractError("local wheel build requires setuptools >= 61.0")
    return {
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "pip_version_output": pip_version,
        "backend": "setuptools.build_meta",
        "backend_packages": package_versions,
        "build_isolation": "disabled",
        "network_access": "disabled",
    }


def _run_command(
    command: Sequence[str],
    *,
    runner: Runner,
    env: Mapping[str, str] | None = None,
    stdout: Any = subprocess.PIPE,
) -> subprocess.CompletedProcess[str]:
    result = runner(list(command), check=False, text=True, stdout=stdout, stderr=subprocess.STDOUT, env=env)
    if result.returncode != 0:
        output = result.stdout or ""
        raise BuildContractError(f"command failed ({result.returncode}): {' '.join(command)}\n{output[-4000:]}")
    return result


def _current_network_namespace_identity() -> str:
    try:
        return os.readlink("/proc/self/ns/net")
    except OSError as exc:
        raise BuildContractError("OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: cannot identify the current Linux network namespace") from exc


def _network_namespace_probe_script() -> str:
    return (
        "import os, pathlib, sys\n"
        "expected = sys.argv[1]\n"
        "actual = os.readlink('/proc/self/ns/net')\n"
        "if actual == expected: raise SystemExit('network namespace was not changed')\n"
        "interfaces = set(os.listdir('/sys/class/net'))\n"
        "if interfaces != {'lo'}: raise SystemExit(f'unexpected network interfaces: {sorted(interfaces)}')\n"
        "routes = pathlib.Path('/proc/net/route').read_text().splitlines()\n"
        "if len(routes) != 1: raise SystemExit('IPv4 routes exist in offline namespace')\n"
        "ipv6_routes = pathlib.Path('/proc/net/ipv6_route')\n"
        "if ipv6_routes.exists() and ipv6_routes.read_text().strip(): raise SystemExit('IPv6 routes exist in offline namespace')\n"
        f"print('{OFFLINE_NETWORK_PROBE_MARKER}')\n"
    )


def _establish_offline_network_isolation(
    runner: Runner,
    *,
    unshare_path: str | None = None,
    host_namespace_identity: str | None = None,
    is_root: bool | None = None,
) -> NetworkIsolation:
    """Verify a route-free network namespace before creating any build work."""
    candidate = unshare_path or shutil.which("unshare")
    if not candidate:
        raise BuildContractError("OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: util-linux unshare is required")
    unshare_file = _canonical_existing_file(Path(candidate), "unshare executable")
    host_identity = host_namespace_identity or _current_network_namespace_identity()
    if is_root is None:
        get_effective_uid = getattr(os, "geteuid", None)
        is_root = callable(get_effective_uid) and get_effective_uid() == 0
    privilege_mode = "root" if is_root else "user-mapped-root"
    namespace_flags = ("--net",) if is_root else ("--user", "--map-root-user", "--net")
    command = (
        str(unshare_file),
        *namespace_flags,
        "--",
        sys.executable,
        "-c",
        _network_namespace_probe_script(),
        host_identity,
    )
    try:
        result = runner(list(command), check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=None)
    except OSError as exc:
        raise BuildContractError(f"OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: failed to start unshare probe: {exc}") from exc
    output = result.stdout or ""
    if result.returncode != 0 or output.strip() != OFFLINE_NETWORK_PROBE_MARKER:
        raise BuildContractError(
            "OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: could not establish and verify a route-free network namespace; "
            f"unshare returned {result.returncode}: {output[-2000:]}"
        )
    return NetworkIsolation(
        mode="linux-network-namespace",
        command_prefix=(str(unshare_file), *namespace_flags, "--"),
        privilege_mode=privilege_mode,
        unshare_path=str(unshare_file),
        unshare_sha256=_sha256_file(unshare_file),
        probe_command=command,
        probe_output=output.strip(),
    )


def _network_guarded_runner(
    runner: Runner,
    isolation: NetworkIsolation,
    *,
    command_log: list[list[str]] | None = None,
) -> Runner:
    def guarded(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        actual_command = [*isolation.command_prefix, *command]
        if command_log is not None:
            command_log.append(actual_command)
        return runner(actual_command, **kwargs)

    return guarded


def _source_provenance(source: Path, runner: Runner) -> dict[str, str]:
    head = _run_command(("git", "-C", str(source), "rev-parse", "HEAD"), runner=runner).stdout.strip()
    tree = _run_command(("git", "-C", str(source), "rev-parse", "HEAD^{tree}"), runner=runner).stdout.strip()
    status = _run_command(("git", "-C", str(source), "status", "--porcelain", "--untracked-files=all"), runner=runner).stdout
    if status.strip():
        raise BuildContractError("source Git tree is dirty; commit the source before producing a release image")
    if not re.fullmatch(r"[0-9a-f]{40}", head) or not re.fullmatch(r"[0-9a-f]{40}", tree):
        raise BuildContractError("Git did not provide exact commit/tree provenance")
    return {"commit": head, "tree": tree, "dirty": "false"}


def _parse_package_inventory(output: str, *, label: str, required: Sequence[str] = ()) -> tuple[str, ...]:
    packages: dict[str, str] = {}
    for line in output.splitlines():
        match = re.fullmatch(r"([A-Za-z0-9@._+-]+)\s+(\S+)", line.strip())
        if match is None or match.group(1) in packages:
            raise BuildContractError(f"{label} is malformed or contains duplicate package names")
        packages[match.group(1)] = match.group(2)
    if not packages:
        raise BuildContractError(f"{label} is empty")
    missing = sorted(set(required).difference(packages))
    if missing:
        raise BuildContractError(f"{label} is missing required packages: {missing}")
    return tuple(f"{name} {packages[name]}" for name in sorted(packages))


def _copy_wheel_source(source: Path, destination: Path) -> dict[str, str]:
    """Stage only package/build metadata files so pip cannot mutate the checkout."""
    destination.mkdir(mode=0o700)
    for filename in ("pyproject.toml", "README.md", "LICENSE"):
        path = source / filename
        if _is_reparse_or_symlink(path) or not path.is_file():
            raise BuildContractError(f"wheel build input is missing or linked: {path}")
        shutil.copy2(path, destination / filename)
    hashes: dict[str, str] = {}
    for package in ("autoloader", "usb_builder"):
        package_source = source / package
        if not package_source.is_dir() or _is_reparse_or_symlink(package_source):
            raise BuildContractError(f"wheel package source is missing or linked: {package_source}")
        for item in package_source.rglob("*"):
            if _is_reparse_or_symlink(item):
                raise BuildContractError(f"wheel source contains a symlink or junction: {item}")
            if item.is_file() and item.suffix.casefold() in {
                ".rom", ".bin", ".dump", ".backup", ".pem", ".key", ".p12", ".pfx", ".kdbx"
            }:
                raise BuildContractError(f"firmware, backup, or credential file cannot enter the wheel build: {item}")
        shutil.copytree(
            package_source,
            destination / package,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
        )
    for path in sorted(destination.rglob("*")):
        if path.is_file():
            hashes[path.relative_to(destination).as_posix()] = _sha256_file(path)
    return hashes


def _snapshot_url_for_profile(pacman_conf: str) -> str:
    match = re.search(r"(?m)^\s*Server\s*=\s*(https://archive\.archlinux\.org/repos/\d{4}/\d{2}/\d{2})/\$repo/os/\$arch\s*$", pacman_conf)
    if match is None:
        raise BuildContractError("cannot derive locked online snapshot URL")
    return match.group(1)


def _copy_runbooks(source_root: Path, stage_profile: Path) -> None:
    rootfs = stage_profile / "airootfs"
    doc_root = rootfs / "usr" / "share" / "doc" / "libre-core-autoloader"
    doc_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_root / "README.md", doc_root / "README.md")
    shutil.copytree(source_root / "docs" / "v1", doc_root / "v1", dirs_exist_ok=True)


def _enable_autoloader_service(stage_profile: Path) -> Path:
    target_dropin = stage_profile / "airootfs" / "etc" / "systemd" / "system" / "multi-user.target.d"
    target_dropin.mkdir(parents=True, exist_ok=True)
    path = target_dropin / "autoloader.conf"
    if path.exists() or _is_reparse_or_symlink(path):
        raise BuildContractError("profile already contains an unexpected service activation override")
    path.write_text("[Unit]\nWants=autoloader.service\n", encoding="utf-8", newline="\n")
    return path


def _write_image_input_provenance(
    source_root: Path,
    stage_profile: Path,
    *,
    snapshot_sha256: str,
    source_info: Mapping[str, str],
    wheel: Path,
    package_names: Sequence[str],
) -> Path:
    target = (
        stage_profile
        / "airootfs"
        / "usr"
        / "share"
        / "doc"
        / "libre-core-autoloader"
        / "image-build-inputs.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": 1,
        "source": dict(source_info),
        "snapshot_lock_sha256": snapshot_sha256,
        "profile_file_sha256": _source_hashes(source_root, source_root / "usb_builder" / "archiso_profile"),
        "wheel": {"filename": wheel.name, "sha256": _sha256_file(wheel), "size_bytes": wheel.stat().st_size},
        "package_names_from_locked_snapshot": list(package_names),
        "wheel_build": {
            "tool": Path(sys.executable).name,
            "operation": "pip wheel",
            "backend": "setuptools.build_meta",
            "source": "temporary package-only source copy",
            "output": "temporary local wheelhouse",
            "build_isolation": "disabled",
            "dependency_resolution": "disabled",
            "network_access": "disabled",
        },
        "build_and_boot_status": "BUILD_NOT_PERFORMED_AT_PACKAGE_INSTALL_TIME; BOOT_NOT_PERFORMED",
    }
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def _copy_wheel_into_profile(wheel: Path, stage_profile: Path) -> None:
    destination = stage_profile / "airootfs" / "opt" / "autoloader" / "wheelhouse"
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(wheel, destination / wheel.name)


def _install_offline_conf(stage_profile: Path, offline_root: Path, temp_root: Path) -> Path:
    staged = temp_root / "pacman-offline.conf"
    staged.write_text(
        (stage_profile / "pacman.conf").read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
    )
    content = staged.read_text(encoding="utf-8")
    url = offline_root.as_uri()
    replaced = content.replace(
        "Server = " + _snapshot_url_for_profile(content) + "/$repo/os/$arch",
        "Server = " + url + "/$repo/os/$arch",
    )
    if "https://" in replaced or "http://" in replaced or replaced == content:
        raise BuildContractError("offline pacman config must contain only local repository URLs")
    staged.write_text(replaced, encoding="utf-8", newline="\n")
    return staged


def _mountpoints_from_proc() -> tuple[str, ...]:
    mountinfo = Path("/proc/self/mountinfo")
    if not mountinfo.is_file():
        raise BuildContractError("cannot inspect Linux mount table before cleanup; retaining temporary work")
    try:
        lines = mountinfo.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise BuildContractError("cannot inspect Linux mount table before cleanup; retaining temporary work") from exc
    values = []
    for line in lines:
        fields = line.split()
        if len(fields) >= 5:
            values.append(fields[4].replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\"))
    return tuple(values)


def cleanup_owned_workdir(
    work_dir: Path,
    work_parent: Path,
    owner_token: str,
    *,
    mountpoints: Sequence[str],
) -> None:
    """Remove only our marked direct child after a fresh containment/mount check."""
    parent = _canonical_existing_dir(work_parent, "work parent")
    candidate = Path(os.path.abspath(work_dir))
    _assert_no_link_components(candidate)
    if candidate.parent.resolve(strict=True) != parent or candidate.name != WORK_PREFIX + owner_token:
        raise BuildContractError("refusing cleanup: work directory is outside its owned parent/prefix")
    if candidate.resolve(strict=True) != candidate:
        raise BuildContractError("refusing cleanup: work directory canonical path changed")
    marker = candidate / ".owner"
    if _is_reparse_or_symlink(marker) or not marker.is_file():
        raise BuildContractError("refusing cleanup: work directory ownership marker is missing or linked")
    if marker.read_text(encoding="ascii") != owner_token:
        raise BuildContractError("refusing cleanup: ownership token changed")
    prefix = str(candidate) + os.sep
    for mount in mountpoints:
        canonical_mount = os.path.normpath(mount)
        if canonical_mount == str(candidate) or canonical_mount.startswith(prefix):
            raise BuildContractError(f"refusing cleanup: a mount is present inside the work tree: {mount}")
    # shutil.rmtree uses an fd-based safe implementation on supported Linux builds.
    shutil.rmtree(candidate)


def _profile_copy(profile: Path, temp_root: Path) -> Path:
    stage_profile = temp_root / "profile"
    shutil.copytree(profile, stage_profile, symlinks=False)
    return stage_profile


def _one_wheel(wheel_dir: Path) -> Path:
    wheels = [path for path in wheel_dir.iterdir() if path.is_file() and path.suffix == ".whl" and not _is_reparse_or_symlink(path)]
    if len(wheels) != 1:
        raise BuildContractError(f"expected exactly one locally built application wheel, found {len(wheels)}")
    if "libre_core_autoloader" not in wheels[0].name.casefold():
        raise BuildContractError("built wheel filename does not identify the AutoLoader application")
    return wheels[0]


def _record_package_versions(target_root: Path, runner: Runner, required_packages: Sequence[str]) -> tuple[str, ...]:
    if not target_root.is_dir():
        raise BuildContractError("mkarchiso completed without its target root filesystem; package provenance is unavailable")
    result = _run_command(("pacman", "--root", str(target_root), "-Q"), runner=runner)
    return _parse_package_inventory(result.stdout, label="installed package version inventory", required=required_packages)


def _require_rootfs_file(root: Path, relative: str, label: str, *, executable: bool = False, symlink_ok: bool = False) -> Path:
    path = root.joinpath(*Path(relative).parts)
    _assert_no_link_components(path, include_leaf=False)
    try:
        info = path.lstat()
    except OSError as exc:
        raise BuildContractError(f"target root is missing {label}: {relative}") from exc
    if stat.S_ISLNK(info.st_mode):
        if not symlink_ok:
            raise BuildContractError(f"target root {label} must be a regular file, not a symlink")
        resolved = _resolve_rootfs_symlink(root, path, label)
        resolved_info = resolved.lstat()
        if not stat.S_ISREG(resolved_info.st_mode):
            raise BuildContractError(f"target root {label} symlink does not resolve to an in-image regular file")
        if executable and sys.platform == "linux" and not (resolved_info.st_mode & 0o111):
            raise BuildContractError(f"target root {label} symlink target is not executable")
    elif not stat.S_ISREG(info.st_mode):
        raise BuildContractError(f"target root {label} must be a regular file")
    if executable and sys.platform == "linux" and not (info.st_mode & 0o111) and not stat.S_ISLNK(info.st_mode):
        raise BuildContractError(f"target root {label} is not executable")
    return path


def _resolve_rootfs_symlink(root: Path, link_path: Path, label: str) -> Path:
    """Follow an in-image symlink lexically without resolving it against the host root."""
    current = link_path
    seen: set[Path] = set()
    for _ in range(40):
        try:
            relative = current.relative_to(root)
        except ValueError as exc:
            raise BuildContractError(f"target root {label} symlink escapes the image") from exc
        _assert_no_link_components(current, include_leaf=False)
        try:
            info = current.lstat()
        except OSError as exc:
            raise BuildContractError(f"target root {label} symlink target is missing") from exc
        if not stat.S_ISLNK(info.st_mode):
            return current
        if current in seen:
            raise BuildContractError(f"target root {label} has a symlink cycle")
        seen.add(current)
        link_value = os.readlink(current)
        # Symlink payloads in the target image use POSIX paths even in Windows fixture tests.
        link_parts = list(Path(link_value.replace("\\", "/")).parts)
        if link_value.startswith("/"):
            parts: list[str] = []
        else:
            parts = list(relative.parent.parts)
        for part in link_parts:
            if part in {"", ".", "/", "\\"}:
                continue
            if part == "..":
                if not parts:
                    raise BuildContractError(f"target root {label} symlink escapes the image")
                parts.pop()
            else:
                parts.append(part)
        current = root.joinpath(*parts)
    raise BuildContractError(f"target root {label} exceeds the symlink resolution limit")


def _rootfs_runtime_validation_script() -> str:
    imports = ", ".join(repr(name) for name in REQUIRED_RUNTIME_IMPORTS)
    return (
        "import hashlib, importlib, importlib.metadata, json, pathlib, sys\n"
        f"required = ({imports},)\n"
        "modules = {name: importlib.import_module(name) for name in required}\n"
        "if not hasattr(importlib.import_module('autoloader.__main__'), 'main'): raise SystemExit('CLI main entrypoint missing')\n"
        "dist = importlib.metadata.distribution('libre-core-autoloader')\n"
        "if dist.metadata.get('Name', '').casefold().replace('_', '-') != 'libre-core-autoloader': raise SystemExit('wrong distribution name')\n"
        "entries = [ep.value for ep in dist.entry_points if ep.group == 'console_scripts' and ep.name == 'autoloader']\n"
        "if entries != ['autoloader.__main__:main']: raise SystemExit('AutoLoader console entrypoint missing or ambiguous')\n"
        "if not str(pathlib.Path(modules['autoloader'].__file__).resolve()).startswith(str(pathlib.Path(sys.prefix).resolve()) + '/'):\n"
        "    raise SystemExit('AutoLoader package is not installed in the application venv')\n"
        "wheelhouse = pathlib.Path('/opt/autoloader/wheelhouse')\n"
        "wheels = list(wheelhouse.glob('*.whl'))\n"
        "if len(wheels) != 1: raise SystemExit('installed wheelhouse is missing or ambiguous')\n"
        "wheel_hash = hashlib.sha256(wheels[0].read_bytes()).hexdigest()\n"
        "if wheel_hash != sys.argv[1]: raise SystemExit('installed wheel fingerprint mismatch')\n"
        "print(json.dumps({'distribution_name': dist.metadata['Name'], 'distribution_version': dist.version, "
        "'entrypoint': entries[0], 'runtime_imports': sorted(modules), 'wheel_sha256': wheel_hash, "
        "'python_version': sys.version.split()[0]}, sort_keys=True))\n"
    )


def _validate_target_root_install(
    target_root: Path,
    *,
    wheel: Path,
    runner: Runner,
) -> dict[str, Any]:
    """Require the customize hook's verified app install and active service in airootfs."""
    _require_rootfs_file(
        target_root,
        "opt/autoloader/venv/bin/python",
        "application venv interpreter",
        executable=True,
        symlink_ok=True,
    )
    _require_rootfs_file(
        target_root,
        "opt/autoloader/venv/bin/autoloader",
        "AutoLoader console entrypoint",
        executable=True,
    )
    _require_rootfs_file(target_root, "opt/autoloader/venv/pyvenv.cfg", "application venv configuration")
    site_packages = list((target_root / "opt/autoloader/venv/lib").glob("python*/site-packages"))
    if len(site_packages) != 1 or _is_reparse_or_symlink(site_packages[0]) or not site_packages[0].is_dir():
        raise BuildContractError("target root does not contain exactly one regular application venv site-packages directory")
    package_file = site_packages[0] / "autoloader" / "__init__.py"
    _require_rootfs_file(target_root, package_file.relative_to(target_root).as_posix(), "installed AutoLoader package")
    dist_infos = list(site_packages[0].glob("libre_core_autoloader-*.dist-info"))
    if len(dist_infos) != 1 or _is_reparse_or_symlink(dist_infos[0]) or not dist_infos[0].is_dir():
        raise BuildContractError("target root must contain exactly one AutoLoader dist-info directory")
    metadata_path = dist_infos[0] / "METADATA"
    entry_points_path = dist_infos[0] / "entry_points.txt"
    _require_rootfs_file(target_root, metadata_path.relative_to(target_root).as_posix(), "AutoLoader distribution metadata")
    _require_rootfs_file(target_root, entry_points_path.relative_to(target_root).as_posix(), "AutoLoader entry-point metadata")
    metadata_text = metadata_path.read_text(encoding="utf-8")
    entry_points_text = entry_points_path.read_text(encoding="utf-8")
    if re.search(r"(?mi)^Name:\s*libre-core-autoloader\s*$", metadata_text) is None:
        raise BuildContractError("target root AutoLoader distribution metadata has the wrong package name")
    if re.search(r"(?m)^autoloader\s*=\s*autoloader\.__main__:main\s*$", entry_points_text) is None:
        raise BuildContractError("target root AutoLoader distribution metadata lacks the expected CLI entrypoint")

    service_path = _require_rootfs_file(
        target_root,
        "etc/systemd/system/autoloader.service",
        "AutoLoader systemd unit",
    )
    activation_path = _require_rootfs_file(
        target_root,
        "etc/systemd/system/multi-user.target.d/autoloader.conf",
        "AutoLoader systemd activation drop-in",
    )
    service = service_path.read_text(encoding="utf-8")
    activation = activation_path.read_text(encoding="utf-8")
    required_service_lines = (
        "ExecStart=/opt/autoloader/start.sh",
        "After=getty@tty1.service",
        "Conflicts=getty@tty1.service",
        "Restart=no",
        "StandardOutput=journal+console",
    )
    if any(line not in service.splitlines() for line in required_service_lines):
        raise BuildContractError("target root AutoLoader systemd unit is incomplete or does not own tty1 safely")
    if activation.strip() != "[Unit]\nWants=autoloader.service":
        raise BuildContractError("target root AutoLoader service is not enabled from multi-user.target")
    start_script_path = _require_rootfs_file(
        target_root,
        "opt/autoloader/start.sh",
        "AutoLoader startup script",
        executable=True,
    )
    marker_path = _require_rootfs_file(
        target_root,
        "var/lib/autoloader/image-install.json",
        "customize-hook install marker",
    )
    marker, _ = _load_json(marker_path, "customize-hook install marker")
    expected_wheel_sha = _sha256_file(wheel)
    marker_distribution = marker.get("distribution_name")
    if (
        marker.get("schema_version") != 1
        or marker.get("status") != "APP_INSTALLED_AND_RUNTIME_CHECKED"
        or not isinstance(marker_distribution, str)
        or marker_distribution.casefold().replace("_", "-") != "libre-core-autoloader"
        or marker.get("entrypoint") != "autoloader.__main__:main"
        or marker.get("wheel_sha256") != expected_wheel_sha
    ):
        raise BuildContractError("target root lacks valid customize-hook app installation evidence")

    runtime = _run_command(
        (
            "chroot",
            str(target_root),
            "/opt/autoloader/venv/bin/python",
            "-c",
            _rootfs_runtime_validation_script(),
            expected_wheel_sha,
        ),
        runner=runner,
    )
    try:
        runtime_document = json.loads(runtime.stdout.strip())
    except (json.JSONDecodeError, AttributeError) as exc:
        raise BuildContractError("target root runtime import check returned no parseable evidence") from exc
    runtime_distribution = runtime_document.get("distribution_name")
    if (
        not isinstance(runtime_distribution, str)
        or runtime_distribution.casefold().replace("_", "-") != "libre-core-autoloader"
        or runtime_document.get("entrypoint") != "autoloader.__main__:main"
        or tuple(runtime_document.get("runtime_imports", ())) != tuple(sorted(REQUIRED_RUNTIME_IMPORTS))
        or runtime_document.get("wheel_sha256") != expected_wheel_sha
    ):
        raise BuildContractError("target root runtime import evidence does not match the application wheel contract")
    cli = _run_command(
        ("chroot", str(target_root), "/opt/autoloader/venv/bin/autoloader", "--help"),
        runner=runner,
    )
    if not cli.stdout.startswith("Usage: autoloader"):
        raise BuildContractError("target root AutoLoader console command did not pass its --help smoke check")
    return {
        "status": "APP_INSTALLED_AND_RUNTIME_CHECKED",
        "customize_hook_marker_sha256": _sha256_file(marker_path),
        "wheel_sha256": expected_wheel_sha,
        "distribution_metadata_sha256": _sha256_file(metadata_path),
        "entrypoint_metadata_sha256": _sha256_file(entry_points_path),
        "service_unit_sha256": _sha256_file(service_path),
        "service_activation_sha256": _sha256_file(activation_path),
        "startup_script_sha256": _sha256_file(start_script_path),
        "runtime_smoke": runtime_document,
        "console_help_sha256": _sha256_bytes(cli.stdout.encode("utf-8")),
    }


def execute_build(
    config: BuildConfig,
    *,
    host_platform: str | None = None,
    mkarchiso_path: str | None = None,
    unshare_path: str | None = None,
    host_namespace_identity: str | None = None,
    mountpoints_reader: Callable[[], Sequence[str]] = _mountpoints_from_proc,
    runner: Runner = subprocess.run,
    clock: Callable[[], float] = time.time,
) -> BuildResult:
    """Execute a reviewed build contract. Production calls use real subprocesses."""
    ready = preflight(config, host_platform=host_platform)
    source = config.source_root.resolve(strict=True)
    profile = config.profile_dir.resolve(strict=True)
    output = config.output_dir.resolve(strict=True)
    work_parent = config.work_parent.resolve(strict=True)
    if config.network_mode == "offline":
        network_isolation = _establish_offline_network_isolation(
            runner,
            unshare_path=unshare_path,
            host_namespace_identity=host_namespace_identity,
        )
    else:
        network_isolation = NetworkIsolation(
            mode="online-allowed-by-request",
            command_prefix=(),
            privilege_mode="not-applicable",
            unshare_path=None,
            unshare_sha256=None,
            probe_command=(),
            probe_output="NOT_REQUIRED",
        )
    command_log: list[list[str]] = [list(network_isolation.probe_command)] if network_isolation.probe_command else []
    build_runner = _network_guarded_runner(runner, network_isolation, command_log=command_log)
    source_info = _source_provenance(source, build_runner)
    version = _run_command(("pacman", "-Q", "archiso"), runner=build_runner).stdout.strip()
    if version != f"archiso {EXPECTED_MKARCHISO_VERSION}":
        raise BuildContractError(
            f"installed Archiso version must be exactly {EXPECTED_MKARCHISO_VERSION}; got {version!r}"
        )
    mkarchiso_candidate = mkarchiso_path or shutil.which("mkarchiso")
    if not mkarchiso_candidate:
        raise BuildContractError("mkarchiso is not installed; install the pinned archiso package in a supported Linux environment")
    mkarchiso_file = _canonical_existing_file(Path(mkarchiso_candidate), "mkarchiso executable")
    owner = _run_command(("pacman", "-Qo", str(mkarchiso_file)), runner=build_runner).stdout.strip()
    if re.search(r"\bis owned by archiso\s+91-1\s*$", owner) is None:
        raise BuildContractError(f"mkarchiso executable is not owned by the pinned archiso 91-1 package: {owner!r}")
    host_inventory = _parse_package_inventory(
        _run_command(("pacman", "-Q"), runner=build_runner).stdout,
        label="host package inventory",
        required=("archiso",),
    )
    host_archiso = next(row for row in host_inventory if row.startswith("archiso "))
    if host_archiso != f"archiso {EXPECTED_MKARCHISO_VERSION}":
        raise BuildContractError(f"host package inventory does not contain pinned Archiso {EXPECTED_MKARCHISO_VERSION}")
    wheel_backend = _build_backend_evidence(build_runner)
    mkarchiso_bin = str(mkarchiso_file)

    owner_token = uuid.uuid4().hex
    work_dir = work_parent / (WORK_PREFIX + owner_token)
    work_dir.mkdir(mode=0o700)
    owner_fd = os.open(work_dir / ".owner", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(owner_fd, "w", encoding="ascii") as owner_file:
        owner_file.write(owner_token)
    build_log = work_dir / "build.log"
    keep_work = True
    start_epoch = int(clock())
    try:
        stage_profile = _profile_copy(profile, work_dir)
        wheel_source_dir = work_dir / "wheel-source"
        wheel_source_hashes = _copy_wheel_source(source, wheel_source_dir)
        wheel_dir = work_dir / "wheelhouse"
        wheel_dir.mkdir()
        build_env = dict(os.environ)
        build_env.update(
            {
                "PIP_NO_INDEX": "1",
                "PIP_CONFIG_FILE": os.devnull,
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PYTHONNOUSERSITE": "1",
            }
        )
        wheel_command = (
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-index",
            "--no-deps",
            "--no-build-isolation",
            "--no-cache-dir",
            "--wheel-dir",
            str(wheel_dir),
            str(wheel_source_dir),
        )
        wheel_log = work_dir / "wheel-build.log"
        with wheel_log.open("w", encoding="utf-8", newline="\n") as log:
            _run_command(wheel_command, runner=build_runner, env=build_env, stdout=log)
        if _source_provenance(source, build_runner) != source_info:
            raise BuildContractError("source checkout changed during isolated wheel build; retaining build logs")
        wheel = _one_wheel(wheel_dir)
        _copy_wheel_into_profile(wheel, stage_profile)
        _copy_runbooks(source, stage_profile)
        _enable_autoloader_service(stage_profile)
        _write_image_input_provenance(
            source,
            stage_profile,
            snapshot_sha256=ready.snapshot_sha256,
            source_info=source_info,
            wheel=wheel,
            package_names=ready.package_names,
        )

        pacman_conf = stage_profile / "pacman.conf"
        if ready.offline_repository is not None:
            pacman_conf = _install_offline_conf(stage_profile, ready.offline_repository, work_dir)
        archiso_work = work_dir / "archiso-work"
        archiso_work.mkdir()
        staged_image_output = work_dir / "image-output"
        staged_image_output.mkdir()
        command = (
            mkarchiso_bin,
            "-v",
            "-C",
            str(pacman_conf),
            "-w",
            str(archiso_work),
            "-o",
            str(staged_image_output),
            str(stage_profile),
        )
        with build_log.open("w", encoding="utf-8", newline="\n") as log:
            try:
                _run_command(command, runner=build_runner, stdout=log)
            except BuildContractError as exc:
                log.write(f"\nBUILD_FAILED: {exc}\n")
                raise

        images = [p for p in staged_image_output.iterdir() if p.suffix.casefold() == ".iso"]
        if len(images) != 1 or _is_reparse_or_symlink(images[0]) or not images[0].is_file() or images[0].stat().st_size == 0:
            raise BuildContractError("mkarchiso did not produce exactly one non-empty regular ISO file")
        image = images[0]
        target_root = archiso_work / "x86_64" / "airootfs"
        target_install = _validate_target_root_install(
            target_root,
            wheel=wheel,
            runner=build_runner,
        )
        package_inventory = _record_package_versions(target_root, build_runner, ready.package_names)
        end_epoch = int(clock())
        profile_hashes = _source_hashes(source, profile)
        staged_hashes = _source_hashes(work_dir, stage_profile)
        image_outputs: list[dict[str, Any]] = []
        for path in sorted(staged_image_output.iterdir()):
            if _is_reparse_or_symlink(path) or not path.is_file():
                raise BuildContractError(f"mkarchiso produced an unexpected non-regular output: {path}")
            image_outputs.append(
                {"filename": path.name, "sha256": _sha256_file(path), "size_bytes": path.stat().st_size}
            )
        provenance = {
            "schema_version": 1,
            "status": "CANDIDATE_ISO_BUILD_COMPLETED; NOT_RELEASE_VALIDATED",
            "release_validation_status": "PENDING_LINUX_QEMU_REVIEW",
            "built_at_utc": datetime.fromtimestamp(end_epoch, timezone.utc).isoformat(),
            "duration_seconds": max(0, end_epoch - start_epoch),
            "source": source_info,
            "snapshot": {
                "lock_sha256": ready.snapshot_sha256,
                "snapshot_date": ready.snapshot.get("snapshot_date"),
                "repository_base": ready.snapshot.get("repository_base"),
                "archiso_package": ready.snapshot.get("archiso_package"),
                "archiso_version": version.split()[-1],
                "snapshot_pin_status": ready.snapshot.get("snapshot_pin_status"),
                "release_validation_status": ready.snapshot.get("release_validation_status"),
                "package_signature_policy": ready.snapshot.get("package_signature_policy"),
                "packages_x86_64_sha256": ready.snapshot.get("packages_x86_64_sha256"),
            },
            "host": {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "package_inventory": list(host_inventory),
                "package_inventory_sha256": _sha256_bytes("\n".join(host_inventory).encode("utf-8")),
                "wheel_build_backend": wheel_backend,
                "mkarchiso": {
                    "path": str(mkarchiso_file),
                    "package": "archiso",
                    "package_version": EXPECTED_MKARCHISO_VERSION,
                    "package_owner_output": owner,
                    "sha256": _sha256_file(mkarchiso_file),
                },
            },
            "network_isolation": {
                "mode": network_isolation.mode,
                "command_prefix": list(network_isolation.command_prefix),
                "privilege_mode": network_isolation.privilege_mode,
                "unshare_path": network_isolation.unshare_path,
                "unshare_sha256": network_isolation.unshare_sha256,
                "probe_command": list(network_isolation.probe_command),
                "probe_output": network_isolation.probe_output,
            },
            "profile_file_sha256": profile_hashes,
            "staged_profile_file_sha256": staged_hashes,
            "wheel_source_file_sha256": wheel_source_hashes,
            "packages_x86_64": list(ready.package_names),
            "installed_package_inventory": list(package_inventory),
            "target_root_install": target_install,
            "commands": command_log,
            "wheel": {"filename": wheel.name, "sha256": _sha256_file(wheel), "size_bytes": wheel.stat().st_size},
            "outputs": image_outputs,
            "logs": {
                "wheel_build_sha256": _sha256_file(wheel_log),
                "mkarchiso_sha256": _sha256_file(build_log),
            },
            "qemu_bios_boot": "NOT_PERFORMED",
            "qemu_uefi_boot": "NOT_PERFORMED",
            "usb_media_write": "NOT_PERFORMED",
        }
        final_image = output / image.name
        shutil.copy2(image, final_image)
        shutil.copy2(build_log, output / "build.log")
        shutil.copy2(wheel_log, output / "wheel-build.log")
        manifest_path = output / "build-provenance.json"
        manifest_path.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        sums = [
            f"{_sha256_file(path)}  {path.name}"
            for path in sorted(output.iterdir())
            if path.is_file() and path.name != "SHA256SUMS"
        ]
        (output / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="ascii")
        keep_work = False
        return BuildResult(final_image, manifest_path, _sha256_file(final_image), _sha256_file(manifest_path))
    except Exception:
        # Keep logs and intermediate evidence after any failure for inspection.
        if not build_log.exists():
            build_log.write_text("Build stopped before mkarchiso; inspect the reported preflight error.\n", encoding="utf-8")
        raise
    finally:
        if not keep_work:
            try:
                cleanup_owned_workdir(work_dir, work_parent, owner_token, mountpoints=mountpoints_reader())
            except (OSError, BuildContractError) as exc:
                print(f"Work directory retained because safe cleanup could not be proven: {work_dir}: {exc}", file=sys.stderr)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the Libre_Core AutoLoader Live USB through a fail-closed Archiso contract")
    parser.add_argument("--source-root", type=Path, required=True, help="Absolute clean Git checkout containing this project")
    parser.add_argument("--profile-dir", type=Path, required=True, help="Absolute reviewed archiso_profile directory")
    parser.add_argument("--snapshot-lock", type=Path, required=True, help="Absolute checked-in snapshot.lock.json path")
    parser.add_argument("--output-dir", type=Path, required=True, help="Absolute existing empty output directory")
    parser.add_argument("--work-parent", type=Path, required=True, help="Absolute existing directory for owned temporary build trees")
    parser.add_argument("--network-mode", choices=sorted(REQUIRED_MODES), required=True, help="Use locked online snapshot or a verified local repository")
    parser.add_argument("--offline-repository", type=Path, help="Absolute local Arch repository root for offline mode")
    parser.add_argument("--offline-manifest", type=Path, help="Absolute exact file/hash manifest for offline repository")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    config = BuildConfig(
        source_root=args.source_root,
        profile_dir=args.profile_dir,
        output_dir=args.output_dir,
        work_parent=args.work_parent,
        snapshot_lock=args.snapshot_lock,
        network_mode=args.network_mode,
        offline_repository=args.offline_repository,
        offline_manifest=args.offline_manifest,
    )
    try:
        result = execute_build(config)
    except BuildContractError as exc:
        print(f"BUILD_BLOCKED: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"BUILD_FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"CANDIDATE_ISO_BUILD_COMPLETED (BIOS/UEFI boot NOT_PERFORMED; not release validated): {result.iso_path}")
    print(f"SHA-256: {result.sha256}")
    print(f"Provenance: {result.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
