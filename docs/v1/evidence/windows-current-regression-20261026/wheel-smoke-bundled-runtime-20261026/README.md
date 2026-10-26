# Current-source Windows wheel readiness smoke — 2026-10-26

The full attempt-15 Windows selection recorded this test as skipped because
the project virtual environment did not have `setuptools` and `wheel` installed.
The same existing test was then run on native Windows with the already-present
Codex workspace Python packages exposed through `PYTHONPATH`; no packages were
installed to prepare the environment. The test invocation used local source
and wheel inputs with `--no-deps --no-build-isolation`. Network access was not
enforced or measured during the run.

The targeted test passed **1 test, 0 skipped, 0 failures** in 17.31 seconds
(pytest exit `0`). It built the project wheel without build isolation or
dependency resolution, installed it into a temporary target, checked that the
recovery runbook was packaged byte-for-byte, and exercised the readiness gate
from outside the source tree. This focused pass does not change the recorded
attempt-15 aggregate of **1,118 passed, 274 skipped**.

`environment.txt`, `command.txt`, `pytest.log`, `junit.xml`, and `exit-code.txt`
record the interpreter, tool versions, invocation, test output and status.
The 149-path source/test manifest is unchanged before and after. `SHA256SUMS`
covers every evidence file.
