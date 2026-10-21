"""Pinned, fail-closed artifact preparation for the three V1 workflows.

The engine never runs on non-Linux hosts. It operates only on explicitly
provided upstream checkouts and output roots outside the AutoLoader source
tree. T430 deliberately has a handoff descriptor instead of a compiled ROM.
"""

from __future__ import annotations

import hashlib
import importlib.resources
import json
import os
import platform
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Mapping, Sequence, cast
from urllib.parse import urlsplit

from autoloader.config import BASE_DIR, BUILD_TIMEOUT
from autoloader.exceptions import ROMBuildUnavailableError
from autoloader.flasher.backup import is_degenerate_dump
from autoloader.flasher.backup_service import BackupManifestStore, BackupSetResult
from autoloader.workflow.compatibility import CompatibilityValidator
from autoloader.workflow.types import (
    ArtifactEvidence,
    BackupEvidence,
    BuildIntentEvidence,
    BackupPairEvidence,
    ChipEvidence,
    ChipTopology,
    CompatibilityDecision,
    DeviceEvidence,
    FirmwareFamily,
    OperationPlan,
    ProgrammerEvidence,
    ProgrammerKind,
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
_BUILD_GIT_USER_NAME = "Libre Core AutoLoader Build"
_BUILD_GIT_USER_EMAIL = "firmware-build@localhost.invalid"
_BUILD_GIT_CONFIG_BYTES = (
    b"[user]\n"
    b"\tname = Libre Core AutoLoader Build\n"
    b"\temail = firmware-build@localhost.invalid\n"
)
_BUILD_GIT_CONFIG_SHA256 = hashlib.sha256(_BUILD_GIT_CONFIG_BYTES).hexdigest()
_BUILD_GIT_CONFIG_MANIFEST_PATH = "<operation-output-directory>/gitconfig"
_LBMK_COREBOOT_REVISION = "fcd716d9a272461da1fdbaf1c048eb5a52c7896b"
_LBMK_COREBOOT_TREE = "e16fe240567cb3fa08619c6a23e51c4af142f6ac"
_LBMK_COREBOOT_ORIGINS = frozenset({
    "https://github.com/coreboot/coreboot.git",
    "https://review.coreboot.org/coreboot",
})
_UBOOT_X86_64_SOURCE_URL = "https://source.denx.de/u-boot/u-boot.git"
_UBOOT_X86_64_BACKUP_URL = "https://github.com/u-boot/u-boot.git"
_UBOOT_X86_64_BASE_REVISION = "f919c3a889f0ec7d63a48b5d0ed064386b0980bd"
_UBOOT_X86_64_BASE_TREE = "66e7e696eeaa875250ff12d586db009bf8631056"
_UBOOT_X86_64_PATCHED_TREE = "12e245c8ad4d68099c7127afd5c61a0e36992b1c"
_UBOOT_X86_64_BASE_CONFIG_SHA256 = "775e0c524aaefcffd4133597104dad638ed7db06cec9f76eb7d31e0e8d934839"
_UBOOT_X86_64_PATCHED_CONFIG_SHA256 = "75ba0582f3240d1bffe9c1cf4710d0620001651e79c5288773607a7d30cf44fb"
_UBOOT_X86_64_TARGET_CFG_SHA256 = "98d8b99e09fc64257aa6d2dfc2d470123e22d42bca718dd34aae107a09f71c9f"
_UBOOT_PACKAGE_CFG_SHA256 = "84088781b2b1630ffc95a5024945986f2ff8c83cc3cc86e474eef52a2f324ce0"
_UBOOT_AMD64_TARGET_CFG_SHA256 = "b7198cb054718c73194c3e7d193a758469c8dac64b0dc2bc5abd0fdb9746afde"
_UBOOT_AMD64_DEFAULT_SHA256 = "16c6839809a9755a39d9674216b40b0b9f299c4869e7a3448ad755f3eec0ae6a"
_X200_UBOOT_PATCH_DIR = "config/u-boot/x86_64/patches"
_X200_UBOOT_OVERLAY_PATH = f"{_X200_UBOOT_PATCH_DIR}/0009-x86-use-output-target-for-efi.patch"
_X200_UBOOT_OVERLAY_SHA256 = "03c17c53372864265bb6716b96ad90022dcc1b84d5ebd7a54fd93820be8b55e4"
_X200_UBOOT_PATCH_INPUTS = (
    ("0001-x86-serial-ns16550-Allow-the-UART-to-be-silently-dis.patch", "03b77b02eaf90bcb35e6fb0c5ba1afb749241a14a94be8a87606a1df9386fa85"),
    ("0002-x86-Avoid-clearing-the-VESA-display.patch", "f7bff931f1f0735d63eb86bfd4a53cd6e93c4fdaf25d04bee804c5c3f64b176e"),
    ("0003-x86-Avoid-clearing-the-VESA-display.patch", "e40b5e133de50befff9b1b1738e20420c0e86873668bed1ae3aec1b309bebf56"),
    ("0004-Support-auto-boot-timeout-delay-bootflow-menu.patch", "36b864aad951ecdb5680ca1d4d87bdf9dfbe82511be840664fbbd34ef8f67b53"),
    ("0005-Libreboot-branding-version-on-the-bootflow-menu.patch", "df7ff820dbe9ebea8b2b19ac7c7f48d2342c1a3e525bbe51644c35873c758081"),
    ("0006-i-made-it-purple.patch", "fdf1baab5844be0a6ed8068afeeb4589fc0e346d72850f2232dc42dd1b007471"),
    ("0008-scripts-dtc-pylibfdt-libfdt.i_shipped-Use-SWIG_Appen.patch", "91ad0bae21b0f459e49e4edebdbd17993af7efffabe379287fb1089d917f77d5"),
)
_X200_UBOOT_OVERLAY_INPUT = (_X200_UBOOT_OVERLAY_PATH.rsplit("/", 1)[1], _X200_UBOOT_OVERLAY_SHA256)
_X200_UBOOT_PATCH_STACK = _X200_UBOOT_PATCH_INPUTS + (_X200_UBOOT_OVERLAY_INPUT,)
_X200_UBOOT_LBMK_BUILD_ROOT = "work/lbmk"
_X200_UBOOT_SOURCE_RELATIVE_PATH = "work/lbmk/src/u-boot/x86_64"
_X200_GRUB_FONT_SOURCE_PATH = "/usr/share/fonts/opentype/unifont/unifont.otf"
_X200_GRUB_FONT_SOURCE_SHA256 = "0e3981ab552231b5a2a870f2b61741903a4bf25c23ef5aeb05fdced1b3c7af4d"
_X200_GRUB_FONT_PACKAGE = "fonts-unifont"
_X200_GRUB_FONT_PACKAGE_VERSION = "1:16.0.04-1build1"
_X200_GRUB_FONT_STAGED_RELATIVE_PATH = "inputs/unifont.otf"
_X200_GRUB_FONT_OVERLAY_RESOURCE = "0010-lbmk-grub-explicit-unifont.patch"
_X200_GRUB_FONT_OVERLAY_PATH = f"autoloader/rom_builder/patches/{_X200_GRUB_FONT_OVERLAY_RESOURCE}"
_X200_GRUB_FONT_OVERLAY_SHA256 = "3356bf29879cf87659230a201ab81f5e45cc5a0e9c80161463a9b868955f036d"
_X200_LBMK_TREE_SH_SHA256 = "de3c294c4401fcc5a6364e644364127e3ddb31d3f0d7b00050350516ae428876"
_X200_LBMK_TREE_SH_PATCHED_SHA256 = "1d2070246558a882a7dcf25a6e54c1b3498111b177318cd44704a549e773a9e7"
_X200_VBOOT_OVERLAY_RESOURCE = "0011-lbmk-vboot-cbfstool-keep-strchr-result-const.patch"
_X200_VBOOT_OVERLAY_RESOURCE_PATH = f"autoloader/rom_builder/patches/{_X200_VBOOT_OVERLAY_RESOURCE}"
_X200_VBOOT_OVERLAY_SHA256 = "172c41a5b92557de0739ec01ec2f37331c9066c8896fbfa3829be89f45bcfaf7"
_X200_VBOOT_OVERLAY_RELATIVE_PATH = (
    "config/submodule/coreboot/default/vboot/patches/"
    "0002-vboot-cbfstool-keep-strchr-result-const.patch"
)
_X200_VBOOT_SOURCE_RELATIVE_PATH = "work/lbmk/src/coreboot/default/3rdparty/vboot"
_X200_VBOOT_BASE_REVISION = "3f94e2c7ed58c4e67d6e7dc6052ec615dbbb9bb4"
_X200_VBOOT_BASE_TREE = "5e90bceae43ffb05372312703105959c1bdca9a1"
_X200_VBOOT_PRE_OVERLAY_TREE = "d45dd5aeaeee63a23d7b92ba38f8ed5641ba2dc7"
_X200_VBOOT_POST_OVERLAY_TREE = "349851764655bb215f6ad63b89a6aa6e9ffa43c5"
_X200_VBOOT_PREIMAGE_SHA256 = "643cbe79bc91455013f23521f646fbc31d44cbc31f0758f57cd9680de6a8e6a4"
_X200_VBOOT_POSTIMAGE_SHA256 = "32f09d3948d5c9471c966767a990545cd2f3293491118ad1ef3f510f62462245"
_X200_VBOOT_SOURCE_FILE = "host/lib/cbfstool.c"
_X200_VBOOT_CACHE_RELATIVE_PATH = "cache/clone/review.coreboot.org/vboot.git"
_X200_VBOOT_ORIGIN_URL = "https://review.coreboot.org/vboot.git"
_X200_VBOOT_EXISTING_PATCH = (
    "config/submodule/coreboot/default/vboot/patches/"
    "0001-extract_vmlinuz.c-Fix-the-bounds-check-on-vmlinuz_he.patch",
    "5008f31ddb78d77fdceb27412d2d56844676b4eb48150fdea8575caf513d8543",
)
_X200_COREBOOT_POST_TREE = "627cbbe4f6e7cba7ff22524b82072f1d7b34321c"
_X200_COREBOOT_CACHE_RELATIVE_PATH = "cache/clone/github.com/coreboot/coreboot.git"
_X200_COREBOOT_ORIGIN_URL = "https://github.com/coreboot/coreboot.git"
_X200_COREBOOT_PATCH_COUNT = 44
_X200_COREBOOT_GENERATED_STATUS = (
    " M 3rdparty/libgfxinit",
    " M 3rdparty/vboot",
    "?? 3rdparty/fspcc36ae2b5775fa7400cb3282680afc0f6cb37a3c/",
    "?? util/cbfstool/fmd_parser.h",
    "?? util/cbfstool/fmd_scanner.h",
    "?? util/vboot_lib/libvboot_host.so",
)
_X200_COREBOOT_GENERATED_FILES = (
    "util/cbfstool/fmd_parser.h",
    "util/cbfstool/fmd_scanner.h",
    "util/vboot_lib/libvboot_host.so",
)
_X200_COREBOOT_GENERATED_HEADER_SOURCES = (
    (
        "util/cbfstool/fmd_parser.h",
        "util/cbfstool/fmd_parser.h_shipped",
        "4e93908f1854f103e832c4c115330689b4bb623f2d94ecdb2dcd7d0d6c9f609f",
    ),
    (
        "util/cbfstool/fmd_scanner.h",
        "util/cbfstool/fmd_scanner.h_shipped",
        "c5b826d911122e6513f1f5c2ad5d30b067caf275e0a949b544b6a853a081cb46",
    ),
)
_X200_COREBOOT_HOST_ELF = "util/vboot_lib/libvboot_host.so"
_X200_COREBOOT_HOST_ELF_MAX_BYTES = 128 * 1024 * 1024
_X200_LIBGFXINIT_BASE_REVISION = "17cfc92f402493979783585b6581efbd98c0cf07"
_X200_LIBGFXINIT_BASE_TREE = "95104a05ac567e1c2b391fbf1a77beb29e5983ce"
_X200_LIBGFXINIT_POST_TREE = "8c338661300d540f564bc6af728a6d61563bc3d3"
_X200_LIBGFXINIT_CACHE_RELATIVE_PATH = "cache/clone/review.coreboot.org/libgfxinit.git"
_X200_LIBGFXINIT_ORIGIN_URL = "https://review.coreboot.org/libgfxinit.git"
_X200_LIBGFXINIT_PATCH_INPUTS = (
    (
        "config/submodule/coreboot/default/libgfxinit/patches/"
        "0001-g45-hw-gfx-gma-plls.adb-Make-reference-clock-frequen.patch",
        "5cb94277754f1711d25c4ddce8c4e5de48afc004ea718bc9e8ff799e6ad29db3",
    ),
    (
        "config/submodule/coreboot/default/libgfxinit/patches/"
        "0002-re-try-EDID-reading-when-it-fails.patch",
        "cbe477a3fb1a64082754269453d8bd98557c93aa0a90931ae251a44dd7d73def",
    ),
)
_X200_VBOOT_PATCH_STACK = (
    _X200_VBOOT_EXISTING_PATCH,
    (_X200_VBOOT_OVERLAY_RELATIVE_PATH, _X200_VBOOT_OVERLAY_SHA256),
)
_X200_LIBGFXINIT_MODULE_CONFIG = (
    "config/submodule/coreboot/default/libgfxinit/module.cfg",
    "d991d35b81f20012cbae7fb9f7fcd8ca55f4e7bb13870ba104b4785bfb2864ad",
)
_X200_VBOOT_MODULE_CONFIG = (
    "config/submodule/coreboot/default/vboot/module.cfg",
    "655585bc4f884c52bdf93b6685e6aa073a92adfa39d96ee73f33ba5ec31aea48",
)
_X200_FSP_MODULE_CONFIG = (
    "config/submodule/coreboot/default/fsp/module.cfg",
    "8d54dcc9ea20155ac2ff18e37adbe60b58820c4b5009cdf07e46ee6c9be652e0",
)
_X200_FSP_REVISION = "cc36ae2b5775fa7400cb3282680afc0f6cb37a3c"
_X200_FSP_TREE = "fd1a467ea43780838eec34f4b893172e2a9b2db6"
_X200_FSP_RELATIVE_PATH = "work/lbmk/src/coreboot/default/3rdparty/fspcc36ae2b5775fa7400cb3282680afc0f6cb37a3c"
_X200_FSP_CACHE_RELATIVE_PATH = "cache/clone/review.coreboot.org/fsp.git"
_X200_FSP_ORIGIN_URL = "https://review.coreboot.org/fsp.git"
_X200_GRUB_PACKAGE_CFG_SHA256 = "6e8886854534db158f8560d7ec99e6eccd2f2c4ca6100aa0c9442c22558081c8"
_X200_GRUB_TARGET_CFG_SHA256 = "a23bb6648ce1f893504c19d957a53de9de8a861f92ae123ade094dade4e8f5a8"
_X200_GRUB_PATCH_INPUTS = (
    ("0001-mitigate-grub-s-missing-characters-for-borders-arrow.patch", "049c385db82d971343e769520964bf557789f727ab0ab7d2d95ed2289c88aac0"),
    ("0002-say-the-name-libreboot-in-the-grub-menu.patch", "93ba1fe44bb731049160fc7ab87641c544cada6741b81b1463f53cb7226fbeb1"),
    ("0003-at_keyboard-coreboot-force-scancodes2-translate.patch", "40dd3c99aa16cea84e44dde75596545a566bf9ff9de08aec31b4f7ca0ac5e44c"),
    ("0004-keylayouts-don-t-print-Unknown-key-message.patch", "1a0bacbfa5191540d3487e1ebac848e19424a1b15cff9c44e9d4526a8d9a4017"),
    ("0005-don-t-print-missing-prefix-errors-on-the-screen.patch", "fdc6501d624697e4d8c2bfdb26cb5ebbecd1aeb566343e273a169f39dda5c8cd"),
    ("0006-don-t-print-error-if-module-not-found.patch", "dacbcf7157605a95c1bf3f974aba41d6f6e70a9b52de4670688cf543bbd50799"),
    ("0007-don-t-print-empty-error-messages.patch", "ee48e0449e968a28789da04904e7e2d6a58f698b66ff2b8864610a90848cd4df"),
    ("0008-kern-coreboot-mmap-Map-to-reserved.patch", "203300078af56bfcc6b9e7958c0cdb24c3827873c3e1b0cd36eb9ce3db712504"),
    ("0009-Revert-configure-Check-linker-for-image-base-support.patch", "71b838b5f9796954e156400100ef2503c04659f0cec04df172c20611a3cf9945"),
    ("0010-Revert-configure-Print-a-more-helpful-error-if-autoc.patch", "4a00cc1ab2b3c04d0251a50f7309a4b82c9943a901ef92207f54baae346d46d6"),
)
_X200_GRUB_SOURCE_URL = "git://git.savannah.gnu.org/grub.git"
_X200_GRUB_CACHE_RELATIVE_PATH = "cache/clone/git.savannah.gnu.org/grub.git"
_X200_GRUB_BASE_REVISION = "eaa3b8f0f90605a82c6bfda4c5c4b73c58eb81ac"
_X200_GRUB_BASE_TREE = "c10cf577627e980308fb0cb3785cdc669c2582d9"
_X200_GRUB_PATCH_TREE_IDS = (
    "06f467b81416069686882c1554571c5ab426e87c",
    "1ad4cbd5d89f8a5106ca2965f16220add9f0aea5",
    "a41a75e38ffa6418767033789380ed825abb23bb",
    "8a8418792d0940c5d31616fefa9b2c11666eefef",
    "46fba2773ac4876e9e0872947deb12d352ae989e",
    "3cd9b4f04a6df77312f89d023019d277e580bed3",
    "d9dfbf8ca26f1851d30bcb929354ae0aa28a6689",
    "c660ecae5afe2b438891c417bbe62b889d0e37b2",
    "7349efc9643a5b4b837bfa825c25d0292ae7c048",
    "9e76050f8ab01306e51fb28ab88c1f8481c9e26a",
)
_X200_GRUB_PATCHED_TREE = _X200_GRUB_PATCH_TREE_IDS[-1]
_X200_GRUB_CONFIGURE_AC_SHA256 = "48c4580fd364054b725eb6b66307b4f326d12471e43883596fdb1508fded8a33"
_X200_GRUB_CONFIG_LOG_RELATIVE_PATH = "work/lbmk/src/grub/default/config.log"
_X200_GRUB_PAYLOAD_RELATIVE_PATH = "work/lbmk/elf/grub/default/default/payload/grub.elf"
_X200_GRUB_CONFIGURE_FIXED_ARGS = ("--with-platform=coreboot", "--disable-werror")


def _uboot_cache_relative_path(repository_url: str) -> str:
    parsed = urlsplit(repository_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
    ):
        raise ValueError("U-Boot cache URL is not a plain pinned HTTPS repository URL")
    parts = PurePosixPath(parsed.path.lstrip("/")).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("U-Boot cache URL contains an invalid repository path")
    return PurePosixPath("cache", "clone", parsed.hostname, *parts).as_posix()


def _uboot_x86_64_cache_origins() -> dict[str, str]:
    return {
        _uboot_cache_relative_path(_UBOOT_X86_64_SOURCE_URL): _UBOOT_X86_64_SOURCE_URL,
        _uboot_cache_relative_path(_UBOOT_X86_64_BACKUP_URL): _UBOOT_X86_64_BACKUP_URL,
    }


def _assert_no_symlink_components(path: Path, *, root: Path) -> None:
    resolved_root = root.resolve(strict=True)
    candidate = Path(path)
    try:
        relative = candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("Pinned U-Boot cache path escapes the disposable lbmk root") from exc
    current = resolved_root
    for component in relative.parts:
        if component in {"", ".", ".."}:
            raise ValueError("Pinned U-Boot cache path contains traversal")
        current = current / component
        if current.is_symlink():
            raise ValueError("Pinned U-Boot cache path contains a symlink component")


def _validate_x200_uboot_cache_binding(
    build_source: Path,
    worktree_origin: str,
    cache_toplevel: str,
    cache_git_dir: str,
    cache_remote_url: str,
    cache_base_revision: str,
    cache_base_tree: str,
) -> str:
    """Bind lbmk's local worktree origin to its exact in-root URL cache."""
    root = Path(build_source).resolve(strict=True)
    if Path(build_source).is_symlink() or not root.is_dir():
        raise ValueError("Disposable lbmk root must be a real directory")
    origin_path = Path(worktree_origin)
    if not origin_path.is_absolute() or ".." in origin_path.parts:
        raise ValueError("U-Boot worktree origin must be an absolute in-root lbmk cache path")
    origins = _uboot_x86_64_cache_origins()
    for relative, expected_remote in origins.items():
        expected_path = root.joinpath(*PurePosixPath(relative).parts)
        if origin_path != expected_path:
            continue
        _assert_no_symlink_components(expected_path, root=root)
        if not expected_path.is_dir():
            raise ValueError("Pinned U-Boot cache repository is missing")
        expected_root = expected_path.resolve(strict=True)
        observed_toplevel = Path(cache_toplevel).resolve(strict=True)
        observed_git_dir = Path(cache_git_dir).resolve(strict=True)
        expected_git_dir = expected_root / ".git"
        if observed_toplevel != expected_root or observed_git_dir != expected_git_dir:
            raise ValueError("U-Boot cache Git metadata is not rooted in the expected in-root checkout")
        if cache_remote_url != expected_remote:
            raise ValueError("U-Boot cache repository remote differs from the pinned package URL")
        if (
            cache_base_revision != _UBOOT_X86_64_BASE_REVISION
            or cache_base_tree != _UBOOT_X86_64_BASE_TREE
        ):
            raise ValueError("U-Boot cache does not contain the exact pinned base commit and tree")
        return relative
    raise ValueError("U-Boot worktree origin is not one of the exact pinned in-root cache paths")


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
class X200UBootDependencyEvidence:
    """Typed provenance for the exact patched x86_64 U-Boot used by lbmk."""

    source_url: str
    backup_url: str
    package_config_sha256: str
    target_config_sha256: str
    amd64_target_config_sha256: str
    amd64_default_config_sha256: str
    target_tree: str
    base_revision: str
    base_tree_id: str
    base_config_sha256: str
    overlay_path: str
    overlay_sha256: str
    patched_config_sha256: str
    patch_inputs: tuple[tuple[str, str], ...]
    patch_commits: tuple[str, ...]
    resulting_revision: str
    resulting_tree_id: str
    cache_origin_relative_path: str
    resulting_origin_url: str
    base_ancestor_verified: bool
    tracked_worktree_clean: bool
    objcopy_path: str
    objcopy_sha256: str
    objcopy_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "patch_inputs", tuple(tuple(item) for item in self.patch_inputs))
        object.__setattr__(self, "patch_commits", tuple(self.patch_commits))
        fixed_values = (
            (self.source_url, _UBOOT_X86_64_SOURCE_URL),
            (self.backup_url, _UBOOT_X86_64_BACKUP_URL),
            (self.package_config_sha256, _UBOOT_PACKAGE_CFG_SHA256),
            (self.target_config_sha256, _UBOOT_X86_64_TARGET_CFG_SHA256),
            (self.amd64_target_config_sha256, _UBOOT_AMD64_TARGET_CFG_SHA256),
            (self.amd64_default_config_sha256, _UBOOT_AMD64_DEFAULT_SHA256),
            (self.target_tree, "x86_64"),
            (self.base_revision, _UBOOT_X86_64_BASE_REVISION),
            (self.base_tree_id, _UBOOT_X86_64_BASE_TREE),
            (self.base_config_sha256, _UBOOT_X86_64_BASE_CONFIG_SHA256),
            (self.overlay_path, _X200_UBOOT_OVERLAY_PATH),
            (self.overlay_sha256, _X200_UBOOT_OVERLAY_SHA256),
            (self.patched_config_sha256, _UBOOT_X86_64_PATCHED_CONFIG_SHA256),
            (self.patch_inputs, _X200_UBOOT_PATCH_STACK),
        )
        if any(actual != expected for actual, expected in fixed_values):
            raise ValueError("X200 U-Boot dependency evidence differs from the pinned source and overlay")
        if (
            len(self.patch_commits) != len(_X200_UBOOT_PATCH_STACK)
            or len(set(self.patch_commits)) != len(self.patch_commits)
            or any(not _GIT_OBJECT_RE.fullmatch(commit) for commit in self.patch_commits)
            or self.patch_commits[-1] != self.resulting_revision
            or not _GIT_OBJECT_RE.fullmatch(self.resulting_revision)
            or not _GIT_OBJECT_RE.fullmatch(self.resulting_tree_id)
            or self.resulting_tree_id != _UBOOT_X86_64_PATCHED_TREE
            or self.cache_origin_relative_path not in _uboot_x86_64_cache_origins()
            or self.resulting_origin_url != _uboot_x86_64_cache_origins()[self.cache_origin_relative_path]
            or not self.base_ancestor_verified
            or not self.tracked_worktree_clean
        ):
            raise ValueError("X200 U-Boot dependency commit chain or clean-tree evidence is incomplete")
        if not Path(self.objcopy_path).is_absolute() or not _SHA256_RE.fullmatch(self.objcopy_sha256):
            raise ValueError("X200 objcopy path or digest is invalid")
        if not self.objcopy_version.startswith("GNU objcopy "):
            raise ValueError("X200 dependency build requires a recorded GNU objcopy version")


