#!/usr/bin/env bash
set -euo pipefail
repo=/mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader
ev="$repo/docs/v1/evidence/software-regression-20261025/exact-tree-recapture-20261025/attempt-11"
tmp=/tmp/librecore-autoloader-v1-current-20261026-linux-attempt-11
test ! -e "$tmp"
mkdir -m 700 "$tmp" "$tmp/pytest" "$tmp/system-tmp"
cd "$repo"
set +e
env TMPDIR="$tmp/system-tmp" TMP="$tmp/system-tmp" TEMP="$tmp/system-tmp" \
  /home/alex/.venvs/autoloader-v1/bin/python -m pytest -vv -ra --durations=25 \
  -o tmp_path_retention_policy=none -o tmp_path_retention_count=1 \
  --junitxml="$ev/junit.xml" --basetemp="$tmp/pytest" 2>&1 | tee "$ev/pytest.log"
status=${PIPESTATUS[0]}
printf '%s\n' "$status" > "$ev/pytest.exit"
exit "$status"
