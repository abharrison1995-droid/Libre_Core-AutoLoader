"""Bounded, non-hardware X200 upstream build diagnostic for Ubuntu WSL.

This runner intentionally does not call ArtifactEngine.build or publish a
COMPLETE artifact. Its fresh source tree is pinned to Libreboot 26.01 and stages
the SOL-reviewed vboot const-correctness overlay before running the single
command ``./mk -b coreboot x200_4mb``. Generated ROMs stay in the Linux-side
scratch tree; only hashes and structural inspection records are copied into
this evidence directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


REPO = Path("/mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader")
OLD_RUN = Path("/home/alex/librecore-x200-upstream-smoke-20261015T185606Z-ca594b8e")
RUN_ROOT = Path("/home/alex/librecore-x200-vboot-overlay-build-20261021-6c3f0a21")
EVIDENCE = REPO / "docs/v1/evidence/x200-vboot-overlay-build-20261021"
LBMK_REVISION = "1cf3181537b8d1fe1df0e91681f700850a3d9bf6"
LBMK_TREE = "0cb9451c6e2e265d3aa5b7e371752d45cab98f53"
COREBOOT_REVISION = "fcd716d9a272461da1fdbaf1c048eb5a52c7896b"
COREBOOT_TREE = "e16fe240567cb3fa08619c6a23e51c4af142f6ac"
UNIFONT_SHA256 = "0e3981ab552231b5a2a870f2b61741903a4bf25c23ef5aeb05fdced1b3c7af4d"
UBOOT_OVERLAY_SHA256 = "03c17c53372864265bb6716b96ad90022dcc1b84d5ebd7a54fd93820be8b55e4"
GRUB_OVERLAY_SHA256 = "3356bf29879cf87659230a201ab81f5e45cc5a0e9c80161463a9b868955f036d"
SOURCE_ORIGIN = "https://codeberg.org/libreboot/lbmk.git"
MAX_BUILD_SECONDS = 14_400
POLL_SECONDS = 60
CACHE_SHA256 = {
    "408aa933cacbef50a728675001204530bae2ea31a8cde1c54a604b350ca9c7b59dd9cde8dccaf7c1a59e466baac59585466c2ccd80ddd038c8f6048edbc5f19c": "eaca4c0248db872a055ab38b0deab4f5f577d1a0cada0fcbc0d80955e2a98e33",
    "4bab4ef6076f8c5dfdc99d810b51108ced61ea2942ba0c1c932d624360a5473df20d32b300fc76f2ba4aa2a97e1f275c9fd494a1ba9f07c4cb2ad7ceaeb1ae97": "ab642492f5cf882b74aa0cb730cd410a81edcdbec895183ce930e706c1c759b8",
    "932bdef0cda94bacedf452ab17f103c0cb511ff2cec55e9112fc0328cbf1d803b42595728ea7b200e0a057c03e85626f937012e49a7515bc5dd256b2bf4bc396": "a7b39bc69cbf9e25826c5a60ab26477001f7c08d85cec04bc0e29cabed6f3cc9",
    "c7b10a7466d9fd398d7a0b3f2a43318432668d714f2ec70069a31bdc93c86d28e0fe83792195727167743707fbae45337c0873a0786416db53bbf22860c16ce7": "c50c0e7f9cb188980e2cc97e4537626b1672441815587f1eab69d2a1bfbef5d2",
    "e85a0dab5195889948a3462189f0e0598d331d3457612e2d3350799dba2e244316d256f8161df5219538eb003e4b5343f989aaa00f96321559063ed8c8f29fd2": "a3c2b80201b89e68616f4ad30bc66aee4927c3ce50e33929ca819d5c43538898",
    "eb9e7f51b5385fb349cc4fba3a45ffdf0dd53be6dfc74932dc01258158a10514667960c530c47dd9dfc5aa18be2bd94859d80499844c5713710581e6ac6259a9": "b67ba0383ef7e8a8563734e2e889ef5ec3c3b898a01d00fa0a6869ad81c6ce01",
    "f28445d368debdf44219cc57df33800a8c0e49186cd60836d4adfec7700d53b801d34aa9fc9bfda74169843f33a1e8b465e11292582eb968bb9c3a26f54dd172": "bef3de159bcd61adf98bb7cc87ee9046e944644ad76b7633f18ab063edb29e57",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def command_record(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int = 300,
) -> dict[str, Any]:
    completed = subprocess.run(
        list(argv),
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        close_fds=True,
    )
    return {
        "argv": list(argv),
        "cwd": str(cwd),
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def require_ok(record: dict[str, Any]) -> None:
    if record["exit_code"] != 0:
        raise RuntimeError(
            f"command failed ({record['exit_code']}): {record['argv']!r}; "
            f"stderr={record['stderr'][-1200:]!r}"
        )


def tool_record(name: str, env: dict[str, str], report: dict[str, Any]) -> None:
    executable = shutil.which(name, path=env["PATH"])
    if executable is None:
        raise RuntimeError(f"required build tool is missing from pinned PATH: {name}")
    resolved = Path(executable).resolve(strict=True)
    if resolved.is_symlink() or not resolved.is_file():
        raise RuntimeError(f"build tool is not a regular file: {resolved}")
    version = command_record([str(resolved), "--version"], cwd=RUN_ROOT, env=env)
    require_ok(version)
    owner = command_record(["dpkg-query", "-S", str(resolved)], cwd=RUN_ROOT, env=env)
    require_ok(owner)
    report.setdefault("tools", {})[name] = {
        "path": str(resolved),
        "sha256": sha256_file(resolved),
        "version_output": version["stdout"],
        "package_owner_output": owner["stdout"],
    }


def validate_clean_checkout(source: Path, env: dict[str, str]) -> dict[str, Any]:
    from autoloader.rom_builder.artifact_engine import (
        LIBREBOOT_2601,
        X200_4MB,
        ArtifactEngine,
        lbmk_expected_output_paths,
        sha256_file as engine_sha256,
    )

    if LIBREBOOT_2601.revision != LBMK_REVISION or LIBREBOOT_2601.tree_id != LBMK_TREE:
        raise RuntimeError("current ArtifactEngine Libreboot source pin differs from the approved diagnostic")
    if X200_4MB.build_command != ("./mk", "-b", "coreboot", "x200_4mb"):
        raise RuntimeError("current ArtifactEngine X200 build argv differs from the approved recipe")

    head = command_record(["git", "-C", str(source), "rev-parse", "HEAD"], cwd=RUN_ROOT, env=env)
    tree = command_record(["git", "-C", str(source), "rev-parse", "HEAD^{tree}"], cwd=RUN_ROOT, env=env)
    origin = command_record(["git", "-C", str(source), "remote", "get-url", "origin"], cwd=RUN_ROOT, env=env)
    require_ok(head)
    require_ok(tree)
    require_ok(origin)
    if head["stdout"].strip() != LBMK_REVISION or tree["stdout"].strip() != LBMK_TREE:
        raise RuntimeError("fresh lbmk checkout differs from the pinned commit/tree")
    if origin["stdout"].strip() != SOURCE_ORIGIN:
        raise RuntimeError("fresh lbmk checkout origin is not the pinned official source")

    source_hashes: dict[str, str] = {}
    for pinned in LIBREBOOT_2601.files:
        path = source.joinpath(*pinned.path.split("/"))
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"pinned lbmk source file missing or unsafe: {pinned.path}")
        actual = engine_sha256(path)
        source_hashes[pinned.path] = actual
        if actual != pinned.sha256:
            raise RuntimeError(f"pinned lbmk source file hash mismatch: {pinned.path}")

    engine = ArtifactEngine()
    engine._validate_lbmk_target(X200_4MB, source)
    expected = lbmk_expected_output_paths(source, X200_4MB)
    if len(expected) != 31 or len(set(expected)) != 31:
        raise RuntimeError("pinned X200 target no longer resolves to its exact 31-output inventory")

    stale = [name for name in ("src", "elf", "bin", "tmp") if (source / name).exists()]
    if stale:
        raise RuntimeError(f"fresh build source contains stale output directories: {stale}")
    return {
        "revision": head["stdout"].strip(),
        "tree": tree["stdout"].strip(),
        "origin": origin["stdout"].strip(),
        "source_file_hashes_before_overlays": source_hashes,
        "expected_rom_relative_paths": list(expected),
        "expected_rom_count": 31,
        "expected_rom_size_bytes_each": X200_4MB.expected_size_bytes,
        "selected_rom_relative_path": X200_4MB.output_relative_path,
    }


def prepare(report: dict[str, Any]) -> tuple[Path, dict[str, str]]:
    from autoloader.rom_builder.artifact_engine import (
        ArtifactEngine,
        LIBREBOOT_2601,
        X200_4MB,
        _X200_GRUB_FONT_OVERLAY_PATH,
        _X200_GRUB_FONT_OVERLAY_SHA256,
        _X200_LBMK_TREE_SH_PATCHED_SHA256,
        _X200_UBOOT_OVERLAY_INPUT,
        _X200_UBOOT_OVERLAY_PATH,
        _X200_UBOOT_OVERLAY_SHA256,
        _X200_VBOOT_OVERLAY_RELATIVE_PATH,
        _X200_VBOOT_OVERLAY_RESOURCE_PATH,
        _X200_VBOOT_OVERLAY_SHA256,
    )

    if RUN_ROOT.exists() or RUN_ROOT.is_symlink():
        raise RuntimeError(f"refusing to reuse an existing scratch path: {RUN_ROOT}")
    if not OLD_RUN.is_dir() or OLD_RUN.is_symlink():
        raise RuntimeError(f"historical source/cache root missing or unsafe: {OLD_RUN}")
    if RUN_ROOT.parent.resolve(strict=True) != Path("/home/alex").resolve(strict=True):
        raise RuntimeError("fresh scratch root escaped the intended Ubuntu WSL Linux home")
    root_stat = RUN_ROOT.parent.stat()
    if root_stat.st_uid != os.getuid() or root_stat.st_mode & 0o022:
        raise RuntimeError("Ubuntu WSL home ownership or write mode is unexpected")
    free_bytes = shutil.disk_usage(RUN_ROOT.parent).free
    if free_bytes < 50 * 1024**3:
        raise RuntimeError(f"insufficient free space before isolated build: {free_bytes} bytes")

    RUN_ROOT.mkdir(mode=0o700)
    work = RUN_ROOT / "work"
    logs = RUN_ROOT / "logs"
    inputs = RUN_ROOT / "inputs"
    home = RUN_ROOT / "home"
    temporary = RUN_ROOT / "tmp"
    for directory in (work, logs, inputs, home, temporary):
        directory.mkdir(mode=0o700)
    source = work / "lbmk"

    gitconfig = RUN_ROOT / "gitconfig"
    with gitconfig.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("[user]\n\tname = Libre Core AutoLoader Build\n\temail = firmware-build@localhost.invalid\n")
        stream.flush()
        os.fsync(stream.fileno())

    env = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": str(home),
        "TMPDIR": str(temporary),
        "GIT_CONFIG_GLOBAL": str(gitconfig),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
        "LANG": "C",
        "TZ": "UTC",
        "LIBRECORE_UNIFONT_PATH": str(inputs / "unifont.otf"),
    }
    report["environment"] = dict(env)
    report["gitconfig_sha256"] = sha256_file(gitconfig)
    report["disk_free_bytes_before"] = free_bytes
    report["home_mode"] = oct(home.stat().st_mode & 0o777)

    commands: list[dict[str, Any]] = []
    clone = command_record(
        ["git", "clone", "--no-local", "--no-hardlinks", "--quiet", str(OLD_RUN / "work/lbmk"), str(source)],
        cwd=work,
        env=env,
        timeout=600,
    )
    commands.append(clone)
    require_ok(clone)
    for argv in (
        ["git", "-C", str(source), "checkout", "--detach", LBMK_REVISION],
        ["git", "-C", str(source), "remote", "set-url", "origin", SOURCE_ORIGIN],
    ):
        record = command_record(argv, cwd=work, env=env)
        commands.append(record)
        require_ok(record)

    report["source"] = validate_clean_checkout(source, env)

    old_cache = OLD_RUN / "work/lbmk/cache/file"
    if old_cache.is_symlink() or not old_cache.is_dir():
        raise RuntimeError("historical dependency tarball cache directory is missing or unsafe")
    new_cache = source / "cache/file"
    if new_cache.exists() or new_cache.is_symlink():
        raise RuntimeError("fresh source unexpectedly contains a tarball cache")
    cache_files = sorted(old_cache.iterdir())
    if (
        {item.name for item in cache_files} != set(CACHE_SHA256)
        or any(item.is_symlink() or not item.is_file() for item in cache_files)
    ):
        raise RuntimeError("historical dependency tarball cache differs from the exact pinned inventory")
    new_cache.mkdir(parents=True, mode=0o700)
    cache_records = []
    for item in cache_files:
        expected_hash = CACHE_SHA256[item.name]
        if sha256_file(item) != expected_hash:
            raise RuntimeError(f"historical dependency tarball cache hash differs from verified pin: {item.name}")
        destination = new_cache / item.name
        shutil.copyfile(item, destination)
        if sha256_file(destination) != expected_hash:
            raise RuntimeError(f"verified dependency cache copy changed bytes: {item.name}")
        cache_records.append({"name": item.name, "size_bytes": destination.stat().st_size, "sha256": expected_hash})
    report["verified_tarball_cache"] = cache_records
    report["old_compiled_crossgcc_objects_reused"] = False
    report["old_worktree_reused"] = False
    report["cache_hash_shortcuts_copied"] = False

    font_source = Path("/usr/share/fonts/opentype/unifont/unifont.otf")
    font = inputs / "unifont.otf"
    if font_source.is_symlink() or not font_source.is_file() or sha256_file(font_source) != UNIFONT_SHA256:
        raise RuntimeError("system Unifont source is missing or differs from the pinned hash")
    shutil.copyfile(font_source, font)
    if sha256_file(font) != UNIFONT_SHA256:
        raise RuntimeError("staged Unifont input hash differs from the source")
    report["unifont"] = {"source_path": str(font_source), "staged_path": str(font), "sha256": sha256_file(font)}

    font_patch = REPO / _X200_GRUB_FONT_OVERLAY_PATH
    uboot_patch = REPO / "autoloader/rom_builder/patches" / _X200_UBOOT_OVERLAY_INPUT[0]
    report["overlays"] = {
        "grub_font": {"source_path": str(font_patch), "sha256": GRUB_OVERLAY_SHA256},
        "uboot": {"source_path": str(uboot_patch), "staged_relative_path": _X200_UBOOT_OVERLAY_PATH, "sha256": UBOOT_OVERLAY_SHA256},
        "vboot_cbfstool": {
            "source_path": str(REPO / _X200_VBOOT_OVERLAY_RESOURCE_PATH),
            "staged_relative_path": _X200_VBOOT_OVERLAY_RELATIVE_PATH,
            "sha256": _X200_VBOOT_OVERLAY_SHA256,
        },
    }
    if (
        _X200_GRUB_FONT_OVERLAY_SHA256 != GRUB_OVERLAY_SHA256
        or _X200_UBOOT_OVERLAY_SHA256 != UBOOT_OVERLAY_SHA256
        or sha256_file(font_patch) != GRUB_OVERLAY_SHA256
        or sha256_file(uboot_patch) != UBOOT_OVERLAY_SHA256
        or sha256_file(REPO / _X200_VBOOT_OVERLAY_RESOURCE_PATH) != _X200_VBOOT_OVERLAY_SHA256
    ):
        raise RuntimeError("checked-in X200 overlay hash differs from the reviewed pin")
    apply_check = command_record(["git", "-C", str(source), "apply", "--check", str(font_patch)], cwd=work, env=env)
    commands.append(apply_check)
    require_ok(apply_check)
    apply_patch = command_record(["git", "-C", str(source), "apply", str(font_patch)], cwd=work, env=env)
    commands.append(apply_patch)
    require_ok(apply_patch)
    uboot_destination = source.joinpath(*_X200_UBOOT_OVERLAY_PATH.split("/"))
    if uboot_destination.exists() or uboot_destination.is_symlink():
        raise RuntimeError("fresh U-Boot overlay destination already exists")
    shutil.copyfile(uboot_patch, uboot_destination)
    if sha256_file(uboot_destination) != UBOOT_OVERLAY_SHA256:
        raise RuntimeError("staged U-Boot overlay changed during copy")
    ArtifactEngine()._stage_x200_vboot_compat_overlay(source)
    vboot_destination = source.joinpath(*_X200_VBOOT_OVERLAY_RELATIVE_PATH.split("/"))
    if vboot_destination.is_symlink() or not vboot_destination.is_file() or sha256_file(vboot_destination) != _X200_VBOOT_OVERLAY_SHA256:
        raise RuntimeError("staged vboot compatibility overlay differs from its reviewed byte pin")

    # Verify the overlay state against the reviewed exact two-file delta. The
    # normal source validator intentionally checks the pristine pinned files,
    # so it is used before overlays only.
    post_overlay_head = command_record(["git", "-C", str(source), "rev-parse", "HEAD"], cwd=work, env=env)
    post_overlay_tree = command_record(["git", "-C", str(source), "rev-parse", "HEAD^{tree}"], cwd=work, env=env)
    post_overlay_origin = command_record(["git", "-C", str(source), "remote", "get-url", "origin"], cwd=work, env=env)
    for record in (post_overlay_head, post_overlay_tree, post_overlay_origin):
        commands.append(record)
        require_ok(record)
    if (
        post_overlay_head["stdout"].strip() != LBMK_REVISION
        or post_overlay_tree["stdout"].strip() != LBMK_TREE
        or post_overlay_origin["stdout"].strip() != SOURCE_ORIGIN
    ):
        raise RuntimeError("overlay application changed the pinned lbmk commit/tree/origin")
    source_hashes_after = {}
    for pinned in LIBREBOOT_2601.files:
        path = source.joinpath(*pinned.path.split("/"))
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"pinned lbmk source path missing or unsafe after overlays: {pinned.path}")
        actual_hash = sha256_file(path)
        source_hashes_after[pinned.path] = actual_hash
        expected_hash = _X200_LBMK_TREE_SH_PATCHED_SHA256 if pinned.path == "include/tree.sh" else pinned.sha256
        if actual_hash != expected_hash:
            raise RuntimeError(f"unexpected pinned source/overlay bytes after overlays: {pinned.path}")
    source_after = {
        "revision": LBMK_REVISION,
        "tree": LBMK_TREE,
        "origin": SOURCE_ORIGIN,
        "source_file_hashes_before_overlays": report["source"]["source_file_hashes_before_overlays"],
        "source_file_hashes_after_overlays": source_hashes_after,
        "expected_rom_relative_paths": report["source"]["expected_rom_relative_paths"],
        "expected_rom_count": 31,
        "expected_rom_size_bytes_each": X200_4MB.expected_size_bytes,
        "selected_rom_relative_path": X200_4MB.output_relative_path,
    }
    source_after["unifont_include_tree_sha256_expected"] = _X200_LBMK_TREE_SH_PATCHED_SHA256
    if source_after["source_file_hashes_after_overlays"]["include/tree.sh"] != _X200_LBMK_TREE_SH_PATCHED_SHA256:
        raise RuntimeError("Unifont overlay did not yield the pinned include/tree.sh bytes")
    status = command_record(["git", "-C", str(source), "status", "--porcelain=v1", "--untracked-files=all"], cwd=work, env=env)
    commands.append(status)
    require_ok(status)
    expected_status = (
        " M include/tree.sh",
        f"?? {_X200_VBOOT_OVERLAY_RELATIVE_PATH}",
        f"?? {_X200_UBOOT_OVERLAY_PATH}",
    )
    if tuple(sorted(status["stdout"].splitlines())) != tuple(sorted(expected_status)):
        raise RuntimeError(f"fresh overlay source status differs from exact reviewed overlay delta: {status['stdout']!r}")
    report["source"] = source_after
    report["setup_commands"] = commands

    packages = command_record(
        ["dpkg-query", "-W", "git", "make", "gcc", "g++", "binutils", "fonts-unifont"],
        cwd=RUN_ROOT,
        env=env,
    )
    require_ok(packages)
    report["package_inventory"] = packages["stdout"]
    for name in ("git", "make", "gcc", "g++", "objcopy"):
        tool_record(name, env, report)
    report["artifact_engine_source_sha256"] = sha256_file(REPO / "autoloader/rom_builder/artifact_engine.py")
    report["build"] = {
        "argv": ["./mk", "-b", "coreboot", "x200_4mb"],
        "cwd": str(source),
        "timeout_seconds": MAX_BUILD_SECONDS,
        "started_utc": None,
        "ended_utc": None,
        "elapsed_seconds": None,
        "exit_code": None,
        "status": "NOT_STARTED",
        "stop_reason": None,
        "log_path": str(logs / "upstream-build.log"),
    }
    return source, env


def kill_group(pid: int, report: dict[str, Any]) -> None:
    events: list[dict[str, Any]] = report.setdefault("timeout_cleanup", [])
    for name, sig in (("SIGTERM", signal.SIGTERM),):
        try:
            os.killpg(pid, sig)
            events.append({"signal": name, "result": "sent", "utc": utc_now()})
        except ProcessLookupError:
            events.append({"signal": name, "result": "group_absent", "utc": utc_now()})
    # Keep the full grace interval even if the leader exits quickly: its
    # descendants may still be alive in the isolated process group.
    time.sleep(10)
    try:
        os.killpg(pid, signal.SIGKILL)
        events.append({"signal": "SIGKILL", "result": "sent_after_grace", "utc": utc_now()})
    except ProcessLookupError:
        events.append({"signal": "SIGKILL", "result": "group_absent_after_grace", "utc": utc_now()})


def run_build(source: Path, env: dict[str, str], report: dict[str, Any]) -> None:
    log_path = RUN_ROOT / "logs/upstream-build.log"
    build = report["build"]
    build["started_utc"] = utc_now()
    build["status"] = "RUNNING"
    atomic_json(RUN_ROOT / "run-result.json", report)
    print(f"BUILD_START {build['started_utc']} argv={build['argv']} cap={MAX_BUILD_SECONDS}s", flush=True)
    start = time.monotonic()
    deadline = start + MAX_BUILD_SECONDS
    timeout = False
    with log_path.open("xb") as log:
        process = subprocess.Popen(
            ["./mk", "-b", "coreboot", "x200_4mb"],
            cwd=source,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
        )
        build["pid"] = process.pid
        build["process_group_id"] = process.pid
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timeout = True
                    kill_group(process.pid, report)
                    break
                try:
                    return_code = process.wait(timeout=min(POLL_SECONDS, remaining))
                    build["exit_code"] = return_code
                    break
                except subprocess.TimeoutExpired:
                    elapsed = time.monotonic() - start
                    tail = log_path.read_text(encoding="utf-8", errors="replace")[-6000:].splitlines()[-8:]
                    build["last_heartbeat_utc"] = utc_now()
                    build["elapsed_seconds"] = round(elapsed, 3)
                    build["log_bytes"] = log_path.stat().st_size
                    build["log_tail"] = tail
                    atomic_json(RUN_ROOT / "run-result.json", report)
                    print(
                        f"BUILD_HEARTBEAT elapsed={elapsed:.1f}s log_bytes={build['log_bytes']} "
                        f"last={tail[-1] if tail else '(no output)'}",
                        flush=True,
                    )
        except BaseException as exc:
            build["status"] = "INTERRUPTED_OR_RUNNER_ERROR"
            build["stop_reason"] = f"runner exception after launch: {type(exc).__name__}: {exc}"
            kill_group(process.pid, report)
            try:
                build["exit_code"] = process.wait(timeout=30)
                build["leader_reaped"] = True
            except subprocess.TimeoutExpired:
                build["leader_reaped"] = False
            build["ended_utc"] = utc_now()
            build["elapsed_seconds"] = round(time.monotonic() - start, 3)
            raise
        if timeout:
            try:
                build["exit_code"] = process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                build["leader_reaped"] = False
            else:
                build["leader_reaped"] = True
        else:
            build["leader_reaped"] = True
        log.flush()
        os.fsync(log.fileno())
    build["ended_utc"] = utc_now()
    build["elapsed_seconds"] = round(time.monotonic() - start, 3)
    build["log_bytes"] = log_path.stat().st_size
    build["log_sha256"] = sha256_file(log_path)
    if timeout:
        build["status"] = "TIMED_OUT"
        build["stop_reason"] = f"{MAX_BUILD_SECONDS}-second wall cap; SIGTERM then unconditional SIGKILL to isolated process group after 10-second grace"
    elif build["exit_code"] == 0:
        build["status"] = "EXITED_ZERO"
    else:
        build["status"] = "EXITED_NONZERO"
        build["stop_reason"] = "upstream build command returned a nonzero exit code"
    print(f"BUILD_END {build['ended_utc']} status={build['status']} exit={build['exit_code']} elapsed={build['elapsed_seconds']}s", flush=True)


def _create_validation_workspace(report: dict[str, Any]) -> tuple[Path, Path, dict[str, str]]:
    from autoloader.rom_builder.artifact_engine import _BUILD_GIT_CONFIG_BYTES

    validation_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    validation_root = RUN_ROOT / f"validation-{validation_id}"
    if validation_root.exists() or validation_root.is_symlink():
        raise RuntimeError(f"refusing to reuse validation workspace: {validation_root}")
    validation_root.mkdir(mode=0o700)
    os.chmod(validation_root, 0o700)
    logs_dir = validation_root / "logs"
    temporary = validation_root / "tmp"
    logs_dir.mkdir(mode=0o700)
    temporary.mkdir(mode=0o700)
    source_gitconfig = RUN_ROOT / "gitconfig"
    if source_gitconfig.is_symlink() or not source_gitconfig.is_file():
        raise RuntimeError("original operation Git configuration is missing or unsafe")
    source_gitconfig_hash = sha256_file(source_gitconfig)
    if source_gitconfig_hash != report.get("gitconfig_sha256"):
        raise RuntimeError("original operation Git configuration changed after the build")
    gitconfig = validation_root / "gitconfig"
    with gitconfig.open("xb") as stream:
        stream.write(_BUILD_GIT_CONFIG_BYTES)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(gitconfig, 0o600)
    if sha256_file(gitconfig) != source_gitconfig_hash:
        raise RuntimeError("validation Git configuration differs from the original operation identity")

    original_environment = report.get("environment")
    if not isinstance(original_environment, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in original_environment.items()
    ):
        raise RuntimeError("recorded build environment is malformed")
    environment = dict(original_environment)
    environment.update({
        "HOME": str(validation_root),
        "TMPDIR": str(temporary),
        "GIT_CONFIG_GLOBAL": str(gitconfig),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    report["validation_only"] = {
        "mode": "VALIDATE_EXISTING_BUILD_ONLY",
        "status": "RUNNING",
        "started_utc": utc_now(),
        "runner_path": str(Path(__file__).resolve(strict=True)),
        "runner_sha256": sha256_file(Path(__file__).resolve(strict=True)),
        "operation_root": str(validation_root),
        "logs_relative_path": "validation/logs",
        "gitconfig_sha256": source_gitconfig_hash,
        "build_command_invoked": False,
        "artifactengine_build_invoked": False,
        "firmware_files_copied_into_repo": False,
        "hardware_or_media_operations": "NONE",
        "command_records": [],
        "evidence_log_files": [],
    }
    return validation_root, logs_dir, environment


def inspect_outputs(
    source: Path,
    env: dict[str, str],
    report: dict[str, Any],
    *,
    previous_rom_files: Sequence[dict[str, Any]] | None = None,
) -> None:
    from autoloader.rom_builder.artifact_engine import (
        X200_4MB,
        ArtifactEngine,
        CBFSInspectionRecord,
        lbmk_expected_output_paths,
        parse_cbfs_parseable_output,
        select_exact_lbmk_artifact,
        sha256_file as engine_sha256,
    )

    output_dir = source / "bin/x200_4mb"
    if output_dir.is_symlink() or not output_dir.is_dir():
        report["output_validation"] = {"status": "NO_OUTPUT_DIRECTORY", "rom_count": 0, "cbfs": []}
        return
    expected = lbmk_expected_output_paths(source, X200_4MB)
    actual_files = sorted(p for p in output_dir.iterdir() if p.suffix.lower() == ".rom")
    actual = sorted(p.relative_to(source).as_posix() for p in actual_files)
    files = []
    for path in actual_files:
        record: dict[str, Any] = {"relative_path": path.relative_to(source).as_posix()}
        if path.is_symlink() or not path.is_file():
            record["validation"] = "REJECTED_NONREGULAR_OR_SYMLINK"
        else:
            record.update({"size_bytes": path.stat().st_size, "sha256": sha256_file(path)})
            record["validation"] = "SIZE_OK" if path.stat().st_size == X200_4MB.expected_size_bytes else "SIZE_MISMATCH"
        files.append(record)

    previous_by_path = {
        str(item.get("relative_path")): item
        for item in (previous_rom_files or ())
        if isinstance(item, dict) and isinstance(item.get("relative_path"), str)
    }
    result: dict[str, Any] = {
        "status": "PENDING",
        "expected_rom_count": len(expected),
        "expected_rom_relative_paths": list(expected),
        "actual_rom_count": len(actual),
        "actual_rom_relative_paths": actual,
        "exact_inventory": actual == sorted(expected),
        "expected_size_bytes_each": X200_4MB.expected_size_bytes,
        "rom_files": files,
        "selected_rom_relative_path": X200_4MB.output_relative_path,
        "cbfstool_inspections": [],
        "original_build_hashes_unchanged": None,
    }
    report["output_validation"] = result
    if actual != sorted(expected) or len(files) != 31 or any(item.get("validation") != "SIZE_OK" for item in files):
        result["status"] = "INCOMPLETE_OR_INVALID_ROM_INVENTORY"
        return
    if previous_rom_files is not None:
        if len(previous_by_path) != 31 or set(previous_by_path) != set(actual):
            result["status"] = "PREVIOUS_OUTPUT_HASH_INVENTORY_INVALID"
            return
        for item in files:
            previous = previous_by_path[item["relative_path"]]
            if (
                previous.get("size_bytes") != item.get("size_bytes")
                or previous.get("sha256") != item.get("sha256")
                or previous.get("validation") != "SIZE_OK"
            ):
                item["unchanged_since_original_build_record"] = False
                result["original_build_hashes_unchanged"] = False
                result["status"] = "ROM_BYTES_CHANGED_SINCE_ORIGINAL_BUILD_RECORD"
                return
            item["unchanged_since_original_build_record"] = True
        result["original_build_hashes_unchanged"] = True

    engine = ArtifactEngine()
    validation_root, logs_dir, validation_env = _create_validation_workspace(report)
    command_records = []
    original_environment = os.environ.copy()
    nested_before = None
    try:
        os.environ.clear()
        os.environ.update(validation_env)
        nested_before = engine._validate_lbmk_nested_coreboot(source, logs_dir, command_records)
        result["pinned_nested_repositories"] = [asdict(item) for item in nested_before]
        nested_payload = json.dumps(
            result["pinned_nested_repositories"], sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        nested_digest = hashlib.sha256(nested_payload).hexdigest()
        result["pinned_nested_repositories_sha256"] = nested_digest

        tool = source / "elf/coreboot/default/cbfstool"
        tool_parts = (source / "elf", source / "elf/coreboot", source / "elf/coreboot/default", tool)
        if any(item.is_symlink() for item in tool_parts) or not tool.is_file() or not os.access(tool, os.X_OK):
            raise RuntimeError("pinned nested cbfstool path is missing, symlinked or non-executable")
        tool_resolved = tool.resolve(strict=True)
        if not tool_resolved.is_relative_to(source.resolve(strict=True)):
            raise RuntimeError("pinned nested cbfstool resolves outside the disposable source checkout")
        tool_hash_before = engine_sha256(tool)
        result["cbfstool"] = {"path": str(tool), "sha256_before": tool_hash_before}
        coreboot = next(item for item in nested_before if item.role == "coreboot")
        region_size = int(dict(X200_4MB.config_expectations)["CONFIG_CBFS_SIZE"], 0)
        cbfs_records: list[dict[str, Any]] = []
        for relative in expected:
            rom = source.joinpath(*relative.split("/"))
            rom_components = (source / "bin", output_dir, rom)
            if any(item.is_symlink() for item in rom_components) or not rom.is_file():
                cbfs_records.append({"relative_path": relative, "status": "REJECTED_UNSAFE_ROM_PATH"})
                continue
            argv = (str(tool), str(rom), "print", "-k")
            record: dict[str, Any] = {"relative_path": relative, "argv": list(argv)}
            try:
                output = engine._run_readonly(argv, source, logs_dir, command_records)
                command_record_value = command_records[-1]
                inventory = parse_cbfs_parseable_output(
                    output,
                    required_payload="fallback/payload",
                    region_size_bytes=region_size,
                    required_payload_type="simple elf",
                )
                typed = CBFSInspectionRecord(
                    tool_relative_path=tool.relative_to(source).as_posix(),
                    tool_sha256=tool_hash_before,
                    source_repository_url=coreboot.repository_url,
                    source_revision=coreboot.resulting_revision,
                    source_tree_id=coreboot.resulting_tree_id,
                    region="whole_flash",
                    required_payload="fallback/payload",
                    inventory=inventory,
                    source_file_hashes=coreboot.source_file_hashes,
                    nested_repositories=nested_before,
                )
                record.update({
                    "status": "PARSED",
                    "stdout_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
                    "command_record": asdict(command_record_value),
                    "typed_cbfs_inspection": asdict(typed),
                    "nested_repository_evidence_sha256": nested_digest,
                })
            except Exception as exc:
                record.update({"status": "CBFSTOOL_OR_PARSE_FAILED", "error": f"{type(exc).__name__}: {exc}"})
                if command_records and command_records[-1].argv == argv:
                    record["command_record"] = asdict(command_records[-1])
            cbfs_records.append(record)
        result["cbfstool_inspections"] = cbfs_records
        nested_after = engine._validate_lbmk_nested_coreboot(source, logs_dir, command_records)
        result["pinned_nested_repositories_after_cbfs"] = [asdict(item) for item in nested_after]
        result["nested_repositories_unchanged_during_cbfs"] = nested_after == nested_before
        if nested_after != nested_before:
            raise RuntimeError("nested lbmk source/overlay state changed during CBFS inspection")
        tool_hash_after = engine_sha256(tool)
        result["cbfstool"]["sha256_after"] = tool_hash_after
        if tool_hash_after != tool_hash_before:
            raise RuntimeError("pinned nested cbfstool bytes changed during CBFS inspection")
        selected = select_exact_lbmk_artifact(source, X200_4MB)
        result["selected_rom_sha256"] = engine_sha256(selected)
        all_parsed = len(cbfs_records) == 31 and all(item.get("status") == "PARSED" for item in cbfs_records)
        result["status"] = (
            "ALL_31_SIZE_AND_CBFS_CHECKS_PASSED"
            if all_parsed and result.get("original_build_hashes_unchanged", True) and nested_after == nested_before
            else "CBFS_CHECK_FAILURE"
        )
    except Exception as exc:  # record a diagnostic failure without masking the upstream result
        result["status"] = "STRUCTURAL_VALIDATION_FAILED"
        result["structural_validation_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        os.environ.clear()
        os.environ.update(original_environment)
        validation_record = report.get("validation_only", {})
        validation_record["ended_utc"] = utc_now()
        validation_record["status"] = (
            "VALIDATION_ONLY_PASSED"
            if result.get("status") == "ALL_31_SIZE_AND_CBFS_CHECKS_PASSED"
            else "VALIDATION_ONLY_INCOMPLETE"
        )
        validation_record["command_records"] = [asdict(item) for item in command_records]
        validation_record["evidence_log_files"] = [
            {
                "source_relative_path": item.log_relative_path,
                "evidence_relative_path": (
                    f"validation-logs/{validation_root.name}/{Path(item.log_relative_path).name}"
                ),
                "sha256": item.log_sha256,
            }
            for item in command_records
        ]
        validation_record["command_count"] = len(command_records)
        validation_record["scratch_outputs_modified"] = False
        validation_record["scratch_logs_written"] = len(command_records) > 0
        validation_record["validation_workspace"] = str(validation_root)


def copy_evidence(report: dict[str, Any]) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    source_report = RUN_ROOT / "run-result.json"
    atomic_json(source_report, report)
    for name in ("run-result.json", "upstream-build.log", "gitconfig"):
        candidate = RUN_ROOT / ("logs/upstream-build.log" if name == "upstream-build.log" else name)
        if candidate.is_file() and not candidate.is_symlink():
            destination = EVIDENCE / name
            if name != "run-result.json" and destination.exists() and sha256_file(destination) != sha256_file(candidate):
                raise RuntimeError(f"preserved build evidence changed; refusing to replace {destination.name}")
            shutil.copyfile(candidate, destination)
    validation_info = report.get("validation_only", {})
    validation_root_value = validation_info.get("operation_root") if isinstance(validation_info, dict) else None
    validation_root = Path(validation_root_value) if isinstance(validation_root_value, str) else None
    try:
        if validation_root is None:
            raise ValueError
        validation_root.relative_to(RUN_ROOT)
    except ValueError:
        validation_root = None
    validation_logs = validation_root / "logs" if validation_root is not None else None
    if validation_logs is not None and validation_logs.is_dir() and not validation_logs.is_symlink():
        evidence_logs = EVIDENCE / "validation-logs" / validation_root.name
        evidence_logs.mkdir(exist_ok=True)
        for candidate in sorted(validation_logs.iterdir()):
            if candidate.is_symlink() or not candidate.is_file() or candidate.suffix != ".log":
                raise RuntimeError(f"validation log directory contains an unsafe entry: {candidate.name}")
            destination = evidence_logs / candidate.name
            if destination.exists() and sha256_file(destination) != sha256_file(candidate):
                raise RuntimeError(f"preserved validation log changed: {candidate.name}")
            if not destination.exists():
                shutil.copyfile(candidate, destination)
    entries = []
    for path in sorted(EVIDENCE.rglob("*")):
        if path.name == "SHA256SUMS" or not path.is_file() or path.is_symlink():
            continue
        entries.append(f"{sha256_file(path)}  {path.relative_to(EVIDENCE).as_posix()}")
    (EVIDENCE / "SHA256SUMS").write_text("\n".join(entries) + "\n", encoding="ascii", newline="\n")


def validate_existing_build() -> int:
    sys.path.insert(0, str(REPO))
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True

    report_path = RUN_ROOT / "run-result.json"
    if RUN_ROOT.is_symlink() or not RUN_ROOT.is_dir() or report_path.is_symlink() or not report_path.is_file():
        raise RuntimeError("existing X200 scratch/result path is missing or unsafe")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise RuntimeError("existing build result must be a JSON object")
    if report.get("fresh_scratch_root") != str(RUN_ROOT) or report.get("artifactengine_build_invoked") is not False:
        raise RuntimeError("existing result does not bind the exact diagnostic scratch or no-ArtifactEngine-build claim")
    if report.get("complete_manifest_published") is not False or report.get("firmware_files_copied_into_repo") is not False:
        raise RuntimeError("existing result contains an unexpected publication/copy claim")
    build = report.get("build")
    if (
        not isinstance(build, dict)
        or build.get("argv") != ["./mk", "-b", "coreboot", "x200_4mb"]
        or build.get("cwd") != str(RUN_ROOT / "work/lbmk")
        or build.get("status") != "EXITED_ZERO"
        or build.get("exit_code") != 0
    ):
        raise RuntimeError("existing scratch does not record the exact successful pinned upstream command")
    source = RUN_ROOT / "work/lbmk"
    if source.is_symlink() or not source.is_dir() or source.resolve(strict=True) != source:
        raise RuntimeError("existing pinned lbmk checkout is missing or resolves through a symlink")
    previous_output = report.get("output_validation")
    if not isinstance(previous_output, dict):
        raise RuntimeError("existing build result lacks its original output inventory record")
    previous_rom_files = previous_output.get("rom_files")
    if not isinstance(previous_rom_files, list) or len(previous_rom_files) != 31:
        raise RuntimeError("original run did not record all 31 output ROM hashes; refusing validation-only update")
    previous_validation = {
        "status": previous_output.get("status"),
        "structural_validation_error": previous_output.get("structural_validation_error"),
        "cbfstool_inspection_count": len(previous_output.get("cbfstool_inspections", []))
        if isinstance(previous_output.get("cbfstool_inspections", []), list) else None,
    }
    report["previous_output_validation_attempt"] = previous_validation
    prior_validation = report.get("validation_only")
    prior_validation_attempts = report.get("previous_validation_only_attempts", [])
    if isinstance(prior_validation, dict):
        if not isinstance(prior_validation_attempts, list):
            raise RuntimeError("prior validation attempt history is malformed")
        report["previous_validation_only_attempts"] = [*prior_validation_attempts, prior_validation]
    log_path = RUN_ROOT / "logs" / "upstream-build.log"
    if log_path.is_symlink() or not log_path.is_file() or sha256_file(log_path) != build.get("log_sha256"):
        raise RuntimeError("original upstream build log no longer matches its captured run result")
    build_log_hash_before = sha256_file(log_path)
    try:
        inspect_outputs(source, report.get("environment", {}), report, previous_rom_files=previous_rom_files)
        if sha256_file(log_path) != build_log_hash_before:
            raise RuntimeError("original upstream build log changed during validation-only inspection")
        report["disk_free_bytes_after"] = shutil.disk_usage(RUN_ROOT.parent).free
        report["validation_only"]["original_build_log_sha256_before"] = build_log_hash_before
        report["validation_only"]["original_build_log_sha256_after"] = sha256_file(log_path)
        report["validation_only"]["completed_utc"] = utc_now()
        report["final_classification"] = (
            "UNACCEPTED WSL UPSTREAM BUILD DIAGNOSTIC; 31 EXISTING ROMS PASSED SIZE AND PINNED CBFS STRUCTURAL VALIDATION; NOT FLASH-READY; NO ARTIFACTENGINE COMPLETE"
            if report.get("output_validation", {}).get("status") == "ALL_31_SIZE_AND_CBFS_CHECKS_PASSED"
            else "UNACCEPTED WSL UPSTREAM BUILD DIAGNOSTIC; VALIDATION INCOMPLETE; NO ARTIFACTENGINE COMPLETE; NOT FLASH-READY"
        )
        atomic_json(RUN_ROOT / "run-result.json", report)
        copy_evidence(report)
        print(json.dumps({
            "validation_status": report.get("validation_only", {}).get("status"),
            "output_status": report.get("output_validation", {}).get("status"),
            "cbfs_count": len(report.get("output_validation", {}).get("cbfstool_inspections", [])),
            "validation_command_count": report.get("validation_only", {}).get("command_count"),
            "classification": report.get("final_classification"),
            "complete_manifest_published": report.get("complete_manifest_published"),
        }, sort_keys=True), flush=True)
        return 0 if report.get("output_validation", {}).get("status") == "ALL_31_SIZE_AND_CBFS_CHECKS_PASSED" else 2
    except Exception as exc:
        report["validation_only"] = report.get("validation_only", {})
        report["validation_only"].update({
            "status": "VALIDATION_ONLY_INCOMPLETE",
            "error": f"{type(exc).__name__}: {exc}",
            "completed_utc": utc_now(),
            "build_command_invoked": False,
            "artifactengine_build_invoked": False,
            "hardware_or_media_operations": "NONE",
        })
        report["final_classification"] = "UNACCEPTED WSL UPSTREAM BUILD DIAGNOSTIC; POST-BUILD VALIDATION INCOMPLETE; NO ARTIFACTENGINE COMPLETE; NOT FLASH-READY"
        if RUN_ROOT.is_dir() and not RUN_ROOT.is_symlink():
            atomic_json(RUN_ROOT / "run-result.json", report)
            copy_evidence(report)
        print(report["validation_only"]["error"], file=sys.stderr, flush=True)
        return 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Pinned X200 upstream build diagnostic")
    parser.add_argument(
        "--validate-existing",
        action="store_true",
        help="validate only the preserved existing scratch outputs; never run ./mk or ArtifactEngine.build",
    )
    args = parser.parse_args()
    if args.validate_existing:
        return validate_existing_build()

    sys.path.insert(0, str(REPO))
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    from autoloader.rom_builder.artifact_engine import sha256_file as engine_sha256

    script_path = Path(__file__).resolve(strict=True)
    report: dict[str, Any] = {
        "classification": "UNACCEPTED WSL UPSTREAM COMPILER DIAGNOSTIC; NOT FLASH-READY; NO ARTIFACTENGINE COMPLETE",
        "run_id": RUN_ROOT.name,
        "started_utc": utc_now(),
        "runner_path": str(script_path),
        "runner_sha256": engine_sha256(script_path),
        "repo_checkout": str(REPO),
        "old_scratch_preserved_read_only": str(OLD_RUN),
        "fresh_scratch_root": str(RUN_ROOT),
        "source_repository": SOURCE_ORIGIN,
        "pinned_lbmK_revision": LBMK_REVISION,
        "pinned_lbmK_tree": LBMK_TREE,
        "hardware_or_media_operations": "NONE",
        "artifactengine_build_invoked": False,
        "complete_manifest_published": False,
        "firmware_files_copied_into_repo": False,
        "build": {},
    }
    try:
        source, env = prepare(report)
        report["preflight_status"] = "PASSED"
        report["build"]["status"] = "NOT_STARTED"
        run_build(source, env, report)
        inspect_outputs(source, env, report)
        report["disk_free_bytes_after"] = shutil.disk_usage(RUN_ROOT.parent).free
        report["ended_utc"] = utc_now()
        report["final_classification"] = (
            "UNACCEPTED WSL UPSTREAM BUILD DIAGNOSTIC; GENERATED ROM CHECKS PASSED; NOT FLASH-READY; NO ARTIFACTENGINE COMPLETE"
            if report.get("output_validation", {}).get("status") == "ALL_31_SIZE_AND_CBFS_CHECKS_PASSED"
            else "UNACCEPTED WSL UPSTREAM BUILD DIAGNOSTIC; NO VALIDATED ARTIFACT; NOT FLASH-READY"
        )
        atomic_json(RUN_ROOT / "run-result.json", report)
        copy_evidence(report)
        return 0 if report["build"]["status"] == "EXITED_ZERO" and report.get("output_validation", {}).get("status") == "ALL_31_SIZE_AND_CBFS_CHECKS_PASSED" else 2
    except Exception as exc:
        report["preflight_status"] = report.get("preflight_status", "FAILED")
        report["runner_error"] = f"{type(exc).__name__}: {exc}"
        report["ended_utc"] = utc_now()
        report["final_classification"] = "INCOMPLETE WSL DIAGNOSTIC; NO ARTIFACTENGINE COMPLETE; NO HARDWARE ACTIONS"
        if RUN_ROOT.exists() and not RUN_ROOT.is_symlink():
            atomic_json(RUN_ROOT / "run-result.json", report)
            copy_evidence(report)
        print(report["runner_error"], file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
