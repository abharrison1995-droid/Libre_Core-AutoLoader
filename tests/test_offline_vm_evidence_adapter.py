from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from usb_builder import build_usb, offline_vm_build as host, offline_vm_evidence as evidence
from usb_builder import offline_vm_worker as worker


pytestmark = pytest.mark.skipif(os.name != "posix", reason="pending host adapter relies on Linux no-follow operation storage")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _logical_evidence_claim(raw: bytes) -> tuple[int, str]:
    _magic, manifest_length, _digest = evidence._HEADER.unpack(raw[: evidence._HEADER.size])
    manifest = evidence._parse_canonical_json(
        raw[evidence.HEADER_SIZE : evidence.HEADER_SIZE + manifest_length], "manifest"
    )
    logical_size = int(manifest["volume_size"])
    return logical_size, _sha(raw[:logical_size])


def _canonical_command_sha(request: dict[str, object]) -> str:
    request_sha = host._sha256_bytes(worker.canonical_json(request))
    operation_id = str(request["operation_id"])
    invocation = host._expected_fixed_invocation(
        request_sha,
        Path(host.WORKER_PACSTRAP_BIN) / "pacstrap",
        operation_id,
    )
    return host._invocation_sha256(invocation)


def _case(tmp_path: Path, *, package_image: bytes = b"package-image") -> tuple[Path, dict[str, object], worker.WorkerRun, bytes, bytes]:
    operation_id = "a" * 32
    tmp_path.mkdir(parents=True, exist_ok=True)
    operation_root = tmp_path / operation_id
    operation_root.mkdir(mode=0o700)
    operation_root.chmod(0o700)

    source_root = operation_root / "source"
    profile_root = source_root / "usb_builder" / "archiso_profile"
    profile_root.mkdir(parents=True)
    (source_root / "usb_builder").mkdir(exist_ok=True)
    shutil.copyfile(worker.SNAPSHOT_LOCK_PATH, source_root / "usb_builder" / "snapshot.lock.json")
    packages_file = profile_root / "packages.x86_64"
    packages_bytes = b"base\nsystemd\n"
    packages_file.write_bytes(packages_bytes)

    source_bytes = b"source-image"
    source_identity = worker.ImageIdentity(operation_root / "source.img", len(source_bytes), _sha(source_bytes))
    package_identity = worker.ImageIdentity(operation_root / "packages.img", len(package_image), _sha(package_image))
    profile_files = (
        worker.ProfileFile("usb_builder/archiso_profile/packages.x86_64", len(packages_bytes), _sha(packages_bytes)),
    )
    output_capacity = worker.RESULT_ISO_OFFSET + 128 * 1024
    plan = worker.WorkerPlan(
        operation_id=operation_id,
        snapshot_lock_sha256=worker._pinned_snapshot_lock_sha256(),
        snapshot_date="2026-09-30",
        package_versions=dict(worker.LOCKED_PACKAGES),
        worker_image_sha256="b" * 64,
        source_image=source_identity,
        package_image=package_identity,
        request_image=worker.ImageIdentity(operation_root / "request.raw", worker.REQUEST_VOLUME_SIZE, "c" * 64),
        scratch_volume=worker.VolumeIdentity(operation_root / "scratch.raw", worker.MIN_SCRATCH_BYTES),
        output_volume=worker.VolumeIdentity(operation_root / "output.raw", output_capacity),
        evidence_volume=worker.VolumeIdentity(operation_root / "evidence.raw", 2 * 1024 * 1024),
        profile_files=profile_files,
        log_path=operation_root / "qemu.log",
        timeout_seconds=600,
    )
    request = worker.make_request(plan)

    iso = bytearray(20 * 2048)
    iso[32769:32774] = b"CD001"
    boot = 17 * 2048
    iso[boot + 1 : boot + 6] = b"CD001"
    iso[boot + 7 : boot + 30] = b"EL TORITO SPECIFICATION"
    iso_bytes = bytes(iso)
    command_sha = _canonical_command_sha(request)

    before = {
        "qemu": "1" * 64,
        "worker": "2" * 64,
        "request": _sha(worker.encode_request_volume(request)),
        "source": source_identity.sha256,
        "packages": package_identity.sha256,
        "snapshot_lock": plan.snapshot_lock_sha256,
    }
    run = worker.WorkerRun(
        argv_sha256="4" * 64,
        returncode=0,
        timed_out=False,
        elapsed_seconds=1.0,
        readonly_hashes_before=before,
        readonly_hashes_after=dict(before),
        outcome="CANDIDATE_STRUCTURALLY_VALIDATED",
        candidate_iso_structurally_validated=True,
    )
    _write_output(operation_root / "output.raw", request, iso_bytes, command_sha, output_capacity)
    return operation_root, request, run, iso_bytes, command_sha


