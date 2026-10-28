# Runtime Adapter Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The owner has selected native implementation and the current model; preserve those choices.

**Goal:** Add independently selectable, bounded runtime adapters with explicit support states and evidence, while retaining the existing quiet default.

**Architecture:** Establish a single capability registry, schema-2 profiles and schema-4 reports before adding adapters. Keep platform-neutral policy/state in C, Apple ABI wrappers in focused Objective-C files, and build-time identity validation in Python. Each module passes through prepare/validate/publish and forwards through saved originals whenever its contract cannot be maintained.

**Tech Stack:** C11, Objective-C/Foundation/UIKit, Mach-O imported-pointer rebinding, Python unittest, WSL clang sanitizers, Theos arm64 builds.

**Spec:** [Approved design](../specs/2026-10-27-runtime-adapter-expansion-design.md). Read both documents before execution.

## Global Constraints

- The shared quiet default remains the existing 22 exact paths and stat/file-existence entry points.
- Additional entry points require explicit per-game selection.
- Limit a profile to 64 selectable entry points and 64 caller images.
- Limit the combined native binding vector, including compatibility bindings, to 64.
- Rules are limited to 64 entries per rule kind.
- The native map has at most 256 tracked streams.
- Bound each snapshot and the lifetime-safe name pool to 4096 images/names, with at most 64 simultaneously pinned thread views.
- Diagnostic builds support at most 4096 attribution rows and 4096 matched callsite rows.
- Quiet builds compile out diagnostic table storage and do no counter/report/phase work.
- Do not patch executable pages, add JIT requirements, modify the kernel, or remove classes/images from the actual runtime/loader. Do not modify dyld's global image arrays.
- Do not create or install another probe app. Unit fixtures may use synthetic files and objects.
- No source or binary copying from reference projects; implementations are original.
- No account resets, credentials, fabricated attestation, publisher identities, receipts or server decisions.
- No automatic batch rebuild or installation. Publishing this branch, merging it or releasing IPAs requires the corresponding owner approval.

## Review Focus

- A new game version changes custom executable sections while retaining familiar image names: disable affected advanced modules (Task 3).
- A directory handle closes on one thread and its address is reused on another: no stale parent association or invented end-of-directory (Task 5).
- A dependent module installs only some of its hooks: retain original behavior until the entire view is publishable (Tasks 1 and 8).
- A URL completion block is absent, reentrant or scheduled during shutdown: preserve API delivery semantics without duplicate completion (Task 7).
- A loader unload overlaps a pinned enumeration or memory allocation fails: never return a stale header or silently truncated view (Task 8).

---

## Execution and file boundaries

This is one ordered plan because the adapters share installation, policy, identity and reporting contracts. Tasks 4–7 deliver independent modules after Tasks 1–3; Tasks 8–10 build on those foundations. Each task gets its own tests and commit. An unsupported Apple ABI is a documented result, not permission to guess its behavior.

Keep `AMDetection.m` as the coordinator. Continue the existing single-translation-unit inclusion from `SideloadCompat.m`; include each new implementation exactly once there through `AMDetection.m`, avoiding changes to every game's Makefile. Headers must also compile in isolated host tests. Do not refactor unrelated compatibility hooks.

New file responsibilities:

| Files | Responsibility |
| --- | --- |
| `runtime/AMDetectionRegistry.h/.c` | Stable entry-point descriptors and module installation state |
| `runtime/AMDetectionDiagnostics.h/.c` | Complete attribution identities, bounded counters and schema-neutral report data |
| `runtime/AMDetectionIdentity.h/.c`; `scripts/detection_identity.py` | Caller executable-range fingerprints and manifest identity validation |
| `runtime/AMDetectionFiles.h/.c` | Native file queries and write-opening contracts |
| `runtime/AMDetectionDirectories.h/.c/.m` | Native stream tracking and Foundation enumeration |
| `runtime/AMDetectionWrites.m` | Foundation write contracts |
| `runtime/AMDetectionFoundation.m` | Existing Foundation queries plus URLs, environment and preferences |
| `runtime/AMDetectionProcess.h/.c` | Explicit spawn rules and process-query capability boundaries |
| `runtime/AMDetectionViews.h/.c`; `runtime/AMDetectionLoader.m`; `runtime/AMDetectionObjC.m` | Snapshot ownership, loader adapters and Objective-C discovery |
| `scripts/component_manifest.py`; `runtime/AMDetectionComponents.h/.c` | Added-component inventory, dependency closure and runtime ownership |
| `runtime/AMDetectionSDK.h/.m` | Version-bound predicate descriptor validation |
| `tests/test_detection_*.py`; `tests/detection_*_harness.c` | Contract fixtures following existing compiler/unittest patterns |
| `scripts/verify_detection_runtime.sh`; `docs/runtime-adapters.md` | Reproducible isolated verification and capability/evidence documentation |

