"""Synthetic tests for the pinned Arch package-database closure contract."""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
import struct
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any, Mapping

import pytest

from usb_builder import offline_vm_package_closure as closure
from usb_builder import offline_vm_build as build
from usb_builder import offline_vm_worker


REPO_ROOT = Path(__file__).resolve().parents[1]
_SNAPSHOT = json.loads((REPO_ROOT / "usb_builder" / "snapshot.lock.json").read_text(encoding="utf-8"))


class NumericOnlyTestResolver:
    """Test-only exact comparator for digit-only synthetic versions."""

    def __init__(self, identity: Mapping[str, object]) -> None:
        self.identity = identity

    def compare(self, left: str, right: str) -> int:
        if not left.isdigit() or not right.isdigit():
            raise closure.PackageClosureError("synthetic resolver does not implement this ALPM version form")
        return (int(left) > int(right)) - (int(left) < int(right))


def _sig(name: str) -> bytes:
    return f"synthetic signed metadata:{name}".encode("ascii")


def _seed_rows(
    *,
    python_depends: tuple[str, ...] = ("virtual-runtime>=2",),
    systemd_depends: tuple[str, ...] = ("base-runtime>=1",),
    systemd_conflicts: tuple[str, ...] = (),
    systemd_replaces: tuple[str, ...] = (),
    provider_arch: str = "any",
    provider_count: int = 1,
    missing_transitive: bool = False,
    duplicate_filename: bool = False,
    omitted_metadata: str | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    repo_by_name = {"archiso": "extra", "arch-install-scripts": "extra"}
    for seed in closure.WORKER_ROOT_SEEDS:
        name = seed.name
        version = offline_vm_worker.LOCKED_PACKAGES.get(name, "2" if name == "python" else "3")
        arch = "any" if name in {"archiso", "arch-install-scripts", "base", "mkinitcpio"} else "x86_64"
        rows.append({
            "repo": repo_by_name.get(name, "core"),
            "name": name,
            "version": version,
            "arch": arch,
            "filename": f"{name}-{version}-{arch}.pkg.tar.zst",
            "size": 1000 + len(name),
            "archive_sha256": hashlib.sha256(f"archive:{name}".encode()).hexdigest(),
            "signature": _sig(name),
            "dependencies": list(python_depends if name == "python" else systemd_depends if name == "systemd" else ()),
            "provides": [],
            "conflicts": list(systemd_conflicts if name == "systemd" else ()),
            "replaces": list(systemd_replaces if name == "systemd" else ()),
        })
    existing_names = {row["name"] for row in rows}
    for seed in closure._target_usb_profile_seeds(_SNAPSHOT):
        if seed.name in existing_names:
            continue
        version = "1"
        arch = "any" if seed.name in {"linux-firmware", "mkinitcpio-archiso", "mkinitcpio-nfs-utils", "ca-certificates-utils", "base"} else "x86_64"
        repo = "extra" if seed.name in {
            "python-pip", "flashrom", "dmidecode", "intel-ucode", "python-textual", "python-rich",
            "python-click", "python-psutil", "nbd", "pv", "mkinitcpio-archiso",
        } else "core"
        rows.append({
            "repo": repo,
            "name": seed.name,
            "version": version,
            "arch": arch,
            "filename": f"{seed.name}-{version}-{arch}.pkg.tar.zst",
            "size": 1000 + len(seed.name),
            "archive_sha256": hashlib.sha256(f"archive:{seed.name}".encode()).hexdigest(),
            "signature": _sig(seed.name),
            "dependencies": [],
            "provides": [],
            "conflicts": [],
            "replaces": [],
        })
        existing_names.add(seed.name)

    if not missing_transitive:
        rows.append({
            "repo": "extra",
            "name": "base-runtime",
            "version": "1",
            "arch": "x86_64",
            "filename": "base-runtime-1-x86_64.pkg.tar.zst",
            "size": 1200,
            "archive_sha256": hashlib.sha256(b"archive:base-runtime").hexdigest(),
            "signature": _sig("base-runtime"),
            "dependencies": [],
            "provides": [],
            "conflicts": [],
            "replaces": [],
        })
    for number in range(provider_count):
        name = f"virtual-provider-{number}"
        rows.append({
            "repo": "extra",
            "name": name,
            "version": "1",
            "arch": provider_arch,
            "filename": f"{name}-1-{provider_arch}.pkg.tar.zst",
            "size": 1300,
            "archive_sha256": hashlib.sha256(f"archive:{name}".encode()).hexdigest(),
            "signature": _sig(name),
            "dependencies": ["missing-transitive>=1"] if missing_transitive else [],
            "provides": ["virtual-runtime=2"],
            "conflicts": [],
            "replaces": [],
        })
    if duplicate_filename:
        duplicate = copy.deepcopy(next(row for row in rows if row["name"] == "bash"))
        duplicate["repo"] = "extra"
        rows.append(duplicate)
    if omitted_metadata:
        rows[-1]["omit"] = omitted_metadata
    return rows


def _desc(row: dict[str, Any]) -> bytes:
    fields: list[tuple[str, list[str]]] = [
        ("NAME", [row["name"]]),
        ("VERSION", [row["version"]]),
        ("FILENAME", [row["filename"]]),
        ("CSIZE", [str(row["size"])]),
        ("SHA256SUM", [row["archive_sha256"]]),
        ("PGPSIG", [base64.b64encode(row["signature"]).decode("ascii")]),
        ("ARCH", [row["arch"]]),
    ]
    for key, field_name in (("dependencies", "DEPENDS"), ("provides", "PROVIDES"), ("conflicts", "CONFLICTS"), ("replaces", "REPLACES")):
        if row[key]:
            fields.append((field_name, row[key]))
    omitted = row.get("omit")
    output: list[str] = []
    for key, values in fields:
        if omitted == key:
            continue
        output.extend((f"%{key}%", *values, ""))
    return "\n".join(output).encode("utf-8")


def _database(rows: list[dict[str, Any]], repo: str) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for row in rows:
            if row["repo"] != repo:
                continue
            directory = tarfile.TarInfo(f"{row['name']}-{row['version']}/")
            directory.type = tarfile.DIRTYPE
            directory.mode = 0o755
            archive.addfile(directory)
            raw = _desc(row)
            member = tarfile.TarInfo(f"{row['name']}-{row['version']}/desc")
            member.size = len(raw)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(raw))
    return output.getvalue()


