#!/usr/bin/env bash
set -euo pipefail

umask 0022

repo_root=/mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader
evidence_dir="$repo_root/docs/v1/evidence/linux-offline-worker-file-regression-20261026"
source_snapshot=/run/user/1000/librecore-autoloader-v1-exact-source-20261026-r16
run_scratch=/tmp/librecore-autoloader-v1-exact-tests-20261026-r16
python_bin=/home/alex/.venvs/autoloader-v1/bin/python

test -d "$repo_root"
test -d "$evidence_dir"
test ! -L "$evidence_dir"
test -x "$python_bin"
for scratch_path in "$source_snapshot" "$run_scratch"; do
    if [ -e "$scratch_path" ] || [ -L "$scratch_path" ]; then
        printf 'SCRATCH_PATH_ALREADY_EXISTS=%s\n' "$scratch_path" >&2
        exit 3
    fi
done

tmpfs_size_kib=$(df -Pk /tmp | awk 'END {print $2}')
tmpfs_available_kib=$(df -Pk /tmp | awk 'END {print $4}')
snapshot_available_kib=$(df -Pk /run/user/1000 | awk 'END {print $4}')
evidence_available_kib=$(df -Pk "$evidence_dir" | awk 'END {print $4}')
if [ "$tmpfs_size_kib" -lt 3500000 ] || [ "$tmpfs_available_kib" -lt 3000000 ]; then
    printf 'TMPFS_CAPACITY_OR_AVAILABLE_SPACE_TOO_LOW size_kib=%s available_kib=%s\n' \
        "$tmpfs_size_kib" "$tmpfs_available_kib" >&2
    exit 88
fi
if [ "$snapshot_available_kib" -lt 500000 ]; then
    printf 'SNAPSHOT_FILESYSTEM_SPACE_TOO_LOW available_kib=%s\n' "$snapshot_available_kib" >&2
    exit 89
fi
if [ "$evidence_available_kib" -lt 1048576 ]; then
    printf 'EVIDENCE_FILESYSTEM_SPACE_TOO_LOW available_kib=%s\n' "$evidence_available_kib" >&2
    exit 88
fi

mkdir -p "$evidence_dir"
{
    printf 'tmpfs_size_kib=%s\n' "$tmpfs_size_kib"
    printf 'tmpfs_available_kib=%s\n' "$tmpfs_available_kib"
    printf 'snapshot_filesystem_available_kib=%s\n' "$snapshot_available_kib"
    printf 'evidence_filesystem_available_kib=%s\n' "$evidence_available_kib"
} > "$evidence_dir/preflight-space.txt"

cleanup_owned_tmpfs_paths() {
    local source_resolved run_resolved
    if [ -L "$source_snapshot" ] || [ -L "$run_scratch" ]; then
        printf '%s\n' 'UNPRIVILEGED_CLEANUP_SKIPPED_SYMLINK; wrapper review required' > "$evidence_dir/unprivileged-cleanup.status"
        return 0
    fi
    source_resolved=$(realpath -m -- "$source_snapshot")
    run_resolved=$(realpath -m -- "$run_scratch")
    if [ "$source_resolved" != "$source_snapshot" ] || [ "$run_resolved" != "$run_scratch" ]; then
        printf '%s\n' 'UNPRIVILEGED_CLEANUP_SKIPPED_PATH_MISMATCH; wrapper review required' > "$evidence_dir/unprivileged-cleanup.status"
        return 0
    fi
    if rm -rf -- "$source_snapshot" "$run_scratch"; then
        printf '%s\n' 'UNPRIVILEGED_CLEANUP_REMOVED_EXACT_RUN_SCOPED_TMPFS_PATHS' > "$evidence_dir/unprivileged-cleanup.status"
    else
        printf '%s\n' 'PRIVILEGED_WRAPPER_CLEANUP_PENDING' > "$evidence_dir/unprivileged-cleanup.status"
    fi
}
trap cleanup_owned_tmpfs_paths EXIT

git -C "$repo_root" rev-parse HEAD > "$evidence_dir/commit.txt"
git -C "$repo_root" status --porcelain=v2 --untracked-files=all > "$evidence_dir/git-status.txt"
git -C "$repo_root" diff --binary HEAD > "$evidence_dir/worktree.patch"
git -C "$repo_root" ls-files --others --exclude-standard > "$evidence_dir/untracked-files.txt"