All paths below are repository relative. Run host commands from this managed worktree in WSL. Set `ROOT` to its resolved absolute path before invoking a helper; never run a build helper against the dirty primary checkout.

## Task 1: Capability registry and installation transactions

**Files:** Create `runtime/AMDetectionRegistry.h/.c`, `tests/test_detection_registry.py`, `tests/detection_registry_harness.c`; modify `runtime/AMDetection.m`, `runtime/AMDetectionHooks.h/.c`, `runtime/SideloadCompat.m`, `runtime/AMImportBindings.h/.c`, `tests/test_import_bindings.py`, `tests/import_bindings_harness.c`.

**Interfaces:** `typedef uint16_t AMDetectionEntryID; typedef uint8_t AMDetectionModuleID;` Registry descriptor `AMDetectionEntry` holds ID, public name, module, ABI kind, rule kind, required-group ID, requirements, original slot and prepare/publish callbacks. `const AMDetectionEntry *AMDetectionEntryByID(AMDetectionEntryID); size_t AMDetectionEntryCount(void); bool AMDetectionValidateSelection(const AMDetectionEntryID *, size_t, size_t compatibility_bindings);`. `bool AMDetectionPrepareModule(AMDetectionModuleID); bool AMDetectionPublishModule(AMDetectionModuleID);` consume immutable startup configuration; expose `AMDetectionModuleStatus(id)` containing adapter state and reason code. These names become the shared interface for later tasks.

- [ ] **1. Write failing registry/import harness tests:** retain IDs 0–7 as legacy aggregates; assign actual `foundation_exists`, `foundation_attributes` and `can_open_url` IDs starting at 8. Assert every concrete descriptor's ID/name is unique and stable, 64 selections succeed, 65 fail, and combined native bindings of 65 fail before any writer runs. Inject a failure at each prepare/publish boundary; assert a dependent group's filtering never becomes active after a partial write. Assert foreign ownership is unchanged.
- [ ] **2. Run:** `python3 -m unittest discover -s tests -p 'test_detection_registry.py' -v` and `python3 -m unittest discover -s tests -p 'test_import_bindings.py' -v`. Expected new assertions fail against the fixed arrays; existing import tests remain the baseline.
- [ ] **3. Implement registry and transaction state:** use fixed bounded storage and a module-active gate read by every replacement. Publish that gate only after required bindings/IMPs validate; wrappers installed earlier forward originals while inactive. On an unsupported publication contract, leave the module unsupported. Replace the six-element binding arrays and magic-index loops with descriptors; preserve existing compatibility entries and original-view caller selection. Append concrete IDs without reusing 0–7. Reserve IDs in a checked-in descriptor table, not by profile order.
- [ ] **4. Rerun both suites plus `test_detection_policy.py`:** all pass; a schema-1 profile selects the same effective hooks and default rules as before. Verify global off installs no new detection interception.
- [ ] **5. Commit only this task's files:** `git commit -m "Add bounded runtime capability registry"` after explicit `git add` and `git diff --cached --check`.

## Task 2: Complete diagnostic identities and schema 4

**Files:** Create `runtime/AMDetectionDiagnostics.h/.c`, `tests/test_detection_diagnostics.py`, `tests/detection_diagnostics_harness.c`; modify `runtime/AMDetectionHooks.h/.c`, `runtime/AMDetection.m`, `scripts/detection_session.py`, `tests/test_detection_session.py`, `tests/test_detection_policy.py`.

