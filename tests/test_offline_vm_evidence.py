from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path
from typing import Any, Callable

import pytest

from usb_builder import offline_vm_evidence as evidence


pytestmark = pytest.mark.skipif(os.name != "posix", reason="evidence extraction contract requires POSIX no-follow and directory-fd operations")


def _binding(operation_id: str = "a" * 32) -> evidence.EvidenceBinding:
    return evidence.EvidenceBinding(
        operation_id=operation_id,
        request_sha256="1" * 64,
        source_sha256="2" * 64,
        package_sha256="3" * 64,
        profile_sha256="4" * 64,
        snapshot_lock_sha256="5" * 64,
        snapshot_date="2026-09-30",
        candidate_iso_sha256="7" * 64,
        candidate_iso_size=40_960,
        command_argv_sha256="6" * 64,
        evidence_capacity=2 * 1024 * 1024,
    )


def _standard_files(bound: evidence.EvidenceBinding | None = None) -> dict[str, tuple[str, int, bytes]]:
    binding = bound or _binding()
    command_status = evidence.canonical_json(
        {
            "format": evidence.FORMAT,
            "status": "EXITED_ZERO",
            "exit_code": 0,
            "argv_sha256": binding.command_argv_sha256,
            "operation_id": binding.operation_id,
            "request_sha256": binding.request_sha256,
            "candidate_iso_sha256": binding.candidate_iso_sha256,
            "candidate_iso_size": binding.candidate_iso_size,
        }
    )
    return {
        "build-provenance.json": ("provenance", 0o600, b'{"build":"captured"}'),
        "command-status.json": ("command-status", 0o600, command_status),
        "package-inventory.json": ("package-inventory", 0o600, b'{"packages":[]}'),
        "target-root/bin/autoloader": ("target-root-file", 0o755, b"ELF-sample"),
        "wheel.whl": ("wheel", 0o600, b"PK\x03\x04wheel"),
    }


