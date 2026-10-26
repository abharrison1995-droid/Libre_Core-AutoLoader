#!/usr/bin/env bash
set -uo pipefail

repo=/mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader
evidence="$repo/docs/v1/evidence/software-regression-20261025/fwcfg-followup"
scratch_root=/home/alex/librecore-autoloader-v1-vm/fwcfg-followup-full-20261025
scratch="$scratch_root/pytest"
tmpdir="$scratch_root/tmp"

if [[ -e "$scratch_root" ]]; then
    printf 'Refusing to reuse test scratch root: %s\n' "$scratch_root" >&2
    exit 2
fi
if [[ ! -d /tmp/librecore-autoloader-v1-evidence-20261025-pytest-bootstrap/extracted/usr/lib/python3/dist-packages ]]; then
    printf 'Expected extracted pytest dependency directory is unavailable.\n' >&2
    exit 2
fi

cd "$repo"
mkdir -p "$tmpdir"
export TMPDIR="$tmpdir"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONOPTIMIZE=0
export PYTHONPATH=/tmp/librecore-autoloader-v1-evidence-20261025-pytest-bootstrap/extracted/usr/lib/python3/dist-packages

{
    printf 'UTC: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'HEAD: %s\n' "$(git rev-parse HEAD)"
    printf 'User: %s\n' "$(id)"
    printf 'Umask: '
    umask
    python3 --version
    python3 -m pytest --version
    python3 -c 'import textual; print("Textual", textual.__version__)'
    df -h "$scratch_root"
    printf 'Scratch root: %s\n' "$scratch_root"
} >"$evidence/environment.txt" 2>&1

set +e
python3 -B -m pytest -p no:cacheprovider \
    --basetemp="$scratch" \
    --junitxml="$evidence/full.junit.xml" \
    -q --color=no \
    >"$evidence/full.pytest.log" 2>&1
pytest_status=$?
set -e

printf '%s\n' "$pytest_status" >"$evidence/full.pytest.exit"
printf 'UTC end: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$evidence/environment.txt"
df -h "$scratch_root" >>"$evidence/environment.txt"
exit "$pytest_status"