@dataclass(frozen=True)
class X200GrubFontEvidence:
    """Immutable provenance for the Ubuntu Unifont input and pinned GRUB output."""

    source_font_path: str
    source_font_sha256: str
    source_package: str
    source_package_version: str
    staged_font_relative_path: str
    staged_font_sha256: str
    overlay_resource_path: str
    overlay_resource_sha256: str
    lbmk_tree_sh_sha256: str
    lbmk_tree_sh_patched_sha256: str
    grub_source_relative_path: str
    grub_repository_url: str
    grub_cache_relative_path: str
    grub_base_revision: str
    grub_base_tree_id: str
    grub_patch_inputs: tuple[tuple[str, str], ...]
    grub_patch_commits: tuple[str, ...]
    grub_patch_tree_ids: tuple[str, ...]
    grub_result_revision: str
    grub_result_tree_id: str
    configure_log_relative_path: str
    configure_log_sha256: str
    configure_argv: tuple[str, ...]
    generated_payload_relative_path: str
    generated_payload_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "grub_patch_inputs", tuple(tuple(item) for item in self.grub_patch_inputs))
        object.__setattr__(self, "grub_patch_commits", tuple(self.grub_patch_commits))
        object.__setattr__(self, "grub_patch_tree_ids", tuple(self.grub_patch_tree_ids))
        object.__setattr__(self, "configure_argv", tuple(self.configure_argv))
        fixed_values = (
            (self.source_font_path, _X200_GRUB_FONT_SOURCE_PATH),
            (self.source_font_sha256, _X200_GRUB_FONT_SOURCE_SHA256),
            (self.source_package, _X200_GRUB_FONT_PACKAGE),
            (self.source_package_version, _X200_GRUB_FONT_PACKAGE_VERSION),
            (self.staged_font_relative_path, _X200_GRUB_FONT_STAGED_RELATIVE_PATH),
            (self.staged_font_sha256, _X200_GRUB_FONT_SOURCE_SHA256),
            (self.overlay_resource_path, _X200_GRUB_FONT_OVERLAY_PATH),
            (self.overlay_resource_sha256, _X200_GRUB_FONT_OVERLAY_SHA256),
            (self.lbmk_tree_sh_sha256, _X200_LBMK_TREE_SH_SHA256),
            (self.lbmk_tree_sh_patched_sha256, _X200_LBMK_TREE_SH_PATCHED_SHA256),
            (self.grub_source_relative_path, "work/lbmk/src/grub/default"),
            (self.grub_repository_url, _X200_GRUB_SOURCE_URL),
            (self.grub_cache_relative_path, _X200_GRUB_CACHE_RELATIVE_PATH),
            (self.grub_base_revision, _X200_GRUB_BASE_REVISION),
            (self.grub_base_tree_id, _X200_GRUB_BASE_TREE),
            (self.grub_patch_inputs, _X200_GRUB_PATCH_INPUTS),
            (self.grub_patch_tree_ids, _X200_GRUB_PATCH_TREE_IDS),
            (self.grub_result_tree_id, _X200_GRUB_PATCHED_TREE),
            (self.configure_log_relative_path, _X200_GRUB_CONFIG_LOG_RELATIVE_PATH),
            (self.configure_argv[0] if self.configure_argv else None, "./configure"),
            (
                self.configure_argv[1] if len(self.configure_argv) > 1 else None,
                "--with-unifont=<operation-output-directory>/inputs/unifont.otf",
            ),
            (self.configure_argv[2:] if len(self.configure_argv) >= 2 else (), _X200_GRUB_CONFIGURE_FIXED_ARGS),
            (self.generated_payload_relative_path, _X200_GRUB_PAYLOAD_RELATIVE_PATH),
        )
        if any(actual != expected for actual, expected in fixed_values):
            raise ValueError("X200 GRUB font evidence differs from the pinned lbmk/GRUB recipe")
        if len(self.configure_argv) != 4:
            raise ValueError("X200 GRUB evidence must record the exact pinned configure argument vector")
        if (
            len(self.grub_patch_commits) != len(_X200_GRUB_PATCH_INPUTS)
            or len(set(self.grub_patch_commits)) != len(self.grub_patch_commits)
            or any(not _GIT_OBJECT_RE.fullmatch(commit) for commit in self.grub_patch_commits)
            or self.grub_patch_commits[-1] != self.grub_result_revision
            or not _GIT_OBJECT_RE.fullmatch(self.grub_result_revision)
            or len(self.grub_patch_tree_ids) != len(self.grub_patch_commits)
            or any(not _GIT_OBJECT_RE.fullmatch(tree) for tree in self.grub_patch_tree_ids)
            or self.grub_patch_tree_ids[-1] != self.grub_result_tree_id
        ):
            raise ValueError("X200 GRUB generated commit IDs or per-patch tree evidence is invalid")
        for digest in (
            self.configure_log_sha256,
            self.generated_payload_sha256,
        ):
            if not _SHA256_RE.fullmatch(digest):
                raise ValueError("X200 GRUB font evidence contains an invalid SHA-256")
        if any(not _GIT_OBJECT_RE.fullmatch(commit) for commit in self.grub_patch_commits):
            raise ValueError("X200 GRUB patch chain contains a non-immutable commit ID")


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
    build_intent: BuildIntentEvidence | None = None

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
class X200GeneratedFileEvidence:
    """Typed evidence for the exact generated files permitted in patched Coreboot."""

    relative_path: str
    size_bytes: int
    sha256: str
    kind: str
    source_relative_path: str | None = None
    source_sha256: str | None = None
    elf_class: str | None = None
    elf_type: str | None = None
    elf_machine: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.relative_path, str) or type(self.size_bytes) is not int:
            raise ValueError("Generated source evidence requires a text path and integer size")
        if not isinstance(self.sha256, str) or not isinstance(self.kind, str):
            raise ValueError("Generated source evidence hash and kind must be text")
        path = PurePosixPath(self.relative_path)
        if path.is_absolute() or ".." in path.parts or "\\" in self.relative_path or "\x00" in self.relative_path:
            raise ValueError("Generated source evidence path must be safe and repository-relative")
        if self.size_bytes <= 0 or not _SHA256_RE.fullmatch(self.sha256):
            raise ValueError("Generated source evidence requires a positive size and lowercase SHA-256")
        if self.kind == "copied-shipped-header":
            if (
                self.source_relative_path is None
                or self.source_sha256 is None
                or not _SHA256_RE.fullmatch(self.source_sha256)
                or self.elf_class is not None
                or self.elf_type is not None
                or self.elf_machine is not None
            ):
                raise ValueError("Copied generated header evidence must bind its shipped source only")
        elif self.kind == "elf-shared-object":
            if (
                self.source_relative_path is not None
                or self.source_sha256 is not None
                or self.elf_class != "ELF64"
                or self.elf_type != "ET_DYN"
                or self.elf_machine != "EM_X86_64"
            ):
                raise ValueError("Generated host library must record the bounded x86-64 ELF shared-object identity")
        else:
            raise ValueError("Generated source evidence has an unsupported file kind")
        if self.source_relative_path is not None:
            if not isinstance(self.source_relative_path, str):
                raise ValueError("Generated source evidence source path must be text or null")
            source = PurePosixPath(self.source_relative_path)
            if source.is_absolute() or ".." in source.parts or "\\" in self.source_relative_path or "\x00" in self.source_relative_path:
                raise ValueError("Generated source evidence source path must be repository-relative")
        for identity in (self.source_sha256, self.elf_class, self.elf_type, self.elf_machine):
            if identity is not None and not isinstance(identity, str):
                raise ValueError("Generated source evidence optional identity fields must be text or null")


@dataclass(frozen=True)
class X200NestedRepositoryEvidence:
    """Exact base-to-patched identity for one lbmk-managed nested repository."""

    role: str
    relative_path: str
    repository_url: str
    cache_origin_relative_path: str
    base_revision: str
    base_tree_id: str
    resulting_revision: str
    resulting_tree_id: str
    patch_inputs: tuple[tuple[str, str], ...]
    patch_revisions: tuple[str, ...]
    patch_tree_ids: tuple[str, ...]
    source_file_hashes: tuple[tuple[str, str], ...] = ()
    source_file_preimage_hashes: tuple[tuple[str, str], ...] = ()
    generated_status: tuple[str, ...] = ()
    generated_files: tuple[X200GeneratedFileEvidence, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "patch_inputs", tuple(tuple(item) for item in self.patch_inputs))
        object.__setattr__(self, "patch_revisions", tuple(self.patch_revisions))
        object.__setattr__(self, "patch_tree_ids", tuple(self.patch_tree_ids))
        object.__setattr__(self, "source_file_hashes", tuple(tuple(item) for item in self.source_file_hashes))
        object.__setattr__(self, "source_file_preimage_hashes", tuple(tuple(item) for item in self.source_file_preimage_hashes))
        object.__setattr__(self, "generated_status", tuple(self.generated_status))
        object.__setattr__(self, "generated_files", tuple(self.generated_files))
        if not self.role or not self.relative_path or not self.cache_origin_relative_path:
            raise ValueError("X200 nested source evidence requires a role and operation-relative paths")
        if not self.repository_url.startswith("https://") or "@" in self.repository_url:
            raise ValueError("X200 nested source evidence requires a credential-free HTTPS repository")
        if any(not _GIT_OBJECT_RE.fullmatch(value) for value in (
            self.base_revision,
            self.base_tree_id,
            self.resulting_revision,
            self.resulting_tree_id,
            *self.patch_revisions,
            *self.patch_tree_ids,
        )):
            raise ValueError("X200 nested source evidence contains a malformed Git identity")
        if len(self.patch_inputs) != len(self.patch_tree_ids) or len(self.patch_inputs) != len(self.patch_revisions):
            raise ValueError("X200 nested source patch inputs, commits and result trees must correspond one-to-one")
        if len(set(self.patch_revisions)) != len(self.patch_revisions):
            raise ValueError("X200 nested source generated patch commits must be unique")
        if self.patch_revisions and self.patch_revisions[-1] != self.resulting_revision:
            raise ValueError("X200 nested source final revision must match its final applied patch commit")
        for path in (
            self.relative_path,
            self.cache_origin_relative_path,
            *(path for path, _ in self.patch_inputs),
            *(path for path, _ in self.source_file_hashes),
            *(path for path, _ in self.source_file_preimage_hashes),
        ):
            candidate = PurePosixPath(path)
            if candidate.is_absolute() or ".." in candidate.parts or "\\" in path or "\x00" in path:
                raise ValueError("X200 nested source paths must be safe repository-relative paths")
        if any(not isinstance(item, str) for item in self.generated_status):
            raise ValueError("X200 nested source generated status must contain text rows")
        if any(not isinstance(item, X200GeneratedFileEvidence) for item in self.generated_files):
            raise ValueError("Generated nested source inventory must contain typed file evidence")
        if any(not _SHA256_RE.fullmatch(digest) for _, digest in (
            *self.patch_inputs, *self.source_file_hashes, *self.source_file_preimage_hashes
        )):
            raise ValueError("X200 nested source evidence contains a malformed SHA-256")
        if self.role != "coreboot" and self.generated_files:
            raise ValueError("Generated Coreboot file inventory cannot be attached to an unrelated nested repository")


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
    nested_repositories: tuple[X200NestedRepositoryEvidence, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "inventory", tuple(self.inventory))
        object.__setattr__(self, "source_file_hashes", tuple(self.source_file_hashes))
        object.__setattr__(self, "nested_repositories", tuple(self.nested_repositories))
        if not _SHA256_RE.fullmatch(self.tool_sha256):
            raise ValueError("CBFS inspection requires the exact tool binary SHA-256")
        if not self.inventory:
            raise ValueError("CBFS inspection requires a complete non-empty inventory")
        if not self.source_file_hashes or any(not _SHA256_RE.fullmatch(digest) for _, digest in self.source_file_hashes):
            raise ValueError("CBFS inspection requires hashed parser source files")


def _validate_x200_coreboot_generated_file_evidence(
    evidence: X200NestedRepositoryEvidence,
) -> None:
    if evidence.role != "coreboot":
        raise ValueError("Generated-file evidence is valid only for the Coreboot nested repository")
    files = evidence.generated_files
    if tuple(item.relative_path for item in files) != _X200_COREBOOT_GENERATED_FILES:
        raise ValueError("X200 Coreboot generated-file inventory is missing, extra, duplicated, or out of order")
    by_path = {item.relative_path: item for item in files}
    for output_path, source_path, source_hash in _X200_COREBOOT_GENERATED_HEADER_SOURCES:
        item = by_path[output_path]
        if (
            item.kind != "copied-shipped-header"
            or item.source_relative_path != source_path
            or item.source_sha256 != source_hash
            or item.sha256 != source_hash
        ):
            raise ValueError("X200 generated cbfstool header evidence differs from the exact shipped source")
    library = by_path[_X200_COREBOOT_HOST_ELF]
    if (
        library.kind != "elf-shared-object"
        or library.source_relative_path is not None
        or library.source_sha256 is not None
        or library.elf_class != "ELF64"
        or library.elf_type != "ET_DYN"
        or library.elf_machine != "EM_X86_64"
        or library.size_bytes > _X200_COREBOOT_HOST_ELF_MAX_BYTES
    ):
        raise ValueError("X200 generated vboot host library evidence is not a bounded x86-64 ELF shared object")


def _validate_x200_nested_repository_evidence(
    repositories: Sequence[X200NestedRepositoryEvidence],
) -> None:
    """Require the exact lbmk-managed Coreboot and submodule result contract."""
    rows = tuple(repositories)
    if tuple(item.role for item in rows) != ("coreboot", "libgfxinit", "vboot", "fsp"):
        raise ValueError("X200 nested repository evidence is incomplete, duplicated, or out of order")

    coreboot, libgfxinit, vboot, fsp = rows
    operation_prefix = "work/lbmk/"
    expected_coreboot_prefix = f"{operation_prefix}config/coreboot/default/patches/"
    if (
        coreboot.relative_path != "work/lbmk/src/coreboot/default"
        or coreboot.repository_url != _X200_COREBOOT_ORIGIN_URL
        or coreboot.cache_origin_relative_path != f"{operation_prefix}{_X200_COREBOOT_CACHE_RELATIVE_PATH}"
        or coreboot.base_revision != _LBMK_COREBOOT_REVISION
        or coreboot.base_tree_id != _LBMK_COREBOOT_TREE
        or coreboot.resulting_tree_id != _X200_COREBOOT_POST_TREE
        or len(coreboot.patch_inputs) != _X200_COREBOOT_PATCH_COUNT
        or any(not path.startswith(expected_coreboot_prefix) for path, _ in coreboot.patch_inputs)
        or len({path for path, _ in coreboot.patch_inputs}) != _X200_COREBOOT_PATCH_COUNT
        or len(coreboot.patch_tree_ids) != _X200_COREBOOT_PATCH_COUNT
        or len(coreboot.patch_revisions) != _X200_COREBOOT_PATCH_COUNT
        or coreboot.patch_tree_ids[-1] != _X200_COREBOOT_POST_TREE
        or coreboot.generated_status != _X200_COREBOOT_GENERATED_STATUS
        or dict(coreboot.source_file_hashes) != {item.path: item.sha256 for item in _LBMK_COREBOOT_CBFSTOOL_SOURCES}
        or coreboot.source_file_preimage_hashes
    ):
        raise ValueError("X200 post-lbmk Coreboot source provenance differs from its exact patched tree")
    _validate_x200_coreboot_generated_file_evidence(coreboot)

    if (
        libgfxinit.relative_path != "work/lbmk/src/coreboot/default/3rdparty/libgfxinit"
        or libgfxinit.repository_url != _X200_LIBGFXINIT_ORIGIN_URL
        or libgfxinit.cache_origin_relative_path != f"{operation_prefix}{_X200_LIBGFXINIT_CACHE_RELATIVE_PATH}"
        or libgfxinit.base_revision != _X200_LIBGFXINIT_BASE_REVISION
        or libgfxinit.base_tree_id != _X200_LIBGFXINIT_BASE_TREE
        or libgfxinit.resulting_tree_id != _X200_LIBGFXINIT_POST_TREE
        or libgfxinit.patch_inputs != tuple(
            (f"{operation_prefix}{path}", digest) for path, digest in _X200_LIBGFXINIT_PATCH_INPUTS
        )
        or len(libgfxinit.patch_tree_ids) != len(_X200_LIBGFXINIT_PATCH_INPUTS)
        or len(libgfxinit.patch_revisions) != len(_X200_LIBGFXINIT_PATCH_INPUTS)
        or libgfxinit.patch_tree_ids[-1] != _X200_LIBGFXINIT_POST_TREE
        or libgfxinit.source_file_hashes
        or libgfxinit.source_file_preimage_hashes
        or libgfxinit.generated_status
        or libgfxinit.generated_files
    ):
        raise ValueError("X200 post-lbmk libgfxinit provenance differs from its exact patched tree")

    if (
        vboot.relative_path != _X200_VBOOT_SOURCE_RELATIVE_PATH
        or vboot.repository_url != _X200_VBOOT_ORIGIN_URL
        or vboot.cache_origin_relative_path != f"{operation_prefix}{_X200_VBOOT_CACHE_RELATIVE_PATH}"
        or vboot.base_revision != _X200_VBOOT_BASE_REVISION
        or vboot.base_tree_id != _X200_VBOOT_BASE_TREE
        or vboot.resulting_tree_id != _X200_VBOOT_POST_OVERLAY_TREE
        or vboot.patch_inputs != tuple(
            (f"{operation_prefix}{path}", digest) for path, digest in _X200_VBOOT_PATCH_STACK
        )
        or len(vboot.patch_revisions) != len(_X200_VBOOT_PATCH_STACK)
        or vboot.patch_tree_ids != (_X200_VBOOT_PRE_OVERLAY_TREE, _X200_VBOOT_POST_OVERLAY_TREE)
        or dict(vboot.source_file_hashes) != {_X200_VBOOT_SOURCE_FILE: _X200_VBOOT_POSTIMAGE_SHA256}
        or dict(vboot.source_file_preimage_hashes) != {_X200_VBOOT_SOURCE_FILE: _X200_VBOOT_PREIMAGE_SHA256}
        or vboot.generated_status
        or vboot.generated_files
    ):
        raise ValueError("X200 vboot overlay provenance differs from its exact pre/post source state")

    if (
        fsp.relative_path != _X200_FSP_RELATIVE_PATH
        or fsp.repository_url != _X200_FSP_ORIGIN_URL
        or fsp.cache_origin_relative_path != f"{operation_prefix}{_X200_FSP_CACHE_RELATIVE_PATH}"
        or fsp.base_revision != _X200_FSP_REVISION
        or fsp.base_tree_id != _X200_FSP_TREE
        or fsp.resulting_revision != _X200_FSP_REVISION
        or fsp.resulting_tree_id != _X200_FSP_TREE
        or fsp.patch_inputs
        or fsp.patch_tree_ids
        or fsp.patch_revisions
        or fsp.source_file_hashes
        or fsp.source_file_preimage_hashes
        or fsp.generated_status
        or fsp.generated_files
    ):
        raise ValueError("X200 fetched FSP source provenance differs from its exact pinned checkout")