def _volume_bytes(
    binding: evidence.EvidenceBinding | None = None,
    *,
    files: dict[str, tuple[str, int, bytes]] | None = None,
    directories: dict[str, tuple[str, int]] | None = None,
    symlinks: dict[str, bytes] | None = None,
    mutate_manifest: Callable[[dict[str, Any]], None] | None = None,
    terminal_status: str = "COMPLETE",
    padding: int = 37,
) -> bytes:
    bound = binding or _binding()
    file_map = files or _standard_files(bound)
    symlink_map = symlinks or {}
    directory_map = directories or {
        "target-root": ("target-root", 0o755),
        "target-root/bin": ("target-root-directory", 0o755),
    }
    raw_entries: list[dict[str, Any]] = []
    payload = bytearray()
    for path in sorted([*directory_map, *file_map, *symlink_map]):
        if path in directory_map:
            role, mode = directory_map[path]
            raw_entries.append(
                {"path": path, "kind": "directory", "role": role, "mode": mode, "offset": 0, "size": 0, "sha256": None}
            )
        elif path in file_map:
            role, mode, contents = file_map[path]
            raw_entries.append(
                {
                    "path": path,
                    "kind": "file",
                    "role": role,
                    "mode": mode,
                    "offset": evidence.PAYLOAD_OFFSET + len(payload),
                    "size": len(contents),
                    "sha256": hashlib.sha256(contents).hexdigest(),
                }
            )
            payload.extend(contents)
        else:
            contents = symlink_map[path]
            raw_entries.append(
                {
                    "path": path,
                    "kind": "symlink",
                    "role": "target-root-symlink",
                    "mode": 0o777,
                    "offset": evidence.PAYLOAD_OFFSET + len(payload),
                    "size": len(contents),
                    "sha256": hashlib.sha256(contents).hexdigest(),
                }
            )
            payload.extend(contents)
    volume_size = evidence.PAYLOAD_OFFSET + len(payload) + padding + evidence.TRAILER_SIZE
    volume_capacity = bound.evidence_capacity
    assert volume_capacity >= volume_size
    manifest: dict[str, Any] = {
        "format": evidence.FORMAT,
        "binding": bound.as_dict(),
        "entries": raw_entries,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "payload_size": len(payload),
        "volume_size": volume_size,
        "volume_capacity": volume_capacity,
    }
    if mutate_manifest is not None:
        mutate_manifest(manifest)
    manifest_raw = evidence.canonical_json(manifest)
    assert len(manifest_raw) <= evidence.MANIFEST_SLOT_SIZE
    manifest_digest = hashlib.sha256(manifest_raw).digest()
    header = evidence._HEADER.pack(evidence.HEADER_MAGIC, len(manifest_raw), manifest_digest)
    image = bytearray(evidence.HEADER_SIZE)
    image[: len(header)] = header
    image.extend(manifest_raw)
    image.extend(b"\0" * (evidence.PAYLOAD_OFFSET - len(image)))
    image.extend(payload)
    image.extend(b"\0" * (volume_size - evidence.TRAILER_SIZE - len(image)))
    terminal = {
        "format": evidence.FORMAT,
        "status": terminal_status,
        "binding": bound.as_dict(),
        "manifest_sha256": manifest_digest.hex(),
        "payload_sha256": manifest["payload_sha256"],
        "payload_size": manifest["payload_size"],
        "entry_count": len(manifest["entries"]),
        "volume_size": volume_size,
        "volume_capacity": volume_capacity,
    }
    terminal_raw = evidence.canonical_json(terminal)
    trailer = evidence._TRAILER.pack(evidence.TRAILER_MAGIC, len(terminal_raw), hashlib.sha256(terminal_raw).digest())
    trailer_block = bytearray(evidence.TRAILER_SIZE)
    trailer_block[: len(trailer)] = trailer
    trailer_block[len(trailer) : len(trailer) + len(terminal_raw)] = terminal_raw
    image.extend(trailer_block)
    image.extend(b"\0" * (volume_capacity - len(image)))
    assert len(image) == volume_capacity
    return bytes(image)


def _operation(tmp_path: Path, *, operation_id: str = "a" * 32) -> tuple[Path, Path]:
    operation_root = tmp_path / operation_id
    operation_root.mkdir(mode=0o700)
    operation_root.chmod(0o700)
    volume = operation_root / "evidence.raw"
    volume.write_bytes(_volume_bytes(_binding(operation_id)))
    volume.chmod(0o600)
    return operation_root, volume


def test_verifies_and_extracts_complete_operation_bound_bundle_once(tmp_path: Path) -> None:
    operation_root, volume = _operation(tmp_path)
    verified = evidence.verify_evidence_volume(volume, _binding())
    assert verified.binding.operation_id == "a" * 32
    assert verified.payload_size > 0
    assert {item.role for item in verified.entries} >= {
        "target-root", "target-root-file", "wheel", "package-inventory", "command-status", "provenance"
    }

    evidence.extract_evidence_volume(volume, operation_root, _binding())
    extracted = operation_root / "evidence-extracted"
    assert (extracted / evidence.EXTRACTION_MARKER).is_file()
    assert (extracted / "target-root/bin/autoloader").read_bytes() == b"ELF-sample"
    assert (extracted / "wheel.whl").read_bytes() == b"PK\x03\x04wheel"
    with pytest.raises(evidence.EvidenceBundleError, match="fresh"):
        evidence.extract_evidence_volume(volume, operation_root, _binding())


