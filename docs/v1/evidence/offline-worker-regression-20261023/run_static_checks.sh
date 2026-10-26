#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
PYTHON="${PYTHON:-/home/alex/librecore-v1-test-venv/bin/python}"
LOG_DIR="$SCRIPT_DIR/static-logs"
RESULTS="$SCRIPT_DIR/static-checks.tsv"
EXIT_STATUS="$SCRIPT_DIR/static-checks.exit"
SOURCE_MANIFEST="$SCRIPT_DIR/source-manifest.sha256"
ENV_MANIFEST="$SCRIPT_DIR/environment.txt"

mkdir -p "$LOG_DIR"
: > "$RESULTS"
rm -f -- "$EXIT_STATUS"

source_tmp="$(mktemp)"
environment_tmp="$(mktemp)"
cleanup() {
    local code=$?
    printf '%s\n' "$code" > "$EXIT_STATUS"
    rm -f -- "$source_tmp" "$environment_tmp"
}
trap cleanup EXIT

if [[ ! -x "$PYTHON" ]]; then
    printf 'Configured Python is not executable: %s\n' "$PYTHON" >&2
    exit 2
fi
if ! bash "$SCRIPT_DIR/snapshot_inputs.sh" source > "$source_tmp" || \
    ! cmp -s "$source_tmp" "$SOURCE_MANIFEST" || \
    ! bash "$SCRIPT_DIR/snapshot_inputs.sh" environment "$PYTHON" > "$environment_tmp" || \
    ! cmp -s "$environment_tmp" "$ENV_MANIFEST"; then
    printf 'Static checks refused: source or environment differs from the captured regression run\n' >&2
    exit 2
fi

status=0
run_check() {
    local label="$1"
    shift
    local log_file="$LOG_DIR/$label.log"
    local command_text
    printf -v command_text '%q ' "$@"
    printf 'RUN\t%s\t%s\n' "$label" "$command_text"
    if "$@" > "$log_file" 2>&1; then
        result=0
    else
        result=$?
        status=1
    fi
    printf '%s\t%s\t%s\n' "$label" "$result" "$command_text" >> "$RESULTS"
    tail -n 1 "$log_file" | tr '\t\r\n' '   '
    printf '\n'
}

cd "$ROOT" || exit 2
run_check collect_only env PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp "$PYTHON" -m pytest \
    --collect-only -q -p no:cacheprovider
run_check mypy_linux env MYPY_CACHE_DIR=/tmp/lc-v1-mypy-native "$PYTHON" -m mypy \
    --no-incremental --cache-dir=/tmp/lc-v1-mypy-native autoloader usb_builder
run_check mypy_win32 env MYPY_CACHE_DIR=/tmp/lc-v1-mypy-win32 "$PYTHON" -m mypy \
    --no-incremental --platform win32 --cache-dir=/tmp/lc-v1-mypy-win32 autoloader usb_builder
run_check cli_help "$PYTHON" -m autoloader --help
run_check cli_flash_help "$PYTHON" -m autoloader flash --help
run_check cli_build_rom_help "$PYTHON" -m autoloader build-rom --help

if ! bash "$SCRIPT_DIR/snapshot_inputs.sh" source > "$source_tmp" || \
    ! cmp -s "$source_tmp" "$SOURCE_MANIFEST" || \
    ! bash "$SCRIPT_DIR/snapshot_inputs.sh" environment "$PYTHON" > "$environment_tmp" || \
    ! cmp -s "$environment_tmp" "$ENV_MANIFEST"; then
    printf 'Source or test environment changed during supporting checks\n' >&2
    status=1
fi

exit "$status"
