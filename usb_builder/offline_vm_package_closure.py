"""Pinned-metadata parser for separate worker and target USB package roots.

The staged 2026-09-30 repository databases are accepted only at their fixed
SHA-256 identities. This module inventories explicit direct seed policies for
both roots and can resolve them transitively only when the exact pacman
``vercmp`` executable is pinned by the snapshot. The current snapshot has no
such executable hash, so production closure generation remains unavailable.
Package signature blobs are identified, never cryptographically verified.
The current trust evidence also lacks authenticated Arch master-key material,
a verified exact pacman archive/signature, and accepted executable/runtime
identity. Synthetic comparator tests do not satisfy these production gates.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import io
import json
import os
import re
import stat
import struct
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, NoReturn, Protocol, Sequence

from usb_builder import offline_vm_worker


CLOSURE_FORMAT = "librecore-arch-package-closure-v1"
SEED_INVENTORY_FORMAT = "librecore-arch-package-seed-policy-v2"
SNAPSHOT_LOCK_SHA256 = "5d66df5f5e6eacd0770399985b65507e84f208a7fbd1352faca13c0ead456cb1"
DATABASE_SHA256 = {
    "core": "08d52f5e83f72a9e9f8464441da87b9a399f4decc7110fcfa3825f7c0af42dd2",
    "extra": "d2262e9def7afc76960c5242624cc14029afa47a9407acd84a29aed3a44f1663",
}
MAX_DATABASE_BYTES = 128 * 1024 * 1024
MAX_DATABASE_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_PACKAGE_COUNT = 100_000
MAX_DESC_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
TARGET_USB_PROFILE_RELATIVE_PATH = "usb_builder/archiso_profile/packages.x86_64"
TARGET_USB_PROFILE_SHA256 = "7a7a3fa4127ab6fbaaf952be4bbde82f787a7da30d3111601a9e7fccd6334dbd"
TARGET_USB_PROFILE_PATH = Path(__file__).with_name("archiso_profile") / "packages.x86_64"
ALLOWED_ARCHITECTURES = frozenset({"x86_64", "any"})
ALLOWED_ARCHIVE_SUFFIXES = (".pkg.tar.zst", ".pkg.tar.xz")
_PACKAGE_NAME_RE = re.compile(r"[A-Za-z0-9@+_.-]+\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_DEP_RE = re.compile(r"([A-Za-z0-9@+_.-]+)(>=|<=|=|>|<)?(.*)\Z")
PROVIDER_AMBIGUITY_POLICY = "REJECT_MORE_THAN_ONE_MATCHING_PROVIDER_ACROSS_CORE_AND_EXTRA"
SEALED_VERCMP_RUNTIME_POLICY = "sealed-static-elf64-x86_64-no-interpreter-no-dynamic-segment-v1"
DYNAMIC_VERCMP_RUNTIME_POLICY = "builder-owned-immutable-dynamic-elf-runtime-plan-v1"
DYNAMIC_VERCMP_RUNTIME_ROOT_POLICY = "0700-private-owner-checked-copy-0500-readonly-after-hash-v1"


class PackageClosureError(RuntimeError):
    """Pinned database metadata or package selection is malformed."""


class PackageClosureUnavailable(PackageClosureError):
    """A required exact resolver or direct-root input pin is unavailable."""


def _fail(message: str) -> NoReturn:
    raise PackageClosureError(message)


def _unavailable(message: str) -> NoReturn:
    raise PackageClosureUnavailable(message)


def _fchmod(fd: int, mode: int) -> None:
    fchmod = getattr(os, "fchmod", None)
    if not callable(fchmod):
        _unavailable("descriptor mode setting is unavailable on this platform")
    fchmod(fd, mode)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


@dataclass(frozen=True)
class Seed:
    name: str
    reason: str


# The worker/build-host root is a fresh, bootable Linux root that runs the
# fixed systemd service. ``archiso`` is needed there to run mkarchiso; it is
# deliberately not a target USB profile package. Kernel/initramfs generation
# is included as a direct package requirement, independently of later pinned
# boot-asset identities.
WORKER_ROOT_SEEDS = (
    Seed("base", "worker/build-host base operating system"),
    Seed("linux", "worker/build-host kernel package source for pinned boot asset"),
    Seed("mkinitcpio", "worker/build-host deterministic initramfs generator"),
    Seed("systemd-sysvcompat", "worker/build-host systemd init entrypoint"),
    Seed("python", "fixed systemd ExecStart interpreter"),
    Seed("systemd", "fixed systemd unit execution contract"),
    Seed("archiso", "worker/build-host mkarchiso tool; excluded from target USB root"),
    Seed("arch-install-scripts", "worker/build-host pacstrap tool"),
    Seed("pacman", "worker/build-host package manager and pinned ALPM comparator package"),
    Seed("util-linux", "worker/build-host setpriv/unshare/mount/umount tools"),
    Seed("bash", "worker/build-host fixed shell tool"),
    Seed("shadow", "worker/build-host fwbuild account and uid/gid mapping helpers"),
    Seed("libcap", "worker/build-host getcap preflight tool"),
)


class VersionComparator(Protocol):
    """An externally supplied exact ALPM version comparator."""

    identity: Mapping[str, object]

    def compare(self, left: str, right: str) -> int: ...


class ExternalVercmpResolver:
    """Seal and run comparator bytes matching the supplied identity.

    This class checks bytes against its caller-supplied identity; it does not
    authenticate that identity or the package signature. Production closure
    generation must first pass ``_require_locked_resolver`` against the fixed,
    independently reviewed snapshot. The current snapshot has no resolver
    identity, so there is no production caller. Dynamic ELF executables remain
    unavailable until their loader and complete shared-library closure are
    pinned and presented through an equally immutable runtime.
    """

    def __init__(self, executable: Path, identity: Mapping[str, object]) -> None:
        required = {
            "kind", "pacman_version", "pacman_package_sha256", "pacman_signature_sha256",
            "executable_path", "executable_sha256", "runtime",
        }
        if not executable.is_absolute() or set(identity) != required:
            _fail("ALPM resolver identity/path has unknown or missing fields")
        runtime = identity.get("runtime")
        if isinstance(runtime, Mapping) and runtime.get("policy") == DYNAMIC_VERCMP_RUNTIME_POLICY:
            _validate_dynamic_vercmp_runtime_plan(runtime)
            _unavailable(
                "dynamic ALPM comparator execution is unavailable until the exact ELF dependency tree "
                "is materialized and executed from an immutable builder-owned runtime root"
            )
        if (
            identity.get("kind") != "pacman-vercmp-executable"
            or identity.get("pacman_version") != offline_vm_worker.LOCKED_PACKAGES["pacman"]
            or not isinstance(identity.get("pacman_package_sha256"), str)
            or not _SHA256_RE.fullmatch(str(identity.get("pacman_package_sha256")))
            or not isinstance(identity.get("pacman_signature_sha256"), str)
            or not _SHA256_RE.fullmatch(str(identity.get("pacman_signature_sha256")))
            or identity.get("executable_path") != "usr/bin/vercmp"
            or not isinstance(identity.get("executable_sha256"), str)
            or not _SHA256_RE.fullmatch(str(identity.get("executable_sha256")))
            or identity.get("runtime") != {
                "policy": SEALED_VERCMP_RUNTIME_POLICY,
                "architecture": "x86_64",
                "interpreter": None,
                "needed_libraries": [],
            }
        ):
            _fail("ALPM resolver identity is malformed or not the locked pacman version")
        before = _safe_db_path(executable, "ALPM version comparator")
        if before.st_size <= 0 or before.st_size > 64 * 1024 * 1024:
            _fail("ALPM version comparator size is outside its bound")
        raw = _read_pinned_file(executable, str(identity["executable_sha256"]), "ALPM version comparator", 64 * 1024 * 1024)
        if not raw:
            _fail("ALPM version comparator is empty")
        _validate_static_elf64(raw)
        self.executable_fd = _seal_executable_bytes(raw)
        self.identity = dict(identity)

    def compare(self, left: str, right: str) -> int:
        if not left or not right or any(char.isspace() for char in left + right):
            _fail("ALPM version comparator input is malformed")
        try:
            result = subprocess.run(
                [f"/proc/self/fd/{self.executable_fd}", left, right],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
                cwd="/",
                pass_fds=(self.executable_fd,),
                shell=False,
                close_fds=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PackageClosureUnavailable("pinned ALPM version comparison failed to run") from exc
        if result.returncode != 0 or len(result.stdout) > 32 or result.stderr:
            _fail("pinned ALPM version comparator returned an invalid result")
        try:
            text = result.stdout.decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise PackageClosureError("pinned ALPM comparator output is not ASCII") from exc
        if text not in {"-1", "0", "1"}:
            _fail("pinned ALPM comparator output is not exactly -1, 0, or 1")
        return int(text)

    def close(self) -> None:
        fd = getattr(self, "executable_fd", -1)
        if isinstance(fd, int) and fd >= 0:
            self.executable_fd = -1
            os.close(fd)

    def __del__(self) -> None:
        try:
            self.close()
        except OSError:
            pass


def _validate_static_elf64(raw: bytes) -> None:
    """Require a static x86_64 ET_EXEC with no loader or dynamic segment."""
    if len(raw) < 64 or raw[:4] != b"\x7fELF" or raw[4:7] != b"\x02\x01\x01":
        _fail("ALPM comparator runtime is not a supported ELF64 little-endian image")
    try:
        elf_type, machine = struct.unpack_from("<HH", raw, 16)
        phoff = struct.unpack_from("<Q", raw, 32)[0]
        ehsize, phentsize, phnum = struct.unpack_from("<HHH", raw, 52)
    except struct.error as exc:
        raise PackageClosureError("ALPM comparator ELF header is truncated") from exc
    if elf_type != 2 or machine != 62 or ehsize != 64 or phentsize < 56 or phnum == 0:
        _fail("ALPM comparator runtime is not the fixed static x86_64 executable form")
    table_end = phoff + phentsize * phnum
    if phoff < ehsize or table_end > len(raw):
        _fail("ALPM comparator ELF program-header table is outside its pinned bytes")
    types: list[int] = []
    for index in range(phnum):
        try:
            types.append(struct.unpack_from("<I", raw, phoff + index * phentsize)[0])
        except struct.error as exc:
            raise PackageClosureError("ALPM comparator ELF program header is truncated") from exc
    if 1 not in types or 2 in types or 3 in types:
        _fail("ALPM comparator runtime must be static and contain no PT_INTERP or PT_DYNAMIC")


def _validate_dynamic_vercmp_runtime_plan(value: Mapping[str, object]) -> dict[str, object]:
    """Validate the pinned PT_INTERP/DT_NEEDED closure shape without executing it.

    The current resolver deliberately has no dynamic execution implementation.
    This exact schema records what a future immutable runtime would need to
    bind; it is not sufficient to enable a live resolver by itself.
    """
    expected = {
        "policy", "architecture", "runtime_root_policy", "executable_needed_libraries",
        "interpreter", "libraries", "runtime_manifest_sha256",
    }
    if set(value) != expected or value.get("policy") != DYNAMIC_VERCMP_RUNTIME_POLICY:
        _fail("dynamic ALPM runtime plan has unknown or missing fields")
    if (
        value.get("architecture") != "x86_64"
        or value.get("runtime_root_policy") != DYNAMIC_VERCMP_RUNTIME_ROOT_POLICY
    ):
        _fail("dynamic ALPM runtime plan has an unsupported architecture or root policy")

    def safe_relative_path(raw: object, label: str) -> str:
        if not isinstance(raw, str) or "\\" in raw or "\x00" in raw:
            _fail(f"dynamic ALPM runtime {label} path is malformed")
        path = PurePosixPath(raw)
        if path.is_absolute() or not path.parts or str(path) != raw or any(part in ("", ".", "..") for part in path.parts):
            _fail(f"dynamic ALPM runtime {label} path is not a canonical relative path")
        return raw

    def safe_needed(raw: object, label: str) -> tuple[str, ...]:
        if not isinstance(raw, list) or any(
            not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9_.+-]+", name) is None
            for name in raw
        ):
            _fail(f"dynamic ALPM runtime {label} dependency list is malformed")
        names = tuple(raw)
        if names != tuple(sorted(set(names))):
            _fail(f"dynamic ALPM runtime {label} dependency list is not sorted and unique")
        return names

    interpreter = value.get("interpreter")
    if not isinstance(interpreter, dict) or set(interpreter) != {"path", "sha256", "needed_libraries"}:
        _fail("dynamic ALPM runtime PT_INTERP record is malformed")
    interpreter_needed = safe_needed(interpreter.get("needed_libraries"), "interpreter")
    interpreter_row: dict[str, object] = {
        "path": safe_relative_path(interpreter.get("path"), "PT_INTERP"),
        "sha256": interpreter.get("sha256"),
        "needed_libraries": list(interpreter_needed),
    }
    if not isinstance(interpreter_row["sha256"], str) or _SHA256_RE.fullmatch(interpreter_row["sha256"]) is None:
        _fail("dynamic ALPM runtime PT_INTERP hash is malformed")

    raw_executable_needed = safe_needed(value.get("executable_needed_libraries"), "executable")
    raw_libraries = value.get("libraries")
    if not isinstance(raw_libraries, list) or len(raw_libraries) > 256:
        _fail("dynamic ALPM runtime library inventory is malformed or oversized")
    libraries: list[dict[str, object]] = []
    library_names: set[str] = set()
    library_dependencies: dict[str, tuple[str, ...]] = {}
    for row in raw_libraries:
        if not isinstance(row, dict) or set(row) != {"soname", "path", "sha256", "needed_libraries"}:
            _fail("dynamic ALPM runtime library row is malformed")
        soname = row.get("soname")
        digest = row.get("sha256")
        if (
            not isinstance(soname, str)
            or re.fullmatch(r"[A-Za-z0-9_.+-]+", soname) is None
            or soname in library_names
            or not isinstance(digest, str)
            or _SHA256_RE.fullmatch(digest) is None
        ):
            _fail("dynamic ALPM runtime library identity is malformed or duplicated")
        library_names.add(soname)
        needed = safe_needed(row.get("needed_libraries"), f"library {soname}")
        library_dependencies[soname] = needed
        libraries.append({
            "soname": soname,
            "path": safe_relative_path(row.get("path"), f"library {soname}"),
            "sha256": digest,
            "needed_libraries": list(needed),
        })
    if [str(row["soname"]) for row in libraries] != sorted(library_names):
        _fail("dynamic ALPM runtime libraries are not sorted by SONAME")
    if len({str(row["path"]) for row in libraries} | {str(interpreter_row["path"])}) != len(libraries) + 1:
        _fail("dynamic ALPM runtime assigns multiple identities to one file path")

    by_name = {str(row["soname"]): row for row in libraries}
    required_names = set(raw_executable_needed) | set(interpreter_needed)
    pending = list(required_names)
    while pending:
        name = pending.pop()
        row = by_name.get(name)
        if row is None:
            _fail("dynamic ALPM runtime omits a transitive DT_NEEDED library")
        for dependency in library_dependencies[name]:
            if dependency not in required_names:
                required_names.add(dependency)
                pending.append(dependency)
    if library_names != required_names:
        _fail("dynamic ALPM runtime contains an unreferenced or missing library")

    body: dict[str, object] = {
        "policy": DYNAMIC_VERCMP_RUNTIME_POLICY,
        "architecture": "x86_64",
        "runtime_root_policy": DYNAMIC_VERCMP_RUNTIME_ROOT_POLICY,
        "executable_needed_libraries": list(raw_executable_needed),
        "interpreter": interpreter_row,
        "libraries": libraries,
    }
    digest = value.get("runtime_manifest_sha256")
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None or _sha256(canonical_json(body)) != digest:
        _fail("dynamic ALPM runtime manifest digest differs from its exact dependency closure")
    body["runtime_manifest_sha256"] = digest
    return body


def _seal_executable_bytes(raw: bytes) -> int:
    """Copy verified bytes into a sealed executable memfd, never a mutable path."""
    if not sys.platform.startswith("linux") or not callable(getattr(os, "memfd_create", None)):
        _unavailable("sealed executable memfd support is unavailable")
    try:
        import fcntl

        fd = os.memfd_create(
            "librecore-pinned-vercmp",
            getattr(os, "MFD_CLOEXEC", 0) | getattr(os, "MFD_ALLOW_SEALING", 0),
        )
    except (ImportError, OSError) as exc:
        raise PackageClosureUnavailable("sealed executable memfd could not be created") from exc
    try:
        _fchmod(fd, 0o500)
        written = 0
        while written < len(raw):
            count = os.write(fd, raw[written:])
            if count <= 0:
                _fail("sealed ALPM comparator copy was incomplete")
            written += count
        os.fsync(fd)
        seals = (
            getattr(fcntl, "F_SEAL_SEAL", 0x0001)
            | getattr(fcntl, "F_SEAL_SHRINK", 0x0002)
            | getattr(fcntl, "F_SEAL_GROW", 0x0004)
            | getattr(fcntl, "F_SEAL_WRITE", 0x0008)
        )
        fcntl.fcntl(fd, getattr(fcntl, "F_ADD_SEALS", 1033), seals)
        observed = fcntl.fcntl(fd, getattr(fcntl, "F_GET_SEALS", 1034))
        if observed & seals != seals or os.fstat(fd).st_size != len(raw):
            _fail("sealed ALPM comparator memfd did not retain the exact immutable bytes")
        return fd
    except BaseException:
        os.close(fd)
        raise


@dataclass(frozen=True)
class Package:
    repo: str
    name: str
    version: str
    arch: str
    filename: str
    size: int
    archive_sha256: str
    signature_blob_sha256: str
    dependencies: tuple[str, ...]
    provides: tuple[str, ...]
    conflicts: tuple[str, ...]
    replaces: tuple[str, ...]
    database_sha256: str

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.repo, self.name, self.version, self.arch)

    def as_metadata(self) -> dict[str, object]:
        return {
            "repo": self.repo,
            "name": self.name,
            "version": self.version,
            "arch": self.arch,
            "filename": self.filename,
            "size": self.size,
            "database_sha256": self.database_sha256,
            "archive_sha256": self.archive_sha256,
            "signature_blob_sha256": self.signature_blob_sha256,
            "signature_verified": False,
            "dependencies": list(self.dependencies),
            "provides": list(self.provides),
            "conflicts": list(self.conflicts),
            "replaces": list(self.replaces),
        }


def _safe_db_path(path: Path, label: str) -> os.stat_result:
    if not path.is_absolute():
        _fail(f"{label} path is not absolute")
    try:
        before = path.lstat()
    except OSError as exc:
        raise PackageClosureError(f"{label} is unavailable") from exc
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        _fail(f"{label} must be a single-link regular file")
    parent = path.parent
    while True:
        try:
            parent_info = parent.lstat()
        except OSError as exc:
            raise PackageClosureError(f"{label} parent is unavailable") from exc
        if stat.S_ISLNK(parent_info.st_mode):
            _fail(f"{label} has a symlinked parent component")
        if parent.parent == parent:
            break
        parent = parent.parent
    return before


def _read_pinned_file(path: Path, expected_sha256: str, label: str, max_bytes: int) -> bytes:
    before = _safe_db_path(path, label)
    if before.st_size <= 0 or before.st_size > max_bytes:
        _fail(f"{label} size is outside its fixed bound")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (before.st_dev, before.st_ino, before.st_size):
            _fail(f"{label} changed while opening")
        chunks: list[bytes] = []
        size = 0
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, min(1024 * 1024, max_bytes + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                _fail(f"{label} exceeds its fixed byte bound")
            digest.update(chunk)
            chunks.append(chunk)
        after = os.fstat(fd)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
            opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns
        ):
            _fail(f"{label} changed while reading")
        if digest.hexdigest() != expected_sha256:
            _fail(f"{label} SHA-256 differs from the fixed snapshot pin")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _parse_desc(raw: bytes, repo: str, database_sha256: str, directory_name: str) -> Package:
    if len(raw) > MAX_DESC_BYTES:
        _fail("package desc exceeds its fixed size bound")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PackageClosureError("package desc is not UTF-8") from exc
    fields: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("%") and line.endswith("%") and len(line) > 2:
            key = line[1:-1]
            if re.fullmatch(r"[A-Z0-9_]+", key) is None or key in fields:
                _fail("package desc contains a malformed or duplicate field")
            current = key
            fields[current] = []
        elif line:
            if current is None or "\x00" in line:
                _fail("package desc contains a value outside a field")
            fields[current].append(line)

    def scalar(name: str) -> str:
        values = fields.get(name)
        if values is None or len(values) != 1 or not values[0]:
            _fail(f"package desc lacks one exact {name} value")
        return values[0]

    def array(name: str) -> tuple[str, ...]:
        values = fields.get(name, [])
        if any(not value or value != value.strip() for value in values):
            _fail(f"package desc {name} array contains an empty or padded row")
        return tuple(values)

    name = scalar("NAME")
    version = scalar("VERSION")
    arch = scalar("ARCH")
    filename = scalar("FILENAME")
    size_text = scalar("CSIZE")
    archive_sha256 = scalar("SHA256SUM")
    signature_values = fields.get("PGPSIG")
    if _PACKAGE_NAME_RE.fullmatch(name) is None or not version or any(ch.isspace() for ch in version):
        _fail("package desc has an invalid name or version")
    if arch not in {"x86_64", "any", "i686", "aarch64", "armv7h"}:
        _fail(f"package desc has an unsupported architecture: {arch}")
    if (
        PurePosixPath(filename).name != filename
        or filename in {"", ".", ".."}
        or "/" in filename
        or "\\" in filename
    ):
        _fail("package desc has an unsafe archive filename")
    suffix = next((item for item in ALLOWED_ARCHIVE_SUFFIXES if filename.endswith(item)), None)
    if suffix is None or filename != f"{name}-{version}-{arch}{suffix}":
        _fail("package archive filename does not exactly match NAME, VERSION, ARCH, and an allowed suffix")
    if re.fullmatch(r"[1-9][0-9]*", size_text) is None:
        _fail("package desc has invalid compressed archive size metadata")
    if _SHA256_RE.fullmatch(archive_sha256) is None:
        _fail("package desc has invalid archive SHA-256 metadata")
    if signature_values is None or not signature_values:
        _fail("package desc lacks the required PGP signature metadata")
    signature_text = "".join(signature_values)
    try:
        signature_bytes = base64.b64decode(signature_text, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise PackageClosureError("package desc PGP signature metadata is not canonical base64") from exc
    if not signature_bytes:
        _fail("package desc PGP signature metadata is empty")
    if directory_name != f"{name}-{version}":
        _fail("package desc directory name differs from NAME/VERSION metadata")
    return Package(
        repo=repo,
        name=name,
        version=version,
        arch=arch,
        filename=filename,
        size=int(size_text),
        archive_sha256=archive_sha256,
        signature_blob_sha256=_sha256(signature_bytes),
        dependencies=array("DEPENDS"),
        provides=array("PROVIDES"),
        conflicts=array("CONFLICTS"),
        replaces=array("REPLACES"),
        database_sha256=database_sha256,
    )


def parse_repository_database(repo: str, raw: bytes, expected_sha256: str) -> tuple[Package, ...]:
    """Parse one pinned Arch sync DB without extracting archive paths."""
    if repo not in DATABASE_SHA256:
        _fail("repository name is outside the fixed core/extra set")
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_DATABASE_BYTES:
        _fail("repository database size is outside its fixed bound")
    actual_sha256 = _sha256(raw)
    if actual_sha256 != expected_sha256:
        _fail(f"{repo} database SHA-256 differs from its snapshot lock")
    packages: list[Package] = []
    seen_names: set[str] = set()
    seen_filenames: set[str] = set()
    seen_members: set[str] = set()
    expanded = 0
    try:
        archive = tarfile.open(fileobj=io.BytesIO(raw), mode="r:*")
    except (tarfile.TarError, OSError) as exc:
        raise PackageClosureError(f"{repo} database is not a supported tar archive") from exc
    with archive:
        members = archive.getmembers()
        if len(members) > MAX_PACKAGE_COUNT * 2:
            _fail(f"{repo} database contains too many archive members")
        for member in members:
            member_name = member.name
            if member_name in seen_members:
                _fail(f"{repo} database contains a duplicate tar member")
            seen_members.add(member_name)
            path = PurePosixPath(member_name)
            if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
                _fail(f"{repo} database contains an unsafe member path")
            if member.isdir():
                if len(path.parts) != 1:
                    _fail(f"{repo} database has a nested directory member")
                continue
            if not member.isfile() or len(path.parts) != 2 or path.parts[1] != "desc":
                _fail(f"{repo} database contains an unsupported non-desc member")
            if member.size <= 0 or member.size > MAX_DESC_BYTES:
                _fail(f"{repo} database desc has an invalid size")
            expanded += member.size
            if expanded > MAX_DATABASE_EXPANDED_BYTES:
                _fail(f"{repo} database exceeds its expanded metadata bound")
            stream = archive.extractfile(member)
            if stream is None:
                _fail(f"{repo} database desc cannot be read")
            desc = stream.read(MAX_DESC_BYTES + 1)
            if len(desc) != member.size:
                _fail(f"{repo} database desc is truncated or inconsistent")
            package = _parse_desc(desc, repo, actual_sha256, path.parts[0])
            if package.name in seen_names:
                _fail(f"{repo} database contains more than one version of a package name")
            if package.filename in seen_filenames:
                _fail("repository database contains a duplicate package filename")
            seen_names.add(package.name)
            seen_filenames.add(package.filename)
            packages.append(package)
            if len(packages) > MAX_PACKAGE_COUNT:
                _fail(f"{repo} database exceeds its package count bound")
    return tuple(sorted(packages, key=lambda item: (item.name, item.version, item.arch)))


def _snapshot(snapshot_raw: bytes, *, require_current_pin: bool = True) -> dict[str, Any]:
    if require_current_pin and _sha256(snapshot_raw) != SNAPSHOT_LOCK_SHA256:
        _fail("snapshot lock SHA-256 differs from its reviewed fixed input")
    try:
        value = json.loads(snapshot_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageClosureError("snapshot lock is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict) or value.get("snapshot_date") != "2026-09-30":
        _fail("snapshot lock date/schema differs from the fixed package closure")
    if value.get("architecture") != "x86_64":
        _fail("snapshot architecture differs from the fixed x86_64 package closure")
    database_hashes = value.get("repository_database_sha256")
    if not isinstance(database_hashes, dict) or set(database_hashes) != {"core", "extra"}:
        _fail("snapshot repository database hash map is malformed")
    if require_current_pin and database_hashes != DATABASE_SHA256:
        _fail("snapshot repository database pins differ from the fixed package closure")
    if value.get("package_signature_policy") != "Required DatabaseOptional":
        _fail("snapshot package-signature policy differs from the fixed package closure")
    rootless = value.get("rootless_builder")
    if not isinstance(rootless, dict):
        _fail("snapshot lock lacks the rootless builder package table")
    version_fields = {
        "arch-install-scripts": "arch_install_scripts_version",
        "pacman": "pacman_version",
        "util-linux": "util_linux_version",
        "bash": "bash_version",
        "shadow": "map_helper_package_version",
        "libcap": "libcap_version",
    }
    versions = {
        name: value.get("archiso_version") if name == "archiso" else rootless.get(version_fields[name])
        for name in offline_vm_worker.LOCKED_PACKAGES
    }
    if versions != offline_vm_worker.LOCKED_PACKAGES:
        _fail("snapshot fixed builder package versions differ from the worker contract")
    return value


def _target_usb_profile_seeds(snapshot: Mapping[str, Any]) -> tuple[Seed, ...]:
    """Read every target USB direct seed from the separately pinned profile."""
    if snapshot.get("packages_x86_64_sha256") != TARGET_USB_PROFILE_SHA256:
        _fail("target USB profile hash differs from the fixed snapshot pin")
    raw = _read_pinned_file(
        TARGET_USB_PROFILE_PATH,
        TARGET_USB_PROFILE_SHA256,
        "target USB package profile",
        2 * 1024 * 1024,
    )
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise PackageClosureError("target USB package profile is not ASCII") from exc
    names: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line or line != line.strip() or _PACKAGE_NAME_RE.fullmatch(line) is None:
            _fail(f"target USB package profile line {line_number} is malformed")
        names.append(line)
    if not names or len(set(names)) != len(names):
        _fail("target USB package profile is empty or contains duplicate roots")
    if "archiso" in names or "mkinitcpio-archiso" not in names:
        _fail("target USB profile must exclude host-only archiso and retain mkinitcpio-archiso")
    return tuple(Seed(name, f"pinned target USB package profile line {index}") for index, name in enumerate(names, start=1))


def _package_index(
    packages: Sequence[Package],
) -> tuple[dict[str, list[Package]], dict[str, list[Package]], dict[str, list[tuple[Package, str | None]]]]:
    names: dict[str, list[Package]] = {}
    filenames: dict[str, list[Package]] = {}
    provides: dict[str, list[tuple[Package, str | None]]] = {}
    for package in packages:
        names.setdefault(package.name, []).append(package)
        filenames.setdefault(package.filename, []).append(package)
        for raw_provide in package.provides:
            provide_name, provide_operator, provide_version = _parse_dependency(raw_provide)
            if provide_operator not in {"", "="}:
                _fail(f"unsupported ALPM provides expression: {raw_provide!r}")
            provides.setdefault(provide_name, []).append((package, provide_version if provide_operator else None))
    duplicate_files = [name for name, rows in filenames.items() if len(rows) > 1]
    if duplicate_files:
        _fail("core/extra databases contain duplicate package filename metadata")
    return names, filenames, provides


def _snapshot_builder_pins(snapshot: Mapping[str, Any]) -> dict[str, dict[str, str | None]]:
    rootless = snapshot.get("rootless_builder")
    if not isinstance(rootless, dict):
        _fail("snapshot lock lacks rootless builder package pins")
    pins = {
        "archiso": (snapshot.get("archiso_version"), None, None),
        "arch-install-scripts": (rootless.get("arch_install_scripts_version"), rootless.get("arch_install_scripts_package_sha256"), rootless.get("arch_install_scripts_signature_sha256")),
        "pacman": (rootless.get("pacman_version"), rootless.get("pacman_package_sha256"), rootless.get("pacman_signature_sha256")),
        "util-linux": (rootless.get("util_linux_version"), rootless.get("util_linux_package_sha256"), rootless.get("util_linux_signature_sha256")),
        "bash": (rootless.get("bash_version"), rootless.get("bash_package_sha256"), rootless.get("bash_signature_sha256")),
        "shadow": (rootless.get("map_helper_package_version"), rootless.get("map_helper_package_sha256"), rootless.get("map_helper_signature_sha256")),
        "libcap": (rootless.get("libcap_version"), rootless.get("libcap_package_sha256"), rootless.get("libcap_signature_sha256")),
    }
    return {name: {"version": row[0], "archive_sha256": row[1], "signature_blob_sha256": row[2]} for name, row in pins.items()}


def _seed_metadata(
    snapshot: Mapping[str, Any],
    packages: Sequence[Package],
    seeds: Sequence[Seed] = WORKER_ROOT_SEEDS,
) -> tuple[dict[str, object], ...]:
    names, _, _ = _package_index(packages)
    builder_pins = _snapshot_builder_pins(snapshot)
    result: list[dict[str, object]] = []
    for seed in seeds:
        candidates = names.get(seed.name, [])
        if len(candidates) != 1:
            _fail(f"direct seed {seed.name} is missing or ambiguous across pinned repositories")
        package = candidates[0]
        if package.arch not in ALLOWED_ARCHITECTURES:
            _fail(f"direct seed {seed.name} has an unsupported architecture {package.arch}")
        if seed.name in builder_pins:
            pin = builder_pins[seed.name]
            if package.version != pin["version"]:
                _fail(f"direct seed {seed.name} version differs from snapshot.lock.json")
            if pin["archive_sha256"] is not None and package.archive_sha256 != pin["archive_sha256"]:
                _fail(f"direct seed {seed.name} archive hash differs from snapshot.lock.json")
            if pin["signature_blob_sha256"] is not None and package.signature_blob_sha256 != pin["signature_blob_sha256"]:
                _fail(f"direct seed {seed.name} signature metadata differs from snapshot.lock.json")
            version_policy = "snapshot.lock.json exact version"
        else:
            version_policy = "exact package row selected by pinned repository database hash"
        record = package.as_metadata()
        record.update({"seed_reason": seed.reason, "version_policy": version_policy})
        result.append(record)
    return tuple(result)


def build_seed_inventory(core_db: bytes, extra_db: bytes, snapshot_raw: bytes) -> dict[str, object]:
    """Build both pinned direct-root inventories without claiming closure."""
    snapshot = _snapshot(snapshot_raw)
    expected = snapshot["repository_database_sha256"]
    if not isinstance(expected, dict):
        _fail("snapshot database hash map is malformed")
    core = parse_repository_database("core", core_db, str(expected["core"]))
    extra = parse_repository_database("extra", extra_db, str(expected["extra"]))
    packages = (*core, *extra)
    target_seeds = _target_usb_profile_seeds(snapshot)
    worker_records = _seed_metadata(snapshot, packages, WORKER_ROOT_SEEDS)
    target_records = _seed_metadata(snapshot, packages, target_seeds)

    def checked_size_total(records: Sequence[Mapping[str, object]]) -> int:
        total = 0
        for record in records:
            size = record.get("size")
            if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
                _fail("internal direct-seed size metadata is invalid")
            total += size
        return total

    unique_rows: dict[tuple[object, object, object, object], Mapping[str, object]] = {}
    for record in (*worker_records, *target_records):
        key = (record.get("repo"), record.get("name"), record.get("version"), record.get("arch"))
        unique_rows[key] = record
    unique_archive_total = checked_size_total(tuple(unique_rows.values()))
    body: dict[str, object] = {
        "format": SEED_INVENTORY_FORMAT,
        "status": "SEED_POLICY_COMPLETE_CLOSURE_UNAVAILABLE",
        "snapshot_date": "2026-09-30",
        "snapshot_lock_sha256": _sha256(snapshot_raw),
        "repository_databases": [
            {"repo": "core", "size": len(core_db), "sha256": _sha256(core_db)},
            {"repo": "extra", "size": len(extra_db), "sha256": _sha256(extra_db)},
        ],
        "seed_policy": {
            "worker_root": {
                "direct_seed_count": len(WORKER_ROOT_SEEDS),
                "complete_direct_policy": True,
                "purpose": "fresh bootable worker/build-host root for the fixed offline builder service",
                "seeds": [{"name": item.name, "reason": item.reason} for item in WORKER_ROOT_SEEDS],
                "archiso_is_worker_build_host_only": True,
            },
            "target_usb_root": {
                "direct_seed_count": len(target_seeds),
                "complete_direct_policy": True,
                "purpose": "exact package roots from the hash-pinned Archiso target profile",
                "profile_path": TARGET_USB_PROFILE_RELATIVE_PATH,
                "profile_sha256": TARGET_USB_PROFILE_SHA256,
                "seeds": [{"name": item.name, "reason": item.reason} for item in target_seeds],
                "archiso_excluded": True,
                "mkinitcpio_archiso_included": True,
            },
            "unresolved": [
                "the snapshot has no exact hash-pinned pacman vercmp executable identity; recursive closure generation remains unavailable",
                "package archive signatures have not been verified against a pinned Arch keyring",
            ],
        },
        "seed_packages": {
            "worker_root": list(worker_records),
            "target_usb_root": list(target_records),
        },
        "direct_seed_archive_bytes": {
            "worker_root": checked_size_total(worker_records),
            "target_usb_root": checked_size_total(target_records),
            "unique_union": unique_archive_total,
        },
        "signature_status": {
            "status": "SIGNATURE_BLOB_IDENTITIES_ONLY",
            "verified": False,
            "reason": "PGPSIG blobs were decoded and hashed from pinned database metadata; no keyring verification was run",
        },
        "dependency_closures": {
            "worker_root": {"status": "UNAVAILABLE_NO_PINNED_ALPM_RESOLVER", "packages": []},
            "target_usb_root": {"status": "UNAVAILABLE_NO_PINNED_ALPM_RESOLVER", "packages": []},
        },
    }
    body["inventory_sha256"] = _sha256(canonical_json(body))
    return body


def build_seed_inventory_from_paths(snapshot_path: Path, core_db_path: Path, extra_db_path: Path) -> dict[str, object]:
    snapshot_raw = _read_pinned_file(snapshot_path, SNAPSHOT_LOCK_SHA256, "snapshot lock", 2 * 1024 * 1024)
    core_raw = _read_pinned_file(core_db_path, DATABASE_SHA256["core"], "core database", MAX_DATABASE_BYTES)
    extra_raw = _read_pinned_file(extra_db_path, DATABASE_SHA256["extra"], "extra database", MAX_DATABASE_BYTES)
    return build_seed_inventory(core_raw, extra_raw, snapshot_raw)


def validate_seed_inventory(
    value: object,
    snapshot_path: Path,
    core_db_path: Path,
    extra_db_path: Path,
) -> dict[str, object]:
    """Independently reparse DBs and compare every provisional seed row."""
    expected = build_seed_inventory_from_paths(snapshot_path, core_db_path, extra_db_path)
    if not isinstance(value, dict) or canonical_json(value) != canonical_json(expected):
        _fail("seed inventory differs from independently reparsed pinned database metadata")
    return expected


def _parse_dependency(value: str) -> tuple[str, str, str]:
    match = _DEP_RE.fullmatch(value)
    if match is None:
        _fail(f"unsupported ALPM dependency expression: {value!r}")
    name, operator, version = match.groups()
    if not name or (operator and not version) or (not operator and version):
        _fail(f"malformed ALPM dependency expression: {value!r}")
    if version and any(char.isspace() for char in version):
        _fail(f"unsupported whitespace in ALPM dependency version: {value!r}")
    return name, operator or "", version


def _version_satisfies(actual: str, operator: str, required: str, compare: Callable[[str, str], int]) -> bool:
    if not operator:
        return True
    value = compare(actual, required)
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("ALPM comparator returned a non-integer result")
    if operator == "=":
        return value == 0
    if operator == ">=":
        return value >= 0
    if operator == "<=":
        return value <= 0
    if operator == ">":
        return value > 0
    if operator == "<":
        return value < 0
    _fail("unsupported ALPM dependency operator")


def _dependency_candidates(
    dep_name: str,
    operator: str,
    dep_version: str,
    names: Mapping[str, Sequence[Package]],
    provides: Mapping[str, Sequence[tuple[Package, str | None]]],
    compare: Callable[[str, str], int],
) -> list[Package]:
    possible: dict[tuple[str, str, str, str], Package] = {}
    for package in names.get(dep_name, ()):
        if _version_satisfies(package.version, operator, dep_version, compare):
            possible[package.key] = package
    for package, provided_version in provides.get(dep_name, ()):
        if not operator or (provided_version is not None and _version_satisfies(provided_version, operator, dep_version, compare)):
            possible[package.key] = package
    return list(possible.values())


def resolve_packages(
    packages: Sequence[Package],
    seed_specs: Sequence[Seed],
    locked_versions: Mapping[str, str],
    compare_versions: Callable[[str, str], int],
) -> tuple[dict[str, object], ...]:
    """Resolve a testable package set with an injected exact ALPM comparator.

    This algorithm never supplies a version comparator. Production entrypoints
    require a comparator identity pinned in the snapshot lock; the current
    snapshot has none, so full resolution remains unavailable.
    """
    by_name, _, by_provide = _package_index(packages)
    selected: dict[tuple[str, str, str, str], Package] = {}
    direct_reasons: dict[tuple[str, str, str, str], set[str]] = {}
    queue: list[Package] = []
    for seed in seed_specs:
        candidates = by_name.get(seed.name, [])
        if len(candidates) != 1:
            _fail(f"direct seed {seed.name} is missing or ambiguous")
        package = candidates[0]
        if package.arch not in ALLOWED_ARCHITECTURES:
            _fail(f"direct seed {seed.name} has an unsupported architecture {package.arch}")
        expected_version = locked_versions.get(seed.name)
        if expected_version is not None and package.version != expected_version:
            _fail(f"direct seed {seed.name} version differs from its exact lock")
        if package.key not in selected:
            selected[package.key] = package
            queue.append(package)
        direct_reasons.setdefault(package.key, set()).add(seed.reason)

    selected_edges: dict[tuple[str, str, str, str], list[dict[str, str]]] = {}
    visited: set[tuple[str, str, str, str]] = set()
    while queue:
        parent = queue.pop(0)
        if parent.key in visited:
            continue
        visited.add(parent.key)
        edges: list[dict[str, str]] = []
        for raw_dependency in parent.dependencies:
            dep_name, operator, dep_version = _parse_dependency(raw_dependency)
            all_candidates = _dependency_candidates(dep_name, operator, dep_version, by_name, by_provide, compare_versions)
            candidates = [item for item in all_candidates if item.arch in ALLOWED_ARCHITECTURES]
            if not candidates:
                wrong_arch = [item for item in all_candidates if item.arch not in ALLOWED_ARCHITECTURES]
                if wrong_arch:
                    _fail(f"dependency {raw_dependency} is only available for the wrong architecture")
                _fail(f"missing transitive dependency {raw_dependency} required by {parent.name}")
            unique = {item.key: item for item in candidates}
            if len(unique) != 1:
                _fail(f"ambiguous ALPM provider for dependency {raw_dependency}")
            package = next(iter(unique.values()))
            previously_selected = [item for item in selected.values() if item.name == package.name and item.key != package.key]
            if previously_selected:
                _fail(f"inconsistent selected version/repository for package {package.name}")
            if package.key not in selected:
                selected[package.key] = package
                queue.append(package)
            edges.append({
                "dependency": raw_dependency,
                "selected_repo": package.repo,
                "selected_name": package.name,
                "selected_version": package.version,
                "selected_arch": package.arch,
            })
        selected_edges[parent.key] = sorted(edges, key=lambda item: (item["dependency"], item["selected_repo"], item["selected_name"]))

    for package in selected.values():
        for raw_replace in package.replaces:
            replace_name, replace_operator, replace_version = _parse_dependency(raw_replace)
            replaced_selected = _dependency_candidates(
                replace_name, replace_operator, replace_version, by_name, by_provide, compare_versions
            )
            for other in selected.values():
                if other.key != package.key and other in replaced_selected:
                    _fail(f"selected package {package.name} replaces selected package {other.name}")
        for raw_conflict in package.conflicts:
            dep_name, operator, dep_version = _parse_dependency(raw_conflict)
            for other in selected.values():
                if other.key != package.key and other in _dependency_candidates(dep_name, operator, dep_version, by_name, by_provide, compare_versions):
                    _fail(f"selected packages conflict: {package.name} conflicts with {other.name}")

    rows: list[dict[str, object]] = []
    for package in sorted(selected.values(), key=lambda item: (item.repo, item.name, item.version, item.arch)):
        reasons = sorted(direct_reasons.get(package.key, set()))
        if reasons:
            root_reason: object = reasons
        else:
            parent_names = sorted(
                parent.name
                for parent in selected.values()
                if any(edge["selected_name"] == package.name for edge in selected_edges.get(parent.key, []))
            )
            root_reason = [f"dependency of {name}" for name in parent_names]
        row = package.as_metadata()
        row.update({
            "root_reason": root_reason,
            "dependency_selection": selected_edges.get(package.key, []),
        })
        rows.append(row)
    return tuple(rows)


def _require_locked_resolver(snapshot: Mapping[str, Any], resolver: VersionComparator | None) -> Mapping[str, object]:
    expected = snapshot.get("alpm_resolver")
    if not isinstance(expected, dict) or not expected:
        _unavailable(
            "UNAVAILABLE_NO_PINNED_ALPM_RESOLVER: snapshot.lock.json has no resolver identity; "
            "no independently authenticated Arch trust anchor, verified exact pacman archive/signature, "
            "or accepted vercmp executable/runtime identity is available"
        )
    if resolver is None or not isinstance(getattr(resolver, "identity", None), Mapping):
        _unavailable("UNAVAILABLE_NO_PINNED_ALPM_RESOLVER: no exact ALPM comparator was supplied")
    identity = dict(resolver.identity)
    if identity != expected:
        _fail("ALPM comparator identity differs from snapshot.lock.json")
    rootless = snapshot.get("rootless_builder")
    if not isinstance(rootless, dict):
        _fail("snapshot lock lacks the pinned pacman package identity")
    if (
        identity.get("pacman_version") != rootless.get("pacman_version")
        or identity.get("pacman_package_sha256") != rootless.get("pacman_package_sha256")
        or identity.get("pacman_signature_sha256") != rootless.get("pacman_signature_sha256")
    ):
        _fail("ALPM comparator is not bound to the exact pacman package pinned by snapshot.lock.json")
    return identity


def _build_full_lock(
    core_db: bytes,
    extra_db: bytes,
    snapshot_raw: bytes,
    resolver: VersionComparator | None,
    *,
    require_current_pin: bool = True,
) -> dict[str, object]:
    snapshot = _snapshot(snapshot_raw, require_current_pin=require_current_pin)
    identity = _require_locked_resolver(snapshot, resolver)
    if resolver is None:
        _unavailable("UNAVAILABLE_NO_PINNED_ALPM_RESOLVER: exact comparator is missing")
    expected_dbs = snapshot["repository_database_sha256"]
    if not isinstance(expected_dbs, dict):
        _fail("snapshot repository database hash map is malformed")
    packages = (
        *parse_repository_database("core", core_db, expected_dbs["core"]),
        *parse_repository_database("extra", extra_db, expected_dbs["extra"]),
    )
    target_seeds = _target_usb_profile_seeds(snapshot)
    _seed_metadata(snapshot, packages, WORKER_ROOT_SEEDS)
    _seed_metadata(snapshot, packages, target_seeds)
    worker_rows = resolve_packages(packages, WORKER_ROOT_SEEDS, offline_vm_worker.LOCKED_PACKAGES, resolver.compare)
    target_rows = resolve_packages(packages, target_seeds, {}, resolver.compare)

    archive_by_hash: dict[str, dict[str, object]] = {}
    for scope, rows in (("worker_root", worker_rows), ("target_usb_root", target_rows)):
        for row in rows:
            archive_hash = row.get("archive_sha256")
            filename = row.get("filename")
            if not isinstance(archive_hash, str) or not isinstance(filename, str):
                _fail("resolved package row lacks its physical archive identity")
            archive = archive_by_hash.get(archive_hash)
            if archive is None:
                archive = {
                    key: row[key]
                    for key in (
                        "repo", "name", "version", "arch", "filename", "size",
                        "database_sha256", "archive_sha256", "signature_blob_sha256",
                        "signature_verified", "dependencies", "provides", "conflicts", "replaces",
                    )
                }
                archive["included_by"] = []
                archive_by_hash[archive_hash] = archive
            elif any(
                archive.get(key) != row.get(key)
                for key in ("repo", "name", "version", "arch", "filename", "size", "database_sha256")
            ):
                _fail("one package archive hash is claimed by inconsistent package rows")
            included_by = archive.get("included_by")
            if not isinstance(included_by, list):
                _fail("internal physical archive scope map is malformed")
            if scope not in included_by:
                included_by.append(scope)
    physical_archives = sorted(archive_by_hash.values(), key=lambda item: str(item["filename"]))
    body: dict[str, object] = {
        "format": CLOSURE_FORMAT,
        "status": "METADATA_CLOSURES_COMPLETE_SIGNATURES_UNVERIFIED",
        "snapshot_date": "2026-09-30",
        "snapshot_lock_sha256": _sha256(snapshot_raw),
        "repository_databases": [
            {"repo": "core", "size": len(core_db), "sha256": _sha256(core_db)},
            {"repo": "extra", "size": len(extra_db), "sha256": _sha256(extra_db)},
        ],
        "resolver_identity": identity,
        "provider_ambiguity_policy": PROVIDER_AMBIGUITY_POLICY,
        "seed_policy": {
            "worker_root": [{"name": item.name, "reason": item.reason} for item in WORKER_ROOT_SEEDS],
            "target_usb_root": [{"name": item.name, "reason": item.reason} for item in target_seeds],
            "target_usb_profile_path": TARGET_USB_PROFILE_RELATIVE_PATH,
            "target_usb_profile_sha256": TARGET_USB_PROFILE_SHA256,
            "archiso_is_worker_build_host_only": True,
        },
        "closures": {
            "worker_root": {"direct_seed_count": len(WORKER_ROOT_SEEDS), "packages": list(worker_rows)},
            "target_usb_root": {"direct_seed_count": len(target_seeds), "packages": list(target_rows)},
        },
        "physical_archive_inventory": physical_archives,
        "signature_verification": {
            "status": "NOT_VERIFIED",
            "verified": False,
            "reason": "package signature blobs are identified but no pinned Arch keyring verification was run",
        },
    }
    body["package_closure_sha256"] = _sha256(canonical_json(body))
    return body


def generate_package_closure_lock(
    core_db: bytes,
    extra_db: bytes,
    snapshot_raw: bytes,
    resolver: VersionComparator | None,
) -> dict[str, object]:
    """Generate only if an exact resolver identity is pinned in the snapshot."""
    return _build_full_lock(core_db, extra_db, snapshot_raw, resolver)


def validate_package_closure_lock(
    value: object,
    core_db: bytes,
    extra_db: bytes,
    snapshot_raw: bytes,
    resolver: VersionComparator | None,
) -> dict[str, object]:
    """Reparse both DBs and independently recompute every selected row."""
    expected = generate_package_closure_lock(core_db, extra_db, snapshot_raw, resolver)
    if not isinstance(value, dict) or canonical_json(value) != canonical_json(expected):
        _fail("package closure differs from independently recomputed pinned database resolution")
    return expected


def _build_synthetic_closure_for_tests(
    core_db: bytes,
    extra_db: bytes,
    snapshot_raw: bytes,
    resolver: VersionComparator,
) -> dict[str, object]:
    """Use only from synthetic tests with an explicitly fake snapshot."""
    return _build_full_lock(core_db, extra_db, snapshot_raw, resolver, require_current_pin=False)


def _validate_synthetic_closure_for_tests(
    value: object,
    core_db: bytes,
    extra_db: bytes,
    snapshot_raw: bytes,
    resolver: VersionComparator,
) -> dict[str, object]:
    expected = _build_synthetic_closure_for_tests(core_db, extra_db, snapshot_raw, resolver)
    if not isinstance(value, dict) or canonical_json(value) != canonical_json(expected):
        _fail("package closure differs from independently recomputed synthetic DB resolution")
    return expected


def write_seed_inventory(path: Path, inventory: Mapping[str, object]) -> None:
    """Create one private, fsynced provisional inventory file without replace."""
    if inventory.get("format") != SEED_INVENTORY_FORMAT or inventory.get("status") != "SEED_POLICY_COMPLETE_CLOSURE_UNAVAILABLE":
        _fail("only the metadata-only seed policy with unavailable closures may be written by this phase")
    if not path.is_absolute() or path.exists() or path.is_symlink():
        _fail("seed inventory output must be an absolute new path")
    parent = path.parent
    parent_info = parent.lstat()
    if not stat.S_ISDIR(parent_info.st_mode):
        _fail("seed inventory output parent is not a real directory")
    ancestor = parent
    while True:
        ancestor_info = ancestor.lstat()
        if stat.S_ISLNK(ancestor_info.st_mode):
            _fail("seed inventory output has a symlinked parent component")
        if ancestor.parent == ancestor:
            break
        ancestor = ancestor.parent
    raw = canonical_json(inventory) + b"\n"
    if len(raw) > MAX_OUTPUT_BYTES:
        _fail("seed inventory exceeds the fixed output bound")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = -1
    info: os.stat_result | None = None
    try:
        fd = os.open(path, flags, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            _fail("seed inventory output is not a single-link regular file")
        _fchmod(fd, 0o600)
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                _fail("seed inventory output write made no progress")
            view = view[written:]
        os.fsync(fd)
        os.close(fd)
        fd = -1
        dir_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
        try:
            current_parent = os.fstat(dir_fd)
            if (parent_info.st_dev, parent_info.st_ino) != (current_parent.st_dev, current_parent.st_ino):
                _fail("seed inventory output parent changed during publication")
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException as original:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        cleanup_error: BaseException | None = None
        try:
            current = path.lstat()
            if info is not None and (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino):
                path.unlink()
                dir_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
        except BaseException as exc:
            cleanup_error = exc
        if cleanup_error is not None:
            raise PackageClosureError(
                f"seed inventory publication failed and cleanup is uncertain; inspect {path}: {cleanup_error}"
            ) from original
        raise


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--core-db", type=Path, required=True)
    parser.add_argument("--extra-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    inventory = build_seed_inventory_from_paths(args.snapshot, args.core_db, args.extra_db)
    write_seed_inventory(args.output, inventory)
    direct_sizes = inventory.get("direct_seed_archive_bytes")
    unique_size = direct_sizes.get("unique_union") if isinstance(direct_sizes, dict) else "invalid"
    print(f"{inventory['status']} unique_direct_seed_archive_bytes={unique_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
