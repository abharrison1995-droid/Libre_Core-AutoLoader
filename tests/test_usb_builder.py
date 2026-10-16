"""Fake-only tests for the Live USB host/profile/build contract."""

from __future__ import annotations

import hashlib
import errno
import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest
from click.testing import CliRunner

from usb_builder import build_usb, mount_probe, network_probe, rootless_mount_probe


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


def _rootless_test_inputs() -> dict[str, Any]:
    user = "builder"
    shadow = build_usb.ROOTLESS_SHADOW_VERSION
    package_versions = {
        "archiso": build_usb.EXPECTED_MKARCHISO_VERSION,
        "arch-install-scripts": build_usb.EXPECTED_ARCH_INSTALL_SCRIPTS_VERSION,
        "pacman": build_usb.ROOTLESS_PACMAN_VERSION,
        "shadow": shadow,
        "libcap": build_usb.ROOTLESS_LIBCAP_VERSION,
        "util-linux": build_usb.ROOTLESS_UTIL_LINUX_VERSION,
        "bash": build_usb.ROOTLESS_BASH_VERSION,
    }
    helpers = {
        name: {
            "path": contract["path"],
            "sha256": contract["sha256"],
            "mode": contract["mode"],
            "owner_uid": 0,
            "owner_output": (
                f"{contract['path']} is owned by "
                f"{contract['package']} {package_versions[contract['package']]}"
            ),
            "file_capabilities": contract["file_capabilities"],
        }
        for name, contract in build_usb.ROOTLESS_TOOL_CONTRACT.items()
    }
    return {
        "username": user,
        "uid": 1000,
        "euid": 1000,
        "gid": 1002,
        "egid": 1002,
        "primary_group_name": user,
        "supplementary_gids": [],
        "status_text": (
            "CapEff:\t0000000000000000\n"
            "CapPrm:\t0000000000000000\n"
            f"CapBnd:\t{(1 << build_usb.ROOTLESS_CAP_SETUID) | (1 << build_usb.ROOTLESS_CAP_SETGID):016x}\n"
            "CapAmb:\t0000000000000000\n"
            "NoNewPrivs:\t0\n"
        ),
        "subuid_text": f"{user}:100000:65536\n",
        # Both subordinate files are keyed by the delegated account name/UID,
        # not the user's primary GID.
        "subgid_text": "1000:200000:65536\n",
        "helpers": helpers,
        "package_versions": package_versions,
    }


def test_rootless_util_linux_tools_have_distinct_locked_hashes():
    expected = {
        "setpriv": "d12c6ed1085a2997b791ff40a4b9a3d4f3d865b67af3051b62a64112bbf8cb53",
        "unshare": "e4e385ae4accf4af5302c4bb32859355b1c640315bef56ce73d31b8b1f97fdc0",
        "mount": "ca6c26aa2b11a8431481d39ee562bc07a88984f3294f34846d3257ec28ae9c91",
        "umount": "b8f6dcb0b8b7a790a431c954beebcb3db5931d8db81b02e09baa1e92beeaafac",
    }
    lock, _ = build_usb._load_json(build_usb.DEFAULT_SNAPSHOT, "snapshot lock")
    locked_builder = lock["rootless_builder"]
    assert build_usb.ROOTLESS_UTIL_LINUX_TOOL_SHA256 == expected
    assert locked_builder["util_linux_tools_sha256"] == expected
    for name, digest in expected.items():
        assert build_usb.ROOTLESS_TOOL_CONTRACT[name]["sha256"] == digest
        assert locked_builder["trusted_tool_contract"][name]["sha256"] == digest
    assert len(set(expected.values())) == 4


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
    _write(
        profile / build_usb.ARCHISO_INITRAMFS_CONFIG_RELATIVE_PATH,
        "HOOKS=(base udev microcode modconf kms memdisk archiso archiso_loop_mnt archiso_pxe_common archiso_pxe_nbd archiso_pxe_http archiso_pxe_nfs block filesystems keyboard)\n"
        'COMPRESSION="xz"\n'
        'COMPRESSION_OPTIONS=(-9e)\n',
    )
    _write(
        profile / build_usb.ARCHISO_USB_INITRAMFS_DERIVATIVE_RELATIVE_PATH,
        "HOOKS=(base udev microcode modconf memdisk archiso archiso_loop_mnt archiso_pxe_common archiso_pxe_nbd archiso_pxe_http archiso_pxe_nfs block filesystems keyboard)\n"
        'COMPRESSION="xz"\n'
        'COMPRESSION_OPTIONS=(-9e)\n',
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
                "rootless_builder": dict(build_usb.ROOTLESS_BUILDER_LOCK),
                "archiso_source_commit": build_usb.ARCHISO_V91_SOURCE_COMMIT,
                "archiso_initramfs_config_sha256": build_usb.ARCHISO_V91_INITRAMFS_CONFIG_SHA256,
                "archiso_usb_initramfs_derivative": {
                    "id": build_usb.ARCHISO_USB_INITRAMFS_DERIVATIVE_ID,
                    "source_path": build_usb.ARCHISO_USB_INITRAMFS_DERIVATIVE_SOURCE_PATH,
                    "profile_path": build_usb.ARCHISO_USB_INITRAMFS_DERIVATIVE_RELATIVE_PATH,
                    "sha256": build_usb.ARCHISO_USB_INITRAMFS_DERIVATIVE_SHA256,
                    "removed_hook": build_usb.ARCHISO_USB_INITRAMFS_REMOVED_HOOK,
                },
                "archiso_initramfs_base_packages": list(build_usb.ARCHISO_INITRAMFS_BASE_PACKAGES),
                "archiso_initramfs_hook_packages": {
                    hook: list(packages) for hook, packages in build_usb.ARCHISO_INITRAMFS_HOOK_PACKAGES.items()
                },
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
    assert lock["archiso_initramfs_config_sha256"] == build_usb.ARCHISO_V91_INITRAMFS_CONFIG_SHA256
    assert lock["archiso_usb_initramfs_derivative"]["sha256"] == (
        build_usb.ARCHISO_USB_INITRAMFS_DERIVATIVE_SHA256
    )
    assert lock["repository_database_sha256"] == {
        "core": "bb477fc11a9f3c19f3da2eced7869ab98c84c7d33dece62807458fdca43d018a",
        "extra": "399231e477e0aaf0afe0a79dd1cd82695236ba8259e454219b4254898112aed1",
    }
    assert set(lock["required_boot_modes"]) == build_usb.REQUIRED_BOOT_MODES
    assert set(build_usb._profile_boot_modes(profile)) >= build_usb.REQUIRED_BOOT_MODES


def test_checked_in_snapshot_pins_rootless_archiso_helper_contract():
    lock, _ = build_usb._load_json(build_usb.DEFAULT_SNAPSHOT, "snapshot lock")
    assert lock["rootless_builder"] == build_usb.ROOTLESS_BUILDER_LOCK
    assert build_usb.ROOTLESS_HOST_PACKAGES == (
        "archiso",
        "arch-install-scripts",
        "pacman",
        "shadow",
        "libcap",
        "util-linux",
        "bash",
    )
    assert build_usb.ROOTLESS_HELPER_CAPABILITIES == {
        "newuidmap": "cap_setuid=ep",
        "newgidmap": "cap_setgid=ep",
    }
    assert lock["rootless_builder"]["pacstrap_status_overlay"] == {
        "id": build_usb.ROOTLESS_PACSTRAP_PATCH_ID,
        "source_path": build_usb.ROOTLESS_ARCH_INSTALL_SCRIPTS_SOURCE_PATH,
        "source_sha256": build_usb.ROOTLESS_PACSTRAP_SHA256,
        "patch_path": "usb_builder/" + build_usb.ROOTLESS_PACSTRAP_PATCH_RELATIVE_PATH,
        "patch_sha256": build_usb.ROOTLESS_PACSTRAP_PATCH_SHA256,
        "patched_sha256": build_usb.ROOTLESS_PACSTRAP_PATCHED_SHA256,
        "path_policy": "operation-owned pacstrap directory first, then /usr/bin:/usr/sbin",
    }
    package_data = __import__("tomllib").loads(Path("pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["setuptools"]["package-data"]
    assert "patches/*.patch" in package_data["usb_builder"]


def _synthetic_pacstrap_source() -> bytes:
    return (
        b"#!/bin/bash\n# source fixture\n"
        + build_usb.ROOTLESS_PACSTRAP_PATCHED_RESULT_CONTEXT
        + b"exit 0\n"
    )


def test_rootless_pacstrap_patch_binds_exact_source_context_and_result_hash():
    source = _synthetic_pacstrap_source()
    expected = source.replace(
        build_usb.ROOTLESS_PACSTRAP_PATCHED_RESULT_CONTEXT,
        build_usb.ROOTLESS_PACSTRAP_PATCHED_RESULT_REPLACEMENT,
        1,
    )
    result = build_usb._render_rootless_pacstrap_patch(
        source,
        expected_source_sha256=hashlib.sha256(source).hexdigest(),
        expected_patched_sha256=hashlib.sha256(expected).hexdigest(),
    )
    assert result == expected
    for changed in (
        source.replace(build_usb.ROOTLESS_PACSTRAP_PATCHED_RESULT_CONTEXT, b"missing", 1),
        source + build_usb.ROOTLESS_PACSTRAP_PATCHED_RESULT_CONTEXT,
    ):
        changed_result = changed.replace(
            build_usb.ROOTLESS_PACSTRAP_PATCHED_RESULT_CONTEXT,
            build_usb.ROOTLESS_PACSTRAP_PATCHED_RESULT_REPLACEMENT,
            1,
        )
        with pytest.raises(build_usb.BuildContractError):
            build_usb._render_rootless_pacstrap_patch(
                changed,
                expected_source_sha256=hashlib.sha256(changed).hexdigest(),
                expected_patched_sha256=hashlib.sha256(changed_result).hexdigest(),
            )
    with pytest.raises(build_usb.BuildContractError, match="source hash differs from its pin"):
        build_usb._render_rootless_pacstrap_patch(source + b"unexpected source drift\n")


def test_rootless_pacstrap_source_path_rejects_noncanonical_and_symlink_inputs(tmp_path, monkeypatch):
    source = tmp_path / "pacstrap"
    _write(source, _synthetic_pacstrap_source().decode("utf-8"))
    os.chmod(source, 0o755)
    monkeypatch.setattr(build_usb, "ROOTLESS_ARCH_INSTALL_SCRIPTS_SOURCE_PATH", str(source))
    monkeypatch.setattr(build_usb, "ROOTLESS_PACSTRAP_SHA256", _sha(source))
    with pytest.raises(build_usb.BuildContractError, match="source path is not the pinned system executable"):
        build_usb._read_pinned_pacstrap_source(source.parent / "other", expected_owner_uid=source.stat().st_uid)
    if hasattr(os, "symlink"):
        linked = tmp_path / "pacstrap-link"
        try:
            linked.symlink_to(source)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation is not available in this test environment")
        monkeypatch.setattr(build_usb, "ROOTLESS_ARCH_INSTALL_SCRIPTS_SOURCE_PATH", str(linked))
        with pytest.raises(build_usb.BuildContractError):
            build_usb._read_pinned_pacstrap_source(linked, expected_owner_uid=source.stat().st_uid)


@pytest.mark.skipif(os.name == "nt", reason="staging requires POSIX ownership and mode semantics")
def test_rootless_pacstrap_overlay_stages_exclusive_private_operation_evidence(tmp_path, monkeypatch):
    source = tmp_path / "system" / "pacstrap"
    _write(source, _synthetic_pacstrap_source().decode("utf-8"))
    os.chmod(source, 0o755)
    source_bytes = source.read_bytes()
    patched = source_bytes.replace(
        build_usb.ROOTLESS_PACSTRAP_PATCHED_RESULT_CONTEXT,
        build_usb.ROOTLESS_PACSTRAP_PATCHED_RESULT_REPLACEMENT,
        1,
    )
    monkeypatch.setattr(build_usb, "ROOTLESS_ARCH_INSTALL_SCRIPTS_SOURCE_PATH", str(source))
    monkeypatch.setattr(build_usb, "ROOTLESS_PACSTRAP_SHA256", hashlib.sha256(source_bytes).hexdigest())
    monkeypatch.setattr(build_usb, "ROOTLESS_PACSTRAP_PATCHED_SHA256", hashlib.sha256(patched).hexdigest())
    operation_root = tmp_path / f"{build_usb.WORK_PREFIX}pacstrap-fixture"
    operation_root.mkdir()
    os.chmod(operation_root, 0o700)
    owner_token = operation_root.name.removeprefix(build_usb.WORK_PREFIX).encode("ascii")
    marker = operation_root / ".owner"
    marker.write_bytes(owner_token)
    os.chmod(marker, 0o600)
    owner_uid = operation_root.stat().st_uid
    monkeypatch.setattr(build_usb.os, "geteuid", lambda: owner_uid, raising=False)

    evidence = build_usb._stage_rootless_pacstrap_overlay(
        operation_root,
        expected_source_owner_uid=source.stat().st_uid,
    )
    staged = Path(evidence.staged_path)
    assert staged.read_bytes() == patched
    assert staged.stat().st_mode & 0o777 == 0o755
    assert evidence.operation_root == str(operation_root.resolve())
    assert evidence.operation_owner_uid == owner_uid
    document = evidence.to_manifest(mkarchiso_argv=("/usr/bin/mkarchiso", "-N", "profile"))
    assert document["operation"]["owner_marker_sha256"] == hashlib.sha256(owner_token).hexdigest()
    assert document["mkarchiso_path"].startswith(f"{staged.parent}:")
    build_usb._verify_rootless_pacstrap_overlay(evidence)

    staged.write_bytes(staged.read_bytes() + b"tampered\n")
    with pytest.raises(build_usb.BuildContractError, match="ROOTLESS_PACSTRAP_OVERLAY_INVALID"):
        build_usb._verify_rootless_pacstrap_overlay(evidence)


@pytest.mark.skipif(os.name == "nt", reason="staging requires POSIX ownership and mode semantics")
@pytest.mark.parametrize("root_change", ["missing_marker", "wrong_marker", "world_writable", "wrong_owner"])
def test_rootless_pacstrap_overlay_rejects_unowned_or_mutated_operation_root(
    tmp_path, monkeypatch, root_change
):
    root = tmp_path / f"{build_usb.WORK_PREFIX}owner-fixture"
    root.mkdir()
    os.chmod(root, 0o700)
    owner_token = root.name.removeprefix(build_usb.WORK_PREFIX)
    marker = root / ".owner"
    if root_change != "missing_marker":
        marker.write_text("wrong" if root_change == "wrong_marker" else owner_token, encoding="ascii")
        os.chmod(marker, 0o600)
    owner_uid = root.stat().st_uid
    monkeypatch.setattr(build_usb.os, "geteuid", lambda: owner_uid + (1 if root_change == "wrong_owner" else 0), raising=False)
    if root_change == "world_writable":
        os.chmod(root, 0o777)
    with pytest.raises(build_usb.BuildContractError, match="ROOTLESS_PACSTRAP_STAGE_INVALID"):
        build_usb._validate_pacstrap_operation_root(root)


@pytest.mark.skipif(os.name == "nt", reason="shell status proof requires POSIX process semantics")
@pytest.mark.parametrize(
    ("child_mode", "expected_returncode", "rm_fails"),
    [
        ("zero", 0, False),
        ("seven", 7, False),
        ("no_repo", 1, False),
        ("signal", 143, False),
        ("zero", 1, True),
    ],
)
def test_patched_pacstrap_block_preserves_child_and_cleanup_status(
    tmp_path, child_mode, expected_returncode, rm_fails
):
    bash = shutil.which("bash")
    assert bash is not None
    tmpfile = tmp_path / "aux-state"
    tmpfile.write_text("aux", encoding="ascii")
    prefix = f'''\
set +e
unshare=1
tmpfile={str(tmpfile)!r}
CHILD_MODE={child_mode!r}
export CHILD_MODE
pacstrap() {{
  case "$CHILD_MODE" in
    zero) return 0 ;;
    seven) return 7 ;;
    no_repo) printf 'error: no usable package repositories configured.\n'; return 1 ;;
    signal) kill -TERM "$$" ;;
    timeout) sleep 30 ;;
  esac
}}
declare_all() {{ printf ':'; }}
export -f pacstrap declare_all
mount_unshare() {{ "$@"; }}
mount_unshare=mount_unshare
rm() {{
  if [[ {1 if rm_fails else 0} == 1 ]]; then return 1; fi
  command rm "$@"
}}
'''
    script = prefix.encode("utf-8") + build_usb.ROOTLESS_PACSTRAP_PATCHED_RESULT_REPLACEMENT
    result = subprocess.run([bash, "-c", script.decode("utf-8")], check=False, text=True, capture_output=True)
    assert result.returncode == expected_returncode
    assert tmpfile.exists() is rm_fails
    if child_mode == "no_repo":
        assert "no usable package repositories configured" in result.stdout


@pytest.mark.skipif(os.name == "nt", reason="process-group timeout proof requires POSIX signals")
def test_pacstrap_overlay_timeout_stops_before_completion(tmp_path):
    bash = shutil.which("bash")
    assert bash is not None
    script = f'''\
set +e
unshare=1
tmpfile={str(tmp_path / "timeout-state")!r}
pacstrap() {{ sleep 30; }}
declare_all() {{ printf ':'; }}
export -f pacstrap declare_all
mount_unshare() {{ "$@"; }}
mount_unshare=mount_unshare
'''.encode("utf-8") + build_usb.ROOTLESS_PACSTRAP_PATCHED_RESULT_REPLACEMENT
    child = subprocess.Popen(
        [bash, "-c", script.decode("utf-8")],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    with pytest.raises(subprocess.TimeoutExpired):
        child.communicate(timeout=0.3)
    os.killpg(child.pid, signal.SIGTERM)
    try:
        child.communicate(timeout=0.3)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.communicate(timeout=3)
    assert child.returncode is not None


def test_rootless_host_state_accepts_only_exact_pinned_subids_caps_and_helpers():
    state = build_usb._validate_rootless_host_state(**_rootless_test_inputs())
    assert state.username == "builder"
    assert state.primary_group_name == "builder"
    assert state.supplementary_gids == ()
    assert (state.subuid_start, state.subuid_count) == (100000, 65536)
    assert (state.subgid_start, state.subgid_count) == (200000, 65536)
    assert state.archiso_version == "91-1"
    assert state.arch_install_scripts_version == "31-2"
    assert state.pacman_version == build_usb.ROOTLESS_PACMAN_VERSION
    assert {tool.name for tool in state.trusted_tools} == set(build_usb.ROOTLESS_TOOL_CONTRACT)
    assert next(tool for tool in state.trusted_tools if tool.name == "pacman").sha256 == (
        build_usb.ROOTLESS_PACMAN_SHA256
    )
    assert state.shadow_version == build_usb.ROOTLESS_SHADOW_VERSION
    assert state.newuidmap_sha256 == build_usb.ROOTLESS_HELPER_SHA256["newuidmap"]
    assert state.newuidmap_file_capabilities == "cap_setuid=ep"
    assert state.newgidmap_file_capabilities == "cap_setgid=ep"
    assert state.getcap_file_capabilities == ""
    assert state.getcap_mode == 0o755
    assert state.getcap_owner_uid == 0


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("uid", 0, "ROOTLESS_ARCHISO_REQUIRED"),
        ("euid", 0, "ROOTLESS_ARCHISO_REQUIRED"),
        ("euid", 1001, "ROOTLESS_ARCHISO_IDENTITY_INVALID"),
        ("primary_group_name", "wheel", "private primary group"),
        ("supplementary_gids", [4, 1002], "private primary group"),
        ("subuid_text", "", "rootless subuid must contain exactly one range"),
        ("subgid_text", "builder:200000:32768\n", "at least 65536 IDs"),
        ("subuid_text", "builder:100000:65536\nother:120000:65536\n", "ranges overlap"),
    ],
)
def test_rootless_host_state_rejects_identity_and_subid_failures(field, replacement, message):
    inputs = _rootless_test_inputs()
    inputs[field] = replacement
    with pytest.raises(build_usb.BuildContractError, match=message):
        build_usb._validate_rootless_host_state(**inputs)


@pytest.mark.parametrize(
    ("status_edit", "message"),
    [
        ("CapEff:\t0000000000000001", "effective/permitted/ambient caps must be empty"),
        ("CapBnd:\t00000000002000c0", "CapBnd must contain exactly SETUID and SETGID"),
        ("CapBnd:\t00000000000000c4", "CapBnd must contain exactly SETUID and SETGID"),
        ("CapBnd:\t0000000000000000", "CapBnd must contain exactly SETUID and SETGID"),
        ("NoNewPrivs:\t1", "NoNewPrivs must remain clear"),
    ],
)
def test_rootless_host_state_rejects_capability_escalation_state(status_edit, message):
    inputs = _rootless_test_inputs()
    lines = inputs["status_text"].splitlines()
    key = status_edit.split(":", 1)[0]
    inputs["status_text"] = "\n".join(
        status_edit if line.startswith(key + ":") else line for line in lines
    ) + "\n"
    with pytest.raises(build_usb.BuildContractError, match=message):
        build_usb._validate_rootless_host_state(**inputs)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("arch-install-scripts", "arch-install-scripts must be pinned to 31-2"),
        ("pacman", "pacman verifier must be pinned to the snapshot"),
        ("shadow", "shadow helper package is not the pinned version"),
        ("libcap", "libcap getcap tool package is not pinned"),
        ("helper-hash", "differs from its exact pinned path, bytes, mode, owner, or capabilities"),
        ("helper-mode", "differs from its exact pinned path, bytes, mode, owner, or capabilities"),
        ("helper-capability", "differs from its exact pinned path, bytes, mode, owner, or capabilities"),
        ("pacman-hash", "differs from its exact pinned path, bytes, mode, owner, or capabilities"),
        ("setpriv-hash", "differs from its exact pinned path, bytes, mode, owner, or capabilities"),
        ("unshare-hash", "differs from its exact pinned path, bytes, mode, owner, or capabilities"),
        ("mount-hash", "differs from its exact pinned path, bytes, mode, owner, or capabilities"),
        ("umount-hash", "differs from its exact pinned path, bytes, mode, owner, or capabilities"),
        ("getcap-hash", "differs from its exact pinned path, bytes, mode, owner, or capabilities"),
        ("getcap-capability", "differs from its exact pinned path, bytes, mode, owner, or capabilities"),
    ],
)
def test_rootless_host_state_rejects_helper_and_package_drift(change, message):
    inputs = _rootless_test_inputs()
    if change == "arch-install-scripts":
        inputs["package_versions"][change] = "31-1"
    elif change == "pacman":
        inputs["package_versions"][change] = "7.1.0-1"
    elif change == "shadow":
        inputs["package_versions"][change] = "4.19-1"
    elif change == "libcap":
        inputs["package_versions"][change] = "2.77-1"
    elif change == "helper-hash":
        inputs["helpers"]["newuidmap"]["sha256"] = "0" * 64
    elif change == "helper-mode":
        inputs["helpers"]["newgidmap"]["mode"] = 0o4755
    elif change == "helper-capability":
        inputs["helpers"]["newuidmap"]["file_capabilities"] = "cap_setuid,cap_setgid=ep"
    elif change == "getcap-capability":
        inputs["helpers"]["getcap"]["file_capabilities"] = "cap_dac_override=ep"
    elif change == "pacman-hash":
        inputs["helpers"]["pacman"]["sha256"] = "0" * 64
    elif change in {"setpriv-hash", "unshare-hash", "mount-hash", "umount-hash"}:
        tool_name = change.removesuffix("-hash")
        inputs["helpers"][tool_name]["sha256"] = "0" * 64
    else:
        inputs["helpers"]["getcap"]["sha256"] = "0" * 64
    with pytest.raises(build_usb.BuildContractError, match=message):
        build_usb._validate_rootless_host_state(**inputs)


