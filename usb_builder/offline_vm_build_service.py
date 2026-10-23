"""Fixed command entrypoint for the sealed offline build systemd unit.

The module accepts no command-line options.  It reads one canonical invocation
written after the NNP=1 guest preflight, checks its one-shot binding and current
service isolation, then executes the single fixed mkarchiso command.  It is
packaged for a future worker image only; the worker deployment pin remains
unset and the default guest entrypoint does not call it.
"""

from __future__ import annotations

import hashlib
import ctypes
import json
import os
import re
import stat
import struct
import sys
from pathlib import Path
from typing import Any, Callable

from usb_builder import offline_vm_build as build
from usb_builder import offline_vm_guest_worker as guest
from usb_builder import offline_vm_worker
from usb_builder.offline_vm_worker import (
    MAX_ISO_BYTES,
    RESULT_ISO_OFFSET,
    RESULT_HEADER_SIZE,
    RESULT_RECEIPT_SIZE,
    RESULT_COMMIT_MAGIC,
    RESULT_COMMIT_SIZE,
    RESULT_TERMINAL_MAGIC,
    RESULT_TERMINAL_SIZE,
    RESULT_TERMINAL_OFFSET,
    RESULT_RECEIPT_OFFSET,
    RESULT_COMMIT_OFFSET,
    build_result_commit_marker,
    build_result_header,
    build_service_success_receipt,
    canonical_json,
    parse_request,
)


OUTPUT_DEVICE = Path("/dev/disk/by-id/virtio-LC-OUTPUT-RW")
BLKGETSIZE64 = 0x80081272
MAX_RESULT_COPY_CHUNK = 1024 * 1024


def _read_status() -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
        key, separator, value = line.partition(":")
        if separator:
            fields[key] = value.strip()
    return fields


def _route_lines(path: Path) -> tuple[str, ...]:
    lines = path.read_text(encoding="ascii").splitlines()
    if path.name == "route" and lines:
        lines = lines[1:]
    return tuple(line for line in lines if line.strip())


def collect_build_service_security() -> build.GuestSecurityState:
    """Observe, rather than infer, the dedicated systemd service boundary."""
    if not sys.platform.startswith("linux"):
        raise build.OfflineVmBuildError("fixed build service is supported only inside Linux worker guest")
    uid = build._linux_effective_uid()
    gid = os.getegid()
    status = _read_status()
    required = ("CapEff", "CapPrm", "CapAmb", "CapBnd", "NoNewPrivs", "Groups")
    if any(key not in status for key in required):
        raise build.OfflineVmBuildError("fixed build service kernel security view is incomplete")
    nnp = status["NoNewPrivs"]
    if nnp not in {"0", "1"}:
        raise build.OfflineVmBuildError("fixed build service NoNewPrivs field is malformed")
    try:
        groups = tuple(int(value) for value in status["Groups"].split())
        import pwd

        account = pwd.getpwuid(uid).pw_name
    except (ValueError, KeyError) as exc:
        raise build.OfflineVmBuildError("fixed build service account identity is unavailable") from exc
    namespace = os.readlink("/proc/self/ns/net")
    interfaces = tuple(sorted(path.name for path in Path("/sys/class/net").iterdir()))
    return build.GuestSecurityState(
        account=account,
        uid=uid,
        gid=gid,
        supplementary_gids=groups,
        cap_effective=status["CapEff"],
        cap_permitted=status["CapPrm"],
        cap_ambient=status["CapAmb"],
        no_new_privs=nnp == "1",
        network_namespace=namespace,
        interfaces=interfaces,
        ipv4_routes=_route_lines(Path("/proc/net/route")),
        ipv6_routes=_route_lines(Path("/proc/net/ipv6_route")),
        cap_bounding=status["CapBnd"],
    )


