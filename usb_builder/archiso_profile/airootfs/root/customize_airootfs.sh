#!/usr/bin/env bash
# Archiso 91 still invokes this legacy hook; the pinned package source marks it
# deprecated. Re-evaluate the installation mechanism before updating the pin.
set -Eeuo pipefail

wheel_dir="/opt/autoloader/wheelhouse"
shopt -s nullglob
wheels=("$wheel_dir"/*.whl)
if [[ ${#wheels[@]} -ne 1 || -L "${wheels[0]:-}" ]]; then
    printf 'ERROR: expected exactly one regular AutoLoader wheel in %s\n' "$wheel_dir" >&2
    exit 71
fi

python -m venv --system-site-packages /opt/autoloader/venv
PIP_NO_INDEX=1 \
PIP_CONFIG_FILE=/dev/null \
PIP_DISABLE_PIP_VERSION_CHECK=1 \
/opt/autoloader/venv/bin/python -m pip install \
    --no-index --no-deps --no-cache-dir "${wheels[0]}"

test -x /opt/autoloader/venv/bin/python
test -x /opt/autoloader/venv/bin/autoloader
test -x /opt/autoloader/start.sh
test -f /etc/systemd/system/autoloader.service
test -f /etc/systemd/system/multi-user.target.d/autoloader.conf
grep -Fxq 'Wants=autoloader.service' /etc/systemd/system/multi-user.target.d/autoloader.conf

systemd_dir="/etc/systemd/system"
ensure_systemd_link() {
    local target="$1"
    local link="$2"
    local current
    if [[ -L "$link" ]]; then
        current="$(readlink -- "$link")"
        if [[ "$current" != "$target" ]]; then
            printf 'ERROR: unexpected systemd link %s -> %s (expected %s)\n' "$link" "$current" "$target" >&2
            exit 72
        fi
    elif [[ -e "$link" ]]; then
        printf 'ERROR: refusing to replace non-link systemd path %s\n' "$link" >&2
        exit 72
    else
        ln -s -- "$target" "$link"
    fi
}

mkdir -p "$systemd_dir/multi-user.target.wants"
ensure_systemd_link "../autoloader.service" "$systemd_dir/multi-user.target.wants/autoloader.service"
test -L "$systemd_dir/multi-user.target.wants/autoloader.service"
test "$(readlink -- "$systemd_dir/multi-user.target.wants/autoloader.service")" = "../autoloader.service"

/opt/autoloader/venv/bin/autoloader --help >/dev/null
/opt/autoloader/venv/bin/python - "${wheels[0]}" <<'PY'
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
import sys

required_imports = ("autoloader", "click", "psutil", "rich", "textual")
modules = {name: importlib.import_module(name) for name in required_imports}
importlib.import_module("autoloader.__main__")
distribution = importlib.metadata.distribution("libre-core-autoloader")
entrypoints = [
    item.value
    for item in distribution.entry_points
    if item.group == "console_scripts" and item.name == "autoloader"
]
if entrypoints != ["autoloader.__main__:main"]:
    raise SystemExit("AutoLoader console entrypoint is missing or ambiguous")
if not str(Path(modules["autoloader"].__file__).resolve()).startswith(str(Path(sys.prefix).resolve()) + "/"):
    raise SystemExit("AutoLoader package is not installed in its application venv")
wheel = Path(sys.argv[1])
wheel_sha256 = hashlib.sha256(wheel.read_bytes()).hexdigest()
marker = {
    "schema_version": 1,
    "status": "APP_INSTALLED_AND_RUNTIME_CHECKED",
    "distribution_name": distribution.metadata["Name"],
    "distribution_version": distribution.version,
    "entrypoint": entrypoints[0],
    "runtime_imports": sorted(modules),
    "wheel_sha256": wheel_sha256,
}
marker_path = Path("/var/lib/autoloader/image-install.json")
marker_path.parent.mkdir(parents=True, exist_ok=True)
marker_path.write_text(json.dumps(marker, sort_keys=True) + "\n", encoding="utf-8")
PY