def _write_output(
    path: Path,
    request: dict[str, object],
    iso: bytes,
    command_sha: str,
    capacity: int,
    *,
    evidence_size: int = 4096,
    evidence_sha256: str = "f" * 64,
) -> None:
    if path.exists():
        with path.open("r+b") as stream:
            stream.truncate(0)
            stream.truncate(capacity)
    else:
        with path.open("xb") as stream:
            stream.truncate(capacity)
    path.chmod(0o600)
    iso_sha = _sha(iso)
    header = worker.build_result_header(
        request,
        status="CANDIDATE_ISO",
        iso_size=len(iso),
        iso_sha256=iso_sha,
        command_argv_sha256=command_sha,
        evidence_size=evidence_size,
        evidence_sha256=evidence_sha256,
    )
    prefix = header[: worker.RESULT_RECEIPT_OFFSET]
    receipt = worker.build_service_success_receipt(
        request,
        iso_size=len(iso),
        iso_sha256=iso_sha,
        command_argv_sha256=command_sha,
        evidence_size=evidence_size,
        evidence_sha256=evidence_sha256,
    )
    commit = worker.build_result_commit_marker(
        request, header_prefix=prefix, service_receipt=receipt,
        evidence_size=evidence_size, evidence_sha256=evidence_sha256,
    )
    terminal = worker.build_result_terminal_marker(
        request, header_prefix=prefix, service_receipt=receipt, commit_marker=commit,
        evidence_size=evidence_size, evidence_sha256=evidence_sha256,
    )
    with path.open("r+b") as stream:
        stream.seek(worker.RESULT_ISO_OFFSET)
        stream.write(iso)
        stream.seek(worker.RESULT_RECEIPT_OFFSET)
        stream.write(receipt)
        stream.seek(0)
        stream.write(prefix)
        stream.seek(worker.RESULT_COMMIT_OFFSET)
        stream.write(commit)
        stream.seek(worker.RESULT_TERMINAL_OFFSET)
        stream.write(terminal)
        stream.flush()
        os.fsync(stream.fileno())


def _target_root_records(target_bytes: bytes) -> list[dict[str, object]]:
    return [
        {"path": "target-root", "kind": "directory", "role": "target-root", "mode": 0o755, "size": 0, "sha256": None, "link_target": None},
        {"path": "target-root/bin", "kind": "directory", "role": "target-root-directory", "mode": 0o755, "size": 0, "sha256": None, "link_target": None},
        {
            "path": "target-root/bin/autoloader",
            "kind": "file",
            "role": "target-root-file",
            "mode": 0o755,
            "size": len(target_bytes),
            "sha256": _sha(target_bytes),
            "link_target": None,
        },
    ]


