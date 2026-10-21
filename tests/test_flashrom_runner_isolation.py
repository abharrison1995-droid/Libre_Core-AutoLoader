"""Fake-only tests for read-only flashrom executable and process isolation."""
from __future__ import annotations

import os
import hashlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import autoloader.flasher.flashrom as flashrom_module
from autoloader.flasher.flashrom import FlashromRunner


def test_flashrom_identity_and_probe_share_pinned_executable_and_fixed_policy(tmp_path, monkeypatch):
    trusted = tmp_path / "trusted" / "flashrom"
    attacker = tmp_path / "attacker" / "flashrom"
    trusted.parent.mkdir()
    attacker.parent.mkdir()
    trusted.write_bytes(b"trusted synthetic flashrom")
    attacker.write_bytes(b"attacker synthetic flashrom")

    monkeypatch.setattr(
        flashrom_module.shutil,
        "which",
        lambda name: str(trusted) if name == "flashrom" else None,
    )

    def fake_provenance(path):
        resolved = str(Path(path).resolve())
        if resolved != pinned_executable:
            return None
        content = Path(resolved).read_bytes()
        info = Path(resolved).stat()
        return flashrom_module._TrustedExecutable(
            file=flashrom_module._FileFingerprint(
                path=resolved,
                sha256=hashlib.sha256(content).hexdigest(),
                device=info.st_dev,
                inode=info.st_ino,
                size=info.st_size,
                mtime_ns=info.st_mtime_ns,
                ctime_ns=info.st_ctime_ns,
                mode=info.st_mode,
                uid=info.st_uid,
                gid=info.st_gid,
            ),
            package_owner="pacman: test/flashrom 1.4.0-1",
        )

    pinned_executable = str(trusted.resolve())
    monkeypatch.setattr(flashrom_module, "_trusted_executable_provenance", fake_provenance)
    runner = FlashromRunner()
    assert runner.tool_path == pinned_executable

    # A later PATH lookup would select a different executable. The runner
    # must continue using the path that was pinned when it was constructed.
    monkeypatch.setattr(
        flashrom_module.shutil,
        "which",
        lambda name: str(attacker) if name in {"flashprog", "flashrom"} else None,
    )
    for name, value in {
        "PATH": str(attacker.parent),
        "HOME": str(tmp_path / "attacker-home"),
        "TMPDIR": str(tmp_path / "attacker-temp"),
        "LD_PRELOAD": str(attacker),
    }.items():
        monkeypatch.setenv(name, value)
    parent_environment_before = dict(os.environ)

    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, "flashrom v1.4.0", "")
        return subprocess.CompletedProcess(argv, 0, "synthetic read-only probe", "")

    monkeypatch.setattr(flashrom_module.subprocess, "run", fake_run)
    identity = runner.inspect_tool()
    success, _output, _duration = runner.run_command(["-p", "internal"], timeout=17)

    assert identity is not None
    assert identity.executable == pinned_executable
    assert identity.name == "flashrom"
    assert identity.sha256 == hashlib.sha256(trusted.read_bytes()).hexdigest()
    assert identity.package_owner == "pacman: test/flashrom 1.4.0-1"
    assert success is True
    assert [argv for argv, _kwargs in calls] == [
        [pinned_executable, "--version"],
        [pinned_executable, "-p", "internal"],
    ]
    expected_common = {
        "cwd": os.path.abspath(os.sep),
        "env": {},
        "stdin": subprocess.DEVNULL,
        "capture_output": True,
        "text": True,
        "check": False,
        "shell": False,
        "close_fds": True,
    }
    assert calls[0][1] == {**expected_common, "timeout": 10}
    assert calls[1][1] == {**expected_common, "timeout": 17}
    assert os.environ == parent_environment_before


