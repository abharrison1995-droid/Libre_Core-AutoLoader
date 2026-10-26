# Focused fixed-service mount regression

This is a synthetic software test of the fixed service and stopped-host evidence contract after centralizing source/package mount paths and binding a private synthetic snapshot lock plus core/extra database hashes. It does not validate Arch package signatures, a worker image, VM boot, production deployment, or hardware.

The Ubuntu WSL run used the repository checkout at `/mnt/c/Users/Alex/Desktop/Libre_Core-AutoLoader`, Python 3.14.4, pytest from the existing extracted bootstrap, cache and bytecode disabled, and a dedicated `/tmp` basetemp.

Command: `python3 -B -m pytest -p no:cacheprovider --basetemp=/tmp/librecore-phase1-final-20261025 tests/test_offline_vm_worker.py -k "fixed_service" -q --color=no`

Result: 34 passed, 91 deselected in 5.15s; pytest exit code 0. The captured output and exit code are in [pytest.log](pytest.log) and [pytest.exit](pytest.exit).
