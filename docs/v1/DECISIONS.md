# V1 decisions

| ID | Decision | Reason | Status |
|---|---|---|---|
| D-001 | Preserve the existing package layout and incrementally stabilize entry points, orchestrator and screens. | Matches the specification's architecture-preservation constraint and keeps changes reviewable. | Active |
| D-002 | Unimplemented build, write or verification workflows must report unavailable and cannot unlock subsequent hazardous actions. | A progress animation or static report is not evidence of an operation. | Active |
| D-003 | The standalone `verify_flash` helper remains the public function API; it currently delegates correctly to `FlashVerifier.verify_flash`. | The reported import mismatch is absent in baseline `d5e6810`; add coverage rather than introduce a needless API rewrite. | Active |
| D-004 | Hardware tests will be recorded as pending until the named physical device passes the workflow and boot/function checks. | Software tests cannot validate hardware compatibility or recovery. | Active |
| D-005 | Physical flashing, IVprep downgrade execution, recovery writes, and destructive media operations are excluded from autonomous software work. | These require explicit human authorization and suitable real hardware. | Active |
| D-006 | IVprep is an explicit external human handoff; do not bundle or execute upstream downgrade binaries from AutoLoader. | Upstream IVprep targets Win64 and warns that power loss during downgrade can brick the device; 1vyrain's exact compatibility rule must be pinned and validated. | Active |