def test_rootless_production_preflight_rejects_root_before_running_commands(monkeypatch):
    monkeypatch.setattr(build_usb.sys, "platform", "linux")
    monkeypatch.setattr(build_usb.os, "getuid", lambda: 0, raising=False)
    monkeypatch.setattr(build_usb.os, "geteuid", lambda: 0, raising=False)
    runner = lambda *args, **kwargs: pytest.fail("preflight launched a command for root")
    with pytest.raises(build_usb.BuildContractError, match="ROOTLESS_ARCHISO_REQUIRED"):
        build_usb._rootless_archiso_host_preflight(runner=runner)


def test_production_build_rejects_root_before_network_probe_or_work_creation(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(build_usb.sys, "platform", "linux")
    monkeypatch.setattr(build_usb.os, "getuid", lambda: 0, raising=False)
    monkeypatch.setattr(build_usb.os, "geteuid", lambda: 0, raising=False)
    with pytest.raises(build_usb.BuildContractError, match="ROOTLESS_ARCHISO_REQUIRED"):
        build_usb.execute_build(config, host_platform="linux")
    assert list(config.output_dir.iterdir()) == []
    assert list(config.work_parent.iterdir()) == []


def test_preflight_rejects_rootless_host_tool_lock_drift(tmp_path):
    config = _config(tmp_path)
    lock, _ = build_usb._load_json(config.snapshot_lock, "snapshot lock")
    lock["rootless_builder"]["arch_install_scripts_version"] = "31-1"
    config.snapshot_lock.write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(build_usb.BuildContractError, match="rootless Archiso helper contract"):
        build_usb.preflight(config, host_platform="linux")


def test_checked_in_lock_binds_official_archiso_initramfs_hooks():
    lock, _ = build_usb._load_json(build_usb.DEFAULT_SNAPSHOT, "snapshot lock")
    config = build_usb.DEFAULT_PROFILE / build_usb.ARCHISO_INITRAMFS_CONFIG_RELATIVE_PATH
    assert build_usb._sha256_file(config) == build_usb.ARCHISO_V91_INITRAMFS_CONFIG_SHA256
    assert lock["archiso_initramfs_config_sha256"] == build_usb.ARCHISO_V91_INITRAMFS_CONFIG_SHA256
    assert "archiso archiso_loop_mnt" in config.read_text(encoding="utf-8")


def test_checked_in_usb_initramfs_derivative_removes_only_kms_and_keeps_compression_and_pxe():
    lock, _ = build_usb._load_json(build_usb.DEFAULT_SNAPSHOT, "snapshot lock")
    original = build_usb.DEFAULT_PROFILE / build_usb.ARCHISO_INITRAMFS_CONFIG_RELATIVE_PATH
    derivative = build_usb.DEFAULT_PROFILE / build_usb.ARCHISO_USB_INITRAMFS_DERIVATIVE_RELATIVE_PATH
    original_bytes = original.read_bytes()
    derivative_bytes = derivative.read_bytes()
    lock_pin = lock["archiso_usb_initramfs_derivative"]
    assert lock_pin == {
        "id": build_usb.ARCHISO_USB_INITRAMFS_DERIVATIVE_ID,
        "source_path": build_usb.ARCHISO_USB_INITRAMFS_DERIVATIVE_SOURCE_PATH,
        "profile_path": build_usb.ARCHISO_USB_INITRAMFS_DERIVATIVE_RELATIVE_PATH,
        "sha256": build_usb.ARCHISO_USB_INITRAMFS_DERIVATIVE_SHA256,
        "removed_hook": "kms",
    }
    assert hashlib.sha256(derivative_bytes).hexdigest() == lock_pin["sha256"]
    assert derivative_bytes == build_usb._expected_usb_initramfs_derivative(original_bytes)
    original_hooks = tuple(original.read_text(encoding="ascii").splitlines()[0][7:-1].split())
    derivative_hooks = build_usb._usb_initramfs_hook_order(build_usb.DEFAULT_PROFILE)
    assert derivative_hooks == tuple(hook for hook in original_hooks if hook != "kms")
    assert derivative_hooks.count("kms") == 0
    assert {hook for hook in derivative_hooks if hook.startswith("archiso_pxe_")} == {
        "archiso_pxe_common",
        "archiso_pxe_nbd",
        "archiso_pxe_http",
        "archiso_pxe_nfs",
    }
    assert derivative_bytes.splitlines()[1:] == original_bytes.splitlines()[1:]


def test_preflight_rejects_usb_initramfs_derivative_lock_drift(tmp_path):
    config = _config(tmp_path)
    snapshot, _ = build_usb._load_json(config.snapshot_lock, "snapshot lock")
    snapshot["archiso_usb_initramfs_derivative"]["sha256"] = "0" * 64
    config.snapshot_lock.write_text(json.dumps(snapshot), encoding="utf-8")
    with pytest.raises(build_usb.BuildContractError, match="does not pin the reviewed USB initramfs derivative"):
        build_usb.preflight(config, host_platform="linux")


@pytest.mark.parametrize(
    "replacement",
    [
        "HOOKS=(base udev microcode modconf memdisk archiso archiso_loop_mnt archiso_pxe_common archiso_pxe_nbd archiso_pxe_http block filesystems keyboard)\nCOMPRESSION=\"xz\"\nCOMPRESSION_OPTIONS=(-9e)\n",
        "HOOKS=(base udev microcode modconf memdisk archiso archiso_loop_mnt archiso_pxe_common archiso_pxe_nbd archiso_pxe_http archiso_pxe_nfs block filesystems keyboard)\nCOMPRESSION=\"zstd\"\nCOMPRESSION_OPTIONS=(-9e)\n",
    ],
)
def test_preflight_rejects_derivative_that_changes_more_than_kms(tmp_path, replacement):
    config = _config(tmp_path)
    derivative = config.profile_dir / build_usb.ARCHISO_USB_INITRAMFS_DERIVATIVE_RELATIVE_PATH
    _write(derivative, replacement)
    with pytest.raises(build_usb.BuildContractError, match="USB initramfs derivative"):
        build_usb.preflight(config, host_platform="linux")


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
    _write(config.profile_dir / "pacman.conf", mutated)
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


@pytest.mark.parametrize("package", sorted(build_usb.ARCHISO_INITRAMFS_REQUIRED_PACKAGES))
def test_preflight_requires_every_configured_archiso_hook_runtime_package(tmp_path, package):
    config = _config(tmp_path)
    package_list = config.profile_dir / "packages.x86_64"
    names = package_list.read_text(encoding="utf-8").splitlines()
    package_list.write_text("\n".join(name for name in names if name != package) + "\n", encoding="utf-8")
    with pytest.raises(build_usb.BuildContractError, match="missing required closed runtime/boot packages"):
        build_usb.preflight(config, host_platform="linux")


def test_preflight_requires_locked_archiso_hook_package_map(tmp_path):
    config = _config(tmp_path)
    snapshot, _ = build_usb._load_json(config.snapshot_lock, "snapshot lock")
    snapshot["archiso_initramfs_hook_packages"]["archiso"] = ["mkinitcpio"]
    config.snapshot_lock.write_text(json.dumps(snapshot), encoding="utf-8")
    with pytest.raises(build_usb.BuildContractError, match="does not pin package requirements"):
        build_usb.preflight(config, host_platform="linux")


def test_preflight_requires_locked_archiso_base_package_list(tmp_path):
    config = _config(tmp_path)
    snapshot, _ = build_usb._load_json(config.snapshot_lock, "snapshot lock")
    snapshot["archiso_initramfs_base_packages"].remove("mkinitcpio")
    config.snapshot_lock.write_text(json.dumps(snapshot), encoding="utf-8")
    with pytest.raises(build_usb.BuildContractError, match="does not pin mandatory Archiso initramfs base packages"):
        build_usb.preflight(config, host_platform="linux")


def test_preflight_requires_exact_archiso_initramfs_hooks(tmp_path):
    config = _config(tmp_path)
    hook_config = config.profile_dir / build_usb.ARCHISO_INITRAMFS_CONFIG_RELATIVE_PATH
    _write(hook_config, "HOOKS=(base systemd autodetect block filesystems fsck)\n")
    with pytest.raises(build_usb.BuildContractError, match="initramfs hook configuration"):
        build_usb.preflight(config, host_platform="linux")


def test_preflight_requires_initramfs_config_lock_pin(tmp_path):
    config = _config(tmp_path)
    snapshot, _ = build_usb._load_json(config.snapshot_lock, "snapshot lock")
    snapshot.pop("archiso_initramfs_config_sha256")
    config.snapshot_lock.write_text(json.dumps(snapshot), encoding="utf-8")
    with pytest.raises(build_usb.BuildContractError, match="initramfs hook configuration"):
        build_usb.preflight(config, host_platform="linux")


def test_bios_syslinux_mode_requires_syslinux_package(tmp_path):
    config = _config(tmp_path)
    package_list = config.profile_dir / "packages.x86_64"
    names = package_list.read_text(encoding="utf-8").splitlines()
    package_list.write_text("\n".join(name for name in names if name != "syslinux") + "\n", encoding="utf-8")
    with pytest.raises(build_usb.BuildContractError, match="syslinux"):
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
    assert snapshot["archiso_initramfs_base_packages"] == list(build_usb.ARCHISO_INITRAMFS_BASE_PACKAGES)
    assert snapshot["archiso_initramfs_hook_packages"] == {
        hook: list(packages) for hook, packages in build_usb.ARCHISO_INITRAMFS_HOOK_PACKAGES.items()
    }
    checked_in_names = set(build_usb._read_package_names(package_path))
    assert build_usb.ARCHISO_INITRAMFS_REQUIRED_PACKAGES <= checked_in_names
    checked_in_bytes = package_path.read_bytes()
    canonical_bytes = checked_in_bytes.replace(b"\r\n", b"\n")
    assert hashlib.sha256(canonical_bytes).hexdigest() == snapshot["packages_x86_64_sha256"]
    build_usb._validate_package_list_pin(package_path, snapshot)

    crlf_copy = tmp_path / "packages.x86_64"
    crlf_copy.write_bytes(canonical_bytes.replace(b"\n", b"\r\n"))
    build_usb._validate_package_list_pin(crlf_copy, snapshot)


def test_checked_in_archiso_profile_inputs_are_lf_only():
    repository_root = Path(__file__).resolve().parents[1]
    profile = repository_root / "usb_builder" / "archiso_profile"
    files = [profile / relative for relative in build_usb.ARCHISO_PROFILE_TEXT_INPUTS]
    assert files
    assert all(path.is_file() for path in files)
    assert all(b"\r" not in path.read_bytes() for path in files)


@pytest.mark.parametrize(
    "relative_path",
    [
        "profiledef.sh",
        "pacman.conf",
        "syslinux/syslinux.cfg",
        "airootfs/root/customize_airootfs.sh",
        "airootfs/opt/autoloader/start.sh",
        "airootfs/etc/systemd/system/autoloader.service",
    ],
)
def test_preflight_rejects_crlf_archiso_script_and_config_inputs(tmp_path, relative_path):
    config = _config(tmp_path)
    path = config.profile_dir / relative_path
    data = path.read_bytes()
    assert b"\r" not in data
    path.write_bytes(data.replace(b"\n", b"\r\n"))
    with pytest.raises(build_usb.BuildContractError, match="LF-only line endings"):
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
    _write_executable(fake_mkarchiso, "synthetic mkarchiso executable fixture\n")
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


def test_strip_expected_arch_chroot_diagnostics_preserves_unknown_output(tmp_path: Path) -> None:
    target_root = tmp_path / "airootfs"
    warning = (
        f"==> WARNING: {target_root} is not a mountpoint. "
        "This may have undesirable side effects.\n"
    )
    unmount_note = f"umount: {target_root}/etc/resolv.conf: not mounted.\n"
    payload = '{"runtime_imports": []}\n'

    assert build_usb._strip_expected_arch_chroot_diagnostics(
        warning + payload + unmount_note, target_root
    ) == payload
    unexpected = "unrecognized arch-chroot output\n"
    assert build_usb._strip_expected_arch_chroot_diagnostics(
        warning + unexpected + payload + unmount_note, target_root
    ) == unexpected + payload
    for invalid_wrapper in (
        warning + warning + payload + unmount_note,
        unmount_note + payload + warning,
        unexpected + warning + payload + unmount_note,
        warning + payload + unmount_note + unexpected,
    ):
        with pytest.raises(build_usb.BuildContractError, match="diagnostic wrapper contract"):
            build_usb._strip_expected_arch_chroot_diagnostics(invalid_wrapper, target_root)


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
        mount_denied: str | None = None,
        mutate_database_after_build: bool = False,
        mutate_profile_config_after_build: bool = False,
        mutate_usb_derivative_after_build: bool = False,
        mutate_profile_crlf_after_build: bool = False,
        wrong_downloaded_database: bool = False,
        retain_sync_databases: bool = False,
        wrong_retained_database: bool = False,
        redirect_to: str | None = None,
        mutate_offline_source_during_build: Path | None = None,
        target_pacman_returncode: int = 0,
        mkarchiso_returncode: int = 0,
        cli_help_suffix: str = "",
        runtime_help_output: str | None = None,
        cli_help_output: str | None = None,
        runtime_help_width: int = build_usb.TARGET_ROOT_CLI_HELP_WIDTH,
        omit_systemd_symlink: str | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self.call_kwargs: list[dict[str, Any]] = []
        self.embedded_provenance = ""
        self.install_app = install_app
        self.marker = marker
        self.service = service
        self.activation = activation
        self.startup = startup
        self.namespace_ok = namespace_ok
        self.mount_denied = mount_denied
        self.mutate_database_after_build = mutate_database_after_build
        self.mutate_profile_config_after_build = mutate_profile_config_after_build
        self.mutate_usb_derivative_after_build = mutate_usb_derivative_after_build
        self.mutate_profile_crlf_after_build = mutate_profile_crlf_after_build
        self.wrong_downloaded_database = wrong_downloaded_database
        self.retain_sync_databases = retain_sync_databases
        self.wrong_retained_database = wrong_retained_database
        self.redirect_to = redirect_to
        self.mutate_offline_source_during_build = mutate_offline_source_during_build
        self.target_pacman_returncode = target_pacman_returncode
        self.mkarchiso_returncode = mkarchiso_returncode
        self.cli_help_suffix = cli_help_suffix
        self.runtime_help_output = runtime_help_output
        self.cli_help_output = cli_help_output
        self.runtime_help_width = runtime_help_width
        self.omit_systemd_symlink = omit_systemd_symlink
        self.pacman_conf_content = ""

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = list(command)
        self.calls.append(command)
        self.call_kwargs.append(dict(kwargs))
        if Path(command[0]).name == "unshare" and "--net" in command:
            separator = command.index("--")
            inner = command[separator + 1 :]
            if len(inner) >= 3 and inner[1:3] == ["-m", "usb_builder.network_probe"]:
                if "--run" in inner:
                    run_index = inner.index("--run")
                    child_separator = inner.index("--", run_index + 1)
                    command = inner[child_separator + 1 :]
                    if not self.namespace_ok:
                        return subprocess.CompletedProcess(
                            command, 125, "network namespace probe failed\n", ""
                        )
                else:
                    output = build_usb.OFFLINE_NETWORK_PROBE_MARKER + "\n" if self.namespace_ok else "network namespace probe failed\n"
                    return subprocess.CompletedProcess(command, 0 if self.namespace_ok else 1, output, "")
            elif len(inner) >= 3 and inner[1:3] == ["-m", "usb_builder.mount_probe"]:
                if not self.namespace_ok:
                    return subprocess.CompletedProcess(command, 125, "network namespace probe failed\n", "")
                host_ids = json.loads(inner[3])
                child_ids = {
                    kind: f"{kind}:[{200 + index}]"
                    for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)
                }
                dependent_mounts = {"devpts-gid-5", "dev-shm-tmpfs"} | {
                    name for name in mount_probe.MOUNT_OPERATION_NAMES if name.startswith("device-bind-")
                }
                attempts = []
                devtmpfs_denied = False
                run_bind_denied = False
                for name in mount_probe.MOUNT_OPERATION_NAMES:
                    if name == self.mount_denied:
                        devtmpfs_denied = name == "devtmpfs"
                        run_bind_denied = name == "run-bind"
                        attempts.append({"name": name, "status": "denied", "errno": 1})
                    elif devtmpfs_denied and name in dependent_mounts:
                        attempts.append({"name": name, "status": "blocked", "errno": None})
                    elif run_bind_denied and name == "run-bind-private":
                        attempts.append({"name": name, "status": "blocked", "errno": None})
                    else:
                        status = "applied" if name == "run-bind-private" else "mounted"
                        attempts.append({"name": name, "status": status, "errno": None})
                report = {
                    "schema_version": 1,
                    "status": "UNSUPPORTED" if self.mount_denied else "SUPPORTED",
                    "namespace_ids": child_ids,
                    "propagation_private": True,
                    "network": {
                        "namespace_id": child_ids["net"],
                        "sysfs_interfaces_observed": ["lo"],
                        "namespace_interfaces": ["lo"],
                        "ipv4_netlink_route_count": 0,
                        "ipv6_netlink_route_count": 0,
                        "ipv4_rule_priorities": [0, 32766, 32767],
                        "ipv6_rule_priorities": [0, 32766],
                        "ipv6_null_unreachable_proc_rows": 0,
                    },
                    "mount_attempts": attempts,
                    "mounts_cleaned": True,
                    "probe_owned_sources_only": True,
                    "host_dev_or_run_bound": False,
                }
                status = 3 if self.mount_denied else 0
                output = mount_probe.PROBE_MARKER + "\n" + json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"
                return subprocess.CompletedProcess(command, status, output, "")
            else:
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
            tool = Path(command[-1]).name
            package = "util-linux 2.41.3" if tool == "unshare" else "iproute2 6.19.0" if tool == "ip" else "archiso 91-1"
            return subprocess.CompletedProcess(command, 0, f"{command[-1]} is owned by {package}\n", "")
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
        if command[:2] == ["pacman", "--config"] and "--dbpath" in command and command[-1] == "-Q":
            package_names = set(build_usb.REQUIRED_PACKAGES) | {"intel-ucode"}
            inventory = "".join(f"{name} 1-1\n" for name in sorted(package_names))
            status = self.target_pacman_returncode
            return subprocess.CompletedProcess(command, status, inventory if status == 0 else "pacman fixture failure\n", "")
        if "-m" in command and "pip" in command and "wheel" in command:
            wheel_dir = Path(command[command.index("--wheel-dir") + 1])
            wheel_dir.mkdir(parents=True, exist_ok=True)
            (wheel_dir / "libre_core_autoloader-0.1.0-py3-none-any.whl").write_bytes(b"synthetic wheel")
            return subprocess.CompletedProcess(command, 0, "built synthetic wheel\n", "")
        if command and command[0] == "arch-chroot" and command[1:2] == ["-N"]:
            target_root = Path(command[2])
            help_output = self.runtime_help_output or "Usage: autoloader [OPTIONS] COMMAND [ARGS]...\n"
            warning = (
                f"==> WARNING: {target_root} is not a mountpoint. "
                "This may have undesirable side effects.\n"
            )
            unmount_note = f"umount: {target_root}/etc/resolv.conf: not mounted.\n"
            if command[3:5] == ["/opt/autoloader/venv/bin/python", "-c"]:
                document = {
                    "distribution_name": "libre-core-autoloader",
                    "distribution_version": "0.1.0",
                    "entrypoint": "autoloader.__main__:main",
                    "runtime_imports": sorted(build_usb.REQUIRED_RUNTIME_IMPORTS),
                    "wheel_sha256": command[-1],
                    "python_version": "3.14.0",
                    "console_help_sha256": hashlib.sha256(help_output.encode("utf-8")).hexdigest(),
                    "console_help_terminal_width": self.runtime_help_width,
                }
                return subprocess.CompletedProcess(
                    command,
                    0,
                    warning + json.dumps(document, sort_keys=True) + "\n" + unmount_note,
                    "",
                )
            if command[3:] == ["/opt/autoloader/venv/bin/autoloader", "--help"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    warning
                    + (self.cli_help_output if self.cli_help_output is not None else help_output)
                    + self.cli_help_suffix
                    + unmount_note,
                    "",
                )
        if Path(command[0]).name == "mkarchiso":
            if self.mkarchiso_returncode:
                return subprocess.CompletedProcess(
                    command,
                    self.mkarchiso_returncode,
                    "synthetic mkarchiso failure before output creation\n",
                    "",
                )
            output = Path(command[command.index("-o") + 1])
            work = Path(command[command.index("-w") + 1])
            profile = Path(command[-1])
            if self.mutate_profile_config_after_build:
                config_path = profile / build_usb.ARCHISO_INITRAMFS_CONFIG_RELATIVE_PATH
                _write(config_path, "HOOKS=(base udev block filesystems)\n")
            if self.mutate_usb_derivative_after_build:
                derivative_path = profile / build_usb.ARCHISO_USB_INITRAMFS_DERIVATIVE_RELATIVE_PATH
                _write(derivative_path, "HOOKS=(base udev block filesystems)\n")
            if self.mutate_profile_crlf_after_build:
                script_path = profile / "profiledef.sh"
                script_path.write_bytes(script_path.read_bytes().replace(b"\n", b"\r\n"))
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
        local_db = root / "var" / "lib" / "pacman" / "local"
        _write(local_db / "ALPM_DB_VERSION", build_usb.EXPECTED_ALPM_LOCAL_DB_VERSION)
        for package in sorted(set(build_usb.REQUIRED_PACKAGES) | {"intel-ucode"}):
            package_record = local_db / f"{package}-1-1"
            package_record.mkdir(parents=True, exist_ok=True)
            _write(package_record / "desc", f"%NAME%\n{package}\n\n%VERSION%\n1-1\n")
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
        if sys.platform == "linux":
            systemd_links = (
                (
                    "autoloader_enablement",
                    "etc/systemd/system/multi-user.target.wants/autoloader.service",
                    "../autoloader.service",
                ),
            )
            for link_name, relative, target in systemd_links:
                if link_name == self.omit_systemd_symlink:
                    continue
                link = root / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(target)


