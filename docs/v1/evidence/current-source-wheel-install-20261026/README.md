# Current-source wheel build/install smoke — 2026-10-26

## Recorded result

The strengthened no-index packaging smoke recorded `PASS` in `run.json` for source snapshot SHA-256 `e071f9dbd6d8510eefa6c865b36475c08142b3c01f9efc8ca1ec8508a6b727b4` (149 source files). The updated assertions were rerun on 2026-10-26 and passed again, with matching pre-run and post-run source manifests. The latest run built `libre_core_autoloader-0.1.0-py3-none-any.whl` (551,061 bytes; SHA-256 `8dd04f50015e45b4bb58e8d38765d4a339d91f4af2002677e7d31dee726fcb86`) and installed it with `pip --no-index --no-deps --no-cache-dir --target`. All seven offline worker modules imported from the installed target. The required snapshot lock and four systemd service/path resources were present and byte-matched the captured source. Every wheel `Requires-Dist` entry, the `Provides-Extra` set, and `Requires-Python` match `pyproject.toml`. `python -m autoloader --help` and `python -m autoloader flash --help` both exited `0`.

The project `.venv` did not contain the packaging backend, so this run exposed the existing bundled workspace Python runtime's site-packages directory through process-local `PYTHONPATH`. The exact `setuptools` and `wheel` versions and import paths are recorded in `run.json`. Build and install pip operations stayed offline, without dependencies, build isolation, or cache.

The exact PowerShell invocation is retained in `command.txt`.

This is limited packaging validation. It does not establish dependency closure or installation in a clean environment: application runtime dependencies were supplied from the repository's `.venv\Lib\site-packages`. It does not validate worker VM/image execution, firmware, USB media, or hardware.

No firmware or physical hardware was accessed. See `SHA256SUMS` for checksums of the files in this evidence directory (excluding the checksum file itself).
