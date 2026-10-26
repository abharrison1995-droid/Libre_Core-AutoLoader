"""
Flashrom/Flashprog wrapper.
"""
import hashlib
import os
import stat
import subprocess
import logging
import re
import shutil
import sys
from enum import Enum
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List, Tuple
import time

from pathlib import Path

from autoloader.config import FLASH_TIMEOUT

logger = logging.getLogger(__name__)

class ProgrammerType(Enum):
    INTERNAL = "internal"
    CH341A = "ch341a_spi"
    RASPBERRY_PI = "linux_spi:dev=/dev/spidev0.0,spispeed=1000"
    BUS_PIRATE = "buspirate_spi:dev=/dev/ttyUSB0"

@dataclass
class FlashResult:
    success: bool
    chip_name: Optional[str]
    chip_size: Optional[int]
    duration: float
    log_output: str
    code: str = "OK"

    @property
    def output(self) -> str:
        return self.log_output


@dataclass(frozen=True)
class FlashToolIdentity:
    """Trusted executable hash/provenance plus version observed by the runner."""

    name: str
    executable: str
    version: str
    raw_version: str
    observed_at_utc: str
    sha256: str = ""
    package_owner: str = ""


@dataclass(frozen=True)
class _FileFingerprint:
    path: str
    sha256: str
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    mode: int
    uid: int
    gid: int


@dataclass(frozen=True)
class _TrustedExecutable:
    file: _FileFingerprint
    package_owner: str


class ProbeOutputError(ValueError):
    """A flashrom probe did not identify exactly one unambiguous chip."""


def _trusted_root_owned_stat(info: os.stat_result, *, directory: bool) -> bool:
    """Require Linux system ownership and no group/other write access."""
    expected_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not expected_type or info.st_uid != 0 or info.st_mode & 0o022:
        return False
    return directory or bool(info.st_mode & 0o111)


def _same_file_stat(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_size,
        left.st_mtime_ns,
        left.st_ctime_ns,
        left.st_mode,
        left.st_uid,
        left.st_gid,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_size,
        right.st_mtime_ns,
        right.st_ctime_ns,
        right.st_mode,
        right.st_uid,
        right.st_gid,
    )


def _fingerprint_trusted_linux_file(path: str) -> _FileFingerprint | None:
    """Hash one root-owned executable below root-owned, non-writable directories.

    This policy intentionally supports Linux system tools only. It does not
    establish trust against a malicious root user or compromised kernel.
    """
    if not sys.platform.startswith("linux") or not os.path.isabs(path):
        return None
    try:
        resolved = Path(path).resolve(strict=True)
        if str(resolved) != path:
            return None

        # Every directory component must be protected from non-root replacement.
        for directory_path in reversed(resolved.parents):
            directory_info = directory_path.lstat()
            if stat.S_ISLNK(directory_info.st_mode) or not _trusted_root_owned_stat(
                directory_info, directory=True
            ):
                return None
        file_info = resolved.lstat()
        if stat.S_ISLNK(file_info.st_mode) or not _trusted_root_owned_stat(file_info, directory=False):
            return None

        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(str(resolved), flags)
        try:
            opened_info = os.fstat(descriptor)
            if not _same_file_stat(file_info, opened_info):
                return None
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            final_info = os.fstat(descriptor)
            if not _same_file_stat(opened_info, final_info):
                return None
        finally:
            os.close(descriptor)
    except OSError:
        return None
    return _FileFingerprint(
        path=str(resolved),
        sha256=digest.hexdigest(),
        device=int(final_info.st_dev),
        inode=int(final_info.st_ino),
        size=int(final_info.st_size),
        mtime_ns=int(final_info.st_mtime_ns),
        ctime_ns=int(final_info.st_ctime_ns),
        mode=int(final_info.st_mode),
        uid=int(final_info.st_uid),
        gid=int(final_info.st_gid),
    )


