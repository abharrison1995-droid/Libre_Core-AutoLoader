"""Pinned, fail-closed artifact preparation for the three V1 workflows.

The engine never runs on non-Linux hosts. It operates only on explicitly
provided upstream checkouts and output roots outside the AutoLoader source
tree. T430 deliberately has a handoff descriptor instead of a compiled ROM.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Mapping, Sequence

from autoloader.config import BASE_DIR, BUILD_TIMEOUT
from autoloader.exceptions import ROMBuildUnavailableError
from autoloader.flasher.backup import is_degenerate_dump
from autoloader.flasher.backup_service import BackupManifestStore, BackupSetResult
from autoloader.workflow.compatibility import CompatibilityValidator
from autoloader.workflow.types import (
    ArtifactEvidence,
    BackupEvidence,
    BuildIntentEvidence,
    CompatibilityDecision,
    FirmwareFamily,
    OperationPlan,
)
from autoloader.workflow.types import RegionEvidence
from autoloader.workflow.types import build_intent_record, build_intent_sha256


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40}$")
_KCONFIG_SET_RE = re.compile(r"^CONFIG_([A-Z0-9_]+)=(.*)$")
_KCONFIG_UNSET_RE = re.compile(r"^# CONFIG_([A-Z0-9_]+) is not set$")
_IFDTOOL_REGION_RE = re.compile(
    r"^\s*Flash Region\s+\d+\s+\(([^)]+)\):\s*"
    r"([0-9a-fA-F]{1,8})\s*-\s*([0-9a-fA-F]{1,8})\s*$"
)
_CBFS_HEX_RE = re.compile(r"^0x[0-9a-fA-F]+$")
_LBMK_COREBOOT_REVISION = "fcd716d9a272461da1fdbaf1c048eb5a52c7896b"
_LBMK_COREBOOT_TREE = "e16fe240567cb3fa08619c6a23e51c4af142f6ac"
_LBMK_COREBOOT_ORIGINS = frozenset({
    "https://github.com/coreboot/coreboot.git",
    "https://review.coreboot.org/coreboot",
})


class RecipeKind(str, Enum):
    COREBOOT = "coreboot"
    LIBREBOOT = "libreboot"
    HANDOFF = "handoff"


class BuildStatus(str, Enum):
    COMPLETE = "complete"
    BLOCKED = "blocked"
    FAILED = "failed"
    HANDOFF_REQUIRED = "handoff_required"


@dataclass(frozen=True)
class PinnedFile:
    path: str
    sha256: str

    def __post_init__(self) -> None:
        if not _SHA256_RE.fullmatch(self.sha256):
            raise ValueError(f"Pinned file hash must be lowercase SHA-256: {self.path}")
        if PurePosixPath(self.path).is_absolute() or ".." in PurePosixPath(self.path).parts:
            raise ValueError("Pinned source paths must be relative and cannot traverse parents")


_LBMK_COREBOOT_CBFSTOOL_SOURCES = (
    PinnedFile("util/cbfstool/cbfs_image.c", "fd6a56a8e20c15f4fd8f03511f05ce8264f120ad0a0dc11900e2e04f89a88dda"),
    PinnedFile("util/cbfstool/cbfstool.c", "0cfd0e5cfb4b169355508e270b1cff1f644010cda72bd36a9a8f07952377e3cb"),
)


@dataclass(frozen=True)
class SourcePin:
    name: str
    repository_url: str
    revision: str
    tree_id: str
    files: tuple[PinnedFile, ...]

    def __post_init__(self) -> None:
        if not self.repository_url.startswith("https://") or "@" in self.repository_url:
            raise ValueError("Source repository must be a credential-free HTTPS URL")
        if not _GIT_OBJECT_RE.fullmatch(self.revision):
            raise ValueError("Source revision must be a full 40-character commit SHA")
        if not _GIT_OBJECT_RE.fullmatch(self.tree_id):
            raise ValueError("Source tree ID must be a full 40-character Git tree SHA")
        object.__setattr__(self, "files", tuple(self.files))


@dataclass(frozen=True)
class ArtifactRecipe:
    recipe_id: str
    workflow_id: str
    kind: RecipeKind
    source: SourcePin
    target: str
    expected_size_bytes: int
    config_file: str
    expected_config_sha256: str
    build_command: tuple[str, ...]
    config_expectations: tuple[tuple[str, str], ...]
    output_relative_path: str

    def __post_init__(self) -> None:
        if self.kind is RecipeKind.HANDOFF:
            raise ValueError("Handoff-only records use HandoffRecipe")
        if self.expected_size_bytes <= 0:
            raise ValueError("Expected artifact size must be positive")
        if not _SHA256_RE.fullmatch(self.expected_config_sha256):
            raise ValueError("Expected configuration hash must be lowercase SHA-256")
        if PurePosixPath(self.config_file).is_absolute() or ".." in PurePosixPath(self.config_file).parts:
            raise ValueError("Configuration path must remain inside its pinned source tree")
        if PurePosixPath(self.output_relative_path).is_absolute() or ".." in PurePosixPath(self.output_relative_path).parts:
            raise ValueError("Output path must be relative and cannot traverse parents")
        if not self.build_command or any("\x00" in part for part in self.build_command):
            raise ValueError("Pinned build command must be non-empty and NUL-free")
        object.__setattr__(self, "build_command", tuple(self.build_command))
        object.__setattr__(self, "config_expectations", tuple(self.config_expectations))


@dataclass(frozen=True)
class HandoffRecipe:
    recipe_id: str
    workflow_id: str
    source: SourcePin
    required_board: str
    handoff_url: str


@dataclass(frozen=True)
class BlobInput:
    name: str
    path: Path
    sha256: str

    def __post_init__(self) -> None:
        if self.name not in {"descriptor.bin", "me.bin", "gbe.bin"}:
            raise ValueError(f"Unsupported X220 blob input name: {self.name}")
        if not _SHA256_RE.fullmatch(self.sha256):
            raise ValueError("Blob input requires a lowercase SHA-256")
        object.__setattr__(self, "path", Path(self.path))


@dataclass(frozen=True)
class BuildRequest:
    recipe_id: str
    source_dir: Path
    output_root: Path
    blob_inputs: tuple[BlobInput, ...] = ()
    expected_regions: tuple[RegionEvidence, ...] = ()
    observed_chip_capacity_bytes: int | None = None
    backup_intent: BuildIntentEvidence | None = None
    backup_set: BackupSetResult | None = None
    cpu_count: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_dir", Path(self.source_dir))
        object.__setattr__(self, "output_root", Path(self.output_root))
        object.__setattr__(self, "blob_inputs", tuple(self.blob_inputs))
        object.__setattr__(self, "expected_regions", tuple(self.expected_regions))
        if self.cpu_count < 1:
            raise ValueError("cpu_count must be positive")
        if self.observed_chip_capacity_bytes is not None and self.observed_chip_capacity_bytes <= 0:
            raise ValueError("Observed chip capacity must be positive")


@dataclass(frozen=True)
class SourceAudit:
    repository_url: str
    revision: str
    tree_id: str
    clean: bool
    pinned_file_hashes: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CommandRecord:
    argv: tuple[str, ...]
    exit_code: int
    log_relative_path: str
    log_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "argv", tuple(self.argv))
        if not _SHA256_RE.fullmatch(self.log_sha256):
            raise ValueError("Command log digest must be lowercase SHA-256")
        if PurePosixPath(self.log_relative_path).is_absolute() or ".." in PurePosixPath(self.log_relative_path).parts:
            raise ValueError("Command log path must remain inside the operation directory")


@dataclass(frozen=True)
class ArtifactRecord:
    relative_path: str | None
    sha256: str
    size_bytes: int
    regions: tuple[RegionEvidence, ...]
    source_relative_path: str
    cbfs_inspection: CBFSInspectionRecord | None = None
    backup_provenance: BackupProvenanceRecord | None = None
    region_inspection_tool: ToolEvidence | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "regions", tuple(self.regions))
        if not _SHA256_RE.fullmatch(self.sha256):
            raise ValueError("Artifact digest must be lowercase SHA-256")
        if self.size_bytes <= 0:
            raise ValueError("Artifact size must be positive")
        if self.relative_path is not None and (
            PurePosixPath(self.relative_path).is_absolute()
            or ".." in PurePosixPath(self.relative_path).parts
        ):
            raise ValueError("Staged artifact path must remain inside the operation directory")
        if PurePosixPath(self.source_relative_path).is_absolute() or ".." in PurePosixPath(self.source_relative_path).parts:
            raise ValueError("Upstream artifact path must remain inside its source checkout")


@dataclass(frozen=True)
class CBFSEntry:
    name: str
    offset_bytes: int
    entry_type: str
    metadata_size_bytes: int
    data_size_bytes: int
    total_size_bytes: int


@dataclass(frozen=True)
class CBFSInspectionRecord:
    tool_relative_path: str
    tool_sha256: str
    source_repository_url: str
    source_revision: str
    source_tree_id: str
    region: str
    required_payload: str
    inventory: tuple[CBFSEntry, ...]
    source_file_hashes: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "inventory", tuple(self.inventory))
        object.__setattr__(self, "source_file_hashes", tuple(self.source_file_hashes))
        if not _SHA256_RE.fullmatch(self.tool_sha256):
            raise ValueError("CBFS inspection requires the exact tool binary SHA-256")
        if not self.inventory:
            raise ValueError("CBFS inspection requires a complete non-empty inventory")
        if not self.source_file_hashes or any(not _SHA256_RE.fullmatch(digest) for _, digest in self.source_file_hashes):
            raise ValueError("CBFS inspection requires hashed parser source files")


@dataclass(frozen=True)
class BackupProvenanceRecord:
    operation_id: str
    profile_id: str
    primary_manifest_sha256: str
    recovery_manifest_sha256: str
    original_chip_sha256: str
    build_intent_sha256: str
    preserved_regions: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "preserved_regions", tuple(self.preserved_regions))
        for digest in (
            self.primary_manifest_sha256,
            self.recovery_manifest_sha256,
            self.original_chip_sha256,
            self.build_intent_sha256,
        ):
            if not _SHA256_RE.fullmatch(digest):
                raise ValueError("X220 backup provenance contains an invalid SHA-256")


@dataclass(frozen=True)
class ToolEvidence:
    relative_path: str
    executable_sha256: str
    source_repository_url: str
    source_revision: str
    source_tree_id: str

    def __post_init__(self) -> None:
        if not _SHA256_RE.fullmatch(self.executable_sha256):
            raise ValueError("Tool evidence requires a SHA-256 for the exact executable")
        if not _GIT_OBJECT_RE.fullmatch(self.source_revision) or not _GIT_OBJECT_RE.fullmatch(self.source_tree_id):
            raise ValueError("Tool evidence requires immutable source commit and tree IDs")


@dataclass(frozen=True)
class ReproducibilityRecord:
    status: str
    first_artifact_sha256: str | None
    second_artifact_sha256: str | None
    explanation: str


@dataclass(frozen=True)
class ArtifactManifest:
    schema_version: int
    operation_id: str
    created_at_utc: str
    recipe_id: str
    workflow_id: str
    status: str
    source_repository_url: str
    source_revision: str
    source_tree_id: str
    source_files: tuple[tuple[str, str], ...]
    commands: tuple[CommandRecord, ...]
    host_platform: str
    toolchain: tuple[tuple[str, str], ...]
    build_environment: tuple[tuple[str, str], ...]
    container_image_digest: str | None
    configuration_sha256: str
    resolved_configuration_sha256: str | None
    observed_chip_capacity_bytes: int | None
    expected_regions: tuple[RegionEvidence, ...]
    blob_sha256: tuple[tuple[str, str], ...]
    artifacts: tuple[ArtifactRecord, ...]
    selected_artifact_relative_path: str | None
    selected_artifact_sha256: str | None
    failure_code: str | None
    failure_detail: str | None
    reproducibility: ReproducibilityRecord
    finalized_operation_plan: OperationPlan | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_files", tuple(self.source_files))
        object.__setattr__(self, "commands", tuple(self.commands))
        object.__setattr__(self, "toolchain", tuple(self.toolchain))
        object.__setattr__(self, "build_environment", tuple(self.build_environment))
        object.__setattr__(self, "expected_regions", tuple(self.expected_regions))
        object.__setattr__(self, "blob_sha256", tuple(self.blob_sha256))
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        if self.status not in {"complete", "failed"}:
            raise ValueError("Artifact manifest status must be complete or failed")
        if self.status == "complete":
            if self.selected_artifact_relative_path is None or self.selected_artifact_sha256 is None:
                raise ValueError("A complete manifest must identify its selected artifact path and digest")
            if not _SHA256_RE.fullmatch(self.selected_artifact_sha256):
                raise ValueError("Selected artifact digest must be lowercase SHA-256")
            selected = [
                item for item in self.artifacts
                if item.relative_path == self.selected_artifact_relative_path
                and item.sha256 == self.selected_artifact_sha256
            ]
            if not self.artifacts or len(selected) != 1 or self.failure_code is not None:
                raise ValueError("A complete manifest requires one selected artifact and no failure")
            selected_record = selected[0]
            if self.recipe_id.startswith(("x200-libreboot-", "x220-coreboot-")):
                if selected_record.cbfs_inspection is None:
                    raise ValueError("A complete firmware artifact requires pinned CBFS structure inspection")
            if self.recipe_id.startswith("x220-coreboot-") and selected_record.backup_provenance is None:
                raise ValueError("A complete X220 full-chip artifact requires original-backup provenance")
            if self.recipe_id.startswith("x220-coreboot-") and selected_record.region_inspection_tool is None:
                raise ValueError("A complete X220 full-chip artifact requires pinned IFD region inspection")
            if self.recipe_id.startswith("x220-coreboot-"):
                plan = self.finalized_operation_plan
                if plan is None:
                    raise ValueError("A complete X220 build must bind its generated artifact in a finalized operation plan")
                artifact = plan.artifact
                selected_parts = PurePosixPath(self.selected_artifact_relative_path).parts
                bound_parts = Path(artifact.path).parts
                if (
                    plan.profile_id != "x220-coreboot"
                    or plan.requested_model_id != "x220"
                    or plan.firmware_family is not FirmwareFamily.COREBOOT
                    or not plan.topology.observed
                    or len(plan.topology.chips) != 1
                    or plan.topology.chips[0].capacity_bytes != self.observed_chip_capacity_bytes
                    or plan.topology.regions != self.expected_regions
                    or artifact.model_id != "x220"
                    or artifact.firmware_family is not FirmwareFamily.COREBOOT
                    or artifact.expected_sha256 != selected_record.sha256
                    or artifact.expected_size_bytes != selected_record.size_bytes
                    or artifact.regions != selected_record.regions
                    or artifact.source_revision != self.source_revision
                    or artifact.source_url != self.source_repository_url
                    or tuple(bound_parts[-len(selected_parts):]) != selected_parts
                ):
                    raise ValueError("Finalized X220 operation plan does not bind the selected generated artifact")
        elif self.selected_artifact_relative_path is not None or self.selected_artifact_sha256 is not None:
            raise ValueError("A failed manifest cannot select an artifact")
        if self.status == "failed" and not self.failure_code:
            raise ValueError("A failed artifact manifest requires a failure code")

    def canonical_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ArtifactBuildResult:
    status: BuildStatus
    code: str
    message: str
    recipe_id: str
    artifacts: tuple[Path, ...] = ()
    manifest: ArtifactManifest | None = None
    manifest_path: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifacts", tuple(Path(path) for path in self.artifacts))
        if self.status is BuildStatus.COMPLETE:
            if not self.artifacts or self.manifest is None or self.manifest.status != "complete":
                raise ValueError("A complete build result requires an artifact and complete immutable manifest")
        elif self.artifacts:
            raise ValueError("Only a complete build result may expose artifact paths")


COREBOOT_2609 = SourcePin(
    name="coreboot-26.09",
    repository_url="https://review.coreboot.org/coreboot.git",
    revision="8b0e34f90e08547cf19e550a43ed72f69c39d6ed",
    tree_id="1cb70fc2c4a0229fa17991b19cf6916b71f75ba7",
    files=(
        PinnedFile("configs/builder/config.lenovo_x220", "fa1a81bb2019586dfcf79d8fb8026a0f622cabc3c92058ae19fb725a5ada37a2"),
        PinnedFile("src/mainboard/lenovo/x220/Kconfig.name", "c78ca677729c27bff9e7428afadfc76bdf5f49d42e198e74647739f05fd8cad7"),
        PinnedFile("src/mainboard/lenovo/x220/Kconfig", "70dff890037e2985b074efab4a0fbce894645935fa0d2aecc528bea2b790a9b7"),
        PinnedFile("src/Kconfig", "9e70bc157540d738a3a506c6bbfa4dd3662441390af405e27aec5bf5182647f2"),
        PinnedFile("util/cbfstool/cbfs_image.c", "2526616afa482cdd2552788e7326f8b0dd840c218c17f1757081f5df516b6ed7"),
        PinnedFile("util/cbfstool/cbfstool.c", "ebdb9caae36be926fae39af2ae3a97d02a18c796ab9c963f68e5b7ea3decc00a"),
    ),
)

LIBREBOOT_2601 = SourcePin(
    name="libreboot-26.01",
    repository_url="https://codeberg.org/libreboot/lbmk.git",
    revision="1cf3181537b8d1fe1df0e91681f700850a3d9bf6",
    tree_id="0cb9451c6e2e265d3aa5b7e371752d45cab98f53",
    files=(
        PinnedFile("include/rom.sh", "0ef30d3d74883fa7ec25ca7cc93e72871f3fa6dfcc323a552010b85d3046f6fb"),
        PinnedFile("config/coreboot/x200_4mb/target.cfg", "0cfe222114c3d426274e4a051fa70351890434815b69b8966f728af54f145c1c"),
        PinnedFile("config/coreboot/x200_4mb/config/libgfxinit_corebootfb", "750097bbd13280ab2cafe23899f5455d18f4f35869113dba97701c6ac50b8fc8"),
        PinnedFile("config/coreboot/x200_4mb/config/libgfxinit_txtmode", "32d374514953a31be8542957d1a9d79d4e809639905ef2f7f429a0c51eed1e5b"),
        PinnedFile("config/coreboot/x200_8mb/target.cfg", "0cfe222114c3d426274e4a051fa70351890434815b69b8966f728af54f145c1c"),
        PinnedFile("config/coreboot/x200_8mb/config/libgfxinit_corebootfb", "b56c94a049701799ad993e574264be44972dc06a39d05d688445d3477c37246a"),
        PinnedFile("config/coreboot/x200_8mb/config/libgfxinit_txtmode", "f4ae1770e8c6a655d1b8d5aa47ed47aa0332ed02c950c0806f42227ad86c0951"),
        PinnedFile("config/coreboot/default/target.cfg", "e139c57c0561933d19f34fcae8e34033245e948637479936c1aabb8578c8ecd1"),
        PinnedFile("config/git/coreboot/pkg.cfg", "c3ba8eeb2f5597be5dd525ea7f211f207996d33124508ca116c86bdb9c823162"),
        PinnedFile("include/lib.sh", "98226349787dbfbe0ffb5752fcb2a236ea7e9dfc43e18c3c8f18c0d6c900861a"),
    ),
)

ONEVYRAIN_HANDOFF = HandoffRecipe(
    recipe_id="t430-1vyrain-upstream-handoff",
    workflow_id="t430-1vyrain",
    source=SourcePin(
        name="1vyrain-upstream",
        repository_url="https://github.com/n4ru/1vyrain.git",
        revision="a3427b34102f8f9887acf65e23d7fd5900fe1d40",
        tree_id="2298fa23e74c7112b7227d8354a0e51404fda4da",
        files=(
            PinnedFile("README.md", "cab46b2a2eccbe0a8723856368e223e697f7d4bf9cbb812b9e5eabc6f5dc9d77"),
            PinnedFile("start.sh", "93c8287fd4e51dcb61a202d027828df50aa548d3de3b2d8ed2f074950b3f93fe"),
        ),
    ),
    required_board="T430",
    handoff_url="https://github.com/n4ru/1vyrain",
)


def derive_lbmk_output_path(target: str, config_stem: str, payload: str) -> str:
    """Derive lbmk's ROM name from its pinned cprom naming convention."""
    if not re.fullmatch(r"x200_(?:4|8)mb", target):
        raise ValueError("Only the two pinned regular-X200 lbmk targets are supported")
    if payload not in {"seabios", "seauboot"}:
        raise ValueError("Unknown payload in the pinned X200 output inventory")
    if config_stem not in {"libgfxinit_corebootfb", "libgfxinit_txtmode"}:
        raise ValueError("Unknown pinned lbmk X200 configuration variant")
    initmode, displaymode = config_stem.split("_", 1)
    filename = f"{payload}_{target}_{initmode}_{displaymode}.rom"
    return (PurePosixPath("bin") / target / filename).as_posix()