hash_tree() {
    local tree_root=$1
    (
        cd "$tree_root"
        {
            for source_dir in autoloader backups test_vm tests usb_builder; do
                if [ -d "$source_dir" ]; then
                    find "$source_dir" \
                        \( -path '*/__pycache__' \
                        -o -path '*/.pytest_cache' -o -path '*/.mypy_cache' \) -prune \
                        -o -type f ! -name '*.pyc' -print0
                fi
            done
            for fixture_file in \
                docs/v1/RECOVERY_RUNBOOK.md \
                docs/v1/evidence/x200-const-compat-overlay-20261021/0002-vboot-cbfstool-keep-strchr-result-const.patch \
                docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry8/run-result.json \
                docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry8/SHA256SUMS \
                docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/retry8-candidate/guest_probe.py \
                docs/v1/evidence/worker-package-seed-policy-20261025/seed-policy.json \
                docs/v1/evidence/worker-package-seed-policy-20261025/trust-manifest-proposal-20261025.json; do
                if [ -f "$fixture_file" ]; then
                    printf '%s\0' "$fixture_file"
                fi
            done
            find . -maxdepth 1 -type f \
                \( -name 'README.md' -o -name 'LICENSE' -o -name '.gitignore' \
                -o -name '.gitattributes' -o -name 'pyproject.toml' \
                -o -name 'setup.py' -o -name 'setup.cfg' -o -name 'pytest.ini' \
                -o -name 'tox.ini' -o -name 'mypy.ini' -o -name 'MANIFEST.in' \
                -o -name 'requirements*.txt' \) -print0
        } |
            sort -z |
            xargs -0 -r sha256sum
    )
}

hash_tree "$repo_root" > "$evidence_dir/source-manifest.sha256"
mkdir -p "$source_snapshot"
snapshot_paths=(
    autoloader backups test_vm tests usb_builder README.md LICENSE
    docs/v1/RECOVERY_RUNBOOK.md
    docs/v1/evidence/x200-const-compat-overlay-20261021/0002-vboot-cbfstool-keep-strchr-result-const.patch
    docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry8/run-result.json
    docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry8/SHA256SUMS
    docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/retry8-candidate/guest_probe.py
    docs/v1/evidence/worker-package-seed-policy-20261025/seed-policy.json
    docs/v1/evidence/worker-package-seed-policy-20261025/trust-manifest-proposal-20261025.json
)
for root_file in .gitignore .gitattributes pyproject.toml setup.py setup.cfg pytest.ini tox.ini mypy.ini MANIFEST.in; do
    if [ -f "$repo_root/$root_file" ]; then
        snapshot_paths+=("$root_file")
    fi
done
for root_file in "$repo_root"/requirements*.txt; do
    if [ -f "$root_file" ]; then
        snapshot_paths+=("${root_file#"$repo_root"/}")
    fi
done
git -C "$repo_root" ls-files --stage -z -- "${snapshot_paths[@]}" > "$evidence_dir/git-index-modes.z"
snapshot_archive_bytes=$(tar -C "$repo_root" -cf - "${snapshot_paths[@]}" | wc -c)
snapshot_required_kib=$(( (snapshot_archive_bytes + 1023) / 1024 + 131072 ))
snapshot_available_kib=$(df -Pk /run/user/1000 | awk 'END {print $4}')
printf 'snapshot_archive_bytes=%s\nsnapshot_required_with_reserve_kib=%s\nsnapshot_filesystem_available_before_copy_kib=%s\n' \
    "$snapshot_archive_bytes" "$snapshot_required_kib" "$snapshot_available_kib" \
    > "$evidence_dir/preflight-snapshot.txt"
if [ "$snapshot_available_kib" -lt "$snapshot_required_kib" ]; then
    printf 'SNAPSHOT_ARCHIVE_WITH_RESERVE_EXCEEDS_AVAILABLE_SPACE required_kib=%s available_kib=%s\n' \
        "$snapshot_required_kib" "$snapshot_available_kib" >&2
    exit 89
fi
tar \
    --exclude='*/__pycache__' \
    --exclude='*/.pytest_cache' \
    --exclude='*/.mypy_cache' \
    -C "$repo_root" -cf - "${snapshot_paths[@]}" | tar -C "$source_snapshot" -xf -
find "$source_snapshot" -type d -exec chmod 755 {} +
while IFS= read -r -d '' index_entry; do
    mode=${index_entry%% *}
    relative_path=${index_entry#*$'\t'}
    case "$mode" in
        100644) chmod 644 "$source_snapshot/$relative_path" ;;
        100755) chmod 755 "$source_snapshot/$relative_path" ;;
        120000) : ;;
    esac
done < "$evidence_dir/git-index-modes.z"
git -C "$repo_root" ls-files --others --exclude-standard -z -- autoloader backups test_vm tests usb_builder > "$evidence_dir/untracked-source-files.z"
while IFS= read -r -d '' relative_path; do
    case "$relative_path" in
        *.sh) chmod 755 "$source_snapshot/$relative_path" ;;
        *) chmod 644 "$source_snapshot/$relative_path" ;;
    esac