def _run_trusted_package_query(manager: str, args: list[str]) -> subprocess.CompletedProcess[str] | None:
    """Run a fixed package-manager query only while its own bytes are stable."""
    before = _fingerprint_trusted_linux_file(manager)
    if before is None:
        return None
    try:
        result = subprocess.run(
            [manager, *args],
            cwd=os.path.abspath(os.sep),
            env={"LC_ALL": "C"},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            shell=False,
            close_fds=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    after = _fingerprint_trusted_linux_file(manager)
    return result if after == before else None


def _linux_package_owner(executable: str) -> str | None:
    """Return manager-backed ownership for an exact system executable path."""
    pacman = "/usr/bin/pacman"
    pacman_result = _run_trusted_package_query(pacman, ["-Qo", "--", executable])
    if pacman_result is not None and pacman_result.returncode == 0:
        prefix = f"{executable} is owned by "
        lines = pacman_result.stdout.splitlines()
        if len(lines) == 1 and lines[0].startswith(prefix):
            owner = lines[0][len(prefix):].strip()
            if owner and not any(ord(character) < 32 for character in owner):
                return f"pacman: {owner}"

    dpkg = "/usr/bin/dpkg-query"
    dpkg_result = _run_trusted_package_query(dpkg, ["-S", "--", executable])
    if dpkg_result is not None and dpkg_result.returncode == 0:
        matches = []
        for line in dpkg_result.stdout.splitlines():
            package, separator, owned_path = line.partition(": ")
            if separator and owned_path == executable and package.strip():
                matches.append(line.strip())
        if len(matches) == 1 and not any(ord(character) < 32 for character in matches[0]):
            return f"dpkg: {matches[0]}"
    return None


def _trusted_executable_provenance(path: str) -> _TrustedExecutable | None:
    """Accept only root-protected Linux executables owned by pacman or dpkg.

    PATH is discovery input only. Windows and systems without a trusted local
    package ownership query remain unavailable unless a separate explicit
    trusted pin/policy is introduced.
    """
    before = _fingerprint_trusted_linux_file(path)
    if before is None:
        return None
    package_owner = _linux_package_owner(path)
    after = _fingerprint_trusted_linux_file(path)
    if package_owner is None or before != after:
        return None
    return _TrustedExecutable(file=after, package_owner=package_owner)


_CHIP_LINE = re.compile(
    r'^\s*Found\s+.+?\s+flash chip\s+["\']([^"\']+)["\']\s+'
    r'\(\s*([0-9]+)\s+kB\s*,\s*SPI\s*\)\s+on\s+(.+?)\.?\s*$',
    re.IGNORECASE,
)
_TOOL_VERSION = re.compile(
    r'\b(flashrom|flashprog)\s+v?([0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.-]+)?)\b',
    re.IGNORECASE,
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PACKAGE_OWNER = re.compile(r"^(?:pacman|dpkg): [^\r\n]+$")
_PROGRAMMER_VALUES = frozenset(programmer.value for programmer in ProgrammerType)
_CHIP_SELECTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_REGION_SELECTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_READ_ONLY_REJECTION = (
    "Rejected: only exact flashrom version, probe, read, and verify command forms are permitted."
)


def _safe_path_argument(value: str) -> bool:
    return (
        bool(value)
        and not value.startswith("-")
        and all(ord(character) >= 32 and ord(character) != 127 for character in value)
    )


def _is_allowed_read_only_command(args: object) -> bool:
    """Accept only the exact argument shapes built by this module's read APIs."""
    if not isinstance(args, list) or not args or not all(isinstance(arg, str) for arg in args):
        return False
    values = list(args)
    if values == ["--version"]:
        return True
    if len(values) < 2 or values[0] != "-p" or values[1] not in _PROGRAMMER_VALUES:
        return False
    if len(values) == 2:
        return True

    index = 2
    if values[index] == "-c":
        if index + 1 >= len(values) or not _CHIP_SELECTOR.fullmatch(values[index + 1]):
            return False
        index += 2
    if index < len(values) and values[index] == "--ifd":
        if (
            index + 2 >= len(values)
            or values[index + 1] != "-i"
            or not _REGION_SELECTOR.fullmatch(values[index + 2])
        ):
            return False
        index += 3
    return (
        index + 2 == len(values)
        and values[index] in {"-r", "-v"}
        and _safe_path_argument(values[index + 1])
    )


def parse_probe_output(output: str) -> tuple[str, int, str]:
    """Parse exactly one standard flashrom/flashprog SPI chip result.

    Caller-supplied chip selectors are deliberately not part of this parser:
    the name and capacity must come from the tool's raw observation.
    """
    matches = []
    for line in output.splitlines():
        if "flash chip" not in line.casefold():
            continue
        match = _CHIP_LINE.fullmatch(line)
        if match is None:
            raise ProbeOutputError("probe output contains a malformed flash-chip result")
        matches.append(match)
    if len(matches) != 1:
        raise ProbeOutputError(
            "probe output must contain exactly one recognized flash-chip result; "
            f"found {len(matches)}"
        )
    match = matches[0]
    name = match.group(1).strip()
    capacity_bytes = int(match.group(2)) * 1024
    reported_programmer = match.group(3).strip().rstrip(".").strip()
    if not name or capacity_bytes <= 0 or not reported_programmer:
        raise ProbeOutputError("probe result has an empty chip, capacity, or programmer field")
    return name, capacity_bytes, reported_programmer

class FlashromRunner:
    def __init__(self):
        self._tool_name: str | None = None
        self._trusted_executable: _TrustedExecutable | None = None
        self._tool_path = self.detect_tool()

    @property
    def tool_path(self) -> str | None:
        """Executable resolved once so probing and identity use the same path."""
        return self._tool_path

    def detect_tool(self) -> str | None:
        # PATH is discovery input only. A candidate must also pass Linux owner,
        # mode, parent-path, hash, and package-owner checks before use.
        for candidate in ("flashprog", "flashrom"):
            found = shutil.which(candidate)
            if found is None:
                continue
            try:
                resolved = Path(found).resolve(strict=True)
            except OSError:
                continue
            executable = str(resolved)
            provenance = _trusted_executable_provenance(executable)
            if provenance is None:
                continue
            self._tool_name = candidate
            self._trusted_executable = provenance
            return executable
        return None

    def _trusted_tool_unchanged(self) -> bool:
        expected = self._trusted_executable
        executable = self.tool_path
        if expected is None or executable is None:
            return False
        current = _trusted_executable_provenance(executable)
        return current == expected

    def inspect_tool(self) -> Optional[FlashToolIdentity]:
        """Return an exact tool/version record, or None when unavailable/ambiguous."""
        executable = self.tool_path
        provenance = self._trusted_executable
        if (
            executable is None
            or provenance is None
            or not Path(executable).is_absolute()
            or provenance.file.path != executable
            or not self._trusted_tool_unchanged()
        ):
            return None
        candidate = self._tool_name
        if candidate not in {"flashrom", "flashprog"}:
            return None
        try:
            result = subprocess.run(
                [executable, "--version"],
                cwd=os.path.abspath(os.sep),
                env={},
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
                shell=False,
                close_fds=True,
            )
        except (OSError, subprocess.TimeoutExpired):
            self._trusted_tool_unchanged()
            return None
        if not self._trusted_tool_unchanged():
            return None
        raw = f"{result.stdout}\n{result.stderr}".strip()
        match = _TOOL_VERSION.search(raw)
        if result.returncode != 0 or match is None or match.group(1).casefold() != candidate:
            return None
        return FlashToolIdentity(
            name=candidate,
            executable=executable,
            version=match.group(2),
            raw_version=raw,
            observed_at_utc=datetime.now(timezone.utc).isoformat(),
            sha256=provenance.file.sha256,
            package_owner=provenance.package_owner,
        )

    def run_command(self, args: List[str], timeout: int = FLASH_TIMEOUT) -> Tuple[bool, str, float]:
        if not _is_allowed_read_only_command(args):
            return False, _READ_ONLY_REJECTION, 0.0
        executable = self.tool_path
        if (
            executable is None
            or not Path(executable).is_absolute()
            or not self._trusted_tool_unchanged()
        ):
            return False, "Flashrom/flashprog executable is unavailable or lacks trusted, unchanged system provenance.", 0.0
        start = time.time()
        cmd = [executable, *args]
        logger.info(f"Running command: {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd,
                cwd=os.path.abspath(os.sep),
                env={},
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
                shell=False,
                close_fds=True,
            )
            duration = time.time() - start
            if not self._trusted_tool_unchanged():
                return False, "Flashrom/flashprog executable identity changed during the command.", duration
            success = result.returncode == 0
            log_output = result.stdout + "\n" + result.stderr
            return success, log_output, duration
        except subprocess.TimeoutExpired:
            duration = time.time() - start
            if not self._trusted_tool_unchanged():
                return False, "Flashrom/flashprog executable identity changed during the command.", duration
            err_msg = f"Flashrom command timed out after {timeout}s: {' '.join(cmd)}"
            logger.error(err_msg)
            return False, err_msg, duration
        except Exception as e:
            self._trusted_tool_unchanged()
            logger.error(f"Failed to run command: {e}")
            return False, str(e), time.time() - start

    def parse_chip_info(self, output: str) -> Tuple[Optional[str], Optional[int]]:
        try:
            name, capacity, _reported_programmer = parse_probe_output(output)
            return name, capacity
        except ProbeOutputError:
            return None, None

    def probe_chip(self, programmer: ProgrammerType, chip_name: Optional[str] = None) -> FlashResult:
        if chip_name is not None:
            return FlashResult(
                False, None, None, 0.0,
                "User-supplied chip names are not observations; probe without a chip override.",
                "PROBE_OVERRIDE_REJECTED",
            )
        args = ["-p", programmer.value]
        success, output, duration = self.run_command(args)
        try:
            parsed_name, chip_size, reported_programmer = parse_probe_output(output)
        except ProbeOutputError as exc:
            return FlashResult(False, None, None, duration, f"{output}\nProbe rejected: {exc}", "PROBE_UNKNOWN")
        expected_programmer = programmer.value.split(":", 1)[0].casefold()
        reported_base = reported_programmer.split(":", 1)[0].casefold()
        if expected_programmer != reported_base:
            return FlashResult(
                False, None, None, duration,
                f"{output}\nProbe programmer does not match the requested interface.",
                "PROBE_PROGRAMMER_MISMATCH",
            )
        return FlashResult(success, parsed_name, chip_size, duration, output, "OK" if success else "PROBE_FAILED")

    def read_rom(
        self,
        programmer: ProgrammerType,
        output_path: str,
        chip_name: Optional[str] = None,
        layout_region: Optional[str] = None,
        extra_args: Optional[List[str]] = None
    ) -> FlashResult:
        if extra_args:
            return FlashResult(
                False, None, None, 0.0,
                "Caller-supplied flashrom options are disabled for read operations.",
                "UNSUPPORTED_ARGUMENTS",
            )
        target_path = Path(output_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        args = ["-p", programmer.value]
        if chip_name:
            args.extend(["-c", chip_name])
        if layout_region:
            args.extend(["--ifd", "-i", layout_region])
        args.extend(["-r", str(target_path)])
        success, output, duration = self.run_command(args)
        parsed_name, chip_size = self.parse_chip_info(output)
        code = "OK" if success else "READ_ONLY_COMMAND_REJECTED" if output.startswith("Rejected:") else "READ_FAILED"
        return FlashResult(success, chip_name or parsed_name, chip_size, duration, output, code)

    def write_rom(
        self,
        programmer: ProgrammerType,
        rom_path: str,
        chip_name: Optional[str] = None,
        layout_region: Optional[str] = None,
        extra_args: Optional[List[str]] = None
    ) -> FlashResult:
        source_path = Path(rom_path)
        if not source_path.is_file():
            err_msg = f"ROM file not found for writing: {rom_path}"
            logger.error(err_msg)
            return FlashResult(False, None, None, 0.0, err_msg, "ROM_NOT_FOUND")

        del programmer, chip_name, layout_region, extra_args
        err_msg = "Physical ROM writes are disabled; no write command was issued."
        logger.warning(err_msg)
        return FlashResult(False, None, None, 0.0, err_msg, "PHYSICAL_WRITES_DISABLED")

    def verify_rom(
        self,
        programmer: ProgrammerType,
        rom_path: str,
        chip_name: Optional[str] = None,
        layout_region: Optional[str] = None,
        extra_args: Optional[List[str]] = None
    ) -> FlashResult:
        if extra_args:
            return FlashResult(
                False, None, None, 0.0,
                "Caller-supplied flashrom options are disabled for verify operations.",
                "UNSUPPORTED_ARGUMENTS",
            )
        source_path = Path(rom_path)
        if not source_path.is_file():
            err_msg = f"ROM file not found for verification: {rom_path}"
            logger.error(err_msg)
            return FlashResult(False, None, None, 0.0, err_msg)
        
        args = ["-p", programmer.value]
        if chip_name:
            args.extend(["-c", chip_name])
        if layout_region:
            args.extend(["--ifd", "-i", layout_region])
        args.extend(["-v", str(source_path)])
        success, output, duration = self.run_command(args)
        parsed_name, chip_size = self.parse_chip_info(output)
        code = "OK" if success else "READ_ONLY_COMMAND_REJECTED" if output.startswith("Rejected:") else "VERIFY_FAILED"
        return FlashResult(success, chip_name or parsed_name, chip_size, duration, output, code)
    
    def get_chip_info(self, programmer: ProgrammerType) -> Tuple[Optional[str], Optional[int]]:
        res = self.probe_chip(programmer)
        return res.chip_name, res.chip_size
