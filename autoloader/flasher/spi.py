"""Read-only SPI probe transport and typed physical chip observations.

The transport can issue only version, probe and read commands. All write and
flash-protection command paths are rejected by :class:`FlashromRunner`.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .flashrom import (
    FlashResult,
    FlashToolIdentity,
    FlashromRunner,
    ProgrammerType,
    ProbeOutputError,
    parse_probe_output,
)


class SPIOperationError(RuntimeError):
    """A safe probe/read request could not produce verified evidence."""


_VERSION_ONLY = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.-]+)?$")
_SHA256_ONLY = re.compile(r"^[0-9a-f]{64}$")
_PACKAGE_OWNER_ONLY = re.compile(r"^(?:pacman|dpkg): [^\r\n]+$")


@dataclass(frozen=True)
class SPITransportResult:
    success: bool
    output: str
    duration_seconds: float


@dataclass(frozen=True)
class ChipProbeEvidence:
    """Chip details parsed from one raw, unforced flashrom observation."""

    probe_id: str
    programmer: ProgrammerType
    tool: FlashToolIdentity
    chip_name: str
    capacity_bytes: int
    reported_programmer: str
    raw_output: str
    raw_output_sha256: str
    observed_at_utc: str

    @classmethod
    def from_output(
        cls,
        *,
        programmer: ProgrammerType,
        tool: FlashToolIdentity,
        output: str,
    ) -> ChipProbeEvidence:
        try:
            chip_name, capacity_bytes, reported_programmer = parse_probe_output(output)
        except ProbeOutputError as exc:
            raise SPIOperationError(f"probe result is unknown or ambiguous: {exc}") from exc
        if _programmer_key(programmer.value) != _programmer_key(reported_programmer):
            raise SPIOperationError("probe output names a different programmer than the requested interface")
        digest = hashlib.sha256(output.encode("utf-8", errors="replace")).hexdigest()
        return cls(
            probe_id=str(uuid.uuid4()),
            programmer=programmer,
            tool=tool,
            chip_name=chip_name,
            capacity_bytes=capacity_bytes,
            reported_programmer=reported_programmer,
            raw_output=output,
            raw_output_sha256=digest,
            observed_at_utc=datetime.now(timezone.utc).isoformat(),
        )

    def validate_integrity(self) -> None:
        """Reparse the retained raw output before using any chip selector/data."""
        try:
            uuid.UUID(self.probe_id)
            observed_at = datetime.fromisoformat(self.observed_at_utc)
            name, capacity, reported_programmer = parse_probe_output(self.raw_output)
        except (ValueError, ProbeOutputError) as exc:
            raise SPIOperationError("probe evidence is malformed or ambiguous") from exc
        digest = hashlib.sha256(self.raw_output.encode("utf-8", errors="replace")).hexdigest()
        if (
            observed_at.tzinfo is None
            or digest != self.raw_output_sha256
            or name != self.chip_name
            or capacity != self.capacity_bytes
            or reported_programmer != self.reported_programmer
            or _programmer_key(self.programmer.value) != _programmer_key(reported_programmer)
            or not _valid_tool_identity(self.tool)
        ):
            raise SPIOperationError("probe evidence fields do not match the raw observation")


def _programmer_key(value: str) -> str:
    return value.strip().split(":", 1)[0].strip().casefold()


def _valid_tool_identity(tool: FlashToolIdentity) -> bool:
    if (
        tool.name.casefold() not in {"flashrom", "flashprog"}
        or not _VERSION_ONLY.fullmatch(tool.version)
        or not Path(tool.executable).is_absolute()
        or not _SHA256_ONLY.fullmatch(tool.sha256)
        or not _PACKAGE_OWNER_ONLY.fullmatch(tool.package_owner)
    ):
        return False
    match = re.search(r"\b(flashrom|flashprog)\s+v?([0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.-]+)?)\b", tool.raw_version, re.I)
    return bool(match and match.group(1).casefold() == tool.name.casefold() and match.group(2) == tool.version)


class ReadOnlySPITransport(Protocol):
    def inspect_tool(self) -> FlashToolIdentity | None: ...

    def probe(self, programmer: ProgrammerType) -> SPITransportResult: ...

    def read_chip(
        self,
        programmer: ProgrammerType,
        probe: ChipProbeEvidence,
        destination: Path,
    ) -> SPITransportResult: ...


class FlashromReadOnlyTransport:
    """Production read-only adapter. It is never called by the CLI/TUI today."""

    def __init__(self, runner: FlashromRunner | None = None) -> None:
        self.runner = runner or FlashromRunner()

    def inspect_tool(self) -> FlashToolIdentity | None:
        return self.runner.inspect_tool()

    def probe(self, programmer: ProgrammerType) -> SPITransportResult:
        success, output, duration = self.runner.run_command(["-p", programmer.value])
        return SPITransportResult(success, output, duration)

    def read_chip(
        self,
        programmer: ProgrammerType,
        probe: ChipProbeEvidence,
        destination: Path,
    ) -> SPITransportResult:
        probe.validate_integrity()
        if probe.programmer is not programmer:
            raise SPIOperationError("read programmer differs from the captured probe evidence")
        result: FlashResult = self.runner.read_rom(
            programmer,
            str(destination),
            chip_name=probe.chip_name,
        )
        return SPITransportResult(result.success, result.log_output, result.duration)


class SPIProbeService:
    """Collect a strict probe record without accepting a chip-name override."""

    def __init__(self, transport: ReadOnlySPITransport) -> None:
        self.transport = transport

    def probe(self, programmer: ProgrammerType) -> ChipProbeEvidence:
        tool = self.transport.inspect_tool()
        if tool is None or not _valid_tool_identity(tool):
            raise SPIOperationError("a discoverable flashrom/flashprog executable and exact version are required")
        result = self.transport.probe(programmer)
        if not result.success:
            raise SPIOperationError(f"read-only probe failed: {result.output}")
        return ChipProbeEvidence.from_output(
            programmer=programmer,
            tool=tool,
            output=result.output,
        )