**Interfaces:** `AMDetectionEventKey { AMDetectionEntryID entry; uint16_t image; AMDetectionRuleRef rule; AMDetectionPhase phase; }`; `bool AMDetectionEventKeyEqual(const AMDetectionEventKey *, const AMDetectionEventKey *); void AMDetectionRecordEvent(AMDetectionEventKey, uint64_t token, bool filtered, AMDetectionResult);`. Preserve existing record/read functions as compatibility aggregates. `AMDetectionReadCoverage()` returns separate attribution drops, callsite drops, allocation failures, saturation and incomplete flags. Extend session parsing to schemas 2/3/4 while retaining current nonce/ack checks.

- [ ] **1. Write failing tests:** IDs 1, 9 and 17 remain distinct under deliberately colliding hashes; image 64 remains unknown rather than aliasing image 0. Fill exactly 4096 attribution/callsite rows; the next distinct row increments its own drop counter and marks coverage incomplete. Force allocation failure and counter saturation. Concurrent updates preserve complete identities. A quiet object file contains no diagnostic table symbols, readers return zero rows, and filtering still works. Schema-4 parsing preserves module reason/evidence; legacy schema 2/3 fixtures still parse and unknown future schema fails explicitly.
- [ ] **2. Run:** `python3 -m unittest discover -s tests -p 'test_detection_diagnostics.py' -v` and `python3 -m unittest discover -s tests -p 'test_detection_session.py' -v`. Expected failure for missing structured identity/schema support.
- [ ] **3. Implement full-key comparison in bounded synchronized tables:** no packed API identity. Use saturating updates and separate drop accounting. Compile storage and timers out under `AM_DETECTION_DIAGNOSTICS_ENABLED=0`. Emit schema 4 with token-algorithm version, entry/module IDs, support reason, adapter state and separate evidence fields. Preserve historical counter names only as documented aggregates; do not silently assign legacy aggregate counts to a concrete selector. Keep raw query arguments and addresses out of reports.
- [ ] **4. Rerun diagnostics/session/policy suites:** all pass, including session isolation and quiet filtering. Add adversarial sentinel values to inputs and assert none appears in serialized reports.
- [ ] **5. Stage explicit files, check and commit:** `git commit -m "Add complete runtime attribution and schema 4 reports"`.

## Task 3: Schema-2 profiles and caller fingerprints

**Files:** Create `scripts/detection_identity.py`, `runtime/AMDetectionIdentity.h/.c`, `tests/test_detection_identity.py`, `tests/detection_identity_harness.c`; modify `scripts/detection_profile.py`, `scripts/detection_preflight.py`, `runtime/AMDetectionPolicy.h/.c`, `runtime/AMDetection.m`, `tests/test_detection_profile.py`, `tests/test_detection_preflight.py`.

**Interfaces:** Python `executable_identity(macho: bytes) -> dict` emits UUID, SHA-256, and sorted file-backed executable ranges. C `bool AMDetectionIdentityMatches(const AMDetectionImageIdentity *expected, const AMDetectionImageIdentity *actual);` compares bounded identities computed using saved originals. Schema-2 fields are `image_fingerprints`, `module_modes`, `write_probes`, `preference_domains`, `spawn_rules`, `hidden_components`, `sdk_adapters`; all optional, strictly validated. `AMDetectionEffectiveMode(global, module)` permits only equal or reduced modes. New rule kinds append to existing enums.