_PINNED_LBMK_CONFIG_STEMS = ("libgfxinit_corebootfb", "libgfxinit_txtmode")
_PINNED_LBMK_KEYMAPS = (
    "colemak", "deqwertz", "esqwerty", "frazerty", "frdvbepo", "itqwerty",
    "noqwerty", "ptqwerty", "svenska", "trqwerty", "ukdvorak", "ukqwerty",
    "usdvorak", "usqwerty",
)


def lbmk_expected_output_paths(source: Path, recipe: ArtifactRecipe) -> tuple[str, ...]:
    """Derive every expected ROM output from the pinned X200 target/config inputs.

    The pinned `include/rom.sh` `mkcorebootbin_real()` iterates each config,
    emits SeaBIOS when CONFIG_PAYLOAD_NONE=y, creates SeaUBoot for amd64 except
    text mode, and emits one SeaGRUB ROM per pinned `.gkb` keymap. See the
    `cprom()` naming logic at lines 448-473 in Libreboot 26.01's include/rom.sh.
    """
    if recipe.kind is not RecipeKind.LIBREBOOT:
        raise ValueError("Libreboot output inventory requires an X200 recipe")
    source = Path(source)
    target_dir = source / "config" / "coreboot" / recipe.target
    config_dir = target_dir / "config"
    if config_dir.is_symlink() or not config_dir.is_dir():
        raise ValueError("Pinned lbmk X200 configuration directory is missing or unsafe")
    config_entries = tuple(config_dir.iterdir())
    if any(path.is_symlink() or not path.is_file() for path in config_entries):
        raise ValueError("Pinned lbmk X200 configuration inventory contains an unsafe entry")
    config_stems = tuple(sorted(path.name for path in config_entries))
    if config_stems != tuple(sorted(_PINNED_LBMK_CONFIG_STEMS)):
        raise ValueError("Pinned lbmk X200 configuration inventory changed")
    for config_stem in _PINNED_LBMK_CONFIG_STEMS:
        config_path = config_dir / config_stem
        if config_path.is_symlink():
            raise ValueError("Pinned lbmk X200 configuration must not be a symlink")
        text = config_path.read_text(encoding="utf-8")
        if "CONFIG_PAYLOAD_NONE=y" not in text:
            raise ValueError("Pinned lbmk X200 configuration no longer uses the expected payload path")

    keymap_dir = source / "config" / "data" / "grub" / "keymap"
    if keymap_dir.is_symlink() or not keymap_dir.is_dir():
        raise ValueError("Pinned lbmk GRUB keymap directory is missing or unsafe")
    keymap_paths = tuple(sorted(keymap_dir.glob("*.gkb")))
    keymaps = tuple(sorted(path.stem for path in keymap_paths))
    if keymaps != tuple(sorted(_PINNED_LBMK_KEYMAPS)) or any(path.is_symlink() or not path.is_file() for path in keymap_paths):
        raise ValueError("Pinned lbmk keymap inventory changed")

    result: list[str] = []
    for config_stem in _PINNED_LBMK_CONFIG_STEMS:
        result.append(derive_lbmk_output_path(recipe.target, config_stem, "seabios"))
        for keymap in _PINNED_LBMK_KEYMAPS:
            result.append(_derive_lbmk_grub_path(recipe.target, config_stem, keymap))
        if config_stem == "libgfxinit_corebootfb":
            result.append(derive_lbmk_output_path(recipe.target, config_stem, "seauboot"))
    return tuple(sorted(result))