done < "$evidence_dir/untracked-source-files.z"
for fixture_file in \
    docs/v1/evidence/x200-const-compat-overlay-20261021/0002-vboot-cbfstool-keep-strchr-result-const.patch \
    docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry8/run-result.json \
    docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/results/retry8/SHA256SUMS \
    docs/v1/evidence/live-linux-vm-20261016/attempt4-disposable-arch-guest/retry8-candidate/guest_probe.py \
    docs/v1/evidence/worker-package-seed-policy-20261025/seed-policy.json \
    docs/v1/evidence/worker-package-seed-policy-20261025/trust-manifest-proposal-20261025.json; do
    chmod 644 "$source_snapshot/$fixture_file"
done
(
    cd "$source_snapshot"
    find . \( -path './.pytest_cache' -o -path '*/__pycache__' \) -prune -o \
        \( -type f -o -type d \) -printf '%m %p\n' | sort
) > "$evidence_dir/snapshot-modes.txt"
hash_tree "$source_snapshot" > "$evidence_dir/snapshot-manifest.sha256"
if ! cmp -s "$evidence_dir/source-manifest.sha256" "$evidence_dir/snapshot-manifest.sha256"; then
    printf '%s\n' 'SOURCE_SNAPSHOT_MANIFEST_MISMATCH' > "$evidence_dir/snapshot-manifest-comparison.txt"
    exit 90
fi
{
    printf '%s\n' 'result=SOURCE_MANIFEST_MATCHES_POSIX_SNAPSHOT'
    printf 'source_manifest_file_sha256=%s\n' "$(sha256sum "$evidence_dir/source-manifest.sha256" | awk '{print $1}')"
    printf 'snapshot_manifest_file_sha256=%s\n' "$(sha256sum "$evidence_dir/snapshot-manifest.sha256" | awk '{print $1}')"
} > "$evidence_dir/snapshot-manifest-comparison.txt"
rm "$evidence_dir/snapshot-manifest.sha256"

{
    date -u '+utc=%Y-%m-%dT%H:%M:%SZ'
    uname -a
    cat /etc/os-release
    df -h /tmp
    "$python_bin" --version
    "$python_bin" -m pytest --version
    "$python_bin" -m mypy --version
    "$python_bin" -m pip freeze
    printf 'umask=%s\n' "$(umask)"
    printf 'pytest_tmp=%s\n' "$run_scratch/pytest"
    printf 'system_tmp=%s\n' "$run_scratch/system-tmp"
} > "$evidence_dir/environment.txt" 2>&1

printf '%s\n' \
    'cd /run/user/1000/librecore-autoloader-v1-exact-source-20261026-r16 && TMPDIR=/tmp/librecore-autoloader-v1-exact-tests-20261026-r16/system-tmp /home/alex/.venvs/autoloader-v1/bin/python -m pytest -vv -ra --durations=25 -o tmp_path_retention_policy=none -o tmp_path_retention_count=1 --junitxml=/mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader/docs/v1/evidence/linux-offline-worker-file-regression-20261026/junit.xml --basetemp=/tmp/librecore-autoloader-v1-exact-tests-20261026-r16/pytest tests/test_offline_vm_worker.py' \
    > "$evidence_dir/command.txt"

mkdir -p "$run_scratch/system-tmp" "$run_scratch/pytest"
cd "$source_snapshot"
set +e
TMPDIR="$run_scratch/system-tmp" "$python_bin" -m pytest -vv -ra --durations=25 \
    -o tmp_path_retention_policy=none -o tmp_path_retention_count=1 \
    --junitxml="$evidence_dir/junit.xml" \
    --basetemp="$run_scratch/pytest" tests/test_offline_vm_worker.py 2>&1 | tee "$evidence_dir/pytest.log"
test_status=${PIPESTATUS[0]}
set -e
printf '%s\n' "$test_status" > "$evidence_dir/pytest.exit"

hash_tree "$source_snapshot" > "$evidence_dir/source-manifest-after.sha256"
if ! cmp -s "$evidence_dir/source-manifest.sha256" "$evidence_dir/source-manifest-after.sha256"; then
    diff -u "$evidence_dir/source-manifest.sha256" "$evidence_dir/source-manifest-after.sha256" \
        > "$evidence_dir/source-mutation.diff" || true
    printf '%s\n' 'SOURCE_TREE_CHANGED_DURING_TEST'
    exit 90
fi
{
    printf '%s\n' 'result=SOURCE_MANIFEST_UNCHANGED_AFTER_PYTEST'
    printf 'source_manifest_file_sha256=%s\n' "$(sha256sum "$evidence_dir/source-manifest.sha256" | awk '{print $1}')"
    printf 'postrun_manifest_file_sha256=%s\n' "$(sha256sum "$evidence_dir/source-manifest-after.sha256" | awk '{print $1}')"
} > "$evidence_dir/postrun-source-manifest-comparison.txt"

exit "$test_status"
