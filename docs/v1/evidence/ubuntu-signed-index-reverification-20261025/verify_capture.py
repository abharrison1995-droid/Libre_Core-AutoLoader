#!/usr/bin/env python3
"""Re-check retained Ubuntu signed-index and Schannel-captured package bytes.

This evidence-only helper does not install packages or fetch network inputs.
It runs the installed gpgv only after validating the exact executable bytes
against the extracted, Schannel-captured gpgv package.
"""

from __future__ import annotations

import hashlib
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any


ROOT = Path(__file__).resolve().parent
REPO = Path(__file__).resolve().parents[4]
if len(sys.argv) != 2 or not re.fullmatch(r"attempt-[0-9]{2}", sys.argv[1]):
    raise SystemExit("usage: verify_capture.py attempt-NN")
RUN_ID = sys.argv[1]
RUN_ROOT = ROOT / "runs" / RUN_ID
INPUTS = ROOT / "inputs"
LOGS = RUN_ROOT / "logs"
EXTRACTED = RUN_ROOT / "extracted"
EXTRACTION_RUN = EXTRACTED / "package-payloads"
RUNTIME = RUN_ROOT / "runtime"
WINDOWS_TLS = REPO / "docs/v1/evidence/windows-tls-corroboration-20261025"
PRIOR_APT = REPO / "docs/v1/evidence/ubuntu-verifier-provenance-20261025"

PRIMARY_FINGERPRINT = "F6ECB3762474EDA9D21B7022871920D1991BC93C"
EXPECTED_FILES: dict[str, tuple[int | None, str]] = {
    "resolute_InRelease": (
        None,
        "45f95ce276cdba3e41870516a130e03c58b8b7a79e9546b0efe9e526d255740c",
    ),
    "resolute-updates_InRelease": (
        None,
        "814669b5013bfe42468b8deb780bc4b7e206173c5c30f398040cd31a4d9170ad",
    ),
    "resolute-main-Packages.xz": (
        1480032,
        "ed9ac41cb263efaecc5a06a0742dc56570e2fb5ff94c4f2e4b2fbdcddd73cf24",
    ),
    "resolute-updates-main-Packages.xz": (
        744388,
        "553c802aee13bedfcf262a111a16b41918ea3a8b1dc98c2fd49c465883af27e4",
    ),
    "gpgv_2.4.8-4ubuntu3.1_amd64.deb": (
        155072,
        "0fcce72656439d90821bbd23580afc794a01ec2f28622ae31b4f5394a830bb09",
    ),
    "gpg_2.4.8-4ubuntu3.1_amd64.deb": (
        636848,
        "fb8da4f691b6e2139fa5cdf7f54d000ef1b7b27753efb2dbdc5a9546057a62c6",
    ),
    "ubuntu-keyring_2023.11.28.1build1_all.deb": (
        11228,
        "c377ccf26964f4c206c05c0bc7adb708e031beb919bb9a0ff63af983d064cd66",
    ),
    "ubuntu-archive-keyring.gpg": (
        3607,
        "80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31",
    ),
}