def test_fake_builder_records_candidate_iso_and_boot_pending_provenance(tmp_path):
    config = _config(tmp_path)
    runner = _FakeBuilder()
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_mkarchiso = fake_tools / "mkarchiso"
    _write_executable(fake_mkarchiso, "synthetic mkarchiso executable fixture\n")
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
    assert manifest["snapshot"]["archiso_initramfs_config_sha256"] == build_usb.ARCHISO_V91_INITRAMFS_CONFIG_SHA256
    assert manifest["snapshot"]["archiso_initramfs_config_source_path"] == (
        build_usb.ARCHISO_V91_INITRAMFS_CONFIG_SOURCE_PATH
    )
    assert manifest["snapshot"]["archiso_initramfs_config_staged_path"] == (
        build_usb.ARCHISO_INITRAMFS_CONFIG_RELATIVE_PATH
    )
    assert manifest["snapshot"]["staged_archiso_initramfs_config_sha256"] == (
        build_usb.ARCHISO_V91_INITRAMFS_CONFIG_SHA256
    )
    assert manifest["snapshot"]["archiso_initramfs_hook_packages"] == {
        hook: list(packages) for hook, packages in build_usb.ARCHISO_INITRAMFS_HOOK_PACKAGES.items()
    }
    assert manifest["snapshot"]["archiso_initramfs_base_packages"] == list(
        build_usb.ARCHISO_INITRAMFS_BASE_PACKAGES
    )
    assert manifest["snapshot"]["archiso_initramfs_required_packages"] == sorted(
        build_usb.ARCHISO_INITRAMFS_REQUIRED_PACKAGES
    )
    derivative_evidence = manifest["snapshot"]["archiso_usb_initramfs_derivative"]
    assert derivative_evidence == {
        "id": build_usb.ARCHISO_USB_INITRAMFS_DERIVATIVE_ID,
        "source_path": build_usb.ARCHISO_USB_INITRAMFS_DERIVATIVE_SOURCE_PATH,
        "source_sha256": build_usb.ARCHISO_USB_INITRAMFS_DERIVATIVE_SHA256,
        "profile_path": build_usb.ARCHISO_USB_INITRAMFS_DERIVATIVE_RELATIVE_PATH,
        "staged_sha256": build_usb.ARCHISO_USB_INITRAMFS_DERIVATIVE_SHA256,
        "removed_hook": "kms",
        "retained_hook_order": list(build_usb._usb_initramfs_hook_order(config.profile_dir)),
        "compression_policy": "INHERIT_EXACT_PINNED_ARCHISO_FRAGMENT",
    }
    assert manifest["staged_profile_file_sha256"][
        f"profile/{Path(build_usb.ARCHISO_INITRAMFS_CONFIG_RELATIVE_PATH).as_posix()}"
    ] == build_usb.ARCHISO_V91_INITRAMFS_CONFIG_SHA256
    assert manifest["profile_file_sha256"][build_usb.ARCHISO_USB_INITRAMFS_DERIVATIVE_SOURCE_PATH] == (
        build_usb.ARCHISO_USB_INITRAMFS_DERIVATIVE_SHA256
    )
    assert manifest["staged_profile_file_sha256"][
        f"profile/{Path(build_usb.ARCHISO_USB_INITRAMFS_DERIVATIVE_RELATIVE_PATH).as_posix()}"
    ] == build_usb.ARCHISO_USB_INITRAMFS_DERIVATIVE_SHA256
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
    if sys.platform == "linux":
        assert manifest["target_root_install"]["systemd_links"] == {
            "status": "CHECKED",
            "autoloader_enablement": {
                "path": "etc/systemd/system/multi-user.target.wants/autoloader.service",
                "target": "../autoloader.service",
            },
        }
    else:
        assert manifest["target_root_install"]["systemd_links"]["status"] == (
            "NOT_CHECKED_NON_LINUX_TEST_HOST"
        )
    assert manifest["target_root_install"]["console_help_terminal_width"] == 78
    assert manifest["target_root_install"]["runtime_smoke"]["console_help_terminal_width"] == 78
    console_help_call_index = next(
        index
        for index, command in enumerate(runner.calls)
        if command[-2:] == ["/opt/autoloader/venv/bin/autoloader", "--help"]
    )
    assert "COLUMNS" not in runner.call_kwargs[console_help_call_index]["env"]
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


def test_fake_builder_rejects_unexpected_output_after_console_help(tmp_path: Path) -> None:
    config = _config(tmp_path)
    runner = _FakeBuilder(cli_help_suffix="unexpected post-help output\n")
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_mkarchiso = fake_tools / "mkarchiso"
    _write_executable(fake_mkarchiso, "synthetic mkarchiso executable fixture\n")

    with pytest.raises(build_usb.BuildContractError, match="differs from the installed Click help contract"):
        build_usb.execute_build(
            config,
            host_platform="linux",
            mkarchiso_path=str(fake_mkarchiso),
            runner=runner,
            mountpoints_reader=lambda: (),
            clock=lambda: 1_800_000_000,
        )
    assert not (config.output_dir / "candidate-bundle").exists()


def test_fake_builder_binds_full_click_help_fingerprint_to_width_78(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "120")
    runtime_script = build_usb._rootfs_runtime_validation_script()
    compile(runtime_script, "<target-root-runtime-validation>", "exec")
    assert f"terminal_width={build_usb.TARGET_ROOT_CLI_HELP_WIDTH}" in runtime_script
    assert f"'console_help_terminal_width': {build_usb.TARGET_ROOT_CLI_HELP_WIDTH}" in runtime_script
    from autoloader import __main__ as app_cli

    help_78_result = CliRunner().invoke(
        app_cli.cli,
        ["--help"],
        prog_name="autoloader",
        terminal_width=78,
    )
    help_80_result = CliRunner().invoke(
        app_cli.cli,
        ["--help"],
        prog_name="autoloader",
        terminal_width=80,
    )
    assert help_78_result.exit_code == 0
    assert help_80_result.exit_code == 0
    assert help_78_result.output.startswith("Usage: autoloader ")
    assert hashlib.sha256(help_78_result.output.encode("utf-8")).hexdigest() != hashlib.sha256(
        help_80_result.output.encode("utf-8")
    ).hexdigest()

    def run_fake_build(
        build_root: Path,
        *,
        runtime_output: str,
        cli_output: str,
    ) -> tuple[build_usb.BuildConfig, _FakeBuilder, Any]:
        config = _config(build_root)
        runner = _FakeBuilder(
            runtime_help_output=runtime_output,
            cli_help_output=cli_output,
        )
        fake_tools = build_root / "fake-tools"
        fake_tools.mkdir()
        fake_mkarchiso = fake_tools / "mkarchiso"
        _write_executable(fake_mkarchiso, "synthetic mkarchiso executable fixture\n")
        result = build_usb.execute_build(
            config,
            host_platform="linux",
            mkarchiso_path=str(fake_mkarchiso),
            runner=runner,
            mountpoints_reader=lambda: (),
            clock=lambda: 1_800_000_000,
        )
        return config, runner, result

    config_78, runner_78, result_78 = run_fake_build(
        tmp_path / "width-78",
        runtime_output=help_78_result.output,
        cli_output=help_78_result.output,
    )
    assert result_78.iso_path.is_file()
    assert json.loads(result_78.manifest_path.read_text(encoding="utf-8"))["target_root_install"][
        "console_help_terminal_width"
    ] == 78
    console_help_call_index = next(
        index
        for index, command in enumerate(runner_78.calls)
        if command[-2:] == ["/opt/autoloader/venv/bin/autoloader", "--help"]
    )
    assert "COLUMNS" not in runner_78.call_kwargs[console_help_call_index]["env"]

    config_80 = _config(tmp_path / "width-80")
    runner_80 = _FakeBuilder(
        runtime_help_output=help_78_result.output,
        cli_help_output=help_80_result.output,
    )
    fake_tools = tmp_path / "width-80" / "fake-tools"
    fake_tools.mkdir()
    fake_mkarchiso = fake_tools / "mkarchiso"
    _write_executable(fake_mkarchiso, "synthetic mkarchiso executable fixture\n")

    with pytest.raises(build_usb.BuildContractError, match="differs from the installed Click help contract"):
        build_usb.execute_build(
            config_80,
            host_platform="linux",
            mkarchiso_path=str(fake_mkarchiso),
            runner=runner_80,
            mountpoints_reader=lambda: (),
            clock=lambda: 1_800_000_000,
        )

    console_help_call_index = next(
        index
        for index, command in enumerate(runner_80.calls)
        if command[-2:] == ["/opt/autoloader/venv/bin/autoloader", "--help"]
    )
    assert "COLUMNS" not in runner_80.call_kwargs[console_help_call_index]["env"]
    assert not (config_80.output_dir / "candidate-bundle").exists()


def test_fake_builder_rejects_runtime_help_width_mismatch_before_console_command(tmp_path: Path) -> None:
    config = _config(tmp_path)
    runner = _FakeBuilder(runtime_help_width=80)
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_mkarchiso = fake_tools / "mkarchiso"
    _write_executable(fake_mkarchiso, "synthetic mkarchiso executable fixture\n")

    with pytest.raises(build_usb.BuildContractError, match="runtime import evidence"):
        build_usb.execute_build(
            config,
            host_platform="linux",
            mkarchiso_path=str(fake_mkarchiso),
            runner=runner,
            mountpoints_reader=lambda: (),
            clock=lambda: 1_800_000_000,
        )

    assert not any(
        command[-2:] == ["/opt/autoloader/venv/bin/autoloader", "--help"]
        for command in runner.calls
    )
    assert not (config.output_dir / "candidate-bundle").exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux usrmerge executable path contract")
def test_candidate_builder_resolves_usrmerge_mkarchiso_alias_and_binds_canonical_owner(tmp_path):
    config = _config(tmp_path)
    runner = _FakeBuilder()
    usr = tmp_path / "usr"
    canonical_dir = usr / "bin"
    canonical_dir.mkdir(parents=True)
    alias_dir = usr / "sbin"
    alias_dir.symlink_to("bin", target_is_directory=True)
    canonical_mkarchiso = canonical_dir / "mkarchiso"
    _write_executable(canonical_mkarchiso, "synthetic pinned Archiso executable fixture\n")
    alias_mkarchiso = alias_dir / "mkarchiso"

    result = build_usb.execute_build(
        config,
        host_platform="linux",
        mkarchiso_path=str(alias_mkarchiso),
        runner=runner,
        mountpoints_reader=lambda: (),
        clock=lambda: 1_800_000_000,
    )

    canonical_path = str(canonical_mkarchiso.resolve())
    assert ["pacman", "-Qo", canonical_path] in runner.calls
    assert all(str(alias_mkarchiso) not in command for command in runner.calls)
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["host"]["mkarchiso"]["path"] == canonical_path
    assert manifest["host"]["mkarchiso"]["package_owner_output"] == (
        f"{canonical_path} is owned by archiso 91-1"
    )
    assert any(command[0] == canonical_path for command in manifest["commands"])


def _make_rootfs_symlink_or_skip(link: Path, target: str, *, is_directory: bool = False) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(target, link, target_is_directory=is_directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"POSIX-style target-root symlinks unavailable: {exc}")


@pytest.mark.skipif(sys.platform != "linux", reason="target-root uses POSIX symlink semantics")
def test_rootfs_resolver_accepts_internal_usrmerge_and_absolute_guest_path(tmp_path):
    root = tmp_path / "airootfs"
    interpreter = root / "usr/bin/python"
    interpreter.parent.mkdir(parents=True)
    _write_executable(interpreter, "synthetic target interpreter\n")
    _make_rootfs_symlink_or_skip(root / "usr/sbin", "bin", is_directory=True)
    guest_python = root / "opt/autoloader/venv/bin/python"
    _make_rootfs_symlink_or_skip(guest_python, "/usr/sbin/python")

    resolved = build_usb._resolve_rootfs_symlink(root, guest_python, "venv interpreter")

    assert resolved == interpreter
    assert build_usb._require_rootfs_file(
        root,
        "opt/autoloader/venv/bin/python",
        "application venv interpreter",
        executable=True,
        symlink_ok=True,
    ) == interpreter


@pytest.mark.skipif(sys.platform != "linux", reason="target-root uses POSIX symlink semantics")
def test_rootfs_resolver_rejects_symlink_escape_cycle_missing_and_non_directory(tmp_path):
    root = tmp_path / "airootfs"
    root.mkdir()

    escaping = root / "opt/escape"
    _make_rootfs_symlink_or_skip(escaping, "../../outside")
    with pytest.raises(build_usb.BuildContractError, match="escapes the image"):
        build_usb._resolve_rootfs_symlink(root, escaping, "escape fixture")

    cycle_a = root / "opt/cycle-a"
    cycle_b = root / "opt/cycle-b"
    _make_rootfs_symlink_or_skip(cycle_a, "cycle-b")
    _make_rootfs_symlink_or_skip(cycle_b, "cycle-a")
    with pytest.raises(build_usb.BuildContractError, match="cycle|resolution limit"):
        build_usb._resolve_rootfs_symlink(root, cycle_a, "cycle fixture")

    missing = root / "opt/missing"
    _make_rootfs_symlink_or_skip(missing, "not-present")
    with pytest.raises(build_usb.BuildContractError, match="missing"):
        build_usb._resolve_rootfs_symlink(root, missing, "missing fixture")

    regular_file = root / "usr/file"
    _write(regular_file, "not a directory\n")
    non_directory = root / "opt/non-directory"
    _make_rootfs_symlink_or_skip(non_directory, "/usr/file/child")
    with pytest.raises(build_usb.BuildContractError, match="non-directory"):
        build_usb._resolve_rootfs_symlink(root, non_directory, "non-directory fixture")


@pytest.mark.skipif(sys.platform != "linux", reason="Linux Archiso ownership contract")
@pytest.mark.parametrize("owner_mismatch", ["different-path", "different-version"])
def test_candidate_builder_rejects_mismatched_archiso_owner_before_work_or_output(tmp_path, owner_mismatch):
    config = _config(tmp_path)
    fake_mkarchiso = tmp_path / "fake-mkarchiso"
    _write_executable(fake_mkarchiso, "untrusted executable fixture\n")
    base_runner = _FakeBuilder()
    calls: list[list[str]] = []

    def wrong_owner_runner(command, **kwargs):
        command = list(command)
        calls.append(command)
        if command[:2] == ["pacman", "-Qo"]:
            executable = Path(command[-1])
            if executable.name == "unshare":
                return subprocess.CompletedProcess(
                    command, 0, f"{executable} is owned by util-linux 2.42.4-1\n", ""
                )
            if executable.name == "ip":
                return subprocess.CompletedProcess(
                    command, 0, f"{executable} is owned by iproute2 7.2.0-1\n", ""
                )
            owner_path = str(fake_mkarchiso.resolve())
            version = "91-1"
            if owner_mismatch == "different-path":
                owner_path = str(fake_mkarchiso.parent / "other-mkarchiso")
            else:
                version = "90-1"
            return subprocess.CompletedProcess(
                command,
                0,
                f"{owner_path} is owned by archiso {version}\n",
                "",
            )
        return base_runner(command, **kwargs)

    with pytest.raises(build_usb.BuildContractError, match="exactly bind canonical path and pinned package version"):
        build_usb.execute_build(
            config,
            host_platform="linux",
            mkarchiso_path=str(fake_mkarchiso),
            runner=wrong_owner_runner,
            mountpoints_reader=lambda: (),
        )
    assert not any(Path(command[0]).name == "mkarchiso" for command in calls)
    assert list(config.output_dir.iterdir()) == []
    assert list(config.work_parent.iterdir()) == []


def test_offline_candidate_build_uses_only_verified_owned_cache_copy(tmp_path):
    config = _offline_config(tmp_path)
    assert config.offline_repository is not None
    source_archive = config.offline_repository / "extra/os/x86_64/base-1-1-x86_64.pkg.tar.zst"
    assert source_archive.is_file()
    runner = _FakeBuilder(mutate_offline_source_during_build=source_archive)
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_mkarchiso = fake_tools / "mkarchiso"
    _write_executable(fake_mkarchiso, "synthetic mkarchiso executable fixture\n")
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
    _write_executable(fake_mkarchiso, "synthetic mkarchiso executable fixture\n")
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
    _write_executable(fake_mkarchiso, "synthetic mkarchiso executable fixture\n")
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
    _write_executable(fake_mkarchiso, "synthetic mkarchiso executable fixture\n")
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
    _write_executable(fake_mkarchiso, "synthetic mkarchiso executable fixture\n")
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


@pytest.mark.skipif(sys.platform != "linux", reason="target-root POSIX symlink contract")
@pytest.mark.parametrize(
    ("omitted_link", "expected_error"),
    [
        ("autoloader_enablement", "AutoLoader multi-user enablement symlink"),
    ],
)
def test_candidate_build_fails_closed_when_systemd_enablement_link_is_missing(
    tmp_path: Path,
    omitted_link: str,
    expected_error: str,
) -> None:
    config = _config(tmp_path)
    runner = _FakeBuilder(omit_systemd_symlink=omitted_link)
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_mkarchiso = fake_tools / "mkarchiso"
    _write_executable(fake_mkarchiso, "synthetic mkarchiso executable fixture\n")

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


@pytest.mark.skipif(sys.platform != "linux", reason="target-root POSIX symlink contract")
@pytest.mark.parametrize(
    ("actual_target", "expected_target"),
    [
        ("/tmp/host-service.service", "../autoloader.service"),
        ("/etc/systemd/system/another.service", "../autoloader.service"),
    ],
)
def test_target_root_symlink_validator_rejects_wrong_enablement_target(
    tmp_path: Path,
    actual_target: str,
    expected_target: str,
) -> None:
    root = tmp_path / "root"
    relative = "etc/systemd/system/multi-user.target.wants/autoloader.service"
    link = root / relative
    link.parent.mkdir(parents=True)
    link.symlink_to(actual_target)

    with pytest.raises(build_usb.BuildContractError, match="symlink target is unexpected"):
        build_usb._require_rootfs_symlink(root, relative, "fixture systemd", expected_target)


