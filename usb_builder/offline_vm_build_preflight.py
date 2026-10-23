"""NNP=1 one-shot coordinator for the sealed offline VM build prototype.

The module is intended only for the separately pinned guest image's fixed
systemd preflight unit. The default guest worker entry remains unavailable,
and this module is not called by the host product CLI.
"""

from __future__ import annotations

import json
import os
import stat
import struct
import sys
from pathlib import Path

from usb_builder import offline_vm_build as build
from usb_builder import offline_vm_build_service as service
from usb_builder import offline_vm_guest_worker as guest
from usb_builder.offline_vm_worker import (
    REQUEST_VOLUME_SIZE,
    OfflineVmContractError,
    canonical_json,
    decode_request_volume,
)


REQUEST_DEVICE = Path(guest.REQUEST_PATH)
BLKGETSIZE64 = 0x80081272


def read_request() -> dict[str, object]:
    """Read the exact read-only request block and bind it to its open inode."""
    try:
        before = REQUEST_DEVICE.stat()
    except OSError as exc:
        raise build.OfflineVmBuildError("fixed request block is unavailable") from exc
    if not stat.S_ISBLK(before.st_mode):
        build._reject("fixed request path is not a block device")
    fd = os.open(REQUEST_DEVICE, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        opened = os.fstat(fd)
        after = REQUEST_DEVICE.stat()
        if (
            not stat.S_ISBLK(opened.st_mode)
            or build._linux_stat_rdev(opened) != build._linux_stat_rdev(before)
            or build._linux_stat_rdev(after) != build._linux_stat_rdev(before)
        ):
            build._reject("fixed request block identity changed while opening")
        size = struct.unpack("Q", build._linux_ioctl(fd, BLKGETSIZE64, struct.pack("Q", 0)))[0]
        if size != REQUEST_VOLUME_SIZE:
            build._reject("fixed request block has the wrong capacity")
        chunks: list[bytes] = []
        remaining = REQUEST_VOLUME_SIZE
        while remaining:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                build._reject("fixed request block ended before its exact size")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            build._reject("fixed request block contains unexpected trailing bytes")
        if build._linux_stat_rdev(REQUEST_DEVICE.stat()) != build._linux_stat_rdev(before):
            build._reject("fixed request block identity changed after reading")
        return decode_request_volume(b"".join(chunks))
    finally:
        os.close(fd)


def main() -> int:
    """Run a single fully checked build request, then let systemd power off."""
    try:
        build._verify_installed_preflight_unit()
        build._verify_installed_preflight_path_unit()
        build._verify_installed_service_unit()
        build._verify_installed_path_unit()
        request = read_request()
        build._write_exclusive_private(
            Path(build.BUILD_PREFLIGHT_STARTED_PATH),
            canonical_json({"format": build.BUILD_RECEIPT_FORMAT, "purpose": "nnp1-preflight"}),
            0o600,
        )
        isolation = guest.collect_guest_isolation()
        guest.validate_guest_isolation(request, isolation)
        security = service.collect_build_service_security()
        build.validate_preflight_security(security, isolation)
        result = build.run_preflight_and_systemd_build(
            request,
            isolation,
            security,
            Path("/mnt/librecore-source"),
            Path("/mnt/librecore-packages"),
            Path(build.WORKER_ROOT),
            build.LinuxGuestBuildOperations(security),
        )
        if result.status != "CANDIDATE_ISO_PENDING_HOST_VALIDATION" or not result.cleanup_complete:
            guest._write_unavailable_header(Path("/dev/disk/by-id/virtio-LC-OUTPUT-RW"), request)
            print(json.dumps({"status": result.status, "request_sha256": result.request_sha256}, sort_keys=True, separators=(",", ":")))
            return 1
        print(json.dumps({
            "status": result.status,
            "operation_id": result.operation_id,
            "request_sha256": result.request_sha256,
            "iso_size": result.iso_size,
            "iso_sha256": result.iso_sha256,
            "cleanup_complete": result.cleanup_complete,
        }, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, ValueError, OfflineVmContractError, build.OfflineVmBuildError) as exc:
        print(json.dumps({"status": "INCOMPLETE_BUILD_PREFLIGHT", "reason": str(exc)}, sort_keys=True, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
