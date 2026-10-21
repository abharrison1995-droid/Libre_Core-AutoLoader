"""Fail-closed Archiso build contract.

This module never installs host dependencies or invokes an implicit privilege
escalation. Candidate images use a fixed, signature-required package snapshot;
this builder never claims that a candidate boots or is release validated.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import math
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from usb_builder.mount_probe import (
    INTEGRITY_EXIT as MOUNT_PROBE_INTEGRITY_EXIT,
    PROBE_MARKER as OFFLINE_MOUNT_PROBE_MARKER,
    SUPPORTED_EXIT as MOUNT_PROBE_SUPPORTED_EXIT,
    UNSUPPORTED_EXIT as MOUNT_PROBE_UNSUPPORTED_EXIT,
    current_namespace_identities as _current_namespace_identities,
    validate_mount_probe_output as _validate_mount_probe_output,
)
from usb_builder.network_probe import PROBE_MARKER as OFFLINE_NETWORK_PROBE_MARKER
from usb_builder import rootless_mount_probe
from usb_builder import rootless_offline_preflight


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = REPOSITORY_ROOT / "usb_builder" / "archiso_profile"
DEFAULT_SNAPSHOT = REPOSITORY_ROOT / "usb_builder" / "snapshot.lock.json"
WORK_PREFIX = "librecore-autoloader-"
ARCHISO_INITRAMFS_HOOK_PACKAGES = {
    "archiso": ("mkinitcpio-archiso", "mkinitcpio", "pv"),
    "archiso_loop_mnt": ("mkinitcpio-archiso",),
    "archiso_pxe_common": ("mkinitcpio-archiso", "gnupg", "openssl"),
    "archiso_pxe_nbd": ("mkinitcpio-archiso", "nbd"),
    "archiso_pxe_http": ("mkinitcpio-archiso", "curl", "ca-certificates-utils"),
    "archiso_pxe_nfs": ("mkinitcpio-archiso", "mkinitcpio-nfs-utils", "nfs-utils"),
}
ARCHISO_INITRAMFS_BASE_PACKAGES = ("bash", "coreutils", "device-mapper", "mkinitcpio", "util-linux")
ARCHISO_INITRAMFS_REQUIRED_PACKAGES = frozenset(ARCHISO_INITRAMFS_BASE_PACKAGES).union(
    package for packages in ARCHISO_INITRAMFS_HOOK_PACKAGES.values() for package in packages
)
REQUIRED_PACKAGES = frozenset(
    {
        "base",
        "linux",
        "linux-firmware",
        "python",
        "python-pip",
        "python-textual",
        "python-rich",
        "python-click",
        "python-psutil",
        "flashrom",
        "dmidecode",
        "usbutils",
        "pciutils",
        "syslinux",
    }
) | ARCHISO_INITRAMFS_REQUIRED_PACKAGES
REQUIRED_MODES = frozenset(("online", "offline"))
REQUIRED_BOOT_MODES = frozenset(("bios.syslinux", "uefi.systemd-boot"))
EXPECTED_MKARCHISO_VERSION = "91-1"
EXPECTED_ARCH_INSTALL_SCRIPTS_VERSION = "31-2"
ROOTLESS_HOST_PACKAGES = (
    "archiso",
    "arch-install-scripts",
    "pacman",
    "shadow",
    "libcap",
    "util-linux",
    "bash",
)
ROOTLESS_SUBID_MINIMUM = 65536
ROOTLESS_CAP_SETGID = 6
ROOTLESS_CAP_SETUID = 7
ROOTLESS_CAP_BND_EXPECTED = (1 << ROOTLESS_CAP_SETUID) | (1 << ROOTLESS_CAP_SETGID)
ROOTLESS_SHADOW_VERSION = "4.20.0.arch1-1"
ROOTLESS_ARCH_INSTALL_SCRIPTS_VERSION = "31-2"
ROOTLESS_ARCH_INSTALL_SCRIPTS_PACKAGE_SHA256 = "21844567f7811bb957cb57982c697ff6a466a13ea3c8ad2cd159a7603c83608e"
ROOTLESS_ARCH_INSTALL_SCRIPTS_SIGNATURE_SHA256 = "1ea8adf9657c03ffa11af114babce8dee8269abb166a19ed8aa43b0892fbb00f"
ROOTLESS_ARCH_INSTALL_SCRIPTS_SOURCE_PATH = "/usr/bin/pacstrap"
ROOTLESS_PACSTRAP_SHA256 = "c0cbe2eb3e77a2a4437b03021ec1e63f31f20888778cd3c70ff49d2368371207"
ROOTLESS_PACSTRAP_PATCH_ID = "arch-install-scripts-31-2-pacstrap-preserve-child-status-v1"
ROOTLESS_PACSTRAP_PATCH_RELATIVE_PATH = "patches/arch-install-scripts-31-2-pacstrap-status.patch"
ROOTLESS_PACSTRAP_PATCH_SHA256 = "11394f8b901364236d449c35a84170eda29818bc9d41cbadd9677a04fddbceaa"
ROOTLESS_PACSTRAP_PATCHED_SHA256 = "3964ed5b509f791e9f98cda494c4af5febe6daf0174df75b9bb10a7af3f2c04c"
ROOTLESS_PACSTRAP_PATCH_TEXT = (
    "--- a/usr/bin/pacstrap\n"
    "+++ b/usr/bin/pacstrap\n"
    "@@ -295,11 +295,14 @@\n"
    " if (( unshare )); then\n"
    "   setup=unshare_setup\n"
    "   $mount_unshare bash -c \"$(declare_all); pacstrap\"\n"
    "+  result=$?\n"
    " else\n"
    "   setup=chroot_setup\n"
    "   pacstrap\n"
    "+  result=$?\n"
    " fi\n"
    " \n"
    " # TODO: There is a trap check on exit. Need to rework the trap handling with\n"
    " # hook-ins/callbacks to remove aux files\n"
    "-rm \"$tmpfile\"\n"
    "+rm \"$tmpfile\" || exit 1\n"
    "+exit \"$result\"\n"
)
ROOTLESS_PACSTRAP_PATCH_BYTES = ROOTLESS_PACSTRAP_PATCH_TEXT.encode("utf-8")
ROOTLESS_PACSTRAP_PATCHED_RESULT_CONTEXT = (
    b'if (( unshare )); then\n'
    b'  setup=unshare_setup\n'
    b'  $mount_unshare bash -c "$(declare_all); pacstrap"\n'
    b'else\n'
    b'  setup=chroot_setup\n'
    b'  pacstrap\n'
    b'fi\n'
    b'\n'
    b'# TODO: There is a trap check on exit. Need to rework the trap handling with\n'
    b'# hook-ins/callbacks to remove aux files\n'
    b'rm "$tmpfile"\n'
)
ROOTLESS_PACSTRAP_PATCHED_RESULT_REPLACEMENT = (
    b'if (( unshare )); then\n'
    b'  setup=unshare_setup\n'
    b'  $mount_unshare bash -c "$(declare_all); pacstrap"\n'
    b'  result=$?\n'
    b'else\n'
    b'  setup=chroot_setup\n'
    b'  pacstrap\n'
    b'  result=$?\n'
    b'fi\n'
    b'\n'
    b'# TODO: There is a trap check on exit. Need to rework the trap handling with\n'
    b'# hook-ins/callbacks to remove aux files\n'
    b'rm "$tmpfile" || exit 1\n'
    b'exit "$result"\n'
)
ROOTLESS_ARCH_CHROOT_SHA256 = "56c50ac2f6d58325d4f5eaa0a0e359ca3019bffe08b7b37468de3bddae09e8d5"
ROOTLESS_PACMAN_VERSION = "7.1.0.r9.g54d9411-2"
ROOTLESS_PACMAN_PACKAGE_SHA256 = "2092ec7a0391416e4a1757c455bc071d7b58a124cedc83f582cb7d37d52f211b"
ROOTLESS_PACMAN_SIGNATURE_SHA256 = "3e31debd8c57ac1c84c9586e57611e95e8a2ff7dfb8f3b54be95028ee36644e2"
ROOTLESS_PACMAN_SHA256 = "bef41dfdb55bf620aa6d5b50a657b6d04b894d1b48da7d447a9d23a29ff66478"
ROOTLESS_UTIL_LINUX_VERSION = "2.42.4-1"
ROOTLESS_UTIL_LINUX_PACKAGE_SHA256 = "95b7096bf4b45274645f6bb96f5b5508af6ea80e5fccba29244abaa53a341962"
ROOTLESS_UTIL_LINUX_SIGNATURE_SHA256 = "3fbd1ba4d323e2bb047e39d1f3642744a86582d11ef502c3cc31469846e4c453"
ROOTLESS_UTIL_LINUX_TOOL_SHA256 = {
    "setpriv": "d12c6ed1085a2997b791ff40a4b9a3d4f3d865b67af3051b62a64112bbf8cb53",
    "unshare": "e4e385ae4accf4af5302c4bb32859355b1c640315bef56ce73d31b8b1f97fdc0",
    "mount": "ca6c26aa2b11a8431481d39ee562bc07a88984f3294f34846d3257ec28ae9c91",
    "umount": "b8f6dcb0b8b7a790a431c954beebcb3db5931d8db81b02e09baa1e92beeaafac",
}
ROOTLESS_BASH_VERSION = "5.3.20-1"
ROOTLESS_BASH_PACKAGE_SHA256 = "73a67dc509ed4a6dd64e4c7eca2895643f48083940f49431782ce78c4d4619e1"
ROOTLESS_BASH_SIGNATURE_SHA256 = "3f9eeacf7bff152be34b6181bc8dcb656c4fef6bf2b576b842a63dfd877871d7"
ROOTLESS_BASH_SHA256 = "c14c8f498f3be96f076ceb07d65dba584a86dca3c86b5dce65ed0f6cae73358d"
ROOTLESS_HELPER_SHA256 = {
    "newuidmap": "85099fcbafde31fe5f27dbca529e3da1531f9b97f899db84758132313445af36",
    "newgidmap": "326370106ae7d202a21216d75eb74dfc38ba93ac81d0fe9dc3ddd19bd68efa2f",
}
ROOTLESS_SHADOW_PACKAGE_SHA256 = "615c819eb2d3e22158289f3cc2447f81ae981a6d11b3418da30b6085306b3e77"
ROOTLESS_SHADOW_SIGNATURE_SHA256 = "4b2c1845cb09de397536097b15c4d9b5a3282fe051a21e20c43efe4bd584c95a"
ROOTLESS_LIBCAP_VERSION = "2.78-1"
ROOTLESS_LIBCAP_PACKAGE_SHA256 = "3e984fe1d323b1c5a5fc60fef005776a4882fbca913d6fe3c41ad11128929ed1"
ROOTLESS_LIBCAP_SIGNATURE_SHA256 = "ea04b0e2c6ed14aa62978b0048b17e01d55ace6959ab088edf006af209ebb6ee"
ROOTLESS_GETCAP_SHA256 = "3d8bc2191227c1ee2fad5d83c025e8b62e283338673a1630babe6b81ee232814"
ROOTLESS_HELPER_CAPABILITIES = {
    "newuidmap": "cap_setuid=ep",
    "newgidmap": "cap_setgid=ep",
}
ROOTLESS_TOOL_CONTRACT: dict[str, dict[str, Any]] = {
    "pacman": {"path": "/usr/bin/pacman", "package": "pacman", "sha256": ROOTLESS_PACMAN_SHA256, "mode": 0o755, "file_capabilities": ""},
    "newuidmap": {"path": "/usr/bin/newuidmap", "package": "shadow", "sha256": ROOTLESS_HELPER_SHA256["newuidmap"], "mode": 0o755, "file_capabilities": "cap_setuid=ep"},
    "newgidmap": {"path": "/usr/bin/newgidmap", "package": "shadow", "sha256": ROOTLESS_HELPER_SHA256["newgidmap"], "mode": 0o755, "file_capabilities": "cap_setgid=ep"},
    "getcap": {"path": "/usr/bin/getcap", "package": "libcap", "sha256": ROOTLESS_GETCAP_SHA256, "mode": 0o755, "file_capabilities": ""},
    "pacstrap": {"path": "/usr/bin/pacstrap", "package": "arch-install-scripts", "sha256": ROOTLESS_PACSTRAP_SHA256, "mode": 0o755, "file_capabilities": ""},
    "arch-chroot": {"path": "/usr/bin/arch-chroot", "package": "arch-install-scripts", "sha256": ROOTLESS_ARCH_CHROOT_SHA256, "mode": 0o755, "file_capabilities": ""},
    "setpriv": {"path": "/usr/bin/setpriv", "package": "util-linux", "sha256": ROOTLESS_UTIL_LINUX_TOOL_SHA256["setpriv"], "mode": 0o755, "file_capabilities": ""},
    "unshare": {"path": "/usr/bin/unshare", "package": "util-linux", "sha256": ROOTLESS_UTIL_LINUX_TOOL_SHA256["unshare"], "mode": 0o755, "file_capabilities": ""},
    "mount": {"path": "/usr/bin/mount", "package": "util-linux", "sha256": ROOTLESS_UTIL_LINUX_TOOL_SHA256["mount"], "mode": 0o4755, "file_capabilities": ""},
    "umount": {"path": "/usr/bin/umount", "package": "util-linux", "sha256": ROOTLESS_UTIL_LINUX_TOOL_SHA256["umount"], "mode": 0o4755, "file_capabilities": ""},
    "bash": {"path": "/usr/bin/bash", "package": "bash", "sha256": ROOTLESS_BASH_SHA256, "mode": 0o755, "file_capabilities": ""},
}
ROOTLESS_BUILDER_LOCK = {
    "arch_install_scripts_package": "arch-install-scripts",
    "arch_install_scripts_version": EXPECTED_ARCH_INSTALL_SCRIPTS_VERSION,
    "arch_install_scripts_package_sha256": ROOTLESS_ARCH_INSTALL_SCRIPTS_PACKAGE_SHA256,
    "arch_install_scripts_signature_sha256": ROOTLESS_ARCH_INSTALL_SCRIPTS_SIGNATURE_SHA256,
    "pacman_package": "pacman",
    "pacman_version": ROOTLESS_PACMAN_VERSION,
    "pacman_package_sha256": ROOTLESS_PACMAN_PACKAGE_SHA256,
    "pacman_signature_sha256": ROOTLESS_PACMAN_SIGNATURE_SHA256,
    "pacman_sha256": ROOTLESS_PACMAN_SHA256,
    "pacstrap_sha256": ROOTLESS_PACSTRAP_SHA256,
    "pacstrap_status_overlay": {
        "id": ROOTLESS_PACSTRAP_PATCH_ID,
        "source_path": ROOTLESS_ARCH_INSTALL_SCRIPTS_SOURCE_PATH,
        "source_sha256": ROOTLESS_PACSTRAP_SHA256,
        "patch_path": "usb_builder/" + ROOTLESS_PACSTRAP_PATCH_RELATIVE_PATH,
        "patch_sha256": ROOTLESS_PACSTRAP_PATCH_SHA256,
        "patched_sha256": ROOTLESS_PACSTRAP_PATCHED_SHA256,
        "path_policy": "operation-owned pacstrap directory first, then /usr/bin:/usr/sbin",
    },
    "arch_chroot_sha256": ROOTLESS_ARCH_CHROOT_SHA256,
    "arch_install_scripts_shared_runtime_files": [],
    "util_linux_package": "util-linux",
    "util_linux_version": ROOTLESS_UTIL_LINUX_VERSION,
    "util_linux_package_sha256": ROOTLESS_UTIL_LINUX_PACKAGE_SHA256,
    "util_linux_signature_sha256": ROOTLESS_UTIL_LINUX_SIGNATURE_SHA256,
    "util_linux_tools_sha256": dict(ROOTLESS_UTIL_LINUX_TOOL_SHA256),
    "bash_package": "bash",
    "bash_version": ROOTLESS_BASH_VERSION,
    "bash_package_sha256": ROOTLESS_BASH_PACKAGE_SHA256,
    "bash_signature_sha256": ROOTLESS_BASH_SIGNATURE_SHA256,
    "bash_sha256": ROOTLESS_BASH_SHA256,
    "trusted_tool_contract": ROOTLESS_TOOL_CONTRACT,
    "subid_range_minimum": ROOTLESS_SUBID_MINIMUM,
    "map_helper_package": "shadow",
    "map_helper_package_version": ROOTLESS_SHADOW_VERSION,
    "map_helper_package_sha256": ROOTLESS_SHADOW_PACKAGE_SHA256,
    "map_helper_signature_sha256": ROOTLESS_SHADOW_SIGNATURE_SHA256,
    "newuidmap_sha256": ROOTLESS_HELPER_SHA256["newuidmap"],
    "newgidmap_sha256": ROOTLESS_HELPER_SHA256["newgidmap"],
    "required_helper_mode": "0755",
    "required_helper_capabilities": dict(ROOTLESS_HELPER_CAPABILITIES),
    "libcap_package": "libcap",
    "libcap_version": ROOTLESS_LIBCAP_VERSION,
    "libcap_package_sha256": ROOTLESS_LIBCAP_PACKAGE_SHA256,
    "libcap_signature_sha256": ROOTLESS_LIBCAP_SIGNATURE_SHA256,
    "getcap_sha256": ROOTLESS_GETCAP_SHA256,
}
EXPECTED_ALPM_LOCAL_DB_VERSION = "9\n"
ARCHISO_V91_SOURCE_COMMIT = "723da192226e1c9d5def9168f13332793c82a85e"
ARCHISO_V91_INITRAMFS_CONFIG_SHA256 = "cd1d328d0b343719c5975729bf7c8c5a227542beb396574ff4adeac30381db0b"
ARCHISO_V91_INITRAMFS_CONFIG_SOURCE_PATH = "configs/releng/airootfs/etc/mkinitcpio.conf.d/archiso.conf"
ARCHISO_INITRAMFS_CONFIG_RELATIVE_PATH = "airootfs/etc/mkinitcpio.conf.d/archiso.conf"
ARCHISO_USB_INITRAMFS_DERIVATIVE_ID = "archiso-91-usb-remove-kms-v1"
ARCHISO_USB_INITRAMFS_DERIVATIVE_SOURCE_PATH = (
    "usb_builder/archiso_profile/airootfs/etc/mkinitcpio.conf.d/zz-autoloader-usb.conf"
)
ARCHISO_USB_INITRAMFS_DERIVATIVE_RELATIVE_PATH = "airootfs/etc/mkinitcpio.conf.d/zz-autoloader-usb.conf"
ARCHISO_USB_INITRAMFS_DERIVATIVE_SHA256 = "ad143618ba35dd8ff00fab585cb6e6324fdccd6e19d7ca59c4abc69a610a1491"
ARCHISO_USB_INITRAMFS_REMOVED_HOOK = "kms"
ARCHISO_PROFILE_TEXT_INPUTS = (
    "profiledef.sh",
    "pacman.conf",
    "syslinux/syslinux.cfg",
    "syslinux/archiso_sys.cfg",
    "syslinux/archiso_sys-linux.cfg",
    "efiboot/loader/loader.conf",
    "efiboot/loader/entries/01-autoloader.conf",
    "airootfs/root/customize_airootfs.sh",
    "airootfs/opt/autoloader/start.sh",
    "airootfs/etc/systemd/system/autoloader.service",
    "airootfs/etc/motd",
    ARCHISO_INITRAMFS_CONFIG_RELATIVE_PATH,
    ARCHISO_USB_INITRAMFS_DERIVATIVE_RELATIVE_PATH,
)
NETWORK_DISABLED_SYSTEMD_UNITS = (
    "NetworkManager.service",
    "NetworkManager-dispatcher.service",
    "NetworkManager-wait-online.service",
    "avahi-daemon.service",
    "avahi-daemon.socket",
    "connman.service",
    "dhcpcd.service",
    "dhcpcd@.service",
    "iwd.service",
    "systemd-networkd.service",
    "systemd-networkd.socket",
    "systemd-networkd-wait-online.service",
    "systemd-networkd-wait-online@.service",
    "systemd-resolved.service",
    "systemd-resolved.socket",
    "systemd-timesyncd.service",
    "systemd-timesyncd.socket",
    "wpa_supplicant.service",
    "wpa_supplicant@.service",
)
AUTOLOADER_NETWORK_POLICY = {
    "PrivateNetwork": "yes",
    "RestrictAddressFamilies": "AF_UNIX",
    "CapabilityBoundingSet": "~CAP_SYS_ADMIN CAP_NET_ADMIN",
    "NoNewPrivileges": "yes",
}
AUTOLOADER_SERVICE_SHA256 = "2fd94492c77eacd9fd72fee1f67661856c378b8d3c42727fd598a37cfe05a9d5"
ARCHISO_V91_SOURCE_URL = (
    "https://github.com/archlinux/archiso/blob/"
    + ARCHISO_V91_SOURCE_COMMIT
    + "/archiso/mkarchiso"
)
EXPECTED_SNAPSHOT_DATE = "2026-09-30"
REQUIRED_RUNTIME_IMPORTS = ("autoloader", "click", "psutil", "rich", "textual")
TARGET_ROOT_CLI_HELP_WIDTH = 78
SNAPSHOT_REPOSITORIES = ("core", "extra")
SNAPSHOT_DATABASE_RELATIVE_PATHS = {
    repository: f"{repository}/os/x86_64/{repository}.db" for repository in SNAPSHOT_REPOSITORIES
}
SNAPSHOT_FETCH_MARKER = "AUTOLOADER_SNAPSHOT_FETCH_V1"
_SNAPSHOT_FETCH_SCRIPT = (
    "import sys, urllib.error, urllib.parse, urllib.request\n"
    "url, destination, optional = sys.argv[1], sys.argv[2], sys.argv[3] == 'optional'\n"
    f"print('{SNAPSHOT_FETCH_MARKER}', flush=True)\n"
    "def validate_final_url(value):\n"
    "    parsed = urllib.parse.urlsplit(value)\n"
    "    if (parsed.scheme != 'https' or parsed.hostname != 'archive.archlinux.org'\n"
    "            or parsed.username is not None or parsed.password is not None or parsed.port is not None\n"
    "            or parsed.query or parsed.fragment):\n"
    "        raise RuntimeError('snapshot redirect escaped the pinned HTTPS archive host')\n"
    "    if parsed.path != requested_path:\n"
    "        raise RuntimeError('snapshot redirect changed the exact requested archive resource')\n"
    "requested_path = urllib.parse.urlsplit(url).path\n"
    "validate_final_url(url)\n"
    "class PinnedRedirectHandler(urllib.request.HTTPRedirectHandler):\n"
    "    def redirect_request(self, request, fp, code, message, headers, newurl):\n"
    "        target = urllib.parse.urljoin(request.full_url, newurl)\n"
    "        validate_final_url(target)\n"
    "        return super().redirect_request(request, fp, code, message, headers, target)\n"
    "opener = urllib.request.build_opener(PinnedRedirectHandler)\n"
    "try:\n"
    "    response = opener.open(url, timeout=30)\n"
    "except urllib.error.HTTPError as error:\n"
    "    final_url = error.geturl()\n"
    "    validate_final_url(final_url)\n"
    "    print('FINAL_URL=' + final_url)\n"
    "    if optional and error.code == 404:\n"
    "        print('HTTP_STATUS=404')\n"
    "        raise SystemExit(0)\n"
    "    raise\n"
    "with response:\n"
    "    final_url = response.geturl()\n"
    "    validate_final_url(final_url)\n"
    "    print('FINAL_URL=' + final_url)\n"
    "    with open(destination, 'xb') as output:\n"
    "        output.write(response.read())\n"
    "    print('HTTP_STATUS=' + str(response.status))\n"
)


class BuildContractError(RuntimeError):
    """A build request does not satisfy the reviewed environment contract."""


@dataclass(frozen=True)
class BuildConfig:
    source_root: Path
    profile_dir: Path
    output_dir: Path
    work_parent: Path
    snapshot_lock: Path
    network_mode: str
    offline_repository: Path | None = None
    offline_manifest: Path | None = None


@dataclass(frozen=True)
class PreflightResult:
    snapshot: Mapping[str, Any]
    snapshot_sha256: str
    package_names: tuple[str, ...]
    required_boot_modes: tuple[str, ...]
    offline_repository: Path | None
    offline_manifest_sha256: str | None = None


@dataclass(frozen=True)
class BuildResult:
    iso_path: Path
    manifest_path: Path
    sha256: str
    manifest_sha256: str


@dataclass(frozen=True)
class NetworkIsolation:
    mode: str
    command_prefix: tuple[str, ...]
    privilege_mode: str
    unshare_path: str | None
    unshare_sha256: str | None
    unshare_package_owner_output: str | None
    iproute_path: str | None
    iproute_sha256: str | None
    iproute_package_owner_output: str | None
    host_namespace_identity: str | None
    host_user_namespace_identity: str | None
    host_mount_namespace_identity: str | None
    host_pid_namespace_identity: str | None
    probe_command: tuple[str, ...]
    probe_output: str
    mount_probe_command: tuple[str, ...]
    mount_probe_output: str
    host_mount_table_sha256: str | None


@dataclass(frozen=True)
class RootlessToolEvidence:
    name: str
    path: str
    sha256: str
    mode: int
    owner_uid: int
    owner_output: str
    file_capabilities: str


@dataclass(frozen=True)
class RootlessPacstrapOverlayEvidence:
    """Operation-owned, hash-bound pacstrap exit-status correction evidence."""

    patch_id: str
    operation_root: str
    operation_owner_uid: int
    operation_owner_marker_sha256: str
    source_path: str
    source_sha256: str
    source_owner_uid: int
    patch_resource_path: str
    patch_sha256: str
    staged_path: str
    staged_sha256: str
    staged_mode: int
    child_path: str

    def to_manifest(self, *, mkarchiso_argv: Sequence[str]) -> dict[str, Any]:
        return {
            "status": "PATCHED_PACSTRAP_STAGED",
            "patch_id": self.patch_id,
            "operation": {
                "root": self.operation_root,
                "owner_uid": self.operation_owner_uid,
                "owner_marker_sha256": self.operation_owner_marker_sha256,
            },
            "source": {
                "path": self.source_path,
                "package": ROOTLESS_BUILDER_LOCK["arch_install_scripts_package"],
                "version": ROOTLESS_BUILDER_LOCK["arch_install_scripts_version"],
                "sha256": self.source_sha256,
                "owner_uid": self.source_owner_uid,
            },
            "patch": {
                "resource_path": self.patch_resource_path,
                "sha256": self.patch_sha256,
            },
            "staged": {
                "path": self.staged_path,
                "sha256": self.staged_sha256,
                "mode": f"{self.staged_mode:04o}",
            },
            "mkarchiso_argv": list(mkarchiso_argv),
            "mkarchiso_path": self.child_path,
            "lookup_precedence": ["operation-pacstrap", "/usr/bin", "/usr/sbin"],
        }


@dataclass(frozen=True)
class RootlessHostEvidence:
    """Validated host identity needed before invoking Archiso's ``-N`` path."""

    username: str
    uid: int
    gid: int
    primary_group_name: str
    supplementary_gids: tuple[int, ...]
    subuid_start: int
    subuid_count: int
    subgid_start: int
    subgid_count: int
    cap_eff: str
    cap_prm: str
    cap_bnd: str
    cap_amb: str
    no_new_privs: int
    newuidmap_path: str
    newuidmap_sha256: str
    newuidmap_mode: int
    newuidmap_owner_uid: int
    newuidmap_file_capabilities: str
    newuidmap_owner_output: str
    newgidmap_path: str
    newgidmap_sha256: str
    newgidmap_mode: int
    newgidmap_owner_uid: int
    newgidmap_file_capabilities: str
    newgidmap_owner_output: str
    archiso_version: str
    arch_install_scripts_version: str
    pacman_version: str
    shadow_version: str
    libcap_version: str
    getcap_path: str
    getcap_sha256: str
    getcap_mode: int
    getcap_owner_uid: int
    getcap_file_capabilities: str
    getcap_owner_output: str
    arch_install_scripts_package_version: str
    util_linux_version: str
    bash_version: str
    trusted_tools: tuple[RootlessToolEvidence, ...]