def _bundle(rows: list[dict[str, Any]]) -> tuple[bytes, bytes, bytes, NumericOnlyTestResolver]:
    core_db = _database(rows, "core")
    extra_db = _database(rows, "extra")
    snapshot = copy.deepcopy(_SNAPSHOT)
    snapshot["repository_database_sha256"] = {
        "core": hashlib.sha256(core_db).hexdigest(),
        "extra": hashlib.sha256(extra_db).hexdigest(),
    }
    by_name = {row["name"]: row for row in rows}
    rootless = snapshot["rootless_builder"]
    fields = {
        "arch-install-scripts": ("arch_install_scripts_package_sha256", "arch_install_scripts_signature_sha256"),
        "pacman": ("pacman_package_sha256", "pacman_signature_sha256"),
        "util-linux": ("util_linux_package_sha256", "util_linux_signature_sha256"),
        "bash": ("bash_package_sha256", "bash_signature_sha256"),
        "shadow": ("map_helper_package_sha256", "map_helper_signature_sha256"),
        "libcap": ("libcap_package_sha256", "libcap_signature_sha256"),
    }
    for name, (archive_field, signature_field) in fields.items():
        row = by_name[name]
        rootless[archive_field] = row["archive_sha256"]
        rootless[signature_field] = hashlib.sha256(row["signature"]).hexdigest()
    resolver_identity: dict[str, object] = {
        "kind": "synthetic-numeric-only-test-resolver",
        "pacman_version": rootless["pacman_version"],
        "pacman_package_sha256": rootless["pacman_package_sha256"],
        "pacman_signature_sha256": rootless["pacman_signature_sha256"],
        "executable_path": "usr/bin/vercmp",
        "executable_sha256": "a" * 64,
    }
    snapshot["alpm_resolver"] = resolver_identity
    snapshot_raw = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return core_db, extra_db, snapshot_raw, NumericOnlyTestResolver(resolver_identity)


