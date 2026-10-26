"""No-launch regressions for the disposable retry7 source/import gates."""

from __future__ import annotations

import errno
import hashlib
import importlib.util
import os
import struct
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


EVIDENCE_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVIDENCE_DIR.parents[4]
HELPER_PATH = EVIDENCE_DIR / "guest_probe.py"
EXPECTED_MANIFEST_SHA256 = "176ae9db968ddf6fae1718c793e924dc42937b0bb9d3044c08e45937fc5396c5"


def load_helper():
    spec = importlib.util.spec_from_file_location("retry7_guest_probe", HELPER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = load_helper()


def test_fixed_manifest_matches_current_guest_source_inventory() -> None:
    verified = probe.verify_usb_builder_source_manifest(REPO_ROOT)
    assert verified["manifest_sha256"] == EXPECTED_MANIFEST_SHA256
    assert verified["file_count"] == 24
    assert set(verified["files"]) == set(probe.USB_BUILDER_SOURCE_MANIFEST)


def test_host_runner_binds_helper_manifest_and_guest_does_not_run_git() -> None:
    runner_path = EVIDENCE_DIR / "run_arch_guest.py"
    runner_source = runner_path.read_text(encoding="utf-8")
    expected_helper = hashlib.sha256(HELPER_PATH.read_bytes()).hexdigest()
    assert f'EXPECTED_GUEST_PROBE_SHA256 = "{expected_helper}"' in runner_source
    assert f'EXPECTED_USB_BUILDER_MANIFEST_SHA256 = "{EXPECTED_MANIFEST_SHA256}"' in runner_source
    assert "/usr/bin/git" not in HELPER_PATH.read_text(encoding="utf-8")
    assert 'GUEST_GIT_CHECK=NOT_PERFORMED; host Git HEAD/status validation only' in HELPER_PATH.read_text(encoding="utf-8")


def test_manifest_rejects_changed_bytes_and_symlink_components(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    source = root / "usb_builder" / "module.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"value = 1\n")
    manifest = {
        "usb_builder/module.py": {
            "type": "regular",
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
    }
    digest = probe.manifest_sha256(manifest)
    assert probe.verify_usb_builder_source_manifest(root, manifest, digest)["file_count"] == 1

    source.write_bytes(b"value = 2\n")
    with pytest.raises(RuntimeError, match="source SHA-256 mismatch"):
        probe.verify_usb_builder_source_manifest(root, manifest, digest)

    source.unlink()
    source.symlink_to(tmp_path / "outside.py")
    with pytest.raises(RuntimeError, match="linked/non-directory|regular non-symlink"):
        probe.verify_usb_builder_source_manifest(root, manifest, digest)


def test_manifest_rejects_noncanonical_or_unexpected_inventory(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    manifest = {"usb_builder/../outside.py": {"type": "regular", "sha256": "0" * 64}}
    with pytest.raises(RuntimeError, match="manifest path"):
        probe.verify_usb_builder_source_manifest(root, manifest, probe.manifest_sha256(manifest))


def test_source_write_open_requires_exact_read_only_filesystem_error(tmp_path: Path) -> None:
    target = tmp_path / "source.py"
    target.write_text("pass\n", encoding="ascii")
    with patch.object(probe.os, "open", side_effect=OSError(errno.EROFS, "read-only")):
        assert probe.require_readonly_source_write_open(target) == f"BLOCKED errno={errno.EROFS}"
    with patch.object(probe.os, "open", side_effect=OSError(errno.EACCES, "permission denied")):
        with pytest.raises(RuntimeError, match="did not fail with EROFS"):
            probe.require_readonly_source_write_open(target)
    with patch.object(probe.os, "open", return_value=917), patch.object(probe.os, "close") as close:
        with pytest.raises(RuntimeError, match="unexpectedly allowed"):
            probe.require_readonly_source_write_open(target)
        close.assert_called_once_with(917)


def test_private_cache_prefix_requires_fresh_empty_private_directory(tmp_path: Path) -> None:
    fresh = tmp_path / "fresh" / "pycache"
    fresh.parent.mkdir()
    probe.prepare_private_cache_prefix(fresh)
    probe.validate_private_cache_prefix(fresh)
    assert fresh.stat().st_mode & 0o777 == 0o700

    existing_empty = tmp_path / "existing-empty"
    existing_empty.mkdir(mode=0o700)
    with pytest.raises(RuntimeError, match="already exists"):
        probe.prepare_private_cache_prefix(existing_empty)

    nonempty = tmp_path / "nonempty"
    nonempty.mkdir(mode=0o700)
    (nonempty / "stale.pyc").write_bytes(b"stale")
    with pytest.raises(RuntimeError, match="private, empty"):
        probe.validate_private_cache_prefix(nonempty)

    link = tmp_path / "cache-link"
    link.symlink_to(fresh, target_is_directory=True)
    with pytest.raises(RuntimeError, match="already exists"):
        probe.prepare_private_cache_prefix(link)


def test_cache_prefix_can_be_created_for_the_dedicated_build_uid(tmp_path: Path) -> None:
    if os.geteuid() != 0:
        pytest.skip("changing a probe-owned temporary directory to a different UID requires root")
    prefix = tmp_path / "fwbuild-cache"
    probe.prepare_private_cache_prefix(prefix, expected_uid=65534)
    assert prefix.lstat().st_uid == 65534
    assert prefix.stat().st_mode & 0o777 == 0o700


def test_private_pyc_prefix_prevents_adjacent_malicious_cache_override(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    package = source_root / "usb_builder"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("\n", encoding="ascii")
    module_path = package / "build_usb.py"
    source = b"VALUE = 'source'\n"
    module_path.write_bytes(source)
    pycache = package / "__pycache__"
    pycache.mkdir()

    source_stat = module_path.stat()
    malicious_code = compile("VALUE = 'malicious'\n", str(module_path), "exec")
    header = importlib.util.MAGIC_NUMBER + struct.pack(
        "<III", 0, int(source_stat.st_mtime), source_stat.st_size
    )
    cache_path = Path(importlib.util.cache_from_source(str(module_path)))
    cache_path.write_bytes(header + __import__("marshal").dumps(malicious_code))

    # Control: without a separate prefix the valid timestamp cache is active.
    control_env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPYCACHEPREFIX", "PYTHONDONTWRITEBYTECODE"}
    }
    control_env.update({"PYTHONPATH": str(source_root), "PYTHONDONTWRITEBYTECODE": "1"})
    control = subprocess.run(
        [sys.executable, "-P", "-c", "import usb_builder.build_usb as m; print(m.VALUE)"],
        env=control_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
        check=False,
    )
    assert control.returncode == 0, control.stdout
    assert control.stdout.strip() == "malicious"

    cache_prefix = tmp_path / "private-cache"
    cache_prefix.mkdir(mode=0o700)
    isolated = subprocess.run(
        [
            sys.executable,
            "-P",
            "-c",
            "import os,sys; import usb_builder.build_usb as m; "
            "assert sys.pycache_prefix == os.environ['PYTHONPYCACHEPREFIX']; "
            "assert sys.dont_write_bytecode is True; print(m.VALUE)",
        ],
        env={
            **os.environ,
            "PYTHONPATH": str(source_root),
            "PYTHONPYCACHEPREFIX": str(cache_prefix),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
        check=False,
    )
    assert isolated.returncode == 0, isolated.stdout
    assert isolated.stdout.strip() == "source"
    assert list(cache_prefix.iterdir()) == []


def test_child_preflight_program_is_valid_python_without_launching_it() -> None:
    code = probe.rootless_import_preflight_code()
    compile(code, "<retry7-rootless-import-preflight>", "exec")
    assert code.index("if sys.flags.optimize != 0:") < code.index("prefix = Path(")
    assert "CHILD_PYTHON_OPTIMIZE=0" in code
    assert "PYTHONPYCACHEPREFIX" in probe.run_rootless_preflights.__code__.co_consts or "rootless_import_preflight_code" in probe.run_rootless_preflights.__code__.co_names
    assert probe.rootless_preflight_command(code) == [
        "/usr/bin/setpriv",
        "--bounding-set=-all,+setuid,+setgid",
        "--reuid=1000",
        "--regid=1000",
        "--clear-groups",
        "--",
        "/usr/bin/python3",
        "-P",
        "-c",
        code,
    ]


def test_optimized_child_preflight_fails_before_assertions_or_path_checks() -> None:
    code = probe.rootless_import_preflight_code()
    result = subprocess.run(
        [sys.executable, "-O", "-c", code],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
        check=False,
        env={key: value for key, value in os.environ.items() if key != "PYTHONOPTIMIZE"},
    )
    assert result.returncode != 0
    assert "RuntimeError: optimized Python is unsupported" in result.stderr
    assert "AssertionError" not in result.stderr
    assert "FileNotFoundError" not in result.stderr


def test_rootless_child_environment_forces_nonoptimized_python(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("LC_EXPECTED_GUEST_HELPER_SHA256", "a" * 64)
    monkeypatch.setenv("PYTHONOPTIMIZE", "1")
    monkeypatch.setattr(probe, "prepare_private_cache_prefix", lambda **kwargs: None)

    def fake_run(command, *, check, env):
        captured["command"] = command
        captured["env"] = env
        lines = [
            "CHILD_PYTHON_OPTIMIZE=0",
            "CHILD_PYTHON_CACHE_POLICY=PASS",
            f"CHILD_USB_BUILDER_MANIFEST_SHA256={EXPECTED_MANIFEST_SHA256}",
            "CHILD_USB_BUILDER_MANIFEST_FILE_COUNT=24",
            "CHILD_GUEST_HELPER_SHA256=" + "a" * 64,
            "ROOTLESS_HOST_PREFLIGHT=PASS",
            "ROOTLESS_MOUNT_PREFLIGHT=PASS",
            "GUEST_PROBE_COMPLETE_NO_BUILD",
        ]
        return SimpleNamespace(returncode=0, stdout="\n".join(lines))

    monkeypatch.setattr(probe, "run", fake_run)
    probe.run_rootless_preflights()
    assert captured["env"]["PYTHONOPTIMIZE"] == "0"
    assert "PYTHONOPTIMIZE=0" in (EVIDENCE_DIR / "run_arch_guest.py").read_text(encoding="utf-8")
