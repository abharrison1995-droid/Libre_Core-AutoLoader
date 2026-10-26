#!/bin/bash
set -u
if [[ $# -ne 1 || ! "$1" =~ ^attempt-[0-9]{2}$ ]]; then
  printf '%s\n' 'usage: run_verification.sh attempt-NN' >&2
  exit 64
fi
bundle_dir=$(cd "$(dirname "$0")" && pwd -P) || exit 125
run_id=$1
run_root="$bundle_dir/runs/$run_id"
if [[ -e "$run_root" || -L "$run_root" ]]; then
  printf 'refusing pre-existing unique run directory: %s\n' "$run_root" >&2
  exit 73
fi
umask 077
mkdir -p "$bundle_dir/runs" || exit 125
mkdir "$run_root" || exit 73
mkdir "$run_root/logs" "$run_root/extracted" "$run_root/runtime" || exit 125
printf '%s\n' "bundle=$bundle_dir" "run_id=$run_id" "argv=/usr/bin/env -i LANG=C LC_ALL=C PATH=/usr/bin:/bin TZ=UTC PYTHONDONTWRITEBYTECODE=1 PYTHONOPTIMIZE=0 /usr/bin/python3 -B verify_capture.py $run_id" > "$run_root/run-invocation.txt"
cd "$bundle_dir" || exit 125
/usr/bin/env -i \
  LANG=C \
  LC_ALL=C \
  PATH=/usr/bin:/bin \
  TZ=UTC \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONOPTIMIZE=0 \
  /usr/bin/python3 -B verify_capture.py "$run_id" \
  > "$run_root/logs/runner.stdout.bin" \
  2> "$run_root/logs/runner.stderr.bin"
result=$?
printf '%s\n' "$result" > "$run_root/logs/runner.exit"
printf '%s\n' "$result" > "$run_root/verification.exit"
exit "$result"