Runner = Callable[..., subprocess.CompletedProcess[str]]


def repository_root() -> Path:
    return REPOSITORY_ROOT


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _assert_no_link_components(path: Path, *, include_leaf: bool = True) -> None:
    absolute = Path(os.path.abspath(path))
    components = [absolute.anchor]
    current = Path(absolute.anchor)
    parts = absolute.parts[1:]
    if not include_leaf:
        parts = parts[:-1]
    for part in parts:
        current = current / part
        if _is_reparse_or_symlink(current):
            raise BuildContractError(f"path contains a symlink or junction component: {current}")


def _canonical_existing_dir(raw: Path, label: str, *, reject_root: bool = True) -> Path:
    if not raw.is_absolute():
        raise BuildContractError(f"{label} must be an absolute path: {raw}")
    _assert_no_link_components(raw)
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise BuildContractError(f"{label} must exist: {raw}") from exc
    if not resolved.is_dir():
        raise BuildContractError(f"{label} must be an existing directory: {resolved}")
    if reject_root and resolved == Path(resolved.anchor):
        raise BuildContractError(f"{label} cannot be a filesystem root")
    return resolved


def _canonical_existing_file(raw: Path, label: str) -> Path:
    if not raw.is_absolute():
        raise BuildContractError(f"{label} must be an absolute path: {raw}")
    _assert_no_link_components(raw)
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise BuildContractError(f"{label} must exist: {raw}") from exc
    if not resolved.is_file():
        raise BuildContractError(f"{label} must be a regular file: {resolved}")
    return resolved


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_proc_status_capabilities(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if not separator or key not in {"CapEff", "CapPrm", "CapBnd", "CapAmb", "NoNewPrivs"}:
            continue
        if key in values:
            raise BuildContractError(f"rootless host preflight found duplicate {key} process status")
        raw = value.strip()
        try:
            values[key] = int(raw, 10 if key == "NoNewPrivs" else 16)
        except ValueError as exc:
            raise BuildContractError(f"rootless host preflight found malformed {key} process status") from exc
    required = {"CapEff", "CapPrm", "CapBnd", "CapAmb", "NoNewPrivs"}
    if set(values) != required:
        raise BuildContractError("rootless host preflight is missing required capability/NoNewPrivs status")
    return values


def _parse_subid_ranges(text: str, *, username: str, numeric_id: int, label: str) -> tuple[int, int]:
    """Select one safe, sufficiently large subordinate ID range for a caller."""
    rows: list[tuple[str, int, int]] = []
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        pieces = line.split(":")
        if len(pieces) != 3 or not pieces[0]:
            raise BuildContractError(f"rootless {label} file has a malformed row at line {line_number}")
        try:
            start, count = int(pieces[1], 10), int(pieces[2], 10)
        except ValueError as exc:
            raise BuildContractError(f"rootless {label} file has a nonnumeric row at line {line_number}") from exc
        if start < ROOTLESS_SUBID_MINIMUM or count <= 0 or start + count > 2**32:
            raise BuildContractError(f"rootless {label} range at line {line_number} is outside the safe ID space")
        rows.append((pieces[0], start, count))
    ordered = sorted(rows, key=lambda row: row[1])
    for previous, current in zip(ordered, ordered[1:]):
        if previous[1] + previous[2] > current[1]:
            raise BuildContractError(f"rootless {label} ranges overlap")
    matching = [
        (start, count)
        for owner, start, count in rows
        if owner in {username, str(numeric_id)}
    ]
    if len(matching) != 1:
        raise BuildContractError(f"rootless {label} must contain exactly one range for the invoking user")
    start, count = matching[0]
    if count < ROOTLESS_SUBID_MINIMUM:
        raise BuildContractError(
            f"rootless {label} range must contain at least {ROOTLESS_SUBID_MINIMUM} IDs"
        )
    return start, count


def _validate_rootless_host_state(
    *,
    username: str,
    uid: int,
    euid: int,
    gid: int,
    egid: int,
    primary_group_name: str,
    supplementary_gids: Sequence[int],
    status_text: str,
    subuid_text: str,
    subgid_text: str,
    helpers: Mapping[str, Mapping[str, Any]],
    package_versions: Mapping[str, str],
) -> RootlessHostEvidence:
    """Validate identity, capability and pinned-tool evidence without side effects."""
    if uid == 0 or euid == 0:
        raise BuildContractError("ROOTLESS_ARCHISO_REQUIRED: build commands must run as a non-root account")
    if uid != euid or gid != egid or not username:
        raise BuildContractError("ROOTLESS_ARCHISO_IDENTITY_INVALID: real/effective caller identity must match")
    if primary_group_name != username or any(group_id != gid for group_id in supplementary_gids):
        raise BuildContractError(
            "ROOTLESS_ARCHISO_IDENTITY_INVALID: build account must use a private primary group and no host device groups"
        )
    caps = _parse_proc_status_capabilities(status_text)
    if caps["CapEff"] != 0 or caps["CapPrm"] != 0 or caps["CapAmb"] != 0:
        raise BuildContractError("ROOTLESS_ARCHISO_CAPABILITIES_INVALID: effective/permitted/ambient caps must be empty")
    if caps["CapBnd"] != ROOTLESS_CAP_BND_EXPECTED:
        raise BuildContractError(
            "ROOTLESS_ARCHISO_CAPABILITIES_INVALID: CapBnd must contain exactly SETUID and SETGID"
        )
    if caps["NoNewPrivs"] != 0:
        raise BuildContractError("ROOTLESS_ARCHISO_CAPABILITIES_INVALID: NoNewPrivs must remain clear for -N helpers")
    subuid_start, subuid_count = _parse_subid_ranges(
        subuid_text, username=username, numeric_id=uid, label="subuid"
    )
    subgid_start, subgid_count = _parse_subid_ranges(
        subgid_text, username=username, numeric_id=uid, label="subgid"
    )
    if package_versions.get("archiso") != EXPECTED_MKARCHISO_VERSION:
        raise BuildContractError("ROOTLESS_ARCHISO_TOOLCHAIN_INVALID: host archiso must be pinned to 91-1")
    if package_versions.get("arch-install-scripts") != EXPECTED_ARCH_INSTALL_SCRIPTS_VERSION:
        raise BuildContractError("ROOTLESS_ARCHISO_TOOLCHAIN_INVALID: arch-install-scripts must be pinned to 31-2")
    if package_versions.get("pacman") != ROOTLESS_PACMAN_VERSION:
        raise BuildContractError("ROOTLESS_ARCHISO_TOOLCHAIN_INVALID: pacman verifier must be pinned to the snapshot")
    if package_versions.get("util-linux") != ROOTLESS_UTIL_LINUX_VERSION:
        raise BuildContractError("ROOTLESS_ARCHISO_TOOLCHAIN_INVALID: util-linux launcher package is not pinned")
    if package_versions.get("bash") != ROOTLESS_BASH_VERSION:
        raise BuildContractError("ROOTLESS_ARCHISO_TOOLCHAIN_INVALID: bash interpreter package is not pinned")
    shadow_version = package_versions.get("shadow")
    if shadow_version != ROOTLESS_SHADOW_VERSION:
        raise BuildContractError("ROOTLESS_ARCHISO_TOOLCHAIN_INVALID: host shadow helper package is not the pinned version")
    libcap_version = package_versions.get("libcap")
    if libcap_version != ROOTLESS_LIBCAP_VERSION:
        raise BuildContractError("ROOTLESS_ARCHISO_TOOLCHAIN_INVALID: libcap getcap tool package is not pinned")
    if set(helpers) != set(ROOTLESS_TOOL_CONTRACT):
        raise BuildContractError("ROOTLESS_ARCHISO_HELPER_INVALID: trusted tool inventory is incomplete or has extra entries")
    typed_tools: list[RootlessToolEvidence] = []
    for name, contract in ROOTLESS_TOOL_CONTRACT.items():
        value = helpers.get(name)
        if not isinstance(value, Mapping):
            raise BuildContractError(f"ROOTLESS_ARCHISO_HELPER_INVALID: {name} evidence is missing")
        path = contract["path"]
        package = contract["package"]
        expected_owner = f"{path} is owned by {package} {package_versions[package]}"
        if (
            value.get("path") != path
            or value.get("sha256") != contract["sha256"]
            or value.get("mode") != contract["mode"]
            or value.get("owner_uid") != 0
            or value.get("file_capabilities") != contract["file_capabilities"]
            or value.get("owner_output") != expected_owner
        ):
            raise BuildContractError(
                f"ROOTLESS_ARCHISO_HELPER_INVALID: {name} differs from its exact pinned path, bytes, mode, owner, or capabilities"
            )
        typed_tools.append(
            RootlessToolEvidence(
                name=name,
                path=path,
                sha256=str(value["sha256"]),
                mode=int(value["mode"]),
                owner_uid=int(value["owner_uid"]),
                owner_output=str(value["owner_output"]),
                file_capabilities=str(value["file_capabilities"]),
            )
        )
    helper_values = {tool.name: tool for tool in typed_tools}
    getcap = helper_values["getcap"]
    newuidmap = helper_values["newuidmap"]
    newgidmap = helper_values["newgidmap"]
    return RootlessHostEvidence(
        username=username,
        uid=uid,
        gid=gid,
        primary_group_name=primary_group_name,
        supplementary_gids=tuple(sorted(set(supplementary_gids))),
        subuid_start=subuid_start,
        subuid_count=subuid_count,
        subgid_start=subgid_start,
        subgid_count=subgid_count,
        cap_eff=f"{caps['CapEff']:x}",
        cap_prm=f"{caps['CapPrm']:x}",
        cap_bnd=f"{caps['CapBnd']:x}",
        cap_amb=f"{caps['CapAmb']:x}",
        no_new_privs=caps["NoNewPrivs"],
        newuidmap_path=newuidmap.path,
        newuidmap_sha256=newuidmap.sha256,
        newuidmap_mode=newuidmap.mode,
        newuidmap_owner_uid=newuidmap.owner_uid,
        newuidmap_file_capabilities=newuidmap.file_capabilities,
        newuidmap_owner_output=newuidmap.owner_output,
        newgidmap_path=newgidmap.path,
        newgidmap_sha256=newgidmap.sha256,
        newgidmap_mode=newgidmap.mode,
        newgidmap_owner_uid=newgidmap.owner_uid,
        newgidmap_file_capabilities=newgidmap.file_capabilities,
        newgidmap_owner_output=newgidmap.owner_output,
        archiso_version=package_versions["archiso"],
        arch_install_scripts_version=package_versions["arch-install-scripts"],
        pacman_version=package_versions["pacman"],
        shadow_version=shadow_version,
        libcap_version=libcap_version,
        getcap_path=getcap.path,
        getcap_sha256=getcap.sha256,
        getcap_mode=getcap.mode,
        getcap_owner_uid=getcap.owner_uid,
        getcap_file_capabilities=getcap.file_capabilities,
        getcap_owner_output=getcap.owner_output,
        arch_install_scripts_package_version=package_versions["arch-install-scripts"],
        util_linux_version=package_versions["util-linux"],
        bash_version=package_versions["bash"],
        trusted_tools=tuple(typed_tools),
    )


def _read_rootless_policy_file(path: Path, label: str) -> str:
    _assert_no_link_components(path)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
            raise BuildContractError(f"ROOTLESS_ARCHISO_POLICY_INVALID: {label} must be a root-owned, non-writable regular file")
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BuildContractError(f"ROOTLESS_ARCHISO_POLICY_INVALID: cannot read {label}") from exc


def _rootless_archiso_host_preflight(*, runner: Runner = subprocess.run) -> RootlessHostEvidence:
    """Read-only gate for Archiso's non-root ``-N`` build contract."""
    if sys.platform != "linux":
        raise BuildContractError("ROOTLESS_ARCHISO_REQUIRED: rootless Archiso execution is Linux-only")
    uid, euid = os.getuid(), os.geteuid()
    if uid == 0 or euid == 0:
        raise BuildContractError("ROOTLESS_ARCHISO_REQUIRED: build commands must run as a non-root account")
    gid, egid = os.getgid(), os.getegid()
    try:
        import pwd
        import grp

        username = pwd.getpwuid(uid).pw_name
        primary_group_name = grp.getgrgid(gid).gr_name
        supplementary_gids = os.getgroups()
    except (ImportError, KeyError, OSError) as exc:
        raise BuildContractError("ROOTLESS_ARCHISO_IDENTITY_INVALID: invoking UID has no local account") from exc
    try:
        status_text = Path("/proc/self/status").read_text(encoding="ascii")
    except OSError as exc:
        raise BuildContractError("ROOTLESS_ARCHISO_CAPABILITIES_INVALID: cannot read /proc/self/status") from exc
    subuid_text = _read_rootless_policy_file(Path("/etc/subuid"), "/etc/subuid")
    subgid_text = _read_rootless_policy_file(Path("/etc/subgid"), "/etc/subgid")

    # pacman is the authority used below to report installed versions and file
    # ownership, so authenticate its exact executable before invoking it.
    pacman_contract = ROOTLESS_TOOL_CONTRACT["pacman"]
    pacman_path = Path(str(pacman_contract["path"]))
    _assert_no_link_components(pacman_path)
    try:
        pacman_info = pacman_path.lstat()
    except OSError as exc:
        raise BuildContractError("ROOTLESS_ARCHISO_HELPER_INVALID: pacman is missing") from exc
    if (
        not stat.S_ISREG(pacman_info.st_mode)
        or pacman_info.st_uid != 0
        or stat.S_IMODE(pacman_info.st_mode) != pacman_contract["mode"]
        or _sha256_file(pacman_path) != pacman_contract["sha256"]
    ):
        raise BuildContractError("ROOTLESS_ARCHISO_HELPER_INVALID: pacman differs from the exact snapshot pin")
    package_result = _run_command(
        (str(pacman_path), "-Q", *ROOTLESS_HOST_PACKAGES), runner=runner
    )
    inventory = _parse_package_inventory(
        package_result.stdout,
        label="rootless builder package inventory",
        required=ROOTLESS_HOST_PACKAGES,
    )
    versions = {row.split(" ", 1)[0]: row.split(" ", 1)[1] for row in inventory}
    helpers: dict[str, dict[str, Any]] = {}
    # Verify getcap's identity before executing it; then verify the exact
    # capability xattr output for every pinned launcher/helper, including that
    # getcap itself has no file capabilities.
    tool_order = ("pacman", "getcap", *(name for name in ROOTLESS_TOOL_CONTRACT if name not in {"pacman", "getcap"}))
    for name in tool_order:
        contract = ROOTLESS_TOOL_CONTRACT[name]
        path = Path(str(contract["path"]))
        _assert_no_link_components(path)
        try:
            info = path.lstat()
        except OSError as exc:
            raise BuildContractError(f"ROOTLESS_ARCHISO_HELPER_INVALID: {name} is missing") from exc
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0:
            raise BuildContractError(f"ROOTLESS_ARCHISO_HELPER_INVALID: {name} must be a root-owned regular file")
        helper_mode = stat.S_IMODE(info.st_mode)
        helper_sha = _sha256_file(path)
        if helper_mode != contract["mode"] or helper_sha != contract["sha256"]:
            raise BuildContractError(f"ROOTLESS_ARCHISO_HELPER_INVALID: {name} mode/hash differs from the snapshot pin")
        owner_output = _run_command((str(pacman_path), "-Qo", str(path)), runner=runner).stdout.strip()
        owner_package = str(contract["package"])
        expected_owner = f"{path} is owned by {owner_package} {versions[owner_package]}"
        if owner_output != expected_owner:
            raise BuildContractError(f"ROOTLESS_ARCHISO_HELPER_INVALID: pacman ownership does not bind exact {name} path/version")
        capability_result = _run_command(("/usr/bin/getcap", str(path)), runner=runner).stdout.strip()
        expected_capability = (
            f"{path} {contract['file_capabilities']}" if contract["file_capabilities"] else ""
        )
        if capability_result != expected_capability:
            raise BuildContractError(
                f"ROOTLESS_ARCHISO_HELPER_INVALID: {name} file capabilities differ from the exact snapshot contract"
            )
        helpers[name] = {
            "path": str(path),
            "sha256": helper_sha,
            "mode": helper_mode,
            "owner_uid": info.st_uid,
            "file_capabilities": str(contract["file_capabilities"]),
            "owner_output": owner_output,
        }
    return _validate_rootless_host_state(
        username=username,
        uid=uid,
        euid=euid,
        gid=gid,
        egid=egid,
        primary_group_name=primary_group_name,
        supplementary_gids=supplementary_gids,
        status_text=status_text,
        subuid_text=subuid_text,
        subgid_text=subgid_text,
        helpers=helpers,
        package_versions=versions,
    )


def _rootless_archiso_mount_preflight(
    host: RootlessHostEvidence,
    *,
    runner: Runner = subprocess.run,
    host_mount_table_reader: Callable[[], bytes] | None = None,
) -> dict[str, Any]:
    """Probe the pinned arch-chroot -N mounts before creating build work.

    The probe uses the same one-level UID/GID map-auto boundary as the pinned
    non-root Archiso path. It is deliberately separate from the legacy
    devtmpfs-oriented probe, which describes root-mode arch-chroot instead.
    Production online execute_build runs this check before creating work or
    output, then launches the build as the same nonroot caller with online
    egress. Offline execute_build remains unavailable until a reviewed
    no-egress launcher is compatible with the pinned -N path. Signed package
    installation, full mkarchiso execution, target-root validation, and
    BIOS/UEFI boots remain separate gates.
    """
    if sys.platform != "linux":
        raise BuildContractError("ARCHISO_MOUNT_CAPABILITY_UNAVAILABLE: rootless -N probe is Linux-only")
    if host.uid == 0 or os.geteuid() == 0:
        raise BuildContractError("ROOTLESS_ARCHISO_REQUIRED: rootless -N probe requires the validated build account")
    unshare_evidence = next((item for item in host.trusted_tools if item.name == "unshare"), None)
    if unshare_evidence is None:
        raise BuildContractError("ROOTLESS_ARCHISO_HELPER_INVALID: exact unshare evidence is missing")
    unshare_path = Path(unshare_evidence.path)
    _assert_no_link_components(unshare_path)
    if not unshare_path.is_file() or _sha256_file(unshare_path) != unshare_evidence.sha256:
        raise BuildContractError("ROOTLESS_ARCHISO_HELPER_INVALID: unshare changed after host preflight")

    ip_candidate = shutil.which("ip")
    if not ip_candidate:
        raise BuildContractError("ROOTLESS_ARCHISO_HELPER_INVALID: iproute2 is required for namespace route checks")
    ip_path = _canonical_existing_file(Path(ip_candidate).resolve(strict=True), "iproute2 executable")
    if not (ip_path.stat().st_mode & 0o111):
        raise BuildContractError("ROOTLESS_ARCHISO_HELPER_INVALID: iproute2 executable is not executable")
    ip_hash = _sha256_file(ip_path)
    pacman_path = Path(str(ROOTLESS_TOOL_CONTRACT["pacman"]["path"]))
    ip_owner = _run_command((str(pacman_path), "-Qo", str(ip_path)), runner=runner).stdout.strip()
    ip_owner_match = re.fullmatch(re.escape(str(ip_path)) + r" is owned by iproute2 (\S+)", ip_owner)
    if ip_owner_match is None:
        raise BuildContractError("ROOTLESS_ARCHISO_HELPER_INVALID: iproute2 ownership does not bind the exact path")
    ip_inventory = _parse_package_inventory(
        _run_command((str(pacman_path), "-Q", "iproute2"), runner=runner).stdout,
        label="rootless iproute2 inventory",
        required=("iproute2",),
    )
    if ip_inventory != (f"iproute2 {ip_owner_match.group(1)}",):
        raise BuildContractError("ROOTLESS_ARCHISO_HELPER_INVALID: iproute2 owner and installed version disagree")

    try:
        host_ids = _current_namespace_identities()
        if host_ids["net"] == "":
            raise ValueError("empty network namespace identity")
    except (OSError, RuntimeError, ValueError) as exc:
        raise BuildContractError("ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: cannot capture host namespaces") from exc
    if host_mount_table_reader is None:
        host_mount_table_reader = _host_mount_table_bytes if runner is subprocess.run else lambda: b"synthetic-host-mountinfo"
    try:
        host_mount_before = host_mount_table_reader()
    except (OSError, BuildContractError) as exc:
        raise BuildContractError("ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: cannot snapshot host mount table") from exc
    if not isinstance(host_mount_before, bytes) or not host_mount_before:
        raise BuildContractError("ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: host mount snapshot is malformed")

    expected_uid_map = [[0, host.uid, 1], [1, host.subuid_start, host.subuid_count]]
    expected_gid_map = [[0, host.gid, 1], [1, host.subgid_start, host.subgid_count]]
    try:
        parent_source_observations = rootless_mount_probe.collect_parent_source_observations(
            uid_map=expected_uid_map,
            gid_map=expected_gid_map,
        )
    except (OSError, ValueError, rootless_mount_probe.RootlessMountProbeIntegrityError) as exc:
        raise BuildContractError(
            "ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: cannot capture trusted parent /dev and /sys source identities"
        ) from exc

    def recheck_parent_sources() -> bool:
        try:
            current = rootless_mount_probe.collect_parent_source_observations(
                uid_map=expected_uid_map,
                gid_map=expected_gid_map,
            )
        except (OSError, ValueError, rootless_mount_probe.RootlessMountProbeIntegrityError) as exc:
            raise BuildContractError(
                "ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: parent /dev or /sys source identity cannot be rechecked"
            ) from exc
        return current == parent_source_observations

    # This is the pinned arch-chroot -N map-auto mapping in one user namespace,
    # with a fresh route-free net namespace and private mount/PID namespaces.
    # Do not wrap this in an outer user namespace: the helper mapping must be a
    # single level, as demonstrated by the disposable-VM evidence.
    namespace_flags = (
        "--net",
        "--fork",
        "--pid",
        "--mount",
        "--map-auto",
        "--map-root-user",
        "--setuid",
        "0",
        "--setgid",
        "0",
        "--propagation",
        "private",
    )
    command = (
        str(unshare_path),
        *namespace_flags,
        "--",
        sys.executable,
        "-m",
        "usb_builder.rootless_mount_probe",
        json.dumps(host_ids, sort_keys=True, separators=(",", ":")),
        host_ids["net"],
        str(ip_path),
        json.dumps(parent_source_observations, sort_keys=True, separators=(",", ":")),
    )
    try:
        result = _run_bounded_mount_probe(command, runner=runner)
    except (OSError, subprocess.TimeoutExpired, BuildContractError) as exc:
        try:
            host_mount_after = host_mount_table_reader()
            source_unchanged = recheck_parent_sources()
        except (OSError, BuildContractError) as snapshot_exc:
            raise BuildContractError(
                "ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: failed probe and host/source identities cannot be rechecked"
            ) from snapshot_exc
        if host_mount_after != host_mount_before:
            raise BuildContractError(
                "ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: host mount table changed during failed rootless probe"
            ) from exc
        if not source_unchanged:
            raise BuildContractError(
                "ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: /dev or /sys source identity changed during failed probe"
            ) from exc
        raise BuildContractError(
            "ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: rootless -N mount probe did not complete; "
            "host mount table was rechecked"
        ) from exc
    try:
        host_mount_after = host_mount_table_reader()
        source_unchanged = recheck_parent_sources()
    except (OSError, BuildContractError) as exc:
        raise BuildContractError(
            "ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: cannot recheck host mount table or source identities"
        ) from exc
    if not isinstance(host_mount_after, bytes) or host_mount_after != host_mount_before:
        raise BuildContractError("ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: host mount table changed during rootless probe")
    if not source_unchanged:
        raise BuildContractError("ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: /dev or /sys source identity changed during probe")
    if _sha256_file(unshare_path) != unshare_evidence.sha256 or _sha256_file(ip_path) != ip_hash:
        raise BuildContractError("ROOTLESS_ARCHISO_HELPER_INVALID: a trusted probe executable changed during preflight")
    output = result.stdout if isinstance(result.stdout, str) else ""
    parsed = rootless_mount_probe.validate_probe_output(
        output,
        host_namespace_ids=host_ids,
        host_net_namespace=host_ids["net"],
        parent_source_observations=parent_source_observations,
        allow_synthetic=runner is not subprocess.run,
    )
    if parsed is None:
        raise BuildContractError("ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: rootless -N mount report is malformed")
    status, report = parsed
    expected_return = rootless_mount_probe.SUPPORTED_EXIT if status == "SUPPORTED" else rootless_mount_probe.UNSUPPORTED_EXIT
    if result.returncode != expected_return:
        raise BuildContractError("ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: rootless -N probe exit/report disagree")
    if status != "SUPPORTED":
        raise BuildContractError(
            "ARCHISO_MOUNT_CAPABILITY_UNAVAILABLE: pinned arch-chroot -N mount stack is not supported by this host"
        )
    return {
        "status": status,
        "command": list(command),
        "output": output.strip(),
        "host_namespace_ids": dict(host_ids),
        "host_mount_table_sha256": _sha256_bytes(host_mount_before),
        "unshare_path": str(unshare_path),
        "unshare_sha256": unshare_evidence.sha256,
        "iproute_path": str(ip_path),
        "iproute_sha256": ip_hash,
        "iproute_package_owner_output": ip_owner,
        "report": report,
    }


def _rootless_pacstrap_patch_resource() -> tuple[Path, bytes]:
    package_root = Path(__file__).resolve().parent
    patch_path = package_root / ROOTLESS_PACSTRAP_PATCH_RELATIVE_PATH
    _assert_no_link_components(patch_path)
    try:
        info = patch_path.lstat()
        patch_bytes = patch_path.read_bytes()
    except OSError as exc:
        raise BuildContractError("ROOTLESS_PACSTRAP_PATCH_INVALID: pinned patch resource is missing") from exc
    if not stat.S_ISREG(info.st_mode) or _sha256_bytes(patch_bytes) != ROOTLESS_PACSTRAP_PATCH_SHA256:
        raise BuildContractError("ROOTLESS_PACSTRAP_PATCH_INVALID: patch resource differs from the pinned bytes")
    if patch_bytes != ROOTLESS_PACSTRAP_PATCH_BYTES:
        raise BuildContractError("ROOTLESS_PACSTRAP_PATCH_INVALID: patch content does not match the reviewed correction")
    return patch_path, patch_bytes


def _render_rootless_pacstrap_patch(
    source_bytes: bytes,
    *,
    expected_source_sha256: str = ROOTLESS_PACSTRAP_SHA256,
    expected_patched_sha256: str = ROOTLESS_PACSTRAP_PATCHED_SHA256,
) -> bytes:
    """Apply one exact status-preserving edit to the pinned pacstrap bytes."""
    if _sha256_bytes(source_bytes) != expected_source_sha256:
        raise BuildContractError("ROOTLESS_PACSTRAP_SOURCE_INVALID: pacstrap source hash differs from its pin")
    if source_bytes.count(ROOTLESS_PACSTRAP_PATCHED_RESULT_CONTEXT) != 1:
        raise BuildContractError("ROOTLESS_PACSTRAP_SOURCE_INVALID: patch context must occur exactly once")
    patched = source_bytes.replace(
        ROOTLESS_PACSTRAP_PATCHED_RESULT_CONTEXT,
        ROOTLESS_PACSTRAP_PATCHED_RESULT_REPLACEMENT,
        1,
    )
    if _sha256_bytes(patched) != expected_patched_sha256:
        raise BuildContractError("ROOTLESS_PACSTRAP_PATCH_INVALID: patched source hash differs from its pin")
    return patched


def _read_pinned_pacstrap_source(
    path: Path | None = None,
    *,
    expected_owner_uid: int = 0,
) -> bytes:
    if path is None:
        path = Path(ROOTLESS_ARCH_INSTALL_SCRIPTS_SOURCE_PATH)
    if str(path) != ROOTLESS_ARCH_INSTALL_SCRIPTS_SOURCE_PATH:
        raise BuildContractError("ROOTLESS_PACSTRAP_SOURCE_INVALID: source path is not the pinned system executable")
    _assert_no_link_components(path)
    try:
        before = path.lstat()
        source_bytes = path.read_bytes()
        after = path.lstat()
    except OSError as exc:
        raise BuildContractError("ROOTLESS_PACSTRAP_SOURCE_INVALID: pinned pacstrap source is unavailable") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != expected_owner_uid
        or stat.S_IMODE(before.st_mode) != 0o755
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or _sha256_bytes(source_bytes) != ROOTLESS_PACSTRAP_SHA256
    ):
        raise BuildContractError("ROOTLESS_PACSTRAP_SOURCE_INVALID: pinned pacstrap mode, owner, or bytes changed")
    return source_bytes