- [ ] **1. Write failing tests:** schema-1 round trips preserve explicit settings; default profile remains 22 exact paths, two selected APIs and diagnostics false. Schema 2 rejects unknown fields/APIs, 65 rules/images/entries, broad roots, protected storage overlaps, observe+quiet and filtering children under observing/off parents. Mach-O fixtures with executable custom sections change identity when code changes, but not when signing blobs change. Test malformed/overlapping/out-of-file ranges, UUID mismatch and game-version mismatch.
- [ ] **2. Run:** `python3 -m unittest discover -s tests -p 'test_detection_profile.py' -v`, `test_detection_preflight.py` and `test_detection_identity.py` with the same discovery command. Expected new schema/fingerprint assertions fail.
- [ ] **3. Implement schema compiler and shared identity format:** hash the ordered range descriptors plus their file bytes with SHA-256; include only file-backed executable mappings, excluding signing metadata. Validate integer arithmetic before reads. At startup recompute through the original file view before publication; do not hash rebound in-memory imports as executable identity. Require version+UUID+digest for advanced modules; mismatch disables only dependent modules with an explicit reason. Do not change bundle ID or signing inputs.
- [ ] **4. Rerun all three suites plus registry tests:** all pass; generated schema-1 default selection is unchanged. Validate limits before allocating or installing modules.
- [ ] **5. Stage explicit files, check and commit:** `git commit -m "Validate advanced profiles and caller identities"`.

## Task 4: File-query and write-opening contracts

**Files:** Create `runtime/AMDetectionFiles.h/.c`, `tests/test_detection_files.py`, `tests/detection_files_harness.c`; modify registry, policy, hooks and coordinator files, `tests/test_detection_policy.py`.

**Interfaces:** Move existing access/stat/lstat/open/fopen wrappers without changing public names. Add `ssize_t AMDetectionReadlink(const char *, char *, size_t); int AMDetectionFstatat(int, const char *, struct stat *, int);`. `bool AMDetectionResolvePath(int dirfd, const char *path, char *out, size_t capacity);` uses saved resolver operations and returns false for unknown context. Add explicit originals for new APIs; resolve them before activation.

- [ ] **1. Write failing tests:** readlink returns bytes without a trailing NUL on original success and leaves buffers untouched on configured denial; verify errno. Test fstatat descriptor-relative paths, AT_FDCWD, symlinks, normalization and unsupported flags. Unresolved context forwards unchanged. For open, consume mode only when flags require it; test O_CREAT, supported O_TMPFILE semantics, read-only opens, fopen modes and ordinary file writes. Assert protected storage cannot become a denied write target.
- [ ] **2. Run:** `python3 -m unittest discover -s tests -p 'test_detection_files.py' -v`. Expected failure for absent wrappers.
- [ ] **3. Implement wrappers and descriptor entries:** exact file queries use ENOENT denial; selected write probes use EACCES without creating/truncating the target. Preserve original errno on forwarding, including observe mode. Resolve only supported directory contexts, retain lexical versus symlink semantics, and pass through ambiguous cases. Unsupported platform flags/layouts produce support reasons rather than fabricated results.
- [ ] **4. Rerun files/policy/registry suites:** all pass; run the native files harness under ASan/UBSan with sanitizer flags added to its compiler command. No invalid vararg access, buffer write or leak.
- [ ] **5. Stage explicit files, check and commit:** `git commit -m "Extend exact file query and write probe adapters"`.

## Task 5: Directory streams and Foundation enumeration

**Files:** Create `runtime/AMDetectionDirectories.h/.c/.m`, `tests/test_detection_directories.py`, `tests/detection_directories_harness.c`; modify registry/coordinator and profile API declarations.

**Interfaces:** `DIR *AMDetectionOpendir(const char *); struct dirent *AMDetectionReaddir(DIR *); int AMDetectionClosedir(DIR *);`. The private 256-slot map stores handle, generation, parent and lock-protected lifetime. `AMDetectionDirectoryShouldSkip(handle, child)` resolves the complete child rule without logging names. Foundation adapters cover `contentsOfDirectoryAtPath:error:`, `contentsOfDirectoryAtURL:includingPropertiesForKeys:options:error:` and path/URL enumerators only where their concrete contracts can be preserved.

