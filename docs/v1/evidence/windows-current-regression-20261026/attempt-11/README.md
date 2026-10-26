# Native Windows focused check — attempt 11 — Git long-path failure

Pytest collected both Windows-only tests. The USB CLI safety test passed; the
durability-policy test failed during synthetic fixture setup when Git could
not create `.git/refs/heads` under the nested path. No production safety
assertion was reached. Full output, JUnit, command, environment, and source
manifest are retained.

Attempt 12 below reruns both test nodes with Git long-path support enabled for
the test subprocesses.
