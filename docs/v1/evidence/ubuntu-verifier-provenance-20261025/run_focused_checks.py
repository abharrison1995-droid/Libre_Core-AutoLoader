from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
TEST = ROOT / "test_verify_bundle.py"
BASE = ROOT / "pytest-tmp-final"
JUNIT = ROOT / "focused-final.junit.xml"
LOG = ROOT / "focused-final.pytest.log"
EXIT = ROOT / "focused-final.pytest.exit"
ARGV = [
    sys.executable,
    "-m",
    "pytest",
    "-p",
    "no:cacheprovider",
    f"--basetemp={BASE}",
    f"--junitxml={JUNIT}",
    "-q",
    "--color=no",
    str(TEST),
]

result = subprocess.run(ARGV, capture_output=True, text=True, check=False)
LOG.write_text(
    "ARGV_JSON=" + json.dumps(ARGV, ensure_ascii=True) + "\n"
    + result.stdout
    + result.stderr,
    encoding="utf-8",
)
EXIT.write_text(f"{result.returncode}\n", encoding="ascii")
sys.stdout.write(result.stdout)
sys.stderr.write(result.stderr)
raise SystemExit(result.returncode)