def _write_bundle(
    path: Path,
    binding: evidence.EvidenceBinding,
    request: dict[str, object],
    *,
    package_image_sha: str,
    command_sha: str,
    iso_sha: str,
    iso_size: int,
    package_list: list[str] | None = None,
    provenance_updates: dict[str, object] | None = None,
    terminal_status: str = "COMPLETE",
    include_roles: set[str] | None = None,
    extra_log: bytes | None = None,
) -> bytes:
    target_bytes = b"target-root-test-payload"
    root_records = _target_root_records(target_bytes)
    root_digest = _sha(evidence.canonical_json({"format": host.TARGET_ROOT_INVENTORY_FORMAT, "entries": root_records}))
    package_record: dict[str, object] = {
        "format": host.PACKAGE_EVIDENCE_FORMAT,
        "operation_id": binding.operation_id,
        "request_sha256": binding.request_sha256,
        "package_image_sha256": package_image_sha,
        "candidate_iso_sha256": iso_sha,
        "candidate_iso_size": iso_size,
        "packages": package_list or ["base 1.0", "systemd 1.0"],
    }
    package_raw = evidence.canonical_json(package_record)
    wheel_raw = b"PK\x03\x04synthetic-wheel"
    entries: list[tuple[str, str, int, bytes | None]] = [
        ("build-provenance.json", "provenance", 0o600, None),
        ("command-status.json", "command-status", 0o600, evidence.canonical_json({
            "format": evidence.FORMAT,
            "status": "EXITED_ZERO",
            "exit_code": 0,
            "argv_sha256": command_sha,
            "operation_id": binding.operation_id,
            "request_sha256": binding.request_sha256,
            "candidate_iso_sha256": iso_sha,
            "candidate_iso_size": iso_size,
        })),
        ("package-inventory.json", "package-inventory", 0o600, package_raw),
        ("target-root", "target-root", 0o755, None),
        ("target-root/bin", "target-root-directory", 0o755, None),
        ("target-root/bin/autoloader", "target-root-file", 0o755, target_bytes),
        ("wheel.whl", "wheel", 0o600, wheel_raw),
    ]
    if extra_log is not None:
        entries.insert(2, ("logs/build.log", "log", 0o600, extra_log))
    provenance: dict[str, object] = {
        "format": host.PROVENANCE_EVIDENCE_FORMAT,
        "operation_id": binding.operation_id,
        "request_sha256": binding.request_sha256,
        "source_sha256": binding.source_sha256,
        "package_image_sha256": package_image_sha,
        "profile_inventory_sha256": binding.profile_sha256,
        "snapshot_lock_sha256": binding.snapshot_lock_sha256,
        "snapshot_date": binding.snapshot_date,
        "candidate_iso_sha256": iso_sha,
        "candidate_iso_size": iso_size,
        "command_argv_sha256": command_sha,
        "package_inventory_sha256": _sha(package_raw),
        "wheel_sha256": _sha(wheel_raw),
        "wheel_size": len(wheel_raw),
        "target_root_inventory_sha256": root_digest,
    }
    if provenance_updates:
        provenance.update(provenance_updates)
    entries[0] = (entries[0][0], entries[0][1], entries[0][2], evidence.canonical_json(provenance))
    if include_roles is not None:
        entries = [item for item in entries if item[1] in include_roles]
    payload = bytearray()
    raw_entries: list[dict[str, object]] = []
    for name, role, mode, content in entries:
        kind = "directory" if content is None else "file"
        if kind == "directory":
            raw_entries.append({"path": name, "kind": kind, "role": role, "mode": mode, "offset": 0, "size": 0, "sha256": None})
        else:
            assert content is not None
            raw_entries.append({
                "path": name, "kind": kind, "role": role, "mode": mode,
                "offset": evidence.PAYLOAD_OFFSET + len(payload), "size": len(content), "sha256": _sha(content),
            })
            payload.extend(content)
    volume_size = evidence.PAYLOAD_OFFSET + len(payload) + 37 + evidence.TRAILER_SIZE
    volume_capacity = binding.evidence_capacity
    assert volume_capacity >= volume_size
    manifest = {
        "format": evidence.FORMAT,
        "binding": binding.as_dict(),
        "entries": raw_entries,
        "payload_sha256": _sha(bytes(payload)),
        "payload_size": len(payload),
        "volume_size": volume_size,
        "volume_capacity": volume_capacity,
    }
    manifest_raw = evidence.canonical_json(manifest)
    manifest_hash = hashlib.sha256(manifest_raw).digest()
    header = evidence._HEADER.pack(evidence.HEADER_MAGIC, len(manifest_raw), manifest_hash)
    output = bytearray(evidence.HEADER_SIZE)
    output[:len(header)] = header
    output.extend(manifest_raw)
    output.extend(b"\0" * (evidence.PAYLOAD_OFFSET - len(output)))
    output.extend(payload)
    output.extend(b"\0" * (volume_size - evidence.TRAILER_SIZE - len(output)))
    terminal = {
        "format": evidence.FORMAT,
        "status": terminal_status,
        "binding": binding.as_dict(),
        "manifest_sha256": manifest_hash.hex(),
        "payload_sha256": manifest["payload_sha256"],
        "payload_size": manifest["payload_size"],
        "entry_count": len(raw_entries),
        "volume_size": volume_size,
        "volume_capacity": volume_capacity,
    }
    terminal_raw = evidence.canonical_json(terminal)
    trailer = evidence._TRAILER.pack(evidence.TRAILER_MAGIC, len(terminal_raw), hashlib.sha256(terminal_raw).digest())
    trailer_block = bytearray(evidence.TRAILER_SIZE)
    trailer_block[:len(trailer)] = trailer
    trailer_block[len(trailer):len(trailer) + len(terminal_raw)] = terminal_raw
    output.extend(trailer_block)
    output.extend(b"\0" * (volume_capacity - len(output)))
    assert len(output) == volume_capacity
    path.write_bytes(output)
    path.chmod(0o600)
    return bytes(output)


