# Studio workflow implementation plan

**Goal:** Apply the eight approved Studio improvements in order and review JIT candidates for defensible no-JIT alternatives.

**Architecture:** Keep the stdlib server and vanilla-JavaScript page. Put local workspace persistence in
`studio/workspace.py` and scan presentation/analysis in `studio/scan_view.py`; keep game mutation in dev.py.
Private runtime data stays under AM_ROOT/studio, outside the repository. No new dependencies.

**Approved design:** The eight-item recommendation in this chat, followed by the owner's instruction to apply it in order.
Publication continues through the existing Studio PR. Personal mods are read-only during verification.

## Global constraints
- Preserve authentication, local-only console controls, private artifacts and publication approval.
- No game, device or signing action during tests. Use synthetic fixtures.
- No-JIT alternatives are candidates until tested on the exact game version; a direct hook still requires JIT.
- Preserve unrelated files and include only task files in commits.

## Ordered tasks
1. [x] Instance/version: source fingerprint, loaded/current status, local idle-only graceful restart; reuse registered instance in launcher.
2. [x] Filters: Pin, Hook, No JIT, Needs investigation and tested-state filters; show capability and candidate evidence separately.
3. [x] Index/paging: invalidate method caches by file stat; cache enriched scan by dump/config/pattern stamps; offset/limit paging and append-only UI.
4. [x] Related targets: group field/getter/setter entries; preserve exact overload signatures as variants.
5. [x] Explanations/no-JIT: explain name/type/reachability evidence; exact same-class/type alternatives; never equate setter calls with intercepting code.
6. [x] Snapshots: allowlisted config snapshots, content diff preview, checked restore with an automatic pre-restore snapshot; UI actions.
7. [x] Jobs: local persistent redacted history; restart marks interrupted jobs; serialize conflicting mutations and device operations; cancellation and history UI.
8. [x] Verification: attach switch results to targets with exact game version; stale results require retest; expose status filters and scan evidence.

## Review focus and verification
- A stale server must report it is stale and decline restart while jobs run.
- Cached data must invalidate when dump or configuration changes at the same path.
- No-JIT filters must not claim raw direct hooks are converted or verified.
- Snapshot traversal/stale previews must fail before writes; restore must preserve a recovery copy.
- Jobs must not persist typed secrets, restart must not rerun an interrupted signing/install command.

For each task: add a behavioral regression, see it fail, implement, run targeted tests and review diff.
After integration run the full unittest suite, validators, JavaScript checks, and read-only local API checks.
Record results and limitations in studio/HANDOFF.md; update the private ATLAS for new module paths.