- [ ] **1. Write failing tests:** filter one child while retaining siblings and stream position; end-of-directory preserves errno separately from errors. Fill 256 handles; the 257th forwards unfiltered and marks incomplete. Close/reuse a handle across threads and verify no stale parent. Test unknown handles, original double-close behavior without dereferencing retired state, symlink parents and relative paths. Foundation fixture verifies requested URL properties, enumeration options, skipDescendants, error handler and item ownership; unsupported concrete enumeration behavior fails capability preparation.
- [ ] **2. Run:** `python3 -m unittest discover -s tests -p 'test_detection_directories.py' -v`. Expected native tests fail; Apple fixture is explicitly unavailable on a Linux host rather than counted as passed.
- [ ] **3. Implement stream tracking and Foundation adapters:** synchronize map lifetime, not the game's unrelated stream operations. Forward through saved originals; never manufacture an empty parent or return a pointer into temporary storage. Register opendir/readdir/closedir as one dependent group. Foundation methods activate only for contracts actually exercised on Apple runtime; otherwise report unsupported.
- [ ] **4. Rerun host suite under ASan/UBSan; compile the Apple fixture through the arm64 verification harness in Task 11:** host memory checks pass; record Apple execution as pending until actually run on a supported environment. The compile helper does not execute iOS code. No installed probe app.
- [ ] **5. Stage explicit files, check and commit:** `git commit -m "Add bounded directory filtering contracts"`.

## Task 6: Foundation write probes and process boundaries

**Files:** Create `runtime/AMDetectionWrites.m`, `runtime/AMDetectionProcess.h/.c`, `tests/test_detection_writes.py`, `tests/test_detection_process.py`, `tests/detection_process_harness.c`; modify profile compiler, policy, registry and coordinator.

**Interfaces:** Write adapters cover NSString `writeToFile:atomically:encoding:error:` and NSData `writeToFile:options:error:` plus their URL variants. Python `expand_write_probe(display_name: str) -> str` and runtime equivalent accept only a safe single-component name for `/private/<CFBundleDisplayName>`; use a 4096-byte destination bound. `int AMDetectionPosixSpawn(pid_t *, const char *, const posix_spawn_file_actions_t *, const posix_spawnattr_t *, char *const [], char *const []);` uses exact configured executable paths, not argv/env contents. Process/debug query descriptors declare the exact supported ABI; no generic sysctl rewrite.

- [ ] **1. Write failing tests:** empty, slash/backslash, dot/traversal, embedded-NUL and oversized display names reject expansion; exact accepted name expands without creating a /private prefix. Denied Foundation writes return NO and NSCocoaErrorDomain/NSFileWriteNoPermissionError when error storage exists; null NSError pointers are safe. Test atomic options, URL variants and ordinary saves. Spawn denial returns EACCES directly, leaves PID storage unchanged and preserves errno; unrelated helpers forward with untouched actions/attributes/argv/env. Unknown process-query ABI is unsupported.
- [ ] **2. Run:** `python3 -m unittest discover -s tests -p 'test_detection_writes.py' -v` and the process suite. Expected missing adapters/token validation fail.
- [ ] **3. Implement constrained probes and spawn adapter:** validate against protected storage before publication; obtain installed display name through original metadata. Do not inspect/log argv or environment values. Add process-query support only for a fixture-proven ABI and exact configured predicate; leave other queries original with a reason. No successful fake PID and no unconditional debugger-clear operation.
- [ ] **4. Rerun write/process/profile suites:** all available contracts pass; Apple NSError behavior remains separately tagged until executed. Repeat process native fixture under sanitizers.
- [ ] **5. Stage explicit files, check and commit:** `git commit -m "Add explicit write and process probe boundaries"`.

## Task 7: URLs, environment and preference returned views

**Files:** Create `runtime/AMDetectionFoundation.m`, `tests/test_detection_foundation.py`; move existing Foundation query wrappers out of `AMDetection.m`; modify registry, policy and profile declarations.

**Interfaces:** Register NSURL `URLWithString:` / `initWithString:`, UIApplication `canOpenURL:`, `openURL:` and `openURL:options:completionHandler:` as separate entries with exact ABIs. Environment entries cover existing getenv and NSProcessInfo.environment. Preference entries cover NSUserDefaults `persistentDomainForName:` and CFPreferences `CopyAppValue`/`CopyMultiple` with explicit domain matching. Preserve the legacy Foundation and URL aggregates.