@pytest.fixture(autouse=True)
def _fixed_output_volume_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "_validate_operation_directory", lambda *_args: None)
    monkeypatch.setattr(worker, "verify_writable_volume", lambda *_args: None)


@pytest.fixture(autouse=True)
def _existing_usb_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_usb, "_profile_boot_modes", lambda _path: ("bios", "uefi"))
    monkeypatch.setattr(build_usb, "_audit_profile_line_endings", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(build_usb, "_verify_archiso_initramfs_config", lambda *_args, **_kwargs: "pinned")
    monkeypatch.setattr(build_usb, "_verify_usb_initramfs_derivative", lambda *_args, **_kwargs: "pinned")
    monkeypatch.setattr(
        build_usb,
        "_validate_target_root_install",
        lambda target_root, *, wheel, runner: {"status": "APP_INSTALLED_AND_RUNTIME_CHECKED"},
    )
    monkeypatch.setattr(build_usb, "_record_package_versions", lambda *_args, **_kwargs: ("base 1.0", "systemd 1.0"))


def _ready_case(tmp_path: Path, **bundle_overrides: Any) -> tuple[Path, Path, Path, dict[str, object], worker.WorkerRun]:
    operation_root, request, run, iso, command_sha = _case(tmp_path)
    output = operation_root / "output.raw"
    header = worker.validate_result_volume(output, request)
    parsed = worker.parse_request(worker.canonical_json(request))
    binding = evidence.EvidenceBinding(
        operation_id=str(parsed["operation_id"]),
        request_sha256=host._sha256_bytes(worker.canonical_json(parsed)),
        source_sha256=parsed["source"]["sha256"],  # type: ignore[index]
        package_sha256=parsed["packages"]["sha256"],  # type: ignore[index]
        profile_sha256=parsed["profile"]["sha256"],  # type: ignore[index]
        snapshot_lock_sha256=parsed["snapshot"]["lock_sha256"],  # type: ignore[index]
        snapshot_date=str(parsed["snapshot"]["date"]),  # type: ignore[index]
        candidate_iso_sha256=str(header["iso_sha256"]),
        candidate_iso_size=int(header["iso_size"]),
        command_argv_sha256=command_sha,
        evidence_capacity=int(parsed["layout"]["evidence_capacity"]),  # type: ignore[index]
    )
    evidence_volume = operation_root / "evidence.raw"
    package_sha = parsed["packages"]["sha256"]  # type: ignore[index]
    bundle_bytes = _write_bundle(
        evidence_volume,
        binding,
        parsed,
        package_image_sha=str(package_sha),
        command_sha=command_sha,
        iso_sha=str(header["iso_sha256"]),
        iso_size=int(header["iso_size"]),
        **bundle_overrides,
    )
    evidence_size, evidence_sha = _logical_evidence_claim(bundle_bytes)
    _write_output(
        output, parsed, iso,
        command_sha,
        int(parsed["layout"]["output_capacity"]),  # type: ignore[index]
        evidence_size=evidence_size,
        evidence_sha256=evidence_sha,
    )
    return operation_root, output, evidence_volume, parsed, run


def test_pending_adapter_extracts_exact_bound_evidence_and_reuses_usb_validators(tmp_path: Path) -> None:
    operation_root, output, evidence_volume, request, run = _ready_case(tmp_path)
    report = host.validate_evidence_bundle_with_usb_builder(
        request=request,
        worker_run=run,
        output_volume=output,
        evidence_volume=evidence_volume,
        operation_root=operation_root,
        runner=object(),
    )
    assert report.status == "USB_PROFILE_TARGET_ROOT_PACKAGES_AND_TRANSPORT_EVIDENCE_SELF_CONSISTENCY_CHECKED_PENDING_REVIEW_AND_PUBLICATION"
    assert report.published is False
    assert report.candidate_iso_size == 20 * 2048
    header = worker.validate_result_volume(output, request)
    assert report.candidate_iso_sha256 == header["iso_sha256"]
    assert report.evidence_manifest_sha256 is not None
    assert (operation_root / evidence.EXTRACTION_DIRECTORY / "target-root/bin/autoloader").read_bytes() == b"target-root-test-payload"
    assert (operation_root / evidence.EXTRACTION_DIRECTORY / "wheel.whl").is_file()


def test_pending_adapter_rejects_self_consistent_noncanonical_command_digest(tmp_path: Path) -> None:
    operation_root, request, run, iso_bytes, _command_sha = _case(tmp_path)
    output = operation_root / "output.raw"
    output.unlink()
    _write_output(output, request, iso_bytes, "e" * 64, worker.RESULT_ISO_OFFSET + 128 * 1024)
    evidence_volume = operation_root / "evidence.raw"
    with pytest.raises(host.OfflineVmBuildError, match="fixed invocation"):
        host.validate_evidence_bundle_with_usb_builder(
            request=request,
            worker_run=run,
            output_volume=output,
            evidence_volume=evidence_volume,
            operation_root=operation_root,
            runner=object(),
        )
    assert not (operation_root / ".host-candidate-consumed").exists()
    assert not (operation_root / evidence.EXTRACTION_DIRECTORY).exists()


@pytest.mark.parametrize(
    "wrong",
    ["request", "command", "package", "provenance", "iso", "iso-size", "packages-list", "operation"],
    ids=["request", "command", "package", "provenance", "iso", "iso-size", "packages-list", "operation"],
)
def test_pending_adapter_rejects_swapped_binding_evidence(tmp_path: Path, wrong: str) -> None:
    operation_root, request, run, iso_bytes, command_sha = _case(tmp_path)
    output = operation_root / "output.raw"
    header = worker.validate_result_volume(output, request)
    parsed = worker.parse_request(worker.canonical_json(request))
    binding = evidence.EvidenceBinding(
        operation_id=str(parsed["operation_id"]),
        request_sha256=host._sha256_bytes(worker.canonical_json(parsed)),
        source_sha256=parsed["source"]["sha256"],  # type: ignore[index]
        package_sha256=parsed["packages"]["sha256"],  # type: ignore[index]
        profile_sha256=parsed["profile"]["sha256"],  # type: ignore[index]
        snapshot_lock_sha256=parsed["snapshot"]["lock_sha256"],  # type: ignore[index]
        snapshot_date=str(parsed["snapshot"]["date"]),  # type: ignore[index]
        candidate_iso_sha256=str(header["iso_sha256"]),
        candidate_iso_size=int(header["iso_size"]),
        command_argv_sha256=command_sha,
        evidence_capacity=int(parsed["layout"]["evidence_capacity"]),  # type: ignore[index]
    )
    if wrong == "request":
        binding = replace(binding, request_sha256="e" * 64)
    elif wrong == "command":
        binding = replace(binding, command_argv_sha256="e" * 64)
    elif wrong == "iso":
        binding = replace(binding, candidate_iso_sha256="e" * 64)
    elif wrong == "iso-size":
        binding = replace(binding, candidate_iso_size=binding.candidate_iso_size + 1)
    elif wrong == "operation":
        binding = replace(binding, operation_id="f" * 32)
    evidence_volume = operation_root / "evidence.raw"
    bundle_bytes = _write_bundle(
        evidence_volume,
        binding,
        parsed,
        package_image_sha=("e" * 64 if wrong == "package" else parsed["packages"]["sha256"]),  # type: ignore[index]
        command_sha=binding.command_argv_sha256,
        iso_sha=binding.candidate_iso_sha256,
        iso_size=binding.candidate_iso_size,
        package_list=["base 1.0"] if wrong == "packages-list" else None,
        provenance_updates={"source_sha256": "e" * 64} if wrong == "provenance" else None,
    )
    bundle_size, bundle_sha = _logical_evidence_claim(bundle_bytes)
    _write_output(
        output, parsed, iso_bytes, command_sha,
        int(parsed["layout"]["output_capacity"]),  # type: ignore[index]
        evidence_size=bundle_size, evidence_sha256=bundle_sha,
    )
    with pytest.raises((host.OfflineVmBuildError, evidence.EvidenceBundleError)):
        host.validate_evidence_bundle_with_usb_builder(
            request=request, worker_run=run, output_volume=output, evidence_volume=evidence_volume,
            operation_root=operation_root, runner=object(),
        )
    if wrong in {"package", "packages-list", "provenance"}:
        # Extraction is complete at the transport layer, but semantic failure
        # consumes the one-use operation and still yields no host report.
        assert (operation_root / evidence.EXTRACTION_DIRECTORY / evidence.EXTRACTION_MARKER).is_file()
        assert (operation_root / ".host-candidate-consumed").is_file()
    else:
        assert not (operation_root / evidence.EXTRACTION_DIRECTORY / evidence.EXTRACTION_MARKER).exists()


def test_pending_adapter_rejects_missing_partial_and_replayed_evidence(tmp_path: Path) -> None:
    operation_root, request, run, _iso, _command = _case(tmp_path)
    output = operation_root / "output.raw"
    missing = operation_root / "evidence.raw"
    with pytest.raises((host.OfflineVmBuildError, evidence.EvidenceBundleError)):
        host.validate_evidence_bundle_with_usb_builder(
            request=request, worker_run=run, output_volume=output, evidence_volume=missing,
            operation_root=operation_root, runner=object(),
        )
    operation_root2, output2, volume2, request2, run2 = _ready_case(tmp_path / "partial")
    parsed2 = worker.parse_request(worker.canonical_json(request2))
    header2 = worker.validate_result_volume(output2, request2)
    binding2 = evidence.EvidenceBinding(
        operation_id=str(parsed2["operation_id"]),
        request_sha256=host._sha256_bytes(worker.canonical_json(parsed2)),
        source_sha256=parsed2["source"]["sha256"],  # type: ignore[index]
        package_sha256=parsed2["packages"]["sha256"],  # type: ignore[index]
        profile_sha256=parsed2["profile"]["sha256"],  # type: ignore[index]
        snapshot_lock_sha256=parsed2["snapshot"]["lock_sha256"],  # type: ignore[index]
        snapshot_date=str(parsed2["snapshot"]["date"]),  # type: ignore[index]
        candidate_iso_sha256=str(header2["iso_sha256"]),
        candidate_iso_size=int(header2["iso_size"]),
        command_argv_sha256=_canonical_command_sha(parsed2),
        evidence_capacity=int(parsed2["layout"]["evidence_capacity"]),  # type: ignore[index]
    )
    raw = volume2.read_bytes()
    output_raw = output2.read_bytes()
    incomplete_bundle = _write_bundle(
        volume2,
        binding2,
        request2,
        package_image_sha=request2["packages"]["sha256"],  # type: ignore[index]
        command_sha=binding2.command_argv_sha256,
        iso_sha=binding2.candidate_iso_sha256,
        iso_size=binding2.candidate_iso_size,
        terminal_status="INCOMPLETE",
    )
    volume2.chmod(0o600)
    incomplete_size, incomplete_sha = _logical_evidence_claim(incomplete_bundle)
    _write_output(
        output2, parsed2, _iso,
        _canonical_command_sha(parsed2),
        int(parsed2["layout"]["output_capacity"]),  # type: ignore[index]
        evidence_size=incomplete_size, evidence_sha256=incomplete_sha,
    )
    with pytest.raises(evidence.EvidenceBundleError):
        host.validate_evidence_bundle_with_usb_builder(
            request=request2, worker_run=run2, output_volume=output2, evidence_volume=volume2,
            operation_root=operation_root2, runner=object(),
        )
    volume2.write_bytes(raw)
    volume2.chmod(0o600)
    output2.write_bytes(output_raw)
    output2.chmod(0o600)
    first = host.validate_evidence_bundle_with_usb_builder(
        request=request2, worker_run=run2, output_volume=output2, evidence_volume=volume2,
        operation_root=operation_root2, runner=object(),
    )
    assert first.published is False
    with pytest.raises(host.OfflineVmBuildError, match="fresh|replayed"):
        host.validate_evidence_bundle_with_usb_builder(
            request=request2, worker_run=run2, output_volume=output2, evidence_volume=volume2,
            operation_root=operation_root2, runner=object(),
        )


def test_output_commit_rejects_same_request_same_iso_evidence_volume_substitution(tmp_path: Path) -> None:
    operation_root, output, evidence_volume, request, run = _ready_case(tmp_path / "substitution")
    original_header = worker.validate_result_volume(output, request)
    original_bundle = evidence.verify_evidence_volume(
        evidence_volume,
        evidence.EvidenceBinding(
            operation_id=str(request["operation_id"]),
            request_sha256=host._sha256_bytes(worker.canonical_json(request)),
            source_sha256=request["source"]["sha256"],  # type: ignore[index]
            package_sha256=request["packages"]["sha256"],  # type: ignore[index]
            profile_sha256=request["profile"]["sha256"],  # type: ignore[index]
            snapshot_lock_sha256=request["snapshot"]["lock_sha256"],  # type: ignore[index]
            snapshot_date=request["snapshot"]["date"],  # type: ignore[index]
            candidate_iso_sha256=str(original_header["iso_sha256"]),
            candidate_iso_size=int(original_header["iso_size"]),
            command_argv_sha256=str(original_header["command_argv_sha256"]),
            evidence_capacity=int(request["layout"]["evidence_capacity"]),  # type: ignore[index]
        ),
    )
    substituted = _write_bundle(
        evidence_volume,
        original_bundle.binding,
        request,
        package_image_sha=str(request["packages"]["sha256"]),  # type: ignore[index]
        command_sha=str(original_header["command_argv_sha256"]),
        iso_sha=str(original_header["iso_sha256"]),
        iso_size=int(original_header["iso_size"]),
        package_list=["base 1.1", "systemd 1.0"],
    )
    substituted_size, substituted_sha = _logical_evidence_claim(substituted)
    assert substituted_size == int(original_header["evidence_size"])
    assert substituted_sha != original_header["evidence_sha256"]
    with pytest.raises(host.OfflineVmBuildError, match="evidence volume size or digest"):
        host.validate_evidence_bundle_with_usb_builder(
            request=request,
            worker_run=run,
            output_volume=output,
            evidence_volume=evidence_volume,
            operation_root=operation_root,
            runner=object(),
        )
    assert not (operation_root / ".host-candidate-consumed").exists()
    assert not (operation_root / evidence.EXTRACTION_DIRECTORY).exists()


@pytest.mark.parametrize("mutation", ["volume", "extracted-tree"])
def test_pending_adapter_rejects_linked_and_concurrently_modified_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    operation_root, output, volume, request, run = _ready_case(tmp_path / "linked")
    actual = operation_root / "evidence.raw"
    alias = operation_root / "evidence-real.raw"
    actual.rename(alias)
    actual.symlink_to(alias)
    with pytest.raises(evidence.EvidenceBundleError):
        host.validate_evidence_bundle_with_usb_builder(
            request=request, worker_run=run, output_volume=output, evidence_volume=actual,
            operation_root=operation_root, runner=object(),
        )

    operation_root2, output2, volume2, request2, run2 = _ready_case(tmp_path / "mutation")
    original = build_usb._validate_target_root_install

    def mutate_during_validation(target_root: Path, *, wheel: Path, runner: Any) -> dict[str, str]:
        if mutation == "volume":
            with volume2.open("r+b") as stream:
                stream.seek(evidence.PAYLOAD_OFFSET)
                byte = stream.read(1)
                stream.seek(evidence.PAYLOAD_OFFSET)
                stream.write(bytes([byte[0] ^ 1]))
                stream.flush()
                os.fsync(stream.fileno())
        else:
            target = target_root / "bin" / "autoloader"
            target.write_bytes(b"changed after extraction")
        return original(target_root, wheel=wheel, runner=runner)

    monkeypatch.setattr(build_usb, "_validate_target_root_install", mutate_during_validation)
    with pytest.raises(evidence.EvidenceBundleError, match="digest|changed"):
        host.validate_evidence_bundle_with_usb_builder(
            request=request2, worker_run=run2, output_volume=output2, evidence_volume=volume2,
            operation_root=operation_root2, runner=object(),
        )