def test_extracts_validated_target_root_symlinks_last(tmp_path: Path) -> None:
    operation_id = "a" * 32
    operation_root = tmp_path / operation_id
    operation_root.mkdir(mode=0o700)
    operation_root.chmod(0o700)
    volume = operation_root / "evidence.raw"
    directories = {
        "target-root": ("target-root", 0o755),
        "target-root/bin": ("target-root-directory", 0o755),
        "target-root/etc": ("target-root-directory", 0o755),
        "target-root/etc/systemd": ("target-root-directory", 0o755),
        "target-root/etc/systemd/system": ("target-root-directory", 0o755),
        "target-root/usr": ("target-root-directory", 0o755),
        "target-root/usr/bin": ("target-root-directory", 0o755),
    }
    symlinks = {
        "target-root/etc/systemd/system/NetworkManager.service": b"/dev/null",
        "target-root/usr/bin/autoloader-absolute": b"/bin/autoloader",
        "target-root/usr/bin/autoloader-relative": b"../../bin/autoloader",
    }
    volume.write_bytes(_volume_bytes(directories=directories, symlinks=symlinks))
    volume.chmod(0o600)

    verified = evidence.verify_evidence_volume(volume, _binding(operation_id))
    links = {item.path: item.link_target for item in verified.entries if item.kind == "symlink"}
    assert links == {
        "target-root/etc/systemd/system/NetworkManager.service": "/dev/null",
        "target-root/usr/bin/autoloader-absolute": "/bin/autoloader",
        "target-root/usr/bin/autoloader-relative": "../../bin/autoloader",
    }
    expected_payload_size = sum(len(item[2]) for item in _standard_files(_binding(operation_id)).values()) + sum(map(len, symlinks.values()))
    assert verified.payload_size == expected_payload_size

    evidence.extract_evidence_volume(volume, operation_root, _binding(operation_id))
    extracted_root = operation_root / evidence.EXTRACTION_DIRECTORY / "target-root"
    assert (extracted_root / "usr/bin/autoloader-relative").is_symlink()
    assert os.readlink(extracted_root / "usr/bin/autoloader-relative") == "../../bin/autoloader"
    assert os.readlink(extracted_root / "usr/bin/autoloader-absolute") == "/bin/autoloader"
    assert os.readlink(extracted_root / "etc/systemd/system/NetworkManager.service") == "/dev/null"
    assert (extracted_root / "usr/bin/autoloader-relative").resolve() == (extracted_root / "bin/autoloader").resolve()


@pytest.mark.parametrize(
    "target",
    [b"../../../outside", b"/../../outside", b"bad\x00target", b"bad\\target", b"\xff"],
    ids=["relative-escape", "absolute-escape", "nul", "backslash", "invalid-utf8"],
)
def test_rejects_symlink_targets_outside_target_root_or_invalid_encoding(tmp_path: Path, target: bytes) -> None:
    operation_id = "a" * 32
    operation_root = tmp_path / operation_id
    operation_root.mkdir(mode=0o700)
    operation_root.chmod(0o700)
    volume = operation_root / "evidence.raw"
    directories = {
        "target-root": ("target-root", 0o755),
        "target-root/bin": ("target-root-directory", 0o755),
        "target-root/usr": ("target-root-directory", 0o755),
        "target-root/usr/bin": ("target-root-directory", 0o755),
    }
    volume.write_bytes(
        _volume_bytes(directories=directories, symlinks={"target-root/usr/bin/link": target})
    )
    volume.chmod(0o600)
    with pytest.raises(evidence.EvidenceBundleError, match="symlink target"):
        evidence.verify_evidence_volume(volume, _binding(operation_id))


