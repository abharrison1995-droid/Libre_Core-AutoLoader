"""Compile-only protocol checks for the deliberately non-deployable C helper."""

from __future__ import annotations

import os
import shutil
import subprocess
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(os.name != "posix", reason="C launcher protocol uses POSIX process semantics")

SOURCE = Path(__file__).parents[1] / "usb_builder" / "rootless_offline_launcher.c"
EXPECTED_PROGRAM = "/usr/local/libexec/librecore-rootless-offline-preflight"
PROBE_ROOT = "/tmp/lc-offline-preflight-unit-test/target-root"
TOKEN = "a" * 64


def _compiler() -> str:
    compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if compiler is None:
        pytest.skip("a C compiler is not installed")
    return compiler


def _compile(tmp_path: Path, *, testing: bool) -> Path:
    output = tmp_path / ("launcher-test" if testing else "launcher-production")
    command = [_compiler(), "-std=c11", "-Wall", "-Wextra", "-Werror", str(SOURCE), "-o", str(output)]
    if testing:
        command.insert(1, "-DROOTLESS_OFFLINE_LAUNCHER_TESTING")
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
    return output


def _request() -> list[str]:
    return [
        "--offline-preflight-v1",
        "--probe-root",
        PROBE_ROOT,
        "--uid",
        "1000",
        "--gid",
        "1000",
        "--subuid",
        "100000:65536",
        "--subgid",
        "100000:65536",
        "--token",
        TOKEN,
    ]


def test_c_source_is_in_usb_builder_package_data() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    package_data = project["tool"]["setuptools"]["package-data"]["usb_builder"]
    assert "rootless_offline_launcher.c" in package_data


def test_testing_build_accepts_exact_fixed_protocol(tmp_path: Path) -> None:
    binary = _compile(tmp_path, testing=True)
    result = subprocess.run([str(binary), *_request()], check=False, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert not Path(PROBE_ROOT).exists()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda args: args.__setitem__(0, "--wrong-version"),
        lambda args: args.__setitem__(2, "/tmp/elsewhere/target-root"),
        lambda args: args.__setitem__(2, "/tmp/lc-offline-preflight-../target-root"),
        lambda args: args.__setitem__(4, "0"),
        lambda args: args.__setitem__(4, "+1000"),
        lambda args: args.__setitem__(4, " 1000"),
        lambda args: args.__setitem__(6, "0"),
        lambda args: args.__setitem__(8, "100000:65535"),
        lambda args: args.__setitem__(10, "100000:65535"),
        lambda args: args.__setitem__(8, "4294967295:65536"),
        lambda args: args.__setitem__(8, "100000:+65536"),
        lambda args: args.__setitem__(12, "A" * 64),
        lambda args: args.__setitem__(12, "a" * 63),
    ],
    ids=[
        "version",
        "foreign-root",
        "traversal-component",
        "zero-uid",
        "signed-uid",
        "whitespace-uid",
        "zero-gid",
        "short-subuid",
        "short-subgid",
        "range-overflow",
        "signed-subid-count",
        "uppercase-token",
        "short-token",
    ],
)
def test_testing_build_rejects_malformed_protocol(
    tmp_path: Path, mutation: Callable[[list[str]], None]
) -> None:
    binary = _compile(tmp_path, testing=True)
    args = _request()
    mutation(args)
    result = subprocess.run([str(binary), *args], check=False, capture_output=True, text=True, timeout=5)
    assert result.returncode == 64  # EX_USAGE
    assert result.stdout == ""
    assert result.stderr == ""
    assert not Path(PROBE_ROOT).exists()


def test_production_build_stays_unavailable_even_for_valid_protocol(tmp_path: Path) -> None:
    binary = _compile(tmp_path, testing=False)
    argv = [EXPECTED_PROGRAM, *_request()]
    result = subprocess.run(
        argv,
        executable=str(binary),
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 69  # EX_UNAVAILABLE
    assert result.stdout == ""
    assert "namespace/mount backend is not implemented or reviewed" in result.stderr
    assert not Path(PROBE_ROOT).exists()


def test_production_build_rejects_invalid_argv_before_backend(tmp_path: Path) -> None:
    binary = _compile(tmp_path, testing=False)
    argv = [EXPECTED_PROGRAM, *_request()]
    argv[2] = "/tmp/elsewhere/target-root"
    result = subprocess.run(
        argv,
        executable=str(binary),
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 64  # EX_USAGE
    assert "fixed launcher argv rejected" in result.stderr
    assert "backend is not implemented" not in result.stderr
    assert not Path(PROBE_ROOT).exists()