- [ ] **1. Write failing tests:** exact parsed schemes match case-insensitively but prefixes, malformed inputs and unrelated URLs forward. Test nil input, nil completion, reentrancy and queue teardown; denied modern open completes NO once asynchronously on the main queue. Environment dictionary is immutable and contains unchanged unrelated values while actual process environment remains unchanged. Hidden preferences return the documented absent form without modifying stored domains; CF ownership follows Copy rules. Add secret sentinel assertions to report serialization.
- [ ] **2. Run:** `python3 -m unittest discover -s tests -p 'test_detection_foundation.py' -v` plus profile/policy tests. Expected new selection/contract assertions fail; mark Apple execution unavailable explicitly where necessary.
- [ ] **3. Implement selected wrappers through saved originals and TLS guards:** never mutate shared dictionaries or persistent preferences. Do not make URL creation a prerequisite for unrelated URL operations. Validate required combinations where a profile promises coherent returned views. Use exact signatures and appropriate ARC/CF bridges; no blanket object-return hook.
- [ ] **4. Rerun suites and compile ARC arm64 fixture:** no type/ownership warnings; distinguish compile evidence from Apple runtime execution. Existing schema-1 Foundation behavior remains covered by its legacy tests.
- [ ] **5. Stage explicit files, check and commit:** `git commit -m "Extend URL environment and preference query views"`.

## Task 8: Coherent loader views and Objective-C discovery

**Files:** Create `runtime/AMDetectionViews.h/.c`, `runtime/AMDetectionLoader.m`, `runtime/AMDetectionObjC.m`, `tests/test_detection_views.py`, `tests/detection_views_harness.c`, `tests/test_detection_objc.py`; modify registry, coordinator and identity integration.

**Interfaces:** `bool AMDetectionViewsPublish(const AMDetectionImageRecord *, size_t count, uint64_t generation); bool AMDetectionViewsPin(void); bool AMDetectionViewsRead(uint32_t index, AMDetectionImageRecord *out); void AMDetectionViewsInvalidate(const void *header); void AMDetectionViewsReleaseThread(void);`. Records hold stable copied names, header, slide, ownership ID and generation; headers are returned only while generation/lifetime validation succeeds. Loader entries cover count/name/header/slide, add/remove callback registration and dladdr. ObjC entries cover objc_getClassList, objc_copyClassList, objc_getClass, objc_lookUpClass, objc_copyClassNamesForImage and class_getImageName.

- [ ] **1. Write failing tests:** count/name/header/slide agree for a generation; invalid index yields documented null/zero. Test 4096 records/names and 64 pinned views; overflow and allocation failure forward the entire view and mark incomplete. Race unload with a pinned enumeration and assert no stale header is returned. Test callback initial delivery/order/reentrancy, dlerror preservation and mod-internal dependency resolution. ObjC class count/copy/lookup/ownership results agree, allocated lists free correctly, and original game classes remain present. Simulate failed publication after one hook and assert all wrappers still forward.
- [ ] **2. Run:** `python3 -m unittest discover -s tests -p 'test_detection_views.py' -v` and the ObjC suite. Expected missing snapshots/consistent queries fail.
- [ ] **3. Implement bounded immutable snapshots with per-thread pins and unload invalidation:** use original loader callbacks/view internally; do not mutate loader arrays or runtime metadata. Manifest-owned additions are the only hideable records. Until Task 9 supplies a validated manifest, activate no nonempty hiding selection. If unload/callback guarantees cannot be demonstrated for an ABI, keep that module unsupported; do not return stale memory or mixed indexes. Runtime classes remain registered and internally usable.
- [ ] **4. Run host race/ownership fixture with ASan/UBSan and supported thread sanitizer separately; compile Apple wrappers:** no host lifetime failures. Actual callback/ObjC runtime behavior requires Apple execution evidence; label unexecuted contracts pending and keep the corresponding capability unsupported.
- [ ] **5. Stage explicit files, check and commit:** `git commit -m "Add coherent bounded loader and class views"`.

## Task 9: Builder component manifest and strict dependency selection

**Files:** Create `scripts/component_manifest.py`, `runtime/AMDetectionComponents.h/.c`, `tests/test_component_manifest.py`; modify `scripts/build.sh`, `scripts/local_ipa.sh`, `scripts/detection_preflight.py`, profile compiler and identity integration.