def test_rejects_symlink_path_collision_and_symlink_as_parent(tmp_path: Path) -> None:
    operation_id = "a" * 32
    operation_root = tmp_path / operation_id
    operation_root.mkdir(mode=0o700)
    operation_root.chmod(0o700)
    volume = operation_root / "evidence.raw"
    collision_files = _standard_files(_binding(operation_id))
    collision_files["target-root/bin/autoloader"] = ("target-root-file", 0o755, b"ELF-sample")
    volume.write_bytes(
        _volume_bytes(
            files=collision_files,
            symlinks={"target-root/bin/autoloader": b"../bin/autoloader"},
        )
    )
    volume.chmod(0o600)
    with pytest.raises(evidence.EvidenceBundleError, match="duplicate evidence path"):
        evidence.verify_evidence_volume(volume, _binding(operation_id))

    parent_symlink = operation_root / "parent-symlink.raw"
    directories = {
        "target-root": ("target-root", 0o755),
        "target-root/bin": ("target-root-directory", 0o755),
        "target-root/usr": ("target-root-directory", 0o755),
    }
    files = _standard_files(_binding(operation_id))
    files["target-root/usr/link/child"] = ("target-root-file", 0o644, b"child")
    parent_symlink.write_bytes(
        _volume_bytes(
            files=files,
            directories=directories,
            symlinks={"target-root/usr/link": b"../bin"},
        )
    )
    parent_symlink.chmod(0o600)
    with pytest.raises(evidence.EvidenceBundleError, match="missing parent directory"):
        evidence.verify_evidence_volume(parent_symlink, _binding(operation_id))


def test_symlink_target_bytes_are_individually_hash_bound(tmp_path: Path) -> None:
    operation_id = "a" * 32
    operation_root = tmp_path / operation_id
    operation_root.mkdir(mode=0o700)
    operation_root.chmod(0o700)
    volume = operation_root / "evidence.raw"
    directories = {
        "target-root": ("target-root", 0o755),
        "target-root/bin": ("target-root-directory", 0o755),
        "target-root/usr": ("target-root-directory", 0o755),
    }
    volume.write_bytes(
        _volume_bytes(directories=directories, symlinks={"target-root/usr/link": b"../bin"})
    )
    volume.chmod(0o600)
    entry = next(
        item for item in evidence.verify_evidence_volume(volume, _binding(operation_id)).entries
        if item.kind == "symlink"
    )
    with volume.open("r+b") as stream:
        stream.seek(entry.offset)
        first = stream.read(1)
        stream.seek(entry.offset)
        stream.write(bytes([first[0] ^ 1]))
    with pytest.raises(evidence.EvidenceBundleError, match="digest"):
        evidence.verify_evidence_volume(volume, _binding(operation_id))


def test_rejects_symlink_entries_outside_target_root(tmp_path: Path) -> None:
    operation_id = "a" * 32
    operation_root = tmp_path / operation_id
    operation_root.mkdir(mode=0o700)
    operation_root.chmod(0o700)
    volume = operation_root / "evidence.raw"
    directories = {
        "target-root": ("target-root", 0o755),
        "target-root/bin": ("target-root-directory", 0o755),
        "target-root/usr": ("target-root-directory", 0o755),
    }

    def move_link_outside_target_root(manifest: dict[str, Any]) -> None:
        next(item for item in manifest["entries"] if item["kind"] == "symlink")["path"] = "z/link"

    volume.write_bytes(
        _volume_bytes(
            directories=directories,
            symlinks={"target-root/usr/link": b"../bin"},
            mutate_manifest=move_link_outside_target_root,
        )
    )
    volume.chmod(0o600)
    with pytest.raises(evidence.EvidenceBundleError, match="role/path/type"):
        evidence.verify_evidence_volume(volume, _binding(operation_id))


@pytest.mark.parametrize(
    "link_path",
    ["target-root/usr/bin/tool", "target-root/etc/systemd/system/unreviewed.service"],
    ids=["binary-path", "unreviewed-service"],
)
def test_dev_null_is_only_accepted_for_reviewed_network_masks(tmp_path: Path, link_path: str) -> None:
    operation_id = "a" * 32
    operation_root = tmp_path / operation_id
    operation_root.mkdir(mode=0o700)
    operation_root.chmod(0o700)
    volume = operation_root / "evidence.raw"
    directories = {
        "target-root": ("target-root", 0o755),
        "target-root/bin": ("target-root-directory", 0o755),
        "target-root/etc": ("target-root-directory", 0o755),
        "target-root/etc/systemd": ("target-root-directory", 0o755),
        "target-root/etc/systemd/system": ("target-root-directory", 0o755),
        "target-root/usr": ("target-root-directory", 0o755),
        "target-root/usr/bin": ("target-root-directory", 0o755),
    }
    volume.write_bytes(_volume_bytes(directories=directories, symlinks={link_path: b"/dev/null"}))
    volume.chmod(0o600)
    with pytest.raises(evidence.EvidenceBundleError, match="reviewed network systemd masks"):
        evidence.verify_evidence_volume(volume, _binding(operation_id))


