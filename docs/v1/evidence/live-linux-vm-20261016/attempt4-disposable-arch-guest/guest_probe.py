#!/usr/bin/env python3
"""Disposable live-guest checks; never invokes the product build workflow."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import pwd
import grp
import re
import stat
import subprocess
from pathlib import Path


EXPECTED_SCRATCH_BYTES = 3 * 1024**3
EXPECTED_SCRATCH_SERIAL = "LCV1SCRATCH3G"
BUILD_UID = 1000
BUILD_GID = 1000
PYTHON_CACHE_PREFIX = Path("/run/lc-pycache")
USB_BUILDER_SOURCE_MANIFEST: dict[str, dict[str, str]] = {
    "usb_builder/__init__.py": {"type": "regular", "sha256": "0eea27447961dd910d5c4b0fdf39b3f5dc8400bce24a0ef96e23db86d3ce954f"},
    "usb_builder/archiso_profile/airootfs/etc/mkinitcpio.conf.d/archiso.conf": {"type": "regular", "sha256": "cd1d328d0b343719c5975729bf7c8c5a227542beb396574ff4adeac30381db0b"},
    "usb_builder/archiso_profile/airootfs/etc/mkinitcpio.conf.d/zz-autoloader-usb.conf": {"type": "regular", "sha256": "ad143618ba35dd8ff00fab585cb6e6324fdccd6e19d7ca59c4abc69a610a1491"},
    "usb_builder/archiso_profile/airootfs/etc/motd": {"type": "regular", "sha256": "6b162110ef960d05ee8812457203933122b39824eadcd31348566b0bd50bcccc"},
    "usb_builder/archiso_profile/airootfs/etc/systemd/system/autoloader.service": {"type": "regular", "sha256": "e466ce4462f2835ddf2a45edf85eb0ab38f24cbc2f615840316800f7169ce764"},
    "usb_builder/archiso_profile/airootfs/opt/autoloader/start.sh": {"type": "regular", "sha256": "13d0f0ccb067d8258bb18edbfb8694fd8af06d3de65d081383bfde005b63af15"},
    "usb_builder/archiso_profile/airootfs/root/customize_airootfs.sh": {"type": "regular", "sha256": "181b259b1bd9a050b03c195de5fc4006021bc5182bc3d5f4b681cc310d8df26b"},
    "usb_builder/archiso_profile/efiboot/loader/entries/01-autoloader.conf": {"type": "regular", "sha256": "6cba1dcb03d28866bea2e2283bebc63ee70f0d2ad04b8b4e4ffb8b9c567ece97"},
    "usb_builder/archiso_profile/efiboot/loader/loader.conf": {"type": "regular", "sha256": "e33a12ea9537532fd2cc5cda2a42733f900043fe50e740fdb9977e349cd0ad9b"},
    "usb_builder/archiso_profile/packages.x86_64": {"type": "regular", "sha256": "7a7a3fa4127ab6fbaaf952be4bbde82f787a7da30d3111601a9e7fccd6334dbd"},
    "usb_builder/archiso_profile/pacman.conf": {"type": "regular", "sha256": "4dfb2f60a79083d950876305432a739b45a89644b89996fd3f4474561556ec16"},
    "usb_builder/archiso_profile/profiledef.sh": {"type": "regular", "sha256": "3cca6edc31e26a5353ade8920a5ceaefc0153d84e9e43978211ab57a00a9f5a8"},
    "usb_builder/archiso_profile/syslinux/archiso_sys-linux.cfg": {"type": "regular", "sha256": "684c781e19b884a7d41d62ce7d249f17efcd5f590ee1ec816b5deb458eb57f77"},
    "usb_builder/archiso_profile/syslinux/archiso_sys.cfg": {"type": "regular", "sha256": "5830c32df050001103b9b5e07629d1c956a5443c06cd3d9824d9cc7a70f6de0b"},
    "usb_builder/archiso_profile/syslinux/syslinux.cfg": {"type": "regular", "sha256": "977701a98b3d8587a0b154ab51c5b2788957a4998a1cf6b2dec96ad6f2347b36"},
    "usb_builder/build_usb.py": {"type": "regular", "sha256": "e1efcc3b53c195e5a683227a613df048a8409c6bae246e14d99b0de53e090ac7"},
    "usb_builder/build_usb.sh": {"type": "regular", "sha256": "eac760ea81e2d7f79b087b1b4bab61f8f20a0902e8fd16e1c184315921b260a3"},
    "usb_builder/mount_probe.py": {"type": "regular", "sha256": "fbc25ee4d0be65f1d71080e34ea3f135e38e07320df681dbbb8b1acadd1c8de0"},
    "usb_builder/network_probe.py": {"type": "regular", "sha256": "07abaecf237c947141ce59534706d608ad92b7fb543240381e336e230b8d41ec"},
    "usb_builder/patches/arch-install-scripts-31-2-pacstrap-status.patch": {"type": "regular", "sha256": "11394f8b901364236d449c35a84170eda29818bc9d41cbadd9677a04fddbceaa"},
    "usb_builder/rootless_mount_probe.py": {"type": "regular", "sha256": "3f54f535fe0f013c7a50a29733dc0a9165afb4cefb47c793686e8e4dde7a71ce"},
    "usb_builder/rootless_offline_preflight.py": {"type": "regular", "sha256": "ca9a5c926e0810886015c657f778bddf82c12625b29bf3fb4ec898644e0a2398"},
    "usb_builder/snapshot.lock.json": {"type": "regular", "sha256": "3b73f6f1fc123c223abc93f58cef9bb5d2590d4ce5cdf219c6209ba432291f8e"},
    "usb_builder/ventoy/ventoy.json": {"type": "regular", "sha256": "326286ae29891c361c4e70d534880d3375bd7426b518f521e42bf0135635f1ff"},
}
EXPECTED_USB_BUILDER_MANIFEST_SHA256 = "176ae9db968ddf6fae1718c793e924dc42937b0bb9d3044c08e45937fc5396c5"
RETRY1_SERIAL_RELATIVE = Path(
    "docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry1/serial.log"
)
RETRY3_SERIAL_RELATIVE = Path(
    "docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry3/serial.log"
)
RETRY4_SERIAL_RELATIVE = Path(
    "docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry4/serial.log"
)
RETRY5_SERIAL_RELATIVE = Path(
    "docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry5/serial.log"
)
RETRY6_SERIAL_RELATIVE = Path(
    "docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry6/serial.log"
)
RETRY_ARCHIVE_EXPECTED = {
    "results/retry1": {
        "manifest_sha256": "f854341312fe036d2104d424369f7425fde8d25988813f35f30900a1595227c6",
        "files": {
            "run-result.json": "6aa456051d1bef66173e78cd6347e57fa486b0b34c2b3e25bc00237136d015e2",
            "serial.log": "25ea9424fcf940869759c4322d2cc9896ef460a0e5080d4afee9ba4808ac4ab6",
            "hmp-transcript.txt": "2a760b9b885ced543a1964ee15fcae254b567c0454160ed00b482b5e236a40e8",
            "vga.ppm": "711378f4bef4cce5f3c3b95aba1f01692d0368c8dc5e82b21762bd808db3beec",
        },
    },
    "results/retry2-prelaunch": {
        "manifest_sha256": "74404c7156016d0c5290a0de6c78c8f6544416d3856e4920c8063dda0305daec",
        "files": {
            "run-result.json": "01c0d7bac6bd7d2fa86996e113445559364169b03b716d8e239a009d353c246a",
        },
    },
    "results/retry3": {
        "manifest_sha256": "9098fccc2d2f4f7fd5bc7ea1dab0e53ac7e3c2a508a9bac8dbe5a42e75d90a75",
        "files": {
            "run-result.json": "0508775e4af3efa15098e11f36229cd90f71761360e25b2738a09a88c04758b2",
            "serial.log": "c6de37f9c221b252f692d9ca0e1bff471f2c167ece33d4f8dacd9dbefeec2625",
            "hmp-transcript.txt": "5586c683f668ecc0f915b2b506c590f53e7582ef2143aa4f2c24feaf6f789ef8",
            "vga.ppm": "2cb0382369604884fd63ae0b5ba6a6bc658e1cb66e6239d09a698ed50d8fcc25",
        },
    },
    "results/retry4": {
        "manifest_sha256": "373cb75123dc6e7ae5d84f34db4152b7fdc396ba6630ba757e6ab96db6c2cf19",
        "files": {
            "run-result.json": "be3e63815d8ce2fd90aa935913aebfbcb71cf0fdafdc8c45005b54d503ca5462",
            "serial.log": "722adaa927749d7ba70775a5fb0479e1c9fe5eaf27c7909949a8dc569339fc72",
            "hmp-transcript.txt": "8d0b0a2912cdf4dab9356a3f33f0e388a02bb6205de399e3fb3914a9d34b1043",
            "vga.ppm": "d5dc4ecc27f947e41ebd1a948bf88d5ead3d12dfb2734e3bcad239c158db5227",
        },
    },
    "results/retry5": {
        "manifest_sha256": "504a913a422ad0232de4279fc5ffd21b3be01d5e2cd336ee33661cd54ee212b8",
        "files": {
            "run-result.json": "e6cb2f95d6c51a4a3729cc9d4e0796a7be17ea9d8e39c7453dbeaaf7ab1c4108",
            "serial.log": "1453123437effabc4120fbc9334e3fc2b5375d7246152ee92dc9d71cc7e2bd05",
            "hmp-transcript.txt": "8536ab4e67b5f24c78ad5edd239544b4f9d108bbc35334ea36f31dd9bea046ee",
            "vga.ppm": "843c2cf78a128d4253b0210e1750098d416a4b3232bc1298e82e8d78ee56d310",
        },
    },
    "results/retry6": {
        "manifest_sha256": "f33b74c84f1b0c306ed3486c893285c17675b02c863d4500f20bae94ff9be563",
        "files": {
            "run-result.json": "bb7a1edac63ab4afa36abb541e2f979c2410d34d6a3485f2e977987c6e5ce83e",
            "serial.log": "0867095973a43a1dc83bd490e905746e63db33af5de3cab6c8dc1324c28dff36",
            "hmp-transcript.txt": "10b77d1818dbf5881fed07e889c3d5fdf1d266ab597ac3da03edb332412c7c44",
            "vga.ppm": "233e15ddaa525015605cbfc06d68188f57a02fe45b211eea4d63ef546899e95a",
        },
    },
}
SOURCE = Path("/mnt/source")
SCRATCH = Path("/mnt/scratch")
EVIDENCE_RELATIVE = Path(
    "docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest"
)


def run(
    argv: list[str],
    *,
    check: bool = True,
    timeout: int = 60,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    print("COMMAND=" + json.dumps(argv), flush=True)
    command_env = os.environ.copy()
    if env is not None:
        command_env.update(env)
        print("COMMAND_ENV=" + json.dumps(env, sort_keys=True), flush=True)
    result = subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        close_fds=True,
        timeout=timeout,
        env=command_env,
    )
    print(result.stdout, end="", flush=True)
    print(f"COMMAND_EXIT={result.returncode}", flush=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {argv[0]}")
    return result


def exact_locked_packages() -> dict[str, str]:
    lock_path = SOURCE / "usb_builder" / "snapshot.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    rootless = lock.get("rootless_builder")
    if not isinstance(rootless, dict):
        raise RuntimeError("snapshot lock has no rootless-builder contract")
    packages = {
        lock.get("archiso_package"): lock.get("archiso_version"),
        rootless.get("arch_install_scripts_package"): rootless.get("arch_install_scripts_version"),
        rootless.get("pacman_package"): rootless.get("pacman_version"),
        rootless.get("util_linux_package"): rootless.get("util_linux_version"),
        rootless.get("bash_package"): rootless.get("bash_version"),
        rootless.get("map_helper_package"): rootless.get("map_helper_package_version"),
        rootless.get("libcap_package"): rootless.get("libcap_version"),
    }
    if any(not isinstance(name, str) or not name or not isinstance(version, str) or not version for name, version in packages.items()):
        raise RuntimeError("snapshot lock has incomplete pinned package identities")
    if len(packages) != len(set(packages)):
        raise RuntimeError("snapshot lock package identity map is ambiguous")
    return {str(name): str(version) for name, version in packages.items()}


def verify_source_mount() -> str:
    rows = []
    for line in Path("/proc/mounts").read_text(encoding="ascii").splitlines():
        fields = line.split()
        if len(fields) == 6 and fields[1] == str(SOURCE):
            rows.append(fields)
    if len(rows) != 1:
        raise RuntimeError("source mount is missing or ambiguous")
    fields = rows[0]
    if fields[0] != "source" or fields[2] != "9p" or "ro" not in fields[3].split(","):
        raise RuntimeError(f"source mount is not the expected read-only 9p share: {fields!r}")
    return " ".join(fields)


def regular_source_file(root: Path, relative: Path) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError(f"source path is not a safe relative path: {relative}")
    root_info = root.lstat()
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise RuntimeError(f"source root must be a real directory: {root}")
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise RuntimeError(f"source path has a linked/non-directory component: {current}")
    path = current / relative.name
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"source must be a regular non-symlink file: {relative}")
    return path


def manifest_bytes(manifest: dict[str, dict[str, str]]) -> bytes:
    payload = {"schema_version": 1, "files": {name: manifest[name] for name in sorted(manifest)}}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")


def manifest_sha256(manifest: dict[str, dict[str, str]]) -> str:
    return hashlib.sha256(manifest_bytes(manifest)).hexdigest()


def verify_usb_builder_source_manifest(
    root: Path = SOURCE,
    manifest: dict[str, dict[str, str]] = USB_BUILDER_SOURCE_MANIFEST,
    expected_sha256: str = EXPECTED_USB_BUILDER_MANIFEST_SHA256,
) -> dict[str, object]:
    if not manifest or manifest_sha256(manifest) != expected_sha256:
        raise RuntimeError("pinned usb_builder manifest digest mismatch")
    checked: dict[str, str] = {}
    for name in sorted(manifest):
        if "\\" in name:
            raise RuntimeError(f"manifest path is not canonical POSIX: {name!r}")
        relative = Path(name)
        if relative.is_absolute() or relative.as_posix() != name or ".." in relative.parts:
            raise RuntimeError(f"manifest path is not a safe exact relative name: {name!r}")
        if not name.startswith("usb_builder/") or "/docs/" in name or name.endswith(".md"):
            raise RuntimeError(f"manifest includes a non-guest source path: {name!r}")
        entry = manifest[name]
        if set(entry) != {"type", "sha256"} or entry.get("type") != "regular":
            raise RuntimeError(f"manifest has an unexpected source type/shape: {name!r}")
        digest = entry.get("sha256", "")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise RuntimeError(f"manifest has an invalid SHA-256: {name!r}")
        path = regular_source_file(root, relative)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != digest:
            raise RuntimeError(f"pinned usb_builder source SHA-256 mismatch: {name}")
        checked[name] = actual
    return {
        "manifest_sha256": expected_sha256,
        "file_count": len(checked),
        "files": checked,
    }


def require_readonly_source_write_open(path: Path) -> str:
    flags = os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        if exc.errno != errno.EROFS:
            raise RuntimeError(f"read-only source write-open did not fail with EROFS: errno={exc.errno}") from exc
        return f"BLOCKED errno={exc.errno}"
    os.close(descriptor)
    raise RuntimeError("source share unexpectedly allowed write-open")


def prepare_private_cache_prefix(path: Path = PYTHON_CACHE_PREFIX, expected_uid: int | None = None) -> None:
    owner = os.geteuid() if expected_uid is None else expected_uid
    if owner != os.geteuid() and os.geteuid() != 0:
        raise RuntimeError("only root may create a cache prefix for another UID")
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"Python cache prefix already exists: {path}")
    parent_info = path.parent.lstat()
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
        raise RuntimeError(f"Python cache parent is not a real directory: {path.parent}")
    os.mkdir(path, 0o700)
    if owner != os.geteuid():
        os.chown(path, owner, owner)
    validate_private_cache_prefix(path, expected_uid=owner)


def validate_private_cache_prefix(path: Path, expected_uid: int | None = None) -> None:
    owner = os.geteuid() if expected_uid is None else expected_uid
    info = path.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != owner
        or stat.S_IMODE(info.st_mode) != 0o700
        or any(path.iterdir())
    ):
        raise RuntimeError(f"Python cache prefix must be private, empty, and owned by UID {owner}: {path}")


def verify_guest_helper_binding(expected_helper_sha256: str) -> str:
    helper = Path(__file__)
    info = helper.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RuntimeError("guest helper must be a regular non-symlink file")
    actual = hashlib.sha256(helper.read_bytes()).hexdigest()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_helper_sha256) or actual != expected_helper_sha256:
        raise RuntimeError("guest helper bytes differ from the host-pinned helper SHA-256")
    return actual


def verify_archived_evidence(root: Path) -> dict[str, str]:
    verified: dict[str, str] = {}
    for directory, expected in RETRY_ARCHIVE_EXPECTED.items():
        manifest_relative = Path(directory) / "SHA256SUMS"
        manifest = regular_source_file(root, manifest_relative)
        manifest_bytes = manifest.read_bytes()
        manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
        if manifest_hash != expected["manifest_sha256"]:
            raise RuntimeError(f"archived evidence manifest SHA-256 mismatch: {manifest_relative}")
        try:
            manifest_lines = manifest_bytes.decode("ascii").splitlines()
        except UnicodeDecodeError as exc:
            raise RuntimeError(f"archived evidence manifest is not ASCII: {manifest_relative}") from exc
        parsed: dict[str, str] = {}
        for line in manifest_lines:
            digest, separator, filename = line.partition("  ")
            if not separator or filename in parsed or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise RuntimeError(f"archived evidence manifest has malformed/duplicate rows: {manifest_relative}")
            parsed[filename] = digest
        if parsed != expected["files"]:
            raise RuntimeError(f"archived evidence manifest entries differ from pins: {manifest_relative}")
        verified[manifest_relative.as_posix()] = manifest_hash
        for filename, expected_hash in expected["files"].items():
            relative = Path(directory) / filename
            evidence = regular_source_file(root, relative)
            actual_hash = hashlib.sha256(evidence.read_bytes()).hexdigest()
            if actual_hash != expected_hash:
                raise RuntimeError(f"archived evidence SHA-256 mismatch: {relative}")
            verified[relative.as_posix()] = actual_hash
    return verified


def verify_scratch_device() -> None:
    inventory = run(
        ["/usr/bin/lsblk", "--json", "--bytes", "--output", "NAME,TYPE,SIZE,RO,RM,SERIAL,MOUNTPOINTS"]
    ).stdout
    print("BLOCK_INVENTORY_JSON_BEGIN", flush=True)
    print(inventory, end="", flush=True)
    print("BLOCK_INVENTORY_JSON_END", flush=True)
    parsed = json.loads(inventory)
    devices = parsed.get("blockdevices")
    if not isinstance(devices, list):
        raise RuntimeError("lsblk did not return a blockdevices list")
    disks = [item for item in devices if isinstance(item, dict) and item.get("type") == "disk"]
    if len(disks) != 1 or disks[0].get("name") != "vda":
        raise RuntimeError(f"unexpected writable disk inventory: {disks!r}")
    disk = disks[0]
    if (
        disk.get("size") != EXPECTED_SCRATCH_BYTES
        or disk.get("ro") not in (0, False)
        or disk.get("rm") not in (0, False)
        or disk.get("serial") != EXPECTED_SCRATCH_SERIAL
        or disk.get("mountpoints") not in (None, [])
        or disk.get("children") not in (None, [])
    ):
        raise RuntimeError(f"/dev/vda is not the exact unmounted scratch disk: {disk!r}")
    size = run(["/usr/bin/blockdev", "--getsize64", "/dev/vda"]).stdout.strip()
    read_only = run(["/usr/bin/blockdev", "--getro", "/dev/vda"]).stdout.strip()
    if size != str(EXPECTED_SCRATCH_BYTES) or read_only != "0":
        raise RuntimeError("blockdev size/read-only identity does not match the scratch file")
    for line in Path("/proc/mounts").read_text(encoding="ascii").splitlines():
        fields = line.split()
        if fields and re.fullmatch(r"/dev/vda(?:[0-9]+)?", fields[0]):
            raise RuntimeError(f"scratch block device is already mounted: {line}")
    print(f"SCRATCH_DEVICE=/dev/vda SERIAL={EXPECTED_SCRATCH_SERIAL} SIZE={size} RO={read_only}", flush=True)


def verify_scratch_write() -> None:
    SCRATCH.mkdir(mode=0o755, parents=True, exist_ok=True)
    run(["/usr/bin/mkfs.ext4", "-F", "-q", "/dev/vda"])
    run(["/usr/bin/mount", "/dev/vda", str(SCRATCH)])
    marker = SCRATCH / "writable-scratch-probe.txt"
    marker.write_text("disposable Arch VM scratch only\n", encoding="ascii")
    run(["/usr/bin/sync"])
    marker_hash = run(["/usr/bin/sha256sum", str(marker)]).stdout.split()[0]
    run(["/usr/bin/umount", str(SCRATCH)])
    run(["/usr/bin/mount", "/dev/vda", str(SCRATCH)])
    persisted = marker.read_bytes()
    if persisted != b"disposable Arch VM scratch only\n":
        raise RuntimeError("scratch marker did not survive unmount/remount")
    run(["/usr/bin/umount", str(SCRATCH)])
    print(f"SCRATCH_WRITE_SHA256={marker_hash}", flush=True)
    print("SCRATCH_DISK=WRITABLE_AND_UNMOUNTED", flush=True)


def prepare_build_identity() -> None:
    if any(entry.pw_name == "fwbuild" for entry in pwd.getpwall()):
        raise RuntimeError("fwbuild already exists; refusing to reuse an unknown account")
    if any(entry.pw_uid == 1000 for entry in pwd.getpwall()) or any(entry.gr_gid == 1000 for entry in grp.getgrall()):
        raise RuntimeError("UID/GID 1000 is already assigned; refusing to alter an existing identity")
    for policy in (Path("/etc/subuid"), Path("/etc/subgid")):
        if policy.exists() and policy.read_text(encoding="ascii").strip():
            raise RuntimeError(f"{policy} is not empty; refusing to change existing subordinate-ID policy")
    run(["/usr/bin/groupadd", "--gid", "1000", "fwbuild"])
    run(["/usr/bin/useradd", "--uid", "1000", "--gid", "1000", "--create-home", "--home-dir", "/home/fwbuild", "--shell", "/usr/bin/bash", "fwbuild"])
    Path("/etc/subuid").write_text("fwbuild:100000:65536\n", encoding="ascii")
    Path("/etc/subgid").write_text("fwbuild:100000:65536\n", encoding="ascii")
    os.chmod("/etc/subuid", 0o644)
    os.chmod("/etc/subgid", 0o644)


def rootless_import_preflight_code() -> str:
    return '''\
import hashlib
import importlib
import json
import os
import stat
import sys
from dataclasses import asdict
from pathlib import Path

if sys.flags.optimize != 0:
    raise RuntimeError('optimized Python is unsupported for the rootless import preflight')
print('CHILD_PYTHON_OPTIMIZE=0', flush=True)

prefix = Path('/run/lc-pycache')
assert sys.pycache_prefix == str(prefix), 'unexpected sys.pycache_prefix'
assert sys.dont_write_bytecode is True, 'bytecode writing is enabled'
prefix_info = prefix.lstat()
assert (
    stat.S_ISDIR(prefix_info.st_mode)
    and not stat.S_ISLNK(prefix_info.st_mode)
    and prefix_info.st_uid == 1000
    and stat.S_IMODE(prefix_info.st_mode) == 0o700
    and not any(prefix.iterdir())
), 'cache prefix must be fresh, private, empty and root-owned'

manifest = json.loads(os.environ['LC_USB_BUILDER_MANIFEST_JSON'])
canonical = json.dumps(
    {'schema_version': 1, 'files': {name: manifest[name] for name in sorted(manifest)}},
    sort_keys=True,
    separators=(',', ':'),
).encode('ascii')
manifest_hash = hashlib.sha256(canonical).hexdigest()
assert manifest_hash == os.environ['LC_EXPECTED_USB_BUILDER_MANIFEST_SHA256'], 'manifest digest mismatch'
assert len(manifest) == 24, 'unexpected manifest inventory size'
root = Path('/mnt/source')
root_info = root.lstat()
assert stat.S_ISDIR(root_info.st_mode) and not stat.S_ISLNK(root_info.st_mode), 'unsafe source mount root'

def check_manifest():
    checked = set()
    for name, entry in manifest.items():
        assert (
            name.startswith('usb_builder/')
            and '\\\\' not in name
            and '/docs/' not in name
            and not name.endswith('.md')
        ), ('unsafe manifest path', name)
        relative = Path(name)
        assert not relative.is_absolute() and relative.as_posix() == name and '..' not in relative.parts, ('noncanonical path', name)
        assert set(entry) == {'type', 'sha256'} and entry['type'] == 'regular', ('invalid manifest entry', name)
        current = root
        for part in relative.parts[:-1]:
            current = current / part
            info = current.lstat()
            assert stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode), ('unsafe component', str(current))
        target = current / relative.name
        info = target.lstat()
        assert stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode), ('not regular', name)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        assert digest == entry['sha256'], ('source hash mismatch', name)
        checked.add(name)
    assert checked == set(manifest), 'manifest inventory mismatch'
    return checked

checked = check_manifest()
helper = Path('/run/lc-guest-probe.py')
helper_info = helper.lstat()
assert stat.S_ISREG(helper_info.st_mode) and not stat.S_ISLNK(helper_info.st_mode), 'helper must be regular'
helper_hash = hashlib.sha256(helper.read_bytes()).hexdigest()
assert helper_hash == os.environ['LC_EXPECTED_GUEST_HELPER_SHA256'], 'helper hash mismatch'
print('CHILD_PYTHON_CACHE_POLICY=PASS', flush=True)
print('CHILD_USB_BUILDER_MANIFEST_SHA256=' + manifest_hash, flush=True)
print('CHILD_USB_BUILDER_MANIFEST_FILE_COUNT=' + str(len(checked)), flush=True)
print('CHILD_GUEST_HELPER_SHA256=' + helper_hash, flush=True)

sys.path.insert(0, str(root))
module = importlib.import_module('usb_builder.build_usb')
expected_module = (root / 'usb_builder/build_usb.py').resolve(strict=True)
assert Path(module.__file__).resolve(strict=True) == expected_module, 'builder imported from unexpected path'
for name, item in tuple(sys.modules.items()):
    if name == 'usb_builder' or name.startswith('usb_builder.'):
        module_file = getattr(item, '__file__', None)
        if module_file is not None:
            resolved = Path(module_file).resolve(strict=True)
            relative = resolved.relative_to(root).as_posix()
            assert relative in manifest, ('unmanifested imported module', name, relative)
check_manifest()
assert not any(prefix.iterdir()), 'Python cache prefix changed during import'
host_preflight = getattr(module, '_rootless_archiso_host_preflight')
mount_preflight = getattr(module, '_rootless_archiso_mount_preflight')
host = host_preflight()
print('ROOTLESS_HOST_PREFLIGHT=PASS', flush=True)
print(json.dumps({'host': asdict(host)}, sort_keys=True), flush=True)
mount = mount_preflight(host)
print('ROOTLESS_MOUNT_PREFLIGHT=PASS', flush=True)
print(json.dumps({'mount': mount}, sort_keys=True), flush=True)
print('GUEST_PROBE_COMPLETE_NO_BUILD', flush=True)
'''


def rootless_preflight_command(code: str) -> list[str]:
    return [
        "/usr/bin/setpriv",
        "--bounding-set=-all,+setuid,+setgid",
        f"--reuid={BUILD_UID}",
        f"--regid={BUILD_GID}",
        "--clear-groups",
        "--",
        "/usr/bin/python3",
        "-P",
        "-c",
        code,
    ]


def run_rootless_preflights() -> None:
    preflight_code = rootless_import_preflight_code()
    compile(preflight_code, "<rootless-import-preflight>", "exec")
    prepare_private_cache_prefix(expected_uid=BUILD_UID)
    manifest_json = json.dumps(USB_BUILDER_SOURCE_MANIFEST, sort_keys=True, separators=(",", ":"))
    expected_helper_sha256 = os.environ.get("LC_EXPECTED_GUEST_HELPER_SHA256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_helper_sha256):
        raise RuntimeError("host did not provide the reviewed guest helper hash")
    env = {
        "HOME": "/home/fwbuild",
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(SOURCE),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONOPTIMIZE": "0",
        "PYTHONPYCACHEPREFIX": str(PYTHON_CACHE_PREFIX),
        "TMPDIR": "/tmp",
        "LC_ALL": "C",
        "LC_EXPECTED_GUEST_HELPER_SHA256": expected_helper_sha256,
        "LC_EXPECTED_USB_BUILDER_MANIFEST_SHA256": EXPECTED_USB_BUILDER_MANIFEST_SHA256,
        "LC_USB_BUILDER_MANIFEST_JSON": manifest_json,
    }
    command = rootless_preflight_command(preflight_code)
    result = run(command, check=False, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"rootless host/mount preflight failed: {result.returncode}")
    required = (
        "CHILD_PYTHON_OPTIMIZE=0",
        "CHILD_PYTHON_CACHE_POLICY=PASS",
        f"CHILD_USB_BUILDER_MANIFEST_SHA256={EXPECTED_USB_BUILDER_MANIFEST_SHA256}",
        f"CHILD_USB_BUILDER_MANIFEST_FILE_COUNT={len(USB_BUILDER_SOURCE_MANIFEST)}",
        f"CHILD_GUEST_HELPER_SHA256={expected_helper_sha256}",
        "ROOTLESS_HOST_PREFLIGHT=PASS",
        "ROOTLESS_MOUNT_PREFLIGHT=PASS",
        "GUEST_PROBE_COMPLETE_NO_BUILD",
    )
    if any(line not in result.stdout.splitlines() for line in required):
        raise RuntimeError("rootless import/preflight did not emit every required integrity record")


def main() -> int:
    print("GUEST_PROBE_BEGIN", flush=True)
    print("UTC=" + run(["/usr/bin/date", "-u", "+%FT%TZ"]).stdout.strip(), flush=True)
    print("ID=" + run(["/usr/bin/id"]).stdout.strip(), flush=True)
    print("KERNEL=" + run(["/usr/bin/uname", "-a"]).stdout.strip(), flush=True)

    source_mount = verify_source_mount()
    print("SOURCE_MOUNT=" + source_mount, flush=True)
    expected_helper_sha256 = os.environ.get("LC_EXPECTED_GUEST_HELPER_SHA256", "")
    expected_manifest_sha256 = os.environ.get("LC_EXPECTED_USB_BUILDER_MANIFEST_SHA256", "")
    helper_sha256 = verify_guest_helper_binding(expected_helper_sha256)
    if expected_manifest_sha256 != EXPECTED_USB_BUILDER_MANIFEST_SHA256:
        raise RuntimeError("host-pinned and helper-pinned usb_builder manifests differ")
    manifest_result = verify_usb_builder_source_manifest()
    write_open = require_readonly_source_write_open(SOURCE / "usb_builder/build_usb.py")
    print("GUEST_HELPER_SHA256=" + helper_sha256, flush=True)
    print("USB_BUILDER_SOURCE_MANIFEST_SHA256=" + str(manifest_result["manifest_sha256"]), flush=True)
    print("USB_BUILDER_SOURCE_MANIFEST_FILE_COUNT=" + str(manifest_result["file_count"]), flush=True)
    print("USB_BUILDER_SOURCE_WRITE_OPEN=" + write_open, flush=True)
    print("GUEST_GIT_CHECK=NOT_PERFORMED; host Git HEAD/status validation only", flush=True)

    expected_packages = exact_locked_packages()
    package_result = run(["/usr/bin/pacman", "-Q", *sorted(expected_packages)], check=False)
    actual_packages: dict[str, str] = {}
    for line in package_result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2 or fields[0] in actual_packages:
            raise RuntimeError(f"malformed or duplicate pacman package row: {line!r}")
        actual_packages[fields[0]] = fields[1]
    if package_result.returncode != 0 or actual_packages != expected_packages:
        print("LOCKED_PACKAGE_EXPECTED=" + json.dumps(expected_packages, sort_keys=True), flush=True)
        print("LOCKED_PACKAGE_ACTUAL=" + json.dumps(actual_packages, sort_keys=True), flush=True)
        raise RuntimeError("live ISO package versions do not match the checked-in rootless build lock")
    print("LOCKED_PACKAGE_VERSIONS=" + json.dumps(actual_packages, sort_keys=True), flush=True)

    prepare_build_identity()
    run_rootless_preflights()
    verify_scratch_device()
    verify_scratch_write()
    print("GUEST_PROBE_COMPLETE_NO_BUILD", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"GUEST_PROBE_EXCEPTION={type(exc).__name__}: {exc}", flush=True)
        raise
