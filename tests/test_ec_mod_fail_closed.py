"""V1 EC modification APIs fail closed until a reviewed workflow exists."""

from pathlib import Path
import subprocess

import pytest

from autoloader.detection.bios import BIOSInfo
from autoloader.detection.model import DetectionResult
from autoloader.ec_mod.thinkpad_ec import ThinkPadECManager
from autoloader.exceptions import ECModError


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ThinkPadECManager:
    monkeypatch.setattr(
        "autoloader.ec_mod.thinkpad_ec.ModelDetector.detect",
        lambda self: DetectionResult("t430", "ThinkPad T430", "2349", "PF00001", "high"),
    )
    monkeypatch.setattr(
        "autoloader.ec_mod.thinkpad_ec.BIOSDetector.detect",
        lambda self: BIOSInfo("2.60", "2013-05-10", "Lenovo", True),
    )
    return ThinkPadECManager(model="t430", working_dir=tmp_path / "ec-work")


@pytest.mark.parametrize(
    ("operation", "args"),
    [
        ("download_source", ()),
        ("build_ec_firmware", (["make", "patched.t430.img"],)),
        ("flash_ec", (Path("missing-ec-image.img"),)),
        ("verify_ec", ()),
    ],
)
def test_legacy_ec_operations_fail_before_external_or_file_calls(
    manager: ThinkPadECManager,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    args: tuple[object, ...],
) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(subprocess, "run", lambda *call_args, **kwargs: calls.append(call_args))
    write_calls: list[Path] = []
    monkeypatch.setattr(Path, "write_bytes", lambda path, data: write_calls.append(path))
    monkeypatch.setattr(Path, "write_text", lambda path, data, *a, **k: write_calls.append(path))

    with pytest.raises(ECModError, match="unavailable in V1"):
        getattr(manager, operation)(*args)

    assert calls == []
    assert write_calls == []
    assert not manager.working_dir.exists()


def test_legacy_build_does_not_touch_an_existing_arbitrary_checkout(
    manager: ThinkPadECManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager.repo_dir.mkdir(parents=True)
    sentinel = manager.repo_dir / "sentinel.txt"
    sentinel.write_text("untrusted existing checkout", encoding="utf-8")
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(subprocess, "run", lambda *call_args, **kwargs: calls.append(call_args))

    with pytest.raises(ECModError, match="unavailable in V1"):
        manager.build_ec_firmware(["make", "patched.t430.img"])

    assert calls == []
    assert sentinel.read_text(encoding="utf-8") == "untrusted existing checkout"


def test_verification_is_unavailable_and_patch_list_does_not_advertise_capability(
    manager: ThinkPadECManager,
) -> None:
    assert manager.get_available_patches() == []
    with pytest.raises(ECModError, match="EC verification unavailable"):
        manager.verify_ec()
