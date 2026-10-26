"""Synthetic-only validation for the sealed worker image candidate contract."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import subprocess
from pathlib import Path
from typing import Any

import pytest

from usb_builder import offline_vm_image as image
from usb_builder import offline_vm_worker


REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_PIN_VALUES = {
    "recipe_sha256": "1" * 64,
    "input_closure_sha256": "2" * 64,
    "package_closure_sha256": "3" * 64,
    "launcher_sha256": "4" * 64,
}


def _make_input_bundle(destination: Path) -> Path:
    for relative in image.INPUT_FILE_PINS:
        source = REPO_ROOT / Path(*relative.split("/"))
        target = destination / Path(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return destination


def _qemu_identity(path: Path) -> offline_vm_worker.ImageIdentity:
    path.write_bytes(b"synthetic-qemu-executable")
    if os.name == "posix":
        path.chmod(0o700)
    raw = path.read_bytes()
    return offline_vm_worker.ImageIdentity(path.resolve(), len(raw), hashlib.sha256(raw).hexdigest())


def _boot_assets(directory: Path) -> tuple[offline_vm_worker.ImageIdentity, offline_vm_worker.ImageIdentity]:
    directory.mkdir(parents=True, exist_ok=True)
    kernel_path = directory / "synthetic-vmlinuz"
    initramfs_path = directory / "synthetic-initramfs.img"
    if not kernel_path.exists():
        kernel_path.write_bytes(b"synthetic immutable kernel asset")
    if not initramfs_path.exists():
        initramfs_path.write_bytes(b"synthetic deterministic initramfs asset")
    identities = []
    for path in (kernel_path, initramfs_path):
        raw = path.read_bytes()
        identities.append(offline_vm_worker.ImageIdentity(path.resolve(), len(raw), hashlib.sha256(raw).hexdigest()))
    return identities[0], identities[1]


def _candidate_manifest(
    raw: bytes,
    qemu_identity: offline_vm_worker.ImageIdentity,
    kernel_image: offline_vm_worker.ImageIdentity,
    initramfs_image: offline_vm_worker.ImageIdentity,
) -> dict[str, object]:
    digest = hashlib.sha256(raw).hexdigest()
    return image._candidate_manifest_for_contract(
        raw_size=len(raw),
        raw_sha256=digest,
        qemu_identity=qemu_identity,
        kernel_image=kernel_image,
        initramfs_image=initramfs_image,
        **TEST_PIN_VALUES,
        build_provenance={
            "status": "guest-reported-build-result-pending-host-review",
            "builder_sha256": "5" * 64,
            "snapshot_lock_sha256": "6" * 64,
            "package_inventory_sha256": "7" * 64,
            "created_utc": "2026-10-25T00:00:00Z",
        },
    )


def _publish(source: Path, destination: Path, qemu: offline_vm_worker.ImageIdentity) -> offline_vm_worker.WorkerDeployment:
    raw = source.read_bytes()
    kernel_image, initramfs_image = _boot_assets(source.parent)
    return image._publish_candidate_artifact(
        source,
        destination,
        qemu,
        _candidate_manifest(raw, qemu, kernel_image, initramfs_image),
        **TEST_PIN_VALUES,
        kernel_image=kernel_image,
        initramfs_image=initramfs_image,
    )


def test_recipe_verifies_exact_input_closure_and_reports_unavailable(tmp_path: Path) -> None:
    bundle = _make_input_bundle(tmp_path / "inputs")
    recipe = image.build_recipe_manifest(bundle)

    assert recipe["status"] == "CANDIDATE_BUILD_UNAVAILABLE"
    closure = recipe["verified_source_input_closure"]
    assert isinstance(closure, dict)
    assert closure["status"] == "HASH_VERIFIED"
    assert len(closure["files"]) == len(image.INPUT_FILE_PINS)
    builder = recipe["builder_toolchain_pins"]
    assert isinstance(builder, dict)
    assert builder["status"] == "PARTIAL_TOOLCHAIN_PINS_ONLY"
    root_closure = builder["worker_root_closure"]
    seed = builder["arch_iso_seed"]
    assert isinstance(root_closure, dict) and root_closure["status"] == "INCOMPLETE"
    assert isinstance(seed, dict) and seed["final_worker_root"] is False
    systemd = recipe["systemd_resources"]
    assert isinstance(systemd, list) and len(systemd) == 4
    assert all(isinstance(unit, dict) for unit in systemd)
    assert [unit["no_new_privileges"] for unit in systemd if unit["kind"] == "service"] == ["no", "yes"]
    build_service = next(unit for unit in systemd if unit["path"].endswith("offline-build.service"))
    preflight_service = next(unit for unit in systemd if unit["path"].endswith("offline-preflight.service"))
    assert build_service["user"] == build_service["group"] == "fwbuild"
    assert build_service["private_network"] == preflight_service["private_network"] == "yes"
    assert build_service["capability_bounding_set"] == preflight_service["capability_bounding_set"] == (
        "CAP_SETUID CAP_SETGID"
    )
    fwbuild = recipe["fwbuild_identity_contract"]
    assert isinstance(fwbuild, dict)
    assert (fwbuild["username"], fwbuild["uid"], fwbuild["primary_group"], fwbuild["gid"]) == (
        "fwbuild", 1000, "fwbuild", 1000
    )
    assert fwbuild["subuid_line"] == "fwbuild:100000:65536\n"
    assert fwbuild["subgid_line"] == "fwbuild:100000:65536\n"
    fw_cfg = recipe["fw_cfg"]
    build_provenance = recipe["image_build_provenance"]
    boot_contract = recipe["boot_contract"]
    assert isinstance(fw_cfg, dict) and fw_cfg["embedded_in_raw_image"] is False
    assert isinstance(build_provenance, dict) and build_provenance["status"] == "NOT_BUILT"
    assert isinstance(boot_contract, dict) and boot_contract["status"] == "UNAVAILABLE_UNPINNED_KERNEL_AND_INITRAMFS"
    direct = boot_contract["direct_kernel_arguments"]
    assert isinstance(direct, dict) and direct["bypasses_default_x86_firmware"] is False
    firmware = boot_contract["default_qemu_firmware"]
    assert isinstance(firmware, dict) and firmware["rom_set_identity_sha256"] is None
    root_layout = boot_contract["root_layout"]
    assert isinstance(root_layout, dict)
    assert root_layout["partition_table"] == "gpt"
    assert root_layout["partition_type_guid"] == offline_vm_worker.WORKER_ROOT_PARTITION_TYPE_GUID
    assert root_layout["partuuid"] == offline_vm_worker.WORKER_ROOT_PARTUUID
    assert root_layout["filesystem_uuid"] == offline_vm_worker.WORKER_ROOT_FILESYSTEM_UUID
    assert root_layout["mount_mode"] == "read-only"
    initramfs_requirements = boot_contract["initramfs_requirements"]
    assert isinstance(initramfs_requirements, dict)
    assert initramfs_requirements["must_include_modules"] == ["virtio_pci", "virtio_blk", "ext4"]
    assert initramfs_requirements["generation_and_contents_validated"] is False
    assert "default x86 QEMU firmware" in " ".join(image.publication_blockers(recipe))
    with pytest.raises(image.OfflineVmImageUnavailable, match="complete signed worker-root"):
        image.require_publication_ready(recipe)
    assert offline_vm_worker.PRODUCTION_WORKER_DEPLOYMENT is None


def test_public_publisher_fails_before_creating_candidate_or_lock(tmp_path: Path) -> None:
    bundle = _make_input_bundle(tmp_path / "inputs")
    raw = tmp_path / "input.raw"
    raw.write_bytes(b"synthetic raw disk bytes")
    qemu = _qemu_identity(tmp_path / "qemu")
    kernel_image, initramfs_image = _boot_assets(tmp_path)
    destination = tmp_path / "candidate"

    with pytest.raises(image.OfflineVmImageUnavailable, match="IMAGE_BUILD_UNAVAILABLE"):
        image.publish_candidate(
            bundle,
            raw,
            destination,
            qemu_identity=qemu,
            kernel_image=kernel_image,
            initramfs_image=initramfs_image,
            build_provenance={},
        )

    assert not destination.exists()
    assert not (tmp_path / ".candidate.publish.lock").exists()
    assert set(path.name for path in tmp_path.iterdir()) == {
        "inputs", "input.raw", "qemu", "synthetic-vmlinuz", "synthetic-initramfs.img"
    }


@pytest.mark.parametrize("damage", ["missing", "extra", "changed"])
def test_input_bundle_rejects_missing_extra_and_hash_changed_files(tmp_path: Path, damage: str) -> None:
    bundle = _make_input_bundle(tmp_path / "inputs")
    if damage == "missing":
        (bundle / "usb_builder" / "snapshot.lock.json").unlink()
    elif damage == "extra":
        (bundle / "unexpected.txt").write_text("extra", encoding="ascii")
    else:
        with (bundle / "usb_builder" / "snapshot.lock.json").open("ab") as stream:
            stream.write(b"changed")

    with pytest.raises(image.OfflineVmImageError, match="file set differs|SHA-256 differs"):
        image.verify_input_bundle(bundle)


def test_input_bundle_rejects_symlink_and_hardlink(tmp_path: Path) -> None:
    bundle = _make_input_bundle(tmp_path / "inputs")
    original = bundle / "usb_builder" / "snapshot.lock.json"
    moved = bundle / "usb_builder" / "snapshot.lock.original"
    original.rename(moved)
    try:
        original.symlink_to(moved)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this filesystem")
    with pytest.raises(image.OfflineVmImageError, match="linked|missing"):
        image.verify_input_bundle(bundle)

    original.unlink()
    moved.rename(original)
    hardlink = bundle / "duplicate"
    try:
        os.link(original, hardlink)
    except OSError:
        pytest.skip("hard links are unavailable on this filesystem")
    with pytest.raises(image.OfflineVmImageError, match="linked|extra"):
        image.verify_input_bundle(bundle)


def test_input_reader_rejects_symlink_swap_between_lstat_and_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "input.txt"
    target.write_bytes(b"pinned original")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"different outside content")
    real_open = image.os.open
    swapped = False

    def swap_then_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if Path(path) == target and not swapped:
            swapped = True
            target.unlink()
            try:
                target.symlink_to(outside)
            except (OSError, NotImplementedError):
                pytest.skip("symlink creation is unavailable on this filesystem")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(image.os, "open", swap_then_open)
    with pytest.raises(image.OfflineVmImageError, match="without following links|changed while opening"):
        image._read_regular_bytes(target, max_bytes=1024, label="racing input")


def test_builder_package_rows_are_exactly_locked_and_ordered(tmp_path: Path) -> None:
    bundle = _make_input_bundle(tmp_path / "inputs")
    snapshot, _ = image._snapshot_from_bundle(bundle)
    valid = list(image.expected_builder_package_records(snapshot))
    assert len(valid) == 7
    assert image.validate_builder_package_records(valid, snapshot) == tuple(valid)

    mutations: list[list[dict[str, object]]] = []
    mutations.append(valid[:-1])
    mutations.append([*valid, {"name": "python", "version": "3.14", "archive_sha256": "a" * 64, "signature_sha256": "b" * 64}])
    wrong_version = [dict(item) for item in valid]
    wrong_version[0]["version"] = "90-1"
    mutations.append(wrong_version)
    wrong_hash = [dict(item) for item in valid]
    wrong_hash[1]["archive_sha256"] = "0" * 64
    mutations.append(wrong_hash)
    mutations.append(list(reversed(valid)))
    for value in mutations:
        with pytest.raises(image.OfflineVmImageError, match="package rows"):
            image.validate_builder_package_records(value, snapshot)


def test_snapshot_and_unit_changes_in_bundle_are_rejected(tmp_path: Path) -> None:
    bundle = _make_input_bundle(tmp_path / "inputs")
    unit = bundle / "usb_builder" / "systemd" / "librecore-offline-build.service"
    unit.write_bytes(unit.read_bytes().replace(b"NoNewPrivileges=no", b"NoNewPrivileges=yes"))
    with pytest.raises(image.OfflineVmImageError, match="SHA-256 differs"):
        image.build_recipe_manifest(bundle)


def test_candidate_manifest_binds_external_fw_cfg_and_fixed_boot_assets(tmp_path: Path) -> None:
    raw = b"synthetic raw image without embedded identity"
    qemu_identity = _qemu_identity(tmp_path / "qemu")
    kernel_image, initramfs_image = _boot_assets(tmp_path / "boot-assets")
    manifest = _candidate_manifest(raw, qemu_identity, kernel_image, initramfs_image)
    validated = image._validate_candidate_manifest(
        manifest,
        **TEST_PIN_VALUES,
        qemu_identity=qemu_identity,
        kernel_image=kernel_image,
        initramfs_image=initramfs_image,
    )
    assert validated["fw_cfg"]["name"] == image.WORKER_IMAGE_FW_CFG_NAME
    assert validated["fw_cfg"]["value"] == hashlib.sha256(raw).hexdigest()
    assert validated["boot_contract"]["kernel"]["sha256"] == kernel_image.sha256
    assert validated["boot_contract"]["initramfs"]["sha256"] == initramfs_image.sha256
    assert validated["boot_contract"]["qemu_executable"]["sha256"] == qemu_identity.sha256
    assert validated["boot_contract"]["kernel_append"] == offline_vm_worker.WORKER_KERNEL_APPEND
    assert validated["boot_contract"]["explicit_bios_option"] is False
    assert validated["boot_contract"]["default_qemu_firmware"]["status"] == (
        "UNRESOLVED_QEMU_DEFAULT_FIRMWARE_DEPENDENCY"
    )

    wrong = json.loads(json.dumps(manifest))
    wrong["fw_cfg"]["value"] = "0" * 64
    with pytest.raises(image.OfflineVmImageError, match="external raw-image identity"):
        image._validate_candidate_manifest(
            wrong,
            **TEST_PIN_VALUES,
            qemu_identity=qemu_identity,
            kernel_image=kernel_image,
            initramfs_image=initramfs_image,
        )
    missing = dict(manifest)
    del missing["build_provenance"]
    with pytest.raises(image.OfflineVmImageError, match="unknown or missing"):
        image._validate_candidate_manifest(
            missing,
            **TEST_PIN_VALUES,
            qemu_identity=qemu_identity,
            kernel_image=kernel_image,
            initramfs_image=initramfs_image,
        )
    missing_boot = dict(manifest)
    del missing_boot["boot_contract"]
    with pytest.raises(image.OfflineVmImageError, match="unknown or missing"):
        image._validate_candidate_manifest(
            missing_boot,
            **TEST_PIN_VALUES,
            qemu_identity=qemu_identity,
            kernel_image=kernel_image,
            initramfs_image=initramfs_image,
        )
    changed_boot = json.loads(json.dumps(manifest))
    changed_boot["boot_contract"]["kernel_append"] += " init=/bin/sh"
    with pytest.raises(image.OfflineVmImageError, match="identities or fixed boot contract"):
        image._validate_candidate_manifest(
            changed_boot,
            **TEST_PIN_VALUES,
            qemu_identity=qemu_identity,
            kernel_image=kernel_image,
            initramfs_image=initramfs_image,
        )
    for field, value in (
        ("partuuid", "00000000-0000-0000-0000-000000000000"),
        ("mount_mode", "read-write"),
    ):
        changed_layout = json.loads(json.dumps(manifest))
        changed_layout["boot_contract"]["root_layout"][field] = value
        with pytest.raises(image.OfflineVmImageError, match="identities or fixed boot contract"):
            image._validate_candidate_manifest(
                changed_layout,
                **TEST_PIN_VALUES,
                qemu_identity=qemu_identity,
                kernel_image=kernel_image,
                initramfs_image=initramfs_image,
            )
    with pytest.raises(image.OfflineVmImageError, match="kernel ImageIdentity is required"):
        image._candidate_manifest_for_contract(
            raw_size=len(raw),
            raw_sha256=hashlib.sha256(raw).hexdigest(),
            qemu_identity=qemu_identity,
            kernel_image=None,  # type: ignore[arg-type]
            initramfs_image=initramfs_image,
            **TEST_PIN_VALUES,
            build_provenance=manifest["build_provenance"],  # type: ignore[arg-type]
        )


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="candidate publication requires Linux dir fsync/renameat2")
def test_synthetic_candidate_publication_is_atomic_and_reopenable(tmp_path: Path) -> None:
    raw_source = tmp_path / "raw.source"
    raw_source.write_bytes(b"synthetic candidate disk payload")
    qemu = _qemu_identity(tmp_path / "qemu")
    destination = tmp_path / "candidate"

    deployment = _publish(raw_source, destination, qemu)
    assert deployment.worker_image_format == "raw"
    assert deployment.qemu == qemu
    offline_vm_worker.verify_file_identity(deployment.worker_image, "worker image")
    reopened = image._open_candidate_artifact(
        destination,
        qemu,
        **TEST_PIN_VALUES,
        kernel_image=deployment.kernel_image,
        initramfs_image=deployment.initramfs_image,
    )
    assert reopened == deployment
    assert {item.name for item in destination.iterdir()} == {"worker.raw", "manifest.json"}
    assert deployment.worker_image.sha256.encode("ascii") not in (destination / "worker.raw").read_bytes()
    first_inspection = image.inspect_candidate_publication(destination)
    assert first_inspection["status"] == image.PUBLICATION_COMMITTED_REOPENED
    assert first_inspection == image.inspect_candidate_publication(destination)
    assert first_inspection["lock_present"] is False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="candidate publication requires Linux dir fsync/renameat2")
def test_candidate_publication_rejects_replay_without_changing_first_candidate(tmp_path: Path) -> None:
    raw_source = tmp_path / "raw.source"
    raw_source.write_bytes(b"first synthetic candidate")
    qemu = _qemu_identity(tmp_path / "qemu")
    destination = tmp_path / "candidate"
    first = _publish(raw_source, destination, qemu)
    before = (destination / "manifest.json").read_bytes()

    with pytest.raises(image.OfflineVmImageError, match="already exists|replay"):
        _publish(raw_source, destination, qemu)

    assert (destination / "manifest.json").read_bytes() == before
    offline_vm_worker.verify_file_identity(first.worker_image, "worker image")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="candidate publication requires Linux dir fsync/renameat2")
def test_candidate_reopen_rejects_kernel_or_initramfs_asset_drift(tmp_path: Path) -> None:
    raw_source = tmp_path / "raw.source"
    raw_source.write_bytes(b"candidate with external boot assets")
    qemu = _qemu_identity(tmp_path / "qemu")
    destination = tmp_path / "candidate"
    deployment = _publish(raw_source, destination, qemu)

    for identity, label in (
        (deployment.kernel_image, "worker kernel"),
        (deployment.initramfs_image, "worker initramfs"),
    ):
        original = identity.path.read_bytes()
        identity.path.write_bytes(b"x" * identity.size)
        with pytest.raises(offline_vm_worker.OfflineVmContractError, match="SHA-256 changed"):
            image._open_candidate_artifact(
                destination,
                qemu,
                **TEST_PIN_VALUES,
                kernel_image=deployment.kernel_image,
                initramfs_image=deployment.initramfs_image,
            )
        identity.path.write_bytes(original)
        offline_vm_worker.verify_file_identity(identity, label)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="candidate publication requires Linux dir fsync/renameat2")
def test_fsync_failure_leaves_no_partial_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw_source = tmp_path / "raw.source"
    raw_source.write_bytes(b"synthetic candidate for fsync failure")
    qemu = _qemu_identity(tmp_path / "qemu")
    destination = tmp_path / "candidate"

    def fail_fsync(_fd: int) -> None:
        raise OSError("synthetic fsync failure")

    monkeypatch.setattr(image.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="synthetic fsync failure"):
        _publish(raw_source, destination, qemu)
    assert not destination.exists()
    state = image.inspect_candidate_publication(destination)
    assert state["status"] == "RECOVERY_REQUIRED_STATE_UPDATE_INCOMPLETE"
    assert state["lock_present"] is True
    assert not list(tmp_path.glob(".candidate.stage-*"))
    assert state == image.inspect_candidate_publication(destination)
    with pytest.raises(image.OfflineVmImageError, match="state already exists|replay"):
        _publish(raw_source, destination, qemu)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="candidate publication requires Linux dir fsync/renameat2")
def test_partial_copy_failure_leaves_no_partial_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw_source = tmp_path / "raw.source"
    raw_source.write_bytes(b"synthetic candidate whose write is interrupted")
    qemu = _qemu_identity(tmp_path / "qemu")
    destination = tmp_path / "candidate"
    real_write = image.os.write
    raw_calls = 0

    def partial_then_fail(fd: int, value: Any) -> int:
        nonlocal raw_calls
        if not os.readlink(f"/proc/self/fd/{fd}").endswith("/worker.raw"):
            return real_write(fd, value)
        raw_calls += 1
        if raw_calls == 1:
            return real_write(fd, value[:7])
        raise OSError("synthetic partial write failure")

    monkeypatch.setattr(image.os, "write", partial_then_fail)
    with pytest.raises(OSError, match="synthetic partial write failure"):
        _publish(raw_source, destination, qemu)
    assert not destination.exists()
    assert not list(tmp_path.glob(".candidate.stage-*"))
    state = image.inspect_candidate_publication(destination)
    assert state["status"] == image.PUBLICATION_PRE_RENAME
    assert state["stage_present"] is False
    assert state["lock_present"] is False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="atomic no-replace is Linux-specific")
def test_destination_race_never_replaces_concurrently_created_empty_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_source = tmp_path / "raw.source"
    raw_source.write_bytes(b"candidate for destination race")
    qemu = _qemu_identity(tmp_path / "qemu")
    destination = tmp_path / "candidate"
    real_rename = image._rename_noreplace

    def race(source: Path, target: Path) -> None:
        if target == destination:
            target.mkdir(mode=0o700)
        real_rename(source, target)

    monkeypatch.setattr(image, "_rename_noreplace", race)
    with pytest.raises(OSError):
        _publish(raw_source, destination, qemu)

    assert destination.is_dir()
    assert list(destination.iterdir()) == []
    assert not list(tmp_path.glob(".candidate.stage-*"))
    state = image.inspect_candidate_publication(destination)
    assert state["status"] == "RECOVERY_REQUIRED_DESTINATION_IDENTITY_MISMATCH"
    assert state["lock_present"] is False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="candidate publication requires Linux dir fsync/renameat2")
def test_post_rename_interruption_preserves_our_directory_for_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_source = tmp_path / "raw.source"
    raw_source.write_bytes(b"candidate for post-rename interruption")
    qemu = _qemu_identity(tmp_path / "qemu")
    destination = tmp_path / "candidate"
    real_fsync_directory = image._fsync_directory

    def interrupt_after_rename(path: Path) -> None:
        if path == tmp_path and destination.exists():
            raise KeyboardInterrupt("synthetic interrupt after atomic rename")
        real_fsync_directory(path)

    monkeypatch.setattr(image, "_fsync_directory", interrupt_after_rename)
    with pytest.raises(KeyboardInterrupt, match="after atomic rename"):
        _publish(raw_source, destination, qemu)

    assert destination.is_dir()
    assert not list(tmp_path.glob(".candidate.stage-*"))
    first_state = image.inspect_candidate_publication(destination)
    assert first_state["status"] == image.PUBLICATION_RENAMED_UNCOMMITTED
    assert first_state["lock_present"] is True
    assert first_state == image.inspect_candidate_publication(destination)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="candidate publication requires Linux dir fsync/renameat2")
def test_rename_completed_before_error_is_inferred_as_uncommitted_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_source = tmp_path / "raw.source"
    raw_source.write_bytes(b"candidate whose rename observer is interrupted")
    qemu = _qemu_identity(tmp_path / "qemu")
    destination = tmp_path / "candidate"
    real_rename = image._rename_noreplace

    def rename_then_error(source: Path, target: Path) -> None:
        real_rename(source, target)
        if target == destination:
            raise OSError("synthetic observer interruption after rename")

    monkeypatch.setattr(image, "_rename_noreplace", rename_then_error)
    with pytest.raises(OSError, match="observer interruption"):
        _publish(raw_source, destination, qemu)

    first_state = image.inspect_candidate_publication(destination)
    assert first_state["status"] == image.PUBLICATION_RENAMED_UNCOMMITTED
    assert first_state["lock_present"] is True
    assert first_state == image.inspect_candidate_publication(destination)
    assert destination.is_dir()
    with pytest.raises(image.OfflineVmImageError, match="already exists|replay"):
        _publish(raw_source, destination, qemu)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="candidate publication requires Linux dir fsync/renameat2")
def test_reopen_failure_preserves_destination_and_does_not_attempt_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_source = tmp_path / "raw.source"
    raw_source.write_bytes(b"candidate whose reopen fails")
    qemu = _qemu_identity(tmp_path / "qemu")
    destination = tmp_path / "candidate"
    real_rmtree = image.shutil.rmtree
    cleanup_calls: list[Path] = []

    def fail_reopen(*_args: object, **_kwargs: object) -> offline_vm_worker.WorkerDeployment:
        raise OSError("synthetic candidate reopen failure")

    def record_cleanup(path: str | os.PathLike[str], *args: object, **kwargs: object) -> None:
        candidate_path = Path(path)
        cleanup_calls.append(candidate_path)
        if candidate_path == destination:
            raise OSError("published destination cleanup must not run")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(image, "_open_candidate_artifact", fail_reopen)
    monkeypatch.setattr(image.shutil, "rmtree", record_cleanup)
    with pytest.raises(OSError, match="candidate reopen failure"):
        _publish(raw_source, destination, qemu)

    state = image.inspect_candidate_publication(destination)
    assert state["status"] == image.PUBLICATION_RENAMED_UNCOMMITTED
    assert state["lock_present"] is True
    assert destination.is_dir()
    assert destination not in cleanup_calls
    assert state == image.inspect_candidate_publication(destination)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="candidate publication requires Linux dir fsync/renameat2")
def test_stage_cleanup_failure_is_reported_and_left_for_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_source = tmp_path / "raw.source"
    raw_source.write_bytes(b"candidate whose pre-rename stage cleanup fails")
    qemu = _qemu_identity(tmp_path / "qemu")
    destination = tmp_path / "candidate"
    real_fsync_directory = image._fsync_directory
    real_rmtree = image.shutil.rmtree

    def fail_stage_fsync(path: Path) -> None:
        if path.name.startswith(".candidate.stage-"):
            raise OSError("synthetic pre-rename stage fsync failure")
        real_fsync_directory(path)

    def fail_stage_cleanup(path: str | os.PathLike[str], *_args: object, **_kwargs: object) -> None:
        if Path(path).name.startswith(".candidate.stage-"):
            raise OSError("synthetic stage cleanup failure")
        real_rmtree(path)

    monkeypatch.setattr(image, "_fsync_directory", fail_stage_fsync)
    monkeypatch.setattr(image.shutil, "rmtree", fail_stage_cleanup)
    with pytest.raises(image.OfflineVmImageError, match="stage cleanup failure.*inspect"):
        _publish(raw_source, destination, qemu)

    state = image.inspect_candidate_publication(destination)
    assert state["status"] == image.PUBLICATION_PRE_RENAME
    assert state["stage_present"] is True
    assert state["lock_present"] is True
    assert not destination.exists()
    assert state == image.inspect_candidate_publication(destination)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="candidate publication requires Linux dir fsync/renameat2")
def test_lock_unlink_failure_is_reported_after_reopen_without_false_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_source = tmp_path / "raw.source"
    raw_source.write_bytes(b"candidate whose lock unlink fails")
    qemu = _qemu_identity(tmp_path / "qemu")
    destination = tmp_path / "candidate"
    lock_path = tmp_path / ".candidate.publish.lock"
    real_unlink = Path.unlink

    def fail_lock_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path == lock_path:
            raise OSError("synthetic publication lock unlink failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_lock_unlink)
    with pytest.raises(OSError, match="publication lock unlink failure"):
        _publish(raw_source, destination, qemu)

    state = image.inspect_candidate_publication(destination)
    assert state["status"] == image.PUBLICATION_COMMITTED_REOPENED
    assert state["lock_present"] is True
    assert destination.is_dir()
    assert state == image.inspect_candidate_publication(destination)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="candidate publication requires Linux dir fsync/renameat2")
def test_lock_unlink_parent_fsync_failure_is_reported_without_false_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_source = tmp_path / "raw.source"
    raw_source.write_bytes(b"candidate whose lock unlink fsync fails")
    qemu = _qemu_identity(tmp_path / "qemu")
    destination = tmp_path / "candidate"
    lock_path = tmp_path / ".candidate.publish.lock"
    real_fsync_directory = image._fsync_directory

    def fail_after_lock_unlink(path: Path) -> None:
        if path == tmp_path and destination.exists() and not lock_path.exists():
            raise OSError("synthetic lock unlink parent fsync failure")
        real_fsync_directory(path)

    monkeypatch.setattr(image, "_fsync_directory", fail_after_lock_unlink)
    with pytest.raises(OSError, match="lock unlink parent fsync failure"):
        _publish(raw_source, destination, qemu)

    state = image.inspect_candidate_publication(destination)
    assert state["status"] == image.PUBLICATION_COMMITTED_REOPENED
    assert state["lock_present"] is False
    assert destination.is_dir()
    assert state == image.inspect_candidate_publication(destination)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="candidate publication requires Linux dir fsync/renameat2")
@pytest.mark.parametrize("bad_phase", [[], {}])
def test_publication_inspector_rejects_nonstring_phase_without_type_error(
    tmp_path: Path,
    bad_phase: object,
) -> None:
    raw_source = tmp_path / "raw.source"
    raw_source.write_bytes(b"candidate with malformed journal phase")
    qemu = _qemu_identity(tmp_path / "qemu")
    destination = tmp_path / "candidate"
    _publish(raw_source, destination, qemu)
    _, state_path, _ = image._publication_paths(destination)
    state = json.loads(state_path.read_text(encoding="ascii"))
    state["phase"] = bad_phase
    state_path.write_bytes(image._canonical_json(state) + b"\n")

    with pytest.raises(image.OfflineVmImageError, match="unknown phase"):
        image.inspect_candidate_publication(destination)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="candidate publication requires Linux dir fsync/renameat2")
@pytest.mark.parametrize(
    ("journal_bytes", "error_match"),
    [
        (b"{truncated", "malformed"),
        (b'{"phase":"PRE_RENAME","phase":"PRE_RENAME"}', "duplicate"),
    ],
)
def test_publication_inspector_rejects_malformed_or_duplicate_journal(
    tmp_path: Path,
    journal_bytes: bytes,
    error_match: str,
) -> None:
    raw_source = tmp_path / "raw.source"
    raw_source.write_bytes(b"candidate with malformed state journal")
    qemu = _qemu_identity(tmp_path / "qemu")
    destination = tmp_path / "candidate"
    _publish(raw_source, destination, qemu)
    _, state_path, _ = image._publication_paths(destination)
    state_path.write_bytes(journal_bytes)

    with pytest.raises(image.OfflineVmImageError, match=error_match):
        image.inspect_candidate_publication(destination)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="candidate publication requires Linux dir fsync/renameat2")
def test_publication_inspector_rejects_noncanonical_journal(tmp_path: Path) -> None:
    raw_source = tmp_path / "raw.source"
    raw_source.write_bytes(b"candidate with noncanonical state journal")
    qemu = _qemu_identity(tmp_path / "qemu")
    destination = tmp_path / "candidate"
    _publish(raw_source, destination, qemu)
    _, state_path, _ = image._publication_paths(destination)
    state = json.loads(state_path.read_text(encoding="ascii"))
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="ascii")

    with pytest.raises(image.OfflineVmImageError, match="unknown or noncanonical schema"):
        image.inspect_candidate_publication(destination)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="candidate publication requires Linux dir fsync/renameat2")
@pytest.mark.parametrize(
    ("field", "value", "error_match"),
    [
        ("candidate", "other-candidate", "not bound to this destination"),
        ("phase", "UNKNOWN_PHASE", "unknown phase"),
        ("stage", "../outside-stage", "malformed stage identity"),
    ],
)
def test_publication_inspector_rejects_wrong_candidate_phase_or_stage(
    tmp_path: Path,
    field: str,
    value: str,
    error_match: str,
) -> None:
    raw_source = tmp_path / "raw.source"
    raw_source.write_bytes(b"candidate with wrong state identity")
    qemu = _qemu_identity(tmp_path / "qemu")
    destination = tmp_path / "candidate"
    _publish(raw_source, destination, qemu)
    _, state_path, _ = image._publication_paths(destination)
    state = json.loads(state_path.read_text(encoding="ascii"))
    state[field] = value
    state_path.write_bytes(image._canonical_json(state) + b"\n")

    with pytest.raises(image.OfflineVmImageError, match=error_match):
        image.inspect_candidate_publication(destination)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="candidate state symlink policy is Linux-specific")
def test_publication_inspector_rejects_symlinked_journal(tmp_path: Path) -> None:
    raw_source = tmp_path / "raw.source"
    raw_source.write_bytes(b"candidate with linked state journal")
    qemu = _qemu_identity(tmp_path / "qemu")
    destination = tmp_path / "candidate"
    _publish(raw_source, destination, qemu)
    _, state_path, _ = image._publication_paths(destination)
    saved_state = tmp_path / "saved-state.json"
    state_path.rename(saved_state)
    state_path.symlink_to(saved_state)

    with pytest.raises(image.OfflineVmImageError, match="symlink|single-link"):
        image.inspect_candidate_publication(destination)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="candidate state hardlink policy is Linux-specific")
def test_publication_inspector_rejects_hardlinked_journal(tmp_path: Path) -> None:
    raw_source = tmp_path / "raw.source"
    raw_source.write_bytes(b"candidate with hardlinked state journal")
    qemu = _qemu_identity(tmp_path / "qemu")
    destination = tmp_path / "candidate"
    _publish(raw_source, destination, qemu)
    _, state_path, _ = image._publication_paths(destination)
    os.link(state_path, tmp_path / "state-hardlink")

    with pytest.raises(image.OfflineVmImageError, match="single-link regular file"):
        image.inspect_candidate_publication(destination)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="restart inspection test requires Linux candidate publication")
def test_publication_state_can_be_inspected_in_a_fresh_process_after_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_source = tmp_path / "raw.source"
    raw_source.write_bytes(b"candidate inspected after simulated process loss")
    qemu = _qemu_identity(tmp_path / "qemu")
    destination = tmp_path / "candidate"

    def fail_reopen(*_args: object, **_kwargs: object) -> offline_vm_worker.WorkerDeployment:
        raise OSError("synthetic process interruption after rename")

    monkeypatch.setattr(image, "_open_candidate_artifact", fail_reopen)
    with pytest.raises(OSError, match="process interruption"):
        _publish(raw_source, destination, qemu)

    script = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "from usb_builder.offline_vm_image import inspect_candidate_publication\n"
        "print(json.dumps(inspect_candidate_publication(Path(sys.argv[1])), sort_keys=True))\n"
    )
    inspected: list[dict[str, object]] = []
    child_env = dict(os.environ)
    child_env["PYTHONDONTWRITEBYTECODE"] = "1"
    for _ in range(2):
        completed = subprocess.run(
            [sys.executable, "-B", "-c", script, str(destination)],
            cwd=REPO_ROOT,
            env=child_env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        inspected.append(json.loads(completed.stdout))

    assert inspected[0]["status"] == image.PUBLICATION_RENAMED_UNCOMMITTED
    assert inspected[0]["lock_present"] is True
    assert inspected[0] == inspected[1]


@pytest.mark.skipif(os.name != "posix", reason="POSIX candidate permission modes are not available")
def test_reopen_rejects_candidate_permission_drift(tmp_path: Path) -> None:
    raw_source = tmp_path / "raw.source"
    raw_source.write_bytes(b"candidate for mode check")
    qemu = _qemu_identity(tmp_path / "qemu")
    destination = tmp_path / "candidate"
    deployment = _publish(raw_source, destination, qemu)
    (destination / "manifest.json").chmod(0o644)

    with pytest.raises(image.OfflineVmImageError, match="owner/mode"):
        image._open_candidate_artifact(
            destination,
            qemu,
            **TEST_PIN_VALUES,
            kernel_image=deployment.kernel_image,
            initramfs_image=deployment.initramfs_image,
        )


def test_candidate_source_rejects_hardlinks_and_symlinks(tmp_path: Path) -> None:
    source = tmp_path / "raw.source"
    source.write_bytes(b"synthetic image")
    linked = tmp_path / "hardlink"
    try:
        os.link(source, linked)
    except OSError:
        pytest.skip("hard links are unavailable on this filesystem")
    with pytest.raises(image.OfflineVmImageError, match="single-link"):
        image._candidate_file_info(source, label="raw-image source")

    linked.unlink()
    symlink = tmp_path / "symlink"
    try:
        symlink.symlink_to(source)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this filesystem")
    with pytest.raises(image.OfflineVmImageError, match="symlink|linked"):
        image._candidate_file_info(symlink, label="raw-image source")