**Interfaces:** Python `inventory_base(app_dir: Path) -> dict`, `build_manifest(base: dict, added: list[dict]) -> dict`, `validate_components(base: dict, final: dict, manifest: dict) -> list[str]`. Manifest version 1 records bundle-relative POSIX path, role, unsigned SHA-256, declared dependencies and build ID. Runtime `bool AMDetectionComponentOwned(const AMDetectionComponentIdentity *);` checks loaded identity against the embedded manifest. Paths must stay within the bundle after normalization and symlink resolution.

- [ ] **1. Write failing tests:** original framework with a tweak-like name is preserved; original-byte changes and undeclared additions fail strict packaging. Include exactly declared transitive dependencies and reject cycles/missing dependencies, escaping paths, symlink escapes and duplicate roles/identities. Wrong build ID cannot grant hiding ownership. Signing validation is separate and does not claim publisher identity.
- [ ] **2. Run:** `python3 -m unittest discover -s tests -p 'test_component_manifest.py' -v` and preflight/profile suites. Expected absent inventory/dependency enforcement fails.
- [ ] **3. Implement inventory before injection and strict validation after assembly:** preserve every base component, emit only declared additions and closure, embed the manifest without signing material. Record signed artifact hash separately in local evidence. Wire component identity to loader/class rules and forbid arbitrary container paths. Keep current bundle/signing choice unchanged.
- [ ] **4. Rerun suites using temporary synthetic bundles only:** all pass; no real IPA is repackaged or installed by unit tests.
- [ ] **5. Stage explicit files, check and commit:** `git commit -m "Enforce declared runtime component ownership"`.

## Task 10: SDK predicate contracts and owned-hook conflicts

**Files:** Create `runtime/AMDetectionSDK.h/.m`, `tests/test_detection_sdk.py`; modify registry and `runtime/AMImportBindings.h/.c`, import tests and profile compiler.

**Interfaces:** `AMDetectionSDKDescriptor` holds adapter ID, required image identity, selector/import name, exact ABI, return contract and side-effect classification. `bool AMDetectionSDKPrepare(const AMDetectionSDKDescriptor *);`. No production SDK adapter is enabled without an identified real target and known contract. `bool AMDetectionRepairOwnedSlot(void **slot, void *recorded_replacement, void *expected_owner);` repairs only a ledger-recorded slot after revalidation; no executable write support.

- [ ] **1. Write failing tests:** fake fixture's exact boolean predicate can prepare; wrong UUID/digest/signature, inlined or missing predicate, side-effecting method and server-backed method remain unsupported. Unknown foreign IMP/pointer remains untouched. Owned-slot repair refuses reused slots, changed owners, absent ledger records and code-page addresses. Global off and observe cannot be escalated by SDK selection.
- [ ] **2. Run:** `python3 -m unittest discover -s tests -p 'test_detection_sdk.py' -v` and import/profile suites. Expected missing validation/ledger checks fail.
- [ ] **3. Implement descriptor validation and ownership ledger:** track only installed data slots/IMPs within bounded registry state. Keep unknown predicates unsupported, with no guessed detector names or universal success replacement. Include capability reasons in schema 4 and leave automatic repair disabled.
- [ ] **4. Rerun suites and compile Apple wrappers:** all supported tests pass. A synthetic predicate validates the adapter mechanism only; document that no real SDK bypass has thereby been demonstrated.
- [ ] **5. Stage explicit files, check and commit:** `git commit -m "Validate SDK adapters and owned hook repairs"`.

## Task 11: Reproducible verification and evidence handoff

**Files:** Create `scripts/verify_detection_runtime.sh`, `tests/test_detection_build.py`, `docs/runtime-adapters.md`; update `scripts/validate_mods.py`, runtime-probe CI configuration located with `rg --files .github/workflows`, and applicable test/build documentation. Do not modify personal game profiles.

**Interfaces:** `bash scripts/verify_detection_runtime.sh --output <absolute-private-dir>` creates isolated temporary source copies and compiles quiet/off/observe/advanced-selected arm64 dylib variants; it never signs, installs, starts a network service or edits primary game folders. Each result records source SHA, profile digest, compiler/SDK, artifact hash, entry states and compile evidence. CI invokes the same host-safe contract checks.

