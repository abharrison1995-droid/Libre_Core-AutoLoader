"""No-launch regressions for the disposable retry8 package-query candidate."""

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
REPO_ROOT = EVIDENCE_DIR.parents[5]
HELPER_PATH = EVIDENCE_DIR / "guest_probe.py"
EXPECTED_MANIFEST_SHA256 = "176ae9db968ddf6fae1718c793e924dc42937b0bb9d3044c08e45937fc5396c5"


def load_helper():
    spec = importlib.util.spec_from_file_location("retry8_guest_probe", HELPER_PATH)
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


def test_archived_retries_one_through_seven_all_match_their_full_manifests() -> None:
    verified = probe.verify_archived_evidence(REPO_ROOT / probe.EVIDENCE_RELATIVE)
    assert "results/retry7/SHA256SUMS" in verified
    assert "results/retry7/serial.log" in verified


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


PINNED_PACKAGES = {
    "arch-install-scripts": "31-2",
    "archiso": "91-1",
    "bash": "5.3.20-1",
    "libcap": "2.78-1",
    "pacman": "7.1.0.r9.g54d9411-2",
    "shadow": "4.20.0.arch1-1",
    "util-linux": "2.42.4-1",
}


class FakeProcess:
    def __init__(self, argv, *, stdout, returncode=0, pending=False):
        self.argv = argv
        self.pid = 424242
        self.returncode = None if pending else returncode
        self._final_returncode = returncode
        self.pending = pending
        stdout.write(b"" if pending else b"archiso 91-1\n")
        stdout.flush()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = self._final_returncode
        return self.returncode


def test_query_argv_is_exactly_pacman_local_query() -> None:
    argv = probe.locked_package_query_argv(PINNED_PACKAGES)
    assert argv == ["/usr/bin/pacman", "-Q", *sorted(PINNED_PACKAGES)]
    assert not any(arg in {"-S", "-Sy", "-Syu", "--sync", "--refresh"} for arg in argv)


def test_locked_package_parser_accepts_exact_pinned_output_only() -> None:
    output = "".join(f"{name} {version}\n" for name, version in sorted(PINNED_PACKAGES.items()))
    assert probe.parse_locked_package_output(output, PINNED_PACKAGES) == PINNED_PACKAGES

    wrong = output.replace("archiso 91-1", "archiso 90-1")
    with pytest.raises(RuntimeError, match="do not match"):
        probe.parse_locked_package_output(wrong, PINNED_PACKAGES)
    with pytest.raises(RuntimeError, match="missing or extra"):
        probe.parse_locked_package_output(output + "unexpected 1\n", PINNED_PACKAGES)
    duplicate_rows = output.splitlines()
    duplicate_rows[-1] = duplicate_rows[0]
    with pytest.raises(RuntimeError, match="malformed or duplicate"):
        probe.parse_locked_package_output("\n".join(duplicate_rows), PINNED_PACKAGES)
    malformed_rows = output.splitlines()
    malformed_rows[0] = "bad row has extra fields"
    with pytest.raises(RuntimeError, match="malformed or duplicate"):
        probe.parse_locked_package_output("\n".join(malformed_rows), PINNED_PACKAGES)


