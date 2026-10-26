"""Keep builder-host tooling distinct from the installed live-system package set."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from usb_builder import build_usb


REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = REPO_ROOT / "usb_builder" / "snapshot.lock.json"
TARGET_PACKAGES_PATH = REPO_ROOT / "usb_builder" / "archiso_profile" / "packages.x86_64"
RETRY8_RESULT_PATH = (
    REPO_ROOT
    / "docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry8/run-result.json"
)
RETRY8_SUMS_PATH = RETRY8_RESULT_PATH.parent / "SHA256SUMS"
RETRY8_RESULT_SHA256 = "2fc3e0c3231834f8d498853793cc709b570793d89230b08e37e727425a35b0c2"
RETRY8_HELPER_PATH = (
    REPO_ROOT
    / "docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/retry8-candidate/guest_probe.py"
)


def _load_retry8_helper():
    pytest.importorskip("pwd", reason="the disposable guest helper is Linux-only")
    spec = importlib.util.spec_from_file_location("retry8_package_context_probe", RETRY8_HELPER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _pinned_builder_packages(lock: dict[str, object]) -> dict[str, str]:
    rootless = lock["rootless_builder"]
    assert isinstance(rootless, dict)
    package_keys = (
        (lock, "archiso_package", "archiso_version"),
        (rootless, "arch_install_scripts_package", "arch_install_scripts_version"),
        (rootless, "pacman_package", "pacman_version"),
        (rootless, "util_linux_package", "util_linux_version"),
        (rootless, "bash_package", "bash_version"),
        (rootless, "map_helper_package", "map_helper_package_version"),
        (rootless, "libcap_package", "libcap_version"),
    )
    return {str(source[name]): str(source[version]) for source, name, version in package_keys}


def test_archiso_lock_is_builder_host_tool_and_profile_uses_runtime_package() -> None:
    lock = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    target_packages = set(TARGET_PACKAGES_PATH.read_text(encoding="utf-8").splitlines())

    assert lock["archiso_package"] == "archiso"
    assert lock["archiso_version"] == "91-1"
    assert "archiso" in build_usb.ROOTLESS_HOST_PACKAGES
    assert "archiso" not in target_packages
    assert "mkinitcpio-archiso" in target_packages


def test_retry8_missing_host_archiso_stays_incomplete_before_later_guest_gates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    helper = _load_retry8_helper()
    raw_result = RETRY8_RESULT_PATH.read_bytes()
    sums_text = RETRY8_SUMS_PATH.read_text(encoding="ascii")
    assert hashlib.sha256(raw_result).hexdigest() == RETRY8_RESULT_SHA256
    assert f"{RETRY8_RESULT_SHA256}  run-result.json" in sums_text

    result = json.loads(raw_result)
    capture_prefix = "LOCKED_PACKAGE_QUERY_CAPTURE="
    capture_line = next(
        line for line in result["guest_locked_package_query_records"] if line.startswith(capture_prefix)
    )
    capture = json.loads(capture_line[len(capture_prefix) :])
    assert capture["returncode"] == 1
    assert capture["elapsed_seconds"] == 140.495
    assert "error: package 'archiso' was not found" in capture["output"]

    # pacman emitted two DB warnings and the missing-package diagnostic around
    # the six actual version rows; give those captured rows to the exact parser.
    six_rows = "\n".join(
        line
        for line in capture["output"].splitlines()
        if re.fullmatch(r"[A-Za-z0-9@._+-]+\s+\S+", line)
    )
    assert len(six_rows.splitlines()) == 6
    expected = _pinned_builder_packages(json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8")))
    assert expected["archiso"] == "91-1"
    with pytest.raises(RuntimeError, match="missing or extra package row"):
        helper.parse_locked_package_output(six_rows, expected)

    # Replay the captured query result through the real bounded query and main
    # sequencing, but with a fake child process and fake pre-query observations.
    calls: list[str] = []

    class CapturedPacmanFailure:
        def __init__(self, argv, **kwargs):
            assert argv == ["/usr/bin/pacman", "-Q", *sorted(expected)]
            assert kwargs["stdin"] == subprocess.DEVNULL
            assert kwargs["stderr"] == subprocess.STDOUT
            assert kwargs["close_fds"] is True
            kwargs["stdout"].write(capture["output"].encode("utf-8"))
            kwargs["stdout"].flush()
            self.pid = 524
            self.returncode = capture["returncode"]

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    real_query = helper.query_locked_packages

    def captured_query(package_map):
        return real_query(
            package_map,
            output_path=tmp_path / "pacman-query-output.txt",
            popen_factory=CapturedPacmanFailure,
        )

    monkeypatch.setattr(
        helper,
        "run",
        lambda argv, **kwargs: SimpleNamespace(
            stdout=("2026-10-17T01:26:06Z\n" if argv[0].endswith("date") else "guest\n"),
            returncode=0,
        ),
    )
    monkeypatch.setattr(helper, "verify_source_mount", lambda: "source /mnt/source 9p ro")
    monkeypatch.setattr(helper, "verify_guest_helper_binding", lambda _expected: "a" * 64)
    monkeypatch.setattr(
        helper,
        "verify_usb_builder_source_manifest",
        lambda: {"manifest_sha256": helper.EXPECTED_USB_BUILDER_MANIFEST_SHA256, "file_count": 24},
    )
    monkeypatch.setattr(helper, "require_readonly_source_write_open", lambda _path: "BLOCKED errno=30")
    monkeypatch.setattr(helper, "exact_locked_packages", lambda: expected)
    monkeypatch.setattr(helper, "query_locked_packages", captured_query)
    monkeypatch.setattr(helper, "prepare_build_identity", lambda: calls.append("fwbuild-identity"))
    monkeypatch.setattr(helper, "run_rootless_preflights", lambda: calls.append("rootless-preflight"))
    monkeypatch.setattr(helper, "verify_scratch_device", lambda: calls.append("scratch-device"))
    monkeypatch.setattr(helper, "verify_scratch_write", lambda: calls.append("scratch-write"))
    monkeypatch.setenv("LC_EXPECTED_GUEST_HELPER_SHA256", "a" * 64)
    monkeypatch.setenv("LC_EXPECTED_USB_BUILDER_MANIFEST_SHA256", helper.EXPECTED_USB_BUILDER_MANIFEST_SHA256)

    with pytest.raises(RuntimeError, match="LOCKED_PACKAGE_QUERY_INCOMPLETE: pacman -Q exited 1"):
        helper.main()

    assert calls == []
    output = capsys.readouterr().out
    assert "LOCKED_PACKAGE_QUERY_OUTCOME=INCOMPLETE_NONZERO_EXIT" in output
    assert "GUEST_PROBE_COMPLETE_NO_BUILD" not in output