- [ ] **1. Add failing build tests:** default compiled selection remains 22 paths/two APIs, quiet artifacts contain no diagnostic table/report/phase timer references, off adds no detection hooks, advanced selection includes only requested modules, and unsupported layouts remain unsupported. Assert build helper never invokes device/signing commands or writes outside its output/temp directory.
- [ ] **2. Run:** `python3 -m unittest discover -s tests -p 'test_detection_build.py' -v`. Expected absence of isolated helper/evidence contract fails.
- [ ] **3. Implement isolated helper and docs:** document each entry's ABI, ordinary forwarding, unsupported reason, profile example and evidence state. Cover all spec families, including system-wide injection/kernel/server-attestation limits. Add a real-game comparison worksheet with control/artifact hashes, original bundle choice, game version, signing setup, phase, duration, caller coverage and drops. Use no account/device identifiers.
- [ ] **4. Verify exact branch:** `python3 -m unittest discover -s tests -v`; `python3 scripts/validate_mods.py`; `bash scripts/verify_detection_runtime.sh --output "$EVIDENCE"`; `git diff --check`. Run native harnesses under sanitizers, public secret scanning and repository commit-identity checks. Inspect exact changed-file list and generated artifacts; record command results rather than reusing prior PR #85 test counts. A compile/skip is never reported as a runtime pass.
- [ ] **5. Commit verification/docs after passing available checks:** `git commit -m "Document and verify runtime adapter capabilities"`. Keep failures/unsupported modules visible; no blanket readiness claim. Prepare a separate expansion review diff from `d9878b4`, leaving draft PR #85 unchanged.

## Real-game trial and publication gates

Implementation may finish with experimental modules disabled by default and device evidence pending. Device availability must not block unrelated host implementation, and a missing positive case must never be filled with a synthetic success claim.

- [ ] Review the final capability table and pick one implemented module with its Apple contract checks satisfied. If none satisfies them, resolve that module's contract first.
- [ ] Prepare one original-ID Pocket no-runtime control and one matched observation artifact privately, preserving previous working IPAs/reports. Show the concrete build/profile changes before a device step; retain the existing account and signing setup.
- [ ] Run matched real-game screens without account login/reset; retrieve only local diagnostic evidence. Use Nuclear Day or Plague only as separate, explicitly labeled comparisons. Do not install a new probe app.
- [ ] Advance to a selected filter comparison only when contracts and profile validation permit it. Record visible compatibility independently from original-result observations and filtered outcomes. A positive-artifact claim additionally requires a real selected caller's positive original result for a known-present artifact and a controlled filter comparison; absent that evidence, label it unverified.
- [ ] Repeat one module at a time; preserve unsupported and incomplete-coverage states. No automatic enablement across all games.
- [ ] Prepare a concrete new PR description and exact destination `AccretionMods/mods`; request owner publication approval for this branch before pushing/opening it. Keep private artifacts and handbook content out of the public diff. Merge and IPA release remain separate decisions.

## Stop, rollback and handoff rules

- A changed fingerprint or foreign hook disables the affected module and leaves originals available. A dependent module that cannot publish a coherent view stays unsupported.
- Contract regression, stale pointers, unexplained missing game data or report leakage stops that module's trial; preserve the evidence and return to the retained control. Do not erase accounts or traces to retry.
- Each task's commit is a review boundary. Revert only the affected task and its dependent unshipped work if rollback is needed; preserve unrelated changes and do not force-push.
- Final handoff names source SHA, available test results, arm64 build results, unsupported contracts, actual real-game evidence and pending publication decisions separately.

## Plan self-review

Coverage: registry/bounds/publication (1), reports/privacy/overflow/quiet builds (2), schema/modes/fingerprints (3), files (4), directories (5), writes/process (6), URLs/environment/preferences (7), loader/classes (8), manifest/dependencies (9), SDK/conflicts (10), verification/evidence/review (11 and final gates). Every Review Focus condition has an owning test step. No production SDK identity or Apple runtime test result is invented by this plan. Implementation starts only after owner review of this plan; use the preserved native execution method.
