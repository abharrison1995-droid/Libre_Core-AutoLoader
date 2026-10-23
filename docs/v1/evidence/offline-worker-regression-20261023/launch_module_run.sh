#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER="$SCRIPT_DIR/run_modules.sh"
LOG="$SCRIPT_DIR/module-run.log"
EXIT_LOG="$SCRIPT_DIR/module-run-launch.exit"
RESUME="${RESUME:-0}"

# Refuse resume mode before rotating or otherwise touching any prior evidence.
if [[ "$RESUME" == 1 ]]; then
    printf 'RESUME=1 is disabled; start a fresh run\n' >&2
    exit 2
fi
if [[ "$RESUME" != 0 ]]; then
    printf 'RESUME must be 0; resume mode is disabled\n' >&2
    exit 2
fi

if pgrep -f '[r]un_modules\.sh' >/dev/null; then
    printf 'A module regression runner is already active\n' >&2
    exit 1
fi

if [[ -f "$LOG" ]]; then
    attempt="$SCRIPT_DIR/module-run-interrupted-attempt.log"
    suffix=2
    while [[ -e "$attempt" ]]; do
        attempt="$SCRIPT_DIR/module-run-interrupted-attempt-$suffix.log"
        suffix=$((suffix + 1))
    done
    mv -- "$LOG" "$attempt"
fi
rm -f -- "$SCRIPT_DIR/module-run.pid"

runner_status=not-started
tee_status=not-started
write_launch_status() {
    local wrapper_status=$?
    local temporary
    temporary="$(mktemp "$SCRIPT_DIR/module-run-launch.XXXXXX")" || return
    {
        printf 'runner_exit=%s\n' "$runner_status"
        printf 'tee_exit=%s\n' "$tee_status"
        printf 'wrapper_exit=%s\n' "$wrapper_status"
    } > "$temporary"
    mv -- "$temporary" "$EXIT_LOG"
}
trap write_launch_status EXIT

{
    printf 'Launch time UTC: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'Working directory: %q\n' "$PWD"
    printf 'Wrapper command: bash'
    printf ' %q' "$0" "$@"
    printf '\nRunner command: bash %q\n' "$RUNNER"
    printf 'Python: %q\nTemporary root: %q\nResume: %q\n' \
        "${PYTHON:-/home/alex/librecore-v1-test-venv/bin/python}" \
        "${TMPDIR:-/tmp/lc-per-file}" "${RESUME:-0}"
} > "$LOG"

set +e
bash "$RUNNER" 2>&1 | tee -a "$LOG"
pipeline_status=("${PIPESTATUS[@]}")
runner_status="${pipeline_status[0]}"
tee_status="${pipeline_status[1]}"
printf 'Runner exit: %s\nTee exit: %s\n' "$runner_status" "$tee_status" | tee -a "$LOG"
if [[ "$runner_status" -ne 0 ]]; then
    exit "$runner_status"
fi
exit "$tee_status"
