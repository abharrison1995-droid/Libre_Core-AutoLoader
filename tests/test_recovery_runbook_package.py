"""Wheel-install smoke coverage for the recovery-readiness runbook resource."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path

import pytest


def test_installed_wheel_contains_runbook_accepted_by_readiness_gate(tmp_path: Path) -> None:
    if find_spec("setuptools") is None or find_spec("wheel") is None:
        pytest.skip("wheel smoke requires the already-installed setuptools and wheel build tools")

    repository = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    source.mkdir()
    shutil.copy2(repository / "pyproject.toml", source / "pyproject.toml")
    shutil.copy2(repository / "README.md", source / "README.md")
    shutil.copytree(
        repository / "autoloader",
        source / "autoloader",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheelhouse),
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    wheels = list(wheelhouse.glob("*.whl"))
    assert len(wheels) == 1

    installed = tmp_path / "installed"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-compile",
            "--target",
            str(installed),
            str(wheels[0]),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )

    packaged_runbook = installed / "autoloader" / "flasher" / "data" / "RECOVERY_RUNBOOK.md"
    assert packaged_runbook.is_file()
    assert packaged_runbook.read_bytes() == (
        repository / "autoloader" / "flasher" / "data" / "RECOVERY_RUNBOOK.md"
    ).read_bytes()

    outside = tmp_path / "outside-source"
    outside.mkdir()
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join((str(installed), str(repository)))
    environment["LIBRECORE_WHEEL_TEST_TARGET"] = str(installed)
    script = """
import os
import tempfile
from pathlib import Path
from tests.test_phase03_spi_safety import (
    MIB, FakeTransport, _capture, _make_service, _make_x200, _payload, _probe, _probe_line,
)
from autoloader.flasher.recovery import (
    RecoveryReadinessService, _installed_recovery_runbook_is_valid,
)

with tempfile.TemporaryDirectory(prefix="runbook-wheel-readiness-") as temporary:
    root = Path(temporary)
    plan = _make_x200(root)
    content = _payload(4 * MIB, 73)
    transport = FakeTransport([_probe_line("W25Q32.V", len(content))], {len(content): content})
    probes = _probe(transport)
    service, primary, recovery = _make_service(root, transport)
    backup = _capture(service, plan, probes, primary, recovery)
    readiness = RecoveryReadinessService().evaluate(plan, backup)
    runbook = Path(readiness.runbook)
    target = Path(os.environ["LIBRECORE_WHEEL_TEST_TARGET"]).resolve()
    assert readiness.status == "ready", readiness.reasons
    assert runbook.is_file()
    assert runbook.resolve().is_relative_to(target)
    assert runbook.read_bytes() == (target / "autoloader/flasher/data/RECOVERY_RUNBOOK.md").read_bytes()
    assert _installed_recovery_runbook_is_valid() is True
"""
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=outside,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