def _full(rows: list[dict[str, Any]]) -> tuple[dict[str, object], bytes, bytes, bytes, NumericOnlyTestResolver]:
    core_db, extra_db, snapshot_raw, resolver = _bundle(rows)
    manifest = closure._build_synthetic_closure_for_tests(core_db, extra_db, snapshot_raw, resolver)
    return manifest, core_db, extra_db, snapshot_raw, resolver


def test_both_root_seed_contracts_are_explicit_and_archiso_is_worker_only() -> None:
    assert len(closure.WORKER_ROOT_SEEDS) == 13
    target_seeds = closure._target_usb_profile_seeds(_SNAPSHOT)
    assert len(target_seeds) == 29
    target_names = {item.name for item in target_seeds}
    worker_names = {item.name for item in closure.WORKER_ROOT_SEEDS}
    assert "archiso" in worker_names and "archiso" not in target_names
    assert "mkinitcpio-archiso" in target_names
    assert {"base", "linux", "mkinitcpio", "systemd-sysvcompat"} <= worker_names


def test_actual_snapshot_has_no_exact_resolver_pin() -> None:
    snapshot_raw = (REPO_ROOT / "usb_builder" / "snapshot.lock.json").read_bytes()
    with pytest.raises(closure.PackageClosureUnavailable, match="no resolver identity"):
        closure.generate_package_closure_lock(b"not-read", b"not-read", snapshot_raw, None)


def test_actual_snapshot_rejects_caller_resolver_before_comparator_call() -> None:
    snapshot_raw = (REPO_ROOT / "usb_builder" / "snapshot.lock.json").read_bytes()

    class CallerSuppliedResolver:
        identity: Mapping[str, object] = {"kind": "caller-supplied-untrusted"}

        def __init__(self) -> None:
            self.calls = 0

        def compare(self, left: str, right: str) -> int:
            self.calls += 1
            raise AssertionError("the production snapshot must reject before comparator use")

    resolver = CallerSuppliedResolver()
    with pytest.raises(closure.PackageClosureUnavailable, match="no resolver identity"):
        closure.generate_package_closure_lock(b"not-read", b"not-read", snapshot_raw, resolver)
    assert resolver.calls == 0


def test_snapshot_resolver_injection_fails_exact_lock_hash_before_comparator_call() -> None:
    snapshot_path = REPO_ROOT / "usb_builder" / "snapshot.lock.json"
    snapshot_value = json.loads(snapshot_path.read_bytes())
    snapshot_value["alpm_resolver"] = {"kind": "caller-injected"}
    tampered_snapshot = json.dumps(snapshot_value, sort_keys=True, separators=(",", ":")).encode("utf-8")

    class CallerSuppliedResolver:
        identity: Mapping[str, object] = {"kind": "caller-injected"}

        def __init__(self) -> None:
            self.calls = 0

        def compare(self, left: str, right: str) -> int:
            self.calls += 1
            raise AssertionError("tampered snapshot must fail before comparator use")

    resolver = CallerSuppliedResolver()
    with pytest.raises(closure.PackageClosureError, match="snapshot lock SHA-256"):
        closure.generate_package_closure_lock(b"not-read", b"not-read", tampered_snapshot, resolver)
    assert resolver.calls == 0


def test_retained_seed_inventory_remains_explicitly_unavailable() -> None:
    inventory_path = (
        REPO_ROOT
        / "docs"
        / "v1"
        / "evidence"
        / "worker-package-seed-policy-20261025"
        / "seed-policy.json"
    )
    inventory = json.loads(inventory_path.read_bytes())
    digest = inventory.pop("inventory_sha256")
    assert digest == hashlib.sha256(closure.canonical_json(inventory)).hexdigest()
    assert inventory["status"] == "SEED_POLICY_COMPLETE_CLOSURE_UNAVAILABLE"
    assert inventory["dependency_closures"] == {
        "target_usb_root": {"packages": [], "status": "UNAVAILABLE_NO_PINNED_ALPM_RESOLVER"},
        "worker_root": {"packages": [], "status": "UNAVAILABLE_NO_PINNED_ALPM_RESOLVER"},
    }
    assert inventory["signature_status"]["verified"] is False
    assert all(not row["signature_verified"] for rows in inventory["seed_packages"].values() for row in rows)

    trust_path = inventory_path.with_name("trust-manifest-proposal-20261025.json")
    trust = json.loads(trust_path.read_bytes())
    assert trust["status"] == "ACQUISITION_BLOCKED"
    assert "No key bytes have been fetched or checked" in trust["arch_master_policy"]["material_rule"]
    assert "no certificate/signature bytes were cryptographically verified" in trust["bootstrap_signer"]["certification_status"]
    assert trust["verifier_candidate"]["status"] == "OBSERVED_CANDIDATE_NOT_ACCEPTED"
    assert trust["packages"]["pacman"]["archive_downloaded"] is False
    assert trust["packages"]["pacman"]["signature_verified"] is False
    assert trust["packages"]["pacman"]["vercmp_executed"] is False


