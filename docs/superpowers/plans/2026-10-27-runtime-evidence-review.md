# Runtime evidence and diagnostic privacy review

Reviewed 2026-10-27 at commit `fa3387c` on `codex/runtime-adapter-expansion-design`.
Scope: evidence reporting and diagnostic output privacy. This is not a complete
runtime correctness review or approval for release.

## Finding: diagnostic output has no field-level privacy validation

`scripts/detection_session.py:14` validates only that the root is a dictionary
and its schema version is 2, 3 or 4. It returns every supplied field unchanged.
The report command at line 97 then serializes the whole result to standard output.
The 8 MiB input limit bounds file size; it does not establish field privacy.

A local synthetic check supplied schema version 4 and an unexpected field containing
`FICTIONAL_REVIEW_SENTINEL`. The validator retained the field and the sentinel appeared
in serialized output. No device, account, credentials or real diagnostic data was used.
This establishes a validation gap, not evidence that the current runtime emits secrets.

Before treating exported reports as suitable for sharing, define version-specific
allowed fields, nested shapes and value bounds. Reject unexpected fields or construct
an explicitly approved export projection. Verify the full report-output path with
fictional private fields at both root and nested levels. Keep raw diagnostic files private.

## Evidence verified from local sources

- The worktree was clean before this review; HEAD was `fa3387c`.
- The saved `task6-final-suite.log` records 288 tests and `OK (skipped=2)`.
  This review inspected that historical log; it did not rerun the full suite.
- Both Apple directory and write contract verification macros default to zero.
- The Apple runner explicitly returns unavailable on non-macOS hosts.
- The runner labels its scope as direct-wrapper macOS host execution and leaves
  device and positive-artifact verification false even after host success.
- The handoff explicitly states that implementation and publication readiness are
  incomplete. Apple execution, device validation and a whole-branch review remain pending.

## Disposition

Needs further privacy validation before diagnostic exports can be described as safe
to share. Historical host tests and arm64 compilation do not establish Apple runtime
behavior or device compatibility. No universal compatibility or undetectability claim
is supported by this review.

This review changes documentation only. It adds no concealment capability, runs no
phone operations, and publishes no repository or build artifacts.

## Implementation (2026-10-27, after the review)

`scripts/detection_session.py` now validates every report against a per-schema
field allowlist before anything is printed or used. Each schema (2, 3, 4) names
its root fields; nested shapes are listed for entries, adapters, attribution rows,
callsites and their results, coverage, evidence and the two dynamic maps, whose
keys are restricted to the registry entry names and the import-failure names the
runtime emits. Values are bounded: counts are non-negative integers, text is
printable ASCII up to 256 characters, lists and maps hold at most 4096 items,
session and request tokens are 32 lowercase hex characters or empty, states and
phases are enumerated. `validate_report` returns a freshly built projection; the
input object is never returned. An unexpected field at any depth raises an error
that names only the parent path, never the offending key or value.

Schema 4 fields come from `AMDetectionReport` in `runtime/AMDetection.m` at
`fa3387c`; schema 3 from the layout commit `713448d` replaced. No schema 2 emitter
exists anywhere in this repository's history, so schema 2 reuses the schema 3 list.

`tests/test_detection_session.py` plants `FICTIONAL_REVIEW_SENTINEL` at the root,
inside entries, evidence, adapters, attribution, callsites, callsite results,
coverage and the failure-reason map, and runs the real `report` command path with
a mocked USB transfer: each case is refused, standard output stays empty and the
sentinel appears in neither output nor error text. The earlier test that labelled
a schema 4 shaped report as schema 2 and 3 was replaced by version-specific
fixtures. Result: 7 session tests pass on the Windows host; the detection family
passes except suites that need a C compiler or macOS, which skip or error there as
before. Raw report files stay private; this adds no sharing path.