def test_flashrom_runner_without_resolved_executable_fails_before_spawn(monkeypatch):
    monkeypatch.setattr(flashrom_module.shutil, "which", lambda _name: None)
    calls = []
    monkeypatch.setattr(
        flashrom_module.subprocess,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    runner = FlashromRunner()
    success, output, duration = runner.run_command(["-p", "internal"])

    assert runner.tool_path is None
    assert runner.inspect_tool() is None
    assert success is False
    assert "unavailable" in output.casefold()
    assert duration == 0.0
    assert calls == []


def test_user_writable_path_candidate_is_not_trusted_and_never_spawned(tmp_path, monkeypatch):
    fake = tmp_path / "user-bin" / "flashrom"
    fake.parent.mkdir()
    fake.write_bytes(b"user controlled binary")
    monkeypatch.setattr(
        flashrom_module.shutil,
        "which",
        lambda name: str(fake) if name in {"flashprog", "flashrom"} else None,
    )
    monkeypatch.setattr(flashrom_module, "_trusted_executable_provenance", lambda _path: None)
    calls = []
    monkeypatch.setattr(flashrom_module.subprocess, "run", lambda *a, **kw: calls.append((a, kw)))

    runner = FlashromRunner()
    assert runner.tool_path is None
    assert runner.inspect_tool() is None
    assert runner.run_command(["-p", "internal"])[0] is False
    assert calls == []


def test_trusted_path_replacement_during_version_or_probe_fails_closed(tmp_path, monkeypatch):
    executable = tmp_path / "flashrom"
    executable.write_bytes(b"trusted bytes")

    def dynamic_provenance(path):
        resolved = str(Path(path).resolve())
        if resolved != str(executable.resolve()):
            return None
        content = Path(resolved).read_bytes()
        info = Path(resolved).stat()
        return flashrom_module._TrustedExecutable(
            file=flashrom_module._FileFingerprint(
                path=resolved,
                sha256=hashlib.sha256(content).hexdigest(),
                device=info.st_dev,
                inode=info.st_ino,
                size=info.st_size,
                mtime_ns=info.st_mtime_ns,
                ctime_ns=info.st_ctime_ns,
                mode=info.st_mode,
                uid=info.st_uid,
                gid=info.st_gid,
            ),
            package_owner="pacman: test/flashrom 1.4.0-1",
        )

    monkeypatch.setattr(flashrom_module.shutil, "which", lambda name: str(executable) if name == "flashrom" else None)
    monkeypatch.setattr(flashrom_module, "_trusted_executable_provenance", dynamic_provenance)

    runner = FlashromRunner()
    def replace_during_version(argv, **_kwargs):
        executable.write_bytes(b"replacement bytes")
        return subprocess.CompletedProcess(argv, 0, "flashrom v1.4.0", "")

    monkeypatch.setattr(flashrom_module.subprocess, "run", replace_during_version)
    assert runner.inspect_tool() is None

    executable.write_bytes(b"trusted bytes")
    runner = FlashromRunner()
    def replace_during_probe(argv, **_kwargs):
        executable.write_bytes(b"different replacement")
        return subprocess.CompletedProcess(argv, 0, "synthetic probe", "")

    monkeypatch.setattr(flashrom_module.subprocess, "run", replace_during_probe)
    success, output, _duration = runner.run_command(["-p", "internal"])
    assert success is False
    assert "identity changed" in output


def test_root_owned_mode_policy_rejects_untrusted_files_and_parents():
    from stat import S_IFDIR, S_IFREG

    def info(mode: int, uid: int = 0):
        return SimpleNamespace(st_mode=mode, st_uid=uid)

    assert flashrom_module._trusted_root_owned_stat(info(S_IFREG | 0o755), directory=False)
    assert not flashrom_module._trusted_root_owned_stat(info(S_IFREG | 0o755, uid=1000), directory=False)
    assert not flashrom_module._trusted_root_owned_stat(info(S_IFREG | 0o775), directory=False)
    assert not flashrom_module._trusted_root_owned_stat(info(S_IFREG | 0o644), directory=False)
    assert flashrom_module._trusted_root_owned_stat(info(S_IFDIR | 0o755), directory=True)
    assert not flashrom_module._trusted_root_owned_stat(info(S_IFDIR | 0o775), directory=True)


def test_windows_has_no_implicit_trusted_flashrom_policy(tmp_path, monkeypatch):
    executable = tmp_path / "flashrom.exe"
    executable.write_bytes(b"synthetic executable")
    monkeypatch.setattr(flashrom_module.sys, "platform", "win32")
    assert flashrom_module._fingerprint_trusted_linux_file(str(executable.resolve())) is None


def test_package_query_requires_protected_manager_and_exact_owner_record(monkeypatch):
    calls = []
    monkeypatch.setattr(flashrom_module, "_fingerprint_trusted_linux_file", lambda _path: None)
    monkeypatch.setattr(
        flashrom_module.subprocess,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    assert flashrom_module._run_trusted_package_query("/usr/bin/pacman", ["-Qo", "--", "/usr/bin/flashrom"]) is None
    assert calls == []


def test_package_owner_parser_requires_exact_system_manager_path(monkeypatch):
    package_query = flashrom_module._linux_package_owner
    calls = []

    def fake_query(manager, args):
        calls.append((manager, args))
        if manager.endswith("pacman"):
            return subprocess.CompletedProcess(
                [manager, *args], 0, "/usr/bin/flashrom is owned by extra/flashrom 1.4.0-1\n", ""
            )
        raise AssertionError("dpkg should not be consulted after an exact pacman owner record")

    monkeypatch.setattr(flashrom_module, "_run_trusted_package_query", fake_query)
    assert package_query("/usr/bin/flashrom") == "pacman: extra/flashrom 1.4.0-1"
    assert calls == [("/usr/bin/pacman", ["-Qo", "--", "/usr/bin/flashrom"])]

    monkeypatch.setattr(
        flashrom_module,
        "_run_trusted_package_query",
        lambda manager, args: subprocess.CompletedProcess(
            [manager, *args], 0, "extra/flashrom is owned by /usr/bin/flashrom\n", ""
        ) if manager.endswith("pacman") else None,
    )
    assert flashrom_module._linux_package_owner("/usr/bin/not-flashrom") is None

    def dpkg_query(manager, args):
        if manager.endswith("pacman"):
            return None
        assert args == ["-S", "--", "/usr/bin/flashrom"]
        return subprocess.CompletedProcess(
            [manager, *args], 0, "flashrom: /usr/bin/flashrom\n", ""
        )

    monkeypatch.setattr(flashrom_module, "_run_trusted_package_query", dpkg_query)
    assert flashrom_module._linux_package_owner("/usr/bin/flashrom") == "dpkg: flashrom: /usr/bin/flashrom"