def test_external_resolver_requires_exact_pacman_signature_and_executable_provenance(tmp_path: Path) -> None:
    identity = {
        "kind": "pacman-vercmp-executable",
        "pacman_version": offline_vm_worker.LOCKED_PACKAGES["pacman"],
        "pacman_package_sha256": _SNAPSHOT["rootless_builder"]["pacman_package_sha256"],
        "pacman_signature_sha256": _SNAPSHOT["rootless_builder"]["pacman_signature_sha256"],
        "executable_path": "usr/bin/not-vercmp",
        "executable_sha256": "a" * 64,
        "runtime": {
            "policy": closure.SEALED_VERCMP_RUNTIME_POLICY,
            "architecture": "x86_64",
            "interpreter": None,
            "needed_libraries": [],
        },
    }
    with pytest.raises(closure.PackageClosureError, match="resolver identity is malformed"):
        closure.ExternalVercmpResolver(tmp_path / "missing", identity)


def _minimal_static_elf64() -> bytes:
    raw = bytearray(64 + 56)
    raw[:7] = b"\x7fELF\x02\x01\x01"
    struct.pack_into("<HH", raw, 16, 2, 62)
    struct.pack_into("<Q", raw, 32, 64)
    struct.pack_into("<HHH", raw, 52, 64, 56, 1)
    struct.pack_into("<I", raw, 64, 1)  # PT_LOAD; no interpreter or dynamic segment.
    return bytes(raw)


def _external_static_resolver_identity(raw: bytes) -> dict[str, object]:
    return {
        "kind": "pacman-vercmp-executable",
        "pacman_version": offline_vm_worker.LOCKED_PACKAGES["pacman"],
        "pacman_package_sha256": _SNAPSHOT["rootless_builder"]["pacman_package_sha256"],
        "pacman_signature_sha256": _SNAPSHOT["rootless_builder"]["pacman_signature_sha256"],
        "executable_path": "usr/bin/vercmp",
        "executable_sha256": hashlib.sha256(raw).hexdigest(),
        "runtime": {
            "policy": closure.SEALED_VERCMP_RUNTIME_POLICY,
            "architecture": "x86_64",
            "interpreter": None,
            "needed_libraries": [],
        },
    }


