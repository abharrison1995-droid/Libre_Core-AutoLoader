#!/usr/bin/env bash
# Thin entry point; validation and build orchestration live in build_usb.py.
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
source_root="$(cd -- "$script_dir/.." && pwd -P)"
exec python3 "$script_dir/build_usb.py" \
    --source-root "$source_root" \
    --profile-dir "$script_dir/archiso_profile" \
    --snapshot-lock "$script_dir/snapshot.lock.json" \
    "$@"
