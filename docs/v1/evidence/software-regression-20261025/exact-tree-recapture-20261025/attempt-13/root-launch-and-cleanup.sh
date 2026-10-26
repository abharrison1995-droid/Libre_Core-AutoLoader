#!/usr/bin/env bash
set -uo pipefail

repo_root=/mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader
evidence_dir="$repo_root/docs/v1/evidence/software-regression-20261025/exact-tree-recapture-20261025/attempt-13"
inner_runner="$evidence_dir/run.sh"
focused_runner="$evidence_dir/run-focused.sh"
source_snapshot=/run/user/1000/librecore-autoloader-v1-exact-source-20261026-r13
run_scratch=/tmp/librecore-autoloader-v1-exact-tests-20261026-r13
focused_scratch=/tmp/librecore-autoloader-v1-focused-20261026-r13

if [ "$(id -u)" -ne 0 ]; then
    printf '%s\n' 'ROOT_WRAPPER_REQUIRES_ROOT' >&2
    exit 2
fi
test -d "$repo_root" || exit 2
test -d "$evidence_dir" || exit 2
test ! -L "$evidence_dir" || exit 2
test -f "$inner_runner" || exit 2
test ! -L "$inner_runner" || exit 2
test -f "$focused_runner" || exit 2
test ! -L "$focused_runner" || exit 2

if su - alex -c "bash '$focused_runner'" > "$evidence_dir/focused-launch-console.log" 2>&1; then
    focused_status=0
else
    focused_status=$?
fi
printf '%s\n' "$focused_status" > "$evidence_dir/focused-outer.exit"

full_suite_status_text=NOT_RUN_FOCUSED_FAILURE
if [ "$focused_status" -eq 0 ]; then
    if [ -L "$focused_scratch" ]; then
        printf '%s\n' 'REFUSED_FOCUSED_SCRATCH_SYMLINK' > "$evidence_dir/focused-cleanup.status"
        exit 92
    fi
    focused_resolved=$(realpath -m -- "$focused_scratch") || exit 92
    if [ "$focused_resolved" != "$focused_scratch" ] || mountpoint -q -- "$focused_scratch"; then
        printf 'REFUSED_FOCUSED_SCRATCH_PATH_OR_MOUNT=%s\n' "$focused_scratch" > "$evidence_dir/focused-cleanup.status"
        exit 92
    fi
    if [ -e "$focused_scratch" ]; then
        if [ ! -d "$focused_scratch" ]; then
            printf 'REFUSED_FOCUSED_SCRATCH_NON_DIRECTORY=%s\n' "$focused_scratch" > "$evidence_dir/focused-cleanup.status"
            exit 92
        fi
        if ! rm -rf --one-file-system -- "$focused_scratch"; then
            printf 'FOCUSED_SCRATCH_CLEANUP_FAILED=%s\n' "$focused_scratch" > "$evidence_dir/focused-cleanup.status"
            exit 92
        fi
    fi
    printf '%s\n' 'root wrapper removed exact focused scratch before full-suite preflight' > "$evidence_dir/focused-cleanup.status"
    if su - alex -c "bash '$inner_runner'" > "$evidence_dir/launch-console.log" 2>&1; then
        inner_status=0
    else
        inner_status=$?
    fi
    full_suite_status_text=$inner_status
    printf '%s\n' "$inner_status" > "$evidence_dir/outer-runner.exit"
else
    inner_status=$focused_status
    printf '%s\n' 'NOT_RUN_FOCUSED_RUN_FAILED' > "$evidence_dir/outer-runner.exit"
    printf '%s\n' 'focused selection failed; full suite not started' > "$evidence_dir/launch-console.log"
fi

cleanup_failed=0
cleanup_messages=()
if [ "$focused_status" -ne 0 ]; then
    cleanup_messages+=("full suite not started because focused selection exited $focused_status")
fi

cleanup_exact_path() {
    local scratch_path=$1 resolved
    if [ -L "$scratch_path" ]; then
        cleanup_failed=1
        cleanup_messages+=("refused symlink: $scratch_path")
        return
    fi
    resolved=$(realpath -m -- "$scratch_path") || {
        cleanup_failed=1
        cleanup_messages+=("realpath failed: $scratch_path")
        return
    }
    if [ "$resolved" != "$scratch_path" ]; then
        cleanup_failed=1
        cleanup_messages+=("refused path mismatch: $scratch_path -> $resolved")
        return
    fi
    if mountpoint -q -- "$scratch_path"; then
        cleanup_failed=1
        cleanup_messages+=("refused mountpoint: $scratch_path")
        return
    fi
    if [ -e "$scratch_path" ]; then
        if [ ! -d "$scratch_path" ]; then
            cleanup_failed=1
            cleanup_messages+=("refused non-directory: $scratch_path")
            return
        fi
        if rm -rf --one-file-system -- "$scratch_path"; then
            cleanup_messages+=("removed exact path: $scratch_path")
        else
            cleanup_failed=1
            cleanup_messages+=("removal failed: $scratch_path")
        fi
    else
        cleanup_messages+=("already absent: $scratch_path")
    fi
}

cleanup_exact_path "$source_snapshot"
cleanup_exact_path "$run_scratch"
cleanup_exact_path "$focused_scratch"

{
    printf 'focused_outer_exit=%s\n' "$focused_status"
    printf 'full_suite_outer_exit=%s\n' "$full_suite_status_text"
    if [ "$cleanup_failed" -eq 0 ]; then
        printf '%s\n' 'privileged_cleanup=verified exact paths absent or removed'
    else
        printf '%s\n' 'privileged_cleanup=INCOMPLETE'
    fi
    printf 'detail=%s\n' "${cleanup_messages[*]}"
} > "$evidence_dir/cleanup.status"

if [ "$cleanup_failed" -ne 0 ]; then
    exit 91
fi
if [ "$focused_status" -ne 0 ]; then
    exit "$focused_status"
fi
exit "$inner_status"