def _dynamic_runtime_plan() -> dict[str, object]:
    body: dict[str, object] = {
        "policy": closure.DYNAMIC_VERCMP_RUNTIME_POLICY,
        "architecture": "x86_64",
        "runtime_root_policy": closure.DYNAMIC_VERCMP_RUNTIME_ROOT_POLICY,
        "executable_needed_libraries": ["liba.so.1"],
        "interpreter": {
            "path": "usr/lib/ld-linux-x86-64.so.2",
            "sha256": "a" * 64,
            "needed_libraries": ["libb.so.1"],
        },
        "libraries": [
            {
                "soname": "liba.so.1",
                "path": "usr/lib/liba.so.1",
                "sha256": "b" * 64,
                "needed_libraries": ["libc.so.6"],
            },
            {
                "soname": "libb.so.1",
                "path": "usr/lib/libb.so.1",
                "sha256": "c" * 64,
                "needed_libraries": [],
            },
            {
                "soname": "libc.so.6",
                "path": "usr/lib/libc.so.6",
                "sha256": "d" * 64,
                "needed_libraries": [],
            },
        ],
    }
    return {**body, "runtime_manifest_sha256": hashlib.sha256(closure.canonical_json(body)).hexdigest()}


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="sealed executable memfd is Linux-specific")
def test_external_resolver_executes_the_sealed_bytes_after_path_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fcntl

    original = _minimal_static_elf64()
    executable = tmp_path / "vercmp"
    executable.write_bytes(original)
    executable.chmod(0o500)
    resolver = closure.ExternalVercmpResolver(executable, _external_static_resolver_identity(original))
    replacement = _minimal_static_elf64() + b"replacement"
    executable.chmod(0o600)
    executable.write_bytes(replacement)
    executable.chmod(0o500)
    assert executable.read_bytes() == replacement

    observed: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert argv[0] == f"/proc/self/fd/{resolver.executable_fd}"
        fd = resolver.executable_fd
        with Path(f"/proc/self/fd/{fd}").open("rb") as stream:
            observed["bytes"] = stream.read()
        observed["seals"] = fcntl.fcntl(fd, fcntl.F_GET_SEALS)
        observed["pass_fds"] = kwargs.get("pass_fds")
        return subprocess.CompletedProcess(argv, 0, stdout=b"1\n", stderr=b"")

    monkeypatch.setattr(closure.subprocess, "run", fake_run)
    try:
        assert resolver.compare("1", "2") == 1
        assert observed["bytes"] == original
        required_seals = fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE
        assert isinstance(observed["seals"], int)
        assert observed["seals"] & required_seals == required_seals
        assert observed["pass_fds"] == (resolver.executable_fd,)
    finally:
        resolver.close()


@pytest.mark.parametrize(
    "runtime",
    [
        {"policy": "dynamic-elf-unpinned", "architecture": "x86_64", "interpreter": "/lib64/ld-linux-x86-64.so.2", "needed_libraries": []},
        {"policy": closure.SEALED_VERCMP_RUNTIME_POLICY, "architecture": "aarch64", "interpreter": None, "needed_libraries": []},
        {"policy": closure.SEALED_VERCMP_RUNTIME_POLICY, "architecture": "x86_64", "interpreter": "/lib64/ld-linux-x86-64.so.2", "needed_libraries": []},
    ],
)
def test_external_resolver_rejects_runtime_identity_drift(
    tmp_path: Path, runtime: dict[str, object],
) -> None:
    raw = _minimal_static_elf64()
    executable = tmp_path / "vercmp"
    executable.write_bytes(raw)
    identity = _external_static_resolver_identity(raw)
    identity["runtime"] = runtime
    with pytest.raises(closure.PackageClosureError, match="resolver identity is malformed"):
        closure.ExternalVercmpResolver(executable, identity)


def test_dynamic_runtime_plan_binds_exact_transitive_dependency_closure() -> None:
    plan = _dynamic_runtime_plan()
    assert closure._validate_dynamic_vercmp_runtime_plan(plan) == plan


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing-transitive", "omits a transitive DT_NEEDED library"),
        ("extra-library", "unreferenced or missing library"),
        ("unsafe-path", "not a canonical relative path"),
        ("bad-runtime-hash", "manifest digest differs"),
    ],
)
def test_dynamic_runtime_plan_rejects_incomplete_or_malformed_closure(
    mutation: str, message: str,
) -> None:
    plan = _dynamic_runtime_plan()
    if mutation == "missing-transitive":
        plan["libraries"] = [row for row in plan["libraries"] if row["soname"] != "libc.so.6"]  # type: ignore[index]
    elif mutation == "extra-library":
        libraries = plan["libraries"]
        assert isinstance(libraries, list)
        libraries.append({
            "soname": "libunused.so.1",
            "path": "usr/lib/libunused.so.1",
            "sha256": "e" * 64,
            "needed_libraries": [],
        })
        body = {key: value for key, value in plan.items() if key != "runtime_manifest_sha256"}
        plan["runtime_manifest_sha256"] = hashlib.sha256(closure.canonical_json(body)).hexdigest()
    elif mutation == "unsafe-path":
        interpreter = plan["interpreter"]
        assert isinstance(interpreter, dict)
        interpreter["path"] = "../ld-linux.so"
        body = {key: value for key, value in plan.items() if key != "runtime_manifest_sha256"}
        plan["runtime_manifest_sha256"] = hashlib.sha256(closure.canonical_json(body)).hexdigest()
    else:
        plan["runtime_manifest_sha256"] = "0" * 64
    with pytest.raises(closure.PackageClosureError, match=message):
        closure._validate_dynamic_vercmp_runtime_plan(plan)