@pytest.mark.parametrize(
    "wrong_field",
    [
        "request_sha256", "source_sha256", "package_sha256", "profile_sha256", "snapshot_lock_sha256",
        "candidate_iso_sha256", "candidate_iso_size", "command_argv_sha256",
    ],
)
def test_rejects_cross_request_or_input_binding(tmp_path: Path, wrong_field: str) -> None:
    _, volume = _operation(tmp_path)
    values = _binding().as_dict()
    values[wrong_field] = "f" * 64
    expected = evidence.EvidenceBinding(**values)
    with pytest.raises(evidence.EvidenceBundleError, match="binding"):
        evidence.verify_evidence_volume(volume, expected)


def test_rejects_cross_operation_replay(tmp_path: Path) -> None:
    _, volume = _operation(tmp_path)
    with pytest.raises(evidence.EvidenceBundleError, match="binding"):
        evidence.verify_evidence_volume(volume, _binding("b" * 32))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda manifest: next(item for item in manifest["entries"] if item["path"] == "target-root/bin/autoloader").update(path="target-root/../outside"),
        lambda manifest: next(item for item in manifest["entries"] if item["path"] == "wheel.whl").update(path="package-inventory.json"),
        lambda manifest: next(item for item in manifest["entries"] if item["path"] == "wheel.whl").update(kind="symlink"),
        lambda manifest: next(item for item in manifest["entries"] if item["path"] == "wheel.whl").update(kind="fifo"),
        lambda manifest: manifest["entries"].remove(next(item for item in manifest["entries"] if item["path"] == "target-root/bin")),
    ],
    ids=["traversal", "duplicate", "symlink", "special", "missing-parent"],
)
def test_rejects_unsafe_entry_paths_and_types(tmp_path: Path, mutation: Callable[[dict[str, Any]], None]) -> None:
    operation_root, _ = _operation(tmp_path)
    volume = operation_root / "evidence.raw"
    volume.write_bytes(_volume_bytes(mutate_manifest=mutation))
    volume.chmod(0o600)
    with pytest.raises(evidence.EvidenceBundleError):
        evidence.verify_evidence_volume(volume, _binding())


def test_rejects_missing_required_artifact_role(tmp_path: Path) -> None:
    operation_root, _ = _operation(tmp_path)
    volume = operation_root / "evidence.raw"

    def remove_wheel(manifest: dict[str, Any]) -> None:
        manifest["entries"] = [item for item in manifest["entries"] if item["role"] != "wheel"]

    volume.write_bytes(_volume_bytes(mutate_manifest=remove_wheel))
    volume.chmod(0o600)
    with pytest.raises(evidence.EvidenceBundleError, match="required evidence roles"):
        evidence.verify_evidence_volume(volume, _binding())


def test_rejects_incomplete_terminal_and_nonzero_exit_status(tmp_path: Path) -> None:
    operation_root, _ = _operation(tmp_path)
    volume = operation_root / "evidence.raw"
    volume.write_bytes(_volume_bytes(terminal_status="INCOMPLETE"))
    volume.chmod(0o600)
    with pytest.raises(evidence.EvidenceBundleError, match="terminal evidence"):
        evidence.verify_evidence_volume(volume, _binding())

    files = _standard_files()
    files["command-status.json"] = (
        "command-status", 0o600,
        evidence.canonical_json({"format": evidence.FORMAT, "status": "EXITED_NONZERO", "exit_code": 7, "argv_sha256": "6" * 64}),
    )
    volume.write_bytes(_volume_bytes(files=files))
    volume.chmod(0o600)
    with pytest.raises(evidence.EvidenceBundleError, match="command status"):
        evidence.verify_evidence_volume(volume, _binding())