def _derive_lbmk_grub_path(target: str, config_stem: str, keymap: str) -> str:
    if keymap not in _PINNED_LBMK_KEYMAPS:
        raise ValueError("Unknown pinned lbmk GRUB keymap")
    base = derive_lbmk_output_path(target, config_stem, "seabios")
    filename = PurePosixPath(base).name.replace("seabios_", "seagrub_", 1)
    filename = filename.removesuffix(".rom") + f"_{keymap}.rom"
    return (PurePosixPath("bin") / target / filename).as_posix()


def _recipe(recipe_id: str, target: str, size_mb: int, config_hash: str) -> ArtifactRecipe:
    config_file = f"config/coreboot/{target}/config/libgfxinit_corebootfb"
    return ArtifactRecipe(
        recipe_id=recipe_id,
        workflow_id="x200-libreboot",
        kind=RecipeKind.LIBREBOOT,
        source=LIBREBOOT_2601,
        target=target,
        expected_size_bytes=size_mb * 1024 * 1024,
        config_file=config_file,
        expected_config_sha256=config_hash,
        build_command=("./mk", "-b", "coreboot", target),
        config_expectations=(
            ("CONFIG_BOARD_LENOVO_X200", "y"),
            ("CONFIG_ROM_SIZE", hex(size_mb * 1024 * 1024)),
            ("CONFIG_CBFS_SIZE", hex(size_mb * 1024 * 1024 - 0x3000)),
        ),
        output_relative_path=derive_lbmk_output_path(
            target, PurePosixPath(config_file).name, "seabios"
        ),
    )


X200_4MB = _recipe(
    "x200-libreboot-4mb",
    "x200_4mb",
    4,
    "750097bbd13280ab2cafe23899f5455d18f4f35869113dba97701c6ac50b8fc8",
)
X200_8MB = _recipe(
    "x200-libreboot-8mb",
    "x200_8mb",
    8,
    "b56c94a049701799ad993e574264be44972dc06a39d05d688445d3477c37246a",
)
X220_COREBOOT = ArtifactRecipe(
    recipe_id="x220-coreboot-8mb-seabios",
    workflow_id="x220-coreboot",
    kind=RecipeKind.COREBOOT,
    source=COREBOOT_2609,
    target="lenovo/x220",
    expected_size_bytes=8 * 1024 * 1024,
    config_file="configs/builder/config.lenovo_x220",
    expected_config_sha256="fa1a81bb2019586dfcf79d8fb8026a0f622cabc3c92058ae19fb725a5ada37a2",
    build_command=("make", "-j"),
    config_expectations=(
        ("CONFIG_BOARD_LENOVO_X220", "y"),
        ("CONFIG_ROM_SIZE", "0x800000"),
        ("CONFIG_CBFS_SIZE", "0x300000"),
        ("CONFIG_PAYLOAD_SEABIOS", "y"),
        ("CONFIG_HAVE_IFD_BIN", "y"),
        ("CONFIG_HAVE_ME_BIN", "y"),
        ("CONFIG_HAVE_GBE_BIN", "y"),
        ("CONFIG_IFD_BIN_PATH", '"site-local/descriptor.bin"'),
        ("CONFIG_ME_BIN_PATH", '"site-local/me.bin"'),
        ("CONFIG_GBE_BIN_PATH", '"site-local/gbe.bin"'),
    ),
    output_relative_path="build/coreboot.rom",
)