def test_dynamic_resolver_plan_is_validated_but_execution_remains_unavailable(tmp_path: Path) -> None:
    raw = _minimal_static_elf64()
    executable = tmp_path / "vercmp"
    executable.write_bytes(raw)
    identity = _external_static_resolver_identity(raw)
    identity["runtime"] = _dynamic_runtime_plan()
    with pytest.raises(closure.PackageClosureUnavailable, match="dynamic ALPM comparator execution is unavailable"):
        closure.ExternalVercmpResolver(executable, identity)


def test_synthetic_closure_resolves_versioned_provide_and_round_trips_independently() -> None:
    rows = _seed_rows()
    manifest, core_db, extra_db, snapshot_raw, resolver = _full(rows)
    assert manifest["status"] == "METADATA_CLOSURES_COMPLETE_SIGNATURES_UNVERIFIED"
    signature_verification = manifest["signature_verification"]
    assert isinstance(signature_verification, dict)
    assert signature_verification["verified"] is False
    closures = manifest["closures"]
    assert isinstance(closures, dict)
    worker = closures["worker_root"]
    target = closures["target_usb_root"]
    assert isinstance(worker, dict) and worker["direct_seed_count"] == 13
    assert isinstance(target, dict) and target["direct_seed_count"] == 29
    worker_packages = worker["packages"]
    target_packages = target["packages"]
    assert isinstance(worker_packages, list) and isinstance(target_packages, list)
    package_rows = [row for row in worker_packages if isinstance(row, dict)]
    assert len(package_rows) == len(worker_packages)
    provider = next(row for row in package_rows if row["name"] == "virtual-provider-0")
    python_row = next(row for row in package_rows if row["name"] == "python")
    assert provider["signature_verified"] is False
    dependency_selection = python_row["dependency_selection"]
    assert isinstance(dependency_selection, list) and dependency_selection
    assert isinstance(dependency_selection[0], dict)
    assert dependency_selection[0]["selected_name"] == "virtual-provider-0"
    physical_archives = manifest["physical_archive_inventory"]
    assert isinstance(physical_archives, list)
    assert len(physical_archives) < len(worker_packages) + len(target_packages)
    archiso = next(row for row in physical_archives if row["name"] == "archiso")
    assert archiso["included_by"] == ["worker_root"]
    target_names = {row["name"] for row in target_packages if isinstance(row, dict)}
    assert "archiso" not in target_names and "mkinitcpio-archiso" in target_names
    assert closure._validate_synthetic_closure_for_tests(manifest, core_db, extra_db, snapshot_raw, resolver) == manifest


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"provider_count": 2}, "ambiguous ALPM provider"),
        ({"provider_arch": "aarch64"}, "wrong architecture"),
        ({"python_depends": ("missing>=1",)}, "missing transitive dependency"),
        ({"missing_transitive": True}, "missing transitive dependency"),
        ({"systemd_conflicts": ("base-runtime",)}, "selected packages conflict"),
        ({"systemd_replaces": ("base-runtime",)}, "replaces selected package"),
    ],
)
def test_resolver_rejects_ambiguous_wrong_arch_missing_conflict_and_replaces(
    kwargs: Any, message: str
) -> None:
    rows = _seed_rows(**kwargs)
    core_db, extra_db, snapshot_raw, resolver = _bundle(rows)
    with pytest.raises(closure.PackageClosureError, match=message):
        closure._build_synthetic_closure_for_tests(core_db, extra_db, snapshot_raw, resolver)