def test_query_captures_output_and_returns_only_exact_lock_match(tmp_path: Path, capsys) -> None:
    output_path = tmp_path / "pacman-output.txt"
    captured: dict[str, object] = {}

    def factory(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        stream = kwargs["stdout"]
        stream.write("".join(f"{name} {version}\n" for name, version in sorted(PINNED_PACKAGES.items())).encode())
        stream.flush()
        process = FakeProcess.__new__(FakeProcess)
        process.argv = argv
        process.pid = 424242
        process.returncode = 0
        process._final_returncode = 0
        process.pending = False
        return process

    result = probe.query_locked_packages(PINNED_PACKAGES, output_path=output_path, popen_factory=factory)
    assert result == PINNED_PACKAGES
    assert captured["argv"] == ["/usr/bin/pacman", "-Q", *sorted(PINNED_PACKAGES)]
    assert captured["kwargs"]["start_new_session"] is True
    assert captured["kwargs"]["close_fds"] is True
    assert captured["kwargs"]["stdin"] == subprocess.DEVNULL
    assert captured["kwargs"]["stderr"] == subprocess.STDOUT
    assert output_path.read_text(encoding="utf-8").splitlines() == [
        f"{name} {version}" for name, version in sorted(PINNED_PACKAGES.items())
    ]
    transcript = capsys.readouterr().out
    assert "LOCKED_PACKAGE_QUERY_OUTCOME=COMPLETE_PIN_MATCH" in transcript
    assert '"output_bytes":' in transcript


def test_query_nonzero_exit_is_incomplete_and_never_matches_lock(tmp_path: Path, capsys) -> None:
    output_path = tmp_path / "pacman-output.txt"

    def factory(argv, **kwargs):
        process = FakeProcess.__new__(FakeProcess)
        process.argv = argv
        process.pid = 424242
        process.returncode = 1
        process._final_returncode = 1
        process.pending = False
        kwargs["stdout"].write(b"error: package database unavailable\n")
        kwargs["stdout"].flush()
        return process

    with pytest.raises(RuntimeError, match="INCOMPLETE.*exited 1"):
        probe.query_locked_packages(PINNED_PACKAGES, output_path=output_path, popen_factory=factory)
    assert "LOCKED_PACKAGE_QUERY_OUTCOME=INCOMPLETE_NONZERO_EXIT" in capsys.readouterr().out


def test_query_timeout_emits_heartbeats_and_kills_process_group(tmp_path: Path, capsys) -> None:
    output_path = tmp_path / "pacman-output.txt"
    now = [0.0]
    sent_signals: list[int] = []

    class PendingProcess:
        pid = 515151

        def __init__(self):
            self.returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.returncode = -15
            return self.returncode

    process = PendingProcess()

    def factory(argv, **kwargs):
        assert argv == ["/usr/bin/pacman", "-Q", *sorted(PINNED_PACKAGES)]
        kwargs["stdout"].write(b"partial\n")
        kwargs["stdout"].flush()
        return process

    def fake_killpg(pid, signum):
        assert pid == process.pid
        sent_signals.append(signum)

    def monotonic():
        return now[0]

    def sleep(seconds):
        now[0] += seconds

    with pytest.raises(RuntimeError, match="INCOMPLETE.*timed out"):
        probe.query_locked_packages(
            PINNED_PACKAGES,
            output_path=output_path,
            timeout_seconds=0.5,
            heartbeat_seconds=0.2,
            popen_factory=factory,
            monotonic=monotonic,
            sleep=sleep,
            killpg=fake_killpg,
        )
    assert sent_signals == [probe.signal.SIGTERM, probe.signal.SIGKILL]
    transcript = capsys.readouterr().out
    assert "LOCKED_PACKAGE_QUERY_HEARTBEAT=" in transcript
    assert "LOCKED_PACKAGE_QUERY_TIMEOUT=" in transcript
    assert "LOCKED_PACKAGE_QUERY_OUTCOME=INCOMPLETE_TIMEOUT" in transcript
    assert "LOCKED_PACKAGE_QUERY_OUTCOME=COMPLETE_PIN_MATCH" not in transcript


def test_query_capture_path_is_exclusive_and_never_spawns_on_collision(tmp_path: Path) -> None:
    output_path = tmp_path / "already-present"
    output_path.write_text("preserve", encoding="ascii")
    spawned = False

    def factory(argv, **kwargs):
        nonlocal spawned
        spawned = True
        raise AssertionError("pacman process must not start")

    with pytest.raises(RuntimeError, match="exclusive pacman output capture"):
        probe.query_locked_packages(PINNED_PACKAGES, output_path=output_path, popen_factory=factory)
    assert not spawned
    assert output_path.read_text(encoding="ascii") == "preserve"


def test_query_failure_stops_before_identity_mount_or_scratch(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        probe,
        "run",
        lambda argv, **kwargs: SimpleNamespace(stdout="2026-10-17T00:00:00Z\n" if argv[0].endswith("date") else "guest\n", returncode=0),
    )
    monkeypatch.setattr(probe, "verify_source_mount", lambda: "source /mnt/source 9p ro")
    monkeypatch.setattr(probe, "verify_guest_helper_binding", lambda _expected: "a" * 64)
    monkeypatch.setattr(probe, "verify_usb_builder_source_manifest", lambda: {"manifest_sha256": EXPECTED_MANIFEST_SHA256, "file_count": 24})
    monkeypatch.setattr(probe, "require_readonly_source_write_open", lambda _path: "BLOCKED errno=30")
    monkeypatch.setattr(probe, "exact_locked_packages", lambda: PINNED_PACKAGES)
    monkeypatch.setattr(
        probe,
        "query_locked_packages",
        lambda _expected: (_ for _ in ()).throw(RuntimeError("LOCKED_PACKAGE_QUERY_INCOMPLETE: timeout")),
    )
    monkeypatch.setattr(probe, "prepare_build_identity", lambda: calls.append("identity"))
    monkeypatch.setattr(probe, "run_rootless_preflights", lambda: calls.append("preflight"))
    monkeypatch.setattr(probe, "verify_scratch_device", lambda: calls.append("scratch-device"))
    monkeypatch.setattr(probe, "verify_scratch_write", lambda: calls.append("scratch-write"))
    monkeypatch.setenv("LC_EXPECTED_GUEST_HELPER_SHA256", "a" * 64)
    monkeypatch.setenv("LC_EXPECTED_USB_BUILDER_MANIFEST_SHA256", EXPECTED_MANIFEST_SHA256)
    with pytest.raises(RuntimeError, match="LOCKED_PACKAGE_QUERY_INCOMPLETE"):
        probe.main()
    assert calls == []


def test_retry8_runner_has_finite_query_and_outer_bounds_and_uses_9p_helper() -> None:
    runner = (EVIDENCE_DIR / "run_arch_guest.py").read_text(encoding="utf-8")
    assert "WALL_SECONDS = 1800" in runner
    assert "GUEST_PROBE_COMMAND_SECONDS = 900" in runner
    assert '"guest_probe_command_timeout_seconds": GUEST_PROBE_COMMAND_SECONDS' in runner
    assert "PACMAN_QUERY_TIMEOUT_SECONDS = 300.0" in HELPER_PATH.read_text(encoding="utf-8")
    assert "PACMAN_QUERY_HEARTBEAT_SECONDS = 15.0" in HELPER_PATH.read_text(encoding="utf-8")
    assert "cp -- '{guest_source_helper}' /run/lc-guest-probe.py" in runner
    assert "helper_b64" not in runner
    assert "LOCKED_PACKAGE_QUERY_OUTCOME=COMPLETE_PIN_MATCH" in runner
