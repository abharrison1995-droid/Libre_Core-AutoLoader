"""Fail-closed adapter for the former unpinned Canoeboot builder.

V1's T430 route is an official 1vyrain handoff descriptor only. A generic
Canoeboot build could produce an unreviewed T430 ROM, install dependencies,
and bypass the pinned artifact recipes, so every legacy operation is disabled.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, NoReturn

from autoloader.exceptions import ROMBuildUnavailableError

from .configurator import ROMConfiguration

logger = logging.getLogger(__name__)


class CanoebootBuilder:
    """Compatibility adapter with no source, dependency, build, or write path."""

    def __init__(self, workspace_dir: Path, progress_callback: Callable[[str], None] | None = None):
        self.workspace = Path(workspace_dir)
        self.source_dir = self.workspace / "cbmk"
        self.progress_callback = progress_callback

    def clone_source(self, repo_url: str | None = None) -> NoReturn:
        self._unavailable("Unpinned Canoeboot cloning is disabled; V1 T430 uses only the official 1vyrain handoff.")

    def configure_build(self, config: ROMConfiguration) -> NoReturn:
        self._unavailable("Generic Canoeboot configuration is outside the three pinned V1 artifact recipes.")

    def inject_blobs(self, config: ROMConfiguration, vendor_files_archive: Path | None = None) -> NoReturn:
        self._unavailable("Generic Canoeboot blob operations are disabled.")

    def compile(self, config: ROMConfiguration) -> NoReturn:
        self._unavailable("Canoeboot compilation is disabled; V1 T430 does not produce a compiled flashable ROM.")

    def get_rom_path(self, config: ROMConfiguration) -> NoReturn:
        self._unavailable("Unverified Canoeboot output discovery is disabled.")

    @staticmethod
    def _unavailable(message: str) -> NoReturn:
        logger.warning(message)
        raise ROMBuildUnavailableError(message)
