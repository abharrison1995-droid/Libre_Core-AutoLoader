from __future__ import annotations

import hashlib
import json
import os
import signal
import stat
import struct
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Callable
from types import SimpleNamespace

import pytest

from usb_builder import offline_vm_build as build
from usb_builder import offline_vm_build_service as build_service
from usb_builder import offline_vm_evidence


_TEST_EVIDENCE_SIZE = 4096
_TEST_EVIDENCE_SHA256 = "f" * 64
from usb_builder import offline_vm_guest_worker, offline_vm_worker


@pytest.fixture(autouse=True)
def _approved_test_operation_parent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    parent = tmp_path / "approved-worker-parent"
    parent.mkdir(mode=0o700)
    if os.name == "posix":
        parent.chmod(0o700)
        observed_mode = stat.S_IMODE(parent.stat().st_mode)
        filesystem = offline_vm_worker._mount_filesystem_type(parent)
        monkeypatch.setattr(offline_vm_worker, "APPROVED_PARENT_OWNER_UID", os.geteuid())
        monkeypatch.setattr(offline_vm_worker, "APPROVED_PARENT_MODE", observed_mode)
        monkeypatch.setattr(offline_vm_worker, "APPROVED_OPERATION_FILESYSTEMS", frozenset({filesystem}))
    monkeypatch.setattr(offline_vm_worker, "APPROVED_OPERATION_PARENT", parent)


def _write(path: Path, value: bytes) -> offline_vm_worker.ImageIdentity:
    path.write_bytes(value)
    if path.name == "qemu" and os.name == "posix":
        path.chmod(0o700)
    return offline_vm_worker.ImageIdentity(path, len(value), hashlib.sha256(value).hexdigest())


def _sparse_volume(path: Path, size: int) -> offline_vm_worker.VolumeIdentity:
    with path.open("xb") as stream:
        stream.truncate(size)
    if os.name == "posix":
        path.chmod(offline_vm_worker.OPERATION_FILE_MODE)
    return offline_vm_worker.VolumeIdentity(path, size)


def _fixture_plan(tmp_path: Path) -> tuple[offline_vm_worker.WorkerPlan, offline_vm_worker.WorkerDeployment, dict[str, object]]:
    op = offline_vm_worker.APPROVED_OPERATION_PARENT / ("a" * 32)
    op.mkdir(mode=0o700)
    if os.name == "posix":
        op.chmod(0o700)
    source = _write(tmp_path / "source.img", b"source-image")
    packages = _write(tmp_path / "packages.img", b"package-image")
    worker = _write(tmp_path / "worker.img", b"worker-image")
    qemu = _write(tmp_path / "qemu", b"qemu-binary")
    scratch = _sparse_volume(op / "scratch.raw", offline_vm_worker.MIN_SCRATCH_BYTES)
    output_size = offline_vm_worker.RESULT_HEADER_SIZE + 256 * 1024
    output = _sparse_volume(op / "output.raw", output_size)
    evidence = _sparse_volume(op / "evidence.raw", 2 * 1024 * 1024)
    request_image = offline_vm_worker.ImageIdentity(
        op / "request.raw", offline_vm_worker.REQUEST_VOLUME_SIZE, "0" * 64
    )
    profile = (offline_vm_worker.ProfileFile("usb_builder/archiso_profile/profiledef.sh", 9, hashlib.sha256(b"profile!!").hexdigest()),)
    profile_path = tmp_path / "source-root" / "usb_builder" / "archiso_profile" / "profiledef.sh"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_bytes(b"profile!!")
    plan = offline_vm_worker.WorkerPlan(
        operation_id="a" * 32,
        snapshot_lock_sha256=offline_vm_worker._pinned_snapshot_lock_sha256(),
        snapshot_date="2026-09-30",
        package_versions=dict(offline_vm_worker.LOCKED_PACKAGES),
        worker_image_sha256=worker.sha256,
        source_image=source,
        package_image=packages,
        request_image=request_image,
        scratch_volume=scratch,
        output_volume=output,
        evidence_volume=evidence,
        profile_files=profile,
        log_path=op / "qemu.log",
        timeout_seconds=600,
    )
    plan, request = offline_vm_worker.materialize_request_volume(plan)
    deployment = offline_vm_worker.WorkerDeployment(
        qemu=qemu,
        worker_image=worker,
    )
    return plan, deployment, request


def _write_candidate_metadata(stream: object, request: dict[str, object], iso_size: int, iso_sha256: str) -> None:
    command_digest = "c" * 64
    header = offline_vm_worker.build_result_header(
        request,
        status="CANDIDATE_ISO",
        iso_size=iso_size,
        iso_sha256=iso_sha256,
        command_argv_sha256=command_digest,
        evidence_size=_TEST_EVIDENCE_SIZE,
        evidence_sha256=_TEST_EVIDENCE_SHA256,
    )
    prefix = header[: offline_vm_worker.RESULT_RECEIPT_OFFSET]
    receipt = offline_vm_worker.build_service_success_receipt(
        request,
        iso_size=iso_size,
        iso_sha256=iso_sha256,
        command_argv_sha256=command_digest,
        evidence_size=_TEST_EVIDENCE_SIZE,
        evidence_sha256=_TEST_EVIDENCE_SHA256,
    )
    commit = offline_vm_worker.build_result_commit_marker(
        request, header_prefix=prefix, service_receipt=receipt,
        evidence_size=_TEST_EVIDENCE_SIZE, evidence_sha256=_TEST_EVIDENCE_SHA256,
    )
    terminal = offline_vm_worker.build_result_terminal_marker(
        request,
        header_prefix=prefix,
        service_receipt=receipt,
        commit_marker=commit,
        evidence_size=_TEST_EVIDENCE_SIZE,
        evidence_sha256=_TEST_EVIDENCE_SHA256,
    )
    stream.seek(offline_vm_worker.RESULT_RECEIPT_OFFSET)  # type: ignore[attr-defined]
    stream.write(receipt)  # type: ignore[attr-defined]
    stream.seek(0)  # type: ignore[attr-defined]
    stream.write(prefix)  # type: ignore[attr-defined]
    stream.seek(offline_vm_worker.RESULT_COMMIT_OFFSET)  # type: ignore[attr-defined]
    stream.write(commit)  # type: ignore[attr-defined]
    stream.seek(offline_vm_worker.RESULT_TERMINAL_OFFSET)  # type: ignore[attr-defined]
    stream.write(terminal)  # type: ignore[attr-defined]


def test_request_and_fixed_qemu_argv_bind_exact_pacman_minus_two_inputs(tmp_path: Path) -> None:
    plan, deployment, request = _fixture_plan(tmp_path)
    argv = offline_vm_worker.build_qemu_argv(plan, deployment)
    parsed = offline_vm_worker.decode_request_volume(plan.request_image.path.read_bytes())

    assert parsed == request
    assert parsed["snapshot"]["packages"]["pacman"] == "7.1.0.r9.g54d9411-2"
    assert "-nic" in argv and argv[argv.index("-nic") + 1] == "none"
    assert "-nodefaults" in argv and "-monitor" in argv and "none" in argv
    assert not {"-netdev", "-usb", "-virtfs", "-fsdev", "-hostfwd"}.intersection(argv)
    blockdev_args = [argv[index + 1] for index, item in enumerate(argv[:-1]) if item == "-blockdev"]
    file_nodes = [json.loads(item) for item in blockdev_args if '"driver":"file"' in item]
    assert len(file_nodes) == 7  # seven fixed disks, each with one file node
    by_filename = {item["filename"]: item["read-only"] for item in file_nodes}
    assert by_filename[str(deployment.worker_image.path)] is True
    assert by_filename[str(plan.request_image.path)] is True
    assert by_filename[str(plan.source_image.path)] is True
    assert by_filename[str(plan.package_image.path)] is True
    assert by_filename[str(plan.scratch_volume.path)] is False
    assert by_filename[str(plan.output_volume.path)] is False
    assert by_filename[str(plan.evidence_volume.path)] is False
    assert "virtio-blk-pci,drive=evidence-drive,serial=LC-EVIDENCE-RW" in argv


