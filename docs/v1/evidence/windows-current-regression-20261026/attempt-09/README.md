# Native Windows focused check — attempt 09 — Git template path failure

Pytest collected the two Windows-only tests. The USB CLI safety test passed;
the durability-policy test failed during synthetic fixture setup when Git
copied its default `applypatch-msg.sample` hook into an overlong nested path.
The safety assertion was not reached. Full output, JUnit, command, environment,
and source manifest are retained.

Attempt 12 below reruns both test nodes with an empty Git template and a
process-scoped `core.longpaths=true` setting.
