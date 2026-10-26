#!/usr/bin/env bash
set -euo pipefail

umask 0022
repo_root=/mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader
evidence_dir="$repo_root/docs/v1/evidence/software-regression-20261025/exact-tree-recapture-20261025/attempt-13"
python_bin=/home/alex/.venvs/autoloader-v1/bin/python
basetemp=/tmp/librecore-autoloader-v1-focused-20261026-r13
selection='fixed_service_evidence_volume_round_trips_to_stopped_host_pending_review or fixed_service_host_rejects_corrupted_bound_evidence_records or fixed_service_host_rejects_command_status_hash_mismatch or fixed_service_rejects_swapped_evidence_volume_before_result_markers or fixed_service_interruption_before_terminal_is_rejected_by_stopped_host or fixed_service_synthetic_evidence_modes_are_umask_independent'

test -d "$repo_root"
test -d "$evidence_dir"
test ! -L "$evidence_dir"
test -x "$python_bin"
if [ -e "$basetemp" ] || [ -L "$basetemp" ]; then
    printf 'FOCUSED_BASETEMP_ALREADY_EXISTS=%s\n' "$basetemp" >&2
    exit 3
fi
tmpfs_available_kib=$(df -Pk /tmp | awk 'END {print $4}')
evidence_available_kib=$(df -Pk "$evidence_dir" | awk 'END {print $4}')
if [ "$tmpfs_available_kib" -lt 262144 ] || [ "$evidence_available_kib" -lt 1048576 ]; then
    printf 'FOCUSED_SPACE_PREFLIGHT_FAILED tmpfs_available_kib=%s evidence_available_kib=%s\n' \
        "$tmpfs_available_kib" "$evidence_available_kib" >&2
    exit 88
fi
{
    printf 'tmpfs_available_kib=%s\n' "$tmpfs_available_kib"
    printf 'evidence_available_kib=%s\n' "$evidence_available_kib"
    printf '%s\n' 'focused_tmpfs_minimum_kib=262144' 'evidence_filesystem_minimum_kib=1048576'
} > "$evidence_dir/focused-preflight-space.txt"
mkdir -p "$basetemp/system-tmp"

{
    printf 'umask=%s\n' "$(umask)"
    id
    "$python_bin" --version
    "$python_bin" -m pytest --version
    printf 'basetemp=%s\n' "$basetemp"
    printf 'selection=%s\n' "$selection"
} > "$evidence_dir/focused-environment.txt" 2>&1
printf '%s\n' \
    "python -m pytest -q -ra -o tmp_path_retention_policy=none -o tmp_path_retention_count=1 --basetemp=$basetemp/pytest tests/test_offline_vm_worker.py -k '$selection'" \
    > "$evidence_dir/focused-command.txt"

set +e
cd "$repo_root"
TMPDIR="$basetemp/system-tmp" "$python_bin" -m pytest -q -ra \
    -o tmp_path_retention_policy=none -o tmp_path_retention_count=1 \
    --basetemp="$basetemp/pytest" \
    tests/test_offline_vm_worker.py -k "$selection" \
    2>&1 | tee "$evidence_dir/focused.log"
test_status=${PIPESTATUS[0]}
set -e
printf '%s\n' "$test_status" > "$evidence_dir/focused.exit"
exit "$test_status"