def test_manifest_validator_rejects_extra_or_stale_rows() -> None:
    rows = _seed_rows()
    manifest, core_db, extra_db, snapshot_raw, resolver = _full(rows)
    extra = copy.deepcopy(manifest)
    packages = extra["closures"]["worker_root"]["packages"]
    assert isinstance(packages, list) and packages
    packages.append(copy.deepcopy(packages[0]))
    with pytest.raises(closure.PackageClosureError, match="independently recomputed"):
        closure._validate_synthetic_closure_for_tests(extra, core_db, extra_db, snapshot_raw, resolver)
    stale = copy.deepcopy(manifest)
    packages = stale["closures"]["worker_root"]["packages"]
    assert isinstance(packages, list) and packages and isinstance(packages[0], dict)
    packages[0]["version"] = "999"
    with pytest.raises(closure.PackageClosureError, match="independently recomputed"):
        closure._validate_synthetic_closure_for_tests(stale, core_db, extra_db, snapshot_raw, resolver)


def _archive_manifest_from_closure(value: Mapping[str, Any]) -> dict[str, object]:
    inventory = value["physical_archive_inventory"]
    assert isinstance(inventory, list)
    rows = []
    for item in inventory:
        assert isinstance(item, dict)
        archive_path = f"repos/{item['repo']}/os/x86_64/{item['filename']}"
        rows.append({
            "repo": item["repo"],
            "name": item["name"],
            "version": item["version"],
            "arch": item["arch"],
            "filename": item["filename"],
            "database_sha256": item["database_sha256"],
            "archive_path": archive_path,
            "archive_size": item["size"],
            "archive_sha256": item["archive_sha256"],
            "signature_blob_sha256": item["signature_blob_sha256"],
            "included_by": item["included_by"],
            "signature_path": archive_path + ".sig",
            "signature_sha256": "e" * 64,
        })
    return {"packages": rows}


def test_build_manifest_archive_inventory_must_equal_closure_union() -> None:
    rows = _seed_rows()
    manifest, _core_db, _extra_db, _snapshot_raw, _resolver = _full(rows)
    package_manifest = _archive_manifest_from_closure(manifest)
    build._validate_manifest_archive_union(package_manifest, manifest)

    missing = copy.deepcopy(package_manifest)
    missing["packages"].pop()  # type: ignore[union-attr]
    with pytest.raises(build.OfflineVmBuildError, match="exact worker/target closure union"):
        build._validate_manifest_archive_union(missing, manifest)

    extra = copy.deepcopy(package_manifest)
    extra_row = copy.deepcopy(extra["packages"][0])  # type: ignore[index]
    extra_row["name"] = "unbound-extra"  # type: ignore[index]
    extra["packages"].append(extra_row)  # type: ignore[union-attr]
    with pytest.raises(build.OfflineVmBuildError, match="closure/archive identity is malformed|exact worker/target closure union"):
        build._validate_manifest_archive_union(extra, manifest)

    wrong_database = copy.deepcopy(package_manifest)
    wrong_database["packages"][0]["database_sha256"] = "0" * 64  # type: ignore[index]
    with pytest.raises(build.OfflineVmBuildError, match="exact worker/target closure union"):
        build._validate_manifest_archive_union(wrong_database, manifest)

    wrong_membership = copy.deepcopy(package_manifest)
    rows = wrong_membership["packages"]
    assert isinstance(rows, list)
    first_membership = rows[0]["included_by"]
    assert isinstance(first_membership, list)
    if first_membership == ["worker_root"]:
        rows[0]["included_by"] = ["target_usb_root"]
    elif first_membership == ["target_usb_root"]:
        rows[0]["included_by"] = ["worker_root"]
    else:
        rows[0]["included_by"] = ["worker_root"]
    with pytest.raises(build.OfflineVmBuildError, match="exact worker/target closure union"):
        build._validate_manifest_archive_union(wrong_membership, manifest)


def test_recomputation_rejects_database_mutation_and_bad_duplicate_filename() -> None:
    rows = _seed_rows()
    manifest, core_db, extra_db, snapshot_raw, resolver = _full(rows)
    mutated = core_db[:-1] + bytes([core_db[-1] ^ 1])
    with pytest.raises(closure.PackageClosureError, match="database SHA-256"):
        closure._validate_synthetic_closure_for_tests(manifest, mutated, extra_db, snapshot_raw, resolver)
    duplicate = _seed_rows(duplicate_filename=True)
    duplicate_core = _database(duplicate, "core")
    duplicate_extra = _database(duplicate, "extra")
    with pytest.raises(closure.PackageClosureError, match="duplicate package filename metadata"):
        core_packages = closure.parse_repository_database("core", duplicate_core, hashlib.sha256(duplicate_core).hexdigest())
        extra_packages = closure.parse_repository_database("extra", duplicate_extra, hashlib.sha256(duplicate_extra).hexdigest())
        closure._package_index((*core_packages, *extra_packages))


