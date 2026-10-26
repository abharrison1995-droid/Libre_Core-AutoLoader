#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
PYTHON="${PYTHON:-/home/alex/librecore-v1-test-venv/bin/python}"
TMP_ROOT="${TMPDIR:-/tmp/lc-per-file}"
LOG_DIR="$SCRIPT_DIR/module-logs"
JUNIT_DIR="$SCRIPT_DIR/junit"
RESULTS="$SCRIPT_DIR/module-results.tsv"
SOURCE_MANIFEST="$SCRIPT_DIR/source-manifest.sha256"
ENV_MANIFEST="$SCRIPT_DIR/environment.txt"
EXIT_STATUS="$SCRIPT_DIR/module-run.exit"
RESUME="${RESUME:-0}"

printf 'Runner command: bash'
printf ' %q' "$SCRIPT_DIR/run_modules.sh" "$@"
printf '\nWorking directory: %q\n' "$PWD"
printf 'Python: %q\nTemporary root: %q\nResume: %q\n' "$PYTHON" "$TMP_ROOT" "$RESUME"

if [[ "$RESUME" == 1 ]]; then
    printf 'RESUME=1 is disabled; start a fresh run\n' >&2
    exit 2
fi
if [[ "$RESUME" != 0 ]]; then
    printf 'RESUME must be 0; resume mode is disabled\n' >&2
    exit 2
fi
if [[ ! -x "$PYTHON" ]]; then
    printf 'Configured Python is not executable: %s\n' "$PYTHON" >&2
    exit 2
fi

mkdir -p "$LOG_DIR" "$JUNIT_DIR"

source_manifest() (
    bash "$SCRIPT_DIR/snapshot_inputs.sh" source
)

environment_manifest() {
    bash "$SCRIPT_DIR/snapshot_inputs.sh" environment "$PYTHON"
}

source_tmp="$(mktemp)"
environment_tmp="$(mktemp)"
cleanup_temps() {
    rm -f -- "$source_tmp" "$environment_tmp"
}
trap cleanup_temps EXIT

if ! source_manifest > "$source_tmp" || ! environment_manifest > "$environment_tmp"; then
    printf 'Could not capture source or environment identity\n' >&2
    exit 2
fi

mv -- "$source_tmp" "$SOURCE_MANIFEST"
mv -- "$environment_tmp" "$ENV_MANIFEST"
: > "$RESULTS"
rm -f -- "$SCRIPT_DIR/module-results.json"

rm -f -- "$EXIT_STATUS"
write_exit_status() {
    local code=$?
    printf '%s\n' "$code" > "$EXIT_STATUS"
    cleanup_temps
}
trap write_exit_status EXIT

replace_module_row() {
    local module="$1"
    local tmp_file
    tmp_file="$(mktemp "$SCRIPT_DIR/module-results.XXXXXX")"
    if [[ -f "$RESULTS" ]]; then
        awk -F '\t' -v module="$module" '$1 != module' "$RESULTS" > "$tmp_file"
    else
        : > "$tmp_file"
    fi
    mv -- "$tmp_file" "$RESULTS"
}

status=0
mapfile -d '' test_files < <(find "$ROOT/tests" -maxdepth 1 -type f -name 'test_*.py' -print0 | sort -z)
if [[ "${#test_files[@]}" -eq 0 ]]; then
    printf 'No top-level pytest modules found\n' >&2
    exit 2
fi
printf 'Expected top-level test modules: %s\n' "${#test_files[@]}"

for test_file in "${test_files[@]}"; do
    name="$(basename "$test_file" .py)"
    log_file="$LOG_DIR/$name.log"
    junit_file="$JUNIT_DIR/$name.xml"

    rm -f -- "$log_file" "$junit_file"
    pytest_command=(env PYTHONDONTWRITEBYTECODE=1 "TMPDIR=$TMP_ROOT" "$PYTHON" -m pytest \
        -q -p no:cacheprovider "--basetemp=$TMP_ROOT" \
        "--junitxml=$junit_file" "$test_file")
    printf 'RUN\t%s\t' "$name"
    printf '%q ' "${pytest_command[@]}"
    printf '\n'
    printf 'COMMAND: ' > "$log_file"
    printf '%q ' "${pytest_command[@]}" >> "$log_file"
    printf '\n' >> "$log_file"
    if "${pytest_command[@]}" >> "$log_file" 2>&1; then
        result=0
    else
        result=$?
        status=1
    fi

    summary="$(awk 'NF { line=$0 } END { gsub(/[\t\r]/, " ", line); print line }' "$log_file")"
    replace_module_row "$name"
    printf '%s\t%s\t%s\t%s\n' "$name" "$result" "$summary" "junit/$name.xml" >> "$RESULTS"
    printf '%s\t%s\t%s\n' "$name" "$result" "$summary"
done

if ! source_manifest > "$source_tmp" || ! environment_manifest > "$environment_tmp" || \
    ! cmp -s "$source_tmp" "$SOURCE_MANIFEST" || ! cmp -s "$environment_tmp" "$ENV_MANIFEST"; then
    printf 'Source or test environment changed during the run\n' >&2
    status=1
fi

exit "$status"
