"""
Central Orchestrator — wires domain logic to Textual TUI worker threads.

All heavy operations (backup, build, verify) accept callable progress/log
callbacks that the TUI screens register before kicking off work.  This keeps
domain code decoupled from Textual-specific threading primitives.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from autoloader.exceptions import (
    AutoLoaderError,
    BackupUnavailableError,
    PhysicalFlashUnavailableError,
    ROMBuildUnavailableError,
)
from autoloader.flasher.backup import BackupResult
from autoloader.flasher.flashrom import ProgrammerType
from autoloader.detection.model import DetectionResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Typed callback aliases
# ---------------------------------------------------------------------------
LogCallback = Callable[[str], None]          # rich-markup string
ProgressCallback = Callable[[int, str], None]  # (pct 0-100, label)


# ---------------------------------------------------------------------------
# Shared application state (passed around / stored on AutoLoaderApp)
# ---------------------------------------------------------------------------
@dataclass
class AppState:
    """Mutable, shared application state owned by AutoLoaderApp."""
    model_id: Optional[str] = None
    detected: Optional[DetectionResult] = None
    programmer: ProgrammerType = ProgrammerType.INTERNAL
    backup_dir: Path = field(default_factory=lambda: Path("backups"))
    output_dir: Path = field(default_factory=lambda: Path("out"))
    last_backup_result: Optional[BackupResult] = None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
class Orchestrator:
    """
    Runs blocking domain operations in the calling thread (must be a
    Textual @work(thread=True) worker — never the UI thread).

    Usage::

        orch = Orchestrator(state, on_log=..., on_progress=..., on_complete=..., on_error=...)
        orch.run_backup()
    """

    def __init__(
        self,
        state: AppState,
        *,
        on_log: Optional[LogCallback] = None,
        on_progress: Optional[ProgressCallback] = None,
        on_complete: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._state = state
        self._on_log = on_log or (lambda msg: None)
        self._on_progress = on_progress or (lambda pct, lbl: None)
        self._on_complete = on_complete or (lambda: None)
        self._on_error = on_error or (lambda msg: None)
        self._cancelled = threading.Event()

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------
    def cancel(self) -> None:
        """Signal cancellation to any in-progress operation."""
        self._cancelled.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _log(self, msg: str) -> None:
        logger.info(msg)
        self._on_log(msg)

    def _progress(self, pct: int, label: str) -> None:
        self._on_progress(pct, label)

    def _abort_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise AutoLoaderError("Operation cancelled by user.")

    # ------------------------------------------------------------------
    # Backup workflow
    # ------------------------------------------------------------------
    def run_backup(self) -> Optional[BackupResult]:
        """
        Fail closed until observed topology and complete recovery coverage exist.

        The low-level BackupManager remains available for unit testing, but a
        model database entry alone cannot establish that a physical backup is
        complete or suitable for recovery.
        """
        model_id = self._state.model_id
        if not model_id:
            self._on_error("No model selected. Cannot perform backup without knowing the target chip.")
            return None
        error = BackupUnavailableError(
            "Backup capture is unavailable until observed chip topology and complete "
            "recovery coverage are validated. No hardware was read."
        )
        self._log(f"[yellow]{error.code}: {error}[/yellow]")
        self._progress(0, "Backup unavailable; no hardware was read.")
        self._on_error(f"[{error.code}] {error}")
        return None

    # ------------------------------------------------------------------
    # Build ROM workflow
    # ------------------------------------------------------------------
    def run_build(self) -> bool:
        """
        Report the build as unavailable until a validated builder is connected.

        This method intentionally does not emit progress completion or a ROM
        path: no firmware build is currently wired into the orchestrator.
        """
        error = ROMBuildUnavailableError(
            "ROM building is unavailable: no validated firmware builder is connected. No ROM was produced."
        )
        self._log(f"[yellow]{error.code}: {error}[/yellow]")
        self._progress(0, "Build unavailable; no ROM was produced.")
        self._on_error(f"[{error.code}] {error}")
        return False

    def run_flash(self, rom_path: Path) -> bool:
        """Fail closed while no model-validated, authorized writer is connected."""
        error = PhysicalFlashUnavailableError(
            f"Physical flashing is unavailable for {rom_path}: the shared model-specific "
            "safety and authorization backend is not implemented; no write was attempted."
        )
        self._log(f"[yellow]{error.code}: {error}[/yellow]")
        self._progress(0, "Flash unavailable; no write was attempted.")
        self._on_error(f"[{error.code}] {error}")
        return False

    # ------------------------------------------------------------------
    # Verify workflow
    # ------------------------------------------------------------------
    def run_verify(self, rom_path: Path, programmer: Optional[ProgrammerType] = None) -> bool:
        """Keep the legacy API fail-closed; no direct SPI read is permitted."""
        message = f"Read-back verification is unavailable for {rom_path}; no SPI read was attempted."
        self._log(f"[yellow]READBACK_NOT_PERFORMED: {message}[/yellow]")
        self._progress(0, "Read-back unavailable; not performed.")
        self._on_error(f"[READBACK_NOT_PERFORMED] {message}")
        return False
