"""Fake-only tests for the Live USB host/profile/build contract."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from usb_builder import build_usb


_FIXTURE_DB_BYTES = {"core": b"fixture-core-db\n", "extra": b"fixture-extra-db\n"}


@pytest.fixture(autouse=True)
def _short_owned_work_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep synthetic target-root paths below Windows' legacy MAX_PATH limit.
    monkeypatch.setattr(build_usb, "WORK_PREFIX", "w-")
    monkeypatch.setattr(build_usb.uuid, "uuid4", lambda: SimpleNamespace(hex="fixture"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _write_executable(path: Path, content: str) -> None:
    _write(path, content)
    os.chmod(path, path.stat().st_mode | 0o111)


def _make_source(root: Path, *, pinned: bool = True) -> tuple[Path, Path]:
    (root / ".git").mkdir(parents=True, exist_ok=True)
    profile = root / "usb_builder" / "archiso_profile"
    (profile / "syslinux").mkdir(parents=True)
    _write(
        profile / "profiledef.sh",
        'arch="x86_64"\nbuildmodes=(\'iso\')\nbootmodes=(\'bios.syslinux\' \'uefi.systemd-boot\')\n',
    )
    packages = sorted(build_usb.REQUIRED_PACKAGES)
    package_text = "\n".join(packages) + "\n"
    _write(profile / "packages.x86_64", package_text)
    _write(
        profile / "pacman.conf",
        "[options]\nSigLevel = Required DatabaseOptional\nLocalFileSigLevel = Required\n\n"
        "[core]\nServer = https://archive.archlinux.org/repos/2026/09/29/$repo/os/$arch\n\n"
        "[extra]\nServer = https://archive.archlinux.org/repos/2026/09/29/$repo/os/$arch\n",
    )
    _write(profile / "syslinux" / "syslinux.cfg", "INCLUDE archiso_sys.cfg\n")
    _write(profile / "syslinux" / "archiso_sys.cfg", "DEFAULT arch\nINCLUDE archiso_sys-linux.cfg\n")
    _write(
        profile / "syslinux" / "archiso_sys-linux.cfg",
        "LINUX /%INSTALL_DIR%/boot/%ARCH%/vmlinuz-linux\n"
        "INITRD /%INSTALL_DIR%/boot/%ARCH%/initramfs-linux.img\n"
        "APPEND archisobasedir=%INSTALL_DIR% %KERNEL_PARAMS%\n",
    )
    _write(profile / "efiboot" / "loader" / "loader.conf", "default 01-autoloader.conf\n")
    _write(
        profile / "efiboot" / "loader" / "entries" / "01-autoloader.conf",
        "linux /%INSTALL_DIR%/boot/%ARCH%/vmlinuz-linux\n"
        "initrd /%INSTALL_DIR%/boot/%ARCH%/initramfs-linux.img\n"
        "options archisobasedir=%INSTALL_DIR% %KERNEL_PARAMS%\n",
    )
    _write_executable(profile / "airootfs" / "opt" / "autoloader" / "start.sh", "#!/bin/bash\nexit 0\n")
    _write(
        profile / "airootfs" / "etc" / "systemd" / "system" / "autoloader.service",
        "[Unit]\nDescription=AutoLoader\nAfter=getty@tty1.service\nConflicts=getty@tty1.service\n\n"
        "[Service]\nExecStart=/opt/autoloader/start.sh\nRestart=no\nStandardOutput=journal+console\n",
    )
    _write_executable(profile / "airootfs" / "root" / "customize_airootfs.sh", "#!/bin/bash\nexit 0\n")
    lock = root / "usb_builder" / "snapshot.lock.json"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "distribution": "Arch Linux",
                "architecture": "x86_64",
                "snapshot_date": "2026-09-29",
                "repository_base": "https://archive.archlinux.org/repos/2026/09/29",
                "archiso_package": "archiso",
                "archiso_version": "91-1",
                "archiso_source_commit": build_usb.ARCHISO_V91_SOURCE_COMMIT,
                "packages_x86_64_sha256": hashlib.sha256(package_text.encode("utf-8")).hexdigest(),
                "repository_database_sha256": {
                    name: hashlib.sha256(value).hexdigest() for name, value in _FIXTURE_DB_BYTES.items()
                },
                "package_signature_policy": "Required DatabaseOptional",
                "wheel_build_backend": {
                    "backend": "setuptools.build_meta",
                    "minimum_setuptools_version": "61.0",
                    "required_tools": ["setuptools", "wheel"],
                },
                "required_boot_modes": ["bios.syslinux", "uefi.systemd-boot"],
                "snapshot_pin_status": "fixed-candidate" if pinned else "unverified",
                "release_validation_status": "pending-linux-build-and-qemu",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _write(root / "README.md", "Runbook test.\n")
    _write(root / "LICENSE", "Test license notice.\n")
    _write(root / "docs" / "v1" / "RUNBOOK.md", "Runbook test.\n")
    _write(root / "pyproject.toml", "[project]\nname='libre-core-autoloader'\n")
    _write(root / "autoloader" / "__init__.py", "\"\"\"Synthetic package fixture.\"\"\"\n")
    _write(root / "usb_builder" / "__init__.py", "\"\"\"Synthetic package fixture.\"\"\"\n")
    return profile, lock


def _config(tmp_path: Path, *, pinned: bool = True) -> build_usb.BuildConfig:
    source = tmp_path / "source"
    profile, lock = _make_source(source, pinned=pinned)
    output = tmp_path / "output"
    work = tmp_path / "work-parent"
    output.mkdir()
    work.mkdir()
    return build_usb.BuildConfig(source, profile, output, work, lock, "online")


def _offline_config(tmp_path: Path) -> build_usb.BuildConfig:
    online = _config(tmp_path)
    _, lock_bytes = build_usb._load_json(online.snapshot_lock, "snapshot lock")
    repository = tmp_path / "offline-repo"
    files: dict[str, str] = {}
    paths = [
        "core/os/x86_64/core.db",
        "extra/os/x86_64/extra.db",
    ]
    for package in sorted(build_usb.REQUIRED_PACKAGES):
        archive = f"extra/os/x86_64/{package}-1-1-x86_64.pkg.tar.zst"
        paths.extend((archive, archive + ".sig"))
    for index, name in enumerate(paths):
        item = repository / name
        repository_name = Path(name).parts[0]
        item.parent.mkdir(parents=True, exist_ok=True)
        item.write_bytes(_FIXTURE_DB_BYTES[repository_name] if name.endswith(".db") else f"synthetic local fixture {index}\n".encode())
        files[name] = _sha(item)
    manifest = tmp_path / "offline-repo.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "snapshot_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
                "packages": {
                    package: next(path for path in paths if path.endswith(f"/{package}-1-1-x86_64.pkg.tar.zst"))
                    for package in sorted(build_usb.REQUIRED_PACKAGES)
                },
                "files": files,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return build_usb.BuildConfig(
        online.source_root,
        online.profile_dir,
        online.output_dir,
        online.work_parent,
        online.snapshot_lock,
        "offline",
        repository,
        manifest,
    )


def _write_offline_manifest(config: build_usb.BuildConfig, document: dict[str, Any]) -> None:
    assert config.offline_manifest is not None
    _, lock_bytes = build_usb._load_json(config.snapshot_lock, "snapshot lock")
    document["snapshot_lock_sha256"] = hashlib.sha256(lock_bytes).hexdigest()
    config.offline_manifest.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")


def test_checked_in_lock_profile_and_official_archive_url_are_consistent():
    compile(build_usb._SNAPSHOT_FETCH_SCRIPT, "snapshot-fetch-fixture", "exec")
    lock, raw = build_usb._load_json(build_usb.DEFAULT_SNAPSHOT, "snapshot lock")
    profile = build_usb.DEFAULT_PROFILE
    build_usb._validate_online_pacman_config(profile, lock)
    assert lock["snapshot_date"] == "2026-09-29"
    assert lock["repository_base"] == "https://archive.archlinux.org/repos/2026/09/29"
    assert build_usb._sha256_bytes(raw) == hashlib.sha256(raw).hexdigest()
    assert lock["snapshot_pin_status"] == "fixed-candidate"
    assert lock["release_validation_status"] == "pending-linux-build-and-qemu"
    assert lock["package_signature_policy"] == "Required DatabaseOptional"
    assert lock["repository_database_sha256"] == {
        "core": "bb477fc11a9f3c19f3da2eced7869ab98c84c7d33dece62807458fdca43d018a",
        "extra": "399231e477e0aaf0afe0a79dd1cd82695236ba8259e454219b4254898112aed1",
    }
    assert set(lock["required_boot_modes"]) == build_usb.REQUIRED_BOOT_MODES
    assert set(build_usb._profile_boot_modes(profile)) >= build_usb.REQUIRED_BOOT_MODES


@pytest.mark.parametrize(
    "injection",
    [
        "Server = https://mirror.example.invalid/$repo/os/$arch\n",
        "CacheServer = https://mirror.example.invalid/$repo/os/$arch\n",
        "\n[thirdparty]\nServer = https://mirror.example.invalid/$repo/os/$arch\n",
        "XferCommand = /tmp/unreviewed-transfer-tool %u %o\n",
    ],
)
def test_snapshot_database_refresh_cannot_fall_back_to_another_server(tmp_path, injection):
    config = _config(tmp_path)
    original = config.profile_dir.joinpath("pacman.conf").read_text(encoding="utf-8")
    if injection.startswith("XferCommand"):
        mutated = original.replace("[core]\n", injection + "\n[core]\n", 1)
    else:
        mutated = original.replace("[core]\n", "[core]\n" + injection, 1)
    config.profile_dir.joinpath("pacman.conf").write_text(mutated, encoding="utf-8")
    snapshot, _ = build_usb._load_json(config.snapshot_lock, "snapshot lock")
    with pytest.raises(build_usb.BuildContractError, match="pacman config|pinned core"):
        build_usb._validate_online_pacman_config(config.profile_dir, snapshot)


@pytest.mark.parametrize("bad_pins", [None, {"core": "0" * 64}, {"core": "0" * 64, "extra": "z" * 64}])
def test_preflight_requires_exact_valid_database_hash_pins(tmp_path, bad_pins):
    config = _config(tmp_path)
    snapshot, _ = build_usb._load_json(config.snapshot_lock, "snapshot lock")
    snapshot["repository_database_sha256"] = bad_pins
    config.snapshot_lock.write_text(json.dumps(snapshot), encoding="utf-8")
    with pytest.raises(build_usb.BuildContractError, match="snapshot lock"):
        build_usb.preflight(config, host_platform="linux")


def test_preflight_rejects_unpinned_snapshot_before_commands(tmp_path):
    config = _config(tmp_path, pinned=False)
    with pytest.raises(build_usb.BuildContractError, match="SNAPSHOT_NOT_PINNED"):
        build_usb.preflight(config, host_platform="linux")


def test_preflight_rejects_package_list_drift_from_snapshot_hash(tmp_path):
    config = _config(tmp_path)
    with (config.profile_dir / "packages.x86_64").open("a", encoding="utf-8") as stream:
        stream.write("unexpected-package\n")
    with pytest.raises(build_usb.BuildContractError, match="package list does not match"):
        build_usb.preflight(config, host_platform="linux")


def test_package_list_pin_is_stable_across_windows_checkout_line_endings(tmp_path):
    config = _config(tmp_path)
    snapshot, _ = build_usb._load_json(config.snapshot_lock, "snapshot lock")
    package_path = config.profile_dir / "packages.x86_64"
    canonical_bytes = package_path.read_bytes()
    assert hashlib.sha256(canonical_bytes).hexdigest() == snapshot["packages_x86_64_sha256"]
    crlf_bytes = canonical_bytes.replace(b"\n", b"\r\n")
    package_path.write_bytes(crlf_bytes)
    assert hashlib.sha256(crlf_bytes).hexdigest() != snapshot["packages_x86_64_sha256"]
    build_usb.preflight(config, host_platform="linux")


def test_checked_in_package_list_and_lock_bind_canonical_lf_and_accept_crlf(tmp_path):
    repository_root = Path(__file__).resolve().parents[1]
    package_path = repository_root / "usb_builder" / "archiso_profile" / "packages.x86_64"
    lock_path = repository_root / "usb_builder" / "snapshot.lock.json"
    snapshot, _ = build_usb._load_json(lock_path, "snapshot lock")
    checked_in_bytes = package_path.read_bytes()
    canonical_bytes = checked_in_bytes.replace(b"\r\n", b"\n")
    assert hashlib.sha256(canonical_bytes).hexdigest() == snapshot["packages_x86_64_sha256"]
    build_usb._validate_package_list_pin(package_path, snapshot)

    crlf_copy = tmp_path / "packages.x86_64"
    crlf_copy.write_bytes(canonical_bytes.replace(b"\n", b"\r\n"))
    build_usb._validate_package_list_pin(crlf_copy, snapshot)


def test_windows_host_blocks_before_any_build_tool_or_output_creation(tmp_path):
    config = _config(tmp_path, pinned=True)
    with pytest.raises(build_usb.BuildContractError, match="LINUX_BUILD_ENV_REQUIRED"):
        build_usb.execute_build(config, host_platform="win32", runner=lambda *a, **k: pytest.fail("subprocess ran"))
    assert list(config.output_dir.iterdir()) == []
    assert list(config.work_parent.iterdir()) == []


def test_preflight_requires_absolute_existing_empty_and_separate_paths(tmp_path):
    config = _config(tmp_path)
    relative = build_usb.BuildConfig(
        config.source_root, config.profile_dir, Path("relative/out"), config.work_parent, config.snapshot_lock, "online"
    )
    with pytest.raises(build_usb.BuildContractError, match="absolute"):
        build_usb.preflight(relative, host_platform="linux")
    (config.output_dir / "existing.iso").write_bytes(b"existing")
    with pytest.raises(build_usb.BuildContractError, match="empty"):
        build_usb.preflight(config, host_platform="linux")


def test_preflight_rejects_root_output_and_source_nested_work(tmp_path):
    config = _config(tmp_path)
    root_output = build_usb.BuildConfig(
        config.source_root, config.profile_dir, Path(Path.cwd().anchor), config.work_parent,
        config.snapshot_lock, "online"
    )
    with pytest.raises(build_usb.BuildContractError, match="root"):
        build_usb.preflight(root_output, host_platform="linux")
    nested_work = config.source_root / "scratch"
    nested_work.mkdir()
    nested_config = build_usb.BuildConfig(
        config.source_root, config.profile_dir, config.output_dir, nested_work, config.snapshot_lock, "online"
    )
    with pytest.raises(build_usb.BuildContractError, match="inside source"):
        build_usb.preflight(nested_config, host_platform="linux")


def test_preflight_rejects_symlinked_output_and_profile_roms(tmp_path):
    config = _config(tmp_path)
    target = tmp_path / "real-output"
    target.mkdir()
    link = tmp_path / "output-link"
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this host")
    linked = build_usb.BuildConfig(
        config.source_root, config.profile_dir, link, config.work_parent, config.snapshot_lock, "online"
    )
    with pytest.raises(build_usb.BuildContractError, match="symlink|junction"):
        build_usb.preflight(linked, host_platform="linux")
    (config.profile_dir / "airootfs" / "opt" / "autoloader" / "stock.rom").write_bytes(b"fixture")
    with pytest.raises(build_usb.BuildContractError, match="never be bundled"):
        build_usb.preflight(config, host_platform="linux")


def test_offline_mode_requires_snapshot_bound_complete_local_hash_manifest(tmp_path):
    config = _config(tmp_path)
    config = build_usb.BuildConfig(
        config.source_root,
        config.profile_dir,
        config.output_dir,
        config.work_parent,
        config.snapshot_lock,
        "offline",
    )
    with pytest.raises(build_usb.BuildContractError, match="explicit local repository"):
        build_usb.preflight(config, host_platform="linux")

    repository = tmp_path / "offline-repo"
    files: dict[str, str] = {}
    paths = [
        "core/os/x86_64/core.db",
        "extra/os/x86_64/extra.db",
    ]
    for package in sorted(build_usb.REQUIRED_PACKAGES):
        archive = f"extra/os/x86_64/{package}-1-1-x86_64.pkg.tar.zst"
        paths.extend((archive, archive + ".sig"))
    for index, name in enumerate(paths):
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith(".db"):
            path.write_bytes(_FIXTURE_DB_BYTES[Path(name).parts[0]])
        else:
            path.write_bytes(f"synthetic local fixture {index}\n".encode())
        files[name] = _sha(path)
    _, lock_bytes = build_usb._load_json(config.snapshot_lock, "snapshot lock")
    manifest = tmp_path / "offline-repo.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "snapshot_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
                "packages": {
                    package: next(path for path in paths if path.endswith(f"/{package}-1-1-x86_64.pkg.tar.zst"))
                    for package in sorted(build_usb.REQUIRED_PACKAGES)
                },
                "files": files,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    offline = build_usb.BuildConfig(
        config.source_root,
        config.profile_dir,
        config.output_dir,
        config.work_parent,
        config.snapshot_lock,
        "offline",
        repository,
        manifest,
    )
    ready = build_usb.preflight(offline, host_platform="linux")
    assert ready.offline_repository == repository.resolve()
    pacman = build_usb._install_snapshot_conf(
        config.profile_dir,
        tmp_path / "snapshot-databases",
        tmp_path,
        offline_repository=ready.offline_repository,
    )
    content = pacman.read_text(encoding="utf-8")
    assert "Server = file://" in content
    assert "CacheServer = file://" in content
    assert "https://" not in content
    server_lines = [line.strip() for line in content.splitlines() if line.strip().startswith("Server =")]
    cache_lines = [line.strip() for line in content.splitlines() if line.strip().startswith("CacheServer =")]
    assert len(server_lines) == len(cache_lines) == 2
    assert all((tmp_path / "snapshot-databases").as_uri() in line for line in server_lines)
    assert all(ready.offline_repository.as_uri() in line for line in cache_lines)

    (repository / paths[0]).write_text("tampered\n", encoding="utf-8")
    with pytest.raises(build_usb.BuildContractError, match="changed"):
        build_usb.preflight(offline, host_platform="linux")


def test_offline_manifest_rejects_missing_package_signature(tmp_path):
    config = _offline_config(tmp_path)
    assert config.offline_manifest is not None
    manifest, _ = build_usb._load_json(config.offline_manifest, "offline repository manifest")
    package = sorted(build_usb.REQUIRED_PACKAGES)[0]
    archive = manifest["packages"][package]
    real_signature = config.offline_repository / (archive + ".sig")
    assert real_signature.is_file()
    real_signature.unlink()
    manifest["files"].pop(archive + ".sig")
    _write_offline_manifest(config, manifest)
    with pytest.raises(build_usb.BuildContractError, match="no enumerated signature file"):
        build_usb.preflight(config, host_platform="linux")
    assert list(config.output_dir.iterdir()) == []


def test_offline_manifest_rejects_an_unlisted_file(tmp_path):
    config = _offline_config(tmp_path)
    assert config.offline_repository is not None
    _write(config.offline_repository / "extra/os/x86_64/unlisted.txt", "unlisted fixture")
    with pytest.raises(build_usb.BuildContractError, match="unlisted or has changed"):
        build_usb.preflight(config, host_platform="linux")


def test_offline_database_hash_must_match_snapshot_lock(tmp_path):
    config = _offline_config(tmp_path)
    snapshot, _ = build_usb._load_json(config.snapshot_lock, "snapshot lock")
    snapshot["repository_database_sha256"]["core"] = "0" * 64
    config.snapshot_lock.write_text(json.dumps(snapshot, sort_keys=True), encoding="utf-8")
    assert config.offline_manifest is not None
    manifest, _ = build_usb._load_json(config.offline_manifest, "offline repository manifest")
    _write_offline_manifest(config, manifest)
    with pytest.raises(build_usb.BuildContractError, match="core.db does not match"):
        build_usb.preflight(config, host_platform="linux")
    assert list(config.output_dir.iterdir()) == []
    assert list(config.work_parent.iterdir()) == []


def test_offline_requires_signatures_for_transitive_archives_and_rejects_traversal(tmp_path):
    config = _offline_config(tmp_path)
    assert config.offline_repository is not None and config.offline_manifest is not None
    manifest, _ = build_usb._load_json(config.offline_manifest, "offline repository manifest")
    transitive = "extra/os/x86_64/transitive-fixture-1-any.pkg.tar.zst"
    transitive_path = config.offline_repository / transitive
    _write(transitive_path, "synthetic transitive package archive")
    manifest["files"][transitive] = _sha(transitive_path)
    _write_offline_manifest(config, manifest)
    with pytest.raises(build_usb.BuildContractError, match="dependency archive has no enumerated signature"):
        build_usb.preflight(config, host_platform="linux")

    del manifest["files"][transitive]
    manifest["files"]["../escape.pkg.tar.zst"] = "0" * 64
    _write_offline_manifest(config, manifest)
    with pytest.raises(build_usb.BuildContractError, match="relative and traversal-free"):
        build_usb.preflight(config, host_platform="linux")


def test_invalid_transitive_package_signature_is_rejected(tmp_path):
    config = _offline_config(tmp_path)
    assert config.offline_repository is not None and config.offline_manifest is not None
    manifest, _ = build_usb._load_json(config.offline_manifest, "offline repository manifest")
    archive_rel = "extra/os/x86_64/transitive-fixture-1-any.pkg.tar.zst"
    archive = config.offline_repository / archive_rel
    signature = archive.with_name(archive.name + ".sig")
    _write(archive, "synthetic transitive archive")
    _write(signature, "synthetic transitive signature")
    manifest["files"][archive_rel] = _sha(archive)
    manifest["files"][archive_rel + ".sig"] = _sha(signature)
    _write_offline_manifest(config, manifest)
    build_usb.preflight(config, host_platform="linux")

    def reject_transitive(command, **kwargs):
        if command[:2] == ["pacman-key", "--verify"] and "transitive-fixture" in command[-1]:
            return subprocess.CompletedProcess(command, 1, "BAD TRANSITIVE SIGNATURE\n", "")
        return subprocess.CompletedProcess(command, 0, "Good fixture signature\n", "")

    with pytest.raises(build_usb.BuildContractError, match="pacman-key --verify"):
        build_usb._verify_offline_package_signatures(config.offline_repository, reject_transitive)
    assert list(config.output_dir.iterdir()) == []


def test_optional_offline_database_signature_must_verify_when_present(tmp_path):
    config = _offline_config(tmp_path)
    assert config.offline_repository is not None and config.offline_manifest is not None
    signature_rel = "core/os/x86_64/core.db.sig"
    signature = config.offline_repository / signature_rel
    _write(signature, "synthetic optional db signature")
    manifest, _ = build_usb._load_json(config.offline_manifest, "offline repository manifest")
    manifest["files"][signature_rel] = _sha(signature)
    _write_offline_manifest(config, manifest)
    build_usb.preflight(config, host_platform="linux")
    snapshot, _ = build_usb._load_json(config.snapshot_lock, "snapshot lock")
    work = tmp_path / "signature-work"
    work.mkdir()

    def invalid_signature(command, **kwargs):
        if command[:2] == ["pacman-key", "--verify"]:
            return subprocess.CompletedProcess(command, 1, "BAD SIGNATURE fixture\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    with pytest.raises(build_usb.BuildContractError, match="pacman-key --verify"):
        build_usb._stage_repository_databases(
            snapshot,
            work,
            runner=invalid_signature,
            offline_repository=config.offline_repository,
        )


def test_staged_database_hash_is_rechecked_after_pacman_use(tmp_path):
    config = _config(tmp_path)
    snapshot, _ = build_usb._load_json(config.snapshot_lock, "snapshot lock")
    work = tmp_path / "db-work"
    work.mkdir()
    root, evidence = build_usb._stage_repository_databases(
        snapshot,
        work,
        runner=_FakeBuilder(),
        offline_repository=None,
    )
    build_usb._verify_staged_repository_databases(root, evidence, runner=_FakeBuilder())
    (root / build_usb.SNAPSHOT_DATABASE_RELATIVE_PATHS["core"]).write_bytes(b"tampered before/after pacman\n")
    with pytest.raises(build_usb.BuildContractError, match="staged core.db changed"):
        build_usb._verify_staged_repository_databases(root, evidence, runner=_FakeBuilder())


def test_offline_repository_is_copied_and_reverified_before_use(tmp_path):
    config = _offline_config(tmp_path)
    assert config.offline_repository is not None and config.offline_manifest is not None
    ready = build_usb.preflight(config, host_platform="linux")
    assert ready.offline_manifest_sha256 == _sha(config.offline_manifest)
    staged, hashes = build_usb._copy_verified_offline_repository(
        ready.offline_repository,
        config.offline_manifest,
        ready.snapshot_sha256,
        ready.package_names,
        build_usb._snapshot_database_pins(ready.snapshot),
        tmp_path / "staged-offline-repository",
    )
    assert staged != ready.offline_repository
    assert hashes == {
        path.relative_to(staged).as_posix(): _sha(path)
        for path in staged.rglob("*")
        if path.is_file()
    }
    archive = next(iter(hashes))
    source_file = ready.offline_repository / archive
    before = _sha(staged / archive)
    _write(source_file, "changed source after verified staging")
    assert _sha(staged / archive) == before
    with pytest.raises(build_usb.BuildContractError, match="unlisted or has changed"):
        build_usb._validate_offline_repository(
            ready.offline_repository,
            config.offline_manifest,
            ready.snapshot_sha256,
            ready.package_names,
            build_usb._snapshot_database_pins(ready.snapshot),
        )


@pytest.mark.parametrize(
    ("redirect_to", "expected_error"),
    [
        (
            "http://archive.archlinux.org/repos/2026/09/29/core/os/x86_64/core.db",
            "exact official archive host",
        ),
        (
            "https://archive.archlinux.org.attacker.invalid/repos/2026/09/29/core/os/x86_64/core.db",
            "exact official archive host",
        ),
        (
            "https://archive.archlinux.org/repos/2026/09/28/core/os/x86_64/core.db",
            "exact requested archive resource path",
        ),
        (
            "https://archive.archlinux.org/repos/2026/09/29/extra/os/x86_64/extra.db",
            "exact requested archive resource path",
        ),
    ],
)
def test_candidate_rejects_snapshot_redirect_outside_exact_locked_resource(tmp_path, redirect_to, expected_error):
    config = _config(tmp_path)
    runner = _FakeBuilder(redirect_to=redirect_to)
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_mkarchiso = fake_tools / "mkarchiso"
    fake_mkarchiso.write_bytes(b"synthetic mkarchiso executable fixture")
    with pytest.raises(build_usb.BuildContractError, match=expected_error):
        build_usb.execute_build(
            config,
            host_platform="linux",
            mkarchiso_path=str(fake_mkarchiso),
            runner=runner,
            mountpoints_reader=lambda: (),
            clock=lambda: 1_800_000_000,
        )
    assert list(config.output_dir.iterdir()) == []
    assert sum(Path(call[0]).name == "mkarchiso" for call in runner.calls) == 0


@pytest.mark.parametrize(
    "redirect_to",
    [
        "https://archive.archlinux.org/repos/2026/09/28/core/os/x86_64/core.db",
        "https://archive.archlinux.org/repos/2026/09/29/extra/os/x86_64/extra.db",
        "http://archive.archlinux.org/repos/2026/09/29/core/os/x86_64/core.db",
    ],
)
def test_snapshot_fetcher_rejects_same_host_or_downgrade_redirect_before_following(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, redirect_to: str
) -> None:
    requested = "https://archive.archlinux.org/repos/2026/09/29/core/os/x86_64/core.db"
    destination = tmp_path / "core.db"
    followed: list[str] = []

    class FakeOpener:
        def __init__(self, handlers: tuple[object, ...]) -> None:
            handler_type = next(
                handler
                for handler in handlers
                if isinstance(handler, type) and issubclass(handler, urllib.request.HTTPRedirectHandler)
            )
            self.redirect_handler = handler_type()

        def open(self, url: str, *, timeout: int) -> object:
            request = urllib.request.Request(url)
            request = self.redirect_handler.redirect_request(
                request, None, 302, "Found", {}, redirect_to
            )
            followed.append(request.full_url)
            raise AssertionError("invalid redirect target must never be opened")

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: FakeOpener(handlers))
    monkeypatch.setattr(sys, "argv", ["snapshot-fetch", requested, str(destination), "required"])
    with pytest.raises(RuntimeError, match="snapshot redirect"):
        exec(compile(build_usb._SNAPSHOT_FETCH_SCRIPT, "snapshot-fetch-test", "exec"), {})
    assert followed == []
    assert not destination.exists()


def test_cleanup_rechecks_owner_prefix_and_mounts(tmp_path):
    parent = tmp_path / "work"
    parent.mkdir()
    token = "abc123"
    owned = parent / (build_usb.WORK_PREFIX + token)
    owned.mkdir()
    _write(owned / ".owner", token)
    (owned / "log.txt").write_text("test", encoding="utf-8")
    with pytest.raises(build_usb.BuildContractError, match="mount"):
        build_usb.cleanup_owned_workdir(owned, parent, token, mountpoints=(str(owned / "mounted"),))
    assert owned.is_dir()
    _write(owned / ".owner", "wrong")
    with pytest.raises(build_usb.BuildContractError, match="token"):
        build_usb.cleanup_owned_workdir(owned, parent, token, mountpoints=())
    assert owned.is_dir()
    _write(owned / ".owner", token)
    build_usb.cleanup_owned_workdir(owned, parent, token, mountpoints=())
    assert not owned.exists()


class _FakeBuilder:
    def __init__(
        self,
        *,
        install_app: bool = True,
        marker: bool = True,
        service: bool = True,
        activation: bool = True,
        startup: bool = True,
        namespace_ok: bool = True,
        mutate_database_after_build: bool = False,
        wrong_downloaded_database: bool = False,
        retain_sync_databases: bool = False,
        wrong_retained_database: bool = False,
        redirect_to: str | None = None,
        mutate_offline_source_during_build: Path | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self.embedded_provenance = ""
        self.install_app = install_app
        self.marker = marker
        self.service = service
        self.activation = activation
        self.startup = startup
        self.namespace_ok = namespace_ok
        self.mutate_database_after_build = mutate_database_after_build
        self.wrong_downloaded_database = wrong_downloaded_database
        self.retain_sync_databases = retain_sync_databases
        self.wrong_retained_database = wrong_retained_database
        self.redirect_to = redirect_to
        self.mutate_offline_source_during_build = mutate_offline_source_during_build
        self.pacman_conf_content = ""

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = list(command)
        self.calls.append(command)
        if Path(command[0]).name == "unshare" and "--net" in command:
            separator = command.index("--")
            inner = command[separator + 1 :]
            if len(inner) >= 3 and inner[1] == "-c" and build_usb.OFFLINE_NETWORK_PROBE_MARKER in inner[2]:
                output = build_usb.OFFLINE_NETWORK_PROBE_MARKER + "\n" if self.namespace_ok else "network namespace probe failed\n"
                return subprocess.CompletedProcess(command, 0 if self.namespace_ok else 1, output, "")
            command = inner
        if len(command) >= 6 and command[1] == "-c" and build_usb.SNAPSHOT_FETCH_MARKER in command[2]:
            url, destination, _optionality = command[-3:]
            final_url = self.redirect_to or url
            if url.endswith(".sig"):
                return subprocess.CompletedProcess(
                    command,
                    0,
                    f"{build_usb.SNAPSHOT_FETCH_MARKER}\nFINAL_URL={final_url}\nHTTP_STATUS=404\n",
                    "",
                )
            repository = url.rstrip("/").split("/")[-4]
            payload = b"wrong database fixture\n" if self.wrong_downloaded_database else _FIXTURE_DB_BYTES[repository]
            Path(destination).write_bytes(payload)
            return subprocess.CompletedProcess(
                command,
                0,
                f"{build_usb.SNAPSHOT_FETCH_MARKER}\nFINAL_URL={final_url}\nHTTP_STATUS=200\n",
                "",
            )
        stdout = kwargs.get("stdout")
        if isinstance(stdout, (object,)) and hasattr(stdout, "write"):
            stdout.write("fake command log\n")
        if command[0] == "git":
            if command[-1] == "HEAD":
                output = "a" * 40 + "\n"
            elif command[-1] == "HEAD^{tree}":
                output = "b" * 40 + "\n"
            else:
                output = ""
            return subprocess.CompletedProcess(command, 0, output, "")
        if command[:3] == ["pacman", "-Q", "archiso"]:
            return subprocess.CompletedProcess(command, 0, "archiso 91-1\n", "")
        if command[:2] == ["pacman", "-Qo"]:
            return subprocess.CompletedProcess(command, 0, f"{command[-1]} is owned by archiso 91-1\n", "")
        if command[:2] == ["pacman-key", "--verify"]:
            return subprocess.CompletedProcess(command, 0, "Good signature fixture\n", "")
        if command == ["pacman", "-Q"]:
            return subprocess.CompletedProcess(command, 0, "archiso 91-1\nbash 5.3-1\npacman 7.0-1\n", "")
        if len(command) >= 3 and command[1:3] == ["-m", "pip"] and command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "pip 25.0 from /tmp/host-python\n", "")
        if len(command) >= 4 and command[1:4] == ["-m", "pip", "show"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "Name: setuptools\nVersion: 80.9.0\n\nName: wheel\nVersion: 0.45.1\n",
                "",
            )
        if command[:2] == ["pacman", "--root"]:
            package_names = set(build_usb.REQUIRED_PACKAGES) | {"intel-ucode"}
            inventory = "".join(f"{name} 1-1\n" for name in sorted(package_names))
            return subprocess.CompletedProcess(command, 0, inventory, "")
        if "-m" in command and "pip" in command and "wheel" in command:
            wheel_dir = Path(command[command.index("--wheel-dir") + 1])
            wheel_dir.mkdir(parents=True, exist_ok=True)
            (wheel_dir / "libre_core_autoloader-0.1.0-py3-none-any.whl").write_bytes(b"synthetic wheel")
            return subprocess.CompletedProcess(command, 0, "built synthetic wheel\n", "")
        if command and command[0] == "chroot":
            if command[2:4] == ["/opt/autoloader/venv/bin/python", "-c"]:
                document = {
                    "distribution_name": "libre-core-autoloader",
                    "distribution_version": "0.1.0",
                    "entrypoint": "autoloader.__main__:main",
                    "runtime_imports": sorted(build_usb.REQUIRED_RUNTIME_IMPORTS),
                    "wheel_sha256": command[-1],
                    "python_version": "3.14.0",
                }
                return subprocess.CompletedProcess(command, 0, json.dumps(document, sort_keys=True) + "\n", "")
            if command[2:] == ["/opt/autoloader/venv/bin/autoloader", "--help"]:
                return subprocess.CompletedProcess(command, 0, "Usage: autoloader [OPTIONS] COMMAND [ARGS]...\n", "")
        if Path(command[0]).name == "mkarchiso":
            output = Path(command[command.index("-o") + 1])
            work = Path(command[command.index("-w") + 1])
            profile = Path(command[-1])
            self.pacman_conf_content = Path(command[command.index("-C") + 1]).read_text(encoding="utf-8")
            self.embedded_provenance = (
                profile / "airootfs/usr/share/doc/libre-core-autoloader/image-build-inputs.json"
            ).read_text(encoding="utf-8")
            if self.mutate_offline_source_during_build is not None:
                _write(self.mutate_offline_source_during_build, "mutated external offline source fixture")
            (output / "libre_core_autoloader-test-x86_64.iso").write_bytes(b"synthetic fake ISO")
            self._populate_target_root(work / "x86_64" / "airootfs", profile)
            if self.retain_sync_databases:
                sync_directory = work / "x86_64" / "airootfs" / "var/lib/pacman/sync"
                sync_directory.mkdir(parents=True, exist_ok=True)
                for repository in ("core", "extra"):
                    source_database = work.parent / f"snapshot-databases/{repository}/os/x86_64/{repository}.db"
                    shutil.copy2(source_database, sync_directory / f"{repository}.db")
                if self.wrong_retained_database:
                    _write(sync_directory / "core.db", "wrong retained sync DB fixture")
            if self.mutate_database_after_build:
                staged_database = work.parent / "snapshot-databases/core/os/x86_64/core.db"
                staged_database.write_bytes(b"mutated database during fake pacman use\n")
            return subprocess.CompletedProcess(command, 0, "fake mkarchiso completed\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    def _populate_target_root(self, root: Path, staged_profile: Path) -> None:
        root.mkdir(parents=True)
        if self.install_app:
            startup_script = staged_profile / "airootfs/opt/autoloader/start.sh"
            if self.startup and startup_script.is_file():
                _write_executable(root / "opt/autoloader/start.sh", startup_script.read_text(encoding="utf-8"))
            venv = root / "opt" / "autoloader" / "venv"
            bin_dir = venv / "bin"
            site = venv / "lib" / "python3.14" / "site-packages"
            package_dir = site / "autoloader"
            dist_info = site / "libre_core_autoloader-0.1.0.dist-info"
            bin_dir.mkdir(parents=True)
            site.mkdir(parents=True, exist_ok=True)
            package_dir.mkdir(parents=True)
            dist_info.mkdir(parents=True)
            _write_executable(bin_dir / "python", "synthetic interpreter\n")
            _write_executable(bin_dir / "autoloader", "synthetic console script\n")
            _write(venv / "pyvenv.cfg", "include-system-site-packages = true\n")
            _write(package_dir / "__init__.py", "\"\"\"fixture\"\"\"\n")
            _write(package_dir / "__main__.py", "def main():\n    return 0\n")
            _write(dist_info / "METADATA", "Metadata-Version: 2.1\nName: libre-core-autoloader\nVersion: 0.1.0\n")
            _write(dist_info / "entry_points.txt", "[console_scripts]\nautoloader = autoloader.__main__:main\n")
            wheels = list((staged_profile / "airootfs/opt/autoloader/wheelhouse").glob("*.whl"))
            assert len(wheels) == 1
            if self.marker:
                _write(
                    root / "var/lib/autoloader/image-install.json",
                    json.dumps(
                        {
                            "schema_version": 1,
                            "status": "APP_INSTALLED_AND_RUNTIME_CHECKED",
                            "distribution_name": "libre-core-autoloader",
                            "distribution_version": "0.1.0",
                            "entrypoint": "autoloader.__main__:main",
                            "runtime_imports": sorted(build_usb.REQUIRED_RUNTIME_IMPORTS),
                            "wheel_sha256": _sha(wheels[0]),
                        },
                        sort_keys=True,
                    )
                    + "\n",
                )
        if self.service:
            source_service = staged_profile / "airootfs/etc/systemd/system/autoloader.service"
            destination = root / "etc/systemd/system/autoloader.service"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_service, destination)
        if self.activation:
            source_activation = staged_profile / "airootfs/etc/systemd/system/multi-user.target.d/autoloader.conf"
            destination = root / "etc/systemd/system/multi-user.target.d/autoloader.conf"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_activation, destination)


def test_fake_builder_records_candidate_iso_and_boot_pending_provenance(tmp_path):
    config = _config(tmp_path)
    runner = _FakeBuilder()
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_mkarchiso = fake_tools / "mkarchiso"
    fake_mkarchiso.write_bytes(b"synthetic mkarchiso executable fixture")
    result = build_usb.execute_build(
        config,
        host_platform="linux",
        mkarchiso_path=str(fake_mkarchiso),
        runner=runner,
        mountpoints_reader=lambda: (),
        clock=lambda: 1_800_000_000,
    )
    assert result.iso_path.is_file()
    bundle = result.iso_path.parent
    assert bundle.parent == config.output_dir
    assert bundle.name == "candidate-bundle"
    assert result.manifest_path.parent == bundle
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "CANDIDATE_ISO_BUILD_COMPLETED; NOT_RELEASE_VALIDATED"
    assert manifest["release_validation_status"] == "PENDING_LINUX_QEMU_REVIEW"
    assert manifest["publication"] == {
        "mode": "ATOMIC_DIRECTORY_RENAME",
        "bundle_directory": "candidate-bundle",
        "checksum_file": "SHA256SUMS",
        "complete_before_visible": True,
    }
    assert manifest["snapshot"]["archiso_version"] == "91-1"
    consumption = manifest["pacman_sync_database_consumption"]
    assert consumption["status"] == "SOURCE_LEVEL_INVARIANT_ONLY"
    assert consumption["runtime_capture_absence_reason"].startswith("Archiso v91 successful cleanup")
    assert consumption["archiso_source_contract"]["source_commit"] == build_usb.ARCHISO_V91_SOURCE_COMMIT
    assert consumption["archiso_source_contract"]["source_invariant_tested"] is True
    assert consumption["archiso_source_contract"]["config_transform_lines"] == "284-286"
    assert consumption["archiso_source_contract"]["pacstrap_config_argument_lines"] == "320-345"
    assert consumption["archiso_source_contract"]["target_sync_database_cleanup_lines"] == "153-162"
    assert "not a runtime capture of pacman" in consumption["archiso_source_contract"]["contract"]
    assert consumption["staged_sync_databases"]["core"]["sha256_before_and_after"] == (
        manifest["snapshot"]["repository_database_sha256"]["core"]
    )
    mkarchiso_command = next(
        command for command in manifest["commands"] if Path(command[0]).name == "mkarchiso"
    )
    assert Path(mkarchiso_command[mkarchiso_command.index("-C") + 1]).name == Path(
        consumption["effective_pacman_config_relative_to_work"]
    ).name
    database_evidence = manifest["snapshot"]["repository_databases"]
    assert database_evidence["core"]["sha256"] == manifest["snapshot"]["repository_database_sha256"]["core"]
    assert database_evidence["core"]["url"].endswith("/core/os/x86_64/core.db")
    assert database_evidence["core"]["final_url"] == database_evidence["core"]["url"]
    assert database_evidence["core"]["signature_status"] == "ABSENT_DATABASEOPTIONAL"
    assert database_evidence["core"]["signature_final_url"] is None
    assert database_evidence["extra"]["signature_present"] is False
    assert "Server = file://" in runner.pacman_conf_content
    assert "CacheServer = https://archive.archlinux.org/repos/2026/09/29/$repo/os/$arch" in runner.pacman_conf_content
    assert runner.pacman_conf_content.count("Server = file://") == 2
    assert manifest["host"]["mkarchiso"]["sha256"] == _sha(fake_mkarchiso)
    assert manifest["host"]["mkarchiso"]["package_owner_output"].endswith("owned by archiso 91-1")
    assert manifest["host"]["package_inventory_sha256"] == hashlib.sha256(
        "\n".join(manifest["host"]["package_inventory"]).encode("utf-8")
    ).hexdigest()
    assert manifest["host"]["wheel_build_backend"]["backend_packages"] == {
        "setuptools": "80.9.0",
        "wheel": "0.45.1",
    }
    assert manifest["wheel"]["sha256"] == hashlib.sha256(b"synthetic wheel").hexdigest()
    wheel_command = next(
        command for command in manifest["commands"] if any("wheel-source" in part for part in command)
    )
    assert Path(wheel_command[-1]).name == "wheel-source"
    assert Path(wheel_command[-1]) != config.source_root
    assert "autoloader/__init__.py" in manifest["wheel_source_file_sha256"]
    embedded = json.loads(runner.embedded_provenance)
    assert "commands_before_image_build" not in embedded
    assert str(tmp_path) not in runner.embedded_provenance
    assert "wheel_build" in embedded
    assert not (config.source_root / "build").exists()
    assert not (config.source_root / "autoloader.egg-info").exists()
    assert manifest["qemu_bios_boot"] == "NOT_PERFORMED"
    assert manifest["qemu_uefi_boot"] == "NOT_PERFORMED"
    assert manifest["usb_media_write"] == "NOT_PERFORMED"
    assert len(manifest["installed_package_inventory"]) >= len(manifest["packages_x86_64"])
    assert manifest["target_root_install"]["status"] == "APP_INSTALLED_AND_RUNTIME_CHECKED"
    assert manifest["target_root_install"]["startup_script_sha256"] == _sha(
        config.profile_dir / "airootfs/opt/autoloader/start.sh"
    )
    assert manifest["target_root_install"]["runtime_smoke"]["runtime_imports"] == sorted(
        build_usb.REQUIRED_RUNTIME_IMPORTS
    )
    assert any(command[0] == str(fake_mkarchiso.resolve()) and "-C" in command for command in manifest["commands"])
    assert any("--no-index" in command and "--no-deps" in command for command in manifest["commands"])
    assert (bundle / "build.log").is_file()
    assert (bundle / "wheel-build.log").is_file()
    assert (bundle / "SHA256SUMS").is_file()
    sums = (bundle / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    assert f"{result.sha256}  {result.iso_path.name}" in sums
    assert f"{result.manifest_sha256}  {result.manifest_path.name}" in sums
    assert f"{_sha(bundle / 'build.log')}  build.log" in sums
    assert f"{_sha(bundle / 'wheel-build.log')}  wheel-build.log" in sums
    assert {line.split("  ", 1)[1] for line in sums} == {
        result.iso_path.name,
        result.manifest_path.name,
        "build.log",
        "wheel-build.log",
    }
    assert list(config.output_dir.iterdir()) == [bundle]
    assert list(config.work_parent.iterdir()) == []
    assert all(command[0] not in {"sudo", "flashrom"} for command in runner.calls)


def test_offline_candidate_build_uses_only_verified_owned_cache_copy(tmp_path):
    config = _offline_config(tmp_path)
    assert config.offline_repository is not None
    source_archive = config.offline_repository / "extra/os/x86_64/base-1-1-x86_64.pkg.tar.zst"
    assert source_archive.is_file()
    runner = _FakeBuilder(mutate_offline_source_during_build=source_archive)
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_mkarchiso = fake_tools / "mkarchiso"
    fake_mkarchiso.write_bytes(b"synthetic mkarchiso executable fixture")
    fake_unshare = fake_tools / "unshare"
    _write_executable(fake_unshare, "synthetic util-linux executable fixture\n")
    result = build_usb.execute_build(
        config,
        host_platform="linux",
        mkarchiso_path=str(fake_mkarchiso),
        unshare_path=str(fake_unshare),
        host_namespace_identity="net:[host-fixture]",
        runner=runner,
        mountpoints_reader=lambda: (),
        clock=lambda: 1_800_000_000,
    )
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    cache_lines = [
        line for line in runner.pacman_conf_content.splitlines()
        if line.strip().startswith("CacheServer =")
    ]
    assert len(cache_lines) == 2
    assert all("offline-repository/$repo/os/$arch" in line for line in cache_lines)
    assert all(config.offline_repository.as_uri() not in line for line in cache_lines)
    offline_evidence = manifest["offline_repository"]
    assert offline_evidence["manifest_sha256"] == _sha(config.offline_manifest)
    assert offline_evidence["staged_copy_removed_with_owned_workdir"] is True
    assert len(offline_evidence["staged_file_sha256"]) == len(
        json.loads(config.offline_manifest.read_text(encoding="utf-8"))["files"]
    )
    assert _sha(source_archive) != offline_evidence["staged_file_sha256"][
        "extra/os/x86_64/base-1-1-x86_64.pkg.tar.zst"
    ]
    assert manifest["snapshot"]["repository_databases"]["core"]["source_kind"] == "offline-manifest"
    assert manifest["snapshot"]["repository_databases"]["core"]["final_url"] is None
    embedded = json.loads(runner.embedded_provenance)
    assert embedded["offline_repository"]["manifest_sha256"] == offline_evidence["manifest_sha256"]
    assert embedded["offline_repository"]["staged_file_sha256"] == offline_evidence["staged_file_sha256"]


@pytest.mark.parametrize("failure_point", ["copy", "write", "hash", "directory-fsync", "bundle-rename"])
def test_bundle_publication_failure_leaves_no_candidate_iso(tmp_path, monkeypatch, failure_point):
    output = tmp_path / "output"
    output.mkdir()
    image = tmp_path / "candidate.iso"
    build_log = tmp_path / "build.log"
    wheel_log = tmp_path / "wheel-build.log"
    image.write_bytes(b"synthetic candidate ISO payload")
    build_log.write_bytes(b"synthetic mkarchiso log")
    wheel_log.write_bytes(b"synthetic wheel log")
    expected_image = _sha(image)
    expected_build = _sha(build_log)
    expected_wheel = _sha(wheel_log)

    if failure_point == "copy":
        original = build_usb._copy_publication_file

        def fail_copy(source, destination, expected):
            if destination.name == "build.log":
                destination.write_bytes(b"partial copied log")
                raise OSError("injected copy failure")
            return original(source, destination, expected)

        monkeypatch.setattr(build_usb, "_copy_publication_file", fail_copy)
    elif failure_point == "write":
        original = build_usb._write_publication_file

        def fail_write(destination, content):
            if destination.name == "SHA256SUMS":
                destination.write_bytes(b"partial checksum list")
                raise OSError("injected metadata write failure")
            return original(destination, content)

        monkeypatch.setattr(build_usb, "_write_publication_file", fail_write)
    elif failure_point == "hash":
        original = build_usb._sha256_file

        def fail_hash(path):
            if path.name == ".candidate.iso.pending":
                raise OSError("injected hash failure")
            return original(path)

        monkeypatch.setattr(build_usb, "_sha256_file", fail_hash)
    elif failure_point == "directory-fsync":
        original = build_usb._fsync_directory

        def fail_staged_bundle_fsync(path):
            if path.name.startswith(".candidate-stage-"):
                raise build_usb.BuildContractError("injected candidate bundle fsync failure")
            return original(path)

        monkeypatch.setattr(build_usb, "_fsync_directory", fail_staged_bundle_fsync)
    else:
        original = build_usb.os.replace

        def fail_bundle_rename(source, destination):
            if Path(source).name.startswith(".candidate-stage-") and Path(destination).name == "candidate-bundle":
                raise OSError("injected atomic bundle publication failure")
            return original(source, destination)

        monkeypatch.setattr(build_usb.os, "replace", fail_bundle_rename)

    with pytest.raises((OSError, build_usb.BuildContractError)):
        build_usb._publish_candidate_bundle(
            output,
            artifact_paths=(image,),
            selected_iso=image,
            build_log=build_log,
            wheel_log=wheel_log,
            provenance={"schema_version": 1},
            expected_artifact_sha256={image.name: expected_image},
            expected_build_log_sha256=expected_build,
            expected_wheel_log_sha256=expected_wheel,
        )

    assert list(output.iterdir()) == []
    assert list(output.rglob("*.iso")) == []


def test_candidate_build_rejects_wrong_database_bytes_before_mkarchiso_or_output(tmp_path):
    config = _config(tmp_path)
    runner = _FakeBuilder(wrong_downloaded_database=True)
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_mkarchiso = fake_tools / "mkarchiso"
    fake_mkarchiso.write_bytes(b"synthetic mkarchiso executable fixture")
    with pytest.raises(build_usb.BuildContractError, match="snapshot core.db bytes do not match"):
        build_usb.execute_build(
            config,
            host_platform="linux",
            mkarchiso_path=str(fake_mkarchiso),
            runner=runner,
            mountpoints_reader=lambda: (),
            clock=lambda: 1_800_000_000,
        )
    assert list(config.output_dir.iterdir()) == []
    assert sum(Path(call[0]).name == "mkarchiso" for call in runner.calls) == 0


def test_pacman_sync_database_bytes_are_captured_when_archiso_retains_them(tmp_path):
    config = _config(tmp_path)
    runner = _FakeBuilder(retain_sync_databases=True)
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_mkarchiso = fake_tools / "mkarchiso"
    fake_mkarchiso.write_bytes(b"synthetic mkarchiso executable fixture")
    result = build_usb.execute_build(
        config,
        host_platform="linux",
        mkarchiso_path=str(fake_mkarchiso),
        runner=runner,
        mountpoints_reader=lambda: (),
        clock=lambda: 1_800_000_000,
    )
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    evidence = manifest["pacman_sync_database_consumption"]
    assert evidence["status"] == "RUNTIME_SYNC_DATABASES_HASH_CAPTURED"
    assert evidence["runtime_capture"]["core.db"]["sha256"] == manifest["snapshot"]["repository_database_sha256"]["core"]
    assert evidence["runtime_capture"]["extra.db"]["sha256"] == manifest["snapshot"]["repository_database_sha256"]["extra"]


def test_wrong_retained_pacman_sync_database_blocks_candidate_publication(tmp_path):
    config = _config(tmp_path)
    runner = _FakeBuilder(retain_sync_databases=True, wrong_retained_database=True)
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_mkarchiso = fake_tools / "mkarchiso"
    fake_mkarchiso.write_bytes(b"synthetic mkarchiso executable fixture")
    with pytest.raises(build_usb.BuildContractError, match="retained a different core.db"):
        build_usb.execute_build(
            config,
            host_platform="linux",
            mkarchiso_path=str(fake_mkarchiso),
            runner=runner,
            mountpoints_reader=lambda: (),
            clock=lambda: 1_800_000_000,
        )
    assert list(config.output_dir.iterdir()) == []


@pytest.mark.parametrize(
    ("builder_options", "expected_error"),
    [
        ({"marker": False}, "customize-hook install marker"),
        ({"install_app": False}, "application venv interpreter"),
        ({"service": False}, "systemd unit"),
        ({"activation": False}, "systemd activation drop-in"),
        ({"startup": False}, "AutoLoader startup script"),
    ],
)
def test_candidate_build_fails_closed_when_target_install_or_service_is_missing(
    tmp_path, builder_options, expected_error
):
    config = _config(tmp_path)
    runner = _FakeBuilder(**builder_options)
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_mkarchiso = fake_tools / "mkarchiso"
    fake_mkarchiso.write_bytes(b"synthetic mkarchiso executable fixture")
    with pytest.raises(build_usb.BuildContractError, match=expected_error):
        build_usb.execute_build(
            config,
            host_platform="linux",
            mkarchiso_path=str(fake_mkarchiso),
            runner=runner,
            mountpoints_reader=lambda: (),
            clock=lambda: 1_800_000_000,
        )
    assert list(config.output_dir.iterdir()) == [], "unvalidated image must not appear as a candidate output"
    assert not (config.output_dir / "build-provenance.json").exists()


def test_candidate_build_rejects_database_mutated_during_mkarchiso_before_output(tmp_path):
    config = _config(tmp_path)
    runner = _FakeBuilder(mutate_database_after_build=True)
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_mkarchiso = fake_tools / "mkarchiso"
    fake_mkarchiso.write_bytes(b"synthetic mkarchiso executable fixture")
    with pytest.raises(build_usb.BuildContractError, match="staged core.db changed"):
        build_usb.execute_build(
            config,
            host_platform="linux",
            mkarchiso_path=str(fake_mkarchiso),
            runner=runner,
            mountpoints_reader=lambda: (),
            clock=lambda: 1_800_000_000,
        )
    assert list(config.output_dir.iterdir()) == []
    assert sum(Path(call[0]).name == "mkarchiso" for call in runner.calls) == 1


def test_offline_build_wraps_every_subprocess_in_verified_network_namespace(tmp_path):
    config = _offline_config(tmp_path)
    runner = _FakeBuilder()
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_mkarchiso = fake_tools / "mkarchiso"
    fake_mkarchiso.write_bytes(b"synthetic mkarchiso executable fixture")
    fake_unshare = fake_tools / "unshare"
    _write_executable(fake_unshare, "synthetic util-linux executable fixture\n")
    result = build_usb.execute_build(
        config,
        host_platform="linux",
        mkarchiso_path=str(fake_mkarchiso),
        unshare_path=str(fake_unshare),
        host_namespace_identity="net:[host-fixture]",
        runner=runner,
        mountpoints_reader=lambda: (),
        clock=lambda: 1_800_000_000,
    )
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["network_isolation"]["mode"] == "linux-network-namespace"
    is_root = callable(getattr(os, "geteuid", None)) and os.geteuid() == 0
    expected_mode = "root" if is_root else "user-mapped-root"
    expected_flags = ["--net"] if is_root else ["--user", "--map-root-user", "--net"]
    assert manifest["network_isolation"]["privilege_mode"] == expected_mode
    assert manifest["network_isolation"]["unshare_sha256"] == _sha(fake_unshare)
    assert manifest["network_isolation"]["probe_output"] == build_usb.OFFLINE_NETWORK_PROBE_MARKER
    expected_prefix = manifest["network_isolation"]["command_prefix"]
    assert expected_prefix[1:-1] == expected_flags
    assert all(
        call[: len(expected_prefix)] == expected_prefix
        for call in runner.calls
    ), "all offline subprocesses must inherit the verified network namespace"
    assert any(Path(call[len(expected_prefix)]).name == "mkarchiso" for call in runner.calls)
    assert any(call[len(expected_prefix)] == "chroot" for call in runner.calls)
    assert all(
        command[: len(expected_prefix)] == expected_prefix
        for command in manifest["commands"]
    )


def test_offline_build_fails_before_creating_work_or_output_without_namespace(tmp_path):
    config = _offline_config(tmp_path)
    runner = _FakeBuilder(namespace_ok=False)
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_unshare = fake_tools / "unshare"
    _write_executable(fake_unshare, "synthetic util-linux executable fixture\n")
    fake_mkarchiso = fake_tools / "mkarchiso"
    fake_mkarchiso.write_bytes(b"synthetic mkarchiso executable fixture")
    with pytest.raises(build_usb.BuildContractError, match="OFFLINE_NETWORK_ISOLATION_UNAVAILABLE"):
        build_usb.execute_build(
            config,
            host_platform="linux",
            mkarchiso_path=str(fake_mkarchiso),
            unshare_path=str(fake_unshare),
            host_namespace_identity="net:[host-fixture]",
            runner=runner,
            mountpoints_reader=lambda: (),
        )
    assert len(runner.calls) == 1
    assert runner.calls[0][0] == str(fake_unshare.resolve())
    assert list(config.output_dir.iterdir()) == []
    assert list(config.work_parent.iterdir()) == []


@pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("unshare") is None,
    reason="UNVALIDATED: host lacks Linux util-linux unshare",
)
def test_real_offline_network_namespace_has_no_external_routes():
    try:
        isolation = build_usb._establish_offline_network_isolation(subprocess.run)
    except build_usb.BuildContractError as exc:
        if any(
            marker in str(exc)
            for marker in (
                "trusted Arch pacman/util-linux ownership prerequisite",
                "exact resolved unshare path is owned by util-linux",
                "pacman did not confirm that the exact resolved unshare path",
            )
        ):
            pytest.skip("UNVALIDATED: host lacks trusted Arch pacman/util-linux executable ownership evidence")
        if any(
            marker in str(exc)
            for marker in (
                "Operation not permitted",
                "Permission denied",
                "unexpected network interfaces",
                "IPv4 routes exist",
                "IPv6 routes exist",
            )
        ):
            pytest.skip("UNVALIDATED: host does not expose a fully route-free network namespace to this test")
        raise
    assert isolation.mode == "linux-network-namespace"
    assert isolation.probe_output == build_usb.OFFLINE_NETWORK_PROBE_MARKER


@pytest.mark.parametrize(
    ("is_root", "expected_flags", "expected_mode"),
    [
        (True, ("--net",), "root"),
        (False, ("--user", "--map-root-user", "--net"), "user-mapped-root"),
    ],
)
def test_offline_namespace_uses_mode_appropriate_unshare_flags(tmp_path, is_root, expected_flags, expected_mode):
    fake_unshare = tmp_path / "unshare"
    _write_executable(fake_unshare, "synthetic util-linux executable fixture\n")
    runner = _FakeBuilder()
    isolation = build_usb._establish_offline_network_isolation(
        runner,
        unshare_path=str(fake_unshare),
        host_namespace_identity="net:[host-fixture]",
        is_root=is_root,
    )
    assert isolation.privilege_mode == expected_mode
    assert isolation.command_prefix == (str(fake_unshare.resolve()), *expected_flags, "--")


@pytest.mark.skipif(sys.platform != "linux", reason="Linux executable ownership contract")
@pytest.mark.parametrize("owner_output_kind", ["unowned", "different-path"])
def test_default_unshare_lookup_rejects_attacker_controlled_path_symlink_before_execution(
    tmp_path, monkeypatch, owner_output_kind
):
    outside = tmp_path / "attacker-unshare"
    _write_executable(outside, "not util-linux\n")
    bin_dir = tmp_path / "attacker-bin"
    bin_dir.mkdir()
    path_entry = bin_dir / "unshare"
    path_entry.symlink_to(outside)
    monkeypatch.setattr(build_usb.shutil, "which", lambda name: str(path_entry) if name == "unshare" else None)
    calls: list[list[str]] = []

    def reject_unowned(command, **kwargs):
        calls.append(list(command))
        assert command[:2] == ["pacman", "-Qo"]
        output = (
            f"{command[-1]} is not owned by any package\n"
            if owner_output_kind == "unowned"
            else f"{tmp_path / 'different-unshare'} is owned by util-linux 99.0\n"
        )
        return subprocess.CompletedProcess(
            command,
            0,
            output,
            "",
        )

    with pytest.raises(build_usb.BuildContractError, match="OFFLINE_NETWORK_ISOLATION_UNAVAILABLE"):
        build_usb._establish_offline_network_isolation(
            reject_unowned,
            host_namespace_identity="net:[test-host]",
            is_root=True,
        )
    assert calls == [["pacman", "-Qo", str(outside.resolve())]]


@pytest.mark.skipif(sys.platform != "linux", reason="Linux pacman ownership contract")
def test_default_unshare_lookup_reports_missing_pacman_as_unavailable(tmp_path, monkeypatch):
    unshare = tmp_path / "unshare"
    _write_executable(unshare, "synthetic executable fixture\n")
    monkeypatch.setattr(build_usb.shutil, "which", lambda name: str(unshare) if name == "unshare" else None)
    calls: list[list[str]] = []

    def missing_pacman(command, **kwargs):
        calls.append(list(command))
        raise FileNotFoundError("pacman is unavailable")

    with pytest.raises(build_usb.BuildContractError, match="OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: trusted Arch pacman/util-linux ownership prerequisite"):
        build_usb._establish_offline_network_isolation(
            missing_pacman,
            host_namespace_identity="net:[test-host]",
            is_root=True,
        )
    assert calls == [["pacman", "-Qo", str(unshare.resolve())]]


def test_target_package_inventory_requires_every_profile_package_and_valid_rows(tmp_path):
    target = tmp_path / "target-root"
    target.mkdir()

    def incomplete_runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "python 3.13-1\n", "")

    with pytest.raises(build_usb.BuildContractError, match="missing required packages"):
        build_usb._record_package_versions(target, incomplete_runner, ("python", "flashrom"))

    def malformed_runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "python 3.13-1\nnot a package row\n", "")

    with pytest.raises(build_usb.BuildContractError, match="malformed"):
        build_usb._record_package_versions(target, malformed_runner, ("python",))


def test_wheel_build_backend_requires_setuptools_and_wheel():
    def missing_wheel(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "pip 25.0 from fixture\n", "")
        return subprocess.CompletedProcess(command, 0, "Name: setuptools\nVersion: 80.9.0\n", "")

    with pytest.raises(build_usb.BuildContractError, match="setuptools and wheel"):
        build_usb._build_backend_evidence(missing_wheel)

    def old_setuptools(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "pip 25.0 from fixture\n", "")
        return subprocess.CompletedProcess(
            command, 0, "Name: setuptools\nVersion: 60.9.0\n\nName: wheel\nVersion: 0.45.1\n", ""
        )

    with pytest.raises(build_usb.BuildContractError, match="setuptools >= 61.0"):
        build_usb._build_backend_evidence(old_setuptools)


def test_cli_argument_parser_rejects_missing_values_and_include_roms():
    with pytest.raises(SystemExit):
        build_usb._parse_args(["--source-root"])
    with pytest.raises(SystemExit):
        build_usb._parse_args(["--include-roms", "arbitrary-roms"])


def test_click_build_usb_uses_shared_candidate_builder(monkeypatch, tmp_path):
    from autoloader import __main__ as app_cli

    output = tmp_path / "output"
    work = tmp_path / "work"
    output.mkdir()
    work.mkdir()
    iso = output / "candidate.iso"
    manifest = output / "build-provenance.json"
    iso.write_bytes(b"synthetic fixture only")
    manifest.write_text("{}\n", encoding="utf-8")
    observed: dict[str, Any] = {}

    def fake_execute(config):
        observed["config"] = config
        return build_usb.BuildResult(iso, manifest, _sha(iso), _sha(manifest))

    monkeypatch.setattr(app_cli.live_usb_builder, "execute_build", fake_execute)
    result = CliRunner().invoke(
        app_cli.cli,
        [
            "build-usb",
            "--source-root", str(build_usb.REPOSITORY_ROOT),
            "--profile-dir", str(build_usb.DEFAULT_PROFILE),
            "--snapshot-lock", str(build_usb.DEFAULT_SNAPSHOT),
            "--output-dir", str(output),
            "--work-parent", str(work),
            "--network-mode", "online",
        ],
    )
    assert result.exit_code == 0, result.output
    assert observed["config"].network_mode == "online"
    assert observed["config"].offline_repository is None
    assert "Candidate ISO build completed" in result.output
    assert "BIOS boot: NOT_PERFORMED" in result.output
    assert "Release validation: PENDING" in result.output


def test_click_build_usb_rejects_missing_options_before_builder(monkeypatch):
    from autoloader import __main__ as app_cli

    monkeypatch.setattr(
        app_cli.live_usb_builder,
        "execute_build",
        lambda config: pytest.fail("builder must not run with missing CLI inputs"),
    )
    result = CliRunner().invoke(app_cli.cli, ["build-usb", "--network-mode", "online"])
    assert result.exit_code != 0
    assert "Missing option '--source-root'" in result.output


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific host platform contract")
def test_real_build_usb_cli_fails_before_subprocess_on_windows(tmp_path):
    from autoloader.__main__ import cli

    output = tmp_path / "output"
    work = tmp_path / "work"
    output.mkdir()
    work.mkdir()
    result = CliRunner().invoke(
        cli,
        [
            "build-usb",
            "--source-root", str(build_usb.REPOSITORY_ROOT),
            "--profile-dir", str(build_usb.DEFAULT_PROFILE),
            "--snapshot-lock", str(build_usb.DEFAULT_SNAPSHOT),
            "--output-dir", str(output),
            "--work-parent", str(work),
            "--network-mode", "online",
        ],
    )
    assert result.exit_code != 0
    assert "LINUX_BUILD_ENV_REQUIRED" in result.output
    assert list(output.iterdir()) == []
    assert list(work.iterdir()) == []


def test_rootfs_service_logs_startup_without_root_shell_or_restart_loop():
    profile = build_usb.DEFAULT_PROFILE
    service = (profile / "airootfs/etc/systemd/system/autoloader.service").read_text(encoding="utf-8")
    startup = (profile / "airootfs/opt/autoloader/start.sh").read_text(encoding="utf-8")
    hook = (profile / "airootfs/root/customize_airootfs.sh").read_text(encoding="utf-8")
    enabled = (profile / "airootfs/etc/systemd/system/multi-user.target.d/autoloader.conf")
    assert "After=getty@tty1.service" in service
    assert "Conflicts=getty@tty1.service" in service
    assert "Restart=no" in service
    assert "journal+console" in service
    assert "journalctl -u autoloader.service" in startup
    assert "exec /opt/autoloader/venv/bin/python -m autoloader run" in startup
    assert "exec /bin/bash" not in startup
    assert "flashrom" not in startup
    assert "dialog" not in startup
    assert "--no-index" in hook and "--no-deps" in hook
    assert not enabled.exists(), "activation drop-in is generated in the isolated staging profile"
    assert not (profile / "airootfs/etc/systemd/system/getty@tty1.service.d/autologin.conf").exists()
    ventoy = json.loads((build_usb.REPOSITORY_ROOT / "usb_builder/ventoy/ventoy.json").read_text(encoding="utf-8"))
    assert "auto_install" not in ventoy
