#!/usr/bin/env bash
set -euo pipefail

repo_root=/mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader
evidence_dir="$repo_root/docs/v1/evidence/software-regression-20261025/exact-tree-recapture-20261025/attempt-07"
source_snapshot=/run/user/1000/librecore-autoloader-v1-exact-source-20261025-r4
run_scratch=/tmp/librecore-autoloader-v1-exact-tests-20261025-r4
python_bin=/home/alex/.venvs/autoloader-v1/bin/python

test -d "$repo_root"
test ! -e "$evidence_dir"
mkdir -p "$evidence_dir"
test -x "$python_bin"
test ! -e "$source_snapshot"
test ! -e "$run_scratch"
tmpfs_size_kib=$(df -Pk /tmp | awk 'END {print $2}')
printf '%s\n' "$tmpfs_size_kib" > "$evidence_dir/tmpfs-size-kib.txt"
if [ "$tmpfs_size_kib" -lt 6000000 ]; then
    printf 'TMPFS_TOO_SMALL_KIB=%s\n' "$tmpfs_size_kib" >&2
    exit 88
fi

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
cmp "$evidence_dir/source-manifest.sha256" "$evidence_dir/snapshot-manifest.sha256"
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
    printf 'pytest_tmp=%s\n' "$run_scratch/pytest"
    printf 'system_tmp=%s\n' "$run_scratch/system-tmp"
} > "$evidence_dir/environment.txt" 2>&1

printf '%s\n' \
    'cd /run/user/1000/librecore-autoloader-v1-exact-source-20261025-r4 && TMPDIR=/tmp/librecore-autoloader-v1-exact-tests-20261025-r4/system-tmp /home/alex/.venvs/autoloader-v1/bin/python -m pytest -vv -ra --durations=25 -o tmp_path_retention_policy=none -o tmp_path_retention_count=1 --junitxml=/mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader/docs/v1/evidence/software-regression-20261025/exact-tree-recapture-20261025/attempt-07/junit.xml --basetemp=/tmp/librecore-autoloader-v1-exact-tests-20261025-r4/pytest' \
    > "$evidence_dir/command.txt"

mkdir -p "$run_scratch/system-tmp" "$run_scratch/pytest"
cd "$source_snapshot"
set +e
TMPDIR="$run_scratch/system-tmp" "$python_bin" -m pytest -vv -ra --durations=25 \
    -o tmp_path_retention_policy=none -o tmp_path_retention_count=1 \
    --junitxml="$evidence_dir/junit.xml" \
    --basetemp="$run_scratch/pytest" 2>&1 | tee "$evidence_dir/pytest.log"
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
rm "$evidence_dir/source-manifest-after.sha256"

cleanup_status=0
if [ "$test_status" -eq 0 ]; then
    for cleanup_path in "$run_scratch" "$source_snapshot"; do
        if [ -e "$cleanup_path" ]; then
            resolved_path=$(realpath "$cleanup_path") || cleanup_status=2
            if [ "$cleanup_status" -eq 0 ] && [ "$resolved_path" = "$cleanup_path" ]; then
                if ! rm -rf -- "$cleanup_path" 2>> "$evidence_dir/cleanup.log"; then
                    cleanup_status=1
                fi
            else
                cleanup_status=2
            fi
        fi
    done
fi
printf '%s\n' "$cleanup_status" > "$evidence_dir/cleanup.exit"
exit "$test_status"