def _read_private_json(path: Path) -> tuple[dict[str, Any], bytes]:
    info = build._verify_private_file(path, expected_mode=0o600)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino, opened.st_uid, opened.st_nlink) != (
            info.st_dev, info.st_ino, info.st_uid, info.st_nlink,
        ):
            raise build.OfflineVmBuildError("fixed build service input changed while opening")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 2 * 1024 * 1024:
                raise build.OfflineVmBuildError("fixed build service input exceeds its size bound")
            chunks.append(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(fd)
    try:
        value = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise build.OfflineVmBuildError("fixed build service input is malformed") from exc
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise build.OfflineVmBuildError("fixed build service input is not canonical")
    return value, raw


def load_invocation() -> tuple[build.BuildInvocation, dict[str, Any]]:
    request_path = Path(build.BUILD_REQUEST_PATH)
    request_record, request_bytes = _read_private_json(request_path)
    request = parse_request(request_bytes)
    request_sha256 = hashlib.sha256(canonical_json(request)).hexdigest()
    if request_record != request or request_sha256 != hashlib.sha256(request_bytes).hexdigest():
        raise build.OfflineVmBuildError("fixed build request is not bound to canonical request bytes")
    claim_path = Path(build.WORKER_ROOT) / ".offline-build-consumed"
    claim, _ = _read_private_json(claim_path)
    if claim != {"format": build.BUILD_RECEIPT_FORMAT, "request_sha256": request_sha256}:
        raise build.OfflineVmBuildError("fixed build request lacks its exact one-shot claim")
    payload, _ = _read_private_json(Path(build.BUILD_INVOCATION_PATH))
    expected = build._expected_fixed_invocation(
        request_sha256,
        Path(build.WORKER_PACSTRAP_BIN) / "pacstrap",
        str(request["operation_id"]),
    )
    if payload != build._invocation_payload(expected):
        raise build.OfflineVmBuildError("fixed build invocation differs from the canonical command policy")
    return expected, request


def _result_payload(result: build.GuestBuildResult) -> dict[str, object]:
    return {
        "format": build.BUILD_RESULT_FORMAT,
        "status": result.status,
        "operation_id": result.operation_id,
        "request_sha256": result.request_sha256,
        "iso_path": result.iso_path,
        "iso_size": result.iso_size,
        "iso_sha256": result.iso_sha256,
        "command_argv_sha256": result.command_argv_sha256,
        "elapsed_seconds": result.elapsed_seconds,
        "commands_run": list(result.commands_run),
        "cleanup_complete": result.cleanup_complete,
    }


def _publish_private_record(path: Path, payload: dict[str, object], *, label: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    if path.exists() or path.is_symlink() or temporary.exists() or temporary.is_symlink():
        raise build.OfflineVmBuildError(f"{label} is not a fresh operation-owned path")
    build._write_exclusive_private(temporary, canonical_json(payload), 0o600)
    published_inode: tuple[int, int] | None = None
    try:
        temporary_info = temporary.lstat()
        if not hasattr(os, "uname") or os.uname().sysname != "Linux":
            raise build.OfflineVmBuildError("atomic no-replace result publication requires Linux renameat2")
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise build.OfflineVmBuildError("atomic no-replace result publication is unavailable")
        renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        renameat2.restype = ctypes.c_int
        result_fd = -100  # AT_FDCWD
        rename_no_replace = 1  # RENAME_NOREPLACE
        status = renameat2(result_fd, os.fsencode(temporary), result_fd, os.fsencode(path), rename_no_replace)
        if status != 0:
            error = ctypes.get_errno()
            raise OSError(error, "cannot atomically publish fixed build result")
        result_info = path.lstat()
        published_inode = (result_info.st_dev, result_info.st_ino)
        if (
            not stat.S_ISREG(result_info.st_mode)
            or result_info.st_nlink != 1
            or stat.S_IMODE(result_info.st_mode) != 0o600
            or (result_info.st_dev, result_info.st_ino, result_info.st_uid)
            != (temporary_info.st_dev, temporary_info.st_ino, temporary_info.st_uid)
        ):
            raise build.OfflineVmBuildError("fixed build service result publication changed its staged inode")
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        if published_inode is not None:
            try:
                result_info = path.lstat()
                if (result_info.st_dev, result_info.st_ino) == published_inode:
                    path.unlink()
            except OSError:
                pass
        raise


def _write_result(result: build.GuestBuildResult) -> None:
    _publish_private_record(
        Path(build.BUILD_RESULT_PATH), _result_payload(result),
        label="fixed build service result",
    )


def _write_result_ready(result: build.GuestBuildResult) -> None:
    """Publish a result-bound readiness receipt before a candidate commit."""
    result_payload, _ = _read_private_json(Path(build.BUILD_RESULT_PATH))
    expected = _result_payload(result)
    if result_payload != expected:
        raise build.OfflineVmBuildError("fixed build ready receipt does not match the durable service result")
    ready_payload: dict[str, object] = {
        "format": build.BUILD_RESULT_READY_FORMAT,
        "operation_id": result.operation_id,
        "request_sha256": result.request_sha256,
        "result_sha256": hashlib.sha256(canonical_json(result_payload)).hexdigest(),
    }
    _publish_private_record(
        Path(build.BUILD_RESULT_READY_PATH), ready_payload,
        label="fixed build ready receipt",
    )


def candidate_commit_is_present(request: dict[str, Any]) -> bool:
    """Require the fixed commit and post-fsync terminal slots; host validates payload."""
    layout = request.get("layout")
    if not isinstance(layout, dict) or layout.get("output_device") != guest.DEVICE_IDS["output"]:
        raise build.OfflineVmBuildError("request does not bind the fixed output device")
    capacity = layout.get("output_capacity")
    if isinstance(capacity, bool) or not isinstance(capacity, int):
        raise build.OfflineVmBuildError("request output capacity is malformed")
    try:
        link_info = OUTPUT_DEVICE.stat()
    except OSError:
        return False
    if not stat.S_ISBLK(link_info.st_mode):
        raise build.OfflineVmBuildError("fixed result path is not the expected block device")
    fd = os.open(OUTPUT_DEVICE, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        opened = os.fstat(fd)
        if not stat.S_ISBLK(opened.st_mode) or build._linux_stat_rdev(opened) != build._linux_stat_rdev(link_info):
            raise build.OfflineVmBuildError("fixed result block identity changed while opening")
        actual_capacity = struct.unpack("Q", build._linux_ioctl(fd, BLKGETSIZE64, struct.pack("Q", 0)))[0]
        if actual_capacity != capacity:
            raise build.OfflineVmBuildError("fixed result block capacity differs from the request")
        header_prefix = build._linux_pread(fd, RESULT_RECEIPT_OFFSET, 0)
        service_receipt = build._linux_pread(fd, RESULT_RECEIPT_SIZE, RESULT_RECEIPT_OFFSET)
        slot = build._linux_pread(fd, RESULT_COMMIT_SIZE, RESULT_COMMIT_OFFSET)
        terminal_slot = build._linux_pread(fd, RESULT_TERMINAL_SIZE, RESULT_TERMINAL_OFFSET)
    finally:
        os.close(fd)
    if len(slot) != RESULT_COMMIT_SIZE or not slot.startswith(RESULT_COMMIT_MAGIC):
        return False
    length_start = len(RESULT_COMMIT_MAGIC)
    length = int.from_bytes(slot[length_start : length_start + 4], "big")
    if not 0 < length <= RESULT_COMMIT_SIZE - length_start - 4:
        raise build.OfflineVmBuildError("fixed output commit length is invalid")
    payload = slot[length_start + 4 : length_start + 4 + length]
    if any(slot[length_start + 4 + length :]):
        raise build.OfflineVmBuildError("fixed output commit padding is not zero")
    try:
        commit = json.loads(payload.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise build.OfflineVmBuildError("fixed output commit is malformed") from exc
    commit_matches = (
        isinstance(commit, dict)
        and canonical_json(commit) == payload
        and commit.get("format") == "librecore-offline-vm-commit-v2"
        and commit.get("operation_id") == request.get("operation_id")
        and commit.get("request_sha256") == hashlib.sha256(canonical_json(request)).hexdigest()
        and isinstance(commit.get("evidence_size"), int)
        and not isinstance(commit.get("evidence_size"), bool)
        and int(commit.get("evidence_size", 0)) > 0
        and isinstance(commit.get("evidence_sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", str(commit.get("evidence_sha256"))) is not None
    )
    if not commit_matches:
        return False
    if len(header_prefix) != RESULT_RECEIPT_OFFSET or len(service_receipt) != RESULT_RECEIPT_SIZE:
        return False
    if len(terminal_slot) != RESULT_TERMINAL_SIZE or not terminal_slot.startswith(RESULT_TERMINAL_MAGIC):
        return False
    try:
        offline_vm_worker._validate_terminal_marker(
            terminal_slot,
            request=request,
            header_prefix=header_prefix,
            service_receipt=service_receipt,
            commit_marker=slot,
            evidence_size=int(commit["evidence_size"]),
            evidence_sha256=str(commit["evidence_sha256"]),
        )
    except offline_vm_worker.OfflineVmContractError:
        return False
    return True


def write_candidate_result_volume(
    candidate: Path,
    result: build.GuestBuildResult,
    request: dict[str, Any],
    isolation: guest.GuestIsolation,
    *,
    publish_service_result: Callable[[], None],
    publish_service_ready: Callable[[], None],
    finalize_evidence_volume: Callable[[], tuple[int, str]] | None = None,
) -> None:
    """Copy only the already-validated ISO into the dedicated output block."""
    if result.status != "CANDIDATE_ISO_PENDING_HOST_VALIDATION" or result.iso_path != candidate.as_posix():
        raise build.OfflineVmBuildError("candidate result is not bound to its fixed ISO path")
    if finalize_evidence_volume is None:
        raise build.OfflineVmBuildError("fixed guest evidence-volume writer is unavailable; candidate publication is closed")
    if build._path_has_symlink_component(candidate) or candidate.parent != Path(build.WORKER_OUTPUT):
        raise build.OfflineVmBuildError("candidate ISO is linked or outside the fixed output directory")
    info = candidate.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != result.iso_size or not 0 < info.st_size <= MAX_ISO_BYTES:
        raise build.OfflineVmBuildError("candidate ISO file identity differs from the fixed result")
    if build._sha256_path(candidate) != result.iso_sha256:
        raise build.OfflineVmBuildError("candidate ISO changed after fixed-command validation")
    layout = request.get("layout")
    if not isinstance(layout, dict) or layout.get("output_device") != guest.DEVICE_IDS["output"]:
        raise build.OfflineVmBuildError("request does not bind the fixed output device")
    capacity = layout.get("output_capacity")
    if isinstance(capacity, bool) or not isinstance(capacity, int) or RESULT_ISO_OFFSET + info.st_size > capacity:
        raise build.OfflineVmBuildError("candidate ISO does not fit in the exact output capacity")
    link_info = OUTPUT_DEVICE.stat()
    if not stat.S_ISBLK(link_info.st_mode):
        raise build.OfflineVmBuildError("fixed result path is not the expected block device")
    number_map = {role: (major, minor) for role, major, minor in isolation.device_numbers}
    major_fn = getattr(os, "major", None)
    minor_fn = getattr(os, "minor", None)
    if not callable(major_fn) or not callable(minor_fn):
        raise build.OfflineVmBuildError("fixed result block identity helpers are unavailable")
    output_rdev = build._linux_stat_rdev(link_info)
    actual_number = (major_fn(output_rdev), minor_fn(output_rdev))
    if number_map.get("output") != actual_number or actual_number in isolation.all_mounted_devices:
        raise build.OfflineVmBuildError("fixed result block differs from the unmounted operation output device")
    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(OUTPUT_DEVICE, flags)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISBLK(opened.st_mode) or build._linux_stat_rdev(opened) != output_rdev:
            raise build.OfflineVmBuildError("fixed result block identity changed while opening")
        actual_capacity = struct.unpack("Q", build._linux_ioctl(fd, BLKGETSIZE64, struct.pack("Q", 0)))[0]
        if actual_capacity != capacity:
            raise build.OfflineVmBuildError("fixed result block capacity differs from the request")
        _write_candidate_payload_receipt_header(
            fd, candidate, result, request,
            publish_service_result=publish_service_result,
            publish_service_ready=publish_service_ready,
            finalize_evidence_volume=finalize_evidence_volume,
        )
    finally:
        os.close(fd)


def _write_candidate_payload_receipt_header(
    fd: int,
    candidate: Path,
    result: build.GuestBuildResult,
    request: dict[str, Any],
    *,
    publish_service_result: Callable[[], None],
    publish_service_ready: Callable[[], None],
    finalize_evidence_volume: Callable[[], tuple[int, str]],
) -> None:
    """Flush ISO then evidence before binding both volumes into output commit."""
    if build._linux_pread(fd, RESULT_HEADER_SIZE, 0) != bytes(RESULT_HEADER_SIZE):
        raise build.OfflineVmBuildError("fixed result block header is not blank before candidate publication")
    command_digest = result.command_argv_sha256
    if (
        not isinstance(command_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", command_digest) is None
        or not isinstance(result.iso_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", result.iso_sha256) is None
    ):
        raise build.OfflineVmBuildError("fixed candidate receipt lacks a complete command or ISO digest")
    with candidate.open("rb") as stream:
        offset = RESULT_ISO_OFFSET
        remaining = result.iso_size
        digest = hashlib.sha256()
        while remaining:
            chunk = stream.read(min(MAX_RESULT_COPY_CHUNK, remaining))
            if not chunk:
                raise build.OfflineVmBuildError("candidate ISO shortened during output copy")
            digest.update(chunk)
            _pwrite_all(fd, chunk, offset)
            offset += len(chunk)
            remaining -= len(chunk)
        if digest.hexdigest() != result.iso_sha256:
            raise build.OfflineVmBuildError("candidate ISO changed during output copy")

    # First make ISO bytes durable, then require the separate evidence-volume
    # writer to fsync and hash its exact completed bytes. No output receipt or
    # header is written if evidence finalization fails.
    os.fsync(fd)
    evidence_size, evidence_sha256 = finalize_evidence_volume()
    header = build_result_header(
        request,
        status="CANDIDATE_ISO",
        iso_size=result.iso_size,
        iso_sha256=result.iso_sha256,
        command_argv_sha256=command_digest,
        evidence_size=evidence_size,
        evidence_sha256=evidence_sha256,
    )
    receipt = build_service_success_receipt(
        request,
        iso_size=result.iso_size,
        iso_sha256=result.iso_sha256,
        command_argv_sha256=command_digest,
        evidence_size=evidence_size,
        evidence_sha256=evidence_sha256,
    )
    header_prefix = header[:RESULT_RECEIPT_OFFSET]
    commit = build_result_commit_marker(
        request,
        header_prefix=header_prefix,
        service_receipt=receipt,
        evidence_size=evidence_size,
        evidence_sha256=evidence_sha256,
    )
    terminal = offline_vm_worker.build_result_terminal_marker(
        request,
        header_prefix=header_prefix,
        service_receipt=receipt,
        commit_marker=commit,
        evidence_size=evidence_size,
        evidence_sha256=evidence_sha256,
    )
    # The durable service result and ready receipt follow both volume fsyncs;
    # output receipt/header/commit/terminal are then published last.
    publish_service_result()
    _pwrite_all(fd, receipt, RESULT_RECEIPT_OFFSET)
    os.fsync(fd)
    _pwrite_all(fd, header_prefix, 0)
    os.fsync(fd)
    publish_service_ready()
    _pwrite_all(fd, commit, RESULT_COMMIT_OFFSET)
    os.fsync(fd)
    # This host-visible marker is written only after the commit fsync returned.
    # The host later fsyncs the output image after QEMU exits and requires this
    # marker before it can inspect or accept the candidate bytes.
    _pwrite_all(fd, terminal, RESULT_TERMINAL_OFFSET)
    os.fsync(fd)


def _pwrite_all(fd: int, payload: bytes, offset: int) -> None:
    pwrite = getattr(os, "pwrite", None)
    if not callable(pwrite):
        raise build.OfflineVmBuildError("positional result-volume writes are unavailable")
    written = 0
    while written < len(payload):
        count = pwrite(fd, payload[written:], offset + written)
        if count <= 0:
            raise build.OfflineVmBuildError("result-volume write was incomplete")
        written += count


def main() -> int:
    try:
        build._verify_installed_service_unit()
        build._verify_installed_path_unit()
        invocation, request = load_invocation()
        build._write_exclusive_private(
            Path(build.BUILD_SERVICE_STARTED_PATH),
            canonical_json({
                "format": build.BUILD_RECEIPT_FORMAT,
                "operation_id": invocation.operation_id,
                "request_sha256": invocation.request_sha256,
            }),
            0o600,
        )
        isolation = guest.collect_guest_isolation()
        guest.validate_guest_isolation(request, isolation)
        service_state = collect_build_service_security()
        operations = build.LinuxGuestBuildOperations(service_state)
        result = build.execute_authorized_guest_build(invocation, service_state, operations)
        if result.status == "CANDIDATE_ISO_PENDING_HOST_VALIDATION" and result.cleanup_complete and result.iso_path:
            write_candidate_result_volume(
                Path(result.iso_path), result, request, isolation,
                publish_service_result=lambda: _write_result(result),
                publish_service_ready=lambda: _write_result_ready(result),
            )
        else:
            _write_result(result)
            _write_result_ready(result)
        return 0 if result.status == "CANDIDATE_ISO_PENDING_HOST_VALIDATION" and result.cleanup_complete and result.iso_path else 1
    except (OSError, ValueError, build.OfflineVmBuildError) as exc:
        print(json.dumps({"status": "INCOMPLETE_BUILD_SERVICE", "reason": str(exc)}, sort_keys=True, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