def _regular_generated_file(checkout: Path, relative_path: str) -> Path:
    candidate = checkout.joinpath(*PurePosixPath(relative_path).parts)
    _assert_no_symlink_components(candidate, root=checkout)
    try:
        mode = candidate.lstat().st_mode
    except OSError as exc:
        raise ValueError(f"Required generated Coreboot file is missing: {relative_path}") from exc
    if not stat.S_ISREG(mode):
        raise ValueError(f"Generated Coreboot path must be a regular non-symlink file: {relative_path}")
    return candidate


def _elf_shared_object_identity(path: Path) -> tuple[int, str, str, str]:
    """Read and bound-check the ELF64 header without loading the library."""
    file_size = path.stat().st_size
    if file_size < 64 or file_size > _X200_COREBOOT_HOST_ELF_MAX_BYTES or not os.access(path, os.X_OK):
        raise ValueError("Generated vboot host library is empty, oversized, or non-executable")
    with path.open("rb") as stream:
        header = stream.read(64)
    if (
        len(header) != 64
        or header[:4] != b"\x7fELF"
        or header[4] != 2
        or header[5] != 1
        or header[6] != 1
    ):
        raise ValueError("Generated vboot host library has an unsupported ELF identity")
    elf_type = int.from_bytes(header[16:18], "little")
    machine = int.from_bytes(header[18:20], "little")
    elf_version = int.from_bytes(header[20:24], "little")
    program_offset = int.from_bytes(header[32:40], "little")
    header_size = int.from_bytes(header[52:54], "little")
    program_entry_size = int.from_bytes(header[54:56], "little")
    program_count = int.from_bytes(header[56:58], "little")
    program_end = program_offset + program_entry_size * program_count
    if (
        elf_type != 3
        or machine != 62
        or elf_version != 1
        or header_size != 64
        or program_offset < 64
        or program_entry_size != 56
        or not 1 <= program_count <= 256
        or program_end > file_size
    ):
        raise ValueError("Generated vboot host library ELF header is malformed or out of bounds")
    with path.open("rb") as stream:
        stream.seek(program_offset)
        program_headers = stream.read(program_entry_size * program_count)
    if len(program_headers) != program_entry_size * program_count:
        raise ValueError("Generated vboot host library ELF program-header table is truncated")
    for index in range(program_count):
        start = index * program_entry_size
        header_entry = program_headers[start:start + program_entry_size]
        segment_offset = int.from_bytes(header_entry[8:16], "little")
        segment_file_size = int.from_bytes(header_entry[32:40], "little")
        if segment_offset + segment_file_size > file_size:
            raise ValueError("Generated vboot host library ELF segment extends beyond the file")
    return file_size, "ELF64", "ET_DYN", "EM_X86_64"


def _capture_x200_coreboot_generated_files(checkout: Path) -> tuple[X200GeneratedFileEvidence, ...]:
    """Capture exactly the deterministic shipped-header copies and host ELF output."""
    rows: list[X200GeneratedFileEvidence] = []
    for output_relative, source_relative, expected_hash in _X200_COREBOOT_GENERATED_HEADER_SOURCES:
        output = _regular_generated_file(checkout, output_relative)
        source = _regular_generated_file(checkout, source_relative)
        source_bytes = source.read_bytes()
        output_bytes = output.read_bytes()
        source_hash = hashlib.sha256(source_bytes).hexdigest()
        if source_hash != expected_hash or output_bytes != source_bytes:
            raise ValueError(f"Generated Coreboot header differs from its exact shipped source: {output_relative}")
        rows.append(X200GeneratedFileEvidence(
            relative_path=output_relative,
            size_bytes=len(output_bytes),
            sha256=hashlib.sha256(output_bytes).hexdigest(),
            kind="copied-shipped-header",
            source_relative_path=source_relative,
            source_sha256=source_hash,
        ))
    library = _regular_generated_file(checkout, _X200_COREBOOT_HOST_ELF)
    library_size, elf_class, elf_type, elf_machine = _elf_shared_object_identity(library)
    rows.append(X200GeneratedFileEvidence(
        relative_path=_X200_COREBOOT_HOST_ELF,
        size_bytes=library_size,
        sha256=sha256_file(library),
        kind="elf-shared-object",
        elf_class=elf_class,
        elf_type=elf_type,
        elf_machine=elf_machine,
    ))
    return tuple(rows)


def _verify_x200_coreboot_generated_files(
    operation_dir: str | Path,
    evidence: X200NestedRepositoryEvidence,
    *,
    check_repository_status: bool = True,
) -> None:
    """Rehash generated outputs and exact status at reopen and workflow acceptance."""
    _validate_x200_coreboot_generated_file_evidence(evidence)
    operation = Path(operation_dir).absolute()
    if operation.is_symlink() or not operation.is_dir():
        raise ValueError("X200 artifact operation directory is missing or symlinked")
    current = Path(operation.anchor)
    for component in operation.parts[1:]:
        current = current / component
        if current.is_symlink():
            raise ValueError("X200 artifact operation directory has a symlinked path component")
    checkout = operation.joinpath(*PurePosixPath(evidence.relative_path).parts)
    _assert_no_symlink_components(checkout, root=operation)
    if not checkout.is_dir():
        raise ValueError("Persisted X200 Coreboot generated-file checkout is missing")
    if check_repository_status:
        git_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        git_env.update({
            "GIT_CONFIG_GLOBAL": str(operation / _BUILD_GIT_CONFIG_MANIFEST_PATH.removeprefix("<operation-output-directory>/")),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        })
        status = subprocess.run(
            ["git", "-C", str(checkout), "status", "--porcelain=v1", "--untracked-files=all"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=git_env,
        )
        if status.returncode != 0:
            raise ValueError("Unable to recheck persisted X200 Coreboot generated-file source status")
        observed_status = tuple(filter(None, status.stdout.rstrip("\r\n").splitlines()))
        if observed_status != _X200_COREBOOT_GENERATED_STATUS:
            raise ValueError("Persisted X200 Coreboot generated-file source status changed")
    if _capture_x200_coreboot_generated_files(checkout) != evidence.generated_files:
        raise ValueError("Persisted X200 Coreboot generated-file bytes differ from typed evidence")


def _validate_x200_lbmk_overlay_root_state(
    *,
    revision: str,
    tree_id: str,
    origin: str,
    status: Sequence[str],
    after_build: bool,
) -> None:
    expected_status = (
        (
            " M include/tree.sh",
            f"?? {_X200_UBOOT_OVERLAY_PATH}",
            f"?? {_X200_VBOOT_OVERLAY_RELATIVE_PATH}",
        )
        if after_build
        else (f"?? {_X200_UBOOT_OVERLAY_PATH}",)
    )
    if (
        revision != LIBREBOOT_2601.revision
        or tree_id != LIBREBOOT_2601.tree_id
        or origin != LIBREBOOT_2601.repository_url
        or tuple(sorted(status)) != tuple(sorted(expected_status))
    ):
        raise ValueError("Disposable lbmk source identity/status differs from the exact pinned overlay state")


def _validate_x200_vboot_overlay_bytes(patch_bytes: bytes, before: bytes, after: bytes) -> None:
    if hashlib.sha256(patch_bytes).hexdigest() != _X200_VBOOT_OVERLAY_SHA256:
        raise ValueError("X200 vboot compatibility patch bytes differ from the reviewed overlay")
    if (
        hashlib.sha256(before).hexdigest() != _X200_VBOOT_PREIMAGE_SHA256
        or hashlib.sha256(after).hexdigest() != _X200_VBOOT_POSTIMAGE_SHA256
    ):
        raise ValueError("X200 vboot parser source preimage/postimage hash differs from the reviewed overlay")
    old = b"char *end = strchr(start, '\\n');"
    new = b"const char *end = strchr(start, '\\n');"
    if before.count(old) != 1 or before.replace(old, new, 1) != after:
        raise ValueError("X200 vboot parser postimage contains changes beyond the exact const-correctness line")


def _x200_vboot_overlay_resource_bytes() -> bytes:
    """Read the wheel-packaged overlay resource, never an evidence-copy path."""
    resource = importlib.resources.files("autoloader.rom_builder").joinpath(
        "patches", _X200_VBOOT_OVERLAY_RESOURCE
    )
    try:
        return resource.read_bytes()
    except (FileNotFoundError, OSError) as exc:
        raise ValueError("Packaged X200 vboot compatibility overlay is unavailable") from exc


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
    prebuild_intent_sha256: str | None = None
    x200_uboot_dependency: X200UBootDependencyEvidence | None = None
    x200_grub_font: X200GrubFontEvidence | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_files", tuple(self.source_files))
        object.__setattr__(self, "commands", tuple(self.commands))
        object.__setattr__(self, "toolchain", tuple(self.toolchain))
        object.__setattr__(self, "build_environment", tuple(self.build_environment))
        object.__setattr__(self, "expected_regions", tuple(self.expected_regions))
        object.__setattr__(self, "blob_sha256", tuple(self.blob_sha256))
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        if self.schema_version not in {3, 4}:
            raise ValueError("Artifact manifest schema version 3 or 4 is required")
        if self.status not in {"complete", "failed"}:
            raise ValueError("Artifact manifest status must be complete or failed")
        if not self.recipe_id.startswith("x200-libreboot-") and any(
            nested.generated_files
            for artifact in self.artifacts
            if artifact.cbfs_inspection is not None
            for nested in artifact.cbfs_inspection.nested_repositories
        ):
            raise ValueError("X200 generated Coreboot inventory cannot be attached to an unrelated artifact manifest")
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
            if self.recipe_id.startswith("x200-libreboot-") and self.schema_version != 4:
                raise ValueError("A complete X200 artifact requires schema 4 nested lbmk source provenance")
            if self.recipe_id.startswith(("x200-libreboot-", "x220-coreboot-")):
                if selected_record.cbfs_inspection is None:
                    raise ValueError("A complete firmware artifact requires pinned CBFS structure inspection")
            if self.recipe_id.startswith("x200-libreboot-") and (
                self.prebuild_intent_sha256 is None
                or not _SHA256_RE.fullmatch(self.prebuild_intent_sha256)
            ):
                raise ValueError("A complete X200 artifact requires its pre-build intent digest")
            if self.recipe_id.startswith("x200-libreboot-") and self.x200_uboot_dependency is None:
                raise ValueError("A complete X200 artifact requires pinned patched U-Boot dependency provenance")
            if self.recipe_id.startswith("x200-libreboot-") and self.x200_grub_font is None:
                raise ValueError("A complete X200 artifact requires pinned GRUB Unifont and payload provenance")
            if self.recipe_id.startswith("x200-libreboot-"):
                if selected_record.cbfs_inspection is None:
                    raise ValueError("A complete X200 artifact requires schema 4 nested lbmk source provenance")
                _validate_x200_nested_repository_evidence(selected_record.cbfs_inspection.nested_repositories)
                coreboot_evidence = next(
                    item for item in selected_record.cbfs_inspection.nested_repositories
                    if item.role == "coreboot"
                )
                if (
                    selected_record.cbfs_inspection.source_repository_url != coreboot_evidence.repository_url
                    or selected_record.cbfs_inspection.source_revision != coreboot_evidence.resulting_revision
                    or selected_record.cbfs_inspection.source_tree_id != coreboot_evidence.resulting_tree_id
                ):
                    raise ValueError("X200 CBFS evidence does not bind its exact post-lbmk Coreboot checkout")
            if self.recipe_id.startswith("x220-coreboot-") and self.x200_uboot_dependency is not None:
                raise ValueError("X200 U-Boot overlay evidence cannot be attached to an X220 artifact")
            if self.recipe_id.startswith("x220-coreboot-") and self.x200_grub_font is not None:
                raise ValueError("X200 GRUB font evidence cannot be attached to an X220 artifact")
            if self.recipe_id.startswith("x220-coreboot-") and selected_record.backup_provenance is None:
                raise ValueError("A complete X220 full-chip artifact requires original-backup provenance")
            if self.recipe_id.startswith("x220-coreboot-") and selected_record.region_inspection_tool is None:
                raise ValueError("A complete X220 full-chip artifact requires pinned IFD region inspection")
            if self.recipe_id.startswith("x220-coreboot-") and selected_record.cbfs_inspection is not None and any(
                nested.generated_files for nested in selected_record.cbfs_inspection.nested_repositories
            ):
                raise ValueError("X200 generated Coreboot inventory cannot be attached to an X220 artifact")
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
            build_environment = dict(self.build_environment)
            if (
                build_environment.get("GIT_CONFIG_GLOBAL") != _BUILD_GIT_CONFIG_MANIFEST_PATH
                or build_environment.get("GIT_CONFIG_GLOBAL_SHA256") != _BUILD_GIT_CONFIG_SHA256
                or build_environment.get("GIT_CONFIG_NOSYSTEM") != "1"
                or build_environment.get("GIT_USER_NAME") != _BUILD_GIT_USER_NAME
                or build_environment.get("GIT_USER_EMAIL") != _BUILD_GIT_USER_EMAIL
            ):
                raise ValueError("A complete artifact manifest must record the isolated deterministic Git identity")
        elif self.selected_artifact_relative_path is not None or self.selected_artifact_sha256 is not None:
            raise ValueError("A failed manifest cannot select an artifact")
        if self.x200_uboot_dependency is not None and not self.recipe_id.startswith("x200-libreboot-"):
            raise ValueError("X200 U-Boot overlay evidence cannot be attached to a non-X200 manifest")
        if self.x200_grub_font is not None and not self.recipe_id.startswith("x200-libreboot-"):
            raise ValueError("X200 GRUB font evidence cannot be attached to a non-X200 manifest")
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
    repository_url="https://github.com/coreboot/coreboot.git",
    revision="8b0e34f90e08547cf19e550a43ed72f69c39d6ed",
    tree_id="1cb70fc2c4a0229fa17991b19cf6916b71f75ba7",
    files=(
        PinnedFile("configs/builder/config.lenovo_x220", "beeb15673bd4ef1394e19c7bbbac7f9ab3589552671d9502de4621b0b32c2cd1"),
        PinnedFile("src/mainboard/lenovo/x220/Kconfig.name", "1ca8ac6f14193e04be8d49e2ddfc7383aed8af0988db93d482140b6852664fa5"),
        PinnedFile("src/mainboard/lenovo/x220/Kconfig", "8c94a9d6976698b6e5cd7e3c3e0ee652a9916a878e85ab6bb9f7ba85a6c71f53"),
        PinnedFile("src/Kconfig", "0557dc3ea3ffbdeaddd9c55dd8fbfe0e6d8259b18e7ca250f403fd5fe992eb01"),
        PinnedFile("util/cbfstool/cbfs_image.c", "fd6a56a8e20c15f4fd8f03511f05ce8264f120ad0a0dc11900e2e04f89a88dda"),
        PinnedFile("util/cbfstool/cbfstool.c", "f34621b9d58083fc7c3e31571aaf5bd02f78476101e249a53995189d8a71488b"),
    ),
)