def test_request_rejects_unknown_command_fields_paths_and_pacman_minus_three(tmp_path: Path) -> None:
    plan, _, request = _fixture_plan(tmp_path)
    with pytest.raises(offline_vm_worker.OfflineVmContractError, match="exactly match snapshot.lock"):
        offline_vm_worker.validate_package_versions({**offline_vm_worker.LOCKED_PACKAGES, "pacman": "7.1.0.r9.g54d9411-3"})

    command_injection = dict(request)
    command_injection["command"] = ["/bin/sh", "-c", "touch /tmp/owned"]
    with pytest.raises(offline_vm_worker.OfflineVmContractError, match="unknown/missing fields"):
        offline_vm_worker.parse_request(offline_vm_worker.canonical_json(command_injection))

    bad_path = replace(plan, profile_files=(offline_vm_worker.ProfileFile("../outside", 1, "0" * 64),))
    with pytest.raises(offline_vm_worker.OfflineVmContractError, match="traverses"):
        offline_vm_worker.make_request(bad_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [("evidence_device", "LC-OUTPUT-RW"), ("evidence_capacity", 1)],
    ids=["device-name-drift", "capacity-underflow"],
)
def test_request_binds_exact_evidence_volume_contract(tmp_path: Path, field: str, value: object) -> None:
    _plan, _deployment, request = _fixture_plan(tmp_path)
    changed = json.loads(json.dumps(request))
    changed["layout"][field] = value
    with pytest.raises(offline_vm_worker.OfflineVmContractError):
        offline_vm_worker.parse_request(offline_vm_worker.canonical_json(changed))


def test_request_volume_is_exclusive_and_integrity_checked(tmp_path: Path) -> None:
    plan, _, request = _fixture_plan(tmp_path)
    encoded = offline_vm_worker.encode_request_volume(request)
    assert offline_vm_worker.decode_request_volume(encoded) == request
    with pytest.raises(offline_vm_worker.OfflineVmContractError, match="padding"):
        offline_vm_worker.decode_request_volume(encoded[:-1] + b"X")
    existing = plan.scratch_volume.path.parent / "existing-request.img"
    existing.write_bytes(b"do not overwrite")
    with pytest.raises(offline_vm_worker.OfflineVmContractError, match="already exists"):
        offline_vm_worker.write_request_volume(existing, request)
    assert existing.read_bytes() == b"do not overwrite"


def test_bad_request_is_serialized_before_file_or_descriptor_creation(tmp_path: Path) -> None:
    plan, _, request = _fixture_plan(tmp_path)
    malformed = {**request, "extra": "must reject"}
    destination = plan.scratch_volume.path.parent / "bad-request.img"
    before_fds = len(list(Path("/proc/self/fd").iterdir())) if os.name == "posix" else None
    with pytest.raises(offline_vm_worker.OfflineVmContractError, match="unknown/missing fields"):
        offline_vm_worker.write_request_volume(destination, malformed)
    after_fds = len(list(Path("/proc/self/fd").iterdir())) if os.name == "posix" else None
    assert not destination.exists()
    if before_fds is not None:
        assert after_fds == before_fds


@pytest.mark.skipif(os.name != "posix", reason="descriptor/fchmod policy is POSIX-specific")
def test_request_volume_closes_descriptor_and_removes_file_when_fchmod_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, _, request = _fixture_plan(tmp_path)
    destination = plan.scratch_volume.path.parent / "fchmod-failure-request.img"
    before_fds = len(list(Path("/proc/self/fd").iterdir()))

    def fail_fchmod(fd: int, mode: int) -> None:
        raise OSError("injected fchmod failure")

    monkeypatch.setattr(offline_vm_worker.os, "fchmod", fail_fchmod)
    with pytest.raises(OSError, match="injected fchmod"):
        offline_vm_worker.write_request_volume(destination, request)
    after_fds = len(list(Path("/proc/self/fd").iterdir()))
    assert after_fds == before_fds
    assert not destination.exists()


def test_writable_volume_rejects_hardlink_wrong_mode_and_alias(tmp_path: Path) -> None:
    plan, deployment, _ = _fixture_plan(tmp_path)
    output_alias = tmp_path / "external-output-link.raw"
    try:
        os.link(plan.output_volume.path, output_alias)
    except (OSError, NotImplementedError):
        pytest.skip("host does not permit creating a hard-link fixture")
    with pytest.raises(offline_vm_worker.OfflineVmContractError, match="single-link"):
        offline_vm_worker.verify_writable_volume(plan.output_volume, "output", plan.scratch_volume.path.parent)
    output_alias.unlink()

    if os.name == "posix":
        plan.output_volume.path.chmod(0o644)
        with pytest.raises(offline_vm_worker.OfflineVmContractError, match="owner/mode/filesystem"):
            offline_vm_worker.verify_writable_volume(plan.output_volume, "output", plan.scratch_volume.path.parent)
        plan.output_volume.path.chmod(offline_vm_worker.OPERATION_FILE_MODE)

    aliased = replace(plan, output_volume=offline_vm_worker.VolumeIdentity(plan.scratch_volume.path, plan.scratch_volume.size))
    with pytest.raises(offline_vm_worker.OfflineVmContractError, match="separate files"):
        offline_vm_worker.build_qemu_argv(aliased, deployment)


@pytest.mark.skipif(os.name != "posix", reason="approved worker storage policy is Linux-specific")
def test_worker_operation_parent_and_filesystem_are_fixed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan, deployment, _ = _fixture_plan(tmp_path)
    monkeypatch.setattr(offline_vm_worker, "APPROVED_OPERATION_PARENT", tmp_path / "other-parent")
    with pytest.raises(offline_vm_worker.OfflineVmContractError, match="outside the fixed approved parent"):
        offline_vm_worker.build_qemu_argv(plan, deployment)


@pytest.mark.skipif(os.name != "posix", reason="mountinfo filesystem policy is Linux-specific")
def test_worker_operation_parent_rejects_unapproved_filesystem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan, deployment, _ = _fixture_plan(tmp_path)
    monkeypatch.setattr(offline_vm_worker, "APPROVED_OPERATION_FILESYSTEMS", frozenset({"not-the-current-fs"}))
    with pytest.raises(offline_vm_worker.OfflineVmContractError, match="not on an approved filesystem"):
        offline_vm_worker.build_qemu_argv(plan, deployment)


def test_qemu_argv_rejects_changed_or_linked_input_before_any_process(tmp_path: Path) -> None:
    plan, deployment, _ = _fixture_plan(tmp_path)
    plan.source_image.path.write_bytes(b"tampered-img")
    with pytest.raises(offline_vm_worker.OfflineVmContractError, match="SHA-256 changed"):
        offline_vm_worker.build_qemu_argv(plan, deployment)