def test_rejects_truncation_manifest_length_and_payload_tampering(tmp_path: Path) -> None:
    operation_root, volume = _operation(tmp_path)
    original = volume.read_bytes()
    volume.write_bytes(original[:-1])
    with pytest.raises(evidence.EvidenceBundleError):
        evidence.verify_evidence_volume(volume, _binding())
    volume.write_bytes(original)
    volume.chmod(0o600)
    with volume.open("r+b") as stream:
        stream.seek(evidence.PAYLOAD_OFFSET)
        byte = stream.read(1)
        stream.seek(evidence.PAYLOAD_OFFSET)
        stream.write(bytes([byte[0] ^ 1]))
    with pytest.raises(evidence.EvidenceBundleError, match="digest"):
        evidence.verify_evidence_volume(volume, _binding())


def test_rejects_volume_symlink_and_hardlink(tmp_path: Path) -> None:
    operation_root, volume = _operation(tmp_path)
    alias = operation_root / "alias.raw"
    os.link(volume, alias)
    with pytest.raises(evidence.EvidenceBundleError, match="single-link"):
        evidence.verify_evidence_volume(alias, _binding())
    alias.unlink()
    volume.unlink()
    outside = tmp_path / "outside.raw"
    outside.write_bytes(_volume_bytes())
    outside.chmod(0o600)
    volume.symlink_to(outside)
    with pytest.raises(evidence.EvidenceBundleError):
        evidence.extract_evidence_volume(volume, operation_root, _binding())


def test_rejects_nonempty_or_symlinked_extraction_destination(tmp_path: Path) -> None:
    operation_root, volume = _operation(tmp_path)
    existing = operation_root / "evidence-extracted"
    existing.mkdir(mode=0o700)
    with pytest.raises(evidence.EvidenceBundleError, match="fresh"):
        evidence.extract_evidence_volume(volume, operation_root, _binding())


def test_extraction_is_bound_to_exact_operation_directory(tmp_path: Path) -> None:
    operation_root, volume = _operation(tmp_path)
    wrong_root = tmp_path / ("b" * 32)
    wrong_root.mkdir(mode=0o700)
    wrong_root.chmod(0o700)
    copied_volume = wrong_root / "evidence.raw"
    copied_volume.write_bytes(volume.read_bytes())
    copied_volume.chmod(0o600)
    with pytest.raises(evidence.EvidenceBundleError, match="operation directory"):
        evidence.extract_evidence_volume(copied_volume, wrong_root, _binding())
    assert not (wrong_root / evidence.EXTRACTION_DIRECTORY).exists()

    alias_parent = tmp_path / "alias-parent"
    alias_parent.mkdir(mode=0o700)
    alias_parent.chmod(0o700)
    symlink_root = alias_parent / ("a" * 32)
    symlink_root.symlink_to(operation_root, target_is_directory=True)
    with pytest.raises(evidence.EvidenceBundleError):
        evidence.extract_evidence_volume(symlink_root / "evidence.raw", symlink_root, _binding())
    assert not (operation_root / evidence.EXTRACTION_DIRECTORY).exists()