LIBREBOOT_2601 = SourcePin(
    name="libreboot-26.01",
    repository_url="https://codeberg.org/libreboot/lbmk.git",
    revision="1cf3181537b8d1fe1df0e91681f700850a3d9bf6",
    tree_id="0cb9451c6e2e265d3aa5b7e371752d45cab98f53",
    files=(
        PinnedFile("include/rom.sh", "9b04ea21df571682ccbc1a66e19d17ca9fe6ccde30a8089d1cb45e3683283e3b"),
        PinnedFile("include/get.sh", "b473d8af1ae2fea9663103aad12a191d297611d65d50f89e04bd8de6e561feb9"),
        PinnedFile("config/git/u-boot/pkg.cfg", _UBOOT_PACKAGE_CFG_SHA256),
        PinnedFile("config/git/grub/pkg.cfg", _X200_GRUB_PACKAGE_CFG_SHA256),
        PinnedFile("config/grub/default/target.cfg", _X200_GRUB_TARGET_CFG_SHA256),
        PinnedFile("include/tree.sh", _X200_LBMK_TREE_SH_SHA256),
        *(PinnedFile(f"config/grub/default/patches/{name}", digest) for name, digest in _X200_GRUB_PATCH_INPUTS),
        PinnedFile("config/u-boot/x86_64/target.cfg", _UBOOT_X86_64_TARGET_CFG_SHA256),
        PinnedFile("config/u-boot/amd64coreboot/target.cfg", _UBOOT_AMD64_TARGET_CFG_SHA256),
        PinnedFile("config/u-boot/amd64coreboot/config/default", _UBOOT_AMD64_DEFAULT_SHA256),
        *(PinnedFile(f"{_X200_UBOOT_PATCH_DIR}/{name}", digest) for name, digest in _X200_UBOOT_PATCH_INPUTS),
        PinnedFile("config/coreboot/x200_4mb/target.cfg", "74e0db657f688a39dcc581b4209f786da5397b5bb89920bfb8659a3650b7c34b"),
        PinnedFile("config/coreboot/x200_4mb/config/libgfxinit_corebootfb", "4038c9b655c1c74c582cb89a9f59688308d47faf8069679adce1a06da756410c"),
        PinnedFile("config/coreboot/x200_4mb/config/libgfxinit_txtmode", "76d49f1b10c3b45c65fd9149263e528aa60bbddf1d4ea4258835d9410a35cc65"),
        PinnedFile("config/coreboot/x200_8mb/target.cfg", "74e0db657f688a39dcc581b4209f786da5397b5bb89920bfb8659a3650b7c34b"),
        PinnedFile("config/coreboot/x200_8mb/config/libgfxinit_corebootfb", "6435d325d4f77c18ec6b411d708508d4eda2e5a4d2f6b83dfd3c0c0d230efac4"),
        PinnedFile("config/coreboot/x200_8mb/config/libgfxinit_txtmode", "d0558fb2221060c7062bd225b30ab65c71d994c3d1dcad98240440475062edaf"),
        PinnedFile("config/coreboot/default/target.cfg", "b0fa4882ec69979345e117076dda3c751143cd7d720e9cb4651796f77b2c395e"),
        PinnedFile("config/git/coreboot/pkg.cfg", "82e283bbd1c2b60ec9b2742e5e8c0a108dca8fe8ea483283a705b6102238c42f"),
        PinnedFile("include/lib.sh", "f429d5c14be6e988f7472d1f02e19f893be467f1685ad5a5822f41000e47754a"),
        PinnedFile(*_X200_LIBGFXINIT_MODULE_CONFIG),
        *(PinnedFile(*item) for item in _X200_LIBGFXINIT_PATCH_INPUTS),
        PinnedFile(*_X200_VBOOT_MODULE_CONFIG),
        PinnedFile(*_X200_VBOOT_EXISTING_PATCH),
        PinnedFile(*_X200_FSP_MODULE_CONFIG),
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
            PinnedFile("README.md", "5af0f463e56e8fcfbc6d3b57224859e755e701d4e04087167c9e335b14e8c33a"),
            PinnedFile("start.sh", "d00089253cb56a8967e242a9e73496c413a7c3d378044968acbc6278b31e873a"),
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

    return derive_lbmk_output_inventory(recipe.target)


def derive_lbmk_output_inventory(target: str) -> tuple[str, ...]:
    """Derive the exact pinned X200 26.01 ROM inventory for an allowlisted target."""
    if not re.fullmatch(r"x200_(?:4|8)mb", target):
        raise ValueError("Only the two pinned regular-X200 lbmk targets are supported")
    result: list[str] = []
    for config_stem in _PINNED_LBMK_CONFIG_STEMS:
        result.append(derive_lbmk_output_path(target, config_stem, "seabios"))
        for keymap in _PINNED_LBMK_KEYMAPS:
            result.append(_derive_lbmk_grub_path(target, config_stem, keymap))
        if config_stem == "libgfxinit_corebootfb":
            result.append(derive_lbmk_output_path(target, config_stem, "seauboot"))
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
    "4038c9b655c1c74c582cb89a9f59688308d47faf8069679adce1a06da756410c",
)
X200_8MB = _recipe(
    "x200-libreboot-8mb",
    "x200_8mb",
    8,
    "6435d325d4f77c18ec6b411d708508d4eda2e5a4d2f6b83dfd3c0c0d230efac4",
)
X220_COREBOOT = ArtifactRecipe(
    recipe_id="x220-coreboot-8mb-seabios",
    workflow_id="x220-coreboot",
    kind=RecipeKind.COREBOOT,
    source=COREBOOT_2609,
    target="lenovo/x220",
    expected_size_bytes=8 * 1024 * 1024,
    config_file="configs/builder/config.lenovo_x220",
    expected_config_sha256="beeb15673bd4ef1394e19c7bbbac7f9ab3589552671d9502de4621b0b32c2cd1",
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


def _email_patch_subject(patch_bytes: bytes) -> str:
    text = patch_bytes.decode("utf-8")
    match = re.search(r"(?m)^Subject: ([^\n]+(?:\n[ \t]+[^\n]+)*)\n\n", text)
    if match is None:
        raise ValueError("Pinned U-Boot patch lacks a valid email Subject header")
    subject = re.sub(r"\n[ \t]+", " ", match.group(1)).strip()
    subject = re.sub(r"^\[PATCH[^\]]*\][ \t]*", "", subject)
    if not subject:
        raise ValueError("Pinned U-Boot patch has an empty normalized subject")
    return subject


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
    required_payload_type: str = "payload",
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
    if not required_payload or not required_payload_type or region_size_bytes <= 0:
        raise ValueError("CBFS validation requires a payload name/type and positive region size")
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
    entry_intervals: list[tuple[int, int, int]] = []
    for index, line in enumerate(lines[1:], start=2 + marker_offset):
        fields = line.split("\t")
        if len(fields) != 6:
            raise ValueError(f"Malformed pinned cbfstool inventory row {index}")
        name, offset_text, entry_type, metadata_text, data_text, total_text = fields
        if not name or not entry_type.strip() or entry_type != entry_type.strip():
            raise ValueError(f"Duplicate or empty CBFS entry name/type on row {index}")
        if name == "(empty)":
            if entry_type != "null":
                raise ValueError(f"CBFS empty placeholder row {index} must have null type")
        else:
            if name in names:
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
        # Null `(empty)` rows are placeholders, but their reported extents are
        # still part of cbfstool's inventory and must not alias another row.
        entry_intervals.append((offset, offset + total_size, index))
    ordered_intervals = sorted(entry_intervals)
    for previous, current in zip(ordered_intervals, ordered_intervals[1:]):
        if current[0] < previous[1]:
            raise ValueError(
                f"CBFS rows {previous[2]} and {current[2]} overlap"
            )
    matching_payloads = [entry for entry in entries if entry.name == required_payload]
    if (
        len(matching_payloads) != 1
        or matching_payloads[0].entry_type != required_payload_type
        or matching_payloads[0].data_size_bytes <= 0
    ):
        raise ValueError(
            f"CBFS inventory lacks exactly one required payload entry {required_payload!r} "
            f"with type {required_payload_type!r}"
        )
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


def _require_operation_file(operation_root: Path, relative_path: str) -> Path:
    """Resolve a regular file under an operation directory without following links."""
    root = Path(operation_root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Artifact operation root must be a real directory")
    root = root.resolve(strict=True)
    pure = PurePosixPath(relative_path)
    if (
        "\\" in relative_path
        or pure.is_absolute()
        or not pure.parts
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ValueError("Artifact evidence path must be a safe operation-relative path")
    current = root
    for index, component in enumerate(pure.parts):
        current = current / component
        if current.is_symlink():
            raise ValueError("Artifact evidence path contains a symlink component")
        if index < len(pure.parts) - 1 and not current.is_dir():
            raise ValueError("Artifact evidence path traverses a non-directory component")
    resolved = current.resolve(strict=True)
    if not _is_inside(resolved, root) or not resolved.is_file():
        raise ValueError("Artifact evidence path is missing or escapes its operation directory")
    return resolved


def _require_absolute_regular_file_without_links(path: Path, label: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ValueError(f"{label} must use its exact absolute source path")
    current = Path(candidate.anchor)
    for component in candidate.parts[1:]:
        current = current / component
        if current.is_symlink():
            raise ValueError(f"{label} contains a symlink component")
    resolved = candidate.resolve(strict=True)
    if resolved != candidate or not candidate.is_file():
        raise ValueError(f"{label} must be a regular, non-symlink file")
    return candidate


def _parse_grub_configure_argv(config_log: str, staged_font_path: Path) -> tuple[str, ...]:
    matches = re.findall(
        r"(?m)^\s+\$ (\./configure(?: .*)?)\s*$",
        config_log,
    )
    if len(matches) != 1:
        raise ValueError("Pinned GRUB config.log must record exactly one configure invocation")
    try:
        argv = tuple(shlex.split(matches[0], posix=True))
    except ValueError as exc:
        raise ValueError("Pinned GRUB config.log configure argv is malformed") from exc
    expected = (
        "./configure",
        f"--with-unifont={staged_font_path.as_posix()}",
        *_X200_GRUB_CONFIGURE_FIXED_ARGS,
    )
    if argv != expected:
        raise ValueError("Pinned GRUB configure argv does not bind the exact staged Unifont path and options")
    return argv


def _require_external_path(path: Path, label: str, *, must_exist: bool) -> Path:
    resolved = path.resolve(strict=must_exist)
    repo_root = BASE_DIR.resolve()
    if _is_inside(resolved, repo_root):
        raise ValueError(f"{label} must be outside the AutoLoader application tree")
    return resolved


def _require_disjoint_paths(source: Path, output_root: Path) -> None:
    if _is_inside(output_root, source) or _is_inside(source, output_root):
        raise ValueError("Pinned source checkout and artifact output root must be separate directories")


def _ensure_operation_git_config(operation_dir: Path) -> tuple[Path, str]:
    """Create or verify the build's private, deterministic Git global config.

    Upstream lbmk checks for a global ``user.name``. Pointing GIT_CONFIG_GLOBAL
    at this operation-owned file avoids reading or modifying the operator's
    account config; the exact fixed bytes are rechecked around every command.
    """
    directory = Path(operation_dir)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Build operation directory must be a real directory")
    root = directory.resolve(strict=True)
    config_path = root / "gitconfig"
    if config_path.is_symlink():
        raise ValueError("Operation Git config must not be a symlink")
    if not config_path.exists():
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(config_path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as file_obj:
            file_obj.write(_BUILD_GIT_CONFIG_BYTES)
            file_obj.flush()
            os.fsync(file_obj.fileno())
    return _verify_operation_git_config(root)


def _verify_operation_git_config(operation_dir: Path) -> tuple[Path, str]:
    """Verify an existing operation Git config without recreating missing state."""
    directory = Path(operation_dir)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Build operation directory must be a real directory")
    root = directory.resolve(strict=True)
    config_path = root / "gitconfig"
    if config_path.is_symlink():
        raise ValueError("Operation Git config must not be a symlink")
    try:
        metadata = os.lstat(config_path)
    except OSError as exc:
        raise ValueError("Operation Git config cannot be inspected") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ValueError("Operation Git config must be a single-link regular file")
    if os.name != "nt" and stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError("Operation Git config permissions must be owner-only")
    try:
        payload = config_path.read_bytes()
    except OSError as exc:
        raise ValueError("Operation Git config cannot be read") from exc
    if payload != _BUILD_GIT_CONFIG_BYTES:
        raise ValueError("Operation Git config differs from the pinned deterministic identity")
    digest = hashlib.sha256(payload).hexdigest()
    if digest != _BUILD_GIT_CONFIG_SHA256:
        raise ValueError("Operation Git config digest differs from its fixed identity")
    return config_path, digest


def _build_environment_record(git_config_sha256: str | None) -> tuple[tuple[str, str], ...]:
    return (
        ("HOME", "<operation-output-directory>"),
        ("TMPDIR", "<operation-output-directory>/tmp"),
        ("GIT_CONFIG_GLOBAL", _BUILD_GIT_CONFIG_MANIFEST_PATH),
        ("GIT_CONFIG_GLOBAL_SHA256", git_config_sha256 or "unavailable"),
        ("GIT_CONFIG_NOSYSTEM", "1"),
        ("GIT_USER_NAME", _BUILD_GIT_USER_NAME),
        ("GIT_USER_EMAIL", _BUILD_GIT_USER_EMAIL),
        ("PATH_SHA256", hashlib.sha256(os.environ.get("PATH", "").encode()).hexdigest()),
        ("LC_ALL", "C"),
        ("LANG", "C"),
        ("TZ", "UTC"),
    )


def _validate_persisted_git_config(operation_dir: Path, build_environment: Sequence[tuple[str, str]]) -> None:
    """Reopen and verify the operation-scoped Git identity recorded in a manifest."""
    values = dict(build_environment)
    if (
        values.get("GIT_CONFIG_GLOBAL") != _BUILD_GIT_CONFIG_MANIFEST_PATH
        or values.get("GIT_CONFIG_GLOBAL_SHA256") != _BUILD_GIT_CONFIG_SHA256
        or values.get("GIT_CONFIG_NOSYSTEM") != "1"
        or values.get("GIT_USER_NAME") != _BUILD_GIT_USER_NAME
        or values.get("GIT_USER_EMAIL") != _BUILD_GIT_USER_EMAIL
    ):
        raise ValueError("Artifact manifest lacks the pinned isolated Git identity evidence")
    config_path, digest = _verify_operation_git_config(operation_dir)
    if digest != values["GIT_CONFIG_GLOBAL_SHA256"]:
        raise ValueError("Persisted operation Git config differs from its manifest digest")
    try:
        config_path.resolve(strict=True).relative_to(operation_dir.resolve(strict=True))
    except ValueError as exc:
        raise ValueError("Persisted operation Git config escapes its artifact directory") from exc


class ArtifactEngine:
    """Build only from immutable upstream pins and record verified outputs."""

    def load_completed_result(self, manifest_path: str | Path) -> ArtifactBuildResult:
        """Reopen a persisted COMPLETE result without trusting caller JSON objects.

        The workflow acceptance boundary performs the model-specific source,
        log, CBFS, layout, backup and staged-path checks after this parser has
        reconstructed immutable records. This method also checks the manifest
        sidecar and selected file bytes before returning a typed result.
        """
        path = Path(manifest_path).expanduser()
        if path.name != "manifest.json" or path.is_symlink() or not path.is_file():
            raise ValueError("Artifact manifest path must be a regular manifest.json file")
        raw = path.read_bytes()
        checksum = path.with_name("manifest.sha256")
        if checksum.is_symlink() or not checksum.is_file():
            raise ValueError("Artifact manifest checksum sidecar is missing or unsafe")
        sidecar = checksum.read_text(encoding="ascii").strip().split()
        if len(sidecar) != 2 or sidecar[1] != "manifest.json" or sidecar[0] != hashlib.sha256(raw).hexdigest():
            raise ValueError("Artifact manifest checksum sidecar does not match its bytes")
        try:
            document = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Artifact manifest JSON is malformed") from exc
        manifest = _artifact_manifest_from_mapping(document)
        if manifest.status != "complete":
            raise ValueError("Only a COMPLETE persisted artifact can be reopened")
        _validate_persisted_git_config(path.parent, manifest.build_environment)
        if manifest.recipe_id.startswith("x200-libreboot-"):
            dependency = manifest.x200_uboot_dependency
            if dependency is None:
                raise ValueError("Complete X200 artifact is missing typed U-Boot dependency evidence")
            objcopy = Path(dependency.objcopy_path)
            if (
                objcopy.is_symlink()
                or not objcopy.is_file()
                or not os.access(objcopy, os.X_OK)
                or sha256_file(objcopy) != dependency.objcopy_sha256
            ):
                raise ValueError("Recorded X200 GNU objcopy identity is missing or changed")
            if manifest.x200_grub_font is None:
                raise ValueError("Complete X200 artifact is missing typed GRUB Unifont evidence")
            self._validate_x200_grub_font_evidence(path.parent, manifest.x200_grub_font)
        if manifest.selected_artifact_relative_path is None:
            raise ValueError("Complete artifact manifest lacks a selected relative path")
        selected = path.parent.joinpath(*PurePosixPath(manifest.selected_artifact_relative_path).parts)
        if selected.is_symlink() or not selected.is_file():
            raise ValueError("Manifest-selected artifact is missing or unsafe")
        try:
            selected.resolve(strict=True).relative_to(path.parent.resolve(strict=True))
        except ValueError as exc:
            raise ValueError("Manifest-selected artifact escapes its operation directory") from exc
        if (
            selected.stat().st_size != next(
                item.size_bytes for item in manifest.artifacts
                if item.relative_path == manifest.selected_artifact_relative_path
                and item.sha256 == manifest.selected_artifact_sha256
            )
            or sha256_file(selected) != manifest.selected_artifact_sha256
        ):
            raise ValueError("Manifest-selected artifact size or SHA-256 changed")
        if manifest.recipe_id.startswith("x200-libreboot-"):
            selected_record = next(
                item for item in manifest.artifacts
                if item.relative_path == manifest.selected_artifact_relative_path
                and item.sha256 == manifest.selected_artifact_sha256
            )
            if selected_record.cbfs_inspection is None:
                raise ValueError("Complete X200 artifact is missing its persisted CBFS source evidence")
            coreboot_evidence = next(
                item for item in selected_record.cbfs_inspection.nested_repositories
                if item.role == "coreboot"
            )
            _verify_x200_coreboot_generated_files(path.parent, coreboot_evidence)
        return ArtifactBuildResult(
            BuildStatus.COMPLETE,
            "PERSISTED_ARTIFACT_REOPENED",
            "Persisted manifest and selected artifact bytes were reopened; workflow-specific acceptance remains required.",
            manifest.recipe_id,
            (selected,),
            manifest,
            path,
        )

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
        if recipe.workflow_id == "x200-libreboot":
            intent = request.build_intent
            if intent is None:
                raise ValueError("X200 builds require a pre-build BuildIntentEvidence")
            if (
                intent.profile_id != "x200-libreboot"
                or intent.requested_model_id != "x200"
                or intent.firmware_family is not FirmwareFamily.LIBREBOOT
                or intent.recipe_id != recipe.recipe_id
                or intent.source_url != recipe.source.repository_url
                or intent.source_revision != recipe.source.revision
                or intent.source_tree_id != recipe.source.tree_id
                or intent.configuration_sha256 != recipe.expected_config_sha256
                or intent.expected_size_bytes != recipe.expected_size_bytes
            ):
                raise ValueError("X200 pre-build intent differs from the exact pinned Libreboot recipe")
            intent_result = CompatibilityValidator().validate_build_intent(intent)
            if intent_result.decision is not CompatibilityDecision.ALLOWED:
                raise ValueError("X200 pre-build intent is not compatible: " + "; ".join(intent_result.reasons))
            if request.observed_chip_capacity_bytes != intent.expected_size_bytes:
                raise ValueError("X200 observed chip capacity differs from its validated pre-build intent")
        elif request.build_intent is not None:
            raise ValueError("BuildIntentEvidence is only accepted by the X200 Libreboot recipes")
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
        build_source = source
        config_hash = recipe.expected_config_sha256
        resolved_config_hash: str | None = None
        blob_hashes: list[tuple[str, str]] = []
        artifacts: list[ArtifactRecord] = []
        region_inspection_tool: ToolEvidence | None = None
        finalized_operation_plan: OperationPlan | None = None
        x200_uboot_dependency: X200UBootDependencyEvidence | None = None
        x200_grub_font: X200GrubFontEvidence | None = None
        x200_nested_repository_evidence: tuple[X200NestedRepositoryEvidence, ...] | None = None
        x200_unifont_path: Path | None = None
        failure_code: str | None = None
        failure_detail: str | None = None
        git_config_sha256: str | None = None

        try:
            _, git_config_sha256 = _ensure_operation_git_config(run_dir)
            if recipe.workflow_id == "x200-libreboot":
                build_source, x200_uboot_dependency = self._prepare_x200_lbmk_build_source(
                    recipe,
                    source,
                    run_dir,
                    logs_dir,
                    commands,
                )
                x200_unifont_path = self._prepare_x200_grub_font(
                    build_source,
                    run_dir,
                    logs_dir,
                    commands,
                )
                self._stage_x200_vboot_compat_overlay(build_source)
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
                self._run_logged(
                    recipe.build_command,
                    build_source,
                    logs_dir,
                    commands,
                    x200_unifont_path=x200_unifont_path,
                )
                resolved_config_path = build_source / "src" / "coreboot" / "default" / ".config"
                if not resolved_config_path.is_file():
                    raise ValueError("Pinned lbmk build did not leave a resolved Coreboot .config")
                resolved_config = resolved_config_path.read_text(encoding="utf-8")
                resolved_config_hash = validate_recipe_config(recipe, resolved_config)
                if recipe.workflow_id == "x200-libreboot":
                    if x200_unifont_path is None:
                        raise ValueError("X200 GRUB Unifont input was not staged before the upstream build")
                    x200_grub_font = self._collect_x200_grub_font_evidence(
                        build_source,
                        run_dir,
                        x200_unifont_path,
                        logs_dir,
                        commands,
                    )

            raw_artifacts = (
                validated_lbmk_artifacts(build_source, recipe)
                if recipe.kind is RecipeKind.LIBREBOOT
                else (build_source / PurePosixPath(recipe.output_relative_path),)
            )
            selected_raw = build_source / PurePosixPath(recipe.output_relative_path)
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
                build_source,
                selected_raw,
                logs_dir,
                commands,
            )
            if recipe.workflow_id == "x200-libreboot":
                x200_nested_repository_evidence = cbfs_inspection.nested_repositories

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
                    source_relative_path=raw_artifact.relative_to(build_source).as_posix(),
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
            elif recipe.workflow_id == "x200-libreboot":
                if x200_uboot_dependency is None:
                    raise ValueError("X200 U-Boot dependency evidence disappeared before final validation")
                x200_uboot_dependency = self._validate_x200_uboot_dependency_after_build(
                    recipe,
                    build_source,
                    run_dir,
                    logs_dir,
                    commands,
                    x200_uboot_dependency,
                )
                if x200_grub_font is None:
                    raise ValueError("X200 GRUB Unifont provenance disappeared before final validation")
                self._validate_x200_grub_font_evidence(run_dir, x200_grub_font)
                if x200_nested_repository_evidence is None:
                    raise ValueError("X200 nested source provenance disappeared before final validation")
                final_nested_evidence = self._validate_lbmk_nested_coreboot(build_source, logs_dir, commands)
                if final_nested_evidence != x200_nested_repository_evidence:
                    raise ValueError("X200 nested source provenance changed before artifact publication")
                final_source_audit = self.inspect_source(recipe.recipe_id, source)
                if final_source_audit != source_audit:
                    raise ValueError("Original pinned Libreboot checkout changed during isolated X200 build")
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            failure_code = "ARTIFACT_VALIDATION_FAILED"
            failure_detail = str(exc)

        # Post-command validation can take a long time and touches upstream
        # output paths. Recheck the operation-scoped identity at the final
        # publication boundary so a later postprocessing mutation cannot
        # leave a COMPLETE manifest behind merely because each child command
        # initially exited with the expected config present.
        if failure_code is None:
            try:
                _, final_git_config_sha256 = _verify_operation_git_config(run_dir)
                if final_git_config_sha256 != git_config_sha256:
                    raise ValueError("Operation Git config digest changed before artifact publication")
                if recipe.workflow_id == "x200-libreboot":
                    if x200_grub_font is None:
                        raise ValueError("X200 artifact publication lacks verified GRUB Unifont evidence")
                    self._validate_x200_grub_font_evidence(run_dir, x200_grub_font)
                    if x200_nested_repository_evidence is None:
                        raise ValueError("X200 artifact publication lacks nested source provenance")
                    final_nested_evidence = self._validate_lbmk_nested_coreboot(build_source, logs_dir, commands)
                    if final_nested_evidence != x200_nested_repository_evidence:
                        raise ValueError("X200 nested source provenance changed at artifact publication")
            except (OSError, ValueError) as exc:
                failure_code = "ARTIFACT_VALIDATION_FAILED"
                failure_detail = f"Final artifact evidence validation failed before publication: {exc}"

        if recipe.workflow_id == "x200-libreboot" and x200_grub_font is None and failure_code is None:
            failure_code = "ARTIFACT_VALIDATION_FAILED"
            failure_detail = "X200 COMPLETE status requires the verified pinned GRUB Unifont build evidence"
        status = "complete" if artifacts and failure_code is None else "failed"
        manifest = ArtifactManifest(
            schema_version=4 if recipe.workflow_id == "x200-libreboot" else 3,
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
            build_environment=_build_environment_record(git_config_sha256),
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
            prebuild_intent_sha256=(
                build_intent_sha256(request.build_intent)
                if recipe.workflow_id == "x200-libreboot" and request.build_intent is not None
                else None
            ),
            x200_uboot_dependency=x200_uboot_dependency,
            x200_grub_font=x200_grub_font,
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

    @staticmethod
    def _validate_lbmk_cbfstool_path_selection(lib_script_text: str) -> None:
        """Require the literal cbfstool path from the pinned Libreboot 26.01 source."""
        paths = re.findall(r'^cbfstool="([^"]+)"$', lib_script_text, re.MULTILINE)
        if paths != ["elf/coreboot/default/cbfstool"]:
            raise ValueError("Pinned lbmk cbfstool path selection changed")

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
        if sha256_file(default_coreboot_target) != "b0fa4882ec69979345e117076dda3c751143cd7d720e9cb4651796f77b2c395e":
            raise ValueError("Pinned lbmk default Coreboot target config hash mismatch")
        pkg_text = coreboot_pkg_config.read_text(encoding="utf-8")
        pkg_values = dict(re.findall(r'^([A-Za-z0-9_]+)="([^"]*)"$', pkg_text, re.MULTILINE))
        if pkg_values.get("url") != "https://github.com/coreboot/coreboot.git" or pkg_values.get("bkup_url") != "https://review.coreboot.org/coreboot":
            raise ValueError("Pinned lbmk Coreboot source repository configuration changed")
        if sha256_file(coreboot_pkg_config) != "82e283bbd1c2b60ec9b2742e5e8c0a108dca8fe8ea483283a705b6102238c42f":
            raise ValueError("Pinned lbmk Coreboot source repository config hash mismatch")
        self._validate_lbmk_cbfstool_path_selection(lib_script.read_text(encoding="utf-8"))
        if sha256_file(lib_script) != "f429d5c14be6e988f7472d1f02e19f893be467f1685ad5a5822f41000e47754a":
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

    def _prepare_x200_lbmk_build_source(
        self,
        recipe: ArtifactRecipe,
        source: Path,
        run_dir: Path,
        logs_dir: Path,
        commands: list[CommandRecord],
    ) -> tuple[Path, X200UBootDependencyEvidence]:
        """Clone the pinned lbmk input, add one fixed patch, and fetch its U-Boot target."""
        if recipe.workflow_id != "x200-libreboot":
            raise ValueError("The U-Boot compatibility overlay is limited to X200 Libreboot recipes")
        build_source = run_dir.joinpath(*PurePosixPath(_X200_UBOOT_LBMK_BUILD_ROOT).parts)
        build_source.parent.mkdir(parents=True, exist_ok=False)
        if build_source.exists() or build_source.is_symlink():
            raise ValueError("Disposable X200 lbmk source destination already exists")
        self._run_logged(
            ("git", "clone", "--no-hardlinks", "--no-local", "--quiet", str(source), str(build_source)),
            source.parent,
            logs_dir,
            commands,
        )
        self._run_logged(
            ("git", "-C", str(build_source), "checkout", "--detach", recipe.source.revision),
            source.parent,
            logs_dir,
            commands,
        )
        self._run_logged(
            ("git", "-C", str(build_source), "remote", "set-url", "origin", recipe.source.repository_url),
            source.parent,
            logs_dir,
            commands,
        )
        cloned_audit = self.inspect_source(recipe.recipe_id, build_source)
        if cloned_audit != SourceAudit(
            recipe.source.repository_url,
            recipe.source.revision,
            recipe.source.tree_id,
            True,
            tuple((item.path, item.sha256) for item in recipe.source.files),
        ):
            raise ValueError("Disposable lbmk clone does not match the exact reviewed source pin")
        self._validate_lbmk_target(recipe, build_source)
        self._install_x200_uboot_overlay(build_source)
        before_objcopy = self._capture_gnu_objcopy(run_dir, logs_dir, commands)
        self._run_logged(("./mk", "-f", "u-boot", "x86_64"), build_source, logs_dir, commands)
        return build_source, self._collect_x200_uboot_dependency(build_source, before_objcopy, logs_dir, commands)

    def _prepare_x200_grub_font(
        self,
        build_source: Path,
        run_dir: Path,
        logs_dir: Path,
        commands: list[CommandRecord],
    ) -> Path:
        """Stage the pinned host OTF and apply a configure-only lbmk overlay."""
        source_font = _require_absolute_regular_file_without_links(
            Path(_X200_GRUB_FONT_SOURCE_PATH), "Ubuntu Unifont source"
        )
        if sha256_file(source_font) != _X200_GRUB_FONT_SOURCE_SHA256:
            raise ValueError("Ubuntu Unifont source bytes differ from the pinned OTF")
        self._verify_x200_unifont_package(source_font, logs_dir, commands)

        stage_dir = run_dir / "inputs"
        stage_dir.mkdir(exist_ok=False)
        staged_font = stage_dir / "unifont.otf"
        with source_font.open("rb") as input_file, staged_font.open("xb") as output_file:
            shutil.copyfileobj(input_file, output_file)
        if staged_font.is_symlink() or not staged_font.is_file() or sha256_file(staged_font) != _X200_GRUB_FONT_SOURCE_SHA256:
            raise ValueError("Operation-owned X200 Unifont copy failed exact hash verification")

        resource = Path(__file__).parent / "patches" / _X200_GRUB_FONT_OVERLAY_RESOURCE
        if resource.is_symlink() or not resource.is_file() or sha256_file(resource) != _X200_GRUB_FONT_OVERLAY_SHA256:
            raise ValueError("Pinned X200 GRUB Unifont overlay resource is missing or changed")
        tree_script = build_source / "include" / "tree.sh"
        if tree_script.is_symlink() or not tree_script.is_file() or sha256_file(tree_script) != _X200_LBMK_TREE_SH_SHA256:
            raise ValueError("Pinned lbmk include/tree.sh differs from the source audited for the Unifont overlay")
        self._verify_x200_grub_patch_inputs(build_source)
        self._run_logged(
            ("git", "-C", str(build_source), "apply", "--check", str(resource)),
            build_source,
            logs_dir,
            commands,
        )
        self._run_logged(
            ("git", "-C", str(build_source), "apply", str(resource)),
            build_source,
            logs_dir,
            commands,
        )
        if sha256_file(tree_script) != _X200_LBMK_TREE_SH_PATCHED_SHA256:
            raise ValueError("Pinned lbmk Unifont overlay did not produce the exact reviewed include/tree.sh bytes")
        tracked_status = self._run_readonly(
            ("git", "-C", str(build_source), "status", "--porcelain=v1", "--untracked-files=no"),
            build_source,
            logs_dir,
            commands,
        ).rstrip("\r\n")
        if tracked_status != " M include/tree.sh":
            raise ValueError("X200 lbmk overlay changed tracked inputs beyond the reviewed include/tree.sh file")
        return staged_font

    def _stage_x200_vboot_compat_overlay(self, build_source: Path) -> None:
        """Stage the one reviewed GCC 15 const-correctness patch for lbmk's vboot module."""
        resource_bytes = _x200_vboot_overlay_resource_bytes()
        if hashlib.sha256(resource_bytes).hexdigest() != _X200_VBOOT_OVERLAY_SHA256:
            raise ValueError("Pinned X200 vboot compatibility overlay resource is missing or changed")
        patch_directory = build_source.joinpath(
            *PurePosixPath("config/submodule/coreboot/default/vboot/patches").parts
        )
        _assert_no_symlink_components(patch_directory, root=build_source)
        if not patch_directory.is_dir():
            raise ValueError("Pinned lbmk vboot submodule patch directory is missing")
        expected_existing = Path(_X200_VBOOT_EXISTING_PATCH[0]).name
        entries = tuple(patch_directory.iterdir())
        if (
            any(path.is_symlink() or not path.is_file() for path in entries)
            or tuple(sorted(path.name for path in entries)) != (expected_existing,)
        ):
            raise ValueError("Pinned lbmk vboot patch directory differs from the exact pre-overlay inventory")
        existing = patch_directory / expected_existing
        if sha256_file(existing) != _X200_VBOOT_EXISTING_PATCH[1]:
            raise ValueError("Pinned existing vboot patch changed before compatibility overlay staging")
        destination = build_source.joinpath(*PurePosixPath(_X200_VBOOT_OVERLAY_RELATIVE_PATH).parts)
        _assert_no_symlink_components(destination.parent, root=build_source)
        try:
            with destination.open("xb") as output_file:
                output_file.write(resource_bytes)
        except FileExistsError as exc:
            raise ValueError("X200 vboot compatibility overlay destination already exists") from exc
        if destination.is_symlink() or not destination.is_file() or sha256_file(destination) != _X200_VBOOT_OVERLAY_SHA256:
            raise ValueError("Operation-owned vboot compatibility overlay copy failed its exact hash check")

    @staticmethod
    def _verify_x200_grub_patch_inputs(build_source: Path) -> None:
        patch_directory = build_source / "config" / "grub" / "default" / "patches"
        _assert_no_symlink_components(patch_directory, root=build_source)
        if not patch_directory.is_dir():
            raise ValueError("Pinned Libreboot GRUB patch directory is missing")
        expected_names = tuple(name for name, _ in _X200_GRUB_PATCH_INPUTS)
        entries = tuple(patch_directory.iterdir())
        if any(path.is_symlink() or not path.is_file() for path in entries):
            raise ValueError("Pinned Libreboot GRUB patch directory contains a linked or non-file entry")
        actual_names = tuple(sorted(path.name for path in entries))
        if actual_names != tuple(sorted(expected_names)):
            raise ValueError("Pinned Libreboot GRUB patch input inventory differs from the source pin")
        for name, expected_digest in _X200_GRUB_PATCH_INPUTS:
            path = patch_directory / name
            _assert_no_symlink_components(path, root=build_source)
            if not path.is_file() or sha256_file(path) != expected_digest:
                raise ValueError(f"Pinned Libreboot GRUB patch input changed: {name}")

    def _read_x200_grub_patch_history(
        self,
        grub_source: Path,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        rows = self._git_value(
            grub_source,
            "rev-list",
            "--reverse",
            "--parents",
            f"{_X200_GRUB_BASE_REVISION}..HEAD",
        ).splitlines()
        if len(rows) != len(_X200_GRUB_PATCH_INPUTS):
            raise ValueError("Pinned GRUB patch history must contain exactly the reviewed ten commits")
        commits: list[str] = []
        trees: list[str] = []
        expected_parent = _X200_GRUB_BASE_REVISION
        for row in rows:
            fields = row.split()
            if len(fields) != 2 or fields[1] != expected_parent:
                raise ValueError("Pinned GRUB patch history is not an ordered, linear application over the base")
            commit = fields[0]
            if not _GIT_OBJECT_RE.fullmatch(commit):
                raise ValueError("Pinned GRUB patch history contains a malformed generated commit ID")
            tree = self._git_value(grub_source, "rev-parse", f"{commit}^{{tree}}")
            commits.append(commit)
            trees.append(tree)
            expected_parent = commit
        if tuple(trees) != _X200_GRUB_PATCH_TREE_IDS:
            raise ValueError("Pinned GRUB patch history produced a different per-patch source tree")
        return tuple(commits), tuple(trees)

    def _verify_x200_unifont_package(
        self,
        source_font: Path,
        logs_dir: Path,
        commands: list[CommandRecord],
    ) -> None:
        owner = self._run_readonly(
            ("dpkg-query", "-S", str(source_font)),
            logs_dir.parent,
            logs_dir,
            commands,
        )
        version = self._run_readonly(
            ("dpkg-query", "-W", "-f=${Package}\\t${Version}\\n", _X200_GRUB_FONT_PACKAGE),
            logs_dir.parent,
            logs_dir,
            commands,
        )
        if owner.splitlines() != [f"{_X200_GRUB_FONT_PACKAGE}: {source_font}"]:
            raise ValueError("Pinned Unifont OTF is not owned by the expected Debian fonts-unifont package")
        if version.splitlines() != [f"{_X200_GRUB_FONT_PACKAGE}\t{_X200_GRUB_FONT_PACKAGE_VERSION}"]:
            raise ValueError("Debian fonts-unifont package version differs from the reviewed Ubuntu build input")

    def _collect_x200_grub_font_evidence(
        self,
        build_source: Path,
        run_dir: Path,
        staged_font: Path,
        logs_dir: Path,
        commands: list[CommandRecord],
    ) -> X200GrubFontEvidence:
        source_font = _require_absolute_regular_file_without_links(
            Path(_X200_GRUB_FONT_SOURCE_PATH), "Ubuntu Unifont source"
        )
        if sha256_file(source_font) != _X200_GRUB_FONT_SOURCE_SHA256:
            raise ValueError("Ubuntu Unifont source changed during the pinned X200 build")
        self._verify_x200_unifont_package(source_font, logs_dir, commands)
        if (
            staged_font != run_dir / PurePosixPath(_X200_GRUB_FONT_STAGED_RELATIVE_PATH)
            or staged_font.is_symlink()
            or not staged_font.is_file()
            or sha256_file(staged_font) != _X200_GRUB_FONT_SOURCE_SHA256
        ):
            raise ValueError("Operation-owned X200 Unifont copy changed during the build")

        resource = Path(__file__).parent / "patches" / _X200_GRUB_FONT_OVERLAY_RESOURCE
        if resource.is_symlink() or not resource.is_file() or sha256_file(resource) != _X200_GRUB_FONT_OVERLAY_SHA256:
            raise ValueError("Pinned X200 GRUB Unifont overlay resource changed during the build")
        tree_script = build_source / "include" / "tree.sh"
        if tree_script.is_symlink() or not tree_script.is_file() or sha256_file(tree_script) != _X200_LBMK_TREE_SH_PATCHED_SHA256:
            raise ValueError("Patched lbmk include/tree.sh changed during the X200 build")
        self._verify_x200_grub_patch_inputs(build_source)

        grub_source = build_source / "src" / "grub" / "default"
        grub_cache = build_source.joinpath(*PurePosixPath(_X200_GRUB_CACHE_RELATIVE_PATH).parts)
        for directory in (grub_source, grub_cache):
            _assert_no_symlink_components(directory, root=run_dir)
            if not directory.is_dir():
                raise ValueError("Pinned GRUB source or lbmk cache directory is missing")
        expected_cache = run_dir / PurePosixPath(_X200_GRUB_CACHE_RELATIVE_PATH)
        if (
            self._git_value(grub_source, "rev-parse", "--show-toplevel") != str(grub_source.resolve(strict=True))
            or self._git_value(grub_source, "remote", "get-url", "origin") != str(expected_cache.resolve(strict=True))
            or self._git_value(grub_cache, "remote", "get-url", "origin") != _X200_GRUB_SOURCE_URL
            or self._git_value(grub_cache, "rev-parse", "--show-toplevel") != str(expected_cache.resolve(strict=True))
        ):
            raise ValueError("Pinned GRUB source origin is not the expected in-root lbmk HTTPS/Git cache chain")
        base_tree = self._git_value(grub_source, "rev-parse", f"{_X200_GRUB_BASE_REVISION}^{{tree}}")
        cache_base_tree = self._git_value(grub_cache, "rev-parse", f"{_X200_GRUB_BASE_REVISION}^{{tree}}")
        revision = self._git_value(grub_source, "rev-parse", "HEAD")
        tree_id = self._git_value(grub_source, "rev-parse", "HEAD^{tree}")
        patch_commits, patch_tree_ids = self._read_x200_grub_patch_history(grub_source)
        tracked_status = self._git_value(grub_source, "status", "--porcelain=v1", "--untracked-files=no")
        if (
            base_tree != _X200_GRUB_BASE_TREE
            or cache_base_tree != _X200_GRUB_BASE_TREE
            or revision != patch_commits[-1]
            or tree_id != _X200_GRUB_PATCHED_TREE
            or tracked_status
        ):
            raise ValueError("Built GRUB source does not match the pinned base, patch order and deterministic trees")
        configure_ac = grub_source / "configure.ac"
        if configure_ac.is_symlink() or not configure_ac.is_file() or sha256_file(configure_ac) != _X200_GRUB_CONFIGURE_AC_SHA256:
            raise ValueError("Built GRUB configure.ac differs from the pinned patched source tree")

        configure_log = _require_operation_file(run_dir, _X200_GRUB_CONFIG_LOG_RELATIVE_PATH)
        configure_text = configure_log.read_text(encoding="utf-8", errors="strict")
        actual_argv = _parse_grub_configure_argv(configure_text, staged_font)
        recorded_argv = (
            "./configure",
            "--with-unifont=<operation-output-directory>/inputs/unifont.otf",
            *_X200_GRUB_CONFIGURE_FIXED_ARGS,
        )
        if actual_argv != (
            "./configure",
            f"--with-unifont={staged_font.as_posix()}",
            *_X200_GRUB_CONFIGURE_FIXED_ARGS,
        ):
            raise ValueError("Pinned GRUB configure invocation does not use the exact staged Unifont input")
        payload = _require_operation_file(run_dir, _X200_GRUB_PAYLOAD_RELATIVE_PATH)
        if payload.stat().st_size <= 0:
            raise ValueError("Pinned GRUB output payload is empty")
        return X200GrubFontEvidence(
            source_font_path=str(source_font),
            source_font_sha256=sha256_file(source_font),
            source_package=_X200_GRUB_FONT_PACKAGE,
            source_package_version=_X200_GRUB_FONT_PACKAGE_VERSION,
            staged_font_relative_path=_X200_GRUB_FONT_STAGED_RELATIVE_PATH,
            staged_font_sha256=sha256_file(staged_font),
            overlay_resource_path=_X200_GRUB_FONT_OVERLAY_PATH,
            overlay_resource_sha256=sha256_file(resource),
            lbmk_tree_sh_sha256=_X200_LBMK_TREE_SH_SHA256,
            lbmk_tree_sh_patched_sha256=sha256_file(tree_script),
            grub_source_relative_path="work/lbmk/src/grub/default",
            grub_repository_url=_X200_GRUB_SOURCE_URL,
            grub_cache_relative_path=_X200_GRUB_CACHE_RELATIVE_PATH,
            grub_base_revision=_X200_GRUB_BASE_REVISION,
            grub_base_tree_id=base_tree,
            grub_patch_inputs=_X200_GRUB_PATCH_INPUTS,
            grub_patch_commits=patch_commits,
            grub_patch_tree_ids=patch_tree_ids,
            grub_result_revision=revision,
            grub_result_tree_id=tree_id,
            configure_log_relative_path=_X200_GRUB_CONFIG_LOG_RELATIVE_PATH,
            configure_log_sha256=sha256_file(configure_log),
            configure_argv=recorded_argv,
            generated_payload_relative_path=_X200_GRUB_PAYLOAD_RELATIVE_PATH,
            generated_payload_sha256=sha256_file(payload),
        )

    def _validate_x200_grub_font_evidence(
        self,
        operation_root: Path,
        evidence: X200GrubFontEvidence,
    ) -> None:
        staged = self._validate_x200_grub_font_staged_input(operation_root, evidence)
        resource = Path(__file__).parent / "patches" / _X200_GRUB_FONT_OVERLAY_RESOURCE
        if resource.is_symlink() or not resource.is_file() or sha256_file(resource) != evidence.overlay_resource_sha256:
            raise ValueError("Persisted X200 GRUB overlay resource changed")
        tree_script = _require_operation_file(operation_root, "work/lbmk/include/tree.sh")
        if sha256_file(tree_script) != evidence.lbmk_tree_sh_patched_sha256:
            raise ValueError("Persisted disposable lbmk configure overlay changed")
        build_source = _require_operation_file(operation_root, "work/lbmk/include/tree.sh").parents[1]
        self._verify_x200_grub_patch_inputs(build_source)
        configure_log = _require_operation_file(operation_root, evidence.configure_log_relative_path)
        if sha256_file(configure_log) != evidence.configure_log_sha256:
            raise ValueError("Persisted GRUB configure log changed")
        expected_actual_argv = (
            "./configure",
            f"--with-unifont={staged.as_posix()}",
            *_X200_GRUB_CONFIGURE_FIXED_ARGS,
        )
        if _parse_grub_configure_argv(configure_log.read_text(encoding="utf-8"), staged) != expected_actual_argv:
            raise ValueError("Persisted GRUB configure log does not prove the exact Unifont argument")
        payload = _require_operation_file(operation_root, evidence.generated_payload_relative_path)
        if sha256_file(payload) != evidence.generated_payload_sha256:
            raise ValueError("Persisted generated GRUB payload changed")
        grub_source = operation_root.joinpath(*PurePosixPath(evidence.grub_source_relative_path).parts)
        _assert_no_symlink_components(grub_source, root=operation_root)
        grub_cache = operation_root.joinpath(*PurePosixPath(evidence.grub_cache_relative_path).parts)
        _assert_no_symlink_components(grub_cache, root=operation_root)
        expected_cache = operation_root / PurePosixPath(_X200_GRUB_CACHE_RELATIVE_PATH)
        if (
            self._git_value(grub_source, "rev-parse", "--show-toplevel") != str(grub_source.resolve(strict=True))
            or self._git_value(grub_cache, "rev-parse", "--show-toplevel") != str(expected_cache.resolve(strict=True))
            or self._git_value(grub_source, "remote", "get-url", "origin") != str(expected_cache.resolve(strict=True))
            or self._git_value(grub_cache, "remote", "get-url", "origin") != _X200_GRUB_SOURCE_URL
        ):
            raise ValueError("Persisted GRUB source and cache origin no longer match the pinned in-operation chain")
        actual_commits, actual_trees = self._read_x200_grub_patch_history(grub_source)
        if (
            self._git_value(grub_source, "rev-parse", "HEAD") != evidence.grub_result_revision
            or self._git_value(grub_source, "rev-parse", "HEAD^{tree}") != evidence.grub_result_tree_id
            or self._git_value(grub_source, "rev-parse", f"{evidence.grub_base_revision}^{{tree}}") != evidence.grub_base_tree_id
            or actual_commits != evidence.grub_patch_commits
            or actual_trees != evidence.grub_patch_tree_ids
            or self._git_value(grub_source, "status", "--porcelain=v1", "--untracked-files=no")
        ):
            raise ValueError("Persisted X200 GRUB checkout no longer matches recorded commit ancestry and pinned trees")

    @staticmethod
    def _validate_x200_grub_font_staged_input(
        operation_root: Path,
        evidence: X200GrubFontEvidence,
    ) -> Path:
        """Validate portable, operation-owned font evidence on manifest reopen.

        The Ubuntu source font and its dpkg owner/version are verified while
        preparing the build. Reopening a completed artifact on another host
        must not require that original absolute Ubuntu path to exist. The
        immutable manifest still pins that source identity and digest, while
        this operation-owned copy must remain byte-identical to it.
        """
        if (
            evidence.source_font_path != _X200_GRUB_FONT_SOURCE_PATH
            or evidence.source_font_sha256 != _X200_GRUB_FONT_SOURCE_SHA256
            or evidence.source_package != _X200_GRUB_FONT_PACKAGE
            or evidence.source_package_version != _X200_GRUB_FONT_PACKAGE_VERSION
            or evidence.staged_font_relative_path != _X200_GRUB_FONT_STAGED_RELATIVE_PATH
            or evidence.staged_font_sha256 != evidence.source_font_sha256
            or evidence.staged_font_sha256 != _X200_GRUB_FONT_SOURCE_SHA256
        ):
            raise ValueError("Persisted X200 Unifont source/package identity differs from the pinned input")
        staged = _require_operation_file(operation_root, evidence.staged_font_relative_path)
        if sha256_file(staged) != evidence.staged_font_sha256:
            raise ValueError("Persisted operation-owned Unifont copy changed")
        return staged

    @staticmethod
    def _install_x200_uboot_overlay(build_source: Path) -> None:
        source_patch = Path(__file__).parent / "patches" / _X200_UBOOT_OVERLAY_INPUT[0]
        if source_patch.is_symlink() or not source_patch.is_file():
            raise ValueError("Pinned X200 U-Boot overlay resource is missing or unsafe")
        patch_bytes = source_patch.read_bytes()
        if hashlib.sha256(patch_bytes).hexdigest() != _X200_UBOOT_OVERLAY_SHA256:
            raise ValueError("Pinned X200 U-Boot overlay resource hash mismatch")
        patch_directory = build_source.joinpath(*PurePosixPath(_X200_UBOOT_PATCH_DIR).parts)
        if patch_directory.is_symlink() or not patch_directory.is_dir():
            raise ValueError("Pinned X200 U-Boot patch directory is missing or unsafe")
        actual_files = sorted(path.name for path in patch_directory.glob("*.patch") if path.is_file() and not path.is_symlink())
        expected_files = sorted(name for name, _ in _X200_UBOOT_PATCH_INPUTS)
        if actual_files != expected_files:
            raise ValueError("Pinned X200 U-Boot patch stack differs from the reviewed seven source patches")
        for name, expected_hash in _X200_UBOOT_PATCH_INPUTS:
            candidate = patch_directory / name
            if candidate.is_symlink() or not candidate.is_file() or sha256_file(candidate) != expected_hash:
                raise ValueError(f"Pinned X200 U-Boot source patch mismatch: {name}")
        destination = build_source.joinpath(*PurePosixPath(_X200_UBOOT_OVERLAY_PATH).parts)
        try:
            with destination.open("xb") as output_file:
                output_file.write(patch_bytes)
        except FileExistsError as exc:
            raise ValueError("X200 U-Boot overlay destination already exists") from exc

    def _capture_gnu_objcopy(
        self,
        run_dir: Path,
        logs_dir: Path,
        commands: list[CommandRecord],
    ) -> tuple[str, str, str]:
        executable = shutil.which("objcopy")
        if executable is None:
            raise ValueError("Pinned X200 build requires GNU objcopy on PATH")
        path = Path(executable).resolve(strict=True)
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError("X200 GNU objcopy must resolve to an executable regular file")
        version = self._run_readonly((str(path), "--version"), run_dir, logs_dir, commands).splitlines()
        if not version or not version[0].startswith("GNU objcopy "):
            raise ValueError("X200 build requires GNU objcopy, not an unverified substitute")
        return (str(path), sha256_file(path), version[0].strip())

    @staticmethod
    def _verify_x200_vboot_overlay_stage(build_source: Path) -> None:
        resource_bytes = _x200_vboot_overlay_resource_bytes()
        staged = build_source.joinpath(*PurePosixPath(_X200_VBOOT_OVERLAY_RELATIVE_PATH).parts)
        if (
            staged.is_symlink()
            or not staged.is_file()
            or hashlib.sha256(resource_bytes).hexdigest() != _X200_VBOOT_OVERLAY_SHA256
            or sha256_file(staged) != _X200_VBOOT_OVERLAY_SHA256
        ):
            raise ValueError("Pinned X200 vboot compatibility overlay changed after staging")

    def _patch_file_produces_commit_tree(
        self,
        repository: Path,
        parent_commit: str,
        commit: str,
        patch_file: Path,
        logs_dir: Path,
        command_records: list[CommandRecord],
    ) -> bool:
        """Apply one pinned email patch to an isolated Git index and compare its tree.

        This validates patch content rather than trusting only its mail subject or
        the final source file. All Git commands are fixed, local, and no-shell;
        the temporary index is outside both the checkout and the cache.
        """
        if (
            not _GIT_OBJECT_RE.fullmatch(parent_commit)
            or not _GIT_OBJECT_RE.fullmatch(commit)
            or patch_file.is_symlink()
            or not patch_file.is_file()
        ):
            return False
        repo = Path(repository).resolve(strict=True)
        try:
            expected_tree = self._run_readonly(
                ("git", "-C", str(repo), "rev-parse", f"{commit}^{{tree}}"),
                repo,
                logs_dir,
                command_records,
            ).strip()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            return False
        temporary_parent = logs_dir.parent / "tmp"
        temporary_parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="uboot-patch-index-",
            dir=temporary_parent,
        ) as temporary:
            index_path = Path(temporary) / "index"
            patch_commands = (
                ["git", "-C", str(repo), "read-tree", parent_commit],
                ["git", "-C", str(repo), "apply", "--cached", "--check", str(patch_file)],
                ["git", "-C", str(repo), "apply", "--cached", str(patch_file)],
            )
            for argv in patch_commands:
                try:
                    self._run_readonly(
                        argv,
                        repo,
                        logs_dir,
                        command_records,
                        git_index_path=index_path,
                    )
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                    return False
            try:
                actual_tree = self._run_readonly(
                    ("git", "-C", str(repo), "write-tree"),
                    repo,
                    logs_dir,
                    command_records,
                    git_index_path=index_path,
                ).strip()
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                return False
            return actual_tree == expected_tree

    def _collect_x200_uboot_dependency(
        self,
        build_source: Path,
        objcopy: tuple[str, str, str],
        logs_dir: Path,
        commands: list[CommandRecord],
        *,
        after_build: bool = False,
    ) -> X200UBootDependencyEvidence:
        package_cfg = build_source / "config" / "git" / "u-boot" / "pkg.cfg"
        target_cfg = build_source / "config" / "u-boot" / "x86_64" / "target.cfg"
        amd64_target_cfg = build_source / "config" / "u-boot" / "amd64coreboot" / "target.cfg"
        amd64_default = build_source / "config" / "u-boot" / "amd64coreboot" / "config" / "default"
        for candidate in (package_cfg, target_cfg, amd64_target_cfg, amd64_default):
            if candidate.is_symlink() or not candidate.is_file():
                raise ValueError("Pinned U-Boot package or target configuration is missing or unsafe")
        hashes = tuple(sha256_file(candidate) for candidate in (package_cfg, target_cfg, amd64_target_cfg, amd64_default))
        if hashes != (
            _UBOOT_PACKAGE_CFG_SHA256,
            _UBOOT_X86_64_TARGET_CFG_SHA256,
            _UBOOT_AMD64_TARGET_CFG_SHA256,
            _UBOOT_AMD64_DEFAULT_SHA256,
        ):
            raise ValueError("Pinned U-Boot package/target configuration hash mismatch")
        package_values = dict(re.findall(r'^([A-Za-z0-9_]+)="([^"\n]*)"$', package_cfg.read_text(encoding="utf-8"), re.MULTILINE))
        target_values = dict(re.findall(r'^([A-Za-z0-9_]+)="([^"\n]*)"$', target_cfg.read_text(encoding="utf-8"), re.MULTILINE))
        if (
            package_values.get("url") != _UBOOT_X86_64_SOURCE_URL
            or package_values.get("bkup_url") != _UBOOT_X86_64_BACKUP_URL
            or target_values.get("tree") != "x86_64"
            or target_values.get("rev") != _UBOOT_X86_64_BASE_REVISION
        ):
            raise ValueError("Pinned U-Boot source URL or target revision changed")
        patch_directory = build_source.joinpath(*PurePosixPath(_X200_UBOOT_PATCH_DIR).parts)
        actual_patch_names = sorted(
            path.name for path in patch_directory.glob("*.patch") if path.is_file() and not path.is_symlink()
        )
        if actual_patch_names != sorted(name for name, _ in _X200_UBOOT_PATCH_STACK):
            raise ValueError("X200 U-Boot patch directory contains an unexpected patch stack")
        overlay = patch_directory / _X200_UBOOT_OVERLAY_INPUT[0]
        if overlay.is_symlink() or not overlay.is_file() or sha256_file(overlay) != _X200_UBOOT_OVERLAY_SHA256:
            raise ValueError("X200 U-Boot overlay is missing or changed in the disposable lbmk tree")
        for name, expected_hash in _X200_UBOOT_PATCH_INPUTS:
            candidate = patch_directory / name
            if candidate.is_symlink() or not candidate.is_file() or sha256_file(candidate) != expected_hash:
                raise ValueError(f"X200 U-Boot source patch changed: {name}")

        lbmk_revision = self._run_readonly(("git", "-C", str(build_source), "rev-parse", "HEAD"), build_source, logs_dir, commands).strip()
        lbmk_tree = self._run_readonly(("git", "-C", str(build_source), "rev-parse", "HEAD^{tree}"), build_source, logs_dir, commands).strip()
        lbmk_origin = self._run_readonly(("git", "-C", str(build_source), "remote", "get-url", "origin"), build_source, logs_dir, commands).strip()
        lbmk_status = self._run_readonly(("git", "-C", str(build_source), "status", "--porcelain", "--untracked-files=all"), build_source, logs_dir, commands)
        _validate_x200_lbmk_overlay_root_state(
            revision=lbmk_revision,
            tree_id=lbmk_tree,
            origin=lbmk_origin,
            status=tuple(filter(None, lbmk_status.splitlines())),
            after_build=after_build,
        )
        if after_build:
            self._verify_x200_vboot_overlay_stage(build_source)
        uboot_source = build_source / "src" / "u-boot" / "x86_64"
        for parent_dir in (build_source / "src", build_source / "src" / "u-boot", uboot_source):
            if parent_dir.is_symlink() or not parent_dir.is_dir():
                raise ValueError("lbmk did not produce a safe x86_64 U-Boot source checkout")
        worktree_origin = self._run_readonly(("git", "-C", str(uboot_source), "remote", "get-url", "origin"), build_source, logs_dir, commands).strip()
        origin_path = Path(worktree_origin)
        if not origin_path.is_absolute() or ".." in origin_path.parts:
            raise ValueError("lbmk U-Boot origin is not a canonical local cache path")
        root = build_source.resolve(strict=True)
        relative_origin = next(
            (relative for relative in _uboot_x86_64_cache_origins() if origin_path == root.joinpath(*PurePosixPath(relative).parts)),
            None,
        )
        if relative_origin is None:
            raise ValueError("lbmk U-Boot origin is outside the exact pinned cache locations")
        cache_repository = root.joinpath(*PurePosixPath(relative_origin).parts)
        _assert_no_symlink_components(cache_repository, root=root)
        cache_toplevel = self._run_readonly(
            ("git", "-C", str(cache_repository), "rev-parse", "--show-toplevel"),
            build_source,
            logs_dir,
            commands,
        ).strip()
        cache_git_dir = self._run_readonly(
            ("git", "-C", str(cache_repository), "rev-parse", "--absolute-git-dir"),
            build_source,
            logs_dir,
            commands,
        ).strip()
        cache_remote = self._run_readonly(
            ("git", "-C", str(cache_repository), "remote", "get-url", "origin"),
            build_source,
            logs_dir,
            commands,
        ).strip()
        cache_base_revision = self._run_readonly(
            ("git", "-C", str(cache_repository), "rev-parse", _UBOOT_X86_64_BASE_REVISION),
            build_source,
            logs_dir,
            commands,
        ).strip()
        cache_base_tree = self._run_readonly(
            ("git", "-C", str(cache_repository), "rev-parse", f"{_UBOOT_X86_64_BASE_REVISION}^{{tree}}"),
            build_source,
            logs_dir,
            commands,
        ).strip()
        relative_origin = _validate_x200_uboot_cache_binding(
            root,
            worktree_origin,
            cache_toplevel,
            cache_git_dir,
            cache_remote,
            cache_base_revision,
            cache_base_tree,
        )
        base_tree = self._run_readonly(("git", "-C", str(uboot_source), "rev-parse", f"{_UBOOT_X86_64_BASE_REVISION}^{{tree}}"), build_source, logs_dir, commands).strip()
        revision = self._run_readonly(("git", "-C", str(uboot_source), "rev-parse", "HEAD"), build_source, logs_dir, commands).strip()
        tree_id = self._run_readonly(("git", "-C", str(uboot_source), "rev-parse", "HEAD^{tree}"), build_source, logs_dir, commands).strip()
        self._run_readonly(("git", "-C", str(uboot_source), "merge-base", "--is-ancestor", _UBOOT_X86_64_BASE_REVISION, "HEAD"), build_source, logs_dir, commands)
        patch_commits = tuple(filter(None, self._run_readonly((
            "git", "-C", str(uboot_source), "rev-list", "--reverse",
            f"{_UBOOT_X86_64_BASE_REVISION}..HEAD",
        ), build_source, logs_dir, commands).splitlines()))
        patch_subjects: list[str] = []
        previous_patch_commit = _UBOOT_X86_64_BASE_REVISION
        for (name, _), commit in zip(_X200_UBOOT_PATCH_STACK, patch_commits, strict=True):
            patch_file = patch_directory / name
            expected_subject = _email_patch_subject(patch_file.read_bytes())
            observed_subject = self._run_readonly(
                ("git", "-C", str(uboot_source), "show", "-s", "--format=%s", commit),
                build_source,
                logs_dir,
                commands,
            ).strip()
            if observed_subject != expected_subject:
                raise ValueError(f"Applied U-Boot patch order/content mismatch: {name}")
            parent_commit = self._run_readonly(
                ("git", "-C", str(uboot_source), "rev-parse", f"{commit}^1"),
                build_source,
                logs_dir,
                commands,
            ).strip()
            if parent_commit != previous_patch_commit or not self._patch_file_produces_commit_tree(
                uboot_source, previous_patch_commit, commit, patch_file, logs_dir, commands
            ):
                raise ValueError(f"Applied U-Boot patch tree does not match its pinned patch bytes: {name}")
            previous_patch_commit = commit
            patch_subjects.append(observed_subject)
        tracked_status = self._run_readonly(("git", "-C", str(uboot_source), "status", "--porcelain", "--untracked-files=no"), build_source, logs_dir, commands)
        last_commit_paths = tuple(filter(None, self._run_readonly((
            "git", "-C", str(uboot_source), "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD",
        ), build_source, logs_dir, commands).splitlines()))
        base_config = self._run_readonly((
            "git", "-C", str(uboot_source), "show", f"{_UBOOT_X86_64_BASE_REVISION}:arch/x86/config.mk",
        ), build_source, logs_dir, commands).encode("utf-8")
        final_config_path = uboot_source / "arch" / "x86" / "config.mk"
        if final_config_path.is_symlink() or not final_config_path.is_file():
            raise ValueError("Patched U-Boot x86 configuration is missing or unsafe")
        final_config = final_config_path.read_bytes()
        old_line = b"EFI_TARGET := --target=efi-app-x86_64"
        new_line = b"EFI_TARGET := --output-target=efi-app-x86_64"
        if base_config.count(old_line) != 1 or b"EFI_TARGET := --target=efi-app-ia32" not in base_config:
            raise ValueError("Pinned U-Boot base x86 EFI target lines differ from the reviewed source")
        expected_config = base_config.replace(old_line, new_line, 1)
        if (
            base_tree != _UBOOT_X86_64_BASE_TREE
            or not _GIT_OBJECT_RE.fullmatch(revision)
            or not _GIT_OBJECT_RE.fullmatch(tree_id)
            or len(patch_commits) != len(_X200_UBOOT_PATCH_STACK)
            or len(patch_subjects) != len(_X200_UBOOT_PATCH_STACK)
            or any(not _GIT_OBJECT_RE.fullmatch(commit) for commit in patch_commits)
            or previous_patch_commit != revision
            or patch_commits[-1] != revision
            or tree_id != _UBOOT_X86_64_PATCHED_TREE
            or tracked_status.strip()
            or last_commit_paths != ("arch/x86/config.mk",)
            or hashlib.sha256(base_config).hexdigest() != _UBOOT_X86_64_BASE_CONFIG_SHA256
            or hashlib.sha256(expected_config).hexdigest() != _UBOOT_X86_64_PATCHED_CONFIG_SHA256
            or final_config != expected_config
        ):
            raise ValueError("Patched U-Boot checkout does not prove the exact eight-patch source transformation")
        return X200UBootDependencyEvidence(
            source_url=_UBOOT_X86_64_SOURCE_URL,
            backup_url=_UBOOT_X86_64_BACKUP_URL,
            package_config_sha256=hashes[0],
            target_config_sha256=hashes[1],
            amd64_target_config_sha256=hashes[2],
            amd64_default_config_sha256=hashes[3],
            target_tree="x86_64",
            base_revision=_UBOOT_X86_64_BASE_REVISION,
            base_tree_id=_UBOOT_X86_64_BASE_TREE,
            base_config_sha256=_UBOOT_X86_64_BASE_CONFIG_SHA256,
            overlay_path=_X200_UBOOT_OVERLAY_PATH,
            overlay_sha256=_X200_UBOOT_OVERLAY_SHA256,
            patched_config_sha256=_UBOOT_X86_64_PATCHED_CONFIG_SHA256,
            patch_inputs=_X200_UBOOT_PATCH_STACK,
            patch_commits=patch_commits,
            resulting_revision=revision,
            resulting_tree_id=tree_id,
            cache_origin_relative_path=relative_origin,
            resulting_origin_url=cache_remote,
            base_ancestor_verified=True,
            tracked_worktree_clean=True,
            objcopy_path=objcopy[0],
            objcopy_sha256=objcopy[1],
            objcopy_version=objcopy[2],
        )

    def _validate_x200_uboot_dependency_after_build(
        self,
        recipe: ArtifactRecipe,
        build_source: Path,
        run_dir: Path,
        logs_dir: Path,
        commands: list[CommandRecord],
        before: X200UBootDependencyEvidence,
    ) -> X200UBootDependencyEvidence:
        if recipe.workflow_id != "x200-libreboot":
            raise ValueError("X200 U-Boot validation was requested for a different model workflow")
        after_objcopy = self._capture_gnu_objcopy(run_dir, logs_dir, commands)
        after = self._collect_x200_uboot_dependency(
            build_source, after_objcopy, logs_dir, commands, after_build=True
        )
        if after != before:
            raise ValueError("X200 U-Boot dependency source or GNU objcopy changed during the build")
        return after

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
            nested_repositories = self._validate_lbmk_nested_coreboot(source, logs_dir, commands)
            coreboot_evidence = next(item for item in nested_repositories if item.role == "coreboot")
            nested_origin = coreboot_evidence.repository_url
            nested_revision = coreboot_evidence.resulting_revision
            nested_tree = coreboot_evidence.resulting_tree_id
            parser_source_hashes = coreboot_evidence.source_file_hashes
            tool = source / "elf" / "coreboot" / "default" / "cbfstool"
            relative_tool_path = tool.relative_to(source).as_posix()
            command = (str(tool), str(artifact), "print", "-k")
            region_name = "whole_flash"
            region_size = dict(recipe.config_expectations)["CONFIG_CBFS_SIZE"]
            region_size_bytes = int(region_size, 0)
            required_payload = "fallback/payload"
            required_payload_type = "simple elf"
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
            required_payload_type = "payload"
        else:
            raise ValueError("T430 handoff recipes have no CBFS artifact to inspect")
        if tool.is_symlink() or not tool.is_file():
            raise ValueError("Pinned-source cbfstool executable is missing or unsafe")
        output = self._run_readonly(command, source, logs_dir, commands)
        inventory = parse_cbfs_parseable_output(
            output,
            required_payload=required_payload,
            region_size_bytes=region_size_bytes,
            required_payload_type=required_payload_type,
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
            nested_repositories=(nested_repositories if recipe.kind is RecipeKind.LIBREBOOT else ()),
        )

    def _validate_lbmk_nested_coreboot(
        self,
        build_source: Path,
        logs_dir: Path,
        commands: list[CommandRecord],
    ) -> tuple[X200NestedRepositoryEvidence, ...]:
        """Validate lbmk's exact generated Coreboot/submodule patch chain.

        lbmk converts configured patches into locally generated commits, so
        generated commit IDs are deliberately recorded rather than pinned.
        The base commit/tree, in-root cache remote, exact patch bytes, linear
        ancestry, every resulting patch tree and final source tree are pinned.
        """
        root = _require_external_path(Path(build_source), "disposable lbmk source", must_exist=True)
        if root.is_symlink() or not root.is_dir():
            raise ValueError("Disposable lbmk source is missing or unsafe")
        root = root.resolve(strict=True)
        root_revision = self._git_value(root, "rev-parse", "HEAD")
        root_tree = self._git_value(root, "rev-parse", "HEAD^{tree}")
        root_origin = self._git_value(root, "remote", "get-url", "origin")
        root_status = tuple(filter(None, self._git_value(
            root, "status", "--porcelain=v1", "--untracked-files=all"
        ).splitlines()))
        _validate_x200_lbmk_overlay_root_state(
            revision=root_revision,
            tree_id=root_tree,
            origin=root_origin,
            status=root_status,
            after_build=True,
        )
        tree_script = root / "include" / "tree.sh"
        if tree_script.is_symlink() or not tree_script.is_file() or sha256_file(tree_script) != _X200_LBMK_TREE_SH_PATCHED_SHA256:
            raise ValueError("X200 lbmk tree script changed from its exact reviewed staged overlay")
        self._verify_x200_vboot_overlay_stage(root)

        coreboot = root / "src" / "coreboot" / "default"
        coreboot_evidence = self._validate_lbmk_patched_repository(
            role="coreboot",
            checkout=coreboot,
            root=root,
            cache_relative_path=_X200_COREBOOT_CACHE_RELATIVE_PATH,
            repository_url=_X200_COREBOOT_ORIGIN_URL,
            base_revision=_LBMK_COREBOOT_REVISION,
            base_tree_id=_LBMK_COREBOOT_TREE,
            expected_result_tree=_X200_COREBOOT_POST_TREE,
            patch_directory=PurePosixPath("config/coreboot/default/patches"),
            expected_patch_inputs=None,
            expected_patch_count=_X200_COREBOOT_PATCH_COUNT,
            logs_dir=logs_dir,
            commands=commands,
            source_file_pins=_LBMK_COREBOOT_CBFSTOOL_SOURCES,
            expected_generated_status=_X200_COREBOOT_GENERATED_STATUS,
        )
        if coreboot_evidence.resulting_tree_id != _X200_COREBOOT_POST_TREE:
            raise ValueError("lbmk Coreboot patch stack produced an unexpected final source tree")

        coreboot_status = self._validate_lbmk_coreboot_gitlinks(coreboot)
        if coreboot_status != coreboot_evidence.generated_status:
            raise ValueError("Coreboot nested status changed while source provenance was being recorded")
        libgfxinit = coreboot / "3rdparty" / "libgfxinit"
        libgfx_evidence = self._validate_lbmk_patched_repository(
            role="libgfxinit",
            checkout=libgfxinit,
            root=root,
            cache_relative_path=_X200_LIBGFXINIT_CACHE_RELATIVE_PATH,
            repository_url=_X200_LIBGFXINIT_ORIGIN_URL,
            base_revision=_X200_LIBGFXINIT_BASE_REVISION,
            base_tree_id=_X200_LIBGFXINIT_BASE_TREE,
            expected_result_tree=_X200_LIBGFXINIT_POST_TREE,
            patch_directory=None,
            expected_patch_inputs=_X200_LIBGFXINIT_PATCH_INPUTS,
            expected_patch_count=len(_X200_LIBGFXINIT_PATCH_INPUTS),
            logs_dir=logs_dir,
            commands=commands,
        )
        vboot = coreboot / "3rdparty" / "vboot"
        vboot_evidence = self._validate_lbmk_patched_repository(
            role="vboot",
            checkout=vboot,
            root=root,
            cache_relative_path=_X200_VBOOT_CACHE_RELATIVE_PATH,
            repository_url=_X200_VBOOT_ORIGIN_URL,
            base_revision=_X200_VBOOT_BASE_REVISION,
            base_tree_id=_X200_VBOOT_BASE_TREE,
            expected_result_tree=_X200_VBOOT_POST_OVERLAY_TREE,
            patch_directory=None,
            expected_patch_inputs=_X200_VBOOT_PATCH_STACK,
            expected_patch_count=len(_X200_VBOOT_PATCH_STACK),
            logs_dir=logs_dir,
            commands=commands,
            source_file_pins=(PinnedFile(_X200_VBOOT_SOURCE_FILE, _X200_VBOOT_POSTIMAGE_SHA256),),
            expected_patch_tree_ids=(_X200_VBOOT_PRE_OVERLAY_TREE, _X200_VBOOT_POST_OVERLAY_TREE),
        )
        fsp_evidence = self._validate_lbmk_unpatched_repository(
            role="fsp",
            checkout=root.joinpath(*PurePosixPath(_X200_FSP_RELATIVE_PATH.removeprefix("work/lbmk/")).parts),
            root=root,
            cache_relative_path=_X200_FSP_CACHE_RELATIVE_PATH,
            repository_url=_X200_FSP_ORIGIN_URL,
            expected_revision=_X200_FSP_REVISION,
            expected_tree=_X200_FSP_TREE,
        )
        evidence = (coreboot_evidence, libgfx_evidence, vboot_evidence, fsp_evidence)
        _validate_x200_nested_repository_evidence(evidence)
        return evidence

    def _validate_lbmk_patched_repository(
        self,
        *,
        role: str,
        checkout: Path,
        root: Path,
        cache_relative_path: str,
        repository_url: str,
        base_revision: str,
        base_tree_id: str,
        expected_result_tree: str,
        patch_directory: PurePosixPath | None,
        expected_patch_inputs: tuple[tuple[str, str], ...] | None,
        expected_patch_count: int,
        logs_dir: Path,
        commands: list[CommandRecord],
        source_file_pins: Sequence[PinnedFile] = (),
        expected_patch_tree_ids: tuple[str, ...] | None = None,
        expected_generated_status: tuple[str, ...] = (),
    ) -> X200NestedRepositoryEvidence:
        checkout = Path(checkout)
        _assert_no_symlink_components(checkout, root=root)
        if not checkout.is_dir():
            raise ValueError(f"lbmk {role} nested checkout is missing")
        checkout = checkout.resolve(strict=True)
        cache = root.joinpath(*PurePosixPath(cache_relative_path).parts)
        _assert_no_symlink_components(cache, root=root)
        if not cache.is_dir():
            raise ValueError(f"lbmk {role} pinned local source cache is missing")
        expected_origin = cache.resolve(strict=True)
        checkout_origin = self._git_value(checkout, "remote", "get-url", "origin")
        cache_toplevel = self._git_value(cache, "rev-parse", "--show-toplevel")
        cache_remote = self._git_value(cache, "remote", "get-url", "origin")
        cache_base_revision = self._git_value(cache, "rev-parse", base_revision)
        cache_base_tree = self._git_value(cache, "rev-parse", f"{base_revision}^{{tree}}")
        if (
            not Path(checkout_origin).is_absolute()
            or Path(checkout_origin).resolve(strict=True) != expected_origin
            or Path(cache_toplevel).resolve(strict=True) != expected_origin
            or cache_remote != repository_url
            or cache_base_revision != base_revision
            or cache_base_tree != base_tree_id
        ):
            raise ValueError(f"lbmk {role} source/cache origin or pinned base identity changed")
        actual_base_tree = self._git_value(checkout, "rev-parse", f"{base_revision}^{{tree}}")
        if actual_base_tree != base_tree_id:
            raise ValueError(f"lbmk {role} checkout does not contain the exact pinned base tree")

        if expected_patch_inputs is None:
            if patch_directory is None:
                raise ValueError("Pinned nested patch directory contract is missing")
            patch_root = root.joinpath(*patch_directory.parts)
            _assert_no_symlink_components(patch_root, root=root)
            if not patch_root.is_dir():
                raise ValueError(f"lbmk {role} pinned patch input directory is missing")
            entries = tuple(patch_root.iterdir())
            if any(path.is_symlink() or not path.is_file() for path in entries):
                raise ValueError(f"lbmk {role} patch input directory contains an unsafe entry")
            if len(entries) != expected_patch_count:
                raise ValueError(f"lbmk {role} patch input count differs from the pinned source tree")
            patch_inputs = tuple(
                (path.relative_to(root).as_posix(), sha256_file(path))
                for path in sorted(entries, key=lambda item: item.name)
            )
        else:
            patch_inputs = expected_patch_inputs
        if len(patch_inputs) != expected_patch_count:
            raise ValueError(f"lbmk {role} patch stack has an unexpected number of inputs")

        rows = self._git_value(
            checkout,
            "rev-list", "--reverse", "--parents", f"{base_revision}..HEAD",
        ).splitlines()
        if len(rows) != expected_patch_count:
            raise ValueError(f"lbmk {role} generated patch history has an unexpected commit count")
        patch_tree_ids: list[str] = []
        commits: list[str] = []
        previous = base_revision
        resolved_patch_inputs: list[tuple[str, str]] = []
        for row, (patch_relative, expected_digest) in zip(rows, patch_inputs, strict=True):
            fields = row.split()
            if len(fields) != 2 or fields[1] != previous or not _GIT_OBJECT_RE.fullmatch(fields[0]):
                raise ValueError(f"lbmk {role} patch history is not a linear chain from the pinned base")
            commit = fields[0]
            patch_path = root.joinpath(*PurePosixPath(patch_relative).parts)
            _assert_no_symlink_components(patch_path, root=root)
            if not patch_path.is_file() or sha256_file(patch_path) != expected_digest:
                raise ValueError(f"lbmk {role} patch bytes differ from the staged/pinned source: {patch_relative}")
            subject = _email_patch_subject(patch_path.read_bytes())
            observed_subject = self._git_value(checkout, "show", "-s", "--format=%s", commit)
            if observed_subject != subject:
                raise ValueError(f"lbmk {role} applied patch subject/order differs from its exact patch bytes")
            if not self._patch_file_produces_commit_tree(
                checkout, previous, commit, patch_path, logs_dir, commands
            ):
                raise ValueError(f"lbmk {role} patch commit tree differs from its exact patch bytes")
            tree_id = self._git_value(checkout, "rev-parse", f"{commit}^{{tree}}")
            commits.append(commit)
            patch_tree_ids.append(tree_id)
            resolved_patch_inputs.append((patch_relative, expected_digest))
            previous = commit
        revision = self._git_value(checkout, "rev-parse", "HEAD")
        result_tree = self._git_value(checkout, "rev-parse", "HEAD^{tree}")
        status = tuple(filter(None, self._git_value(
            checkout, "status", "--porcelain=v1", "--untracked-files=all"
        ).splitlines()))
        if (
            revision != previous
            or result_tree != expected_result_tree
            or tuple(sorted(status)) != tuple(sorted(expected_generated_status))
            or (expected_patch_tree_ids is not None and tuple(patch_tree_ids) != expected_patch_tree_ids)
        ):
            raise ValueError(f"lbmk {role} final commit/tree/status differs from the exact reviewed patch chain")

        source_hashes: list[tuple[str, str]] = []
        for pinned in source_file_pins:
            candidate = checkout.joinpath(*PurePosixPath(pinned.path).parts)
            _assert_no_symlink_components(candidate, root=checkout)
            if not candidate.is_file() or sha256_file(candidate) != pinned.sha256:
                raise ValueError(f"lbmk {role} post-patch source file hash mismatch: {pinned.path}")
            source_hashes.append((pinned.path, pinned.sha256))
        if role == "vboot":
            if len(commits) != 2:
                raise ValueError("Pinned vboot patch chain must include the pre-overlay and overlay commits")
            self._validate_x200_vboot_pre_postimage(checkout, commits[0], revision)
        operation_prefix = "work/lbmk/"
        return X200NestedRepositoryEvidence(
            role=role,
            relative_path=f"{operation_prefix}{checkout.relative_to(root).as_posix()}",
            repository_url=repository_url,
            cache_origin_relative_path=f"{operation_prefix}{cache_relative_path}",
            base_revision=base_revision,
            base_tree_id=base_tree_id,
            resulting_revision=revision,
            resulting_tree_id=result_tree,
            patch_inputs=tuple((f"{operation_prefix}{path}", digest) for path, digest in resolved_patch_inputs),
            patch_revisions=tuple(commits),
            patch_tree_ids=tuple(patch_tree_ids),
            source_file_hashes=tuple(sorted(source_hashes)),
            source_file_preimage_hashes=(
                ((_X200_VBOOT_SOURCE_FILE, _X200_VBOOT_PREIMAGE_SHA256),)
                if role == "vboot"
                else ()
            ),
            generated_status=tuple(sorted(status)),
            generated_files=(
                _capture_x200_coreboot_generated_files(checkout)
                if role == "coreboot"
                else ()
            ),
        )

    def _validate_lbmk_unpatched_repository(
        self,
        *,
        role: str,
        checkout: Path,
        root: Path,
        cache_relative_path: str,
        repository_url: str,
        expected_revision: str,
        expected_tree: str,
    ) -> X200NestedRepositoryEvidence:
        checkout = Path(checkout)
        _assert_no_symlink_components(checkout, root=root)
        cache = root.joinpath(*PurePosixPath(cache_relative_path).parts)
        _assert_no_symlink_components(cache, root=root)
        if not checkout.is_dir() or not cache.is_dir():
            raise ValueError(f"lbmk {role} checkout/cache is missing")
        checkout_origin = self._git_value(checkout, "remote", "get-url", "origin")
        cache_toplevel = self._git_value(cache, "rev-parse", "--show-toplevel")
        if (
            not Path(checkout_origin).is_absolute()
            or Path(checkout_origin).resolve(strict=True) != cache.resolve(strict=True)
            or Path(cache_toplevel).resolve(strict=True) != cache.resolve(strict=True)
            or self._git_value(cache, "remote", "get-url", "origin") != repository_url
            or self._git_value(checkout, "rev-parse", "HEAD") != expected_revision
            or self._git_value(checkout, "rev-parse", "HEAD^{tree}") != expected_tree
            or self._git_value(checkout, "status", "--porcelain=v1", "--untracked-files=all")
        ):
            raise ValueError(f"lbmk {role} source is not the exact clean pinned checkout")
        cache_revision = self._git_value(cache, "rev-parse", expected_revision)
        cache_tree = self._git_value(cache, "rev-parse", f"{expected_revision}^{{tree}}")
        if cache_revision != expected_revision or cache_tree != expected_tree:
            raise ValueError(f"lbmk {role} cache lacks the exact pinned source object")
        return X200NestedRepositoryEvidence(
            role=role,
            relative_path=f"work/lbmk/{checkout.relative_to(root).as_posix()}",
            repository_url=repository_url,
            cache_origin_relative_path=f"work/lbmk/{cache_relative_path}",
            base_revision=expected_revision,
            base_tree_id=expected_tree,
            resulting_revision=expected_revision,
            resulting_tree_id=expected_tree,
            patch_inputs=(),
            patch_revisions=(),
            patch_tree_ids=(),
        )

    def _validate_lbmk_coreboot_gitlinks(self, coreboot: Path) -> tuple[str, ...]:
        expected = {
            "3rdparty/libgfxinit": _X200_LIBGFXINIT_BASE_REVISION,
            "3rdparty/vboot": _X200_VBOOT_BASE_REVISION,
        }
        for path, revision in expected.items():
            actual = self._git_value(coreboot, "rev-parse", f"HEAD:{path}")
            if actual != revision:
                raise ValueError(f"Pinned Coreboot superproject gitlink changed: {path}")
        status = tuple(filter(None, self._git_value(
            coreboot, "status", "--porcelain=v1", "--untracked-files=all"
        ).splitlines()))
        if tuple(sorted(status)) != tuple(sorted(_X200_COREBOOT_GENERATED_STATUS)):
            raise ValueError("Coreboot nested submodule/FSP worktree status differs from the exact lbmk output")
        return _X200_COREBOOT_GENERATED_STATUS

    def _validate_x200_vboot_pre_postimage(
        self,
        checkout: Path,
        pre_overlay_revision: str,
        resulting_revision: str,
    ) -> None:
        path = _X200_VBOOT_SOURCE_FILE
        before = subprocess.run(
            ["git", "-C", str(checkout), "show", f"{pre_overlay_revision}:{path}"],
            check=False,
            capture_output=True,
            timeout=30,
        )
        if before.returncode != 0 or hashlib.sha256(before.stdout).hexdigest() != _X200_VBOOT_PREIMAGE_SHA256:
            raise ValueError("Pinned vboot base/parser preimage differs from the reviewed const-correctness overlay")
        after = subprocess.run(
            ["git", "-C", str(checkout), "show", f"{resulting_revision}:{path}"],
            check=False,
            capture_output=True,
            timeout=30,
        )
        if after.returncode != 0 or hashlib.sha256(after.stdout).hexdigest() != _X200_VBOOT_POSTIMAGE_SHA256:
            raise ValueError("Patched vboot parser postimage differs from the reviewed const-correctness overlay")
        _validate_x200_vboot_overlay_bytes(
            _x200_vboot_overlay_resource_bytes(), before.stdout, after.stdout
        )

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
        *,
        x200_unifont_path: Path | None = None,
    ) -> None:
        self._run_command(
            argv,
            cwd,
            logs_dir,
            commands,
            capture=False,
            x200_unifont_path=x200_unifont_path,
        )

    def _run_readonly(
        self,
        argv: Sequence[str],
        cwd: Path,
        logs_dir: Path,
        commands: list[CommandRecord],
        *,
        git_index_path: Path | None = None,
        x200_unifont_path: Path | None = None,
    ) -> str:
        return self._run_command(
            argv,
            cwd,
            logs_dir,
            commands,
            capture=True,
            git_index_path=git_index_path,
            x200_unifont_path=x200_unifont_path,
        )

    def _run_command(
        self,
        argv: Sequence[str],
        cwd: Path,
        logs_dir: Path,
        commands: list[CommandRecord],
        *,
        capture: bool,
        git_index_path: Path | None = None,
        x200_unifont_path: Path | None = None,
    ) -> str:
        if not argv or any("\x00" in part for part in argv):
            raise ValueError("Invalid empty or NUL-containing build command")
        log_path = logs_dir / f"{len(commands) + 1:02d}.log"
        temporary_dir = logs_dir.parent / "tmp"
        temporary_dir.mkdir(exist_ok=True)
        git_config_path, git_config_sha256 = _verify_operation_git_config(logs_dir.parent)
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(logs_dir.parent),
            "TMPDIR": str(temporary_dir),
            "GIT_CONFIG_GLOBAL": str(git_config_path),
            "GIT_CONFIG_NOSYSTEM": "1",
            "LC_ALL": "C",
            "LANG": "C",
            "TZ": "UTC",
        }
        if x200_unifont_path is not None:
            operation_dir = logs_dir.parent.resolve(strict=True)
            expected_font = operation_dir / PurePosixPath(_X200_GRUB_FONT_STAGED_RELATIVE_PATH)
            expected_build_source = operation_dir / PurePosixPath(_X200_UBOOT_LBMK_BUILD_ROOT)
            if (
                tuple(argv) not in {X200_4MB.build_command, X200_8MB.build_command}
                or Path(cwd).resolve(strict=True) != expected_build_source
                or Path(x200_unifont_path) != expected_font
                or sha256_file(_require_operation_file(operation_dir, _X200_GRUB_FONT_STAGED_RELATIVE_PATH))
                != _X200_GRUB_FONT_SOURCE_SHA256
            ):
                raise ValueError("Only the exact staged X200 Unifont path may enter the pinned lbmk command")
            environment["LIBRECORE_UNIFONT_PATH"] = str(expected_font)
        if git_index_path is not None:
            index = Path(git_index_path)
            try:
                relative_index = index.relative_to(temporary_dir.resolve(strict=True))
            except ValueError as exc:
                raise ValueError("Temporary Git index must remain inside the operation temporary directory") from exc
            if (
                len(relative_index.parts) != 2
                or relative_index.name != "index"
                or index.parent.parent.resolve(strict=True) != temporary_dir.resolve(strict=True)
                or index.parent.is_symlink()
                or index.is_symlink()
            ):
                raise ValueError("Temporary Git index path is unsafe")
            environment["GIT_INDEX_FILE"] = str(index)
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
        try:
            _, actual_git_config_sha256 = _verify_operation_git_config(logs_dir.parent)
        except (OSError, ValueError) as exc:
            raise RuntimeError("Operation Git config changed or became unavailable during the command") from exc
        if actual_git_config_sha256 != git_config_sha256:
            raise RuntimeError("Operation Git config changed during the command")
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
        if args[:1] == ("status",) and "--porcelain=v1" in args:
            # Porcelain's leading column is meaningful (for example, " M" is
            # a worktree-only modification).  Strip record terminators only;
            # str.strip() would erase that status column and break exact state
            # validation for the disposable X200 overlay root.
            return result.stdout.rstrip("\r\n")
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


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"Persisted {label} must be a JSON object")
    return value


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"Persisted {label} must be a JSON array")
    return value


def _integer(row: Mapping[str, object], key: str) -> int:
    value = row.get(key)
    if type(value) is not int:
        raise ValueError(f"Persisted {key} must be an integer")
    return value


def _optional_integer(row: Mapping[str, object], key: str) -> int | None:
    value = row.get(key)
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError(f"Persisted {key} must be an integer or null")
    return value


def _optional_bool(row: Mapping[str, object], key: str) -> bool | None:
    value = row.get(key)
    if value is None:
        return None
    if type(value) is not bool:
        raise ValueError(f"Persisted {key} must be a boolean or null")
    return value


def _required_bool(row: Mapping[str, object], key: str) -> bool:
    value = row.get(key)
    if type(value) is not bool:
        raise ValueError(f"Persisted {key} must be a boolean")
    return value


def _optional_text(row: Mapping[str, object], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Persisted {key} must be text or null")
    return value


def _region_from_mapping(value: object) -> RegionEvidence:
    row = _mapping(value, "region")
    return RegionEvidence(str(row["name"]), _integer(row, "start_byte"), _integer(row, "end_byte"))


def _operation_plan_from_mapping(value: object) -> OperationPlan:
    row = _mapping(value, "operation plan")
    device_row = _mapping(row["device"], "device")
    topology_row = _mapping(row["topology"], "topology")
    programmer_row = _mapping(row["programmer"], "programmer")
    artifact_row = _mapping(row["artifact"], "artifact")
    chips: list[ChipEvidence] = []
    for raw_chip in _array(topology_row["chips"], "chip topology"):
        chip = _mapping(raw_chip, "chip")
        chips.append(ChipEvidence(
            chip_id=str(chip["chip_id"]),
            capacity_bytes=_integer(chip, "capacity_bytes"),
            observed=_required_bool(chip, "observed"),
            physical_label=str(chip.get("physical_label", "")),
            voltage_mv=_optional_integer(chip, "voltage_mv"),
            manufacturer=_optional_text(chip, "manufacturer"),
            model_number=_optional_text(chip, "model_number"),
            voltage_evidence_source=_optional_text(chip, "voltage_evidence_source"),
            voltage_attested_by=_optional_text(chip, "voltage_attested_by"),
            voltage_attested_at=_optional_text(chip, "voltage_attested_at"),
            voltage_instrument_id=_optional_text(chip, "voltage_instrument_id"),
            voltage_test_point=_optional_text(chip, "voltage_test_point"),
        ))
    topology = ChipTopology(
        observed=_required_bool(topology_row, "observed"),
        chips=tuple(chips),
        regions=tuple(_region_from_mapping(item) for item in _array(topology_row.get("regions"), "regions")),
    )
    programmer = ProgrammerEvidence(
        kind=ProgrammerKind(str(programmer_row["kind"])),
        observed=_required_bool(programmer_row, "observed"),
        interface_verified=_optional_bool(programmer_row, "interface_verified"),
        wiring_verified=_optional_bool(programmer_row, "wiring_verified"),
        voltage_mv=_optional_integer(programmer_row, "voltage_mv"),
        evidence_source=_optional_text(programmer_row, "evidence_source"),
        attested_by=_optional_text(programmer_row, "attested_by"),
        attested_at=_optional_text(programmer_row, "attested_at"),
        instrument_id=_optional_text(programmer_row, "instrument_id"),
        measurement_test_point=_optional_text(programmer_row, "measurement_test_point"),
    )
    artifact = ArtifactEvidence(
        path=str(artifact_row["path"]),
        model_id=str(artifact_row["model_id"]),
        firmware_family=FirmwareFamily(str(artifact_row["firmware_family"])),
        expected_sha256=str(artifact_row["expected_sha256"]),
        expected_size_bytes=_integer(artifact_row, "expected_size_bytes"),
        source_url=str(artifact_row["source_url"]),
        source_revision=str(artifact_row["source_revision"]),
        regions=tuple(_region_from_mapping(item) for item in _array(artifact_row.get("regions", []), "artifact regions")),
    )
    backup_row = _mapping(row.get("backup", {}), "backup")
    pairs: list[BackupPairEvidence] = []
    for raw_pair in _array(backup_row.get("pairs", []), "backup pairs"):
        pair = _mapping(raw_pair, "backup pair")
        pairs.append(BackupPairEvidence(
            chip_id=str(pair["chip_id"]),
            first_path=str(pair["first_path"]),
            second_path=str(pair["second_path"]),
            first_sha256=str(pair["first_sha256"]),
            second_sha256=str(pair["second_sha256"]),
            first_capture_id=str(pair.get("first_capture_id", "")),
            second_capture_id=str(pair.get("second_capture_id", "")),
            transient=_required_bool(pair, "transient"),
        ))
    device = DeviceEvidence(
        raw_dmi_product_name=_optional_text(device_row, "raw_dmi_product_name"),
        raw_dmi_product_version=_optional_text(device_row, "raw_dmi_product_version"),
        raw_dmi_system_family=_optional_text(device_row, "raw_dmi_system_family"),
        raw_mtm=_optional_text(device_row, "raw_mtm"),
        raw_bios_version=_optional_text(device_row, "raw_bios_version"),
        raw_bios_vendor=_optional_text(device_row, "raw_bios_vendor"),
        stock_lenovo_bios=_optional_bool(device_row, "stock_lenovo_bios"),
        manual_model_candidate=_optional_text(device_row, "manual_model_candidate"),
        internal_flash_unlock_status=_optional_bool(device_row, "internal_flash_unlock_status"),
        internal_flash_unlock_observed=_required_bool(device_row, "internal_flash_unlock_observed"),
    )
    return OperationPlan(
        profile_id=str(row["profile_id"]),
        requested_model_id=str(row["requested_model_id"]),
        firmware_family=FirmwareFamily(str(row["firmware_family"])),
        device=device,
        topology=topology,
        programmer=programmer,
        artifact=artifact,
        backup=BackupEvidence(tuple(pairs)),
    )


def _artifact_manifest_from_mapping(value: object) -> ArtifactManifest:
    row = _mapping(value, "artifact manifest")
    expected_keys = set(ArtifactManifest.__dataclass_fields__)
    if set(row) != expected_keys:
        raise ValueError("Persisted artifact manifest has missing or unsupported fields")
    artifacts: list[ArtifactRecord] = []
    for raw_item in _array(row["artifacts"], "artifact inventory"):
        item = _mapping(raw_item, "artifact record")
        cbfs_value = item.get("cbfs_inspection")
        cbfs: CBFSInspectionRecord | None = None
        if cbfs_value is not None:
            entry = _mapping(cbfs_value, "CBFS inspection")
            entries = tuple(CBFSEntry(
                name=str(_mapping(raw_entry, "CBFS entry")["name"]),
                offset_bytes=_integer(_mapping(raw_entry, "CBFS entry"), "offset_bytes"),
                entry_type=str(_mapping(raw_entry, "CBFS entry")["entry_type"]),
                metadata_size_bytes=_integer(_mapping(raw_entry, "CBFS entry"), "metadata_size_bytes"),
                data_size_bytes=_integer(_mapping(raw_entry, "CBFS entry"), "data_size_bytes"),
                total_size_bytes=_integer(_mapping(raw_entry, "CBFS entry"), "total_size_bytes"),
            ) for raw_entry in _array(entry["inventory"], "CBFS inventory"))
            nested_rows: list[X200NestedRepositoryEvidence] = []
            for raw_nested in _array(entry.get("nested_repositories", []), "CBFS nested repositories"):
                nested = _mapping(raw_nested, "CBFS nested repository")
                def text_pairs(key: str) -> tuple[tuple[str, str], ...]:
                    pairs: list[tuple[str, str]] = []
                    for raw_pair in _array(nested.get(key, []), f"nested repository {key}"):
                        pair = _array(raw_pair, f"nested repository {key} pair")
                        if len(pair) != 2 or any(not isinstance(item, str) for item in pair):
                            raise ValueError(f"Persisted nested repository {key} must contain text pairs")
                        pairs.append((str(pair[0]), str(pair[1])))
                    return tuple(pairs)
                patch_trees = _array(nested.get("patch_tree_ids", []), "nested repository patch trees")
                patch_revisions = _array(nested.get("patch_revisions", []), "nested repository patch revisions")
                generated_status = _array(nested.get("generated_status", []), "nested repository generated status")
                if any(not isinstance(item, str) for item in (*patch_trees, *patch_revisions, *generated_status)):
                    raise ValueError("Persisted nested repository commit, tree and status fields must be text")
                generated_files: list[X200GeneratedFileEvidence] = []
                for raw_generated in _array(nested.get("generated_files", []), "nested repository generated files"):
                    generated = _mapping(raw_generated, "generated nested repository file")
                    if set(generated) != set(X200GeneratedFileEvidence.__dataclass_fields__):
                        raise ValueError("Persisted generated-file evidence has missing or unsupported fields")
                    if any(not isinstance(generated.get(key), str) for key in ("relative_path", "sha256", "kind")):
                        raise ValueError("Persisted generated-file path, hash and kind must be text")
                    generated_files.append(X200GeneratedFileEvidence(
                        relative_path=str(generated["relative_path"]),
                        size_bytes=_integer(generated, "size_bytes"),
                        sha256=str(generated["sha256"]),
                        kind=str(generated["kind"]),
                        source_relative_path=_optional_text(generated, "source_relative_path"),
                        source_sha256=_optional_text(generated, "source_sha256"),
                        elf_class=_optional_text(generated, "elf_class"),
                        elf_type=_optional_text(generated, "elf_type"),
                        elf_machine=_optional_text(generated, "elf_machine"),
                    ))
                nested_rows.append(X200NestedRepositoryEvidence(
                    role=str(nested["role"]),
                    relative_path=str(nested["relative_path"]),
                    repository_url=str(nested["repository_url"]),
                    cache_origin_relative_path=str(nested["cache_origin_relative_path"]),
                    base_revision=str(nested["base_revision"]),
                    base_tree_id=str(nested["base_tree_id"]),
                    resulting_revision=str(nested["resulting_revision"]),
                    resulting_tree_id=str(nested["resulting_tree_id"]),
                    patch_inputs=text_pairs("patch_inputs"),
                    patch_revisions=tuple(str(item) for item in patch_revisions),
                    patch_tree_ids=tuple(str(item) for item in patch_trees),
                    source_file_hashes=text_pairs("source_file_hashes"),
                    source_file_preimage_hashes=text_pairs("source_file_preimage_hashes"),
                    generated_status=tuple(str(item) for item in generated_status),
                    generated_files=tuple(generated_files),
                ))
            cbfs = CBFSInspectionRecord(
                tool_relative_path=str(entry["tool_relative_path"]),
                tool_sha256=str(entry["tool_sha256"]),
                source_repository_url=str(entry["source_repository_url"]),
                source_revision=str(entry["source_revision"]),
                source_tree_id=str(entry["source_tree_id"]),
                region=str(entry["region"]),
                required_payload=str(entry["required_payload"]),
                inventory=entries,
                source_file_hashes=tuple((str(pair[0]), str(pair[1])) for pair in _array(entry["source_file_hashes"], "CBFS source hashes") if isinstance(pair, list) and len(pair) == 2),
                nested_repositories=tuple(nested_rows),
            )
        provenance_value = item.get("backup_provenance")
        provenance = None
        if provenance_value is not None:
            p = _mapping(provenance_value, "backup provenance")
            provenance = BackupProvenanceRecord(
                operation_id=str(p["operation_id"]), profile_id=str(p["profile_id"]),
                primary_manifest_sha256=str(p["primary_manifest_sha256"]),
                recovery_manifest_sha256=str(p["recovery_manifest_sha256"]),
                original_chip_sha256=str(p["original_chip_sha256"]),
                build_intent_sha256=str(p["build_intent_sha256"]),
                preserved_regions=tuple(str(v) for v in _array(p["preserved_regions"], "preserved regions")),
            )
        tool_value = item.get("region_inspection_tool")
        region_tool = None
        if tool_value is not None:
            tool = _mapping(tool_value, "region inspection tool")
            region_tool = ToolEvidence(**{key: str(tool[key]) for key in ToolEvidence.__dataclass_fields__})
        artifacts.append(ArtifactRecord(
            relative_path=str(item["relative_path"]) if item.get("relative_path") is not None else None,
            sha256=str(item["sha256"]),
            size_bytes=_integer(item, "size_bytes"),
            regions=tuple(_region_from_mapping(region) for region in _array(item["regions"], "artifact record regions")),
            source_relative_path=str(item["source_relative_path"]),
            cbfs_inspection=cbfs,
            backup_provenance=provenance,
            region_inspection_tool=region_tool,
        ))
    commands: list[CommandRecord] = []
    for raw_command in _array(row["commands"], "command records"):
        command = _mapping(raw_command, "command record")
        commands.append(CommandRecord(
            argv=tuple(str(arg) for arg in _array(command["argv"], "command argv")),
            exit_code=_integer(command, "exit_code"),
            log_relative_path=str(command["log_relative_path"]),
            log_sha256=str(command["log_sha256"]),
        ))
    repro_row = _mapping(row["reproducibility"], "reproducibility record")
    finalized = row.get("finalized_operation_plan")
    dependency_value = row.get("x200_uboot_dependency")
    dependency = None
    if dependency_value is not None:
        entry = _mapping(dependency_value, "X200 U-Boot dependency evidence")
        if set(entry) != set(X200UBootDependencyEvidence.__dataclass_fields__):
            raise ValueError("Persisted X200 U-Boot evidence has missing or unsupported fields")
        raw_patch_inputs = _array(entry["patch_inputs"], "X200 U-Boot patch inputs")
        patch_inputs: list[tuple[str, str]] = []
        for raw_pair in raw_patch_inputs:
            pair = _array(raw_pair, "X200 U-Boot patch input")
            if len(pair) != 2 or any(not isinstance(part, str) for part in pair):
                raise ValueError("Persisted X200 U-Boot patch input must be a text pair")
            patch_name, patch_digest = pair
            if not isinstance(patch_name, str) or not isinstance(patch_digest, str):
                raise ValueError("Persisted X200 U-Boot patch input must be a text pair")
            patch_inputs.append((patch_name, patch_digest))
        raw_commits = _array(entry["patch_commits"], "X200 U-Boot patch commits")
        if any(not isinstance(commit, str) for commit in raw_commits):
            raise ValueError("Persisted X200 U-Boot patch commits must be text")
        text_fields = {
            "source_url", "backup_url", "package_config_sha256", "target_config_sha256",
            "amd64_target_config_sha256", "amd64_default_config_sha256", "target_tree",
            "base_revision", "base_tree_id", "base_config_sha256", "overlay_path",
            "overlay_sha256", "patched_config_sha256", "resulting_revision", "resulting_tree_id",
            "cache_origin_relative_path", "resulting_origin_url", "objcopy_path", "objcopy_sha256", "objcopy_version",
        }
        if any(not isinstance(entry[key], str) for key in text_fields):
            raise ValueError("Persisted X200 U-Boot dependency text fields are malformed")
        dependency = X200UBootDependencyEvidence(
            source_url=str(entry["source_url"]),
            backup_url=str(entry["backup_url"]),
            package_config_sha256=str(entry["package_config_sha256"]),
            target_config_sha256=str(entry["target_config_sha256"]),
            amd64_target_config_sha256=str(entry["amd64_target_config_sha256"]),
            amd64_default_config_sha256=str(entry["amd64_default_config_sha256"]),
            target_tree=str(entry["target_tree"]),
            base_revision=str(entry["base_revision"]),
            base_tree_id=str(entry["base_tree_id"]),
            base_config_sha256=str(entry["base_config_sha256"]),
            overlay_path=str(entry["overlay_path"]),
            overlay_sha256=str(entry["overlay_sha256"]),
            patched_config_sha256=str(entry["patched_config_sha256"]),
            patch_inputs=tuple(patch_inputs),
            patch_commits=tuple(str(commit) for commit in raw_commits),
            resulting_revision=str(entry["resulting_revision"]),
            resulting_tree_id=str(entry["resulting_tree_id"]),
            cache_origin_relative_path=str(entry["cache_origin_relative_path"]),
            resulting_origin_url=str(entry["resulting_origin_url"]),
            base_ancestor_verified=_required_bool(entry, "base_ancestor_verified"),
            tracked_worktree_clean=_required_bool(entry, "tracked_worktree_clean"),
            objcopy_path=str(entry["objcopy_path"]),
            objcopy_sha256=str(entry["objcopy_sha256"]),
            objcopy_version=str(entry["objcopy_version"]),
        )
    grub_font_value = row.get("x200_grub_font")
    grub_font = None
    if grub_font_value is not None:
        font_entry = _mapping(grub_font_value, "X200 GRUB font evidence")
        if set(font_entry) != set(X200GrubFontEvidence.__dataclass_fields__):
            raise ValueError("Persisted X200 GRUB font evidence has missing or unsupported fields")
        text_fields = {
            "source_font_path", "source_font_sha256", "source_package", "source_package_version",
            "staged_font_relative_path", "staged_font_sha256", "overlay_resource_path",
            "overlay_resource_sha256", "lbmk_tree_sh_sha256", "lbmk_tree_sh_patched_sha256",
            "grub_source_relative_path", "grub_repository_url", "grub_cache_relative_path",
            "grub_base_revision", "grub_base_tree_id", "grub_result_revision", "grub_result_tree_id",
            "configure_log_relative_path", "configure_log_sha256", "generated_payload_relative_path",
            "generated_payload_sha256",
        }
        if any(not isinstance(font_entry[key], str) for key in text_fields):
            raise ValueError("Persisted X200 GRUB font text fields are malformed")
        raw_grub_patch_inputs = _array(font_entry["grub_patch_inputs"], "X200 GRUB patch inputs")
        grub_input_pairs: list[tuple[str, str]] = []
        for raw_grub_patch_row in raw_grub_patch_inputs:
            if (
                not isinstance(raw_grub_patch_row, list)
                or len(raw_grub_patch_row) != 2
                or not isinstance(raw_grub_patch_row[0], str)
                or not isinstance(raw_grub_patch_row[1], str)
            ):
                raise ValueError("Persisted X200 GRUB patch input rows are malformed")
            grub_input_pairs.append((raw_grub_patch_row[0], raw_grub_patch_row[1]))
        raw_patch_commits = _array(font_entry["grub_patch_commits"], "X200 GRUB patch commits")
        raw_patch_trees = _array(font_entry["grub_patch_tree_ids"], "X200 GRUB patch trees")
        raw_configure_argv = _array(font_entry["configure_argv"], "X200 GRUB configure argv")
        if any(not isinstance(value, str) for value in (*raw_patch_commits, *raw_patch_trees, *raw_configure_argv)):
            raise ValueError("Persisted X200 GRUB patch commits or configure argv are malformed")
        grub_font = X200GrubFontEvidence(
            source_font_path=str(font_entry["source_font_path"]),
            source_font_sha256=str(font_entry["source_font_sha256"]),
            source_package=str(font_entry["source_package"]),
            source_package_version=str(font_entry["source_package_version"]),
            staged_font_relative_path=str(font_entry["staged_font_relative_path"]),
            staged_font_sha256=str(font_entry["staged_font_sha256"]),
            overlay_resource_path=str(font_entry["overlay_resource_path"]),
            overlay_resource_sha256=str(font_entry["overlay_resource_sha256"]),
            lbmk_tree_sh_sha256=str(font_entry["lbmk_tree_sh_sha256"]),
            lbmk_tree_sh_patched_sha256=str(font_entry["lbmk_tree_sh_patched_sha256"]),
            grub_source_relative_path=str(font_entry["grub_source_relative_path"]),
            grub_repository_url=str(font_entry["grub_repository_url"]),
            grub_cache_relative_path=str(font_entry["grub_cache_relative_path"]),
            grub_base_revision=str(font_entry["grub_base_revision"]),
            grub_base_tree_id=str(font_entry["grub_base_tree_id"]),
            grub_patch_inputs=tuple(grub_input_pairs),
            grub_patch_commits=tuple(cast(list[str], raw_patch_commits)),
            grub_patch_tree_ids=tuple(cast(list[str], raw_patch_trees)),
            grub_result_revision=str(font_entry["grub_result_revision"]),
            grub_result_tree_id=str(font_entry["grub_result_tree_id"]),
            configure_log_relative_path=str(font_entry["configure_log_relative_path"]),
            configure_log_sha256=str(font_entry["configure_log_sha256"]),
            configure_argv=tuple(cast(list[str], raw_configure_argv)),
            generated_payload_relative_path=str(font_entry["generated_payload_relative_path"]),
            generated_payload_sha256=str(font_entry["generated_payload_sha256"]),
        )
    return ArtifactManifest(
        schema_version=_integer(row, "schema_version"), operation_id=str(row["operation_id"]),
        created_at_utc=str(row["created_at_utc"]), recipe_id=str(row["recipe_id"]),
        workflow_id=str(row["workflow_id"]), status=str(row["status"]),
        source_repository_url=str(row["source_repository_url"]), source_revision=str(row["source_revision"]),
        source_tree_id=str(row["source_tree_id"]),
        source_files=tuple((str(pair[0]), str(pair[1])) for pair in _array(row["source_files"], "source files") if isinstance(pair, list) and len(pair) == 2),
        commands=tuple(commands), host_platform=str(row["host_platform"]),
        toolchain=tuple((str(pair[0]), str(pair[1])) for pair in _array(row["toolchain"], "toolchain") if isinstance(pair, list) and len(pair) == 2),
        build_environment=tuple((str(pair[0]), str(pair[1])) for pair in _array(row["build_environment"], "build environment") if isinstance(pair, list) and len(pair) == 2),
        container_image_digest=str(row["container_image_digest"]) if row.get("container_image_digest") is not None else None,
        configuration_sha256=str(row["configuration_sha256"]),
        resolved_configuration_sha256=str(row["resolved_configuration_sha256"]) if row.get("resolved_configuration_sha256") is not None else None,
        observed_chip_capacity_bytes=_optional_integer(row, "observed_chip_capacity_bytes"),
        expected_regions=tuple(_region_from_mapping(region) for region in _array(row["expected_regions"], "expected regions")),
        blob_sha256=tuple((str(pair[0]), str(pair[1])) for pair in _array(row["blob_sha256"], "blob hashes") if isinstance(pair, list) and len(pair) == 2),
        artifacts=tuple(artifacts),
        selected_artifact_relative_path=str(row["selected_artifact_relative_path"]) if row.get("selected_artifact_relative_path") is not None else None,
        selected_artifact_sha256=str(row["selected_artifact_sha256"]) if row.get("selected_artifact_sha256") is not None else None,
        failure_code=str(row["failure_code"]) if row.get("failure_code") is not None else None,
        failure_detail=str(row["failure_detail"]) if row.get("failure_detail") is not None else None,
        reproducibility=ReproducibilityRecord(
            status=str(repro_row["status"]),
            first_artifact_sha256=str(repro_row["first_artifact_sha256"]) if repro_row.get("first_artifact_sha256") is not None else None,
            second_artifact_sha256=str(repro_row["second_artifact_sha256"]) if repro_row.get("second_artifact_sha256") is not None else None,
            explanation=str(repro_row["explanation"]),
        ),
        finalized_operation_plan=_operation_plan_from_mapping(finalized) if finalized is not None else None,
        prebuild_intent_sha256=str(row["prebuild_intent_sha256"]) if row.get("prebuild_intent_sha256") is not None else None,
        x200_uboot_dependency=dependency,
        x200_grub_font=grub_font,
    )
