# Software regression evidence — 2026-10-21

This record preserves current results outside the full C: volume. It is temporary evidence for later incorporation into the repository; it is not a release claim.

## Ubuntu WSL

- Full suite: **985 passed, 4 skipped, 0 failures**; 989 collected; 687.55 seconds; exit code 0.
- Command: `pytest -q -p no:cacheprovider --basetemp=/mnt/wsl/PHYSICALDRIVE1p2/opt/librecore-autoloader-v1-vm/pytest-linux-full-20261021`
- Focused device-readiness tests: **9 passed** in 128.49 seconds.
- Full-project mypy: **105 source files**, no issues, using a fresh cache directory.

## Windows

- Focused readiness/UI/CLI tests after the screen-registry fix: **11 passed, 1 skipped** in 160.41 seconds.
- Full-project mypy: **105 source files**, no issues.
- A full suite rerun was started after a prior 920 passed / 68 skipped / 1 failed run whose sole reported failure was the stale screen registry expectation. The expectation was updated. The retry could not complete because C: reached **0 bytes free**. An isolated Windows test failed with `OSError: [Errno 28] No space left on device` while creating its 4 MiB fixture ROM. This is an incomplete run, not a pass or a code-level failure result.
- Windows tests against the external WSL UNC path are not a valid substitute: the X200 fixture failed to create its nested MAC-preservation sidecar over that path.

## Product and hardware status

- CLI `python -B -m autoloader --help`: exit 0; shared readiness command is registered.
- Native Archiso build, current-source Live USB boot, and all three real-device workflows remain pending.
- No physical SPI probe, firmware write, recovery, read-back, or hardware boot was performed.
- Hardware testing remains deferred; no simulated operation is presented as hardware validation.

## Capture limits

Pytest summary output was recorded from process results in this task. JUnit XML was not emitted for these 2026-10-21 runs. The full Windows run is explicitly incomplete.