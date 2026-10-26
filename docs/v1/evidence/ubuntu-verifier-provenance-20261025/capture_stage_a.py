"""Capture non-installing provenance for the current Ubuntu verifier candidates.

The installed Ubuntu keyring is intentionally the only trust root used here;
this script does not independently authenticate how that root was provisioned.
"""

from __future__ import annotations

import hashlib
import json
import lzma
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from verify_bundle import find_package_record, parse_deb822_paragraphs, sha256_bytes, verify_archive_file, verify_packages_xz


ROOT = Path(__file__).resolve().parent
LISTS = ROOT / "apt-state" / "lists"
INDEXES = ROOT / "indices"
PACKAGES = ROOT / "packages"
EXTRACTED = ROOT / "extracted"
LOGS = ROOT / "logs"
KEYRING = Path("/usr/share/keyrings/ubuntu-archive-keyring.gpg")
RECORDS = {
    "gpgv": {
        "version": "2.4.8-4ubuntu3.1",
        "architecture": "amd64",
        "index": "resolute-updates-main-Packages.xz",
        "inrelease": "archive.ubuntu.com_ubuntu_dists_resolute-updates_InRelease",
        "base_url": "http://archive.ubuntu.com/ubuntu/",
        "installed_path": Path("/usr/bin/gpgv"),
    },
    "gpg": {
        "version": "2.4.8-4ubuntu3.1",
        "architecture": "amd64",
        "index": "resolute-updates-main-Packages.xz",
        "inrelease": "archive.ubuntu.com_ubuntu_dists_resolute-updates_InRelease",
        "base_url": "http://archive.ubuntu.com/ubuntu/",
        "installed_path": Path("/usr/bin/gpg"),
    },
    "ubuntu-keyring": {
        "version": "2023.11.28.1build1",
        "architecture": "all",
        "index": "resolute-main-Packages.xz",
        "inrelease": "archive.ubuntu.com_ubuntu_dists_resolute_InRelease",
        "base_url": "http://archive.ubuntu.com/ubuntu/",
        "installed_path": Path("/usr/share/keyrings/ubuntu-archive-keyring.gpg"),
    },
}
RELEASES = (
    "archive.ubuntu.com_ubuntu_dists_resolute_InRelease",
    "archive.ubuntu.com_ubuntu_dists_resolute-updates_InRelease",
    "security.ubuntu.com_ubuntu_dists_resolute-security_InRelease",
)
APT_ENV_NAMES = {
    "APT_CONFIG",
    "APT_KEY_DONT_WARN_ON_DANGEROUS_USAGE",
    "DEBIAN_FRONTEND",
    "GNUPGHOME",
    "GPG_AGENT_INFO",
    "http_proxy",
    "https_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "no_proxy",
    "NO_PROXY",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
}


def write_command_log(name: str, argv: list[str], result: subprocess.CompletedProcess[str]) -> None:
    (LOGS / f"{name}.log").write_text(
        "ARGV_JSON=" + json.dumps(argv, ensure_ascii=True) + "\n"
        + "STDOUT_BEGIN\n" + result.stdout
        + "STDOUT_END\nSTDERR_BEGIN\n" + result.stderr
        + "STDERR_END\n",
        encoding="utf-8",
    )
    (LOGS / f"{name}.exit").write_text(f"{result.returncode}\n", encoding="ascii")
    if result.returncode != 0:
        raise RuntimeError(f"{name} returned {result.returncode}; see retained log")


def run(argv: list[str], log_name: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    write_command_log(log_name, argv, result)
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def installed_package_version(package: str) -> str:
    argv = ["dpkg-query", "-W", "-f=${Version}\t${Architecture}\t${db:Status-Abbrev}\n", package]
    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        check=False,
    )
    write_command_log(f"dpkg-query-{package}", argv, result)
    fields = result.stdout.rstrip("\n").split("\t")
    if result.returncode != 0 or len(fields) != 3 or not fields[2].startswith("ii") or fields[0] != RECORDS[package]["version"]:
        raise RuntimeError(f"installed package identity does not match pinned candidate: {package}")
    return fields[0]


def file_identity(path: Path) -> dict[str, Any]:
    info = path.lstat()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"expected regular installed/extracted file: {path}")
    return {
        "path": str(path),
        "size": info.st_size,
        "mode_octal": oct(info.st_mode & 0o777),
        "sha256": sha256_file(path),
    }


