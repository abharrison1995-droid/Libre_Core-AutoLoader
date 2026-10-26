#!/usr/bin/env python3
"""Validate and aggregate one complete, source-pinned per-module test run."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
JUNIT_DIR = HERE / "junit"
RESULTS_TSV = HERE / "module-results.tsv"
OUTPUT = HERE / "module-results.json"
EXIT_STATUS = HERE / "module-run.exit"
SOURCE_MANIFEST = HERE / "source-manifest.sha256"
ENV_MANIFEST = HERE / "environment.txt"


def fail(message: str) -> int:
    # Keep the bytes of an older report, but remove it from the canonical path
    # so a failed aggregation cannot leave a stale success-looking result.
    if OUTPUT.is_file():
        try:
            output_digest = digest(OUTPUT)
            archive = HERE / f"module-results.stale-{output_digest}.json"
            suffix = 2
            while archive.exists() and archive.read_bytes() != OUTPUT.read_bytes():
                archive = HERE / f"module-results.stale-{output_digest}-{suffix}.json"
                suffix += 1
            if archive.exists():
                OUTPUT.unlink()
            else:
                OUTPUT.replace(archive)
            print(f"Preserved prior aggregate as {archive.name}", file=sys.stderr)
        except OSError as exc:
            print(
                f"WARNING: could not quarantine prior aggregate {OUTPUT.name}: {exc}; "
                "do not treat it as the result of this failed aggregation",
                file=sys.stderr,
            )
    print(f"ERROR: {message}", file=sys.stderr)
    return 1


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    required_files = (RESULTS_TSV, EXIT_STATUS, SOURCE_MANIFEST, ENV_MANIFEST)
    missing = [path.name for path in required_files if not path.is_file()]
    if missing:
        return fail(f"run is incomplete; missing {', '.join(missing)}")

    try:
        run_exit = int(EXIT_STATUS.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return fail("module-run.exit is not a valid integer status")
    if run_exit != 0:
        return fail(f"module runner exited {run_exit}")

    try:
        recorded_source = SOURCE_MANIFEST.read_bytes()
        recorded_environment = ENV_MANIFEST.read_bytes()
        environment_header = recorded_environment.decode("utf-8").splitlines()[0]
        key, python_path = environment_header.split("\t", 1)
        if key != "python_path":
            return fail("environment manifest has no Python path header")
        current_source = subprocess.run(
            ["bash", str(HERE / "snapshot_inputs.sh"), "source"],
            cwd=REPO,
            check=True,
            capture_output=True,
        ).stdout
        current_environment = subprocess.run(
            ["bash", str(HERE / "snapshot_inputs.sh"), "environment", python_path],
            cwd=REPO,
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError, UnicodeError, IndexError, ValueError) as exc:
        return fail(f"could not recheck run input identity: {exc}")
    if current_source != recorded_source:
        return fail("current source differs from the regression input manifest")
    if current_environment != recorded_environment:
        return fail("current environment differs from the regression environment manifest")

    expected = {path.stem for path in (REPO / "tests").glob("test_*.py") if path.is_file()}
    if not expected:
        return fail("repository has no top-level test modules")

    rows: dict[str, tuple[int, str, str]] = {}
    try:
        lines = RESULTS_TSV.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return fail(f"cannot read module results: {exc}")
    for line_number, line in enumerate(lines, start=1):
        cells = line.split("\t", 3)
        if len(cells) != 4:
            return fail(f"malformed TSV row {line_number}")
        name, raw_exit, summary, junit_path = cells
        if name in rows:
            return fail(f"duplicate TSV row for {name}")
        if name not in expected:
            return fail(f"unexpected test module in TSV: {name}")
        try:
            exit_code = int(raw_exit)
        except ValueError:
            return fail(f"invalid exit code for {name}")
        if exit_code != 0:
            return fail(f"{name} exited {exit_code}")
        if not summary.strip():
            return fail(f"missing pytest summary for {name}")
        if junit_path != f"junit/{name}.xml":
            return fail(f"unexpected JUnit path for {name}: {junit_path}")
        rows[name] = (exit_code, summary, junit_path)

    missing_modules = sorted(expected - rows.keys())
    if missing_modules:
        return fail(f"incomplete module inventory; missing: {', '.join(missing_modules)}")

    modules: list[dict[str, object]] = []
    for name in sorted(expected):
        _, summary, junit_path = rows[name]
        report_path = HERE / junit_path
        if not report_path.is_file():
            return fail(f"missing JUnit report for {name}")
        try:
            root = ET.parse(report_path).getroot()
        except (ET.ParseError, OSError) as exc:
            return fail(f"invalid JUnit report for {name}: {exc}")
        suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
        if not suites:
            return fail(f"JUnit report for {name} contains no testsuite")
        totals = {key: 0 for key in ("tests", "failures", "errors", "skipped")}
        try:
            for suite in suites:
                for key in totals:
                    totals[key] += int(suite.attrib.get(key, "0"))
        except ValueError:
            return fail(f"JUnit report for {name} has invalid count attributes")
        if totals["tests"] <= 0:
            return fail(f"JUnit report for {name} records no tests")
        if any(value < 0 for value in totals.values()):
            return fail(f"JUnit report for {name} has negative count attributes")
        if totals["failures"] or totals["errors"]:
            return fail(f"JUnit report for {name} records failures or errors")
        cases = list(root.iter("testcase"))
        if len(cases) != totals["tests"]:
            return fail(
                f"JUnit testcase count disagrees with suite totals for {name}: "
                f"{len(cases)} != {totals['tests']}"
            )
        actual_failures = sum(case.find("failure") is not None for case in cases)
        actual_errors = sum(case.find("error") is not None for case in cases)
        if actual_failures != totals["failures"] or actual_errors != totals["errors"]:
            return fail(f"JUnit failure/error records disagree with suite totals for {name}")
        passed = totals["tests"] - totals["skipped"]
        if passed < 0:
            return fail(f"JUnit skip count exceeds test count for {name}")
        skip_records = []
        for case in cases:
            skipped = case.find("skipped")
            if skipped is not None:
                skip_records.append(
                    {
                        "test": case.attrib.get("name", ""),
                        "classname": case.attrib.get("classname", ""),
                        "reason": skipped.attrib.get("message", ""),
                    }
                )
        if len(skip_records) != totals["skipped"]:
            return fail(f"JUnit skip records disagree with the skip count for {name}")
        modules.append(
            {
                "module": name,
                "exit_code": 0,
                "summary": summary,
                **totals,
                "passed": passed,
                "junit": junit_path,
                "skips": skip_records,
            }
        )

    result = {
        "format": 2,
        "runner": "source-pinned pytest per top-level test module; one fresh basetemp per process",
        "module_count": len(modules),
        "tests": sum(int(module["tests"]) for module in modules),
        "passed": sum(int(module["passed"]) for module in modules),
        "skipped": sum(int(module["skipped"]) for module in modules),
        "failures": 0,
        "errors": 0,
        "runner_exit": run_exit,
        "source_manifest_sha256": digest(SOURCE_MANIFEST),
        "environment_sha256": digest(ENV_MANIFEST),
        "modules": modules,
    }
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT)
    print(json.dumps({key: value for key, value in result.items() if key != "modules"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