PACKAGES: tuple[dict[str, Any], ...] = (
    {
        "name": "gpgv",
        "version": "2.4.8-4ubuntu3.1",
        "architecture": "amd64",
        "index": "resolute-updates-main-Packages.xz",
        "inrelease": "resolute-updates_InRelease",
        "filename": "pool/main/g/gnupg2/gpgv_2.4.8-4ubuntu3.1_amd64.deb",
        "deb": "gpgv_2.4.8-4ubuntu3.1_amd64.deb",
    },
    {
        "name": "gpg",
        "version": "2.4.8-4ubuntu3.1",
        "architecture": "amd64",
        "index": "resolute-updates-main-Packages.xz",
        "inrelease": "resolute-updates_InRelease",
        "filename": "pool/main/g/gnupg2/gpg_2.4.8-4ubuntu3.1_amd64.deb",
        "deb": "gpg_2.4.8-4ubuntu3.1_amd64.deb",
    },
    {
        "name": "ubuntu-keyring",
        "version": "2023.11.28.1build1",
        "architecture": "all",
        "index": "resolute-main-Packages.xz",
        "inrelease": "resolute_InRelease",
        "filename": "pool/main/u/ubuntu-keyring/ubuntu-keyring_2023.11.28.1build1_all.deb",
        "deb": "ubuntu-keyring_2023.11.28.1build1_all.deb",
    },
)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def verify_file_manifest(bundle: Path) -> dict[str, Any]:
    manifest = bundle / "SHA256SUMS"
    if not manifest.is_file():
        raise ValueError(f"missing source SHA256SUMS: {manifest}")
    expected: dict[str, str] = {}
    for line in manifest.read_text(encoding="ascii").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None:
            raise ValueError(f"malformed checksum line in {manifest}: {line!r}")
        file_hash, name = match.groups()
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or name in expected:
            raise ValueError(f"unsafe or duplicate manifest path: {name!r}")
        expected[name] = file_hash
    observed: set[str] = set()
    for name, file_hash in expected.items():
        path = bundle.joinpath(*PurePosixPath(name).parts)
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"listed source file missing or non-regular: {name}")
        if digest(path) != file_hash:
            raise ValueError(f"source manifest hash mismatch: {name}")
        observed.add(name)
    actual = {
        p.relative_to(bundle).as_posix()
        for p in bundle.rglob("*")
        if p.is_file() and not p.is_symlink() and p.name != "SHA256SUMS"
    }
    if actual != observed:
        raise ValueError(
            f"source manifest file-set mismatch: unlisted={sorted(actual-observed)}, "
            f"missing={sorted(observed-actual)}"
        )
    return {"manifest": str(manifest), "entries": len(expected), "verified": True}


