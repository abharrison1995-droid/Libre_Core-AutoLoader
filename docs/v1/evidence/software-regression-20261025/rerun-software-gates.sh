#!/usr/bin/env bash
set -uo pipefail

repo=/mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader
evidence="$repo/docs/v1/evidence/software-regression-20261025"
cd "$repo"

export TMPDIR=/tmp
export PYTHONDONTWRITEBYTECODE=1
export PYTHONOPTIMIZE=0
export PYTHONPATH=/tmp/librecore-autoloader-v1-evidence-20261025-pytest-bootstrap/extracted/usr/lib/python3/dist-packages

scratch=/tmp/librecore-autoloader-v1-focused-final-20261025
if [[ -e "$scratch" ]]; then
    printf 'Refusing to reuse existing pytest scratch: %s\n' "$scratch" >&2
    exit 2
fi

python3 -B -m pytest -p no:cacheprovider \
    --basetemp="$scratch" \
    --junitxml="$evidence/focused-final.junit.xml" \
    -q --color=no \
    tests/test_offline_vm_worker.py \
    tests/test_offline_vm_build_executor.py \
    tests/test_offline_vm_evidence.py \
    tests/test_offline_vm_evidence_adapter.py \
    >"$evidence/focused-final.pytest.log" 2>&1
pytest_status=$?
printf '%s\n' "$pytest_status" >"$evidence/focused-final.pytest.exit"

if [[ ! -x /tmp/librecore-autoloader-v1-mypy-venv/bin/mypy ]]; then
    printf 'Expected temporary mypy environment is unavailable.\n' >&2
    exit 2
fi

/tmp/librecore-autoloader-v1-mypy-venv/bin/mypy \
    --no-incremental autoloader usb_builder \
    >"$evidence/mypy-native.log" 2>&1
mypy_native_status=$?
printf '%s\n' "$mypy_native_status" >"$evidence/mypy-native.exit"

/tmp/librecore-autoloader-v1-mypy-venv/bin/mypy \
    --no-incremental --platform win32 autoloader usb_builder \
    >"$evidence/mypy-win32.log" 2>&1
mypy_win32_status=$?
printf '%s\n' "$mypy_win32_status" >"$evidence/mypy-win32.exit"

cli_status=0
{
    run_cli() {
        printf '$'
        printf ' %q' "$@"
        printf '\n'
        "$@"
        rc=$?
        printf 'exit %s\n\n' "$rc"
        if (( rc != 0 )); then
            cli_status=1
        fi
    }
    run_cli python3 -B -m autoloader --help
    run_cli python3 -B -m autoloader flash --help
    run_cli python3 -B -m autoloader build-usb --help
} >"$evidence/cli-help.log" 2>&1
printf '%s\n' "$cli_status" >"$evidence/cli-help.exit"

if (( pytest_status != 0 || mypy_native_status != 0 || mypy_win32_status != 0 || cli_status != 0 )); then
    exit 1
fi
exit 0
