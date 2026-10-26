"""Compatibility adapter for the former unpinned Libreboot builder.

The old code followed a moving lbmk checkout and invoked an implicit apt
dependency installer. Those operations are quarantined. Use ``ArtifactEngine``
with an exact X200 4 MiB or 8 MiB recipe and a clean pinned lbmk checkout.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, NoReturn

from autoloader.exceptions import ROMBuildUnavailableError

from .artifact_engine import ArtifactBuildResult, ArtifactEngine, BuildRequest
from .configurator import ROMConfiguration

logger = logging.getLogger(__name__)


class LibrebootBuilder:
    """Safe adapter exposing only explicit, pinned X200 artifact requests."""

    def __init__(self, workspace_dir: Path, progress_callback: Callable[[str], None] | None = None):
        self.workspace = Path(workspace_dir)
        self.source_dir = self.workspace / "lbmk"
        self.progress_callback = progress_callback
        self.artifact_engine = ArtifactEngine()

    def build_artifact(self, request: BuildRequest) -> ArtifactBuildResult:
        if request.recipe_id not in {"x200-libreboot-4mb", "x200-libreboot-8mb"}:
            raise ROMBuildUnavailableError("LibrebootBuilder accepts only pinned regular-X200 V1 recipes")
        return self.artifact_engine.build(request)

    def clone_source(self, repo_url: str | None = None) -> NoReturn:
        self._unavailable("Automatic source cloning is disabled; supply a clean checkout at the pinned Libreboot release SHA.")

    def configure_build(self, config: ROMConfiguration) -> NoReturn:
        self._unavailable("The former no-op configure_build path is removed; use an exact pinned X200 target config.")

    def inject_blobs(self, config: ROMConfiguration, vendor_files_archive: Path) -> NoReturn:
        self._unavailable("Generic vendor-blob injection is disabled; use the upstream-pinned X200 recipe.")

    def compile(self, config: ROMConfiguration) -> NoReturn:
        self._unavailable("Generic Libreboot compilation is disabled; use ArtifactEngine with a pinned X200 recipe.")

    def get_rom_path(self, config: ROMConfiguration) -> NoReturn:
        self._unavailable("Directory-based ROM discovery is disabled; use the exact verified artifact result.")

    @staticmethod
    def _unavailable(message: str) -> NoReturn:
        logger.warning(message)
        raise ROMBuildUnavailableError(message)
