"""Focused subprocess-boundary tests for the physical writer runner."""
from __future__ import annotations

import os
import subprocess

import autoloader.workflow.write_service as write_service
from autoloader.workflow.write_approval import FixedWriteCommand
from autoloader.workflow.write_service import SubprocessWriteCommandRunner


def test_writer_subprocess_uses_empty_environment_root_cwd_and_closed_descriptors(monkeypatch):
    command = FixedWriteCommand(
        executable="/usr/bin/flashrom",
        executable_sha256="a" * 64,
        tool_name="flashrom",
        tool_version="1.4.0",
        tool_package_owner="pacman: test/flashrom 1.4.0-1",
        argv=("-p", "ch341a_spi", "-c", "W25Q64.V", "-w", "/tmp/selected.rom"),
    )
    policy = write_service._writer_execution_policy_payload()
    invocation = write_service._ConsumedWriteInvocation(
        command=command,
        cwd=os.path.abspath(os.sep),
        env={},
        stdin=subprocess.DEVNULL,
        close_fds=True,
        shell=False,
        capture_output=True,
        text=True,
        check=False,
    )
    monkeypatch.setattr(write_service, "_consume_approved_write_capability", lambda _capability: invocation)
    for name, value in {
        "PATH": "C:\\attacker\\bin",
        "HOME": "C:\\attacker\\home",
        "TMPDIR": "C:\\attacker\\tmp",
        "LD_PRELOAD": "C:\\attacker\\payload.dll",
    }.items():
        monkeypatch.setenv(name, value)
    parent_environment_before = dict(os.environ)
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = SubprocessWriteCommandRunner().run(object(), 30)

    assert result.returncode == 0
    assert captured["argv"] == [command.executable, *command.argv]
    assert captured["cwd"] == os.path.abspath(os.sep)
    assert captured["env"] == {}
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["close_fds"] is True
    assert captured["shell"] is False
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["check"] is False
    assert captured["timeout"] == 30
    assert os.environ == parent_environment_before
    assert policy == write_service._writer_execution_policy_payload()