def test_candidate_build_rejects_database_mutated_during_mkarchiso_before_output(tmp_path):
    config = _config(tmp_path)
    runner = _FakeBuilder(mutate_database_after_build=True)
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_mkarchiso = fake_tools / "mkarchiso"
    _write_executable(fake_mkarchiso, "synthetic mkarchiso executable fixture\n")
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


@pytest.mark.parametrize("changed_profile", ["source", "staged"])
def test_archiso_config_mutation_after_preflight_blocks_before_mkarchiso(tmp_path, monkeypatch, changed_profile):
    config = _config(tmp_path)
    runner = _FakeBuilder()
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_mkarchiso = fake_tools / "mkarchiso"
    _write_executable(fake_mkarchiso, "synthetic mkarchiso executable fixture\n")
    original = build_usb._write_image_input_provenance

    def mutate_after_staging(source_root, stage_profile, **kwargs):
        result = original(source_root, stage_profile, **kwargs)
        profile_root = source_root / "usb_builder" / "archiso_profile" if changed_profile == "source" else stage_profile
        altered_config = profile_root / build_usb.ARCHISO_INITRAMFS_CONFIG_RELATIVE_PATH
        _write(altered_config, "HOOKS=(base udev block filesystems)\n")
        return result

    monkeypatch.setattr(build_usb, "_write_image_input_provenance", mutate_after_staging)
    with pytest.raises(build_usb.BuildContractError, match=f"{changed_profile} Archiso initramfs config differs"):
        build_usb.execute_build(
            config,
            host_platform="linux",
            mkarchiso_path=str(fake_mkarchiso),
            runner=runner,
            mountpoints_reader=lambda: (),
            clock=lambda: 1_800_000_000,
        )
    assert sum(Path(call[0]).name == "mkarchiso" for call in runner.calls) == 0
    assert list(config.output_dir.iterdir()) == []


def test_staged_archiso_config_mutation_during_mkarchiso_blocks_publication(tmp_path):
    config = _config(tmp_path)
    runner = _FakeBuilder(mutate_profile_config_after_build=True)
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_mkarchiso = fake_tools / "mkarchiso"
    _write_executable(fake_mkarchiso, "synthetic mkarchiso executable fixture\n")
    with pytest.raises(build_usb.BuildContractError, match="staged Archiso initramfs config differs"):
        build_usb.execute_build(
            config,
            host_platform="linux",
            mkarchiso_path=str(fake_mkarchiso),
            runner=runner,
            mountpoints_reader=lambda: (),
            clock=lambda: 1_800_000_000,
        )
    assert sum(Path(call[0]).name == "mkarchiso" for call in runner.calls) == 1
    assert list(config.output_dir.iterdir()) == []
    assert list(config.output_dir.rglob("*.iso")) == []


@pytest.mark.parametrize("changed_profile", ["source", "staged"])
def test_usb_derivative_mutation_after_preflight_blocks_before_mkarchiso(tmp_path, monkeypatch, changed_profile):
    config = _config(tmp_path)
    runner = _FakeBuilder()
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_mkarchiso = fake_tools / "mkarchiso"
    _write_executable(fake_mkarchiso, "synthetic mkarchiso executable fixture\n")
    original = build_usb._write_image_input_provenance

    def mutate_after_staging(source_root, stage_profile, **kwargs):
        result = original(source_root, stage_profile, **kwargs)
        profile_root = source_root / "usb_builder" / "archiso_profile" if changed_profile == "source" else stage_profile
        altered_derivative = profile_root / build_usb.ARCHISO_USB_INITRAMFS_DERIVATIVE_RELATIVE_PATH
        _write(altered_derivative, "HOOKS=(base udev block filesystems)\n")
        return result

    monkeypatch.setattr(build_usb, "_write_image_input_provenance", mutate_after_staging)
    with pytest.raises(build_usb.BuildContractError, match=f"{changed_profile} USB initramfs derivative"):
        build_usb.execute_build(
            config,
            host_platform="linux",
            mkarchiso_path=str(fake_mkarchiso),
            runner=runner,
            mountpoints_reader=lambda: (),
            clock=lambda: 1_800_000_000,
        )
    assert sum(Path(call[0]).name == "mkarchiso" for call in runner.calls) == 0
    assert list(config.output_dir.iterdir()) == []


def test_staged_usb_derivative_mutation_during_mkarchiso_blocks_publication(tmp_path):
    config = _config(tmp_path)
    runner = _FakeBuilder(mutate_usb_derivative_after_build=True)
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_mkarchiso = fake_tools / "mkarchiso"
    _write_executable(fake_mkarchiso, "synthetic mkarchiso executable fixture\n")
    with pytest.raises(build_usb.BuildContractError, match="staged USB initramfs derivative"):
        build_usb.execute_build(
            config,
            host_platform="linux",
            mkarchiso_path=str(fake_mkarchiso),
            runner=runner,
            mountpoints_reader=lambda: (),
            clock=lambda: 1_800_000_000,
        )
    assert sum(Path(call[0]).name == "mkarchiso" for call in runner.calls) == 1
    assert list(config.output_dir.iterdir()) == []


def test_staged_profile_crlf_mutation_during_mkarchiso_blocks_publication(tmp_path):
    config = _config(tmp_path)
    runner = _FakeBuilder(mutate_profile_crlf_after_build=True)
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_mkarchiso = fake_tools / "mkarchiso"
    _write_executable(fake_mkarchiso, "synthetic mkarchiso executable fixture\n")
    with pytest.raises(build_usb.BuildContractError, match="LF-only line endings"):
        build_usb.execute_build(
            config,
            host_platform="linux",
            mkarchiso_path=str(fake_mkarchiso),
            runner=runner,
            mountpoints_reader=lambda: (),
            clock=lambda: 1_800_000_000,
        )
    assert any(Path(command[0]).name == "mkarchiso" for command in runner.calls)
    assert list(config.output_dir.iterdir()) == []
    assert list(config.output_dir.rglob("*.iso")) == []


def test_usb_derivative_mutation_after_hashing_blocks_publication(tmp_path, monkeypatch):
    config = _config(tmp_path)
    runner = _FakeBuilder()
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_mkarchiso = fake_tools / "mkarchiso"
    _write_executable(fake_mkarchiso, "synthetic mkarchiso executable fixture\n")
    original = build_usb._source_hashes

    def mutate_after_staged_hash(source_root, profile_dir):
        hashes = original(source_root, profile_dir)
        if profile_dir.name == "profile":
            derivative = profile_dir / build_usb.ARCHISO_USB_INITRAMFS_DERIVATIVE_RELATIVE_PATH
            _write(derivative, "HOOKS=(base udev block filesystems)\n")
        return hashes

    monkeypatch.setattr(build_usb, "_source_hashes", mutate_after_staged_hash)
    with pytest.raises(build_usb.BuildContractError, match="staged USB initramfs derivative"):
        build_usb.execute_build(
            config,
            host_platform="linux",
            mkarchiso_path=str(fake_mkarchiso),
            runner=runner,
            mountpoints_reader=lambda: (),
            clock=lambda: 1_800_000_000,
        )
    assert sum(Path(call[0]).name == "mkarchiso" for call in runner.calls) == 1
    assert list(config.output_dir.iterdir()) == []
    assert list(config.output_dir.rglob("*.iso")) == []


def test_offline_build_wraps_every_subprocess_in_verified_network_namespace(tmp_path):
    config = _offline_config(tmp_path)
    runner = _FakeBuilder()
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_mkarchiso = fake_tools / "mkarchiso"
    _write_executable(fake_mkarchiso, "synthetic mkarchiso executable fixture\n")
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
    expected_mode = "user-mapped-root"
    expected_flags = [
        "--user",
        "--map-root-user",
        "--net",
        "--mount",
        "--pid",
        "--fork",
        "--propagation",
        "private",
        "--mount-proc",
    ]
    assert manifest["network_isolation"]["privilege_mode"] == expected_mode
    assert manifest["network_isolation"]["unshare_sha256"] == _sha(fake_unshare)
    assert manifest["network_isolation"]["probe_output"] == build_usb.OFFLINE_NETWORK_PROBE_MARKER
    assert manifest["network_isolation"]["mount_probe_output"].startswith(mount_probe.PROBE_MARKER + "\n{")
    assert manifest["network_isolation"]["mount_probe_command"][1 : 1 + len(expected_flags)] == expected_flags
    assert len(manifest["network_isolation"]["host_mount_table_sha256"]) == 64
    expected_prefix = manifest["network_isolation"]["command_prefix"]
    assert expected_prefix[1 : 1 + len(expected_flags)] == expected_flags
    assert expected_prefix[1 + len(expected_flags) :] == [
        "--",
        sys.executable,
        "-m",
        "usb_builder.network_probe",
        "--run",
        manifest["network_isolation"]["host_namespace_identity"],
        manifest["network_isolation"]["iproute_path"],
    ]
    assert all(
        call[: len(expected_prefix)] == expected_prefix
        for call in runner.calls
        if "--run" in call
    ), "all offline subprocesses must inherit the verified network namespace"
    assert any(Path(call[len(expected_prefix) + 1]).name == "mkarchiso" for call in runner.calls if "--run" in call)
    assert any(call[len(expected_prefix) + 1] == "arch-chroot" for call in runner.calls if "--run" in call)
    assert all(
        command[: len(expected_prefix)] == expected_prefix
        for command in manifest["commands"]
        if "--run" in command
    )
    assert all(
        call_kwargs.get("close_fds") is True
        and call_kwargs.get("stdin") is subprocess.DEVNULL
        for call, call_kwargs in zip(runner.calls, runner.call_kwargs)
        if "--run" in call
    )
    assert all(
        call_kwargs.get("stdin") is subprocess.DEVNULL
        and call_kwargs.get("close_fds") is True
        for call, call_kwargs in zip(runner.calls, runner.call_kwargs)
        if "usb_builder.network_probe" in call and "--run" not in call
    )


@pytest.mark.parametrize("denied_mount", mount_probe.MOUNT_OPERATION_NAMES)
def test_each_denied_archiso_mount_blocks_before_build_or_output(tmp_path, denied_mount):
    config = _config(tmp_path)
    runner = _FakeBuilder(mount_denied=denied_mount)
    tools = tmp_path / "fake-tools"
    tools.mkdir()
    unshare = tools / "unshare"
    mkarchiso = tools / "mkarchiso"
    _write_executable(unshare, "synthetic unshare fixture\n")
    _write_executable(mkarchiso, "synthetic mkarchiso fixture\n")
    with pytest.raises(build_usb.BuildContractError, match="ARCHISO_MOUNT_CAPABILITY_UNAVAILABLE") as error:
        build_usb.execute_build(
            config,
            host_platform="linux",
            mkarchiso_path=str(mkarchiso),
            unshare_path=str(unshare),
            host_namespace_identity="net:[host-fixture]",
            runner=runner,
            mountpoints_reader=lambda: (),
        )
    assert denied_mount in str(error.value)
    assert all(
        not (Path(call[0]).name == "mkarchiso")
        and not (call and call[0] == "pacman")
        and not ("pip" in call and "wheel" in call)
        for call in runner.calls
    )
    assert all(
        any("usb_builder.mount_probe" in token or "usb_builder.network_probe" in token for token in call)
        for call in runner.calls
    )
    assert list(config.output_dir.iterdir()) == []
    assert list(config.work_parent.iterdir()) == []


def test_unexpected_mount_errno_blocks_before_builder_or_output(tmp_path):
    config = _config(tmp_path)
    builder = _FakeBuilder()
    tools = tmp_path / "fake-tools"
    tools.mkdir()
    unshare = tools / "unshare"
    mkarchiso = tools / "mkarchiso"
    _write_executable(unshare, "synthetic unshare fixture\n")
    _write_executable(mkarchiso, "synthetic mkarchiso fixture\n")

    def invalid_mount_errno(command, **kwargs):
        result = builder(command, **kwargs)
        if "usb_builder.mount_probe" not in command:
            return result
        report = json.loads(result.stdout.splitlines()[1])
        proc_attempt = next(item for item in report["mount_attempts"] if item["name"] == "proc")
        proc_attempt.update(status="denied", errno=errno.ENOENT)
        report["status"] = "UNSUPPORTED"
        output = mount_probe.PROBE_MARKER + "\n" + json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"
        return subprocess.CompletedProcess(command, mount_probe.UNSUPPORTED_EXIT, output, "")

    with pytest.raises(build_usb.BuildContractError, match="ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE"):
        build_usb.execute_build(
            config,
            host_platform="linux",
            mkarchiso_path=str(mkarchiso),
            unshare_path=str(unshare),
            host_namespace_identity="net:[host-fixture]",
            runner=invalid_mount_errno,
            mountpoints_reader=lambda: (),
        )
    assert list(config.output_dir.iterdir()) == []
    assert list(config.work_parent.iterdir()) == []
    assert all(
        "usb_builder.mount_probe" in call or "usb_builder.network_probe" in call
        for call in builder.calls
    )


def test_mount_preflight_rejects_malformed_report_and_unexpected_success_exit(tmp_path):
    config = _config(tmp_path)
    runner = _FakeBuilder()
    tools = tmp_path / "fake-tools"
    tools.mkdir()
    unshare = tools / "unshare"
    mkarchiso = tools / "mkarchiso"
    _write_executable(unshare, "synthetic unshare fixture\n")
    _write_executable(mkarchiso, "synthetic mkarchiso fixture\n")
    original_call = runner.__call__

    def malformed(command, **kwargs):
        result = original_call(command, **kwargs)
        if "usb_builder.mount_probe" in command:
            return subprocess.CompletedProcess(command, 0, mount_probe.PROBE_MARKER + "\n{}\n", "")
        return result

    runner_fn = malformed
    with pytest.raises(build_usb.BuildContractError, match="ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE"):
        build_usb.execute_build(
            config,
            host_platform="linux",
            mkarchiso_path=str(mkarchiso),
            unshare_path=str(unshare),
            host_namespace_identity="net:[host-fixture]",
            runner=runner_fn,
            mountpoints_reader=lambda: (),
        )
    assert list(config.output_dir.iterdir()) == []
    assert list(config.work_parent.iterdir()) == []


def test_mount_preflight_rejects_unsupported_report_with_success_exit(tmp_path):
    config = _config(tmp_path)
    runner = _FakeBuilder(mount_denied="devtmpfs")
    original = runner

    def report_success_exit(command, **kwargs):
        result = original(command, **kwargs)
        if "usb_builder.mount_probe" in command:
            return subprocess.CompletedProcess(command, 0, result.stdout, "")
        return result

    tools = tmp_path / "fake-tools"
    tools.mkdir()
    unshare = tools / "unshare"
    mkarchiso = tools / "mkarchiso"
    _write_executable(unshare, "synthetic unshare fixture\n")
    _write_executable(mkarchiso, "synthetic mkarchiso fixture\n")
    with pytest.raises(build_usb.BuildContractError, match="exit/status mismatch"):
        build_usb.execute_build(
            config,
            host_platform="linux",
            mkarchiso_path=str(mkarchiso),
            unshare_path=str(unshare),
            host_namespace_identity="net:[host-fixture]",
            runner=report_success_exit,
            mountpoints_reader=lambda: (),
        )
    assert list(config.output_dir.iterdir()) == []
    assert list(config.work_parent.iterdir()) == []


def test_mount_probe_rechecks_host_mount_table_after_probe(tmp_path):
    runner = _FakeBuilder()
    unshare = tmp_path / "unshare"
    _write_executable(unshare, "synthetic unshare fixture\n")
    snapshots = iter((b"host mountinfo before", b"host mountinfo after"))
    with pytest.raises(build_usb.BuildContractError, match="host mount table changed"):
        build_usb._establish_offline_network_isolation(
            runner,
            unshare_path=str(unshare),
            host_namespace_identity="net:[host-fixture]",
            host_mount_table_reader=lambda: next(snapshots),
        )
    assert len(runner.calls) == 2


def test_mount_probe_rejects_tool_identity_change_during_probe(tmp_path):
    runner = _FakeBuilder()
    unshare = tmp_path / "unshare"
    _write_executable(unshare, "synthetic unshare fixture\n")
    original_call = runner.__call__

    def mutate_tool(command, **kwargs):
        result = original_call(command, **kwargs)
        if "usb_builder.mount_probe" in command:
            unshare.write_text("changed executable fixture\n", encoding="utf-8")
        return result

    runner_fn = mutate_tool
    with pytest.raises(build_usb.BuildContractError, match="tool identity changed"):
        build_usb._establish_offline_network_isolation(
            runner_fn,
            unshare_path=str(unshare),
            host_namespace_identity="net:[host-fixture]",
        )
    assert len(runner.calls) == 2


def test_mount_probe_timeout_rechecks_host_mount_table(tmp_path):
    base = _FakeBuilder()
    unshare = tmp_path / "unshare"
    _write_executable(unshare, "synthetic unshare fixture\n")
    snapshots: list[bytes] = []

    def timeout_runner(command, **kwargs):
        if "usb_builder.mount_probe" in command:
            raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 90))
        return base(command, **kwargs)

    def snapshot():
        snapshots.append(b"unchanged host mountinfo")
        return snapshots[-1]

    with pytest.raises(build_usb.BuildContractError, match="probe error was TimeoutExpired.*rechecked"):
        build_usb._establish_offline_network_isolation(
            timeout_runner,
            unshare_path=str(unshare),
            host_namespace_identity="net:[host-fixture]",
            host_mount_table_reader=snapshot,
        )
    assert len(snapshots) == 2