def run_logged(label: str, argv: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[bytes]:
    base = LOGS / label
    (LOGS / f"{label}.argv.json").write_text(
        json.dumps(
            {"argv": argv, "cwd": str(cwd), "environment": env},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, check=False)
    (LOGS / f"{label}.stdout.bin").write_bytes(result.stdout)
    (LOGS / f"{label}.stderr.bin").write_bytes(result.stderr)
    (LOGS / f"{label}.exit").write_text(f"{result.returncode}\n", encoding="ascii")
    return result


def parse_inrelease_fields(path: Path) -> tuple[dict[str, list[str]], dict[str, tuple[int, str]]]:
    lines = path.read_bytes().replace(b"\r\n", b"\n").split(b"\n")
    if not lines or lines[0] != b"-----BEGIN PGP SIGNED MESSAGE-----":
        raise ValueError(f"not a clear-signed InRelease: {path.name}")
    try:
        first_blank = lines.index(b"", 1)
        signature_start = lines.index(b"-----BEGIN PGP SIGNATURE-----", first_blank + 1)
    except ValueError as exc:
        raise ValueError(f"malformed clear-sign framing: {path.name}") from exc
    body_lines: list[bytes] = []
    for line in lines[first_blank + 1 : signature_start]:
        body_lines.append(line[2:] if line.startswith(b"- ") else line)
    fields: dict[str, list[str]] = {}
    current: str | None = None
    for raw in body_lines:
        if not raw:
            current = None
            continue
        if raw[:1] in (b" ", b"\t"):
            if current is None:
                raise ValueError(f"orphan continuation in {path.name}")
            fields[current].append(raw.strip().decode("ascii"))
            continue
        if b":" not in raw:
            raise ValueError(f"malformed Release field in {path.name}: {raw!r}")
        key_raw, value_raw = raw.split(b":", 1)
        key = key_raw.decode("ascii")
        if key in fields:
            raise ValueError(f"duplicate Release field {key} in {path.name}")
        fields[key] = [value_raw.strip().decode("ascii")]
        current = key
    if "SHA256" not in fields:
        raise ValueError(f"SHA256 field absent from {path.name}")
    rows: dict[str, tuple[int, str]] = {}
    for continuation in fields["SHA256"][1:]:
        parts = continuation.split()
        if len(parts) != 3:
            raise ValueError(f"malformed SHA256 row in {path.name}: {continuation!r}")
        file_hash, size_text, relative = parts
        if not re.fullmatch(r"[0-9a-fA-F]{64}", file_hash) or not size_text.isdigit():
            raise ValueError(f"malformed SHA256 row in {path.name}: {continuation!r}")
        if relative in rows:
            raise ValueError(f"duplicate Release SHA256 path: {relative}")
        rows[relative] = (int(size_text), file_hash.lower())
    return fields, rows


def check_release_freshness(
    label: str,
    fields: dict[str, list[str]],
    *,
    expected_suite: str,
    now: datetime,
    max_age: timedelta,
) -> dict[str, Any]:
    def one(name: str) -> str:
        values = fields.get(name)
        if values is None or len(values) != 1 or not values[0]:
            raise ValueError(f"{label} missing or ambiguous {name} field")
        return values[0]

    codename = one("Codename")
    suite = one("Suite")
    if codename != "resolute" or suite != expected_suite:
        raise ValueError(f"{label} codename/suite mismatch: {codename!r}/{suite!r}")
    date_text = one("Date")
    release_date = parsedate_to_datetime(date_text)
    if release_date.tzinfo is None:
        raise ValueError(f"{label} Date has no timezone")
    release_date = release_date.astimezone(timezone.utc)
    age = now - release_date
    future_tolerance = timedelta(minutes=5)
    if age < -future_tolerance:
        raise ValueError(f"{label} release Date is too far in the future: {date_text}")
    if age > max_age:
        raise ValueError(f"{label} release Date is stale: age={age}")

    valid_until_values = fields.get("Valid-Until")
    valid_until_text: str | None
    valid_until: datetime | None
    if valid_until_values is None:
        valid_until_text = None
        valid_until = None
    else:
        valid_until_text = one("Valid-Until")
        valid_until = parsedate_to_datetime(valid_until_text)
        if valid_until.tzinfo is None:
            raise ValueError(f"{label} Valid-Until has no timezone")
        valid_until = valid_until.astimezone(timezone.utc)
        if valid_until <= release_date:
            raise ValueError(f"{label} Valid-Until does not follow Date")
        if valid_until - release_date > timedelta(days=30):
            raise ValueError(f"{label} Valid-Until is outside the 30-day policy window")
        if now > valid_until:
            raise ValueError(f"{label} metadata expired at {valid_until_text}")
    return {
        "codename": codename,
        "suite": suite,
        "date_text": date_text,
        "date_utc": release_date.isoformat(),
        "age_seconds_at_capture": int(age.total_seconds()),
        "max_age_seconds": int(max_age.total_seconds()),
        "future_tolerance_seconds": int(future_tolerance.total_seconds()),
        "valid_until_text": valid_until_text,
        "valid_until_utc": valid_until.isoformat() if valid_until else None,
        "valid_until_present": valid_until is not None,
        "fresh_at_captured_utc": True,
    }


def parse_control_stanzas(compressed: Path) -> list[dict[str, str]]:
    raw = lzma.decompress(compressed.read_bytes())
    stanzas: list[dict[str, str]] = []
    for stanza_bytes in re.split(rb"\n\s*\n", raw.strip()):
        fields: dict[str, list[str]] = {}
        current: str | None = None
        for line in stanza_bytes.splitlines():
            if line[:1] in (b" ", b"\t"):
                if current is None:
                    raise ValueError(f"orphan Package continuation in {compressed.name}")
                fields[current].append(line.decode("utf-8").strip())
                continue
            if b":" not in line:
                raise ValueError(f"malformed Package field in {compressed.name}: {line!r}")
            key_raw, value_raw = line.split(b":", 1)
            key = key_raw.decode("ascii")
            if key in fields:
                raise ValueError(f"duplicate Package field {key} in {compressed.name}")
            fields[key] = [value_raw.strip().decode("utf-8")]
            current = key
        stanzas.append({key: "\n".join(values) for key, values in fields.items()})
    return stanzas


def parse_readelf_interp(output: str) -> str:
    match = re.search(r"Requesting program interpreter: ([^]]+)\]", output)
    if match is None:
        raise ValueError("ELF has no explicit PT_INTERP")
    return match.group(1)


def resolved_file_record(path_text: str) -> dict[str, Any]:
    path = Path(path_text)
    if not path.is_absolute() or not path.is_file():
        raise ValueError(f"runtime object missing or not absolute: {path_text}")
    resolved = path.resolve(strict=True)
    return {
        "reported_path": str(path),
        "resolved_path": str(resolved),
        "is_symlink": path.is_symlink(),
        "size": resolved.stat().st_size,
        "sha256": digest(resolved),
    }


def main() -> int:
    if not RUN_ROOT.is_dir() or not LOGS.is_dir() or not EXTRACTED.is_dir() or not RUNTIME.is_dir():
        print(f"refusing unprepared unique run directory: {RUN_ROOT}", file=sys.stderr)
        return 73
    permitted_runner_logs = {"runner.stdout.bin", "runner.stderr.bin"}
    preexisting_logs = {path.name for path in LOGS.iterdir()} - permitted_runner_logs
    if preexisting_logs or any(EXTRACTED.iterdir()) or any(RUNTIME.iterdir()):
        print(f"refusing non-empty run directory: {RUN_ROOT}", file=sys.stderr)
        return 73
    summary: dict[str, Any] = {
        "schema": "ubuntu-signed-index-reverification-v1",
        "run_id": RUN_ID,
        "status": "FAILED",
        "proposal_only": True,
        "stage_a_authorized": False,
        "arch_material_acquired": False,
        "package_installation": False,
        "downloaded_payload_execution": False,
        "inputs": {},
        "source_manifests": {},
        "package_records": [],
        "signatures": [],
        "runtime": {},
    }
    try:
        now = datetime.now(timezone.utc)
        summary["captured_utc"] = now.isoformat()
        summary["source_manifests"]["windows_tls"] = verify_file_manifest(WINDOWS_TLS)
        summary["source_manifests"]["prior_apt"] = verify_file_manifest(PRIOR_APT)

        for name, (expected_size, expected_hash) in EXPECTED_FILES.items():
            path = INPUTS / name
            if not path.is_file() or path.is_symlink():
                raise ValueError(f"missing or non-regular staged input: {name}")
            actual_size = path.stat().st_size
            actual_hash = digest(path)
            if expected_size is not None and actual_size != expected_size:
                raise ValueError(f"unexpected size for {name}: {actual_size}")
            if actual_hash != expected_hash:
                raise ValueError(f"unexpected SHA-256 for {name}: {actual_hash}")
            summary["inputs"][name] = {
                "size": actual_size,
                "sha256": actual_hash,
                "verified": True,
            }

        tls_summary = json.loads((WINDOWS_TLS / "capture-summary.json").read_text(encoding="utf-8"))
        tls_rows = tls_summary.get("package_results")
        if not isinstance(tls_rows, list) or len(tls_rows) != 3:
            raise ValueError("Windows TLS capture must contain exactly three package rows")
        tls_by_name: dict[str, dict[str, Any]] = {}
        for row in tls_rows:
            name = row.get("package")
            if name in tls_by_name:
                raise ValueError(f"duplicate Windows TLS package row: {name}")
            tls_by_name[name] = row
        if set(tls_by_name) != {p["name"] for p in PACKAGES}:
            raise ValueError("Windows TLS package set differs from the expected three packages")

        # Re-extract only the exact TLS-captured gpgv and keyring .debs.
        package_env = {"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin", "TZ": "UTC"}
        if EXTRACTION_RUN.exists():
            raise ValueError(f"refusing pre-existing extraction run: {EXTRACTION_RUN}")
        EXTRACTION_RUN.mkdir(mode=0o700)
        for package_name, deb_name, out_dir in (
            ("gpgv", "gpgv_2.4.8-4ubuntu3.1_amd64.deb", "gpgv"),
            ("ubuntu-keyring", "ubuntu-keyring_2023.11.28.1build1_all.deb", "ubuntu-keyring"),
        ):
            target = EXTRACTION_RUN / out_dir
            if target.exists():
                raise ValueError(f"refusing pre-existing extraction target: {target}")
            target.mkdir(mode=0o700)
            proc = run_logged(
                f"dpkg-deb-extract-{package_name}",
                ["/usr/bin/dpkg-deb", "--extract", str(INPUTS / deb_name), str(target)],
                cwd=ROOT,
                env=package_env,
            )
            if proc.returncode != 0:
                raise ValueError(f"dpkg-deb extraction failed for {package_name}: {proc.returncode}")

        extracted_gpgv = EXTRACTION_RUN / "gpgv/usr/bin/gpgv"
        installed_gpgv = Path("/usr/bin/gpgv")
        extracted_keyring = EXTRACTION_RUN / "ubuntu-keyring/usr/share/keyrings/ubuntu-archive-keyring.gpg"
        installed_observation = {
            "path": str(installed_gpgv),
            "size": installed_gpgv.stat().st_size,
            "sha256": digest(installed_gpgv),
        }
        extracted_observation = {
            "path": str(extracted_gpgv),
            "size": extracted_gpgv.stat().st_size,
            "sha256": digest(extracted_gpgv),
        }
        if installed_observation["size"] != 302296 or extracted_observation["size"] != 302296:
            raise ValueError("gpgv executable size differs from expected package payload")
        expected_gpgv_hash = "95ecd00d02b79d091f103af175b1dbf95b5b0c66503104bfcea447cca7e3829d"
        if installed_observation["sha256"] != expected_gpgv_hash or extracted_observation["sha256"] != expected_gpgv_hash:
            raise ValueError("installed/extracted gpgv does not match the TLS-captured package payload")
        if digest(extracted_keyring) != EXPECTED_FILES["ubuntu-archive-keyring.gpg"][1]:
            raise ValueError("keyring extracted from TLS-captured .deb differs from direct HTTPS keyring capture")
        summary["gpgv_payload_binding"] = {
            "windows_tls_deb": {
                "size": 155072,
                "sha256": EXPECTED_FILES["gpgv_2.4.8-4ubuntu3.1_amd64.deb"][1],
            },
            "extracted": extracted_observation,
            "installed": installed_observation,
            "all_bytes_match": True,
            "reextraction_root": str(EXTRACTION_RUN),
        }
        summary["keyring_payload_binding"] = {
            "downloaded_tls_keyring_sha256": EXPECTED_FILES["ubuntu-archive-keyring.gpg"][1],
            "extracted_from_tls_deb_sha256": digest(extracted_keyring),
            "all_bytes_match": True,
        }

        # Parse the retained signed Release body and bind each compressed index.
        release_documents = {
            "resolute_InRelease": parse_inrelease_fields(INPUTS / "resolute_InRelease"),
            "resolute-updates_InRelease": parse_inrelease_fields(INPUTS / "resolute-updates_InRelease"),
        }
        release_freshness = {
            "resolute_InRelease": check_release_freshness(
                "resolute_InRelease",
                release_documents["resolute_InRelease"][0],
                expected_suite="resolute",
                now=now,
                max_age=timedelta(days=400),
            ),
            "resolute-updates_InRelease": check_release_freshness(
                "resolute-updates_InRelease",
                release_documents["resolute-updates_InRelease"][0],
                expected_suite="resolute-updates",
                now=now,
                max_age=timedelta(days=14),
            ),
        }
        summary["release_freshness"] = release_freshness
        release_index_bindings: dict[str, dict[str, Any]] = {}
        for index_name, release_name, release_path in (
            ("resolute-main-Packages.xz", "resolute_InRelease", "main/binary-amd64/Packages.xz"),
            ("resolute-updates-main-Packages.xz", "resolute-updates_InRelease", "main/binary-amd64/Packages.xz"),
        ):
            index = INPUTS / index_name
            matching = release_documents[release_name][1].get(release_path)
            if matching is None:
                raise ValueError(f"InRelease has no SHA256 row for {release_path}: {release_name}")
            observed = (index.stat().st_size, digest(index))
            if matching != observed:
                raise ValueError(f"InRelease -> Packages.xz binding mismatch: {index_name}")
            release_index_bindings[index_name] = {
                "inrelease": release_name,
                "release_path": release_path,
                "size": observed[0],
                "sha256": observed[1],
                "matches_signed_release_body": True,
            }
        summary["release_index_bindings"] = release_index_bindings

        stanzas = {name: parse_control_stanzas(INPUTS / name) for name in (
            "resolute-main-Packages.xz",
            "resolute-updates-main-Packages.xz",
        )}
        for expected in PACKAGES:
            entries = stanzas[expected["index"]]
            named = [row for row in entries if row.get("Package") == expected["name"]]
            if len(named) != 1:
                raise ValueError(
                    f"expected exactly one {expected['name']} record in {expected['index']}; got {len(named)}"
                )
            row = named[0]
            for field, key in (("Version", "version"), ("Architecture", "architecture"), ("Filename", "filename")):
                if row.get(field) != expected[key]:
                    raise ValueError(f"{expected['name']} {field} mismatch: {row.get(field)!r}")
            if not row.get("Size", "").isdigit():
                raise ValueError(f"{expected['name']} has invalid Size field")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", row.get("SHA256", "")):
                raise ValueError(f"{expected['name']} has invalid SHA256 field")
            archive = INPUTS / expected["deb"]
            if int(row["Size"]) != archive.stat().st_size or row["SHA256"].lower() != digest(archive):
                raise ValueError(f"{expected['name']} Packages row does not match captured .deb bytes")
            tls = tls_by_name[expected["name"]]
            tls_capture_path = WINDOWS_TLS / str(tls["downloaded_file"])
            expected_tls_index = "resolute-main-Packages.xz" if expected["name"] == "ubuntu-keyring" else "resolute-updates-main-Packages.xz"
            expected_tls_release = "archive.ubuntu.com_ubuntu_dists_resolute_InRelease" if expected["name"] == "ubuntu-keyring" else "archive.ubuntu.com_ubuntu_dists_resolute-updates_InRelease"
            if (
                tls.get("version") != expected["version"]
                or tls.get("architecture") != expected["architecture"]
                or tls.get("signed_index") != expected_tls_index
                or tls.get("inrelease") != expected_tls_release
            ):
                raise ValueError(f"{expected['name']} Windows TLS row is bound to a different signed index")
            if (
                tls.get("downloaded_size") != archive.stat().st_size
                or tls.get("downloaded_sha256") != digest(archive)
                or tls.get("expected_size_from_signed_index") != int(row["Size"])
                or tls.get("expected_sha256_from_signed_index") != row["SHA256"].lower()
                or not tls_capture_path.is_file()
                or digest(tls_capture_path) != digest(archive)
                or not tls.get("redirects")
                or tls["redirects"][0].get("http_status") != 200
                or tls["redirects"][0].get("schannel_ssl_verify_result") != 0
            ):
                raise ValueError(f"{expected['name']} Windows TLS capture does not corroborate exact signed-row bytes")
            summary["package_records"].append(
                {
                    "package": expected["name"],
                    "version": row["Version"],
                    "architecture": row["Architecture"],
                    "index": expected["index"],
                    "inrelease": expected["inrelease"],
                    "filename": row["Filename"],
                    "size": int(row["Size"]),
                    "sha256": row["SHA256"].lower(),
                    "windows_tls_status": 200,
                    "windows_schannel_verify_result": 0,
                    "windows_tls_archive_sha256_matches": True,
                    "exactly_one_package_record": True,
                }
            )

        # The only dynamic executable permitted below is the exact verified gpgv.
        pre_env = {"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin", "TZ": "UTC"}
        readelf_program = "/usr/bin/readelf"
        readelf_hash = digest(Path(readelf_program))
        readelf_interp = run_logged(
            "readelf-program-headers",
            [readelf_program, "-W", "-l", str(installed_gpgv)],
            cwd=ROOT,
            env=pre_env,
        )
        readelf_dynamic = run_logged(
            "readelf-dynamic-section",
            [readelf_program, "-W", "-d", str(installed_gpgv)],
            cwd=ROOT,
            env=pre_env,
        )
        if readelf_interp.returncode or readelf_dynamic.returncode:
            raise ValueError("readelf failed to inspect verified gpgv")
        interp_path = parse_readelf_interp(readelf_interp.stdout.decode("utf-8", errors="strict"))
        needed = re.findall(
            r"Shared library: \[([^]]+)\]",
            readelf_dynamic.stdout.decode("utf-8", errors="strict"),
        )
        if not needed or len(needed) != len(set(needed)):
            raise ValueError("gpgv DT_NEEDED list missing or duplicated")
        ldd_result = run_logged(
            "ldd-runtime-resolution",
            ["/usr/bin/ldd", str(installed_gpgv)],
            cwd=ROOT,
            env=pre_env,
        )
        if ldd_result.returncode != 0:
            raise ValueError(f"ldd failed: {ldd_result.returncode}")
        ldd_text = ldd_result.stdout.decode("utf-8", errors="strict")
        if "not found" in ldd_text:
            raise ValueError("gpgv has an unresolved runtime dependency")
        library_paths: list[str] = []
        for line in ldd_text.splitlines():
            map_match = re.match(r"^\s*\S+\s+=>\s+(/\S+)\s+\(0x[0-9a-fA-F]+\)\s*$", line)
            direct_match = re.match(r"^\s*(/\S+)\s+\(0x[0-9a-fA-F]+\)\s*$", line)
            if map_match:
                library_paths.append(map_match.group(1))
            elif direct_match:
                library_paths.append(direct_match.group(1))
        if len(library_paths) != len(set(library_paths)) or not library_paths:
            raise ValueError("could not uniquely parse ldd runtime paths")
        runtime_objects = [resolved_file_record(interp_path)]
        runtime_objects.extend(resolved_file_record(path) for path in library_paths)
        summary["runtime"] = {
            "binary": installed_observation,
            "pt_interp": interp_path,
            "dt_needed": needed,
            "resolved_runtime_objects": runtime_objects,
            "ldd_output": ldd_text,
            "readelf_path": readelf_program,
            "readelf_sha256": readelf_hash,
            "ldd_path": "/usr/bin/ldd",
            "ldd_sha256": digest(Path("/usr/bin/ldd")),
            "resolution_observation_is_host_relative": True,
        }
        summary["executing_toolchain"] = {
            "python": {
                "path": sys.executable,
                "version": sys.version,
                "sha256": digest(Path(sys.executable)),
                "host_relative": True,
            },
            "dpkg_deb": {
                "path": "/usr/bin/dpkg-deb",
                "sha256": digest(Path("/usr/bin/dpkg-deb")),
                "host_relative": True,
            },
        }
        manpage_path = Path("/usr/share/man/man1/gpgv.1.gz")
        manpage = run_logged(
            "gpgv-manpage",
            ["/usr/bin/zcat", str(manpage_path)],
            cwd=ROOT,
            env=pre_env,
        )
        if manpage.returncode != 0:
            raise ValueError("could not read local gpgv manual page")
        manpage_text = manpage.stdout.decode("utf-8", errors="strict")
        # The local manpage is roff: remove only font/control tokens and
        # collapse formatter whitespace before checking its literal claims.
        manpage_text = manpage_text.replace(r"\fB", "").replace(r"\fI", "").replace(r"\fP", "")
        manpage_text = manpage_text.replace(r"\-", "-")
        manpage_text = " ".join(manpage_text.split())
        required_manpage_terms = (
            "There are no configuration files",
            "will not look for the default keyring",
            "--homedir",
        )
        if any(term not in manpage_text for term in required_manpage_terms):
            raise ValueError("local gpgv manual does not support the isolated no-config invocation policy")
        help_result = run_logged(
            "gpgv-help",
            ["/usr/bin/gpgv", "--help"],
            cwd=ROOT,
            env=pre_env,
        )
        help_text = help_result.stdout.decode("utf-8", errors="strict")
        if help_result.returncode != 0 or any(
            term not in help_text for term in ("--keyring FILE", "--status-fd FD", "--output FILE")
        ):
            raise ValueError("verified gpgv help does not advertise required explicit options")
        summary["gpgv_no_config_policy"] = {
            "local_manpage_path": str(manpage_path),
            "local_manpage_sha256": digest(manpage_path),
            "local_manpage_gzip_size": manpage_path.stat().st_size,
            "statements_observed": [
                "There are no configuration files",
                "an explicit --keyring prevents lookup of the default keyring",
                "--homedir is documented by the local manpage",
                "--keyring, --status-fd, and --output are advertised by gpgv --help",
            ],
            "unsupported_options_omitted": ["--no-options", "--batch", "--no-default-keyring"],
            "host_relative_documentation": True,
        }
        version_result = run_logged(
            "gpgv-version",
            ["/usr/bin/gpgv", "--version"],
            cwd=ROOT,
            env=pre_env,
        )
        if version_result.returncode != 0:
            raise ValueError("verified gpgv --version invocation failed")
        summary["gpgv_version_output"] = version_result.stdout.decode("utf-8", errors="replace")

        home = Path(tempfile.mkdtemp(prefix="lc-ubuntu-gpgv-review-", dir="/tmp"))
        home.chmod(0o700)
        resolved_home = home.resolve(strict=True)
        if resolved_home.parent != Path("/tmp").resolve(strict=True) or not resolved_home.name.startswith("lc-ubuntu-gpgv-review-"):
            raise ValueError("temporary gpgv home escaped its fixed /tmp prefix")
        entries_before = sorted(p.name for p in resolved_home.iterdir())
        if entries_before:
            raise ValueError(f"new private gpgv home was not empty: {entries_before}")
        summary["gpgv_home"] = {
            "path": str(resolved_home),
            "mode": oct(resolved_home.stat().st_mode & 0o777),
            "entries_before": entries_before,
            "no_user_gpgv_conf": True,
        }
        safe_env = {
            "HOME": str(resolved_home),
            "GNUPGHOME": str(resolved_home),
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "TZ": "UTC",
        }
        signature_specs = (
            ("resolute_InRelease", "inputs/resolute_InRelease"),
            ("resolute-updates_InRelease", "inputs/resolute-updates_InRelease"),
        )
        signatures: list[dict[str, Any]] = []
        try:
            for label, rel_path in signature_specs:
                inrelease = INPUTS / Path(rel_path).name
                result = run_logged(
                    f"gpgv-{label}",
                    [
                        "/usr/bin/gpgv",
                        "--homedir",
                        str(resolved_home),
                        "--keyring",
                        str(INPUTS / "ubuntu-archive-keyring.gpg"),
                        "--status-fd",
                        "1",
                        "--output",
                        "/dev/null",
                        str(inrelease),
                    ],
                    cwd=ROOT,
                    env=safe_env,
                )
                stdout = result.stdout.decode("utf-8", errors="strict")
                stderr = result.stderr.decode("utf-8", errors="replace")
                valid_lines = [line.split() for line in stdout.splitlines() if line.startswith("[GNUPG:] VALIDSIG ")]
                if result.returncode != 0 or len(valid_lines) != 1:
                    raise ValueError(f"gpgv signature check failed or emitted unexpected VALIDSIG for {label}")
                valid = valid_lines[0]
                fingerprints = [field.upper() for field in (valid[2], valid[-1]) if re.fullmatch(r"[0-9A-Fa-f]{40,64}", field)]
                if PRIMARY_FINGERPRINT not in fingerprints:
                    raise ValueError(f"unexpected primary signer for {label}: {fingerprints}")
                if "Good signature from" not in stderr:
                    raise ValueError(f"gpgv did not report a good signature for {label}")
                signatures.append(
                    {
                        "inrelease": label,
                        "exit": result.returncode,
                        "validsig_line": next(line for line in stdout.splitlines() if line.startswith("[GNUPG:] VALIDSIG ")),
                        "primary_fingerprint": PRIMARY_FINGERPRINT,
                        "good_signature_message": True,
                    }
                )
        finally:
            # Retain the private home through both checks, then remove only the
            # exact freshly-created path after confirming its canonical parent.
            if resolved_home.exists():
                summary["gpgv_home"]["entries_after_verification"] = sorted(
                    p.name for p in resolved_home.iterdir()
                )
            if resolved_home.exists():
                if resolved_home.parent != Path("/tmp").resolve(strict=True) or not resolved_home.name.startswith("lc-ubuntu-gpgv-review-"):
                    raise ValueError("refusing to remove unexpected gpgv home path")
                shutil.rmtree(resolved_home)
            if resolved_home.exists():
                raise ValueError("temporary gpgv home remained after cleanup")
            summary["gpgv_home"]["removed_after_checks"] = not resolved_home.exists()
        summary["signatures"] = signatures
        if len(signatures) != 2:
            raise ValueError("expected exactly two verified InRelease signatures")
        summary["status"] = "HOST_RELATIVE_REVERIFICATION_PASSED_PROPOSAL_ONLY"
        return_code = 0
    except Exception as exc:  # evidence helper records failure and stops
        summary["error"] = f"{type(exc).__name__}: {exc}"
        return_code = 1
    finally:
        (RUN_ROOT / "verification-summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (RUN_ROOT / "verification.exit").write_text(f"{return_code}\n", encoding="ascii")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
