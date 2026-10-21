#!/usr/bin/env python3
import hashlib
import json
import re
from pathlib import Path

attempt = Path("/mnt/wsl/PHYSICALDRIVE1p2/opt/librecore-autoloader-v1-vm/native-arch-build-20261021/attempt-current-retry14-post-failure-observation")
raw_path = attempt / "serial-output.log"
normalized_path = attempt / "serial-output.normalized.log"
scan_path = attempt / "postrun-scan.json"
ansi = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
raw = raw_path.read_bytes()
text = raw.decode("utf-8", errors="replace")
normalized = ansi.sub("", text)
normalized_bytes = normalized.encode("utf-8")
normalized_path.write_bytes(normalized_bytes)
failures = re.findall(r"\[FAILED\]\s+Failed to start Namespace Resource Manager", normalized)
normal_targets = re.findall(r"Reached target (?:Multi-User System|Graphical Interface)", normalized)
emergency_targets = re.findall(
    r"Reached target Emergency Mode|You are in emergency mode|Kernel panic - not syncing|Reached target Power-Off|Reached target Reboot",
    normalized,
    flags=re.IGNORECASE,
)
lines = normalized.splitlines()
result = {
    "classification": "post-run audit of the raw retry14 serial transcript; no guest input",
    "raw_serial_bytes": len(raw),
    "raw_serial_sha256": hashlib.sha256(raw).hexdigest(),
    "normalized_serial_bytes": len(normalized_bytes),
    "normalized_serial_sha256": hashlib.sha256(normalized_bytes).hexdigest(),
    "normal_startup_target_count": len(normal_targets),
    "emergency_or_fatal_terminal_marker_count": len(emergency_targets),
    "namespace_resource_manager_failure_count": len(failures),
    "last_nonempty_lines": [line for line in lines if line.strip()][-12:],
    "method": "ANSI CSI/escape sequences removed before literal/regular-expression scans; raw transcript retained unchanged",
}
scan_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(json.dumps(result, indent=2))