def test_bounded_mount_probe_kills_process_group_after_leader_exits(monkeypatch):
    events: list[tuple[str, int | None]] = []
    grandchild_alive = True

    class FakeChild:
        pid = 43210
        returncode = 0
        stdout = None
        stderr = None

        def __init__(self):
            self.communicate_calls = 0

        def communicate(self, timeout=None):
            self.communicate_calls += 1
            events.append(("communicate", self.communicate_calls))
            if self.communicate_calls == 1:
                raise subprocess.TimeoutExpired("probe", timeout)
            if self.communicate_calls == 2:
                # Model the group leader exiting on SIGTERM while a grandchild
                # without an inherited pipe remains alive in the same group.
                self.returncode = -int(signal.SIGTERM)
                return "leader exited", ""
            assert not grandchild_alive
            return "reaped", ""

        def wait(self, timeout=None):
            events.append(("wait", None))
            return self.returncode

    child = FakeChild()
    monkeypatch.setattr(build_usb.subprocess, "Popen", lambda *args, **kwargs: child)
    monkeypatch.setattr(build_usb, "MOUNT_PROBE_TERMINATION_GRACE_SECONDS", 0.01)

    def killpg(pid, signum):
        nonlocal grandchild_alive
        events.append(("killpg", signum))
        assert pid == child.pid
        if signum == signal.SIGKILL:
            grandchild_alive = False

    monkeypatch.setattr(build_usb.os, "killpg", killpg, raising=False)
    monkeypatch.setattr(build_usb.signal, "SIGKILL", 9, raising=False)
    with pytest.raises(subprocess.TimeoutExpired):
        build_usb._run_bounded_mount_probe(["unshare", "probe"], runner=subprocess.run)
    assert events == [
        ("communicate", 1),
        ("killpg", signal.SIGTERM),
        ("communicate", 2),
        ("killpg", signal.SIGKILL),
        ("communicate", 3),
    ]
    assert not grandchild_alive


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux process groups and /proc")
def test_bounded_mount_probe_timeout_kills_real_grandchild(monkeypatch, tmp_path):
    pid_path = tmp_path / "probe-grandchild.pid"
    script = "\n".join(
        (
            "import os, signal, sys, time",
            "pid = os.fork()",
            "if pid == 0:",
            "    signal.signal(signal.SIGTERM, signal.SIG_IGN)",
            "    with open(sys.argv[1], 'w', encoding='ascii') as stream: stream.write(str(os.getpid()))",
            "    while True: time.sleep(0.05)",
            "else:",
            "    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))",
            "    while True: time.sleep(0.05)",
        )
    )
    monkeypatch.setattr(build_usb, "MOUNT_PROBE_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(build_usb, "MOUNT_PROBE_TERMINATION_GRACE_SECONDS", 0.1)
    with pytest.raises(subprocess.TimeoutExpired):
        build_usb._run_bounded_mount_probe(
            [sys.executable, "-c", script, str(pid_path)],
            runner=subprocess.run,
        )
    assert pid_path.is_file()
    child_pid = int(pid_path.read_text(encoding="ascii"))

    def executing(pid: int) -> bool:
        try:
            state = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()[0]
        except FileNotFoundError:
            return False
        return state != "Z"

    deadline = time.monotonic() + 2
    while executing(child_pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not executing(child_pid), "mount probe grandchild survived group cleanup"


@pytest.mark.parametrize(
    "unsafe_kwargs",
    [
        {"close_fds": False},
        {"pass_fds": ()},
        {"preexec_fn": lambda: None},
        {"shell": True},
        {"start_new_session": False},
        {"stdin": None},
    ],
)
def test_offline_guard_rejects_unsupported_subprocess_controls_before_runner(
    tmp_path, unsafe_kwargs
):
    runner = _FakeBuilder()
    fake_unshare = tmp_path / "unshare"
    _write_executable(fake_unshare, "synthetic util-linux executable fixture\n")
    isolation = build_usb._establish_offline_network_isolation(
        runner,
        unshare_path=str(fake_unshare),
        host_namespace_identity="net:[host-fixture]",
    )
    guarded = build_usb._network_guarded_runner(runner, isolation)
    calls_before = len(runner.calls)

    with pytest.raises(build_usb.BuildContractError, match="unsupported subprocess controls"):
        guarded(["must-not-run"], **unsafe_kwargs)

    assert len(runner.calls) == calls_before


def test_offline_build_fails_before_creating_work_or_output_without_namespace(tmp_path):
    config = _offline_config(tmp_path)
    runner = _FakeBuilder(namespace_ok=False)
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_unshare = fake_tools / "unshare"
    _write_executable(fake_unshare, "synthetic util-linux executable fixture\n")
    fake_mkarchiso = fake_tools / "mkarchiso"
    _write_executable(fake_mkarchiso, "synthetic mkarchiso executable fixture\n")
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
    unshare = build_usb._canonical_existing_file(Path(shutil.which("unshare") or "").resolve(strict=True), "unshare")
    ip = build_usb._canonical_existing_file(Path(shutil.which("ip") or "").resolve(strict=True), "iproute2")
    try:
        for path, package in ((unshare, "util-linux"), (ip, "iproute2")):
            owner = build_usb._run_command(("pacman", "-Qo", str(path)), runner=subprocess.run).stdout.strip()
            if re.fullmatch(re.escape(str(path)) + rf" is owned by {package} \S+", owner) is None:
                pytest.skip("UNVALIDATED: host lacks trusted Arch pacman executable ownership evidence")
    except (build_usb.BuildContractError, OSError):
        pytest.skip("UNVALIDATED: host lacks trusted Arch pacman executable ownership evidence")
    host_identity = build_usb._current_network_namespace_identity()
    command = [
        str(unshare),
        "--user",
        "--map-root-user",
        "--net",
        "--mount",
        "--pid",
        "--fork",
        "--propagation",
        "private",
        "--mount-proc",
        "--",
        sys.executable,
        "-m",
        "usb_builder.network_probe",
        host_identity,
        str(ip),
    ]
    result = subprocess.run(
        command,
        check=False,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=build_usb._offline_child_environment(None),
        close_fds=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout
    assert build_usb._validate_network_probe_output(
        result.stdout,
        host_namespace_identity=host_identity,
        allow_synthetic=False,
    )
    report = json.loads(result.stdout.splitlines()[1])
    assert report["namespace_id"] != host_identity
    assert report["namespace_interfaces"] == ["lo"]
    assert report["ipv4_netlink_route_count"] == report["ipv6_netlink_route_count"] == 0


@pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("unshare") is None,
    reason="UNVALIDATED: host lacks Linux util-linux unshare",
)
def test_real_production_preflight_reports_mount_gate_after_route_probe(monkeypatch):
    network_reports: list[dict[str, Any]] = []
    host_mount_snapshots: list[bytes] = []
    validate_network = build_usb._validate_network_probe_output
    read_mount_table = build_usb._host_mount_table_bytes

    def capture_network(output, **kwargs):
        valid = validate_network(output, **kwargs)
        if valid:
            network_reports.append(json.loads(output.splitlines()[1]))
        return valid

    def capture_mount_table():
        result = read_mount_table()
        host_mount_snapshots.append(result)
        return result

    monkeypatch.setattr(build_usb, "_validate_network_probe_output", capture_network)
    monkeypatch.setattr(build_usb, "_host_mount_table_bytes", capture_mount_table)
    try:
        isolation = build_usb._establish_offline_network_isolation(subprocess.run)
    except build_usb.BuildContractError as exc:
        if "trusted Arch pacman/util-linux ownership prerequisite" in str(exc):
            pytest.skip("UNVALIDATED: host lacks trusted Arch pacman executable ownership evidence")
        error_text = str(exc)
        if "ARCHISO_MOUNT_CAPABILITY_UNAVAILABLE" in error_text:
            assert "OFFLINE_NETWORK_ISOLATION_UNAVAILABLE" not in error_text
        elif (
            "ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE" in error_text
            and "mount operation tmpfs-run failed with unexpected errno 22" in error_text
        ):
            # The stricter errno classifier intentionally surfaces this current
            # WSL result as an integrity issue pending source/option diagnosis.
            assert "OFFLINE_NETWORK_ISOLATION_UNAVAILABLE" not in error_text
        else:
            raise
    else:
        assert isolation.mode == "linux-network-namespace"
        parsed = build_usb._validate_mount_probe_output(
            isolation.mount_probe_output,
            host_namespace_ids={
                "user": isolation.host_user_namespace_identity,
                "net": isolation.host_namespace_identity,
                "mnt": isolation.host_mount_namespace_identity,
                "pid": isolation.host_pid_namespace_identity,
            },
            host_net_namespace=isolation.host_namespace_identity,
            allow_synthetic=False,
        )
        assert parsed is not None and parsed[0] == "SUPPORTED"

    assert len(network_reports) == 1
    report = network_reports[0]
    assert report["namespace_id"] != build_usb._current_network_namespace_identity()
    assert report["namespace_interfaces"] == ["lo"]
    assert report["ipv4_netlink_route_count"] == report["ipv6_netlink_route_count"] == 0
    assert report["ipv6_null_unreachable_proc_rows"] in (0, 2)
    assert len(host_mount_snapshots) == 2
    assert host_mount_snapshots[0] == host_mount_snapshots[1]


@pytest.mark.parametrize(
    "is_root",
    [
        True,
        False,
    ],
)
def test_offline_namespace_always_uses_user_mapped_netns(tmp_path, is_root):
    fake_unshare = tmp_path / "unshare"
    _write_executable(fake_unshare, "synthetic util-linux executable fixture\n")
    runner = _FakeBuilder()
    isolation = build_usb._establish_offline_network_isolation(
        runner,
        unshare_path=str(fake_unshare),
        host_namespace_identity="net:[host-fixture]",
        is_root=is_root,
    )
    detected_ip = shutil.which("ip")
    expected_ip = str(Path(detected_ip).resolve(strict=True)) if detected_ip else "ip"
    assert isolation.privilege_mode == "user-mapped-root"
    assert isolation.command_prefix == (
        str(fake_unshare.resolve()),
        "--user",
        "--map-root-user",
        "--net",
        "--mount",
        "--pid",
        "--fork",
        "--propagation",
        "private",
        "--mount-proc",
        "--",
        sys.executable,
        "-m",
        "usb_builder.network_probe",
        "--run",
        "net:[host-fixture]",
        expected_ip,
    )


def test_offline_child_environment_removes_loader_python_and_shell_injection():
    environment = build_usb._offline_child_environment(
        {
            "PATH": "/usr/bin",
            "HOME": "/tmp/home",
            "PYTHONPATH": "/attacker/python",
            "PYTHONHOME": "/attacker/python-home",
            "LD_PRELOAD": "/attacker/preload.so",
            "LD_LIBRARY_PATH": "/attacker/lib",
            "BASH_ENV": "/attacker/bashrc",
            "ENV": "/attacker/shrc",
            "PIP_NO_INDEX": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    assert environment == {
        "PATH": "/usr/bin",
        "HOME": "/tmp/home",
        "PIP_NO_INDEX": "1",
        "PYTHONNOUSERSITE": "1",
    }


def _valid_namespace_views(**overrides):
    values = {
        "host_namespace": "net:[100]",
        "child_namespace": "net:[101]",
        "sysfs_interfaces": ("eth0", "lo"),
        "socket_interfaces": ((1, "lo"),),
        "link_rows": ({"ifindex": 1, "ifname": "lo", "flags": ["LOOPBACK"], "link_type": "loopback"},),
        "address_rows": ({"ifname": "lo", "addr_info": []},),
        "ipv4_routes": (),
        "ipv6_routes": (),
        "ipv4_rules": network_probe.IPV4_DEFAULT_RULES,
        "ipv6_rules": network_probe.IPV6_DEFAULT_RULES,
        "proc_dev": "Inter-| Receive | Transmit\n face | bytes\n lo: 0\n",
        "proc_ipv4_routes": "",
        "proc_ipv6_routes": "\n".join(" ".join(network_probe.IPV6_NULL_UNREACHABLE_ROW) for _ in range(2)) + "\n",
        "proc_if_inet6": "",
    }
    values.update(overrides)
    return values


def test_namespace_probe_accepts_only_the_exact_two_row_ipv6_null_sentinel():
    exact = " ".join(network_probe.IPV6_NULL_UNREACHABLE_ROW)
    assert len(exact.split()) == 10
    assert network_probe.validate_ipv6_proc_routes("") == 0
    assert network_probe.validate_ipv6_proc_routes(f"{exact}\n{exact}\n") == 2
    for invalid in (
        f"{exact}\n",  # unsupported count
        f"{exact}\n{exact}\n{exact}\n",  # unsupported count
        exact.replace("00200200", "00200000") + "\n" + exact,  # not RTF_REJECT
        exact.replace(" lo", " eth0") + "\n" + exact,  # wrong interface
        exact + " unexpected\n" + exact,  # extra token
        exact.replace("ffffffff", "00000000") + "\n" + exact,  # wrong metric
    ):
        with pytest.raises(network_probe.ProbeError):
            network_probe.validate_ipv6_proc_routes(invalid)


def test_namespace_probe_accepts_wsl_stale_sysfs_only_with_clean_namespace_views():
    result = network_probe.validate_namespace_views(**_valid_namespace_views())
    assert result["namespace_interfaces"] == ["lo"]
    assert result["sysfs_interfaces_observed"] == ["eth0", "lo"]
    assert result["ipv6_null_unreachable_proc_rows"] == 2


@pytest.mark.parametrize(
    "mutation",
    [
        {"child_namespace": "net:[100]"},
        {"socket_interfaces": ((1, "lo"), (2, "eth0"))},
        {"link_rows": ({"ifindex": 1, "ifname": "lo", "flags": ["LOOPBACK"], "link_type": "loopback"}, {"ifindex": 2, "ifname": "eth0", "flags": [], "link_type": "ether"})},
        {"address_rows": ({"ifname": "lo", "addr_info": [{"local": "127.0.0.2"}]},)},
        {"ipv4_routes": ({"dst": "default", "gateway": "192.0.2.1"},)},
        {"ipv6_routes": ({"dst": "default", "gateway": "2001:db8::1"},)},
        {"ipv4_rules": (*network_probe.IPV4_DEFAULT_RULES, {"priority": 100, "src": "all", "table": 100})},
        {"ipv6_rules": (*network_probe.IPV6_DEFAULT_RULES, {"priority": 100, "src": "all", "table": 100})},
        {"proc_dev": "Inter-| Receive | Transmit\n face | bytes\n lo: 0\n eth0: 0\n"},
        {"proc_ipv4_routes": "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\neth0 00000000 0100007F 0003 0 0 0 00000000 0 0 0\n"},
        {"proc_ipv6_routes": " ".join(network_probe.IPV6_NULL_UNREACHABLE_ROW).replace("00200200", "00200000")},
        {"proc_if_inet6": "00000000000000000000000000000001 01 80 10 80 lo\n"},
        {"proc_dev": None},
        {"sysfs_interfaces": (1, "lo")},
        {"link_rows": ({"ifindex": 1, "ifname": "lo", "flags": None, "link_type": "loopback"},)},
        {"link_rows": ({"ifindex": True, "ifname": "lo", "flags": ["LOOPBACK"], "link_type": "loopback"},)},
    ],
)
def test_namespace_probe_rejects_any_non_loopback_or_unexpected_route_view(mutation):
    with pytest.raises(network_probe.ProbeError):
        network_probe.validate_namespace_views(**_valid_namespace_views(**mutation))


@pytest.mark.parametrize("stdout", ["not-json", "null", "{}", "[1]"])
def test_namespace_probe_rejects_malformed_iproute_json(monkeypatch, stdout):
    monkeypatch.setattr(
        network_probe.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout, ""),
    )
    with pytest.raises(network_probe.ProbeError):
        network_probe._run_ip("/usr/bin/ip", "-j", "link", "show")


def test_iproute_probe_disconnects_inherited_stdin(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "[]", "")

    monkeypatch.setattr(network_probe.subprocess, "run", fake_run)
    assert network_probe._run_ip("/usr/bin/ip", "-j", "link", "show") == []
    assert calls[0][1]["stdin"] is subprocess.DEVNULL
    assert calls[0][1]["close_fds"] is True


def _valid_probe_report(host_namespace="net:[100]", child_namespace="net:[123]"):
    return build_usb.OFFLINE_NETWORK_PROBE_MARKER + "\n" + json.dumps(
        {
            "namespace_id": child_namespace,
            "sysfs_interfaces_observed": ["eth0", "lo"],
            "namespace_interfaces": ["lo"],
            "ipv4_netlink_route_count": 0,
            "ipv6_netlink_route_count": 0,
            "ipv4_rule_priorities": [0, 32766, 32767],
            "ipv6_rule_priorities": [0, 32766],
            "ipv6_null_unreachable_proc_rows": 2,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _valid_mount_probe_output(
    *, denied: str | None = None, dependent_state: str = "blocked", denied_errno: int = errno.EPERM
) -> str:
    host_ids = {kind: f"{kind}:[{100 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    child_ids = {kind: f"{kind}:[{200 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    dependent = {"devpts-gid-5", "dev-shm-tmpfs"} | {
        name for name in mount_probe.MOUNT_OPERATION_NAMES if name.startswith("device-bind-")
    }
    devtmpfs_denied = denied == "devtmpfs"
    run_bind_denied = denied == "run-bind"
    attempts = []
    for name in mount_probe.MOUNT_OPERATION_NAMES:
        if name == denied:
            attempts.append({"name": name, "status": "denied", "errno": denied_errno})
        elif devtmpfs_denied and name in dependent:
            attempts.append({"name": name, "status": dependent_state, "errno": None})
        elif run_bind_denied and name == "run-bind-private":
            attempts.append({"name": name, "status": dependent_state, "errno": None})
        elif name == "run-bind-private":
            attempts.append({"name": name, "status": "applied", "errno": None})
        else:
            attempts.append({"name": name, "status": "mounted", "errno": None})
    report = {
        "schema_version": 1,
        "status": "UNSUPPORTED" if denied is not None else "SUPPORTED",
        "namespace_ids": child_ids,
        "propagation_private": True,
        "network": {
            "namespace_id": child_ids["net"],
            "sysfs_interfaces_observed": ["lo"],
            "namespace_interfaces": ["lo"],
            "ipv4_netlink_route_count": 0,
            "ipv6_netlink_route_count": 0,
            "ipv4_rule_priorities": [0, 32766, 32767],
            "ipv6_rule_priorities": [0, 32766],
            "ipv6_null_unreachable_proc_rows": 0,
        },
        "mount_attempts": attempts,
        "mounts_cleaned": True,
        "probe_owned_sources_only": True,
        "host_dev_or_run_bound": False,
    }
    return mount_probe.PROBE_MARKER + "\n" + json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"


def test_mount_probe_parser_accepts_complete_report_and_derives_unsupported_from_denied_prerequisite():
    host_ids = {kind: f"{kind}:[{100 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    supported = mount_probe.validate_mount_probe_output(
        _valid_mount_probe_output(),
        host_namespace_ids=host_ids,
        host_net_namespace=host_ids["net"],
        allow_synthetic=True,
    )
    assert supported is not None and supported[0] == "SUPPORTED"
    assert [item["name"] for item in supported[1]["mount_attempts"]] == list(mount_probe.MOUNT_OPERATION_NAMES)

    unsupported_output = _valid_mount_probe_output(denied="devtmpfs", dependent_state="blocked")
    unsupported = mount_probe.validate_mount_probe_output(
        unsupported_output,
        host_namespace_ids=host_ids,
        host_net_namespace=host_ids["net"],
        allow_synthetic=True,
    )
    assert unsupported is not None and unsupported[0] == "UNSUPPORTED"
    assert all(
        item["status"] == "blocked"
        for item in unsupported[1]["mount_attempts"]
        if item["name"] in {"devpts-gid-5", "dev-shm-tmpfs"}
        or item["name"].startswith("device-bind-")
    )

    # `not_attempted` is an explicit equivalent for a dependent syscall that
    # cannot run after devtmpfs denial; ordering and overall status remain fixed.
    not_attempted = mount_probe.validate_mount_probe_output(
        _valid_mount_probe_output(denied="devtmpfs", dependent_state="not_attempted"),
        host_namespace_ids=host_ids,
        host_net_namespace=host_ids["net"],
        allow_synthetic=True,
    )
    assert not_attempted is not None and not_attempted[0] == "UNSUPPORTED"
    run_bind_not_attempted = mount_probe.validate_mount_probe_output(
        _valid_mount_probe_output(denied="run-bind", dependent_state="not_attempted"),
        host_namespace_ids=host_ids,
        host_net_namespace=host_ids["net"],
        allow_synthetic=True,
    )
    assert run_bind_not_attempted is not None and run_bind_not_attempted[0] == "UNSUPPORTED"


@pytest.mark.parametrize("denied", mount_probe.MOUNT_OPERATION_NAMES)
def test_mount_probe_parser_accepts_each_real_denial_as_unsupported(denied):
    host_ids = {kind: f"{kind}:[{100 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    parsed = mount_probe.validate_mount_probe_output(
        _valid_mount_probe_output(denied=denied),
        host_namespace_ids=host_ids,
        host_net_namespace=host_ids["net"],
        allow_synthetic=True,
    )
    assert parsed is not None and parsed[0] == "UNSUPPORTED"


@pytest.mark.parametrize(
    ("operation", "error_number"),
    [
        ("proc", errno.EPERM),
        ("proc", errno.EACCES),
        ("proc", errno.ENODEV),
        ("proc", errno.EOPNOTSUPP),
        ("run-bind", errno.EPERM),
        ("run-bind", errno.EACCES),
    ],
)
def test_mount_probe_parser_accepts_only_operation_valid_capability_denials(operation, error_number):
    host_ids = {kind: f"{kind}:[{100 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    output = _valid_mount_probe_output(denied=operation, denied_errno=error_number)
    parsed = mount_probe.validate_mount_probe_output(
        output,
        host_namespace_ids=host_ids,
        host_net_namespace=host_ids["net"],
        allow_synthetic=True,
    )
    assert parsed is not None and parsed[0] == "UNSUPPORTED"


@pytest.mark.parametrize(
    ("operation", "error_number"),
    [
        ("proc", errno.ENOENT),
        ("proc", errno.EFAULT),
        ("proc", errno.EBADF),
        ("proc", errno.EIO),
        ("proc", errno.EINVAL),
        ("run-bind", errno.ENODEV),
        ("run-bind", errno.EOPNOTSUPP),
        ("run-bind", errno.EINVAL),
    ],
)
def test_mount_probe_parser_rejects_unexpected_errno_as_non_capability(operation, error_number):
    host_ids = {kind: f"{kind}:[{100 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    output = _valid_mount_probe_output(denied=operation, denied_errno=error_number)
    assert mount_probe.validate_mount_probe_output(
        output,
        host_namespace_ids=host_ids,
        host_net_namespace=host_ids["net"],
        allow_synthetic=True,
    ) is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda report: report["mount_attempts"].reverse(),
        lambda report: report["mount_attempts"][0].update(status="blocked", errno=None),
        lambda report: report["mount_attempts"][4].update(status="mounted", errno=None),
        lambda report: report["mount_attempts"][3].update(status="mounted", errno=None),
        lambda report: report["mount_attempts"][5].update(status="denied", errno=1),
        lambda report: report.update(status="SUPPORTED"),
    ],
)
def test_mount_probe_parser_rejects_false_success_or_invalid_dependency_states(mutate):
    host_ids = {kind: f"{kind}:[{100 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    report = json.loads(_valid_mount_probe_output(denied="devtmpfs").splitlines()[1])
    mutate(report)
    output = mount_probe.PROBE_MARKER + "\n" + json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"
    assert mount_probe.validate_mount_probe_output(
        output,
        host_namespace_ids=host_ids,
        host_net_namespace=host_ids["net"],
        allow_synthetic=True,
    ) is None


def test_mount_probe_parser_rejects_dependency_block_without_denial_and_false_run_bind_success():
    host_ids = {kind: f"{kind}:[{100 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    report = json.loads(_valid_mount_probe_output().splitlines()[1])
    report["mount_attempts"][4].update(status="blocked", errno=None)
    output = mount_probe.PROBE_MARKER + "\n" + json.dumps(report, sort_keys=True, separators=(",", ":"))
    assert mount_probe.validate_mount_probe_output(
        output,
        host_namespace_ids=host_ids,
        host_net_namespace=host_ids["net"],
        allow_synthetic=True,
    ) is None

    report = json.loads(_valid_mount_probe_output(denied="run-bind").splitlines()[1])
    report["mount_attempts"][7].update(status="applied", errno=None)
    output = mount_probe.PROBE_MARKER + "\n" + json.dumps(report, sort_keys=True, separators=(",", ":"))
    assert mount_probe.validate_mount_probe_output(
        output,
        host_namespace_ids=host_ids,
        host_net_namespace=host_ids["net"],
        allow_synthetic=True,
    ) is None


def test_mount_plan_passes_vfs_options_as_mount_flags_not_tmpfs_data():
    source = Path("/probe/sources")
    rootfs = Path("/probe/rootfs")
    plan = {item[0]: item[1:] for item in mount_probe._mount_plan({"source": source, "rootfs": rootfs})}

    assert plan["devpts-gid-5"] == (
        "devpts",
        rootfs / "dev" / "pts",
        "devpts",
        mount_probe.MS_NOSUID | mount_probe.MS_NOEXEC,
        "mode=0620,gid=5",
    )
    assert plan["dev-shm-tmpfs"] == (
        "shm",
        rootfs / "dev" / "shm",
        "tmpfs",
        mount_probe.MS_NOSUID | mount_probe.MS_NODEV,
        "mode=1777",
    )
    assert plan["tmpfs-run"] == (
        "run",
        rootfs / "run-tmpfs",
        "tmpfs",
        mount_probe.MS_NOSUID | mount_probe.MS_NODEV,
        "mode=0755",
    )
    assert plan["tmpfs-tmp"] == (
        "tmp",
        rootfs / "tmp",
        "tmpfs",
        mount_probe.MS_NOSUID | mount_probe.MS_NODEV | mount_probe.MS_STRICTATIME,
        "mode=1777",
    )


@pytest.mark.parametrize(
    ("denied_label", "denied_errno"),
    [
        (None, None),
        ("devtmpfs", errno.EPERM),
        ("devtmpfs", errno.EACCES),
        ("devtmpfs", errno.ENODEV),
        ("devtmpfs", errno.EOPNOTSUPP),
        ("run-bind", errno.EPERM),
        ("run-bind", errno.EACCES),
    ],
)
def test_mount_probe_fake_syscalls_report_dependencies_and_cleanup(denied_label, denied_errno, tmp_path):
    host_ids = {kind: f"{kind}:[{100 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    child_ids = {kind: f"{kind}:[{200 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    labels = list(mount_probe.MOUNT_OPERATION_NAMES)
    successful_targets: set[str] = set()
    calls: list[tuple[str, str | None, str | None, int, str | None]] = []
    unmounted: list[str] = []
    next_label_index = 0
    devtmpfs_failed = False
    run_bind_failed = False
    network = {
        "namespace_id": child_ids["net"],
        "sysfs_interfaces_observed": ["lo"],
        "namespace_interfaces": ["lo"],
        "ipv4_netlink_route_count": 0,
        "ipv6_netlink_route_count": 0,
        "ipv4_rule_priorities": [0, 32766, 32767],
        "ipv6_rule_priorities": [0, 32766],
        "ipv6_null_unreachable_proc_rows": 0,
    }

    def namespace_reader():
        return child_ids

    def mountinfo_reader():
        rows = [("1", "0", "0:1", "/", "/", "rw", "-", "ext4", "/dev/root", "rw")]
        for index, target in enumerate(sorted(successful_targets), start=2):
            rows.append((str(index), "1", "0:2", "/", target, "rw", "-", "tmpfs", "tmpfs", "rw"))
        return rows

    def mount_call(source, target, filesystem, flags, data):
        nonlocal next_label_index, devtmpfs_failed, run_bind_failed
        dependent = {"devpts-gid-5", "dev-shm-tmpfs"} | {
            name for name in labels if name.startswith("device-bind-")
        }
        while True:
            label = labels[next_label_index]
            if devtmpfs_failed and label in dependent:
                next_label_index += 1
                continue
            if run_bind_failed and label == "run-bind-private":
                next_label_index += 1
                continue
            break
        next_label_index += 1
        calls.append((target, source, filesystem, flags, data))
        if label == denied_label:
            devtmpfs_failed = label == "devtmpfs"
            run_bind_failed = label == "run-bind"
            raise OSError(denied_errno, "synthetic capability denial", target)
        if label in {"devpts-gid-5", "dev-shm-tmpfs"} or label.startswith("device-bind-"):
            assert Path(target).exists(), "dependent targets must exist inside mounted devtmpfs"
        if label != "run-bind-private":
            successful_targets.add(target)

    def umount_call(target, _flags):
        assert target in successful_targets
        successful_targets.remove(target)
        unmounted.append(target)

    report = mount_probe.run_mount_capability_probe(
        host_ids,
        host_ids["net"],
        "/synthetic/ip",
        mount_call=mount_call,
        umount_call=umount_call,
        namespace_reader=namespace_reader,
        mountinfo_reader=mountinfo_reader,
        network_reader=lambda *_args: network,
        temp_root=str(tmp_path),
    )
    assert [item["name"] for item in report["mount_attempts"]] == labels
    assert report["mounts_cleaned"] is True
    assert successful_targets == set()
    assert len(unmounted) == len(set(unmounted))
    assert sum(Path(path).name == "run" for path in unmounted) == (0 if denied_label == "run-bind" else 1)
    serialized = mount_probe.PROBE_MARKER + "\n" + json.dumps(report, sort_keys=True, separators=(",", ":"))
    parsed = mount_probe.validate_mount_probe_output(
        serialized,
        host_namespace_ids=host_ids,
        host_net_namespace=host_ids["net"],
        allow_synthetic=True,
    )
    assert parsed is not None and parsed[0] == report["status"]
    if denied_label is None:
        assert report["status"] == "SUPPORTED"
        assert all(
            item["status"] == ("applied" if item["name"] == "run-bind-private" else "mounted")
            for item in report["mount_attempts"]
        )
        assert len(calls) == len(labels)
    else:
        assert report["status"] == "UNSUPPORTED"
        assert next(item for item in report["mount_attempts"] if item["name"] == denied_label)["errno"] == denied_errno
        blocked_names = {
            item["name"] for item in report["mount_attempts"] if item["status"] == "blocked"
        }
        if denied_label == "devtmpfs":
            assert report["mount_attempts"][labels.index("devtmpfs")]["status"] == "denied"
            dependent = {"devpts-gid-5", "dev-shm-tmpfs"} | {
                name for name in labels if name.startswith("device-bind-")
            }
            assert blocked_names == dependent
            assert len(calls) == len(labels) - len(dependent)
        else:
            assert report["mount_attempts"][labels.index("run-bind")]["status"] == "denied"
            assert blocked_names == {"run-bind-private"}
            assert len(calls) == len(labels) - 1


@pytest.mark.parametrize(
    "unexpected_errno",
    [errno.ENOENT, errno.EFAULT, errno.EBADF, errno.EIO, errno.EINVAL],
)
def test_mount_probe_unexpected_syscall_errno_is_integrity_failure_and_cleans_up(unexpected_errno, tmp_path):
    host_ids = {kind: f"{kind}:[{100 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    child_ids = {kind: f"{kind}:[{200 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    network = {
        "namespace_id": child_ids["net"],
        "sysfs_interfaces_observed": ["lo"],
        "namespace_interfaces": ["lo"],
        "ipv4_netlink_route_count": 0,
        "ipv6_netlink_route_count": 0,
        "ipv4_rule_priorities": [0, 32766, 32767],
        "ipv6_rule_priorities": [0, 32766],
        "ipv6_null_unreachable_proc_rows": 0,
    }
    mounted: set[str] = set()
    called_targets: list[str] = []
    unmounted: list[str] = []

    def mount_call(source, target, filesystem, flags, data):
        called_targets.append(target)
        if filesystem == "proc":
            raise OSError(unexpected_errno, "synthetic malformed-target or I/O failure", target)
        mounted.add(target)

    def mountinfo_reader():
        rows = [("1", "0", "0:1", "/", "/", "rw", "-", "ext4", "root", "rw")]
        rows.extend(
            (str(index + 2), "1", "0:2", "/", target, "rw", "-", "tmpfs", "tmpfs", "rw")
            for index, target in enumerate(sorted(mounted))
        )
        return rows

    def umount_call(target, _flags):
        assert target in mounted
        mounted.remove(target)
        unmounted.append(target)

    with pytest.raises(mount_probe.MountProbeIntegrityError, match=f"unexpected errno {unexpected_errno}"):
        mount_probe.run_mount_capability_probe(
            host_ids,
            host_ids["net"],
            "/synthetic/ip",
            mount_call=mount_call,
            umount_call=umount_call,
            namespace_reader=lambda: child_ids,
            mountinfo_reader=mountinfo_reader,
            network_reader=lambda *_args: network,
            temp_root=str(tmp_path),
        )
    assert len(called_targets) == 2  # own root bind, then proc fails
    assert mounted == set()
    assert unmounted == [called_targets[0]]
    assert list(tmp_path.iterdir()) == []


def test_devtmpfs_child_creation_is_idempotent_for_populated_devices(tmp_path, monkeypatch):
    rootfs = tmp_path / "rootfs"
    dev = rootfs / "dev"
    (dev / "pts").mkdir(parents=True)
    (dev / "shm").mkdir()
    targets = {dev / name for name in ("full", "null", "random", "tty", "urandom", "zero")}
    real_lstat = Path.lstat

    def populated_lstat(path):
        if path in targets:
            return SimpleNamespace(st_mode=stat.S_IFCHR | 0o666)
        return real_lstat(path)

    def unexpected_touch(_path, *_args, **_kwargs):
        raise AssertionError("existing devtmpfs device entries must not be touched")

    monkeypatch.setattr(Path, "lstat", populated_lstat)
    monkeypatch.setattr(Path, "touch", unexpected_touch)
    mount_probe._create_devtmpfs_children(rootfs)
    assert (dev / "pts").is_dir() and (dev / "shm").is_dir()


def test_devtmpfs_child_creation_rejects_non_directory_without_mutation(tmp_path):
    rootfs = tmp_path / "rootfs"
    dev = rootfs / "dev"
    dev.mkdir(parents=True)
    unexpected = dev / "pts"
    unexpected.write_text("keep", encoding="ascii")
    before = unexpected.stat().st_mtime_ns
    with pytest.raises(mount_probe.MountProbeIntegrityError, match="not a real directory"):
        mount_probe._create_devtmpfs_children(rootfs)
    assert unexpected.read_text(encoding="ascii") == "keep"
    assert unexpected.stat().st_mtime_ns == before


@pytest.mark.parametrize("failure", ["unchanged_namespace", "shared_mount_propagation", "network_drift"])
def test_mount_probe_rejects_namespace_or_route_evidence_before_mount_calls(failure, tmp_path):
    host_ids = {kind: f"{kind}:[{100 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    child_ids = {kind: f"{kind}:[{200 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    if failure == "unchanged_namespace":
        child_ids["mnt"] = host_ids["mnt"]
    calls = []
    report = {
        "namespace_id": child_ids["net"],
        "sysfs_interfaces_observed": ["lo"],
        "namespace_interfaces": ["lo"],
        "ipv4_netlink_route_count": 0,
        "ipv6_netlink_route_count": 0,
        "ipv4_rule_priorities": [0, 32766, 32767],
        "ipv6_rule_priorities": [0, 32766],
        "ipv6_null_unreachable_proc_rows": 0,
    }
    network_calls = 0

    def network_reader(*_args):
        nonlocal network_calls
        network_calls += 1
        if failure == "network_drift" and network_calls == 2:
            return {**report, "ipv4_netlink_route_count": 1}
        return report

    def mountinfo_reader():
        fields = ["1", "0", "0:1", "/", "/", "rw", "-", "ext4", "/dev/root", "rw"]
        if failure == "shared_mount_propagation":
            fields[6:6] = ["shared:1"]
        return [tuple(fields)]

    with pytest.raises(mount_probe.MountProbeIntegrityError):
        mount_probe.run_mount_capability_probe(
            host_ids,
            host_ids["net"],
            "/synthetic/ip",
            mount_call=lambda *args: calls.append(args),
            umount_call=lambda *_args: None,
            namespace_reader=lambda: child_ids,
            mountinfo_reader=mountinfo_reader,
            network_reader=network_reader,
            temp_root=str(tmp_path),
        )
    if failure == "network_drift":
        assert calls
    else:
        assert calls == []
    assert list(tmp_path.iterdir()) == []


def _rootless_network_report(net_id: str) -> dict[str, Any]:
    return {
        "namespace_id": net_id,
        "sysfs_interfaces_observed": ["eth0", "lo"],
        "namespace_interfaces": ["lo"],
        "ipv4_netlink_route_count": 0,
        "ipv6_netlink_route_count": 0,
        "ipv4_rule_priorities": [0, 32766, 32767],
        "ipv6_rule_priorities": [0, 32766],
        "ipv6_null_unreachable_proc_rows": 2,
    }


def _fake_rootless_device(path: Path, major: int, minor: int) -> dict[str, Any]:
    return {
        "path": path.as_posix(),
        "kind": "character-device",
        "major": major,
        "minor": minor,
        "owner_uid": 0,
        "owner_gid": 0,
        "mode": 0o666,
        "st_dev": 7,
        "st_ino": major * 100 + minor + 1,
        "st_rdev": (major << 8) | minor,
    }


def _fake_rootless_parent_sources(
    uid_map: list[list[int]] | None = None,
    gid_map: list[list[int]] | None = None,
) -> dict[str, Any]:
    if uid_map is None:
        uid_map = [[0, 1000, 1], [1, 100000, 65536]]
    if gid_map is None:
        gid_map = [[0, 1000, 1], [1, 100000, 65536]]
    return {
        "schema_version": 1,
        "overflow_uid": 65534,
        "overflow_gid": 65534,
        "uid_map": uid_map,
        "gid_map": gid_map,
        "dev_directory": {
            "path": "/dev", "kind": "directory", "owner_uid": 0, "owner_gid": 0,
            "mode": 0o755, "st_dev": 1, "st_ino": 3,
        },
        "device_sources": [
            _fake_rootless_device(Path(path), major, minor)
            for _name, path, major, minor in rootless_mount_probe.DEVICE_NODES
        ],
        "sys_source": {
            "path": "/sys", "kind": "directory", "owner_uid": 0, "owner_gid": 0,
            "mode": 0o755, "st_dev": 1, "st_ino": 2,
        },
    }


def _fake_child_owner(_path: Path, _parent: Mapping[str, Any], expected: Mapping[str, int]) -> dict[str, int]:
    return dict(expected)


def _fake_id_map_reader(parent_sources: Mapping[str, Any]):
    def read(path: Path) -> list[list[int]]:
        return parent_sources["uid_map" if path.name == "uid_map" else "gid_map"]

    return read


def _fake_overflow_reader(parent_sources: Mapping[str, Any]):
    return lambda name: parent_sources["overflow_uid" if name == "overflowuid" else "overflow_gid"]


def _fake_rootless_report(
    *,
    host_ids: dict[str, str],
    denied: str | None = None,
    denied_errno: int = errno.EPERM,
) -> dict[str, Any]:
    child_ids = {kind: f"{kind}:[{300 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    attempts: list[dict[str, Any]] = []
    root_denied = denied == "target-root-self-bind"
    dev_denied = denied == "tmpfs-dev"
    root_done = not root_denied
    dev_done = root_done and not dev_denied
    for name in rootless_mount_probe.MOUNT_OPERATION_NAMES:
        if root_denied and name != "target-root-self-bind":
            attempts.append({"name": name, "status": "blocked", "errno": None})
        elif dev_denied and name.startswith("device-bind-"):
            attempts.append({"name": name, "status": "blocked", "errno": None})
        elif name == denied:
            attempts.append({"name": name, "status": "denied", "errno": denied_errno})
        else:
            attempts.append({"name": name, "status": "mounted", "errno": None})
    parent_sources = _fake_rootless_parent_sources()
    owners: dict[str, dict[str, int]] = {}
    owners["/dev"] = rootless_mount_probe._visible_owner_record(
        parent_sources["dev_directory"], parent_sources["uid_map"], parent_sources["gid_map"], 65534, 65534
    )
    for record, (_name, path, _major, _minor) in zip(parent_sources["device_sources"], rootless_mount_probe.DEVICE_NODES):
        owners[path] = rootless_mount_probe._visible_owner_record(
            record, parent_sources["uid_map"], parent_sources["gid_map"], 65534, 65534
        )
    owners["/sys"] = rootless_mount_probe._visible_owner_record(
        parent_sources["sys_source"], parent_sources["uid_map"], parent_sources["gid_map"], 65534, 65534
    )
    return {
        "schema_version": 1,
        "status": "UNSUPPORTED" if denied else "SUPPORTED",
        "namespace_ids": child_ids,
        "propagation_private": True,
        "network": _rootless_network_report(child_ids["net"]),
        "parent_source_observations": parent_sources,
        "namespace_source_owners": owners,
        "mount_attempts": attempts,
        "mounts_cleaned": True,
        "device_sources": parent_sources["device_sources"],
        "sys_source": parent_sources["sys_source"],
        "bound_device_count": sum(
            item["status"] == "mounted" and item["name"].startswith("device-bind-") for item in attempts
        ),
        "whole_dev_bound": False,
        "run_source_bound": False,
        "child_mount_table_restored": True,
    }


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="real mapped user namespaces are Linux-only")
def test_real_mapped_namespace_binds_host_root_dev_null_and_sys_ownership():
    if os.getuid() == 0:
        pytest.skip("UNVALIDATED: test requires a non-root host account so host-root ownership is unmapped")
    unshare = shutil.which("unshare")
    if not unshare:
        pytest.skip("UNVALIDATED: util-linux unshare is unavailable")

    uid_map = [[0, os.getuid(), 1]]
    gid_map = [[0, os.getgid(), 1]]
    try:
        parent_sources = rootless_mount_probe.collect_parent_source_observations(uid_map=uid_map, gid_map=gid_map)
    except rootless_mount_probe.RootlessMountProbeIntegrityError as exc:
        pytest.skip(f"UNVALIDATED: host does not expose the pinned root-owned /dev and /sys sources: {exc}")

    capability = subprocess.run(
        (unshare, "--user", "--map-root-user", "--", sys.executable, "-c", "pass"),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if capability.returncode != 0:
        pytest.skip(f"UNVALIDATED: mapped user namespaces unavailable: {capability.stderr.strip()}")

    null_record = next(record for record in parent_sources["device_sources"] if record["path"] == "/dev/null")
    script = r"""
import json, sys
from pathlib import Path
from usb_builder import rootless_mount_probe as p
evidence = json.loads(sys.argv[1])
uid_map = p._read_namespace_id_map(Path('/proc/self/uid_map'))
gid_map = p._read_namespace_id_map(Path('/proc/self/gid_map'))
overflow_uid = p._read_overflow_id('overflowuid')
overflow_gid = p._read_overflow_id('overflowgid')
if uid_map != evidence['uid_map'] or gid_map != evidence['gid_map']:
    raise RuntimeError('actual mapped namespace differs from parent-bound map')
checks = {}
for path, record, kind, major, minor in (
    ('/dev/null', evidence['null_record'], 'character-device', 1, 3),
    ('/sys', evidence['sys_source'], 'directory', None, None),
):
    owner = p._visible_owner_record(record, uid_map, gid_map, overflow_uid, overflow_gid)
    observed = p._inspect_child_source(Path(path), record, owner, kind=kind, major=major, minor=minor)
    checks[path] = {'parent_uid': record['owner_uid'], 'visible': observed}
if any(check['parent_uid'] != 0 for check in checks.values()):
    raise RuntimeError('the parent sources were not host-root-owned')
if any(check['visible']['uid'] != overflow_uid for check in checks.values()):
    raise RuntimeError('unmapped host-root UID did not appear as the kernel overflow UID')
print(json.dumps({'checks': checks, 'overflow_uid': overflow_uid, 'overflow_gid': overflow_gid}, sort_keys=True))
"""
    result = subprocess.run(
        (
            unshare,
            "--user",
            "--map-root-user",
            "--",
            sys.executable,
            "-c",
            script,
            json.dumps({
                "uid_map": uid_map,
                "gid_map": gid_map,
                "null_record": null_record,
                "sys_source": parent_sources["sys_source"],
            }),
        ),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["checks"]["/dev/null"]["visible"]["uid"] == report["overflow_uid"]
    assert report["checks"]["/sys"]["visible"]["uid"] == report["overflow_uid"]


@pytest.mark.parametrize(
    "denied",
    [None, "target-root-self-bind", "proc", "sys-recursive-bind", "tmpfs-dev", "device-bind-random", "tmpfs-run", "tmpfs-tmp"],
)
def test_rootless_archiso_mount_probe_uses_exact_n_stack_and_cleans_up(denied, tmp_path, monkeypatch):
    host_ids = {kind: f"{kind}:[{100 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    child_ids = {kind: f"{kind}:[{200 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    labels = list(rootless_mount_probe.MOUNT_OPERATION_NAMES)
    parent_sources = _fake_rootless_parent_sources()
    # Mount callbacks below are synthetic and do not expose a real tmpfs;
    # avoid requiring Windows symlink privileges for Linux-only /dev links.
    monkeypatch.setattr(rootless_mount_probe, "_create_dev_targets", lambda _rootfs: None)
    active: set[str] = set()
    calls: list[tuple[str | None, str, str | None, int, str | None]] = []
    unmounted: list[str] = []
    next_label_index = 0
    network = _rootless_network_report(child_ids["net"])

    def mount_call(source, target, filesystem, flags, data):
        nonlocal next_label_index
        while next_label_index < len(labels):
            candidate = labels[next_label_index]
            if denied == "target-root-self-bind" and candidate != "target-root-self-bind":
                next_label_index += 1
                continue
            if denied == "tmpfs-dev" and candidate.startswith("device-bind-"):
                next_label_index += 1
                continue
            break
        name = labels[next_label_index]
        next_label_index += 1
        calls.append((source, target, filesystem, flags, data))
        if name == denied:
            raise OSError(errno.EPERM, "synthetic capability denial", target)
        active.add(target)

    def mountinfo_reader():
        rows = [("1", "0", "0:1", "/", "/", "rw", "-", "ext4", "root", "rw")]
        rows.extend(
            (str(index + 2), "1", "0:2", "/", target, "rw", "-", "tmpfs", "tmpfs", "rw")
            for index, target in enumerate(sorted(active))
        )
        return rows

    def umount_call(target, _flags):
        assert target in active
        active.remove(target)
        unmounted.append(target)

    report = rootless_mount_probe.run_rootless_archiso_mount_probe(
        host_ids,
        host_ids["net"],
        "/synthetic/ip",
        parent_sources,
        mount_call=mount_call,
        umount_call=umount_call,
        namespace_reader=lambda: child_ids,
        mountinfo_reader=mountinfo_reader,
        network_reader=lambda *_args: network,
        id_map_reader=_fake_id_map_reader(parent_sources),
        overflow_id_reader=_fake_overflow_reader(parent_sources),
        device_inspector=_fake_child_owner,
        directory_inspector=_fake_child_owner,
        temp_root=str(tmp_path),
    )
    assert [item["name"] for item in report["mount_attempts"]] == labels
    assert report["mounts_cleaned"] is True
    assert active == set()
    assert len(unmounted) == len(set(unmounted))
    assert len(report["device_sources"]) == 6
    assert report["whole_dev_bound"] is False
    assert report["run_source_bound"] is False
    if denied is None:
        assert report["status"] == "SUPPORTED"
        assert len(calls) == len(labels)
        assert report["bound_device_count"] == 6
    else:
        assert report["status"] == "UNSUPPORTED"
        denied_index = labels.index(denied)
        assert report["mount_attempts"][denied_index]["status"] == "denied"
        if denied == "target-root-self-bind":
            assert all(item["status"] == "blocked" for item in report["mount_attempts"][1:])
        elif denied == "tmpfs-dev":
            assert all(
                item["status"] == "blocked"
                for item in report["mount_attempts"]
                if item["name"].startswith("device-bind-")
            )
            assert report["bound_device_count"] == 0
        else:
            assert report["bound_device_count"] == sum(
                item["status"] == "mounted" and item["name"].startswith("device-bind-")
                for item in report["mount_attempts"]
            )
    output = rootless_mount_probe.PROBE_MARKER + "\n" + json.dumps(report, sort_keys=True, separators=(",", ":"))
    assert rootless_mount_probe.validate_probe_output(
        output,
        host_namespace_ids=host_ids,
        host_net_namespace=host_ids["net"],
        parent_source_observations=parent_sources,
        allow_synthetic=True,
    ) is not None


def test_rootless_archiso_mount_probe_mount_plan_matches_pinned_n_stack():
    rootfs = Path("/probe/rootfs")
    plan = rootless_mount_probe._mount_plan(rootfs)
    assert [item[0] for item in plan] == list(rootless_mount_probe.MOUNT_OPERATION_NAMES)
    assert plan[0] == ("target-root-self-bind", str(rootfs), rootfs, None, mount_probe.MS_BIND, None)
    assert plan[1] == (
        "proc", "proc", rootfs / "proc", "proc", mount_probe.MS_NOSUID | mount_probe.MS_NOEXEC | mount_probe.MS_NODEV, None
    )
    assert plan[2] == ("sys-recursive-bind", "/sys", rootfs / "sys", None, mount_probe.MS_BIND | mount_probe.MS_REC, None)
    assert plan[3] == (
        "tmpfs-dev", "tmpfs", rootfs / "dev", "tmpfs", mount_probe.MS_NOSUID | mount_probe.MS_NODEV, "mode=0755"
    )
    for item, device in zip(plan[4:10], rootless_mount_probe.DEVICE_NODES):
        name, source, _major, _minor = device
        assert item == (f"device-bind-{name}", source, rootfs / "dev" / name, None, mount_probe.MS_BIND, None)
    assert plan[10][0] == "tmpfs-run" and plan[11][0] == "tmpfs-tmp"


@pytest.mark.parametrize("bad_source", ["wrong-type", "wrong-major", "wrong-minor", "wrong-owner"])
def test_rootless_archiso_mount_probe_rejects_wrong_device_source_before_mount(bad_source, tmp_path):
    host_ids = {kind: f"{kind}:[{100 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    child_ids = {kind: f"{kind}:[{200 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    parent_sources = _fake_rootless_parent_sources()
    null_record = parent_sources["device_sources"][1]
    calls: list[str] = []
    if bad_source == "wrong-type":
        null_record["kind"] = "regular-file"
    elif bad_source == "wrong-major":
        null_record["major"] += 1
    elif bad_source == "wrong-minor":
        null_record["minor"] += 1
        null_record["st_rdev"] += 1
    else:
        null_record["owner_uid"] = 1000

    with pytest.raises(rootless_mount_probe.RootlessMountProbeIntegrityError):
        rootless_mount_probe.run_rootless_archiso_mount_probe(
            host_ids,
            host_ids["net"],
            "/synthetic/ip",
            parent_sources,
            mount_call=lambda _source, target, *_args: calls.append(target),
            umount_call=lambda *_args: None,
            namespace_reader=lambda: child_ids,
            mountinfo_reader=lambda: [("1", "0", "0:1", "/", "/", "rw", "-", "ext4", "root", "rw")],
            network_reader=lambda *_args: _rootless_network_report(child_ids["net"]),
            id_map_reader=_fake_id_map_reader(parent_sources),
            overflow_id_reader=_fake_overflow_reader(parent_sources),
            device_inspector=_fake_child_owner,
            directory_inspector=_fake_child_owner,
            temp_root=str(tmp_path),
        )
    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_rootless_archiso_mount_probe_rejects_wrong_mapped_owner_before_mount(tmp_path):
    host_ids = {kind: f"{kind}:[{100 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    child_ids = {kind: f"{kind}:[{200 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    parent_sources = _fake_rootless_parent_sources()
    calls: list[str] = []

    def wrong_owner_for_null(path: Path, _parent: Mapping[str, Any], expected: Mapping[str, int]) -> dict[str, int]:
        if path.as_posix() == "/dev/null":
            return {"uid": 0, "gid": expected["gid"]}
        return dict(expected)

    with pytest.raises(rootless_mount_probe.RootlessMountProbeIntegrityError, match="parent-bound mapping"):
        rootless_mount_probe.run_rootless_archiso_mount_probe(
            host_ids,
            host_ids["net"],
            "/synthetic/ip",
            parent_sources,
            mount_call=lambda _source, target, *_args: calls.append(target),
            umount_call=lambda *_args: None,
            namespace_reader=lambda: child_ids,
            mountinfo_reader=lambda: [("1", "0", "0:1", "/", "/", "rw", "-", "ext4", "root", "rw")],
            network_reader=lambda *_args: _rootless_network_report(child_ids["net"]),
            id_map_reader=_fake_id_map_reader(parent_sources),
            overflow_id_reader=_fake_overflow_reader(parent_sources),
            device_inspector=wrong_owner_for_null,
            directory_inspector=_fake_child_owner,
            temp_root=str(tmp_path),
        )
    assert calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failure", ["route", "interface", "namespace"])
def test_rootless_archiso_mount_probe_rejects_network_state_before_any_mount(failure, tmp_path):
    host_ids = {kind: f"{kind}:[{100 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    child_ids = {kind: f"{kind}:[{200 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    parent_sources = _fake_rootless_parent_sources()
    network = _rootless_network_report(child_ids["net"])
    if failure == "route":
        network["ipv4_netlink_route_count"] = 1
    elif failure == "interface":
        network["namespace_interfaces"] = ["eth0", "lo"]
    else:
        network["namespace_id"] = host_ids["net"]
    calls: list[str] = []
    with pytest.raises(rootless_mount_probe.RootlessMountProbeIntegrityError, match="route-free network"):
        rootless_mount_probe.run_rootless_archiso_mount_probe(
            host_ids,
            host_ids["net"],
            "/synthetic/ip",
            parent_sources,
            mount_call=lambda _source, target, *_args: calls.append(target),
            umount_call=lambda *_args: None,
            namespace_reader=lambda: child_ids,
            mountinfo_reader=lambda: [("1", "0", "0:1", "/", "/", "rw", "-", "ext4", "root", "rw")],
            network_reader=lambda *_args: network,
            id_map_reader=_fake_id_map_reader(parent_sources),
            overflow_id_reader=_fake_overflow_reader(parent_sources),
            device_inspector=_fake_child_owner,
            directory_inspector=_fake_child_owner,
            temp_root=str(tmp_path),
        )
    assert calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("mutation", ["wrong-network", "whole-dev", "device-order", "false-cleanup"])
def test_rootless_mount_report_parser_rejects_false_or_changed_evidence(mutation):
    host_ids = {kind: f"{kind}:[{100 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    report = _fake_rootless_report(host_ids=host_ids)
    if mutation == "wrong-network":
        report["network"]["ipv4_netlink_route_count"] = 1
    elif mutation == "whole-dev":
        report["whole_dev_bound"] = True
    elif mutation == "device-order":
        report["device_sources"].reverse()
    else:
        report["mounts_cleaned"] = False
    output = rootless_mount_probe.PROBE_MARKER + "\n" + json.dumps(report, sort_keys=True, separators=(",", ":"))
    assert rootless_mount_probe.validate_probe_output(
        output,
        host_namespace_ids=host_ids,
        host_net_namespace=host_ids["net"],
        parent_source_observations=_fake_rootless_parent_sources(),
        allow_synthetic=True,
    ) is None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="real Linux /dev character-node inspection")
def test_rootless_mount_probe_accepts_only_the_six_pinned_device_numbers():
    for _name, path, major, minor in rootless_mount_probe.DEVICE_NODES:
        evidence = rootless_mount_probe._inspect_device(Path(path), major, minor)
        assert evidence["kind"] == "character-device"
        assert evidence["owner_uid"] == 0
        assert evidence["major"] == major
        assert evidence["minor"] == minor


def test_rootless_mount_preflight_uses_one_level_auto_map_and_host_mount_recheck(monkeypatch, tmp_path):
    host_ids = {kind: f"{kind}:[{100 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    report = _fake_rootless_report(host_ids=host_ids)
    output = rootless_mount_probe.PROBE_MARKER + "\n" + json.dumps(report, sort_keys=True, separators=(",", ":"))
    helper = Path(sys.executable)
    helper_hash = build_usb._sha256_file(helper)
    host = SimpleNamespace(
        uid=1000,
        gid=1000,
        subuid_start=100000,
        subuid_count=65536,
        subgid_start=100000,
        subgid_count=65536,
        trusted_tools=(SimpleNamespace(name="unshare", path=str(helper), sha256=helper_hash),),
    )
    calls: list[list[str]] = []
    snapshots = iter((b"before-mountinfo", b"before-mountinfo"))
    monkeypatch.setattr(build_usb.sys, "platform", "linux")
    monkeypatch.setattr(build_usb.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(build_usb, "_assert_no_link_components", lambda _path: None)
    monkeypatch.setattr(build_usb.shutil, "which", lambda name: str(helper) if name == "ip" else None)
    monkeypatch.setattr(build_usb, "_canonical_existing_file", lambda path, _label: path)
    monkeypatch.setattr(build_usb, "_current_namespace_identities", lambda: host_ids)
    monkeypatch.setattr(build_usb, "_sha256_file", lambda _path: helper_hash)
    monkeypatch.setattr(
        rootless_mount_probe,
        "collect_parent_source_observations",
        lambda *, uid_map, gid_map: _fake_rootless_parent_sources(uid_map, gid_map),
    )
    monkeypatch.setattr(
        build_usb,
        "_run_command",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            list(command), 0,
            (f"{command[-1]} is owned by iproute2 6.17.0-1\n" if "-Qo" in command else "iproute2 6.17.0-1\n"),
            "",
        ),
    )

    def run_probe(command, *, runner):
        calls.append(list(command))
        assert runner is not subprocess.run
        return subprocess.CompletedProcess(list(command), 0, output, "")

    monkeypatch.setattr(build_usb, "_run_bounded_mount_probe", run_probe)
    result = build_usb._rootless_archiso_mount_preflight(
        host,
        runner=lambda *args, **kwargs: pytest.fail("unexpected host command"),
        host_mount_table_reader=lambda: next(snapshots),
    )
    command = calls[0]
    separator = command.index("--")
    assert command[1:separator] == [
        "--net", "--fork", "--pid", "--mount", "--map-auto", "--map-root-user",
        "--setuid", "0", "--setgid", "0", "--propagation", "private",
    ]
    assert command[separator + 1 : separator + 4] == [sys.executable, "-m", "usb_builder.rootless_mount_probe"]
    assert result["status"] == "SUPPORTED"
    assert result["host_mount_table_sha256"] == build_usb._sha256_bytes(b"before-mountinfo")


def test_rootless_mount_preflight_rejects_host_mount_drift(tmp_path, monkeypatch):
    host_ids = {kind: f"{kind}:[{100 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    report = _fake_rootless_report(host_ids=host_ids)
    output = rootless_mount_probe.PROBE_MARKER + "\n" + json.dumps(report, sort_keys=True, separators=(",", ":"))
    helper = Path(sys.executable)
    helper_hash = build_usb._sha256_file(helper)
    host = SimpleNamespace(
        uid=1000,
        gid=1000,
        subuid_start=100000,
        subuid_count=65536,
        subgid_start=100000,
        subgid_count=65536,
        trusted_tools=(SimpleNamespace(name="unshare", path=str(helper), sha256=helper_hash),),
    )
    snapshots = iter((b"before", b"changed"))
    monkeypatch.setattr(build_usb.sys, "platform", "linux")
    monkeypatch.setattr(build_usb.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(build_usb, "_assert_no_link_components", lambda _path: None)
    monkeypatch.setattr(build_usb.shutil, "which", lambda _name: str(helper))
    monkeypatch.setattr(build_usb, "_canonical_existing_file", lambda path, _label: path)
    monkeypatch.setattr(build_usb, "_current_namespace_identities", lambda: host_ids)
    monkeypatch.setattr(build_usb, "_sha256_file", lambda _path: helper_hash)
    monkeypatch.setattr(
        rootless_mount_probe,
        "collect_parent_source_observations",
        lambda *, uid_map, gid_map: _fake_rootless_parent_sources(uid_map, gid_map),
    )
    monkeypatch.setattr(
        build_usb,
        "_run_command",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            list(command), 0,
            (f"{command[-1]} is owned by iproute2 6.17.0-1\n" if "-Qo" in command else "iproute2 6.17.0-1\n"),
            "",
        ),
    )
    monkeypatch.setattr(
        build_usb,
        "_run_bounded_mount_probe",
        lambda command, **_kwargs: subprocess.CompletedProcess(list(command), 0, output, ""),
    )
    with pytest.raises(build_usb.BuildContractError, match="host mount table changed"):
        build_usb._rootless_archiso_mount_preflight(
            host,
            runner=lambda *args, **kwargs: pytest.fail("unexpected host command"),
            host_mount_table_reader=lambda: next(snapshots),
        )
    assert list(tmp_path.iterdir()) == []


def test_production_offline_build_stays_closed_after_rootless_preflight(tmp_path, monkeypatch):
    config = _offline_config(tmp_path)
    host = SimpleNamespace(
        uid=1000,
        arch_install_scripts_package_version=build_usb.EXPECTED_ARCH_INSTALL_SCRIPTS_VERSION,
        trusted_tools=(
            SimpleNamespace(name="pacstrap", path="/usr/bin/pacstrap", sha256="a" * 64),
            SimpleNamespace(name="arch-chroot", path="/usr/bin/arch-chroot", sha256="b" * 64),
        ),
    )
    mount_calls: list[object] = []
    monkeypatch.setattr(build_usb, "_rootless_archiso_host_preflight", lambda **_kwargs: host)
    monkeypatch.setattr(build_usb, "_assert_rootless_process_state", lambda _host: None)
    monkeypatch.setattr(
        build_usb,
        "_rootless_archiso_mount_preflight",
        lambda *_args, **_kwargs: mount_calls.append("supported") or {"status": "SUPPORTED"},
    )
    with pytest.raises(build_usb.BuildContractError, match="OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE"):
        build_usb.execute_build(config, host_platform="linux", runner=subprocess.run)
    assert mount_calls == ["supported"]
    assert list(config.output_dir.iterdir()) == []
    assert list(config.work_parent.iterdir()) == []


def test_rootless_online_runner_sanitizes_environment_and_stdio(monkeypatch, tmp_path):
    host = SimpleNamespace(uid=1000, username="builder")
    monkeypatch.setattr(build_usb, "_rootless_build_home", lambda _host: "/home/builder")
    state_checks: list[str] = []
    monkeypatch.setattr(build_usb, "_assert_rootless_process_state", lambda _host: state_checks.append("checked"))
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def child(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "ok\n", "")

    commands: list[list[str]] = []
    runner = build_usb._rootless_online_runner(
        child,
        host,
        command_log=commands,
        permitted_paths={build_usb.ROOTLESS_SYSTEM_PATH, "/operation/pacstrap:/usr/bin:/usr/sbin"},
    )
    result = runner(
        ["/usr/bin/mkarchiso", "-v"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={
            "PATH": build_usb.ROOTLESS_SYSTEM_PATH,
            "HOME": "/attacker/home",
            "PYTHONPATH": "/attacker/modules",
            "LD_PRELOAD": "/attacker/library.so",
            "PIP_NO_INDEX": "1",
        },
    )
    assert result.returncode == 0
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command == ["/usr/bin/mkarchiso", "-v"]
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["close_fds"] is True
    assert kwargs["stderr"] is subprocess.STDOUT
    assert kwargs["env"]["PATH"] == build_usb.ROOTLESS_SYSTEM_PATH
    assert kwargs["env"]["HOME"] == "/home/builder"
    assert kwargs["env"]["USER"] == kwargs["env"]["LOGNAME"] == "builder"
    assert kwargs["env"]["PIP_NO_INDEX"] == "1"
    assert "PYTHONPATH" not in kwargs["env"]
    assert "LD_PRELOAD" not in kwargs["env"]
    assert commands == [["/usr/bin/mkarchiso", "-v"]]
    assert state_checks == ["checked", "checked"]


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"stdin": subprocess.PIPE}, "unsupported subprocess controls: stdin"),
        ({"close_fds": False}, "unsupported subprocess controls: close_fds"),
        ({"start_new_session": True}, "unsupported subprocess controls: start_new_session"),
        ({"env": {"PATH": "/home/builder/.local/bin:/usr/bin"}}, "PATH is not one"),
    ],
)
def test_rootless_online_runner_rejects_subprocess_escape_controls_before_child(
    monkeypatch, kwargs, message
):
    host = SimpleNamespace(uid=1000, username="builder")
    monkeypatch.setattr(build_usb, "_rootless_build_home", lambda _host: "/home/builder")
    monkeypatch.setattr(build_usb, "_assert_rootless_process_state", lambda _host: None)
    calls: list[object] = []
    runner = build_usb._rootless_online_runner(
        lambda *args, **child_kwargs: calls.append((args, child_kwargs)),
        host,
        command_log=[],
        permitted_paths={build_usb.ROOTLESS_SYSTEM_PATH},
    )
    with pytest.raises(build_usb.BuildContractError, match=message):
        runner(["/usr/bin/true"], **kwargs)
    assert calls == []


def test_production_online_build_uses_rootless_path_and_records_pacstrap_overlay(tmp_path, monkeypatch):
    config = _config(tmp_path)
    fake_builder = _FakeBuilder()
    mkarchiso_dir = tmp_path / "tools"
    mkarchiso_dir.mkdir()
    fake_mkarchiso = mkarchiso_dir / "mkarchiso"
    _write_executable(fake_mkarchiso, "synthetic rootless mkarchiso fixture\n")
    inputs = _rootless_test_inputs()
    host = build_usb._validate_rootless_host_state(**inputs)
    host_ids = {kind: f"{kind}:[{500 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)}
    report = {
        "namespace_ids": {kind: f"{kind}:[{600 + index}]" for index, kind in enumerate(mount_probe.NAMESPACE_KINDS)},
        "network": {
            "namespace_id": "net:[601]",
            "namespace_interfaces": ["lo"],
            "ipv4_netlink_route_count": 0,
            "ipv6_netlink_route_count": 0,
            "ipv4_rule_priorities": [0, 32766, 32767],
            "ipv6_rule_priorities": [0, 32766],
            "ipv6_null_unreachable_proc_rows": 0,
        },
    }
    mount = {
        "status": "SUPPORTED",
        "command": ["/usr/bin/unshare", "--net", "--mount", "--pid"],
        "output": "rootless namespace mount probe passed",
        "host_namespace_ids": host_ids,
        "host_mount_table_sha256": "a" * 64,
        "unshare_path": "/usr/bin/unshare",
        "iproute_path": "/usr/bin/ip",
        "iproute_sha256": "b" * 64,
        "iproute_package_owner_output": "/usr/bin/ip is owned by iproute2 7.2.0-1",
        "report": report,
    }
    monkeypatch.setattr(build_usb.subprocess, "run", lambda command, **kwargs: fake_builder(command, **kwargs))
    monkeypatch.setattr(build_usb, "_rootless_archiso_host_preflight", lambda **_kwargs: host)
    monkeypatch.setattr(build_usb, "_rootless_archiso_mount_preflight", lambda *_args, **_kwargs: mount)
    monkeypatch.setattr(build_usb, "_assert_rootless_process_state", lambda _host: None)
    monkeypatch.setattr(build_usb, "_rootless_build_home", lambda _host: str(tmp_path))
    monkeypatch.setattr(build_usb, "_verify_rootless_pacstrap_overlay", lambda _evidence: None)

    def stage_overlay(work_dir):
        return build_usb.RootlessPacstrapOverlayEvidence(
            patch_id=build_usb.ROOTLESS_PACSTRAP_PATCH_ID,
            operation_root=str(work_dir),
            operation_owner_uid=host.uid,
            operation_owner_marker_sha256=hashlib.sha256(b"fixture").hexdigest(),
            source_path=build_usb.ROOTLESS_ARCH_INSTALL_SCRIPTS_SOURCE_PATH,
            source_sha256=build_usb.ROOTLESS_PACSTRAP_SHA256,
            source_owner_uid=0,
            patch_resource_path="usb_builder/" + build_usb.ROOTLESS_PACSTRAP_PATCH_RELATIVE_PATH,
            patch_sha256=build_usb.ROOTLESS_PACSTRAP_PATCH_SHA256,
            staged_path=str(work_dir / "rootless-tools" / "pacstrap"),
            staged_sha256=build_usb.ROOTLESS_PACSTRAP_PATCHED_SHA256,
            staged_mode=0o755,
            child_path=f"{work_dir / 'rootless-tools'}:/usr/bin:/usr/sbin",
        )

    monkeypatch.setattr(build_usb, "_stage_rootless_pacstrap_overlay", stage_overlay)
    result = build_usb.execute_build(
        config,
        host_platform="linux",
        mkarchiso_path=str(fake_mkarchiso),
        runner=build_usb.subprocess.run,
        mountpoints_reader=lambda: (),
        clock=lambda: 1_800_000_000,
    )
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["rootless_archiso_execution"]["status"] == "NONROOT_ONLINE_AFTER_ROUTE_FREE_MOUNT_PREFLIGHT"
    assert manifest["rootless_archiso_execution"]["mount_preflight"]["status"] == "SUPPORTED"
    overlay = manifest["rootless_archiso_execution"]["pacstrap_overlay"]
    assert overlay["patch"]["sha256"] == build_usb.ROOTLESS_PACSTRAP_PATCH_SHA256
    assert overlay["staged"]["sha256"] == build_usb.ROOTLESS_PACSTRAP_PATCHED_SHA256
    assert manifest["network_isolation"]["mode"] == "online-egress-after-rootless-preflight"
    assert manifest["network_isolation"]["command_prefix"] == []
    mkarchiso_call_index = next(
        index for index, item in enumerate(fake_builder.calls) if Path(item[0]).name == "mkarchiso"
    )
    mkarchiso_env = fake_builder.call_kwargs[mkarchiso_call_index]["env"]
    assert "rootless-tools:/usr/bin:/usr/sbin" in mkarchiso_env["PATH"]
    assert all("--net" not in command for command in manifest["commands"] if Path(command[0]).name == "mkarchiso")


@pytest.mark.parametrize(
    "report_mutation",
    [
        lambda report: report.pop("ipv4_rule_priorities"),
        lambda report: report.update(namespace_id="not-a-netns"),
        lambda report: report.update(namespace_id="net:[100]"),
        lambda report: report.update(sysfs_interfaces_observed=["eth0"]),
        lambda report: report.update(ipv4_netlink_route_count=True),
        lambda report: report.update(ipv6_rule_priorities=[0, 32766, 100]),
        lambda report: report.update(ipv6_null_unreachable_proc_rows=3),
        lambda report: report.update(ipv6_null_unreachable_proc_rows=True),
        lambda report: report.update(extra_unverified_field=True),
    ],
)
def test_network_probe_output_requires_complete_strict_child_report(report_mutation):
    report = json.loads(_valid_probe_report().splitlines()[1])
    report_mutation(report)
    output = build_usb.OFFLINE_NETWORK_PROBE_MARKER + "\n" + json.dumps(report)
    assert not build_usb._validate_network_probe_output(
        output,
        host_namespace_identity="net:[100]",
        allow_synthetic=False,
    )


def test_network_probe_wrapper_checks_before_and_after_child_in_same_namespace(monkeypatch):
    checks = []
    calls = []

    def collect(host, ip):
        checks.append((host, ip))
        return {"namespace_id": "net:[123]"}

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return type("FakeProcess", (), {"pid": 111, "wait": lambda self, timeout=None: 7})()

    monkeypatch.setattr(network_probe, "collect_namespace_views", collect)
    monkeypatch.setattr(network_probe.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(network_probe.os, "killpg", lambda *_args: None, raising=False)
    monkeypatch.setattr(network_probe.signal, "SIGKILL", 9, raising=False)
    assert network_probe.run_command_with_namespace_recheck("net:[100]", "/usr/bin/ip", ("true",)) == 7
    assert checks == [("net:[100]", "/usr/bin/ip")] * 2
    assert calls == [
        (
            ["true"],
            {
                "stdin": subprocess.DEVNULL,
                "close_fds": True,
                "start_new_session": True,
            },
        )
    ]


def test_network_probe_wrapper_blocks_child_on_precheck_failure(monkeypatch):
    calls = []

    def fail_probe(*_args):
        raise network_probe.ProbeError("unexpected route")

    monkeypatch.setattr(network_probe, "collect_namespace_views", fail_probe)
    monkeypatch.setattr(network_probe.subprocess, "Popen", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(network_probe.os, "killpg", lambda *_args: None, raising=False)
    monkeypatch.setattr(network_probe.signal, "SIGKILL", 9, raising=False)
    assert network_probe.run_command_with_namespace_recheck("net:[100]", "/usr/bin/ip", ("touch", "x")) == 125
    assert calls == []


def test_network_probe_wrapper_fails_after_child_if_namespace_drifted(monkeypatch):
    checks = 0
    calls = []

    def collect(*_args):
        nonlocal checks
        checks += 1
        if checks == 2:
            raise network_probe.ProbeError("route appeared")
        return {}

    def fake_popen(command, **kwargs):
        calls.append(command)
        return type("FakeProcess", (), {"pid": 123, "wait": lambda self, timeout=None: 0})()

    monkeypatch.setattr(network_probe, "collect_namespace_views", collect)
    monkeypatch.setattr(network_probe.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(network_probe.os, "killpg", lambda *_args: None, raising=False)
    monkeypatch.setattr(network_probe.signal, "SIGKILL", 9, raising=False)
    assert network_probe.run_command_with_namespace_recheck("net:[100]", "/usr/bin/ip", ("build",)) == 125
    assert calls == [["build"]]


@pytest.mark.parametrize("ignore_term", [False, True])
def test_network_probe_timeout_terminates_entire_child_process_group(monkeypatch, ignore_term):
    checks = []
    spawned = []
    killed = []
    grandchild_alive = True

    class FakeProcess:
        pid = 4321

        def __init__(self):
            self.wait_calls = 0

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired("build", timeout)
            if self.wait_calls == 2 and ignore_term:
                raise subprocess.TimeoutExpired("build", timeout)
            return -int(signal.SIGKILL if self.wait_calls > 2 else signal.SIGTERM)

    def fake_popen(command, **kwargs):
        spawned.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(network_probe, "collect_namespace_views", lambda *_args: checks.append(1) or {})
    monkeypatch.setattr(network_probe.subprocess, "Popen", fake_popen)
    # Windows does not expose SIGKILL, but this unit test injects the Linux
    # process-group API and only asserts the requested signal sequence.
    monkeypatch.setattr(network_probe.signal, "SIGKILL", 9, raising=False)

    def fake_killpg(pid, signum):
        nonlocal grandchild_alive
        killed.append((pid, signum))
        if signum == signal.SIGKILL:
            grandchild_alive = False

    monkeypatch.setattr(network_probe.os, "killpg", fake_killpg, raising=False)
    result = network_probe.run_command_with_namespace_recheck(
        "net:[100]", "/usr/bin/ip", ("mkarchiso", "build"), timeout=2.5
    )
    assert result == 124
    assert checks == [1, 1]
    assert spawned == [
        (
            ["mkarchiso", "build"],
            {
                "stdin": subprocess.DEVNULL,
                "close_fds": True,
                "start_new_session": True,
            },
        )
    ]
    assert killed == [(4321, signal.SIGTERM), (4321, signal.SIGKILL)]
    assert grandchild_alive is False


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux process groups and /proc")
def test_network_probe_timeout_kills_grandchild_after_leader_exits_on_sigterm(
    monkeypatch, tmp_path
):
    grandchild_pid_file = tmp_path / "grandchild.pid"
    script = "\n".join(
        (
            "import os, signal, sys, time",
            "pid = os.fork()",
            "if pid == 0:",
            "    signal.signal(signal.SIGTERM, signal.SIG_IGN)",
            "    with open(sys.argv[1], 'w', encoding='ascii') as stream:",
            "        stream.write(str(os.getpid()))",
            "    while True: time.sleep(0.05)",
            "else:",
            "    while True: time.sleep(0.05)",
        )
    )
    monkeypatch.setattr(network_probe, "collect_namespace_views", lambda *_args: {})
    monkeypatch.setattr(
        network_probe,
        "PROCESS_GROUP_TERMINATION_GRACE_SECONDS",
        0.1,
    )

    result = network_probe.run_command_with_namespace_recheck(
        "net:[100]",
        "/usr/bin/ip",
        (sys.executable, "-c", script, str(grandchild_pid_file)),
        timeout=0.3,
    )

    assert result == 124
    assert grandchild_pid_file.is_file()
    grandchild_pid = int(grandchild_pid_file.read_text(encoding="ascii"))

    def is_alive(pid: int) -> bool:
        proc_stat = Path(f"/proc/{pid}/stat")
        try:
            # A killed orphan may remain briefly as a zombie until PID 1 reaps
            # it. It is no longer executing in either case.
            fields = proc_stat.read_text(encoding="ascii").rsplit(")", 1)[1].split()
        except FileNotFoundError:
            return False
        return bool(fields) and fields[0] != "Z"

    deadline = time.monotonic() + 2.0
    while is_alive(grandchild_pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    if is_alive(grandchild_pid):
        # Avoid leaking a process if the assertion catches a regression.
        os.kill(grandchild_pid, signal.SIGKILL)
        pytest.fail("grandchild survived after its process-group leader exited")


@pytest.mark.parametrize(
    ("kind", "expected_message"),
    [
        ("stdout_socket", "regular log file"),
        ("stdout_socket_makefile", "regular log file"),
        ("stdout_fd", "stdout must be PIPE or an already-open regular log file"),
        ("stdout_none", "stdout must be PIPE or an already-open regular log file"),
        ("stderr_socket", "stderr must be merged into stdout"),
        ("stderr_none", "stderr must be merged into stdout"),
        ("stdin_socket", "unsupported subprocess controls"),
    ],
)
def test_offline_guard_rejects_socket_and_inherited_stdio_before_runner(
    tmp_path, kind, expected_message
):
    runner = _FakeBuilder()
    fake_unshare = tmp_path / "unshare"
    _write_executable(fake_unshare, "synthetic util-linux executable fixture\n")
    isolation = build_usb._establish_offline_network_isolation(
        runner,
        unshare_path=str(fake_unshare),
        host_namespace_identity="net:[host-fixture]",
    )
    guarded = build_usb._network_guarded_runner(runner, isolation)
    calls_before = len(runner.calls)
    makefile = None
    sock, peer = socket.socketpair()
    try:
        if kind == "stdout_socket":
            controls = {"stdout": sock}
        elif kind == "stdout_socket_makefile":
            makefile = sock.makefile("wb")
            controls = {"stdout": makefile}
        elif kind == "stdout_fd":
            controls = {"stdout": sock.fileno()}
        elif kind == "stdout_none":
            controls = {"stdout": None}
        elif kind == "stderr_socket":
            controls = {"stderr": sock}
        elif kind == "stderr_none":
            controls = {"stderr": None}
        else:
            controls = {"stdin": sock}
        try:
            with pytest.raises(build_usb.BuildContractError, match=expected_message):
                guarded(["must-not-run"], **controls)
        finally:
            if makefile is not None:
                makefile.close()
    finally:
        sock.close()
        peer.close()
    assert len(runner.calls) == calls_before


@pytest.mark.parametrize("which_tool", ["unshare", "iproute2"])
def test_namespace_guard_rejects_changed_tool_hash_before_command(tmp_path, which_tool):
    fake_tools = tmp_path / "tools"
    fake_tools.mkdir()
    fake_unshare = fake_tools / "unshare"
    fake_ip = fake_tools / "ip"
    _write_executable(fake_unshare, "trusted fixture unshare\n")
    _write_executable(fake_ip, "trusted fixture iproute2\n")
    runner = _FakeBuilder()
    isolation = build_usb._establish_offline_network_isolation(
        runner,
        unshare_path=str(fake_unshare),
        iproute_path=str(fake_ip),
        host_namespace_identity="net:[host-fixture]",
        is_root=True,
    )
    changed = fake_unshare if which_tool == "unshare" else fake_ip
    changed.write_text("changed executable fixture\n", encoding="ascii")
    guarded = build_usb._network_guarded_runner(runner, isolation)
    before = len(runner.calls)
    with pytest.raises(build_usb.BuildContractError, match=f"{which_tool} changed during build"):
        guarded(["do-not-run"])
    assert len(runner.calls) == before


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


def _populate_local_package_database(root: Path, *package_names: str) -> Path:
    db_path = root / "var" / "lib" / "pacman"
    local = db_path / "local"
    _write(local / "ALPM_DB_VERSION", build_usb.EXPECTED_ALPM_LOCAL_DB_VERSION)
    for name in package_names:
        record = local / f"{name}-1-1"
        record.mkdir(parents=True, exist_ok=True)
        _write(record / "desc", f"%NAME%\n{name}\n\n%VERSION%\n1-1\n")
    return db_path


def test_target_package_inventory_uses_exact_offline_image_database_command(tmp_path):
    target = tmp_path / "target-root"
    target.mkdir()
    db_path = _populate_local_package_database(target, "flashrom", "python")
    calls: list[list[str]] = []

    def valid_runner(command, **kwargs):
        calls.append(list(command))
        assert kwargs["stderr"] == subprocess.STDOUT
        return subprocess.CompletedProcess(command, 0, "python 3.13-1\nflashrom 1.4-1\n", "")

    inventory = build_usb._record_package_versions(target, valid_runner, ("python", "flashrom"))

    assert inventory == ("flashrom 1.4-1", "python 3.13-1")
    assert calls == [["pacman", "--config", "/dev/null", "--dbpath", str(db_path.resolve()), "-Q"]]


@pytest.mark.parametrize(
    ("output", "error"),
    [
        ("python 3.13-1\n", "missing required packages"),
        ("python 3.13-1\nnot a package row\n", "malformed"),
        ("python 3.13-1\npython 3.13-1\n", "duplicate package names"),
        ("warning: package database is corrupt\npython 3.13-1\n", "malformed"),
    ],
)
def test_target_package_inventory_rejects_missing_malformed_duplicate_and_warning_rows(tmp_path, output, error):
    target = tmp_path / "target-root"
    target.mkdir()
    _populate_local_package_database(target, "python", "flashrom")

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, output, "")

    with pytest.raises(build_usb.BuildContractError, match=error):
        build_usb._record_package_versions(target, runner, ("python", "flashrom"))


@pytest.mark.parametrize("database_state", ["missing-db", "missing-local", "empty-local", "missing-desc"])
def test_target_package_inventory_rejects_missing_or_incomplete_local_database_before_runner(tmp_path, database_state):
    target = tmp_path / "target-root"
    target.mkdir()
    if database_state in {"empty-local", "missing-desc"}:
        local = target / "var" / "lib" / "pacman" / "local"
        local.mkdir(parents=True)
        if database_state == "missing-desc":
            _write(local / "ALPM_DB_VERSION", build_usb.EXPECTED_ALPM_LOCAL_DB_VERSION)
            (local / "python-1-1").mkdir()
    elif database_state == "missing-db":
        (target / "var" / "lib").mkdir(parents=True)
    elif database_state == "missing-local":
        (target / "var" / "lib" / "pacman").mkdir(parents=True)

    def runner(command, **kwargs):
        pytest.fail(f"pacman must not run against {database_state}: {command}")

    with pytest.raises(build_usb.BuildContractError, match="target root local pacman database"):
        build_usb._record_package_versions(target, runner, ())


@pytest.mark.parametrize(
    ("marker_state", "error"),
    [
        ("missing", "ALPM_DB_VERSION"),
        ("wrong-version", "unsupported ALPM_DB_VERSION"),
        ("unexpected-root-file", "unsafe package record"),
    ],
)
def test_target_package_inventory_validates_pacman_local_db_metadata(tmp_path, marker_state, error):
    target = tmp_path / "target-root"
    target.mkdir()
    db_path = _populate_local_package_database(target, "python")
    local = db_path / "local"
    marker = local / "ALPM_DB_VERSION"
    if marker_state == "missing":
        marker.unlink()
    elif marker_state == "wrong-version":
        marker.write_text("8\n", encoding="ascii")
    else:
        _write(local / "unrecognized-metadata", "must not be treated as a package record")

    def runner(command, **kwargs):
        pytest.fail(f"pacman must not run with {marker_state}: {command}")

    with pytest.raises(build_usb.BuildContractError, match=error):
        build_usb._record_package_versions(target, runner, ())


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX symlink test")
def test_target_package_inventory_rejects_linked_pacman_db_version_marker(tmp_path):
    target = tmp_path / "target-root"
    target.mkdir()
    db_path = _populate_local_package_database(target, "python")
    marker = db_path / "local" / "ALPM_DB_VERSION"
    external = tmp_path / "external-version"
    external.write_text(build_usb.EXPECTED_ALPM_LOCAL_DB_VERSION, encoding="ascii")
    marker.unlink()
    marker.symlink_to(external)

    def runner(command, **kwargs):
        pytest.fail(f"pacman must not run with a linked version marker: {command}")

    with pytest.raises(build_usb.BuildContractError, match="ALPM_DB_VERSION"):
        build_usb._record_package_versions(target, runner, ())


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX symlink test")
@pytest.mark.parametrize("link_component", ["pacman", "local"])
def test_target_package_inventory_rejects_symlinked_database_component_before_runner(tmp_path, link_component):
    target = tmp_path / "target-root"
    target.mkdir()
    outside = tmp_path / "outside-pacman-db"
    outside_local = outside / "local"
    outside_local.mkdir(parents=True)
    pacman_db = target / "var" / "lib" / "pacman"
    if link_component == "pacman":
        pacman_db.parent.mkdir(parents=True)
        pacman_db.symlink_to(outside, target_is_directory=True)
    else:
        pacman_db.mkdir(parents=True)
        (pacman_db / "local").symlink_to(outside_local, target_is_directory=True)

    def runner(command, **kwargs):
        pytest.fail(f"pacman must not run against linked {link_component} database path: {command}")

    with pytest.raises(build_usb.BuildContractError, match="missing or unsafe"):
        build_usb._record_package_versions(target, runner, ())


def test_nonzero_target_pacman_inventory_prevents_candidate_publication(tmp_path):
    config = _config(tmp_path)
    runner = _FakeBuilder(target_pacman_returncode=1)
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_mkarchiso = fake_tools / "mkarchiso"
    _write_executable(fake_mkarchiso, "synthetic mkarchiso executable fixture\n")

    with pytest.raises(build_usb.BuildContractError, match="command failed") as exc:
        build_usb.execute_build(
            config,
            host_platform="linux",
            mkarchiso_path=str(fake_mkarchiso),
            runner=runner,
            mountpoints_reader=lambda: (),
            clock=lambda: 1_800_000_000,
        )
    assert "pacman fixture failure" in str(exc.value)

    target_commands = [command for command in runner.calls if command[:2] == ["pacman", "--config"]]
    assert len(target_commands) == 1
    assert target_commands[0][2] == "/dev/null"
    assert target_commands[0][-1] == "-Q"
    assert list(config.output_dir.iterdir()) == []


def test_nonzero_fake_mkarchiso_status_prevents_candidate_publication(tmp_path):
    config = _config(tmp_path)
    runner = _FakeBuilder(mkarchiso_returncode=23)
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir()
    fake_mkarchiso = fake_tools / "mkarchiso"
    _write_executable(fake_mkarchiso, "synthetic mkarchiso executable fixture\n")

    with pytest.raises(build_usb.BuildContractError, match=r"command failed \(23\)") as exc:
        build_usb.execute_build(
            config,
            host_platform="linux",
            mkarchiso_path=str(fake_mkarchiso),
            runner=runner,
            mountpoints_reader=lambda: (),
            clock=lambda: 1_800_000_000,
        )
    assert "synthetic mkarchiso failure" in str(exc.value)
    assert list(config.output_dir.iterdir()) == []


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
    assert 'ensure_systemd_link "../autoloader.service"' in hook
    assert "systemd-nsresourced" not in hook
    assert not enabled.exists(), "activation drop-in is generated in the isolated staging profile"
    assert not (profile / "airootfs/etc/systemd/system/getty@tty1.service.d/autologin.conf").exists()
    ventoy = json.loads((build_usb.REPOSITORY_ROOT / "usb_builder/ventoy/ventoy.json").read_text(encoding="utf-8"))
    assert "auto_install" not in ventoy
