#!/usr/bin/env python3
"""Post-run ANSI normalization and marker counts for retry16's raw serial log."""

import hashlib
import json
import re
from pathlib import Path


bundle = Path(__file__).resolve().parent
raw = (bundle / "serial-output.log").read_bytes()
text = raw.decode("utf-8", errors="replace")
ansi = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
normalized = ansi.sub("", text)
normalized_bytes = normalized.encode("utf-8")

failure_lines = re.findall(
    r"\[FAILED\]\s+Failed to start Namespace Resource Manager", normalized
)
normal_targets = re.findall(
    r"Reached target (?:Multi-User System|Graphical Interface)", normalized
)
emergency_or_fatal = re.findall(
    r"Reached target Emergency Mode|You are in emergency mode|"
    r"Kernel panic - not syncing|Reached target Power-Off|Reached target Reboot",
    normalized,
    flags=re.IGNORECASE,
)

result = {
    "classification": "post-run audit of retry16 raw serial bytes; no guest input",
    "raw_serial_bytes": len(raw),
    "raw_serial_sha256": hashlib.sha256(raw).hexdigest(),
    "normalized_serial_bytes": len(normalized_bytes),
    "normalized_serial_sha256": hashlib.sha256(normalized_bytes).hexdigest(),
    "namespace_resource_manager_failure_count": len(failure_lines),
    "normal_startup_target_count": len(normal_targets),
    "emergency_or_fatal_terminal_marker_count": len(emergency_or_fatal),
    "method": (
        "Decode UTF-8 with replacement, remove ANSI single-character and CSI "
        "escape sequences using the retry14 audit regex, then count literal "
        "systemd failure/target markers; preserve raw serial unchanged."
    ),
}
(bundle / "postrun-scan.json").write_text(
    json.dumps(result, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps(result, indent=2))