def test_final_directory_sync_failure_removes_completion_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation_root, volume = _operation(tmp_path)
    marker = operation_root / evidence.EXTRACTION_DIRECTORY / evidence.EXTRACTION_MARKER
    root_inode = operation_root.stat().st_ino
    real_fsync = os.fsync
    failed_once = False

    def fail_root_sync_after_marker(fd: int) -> None:
        nonlocal failed_once
        if not failed_once and marker.exists() and os.fstat(fd).st_ino == root_inode:
            failed_once = True
            raise OSError("injected final parent-directory fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(evidence.os, "fsync", fail_root_sync_after_marker)
    with pytest.raises(OSError, match="injected final"):
        evidence.extract_evidence_volume(volume, operation_root, _binding())
    assert failed_once
    assert not marker.exists()


def test_marker_fsync_failure_removes_completion_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation_root, volume = _operation(tmp_path)
    marker = operation_root / evidence.EXTRACTION_DIRECTORY / evidence.EXTRACTION_MARKER
    real_fsync = os.fsync
    failed_once = False

    def fail_marker_sync(fd: int) -> None:
        nonlocal failed_once
        if marker.exists() and os.fstat(fd).st_ino == marker.stat().st_ino and not failed_once:
            failed_once = True
            raise OSError("injected completion-marker fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(evidence.os, "fsync", fail_marker_sync)
    with pytest.raises(OSError, match="completion-marker fsync"):
        evidence.extract_evidence_volume(volume, operation_root, _binding())
    assert failed_once
    assert not marker.exists()


def test_marker_cleanup_failure_is_reported_and_never_returns_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation_root, volume = _operation(tmp_path)
    marker = operation_root / evidence.EXTRACTION_DIRECTORY / evidence.EXTRACTION_MARKER
    root_inode = operation_root.stat().st_ino
    real_fsync = os.fsync
    real_unlink = os.unlink
    failed_sync = False

    def fail_root_sync_after_marker(fd: int) -> None:
        nonlocal failed_sync
        if not failed_sync and marker.exists() and os.fstat(fd).st_ino == root_inode:
            failed_sync = True
            raise OSError("injected final parent-directory fsync failure")
        real_fsync(fd)

    def fail_marker_unlink(path: str | bytes, *, dir_fd: int | None = None) -> None:
        if path == evidence.EXTRACTION_MARKER:
            raise OSError("injected marker unlink failure")
        if dir_fd is None:
            real_unlink(path)
        else:
            real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(evidence.os, "fsync", fail_root_sync_after_marker)
    monkeypatch.setattr(evidence.os, "unlink", fail_marker_unlink)
    with pytest.raises(evidence.EvidenceBundleError, match="cleanup could not be confirmed"):
        evidence.extract_evidence_volume(volume, operation_root, _binding())
    assert failed_sync
    assert marker.exists()


def test_concurrent_volume_mutation_during_verification_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, volume = _operation(tmp_path)
    bundle = evidence.verify_evidence_volume(volume, _binding())
    first_file = next(entry for entry in bundle.entries if entry.kind == "file")
    mutate = threading.Event()
    mutated = threading.Event()
    failures: list[BaseException] = []
    real_read_exact = evidence._read_exact

    def mutate_volume() -> None:
        try:
            if not mutate.wait(timeout=5):
                raise AssertionError("verification did not reach a payload read")
            with volume.open("r+b") as stream:
                stream.seek(first_file.offset)
                original = stream.read(1)
                stream.seek(first_file.offset)
                stream.write(bytes([original[0] ^ 1]))
                stream.flush()
            mutated.set()
        except BaseException as exc:
            failures.append(exc)

    writer = threading.Thread(target=mutate_volume, daemon=True)
    writer.start()

    def signal_writer_after_payload_read(fd: int, count: int, offset: int) -> bytes:
        data = real_read_exact(fd, count, offset)
        if offset == first_file.offset and not mutate.is_set():
            mutate.set()
            if not mutated.wait(timeout=5):
                raise AssertionError("concurrent volume mutation did not finish")
        return data

    monkeypatch.setattr(evidence, "_read_exact", signal_writer_after_payload_read)
    with pytest.raises(evidence.EvidenceBundleError, match="digest|changed during verification"):
        evidence.verify_evidence_volume(volume, _binding())
    writer.join(timeout=5)
    assert not writer.is_alive()
    assert not failures
    assert mutated.is_set()


def test_concurrent_volume_mutation_during_extraction_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation_root, volume = _operation(tmp_path)
    bundle = evidence.verify_evidence_volume(volume, _binding())
    first_file = next(entry for entry in bundle.entries if entry.kind == "file")
    wheel = next(entry for entry in bundle.entries if entry.path == "wheel.whl")
    real_verify = evidence.verify_evidence_volume
    real_read_exact = evidence._read_exact
    extraction_started = False
    mutated = False

    def mark_extraction_started(*args: Any, **kwargs: Any) -> evidence.VerifiedEvidenceBundle:
        nonlocal extraction_started
        result = real_verify(*args, **kwargs)
        extraction_started = True
        return result

    def mutate_after_extract_read(fd: int, count: int, offset: int) -> bytes:
        nonlocal mutated
        if extraction_started and not mutated and offset == first_file.offset:
            with volume.open("r+b") as stream:
                stream.seek(wheel.offset)
                original = stream.read(1)
                stream.seek(wheel.offset)
                stream.write(bytes([original[0] ^ 1]))
                stream.flush()
            mutated = True
        return real_read_exact(fd, count, offset)

    monkeypatch.setattr(evidence, "verify_evidence_volume", mark_extraction_started)
    monkeypatch.setattr(evidence, "_read_exact", mutate_after_extract_read)
    with pytest.raises(evidence.EvidenceBundleError, match="changed during extraction"):
        evidence.extract_evidence_volume(volume, operation_root, _binding())
    assert mutated
    assert not (operation_root / evidence.EXTRACTION_DIRECTORY / evidence.EXTRACTION_MARKER).exists()


def test_interrupted_payload_write_never_publishes_completion_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation_root, volume = _operation(tmp_path)
    marker = operation_root / evidence.EXTRACTION_DIRECTORY / evidence.EXTRACTION_MARKER

    def interrupt_write(fd: int, data: bytes | bytearray | memoryview) -> int:
        raise KeyboardInterrupt("injected extraction interruption")

    monkeypatch.setattr(evidence.os, "write", interrupt_write)
    with pytest.raises(KeyboardInterrupt, match="injected extraction interruption"):
        evidence.extract_evidence_volume(volume, operation_root, _binding())
    assert not marker.exists()


def test_streams_large_file_content_in_bounded_chunks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = _standard_files()
    files["target-root/bin/autoloader"] = ("target-root-file", 0o755, b"x" * (256 * 1024))
    operation_root, _ = _operation(tmp_path)
    volume = operation_root / "evidence.raw"
    volume.write_bytes(_volume_bytes(files=files))
    volume.chmod(0o600)
    monkeypatch.setattr(evidence, "HASH_CHUNK_BYTES", 4096)
    assert evidence.verify_evidence_volume(volume, _binding()).payload_size > 256 * 1024
    evidence.extract_evidence_volume(volume, operation_root, _binding())
    assert (operation_root / "evidence-extracted/target-root/bin/autoloader").stat().st_size == 256 * 1024


def test_rejects_volume_over_configured_cap(tmp_path: Path) -> None:
    _, volume = _operation(tmp_path)
    with pytest.raises(evidence.EvidenceBundleError, match="size"):
        evidence.verify_evidence_volume(volume, _binding(), max_volume_bytes=1)


@pytest.mark.parametrize("limit", ["entries", "payload"])
def test_rejects_entry_count_and_payload_caps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: str) -> None:
    _, volume = _operation(tmp_path)
    if limit == "entries":
        monkeypatch.setattr(evidence, "MAX_ENTRIES", 3)
    else:
        monkeypatch.setattr(evidence, "MAX_EVIDENCE_PAYLOAD_BYTES", 8)
    with pytest.raises(evidence.EvidenceBundleError, match="entry count|payload"):
        evidence.verify_evidence_volume(volume, _binding())


def test_rejects_manifest_digest_tampering(tmp_path: Path) -> None:
    _, volume = _operation(tmp_path)
    raw = bytearray(volume.read_bytes())
    raw[16] ^= 1
    volume.write_bytes(raw)
    volume.chmod(0o600)
    with pytest.raises(evidence.EvidenceBundleError, match="manifest digest"):
        evidence.verify_evidence_volume(volume, _binding())