ARTIFACT_RECIPES: Mapping[str, ArtifactRecipe] = MappingProxyType(
    {recipe.recipe_id: recipe for recipe in (X200_4MB, X200_8MB, X220_COREBOOT)}
)
HANDOFF_RECIPES: Mapping[str, HandoffRecipe] = MappingProxyType(
    {ONEVYRAIN_HANDOFF.recipe_id: ONEVYRAIN_HANDOFF}
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file_obj:
        for block in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_kconfig(text: str) -> dict[str, str]:
    """Parse the value-bearing subset of a resolved Kconfig file."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = _KCONFIG_SET_RE.fullmatch(line.strip())
        if match:
            key, value = match.groups()
            if key in values:
                raise ValueError(f"Duplicate Kconfig setting: CONFIG_{key}")
            values[key] = value
            continue
        unset = _KCONFIG_UNSET_RE.fullmatch(line.strip())
        if unset:
            key = unset.group(1)
            if key in values:
                raise ValueError(f"Duplicate Kconfig setting: CONFIG_{key}")
            values[key] = "n"
    return values


def _parse_int(value: str, symbol: str) -> int:
    try:
        return int(value, 0)
    except ValueError as exc:
        raise ValueError(f"Kconfig {symbol} is not a numeric value: {value!r}") from exc


def validate_recipe_config(recipe: ArtifactRecipe, text: str) -> str:
    """Fail closed unless the resolved config matches its V1 target profile."""
    values = parse_kconfig(text)
    for symbol, expected in recipe.config_expectations:
        normalized_symbol = symbol.removeprefix("CONFIG_")
        actual = values.get(normalized_symbol)
        if symbol in {"CONFIG_ROM_SIZE", "CONFIG_CBFS_SIZE"}:
            if actual is None or _parse_int(actual, symbol) != int(expected, 0):
                raise ValueError(f"Resolved {symbol} does not match pinned recipe")
        elif actual != expected:
            raise ValueError(f"Resolved {symbol} must be {expected!r}, got {actual!r}")
    if recipe.kind is RecipeKind.COREBOOT and values.get("PAYLOAD_NONE", "n") != "n":
        raise ValueError("X220 recipe must resolve to SeaBIOS, not payload-none")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_ifdtool_regions(text: str, flash_size_bytes: int) -> tuple[RegionEvidence, ...]:
    """Parse `ifdtool -d` output; reject missing, duplicate, overlapping regions."""
    names = {
        "flash descriptor": "ifd",
        "bios": "bios",
        "intel me": "me",
        "gbe": "gbe",
        "platform data": "pd",
    }
    regions: list[RegionEvidence] = []
    seen: set[str] = set()
    for line in text.splitlines():
        match = _IFDTOOL_REGION_RE.fullmatch(line)
        if not match:
            continue
        label, start_text, inclusive_end_text = match.groups()
        normalized = names.get(label.strip().lower())
        if normalized is None:
            raise ValueError(f"Unexpected IFD region label: {label}")
        if normalized in seen:
            raise ValueError(f"Duplicate IFD region: {normalized}")
        start, inclusive_end = int(start_text, 16), int(inclusive_end_text, 16)
        end = inclusive_end + 1
        if start < 0 or end <= start or end > flash_size_bytes:
            raise ValueError(f"Invalid IFD region bounds for {normalized}")
        seen.add(normalized)
        regions.append(RegionEvidence(normalized, start, end))
    ordered = tuple(sorted(regions, key=lambda region: region.start_byte))
    _validate_x220_region_map(ordered, flash_size_bytes)
    return ordered


def _validate_x220_region_map(regions: Sequence[RegionEvidence], flash_size_bytes: int) -> None:
    names = [region.name for region in regions]
    allowed = {"ifd", "bios", "me", "gbe", "pd"}
    required = {"ifd", "bios", "me", "gbe"}
    if len(names) != len(set(names)) or not set(names).issubset(allowed):
        raise ValueError("X220 IFD region map has duplicate or unsupported regions")
    if not required.issubset(set(names)):
        raise ValueError(f"IFD region map is incomplete: missing {sorted(required - set(names))}")
    ordered = tuple(sorted(regions, key=lambda region: region.start_byte))
    for previous, current in zip(ordered, ordered[1:]):
        if previous.end_byte > current.start_byte:
            raise ValueError("IFD regions overlap")
        if previous.end_byte != current.start_byte:
            raise ValueError("IFD region map contains an unclassified gap")
    if any(region.start_byte < 0 or region.end_byte <= region.start_byte for region in ordered):
        raise ValueError("IFD region map contains invalid region bounds")
    bios = next(region for region in ordered if region.name == "bios")
    if bios.size_bytes != 3 * 1024 * 1024:
        raise ValueError("X220 BIOS region must be exactly 3 MiB")
    if not ordered or ordered[0].start_byte != 0 or ordered[-1].end_byte != flash_size_bytes:
        raise ValueError("IFD region map does not cover the full X220 flash")


def compare_region_maps(
    observed: Sequence[RegionEvidence],
    expected: Sequence[RegionEvidence],
) -> None:
    observed_map = tuple(sorted(observed, key=lambda region: (region.name, region.start_byte, region.end_byte)))
    expected_map = tuple(sorted(expected, key=lambda region: (region.name, region.start_byte, region.end_byte)))
    if not expected_map:
        raise ValueError("An expected region map is required for X220 builds")
    if observed_map != expected_map:
        raise ValueError("Built image IFD regions differ from the evidence-bound expected map")


def validate_x220_region_preservation(
    original: bytes,
    generated: bytes,
    regions: Sequence[RegionEvidence],
) -> tuple[str, ...]:
    """Require every non-BIOS byte in a full-chip X220 image to be preserved."""
    if len(original) != 8 * 1024 * 1024 or len(generated) != len(original):
        raise ValueError("X220 preservation check requires two exact 8 MiB full-chip images")
    _validate_x220_region_map(regions, len(original))
    preserved: list[str] = []
    for region in regions:
        if region.name == "bios":
            continue
        if generated[region.start_byte:region.end_byte] != original[region.start_byte:region.end_byte]:
            raise ValueError(f"X220 generated full-chip {region.name} region differs from the exact original backup")
        preserved.append(region.name)
    return tuple(sorted(preserved))


def parse_cbfs_parseable_output(
    output: str,
    *,
    required_payload: str,
    region_size_bytes: int,
    expected_region: str | None = None,
) -> tuple[CBFSEntry, ...]:
    """Parse pinned coreboot cbfstool ``print -k`` output without guessing.

    Coreboot's pinned cbfs_image.c defines a six-column tab-separated header
    and six-column rows for ``-k``. Non-verbose ``print -r COREBOOT -k`` output
    contains no FMAP marker; if a marker is present, it must match the explicit
    selected region. The verbose ``-v`` extension is deliberately not requested
    or accepted here. Any extra or malformed row invalidates the inventory
    rather than being silently skipped.
    """
    if not required_payload or region_size_bytes <= 0:
        raise ValueError("CBFS validation requires a payload name and positive region size")
    lines = output.splitlines()
    expected_header = "Name\tOffset\tType\tMetadata Size\tData Size\tTotal Size"
    marker_offset = 0
    if lines and lines[0].startswith("[FMAP REGION]\t"):
        expected_marker = f"[FMAP REGION]\t{expected_region}" if expected_region is not None else None
        if expected_marker is None or lines[0] != expected_marker:
            raise ValueError("Pinned cbfstool output selected an unexpected FMAP region")
        lines = lines[1:]
        marker_offset = 1
    if not lines or lines[0] != expected_header:
        raise ValueError("Pinned cbfstool output has an unexpected or missing parseable header")
    entries: list[CBFSEntry] = []
    names: set[str] = set()
    for index, line in enumerate(lines[1:], start=2 + marker_offset):
        fields = line.split("\t")
        if len(fields) != 6:
            raise ValueError(f"Malformed pinned cbfstool inventory row {index}")
        name, offset_text, entry_type, metadata_text, data_text, total_text = fields
        if not name or name in names or not entry_type:
            raise ValueError(f"Duplicate or empty CBFS entry name/type on row {index}")
        names.add(name)
        if any(not _CBFS_HEX_RE.fullmatch(value) for value in (offset_text, metadata_text, data_text, total_text)):
            raise ValueError(f"CBFS row {index} has malformed hexadecimal size or offset")
        offset, metadata_size, data_size, total_size = (
            int(value, 16)
            for value in (offset_text, metadata_text, data_text, total_text)
        )
        if total_size != metadata_size + data_size:
            raise ValueError(f"CBFS row {index} total size differs from metadata plus data size")
        if total_size <= 0 or offset + total_size > region_size_bytes:
            raise ValueError(f"CBFS row {index} extends outside the selected firmware region")
        entries.append(CBFSEntry(name, offset, entry_type, metadata_size, data_size, total_size))
    matching_payloads = [entry for entry in entries if entry.name == required_payload]
    if (
        len(matching_payloads) != 1
        or matching_payloads[0].entry_type != "payload"
        or matching_payloads[0].data_size_bytes <= 0
    ):
        raise ValueError(f"CBFS inventory lacks exactly one required payload entry {required_payload!r}")
    if not entries:
        raise ValueError("Pinned cbfstool returned an empty CBFS inventory")
    return tuple(entries)


def validated_lbmk_artifacts(source: Path, recipe: ArtifactRecipe) -> tuple[Path, ...]:
    """Validate every ROM emitted by the pinned X200 target and select no fallback."""
    if recipe.kind is not RecipeKind.LIBREBOOT:
        raise ValueError("Exact lbmk output selection requires a Libreboot recipe")
    source = Path(source)
    target_output_dir = source / "bin" / recipe.target
    if target_output_dir.is_symlink() or not target_output_dir.is_dir():
        raise ValueError("Pinned lbmk target output directory is missing or unsafe")
    expected_relative = lbmk_expected_output_paths(source, recipe)
    expected_casefolded = [path.casefold() for path in expected_relative]
    if len(set(expected_casefolded)) != len(expected_relative):
        raise ValueError("Pinned lbmk expected output inventory contains filename collisions")

    candidates = sorted(
        path for path in target_output_dir.iterdir()
        if path.suffix.lower() == ".rom"
    )
    candidate_names = [path.name.casefold() for path in candidates]
    if len(candidate_names) != len(set(candidate_names)):
        raise ValueError("Pinned lbmk build emitted colliding ROM filenames")
    actual_relative = tuple(sorted(path.relative_to(source).as_posix() for path in candidates))
    if actual_relative != expected_relative:
        missing = sorted(set(expected_relative) - set(actual_relative))
        unexpected = sorted(set(actual_relative) - set(expected_relative))
        raise ValueError(
            "Pinned lbmk output inventory mismatch; "
            f"missing={missing}, unexpected={unexpected}"
        )
    for path in candidates:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Generated ROM must be a regular non-symlink file: {path.name}")
        if path.stat().st_size != recipe.expected_size_bytes:
            raise ValueError(f"Generated ROM has an unexpected chip capacity: {path.name}")

    selected = source / PurePosixPath(recipe.output_relative_path)
    if not selected.is_file() or selected.is_symlink():
        raise ValueError("The exact recipe-selected SeaBIOS output is missing or unsafe")
    return tuple(candidates)


def select_exact_lbmk_artifact(source: Path, recipe: ArtifactRecipe) -> Path:
    """Return only the exact SeaBIOS variant after validating the full output set."""
    validated_lbmk_artifacts(source, recipe)
    selected = Path(source) / PurePosixPath(recipe.output_relative_path)
    if selected.stat().st_size != recipe.expected_size_bytes:
        raise ValueError("Selected SeaBIOS output does not match the exact observed-chip target size")
    return selected


def validate_x220_backup_evidence(
    intent: BuildIntentEvidence | None,
    backup: BackupSetResult | None,
    expected_regions: Sequence[RegionEvidence],
    blob_inputs: Sequence[BlobInput],
) -> tuple[bytes, BackupProvenanceRecord]:
    """Bind X220 full-chip inputs to two matching, durable original reads.

    This validates software-recorded Phase 03 evidence and file contents. It
    does not attest hardware observations or enable any write operation.
    """
    if intent is None or backup is None:
        raise ValueError("X220 full-chip preparation requires a pre-build intent and complete original backup set")
    if (
        intent.profile_id != "x220-coreboot"
        or intent.requested_model_id != "x220"
        or intent.firmware_family is not FirmwareFamily.COREBOOT
    ):
        raise ValueError("X220 backup evidence belongs to a different workflow profile")
    if (
        intent.recipe_id != X220_COREBOOT.recipe_id
        or intent.source_url != X220_COREBOOT.source.repository_url
        or intent.source_revision != X220_COREBOOT.source.revision
        or intent.source_tree_id != X220_COREBOOT.source.tree_id
        or intent.configuration_sha256 != X220_COREBOOT.expected_config_sha256
        or intent.expected_size_bytes != X220_COREBOOT.expected_size_bytes
    ):
        raise ValueError("X220 build intent differs from the exact pinned recipe and configuration")
    if not intent.topology.observed or len(intent.topology.chips) != 1:
        raise ValueError("X220 build intent requires one independently observed physical chip")
    chip = intent.topology.chips[0]
    if not chip.observed or chip.capacity_bytes != 8 * 1024 * 1024:
        raise ValueError("X220 build intent must identify the observed 8 MiB chip")
    compare_region_maps(intent.topology.regions, expected_regions)
    _validate_x220_region_map(expected_regions, chip.capacity_bytes)
    if backup.profile_id != intent.profile_id or not backup.operation_id:
        raise ValueError("X220 backup set operation/profile identity does not match its build intent")
    validator = CompatibilityValidator()
    intent_decision = validator.validate_build_intent(intent)
    if intent_decision.decision is not CompatibilityDecision.ALLOWED:
        raise ValueError("X220 build intent is not currently compatible: " + "; ".join(intent_decision.reasons))
    if len(backup.chips) != 1 or backup.chips[0].chip_id != chip.chip_id:
        raise ValueError("X220 backup set must cover exactly the observed 8 MiB chip")
    captured = backup.chips[0]
    if captured.capacity_bytes != chip.capacity_bytes:
        raise ValueError("X220 captured backup capacity differs from the observed chip")
    if len(backup.backup.pairs) != 1:
        raise ValueError("X220 backup manifest must contain exactly one chip backup pair")
    pair = backup.backup.pairs[0]
    if (
        pair.chip_id != chip.chip_id
        or pair.first_path != captured.first_path
        or pair.second_path != captured.second_path
        or pair.first_sha256 != captured.first_sha256
        or pair.second_sha256 != captured.second_sha256
        or pair.transient
        or not pair.first_capture_id
        or not pair.second_capture_id
        or pair.first_capture_id == pair.second_capture_id
    ):
        raise ValueError("X220 backup pair is incomplete, transient or inconsistent with the captured set")
    verified_backup_intent = replace(intent, backup=backup.backup)
    backup_decision = validator.validate_backup(verified_backup_intent)
    if backup_decision.decision is not CompatibilityDecision.ALLOWED:
        raise ValueError("X220 original backup evidence is incomplete: " + "; ".join(backup_decision.reasons))
    if not backup.primary_storage.usable or not backup.recovery_storage.usable:
        raise ValueError("X220 original backup copies must be durably stored and rereadable")
    if backup.primary_storage.volume_id == backup.recovery_storage.volume_id:
        raise ValueError("X220 original and recovery copies must be on separate observed volumes")
    if (
        Path(backup.primary_manifest).parent.resolve(strict=False)
        != Path(backup.primary_storage.path).resolve(strict=False)
        or Path(backup.recovery_manifest).parent.resolve(strict=False)
        != Path(backup.recovery_storage.path).resolve(strict=False)
    ):
        raise ValueError("X220 backup manifests are not stored at their recorded persistent destinations")
    if intent.backup.pairs and intent.backup != backup.backup:
        raise ValueError("X220 build intent backup evidence differs from the captured original set")
    if (
        not _SHA256_RE.fullmatch(backup.primary_manifest_sha256)
        or not _SHA256_RE.fullmatch(backup.recovery_manifest_sha256)
    ):
        raise ValueError("X220 backup manifests must have recorded SHA-256 checksums")

    primary_path = Path(captured.first_path)
    recovery_path = Path(captured.second_path)
    if primary_path.resolve(strict=False) == recovery_path.resolve(strict=False):
        raise ValueError("X220 original backup reads must be separate files")
    for path, declared in (
        (primary_path, captured.first_sha256),
        (recovery_path, captured.second_sha256),
    ):
        if not path.is_file() or path.is_symlink() or path.stat().st_size != chip.capacity_bytes:
            raise ValueError("X220 full-chip backup read is missing, unsafe or the wrong size")
        if sha256_file(path) != declared:
            raise ValueError("X220 full-chip backup read no longer matches its declared checksum")
    original = primary_path.read_bytes()
    recovery_copy = recovery_path.read_bytes()
    if original != recovery_copy or captured.first_sha256 != captured.second_sha256:
        raise ValueError("X220 independent full-chip reads differ; no original backup can be selected")
    if is_degenerate_dump(original):
        raise ValueError("X220 original full-chip backup is blank or degenerate")

    primary_event = _read_verified_backup_event(
        Path(backup.primary_manifest), backup.operation_id, backup.primary_manifest_sha256
    )
    recovery_event = _read_verified_backup_event(
        Path(backup.recovery_manifest), backup.operation_id, backup.recovery_manifest_sha256
    )
    if _event_content(primary_event) != _event_content(recovery_event):
        raise ValueError("X220 primary and recovery manifests disagree about the captured backup")
    _validate_x220_manifest_event(primary_event, intent, captured, expected_regions, backup)

    expected_blob_region = {
        "descriptor.bin": "ifd",
        "me.bin": "me",
        "gbe.bin": "gbe",
    }
    if len(blob_inputs) != 3 or {item.name for item in blob_inputs} != set(expected_blob_region):
        raise ValueError("X220 requires exactly the descriptor, ME and GbE slices from the original backup")
    regions = {region.name: region for region in expected_regions}
    for item in blob_inputs:
        path = Path(item.path)
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"X220 original backup blob input is missing or unsafe: {item.name}")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != item.sha256:
            raise ValueError(f"X220 original backup blob checksum mismatch: {item.name}")
        region = regions[expected_blob_region[item.name]]
        if data != original[region.start_byte:region.end_byte]:
            raise ValueError(f"X220 {item.name} is not the exact matching slice from the original backup")

    preserved = tuple(sorted(region.name for region in expected_regions if region.name != "bios"))
    provenance = BackupProvenanceRecord(
        operation_id=backup.operation_id,
        profile_id=backup.profile_id,
        primary_manifest_sha256=backup.primary_manifest_sha256,
        recovery_manifest_sha256=backup.recovery_manifest_sha256,
        original_chip_sha256=captured.first_sha256,
        build_intent_sha256=build_intent_sha256(intent),
        preserved_regions=preserved,
    )
    return original, provenance


def _finalize_x220_operation_plan(
    intent: BuildIntentEvidence,
    backup: BackupSetResult,
    generated_artifact: str | Path,
    artifact_regions: Sequence[RegionEvidence],
    *,
    expected_sha256: str | None = None,
) -> OperationPlan:
    """Internal final bind step for an artifact already validated by the engine.

    ``expected_sha256`` is supplied only when a prior build-stage measurement
    must be cross-checked. The plan digest is always taken from the staged file
    itself, never from a pre-build intent. Callers must already have passed the
    pinned source/configuration, CBFS inventory, IFD map, and original-region
    preservation checks. This helper is not a standalone artifact acceptance
    API and does not perform those structural checks itself.
    """
    path = Path(generated_artifact)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Generated X220 artifact must be a regular staged file")
    if path.stat().st_size != X220_COREBOOT.expected_size_bytes:
        raise ValueError("Generated X220 artifact does not have the exact 8 MiB chip size")
    if (
        intent.recipe_id != X220_COREBOOT.recipe_id
        or intent.source_url != X220_COREBOOT.source.repository_url
        or intent.source_revision != X220_COREBOOT.source.revision
        or intent.source_tree_id != X220_COREBOOT.source.tree_id
        or intent.configuration_sha256 != X220_COREBOOT.expected_config_sha256
        or intent.expected_size_bytes != X220_COREBOOT.expected_size_bytes
    ):
        raise ValueError("X220 finalization intent differs from the exact pinned recipe")
    intent_decision = CompatibilityValidator().validate_build_intent(intent)
    if intent_decision.decision is not CompatibilityDecision.ALLOWED:
        raise ValueError("X220 finalization intent is no longer compatible: " + "; ".join(intent_decision.reasons))
    digest = sha256_file(path)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError("Generated X220 artifact hash differs from the validated staged bytes")
    regions = tuple(artifact_regions)
    compare_region_maps(intent.topology.regions, regions)
    plan = OperationPlan(
        profile_id=intent.profile_id,
        requested_model_id=intent.requested_model_id,
        firmware_family=intent.firmware_family,
        device=intent.device,
        topology=intent.topology,
        programmer=intent.programmer,
        artifact=ArtifactEvidence(
            path=str(path.resolve(strict=True)),
            model_id="x220",
            firmware_family=FirmwareFamily.COREBOOT,
            expected_sha256=digest,
            expected_size_bytes=path.stat().st_size,
            source_url=X220_COREBOOT.source.repository_url,
            source_revision=X220_COREBOOT.source.revision,
            regions=regions,
        ),
        backup=backup.backup,
    )
    decision = CompatibilityValidator().validate(plan)
    if decision.decision is not CompatibilityDecision.ALLOWED:
        raise ValueError("Generated X220 artifact failed final evidence binding: " + "; ".join(decision.reasons))
    return plan


def _event_content(event: Mapping[str, object]) -> str:
    excluded = {"sequence", "previous_sha256", "event_sha256"}
    return json.dumps(
        {key: value for key, value in event.items() if key not in excluded},
        sort_keys=True,
        separators=(",", ":"),
    )


def _read_verified_backup_event(path: Path, operation_id: str, expected_digest: str) -> Mapping[str, object]:
    if path.name != "backup-manifest.json" or not path.is_file() or path.is_symlink():
        raise ValueError("X220 backup manifest is missing, unsafe or has an unexpected name")
    store = BackupManifestStore(path.parent)
    events = store.read_events()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("X220 backup manifest document cannot be reread") from exc
    if not isinstance(document, dict) or document.get("manifest_sha256") != expected_digest:
        raise ValueError("X220 backup manifest no longer matches its declared checksum")
    matches = [event for event in events if event.get("operation_id") == operation_id and event.get("status") == "verified"]
    if len(matches) != 1:
        raise ValueError("X220 backup manifest must contain exactly one verified event for the operation")
    return matches[0]


def _validate_x220_manifest_event(
    event: Mapping[str, object],
    intent: BuildIntentEvidence,
    captured: object,
    expected_regions: Sequence[RegionEvidence],
    backup: BackupSetResult,
) -> None:
    if event.get("profile_id") != intent.profile_id or event.get("requested_model_id") != intent.requested_model_id:
        raise ValueError("X220 backup manifest identity differs from its build intent")
    tool = event.get("tool")
    if (
        not isinstance(tool, Mapping)
        or tool.get("name") != backup.tool_name
        or tool.get("version") != backup.tool_version
        or event.get("programmer") != backup.programmer
    ):
        raise ValueError("X220 backup manifest tool/programmer identity differs from the captured backup set")
    expected_device = {
        "raw_dmi_product_name": intent.device.raw_dmi_product_name,
        "raw_mtm": intent.device.raw_mtm,
        "raw_bios_version": intent.device.raw_bios_version,
    }
    if (
        event.get("device_identifiers") != expected_device
        or event.get("source_artifact") is not None
        or event.get("build_intent") != build_intent_record(intent)
        or event.get("build_intent_sha256") != build_intent_sha256(intent)
    ):
        raise ValueError("X220 backup manifest device or build intent differs from the pre-build evidence")
    expected_chips = [{
        "chip_id": intent.topology.chips[0].chip_id,
        "physical_label": intent.topology.chips[0].physical_label,
        "capacity_bytes": intent.topology.chips[0].capacity_bytes,
        "voltage_mv": intent.topology.chips[0].voltage_mv,
    }]
    raw_chips = event.get("topology")
    if not isinstance(raw_chips, list) or len(raw_chips) != 1:
        raise ValueError("X220 backup manifest lacks exactly one observed chip record")
    chip_entry = raw_chips[0]
    if not isinstance(chip_entry, Mapping) or any(chip_entry.get(key) != value for key, value in expected_chips[0].items()):
        raise ValueError("X220 backup manifest topology differs from the current observed plan")
    expected_map = sorted((r.name, r.start_byte, r.end_byte) for r in expected_regions)
    raw_regions = event.get("region_map")
    if not isinstance(raw_regions, list):
        raise ValueError("X220 backup manifest lacks the observed IFD region map")
    actual_map = sorted(
        (item.get("name"), item.get("start_byte"), item.get("end_byte"))
        for item in raw_regions if isinstance(item, Mapping)
    )
    if len(actual_map) != len(raw_regions) or actual_map != expected_map:
        raise ValueError("X220 backup manifest region map differs from the observed plan")
    for field, evidence in (
        ("primary_storage", backup.primary_storage),
        ("recovery_storage", backup.recovery_storage),
    ):
        storage = event.get(field)
        if not isinstance(storage, Mapping):
            raise ValueError(f"X220 backup manifest lacks {field} persistence evidence")
        if (
            storage.get("path") != evidence.path
            or storage.get("volume_id") != evidence.volume_id
            or storage.get("persistent_confirmed") is not True
            or storage.get("scratch_readback_verified") is not True
            or not isinstance(storage.get("available_bytes"), int)
            or storage.get("available_bytes", 0) < storage.get("required_bytes", 1)
        ):
            raise ValueError(f"X220 backup manifest {field} differs from the current storage evidence")
    chips = event.get("chips")
    expected_capture = {
        "chip_id": getattr(captured, "chip_id"),
        "capacity_bytes": getattr(captured, "capacity_bytes"),
        "first_path": getattr(captured, "first_path"),
        "second_path": getattr(captured, "second_path"),
        "first_sha256": getattr(captured, "first_sha256"),
        "second_sha256": getattr(captured, "second_sha256"),
        "first_capture_id": getattr(captured, "first_capture_id"),
        "second_capture_id": getattr(captured, "second_capture_id"),
    }
    if not isinstance(chips, list) or len(chips) != 1 or not isinstance(chips[0], Mapping):
        raise ValueError("X220 backup manifest lacks exactly one captured two-read chip record")
    if any(chips[0].get(key) != value for key, value in expected_capture.items()):
        raise ValueError("X220 backup manifest captured bytes differ from the returned backup set")


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _require_external_path(path: Path, label: str, *, must_exist: bool) -> Path:
    resolved = path.resolve(strict=must_exist)
    repo_root = BASE_DIR.resolve()
    if _is_inside(resolved, repo_root):
        raise ValueError(f"{label} must be outside the AutoLoader application tree")
    return resolved


def _require_disjoint_paths(source: Path, output_root: Path) -> None:
    if _is_inside(output_root, source) or _is_inside(source, output_root):
        raise ValueError("Pinned source checkout and artifact output root must be separate directories")


class ArtifactEngine:
    """Build only from immutable upstream pins and record verified outputs."""

    def build(self, request: BuildRequest) -> ArtifactBuildResult:
        if request.recipe_id in HANDOFF_RECIPES:
            handoff = HANDOFF_RECIPES[request.recipe_id]
            return ArtifactBuildResult(
                BuildStatus.HANDOFF_REQUIRED,
                "UPSTREAM_HANDOFF_REQUIRED",
                (
                    f"Use the official {handoff.required_board} 1vyrain handoff at {handoff.handoff_url} "
                    f"pinned to {handoff.source.revision}; no ROM is compiled, executed, or bundled."
                ),
                request.recipe_id,
            )
        recipe = ARTIFACT_RECIPES.get(request.recipe_id)
        if recipe is None:
            return ArtifactBuildResult(
                BuildStatus.BLOCKED,
                "UNKNOWN_ARTIFACT_RECIPE",
                "No allowlisted V1 artifact recipe matches this request.",
                request.recipe_id,
            )
        if platform.system() != "Linux":
            return ArtifactBuildResult(
                BuildStatus.BLOCKED,
                "LINUX_BUILD_ENV_REQUIRED",
                "Pinned upstream firmware builds require Linux toolchain support; this host has no supported Linux build environment. No command was run and no artifact was created.",
                recipe.recipe_id,
            )
        try:
            return self._build_linux(recipe, request)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            return ArtifactBuildResult(
                BuildStatus.BLOCKED,
                "ARTIFACT_BUILD_BLOCKED",
                str(exc),
                recipe.recipe_id,
            )

    def inspect_source(self, recipe_id: str, source_dir: Path) -> SourceAudit:
        recipe = ARTIFACT_RECIPES.get(recipe_id)
        handoff = HANDOFF_RECIPES.get(recipe_id)
        source_pin = recipe.source if recipe else handoff.source if handoff else None
        if source_pin is None:
            raise ValueError(f"Unknown pinned source recipe: {recipe_id}")
        source = _require_external_path(Path(source_dir), "Source checkout", must_exist=True)
        if not source.is_dir():
            raise ValueError("Source checkout must be a directory")
        origin = self._git_value(source, "remote", "get-url", "origin")
        revision = self._git_value(source, "rev-parse", "HEAD")
        tree_id = self._git_value(source, "rev-parse", "HEAD^{tree}")
        status = self._git_value(source, "status", "--porcelain=v1", "--untracked-files=all")
        if origin != source_pin.repository_url:
            raise ValueError("Source checkout origin does not match the pinned upstream URL")
        if revision != source_pin.revision or tree_id != source_pin.tree_id:
            raise ValueError("Source checkout revision/tree does not match the pinned release")
        if status:
            raise ValueError("Source checkout is dirty; refusing to use modified or untracked build inputs")
        file_hashes: list[tuple[str, str]] = []
        for pinned_file in source_pin.files:
            candidate = (source / PurePosixPath(pinned_file.path)).resolve(strict=True)
            if not _is_inside(candidate, source):
                raise ValueError(f"Pinned file escapes source checkout: {pinned_file.path}")
            actual = sha256_file(candidate)
            if actual != pinned_file.sha256:
                raise ValueError(f"Pinned source file hash mismatch: {pinned_file.path}")
            file_hashes.append((pinned_file.path, actual))
        return SourceAudit(origin, revision, tree_id, True, tuple(file_hashes))

    def _build_linux(self, recipe: ArtifactRecipe, request: BuildRequest) -> ArtifactBuildResult:
        source = _require_external_path(request.source_dir, "Source checkout", must_exist=True)
        output_root = _require_external_path(request.output_root, "Artifact output root", must_exist=False)
        _require_disjoint_paths(source, output_root)
        source_audit = self.inspect_source(recipe.recipe_id, source)
        self._assert_no_stale_outputs(recipe, source)
        if request.observed_chip_capacity_bytes != recipe.expected_size_bytes:
            raise ValueError("Build recipe size must match the independently observed chip capacity")

        original_x220_backup: bytes | None = None
        backup_provenance: BackupProvenanceRecord | None = None
        if recipe is X220_COREBOOT:
            if len(request.blob_inputs) != 3 or {item.name for item in request.blob_inputs} != {"descriptor.bin", "me.bin", "gbe.bin"}:
                raise ValueError("X220 full-chip build requires exact descriptor, ME and GbE inputs")
            if not request.expected_regions:
                raise ValueError("X220 build requires an independently observed expected IFD region map")
            _validate_x220_region_map(request.expected_regions, recipe.expected_size_bytes)
            original_x220_backup, backup_provenance = validate_x220_backup_evidence(
                request.backup_intent,
                request.backup_set,
                request.expected_regions,
                request.blob_inputs,
            )
        elif request.blob_inputs:
            raise ValueError("Blob inputs are only accepted by the pinned X220 coreboot recipe")
        elif request.expected_regions:
            raise ValueError("X200 build does not accept an X220 IFD region map")
        elif request.backup_intent is not None or request.backup_set is not None:
            raise ValueError("Original backup evidence is accepted only by the X220 full-chip recipe")

        prepared_blobs: list[tuple[BlobInput, Path]] = []
        if recipe is X220_COREBOOT:
            for item in sorted(request.blob_inputs, key=lambda blob: blob.name):
                source_path = _require_external_path(item.path, f"{item.name} input", must_exist=True)
                if not source_path.is_file() or sha256_file(source_path) != item.sha256:
                    raise ValueError(f"Blob input hash mismatch: {item.name}")
                prepared_blobs.append((item, source_path))

        self._validate_lbmk_target(recipe, source) if recipe.kind is RecipeKind.LIBREBOOT else None
        toolchain = self._toolchain_versions()

        run_id = str(uuid.uuid4())
        run_dir = output_root / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        logs_dir = run_dir / "logs"
        logs_dir.mkdir()
        commands: list[CommandRecord] = []
        config_hash = recipe.expected_config_sha256
        resolved_config_hash: str | None = None
        blob_hashes: list[tuple[str, str]] = []
        artifacts: list[ArtifactRecord] = []
        region_inspection_tool: ToolEvidence | None = None
        finalized_operation_plan: OperationPlan | None = None
        failure_code: str | None = None
        failure_detail: str | None = None

        try:
            if recipe is X220_COREBOOT:
                self._stage_x220_inputs(source, prepared_blobs, blob_hashes)
                seed_path = source / ".config"
                source_config = (source / recipe.config_file).read_text(encoding="utf-8")
                if source_config.count("CONFIG_PAYLOAD_NONE=y") != 1:
                    raise ValueError("Pinned X220 upstream seed changed its payload-none setting")
                seed = source_config.replace(
                    "CONFIG_PAYLOAD_NONE=y",
                    "# CONFIG_PAYLOAD_NONE is not set\nCONFIG_PAYLOAD_SEABIOS=y",
                    1,
                )
                if seed_path.exists():
                    raise ValueError("Stale Coreboot .config exists; use a fresh pinned source checkout")
                seed_path.write_text(seed, encoding="utf-8", newline="\n")
                self._run_logged(("make", "olddefconfig"), source, logs_dir, commands)
                resolved_config = (source / ".config").read_text(encoding="utf-8")
                resolved_config_hash = validate_recipe_config(recipe, resolved_config)
                self._run_logged(("make", "crossgcc-i386", f"CPUS={request.cpu_count}"), source, logs_dir, commands)
                self._run_logged(("make", f"-j{request.cpu_count}"), source, logs_dir, commands)
            else:
                self._run_logged(recipe.build_command, source, logs_dir, commands)
                resolved_config_path = source / "src" / "coreboot" / "default" / ".config"
                if not resolved_config_path.is_file():
                    raise ValueError("Pinned lbmk build did not leave a resolved Coreboot .config")
                resolved_config = resolved_config_path.read_text(encoding="utf-8")
                resolved_config_hash = validate_recipe_config(recipe, resolved_config)

            raw_artifacts = (
                validated_lbmk_artifacts(source, recipe)
                if recipe.kind is RecipeKind.LIBREBOOT
                else (source / PurePosixPath(recipe.output_relative_path),)
            )
            selected_raw = source / PurePosixPath(recipe.output_relative_path)
            if selected_raw not in raw_artifacts:
                raise ValueError("The exact recipe-selected artifact is absent from the validated output set")
            regions: tuple[RegionEvidence, ...]
            if recipe is X220_COREBOOT:
                if not selected_raw.is_file() or selected_raw.is_symlink():
                    raise ValueError(f"Expected pinned output is missing or unsafe: {recipe.output_relative_path}")
                parser = source / "util" / "ifdtool" / "ifdtool"
                if parser.is_symlink() or not parser.is_file():
                    raise ValueError("Pinned-source ifdtool executable is missing or unsafe")
                region_inspection_tool = ToolEvidence(
                    relative_path=parser.relative_to(source).as_posix(),
                    executable_sha256=sha256_file(parser),
                    source_repository_url=source_audit.repository_url,
                    source_revision=source_audit.revision,
                    source_tree_id=source_audit.tree_id,
                )
                if request.backup_set is None:
                    raise ValueError("X220 backup set disappeared before original descriptor validation")
                original_path = Path(request.backup_set.chips[0].first_path)
                original_map_output = self._run_readonly(
                    (str(parser), "-d", str(original_path)), source, logs_dir, commands
                )
                original_regions = parse_ifdtool_regions(original_map_output, recipe.expected_size_bytes)
                compare_region_maps(original_regions, request.expected_regions)
                output = self._run_readonly((str(parser), "-d", str(selected_raw)), source, logs_dir, commands)
                regions = parse_ifdtool_regions(output, recipe.expected_size_bytes)
                compare_region_maps(regions, request.expected_regions)
                if original_x220_backup is None:
                    raise ValueError("X220 original full-chip backup evidence is unavailable")
                generated = selected_raw.read_bytes()
                validate_x220_region_preservation(original_x220_backup, generated, regions)
            else:
                regions = (RegionEvidence("whole_flash", 0, recipe.expected_size_bytes),)
            cbfs_inspection = self._inspect_cbfs(
                recipe,
                source,
                selected_raw,
                logs_dir,
                commands,
            )

            staged_selected: Path | None = None
            for raw_artifact in raw_artifacts:
                if not raw_artifact.is_file() or raw_artifact.is_symlink():
                    raise ValueError(f"Generated artifact is missing or unsafe: {raw_artifact.name}")
                if raw_artifact.stat().st_size != recipe.expected_size_bytes:
                    raise ValueError("Output size does not match the exact observed-chip target recipe")
                raw_digest = sha256_file(raw_artifact)
                relative_stage_path: str | None = None
                if raw_artifact == selected_raw:
                    destination = run_dir / "artifacts" / raw_artifact.name
                    destination.parent.mkdir()
                    with raw_artifact.open("rb") as input_file, destination.open("xb") as output_file:
                        shutil.copyfileobj(input_file, output_file)
                    if destination.stat().st_size != recipe.expected_size_bytes:
                        raise ValueError("Copied artifact size changed during staging")
                    destination_digest = sha256_file(destination)
                    if destination_digest != raw_digest:
                        raise ValueError("Copied artifact hash differs from the validated upstream output")
                    relative_stage_path = destination.relative_to(run_dir).as_posix()
                    staged_selected = destination
                artifacts.append(ArtifactRecord(
                    relative_path=relative_stage_path,
                    sha256=raw_digest,
                    size_bytes=raw_artifact.stat().st_size,
                    regions=regions,
                    source_relative_path=raw_artifact.relative_to(source).as_posix(),
                    cbfs_inspection=(cbfs_inspection if raw_artifact == selected_raw else None),
                    backup_provenance=(backup_provenance if raw_artifact == selected_raw else None),
                    region_inspection_tool=(region_inspection_tool if raw_artifact == selected_raw else None),
                ))
            if staged_selected is None:
                raise ValueError("The selected artifact was not staged")
            if recipe is X220_COREBOOT:
                intent = request.backup_intent
                if intent is None or request.backup_set is None or backup_provenance is None:
                    raise ValueError("X220 finalization requires the pre-build intent and verified original backup")
                selected_record = next(
                    item for item in artifacts
                    if item.relative_path == staged_selected.relative_to(run_dir).as_posix()
                )
                finalized_operation_plan = _finalize_x220_operation_plan(
                    intent,
                    request.backup_set,
                    staged_selected,
                    selected_record.regions,
                    expected_sha256=selected_record.sha256,
                )
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            failure_code = "ARTIFACT_VALIDATION_FAILED"
            failure_detail = str(exc)

        status = "complete" if artifacts and failure_code is None else "failed"
        manifest = ArtifactManifest(
            schema_version=1,
            operation_id=run_id,
            created_at_utc=datetime.now(timezone.utc).isoformat(),
            recipe_id=recipe.recipe_id,
            workflow_id=recipe.workflow_id,
            status=status,
            source_repository_url=source_audit.repository_url,
            source_revision=source_audit.revision,
            source_tree_id=source_audit.tree_id,
            source_files=source_audit.pinned_file_hashes,
            commands=tuple(commands),
            host_platform=platform.platform(),
            toolchain=toolchain,
            build_environment=(
                ("HOME", "<operation-output-directory>"),
                ("TMPDIR", "<operation-output-directory>/tmp"),
                ("PATH_SHA256", hashlib.sha256(os.environ.get("PATH", "").encode()).hexdigest()),
                ("LC_ALL", "C"),
                ("LANG", "C"),
                ("TZ", "UTC"),
            ),
            container_image_digest=None,
            configuration_sha256=config_hash,
            resolved_configuration_sha256=resolved_config_hash,
            observed_chip_capacity_bytes=request.observed_chip_capacity_bytes,
            expected_regions=request.expected_regions,
            blob_sha256=tuple(blob_hashes),
            artifacts=tuple(artifacts),
            selected_artifact_relative_path=(
                next((item.relative_path for item in artifacts if item.source_relative_path == recipe.output_relative_path), None)
                if status == "complete" else None
            ),
            selected_artifact_sha256=(
                next((item.sha256 for item in artifacts if item.source_relative_path == recipe.output_relative_path), None)
                if status == "complete" else None
            ),
            failure_code=failure_code,
            failure_detail=failure_detail,
            reproducibility=ReproducibilityRecord(
                status="not_attempted",
                first_artifact_sha256=(
                    next((item.sha256 for item in artifacts if item.source_relative_path == recipe.output_relative_path), None)
                    if status == "complete" else None
                ),
                second_artifact_sha256=None,
                explanation="A second isolated build was not requested; reproducibility remains unproven.",
            ),
            finalized_operation_plan=finalized_operation_plan,
        )
        manifest_path = run_dir / "manifest.json"
        self._write_manifest_exclusive(manifest_path, manifest)
        checksum_path = run_dir / "manifest.sha256"
        with checksum_path.open("x", encoding="ascii", newline="\n") as checksum_file:
            checksum_file.write(f"{sha256_file(manifest_path)}  manifest.json\n")
        if failure_code:
            return ArtifactBuildResult(BuildStatus.FAILED, failure_code, failure_detail or "Build validation failed", recipe.recipe_id, (), manifest, manifest_path)
        return ArtifactBuildResult(
            BuildStatus.COMPLETE,
            "ARTIFACT_VERIFIED",
            "Pinned upstream artifact passed source, configuration, exact-size, CBFS payload and model-specific layout/preservation validation.",
            recipe.recipe_id,
            (run_dir / Path(manifest.selected_artifact_relative_path or ""),),
            manifest,
            manifest_path,
        )

    def _assert_no_stale_outputs(self, recipe: ArtifactRecipe, source: Path) -> None:
        stale: Sequence[Path]
        if recipe.kind is RecipeKind.COREBOOT:
            stale = (source / ".config", source / "build", source / "site-local")
        else:
            stale = tuple(source / name for name in ("src", "elf", "bin", "tmp"))
        present = [str(path.name) for path in stale if path.exists()]
        if present:
            raise ValueError(f"Stale or ignored build outputs exist: {', '.join(present)}")

    def _validate_lbmk_target(self, recipe: ArtifactRecipe, source: Path) -> None:
        target_cfg = source / "config" / "coreboot" / recipe.target / "target.cfg"
        mode_cfg = source / PurePosixPath(recipe.config_file)
        if not target_cfg.is_file() or not mode_cfg.is_file():
            raise ValueError("Pinned lbmk checkout lacks the exact X200 target/config")
        target_text = target_cfg.read_text(encoding="utf-8")
        required_target_values = {
            "payload_seabios": "y",
            "payload_grub": "y",
            "payload_memtest": "y",
            "payload_uboot": "amd64",
        }
        assignments = dict(re.findall(r'^([A-Za-z0-9_]+)="([^"]*)"$', target_text, re.MULTILINE))
        if any(assignments.get(key) != value for key, value in required_target_values.items()):
            raise ValueError("Pinned lbmk target payload settings differ from the reviewed output recipe")
        if assignments.get("payload_grubsea", "") == "y":
            raise ValueError("Pinned lbmk target enables an unsupported SeaGRUB-first output variant")
        default_coreboot_target = source / "config" / "coreboot" / "default" / "target.cfg"
        coreboot_pkg_config = source / "config" / "git" / "coreboot" / "pkg.cfg"
        lib_script = source / "include" / "lib.sh"
        if not default_coreboot_target.is_file() or not coreboot_pkg_config.is_file() or not lib_script.is_file():
            raise ValueError("Pinned lbmk checkout lacks its reviewed nested Coreboot source/tool configuration")
        default_target_text = default_coreboot_target.read_text(encoding="utf-8")
        if not re.search(r'^tree="default"$', default_target_text, re.MULTILINE):
            raise ValueError("Pinned lbmk nested Coreboot tree selection changed")
        if not re.search(rf'^rev="{_LBMK_COREBOOT_REVISION}"$', default_target_text, re.MULTILINE):
            raise ValueError("Pinned lbmk nested Coreboot source revision changed")
        if sha256_file(default_coreboot_target) != "e139c57c0561933d19f34fcae8e34033245e948637479936c1aabb8578c8ecd1":
            raise ValueError("Pinned lbmk default Coreboot target config hash mismatch")
        pkg_text = coreboot_pkg_config.read_text(encoding="utf-8")
        pkg_values = dict(re.findall(r'^([A-Za-z0-9_]+)="([^"]*)"$', pkg_text, re.MULTILINE))
        if pkg_values.get("url") != "https://github.com/coreboot/coreboot.git" or pkg_values.get("bkup_url") != "https://review.coreboot.org/coreboot":
            raise ValueError("Pinned lbmk Coreboot source repository configuration changed")
        if sha256_file(coreboot_pkg_config) != "c3ba8eeb2f5597be5dd525ea7f211f207996d33124508ca116c86bdb9c823162":
            raise ValueError("Pinned lbmk Coreboot source repository config hash mismatch")
        if 'cbfstool="elf/coreboot/$tree/cbfstool"' not in lib_script.read_text(encoding="utf-8"):
            raise ValueError("Pinned lbmk cbfstool path selection changed")
        if sha256_file(lib_script) != "98226349787dbfbe0ffb5752fcb2a236ea7e9dfc43e18c3c8f18c0d6c900861a":
            raise ValueError("Pinned lbmk cbfstool path source hash mismatch")
        if sha256_file(mode_cfg) != recipe.expected_config_sha256:
            raise ValueError("Pinned lbmk resolved-config seed hash mismatch")
        if not (source / "include" / "rom.sh").is_file():
            raise ValueError("Pinned lbmk output naming implementation is missing")
        config_stem = PurePosixPath(recipe.config_file).name
        expected_output = derive_lbmk_output_path(recipe.target, config_stem, "seabios")
        if expected_output != recipe.output_relative_path:
            raise ValueError("Internal X200 output path does not match pinned lbmk naming rules")
        lbmk_expected_output_paths(source, recipe)

    def _inspect_cbfs(
        self,
        recipe: ArtifactRecipe,
        source: Path,
        artifact: Path,
        logs_dir: Path,
        commands: list[CommandRecord],
    ) -> CBFSInspectionRecord:
        command: tuple[str, ...]
        if recipe.kind is RecipeKind.LIBREBOOT:
            nested_source = source / "src" / "coreboot" / "default"
            nested_origin, nested_revision, nested_tree, parser_source_hashes = self._validate_lbmk_nested_coreboot(
                nested_source
            )
            tool = source / "elf" / "coreboot" / "default" / "cbfstool"
            relative_tool_path = tool.relative_to(source).as_posix()
            command = (str(tool), str(artifact), "print", "-k")
            region_name = "whole_flash"
            region_size = dict(recipe.config_expectations)["CONFIG_CBFS_SIZE"]
            region_size_bytes = int(region_size, 0)
            required_payload = "fallback/payload"
        elif recipe is X220_COREBOOT:
            nested_origin, nested_revision, nested_tree = (
                recipe.source.repository_url,
                recipe.source.revision,
                recipe.source.tree_id,
            )
            cbfs_source_files = {
                item.path: item.sha256
                for item in recipe.source.files
                if item.path in {"util/cbfstool/cbfs_image.c", "util/cbfstool/cbfstool.c"}
            }
            if len(cbfs_source_files) != 2:
                raise ValueError("Pinned Coreboot source pin lacks its CBFS parser implementation files")
            parser_source_hashes = tuple(sorted(cbfs_source_files.items()))
            tool = source / "util" / "cbfstool" / "cbfstool"
            relative_tool_path = tool.relative_to(source).as_posix()
            command = (str(tool), str(artifact), "print", "-r", "COREBOOT", "-k")
            region_name = "COREBOOT"
            region_size_bytes = 3 * 1024 * 1024
            required_payload = "fallback/payload"
        else:
            raise ValueError("T430 handoff recipes have no CBFS artifact to inspect")
        if tool.is_symlink() or not tool.is_file():
            raise ValueError("Pinned-source cbfstool executable is missing or unsafe")
        output = self._run_readonly(command, source, logs_dir, commands)
        inventory = parse_cbfs_parseable_output(
            output,
            required_payload=required_payload,
            region_size_bytes=region_size_bytes,
            expected_region="COREBOOT" if recipe is X220_COREBOOT else None,
        )
        return CBFSInspectionRecord(
            tool_relative_path=relative_tool_path,
            tool_sha256=sha256_file(tool),
            source_repository_url=nested_origin,
            source_revision=nested_revision,
            source_tree_id=nested_tree,
            region=region_name,
            required_payload=required_payload,
            inventory=inventory,
            source_file_hashes=parser_source_hashes,
        )

    def _validate_lbmk_nested_coreboot(self, source: Path) -> tuple[str, str, str, tuple[tuple[str, str], ...]]:
        nested = _require_external_path(Path(source), "lbmk nested Coreboot checkout", must_exist=True)
        if not nested.is_dir():
            raise ValueError("Pinned lbmk nested Coreboot checkout is missing")
        origin = self._git_value(nested, "remote", "get-url", "origin")
        revision = self._git_value(nested, "rev-parse", "HEAD")
        tree_id = self._git_value(nested, "rev-parse", "HEAD^{tree}")
        status = self._git_value(nested, "status", "--porcelain=v1", "--untracked-files=all")
        if origin not in _LBMK_COREBOOT_ORIGINS:
            raise ValueError("lbmk nested Coreboot origin differs from its pinned official source")
        if revision != _LBMK_COREBOOT_REVISION or tree_id != _LBMK_COREBOOT_TREE:
            raise ValueError("lbmk nested Coreboot revision/tree differs from the pinned target.cfg")
        if status:
            raise ValueError("lbmk nested Coreboot source tree is dirty after the pinned build")
        file_hashes: list[tuple[str, str]] = []
        for pinned_file in _LBMK_COREBOOT_CBFSTOOL_SOURCES:
            candidate = nested / PurePosixPath(pinned_file.path)
            if candidate.is_symlink() or not candidate.is_file():
                raise ValueError(f"Pinned lbmk Coreboot CBFS parser source is missing or unsafe: {pinned_file.path}")
            digest = sha256_file(candidate)
            if digest != pinned_file.sha256:
                raise ValueError(f"Pinned lbmk Coreboot CBFS parser source hash mismatch: {pinned_file.path}")
            file_hashes.append((pinned_file.path, digest))
        return origin, revision, tree_id, tuple(file_hashes)

    def _stage_x220_inputs(
        self,
        source: Path,
        inputs: Sequence[tuple[BlobInput, Path]],
        blob_hashes: list[tuple[str, str]],
    ) -> None:
        destination_dir = source / "site-local"
        if destination_dir.exists():
            raise ValueError("Stale site-local blob directory exists; refusing to overwrite it")
        destination_dir.mkdir()
        for item, source_path in sorted(inputs, key=lambda pair: pair[0].name):
            target = destination_dir / item.name
            with source_path.open("rb") as input_file, target.open("xb") as output_file:
                shutil.copyfileobj(input_file, output_file)
            digest = sha256_file(target)
            if digest != item.sha256:
                raise ValueError(f"Staged blob changed during copy: {item.name}")
            blob_hashes.append((item.name, digest))

    def _run_logged(
        self,
        argv: Sequence[str],
        cwd: Path,
        logs_dir: Path,
        commands: list[CommandRecord],
    ) -> None:
        self._run_command(argv, cwd, logs_dir, commands, capture=False)

    def _run_readonly(
        self,
        argv: Sequence[str],
        cwd: Path,
        logs_dir: Path,
        commands: list[CommandRecord],
    ) -> str:
        return self._run_command(argv, cwd, logs_dir, commands, capture=True)

    def _run_command(
        self,
        argv: Sequence[str],
        cwd: Path,
        logs_dir: Path,
        commands: list[CommandRecord],
        *,
        capture: bool,
    ) -> str:
        if not argv or any("\x00" in part for part in argv):
            raise ValueError("Invalid empty or NUL-containing build command")
        log_path = logs_dir / f"{len(commands) + 1:02d}.log"
        temporary_dir = logs_dir.parent / "tmp"
        temporary_dir.mkdir(exist_ok=True)
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(logs_dir.parent),
            "TMPDIR": str(temporary_dir),
            "LC_ALL": "C",
            "LANG": "C",
            "TZ": "UTC",
        }
        try:
            result = subprocess.run(
                list(argv),
                cwd=cwd,
                check=False,
                capture_output=True,
                text=True,
                timeout=BUILD_TIMEOUT,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout or ""
            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="replace")
            log_path.write_text(f"COMMAND TIMED OUT: {argv!r}\n{output}", encoding="utf-8")
            commands.append(CommandRecord(
                tuple(argv),
                -1,
                log_path.relative_to(logs_dir.parent).as_posix(),
                sha256_file(log_path),
            ))
            raise RuntimeError(f"Command timed out: {argv[0]}") from exc
        except OSError as exc:
            log_path.write_text(f"COMMAND START FAILED: {argv!r}\n{exc}\n", encoding="utf-8")
            commands.append(CommandRecord(
                tuple(argv),
                -2,
                log_path.relative_to(logs_dir.parent).as_posix(),
                sha256_file(log_path),
            ))
            raise RuntimeError(f"Unable to start pinned upstream command: {argv[0]}") from exc
        log_text = f"$ {' '.join(argv)}\n{result.stdout or ''}{result.stderr or ''}"
        log_path.write_text(log_text, encoding="utf-8")
        digest = sha256_file(log_path)
        commands.append(CommandRecord(tuple(argv), result.returncode, log_path.relative_to(logs_dir.parent).as_posix(), digest))
        if result.returncode != 0:
            raise RuntimeError(f"Pinned upstream command failed with exit code {result.returncode}: {argv[0]}")
        return result.stdout or "" if capture else ""

    @staticmethod
    def _git_value(source: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(source), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise ValueError(f"Unable to inspect pinned Git source ({' '.join(args)})")
        return result.stdout.strip()

    def _toolchain_versions(self) -> tuple[tuple[str, str], ...]:
        versions: list[tuple[str, str]] = []
        for tool, args in (("git", ("--version",)), ("make", ("--version",)), ("gcc", ("--version",))):
            executable = shutil.which(tool)
            if executable is None:
                raise ValueError(f"Required pinned build environment tool is unavailable: {tool}")
            result = subprocess.run(
                [executable, *args], check=False, capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0 or not result.stdout:
                raise ValueError(f"Cannot record toolchain version for {tool}")
            versions.append((f"{tool}.path", executable))
            versions.append((f"{tool}.version", result.stdout.splitlines()[0].strip()))
        return tuple(versions)

    @staticmethod
    def _write_manifest_exclusive(path: Path, manifest: ArtifactManifest) -> None:
        payload = json.dumps(asdict(manifest), sort_keys=True, indent=2) + "\n"
        with path.open("x", encoding="utf-8", newline="\n") as file_obj:
            file_obj.write(payload)