def main() -> None:
    before = shutil.disk_usage("/mnt/c")
    if before.free < 5 * 1024**3:
        raise RuntimeError(f"C: reserve below 5 GiB before capture: {before.free} bytes")
    ROOT.mkdir(parents=True, exist_ok=True)
    for directory in (PACKAGES, EXTRACTED, LOGS):
        directory.mkdir(parents=True, exist_ok=True)

    capture: dict[str, Any] = {
        "schema": "ubuntu-verifier-provenance-v1",
        "status": "PROPOSAL_REVIEW_REQUIRED",
        "captured_utc_epoch": int(time.time()),
        "environment": {
            "release": subprocess.check_output(["lsb_release", "-ds"], text=True).strip(),
            "architecture": subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip(),
            "c_free_bytes_before": before.free,
        },
        "trust_boundary": (
            "InRelease signatures and package/index digests are checked using the Ubuntu archive keyring "
            "already installed in this WSL image and the installed gpgv binary itself is the verifier. This "
            "bundle does not independently authenticate the provisioning of either host component; therefore "
            "it is a verifier-provenance proposal, not an accepted bootstrap trust root. No Arch keys, packages, "
            "or signatures are acquired or imported."
        ),
        "apt_sources": {},
        "apt_policy": {},
        "environment_override_names": {
            name: ("set; value redacted" if name in os.environ else "unset") for name in sorted(APT_ENV_NAMES)
        },
        "releases": [],
        "packages": [],
    }

    source_paths = [ROOT / "ubuntu-main.sources", Path("/etc/apt/sources.list.d/ubuntu.sources")]
    for source_path in source_paths:
        raw = source_path.read_bytes()
        source_content = raw.decode("utf-8", errors="strict")
        if any("@" in token for line in source_content.splitlines() if line.lstrip().startswith("URIs:") for token in line.split()):
            raise RuntimeError(f"APT source URI appears to contain user-info credentials: {source_path}")
        capture["apt_sources"][str(source_path)] = {
            "sha256": sha256_bytes(raw),
            "content": source_content,
        }
    source_text = "\n".join(item["content"] for item in capture["apt_sources"].values())
    if "Trusted: yes" in source_text or "allow-insecure" in source_text.lower():
        raise RuntimeError("APT source configuration contains an insecure-trust override")
    if "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg" not in capture["apt_sources"][str(ROOT / "ubuntu-main.sources")]["content"]:
        raise RuntimeError("private source list is not explicitly bound to installed Ubuntu keyring")

    apt_config_argv = ["apt-config", "dump"]
    apt_config_result = subprocess.run(apt_config_argv, capture_output=True, text=True, check=False)
    if apt_config_result.returncode != 0:
        raise RuntimeError("apt-config dump failed")
    safe_keys = (
        "Acquire::AllowInsecureRepositories",
        "Acquire::AllowDowngradeToInsecureRepositories",
        "APT::Get::AllowUnauthenticated",
        "Acquire::Check-Valid-Until",
        "Acquire::AllowWeakRepositories",
    )
    filtered_config = [line for line in apt_config_result.stdout.splitlines() if any(key in line for key in safe_keys)]
    write_command_log("apt-config-security-filter", apt_config_argv, subprocess.CompletedProcess(apt_config_argv, 0, stdout="\n".join(filtered_config) + "\n", stderr=""))
    capture["apt_policy"] = {"filtered_config": filtered_config}
    for line in filtered_config:
        if ("AllowInsecureRepositories" in line or "AllowDowngradeToInsecureRepositories" in line or "AllowUnauthenticated" in line or "AllowWeakRepositories" in line) and '"0"' not in line:
            raise RuntimeError(f"unsafe APT policy value: {line}")
        if "Check-Valid-Until" in line and '"0"' in line:
            raise RuntimeError(f"APT validity-date checks disabled: {line}")

    gpgv = str(Path(shutil.which("gpgv") or ""))
    gpgv_hash = sha256_file(Path(gpgv))
    keyring_hash = sha256_file(KEYRING)
    capture["installed_verifier"] = {
        "gpgv_path": gpgv,
        "gpgv_sha256": gpgv_hash,
        "gpgv_package_owner": "gpgv",
        "keyring_path": str(KEYRING),
        "keyring_sha256": keyring_hash,
        "keyring_package_owner": "ubuntu-keyring",
    }

    inrelease_raw: dict[str, bytes] = {}
    for release_name in RELEASES:
        release_path = LISTS / release_name
        raw = release_path.read_bytes()
        log_name = f"gpgv-{release_name.replace('.', '-')}"
        run([gpgv, "--keyring", str(KEYRING), "--output", "/dev/null", str(release_path)], log_name)
        capture["releases"].append({
            "path": str(release_path.relative_to(ROOT)),
            "sha256": sha256_bytes(raw),
            "gpgv_command_log": f"logs/{log_name}.log",
            "gpgv_exit": 0,
            "trust_root": "installed /usr/share/keyrings/ubuntu-archive-keyring.gpg (provisioning not independently verified)",
        })
        inrelease_raw[release_name] = raw

    parsed_indexes: dict[str, bytes] = {}
    index_map = {
        "resolute-main-Packages.xz": ("archive.ubuntu.com_ubuntu_dists_resolute_InRelease", "main/binary-amd64/Packages.xz"),
        "resolute-updates-main-Packages.xz": ("archive.ubuntu.com_ubuntu_dists_resolute-updates_InRelease", "main/binary-amd64/Packages.xz"),
        "resolute-security-main-Packages.xz": ("security.ubuntu.com_ubuntu_dists_resolute-security_InRelease", "main/binary-amd64/Packages.xz"),
    }
    for index_name, (release_name, relative) in index_map.items():
        compressed = (INDEXES / index_name).read_bytes()
        expanded = verify_packages_xz(inrelease_raw[release_name], compressed, relative)
        parsed_indexes[index_name] = expanded
        capture.setdefault("package_indexes", []).append({
            "path": f"indices/{index_name}",
            "sha256": sha256_bytes(compressed),
            "size": len(compressed),
            "signed_inrelease": f"apt-state/lists/{release_name}",
            "release_sha256": sha256_bytes(inrelease_raw[release_name]),
            "relative_release_path": relative,
            "expanded_size": len(expanded),
            "verified": True,
        })

    package_sources: dict[str, tuple[dict[str, str], str]] = {}
    for package, policy in RECORDS.items():
        installed_version = installed_package_version(package)
        if installed_version != policy["version"]:
            raise RuntimeError(f"installed version changed for {package}")
        candidates: list[dict[str, str]] = []
        for index_name, expanded in parsed_indexes.items():
            for row in parse_deb822_paragraphs(expanded):
                if row.get("Package") == package and row.get("Version") == policy["version"] and row.get("Architecture") == policy["architecture"]:
                    candidates.append(row)
        if not candidates:
            raise RuntimeError(f"no exact signed-index record for {package}")
        canonical = candidates[0]
        if any((row.get("Filename"), row.get("Size"), row.get("SHA256")) != (canonical.get("Filename"), canonical.get("Size"), canonical.get("SHA256")) for row in candidates[1:]):
            raise RuntimeError(f"ambiguous package archive metadata across signed indexes: {package}")
        record = find_package_record(
            parsed_indexes[policy["index"]],
            name=package,
            version=policy["version"],
            architecture=policy["architecture"],
        )
        archive_name = Path(record["Filename"]).name
        archive_path = PACKAGES / archive_name
        url = urljoin(str(policy["base_url"]), record["Filename"])
        curl = shutil.which("curl")
        if not curl:
            raise RuntimeError("curl is unavailable; no download attempted")
        curl_hash = sha256_file(Path(curl))
        curl_argv = [curl, "--fail", "--location", "--noproxy", "*", "--connect-timeout", "20", "--max-time", "180", "--output", str(archive_path), url]
        download = run(curl_argv, f"download-{package}")
        archive_sha = verify_archive_file(archive_path, record)

        extracted_root = EXTRACTED / package
        extracted_root.mkdir(parents=True, exist_ok=False)
        dpkg_deb = Path(shutil.which("dpkg-deb") or "")
        dpkg_deb_hash = sha256_file(dpkg_deb)
        extract_argv = [str(dpkg_deb), "--extract", str(archive_path), str(extracted_root)]
        run(extract_argv, f"extract-{package}")

        installed_path = policy["installed_path"]
        extracted_path = extracted_root / installed_path.relative_to("/")
        installed_identity = file_identity(installed_path)
        extracted_identity = file_identity(extracted_path)
        if installed_identity["sha256"] != extracted_identity["sha256"]:
            raise RuntimeError(f"extracted {package} payload differs from installed target {installed_path}")
        owner_result = subprocess.run(["dpkg-query", "-S", str(installed_path)], capture_output=True, text=True, check=False)
        if owner_result.returncode != 0 or not owner_result.stdout.startswith(f"{package}: "):
            raise RuntimeError(f"unexpected dpkg owner for {installed_path}")
        capture["packages"].append({
            "name": package,
            "version": policy["version"],
            "architecture": policy["architecture"],
            "index": policy["index"],
            "signed_inrelease": policy["inrelease"],
            "filename": record["Filename"],
            "size": int(record["Size"]),
            "sha256": record["SHA256"],
            "download_url": url,
            "download_exit": download.returncode,
            "download_log": f"logs/download-{package}.log",
            "downloaded_sha256": archive_sha,
            "extract_log": f"logs/extract-{package}.log",
            "dpkg_deb_path": str(dpkg_deb),
            "dpkg_deb_sha256": dpkg_deb_hash,
            "curl_path": curl,
            "curl_sha256": curl_hash,
            "installed_path": str(installed_path),
            "installed_file": installed_identity,
            "extracted_file": extracted_identity,
            "installed_package_owner": owner_result.stdout.strip(),
            "extracted_matches_installed": True,
        })

    after = shutil.disk_usage("/mnt/c")
    capture["environment"]["c_free_bytes_after"] = after.free
    capture["environment"]["c_free_bytes_delta"] = after.free - before.free
    capture["environment"]["c_reserve_at_least_5gib"] = after.free >= 5 * 1024**3
    if after.free < 5 * 1024**3:
        raise RuntimeError(f"C: reserve fell below 5 GiB after capture: {after.free} bytes")

    (ROOT / "manifest.json").write_text(json.dumps(capture, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": capture["status"],
        "packages": len(capture["packages"]),
        "free_bytes_before": before.free,
        "free_bytes_after": after.free,
        "manifest": str(ROOT / "manifest.json"),
        "trust_root_independently_authenticated": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
