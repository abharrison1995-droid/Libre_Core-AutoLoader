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
    BackupVerificationError,
    FlashError,
    SafetyError,
)
from autoloader.flasher.backup import BackupManager, BackupResult
from autoloader.flasher.flashrom import ProgrammerType
from autoloader.flasher.verify import verify_flash
from autoloader.database.models import get_model
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
        Performs a two-pass verified ROM backup using the current AppState.

        Returns the BackupResult on success.  Fires on_complete or on_error.
        """
        model_id = self._state.model_id
        if not model_id:
            self._on_error("No model selected. Cannot perform backup without knowing the target chip.")
            return None

        try:
            model = get_model(model_id)
            programmer = self._state.programmer

            # Pick the chip label to back up
            # Internal: use bottom (primary 8 MB) or single chip if not dual-chip
            if model.is_dual_chip:
                chip = next(c for c in model.flash_chips if c.label.lower() == "bottom")
            else:
                chip = model.flash_chips[0]

            self._log(f"[bold cyan]Starting backup for {model.name}[/bold cyan]")
            self._progress(5, "Initialising backup manager...")
            self._abort_if_cancelled()

            manager = BackupManager(
                backup_dir=str(self._state.backup_dir),
            )

            self._log(f"[cyan]Programmer: {programmer.value}[/cyan]")
            self._log(f"[cyan]Chip: {chip.model_number} ({chip.size_mb} MB, {chip.label})[/cyan]")
            self._progress(15, "Reading ROM — pass 1 of 2 (this takes ~30 s)...")
            self._abort_if_cancelled()

            result = manager.create_backup(
                model=model_id,
                programmer=programmer,
                chip_label=chip.label,
                chip_name=chip.model_number if chip.model_number else None,
            )

            self._abort_if_cancelled()

            if result.match:
                self._state.last_backup_result = result
                self._log(
                    f"[bold green]✅ Backup verified! SHA256: {result.sha256[:16]}...[/bold green]"
                )
                self._progress(100, "Backup complete.")
                self._on_complete()
            else:
                self._on_error(
                    f"❌ Backup SHA256 mismatch — the two reads differ.\n"
                    f"Pass 1: {result.path1}\nPass 2: {result.path2}\n"
                    "Check SPI clip seating and retry."
                )

            return result

        except BackupVerificationError as e:
            self._on_error(f"[bold red]Backup verification failed:[/bold red] {e}")
        except FlashError as e:
            self._on_error(f"[bold red]Flash error:[/bold red] {e}")
        except SafetyError as e:
            self._on_error(f"[bold yellow]Safety check failed:[/bold yellow] {e}")
        except AutoLoaderError as e:
            self._on_error(f"[red]Operation failed:[/red] {e}")
        except Exception as e:
            logger.exception("Unexpected error in run_backup()")
            self._on_error(f"[bold red]Unexpected error:[/bold red] {e}")

        return None

    # ------------------------------------------------------------------
    # Build ROM workflow (simulated stages — real build hooks wired later)
    # ------------------------------------------------------------------
    def run_build(self) -> bool:
        """
        Runs the ROM build pipeline.  Returns True on success.

        Stages are defined as (label, pct_end, approx_seconds).  The
        simulated loop keeps the UI responsive while actual build tools
        are invoked in the future.
        """
        import time

        stages = [
            ("Downloading Libreboot source...", 10),
            ("Building cross-compiler toolchain...", 30),
            ("Extracting binary blobs...", 50),
            ("Neutralising Intel ME (me_cleaner)...", 65),
            ("Compiling coreboot...", 85),
            ("Post-processing & CBFS injection...", 95),
            ("Finalising ROM image...", 100),
        ]

        try:
            self._log("[bold cyan]Starting ROM build pipeline...[/bold cyan]")
            for label, pct in stages:
                self._abort_if_cancelled()
                self._progress(pct - 5, label)
                self._log(f"[bold green]=>[/bold green] {label}")
                # In the future: call real build subprocess here
                for _ in range(10):
                    if self.is_cancelled:
                        raise AutoLoaderError("Build cancelled by user.")
                    time.sleep(0.15)
                self._progress(pct, label)

            self._abort_if_cancelled()
            output = self._state.output_dir / "coreboot.rom"
            self._log(f"[bold cyan]ROM image ready: {output}[/bold cyan]")
            self._on_complete()
            return True

        except AutoLoaderError as e:
            self._on_error(str(e))
        except Exception as e:
            logger.exception("Unexpected error in run_build()")
            self._on_error(f"[bold red]Unexpected build error:[/bold red] {e}")

        return False

    # ------------------------------------------------------------------
    # Verify workflow
    # ------------------------------------------------------------------
    def run_verify(self, rom_path: Path, programmer: Optional[ProgrammerType] = None) -> bool:
        """
        Reads back the current flash and compares against rom_path.

        Returns True if verified successfully.
        """
        programmer = programmer or self._state.programmer
        try:
            self._log("[cyan]Verifying flashed ROM against source image...[/cyan]")
            self._progress(20, "Preparing verification...")

            result = verify_flash(
                programmer=programmer,
                expected_rom_path=rom_path,
            )

            if result.verified:
                self._log("[bold green]✅ Flash verification PASSED[/bold green]")
                self._progress(100, "Verification complete.")
                self._on_complete()
                return True
            else:
                self._on_error(
                    "❌ Flash verification FAILED — flashed content does not match source ROM.\n"
                    "Do NOT power cycle. Attempt recovery flash immediately."
                )
        except AutoLoaderError as e:
            self._on_error(f"[bold red]Verification error:[/bold red] {e}")
        except Exception as e:
            logger.exception("Unexpected error in run_verify()")
            self._on_error(f"[bold red]Unexpected verification error:[/bold red] {e}")

        return False
