"""Fake-only tests for the Live USB host/profile/build contract."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from usb_builder import build_usb


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


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
        "[options]\nSigLevel = Required DatabaseRequired\n\n"
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
    _write(profile / "airootfs" / "opt" / "autoloader" / "start.sh", "#!/bin/bash\nexit 0\n")
    _write(profile / "airootfs" / "etc" / "systemd" / "system" / "autoloader.service", "[Service]\nRestart=no\n")
    _write(profile / "airootfs" / "root" / "customize_airootfs.sh", "#!/bin/bash\nexit 0\n")
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
                "packages_x86_64_sha256": hashlib.sha256(package_text.encode("utf-8")).hexdigest(),
                "package_signature_policy": "Required DatabaseRequired",
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


def test_checked_in_lock_profile_and_official_archive_url_are_consistent():
    lock, raw = build_usb._load_json(build_usb.DEFAULT_SNAPSHOT, "snapshot lock")
    profile = build_usb.DEFAULT_PROFILE
    build_usb._validate_online_pacman_config(profile, lock)
    assert lock["snapshot_date"] == "2026-09-29"
    assert lock["repository_base"] == "https://archive.archlinux.org/repos/2026/09/29"
    assert build_usb._sha256_bytes(raw) == hashlib.sha256(raw).hexdigest()
    assert lock["snapshot_pin_status"] == "fixed-candidate"
    assert lock["release_validation_status"] == "pending-linux-build-and-qemu"
    assert set(lock["required_boot_modes"]) == build_usb.REQUIRED_BOOT_MODES
    assert set(build_usb._profile_boot_modes(profile)) >= build_usb.REQUIRED_BOOT_MODES


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
        "core/os/x86_64/core.db.sig",
        "extra/os/x86_64/extra.db",
        "extra/os/x86_64/extra.db.sig",
    ]
    for package in sorted(build_usb.REQUIRED_PACKAGES):
        archive = f"extra/os/x86_64/{package}-1-1-x86_64.pkg.tar.zst"
        paths.extend((archive, archive + ".sig"))
    for index, name in enumerate(paths):
        path = repository / name
        _write(path, f"synthetic local fixture {index}\n")
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
    pacman = build_usb._install_offline_conf(config.profile_dir, ready.offline_repository, tmp_path)
    content = pacman.read_text(encoding="utf-8")
    assert "file://" in content
    assert "https://" not in content

    (repository / paths[0]).write_text("tampered\n", encoding="utf-8")
    with pytest.raises(build_usb.BuildContractError, match="changed"):
        build_usb.preflight(offline, host_platform="linux")


def test_offline_manifest_rejects_missing_package_or_database_signatures(tmp_path):
    config = _config(tmp_path)
    repository = tmp_path / "offline-repo"
    repository.mkdir()
    manifest = tmp_path / "offline.json"
    manifest.write_text(
        json.dumps({"schema_version": 1, "snapshot_lock_sha256": "0" * 64, "files": {}, "packages": {}}),
        encoding="utf-8",
    )
    with pytest.raises(build_usb.BuildContractError, match="exact snapshot lock"):
        build_usb._validate_offline_repository(repository, manifest, "1" * 64, tuple(build_usb.REQUIRED_PACKAGES))


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
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.embedded_provenance = ""

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
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
        if Path(command[0]).name == "mkarchiso":
            output = Path(command[command.index("-o") + 1])
            work = Path(command[command.index("-w") + 1])
            profile = Path(command[-1])
            self.embedded_provenance = (
                profile / "airootfs/usr/share/doc/libre-core-autoloader/image-build-inputs.json"
            ).read_text(encoding="utf-8")
            (output / "libre_core_autoloader-test-x86_64.iso").write_bytes(b"synthetic fake ISO")
            (work / "x86_64" / "airootfs").mkdir(parents=True)
            return subprocess.CompletedProcess(command, 0, "fake mkarchiso completed\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")


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
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "CANDIDATE_ISO_BUILD_COMPLETED; NOT_RELEASE_VALIDATED"
    assert manifest["release_validation_status"] == "PENDING_LINUX_QEMU_REVIEW"
    assert manifest["snapshot"]["archiso_version"] == "91-1"
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
    wheel_command = next(command for command in manifest["commands"] if "pip" in command and "wheel" in command)
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
    assert any(command[0] == str(fake_mkarchiso.resolve()) and "-C" in command for command in manifest["commands"])
    assert any("--no-index" in command and "--no-deps" in command for command in manifest["commands"])
    assert (config.output_dir / "build.log").is_file()
    assert (config.output_dir / "wheel-build.log").is_file()
    assert (config.output_dir / "SHA256SUMS").is_file()
    assert list(config.work_parent.iterdir()) == []
    assert all(command[0] not in {"sudo", "flashrom"} for command in runner.calls)


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