def _validate_pacstrap_operation_root(operation_root: Path) -> tuple[Path, int, str]:
    """Bind staging to this process's private, token-marked work directory."""
    if not operation_root.is_absolute():
        raise BuildContractError("ROOTLESS_PACSTRAP_STAGE_INVALID: operation root must be absolute")
    _assert_no_link_components(operation_root)
    try:
        root = operation_root.resolve(strict=True)
        root_info = root.lstat()
        effective_uid = getattr(os, "geteuid", None)
        if not callable(effective_uid):
            raise BuildContractError("ROOTLESS_PACSTRAP_STAGE_INVALID: effective UID is unavailable")
        owner_uid = effective_uid()
        expected_token = root.name.removeprefix(WORK_PREFIX)
        marker = root / ".owner"
        _assert_no_link_components(marker)
        marker_info = marker.lstat()
        marker_bytes = marker.read_bytes()
    except (OSError, RuntimeError, AttributeError) as exc:
        raise BuildContractError("ROOTLESS_PACSTRAP_STAGE_INVALID: owner-marked operation root is unavailable") from exc
    if (
        root == Path(root.anchor)
        or not stat.S_ISDIR(root_info.st_mode)
        or root_info.st_uid != owner_uid
        or stat.S_IMODE(root_info.st_mode) != 0o700
        or not root.name.startswith(WORK_PREFIX)
        or not expected_token
        or not stat.S_ISREG(marker_info.st_mode)
        or marker_info.st_uid != owner_uid
        or stat.S_IMODE(marker_info.st_mode) != 0o600
        or marker_bytes != expected_token.encode("ascii")
    ):
        raise BuildContractError(
            "ROOTLESS_PACSTRAP_STAGE_INVALID: operation root must be a private owned directory with its matching token"
        )
    return root, owner_uid, _sha256_bytes(marker_bytes)


def _stage_rootless_pacstrap_overlay(
    operation_root: Path,
    *,
    expected_source_owner_uid: int = 0,
) -> RootlessPacstrapOverlayEvidence:
    """Create an exclusive patched pacstrap first in the future mkarchiso PATH.

    The returned PATH is used only for the mkarchiso subprocess. The system
    pacstrap remains untouched; all staged bytes are operation-owned and
    revalidated around the build and before publication.
    """
    root, operation_owner_uid, owner_marker_sha256 = _validate_pacstrap_operation_root(operation_root)
    source_path = Path(ROOTLESS_ARCH_INSTALL_SCRIPTS_SOURCE_PATH)
    source_bytes = _read_pinned_pacstrap_source(source_path, expected_owner_uid=expected_source_owner_uid)
    patch_path, patch_bytes = _rootless_pacstrap_patch_resource()
    # Confirm the installed patch resource is the one this exact algorithm
    # represents before staging any executable.
    if _sha256_bytes(patch_bytes) != ROOTLESS_PACSTRAP_PATCH_SHA256:
        raise BuildContractError("ROOTLESS_PACSTRAP_PATCH_INVALID: patch hash changed before staging")
    patched = _render_rootless_pacstrap_patch(
        source_bytes,
        expected_source_sha256=ROOTLESS_PACSTRAP_SHA256,
        expected_patched_sha256=ROOTLESS_PACSTRAP_PATCHED_SHA256,
    )
    tools_dir = root / "rootless-tools"
    try:
        tools_dir.mkdir(mode=0o700)
    except OSError as exc:
        raise BuildContractError("ROOTLESS_PACSTRAP_STAGE_INVALID: operation tool directory is not new") from exc
    _assert_no_link_components(tools_dir)
    staged = tools_dir / "pacstrap"
    try:
        descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(patched)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(staged, 0o755)
    except OSError as exc:
        raise BuildContractError("ROOTLESS_PACSTRAP_STAGE_INVALID: cannot create patched executable") from exc
    _verify_rootless_pacstrap_overlay(
        RootlessPacstrapOverlayEvidence(
            patch_id=ROOTLESS_PACSTRAP_PATCH_ID,
            operation_root=str(root),
            operation_owner_uid=operation_owner_uid,
            operation_owner_marker_sha256=owner_marker_sha256,
            source_path=ROOTLESS_ARCH_INSTALL_SCRIPTS_SOURCE_PATH,
            source_sha256=ROOTLESS_PACSTRAP_SHA256,
            source_owner_uid=expected_source_owner_uid,
            patch_resource_path="usb_builder/" + ROOTLESS_PACSTRAP_PATCH_RELATIVE_PATH,
            patch_sha256=_sha256_bytes(patch_bytes),
            staged_path=str(staged),
            staged_sha256=ROOTLESS_PACSTRAP_PATCHED_SHA256,
            staged_mode=0o755,
            child_path=f"{tools_dir}:/usr/bin:/usr/sbin",
        )
    )
    # Re-read the system source after staging to detect replacement between
    # initial validation and creation of the operation-owned executable.
    _read_pinned_pacstrap_source(source_path, expected_owner_uid=expected_source_owner_uid)
    return RootlessPacstrapOverlayEvidence(
        patch_id=ROOTLESS_PACSTRAP_PATCH_ID,
        operation_root=str(root),
        operation_owner_uid=operation_owner_uid,
        operation_owner_marker_sha256=owner_marker_sha256,
        source_path=ROOTLESS_ARCH_INSTALL_SCRIPTS_SOURCE_PATH,
        source_sha256=ROOTLESS_PACSTRAP_SHA256,
        source_owner_uid=expected_source_owner_uid,
        patch_resource_path="usb_builder/" + ROOTLESS_PACSTRAP_PATCH_RELATIVE_PATH,
        patch_sha256=_sha256_bytes(patch_bytes),
        staged_path=str(staged),
        staged_sha256=ROOTLESS_PACSTRAP_PATCHED_SHA256,
        staged_mode=0o755,
        child_path=f"{tools_dir}:/usr/bin:/usr/sbin",
    )


def _verify_rootless_pacstrap_overlay(evidence: RootlessPacstrapOverlayEvidence) -> None:
    _read_pinned_pacstrap_source(
        Path(evidence.source_path),
        expected_owner_uid=evidence.source_owner_uid,
    )
    patch_path, patch_bytes = _rootless_pacstrap_patch_resource()
    if (
        evidence.patch_id != ROOTLESS_PACSTRAP_PATCH_ID
        or evidence.source_path != ROOTLESS_ARCH_INSTALL_SCRIPTS_SOURCE_PATH
        or evidence.source_sha256 != ROOTLESS_PACSTRAP_SHA256
        or evidence.patch_resource_path != "usb_builder/" + ROOTLESS_PACSTRAP_PATCH_RELATIVE_PATH
        or evidence.patch_sha256 != ROOTLESS_PACSTRAP_PATCH_SHA256
        or evidence.staged_sha256 != ROOTLESS_PACSTRAP_PATCHED_SHA256
        or _sha256_bytes(patch_bytes) != evidence.patch_sha256
        or str(patch_path.relative_to(Path(__file__).resolve().parents[1]).as_posix())
        != evidence.patch_resource_path
    ):
        raise BuildContractError("ROOTLESS_PACSTRAP_OVERLAY_INVALID: source or patch identity changed")
    staged = Path(evidence.staged_path)
    _assert_no_link_components(staged)
    tools_dir = staged.parent
    root, owner_uid, owner_marker_sha256 = _validate_pacstrap_operation_root(Path(evidence.operation_root))
    try:
        tools_info = tools_dir.lstat()
        info = staged.lstat()
        entries = tuple(path.name for path in tools_dir.iterdir())
    except OSError as exc:
        raise BuildContractError("ROOTLESS_PACSTRAP_OVERLAY_INVALID: staged executable is missing") from exc
    expected_tools_dir = root / "rootless-tools"
    if (
        str(root) != evidence.operation_root
        or owner_uid != evidence.operation_owner_uid
        or owner_marker_sha256 != evidence.operation_owner_marker_sha256
        or tools_dir.resolve(strict=True) != expected_tools_dir
        or not stat.S_ISDIR(tools_info.st_mode)
        or tools_info.st_uid != owner_uid
        or stat.S_IMODE(tools_info.st_mode) != 0o700
        or entries != ("pacstrap",)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != owner_uid
        or stat.S_IMODE(info.st_mode) != 0o755
        or _sha256_file(staged) != ROOTLESS_PACSTRAP_PATCHED_SHA256
        or evidence.child_path != f"{tools_dir}:/usr/bin:/usr/sbin"
    ):
        raise BuildContractError("ROOTLESS_PACSTRAP_OVERLAY_INVALID: staged file or child PATH differs from the pin")


def _fsync_directory(path: Path) -> None:
    """Make staged publication entries durable on supported POSIX build hosts."""
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise BuildContractError(f"cannot durably publish output bundle directory {path}: {exc}") from exc


def _copy_publication_file(source: Path, destination: Path, expected_sha256: str) -> str:
    if _is_reparse_or_symlink(source) or not source.is_file():
        raise BuildContractError(f"publication source is not a regular file: {source}")
    if destination.exists() or _is_reparse_or_symlink(destination):
        raise BuildContractError(f"publication destination already exists or is linked: {destination}")
    if _sha256_file(source) != expected_sha256:
        raise BuildContractError(f"publication source changed after provenance hashing: {source.name}")
    with source.open("rb") as source_stream, destination.open("xb") as output_stream:
        shutil.copyfileobj(source_stream, output_stream)
        output_stream.flush()
        os.fsync(output_stream.fileno())
    copied_sha256 = _sha256_file(destination)
    if copied_sha256 != expected_sha256:
        raise BuildContractError(f"published file hash does not match source evidence: {source.name}")
    return copied_sha256


def _write_publication_file(destination: Path, content: bytes) -> str:
    if destination.exists() or _is_reparse_or_symlink(destination):
        raise BuildContractError(f"publication destination already exists or is linked: {destination}")
    with destination.open("xb") as output_stream:
        output_stream.write(content)
        output_stream.flush()
        os.fsync(output_stream.fileno())
    return _sha256_file(destination)


def _publish_candidate_bundle(
    output: Path,
    *,
    artifact_paths: Sequence[Path],
    selected_iso: Path,
    build_log: Path,
    wheel_log: Path,
    provenance: Mapping[str, Any],
    expected_artifact_sha256: Mapping[str, str],
    expected_build_log_sha256: str,
    expected_wheel_log_sha256: str,
) -> BuildResult:
    """Publish one complete candidate bundle with a single atomic directory rename."""
    if any(output.iterdir()):
        raise BuildContractError("output directory changed before candidate bundle publication")
    bundle_name = "candidate-bundle"
    bundle_path = output / bundle_name
    stage_path = output / (".candidate-stage-" + uuid.uuid4().hex)
    if bundle_path.exists() or _is_reparse_or_symlink(bundle_path) or stage_path.exists():
        raise BuildContractError("candidate output bundle path already exists")
    if selected_iso not in artifact_paths:
        raise BuildContractError("selected ISO is not present in the Archiso output inventory")
    reserved_names = {"build.log", "wheel-build.log", "build-provenance.json", "SHA256SUMS"}
    names: set[str] = set()
    for path in artifact_paths:
        name = path.name
        if not name or name in reserved_names or Path(name).name != name or name in names:
            raise BuildContractError(f"Archiso artifact name cannot be published safely: {name!r}")
        names.add(name)
    stage_path.mkdir(mode=0o700)
    published = False
    try:
        listed_hashes: dict[str, str] = {}
        for artifact in sorted(artifact_paths, key=lambda item: item.name):
            expected_hash = expected_artifact_sha256.get(artifact.name)
            if expected_hash is None:
                raise BuildContractError(f"Archiso artifact has no provenance hash: {artifact.name}")
            # Keep the selected ISO extension hidden until the entire bundle is ready.
            target_name = "." + artifact.name + ".pending" if artifact == selected_iso else artifact.name
            target_path = stage_path / target_name
            digest = _copy_publication_file(artifact, target_path, expected_hash)
            listed_hashes[artifact.name] = digest
        build_copy = stage_path / "build.log"
        wheel_copy = stage_path / "wheel-build.log"
        listed_hashes[build_copy.name] = _copy_publication_file(build_log, build_copy, expected_build_log_sha256)
        listed_hashes[wheel_copy.name] = _copy_publication_file(wheel_log, wheel_copy, expected_wheel_log_sha256)
        manifest_path = stage_path / "build-provenance.json"
        manifest_bytes = (json.dumps(provenance, indent=2, sort_keys=True) + "\n").encode("utf-8")
        manifest_sha256 = _write_publication_file(manifest_path, manifest_bytes)
        listed_hashes[manifest_path.name] = manifest_sha256
        sums_content = "".join(f"{listed_hashes[name]}  {name}\n" for name in sorted(listed_hashes))
        sums_path = stage_path / "SHA256SUMS"
        _write_publication_file(sums_path, sums_content.encode("ascii"))
        for name, digest in listed_hashes.items():
            staged_name = "." + name + ".pending" if name == selected_iso.name else name
            if _is_reparse_or_symlink(stage_path / staged_name) or _sha256_file(stage_path / staged_name) != digest:
                raise BuildContractError(f"candidate output changed before bundle publication: {name}")
        pending_iso = stage_path / ("." + selected_iso.name + ".pending")
        if not pending_iso.is_file():
            raise BuildContractError("candidate bundle has no staged ISO payload")
        if _is_reparse_or_symlink(pending_iso) or _sha256_file(pending_iso) != listed_hashes[selected_iso.name]:
            raise BuildContractError("staged ISO changed before bundle publication")
        # Rename inside the still-hidden directory; the final directory rename
        # exposes the ISO, logs, provenance, and checksum list together.
        os.replace(pending_iso, stage_path / selected_iso.name)
        _fsync_directory(stage_path)
        os.replace(stage_path, bundle_path)
        published = True
        # The directory rename is the one publication point. The complete
        # bundle was fsynced before it became visible; no fallible work follows.
        return BuildResult(
            bundle_path / selected_iso.name,
            bundle_path / "build-provenance.json",
            listed_hashes[selected_iso.name],
            manifest_sha256,
        )
    except Exception:
        if published and bundle_path.exists():
            shutil.rmtree(bundle_path)
        if stage_path.exists():
            shutil.rmtree(stage_path)
        raise


def _load_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildContractError(f"{label} is not readable JSON: {path}") from exc
    if not isinstance(value, dict):
        raise BuildContractError(f"{label} must contain a JSON object")
    return value, raw