def test_worker_execution_is_closed_without_deployment_pin(tmp_path: Path) -> None:
    plan, _, _ = _fixture_plan(tmp_path)
    calls: list[object] = []

    def fake_popen(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        raise AssertionError("must not launch without a deployment pin")

    with pytest.raises(offline_vm_worker.OfflineVmContractError, match="DEPLOYMENT_UNAVAILABLE"):
        offline_vm_worker.run_worker(plan, popen=fake_popen)  # type: ignore[arg-type]
    assert calls == []
    assert not plan.log_path.exists()


def test_worker_rejects_preexisting_output_header_before_qemu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan, deployment, _ = _fixture_plan(tmp_path)
    monkeypatch.setattr(offline_vm_worker, "PRODUCTION_WORKER_DEPLOYMENT", deployment)
    with plan.output_volume.path.open("r+b") as stream:
        stream.write(offline_vm_worker.RESULT_MAGIC)
    calls: list[object] = []

    def fake_popen(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        raise AssertionError("QEMU must not launch over a pre-existing result marker")

    with pytest.raises(offline_vm_worker.OfflineVmContractError, match="not blank before the worker launch"):
        offline_vm_worker.run_worker(plan, deployment, popen=fake_popen)  # type: ignore[arg-type]
    assert calls == []
    assert not plan.log_path.exists()


def test_worker_timeout_terminates_process_group_and_records_input_hashes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name != "posix":
        pytest.skip("process-group semantics are Linux-specific")
    plan, deployment, _ = _fixture_plan(tmp_path)
    monkeypatch.setattr(offline_vm_worker, "PRODUCTION_WORKER_DEPLOYMENT", deployment)
    kill_calls: list[tuple[int, int]] = []
    monkeypatch.setattr(offline_vm_worker.os, "killpg", lambda pid, sig: kill_calls.append((pid, sig)))

    class FakeProcess:
        pid = 999999
        returncode: int | None = None
        waits = 0

        def wait(self, timeout: float) -> int:
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("qemu", timeout)
            self.returncode = -signal.SIGTERM
            return self.returncode

        def poll(self) -> int | None:
            return self.returncode

    process = FakeProcess()
    captured: dict[str, object] = {}

    def fake_popen(argv: Sequence[str], **kwargs: object) -> FakeProcess:
        captured["argv"] = tuple(argv)
        captured.update(kwargs)
        return process

    result = offline_vm_worker.run_worker(plan, deployment, popen=fake_popen)
    assert result.timed_out is True and result.returncode == -signal.SIGTERM
    assert kill_calls == [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)]
    assert captured["cwd"] == os.path.abspath(os.sep)
    assert captured["env"] == offline_vm_worker._fixed_host_environment(plan.scratch_volume.path.parent)
    assert captured["stdin"] == subprocess.DEVNULL
    assert captured["stdout"] == subprocess.DEVNULL
    assert captured["close_fds"] is True and captured["shell"] is False
    assert captured["executable"].startswith("/proc/self/fd/")
    passed_fds = captured["pass_fds"]
    assert isinstance(passed_fds, tuple) and len(passed_fds) == 8  # QEMU plus seven fixed block volumes
    qemu_arg = captured["argv"]
    assert isinstance(qemu_arg, tuple) and qemu_arg[0] == str(deployment.qemu.path)
    filenames = [
        json.loads(qemu_arg[index + 1])["filename"]
        for index, value in enumerate(qemu_arg[:-1])
        if value == "-blockdev" and '"driver":"file"' in qemu_arg[index + 1]
    ]
    assert len(filenames) == 7
    assert all(item.startswith("/proc/self/fd/") and int(item.rsplit("/", 1)[1]) in passed_fds for item in filenames)
    assert result.readonly_hashes_before == result.readonly_hashes_after
    log_info = plan.log_path.lstat()
    assert stat.S_ISREG(log_info.st_mode)
    assert log_info.st_nlink == 1
    assert log_info.st_uid == os.geteuid()
    assert stat.S_IMODE(log_info.st_mode) == offline_vm_worker.OPERATION_FILE_MODE


@pytest.mark.skipif(os.name != "posix", reason="worker process and output-volume contract is Linux-only")
@pytest.mark.parametrize(
    ("include_commit", "include_terminal", "fail_host_fsync"),
    ((True, True, False), (True, False, False), (False, False, False), (True, True, True)),
)
def test_worker_exit_zero_requires_terminal_commit_proof_before_candidate_acceptance(
    include_commit: bool,
    include_terminal: bool,
    fail_host_fsync: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, deployment, request = _fixture_plan(tmp_path)
    monkeypatch.setattr(offline_vm_worker, "PRODUCTION_WORKER_DEPLOYMENT", deployment)
    iso = bytearray(20 * 2048)
    iso[32769:32774] = b"CD001"
    boot = 17 * 2048
    iso[boot + 1 : boot + 6] = b"CD001"
    iso[boot + 7 : boot + 30] = b"EL TORITO SPECIFICATION"
    iso_bytes = bytes(iso)
    iso_hash = hashlib.sha256(iso_bytes).hexdigest()
    command_hash = "e" * 64
    header = offline_vm_worker.build_result_header(
        request,
        status="CANDIDATE_ISO",
        iso_size=len(iso_bytes),
        iso_sha256=iso_hash,
        command_argv_sha256=command_hash,
        evidence_size=_TEST_EVIDENCE_SIZE,
        evidence_sha256=_TEST_EVIDENCE_SHA256,
    )
    receipt = offline_vm_worker.build_service_success_receipt(
        request,
        iso_size=len(iso_bytes),
        iso_sha256=iso_hash,
        command_argv_sha256=command_hash,
        evidence_size=_TEST_EVIDENCE_SIZE,
        evidence_sha256=_TEST_EVIDENCE_SHA256,
    )
    prefix = header[: offline_vm_worker.RESULT_RECEIPT_OFFSET]
    commit = offline_vm_worker.build_result_commit_marker(
        request, header_prefix=prefix, service_receipt=receipt,
        evidence_size=_TEST_EVIDENCE_SIZE, evidence_sha256=_TEST_EVIDENCE_SHA256,
    )
    terminal = offline_vm_worker.build_result_terminal_marker(
        request, header_prefix=prefix, service_receipt=receipt, commit_marker=commit,
        evidence_size=_TEST_EVIDENCE_SIZE, evidence_sha256=_TEST_EVIDENCE_SHA256,
    )

    events: list[str] = []

    class FakeProcess:
        pid = 38119
        returncode = 0

        def wait(self, timeout: float) -> int:
            events.append("qemu-exit")
            return 0

        def poll(self) -> int:
            return 0

    def fake_popen(*_args: object, **_kwargs: object) -> FakeProcess:
        with plan.output_volume.path.open("r+b") as stream:
            stream.seek(offline_vm_worker.RESULT_ISO_OFFSET)
            stream.write(iso_bytes)
            stream.seek(offline_vm_worker.RESULT_RECEIPT_OFFSET)
            stream.write(receipt)
            stream.seek(0)
            stream.write(prefix)
            if include_commit:
                stream.seek(offline_vm_worker.RESULT_COMMIT_OFFSET)
                stream.write(commit)
            if include_terminal:
                stream.seek(offline_vm_worker.RESULT_TERMINAL_OFFSET)
                stream.write(terminal)
        return FakeProcess()

    original_host_sync = offline_vm_worker._fsync_output_after_worker

    def record_host_sync(path: Path, fd: int) -> None:
        events.append("host-output-fsync")
        if fail_host_fsync:
            raise OSError("injected host output fsync failure")
        original_host_sync(path, fd)

    monkeypatch.setattr(offline_vm_worker, "_fsync_output_after_worker", record_host_sync)
    result = offline_vm_worker.run_worker(plan, deployment, popen=fake_popen)
    assert events[-2:] == ["qemu-exit", "host-output-fsync"]
    if fail_host_fsync:
        assert result.returncode == 0
        assert result.outcome == "OUTPUT_HOST_FSYNC_FAILED"
        assert result.candidate_iso_structurally_validated is False
    elif include_commit and include_terminal:
        assert result.returncode == 0
        assert result.outcome == "CANDIDATE_STRUCTURALLY_VALIDATED"
        assert result.candidate_iso_structurally_validated is True
    else:
        assert result.returncode == 0
        assert result.outcome == "OUTPUT_MISSING_OR_INVALID"
        assert result.candidate_iso_structurally_validated is False


@pytest.mark.skipif(os.name != "posix", reason="Linux worker path validation is POSIX-specific")
def test_worker_path_validation_failure_does_not_create_qemu_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan, deployment, _ = _fixture_plan(tmp_path)
    monkeypatch.setattr(offline_vm_worker, "PRODUCTION_WORKER_DEPLOYMENT", deployment)
    outside = tmp_path / "outside-output.raw"
    try:
        os.link(plan.output_volume.path, outside)
    except (OSError, NotImplementedError):
        pytest.skip("host does not permit creating a hard-link fixture")
    calls: list[object] = []

    def fake_popen(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        raise AssertionError("QEMU must not launch after output-link validation fails")

    with pytest.raises(offline_vm_worker.OfflineVmContractError, match="single-link"):
        offline_vm_worker.run_worker(plan, deployment, popen=fake_popen)  # type: ignore[arg-type]
    assert calls == []
    assert not plan.log_path.exists()
    outside.unlink()


def test_worker_rejects_path_replacement_after_opened_inode_is_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name != "posix":
        pytest.skip("opened-inode pass_fds policy is Linux-specific")
    plan, deployment, _ = _fixture_plan(tmp_path)
    monkeypatch.setattr(offline_vm_worker, "PRODUCTION_WORKER_DEPLOYMENT", deployment)

    class FakeProcess:
        pid = 999998
        returncode = 0

        def wait(self, timeout: float) -> int:
            return 0

        def poll(self) -> int:
            return 0

    def fake_popen(argv: Sequence[str], **kwargs: object) -> FakeProcess:
        passed_fds = kwargs["pass_fds"]
        assert isinstance(passed_fds, tuple)
        output_fd_path = next(
            json.loads(argv[index + 1])["filename"]
            for index, item in enumerate(argv[:-1])
            if item == "-blockdev" and '"filename":"/proc/self/fd/' in argv[index + 1]
            and json.loads(argv[index + 1]).get("node-name") == "output-file"
        )
        output_fd = int(output_fd_path.rsplit("/", 1)[1])
        assert output_fd in passed_fds
        original_inode = os.fstat(output_fd).st_ino
        assert original_inode == plan.output_volume.path.stat().st_ino
        displaced = plan.scratch_volume.path.parent / "displaced-output.raw"
        os.replace(plan.output_volume.path, displaced)
        with plan.output_volume.path.open("xb") as stream:
            stream.truncate(plan.output_volume.size)
        if os.name == "posix":
            plan.output_volume.path.chmod(offline_vm_worker.OPERATION_FILE_MODE)
        assert os.fstat(output_fd).st_ino == original_inode
        assert plan.output_volume.path.stat().st_ino != original_inode
        return FakeProcess()

    with pytest.raises(offline_vm_worker.OfflineVmContractError, match="bound to its opened inode"):
        offline_vm_worker.run_worker(plan, deployment, popen=fake_popen)


def _valid_guest_state(
    worker_image_sha256: str,
    request: dict[str, object] | None = None,
) -> offline_vm_guest_worker.GuestIsolation:
    source_size = int(request["source"]["size"]) if request is not None and isinstance(request.get("source"), dict) else 1024  # type: ignore[index]
    package_size = int(request["packages"]["size"]) if request is not None and isinstance(request.get("packages"), dict) else 1024  # type: ignore[index]
    mounts = (
        offline_vm_guest_worker.GuestMount("source", "LC-SOURCE-RO", "/mnt/librecore-source", "ext4", True, 8, 1),
        offline_vm_guest_worker.GuestMount("packages", "LC-PACKAGES-RO", "/mnt/librecore-packages", "ext4", True, 8, 2),
        offline_vm_guest_worker.GuestMount("scratch", "LC-SCRATCH-RW", "/run/librecore-scratch", "ext4", False, 8, 3),
    )
    return offline_vm_guest_worker.GuestIsolation(
        worker_image_sha256=worker_image_sha256,
        interfaces=("lo",),
        ipv4_routes=(),
        ipv6_routes=(),
        mounts=mounts,
        device_numbers=(
            ("request", 8, 0), ("source", 8, 1), ("packages", 8, 2),
            ("scratch", 8, 3), ("output", 8, 4), ("evidence", 8, 5),
        ),
        device_capacities=(
            ("request", offline_vm_worker.REQUEST_VOLUME_SIZE),
            ("source", source_size), ("packages", package_size),
            ("scratch", offline_vm_worker.MIN_SCRATCH_BYTES),
            ("output", offline_vm_worker.RESULT_HEADER_SIZE + 256 * 1024),
            ("evidence", 2 * 1024 * 1024),
        ),
        readonly_device_roles=("request", "source", "packages"),
        all_mounted_devices=((0, 1), (8, 1), (8, 2), (8, 3)),
    )


def test_guest_one_shot_checks_fixed_isolation_and_runs_no_commands(tmp_path: Path) -> None:
    plan, deployment, request = _fixture_plan(tmp_path)
    payload = offline_vm_worker.canonical_json(request)
    source_root = tmp_path / "source-root"
    result = offline_vm_guest_worker.run_one_shot(payload, _valid_guest_state(deployment.worker_image.sha256, request), source_root)
    assert result["status"] == "BUILD_BACKEND_UNAVAILABLE"
    assert result["commands_run"] == []

    for changed in (
        replace(_valid_guest_state(deployment.worker_image.sha256, request), interfaces=("lo", "eth0")),
        replace(_valid_guest_state(deployment.worker_image.sha256, request), ipv4_routes=("default via 10.0.2.2",)),
        replace(_valid_guest_state(deployment.worker_image.sha256, request), readonly_device_roles=("source", "packages")),
        replace(_valid_guest_state(deployment.worker_image.sha256, request), mounts=()),
    ):
        with pytest.raises(offline_vm_worker.OfflineVmContractError):
            offline_vm_guest_worker.run_one_shot(payload, changed, source_root)

    offline_vm_guest_worker.validate_profile_inventory(request, source_root)
    missing_root = tmp_path / "missing-source"
    missing_root.mkdir()
    with pytest.raises(offline_vm_worker.OfflineVmContractError, match="missing"):
        offline_vm_guest_worker.validate_profile_inventory(request, missing_root)
    source_file = source_root / "usb_builder" / "archiso_profile" / "profiledef.sh"
    source_file.write_bytes(b"tampered!")
    with pytest.raises(offline_vm_worker.OfflineVmContractError, match="hash differs"):
        offline_vm_guest_worker.validate_profile_inventory(request, source_root)


def _guest_evidence_fixture(tmp_path: Path):
    plan, deployment, request = _fixture_plan(tmp_path)
    state = _valid_guest_state(deployment.worker_image.sha256, request)
    binding = offline_vm_evidence.EvidenceBinding(
        operation_id=str(request["operation_id"]),
        request_sha256=hashlib.sha256(offline_vm_worker.canonical_json(request)).hexdigest(),
        source_sha256=str(request["source"]["sha256"]),  # type: ignore[index]
        package_sha256=str(request["packages"]["sha256"]),  # type: ignore[index]
        profile_sha256=str(request["profile"]["sha256"]),  # type: ignore[index]
        snapshot_lock_sha256=str(request["snapshot"]["lock_sha256"]),  # type: ignore[index]
        snapshot_date=str(request["snapshot"]["date"]),  # type: ignore[index]
        candidate_iso_sha256="a" * 64,
        candidate_iso_size=20 * 2048,
        command_argv_sha256="b" * 64,
        evidence_capacity=2 * 1024 * 1024,
    )
    source_root = tmp_path / "evidence-source"
    (source_root / "target-root" / "bin").mkdir(parents=True)
    app_file = source_root / "target-root" / "bin" / "autoloader"
    app_file.write_bytes(b"synthetic app")
    if os.name == "posix":
        app_file.chmod(0o755)
    wheel = source_root / "autoloader.whl"
    wheel.write_bytes(b"PK\x03\x04synthetic wheel")
    package = source_root / "package-inventory.json"
    package.write_bytes(b"synthetic packages")
    provenance = source_root / "build-provenance.json"
    provenance.write_bytes(b"synthetic provenance")
    command_status = source_root / "command-status.json"
    command_status.write_bytes(
        offline_vm_evidence.canonical_json(
            {
                "format": offline_vm_evidence.FORMAT,
                "status": "EXITED_ZERO",
                "exit_code": 0,
                "argv_sha256": binding.command_argv_sha256,
                "operation_id": binding.operation_id,
                "request_sha256": binding.request_sha256,
                "candidate_iso_sha256": binding.candidate_iso_sha256,
                "candidate_iso_size": binding.candidate_iso_size,
            }
        )
    )
    sources = (
        offline_vm_evidence.EvidenceSource("build-provenance.json", "provenance", 0o600, source_path=provenance),
        offline_vm_evidence.EvidenceSource("command-status.json", "command-status", 0o600, source_path=command_status),
        offline_vm_evidence.EvidenceSource("package-inventory.json", "package-inventory", 0o600, source_path=package),
        offline_vm_evidence.EvidenceSource("target-root", "target-root", 0o755, kind="directory"),
        offline_vm_evidence.EvidenceSource("target-root/bin", "target-root-directory", 0o755, kind="directory"),
        offline_vm_evidence.EvidenceSource("target-root/bin/autoloader", "target-root-file", 0o755, source_path=app_file),
        offline_vm_evidence.EvidenceSource("wheel.whl", "wheel", 0o600, source_path=wheel),
    )
    volume_path = plan.evidence_volume.path
    return request, state, binding, sources, volume_path


@pytest.mark.skipif(os.name != "posix", reason="fixed evidence block-device adapter uses Linux/POSIX device semantics")
def test_guest_evidence_writer_binds_fixed_unmounted_device_and_readback(tmp_path: Path) -> None:
    request, state, binding, sources, volume_path = _guest_evidence_fixture(tmp_path)
    evidence_number = dict((role, (major, minor)) for role, major, minor in state.device_numbers)["evidence"]
    rdev = os.makedev(*evidence_number)
    block_info = SimpleNamespace(
        st_mode=stat.S_IFBLK | 0o600, st_rdev=rdev, st_dev=77, st_ino=777, st_nlink=1,
    )

    class FakeDeviceOperations:
        writes = 0

        @staticmethod
        def stat(path: Path) -> object:
            assert path == offline_vm_guest_worker.EVIDENCE_DEVICE_PATH
            return block_info

        @staticmethod
        def open(path: Path, flags: int) -> int:
            assert path == offline_vm_guest_worker.EVIDENCE_DEVICE_PATH
            assert flags & os.O_RDWR
            return os.open(volume_path, os.O_RDWR)

        @staticmethod
        def fstat(fd: int) -> object:
            return block_info

        @staticmethod
        def capacity(fd: int) -> int:
            return binding.evidence_capacity

        def write(self, fd: int, capacity: int, candidate_binding: object, candidate_sources: object) -> tuple[int, str]:
            self.writes += 1
            assert candidate_binding == binding
            assert candidate_sources == sources
            return offline_vm_evidence.write_evidence_volume_fd(fd, capacity, binding, sources)

        @staticmethod
        def close(fd: int) -> None:
            os.close(fd)

    operations = FakeDeviceOperations()
    logical_size, volume_hash = offline_vm_guest_worker._write_guest_evidence_volume_with_operations(
        request, state, binding, sources, operations=operations,
    )
    assert operations.writes == 1
    assert logical_size < binding.evidence_capacity
    verified = offline_vm_evidence.verify_evidence_volume(volume_path, binding)
    assert verified.volume_size == logical_size
    assert verified.volume_sha256 == volume_hash
    with volume_path.open("rb") as stream:
        stream.seek(logical_size)
        assert stream.read() == bytes(binding.evidence_capacity - logical_size)


@pytest.mark.skipif(os.name != "posix", reason="fixed evidence block-device adapter uses Linux/POSIX device semantics")
@pytest.mark.parametrize("mutation", ["mounted", "alias", "capacity", "binding"], ids=["mounted", "device-alias", "capacity-drift", "request-binding"])
def test_guest_evidence_writer_rejects_device_or_binding_drift_before_open(
    tmp_path: Path, mutation: str,
) -> None:
    request, state, binding, sources, _volume_path = _guest_evidence_fixture(tmp_path)
    if mutation == "mounted":
        state = replace(state, all_mounted_devices=(*state.all_mounted_devices, (8, 5)))
    elif mutation == "alias":
        state = replace(state, device_numbers=tuple((role, 8, 4 if role == "evidence" else minor) for role, major, minor in state.device_numbers))
    elif mutation == "capacity":
        state = replace(state, device_capacities=tuple((role, value + 512 if role == "evidence" else value) for role, value in state.device_capacities))
    else:
        binding = replace(binding, request_sha256="c" * 64)

    class NoOpenOperations:
        @staticmethod
        def stat(path: Path) -> object:
            raise AssertionError("device stat must not run before request/isolation validation")

        @staticmethod
        def open(path: Path, flags: int) -> int:
            raise AssertionError("device must not open after failed preflight")

        fstat = capacity = write = close = open

    with pytest.raises(offline_vm_worker.OfflineVmContractError):
        offline_vm_guest_worker._write_guest_evidence_volume_with_operations(
            request, state, binding, sources, operations=NoOpenOperations(),
        )


@pytest.mark.skipif(os.name != "posix", reason="fixed evidence block-device adapter uses Linux/POSIX device semantics")
@pytest.mark.parametrize(
    "mutation",
    ["not-block", "wrong-rdev", "open-inode-race", "capacity-after-open", "writer-failure", "path-replaced-after-write"],
)
def test_guest_evidence_writer_rejects_block_races_and_writer_failures(
    tmp_path: Path, mutation: str,
) -> None:
    request, state, binding, sources, volume_path = _guest_evidence_fixture(tmp_path)
    expected_number = dict((role, (major, minor)) for role, major, minor in state.device_numbers)["evidence"]
    expected_rdev = os.makedev(*expected_number)
    bad_rdev = os.makedev(8, 6)
    link_info = SimpleNamespace(
        st_mode=(stat.S_IFREG | 0o600) if mutation == "not-block" else (stat.S_IFBLK | 0o600),
        st_rdev=bad_rdev if mutation == "wrong-rdev" else expected_rdev,
        st_dev=77,
        st_ino=777,
        st_nlink=1,
    )
    opened_info = SimpleNamespace(**vars(link_info))
    if mutation == "open-inode-race":
        opened_info.st_ino = 778

    class FakeDeviceOperations:
        opened = False
        closed = False
        writer_called = False
        stat_calls = 0

        @staticmethod
        def stat(path: Path) -> object:
            assert path == offline_vm_guest_worker.EVIDENCE_DEVICE_PATH
            FakeDeviceOperations.stat_calls += 1
            if mutation == "path-replaced-after-write" and FakeDeviceOperations.stat_calls > 1:
                return SimpleNamespace(
                    st_mode=stat.S_IFBLK | 0o600, st_rdev=expected_rdev,
                    st_dev=77, st_ino=779, st_nlink=1,
                )
            return link_info

        def open(self, path: Path, flags: int) -> int:
            self.opened = True
            return os.open(volume_path, os.O_RDWR)

        def fstat(self, fd: int) -> object:
            return opened_info if self.opened else link_info

        @staticmethod
        def capacity(fd: int) -> int:
            return binding.evidence_capacity + (512 if mutation == "capacity-after-open" else 0)

        def write(self, fd: int, capacity: int, candidate_binding: object, candidate_sources: object) -> tuple[int, str]:
            self.writer_called = True
            if mutation == "writer-failure":
                raise offline_vm_evidence.EvidenceBundleError("injected evidence write failure")
            return offline_vm_evidence.write_evidence_volume_fd(fd, capacity, binding, sources)

        def close(self, fd: int) -> None:
            self.closed = True
            os.close(fd)

    operations = FakeDeviceOperations()
    with pytest.raises(offline_vm_worker.OfflineVmContractError):
        offline_vm_guest_worker._write_guest_evidence_volume_with_operations(
            request, state, binding, sources, operations=operations,
        )
    assert operations.writer_called is (mutation in {"writer-failure", "path-replaced-after-write"})
    assert operations.closed is operations.opened
    if mutation == "writer-failure":
        assert volume_path.read_bytes() == bytes(binding.evidence_capacity)


def test_guest_evidence_inputs_derive_only_fixed_airootfs_wheel_and_iso(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, _deployment, request = _fixture_plan(tmp_path)
    output_root = tmp_path / "fixed-output"
    work_root = tmp_path / "fixed-work"
    target_root = work_root / "x86_64" / "airootfs"
    wheelhouse = target_root / "opt" / "autoloader" / "wheelhouse"
    wheelhouse.mkdir(parents=True)
    iso = output_root / "librecore.iso"
    output_root.mkdir()
    iso.write_bytes(b"synthetic candidate ISO")
    wheel = wheelhouse / "libre_core_autoloader-1-py3-none-any.whl"
    wheel.write_bytes(b"PK\x03\x04synthetic wheel")
    app_dir = target_root / "usr" / "share" / "autoloader"
    app_dir.mkdir(parents=True)
    (app_dir / "marker.txt").write_bytes(b"synthetic target root")

    monkeypatch.setattr(build, "WORKER_OUTPUT", str(output_root))
    monkeypatch.setattr(build, "WORKER_WORK", str(work_root))
    request_sha = hashlib.sha256(offline_vm_worker.canonical_json(request)).hexdigest()
    invocation = build._expected_fixed_invocation(
        request_sha, Path(build.WORKER_PACSTRAP_BIN) / "pacstrap", str(request["operation_id"]),
    )
    result = build.GuestBuildResult(
        status="CANDIDATE_ISO_PENDING_HOST_VALIDATION",
        operation_id=str(request["operation_id"]),
        request_sha256=request_sha,
        iso_path=iso.as_posix(),
        iso_size=iso.stat().st_size,
        iso_sha256=hashlib.sha256(iso.read_bytes()).hexdigest(),
        command_argv_sha256=build._invocation_sha256(invocation),
        elapsed_seconds=1.0,
        commands_run=(build.BUILD_SERVICE_NAME,),
        cleanup_complete=True,
    )

    derived_root, derived_wheel, sources = offline_vm_guest_worker.collect_fixed_build_output_sources(request, result)
    assert derived_root == target_root
    assert derived_wheel == wheel
    assert any(item.role == "target-root" for item in sources)
    assert any(item.role == "wheel" and item.source_path == wheel for item in sources)

    wheel.unlink()
    with pytest.raises(offline_vm_worker.OfflineVmContractError, match="unique application wheel"):
        offline_vm_guest_worker.collect_fixed_build_output_sources(request, result)


def test_result_header_is_operation_bound_and_candidate_iso_is_structurally_checked(tmp_path: Path) -> None:
    plan, _, request = _fixture_plan(tmp_path)
    iso = bytearray(20 * 2048)
    iso[32769:32774] = b"CD001"
    boot_record = 17 * 2048
    iso[boot_record + 1 : boot_record + 6] = b"CD001"
    iso[boot_record + 7 : boot_record + 30] = b"EL TORITO SPECIFICATION"
    iso_bytes = bytes(iso)
    header = offline_vm_worker.build_result_header(
        request,
        status="CANDIDATE_ISO",
        iso_size=len(iso_bytes),
        iso_sha256=hashlib.sha256(iso_bytes).hexdigest(),
        command_argv_sha256="c" * 64,
        evidence_size=_TEST_EVIDENCE_SIZE,
        evidence_sha256=_TEST_EVIDENCE_SHA256,
    )
    receipt = offline_vm_worker.build_service_success_receipt(
        request,
        iso_size=len(iso_bytes),
        iso_sha256=hashlib.sha256(iso_bytes).hexdigest(),
        command_argv_sha256="c" * 64,
        evidence_size=_TEST_EVIDENCE_SIZE,
        evidence_sha256=_TEST_EVIDENCE_SHA256,
    )
    prefix = header[: offline_vm_worker.RESULT_RECEIPT_OFFSET]
    commit = offline_vm_worker.build_result_commit_marker(
        request, header_prefix=prefix, service_receipt=receipt,
        evidence_size=_TEST_EVIDENCE_SIZE, evidence_sha256=_TEST_EVIDENCE_SHA256,
    )
    terminal = offline_vm_worker.build_result_terminal_marker(
        request, header_prefix=prefix, service_receipt=receipt, commit_marker=commit,
        evidence_size=_TEST_EVIDENCE_SIZE, evidence_sha256=_TEST_EVIDENCE_SHA256,
    )
    with plan.output_volume.path.open("r+b") as stream:
        stream.seek(offline_vm_worker.RESULT_ISO_OFFSET)
        stream.write(iso_bytes)
        stream.seek(offline_vm_worker.RESULT_RECEIPT_OFFSET)
        stream.write(receipt)
        stream.seek(0)
        stream.write(prefix)
        stream.seek(offline_vm_worker.RESULT_COMMIT_OFFSET)
        stream.write(commit)
        stream.seek(offline_vm_worker.RESULT_TERMINAL_OFFSET)
        stream.write(terminal)
    checked = offline_vm_worker.validate_result_volume(plan.output_volume.path, request)
    assert checked["status"] == "CANDIDATE_ISO"

    foreign = dict(request)
    foreign["operation_id"] = "b" * 32
    with pytest.raises(offline_vm_worker.OfflineVmContractError, match="operation directory|not bound"):
        offline_vm_worker.validate_result_volume(plan.output_volume.path, foreign)


def test_candidate_result_header_requires_exact_evidence_volume_identity(tmp_path: Path) -> None:
    _, _, request = _fixture_plan(tmp_path)
    iso_size = 20 * 2048
    iso_sha = "a" * 64
    command_sha = "b" * 64
    with pytest.raises(offline_vm_worker.OfflineVmContractError, match="evidence size, hash"):
        offline_vm_worker.build_result_header(
            request,
            status="CANDIDATE_ISO",
            iso_size=iso_size,
            iso_sha256=iso_sha,
            command_argv_sha256=command_sha,
        )


def _sparse_iso_hash(size: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    pvd_signature_at = 16 * 2048 + 1
    boot_sector = 17 * 2048
    while offset < size:
        length = min(offline_vm_worker.RESULT_HASH_CHUNK_BYTES, size - offset)
        chunk = bytearray(length)
        if offset <= pvd_signature_at < offset + length:
            chunk[pvd_signature_at - offset : pvd_signature_at - offset + 5] = b"CD001"
        if offset <= boot_sector < offset + length:
            descriptor = boot_sector - offset
            chunk[descriptor + 1 : descriptor + 6] = b"CD001"
            chunk[descriptor + 7 : descriptor + 30] = b"EL TORITO SPECIFICATION"
        digest.update(chunk)
        offset += length
    return digest.hexdigest()


def _guest_candidate_result(request: dict[str, object], candidate: Path, payload: bytes) -> build.GuestBuildResult:
    return build.GuestBuildResult(
        status="CANDIDATE_ISO_PENDING_HOST_VALIDATION",
        operation_id=str(request["operation_id"]),
        request_sha256=offline_vm_worker._sha256_bytes(offline_vm_worker.canonical_json(request)),
        iso_path=candidate.as_posix(),
        iso_size=len(payload),
        iso_sha256=hashlib.sha256(payload).hexdigest(),
        command_argv_sha256="d" * 64,
        elapsed_seconds=1.0,
        commands_run=(build.BUILD_SERVICE_NAME,),
        cleanup_complete=True,
    )


def _run_candidate_publication(plan: offline_vm_worker.WorkerPlan, candidate: Path, result: build.GuestBuildResult,
                               request: dict[str, object], *, publish_service_result: Callable[[], None] | None = None,
                               publish_service_ready: Callable[[], None] | None = None,
                               finalize_evidence_volume: Callable[[], tuple[int, str]] | None = None) -> None:
    fd = os.open(plan.output_volume.path, os.O_RDWR)
    try:
        callback = publish_service_result or (lambda: None)
        build_service._write_candidate_payload_receipt_header(
            fd, candidate, result, request,
            publish_service_result=callback,
            publish_service_ready=publish_service_ready or (lambda: None),
            finalize_evidence_volume=finalize_evidence_volume or (
                lambda: (_TEST_EVIDENCE_SIZE, _TEST_EVIDENCE_SHA256)
            ),
        )
    finally:
        os.close(fd)


def test_candidate_publication_orders_payload_receipt_header_and_final_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, _, request = _fixture_plan(tmp_path)
    candidate = plan.output_volume.path.parent / "candidate.iso"
    payload = b"candidate bytes for durable publication"
    candidate.write_bytes(payload)
    result = _guest_candidate_result(request, candidate, payload)
    events: list[tuple[str, int | None]] = []
    original_pwrite = build_service._pwrite_all
    original_fsync = os.fsync

    def recording_pwrite(fd: int, data: bytes, offset: int) -> None:
        events.append(("write", offset))
        original_pwrite(fd, data, offset)

    def recording_fsync(fd: int) -> None:
        events.append(("fsync", None))
        original_fsync(fd)

    def recording_service_result() -> None:
        events.append(("service-result", None))

    def recording_evidence_finalization() -> tuple[int, str]:
        events.append(("evidence-fsync-hash", None))
        return _TEST_EVIDENCE_SIZE, _TEST_EVIDENCE_SHA256

    def recording_service_ready() -> None:
        events.append(("service-ready", None))

    monkeypatch.setattr(build_service, "_pwrite_all", recording_pwrite)
    monkeypatch.setattr(os, "fsync", recording_fsync)
    _run_candidate_publication(
        plan, candidate, result, request,
        publish_service_result=recording_service_result,
        publish_service_ready=recording_service_ready,
        finalize_evidence_volume=recording_evidence_finalization,
    )

    assert events == [
        ("write", offline_vm_worker.RESULT_ISO_OFFSET),
        ("fsync", None),
        ("evidence-fsync-hash", None),
        ("service-result", None),
        ("write", offline_vm_worker.RESULT_RECEIPT_OFFSET),
        ("fsync", None),
        ("write", 0),
        ("fsync", None),
        ("service-ready", None),
        ("write", offline_vm_worker.RESULT_COMMIT_OFFSET),
        ("fsync", None),
        ("write", offline_vm_worker.RESULT_TERMINAL_OFFSET),
        ("fsync", None),
    ]
    checked = offline_vm_worker.read_result_header(plan.output_volume.path, request)
    assert checked["status"] == "CANDIDATE_ISO"


@pytest.mark.parametrize(
    ("failure", "expected_message"),
    (
        ("copy", "copy failure"),
        ("payload_fsync", "fsync failure"),
        ("evidence_finalize", "evidence-volume fsync/hash failure"),
        ("service_result", "service result failure"),
        ("receipt_write", "receipt write failure"),
        ("receipt_fsync", "fsync failure"),
        ("header_write", "header write failure"),
        ("header_fsync", "fsync failure"),
        ("service_ready", "service ready failure"),
        ("commit_write", "commit write failure"),
        ("terminal_write", "terminal write failure"),
    ),
)
def test_candidate_publication_failures_never_leave_host_accepted_header(
    failure: str,
    expected_message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, _, request = _fixture_plan(tmp_path)
    candidate = plan.output_volume.path.parent / "candidate-failure.iso"
    payload = b"candidate bytes for failure injection"
    candidate.write_bytes(payload)
    result = _guest_candidate_result(request, candidate, payload)
    original_pwrite = build_service._pwrite_all
    original_fsync = os.fsync
    fsync_number = [0]

    def failing_pwrite(fd: int, data: bytes, offset: int) -> None:
        if failure == "copy" and offset == offline_vm_worker.RESULT_ISO_OFFSET:
            raise OSError("injected copy failure")
        if failure == "receipt_write" and offset == offline_vm_worker.RESULT_RECEIPT_OFFSET:
            raise OSError("injected receipt write failure")
        if failure == "header_write" and offset == 0:
            raise OSError("injected header write failure")
        if failure == "commit_write" and offset == offline_vm_worker.RESULT_COMMIT_OFFSET:
            raise OSError("injected commit write failure")
        if failure == "terminal_write" and offset == offline_vm_worker.RESULT_TERMINAL_OFFSET:
            raise OSError("injected terminal write failure")
        original_pwrite(fd, data, offset)

    def failing_fsync(fd: int) -> None:
        fsync_number[0] += 1
        selected = {"payload_fsync": 1, "receipt_fsync": 2, "header_fsync": 3}.get(failure)
        if fsync_number[0] == selected:
            raise OSError("injected fsync failure")
        original_fsync(fd)

    def publish_service_result() -> None:
        if failure == "service_result":
            def fail_write_result(_result: build.GuestBuildResult) -> None:
                raise OSError("injected service result failure")

            monkeypatch.setattr(build_service, "_write_result", fail_write_result)
            build_service._write_result(result)

    def publish_service_ready() -> None:
        if failure == "service_ready":
            raise OSError("injected service ready failure")

    def finalize_evidence_volume() -> tuple[int, str]:
        if failure == "evidence_finalize":
            raise OSError("injected evidence-volume fsync/hash failure")
        return _TEST_EVIDENCE_SIZE, _TEST_EVIDENCE_SHA256

    monkeypatch.setattr(build_service, "_pwrite_all", failing_pwrite)
    monkeypatch.setattr(os, "fsync", failing_fsync)
    with pytest.raises(OSError, match=expected_message):
        _run_candidate_publication(
            plan, candidate, result, request,
            publish_service_result=publish_service_result,
            publish_service_ready=publish_service_ready,
            finalize_evidence_volume=finalize_evidence_volume,
        )
    with pytest.raises(offline_vm_worker.OfflineVmContractError):
        offline_vm_worker.read_result_header(plan.output_volume.path, request)


def test_candidate_writer_without_evidence_volume_stops_before_output_or_receipt(tmp_path: Path) -> None:
    plan, _, request = _fixture_plan(tmp_path)
    candidate = plan.output_volume.path.parent / "candidate-without-evidence.iso"
    payload = b"candidate bytes without a real evidence producer"
    candidate.write_bytes(payload)
    result = _guest_candidate_result(request, candidate, payload)
    with pytest.raises(build.OfflineVmBuildError, match="evidence-volume writer is unavailable"):
        build_service.write_candidate_result_volume(
            candidate,
            result,
            request,
            build_service.guest.GuestIsolation("b" * 64, (), (), (), (), (), (), (), ()),
            publish_service_result=lambda: pytest.fail("service receipt must not publish"),
            publish_service_ready=lambda: pytest.fail("ready receipt must not publish"),
        )
    assert plan.output_volume.path.read_bytes()[: offline_vm_worker.RESULT_HEADER_SIZE] == bytes(
        offline_vm_worker.RESULT_HEADER_SIZE
    )


def test_commit_fsync_error_is_rejected_even_when_commit_bytes_are_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, _, request = _fixture_plan(tmp_path)
    candidate = plan.output_volume.path.parent / "candidate-commit-fsync.iso"
    payload = b"candidate bytes for commit fsync case"
    candidate.write_bytes(payload)
    result = _guest_candidate_result(request, candidate, payload)
    original_pwrite = build_service._pwrite_all
    monkeypatch.setattr(build, "WORKER_OUTPUT", candidate.parent.as_posix())
    original_fsync = os.fsync
    fsync_number = [0]

    def fail_commit_fsync(fd: int) -> None:
        fsync_number[0] += 1
        if fsync_number[0] == 4:
            raise OSError("injected final commit fsync failure")
        original_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_commit_fsync)
    with pytest.raises(OSError, match="final commit fsync"):
        _run_candidate_publication(
            plan, candidate, result, request,
            publish_service_result=lambda: None,
            publish_service_ready=lambda: None,
        )
    # The commit bytes can be visible after fsync fails, but the terminal
    # marker is written only after that fsync returns. Host validation rejects
    # this image even if the commit slot itself is readable.
    with pytest.raises(offline_vm_worker.OfflineVmContractError, match="post-commit durability marker"):
        offline_vm_worker.read_result_header(plan.output_volume.path, request)
    with plan.output_volume.path.open("rb") as stream:
        stream.seek(offline_vm_worker.RESULT_COMMIT_OFFSET)
        assert stream.read(offline_vm_worker.RESULT_COMMIT_SIZE).startswith(offline_vm_worker.RESULT_COMMIT_MAGIC)
        stream.seek(offline_vm_worker.RESULT_TERMINAL_OFFSET)
        assert stream.read(offline_vm_worker.RESULT_TERMINAL_SIZE) == bytes(offline_vm_worker.RESULT_TERMINAL_SIZE)


def test_host_fsync_can_finalize_terminal_slot_after_commit_fsync_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, _, request = _fixture_plan(tmp_path)
    candidate = plan.output_volume.path.parent / "candidate-terminal-fsync.iso"
    iso = bytearray(20 * 2048)
    iso[32769:32774] = b"CD001"
    iso[17 * 2048 + 1 : 17 * 2048 + 6] = b"CD001"
    iso[17 * 2048 + 7 : 17 * 2048 + 30] = b"EL TORITO SPECIFICATION"
    payload = bytes(iso)
    candidate.write_bytes(payload)
    result = _guest_candidate_result(request, candidate, payload)
    monkeypatch.setattr(build, "WORKER_OUTPUT", candidate.parent.as_posix())
    original_fsync = os.fsync
    fsync_number = [0]

    def fail_terminal_fsync(fd: int) -> None:
        fsync_number[0] += 1
        if fsync_number[0] == 5:
            raise OSError("injected terminal marker fsync failure")
        original_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_terminal_fsync)
    with pytest.raises(OSError, match="terminal marker fsync"):
        _run_candidate_publication(
            plan, candidate, result, request,
            publish_service_result=lambda: None,
            publish_service_ready=lambda: None,
        )

    # The guest commit fsync (number 4) returned before the terminal write.
    # After QEMU exits, host fsync makes the visible terminal marker durable;
    # the host then independently validates the full ISO bytes.
    fd = os.open(plan.output_volume.path, os.O_RDONLY)
    try:
        monkeypatch.setattr(os, "fsync", original_fsync)
        offline_vm_worker._fsync_output_after_worker(plan.output_volume.path, fd)
    finally:
        os.close(fd)
    assert offline_vm_worker.validate_result_volume(plan.output_volume.path, request)["status"] == "CANDIDATE_ISO"


def test_terminal_marker_after_returned_commit_fsync_is_host_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, _, request = _fixture_plan(tmp_path)
    candidate = plan.output_volume.path.parent / "candidate-terminal-durable.iso"
    iso = bytearray(20 * 2048)
    iso[32769:32774] = b"CD001"
    iso[17 * 2048 + 1 : 17 * 2048 + 6] = b"CD001"
    iso[17 * 2048 + 7 : 17 * 2048 + 30] = b"EL TORITO SPECIFICATION"
    payload = bytes(iso)
    candidate.write_bytes(payload)
    result = _guest_candidate_result(request, candidate, payload)
    monkeypatch.setattr(build, "WORKER_OUTPUT", candidate.parent.as_posix())
    _run_candidate_publication(
        plan, candidate, result, request,
        publish_service_result=lambda: None,
        publish_service_ready=lambda: None,
    )
    fd = os.open(plan.output_volume.path, os.O_RDONLY)
    try:
        called = []
        original_fsync = os.fsync

        def record_fsync(value: int) -> None:
            called.append(value)
            original_fsync(value)

        monkeypatch.setattr(os, "fsync", record_fsync)
        offline_vm_worker._fsync_output_after_worker(plan.output_volume.path, fd)
        assert called == [fd]
    finally:
        os.close(fd)
    assert offline_vm_worker.validate_result_volume(plan.output_volume.path, request)["status"] == "CANDIDATE_ISO"


@pytest.mark.skipif(os.name != "posix", reason="durable renameat2 publication is Linux-only")
def test_commit_failure_after_durable_service_result_is_not_host_acceptable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, _, request = _fixture_plan(tmp_path)
    candidate = plan.output_volume.path.parent / "candidate-service-result-before-commit.iso"
    payload = b"candidate bytes before failed output commit".ljust(18 * 2048, b"\0")
    candidate.write_bytes(payload)
    invocation = build._expected_fixed_invocation(
        build._sha256_bytes(offline_vm_worker.canonical_json(request)),
        Path(build.WORKER_PACSTRAP_BIN) / "pacstrap",
        str(request["operation_id"]),
    )
    result = _guest_candidate_result(request, candidate, payload)
    result = replace(result, command_argv_sha256=build._invocation_sha256(invocation))
    service_result_path = tmp_path / "service-result.json"
    ready_path = tmp_path / "service-ready.json"
    started_path = tmp_path / "service-started.json"
    monkeypatch.setattr(build, "BUILD_RESULT_PATH", str(service_result_path))
    monkeypatch.setattr(build, "BUILD_RESULT_READY_PATH", str(ready_path))
    monkeypatch.setattr(build, "BUILD_SERVICE_STARTED_PATH", str(started_path))
    monkeypatch.setattr(build, "WORKER_OUTPUT", candidate.parent.as_posix())
    original_pwrite = build_service._pwrite_all

    def fail_commit_write(fd: int, data: bytes, offset: int) -> None:
        if offset == offline_vm_worker.RESULT_COMMIT_OFFSET:
            raise OSError("injected output commit failure")
        original_pwrite(fd, data, offset)

    monkeypatch.setattr(build_service, "_pwrite_all", fail_commit_write)
    with pytest.raises(OSError, match="output commit failure"):
        _run_candidate_publication(
            plan, candidate, result, request,
            publish_service_result=lambda: build_service._write_result(result),
            publish_service_ready=lambda: build_service._write_result_ready(result),
        )

    assert service_result_path.is_file()
    assert ready_path.is_file()
    build._write_exclusive_private(started_path, build.canonical_json({
        "format": build.BUILD_RECEIPT_FORMAT,
        "operation_id": result.operation_id,
        "request_sha256": result.request_sha256,
    }), 0o600)
    with pytest.raises(offline_vm_worker.OfflineVmContractError, match="final commit marker"):
        offline_vm_worker.read_result_header(plan.output_volume.path, request)
    assert build._load_service_result(invocation) == result
    assert not build_service.candidate_commit_is_present(request)


def test_guest_readiness_requires_bound_terminal_marker_after_visible_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, _deployment, request = _fixture_plan(tmp_path)
    output_path = plan.output_volume.path
    output_capacity = plan.output_volume.size
    monkeypatch.setattr(build_service, "OUTPUT_DEVICE", output_path)
    monkeypatch.setattr(build_service.stat, "S_ISBLK", lambda _mode: True)
    monkeypatch.setattr(build, "_linux_stat_rdev", lambda _value: 1234)
    monkeypatch.setattr(
        build,
        "_linux_ioctl",
        lambda _fd, _command, _payload: struct.pack("Q", output_capacity),
    )

    command_digest = "c" * 64
    iso_size = 2048
    iso_digest = "d" * 64
    header = offline_vm_worker.build_result_header(
        request,
        status="CANDIDATE_ISO",
        iso_size=iso_size,
        iso_sha256=iso_digest,
        command_argv_sha256=command_digest,
        evidence_size=_TEST_EVIDENCE_SIZE,
        evidence_sha256=_TEST_EVIDENCE_SHA256,
    )
    header_prefix = header[: offline_vm_worker.RESULT_RECEIPT_OFFSET]
    receipt = offline_vm_worker.build_service_success_receipt(
        request,
        iso_size=iso_size,
        iso_sha256=iso_digest,
        command_argv_sha256=command_digest,
        evidence_size=_TEST_EVIDENCE_SIZE,
        evidence_sha256=_TEST_EVIDENCE_SHA256,
    )
    commit = offline_vm_worker.build_result_commit_marker(
        request, header_prefix=header_prefix, service_receipt=receipt,
        evidence_size=_TEST_EVIDENCE_SIZE, evidence_sha256=_TEST_EVIDENCE_SHA256,
    )
    terminal = offline_vm_worker.build_result_terminal_marker(
        request,
        header_prefix=header_prefix,
        service_receipt=receipt,
        commit_marker=commit,
        evidence_size=_TEST_EVIDENCE_SIZE,
        evidence_sha256=_TEST_EVIDENCE_SHA256,
    )
    with output_path.open("r+b") as stream:
        stream.seek(0)
        stream.write(header_prefix)
        stream.seek(offline_vm_worker.RESULT_RECEIPT_OFFSET)
        stream.write(receipt)
        stream.seek(offline_vm_worker.RESULT_COMMIT_OFFSET)
        stream.write(commit)
        stream.seek(offline_vm_worker.RESULT_TERMINAL_OFFSET)
        stream.write(bytes(offline_vm_worker.RESULT_TERMINAL_SIZE))

    # Commit bytes may be visible while the guest is still before or inside
    # its commit fsync. The readiness predicate must keep waiting for the
    # later marker that binds the exact commit and records fsync return.
    assert not build_service.candidate_commit_is_present(request)

    with output_path.open("r+b") as stream:
        stream.seek(offline_vm_worker.RESULT_TERMINAL_OFFSET)
        stream.write(terminal)
    assert build_service.candidate_commit_is_present(request)

    altered_prefix = bytes([header_prefix[0] ^ 1]) + header_prefix[1:]
    altered_commit = offline_vm_worker.build_result_commit_marker(
        request, header_prefix=altered_prefix, service_receipt=receipt,
        evidence_size=_TEST_EVIDENCE_SIZE, evidence_sha256=_TEST_EVIDENCE_SHA256,
    )
    altered_terminal = offline_vm_worker.build_result_terminal_marker(
        request,
        header_prefix=header_prefix,
        service_receipt=receipt,
        commit_marker=altered_commit,
        evidence_size=_TEST_EVIDENCE_SIZE,
        evidence_sha256=_TEST_EVIDENCE_SHA256,
    )
    with output_path.open("r+b") as stream:
        stream.seek(offline_vm_worker.RESULT_TERMINAL_OFFSET)
        stream.write(altered_terminal)
    assert not build_service.candidate_commit_is_present(request)


def test_large_sparse_candidate_is_hashed_in_bounded_chunks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan, _, request = _fixture_plan(tmp_path)
    iso_size = 32 * 1024 * 1024
    output_size = offline_vm_worker.RESULT_HEADER_SIZE + iso_size
    output_path = plan.output_volume.path.parent / "large-output.raw"
    candidate_plan = replace(
        plan,
        output_volume=offline_vm_worker.VolumeIdentity(output_path, output_size),
    )
    request = offline_vm_worker.make_request(candidate_plan)
    with output_path.open("xb") as stream:
        stream.truncate(output_size)
        _write_candidate_metadata(stream, request, iso_size, _sparse_iso_hash(iso_size))
        stream.seek(offline_vm_worker.RESULT_ISO_OFFSET + 16 * 2048 + 1)
        stream.write(b"CD001")
        stream.seek(offline_vm_worker.RESULT_ISO_OFFSET + 17 * 2048 + 1)
        stream.write(b"CD001\x00EL TORITO SPECIFICATION")
    output_path.chmod(offline_vm_worker.OPERATION_FILE_MODE)

    original_open = Path.open

    class ReadBounded:
        def __init__(self, wrapped: object) -> None:
            self._wrapped = wrapped

        def __enter__(self) -> "ReadBounded":
            self._wrapped.__enter__()  # type: ignore[attr-defined]
            return self

        def __exit__(self, *args: object) -> object:
            return self._wrapped.__exit__(*args)  # type: ignore[attr-defined]

        def seek(self, *args: object) -> object:
            return self._wrapped.seek(*args)  # type: ignore[attr-defined]

        def read(self, size: int = -1) -> bytes:
            assert 0 <= size <= offline_vm_worker.RESULT_HASH_CHUNK_BYTES
            return self._wrapped.read(size)  # type: ignore[attr-defined]

    def bounded_open(path: Path, *args: object, **kwargs: object) -> object:
        opened = original_open(path, *args, **kwargs)
        if path == output_path and args and args[0] == "rb":
            return ReadBounded(opened)
        return opened

    monkeypatch.setattr(Path, "open", bounded_open)
    result = offline_vm_worker.validate_result_volume(output_path, request)
    assert result["status"] == "CANDIDATE_ISO"


def test_unavailable_guest_result_is_explicitly_not_a_candidate(tmp_path: Path) -> None:
    plan, _, request = _fixture_plan(tmp_path)
    header = offline_vm_worker.build_result_header(request, status="BUILD_BACKEND_UNAVAILABLE")
    with plan.output_volume.path.open("r+b") as stream:
        stream.seek(0)
        stream.write(header)
    parsed = offline_vm_worker.read_result_header(plan.output_volume.path, request)
    assert parsed["status"] == "BUILD_BACKEND_UNAVAILABLE"
    with pytest.raises(offline_vm_worker.OfflineVmContractError, match="did not produce"):
        offline_vm_worker.validate_result_volume(plan.output_volume.path, request)


def test_linux_ioctl_fails_closed_when_fcntl_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(offline_vm_guest_worker, "_fcntl_module", None)
    with pytest.raises(offline_vm_worker.OfflineVmContractError, match="ioctl support is unavailable"):
        offline_vm_guest_worker._linux_ioctl(1, offline_vm_guest_worker.BLKGETSIZE64, bytes(8))