@pytest.mark.parametrize("omitted", ["CSIZE", "SHA256SUM", "PGPSIG"])
def test_database_parser_rejects_interrupted_retrieval_metadata(omitted: str) -> None:
    row = _seed_rows()[-1]
    row["omit"] = omitted
    raw = _database([row], row["repo"])
    with pytest.raises(closure.PackageClosureError, match="lacks one exact|lacks the required"):
        closure.parse_repository_database(row["repo"], raw, hashlib.sha256(raw).hexdigest())


def test_database_parser_rejects_safe_filename_not_bound_to_package_fields() -> None:
    row = _seed_rows()[-1]
    row["filename"] = "different-safe-name-1-any.pkg.tar.zst"
    raw = _database([row], row["repo"])
    with pytest.raises(closure.PackageClosureError, match="does not exactly match NAME, VERSION, ARCH"):
        closure.parse_repository_database(row["repo"], raw, hashlib.sha256(raw).hexdigest())


def test_db_hash_mismatch_and_unsupported_test_comparison_fail_closed() -> None:
    rows = _seed_rows(python_depends=("virtual-runtime>=2",))
    core_db, extra_db, snapshot_raw, resolver = _bundle(rows)
    bad_snapshot = copy.deepcopy(json.loads(snapshot_raw))
    bad_snapshot["repository_database_sha256"]["core"] = "0" * 64
    bad_raw = json.dumps(bad_snapshot, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(closure.PackageClosureError, match="database SHA-256"):
        closure._build_synthetic_closure_for_tests(core_db, extra_db, bad_raw, resolver)

    class UnsupportedResolver(NumericOnlyTestResolver):
        def compare(self, left: str, right: str) -> int:
            raise closure.PackageClosureError("unsupported ALPM version syntax")

    unsupported = UnsupportedResolver(resolver.identity)
    with pytest.raises(closure.PackageClosureError, match="unsupported ALPM version syntax"):
        closure._build_synthetic_closure_for_tests(core_db, extra_db, snapshot_raw, unsupported)


def test_seed_inventory_writer_refuses_closure_claim(tmp_path: Path) -> None:
    output = tmp_path / "seed-inventory.json"
    for claimed_status in ("COMPLETE", "METADATA_CLOSURES_COMPLETE_SIGNATURES_UNVERIFIED", "SIGNED_CLOSURE_COMPLETE"):
        with pytest.raises(closure.PackageClosureError, match="only the metadata-only seed policy"):
            closure.write_seed_inventory(
                output,
                {"format": closure.SEED_INVENTORY_FORMAT, "status": claimed_status},
            )
    assert not output.exists()


@pytest.mark.skipif(os.name != "posix", reason="durable seed inventory publication requires POSIX descriptor and directory fsync")
def test_seed_inventory_writer_cleans_failed_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "seed-inventory.json"
    inventory = {"format": closure.SEED_INVENTORY_FORMAT, "status": "SEED_POLICY_COMPLETE_CLOSURE_UNAVAILABLE"}
    real_write = closure.os.write

    def failed_write(fd: int, value: bytes | memoryview) -> int:
        raise OSError("injected partial write")

    monkeypatch.setattr(closure.os, "write", failed_write)
    with pytest.raises(OSError, match="partial write"):
        closure.write_seed_inventory(output, inventory)
    assert not output.exists()
    monkeypatch.setattr(closure.os, "write", real_write)


def test_real_seed_inventory_refuses_mutated_db_before_output(tmp_path: Path) -> None:
    snapshot_path = REPO_ROOT / "usb_builder" / "snapshot.lock.json"
    core = tmp_path / "core.db"
    extra = tmp_path / "extra.db"
    core.write_bytes(b"mutated")
    extra.write_bytes(b"mutated")
    output = tmp_path / "inventory.json"
    with pytest.raises(closure.PackageClosureError, match="core database SHA-256"):
        closure.build_seed_inventory_from_paths(snapshot_path, core, extra)
    assert not output.exists()
