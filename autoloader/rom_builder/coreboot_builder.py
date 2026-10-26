"""Compatibility adapter for callers of the former generic Coreboot builder.

The old implementation guessed Kconfig symbols and wrote into arbitrary
source/output paths. Such calls now fail closed. Use ``ArtifactEngine`` with
the pinned ``x220-coreboot-8mb-seabios`` recipe instead.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, NoReturn

from autoloader.exceptions import ROMBuildUnavailableError

from .artifact_engine import ArtifactBuildResult, ArtifactEngine, BuildRequest
from .blob_extractor import BlobSet
from .configurator import ROMConfiguration

logger = logging.getLogger(__name__)


class CorebootBuilder:
    """Safe adapter exposing only the V1 pinned artifact entrypoint."""

    def __init__(self, workspace_dir: Path, progress_callback: Callable[[str], None] | None = None):
        self.workspace = Path(workspace_dir)
        self.source_dir = self.workspace / "coreboot"
        self.progress_callback = progress_callback
        self.artifact_engine = ArtifactEngine()

    def build_artifact(self, request: BuildRequest) -> ArtifactBuildResult:
        if request.recipe_id != "x220-coreboot-8mb-seabios":
            raise ROMBuildUnavailableError("CorebootBuilder accepts only the pinned X220 V1 recipe")
        return self.artifact_engine.build(request)

    def clone_source(self, repo_url: str | None = None, branch: str | None = None) -> None:
        self._unavailable("Automatic clone/branch selection is disabled; supply a clean checkout at the pinned release SHA.")

    def build_toolchain(self) -> None:
        self._unavailable("Generic toolchain building is disabled; the pinned artifact recipe owns its command sequence.")

    def generate_config(self, config: ROMConfiguration, blob_set: BlobSet | None = None) -> None:
        self._unavailable("Generic model-name Kconfig generation is disabled; use the resolved pinned X220 configuration recipe.")

    def place_blobs(self, config: ROMConfiguration, blob_set: BlobSet) -> None:
        self._unavailable("Unverified blob placement is disabled; use hash-checked X220 BlobInput records.")

    def compile(self) -> None:
        self._unavailable("Generic Coreboot compilation is disabled; use ArtifactEngine with the pinned X220 recipe.")

    def post_process(self, config: ROMConfiguration) -> Path:
        self._unavailable("Generic ROM post-processing/copying is disabled; only a fully verified recipe result is exportable.")

    @staticmethod
    def _unavailable(message: str) -> NoReturn:
        logger.warning(message)
        raise ROMBuildUnavailableError(message)
