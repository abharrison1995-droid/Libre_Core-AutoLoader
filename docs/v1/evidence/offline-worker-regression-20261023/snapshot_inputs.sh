#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

case "${1:-}" in
    source)
        cd "$ROOT"
        printf 'HEAD\t%s\n' "$(git rev-parse HEAD)"
        for path in pyproject.toml pytest.ini setup.cfg tox.ini; do
            [[ ! -f "$path" ]] || sha256sum -- "$path"
        done
        find autoloader usb_builder tests -type f \
            ! -path '*/__pycache__/*' ! -name '*.pyc' -print0 |
            sort -z | xargs -0 sha256sum
        ;;
    environment)
        python_path="${2:?environment snapshot requires Python path}"
        printf 'python_path\t%s\n' "$python_path"
        uname -a
        cat /etc/os-release
        "$python_path" --version
        "$python_path" -m pytest --version
        "$python_path" -m pip freeze
        ;;
    *)
        printf 'usage: %s source | environment PYTHON_PATH\n' "$0" >&2
        exit 2
        ;;
esac
