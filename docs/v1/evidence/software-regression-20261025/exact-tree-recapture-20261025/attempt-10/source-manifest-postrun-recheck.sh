#!/usr/bin/env bash
set -euo pipefail
repo_root=/mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader
evidence_dir="$repo_root/docs/v1/evidence/software-regression-20261025/exact-tree-recapture-20261025/attempt-10"

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
        } | sort -z | xargs -0 -r sha256sum
    )
}

hash_tree "$repo_root" > "$evidence_dir/source-manifest-postrun.sha256"
if ! cmp -s "$evidence_dir/source-manifest.sha256" "$evidence_dir/source-manifest-postrun.sha256"; then
    printf 'comparison=FAIL\n' > "$evidence_dir/source-manifest-recheck.txt"
    diff -u "$evidence_dir/source-manifest.sha256" "$evidence_dir/source-manifest-postrun.sha256" >> "$evidence_dir/source-manifest-recheck.txt" || true
    exit 90
fi
printf 'comparison=PASS\nentries=%s\n' "$(wc -l < "$evidence_dir/source-manifest-postrun.sha256")" > "$evidence_dir/source-manifest-recheck.txt"
printf 'pre_run_manifest_sha256=%s\n' "$(sha256sum "$evidence_dir/source-manifest.sha256" | awk '{print $1}')" >> "$evidence_dir/source-manifest-recheck.txt"
printf 'post_run_manifest_sha256=%s\n' "$(sha256sum "$evidence_dir/source-manifest-postrun.sha256" | awk '{print $1}')" >> "$evidence_dir/source-manifest-recheck.txt"