def _read_package_names(path: Path) -> tuple[str, ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise BuildContractError(f"package list is unreadable: {path}") from exc
    names = tuple(line.split("#", 1)[0].strip() for line in lines)
    names = tuple(name for name in names if name)
    if len(set(names)) != len(names):
        raise BuildContractError("package list contains duplicates")
    missing = REQUIRED_PACKAGES.difference(names)
    if missing:
        raise BuildContractError(f"package list is missing required closed runtime/boot packages: {sorted(missing)}")
    return names


def _validate_package_list_pin(path: Path, snapshot: Mapping[str, Any]) -> None:
    expected = snapshot.get("packages_x86_64_sha256")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise BuildContractError("snapshot lock has no exact package-list SHA-256")
    try:
        package_bytes = path.read_bytes()
    except OSError as exc:
        raise BuildContractError(f"package list is unreadable: {path}") from exc
    # Git's Windows checkout may translate the checked-in LF file to CRLF.
    # Pin the canonical LF representation so identical package names remain
    # valid across hosts while any content/order change still fails closed.
    canonical_bytes = package_bytes.replace(b"\r\n", b"\n")
    if b"\r" in canonical_bytes:
        raise BuildContractError("profile package list has unsupported bare-CR line endings")
    if hashlib.sha256(canonical_bytes).hexdigest() != expected:
        raise BuildContractError("profile package list does not match the package list pinned by the snapshot")


def _profile_boot_modes(profile_dir: Path) -> tuple[str, ...]:
    profiledef = profile_dir / "profiledef.sh"
    try:
        content = profiledef.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise BuildContractError("profiledef.sh is unreadable") from exc
    if not re.search(r"(?m)^arch=['\"]x86_64['\"]\s*$", content):
        raise BuildContractError("Archiso profile must explicitly target x86_64")
    if not re.search(r"(?m)^buildmodes=\(['\"]iso['\"]\)\s*$", content):
        raise BuildContractError("Archiso profile must explicitly request ISO build mode")
    match = re.search(r"(?ms)^bootmodes=\((.*?)\)\s*$", content)
    if match is None:
        raise BuildContractError("profiledef.sh does not declare a parseable bootmodes array")
    modes = tuple(re.findall(r"['\"]([^'\"]+)['\"]", match.group(1)))
    if not REQUIRED_BOOT_MODES.issubset(modes):
        raise BuildContractError("profile does not declare the required BIOS and x86_64 UEFI modes")
    boot_files = (
        "syslinux/syslinux.cfg",
        "syslinux/archiso_sys.cfg",
        "syslinux/archiso_sys-linux.cfg",
        "efiboot/loader/loader.conf",
        "efiboot/loader/entries/01-autoloader.conf",
        ARCHISO_INITRAMFS_CONFIG_RELATIVE_PATH,
        ARCHISO_USB_INITRAMFS_DERIVATIVE_RELATIVE_PATH,
    )
    missing = [name for name in boot_files if not (profile_dir / name).is_file()]
    if missing:
        raise BuildContractError(f"profile is missing boot configuration required by Archiso 91: {missing}")
    syslinux = (profile_dir / "syslinux" / "archiso_sys-linux.cfg").read_text(encoding="utf-8")
    uefi = (profile_dir / "efiboot" / "loader" / "entries" / "01-autoloader.conf").read_text(encoding="utf-8")
    for config_text in (syslinux, uefi):
        if "%INSTALL_DIR%" not in config_text or "%ARCH%" not in config_text or "%KERNEL_PARAMS%" not in config_text:
            raise BuildContractError("BIOS/UEFI boot entry is missing required Archiso profile placeholders")
    _validate_offline_boot_arguments(profile_dir)
    return modes


def _validate_offline_boot_arguments(profile_dir: Path) -> None:
    """Require BIOS and UEFI entries to disable initramfs IP configuration."""
    boot_lines = (
        ("syslinux/archiso_sys-linux.cfg", "APPEND"),
        ("efiboot/loader/entries/01-autoloader.conf", "options"),
    )
    for relative, directive in boot_lines:
        try:
            contents = (profile_dir / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise BuildContractError(f"offline boot configuration is unreadable: {relative}") from exc
        lines = [
            line.strip()
            for line in contents.splitlines()
            if line.strip().startswith(directive + " ")
        ]
        if len(lines) != 1:
            raise BuildContractError(
                f"{relative} must contain exactly one {directive} line with offline IP configuration"
            )
        arguments = lines[0].split()[1:]
        if (
            arguments.count("ip=off") != 1
            or sum(argument.startswith("ip=") for argument in arguments) != 1
            or arguments[-1:] != ["ip=off"]
            or "%KERNEL_PARAMS%" not in arguments[:-1]
        ):
            raise BuildContractError(
                f"{relative} must end its {directive} line with the sole ip=off argument"
            )


def _validate_service_network_policy(service_text: str, *, label: str) -> None:
    current_section = ""
    service_values: dict[str, list[str]] = {key: [] for key in AUTOLOADER_NETWORK_POLICY}
    for raw_line in service_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        section = re.fullmatch(r"\[([^]]+)\]", line)
        if section is not None:
            current_section = section.group(1)
            continue
        if current_section != "Service" or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in service_values:
            service_values[key].append(value)
    for key, expected in AUTOLOADER_NETWORK_POLICY.items():
        if service_values[key] != [expected]:
            raise BuildContractError(
                f"{label} must set exactly {key}={expected} in one [Service] section"
            )


def _validate_profile_offline_runtime(profile_dir: Path) -> None:
    """Require the pinned offline unit sandbox and exact network masks."""
    service_path = profile_dir / "airootfs" / "etc" / "systemd" / "system" / "autoloader.service"
    hook_path = profile_dir / "airootfs" / "root" / "customize_airootfs.sh"
    try:
        service_bytes = service_path.read_bytes()
        service_text = service_bytes.decode("utf-8")
        hook_text = hook_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise BuildContractError("offline runtime service or customization hook is unreadable") from exc
    _validate_service_network_policy(service_text, label="profile AutoLoader service")
    if _sha256_bytes(service_bytes) != AUTOLOADER_SERVICE_SHA256:
        raise BuildContractError("profile AutoLoader service differs from the exact reviewed unit bytes")

    array_matches = list(
        re.finditer(r"(?ms)^network_mask_units=\(\s*\n(.*?)^\)\s*$", hook_text)
    )
    if len(array_matches) != 1:
        raise BuildContractError("customize hook must declare exactly one pinned network-mask unit list")
    unit_names = tuple(
        re.findall(r'^\s*"([^"\\]+)"\s*$', array_matches[0].group(1), flags=re.MULTILINE)
    )
    if unit_names != NETWORK_DISABLED_SYSTEMD_UNITS:
        raise BuildContractError("customize hook network-mask units differ from the reviewed offline list")
    required_mask_loop = (
        'for unit in "${network_mask_units[@]}"; do\n'
        '    ensure_systemd_link "/dev/null" "$systemd_dir/$unit"\n'
        '    test -L "$systemd_dir/$unit"\n'
        '    test "$(readlink -- "$systemd_dir/$unit")" = "/dev/null"\n'
        "done"
    )
    if hook_text.count(required_mask_loop) != 1:
        raise BuildContractError("customize hook does not create and verify exact offline network masks")


def _reject_higher_priority_autoloader_unit_files(target_root: Path) -> None:
    """Reject any higher-priority complete unit that could replace the pinned unit."""
    relative_files = (
        "etc/systemd/system.control/autoloader.service",
        "run/systemd/system.control/autoloader.service",
        "run/systemd/transient/autoloader.service",
        "run/systemd/generator.early/autoloader.service",
    )
    for relative in relative_files:
        path = target_root / relative
        try:
            _assert_no_link_components(path, include_leaf=False)
        except BuildContractError as exc:
            raise BuildContractError(
                f"target root has an unsafe higher-priority AutoLoader unit path: {relative}"
            ) from exc
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise BuildContractError(
                f"target root higher-priority AutoLoader unit path cannot be inspected: {relative}"
            ) from exc
        attributes = getattr(info, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if stat.S_ISLNK(info.st_mode) or attributes & reparse_flag:
            kind = "symlink or reparse point"
        elif stat.S_ISREG(info.st_mode):
            kind = "regular file"
        elif stat.S_ISDIR(info.st_mode):
            kind = "directory"
        else:
            kind = "unexpected filesystem object"
        raise BuildContractError(
            f"target root contains an unreviewed higher-priority AutoLoader unit ({kind}): {relative}"
        )


def _reject_service_network_policy_dropins(target_root: Path) -> None:
    """Reject every unreviewed effective drop-in for the AutoLoader service."""
    unit_search_dirs = (
        "etc/systemd/system.control",
        "run/systemd/system.control",
        "run/systemd/transient",
        "run/systemd/generator.early",
        "etc/systemd/system",
        "run/systemd/system",
        "run/systemd/generator",
        "usr/local/lib/systemd/system",
        "usr/lib/systemd/system",
        "run/systemd/generator.late",
    )
    relative_dirs = tuple(
        f"{base}/{unit}.d"
        for base in unit_search_dirs
        for unit in ("autoloader.service", "service")
    )
    for relative in relative_dirs:
        directory = target_root / relative
        try:
            _assert_no_link_components(directory)
        except BuildContractError as exc:
            raise BuildContractError(f"target root has an unsafe AutoLoader service policy override path: {relative}") from exc
        if not directory.exists() and not _is_reparse_or_symlink(directory):
            continue
        if _is_reparse_or_symlink(directory) or not directory.is_dir():
            raise BuildContractError(f"target root has an unsafe AutoLoader service policy override path: {relative}")
        for path in directory.iterdir():
            try:
                _assert_no_link_components(path)
            except BuildContractError as exc:
                raise BuildContractError(f"target root has an unsafe AutoLoader service policy override: {path}") from exc
            if _is_reparse_or_symlink(path) or not path.is_file():
                raise BuildContractError(f"target root has an unsafe AutoLoader service policy override: {path}")
            if path.suffix != ".conf":
                continue
            raise BuildContractError(
                f"target root contains an unreviewed AutoLoader service drop-in: {relative}/{path.name}"
            )


def _audit_profile_line_endings(profile_dir: Path, *, label: str = "profile") -> None:
    """Reject CR bytes in the explicitly supported Archiso text inputs."""
    for relative in ARCHISO_PROFILE_TEXT_INPUTS:
        path = profile_dir / relative
        if _is_reparse_or_symlink(path):
            raise BuildContractError(f"{label} contains an unsupported link: {path}")
        if not path.exists():
            continue
        if not path.is_file():
            raise BuildContractError(f"{label} text input is not a regular file: {relative}")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise BuildContractError(f"{label} text input is unreadable: {path}") from exc
        if b"\r" in data:
            relative = path.relative_to(profile_dir).as_posix()
            raise BuildContractError(f"{label} input must use LF-only line endings: {relative}")


def _audit_profile_tree(profile_dir: Path) -> None:
    for path in profile_dir.rglob("*"):
        if _is_reparse_or_symlink(path):
            raise BuildContractError(f"profile contains an unsupported symlink/junction: {path}")
        if not (path.is_dir() or path.is_file()):
            raise BuildContractError(f"profile contains a non-regular object: {path}")
    _audit_profile_line_endings(profile_dir)
    forbidden_suffixes = (".rom", ".rom.gz", ".bin", ".hex", ".dump", ".backup", ".pem", ".key", ".p12", ".pfx", ".kdbx")
    forbidden = [
        p for p in profile_dir.rglob("*") if p.is_file() and p.name.casefold().endswith(forbidden_suffixes)
    ]
    if forbidden:
        raise BuildContractError("firmware images/blobs must never be bundled into the Live USB profile")


def _validate_online_pacman_config(profile_dir: Path, snapshot: Mapping[str, Any]) -> None:
    pacman_conf = profile_dir / "pacman.conf"
    try:
        content = pacman_conf.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise BuildContractError("profile pacman.conf is unreadable") from exc
    base = snapshot.get("repository_base")
    if not isinstance(base, str) or not re.fullmatch(r"https://archive\.archlinux\.org/repos/\d{4}/\d{2}/\d{2}", base):
        raise BuildContractError("snapshot lock repository URL is not an exact dated official archive URL")
    expected_server = f"Server = {base}/$repo/os/$arch"
    if content.count(expected_server) != 2:
        raise BuildContractError("online pacman config must point core and extra only at the locked dated snapshot")
    sections: list[str] = []
    active_section: str | None = None
    repo_directives: dict[str, list[str]] = {"core": [], "extra": []}
    option_names: set[str] = set()
    allowed_options = {
        "architecture",
        "checkspace",
        "color",
        "holdpkg",
        "localfilesiglevel",
        "paralleldownloads",
        "siglevel",
        "verbosepkglists",
    }
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        section_match = re.fullmatch(r"\[([A-Za-z0-9_-]+)\]", line)
        if section_match:
            active_section = section_match.group(1).casefold()
            sections.append(active_section)
            continue
        if active_section in repo_directives:
            repo_directives[active_section].append(line)
        elif active_section == "options":
            key = line.split("=", 1)[0].strip().casefold()
            if key not in allowed_options:
                raise BuildContractError("online pacman config contains an unreviewed option or database source")
            if key in option_names:
                raise BuildContractError(f"online pacman config repeats option {key}")
            option_names.add(key)
        else:
            raise BuildContractError("online pacman config contains a directive outside the locked sections")
    if sections != ["options", "core", "extra"]:
        raise BuildContractError("online pacman config must contain only options, core, and extra sections")
    if repo_directives != {"core": [expected_server], "extra": [expected_server]}:
        raise BuildContractError("only the pinned core and extra snapshot Server lines are permitted")
    if re.search(r"(?im)^\s*SigLevel\s*=\s*Required\s+DatabaseOptional\s*$", content) is None:
        raise BuildContractError("snapshot package signatures must be required and missing database signatures explicitly optional")
    if re.search(r"(?im)^\s*LocalFileSigLevel\s*=\s*Required\s*$", content) is None:
        raise BuildContractError("local package file signatures must be required")
    if "mirrorlist" in content.casefold():
        raise BuildContractError("online pacman config must not include moving mirrors")


def _verify_archiso_initramfs_config(profile_dir: Path, *, label: str) -> str:
    """Revalidate the exact pinned Archiso hook file at each trust boundary."""
    root = _canonical_existing_dir(profile_dir, f"{label} profile", reject_root=False)
    configured_path = root / ARCHISO_INITRAMFS_CONFIG_RELATIVE_PATH
    try:
        canonical_config = _canonical_existing_file(configured_path, f"{label} Archiso initramfs config")
    except BuildContractError as exc:
        raise BuildContractError(f"{label} Archiso initramfs config is missing or unsafe") from exc
    if canonical_config != configured_path or not _is_within(canonical_config, root):
        raise BuildContractError(f"{label} Archiso initramfs config escaped its profile root")
    digest = _sha256_file(canonical_config)
    if digest != ARCHISO_V91_INITRAMFS_CONFIG_SHA256:
        raise BuildContractError(
            f"{label} Archiso initramfs config differs from the pinned Archiso 91 releng fragment; "
            "initramfs hook configuration mismatch"
        )
    return digest


def _expected_usb_initramfs_derivative(original_config: bytes) -> bytes:
    """Return the exact USB profile derivative: the pinned fragment minus one KMS hook."""
    try:
        text = original_config.decode("ascii")
    except UnicodeDecodeError as exc:
        raise BuildContractError("pinned Archiso initramfs config is not ASCII") from exc
    lines = text.splitlines(keepends=True)
    if not lines or not lines[0].startswith("HOOKS=(") or not lines[0].endswith(")\n"):
        raise BuildContractError("pinned Archiso initramfs config has an unsupported HOOKS line")
    match = re.fullmatch(r"HOOKS=\(([^()]*)\)\n", lines[0])
    if match is None:
        raise BuildContractError("pinned Archiso initramfs HOOKS line is malformed")
    hooks = match.group(1).split()
    if hooks.count(ARCHISO_USB_INITRAMFS_REMOVED_HOOK) != 1:
        raise BuildContractError("pinned Archiso initramfs config must contain exactly one kms hook")
    retained = [hook for hook in hooks if hook != ARCHISO_USB_INITRAMFS_REMOVED_HOOK]
    return ("HOOKS=(" + " ".join(retained) + ")\n").encode("ascii") + "".join(lines[1:]).encode("ascii")


def _verify_usb_initramfs_derivative(profile_dir: Path, *, label: str) -> str:
    """Verify a separately pinned derivative that changes only the KMS hook list."""
    root = _canonical_existing_dir(profile_dir, f"{label} profile", reject_root=False)
    original_path = root / ARCHISO_INITRAMFS_CONFIG_RELATIVE_PATH
    derivative_path = root / ARCHISO_USB_INITRAMFS_DERIVATIVE_RELATIVE_PATH
    _verify_archiso_initramfs_config(root, label=label)
    try:
        canonical_derivative = _canonical_existing_file(derivative_path, f"{label} USB initramfs derivative")
    except BuildContractError as exc:
        raise BuildContractError(f"{label} USB initramfs derivative is missing or unsafe") from exc
    if canonical_derivative != derivative_path or not _is_within(canonical_derivative, root):
        raise BuildContractError(f"{label} USB initramfs derivative escaped its profile root")
    original_bytes = original_path.read_bytes()
    derivative_bytes = canonical_derivative.read_bytes()
    expected_bytes = _expected_usb_initramfs_derivative(original_bytes)
    if derivative_bytes != expected_bytes:
        raise BuildContractError(
            f"{label} USB initramfs derivative must remove exactly the pinned kms hook and preserve all other bytes"
        )
    digest = _sha256_bytes(derivative_bytes)
    if digest != ARCHISO_USB_INITRAMFS_DERIVATIVE_SHA256:
        raise BuildContractError(f"{label} USB initramfs derivative differs from its reviewed SHA-256 pin")
    return digest


def _usb_initramfs_hook_order(profile_dir: Path) -> tuple[str, ...]:
    _verify_usb_initramfs_derivative(profile_dir, label="profile")
    derivative = profile_dir / ARCHISO_USB_INITRAMFS_DERIVATIVE_RELATIVE_PATH
    match = re.match(r"HOOKS=\(([^()]*)\)\n", derivative.read_text(encoding="ascii"))
    if match is None:
        raise BuildContractError("USB initramfs derivative HOOKS line is malformed")
    return tuple(match.group(1).split())


def _snapshot_database_pins(snapshot: Mapping[str, Any]) -> dict[str, str]:
    pins = snapshot.get("repository_database_sha256")
    if not isinstance(pins, dict) or set(pins) != set(SNAPSHOT_REPOSITORIES):
        raise BuildContractError("snapshot lock must pin exact core.db and extra.db SHA-256 values")
    normalized: dict[str, str] = {}
    for repository in SNAPSHOT_REPOSITORIES:
        digest = pins.get(repository)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise BuildContractError(f"snapshot lock has no valid {repository}.db SHA-256 pin")
        normalized[repository] = digest
    return normalized


def _validate_offline_repository(
    repository: Path,
    manifest_path: Path,
    snapshot_lock_sha256: str,
    required_package_names: Sequence[str],
    database_pins: Mapping[str, str],
) -> Path:
    repo_root = _canonical_existing_dir(repository, "offline repository")
    manifest_path = _canonical_existing_file(manifest_path, "offline repository manifest")
    document, _ = _load_json(manifest_path, "offline repository manifest")
    if document.get("schema_version") != 1 or document.get("snapshot_lock_sha256") != snapshot_lock_sha256:
        raise BuildContractError("offline repository manifest must bind schema 1 to the exact snapshot lock")
    files = document.get("files")
    if not isinstance(files, dict) or not files:
        raise BuildContractError("offline repository manifest must enumerate every repository file and SHA-256")
    expected: dict[str, str] = {}
    for name, digest in files.items():
        if not isinstance(name, str) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise BuildContractError("offline repository manifest contains an invalid path or SHA-256")
        relative_path = Path(name)
        if (
            relative_path.is_absolute()
            or name.startswith("/")
            or "\\" in name
            or any(part in {"", ".", ".."} for part in name.split("/"))
        ):
            raise BuildContractError("offline repository file paths must be relative and traversal-free")
        normalized_name = relative_path.as_posix()
        if normalized_name in expected:
            raise BuildContractError("offline repository manifest contains duplicate normalized paths")
        expected[normalized_name] = digest
    required_db = {
        "core/os/x86_64/core.db",
        "extra/os/x86_64/extra.db",
    }
    if not required_db.issubset(expected):
        raise BuildContractError("offline repository is missing pinned core/extra package databases")
    for repository_name, database_name in SNAPSHOT_DATABASE_RELATIVE_PATHS.items():
        if expected[database_name] != database_pins[repository_name]:
            raise BuildContractError(f"offline {repository_name}.db does not match the exact snapshot lock SHA-256")
    package_entries = document.get("packages")
    if not isinstance(package_entries, dict) or not set(required_package_names).issubset(package_entries):
        raise BuildContractError("offline manifest must bind every image package to a local signed archive")
    for package_name in required_package_names:
        archive = package_entries.get(package_name)
        if not isinstance(archive, str) or archive not in expected or not archive.endswith(".pkg.tar.zst"):
            raise BuildContractError(f"offline manifest has no checked zstd archive for package {package_name}")
        if archive + ".sig" not in expected:
            raise BuildContractError(f"offline package has no enumerated signature file: {package_name}")
    for archive in (name for name in expected if name.endswith(".pkg.tar.zst")):
        if archive + ".sig" not in expected:
            raise BuildContractError(f"offline dependency archive has no enumerated signature file: {archive}")
    actual_paths: set[str] = set()
    for item in repo_root.rglob("*"):
        if _is_reparse_or_symlink(item):
            raise BuildContractError(f"offline repository contains a symlink/junction: {item}")
        if item.is_file():
            relative_name = item.relative_to(repo_root).as_posix()
            actual_paths.add(relative_name)
            if relative_name not in expected or _sha256_file(item) != expected[relative_name]:
                raise BuildContractError(f"offline repository file is unlisted or has changed: {relative_name}")
        elif not item.is_dir():
            raise BuildContractError(f"offline repository contains a non-regular object: {item}")
    if actual_paths != set(expected):
        raise BuildContractError("offline repository does not exactly match its file/hash manifest")
    return repo_root


def preflight(config: BuildConfig, *, host_platform: str | None = None) -> PreflightResult:
    """Validate all paths and profile/snapshot inputs without launching tools."""
    if config.network_mode not in REQUIRED_MODES:
        raise BuildContractError("network mode must be explicitly set to online or offline")
    host_platform = host_platform or sys.platform
    if host_platform != "linux":
        raise BuildContractError("LINUX_BUILD_ENV_REQUIRED: Archiso images are not built on this host")

    source = _canonical_existing_dir(config.source_root, "source root")
    profile = _canonical_existing_dir(config.profile_dir, "profile")
    output = _canonical_existing_dir(config.output_dir, "output directory")
    work_parent = _canonical_existing_dir(config.work_parent, "work parent")
    snapshot_path = _canonical_existing_file(config.snapshot_lock, "snapshot lock")
    if profile != source / "usb_builder" / "archiso_profile":
        raise BuildContractError("profile path must be the reviewed archiso_profile inside the source root")
    if snapshot_path != source / "usb_builder" / "snapshot.lock.json":
        raise BuildContractError("snapshot lock must be the checked-in source lock")
    if not (source / ".git").exists():
        raise BuildContractError("source root must be a Git checkout for provenance recording")
    if any(output.iterdir()):
        raise BuildContractError("output directory must already exist and be empty; builder never overwrites outputs")
    if output == work_parent or _is_within(output, work_parent) or _is_within(work_parent, output):
        raise BuildContractError("output directory and work parent must be separate, non-nested paths")
    for protected in (source, profile, snapshot_path.parent):
        if _is_within(output, protected) or _is_within(work_parent, protected):
            raise BuildContractError("output/work paths may not be inside source, profile or snapshot directories")
    _audit_profile_tree(profile)
    package_names = _read_package_names(profile / "packages.x86_64")
    modes = _profile_boot_modes(profile)
    _validate_profile_offline_runtime(profile)
    snapshot, raw_snapshot = _load_json(snapshot_path, "snapshot lock")
    snapshot_sha = _sha256_bytes(raw_snapshot)
    if snapshot.get("schema_version") != 1 or snapshot.get("architecture") != "x86_64":
        raise BuildContractError("snapshot lock schema or architecture is unsupported")
    if snapshot.get("archiso_package") != "archiso" or snapshot.get("archiso_version") != EXPECTED_MKARCHISO_VERSION:
        raise BuildContractError("snapshot lock does not pin the reviewed archiso package version")
    if snapshot.get("rootless_builder") != ROOTLESS_BUILDER_LOCK:
        raise BuildContractError("snapshot lock does not pin the reviewed rootless Archiso helper contract")
    if snapshot.get("archiso_source_commit") != ARCHISO_V91_SOURCE_COMMIT:
        raise BuildContractError("snapshot lock does not bind Archiso 91 to its reviewed source commit")
    if snapshot.get("archiso_initramfs_config_sha256") != ARCHISO_V91_INITRAMFS_CONFIG_SHA256:
        raise BuildContractError("snapshot lock does not pin the Archiso 91 initramfs hook configuration")
    if snapshot.get("archiso_usb_initramfs_derivative") != {
        "id": ARCHISO_USB_INITRAMFS_DERIVATIVE_ID,
        "source_path": ARCHISO_USB_INITRAMFS_DERIVATIVE_SOURCE_PATH,
        "profile_path": ARCHISO_USB_INITRAMFS_DERIVATIVE_RELATIVE_PATH,
        "sha256": ARCHISO_USB_INITRAMFS_DERIVATIVE_SHA256,
        "removed_hook": ARCHISO_USB_INITRAMFS_REMOVED_HOOK,
    }:
        raise BuildContractError("snapshot lock does not pin the reviewed USB initramfs derivative")
    if snapshot.get("archiso_initramfs_hook_packages") != {
        hook: list(packages) for hook, packages in ARCHISO_INITRAMFS_HOOK_PACKAGES.items()
    }:
        raise BuildContractError("snapshot lock does not pin package requirements for configured Archiso initramfs hooks")
    if snapshot.get("archiso_initramfs_base_packages") != list(ARCHISO_INITRAMFS_BASE_PACKAGES):
        raise BuildContractError("snapshot lock does not pin mandatory Archiso initramfs base packages")
    _verify_archiso_initramfs_config(profile, label="source")
    _verify_usb_initramfs_derivative(profile, label="source")
    if snapshot.get("snapshot_date") != EXPECTED_SNAPSHOT_DATE:
        raise BuildContractError("snapshot lock date differs from the reviewed pinned repository snapshot")
    expected_repository_base = "https://archive.archlinux.org/repos/" + EXPECTED_SNAPSHOT_DATE.replace("-", "/")
    if snapshot.get("repository_base") != expected_repository_base:
        raise BuildContractError("snapshot lock URL must use the official YYYY/MM/DD archive path for its locked date")
    locked_modes = snapshot.get("required_boot_modes")
    if not isinstance(locked_modes, list) or set(locked_modes) != REQUIRED_BOOT_MODES or not REQUIRED_BOOT_MODES.issubset(modes):
        raise BuildContractError("profile BIOS/UEFI modes do not match the reviewed snapshot lock")
    if snapshot.get("snapshot_pin_status") != "fixed-candidate":
        raise BuildContractError("SNAPSHOT_NOT_PINNED: candidate build requires a fixed package snapshot")
    if snapshot.get("package_signature_policy") != "Required DatabaseOptional":
        raise BuildContractError("SNAPSHOT_SIGNATURE_POLICY_INVALID: packages must be signed; absent DB signatures are allowed only with exact DB hash pins")
    database_pins = _snapshot_database_pins(snapshot)
    if snapshot.get("wheel_build_backend") != {
        "backend": "setuptools.build_meta",
        "minimum_setuptools_version": "61.0",
        "required_tools": ["setuptools", "wheel"],
    }:
        raise BuildContractError("SNAPSHOT_WHEEL_BACKEND_INVALID: wheel backend/tool requirements are not the reviewed lock")
    if snapshot.get("release_validation_status") not in {"pending-linux-build-and-qemu", "verified"}:
        raise BuildContractError("SNAPSHOT_RELEASE_STATUS_INVALID: release status must be pending or reviewed")
    _validate_package_list_pin(profile / "packages.x86_64", snapshot)
    _validate_online_pacman_config(profile, snapshot)

    offline_root: Path | None = None
    if config.network_mode == "offline":
        if config.offline_repository is None or config.offline_manifest is None:
            raise BuildContractError("offline mode requires an explicit local repository and exact hash manifest")
        offline_root = _validate_offline_repository(
            config.offline_repository,
            config.offline_manifest,
            snapshot_sha,
            package_names,
            database_pins,
        )
    elif config.offline_repository is not None or config.offline_manifest is not None:
        raise BuildContractError("offline repository inputs are invalid in online mode")
    offline_manifest_sha = _sha256_file(config.offline_manifest) if config.offline_manifest is not None else None
    return PreflightResult(snapshot, snapshot_sha, package_names, modes, offline_root, offline_manifest_sha)


def _source_hashes(source_root: Path, profile_dir: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(profile_dir.rglob("*")):
        if path.is_file():
            rel = path.relative_to(source_root).as_posix()
            result[rel] = _sha256_file(path)
    return result


def _build_backend_evidence(runner: Runner) -> dict[str, Any]:
    pip_version = _run_command((sys.executable, "-m", "pip", "--version"), runner=runner).stdout.strip()
    show_output = _run_command(
        (sys.executable, "-m", "pip", "show", "setuptools", "wheel"), runner=runner
    ).stdout
    package_versions: dict[str, str] = {}
    current_name: str | None = None
    for line in show_output.splitlines():
        if line.startswith("Name:"):
            current_name = line.partition(":")[2].strip().casefold()
        elif line.startswith("Version:") and current_name is not None:
            package_versions[current_name] = line.partition(":")[2].strip()
            current_name = None
    if not pip_version or not {"setuptools", "wheel"}.issubset(package_versions):
        raise BuildContractError("local wheel build requires installed pip, setuptools and wheel; no installer is run")
    version_text = package_versions["setuptools"]
    numeric = re.match(r"^(\d+)(?:\.(\d+))?", version_text)
    if numeric is None:
        raise BuildContractError("cannot parse the installed setuptools version")
    setuptools_version = (int(numeric.group(1)), int(numeric.group(2) or 0))
    if setuptools_version < (61, 0):
        raise BuildContractError("local wheel build requires setuptools >= 61.0")
    return {
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "pip_version_output": pip_version,
        "backend": "setuptools.build_meta",
        "backend_packages": package_versions,
        "build_isolation": "disabled",
        "network_access": "disabled",
    }


def _run_command(
    command: Sequence[str],
    *,
    runner: Runner,
    env: Mapping[str, str] | None = None,
    stdout: Any = subprocess.PIPE,
) -> subprocess.CompletedProcess[str]:
    result = runner(list(command), check=False, text=True, stdout=stdout, stderr=subprocess.STDOUT, env=env)
    if result.returncode != 0:
        output = result.stdout or ""
        raise BuildContractError(f"command failed ({result.returncode}): {' '.join(command)}\n{output[-4000:]}")
    return result


def _current_network_namespace_identity() -> str:
    try:
        return os.readlink("/proc/self/ns/net")
    except OSError as exc:
        raise BuildContractError("OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: cannot identify the current Linux network namespace") from exc


_OFFLINE_CHILD_ENVIRONMENT_KEYS = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "TERM",
        "TMPDIR",
        "TMP",
        "TEMP",
        "SOURCE_DATE_EPOCH",
        "MAKEFLAGS",
        "PIP_NO_INDEX",
        "PIP_CONFIG_FILE",
        "PIP_DISABLE_PIP_VERSION_CHECK",
        "PYTHONNOUSERSITE",
    }
)


def _offline_child_environment(environment: Mapping[str, str] | None) -> dict[str, str]:
    """Drop loader, Python path, shell startup and other ambient injection vars."""
    source = os.environ if environment is None else environment
    return {
        key: value
        for key, value in source.items()
        if key in _OFFLINE_CHILD_ENVIRONMENT_KEYS and isinstance(key, str) and isinstance(value, str)
    }


ROOTLESS_SYSTEM_PATH = "/usr/bin:/usr/sbin"


def _rootless_build_home(host: RootlessHostEvidence) -> str:
    """Resolve and validate the private home for the already-validated caller."""
    try:
        pwd_module = __import__("pwd")
        getpwuid = getattr(pwd_module, "getpwuid", None)
        if not callable(getpwuid):
            raise BuildContractError("ROOTLESS_ARCHISO_IDENTITY_INVALID: passwd lookup is unavailable")
        account = getpwuid(host.uid)
        home = Path(account.pw_dir)
        if account.pw_name != host.username or not home.is_absolute():
            raise BuildContractError("ROOTLESS_ARCHISO_IDENTITY_INVALID: caller home does not match its UID")
        _assert_no_link_components(home)
        info = home.lstat()
    except (ImportError, KeyError, OSError) as exc:
        raise BuildContractError("ROOTLESS_ARCHISO_IDENTITY_INVALID: private caller home is unavailable") from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != host.uid
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        raise BuildContractError(
            "ROOTLESS_ARCHISO_IDENTITY_INVALID: caller home must be an owned, non-group/world-writable directory"
        )
    return str(home)


def _assert_rootless_process_state(host: RootlessHostEvidence) -> None:
    """Reject identity, capability, or trusted-tool drift around each child."""
    if sys.platform != "linux":
        raise BuildContractError("ROOTLESS_ARCHISO_REQUIRED: production Archiso child is Linux-only")
    if (
        os.getuid() != host.uid
        or os.geteuid() != host.uid
        or os.getgid() != host.gid
        or os.getegid() != host.gid
        or tuple(sorted(set(os.getgroups()))) != tuple(sorted(set(host.supplementary_gids)))
    ):
        raise BuildContractError("ROOTLESS_ARCHISO_IDENTITY_INVALID: caller identity/groups changed after preflight")
    try:
        status = Path("/proc/self/status").read_text(encoding="ascii")
    except OSError as exc:
        raise BuildContractError("ROOTLESS_ARCHISO_CAPABILITIES_INVALID: cannot re-read caller capabilities") from exc
    caps = _parse_proc_status_capabilities(status)
    if (
        caps["CapEff"] != int(host.cap_eff, 16)
        or caps["CapPrm"] != int(host.cap_prm, 16)
        or caps["CapBnd"] != int(host.cap_bnd, 16)
        or caps["CapAmb"] != int(host.cap_amb, 16)
        or caps["NoNewPrivs"] != host.no_new_privs
    ):
        raise BuildContractError("ROOTLESS_ARCHISO_CAPABILITIES_INVALID: caller capability state changed after preflight")
    for tool in host.trusted_tools:
        path = Path(tool.path)
        _assert_no_link_components(path)
        try:
            info = path.lstat()
        except OSError as exc:
            raise BuildContractError(f"ROOTLESS_ARCHISO_HELPER_INVALID: {tool.name} disappeared during build") from exc
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != tool.owner_uid
            or stat.S_IMODE(info.st_mode) != tool.mode
            or _sha256_file(path) != tool.sha256
        ):
            raise BuildContractError(f"ROOTLESS_ARCHISO_HELPER_INVALID: {tool.name} changed during build")


def _rootless_child_environment(
    host: RootlessHostEvidence,
    environment: Mapping[str, str] | None,
    *,
    permitted_paths: set[str],
) -> dict[str, str]:
    """Build a small environment with a fixed system PATH and no injection vars."""
    filtered = _offline_child_environment(environment)
    requested_path = (
        filtered.get("PATH", ROOTLESS_SYSTEM_PATH)
        if environment is not None
        else ROOTLESS_SYSTEM_PATH
    )
    if requested_path not in permitted_paths:
        raise BuildContractError("ROOTLESS_ARCHISO_ENV_INVALID: child PATH is not one of the reviewed fixed paths")
    home = _rootless_build_home(host)
    result = {
        key: value
        for key, value in filtered.items()
        if key not in {"PATH", "HOME", "USER", "LOGNAME", "SHELL"}
    }
    result.update(
        {
            "PATH": requested_path,
            "HOME": home,
            "USER": host.username,
            "LOGNAME": host.username,
            "SHELL": "/bin/bash",
        }
    )
    return result


def _validate_rootless_stdout(value: Any) -> None:
    if value is subprocess.PIPE:
        return
    if isinstance(value, (int, bool)) or value is None:
        raise BuildContractError("rootless child stdout must be PIPE or an already-open regular log file")
    fileno = getattr(value, "fileno", None)
    if not callable(fileno):
        raise BuildContractError("rootless child stdout must be PIPE or an already-open regular log file")
    try:
        mode = os.fstat(fileno()).st_mode
    except (OSError, ValueError, TypeError) as exc:
        raise BuildContractError("rootless child stdout is not a valid regular log file") from exc
    if not stat.S_ISREG(mode):
        raise BuildContractError("rootless child stdout must be a regular log file")


def _rootless_online_runner(
    runner: Runner,
    host: RootlessHostEvidence,
    *,
    command_log: list[list[str]],
    permitted_paths: set[str],
) -> Runner:
    """Run direct online children as the exact nonroot caller after preflight.

    There is deliberately no namespace prefix here: Archiso's supported `-N`
    helper path requires the invoking user to remain nonroot. The route-free
    network/mount checks are one-shot read-only gates; online package commands
    receive egress only after both have completed successfully.
    """

    def guarded(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if not isinstance(command, list) or not command or any(
            not isinstance(argument, str) or not argument for argument in command
        ):
            raise BuildContractError("rootless child command argv is empty or malformed")
        allowed_kwargs = {"check", "text", "stdout", "stderr", "env", "timeout"}
        unsupported = set(kwargs) - allowed_kwargs
        if unsupported:
            raise BuildContractError(
                "rootless child command has unsupported subprocess controls: " + ", ".join(sorted(unsupported))
            )
        if kwargs.setdefault("check", False) is not False:
            raise BuildContractError("rootless child requires the fixed check=False mode")
        if kwargs.setdefault("text", True) is not True:
            raise BuildContractError("rootless child requires text=True")
        _validate_rootless_stdout(kwargs.setdefault("stdout", subprocess.PIPE))
        if kwargs.setdefault("stderr", subprocess.STDOUT) is not subprocess.STDOUT:
            raise BuildContractError("rootless child stderr must be merged into stdout")
        timeout = kwargs.get("timeout")
        if timeout is not None and (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise BuildContractError("rootless child timeout must be a positive number of seconds")
        requested_env = kwargs.get("env")
        if requested_env is not None and not isinstance(requested_env, Mapping):
            raise BuildContractError("rootless child environment must be a string mapping")
        kwargs["env"] = _rootless_child_environment(
            host,
            requested_env,
            permitted_paths=permitted_paths,
        )
        # Neither caller pipes nor descriptors can cross into package scripts.
        kwargs["stdin"] = subprocess.DEVNULL
        kwargs["close_fds"] = True
        _assert_rootless_process_state(host)
        command_log.append(list(command))
        try:
            result = runner(command, **kwargs)
        finally:
            _assert_rootless_process_state(host)
        return result

    return guarded


def _rootless_host_manifest(host: RootlessHostEvidence) -> dict[str, Any]:
    result = asdict(host)
    result["trusted_tools"] = [asdict(tool) for tool in host.trusted_tools]
    return result


def _rootless_online_network_isolation(
    host: RootlessHostEvidence,
    mount_preflight: Mapping[str, Any],
) -> NetworkIsolation:
    tools = {tool.name: tool for tool in host.trusted_tools}
    unshare = tools["unshare"]
    report = mount_preflight["report"]
    namespace_ids = mount_preflight["host_namespace_ids"]
    return NetworkIsolation(
        mode="online-egress-after-rootless-preflight",
        command_prefix=(),
        privilege_mode="direct-nonroot-archiso-N",
        unshare_path=unshare.path,
        unshare_sha256=unshare.sha256,
        unshare_package_owner_output=unshare.owner_output,
        iproute_path=str(mount_preflight["iproute_path"]),
        iproute_sha256=str(mount_preflight["iproute_sha256"]),
        iproute_package_owner_output=str(mount_preflight["iproute_package_owner_output"]),
        host_namespace_identity=str(namespace_ids["net"]),
        host_user_namespace_identity=str(namespace_ids["user"]),
        host_mount_namespace_identity=str(namespace_ids["mnt"]),
        host_pid_namespace_identity=str(namespace_ids["pid"]),
        probe_command=(),
        probe_output=json.dumps(report["network"], sort_keys=True, separators=(",", ":")),
        mount_probe_command=tuple(str(item) for item in mount_preflight["command"]),
        mount_probe_output=str(mount_preflight["output"]),
        host_mount_table_sha256=str(mount_preflight["host_mount_table_sha256"]),
    )


def _validate_network_probe_output(
    output: str,
    *,
    host_namespace_identity: str,
    allow_synthetic: bool,
) -> bool:
    normalized = output.strip()
    if allow_synthetic and normalized == OFFLINE_NETWORK_PROBE_MARKER:
        return True
    lines = normalized.splitlines()
    if len(lines) != 2 or lines[0] != OFFLINE_NETWORK_PROBE_MARKER:
        return False
    try:
        report = json.loads(lines[1])
    except json.JSONDecodeError:
        return False
    if not isinstance(report, dict) or set(report) != {
        "namespace_id",
        "sysfs_interfaces_observed",
        "namespace_interfaces",
        "ipv4_netlink_route_count",
        "ipv6_netlink_route_count",
        "ipv4_rule_priorities",
        "ipv6_rule_priorities",
        "ipv6_null_unreachable_proc_rows",
    }:
        return False
    namespace_id = report.get("namespace_id")
    sysfs_interfaces = report.get("sysfs_interfaces_observed")
    v4_count = report.get("ipv4_netlink_route_count")
    v6_count = report.get("ipv6_netlink_route_count")
    sentinel_count = report.get("ipv6_null_unreachable_proc_rows")
    return (
        isinstance(namespace_id, str)
        and re.fullmatch(r"net:\[\d+\]", namespace_id) is not None
        and namespace_id != host_namespace_identity
        and isinstance(sysfs_interfaces, list)
        and all(isinstance(name, str) for name in sysfs_interfaces)
        and "lo" in sysfs_interfaces
        and report.get("namespace_interfaces") == ["lo"]
        and type(v4_count) is int
        and v4_count == 0
        and type(v6_count) is int
        and v6_count == 0
        and report.get("ipv4_rule_priorities") == [0, 32766, 32767]
        and report.get("ipv6_rule_priorities") == [0, 32766]
        and type(sentinel_count) is int
        and sentinel_count in (0, 2)
    )


def _host_mount_table_bytes() -> bytes:
    try:
        return Path("/proc/self/mountinfo").read_bytes()
    except OSError as exc:
        raise BuildContractError("ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: cannot read host mount table") from exc


def _host_namespace_identities_for_probe(
    runner: Runner,
    host_network_identity: str,
) -> dict[str, str]:
    if runner is subprocess.run:
        try:
            identities = _current_namespace_identities()
        except (OSError, RuntimeError) as exc:
            raise BuildContractError(
                "OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: cannot record host user/net/mount/PID namespace identities"
            ) from exc
        if identities.get("net") != host_network_identity:
            raise BuildContractError("OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: host network identity changed during preflight")
        return identities
    # Test adapters carry non-authenticating fixture identifiers. The child
    # report must still supply four distinct, correctly typed identities.
    return {
        "user": "user:[100]",
        "net": host_network_identity if re.fullmatch(r"net:\[\d+\]", host_network_identity) else "net:[101]",
        "mnt": "mnt:[102]",
        "pid": "pid:[103]",
    }


MOUNT_PROBE_TIMEOUT_SECONDS = 90.0
MOUNT_PROBE_TERMINATION_GRACE_SECONDS = 5.0


def _run_bounded_mount_probe(command: Sequence[str], *, runner: Runner) -> subprocess.CompletedProcess[str]:
    """Run the isolated capability probe with bounded process-group cleanup."""
    kwargs: dict[str, Any] = {
        "check": False,
        "text": True,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "env": _offline_child_environment(None),
        "close_fds": True,
        "timeout": MOUNT_PROBE_TIMEOUT_SECONDS,
    }
    if runner is not subprocess.run:
        return runner(list(command), **kwargs)

    child = subprocess.Popen(
        list(command),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=kwargs["env"],
        close_fds=True,
        start_new_session=True,
    )
    try:
        stdout, _ = child.communicate(timeout=MOUNT_PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as timeout_error:
        killpg = getattr(os, "killpg", None)
        sigkill = getattr(signal, "SIGKILL", None)
        sigterm = getattr(signal, "SIGTERM", None)
        if not callable(killpg) or not isinstance(sigkill, int) or not isinstance(sigterm, int):
            raise BuildContractError(
                "ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: process-group cleanup is unavailable"
            ) from timeout_error

        def signal_group(number: int) -> None:
            try:
                killpg(child.pid, number)
            except ProcessLookupError:
                pass
            except OSError as exc:
                if exc.errno != errno.ESRCH:
                    raise

        try:
            signal_group(sigterm)
            try:
                child.communicate(timeout=MOUNT_PROBE_TERMINATION_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                pass
            finally:
                # The leader can exit after SIGTERM while a grandchild remains
                # in the group and holds stdout open, so always send SIGKILL.
                signal_group(sigkill)
            try:
                child.communicate(timeout=MOUNT_PROBE_TERMINATION_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                for stream in (child.stdout, child.stderr):
                    if stream is not None:
                        stream.close()
                try:
                    child.wait(timeout=MOUNT_PROBE_TERMINATION_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    raise BuildContractError(
                        "ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: timed-out unshare leader could not be reaped"
                    ) from timeout_error
        except OSError as exc:
            raise BuildContractError(
                "ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: could not terminate timed-out mount probe process group"
            ) from exc
        raise subprocess.TimeoutExpired(
            command,
            MOUNT_PROBE_TIMEOUT_SECONDS,
            output=timeout_error.output,
        ) from timeout_error
    return subprocess.CompletedProcess(list(command), child.returncode, stdout, "")


def _establish_offline_network_isolation(
    runner: Runner,
    *,
    unshare_path: str | None = None,
    iproute_path: str | None = None,
    host_namespace_identity: str | None = None,
    host_mount_table_reader: Callable[[], bytes] | None = None,
    is_root: bool | None = None,
) -> NetworkIsolation:
    """Verify offline networking and exact Archiso mount capability pre-build."""
    candidate = unshare_path or shutil.which("unshare")
    production_lookup = runner is subprocess.run or (
        unshare_path is None and candidate is not None and sys.platform == "linux"
    )
    if not candidate and not production_lookup:
        candidate = "unshare"
    if not candidate:
        raise BuildContractError("OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: util-linux unshare is required")
    if Path(candidate).is_absolute() or Path(candidate).exists():
        try:
            # Resolve usrmerge aliases such as /usr/sbin -> /usr/bin, then invoke
            # only the canonical target so a PATH symlink cannot be swapped later.
            resolved_candidate = Path(candidate).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise BuildContractError("OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: unshare path cannot be resolved") from exc
        unshare_file = _canonical_existing_file(resolved_candidate, "unshare executable")
        if sys.platform == "linux" and not (unshare_file.stat().st_mode & 0o111):
            raise BuildContractError("OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: unshare target is not executable")
    elif not production_lookup:
        # A synthetic runner never executes this symbolic name. This branch is
        # test-only; production requires a canonical file and pacman owner.
        unshare_file = Path(candidate)
    else:
        raise BuildContractError("OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: unshare path is not canonical")
    unshare_digest_before_probe = _sha256_file(unshare_file) if unshare_file.is_file() else None
    owner_output: str | None = None
    iproute_owner_output: str | None = None
    # Production lookup is PATH-based. Require pacman's ownership evidence for
    # the resolved executable, including explicit paths used with the real
    # subprocess runner. Synthetic test executables are allowed only alongside
    # an injected runner; the actual CLI never exposes either injection.
    if production_lookup:
        if sys.platform != "linux":
            raise BuildContractError("OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: trusted util-linux lookup requires Linux")
        try:
            ownership = _run_command(("pacman", "-Qo", str(unshare_file)), runner=runner)
        except (BuildContractError, OSError) as exc:
            raise BuildContractError(
                "OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: trusted Arch pacman/util-linux ownership prerequisite "
                f"is unavailable for resolved executable {unshare_file}; pacman -Qo must verify it"
            ) from exc
        owner_output = ownership.stdout.strip()
        expected_owner = re.compile(re.escape(str(unshare_file)) + r" is owned by util-linux \S+")
        if expected_owner.fullmatch(owner_output) is None:
            raise BuildContractError(
                "OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: pacman did not confirm that the exact resolved unshare "
                "path is owned by util-linux"
            )
    ip_candidate = iproute_path or shutil.which("ip")
    if not ip_candidate:
        if production_lookup:
            raise BuildContractError(
                "OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: trusted iproute2 executable is required for namespace-aware "
                "link, address, route, and rule verification"
            )
        # Fake runners use a symbolic tool name; no process is launched by
        # the test adapter.
        ip_candidate = "ip"
    iproute_file: Path | None = None
    if Path(ip_candidate).is_absolute() or Path(ip_candidate).exists():
        try:
            iproute_resolved = Path(ip_candidate).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise BuildContractError("OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: iproute2 path cannot be resolved") from exc
        iproute_file = _canonical_existing_file(iproute_resolved, "iproute2 executable")
        if sys.platform == "linux" and not (iproute_file.stat().st_mode & 0o111):
            raise BuildContractError("OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: iproute2 target is not executable")
    elif production_lookup:
        raise BuildContractError("OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: iproute2 path is not canonical")
    else:
        iproute_file = Path(ip_candidate)
    iproute_digest_before_probe = _sha256_file(iproute_file) if iproute_file.is_file() else None
    if production_lookup:
        if sys.platform != "linux":
            raise BuildContractError("OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: trusted iproute2 lookup requires Linux")
        try:
            ownership = _run_command(("pacman", "-Qo", str(iproute_file)), runner=runner)
        except (BuildContractError, OSError) as exc:
            raise BuildContractError(
                "OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: trusted Arch pacman/iproute2 ownership prerequisite "
                f"is unavailable for resolved executable {iproute_file}; pacman -Qo must verify it"
            ) from exc
        iproute_owner_output = ownership.stdout.strip()
        expected_iproute_owner = re.compile(re.escape(str(iproute_file)) + r" is owned by iproute2 \S+")
        if expected_iproute_owner.fullmatch(iproute_owner_output) is None:
            raise BuildContractError(
                "OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: pacman did not confirm that the exact resolved iproute2 "
                "path is owned by iproute2"
            )
    host_identity = host_namespace_identity or (
        _current_network_namespace_identity() if production_lookup else "net:[100]"
    )
    host_ids = _host_namespace_identities_for_probe(runner, host_identity)
    # Always create a child user namespace, even when the caller is UID 0.
    # Bare root `unshare --net` retains capabilities in the initial user
    # namespace and could join the host netns with setns(2).  A user-mapped
    # namespace confines CAP_SYS_ADMIN to the child user/net namespaces.
    # `is_root` remains accepted for test/compatibility callers but does not
    # weaken this invariant.
    _ = is_root
    privilege_mode = "user-mapped-root"
    # The private mount namespace lets Archiso set up chroots without touching
    # host mounts. A child PID namespace plus its own procfs lets chroot setup
    # inspect the correct process tree. These flags do not weaken the user-
    # mapped network boundary: CAP_SYS_ADMIN remains scoped to the child user
    # namespace, and network views are still verified inside the child.
    namespace_flags = (
        "--user",
        "--map-root-user",
        "--net",
        "--mount",
        "--pid",
        "--fork",
        "--propagation",
        "private",
        "--mount-proc",
    )
    mount_table_reader = host_mount_table_reader
    if mount_table_reader is None:
        mount_table_reader = _host_mount_table_bytes if runner is subprocess.run else lambda: b"synthetic-host-mountinfo"
    try:
        host_mount_before = mount_table_reader()
    except (OSError, BuildContractError) as exc:
        raise BuildContractError(
            "ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: cannot snapshot host mount table before probe"
        ) from exc
    if not isinstance(host_mount_before, bytes) or not host_mount_before:
        raise BuildContractError("ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: host mount snapshot is empty or malformed")
    command = (
        str(unshare_file),
        *namespace_flags,
        "--",
        sys.executable,
        "-m",
        "usb_builder.network_probe",
        host_identity,
        str(iproute_file),
    )
    try:
        result = runner(
            list(command),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=_offline_child_environment(None),
            close_fds=True,
        )
    except OSError as exc:
        raise BuildContractError(f"OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: failed to start unshare probe: {exc}") from exc
    output = result.stdout or ""
    if result.returncode != 0 or not _validate_network_probe_output(
        output,
        host_namespace_identity=host_identity,
        allow_synthetic=runner is not subprocess.run,
    ):
        raise BuildContractError(
            "OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: could not establish and verify a route-free network namespace; "
            f"unshare returned {result.returncode}: {output[-2000:]}"
        )
    mount_command = (
        str(unshare_file),
        *namespace_flags,
        "--",
        sys.executable,
        "-m",
        "usb_builder.mount_probe",
        json.dumps(host_ids, sort_keys=True, separators=(",", ":")),
        host_identity,
        str(iproute_file),
    )
    mount_run_error: BaseException | None = None
    mount_timeout_group_cleaned = False
    try:
        mount_result = _run_bounded_mount_probe(mount_command, runner=runner)
        mount_output = mount_result.stdout if isinstance(mount_result.stdout, str) else ""
        parsed_mount = _validate_mount_probe_output(
            mount_output,
            host_namespace_ids=host_ids,
            host_net_namespace=host_identity,
            allow_synthetic=runner is not subprocess.run,
        )
    except subprocess.TimeoutExpired as exc:
        mount_run_error = exc
        mount_timeout_group_cleaned = runner is subprocess.run
        mount_output = ""
        parsed_mount = None
    except (OSError, BuildContractError) as exc:
        mount_run_error = exc
        mount_output = ""
        parsed_mount = None
    try:
        host_mount_after = mount_table_reader()
    except (OSError, BuildContractError) as exc:
        raise BuildContractError(
            "ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: cannot snapshot host mount table after probe"
        ) from exc
    if not isinstance(host_mount_after, bytes) or host_mount_after != host_mount_before:
        raise BuildContractError(
            "ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: host mount table changed during capability probe"
        )
    if mount_run_error is not None:
        if mount_timeout_group_cleaned:
            cleanup_evidence = "production timeout cleanup signaled the isolated process group with TERM then KILL"
        else:
            cleanup_evidence = f"probe error was {type(mount_run_error).__name__}: {mount_run_error}"
        raise BuildContractError(
            "ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: bounded mount probe could not complete cleanly; "
            f"{cleanup_evidence} and the host mount table was rechecked"
        ) from mount_run_error
    if parsed_mount is None:
        raise BuildContractError(
            "ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: mount probe report or child namespace evidence was invalid; "
            f"exit={mount_result.returncode}: {mount_output[-2000:]}"
        )
    if (
        (unshare_digest_before_probe is not None and _sha256_file(unshare_file) != unshare_digest_before_probe)
        or (iproute_digest_before_probe is not None and _sha256_file(iproute_file) != iproute_digest_before_probe)
    ):
        raise BuildContractError("ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: namespace probe tool identity changed")
    mount_status, mount_report = parsed_mount
    if mount_result.returncode == MOUNT_PROBE_UNSUPPORTED_EXIT and mount_status == "UNSUPPORTED":
        denied = [
            item["name"]
            for item in mount_report["mount_attempts"]
            if item["status"] == "denied"
        ]
        raise BuildContractError(
            "ARCHISO_MOUNT_CAPABILITY_UNAVAILABLE: exact Archiso chroot mount set is unavailable in this "
            f"namespace: {', '.join(denied)}"
        )
    if mount_result.returncode != MOUNT_PROBE_SUPPORTED_EXIT or mount_status != "SUPPORTED":
        raise BuildContractError(
            "ARCHISO_MOUNT_PREFLIGHT_INTEGRITY_FAILURE: mount probe exit/status mismatch "
            f"({mount_result.returncode}/{mount_status})"
        )
    return NetworkIsolation(
        mode="linux-network-namespace",
        command_prefix=(
            str(unshare_file),
            *namespace_flags,
            "--",
            sys.executable,
            "-m",
            "usb_builder.network_probe",
            "--run",
            host_identity,
            str(iproute_file),
        ),
        privilege_mode=privilege_mode,
        unshare_path=str(unshare_file),
        unshare_sha256=unshare_digest_before_probe,
        unshare_package_owner_output=owner_output,
        iproute_path=str(iproute_file),
        iproute_sha256=iproute_digest_before_probe,
        iproute_package_owner_output=iproute_owner_output,
        host_namespace_identity=host_identity,
        host_user_namespace_identity=host_ids["user"],
        host_mount_namespace_identity=host_ids["mnt"],
        host_pid_namespace_identity=host_ids["pid"],
        probe_command=command,
        probe_output=output.strip(),
        mount_probe_command=mount_command,
        mount_probe_output=mount_output.strip(),
        host_mount_table_sha256=_sha256_bytes(host_mount_before),
    )


def _network_guarded_runner(
    runner: Runner,
    isolation: NetworkIsolation,
    *,
    command_log: list[list[str]] | None = None,
) -> Runner:
    def verify_namespace() -> None:
        if isolation.mode != "linux-network-namespace":
            return
        if isolation.unshare_path and isolation.unshare_sha256:
            if _sha256_file(Path(isolation.unshare_path)) != isolation.unshare_sha256:
                raise BuildContractError("OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: unshare changed during build")
        if isolation.iproute_path and isolation.iproute_sha256:
            if _sha256_file(Path(isolation.iproute_path)) != isolation.iproute_sha256:
                raise BuildContractError("OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: iproute2 changed during build")
        if command_log is not None:
            command_log.append(list(isolation.probe_command))
        try:
            probe = runner(
                list(isolation.probe_command),
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=_offline_child_environment(None),
                close_fds=True,
            )
        except OSError as exc:
            raise BuildContractError("OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: namespace recheck failed to launch") from exc
        output = probe.stdout or ""
        if probe.returncode != 0 or not _validate_network_probe_output(
            output,
            host_namespace_identity=isolation.host_namespace_identity or "",
            allow_synthetic=runner is not subprocess.run,
        ):
            raise BuildContractError(
                "OFFLINE_NETWORK_ISOLATION_UNAVAILABLE: namespace route/interface state failed pre/post command recheck"
            )

    def guarded(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if not isinstance(command, list) or not command or any(
            not isinstance(argument, str) or not argument for argument in command
        ):
            raise BuildContractError("offline child command argv is empty or malformed")
        if isolation.mode != "linux-network-namespace":
            if command_log is not None:
                command_log.append(list(command))
            return runner(command, **kwargs)
        allowed_kwargs = {"check", "text", "stdout", "stderr", "env", "timeout"}
        unsupported_kwargs = set(kwargs) - allowed_kwargs
        if unsupported_kwargs:
            names = ", ".join(sorted(unsupported_kwargs))
            raise BuildContractError(
                f"offline child command has unsupported subprocess controls: {names}"
            )
        if kwargs.setdefault("check", False) is not False:
            raise BuildContractError("offline child command requires the fixed check=False mode")
        if kwargs.setdefault("text", True) is not True:
            raise BuildContractError("offline child command requires text=True")
        requested_stdout = kwargs.setdefault("stdout", subprocess.PIPE)
        if requested_stdout is not subprocess.PIPE:
            if isinstance(requested_stdout, (int, bool)) or requested_stdout is None:
                raise BuildContractError(
                    "offline child stdout must be PIPE or an already-open regular log file"
                )
            fileno = getattr(requested_stdout, "fileno", None)
            if not callable(fileno):
                raise BuildContractError(
                    "offline child stdout must be PIPE or an already-open regular log file"
                )
            try:
                stdout_mode = os.fstat(fileno()).st_mode
            except (OSError, ValueError, TypeError) as exc:
                raise BuildContractError("offline child stdout is not a valid regular log file") from exc
            if not stat.S_ISREG(stdout_mode):
                raise BuildContractError("offline child stdout must be a regular log file")
        if kwargs.setdefault("stderr", subprocess.STDOUT) is not subprocess.STDOUT:
            raise BuildContractError("offline child stderr must be merged into stdout")
        # `stdin` is deliberately not accepted from callers. Never allow a
        # host terminal/socket to flow into the namespace child.
        kwargs["stdin"] = subprocess.DEVNULL
        requested_environment = kwargs.get("env")
        kwargs["env"] = _offline_child_environment(
            requested_environment if isinstance(requested_environment, Mapping) else None
        )
        timeout = kwargs.pop("timeout", None)
        if timeout is not None and (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise BuildContractError("offline child timeout must be a positive number of seconds")
        verify_namespace()
        actual_command = [*isolation.command_prefix]
        if timeout is not None:
            actual_command.extend(("--timeout", str(timeout)))
        actual_command.extend(("--", *command))
        if command_log is not None:
            command_log.append(actual_command)
        # Never let a caller weaken descriptor isolation. This is fixed for
        # the namespace launcher and its subprocess regardless of input.
        kwargs["close_fds"] = True
        try:
            result = runner(actual_command, **kwargs)
        except Exception:
            verify_namespace()
            raise
        verify_namespace()
        return result

    return guarded


def _source_provenance(source: Path, runner: Runner) -> dict[str, str]:
    head = _run_command(("git", "-C", str(source), "rev-parse", "HEAD"), runner=runner).stdout.strip()
    tree = _run_command(("git", "-C", str(source), "rev-parse", "HEAD^{tree}"), runner=runner).stdout.strip()
    status = _run_command(("git", "-C", str(source), "status", "--porcelain", "--untracked-files=all"), runner=runner).stdout
    if status.strip():
        raise BuildContractError("source Git tree is dirty; commit the source before producing a release image")
    if not re.fullmatch(r"[0-9a-f]{40}", head) or not re.fullmatch(r"[0-9a-f]{40}", tree):
        raise BuildContractError("Git did not provide exact commit/tree provenance")
    return {"commit": head, "tree": tree, "dirty": "false"}


def _parse_package_inventory(output: str, *, label: str, required: Sequence[str] = ()) -> tuple[str, ...]:
    packages: dict[str, str] = {}
    for line in output.splitlines():
        match = re.fullmatch(r"([A-Za-z0-9@._+-]+)\s+(\S+)", line.strip())
        if match is None or match.group(1) in packages:
            raise BuildContractError(f"{label} is malformed or contains duplicate package names")
        packages[match.group(1)] = match.group(2)
    if not packages:
        raise BuildContractError(f"{label} is empty")
    missing = sorted(set(required).difference(packages))
    if missing:
        raise BuildContractError(f"{label} is missing required packages: {missing}")
    return tuple(f"{name} {packages[name]}" for name in sorted(packages))


def _copy_wheel_source(source: Path, destination: Path) -> dict[str, str]:
    """Stage only package/build metadata files so pip cannot mutate the checkout."""
    destination.mkdir(mode=0o700)
    for filename in ("pyproject.toml", "README.md", "LICENSE"):
        path = source / filename
        if _is_reparse_or_symlink(path) or not path.is_file():
            raise BuildContractError(f"wheel build input is missing or linked: {path}")
        shutil.copy2(path, destination / filename)
    hashes: dict[str, str] = {}
    for package in ("autoloader", "usb_builder"):
        package_source = source / package
        if not package_source.is_dir() or _is_reparse_or_symlink(package_source):
            raise BuildContractError(f"wheel package source is missing or linked: {package_source}")
        for item in package_source.rglob("*"):
            if _is_reparse_or_symlink(item):
                raise BuildContractError(f"wheel source contains a symlink or junction: {item}")
            if item.is_file() and item.suffix.casefold() in {
                ".rom", ".bin", ".dump", ".backup", ".pem", ".key", ".p12", ".pfx", ".kdbx"
            }:
                raise BuildContractError(f"firmware, backup, or credential file cannot enter the wheel build: {item}")
        shutil.copytree(
            package_source,
            destination / package,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
        )
    for path in sorted(destination.rglob("*")):
        if path.is_file():
            hashes[path.relative_to(destination).as_posix()] = _sha256_file(path)
    return hashes


def _snapshot_url_for_profile(pacman_conf: str) -> str:
    match = re.search(r"(?m)^\s*Server\s*=\s*(https://archive\.archlinux\.org/repos/\d{4}/\d{2}/\d{2})/\$repo/os/\$arch\s*$", pacman_conf)
    if match is None:
        raise BuildContractError("cannot derive locked online snapshot URL")
    return match.group(1)


def _copy_runbooks(source_root: Path, stage_profile: Path) -> None:
    rootfs = stage_profile / "airootfs"
    doc_root = rootfs / "usr" / "share" / "doc" / "libre-core-autoloader"
    doc_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_root / "README.md", doc_root / "README.md")
    shutil.copytree(source_root / "docs" / "v1", doc_root / "v1", dirs_exist_ok=True)


def _enable_autoloader_service(stage_profile: Path) -> Path:
    target_dropin = stage_profile / "airootfs" / "etc" / "systemd" / "system" / "multi-user.target.d"
    target_dropin.mkdir(parents=True, exist_ok=True)
    path = target_dropin / "autoloader.conf"
    if path.exists() or _is_reparse_or_symlink(path):
        raise BuildContractError("profile already contains an unexpected service activation override")
    path.write_text("[Unit]\nWants=autoloader.service\n", encoding="utf-8", newline="\n")
    return path


def _write_image_input_provenance(
    source_root: Path,
    stage_profile: Path,
    *,
    snapshot_sha256: str,
    source_info: Mapping[str, str],
    wheel: Path,
    package_names: Sequence[str],
    repository_databases: Mapping[str, Mapping[str, Any]],
    offline_repository: Mapping[str, Any] | None,
) -> Path:
    target = (
        stage_profile
        / "airootfs"
        / "usr"
        / "share"
        / "doc"
        / "libre-core-autoloader"
        / "image-build-inputs.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": 1,
        "source": dict(source_info),
        "snapshot_lock_sha256": snapshot_sha256,
        "repository_databases": {name: dict(value) for name, value in repository_databases.items()},
        "offline_repository": dict(offline_repository) if offline_repository is not None else None,
        "signature_policy": {
            "package_archives": "REQUIRED_AND_VERIFIED_BY_PACMAN",
            "repository_databases": "OPTIONAL_DETACHED_SIGNATURE; IF_PRESENT_MUST_VERIFY; EXACT_LOCKED_SHA256_ALWAYS_REQUIRED",
            "local_file_packages": "REQUIRED",
        },
        "profile_file_sha256": _source_hashes(source_root, source_root / "usb_builder" / "archiso_profile"),
        "wheel": {"filename": wheel.name, "sha256": _sha256_file(wheel), "size_bytes": wheel.stat().st_size},
        "package_names_from_locked_snapshot": list(package_names),
        "wheel_build": {
            "tool": Path(sys.executable).name,
            "operation": "pip wheel",
            "backend": "setuptools.build_meta",
            "source": "temporary package-only source copy",
            "output": "temporary local wheelhouse",
            "build_isolation": "disabled",
            "dependency_resolution": "disabled",
            "network_access": "disabled",
        },
        "build_and_boot_status": "BUILD_NOT_PERFORMED_AT_PACKAGE_INSTALL_TIME; BOOT_NOT_PERFORMED",
    }
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def _copy_wheel_into_profile(wheel: Path, stage_profile: Path) -> None:
    destination = stage_profile / "airootfs" / "opt" / "autoloader" / "wheelhouse"
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(wheel, destination / wheel.name)


def _install_snapshot_conf(
    stage_profile: Path,
    snapshot_database_root: Path,
    temp_root: Path,
    *,
    offline_repository: Path | None,
) -> Path:
    staged = temp_root / ("pacman-offline.conf" if offline_repository is not None else "pacman-snapshot.conf")
    staged.write_text(
        (stage_profile / "pacman.conf").read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
    )
    content = staged.read_text(encoding="utf-8")
    remote = _snapshot_url_for_profile(content) + "/$repo/os/$arch"
    cache_server = offline_repository.as_uri() + "/$repo/os/$arch" if offline_repository is not None else remote
    database_server = snapshot_database_root.as_uri() + "/$repo/os/$arch"
    # Pacman CacheServer is package-only; the sole Server is the verified DB copy.
    replacement = "CacheServer = " + cache_server + "\nServer = " + database_server
    replaced = content.replace("Server = " + remote, replacement)
    if replaced == content or replaced.count("Server = " + database_server) != 2:
        raise BuildContractError("pacman config could not bind core and extra to the staged pinned databases")
    if offline_repository is not None and ("https://" in replaced or "http://" in replaced):
        raise BuildContractError("offline pacman config must contain only local repository URLs")
    staged.write_text(replaced, encoding="utf-8", newline="\n")
    return staged


def _validate_archive_resource_url(url: str, *, allow_non_archive_path: bool = False) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise BuildContractError("snapshot URL is malformed") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "archive.archlinux.org"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise BuildContractError("snapshot URL must use HTTPS on the exact official archive host")
    if not allow_non_archive_path and re.fullmatch(
        r"/repos/\d{4}/\d{2}/\d{2}/(?:core|extra)/os/x86_64/[^/]+", parsed.path
    ) is None:
        raise BuildContractError("snapshot URL path is outside the pinned core/extra archive resources")


def _validate_archive_redirect(requested_url: str, final_url: str) -> None:
    _validate_archive_resource_url(requested_url)
    _validate_archive_resource_url(final_url)
    if urlsplit(requested_url).path != urlsplit(final_url).path:
        raise BuildContractError("snapshot redirect changed the exact requested archive resource path")


def _fetch_snapshot_resource(url: str, destination: Path, *, optional: bool, runner: Runner) -> str | None:
    """Fetch one fixed archive object and return its validated final URL."""
    _validate_archive_resource_url(url)
    if destination.exists() or _is_reparse_or_symlink(destination):
        raise BuildContractError(f"refusing to overwrite snapshot evidence path: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = _run_command(
        (sys.executable, "-c", _SNAPSHOT_FETCH_SCRIPT, url, str(destination), "optional" if optional else "required"),
        runner=runner,
    )
    status_match = re.search(r"(?m)^HTTP_STATUS=(\d{3})\s*$", result.stdout or "")
    final_url_match = re.search(r"(?m)^FINAL_URL=([^\s]+)\s*$", result.stdout or "")
    if final_url_match is None:
        raise BuildContractError(f"snapshot fetch returned no final URL for {url}")
    final_url = final_url_match.group(1)
    _validate_archive_redirect(url, final_url)
    if status_match is None:
        raise BuildContractError(f"snapshot fetch returned no HTTP status for {url}")
    status = int(status_match.group(1))
    if optional and status == 404:
        if destination.exists():
            destination.unlink()
        return None
    if status != 200 or _is_reparse_or_symlink(destination) or not destination.is_file() or destination.stat().st_size == 0:
        raise BuildContractError(f"snapshot fetch did not return a non-empty HTTP 200 resource: {url} (HTTP {status})")
    return final_url


def _copy_verified_offline_repository(
    source_repository: Path,
    manifest_path: Path,
    snapshot_lock_sha256: str,
    required_package_names: Sequence[str],
    database_pins: Mapping[str, str],
    destination: Path,
) -> tuple[Path, dict[str, str]]:
    """Copy the exact signed, manifest-bound offline tree into owned build work."""
    source_root = _validate_offline_repository(
        source_repository, manifest_path, snapshot_lock_sha256, required_package_names, database_pins
    )
    if destination.exists() or _is_reparse_or_symlink(destination):
        raise BuildContractError("offline repository staging destination already exists or is linked")
    document, manifest_bytes = _load_json(manifest_path, "offline repository manifest")
    files = document.get("files")
    if not isinstance(files, dict):
        raise BuildContractError("offline manifest files changed after validation")
    destination.mkdir(mode=0o700, parents=False)
    staged_hashes: dict[str, str] = {}
    for relative_name, expected_digest in sorted(files.items()):
        relative = Path(relative_name)
        source_file = source_root / relative
        target_file = destination / relative
        # Recheck every component immediately before opening the source path.
        current = source_root
        for part in relative.parts:
            current = current / part
            if _is_reparse_or_symlink(current):
                raise BuildContractError(f"offline repository path became linked during staging: {relative_name}")
        if not source_file.is_file() or _sha256_file(source_file) != expected_digest:
            raise BuildContractError(f"offline repository file changed during staging: {relative_name}")
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with source_file.open("rb") as source_stream, target_file.open("xb") as target_stream:
            shutil.copyfileobj(source_stream, target_stream)
        copied_digest = _sha256_file(target_file)
        if copied_digest != expected_digest:
            raise BuildContractError(f"offline repository staged copy does not match manifest: {relative_name}")
        staged_hashes[relative_name] = copied_digest
    if _sha256_file(manifest_path) != _sha256_bytes(manifest_bytes):
        raise BuildContractError("offline repository manifest changed during staging")
    _validate_offline_repository(
        destination, manifest_path, snapshot_lock_sha256, required_package_names, database_pins
    )
    return destination, staged_hashes


def _verify_pacman_signature(signature: Path, artifact: Path, *, runner: Runner, label: str) -> str:
    if _is_reparse_or_symlink(signature) or not signature.is_file() or signature.stat().st_size == 0:
        raise BuildContractError(f"{label} signature is missing or not a regular file")
    if _is_reparse_or_symlink(artifact) or not artifact.is_file() or artifact.stat().st_size == 0:
        raise BuildContractError(f"{label} signed file is missing or not a regular file")
    result = _run_command(("pacman-key", "--verify", str(signature), str(artifact)), runner=runner)
    return (result.stdout or "").strip()


def _stage_repository_databases(
    snapshot: Mapping[str, Any],
    work_dir: Path,
    *,
    runner: Runner,
    offline_repository: Path | None,
) -> tuple[Path, dict[str, dict[str, Any]]]:
    pins = _snapshot_database_pins(snapshot)
    database_root = work_dir / "snapshot-databases"
    database_root.mkdir(mode=0o700)
    base = snapshot["repository_base"]
    evidence: dict[str, dict[str, Any]] = {}
    for repository in SNAPSHOT_REPOSITORIES:
        relative_name = SNAPSHOT_DATABASE_RELATIVE_PATHS[repository]
        relative_path = Path(relative_name)
        database = database_root / relative_path
        signature = database.with_name(database.name + ".sig")
        url = f"{base}/{relative_name}"
        signature_url = url + ".sig"
        database_final_url: str | None
        signature_final_url: str | None
        _validate_archive_resource_url(url)
        source_kind = "offline-manifest" if offline_repository is not None else "official-archive"
        if offline_repository is not None:
            source_database = offline_repository / relative_path
            if _is_reparse_or_symlink(source_database) or not source_database.is_file():
                raise BuildContractError(f"offline pinned database is missing or linked: {relative_name}")
            database.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_database, database)
            source_signature = source_database.with_name(source_database.name + ".sig")
            has_signature = source_signature.exists()
            if has_signature:
                if _is_reparse_or_symlink(source_signature) or not source_signature.is_file():
                    raise BuildContractError(f"offline database signature is linked or non-regular: {relative_name}.sig")
                shutil.copyfile(source_signature, signature)
            database_final_url = None
            signature_final_url = None
        else:
            fetched_database_url = _fetch_snapshot_resource(url, database, optional=False, runner=runner)
            if fetched_database_url is None:
                raise BuildContractError(f"required snapshot database returned not-found: {url}")
            database_final_url = fetched_database_url
            signature_final_url = _fetch_snapshot_resource(signature_url, signature, optional=True, runner=runner)
            has_signature = signature_final_url is not None
        actual_digest = _sha256_file(database)
        if actual_digest != pins[repository]:
            raise BuildContractError(
                f"snapshot {repository}.db bytes do not match the locked SHA-256: expected {pins[repository]}, got {actual_digest}"
            )
        if has_signature:
            signature_digest = _sha256_file(signature)
            verification = _verify_pacman_signature(signature, database, runner=runner, label=f"{repository}.db")
            signature_status = "PRESENT_AND_VERIFIED"
        else:
            signature_digest = None
            verification = "ABSENT; accepted only because DatabaseOptional is locked and database SHA-256 matches"
            signature_status = "ABSENT_DATABASEOPTIONAL"
        evidence[repository] = {
            "url": url,
            "final_url": database_final_url,
            "source_kind": source_kind,
            "source_relative_path": relative_name,
            "pacman_server_path": (database_root / repository / "os" / "x86_64").relative_to(work_dir).as_posix(),
            "sha256": actual_digest,
            "signature_url": signature_url,
            "signature_final_url": signature_final_url,
            "signature_present": has_signature,
            "signature_sha256": signature_digest,
            "signature_status": signature_status,
            "signature_verification_output": verification,
        }
    return database_root, evidence


def _verify_staged_repository_databases(
    database_root: Path,
    evidence: Mapping[str, Mapping[str, Any]],
    *,
    runner: Runner,
) -> None:
    for repository in SNAPSHOT_REPOSITORIES:
        relative_path = Path(SNAPSHOT_DATABASE_RELATIVE_PATHS[repository])
        database = database_root / relative_path
        expected = evidence[repository]
        if _is_reparse_or_symlink(database) or not database.is_file() or _sha256_file(database) != expected["sha256"]:
            raise BuildContractError(f"staged {repository}.db changed before/after pacman use")
        signature = database.with_name(database.name + ".sig")
        signature_present = bool(expected["signature_present"])
        if signature_present:
            if (
                _is_reparse_or_symlink(signature)
                or not signature.is_file()
                or _sha256_file(signature) != expected["signature_sha256"]
            ):
                raise BuildContractError(f"staged {repository}.db signature changed before/after pacman use")
            _verify_pacman_signature(signature, database, runner=runner, label=f"{repository}.db")
        elif signature.exists() or _is_reparse_or_symlink(signature):
            raise BuildContractError(f"staged {repository}.db signature appeared after optional-signature check")


def _pacman_sync_database_evidence(
    target_root: Path,
    work_dir: Path,
    pacman_conf: Path,
    database_root: Path,
    database_evidence: Mapping[str, Mapping[str, Any]],
    expected_config_sha256: str,
) -> dict[str, Any]:
    """Capture retained target sync DBs or state the pinned Archiso source invariant."""
    if _is_reparse_or_symlink(pacman_conf) or not pacman_conf.is_file():
        raise BuildContractError("mkarchiso pacman config is missing or linked after execution")
    config_sha256 = _sha256_file(pacman_conf)
    if config_sha256 != expected_config_sha256:
        raise BuildContractError("mkarchiso pacman config changed during execution")
    config_text = pacman_conf.read_text(encoding="utf-8")
    expected_server = "Server = " + database_root.as_uri() + "/$repo/os/$arch"
    actual_servers = [
        line.strip()
        for line in config_text.splitlines()
        if re.match(r"(?i)^\s*Server\s*=", line)
    ]
    if actual_servers != [expected_server, expected_server]:
        raise BuildContractError("effective pacman config no longer binds both sync databases to the staged pinned path")
    if not _is_within(pacman_conf.resolve(), work_dir.resolve()) or not _is_within(database_root.resolve(), work_dir.resolve()):
        raise BuildContractError("pacman config or sync database root escaped the owned build work directory")

    staged: dict[str, dict[str, Any]] = {}
    for repository in SNAPSHOT_REPOSITORIES:
        relative = Path(SNAPSHOT_DATABASE_RELATIVE_PATHS[repository])
        path = database_root / relative
        if _is_reparse_or_symlink(path) or not path.is_file():
            raise BuildContractError(f"staged {repository}.db is unavailable for consumption evidence")
        digest = _sha256_file(path)
        expected_digest = database_evidence[repository].get("sha256")
        if digest != expected_digest:
            raise BuildContractError(f"staged {repository}.db differs from validated input after mkarchiso")
        staged[repository] = {
            "path_relative_to_work": path.relative_to(work_dir).as_posix(),
            "sha256_before_and_after": digest,
            "configured_server": expected_server,
        }

    sync_directory = target_root / "var" / "lib" / "pacman" / "sync"
    captured: dict[str, dict[str, str]] = {}
    if _is_reparse_or_symlink(sync_directory):
        raise BuildContractError("target pacman sync directory is linked")
    if sync_directory.is_dir():
        for path in sync_directory.glob("*.db"):
            if _is_reparse_or_symlink(path) or not path.is_file():
                raise BuildContractError("target pacman sync DB is linked or non-regular")
            captured[path.name] = {"path": path.relative_to(target_root).as_posix(), "sha256": _sha256_file(path)}
    expected_names = {"core.db", "extra.db"}
    if captured and set(captured) != expected_names:
        raise BuildContractError("target root retained an incomplete/unexpected repository sync DB set")
    for repository in SNAPSHOT_REPOSITORIES:
        retained = captured.get(repository + ".db")
        if retained is not None and retained["sha256"] != database_evidence[repository].get("sha256"):
            raise BuildContractError(f"target root retained a different {repository}.db than the pinned staged input")
    runtime_capture = bool(captured)
    return {
        "status": "RUNTIME_SYNC_DATABASES_HASH_CAPTURED" if runtime_capture else "SOURCE_LEVEL_INVARIANT_ONLY",
        "runtime_capture": captured if runtime_capture else None,
        "runtime_capture_absence_reason": None if runtime_capture else "Archiso v91 successful cleanup removes target var/lib/pacman/sync",
        "effective_pacman_config_sha256": config_sha256,
        "effective_pacman_config_relative_to_work": pacman_conf.relative_to(work_dir).as_posix(),
        "staged_sync_databases": staged,
        "archiso_source_contract": {
            "package_version": EXPECTED_MKARCHISO_VERSION,
            "source_commit": ARCHISO_V91_SOURCE_COMMIT,
            "source_url": ARCHISO_V91_SOURCE_URL,
            "config_transform_lines": "284-286",
            "pacstrap_config_argument_lines": "320-345",
            "target_sync_database_cleanup_lines": "153-162",
            "source_invariant_tested": True,
            "contract": (
                "For pinned Archiso 91-1 source commit 723da192226e1c9d5def9168f13332793c82a85e, "
                "mkarchiso -C feeds _make_pacman_conf, which derives the work pacman config from the supplied config; "
                "_make_packages invokes pacstrap -C with that work config; successful cleanup removes the target sync DB directory. "
                "The exact staged DB SHA-256 values and effective config are checked before and after mkarchiso. "
                "When the target sync DBs are not retained, this is source-level evidence of the configured inputs, "
                "not a runtime capture of pacman's internal bytes."
            ),
        },
    }


def _verify_offline_package_signatures(repository: Path, runner: Runner) -> dict[str, str]:
    verified: dict[str, str] = {}
    for archive in sorted(repository.rglob("*.pkg.tar.zst")):
        if _is_reparse_or_symlink(archive):
            raise BuildContractError(f"offline package archive is linked: {archive}")
        signature = archive.with_name(archive.name + ".sig")
        output = _verify_pacman_signature(signature, archive, runner=runner, label=f"offline package {archive.name}")
        verified[archive.relative_to(repository).as_posix()] = output
    return verified


def _mountpoints_from_proc() -> tuple[str, ...]:
    mountinfo = Path("/proc/self/mountinfo")
    if not mountinfo.is_file():
        raise BuildContractError("cannot inspect Linux mount table before cleanup; retaining temporary work")
    try:
        lines = mountinfo.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise BuildContractError("cannot inspect Linux mount table before cleanup; retaining temporary work") from exc
    values = []
    for line in lines:
        fields = line.split()
        if len(fields) >= 5:
            values.append(fields[4].replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\"))
    return tuple(values)


def cleanup_owned_workdir(
    work_dir: Path,
    work_parent: Path,
    owner_token: str,
    *,
    mountpoints: Sequence[str],
) -> None:
    """Remove only our marked direct child after a fresh containment/mount check."""
    parent = _canonical_existing_dir(work_parent, "work parent")
    candidate = Path(os.path.abspath(work_dir))
    _assert_no_link_components(candidate)
    if candidate.parent.resolve(strict=True) != parent or candidate.name != WORK_PREFIX + owner_token:
        raise BuildContractError("refusing cleanup: work directory is outside its owned parent/prefix")
    if candidate.resolve(strict=True) != candidate:
        raise BuildContractError("refusing cleanup: work directory canonical path changed")
    marker = candidate / ".owner"
    if _is_reparse_or_symlink(marker) or not marker.is_file():
        raise BuildContractError("refusing cleanup: work directory ownership marker is missing or linked")
    if marker.read_text(encoding="ascii") != owner_token:
        raise BuildContractError("refusing cleanup: ownership token changed")
    prefix = str(candidate) + os.sep
    for mount in mountpoints:
        canonical_mount = os.path.normpath(mount)
        if canonical_mount == str(candidate) or canonical_mount.startswith(prefix):
            raise BuildContractError(f"refusing cleanup: a mount is present inside the work tree: {mount}")
    # shutil.rmtree uses an fd-based safe implementation on supported Linux builds.
    shutil.rmtree(candidate)


def _profile_copy(profile: Path, temp_root: Path) -> Path:
    stage_profile = temp_root / "profile"
    shutil.copytree(profile, stage_profile, symlinks=False)
    return stage_profile


def _one_wheel(wheel_dir: Path) -> Path:
    wheels = [path for path in wheel_dir.iterdir() if path.is_file() and path.suffix == ".whl" and not _is_reparse_or_symlink(path)]
    if len(wheels) != 1:
        raise BuildContractError(f"expected exactly one locally built application wheel, found {len(wheels)}")
    if "libre_core_autoloader" not in wheels[0].name.casefold():
        raise BuildContractError("built wheel filename does not identify the AutoLoader application")
    return wheels[0]


def _record_package_versions(target_root: Path, runner: Runner, required_packages: Sequence[str]) -> tuple[str, ...]:
    try:
        root = _canonical_existing_dir(target_root, "target root filesystem")
    except BuildContractError as exc:
        raise BuildContractError(
            "mkarchiso completed without a safe target root filesystem; package provenance is unavailable"
        ) from exc

    db_path = root / "var" / "lib" / "pacman"
    local_db = db_path / "local"
    try:
        canonical_db_path = _canonical_existing_dir(db_path, "target root pacman database directory")
        canonical_local_db = _canonical_existing_dir(local_db, "target root local pacman database")
    except BuildContractError as exc:
        raise BuildContractError(f"target root local pacman database is missing or unsafe: {exc}") from exc
    if not _is_within(canonical_db_path, root) or not _is_within(canonical_local_db, root):
        raise BuildContractError("target root local pacman database escapes the image root")

    database_entries = tuple(canonical_local_db.iterdir())
    if not database_entries:
        raise BuildContractError("target root local pacman database is empty")
    version_marker = canonical_local_db / "ALPM_DB_VERSION"
    _assert_no_link_components(version_marker)
    if _is_reparse_or_symlink(version_marker) or not version_marker.is_file():
        raise BuildContractError("target root local pacman database has no regular ALPM_DB_VERSION marker")
    try:
        local_db_version = version_marker.read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise BuildContractError("target root local pacman database has an unreadable ALPM_DB_VERSION marker") from exc
    if local_db_version != EXPECTED_ALPM_LOCAL_DB_VERSION:
        raise BuildContractError("target root local pacman database has an unsupported ALPM_DB_VERSION marker")
    for entry in database_entries:
        if entry == version_marker:
            continue
        if _is_reparse_or_symlink(entry) or not entry.is_dir():
            raise BuildContractError("target root local pacman database contains an unsafe package record")
        description = entry / "desc"
        _assert_no_link_components(description)
        if not description.is_file() or _is_reparse_or_symlink(description):
            raise BuildContractError("target root local pacman database package record has no regular desc file")

    command = ("pacman", "--config", "/dev/null", "--dbpath", str(canonical_db_path), "-Q")
    result = _run_command(command, runner=runner)
    return _parse_package_inventory(result.stdout, label="installed package version inventory", required=required_packages)


def _require_rootfs_file(root: Path, relative: str, label: str, *, executable: bool = False, symlink_ok: bool = False) -> Path:
    path = root.joinpath(*Path(relative).parts)
    if symlink_ok:
        resolved = _resolve_rootfs_symlink(root, path, label)
        try:
            info = resolved.lstat()
        except OSError as exc:
            raise BuildContractError(f"target root is missing {label}: {relative}") from exc
        if not stat.S_ISREG(info.st_mode):
            raise BuildContractError(f"target root {label} does not resolve to an in-image regular file")
        if executable and sys.platform == "linux" and not (info.st_mode & 0o111):
            raise BuildContractError(f"target root {label} symlink target is not executable")
        return resolved

    _assert_no_link_components(path, include_leaf=False)
    try:
        info = path.lstat()
    except OSError as exc:
        raise BuildContractError(f"target root is missing {label}: {relative}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise BuildContractError(f"target root {label} must be a regular file, not a symlink")
    if not stat.S_ISREG(info.st_mode):
        raise BuildContractError(f"target root {label} must be a regular file")
    if executable and sys.platform == "linux" and not (info.st_mode & 0o111):
        raise BuildContractError(f"target root {label} is not executable")
    return path


def _require_rootfs_symlink(root: Path, relative: str, label: str, expected_target: str) -> Path:
    """Require one exact symlink in the image without resolving it on the host."""
    path = root.joinpath(*Path(relative).parts)
    _assert_no_link_components(path, include_leaf=False)
    try:
        info = path.lstat()
    except OSError as exc:
        raise BuildContractError(f"target root is missing {label} symlink: {relative}") from exc
    if not stat.S_ISLNK(info.st_mode):
        raise BuildContractError(f"target root {label} must be a symlink: {relative}")
    try:
        actual_target = os.readlink(path)
    except OSError as exc:
        raise BuildContractError(f"target root {label} symlink cannot be read: {relative}") from exc
    if actual_target != expected_target:
        raise BuildContractError(
            f"target root {label} symlink target is unexpected: {relative} -> {actual_target!r}"
        )
    return path


def _resolve_rootfs_symlink(root: Path, link_path: Path, label: str) -> Path:
    """Resolve a target-root POSIX path without following links on the host."""
    root_path = Path(os.path.abspath(root))
    requested_path = Path(os.path.abspath(link_path))
    _assert_no_link_components(root_path, include_leaf=True)
    try:
        relative = requested_path.relative_to(root_path)
    except ValueError as exc:
        raise BuildContractError(f"target root {label} symlink escapes the image") from exc

    pending = list(relative.parts)
    resolved_parts: list[str] = []
    seen_states: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
    symlink_count = 0
    while pending:
        part = pending.pop(0)
        if part in {"", "."}:
            continue
        if part == "..":
            if not resolved_parts:
                raise BuildContractError(f"target root {label} symlink escapes the image")
            resolved_parts.pop()
            continue

        candidate = root_path.joinpath(*resolved_parts, part)
        try:
            info = candidate.lstat()
        except OSError as exc:
            raise BuildContractError(f"target root {label} symlink target is missing: {candidate}") from exc

        if stat.S_ISLNK(info.st_mode):
            state = (tuple((*resolved_parts, part)), tuple(pending))
            if state in seen_states:
                raise BuildContractError(f"target root {label} has a symlink cycle")
            seen_states.add(state)
            symlink_count += 1
            if symlink_count > 40:
                raise BuildContractError(f"target root {label} exceeds the symlink resolution limit")

            link_value = os.readlink(candidate)
            if link_value.startswith("/"):
                resolved_parts = []
            target_parts = [item for item in link_value.split("/") if item not in {"", "."}]
            pending = target_parts + pending
            continue

        if pending and not stat.S_ISDIR(info.st_mode):
            raise BuildContractError(
                f"target root {label} symlink path traverses a non-directory component: {candidate}"
            )
        resolved_parts.append(part)

    resolved_path = root_path.joinpath(*resolved_parts)
    try:
        resolved_path.relative_to(root_path)
    except ValueError as exc:
        raise BuildContractError(f"target root {label} symlink escapes the image") from exc
    return resolved_path


def _rootfs_runtime_validation_script() -> str:
    imports = ", ".join(repr(name) for name in REQUIRED_RUNTIME_IMPORTS)
    return (
        "import hashlib, importlib, importlib.metadata, json, pathlib, sys\n"
        "from click.testing import CliRunner\n"
        f"required = ({imports},)\n"
        "modules = {name: importlib.import_module(name) for name in required}\n"
        "cli_module = importlib.import_module('autoloader.__main__')\n"
        "if not hasattr(cli_module, 'main'): raise SystemExit('CLI main entrypoint missing')\n"
        f"help_result = CliRunner().invoke(cli_module.cli, ['--help'], prog_name='autoloader', terminal_width={TARGET_ROOT_CLI_HELP_WIDTH})\n"
        "if help_result.exit_code != 0 or not help_result.output.startswith('Usage: autoloader '): raise SystemExit('Click CLI help validation failed')\n"
        "console_help_sha256 = hashlib.sha256(help_result.output.encode('utf-8')).hexdigest()\n"
        "dist = importlib.metadata.distribution('libre-core-autoloader')\n"
        "if dist.metadata.get('Name', '').casefold().replace('_', '-') != 'libre-core-autoloader': raise SystemExit('wrong distribution name')\n"
        "entries = [ep.value for ep in dist.entry_points if ep.group == 'console_scripts' and ep.name == 'autoloader']\n"
        "if entries != ['autoloader.__main__:main']: raise SystemExit('AutoLoader console entrypoint missing or ambiguous')\n"
        "if not str(pathlib.Path(modules['autoloader'].__file__).resolve()).startswith(str(pathlib.Path(sys.prefix).resolve()) + '/'):\n"
        "    raise SystemExit('AutoLoader package is not installed in the application venv')\n"
        "wheelhouse = pathlib.Path('/opt/autoloader/wheelhouse')\n"
        "wheels = list(wheelhouse.glob('*.whl'))\n"
        "if len(wheels) != 1: raise SystemExit('installed wheelhouse is missing or ambiguous')\n"
        "wheel_hash = hashlib.sha256(wheels[0].read_bytes()).hexdigest()\n"
        "if wheel_hash != sys.argv[1]: raise SystemExit('installed wheel fingerprint mismatch')\n"
        "print(json.dumps({'distribution_name': dist.metadata['Name'], 'distribution_version': dist.version, "
        "'entrypoint': entries[0], 'runtime_imports': sorted(modules), 'wheel_sha256': wheel_hash, "
        "'python_version': sys.version.split()[0], 'console_help_sha256': console_help_sha256, "
        f"'console_help_terminal_width': {TARGET_ROOT_CLI_HELP_WIDTH}" + "}, sort_keys=True))\n"
    )


def _strip_expected_arch_chroot_diagnostics(stdout: str, target_root: Path) -> str:
    """Remove only the bounded wrapper emitted by pinned arch-chroot -N.

    The non-root ``-N`` path warns that its target root is not a mountpoint and
    reports the expected resolv.conf unmount as not mounted. Both are emitted
    on stdout with stderr merged by :func:`_run_command`, around the command's
    actual output. Require exactly one warning before the payload and exactly
    one unmount note after it. Keep every payload byte so the caller's output
    contract stays strict.
    """
    warning = f"==> WARNING: {target_root} is not a mountpoint. This may have undesirable side effects."
    unmount_note = f"umount: {target_root}/etc/resolv.conf: not mounted."
    lines = stdout.splitlines(keepends=True)
    contents = [line.rstrip("\r\n") for line in lines]
    nonempty = [index for index, content in enumerate(contents) if content]
    warning_indices = [index for index, content in enumerate(contents) if content == warning]
    unmount_indices = [index for index, content in enumerate(contents) if content == unmount_note]
    if (
        len(warning_indices) != 1
        or len(unmount_indices) != 1
        or not nonempty
        or warning_indices[0] != nonempty[0]
        or unmount_indices[0] != nonempty[-1]
        or warning_indices[0] >= unmount_indices[0]
    ):
        raise BuildContractError("arch-chroot -N output did not match the pinned diagnostic wrapper contract")
    return "".join(
        line
        for index, line in enumerate(lines)
        if index not in {warning_indices[0], unmount_indices[0]}
    )


def _validate_target_root_install(
    target_root: Path,
    *,
    wheel: Path,
    runner: Runner,
) -> dict[str, Any]:
    """Require the customize hook's verified app install and active service in airootfs."""
    _require_rootfs_file(
        target_root,
        "opt/autoloader/venv/bin/python",
        "application venv interpreter",
        executable=True,
        symlink_ok=True,
    )
    _require_rootfs_file(
        target_root,
        "opt/autoloader/venv/bin/autoloader",
        "AutoLoader console entrypoint",
        executable=True,
    )
    _require_rootfs_file(target_root, "opt/autoloader/venv/pyvenv.cfg", "application venv configuration")
    site_packages = list((target_root / "opt/autoloader/venv/lib").glob("python*/site-packages"))
    if len(site_packages) != 1 or _is_reparse_or_symlink(site_packages[0]) or not site_packages[0].is_dir():
        raise BuildContractError("target root does not contain exactly one regular application venv site-packages directory")
    package_file = site_packages[0] / "autoloader" / "__init__.py"
    _require_rootfs_file(target_root, package_file.relative_to(target_root).as_posix(), "installed AutoLoader package")
    dist_infos = list(site_packages[0].glob("libre_core_autoloader-*.dist-info"))
    if len(dist_infos) != 1 or _is_reparse_or_symlink(dist_infos[0]) or not dist_infos[0].is_dir():
        raise BuildContractError("target root must contain exactly one AutoLoader dist-info directory")
    metadata_path = dist_infos[0] / "METADATA"
    entry_points_path = dist_infos[0] / "entry_points.txt"
    _require_rootfs_file(target_root, metadata_path.relative_to(target_root).as_posix(), "AutoLoader distribution metadata")
    _require_rootfs_file(target_root, entry_points_path.relative_to(target_root).as_posix(), "AutoLoader entry-point metadata")
    metadata_text = metadata_path.read_text(encoding="utf-8")
    entry_points_text = entry_points_path.read_text(encoding="utf-8")
    if re.search(r"(?mi)^Name:\s*libre-core-autoloader\s*$", metadata_text) is None:
        raise BuildContractError("target root AutoLoader distribution metadata has the wrong package name")
    if re.search(r"(?m)^autoloader\s*=\s*autoloader\.__main__:main\s*$", entry_points_text) is None:
        raise BuildContractError("target root AutoLoader distribution metadata lacks the expected CLI entrypoint")

    service_path = _require_rootfs_file(
        target_root,
        "etc/systemd/system/autoloader.service",
        "AutoLoader systemd unit",
    )
    activation_path = _require_rootfs_file(
        target_root,
        "etc/systemd/system/multi-user.target.d/autoloader.conf",
        "AutoLoader systemd activation drop-in",
    )
    service_bytes = service_path.read_bytes()
    service = service_bytes.decode("utf-8")
    activation = activation_path.read_text(encoding="utf-8")
    if _sha256_bytes(service_bytes) != AUTOLOADER_SERVICE_SHA256:
        raise BuildContractError("target root AutoLoader unit differs from the exact reviewed source bytes")
    _validate_service_network_policy(service, label="target-root AutoLoader service")
    _reject_higher_priority_autoloader_unit_files(target_root)
    _reject_service_network_policy_dropins(target_root)
    required_service_lines = (
        "ExecStart=/opt/autoloader/start.sh",
        "After=getty@tty1.service",
        "Conflicts=getty@tty1.service",
        "Restart=no",
        "StandardOutput=journal+console",
    )
    if any(line not in service.splitlines() for line in required_service_lines):
        raise BuildContractError("target root AutoLoader systemd unit is incomplete or does not own tty1 safely")
    if activation.strip() != "[Unit]\nWants=autoloader.service":
        raise BuildContractError("target root AutoLoader service is not enabled from multi-user.target")
    systemd_links: dict[str, Any]
    if sys.platform == "linux":
        enablement_link = _require_rootfs_symlink(
            target_root,
            "etc/systemd/system/multi-user.target.wants/autoloader.service",
            "AutoLoader multi-user enablement",
            "../autoloader.service",
        )
        network_mask_links: dict[str, dict[str, str]] = {}
        for unit in NETWORK_DISABLED_SYSTEMD_UNITS:
            relative = f"etc/systemd/system/{unit}"
            link = _require_rootfs_symlink(
                target_root,
                relative,
                f"network activation mask for {unit}",
                "/dev/null",
            )
            network_mask_links[unit] = {"path": relative, "target": os.readlink(link)}
        systemd_links = {
            "status": "CHECKED",
            "autoloader_enablement": {
                "path": "etc/systemd/system/multi-user.target.wants/autoloader.service",
                "target": os.readlink(enablement_link),
            },
            "network_activation_masks": network_mask_links,
        }
    else:
        # Candidate production builds are Linux-only. Windows-hosted fake
        # runners cannot create POSIX symlinks, so state this limitation in
        # their synthetic manifest rather than fabricating checked evidence.
        systemd_links = {"status": "NOT_CHECKED_NON_LINUX_TEST_HOST"}
    start_script_path = _require_rootfs_file(
        target_root,
        "opt/autoloader/start.sh",
        "AutoLoader startup script",
        executable=True,
    )
    marker_path = _require_rootfs_file(
        target_root,
        "var/lib/autoloader/image-install.json",
        "customize-hook install marker",
    )
    marker, _ = _load_json(marker_path, "customize-hook install marker")
    expected_wheel_sha = _sha256_file(wheel)
    marker_distribution = marker.get("distribution_name")
    if (
        marker.get("schema_version") != 1
        or marker.get("status") != "APP_INSTALLED_AND_RUNTIME_CHECKED"
        or not isinstance(marker_distribution, str)
        or marker_distribution.casefold().replace("_", "-") != "libre-core-autoloader"
        or marker.get("entrypoint") != "autoloader.__main__:main"
        or marker.get("wheel_sha256") != expected_wheel_sha
    ):
        raise BuildContractError("target root lacks valid customize-hook app installation evidence")

    runtime = _run_command(
        (
            "arch-chroot",
            "-N",
            str(target_root),
            "/opt/autoloader/venv/bin/python",
            "-c",
            _rootfs_runtime_validation_script(),
            expected_wheel_sha,
        ),
        runner=runner,
    )
    runtime_output = _strip_expected_arch_chroot_diagnostics(runtime.stdout, target_root)
    try:
        runtime_document = json.loads(runtime_output.strip())
    except (json.JSONDecodeError, AttributeError) as exc:
        raise BuildContractError("target root runtime import check returned no parseable evidence") from exc
    runtime_distribution = runtime_document.get("distribution_name")
    if (
        not isinstance(runtime_distribution, str)
        or runtime_distribution.casefold().replace("_", "-") != "libre-core-autoloader"
        or runtime_document.get("entrypoint") != "autoloader.__main__:main"
        or tuple(runtime_document.get("runtime_imports", ())) != tuple(sorted(REQUIRED_RUNTIME_IMPORTS))
        or runtime_document.get("wheel_sha256") != expected_wheel_sha
        or type(runtime_document.get("console_help_terminal_width")) is not int
        or runtime_document.get("console_help_terminal_width") != TARGET_ROOT_CLI_HELP_WIDTH
        or not isinstance(runtime_document.get("console_help_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", runtime_document["console_help_sha256"]) is None
    ):
        raise BuildContractError("target root runtime import evidence does not match the application wheel contract")
    cli = _run_command(
        ("arch-chroot", "-N", str(target_root), "/opt/autoloader/venv/bin/autoloader", "--help"),
        runner=runner,
        # Keep COLUMNS unset: Click's non-TTY fallback is 80 terminal columns,
        # which yields the same complete help text as CliRunner width 78 after
        # Click's help formatter reserves its two-column margin.
        env={},
    )
    cli_output = _strip_expected_arch_chroot_diagnostics(cli.stdout, target_root)
    if (
        not cli_output.startswith("Usage: autoloader ")
        or _sha256_bytes(cli_output.encode("utf-8")) != runtime_document["console_help_sha256"]
    ):
        raise BuildContractError("target root AutoLoader console command output differs from the installed Click help contract")
    return {
        "status": "APP_INSTALLED_AND_RUNTIME_CHECKED",
        "customize_hook_marker_sha256": _sha256_file(marker_path),
        "wheel_sha256": expected_wheel_sha,
        "distribution_metadata_sha256": _sha256_file(metadata_path),
        "entrypoint_metadata_sha256": _sha256_file(entry_points_path),
        "service_unit_sha256": _sha256_file(service_path),
        "service_network_policy": dict(AUTOLOADER_NETWORK_POLICY),
        "service_activation_sha256": _sha256_file(activation_path),
        "startup_script_sha256": _sha256_file(start_script_path),
        "runtime_smoke": runtime_document,
        "systemd_links": systemd_links,
        "console_help_terminal_width": TARGET_ROOT_CLI_HELP_WIDTH,
        "console_help_sha256": _sha256_bytes(cli_output.encode("utf-8")),
    }


def execute_build(
    config: BuildConfig,
    *,
    host_platform: str | None = None,
    mkarchiso_path: str | None = None,
    unshare_path: str | None = None,
    host_namespace_identity: str | None = None,
    host_mount_table_reader: Callable[[], bytes] | None = None,
    mountpoints_reader: Callable[[], Sequence[str]] = _mountpoints_from_proc,
    runner: Runner = subprocess.run,
    clock: Callable[[], float] = time.time,
) -> BuildResult:
    """Execute a reviewed build contract. Production calls use real subprocesses."""
    ready = preflight(config, host_platform=host_platform)
    rootless_host: RootlessHostEvidence | None = None
    rootless_mount: dict[str, Any] | None = None
    permitted_paths: set[str] = {ROOTLESS_SYSTEM_PATH}
    if runner is subprocess.run:
        rootless_host = _rootless_archiso_host_preflight(runner=runner)
        rootless_mount = _rootless_archiso_mount_preflight(
            rootless_host,
            runner=runner,
            host_mount_table_reader=host_mount_table_reader,
        )
        _assert_rootless_process_state(rootless_host)
        if config.network_mode == "offline":
            tool_map = {tool.name: tool for tool in rootless_host.trusted_tools}
            try:
                rootless_offline_preflight.preflight(
                    host=rootless_host,
                    mount=rootless_mount,
                    tool_pins={
                        "pacstrap": {
                            "path": tool_map["pacstrap"].path,
                            "sha256": tool_map["pacstrap"].sha256,
                            "version": rootless_host.arch_install_scripts_package_version,
                        },
                        "arch-chroot": {
                            "path": tool_map["arch-chroot"].path,
                            "sha256": tool_map["arch-chroot"].sha256,
                            "version": rootless_host.arch_install_scripts_package_version,
                        },
                    },
                    scratch_parent=Path(tempfile.gettempdir()),
                    runner=runner,
                )
            except rootless_offline_preflight.OfflineLauncherPreflightError as exc:
                # This is strictly a no-build probe; the missing fixed
                # launcher pin keeps offline execution closed before builder
                # work/output creation or package/build commands.
                raise BuildContractError(str(exc)) from exc
            # The installed-policy contract currently authorizes only the
            # no-build preflight. Even if a future deployment makes that probe
            # return SUPPORTED, there is no reviewed fixed build-execution
            # protocol yet. Never fall through to the online runner here.
            raise BuildContractError(
                "OFFLINE_ROOTLESS_BUILD_RUNNER_UNAVAILABLE: no reviewed fixed offline build runner is deployed"
            )
    source = config.source_root.resolve(strict=True)
    profile = config.profile_dir.resolve(strict=True)
    output = config.output_dir.resolve(strict=True)
    work_parent = config.work_parent.resolve(strict=True)
    # Both build modes require a one-shot trusted mount-capability preflight
    # before creating a work directory. Online mode uses the isolated probe
    # only for this check; its later build subprocesses retain requested egress.
    if rootless_host is not None and rootless_mount is not None:
        network_isolation = _rootless_online_network_isolation(rootless_host, rootless_mount)
        permitted_paths = {ROOTLESS_SYSTEM_PATH}
        command_log = [list(network_isolation.mount_probe_command)]
        build_runner = _rootless_online_runner(
            runner,
            rootless_host,
            command_log=command_log,
            permitted_paths=permitted_paths,
        )
    else:
        # Injected runners remain a test adapter. Production online execution
        # must use the checked nonroot route above; fake offline tests preserve
        # the existing isolated-command behavior.
        verified_preflight = _establish_offline_network_isolation(
            runner,
            unshare_path=unshare_path,
            host_namespace_identity=host_namespace_identity,
            host_mount_table_reader=host_mount_table_reader,
        )
        if config.network_mode == "offline":
            network_isolation = verified_preflight
        else:
            network_isolation = replace(
                verified_preflight,
                mode="online-allowed-after-isolated-preflight",
                command_prefix=(),
            )
        command_log = []
        for probe_command in (network_isolation.probe_command, network_isolation.mount_probe_command):
            if probe_command:
                command_log.append(list(probe_command))
        build_runner = _network_guarded_runner(runner, network_isolation, command_log=command_log)
    source_info = _source_provenance(source, build_runner)
    version = _run_command(("pacman", "-Q", "archiso"), runner=build_runner).stdout.strip()
    if version != f"archiso {EXPECTED_MKARCHISO_VERSION}":
        raise BuildContractError(
            f"installed Archiso version must be exactly {EXPECTED_MKARCHISO_VERSION}; got {version!r}"
        )
    mkarchiso_candidate = mkarchiso_path or shutil.which("mkarchiso")
    if not mkarchiso_candidate:
        raise BuildContractError("mkarchiso is not installed; install the pinned archiso package in a supported Linux environment")
    try:
        # Resolve trusted usrmerge aliases (for example /usr/sbin -> /usr/bin)
        # before regular-file validation and package ownership checks. Invoke
        # only this canonical path after ownership is bound to it.
        mkarchiso_resolved = Path(mkarchiso_candidate).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise BuildContractError("mkarchiso executable path cannot be resolved") from exc
    mkarchiso_file = _canonical_existing_file(mkarchiso_resolved, "mkarchiso executable")
    if sys.platform == "linux" and not (mkarchiso_file.stat().st_mode & 0o111):
        raise BuildContractError("mkarchiso canonical target is not executable")
    try:
        owner = _run_command(("pacman", "-Qo", str(mkarchiso_file)), runner=build_runner).stdout.strip()
    except (BuildContractError, OSError) as exc:
        raise BuildContractError(
            f"trusted pacman ownership evidence is unavailable for canonical mkarchiso path {mkarchiso_file}"
        ) from exc
    expected_owner = f"{mkarchiso_file} is owned by archiso {EXPECTED_MKARCHISO_VERSION}"
    if owner != expected_owner:
        raise BuildContractError(
            f"mkarchiso ownership output must exactly bind canonical path and pinned package version; "
            f"expected {expected_owner!r}, got {owner!r}"
        )
    mkarchiso_sha256 = _sha256_file(mkarchiso_file)
    host_inventory = _parse_package_inventory(
        _run_command(("pacman", "-Q"), runner=build_runner).stdout,
        label="host package inventory",
        required=("archiso",),
    )
    host_archiso = next(row for row in host_inventory if row.startswith("archiso "))
    if host_archiso != f"archiso {EXPECTED_MKARCHISO_VERSION}":
        raise BuildContractError(f"host package inventory does not contain pinned Archiso {EXPECTED_MKARCHISO_VERSION}")
    wheel_backend = _build_backend_evidence(build_runner)
    mkarchiso_bin = str(mkarchiso_file)

    owner_token = uuid.uuid4().hex
    work_dir = work_parent / (WORK_PREFIX + owner_token)
    work_dir.mkdir(mode=0o700)
    owner_fd = os.open(work_dir / ".owner", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(owner_fd, "w", encoding="ascii") as owner_file:
        owner_file.write(owner_token)
    build_log = work_dir / "build.log"
    keep_work = True
    start_epoch = int(clock())
    rootless_pacstrap_overlay: RootlessPacstrapOverlayEvidence | None = None
    try:
        if rootless_host is not None:
            rootless_pacstrap_overlay = _stage_rootless_pacstrap_overlay(work_dir)
            permitted_paths.add(rootless_pacstrap_overlay.child_path)
            _verify_rootless_pacstrap_overlay(rootless_pacstrap_overlay)
        stage_profile = _profile_copy(profile, work_dir)
        _audit_profile_line_endings(stage_profile, label="staged profile")
        wheel_source_dir = work_dir / "wheel-source"
        wheel_source_hashes = _copy_wheel_source(source, wheel_source_dir)
        wheel_dir = work_dir / "wheelhouse"
        wheel_dir.mkdir()
        build_env_values = {
            "PIP_NO_INDEX": "1",
            "PIP_CONFIG_FILE": os.devnull,
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PYTHONNOUSERSITE": "1",
        }
        build_env = (
            _rootless_child_environment(
                rootless_host,
                build_env_values,
                permitted_paths=permitted_paths,
            )
            if rootless_host is not None
            else dict(os.environ, **build_env_values)
        )
        wheel_command = (
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-index",
            "--no-deps",
            "--no-build-isolation",
            "--no-cache-dir",
            "--wheel-dir",
            str(wheel_dir),
            str(wheel_source_dir),
        )
        wheel_log = work_dir / "wheel-build.log"
        with wheel_log.open("w", encoding="utf-8", newline="\n") as log:
            _run_command(wheel_command, runner=build_runner, env=build_env, stdout=log)
        if _source_provenance(source, build_runner) != source_info:
            raise BuildContractError("source checkout changed during isolated wheel build; retaining build logs")
        wheel = _one_wheel(wheel_dir)
        _copy_wheel_into_profile(wheel, stage_profile)
        _copy_runbooks(source, stage_profile)
        _enable_autoloader_service(stage_profile)
        offline_repository_for_build: Path | None = None
        offline_repository_hashes: dict[str, str] = {}
        if ready.offline_repository is not None:
            if config.offline_manifest is None or ready.offline_manifest_sha256 is None:
                raise BuildContractError("offline repository manifest evidence is missing after preflight")
            if _sha256_file(config.offline_manifest) != ready.offline_manifest_sha256:
                raise BuildContractError("offline repository manifest changed after preflight")
            offline_repository_for_build, offline_repository_hashes = _copy_verified_offline_repository(
                ready.offline_repository,
                config.offline_manifest,
                ready.snapshot_sha256,
                ready.package_names,
                _snapshot_database_pins(ready.snapshot),
                work_dir / "offline-repository",
            )
        snapshot_database_root, database_evidence = _stage_repository_databases(
            ready.snapshot,
            work_dir,
            runner=build_runner,
            offline_repository=offline_repository_for_build,
        )
        offline_package_signatures: dict[str, str] = {}
        if offline_repository_for_build is not None:
            offline_package_signatures = _verify_offline_package_signatures(
                offline_repository_for_build, build_runner
            )
        pacman_conf = _install_snapshot_conf(
            stage_profile,
            snapshot_database_root,
            work_dir,
            offline_repository=offline_repository_for_build,
        )
        _verify_staged_repository_databases(snapshot_database_root, database_evidence, runner=build_runner)
        pacman_config_sha256_before = _sha256_file(pacman_conf)
        offline_image_evidence: dict[str, Any] | None = None
        if ready.offline_repository is not None:
            offline_image_evidence = {
                "manifest_sha256": ready.offline_manifest_sha256,
                "staged_file_sha256": dict(offline_repository_hashes),
                "package_signature_verification": dict(offline_package_signatures),
                "cache_server": "temporary verified local copy",
            }
        _write_image_input_provenance(
            source,
            stage_profile,
            snapshot_sha256=ready.snapshot_sha256,
            source_info=source_info,
            wheel=wheel,
            package_names=ready.package_names,
            repository_databases=database_evidence,
            offline_repository=offline_image_evidence,
        )

        if ready.offline_repository is not None:
            if config.offline_manifest is None:
                raise BuildContractError("offline manifest path is missing after preflight")
            if ready.offline_manifest_sha256 is None or _sha256_file(config.offline_manifest) != ready.offline_manifest_sha256:
                raise BuildContractError("offline repository manifest changed before mkarchiso")
            _validate_offline_repository(
                offline_repository_for_build or ready.offline_repository,
                config.offline_manifest,
                ready.snapshot_sha256,
                ready.package_names,
                _snapshot_database_pins(ready.snapshot),
            )
        archiso_work = work_dir / "archiso-work"
        archiso_work.mkdir()
        staged_image_output = work_dir / "image-output"
        staged_image_output.mkdir()
        command = (
            mkarchiso_bin,
            "-v",
            "-C",
            str(pacman_conf),
            "-w",
            str(archiso_work),
            "-o",
            str(staged_image_output),
            str(stage_profile),
        )
        if _sha256_file(pacman_conf) != pacman_config_sha256_before:
            raise BuildContractError("pacman config changed before mkarchiso invocation")
        _verify_staged_repository_databases(snapshot_database_root, database_evidence, runner=build_runner)
        _audit_profile_line_endings(profile, label="source profile")
        _audit_profile_line_endings(stage_profile, label="staged profile")
        _verify_archiso_initramfs_config(profile, label="source")
        _verify_archiso_initramfs_config(stage_profile, label="staged")
        _verify_usb_initramfs_derivative(profile, label="source")
        _verify_usb_initramfs_derivative(stage_profile, label="staged")
        mkarchiso_env: Mapping[str, str] | None = None
        if rootless_host is not None:
            if rootless_pacstrap_overlay is None:
                raise BuildContractError("ROOTLESS_PACSTRAP_STAGE_INVALID: production overlay was not staged")
            _verify_rootless_pacstrap_overlay(rootless_pacstrap_overlay)
            mkarchiso_env = dict(build_env)
            mkarchiso_env["PATH"] = rootless_pacstrap_overlay.child_path
        with build_log.open("w", encoding="utf-8", newline="\n") as log:
            try:
                if _sha256_file(mkarchiso_file) != mkarchiso_sha256:
                    raise BuildContractError("mkarchiso executable changed before invocation")
                _run_command(command, runner=build_runner, env=mkarchiso_env, stdout=log)
            except BuildContractError as exc:
                log.write(f"\nBUILD_FAILED: {exc}\n")
                raise
            finally:
                if rootless_pacstrap_overlay is not None:
                    _verify_rootless_pacstrap_overlay(rootless_pacstrap_overlay)
                if _sha256_file(mkarchiso_file) != mkarchiso_sha256:
                    raise BuildContractError("mkarchiso executable changed during invocation")

        _audit_profile_line_endings(profile, label="source profile")
        _audit_profile_line_endings(stage_profile, label="staged profile")
        _verify_archiso_initramfs_config(profile, label="source")
        _verify_archiso_initramfs_config(stage_profile, label="staged")
        _verify_usb_initramfs_derivative(profile, label="source")
        _verify_usb_initramfs_derivative(stage_profile, label="staged")

        _verify_staged_repository_databases(snapshot_database_root, database_evidence, runner=build_runner)
        if _sha256_file(pacman_conf) != pacman_config_sha256_before:
            raise BuildContractError("pacman config changed during mkarchiso execution")
        if ready.offline_repository is not None:
            assert config.offline_manifest is not None
            if ready.offline_manifest_sha256 is None or _sha256_file(config.offline_manifest) != ready.offline_manifest_sha256:
                raise BuildContractError("offline repository manifest changed during mkarchiso")
            _validate_offline_repository(
                offline_repository_for_build or ready.offline_repository,
                config.offline_manifest,
                ready.snapshot_sha256,
                ready.package_names,
                _snapshot_database_pins(ready.snapshot),
            )

        images = [p for p in staged_image_output.iterdir() if p.suffix.casefold() == ".iso"]
        if len(images) != 1 or _is_reparse_or_symlink(images[0]) or not images[0].is_file() or images[0].stat().st_size == 0:
            raise BuildContractError("mkarchiso did not produce exactly one non-empty regular ISO file")
        image = images[0]
        target_root = archiso_work / "x86_64" / "airootfs"
        pacman_database_consumption = _pacman_sync_database_evidence(
            target_root,
            work_dir,
            pacman_conf,
            snapshot_database_root,
            database_evidence,
            pacman_config_sha256_before,
        )
        target_install = _validate_target_root_install(
            target_root,
            wheel=wheel,
            runner=build_runner,
        )
        package_inventory = _record_package_versions(target_root, build_runner, ready.package_names)
        end_epoch = int(clock())
        profile_hashes = _source_hashes(source, profile)
        staged_hashes = _source_hashes(work_dir, stage_profile)
        staged_initramfs_key = f"profile/{Path(ARCHISO_INITRAMFS_CONFIG_RELATIVE_PATH).as_posix()}"
        if staged_hashes.get(staged_initramfs_key) != ARCHISO_V91_INITRAMFS_CONFIG_SHA256:
            raise BuildContractError("staged Archiso initramfs config hash is absent or differs from its pin")
        staged_usb_derivative_key = f"profile/{Path(ARCHISO_USB_INITRAMFS_DERIVATIVE_RELATIVE_PATH).as_posix()}"
        if staged_hashes.get(staged_usb_derivative_key) != ARCHISO_USB_INITRAMFS_DERIVATIVE_SHA256:
            raise BuildContractError("staged USB initramfs derivative hash is absent or differs from its pin")
        image_outputs: list[dict[str, Any]] = []
        for path in sorted(staged_image_output.iterdir()):
            if _is_reparse_or_symlink(path) or not path.is_file():
                raise BuildContractError(f"mkarchiso produced an unexpected non-regular output: {path}")
            image_outputs.append(
                {"filename": path.name, "sha256": _sha256_file(path), "size_bytes": path.stat().st_size}
            )
        wheel_log_sha256 = _sha256_file(wheel_log)
        build_log_sha256 = _sha256_file(build_log)
        provenance = {
            "schema_version": 1,
            "status": "CANDIDATE_ISO_BUILD_COMPLETED; NOT_RELEASE_VALIDATED",
            "release_validation_status": "PENDING_LINUX_QEMU_REVIEW",
            "built_at_utc": datetime.fromtimestamp(end_epoch, timezone.utc).isoformat(),
            "duration_seconds": max(0, end_epoch - start_epoch),
            "source": source_info,
            "snapshot": {
                "lock_sha256": ready.snapshot_sha256,
                "snapshot_date": ready.snapshot.get("snapshot_date"),
                "repository_base": ready.snapshot.get("repository_base"),
                "archiso_package": ready.snapshot.get("archiso_package"),
                "archiso_version": version.split()[-1],
                "snapshot_pin_status": ready.snapshot.get("snapshot_pin_status"),
                "release_validation_status": ready.snapshot.get("release_validation_status"),
                "package_signature_policy": ready.snapshot.get("package_signature_policy"),
                "signature_policy": {
                    "package_archives": "REQUIRED_AND_VERIFIED_BY_PACMAN",
                    "repository_databases": "OPTIONAL_DETACHED_SIGNATURE; IF_PRESENT_MUST_VERIFY; EXACT_LOCKED_SHA256_ALWAYS_REQUIRED",
                    "local_file_packages": "REQUIRED",
                },
                "repository_database_sha256": dict(_snapshot_database_pins(ready.snapshot)),
                "repository_databases": {name: dict(value) for name, value in database_evidence.items()},
                "archiso_source_commit": ARCHISO_V91_SOURCE_COMMIT,
                "archiso_initramfs_config_source_path": ARCHISO_V91_INITRAMFS_CONFIG_SOURCE_PATH,
                "archiso_initramfs_config_sha256": ready.snapshot.get("archiso_initramfs_config_sha256"),
                "archiso_initramfs_config_staged_path": ARCHISO_INITRAMFS_CONFIG_RELATIVE_PATH,
                "staged_archiso_initramfs_config_sha256": staged_hashes[staged_initramfs_key],
                "archiso_initramfs_hook_packages": {
                    hook: list(packages) for hook, packages in ARCHISO_INITRAMFS_HOOK_PACKAGES.items()
                },
                "archiso_initramfs_base_packages": list(ARCHISO_INITRAMFS_BASE_PACKAGES),
                "archiso_initramfs_required_packages": sorted(ARCHISO_INITRAMFS_REQUIRED_PACKAGES),
                "archiso_usb_initramfs_derivative": {
                    "id": ARCHISO_USB_INITRAMFS_DERIVATIVE_ID,
                    "source_path": ARCHISO_USB_INITRAMFS_DERIVATIVE_SOURCE_PATH,
                    "source_sha256": ARCHISO_USB_INITRAMFS_DERIVATIVE_SHA256,
                    "profile_path": ARCHISO_USB_INITRAMFS_DERIVATIVE_RELATIVE_PATH,
                    "staged_sha256": staged_hashes[staged_usb_derivative_key],
                    "removed_hook": ARCHISO_USB_INITRAMFS_REMOVED_HOOK,
                    "retained_hook_order": _usb_initramfs_hook_order(profile),
                    "compression_policy": "INHERIT_EXACT_PINNED_ARCHISO_FRAGMENT",
                },
                "packages_x86_64_sha256": ready.snapshot.get("packages_x86_64_sha256"),
            },
            "host": {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "package_inventory": list(host_inventory),
                "package_inventory_sha256": _sha256_bytes("\n".join(host_inventory).encode("utf-8")),
                "wheel_build_backend": wheel_backend,
                "mkarchiso": {
                    "path": str(mkarchiso_file),
                    "package": "archiso",
                    "package_version": EXPECTED_MKARCHISO_VERSION,
                    "package_owner_output": owner,
                    "sha256": mkarchiso_sha256,
                },
            },
            "network_isolation": {
                "mode": network_isolation.mode,
                "command_prefix": list(network_isolation.command_prefix),
                "privilege_mode": network_isolation.privilege_mode,
                "unshare_path": network_isolation.unshare_path,
                "unshare_sha256": network_isolation.unshare_sha256,
                "unshare_package_owner_output": network_isolation.unshare_package_owner_output,
                "iproute_path": network_isolation.iproute_path,
                "iproute_sha256": network_isolation.iproute_sha256,
                "iproute_package_owner_output": network_isolation.iproute_package_owner_output,
                "host_namespace_identity": network_isolation.host_namespace_identity,
                "host_user_namespace_identity": network_isolation.host_user_namespace_identity,
                "host_mount_namespace_identity": network_isolation.host_mount_namespace_identity,
                "host_pid_namespace_identity": network_isolation.host_pid_namespace_identity,
                "probe_command": list(network_isolation.probe_command),
                "probe_output": network_isolation.probe_output,
                "mount_probe_command": list(network_isolation.mount_probe_command),
                "mount_probe_output": network_isolation.mount_probe_output,
                "host_mount_table_sha256": network_isolation.host_mount_table_sha256,
            },
            "rootless_archiso_execution": (
                {
                    "status": "NONROOT_ONLINE_AFTER_ROUTE_FREE_MOUNT_PREFLIGHT",
                    "host_identity_and_pinned_tools": _rootless_host_manifest(rootless_host),
                    "mount_preflight": {
                        "status": rootless_mount["status"],
                        "command": list(rootless_mount["command"]),
                        "output": rootless_mount["output"],
                        "host_namespace_ids": dict(rootless_mount["host_namespace_ids"]),
                        "host_mount_table_sha256": rootless_mount["host_mount_table_sha256"],
                        "report": dict(rootless_mount["report"]),
                    },
                    "pacstrap_overlay": rootless_pacstrap_overlay.to_manifest(mkarchiso_argv=command)
                    if rootless_pacstrap_overlay is not None
                    else None,
                    "mkarchiso_environment_path": rootless_pacstrap_overlay.child_path
                    if rootless_pacstrap_overlay is not None
                    else None,
                    "execution_policy": "direct nonroot subprocess; online egress allowed only after the isolated read-only network and mount probes passed",
                }
                if rootless_host is not None and rootless_mount is not None
                else {"status": "SYNTHETIC_TEST_RUNNER_ONLY"}
            ),
            "profile_file_sha256": profile_hashes,
            "staged_profile_file_sha256": staged_hashes,
            "wheel_source_file_sha256": wheel_source_hashes,
            "packages_x86_64": list(ready.package_names),
            "offline_package_signature_verification": offline_package_signatures,
            "pacman_sync_database_consumption": pacman_database_consumption,
            "offline_repository": (
                {
                    "source_path": str(ready.offline_repository),
                    "manifest_path": str(config.offline_manifest),
                    "manifest_sha256": ready.offline_manifest_sha256,
                    "staged_cache_server_relative_path": "offline-repository/$repo/os/$arch",
                    "staged_copy_removed_with_owned_workdir": True,
                    "staged_file_sha256": dict(offline_repository_hashes),
                }
                if ready.offline_repository is not None and config.offline_manifest is not None
                else None
            ),
            "pacman_config_sha256": pacman_config_sha256_before,
            "installed_package_inventory": list(package_inventory),
            "target_root_install": target_install,
            "commands": command_log,
            "wheel": {"filename": wheel.name, "sha256": _sha256_file(wheel), "size_bytes": wheel.stat().st_size},
            "outputs": image_outputs,
            "logs": {
                "wheel_build_sha256": wheel_log_sha256,
                "mkarchiso_sha256": build_log_sha256,
            },
            "publication": {
                "mode": "ATOMIC_DIRECTORY_RENAME",
                "bundle_directory": "candidate-bundle",
                "checksum_file": "SHA256SUMS",
                "complete_before_visible": True,
            },
            "qemu_bios_boot": "NOT_PERFORMED",
            "qemu_uefi_boot": "NOT_PERFORMED",
            "usb_media_write": "NOT_PERFORMED",
        }
        # The staged copy is the input mkarchiso consumes. Check it again at
        # the publication boundary so provenance cannot outlive its bytes.
        _audit_profile_line_endings(profile, label="source profile")
        _audit_profile_line_endings(stage_profile, label="staged profile")
        _verify_archiso_initramfs_config(profile, label="source")
        final_staged_initramfs_hash = _verify_archiso_initramfs_config(stage_profile, label="staged")
        if final_staged_initramfs_hash != staged_hashes[staged_initramfs_key]:
            raise BuildContractError("staged Archiso initramfs config changed after provenance hashing")
        final_source_usb_hash = _verify_usb_initramfs_derivative(profile, label="source")
        final_staged_usb_hash = _verify_usb_initramfs_derivative(stage_profile, label="staged")
        if (
            final_source_usb_hash != ARCHISO_USB_INITRAMFS_DERIVATIVE_SHA256
            or final_staged_usb_hash != staged_hashes[staged_usb_derivative_key]
        ):
            raise BuildContractError("USB initramfs derivative changed after provenance hashing")
        if rootless_pacstrap_overlay is not None:
            _verify_rootless_pacstrap_overlay(rootless_pacstrap_overlay)
            if rootless_host is not None:
                _assert_rootless_process_state(rootless_host)
        if _sha256_file(mkarchiso_file) != mkarchiso_sha256:
            raise BuildContractError("mkarchiso executable changed before candidate publication")
        published_result = _publish_candidate_bundle(
            output,
            artifact_paths=tuple(sorted(staged_image_output.iterdir())),
            selected_iso=image,
            build_log=build_log,
            wheel_log=wheel_log,
            provenance=provenance,
            expected_artifact_sha256={item["filename"]: item["sha256"] for item in image_outputs},
            expected_build_log_sha256=build_log_sha256,
            expected_wheel_log_sha256=wheel_log_sha256,
        )
        keep_work = False
        return published_result
    except Exception:
        # Keep logs and intermediate evidence after any failure for inspection.
        if not build_log.exists():
            build_log.write_text("Build stopped before mkarchiso; inspect the reported preflight error.\n", encoding="utf-8")
        raise
    finally:
        if not keep_work:
            try:
                cleanup_owned_workdir(work_dir, work_parent, owner_token, mountpoints=mountpoints_reader())
            except (OSError, BuildContractError) as exc:
                print(f"Work directory retained because safe cleanup could not be proven: {work_dir}: {exc}", file=sys.stderr)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the Libre_Core AutoLoader Live USB through a fail-closed Archiso contract")
    parser.add_argument("--source-root", type=Path, required=True, help="Absolute clean Git checkout containing this project")
    parser.add_argument("--profile-dir", type=Path, required=True, help="Absolute reviewed archiso_profile directory")
    parser.add_argument("--snapshot-lock", type=Path, required=True, help="Absolute checked-in snapshot.lock.json path")
    parser.add_argument("--output-dir", type=Path, required=True, help="Absolute existing empty output directory")
    parser.add_argument("--work-parent", type=Path, required=True, help="Absolute existing directory for owned temporary build trees")
    parser.add_argument("--network-mode", choices=sorted(REQUIRED_MODES), required=True, help="Use locked online snapshot or a verified local repository")
    parser.add_argument("--offline-repository", type=Path, help="Absolute local Arch repository root for offline mode")
    parser.add_argument("--offline-manifest", type=Path, help="Absolute exact file/hash manifest for offline repository")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    config = BuildConfig(
        source_root=args.source_root,
        profile_dir=args.profile_dir,
        output_dir=args.output_dir,
        work_parent=args.work_parent,
        snapshot_lock=args.snapshot_lock,
        network_mode=args.network_mode,
        offline_repository=args.offline_repository,
        offline_manifest=args.offline_manifest,
    )
    try:
        result = execute_build(config)
    except BuildContractError as exc:
        print(f"BUILD_BLOCKED: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"BUILD_FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"CANDIDATE_ISO_BUILD_COMPLETED (BIOS/UEFI boot NOT_PERFORMED; not release validated): {result.iso_path}")
    print(f"SHA-256: {result.sha256}")
    print(f"Provenance: {result.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
