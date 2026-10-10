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

/opt/autoloader/venv/bin/python -c \
    'import autoloader, click, psutil, rich, textual; print("AutoLoader wheel and locked runtime imports verified")'
