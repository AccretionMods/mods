# Runtime adapter expansion

Date: 2026-10-27

Status: written specification approved; implementation plan awaiting owner review.

Base: `codex/minimal-path-filters` at `d9878b4`. This document is on a separate
local design branch. It does not change draft PR #85 or activate additional hooks.

## Intent and success criteria

Provide the main applicable user-space approaches used by the referenced tools in
the existing sideloaded, no-JIT runtime. Preserve the owner's two requirements:
broader coverage and only explicitly needed components/interception points.

Success means that the shared runtime exposes independently selectable modules,
their contracts and limits are tested, and their support/verification state is
reported accurately. Availability of a module is separate from enabling it for a
game or demonstrating that it defeats that game's enforcement.

The shared quiet default remains the existing 22 exact paths and stat/file-existence
entry points. Additional entry points require explicit per-game selection. An
explicit off or observation profile remains authoritative. Ordinary game and mod
operations outside the selected rules continue through their original functions.

## Reference scope and original implementation

The source inventory groups the applicable approaches into filesystem, directory,
writeability, URL/environment/preferences, image discovery, Objective-C discovery,
process/SDK checks, component selection and hook ownership. References are pinned:

- [iHide source](https://github.com/Kc57/iHide/blob/50d6b4eb1125628ccfdc899509786f6ae27ebbff/Tweak.x):
  file/URL queries, write attempts, directory opening, process spawning and image names.
- [Shadow source](https://github.com/jjolano/shadow/tree/5da7c624083ac7fba1f17f17d3e8099c34aa42e8/src/ShadowCore.dylib/hooks):
  capability families including filesystem, environment, loader/runtime discovery,
  anti-debugging and version-specific detector adapters.
- [Choicy scope](https://github.com/opa334/Choicy/blob/b6c9297328a73cff85e69df525afbbd88eeec10c/README.md):
  process-wide and per-library injection configuration in a jailbreak environment.

These references establish approach families, not effectiveness on our phone.
Implementation will be original. No third-party binary is bundled. Any later source
reuse needs a separate licence/notice review; this design does not authorize copying it.

## Platform boundaries

Use precompiled, signed code, imported data-pointer rebinding where supported, and
Objective-C method tables. Retain current unsupported-layout and foreign-owner checks.
Do not patch executable pages, add JIT requirements, modify the kernel, or remove
classes/images from the actual runtime/loader. Do not modify dyld's global image arrays.

An IPA-injected library cannot provide Choicy's system-wide injection controller.
Our equivalent is control over components added by our own builder. Direct memory
inspection, inline syscalls, inlined predicates, authenticated/unsupported pointer
layouts and other unmediated paths remain outside demonstrated coverage.

This work does not fabricate App Attest/DeviceCheck responses, publisher identities,
receipts or server decisions. Preserving a declared bundle ID is separate from possessing
the publisher's signed identity. No account reset or server-response replay is involved.

## Shared architecture

### Capability registry

Replace parallel hard-coded arrays and numeric index assumptions with one registry.
Every entry declares a stable entry-point ID/name, parent module, native or Objective-C
ABI, supported import/layout requirements, installer, original-function storage,
rule category, readiness state and diagnostics mapping.

Limit a profile to 64 selectable entry points and 64 caller images. Limit the combined
native binding vector, including compatibility bindings, to 64. Validate counts before
installing anything. A profile that exceeds a limit is rejected, never truncated into
partial filtering. Objective-C selectors consume their own bounded registry entries.

Installation uses a prepare/validate/publish sequence per dependent module. Resolve and
validate every required entry point before publishing a feature that needs them together.
If atomic publication is unavailable, keep that feature unsupported rather than publish
an inconsistent partial view. Unrelated working modules retain their own status.

### Policy and ownership

Rules remain exact unless an explicit bounded prefix is justified. Broad roots, original
game components and game/mod storage are not turned into blanket denial targets.
There is no keyword-based decision that every class/library containing a certain word
is safe to hide.

Caller selection uses the original image view. Advanced modules require the selected
game version plus image UUID and a preflight digest of declared file-backed executable
ranges. Signing metadata is excluded from that digest. Custom IL2CPP code sections are
included when they belong to executable mappings. A mismatch disables the affected module.

Use an embedded build-component manifest to identify our additions. It records relative
paths, component role, dependency closure and build identity. A hiding rule can target an
added component only when its identity matches that manifest. An original game framework
cannot become a hidden component merely because its name resembles a tweak.

Preserve mod-internal calls and internal resolver operations through saved originals.
Use thread-local recursion guards. No hook uses a filtered public view to determine its
own caller ownership. State is bounded and cannot expose dangling buffers after teardown.

## Adapter contracts

| Module | Initial entry points | Required contract |
| --- | --- | --- |
| File queries | Existing stat/access/lstat/open/fopen and Foundation queries; add readlink and fstatat-style queries where supported | Exact rules, correct return/errno/buffer behavior and unchanged ordinary operations. Relative paths require a resolved directory context; unresolved contexts pass through. |
| Directories | opendir, readdir, closedir; Foundation directory listing/enumeration | Preserve stream position, entry lifetime, end-of-stream/error distinction, ownership and closing. Skip only explicitly matched children; never hide the whole parent directory to conceal one child. |
| Writeability | NSString/NSData write methods and supported write-opening entry points | Deny only configured probe destinations. Correct NSError/errno and optional error pointers; preserve saves, backups, mod state and unrelated write flags. |
| URLs | Existing canOpenURL plus URL creation and opening variants | Exact parsed scheme matching, original handling of unrelated URLs, correct nil/boolean results and completion delivery exactly once with the API's asynchronous contract. |
| Environment/preferences | getenv, NSProcessInfo environment, explicit preference-domain queries | Filter only configured names/domains in the returned view. Do not erase the process environment or change stored preferences. Preserve ownership and dictionary immutability. |
| Loader discovery | Supported dyld count/name/header/slide queries, loader callbacks and dladdr ownership queries | Consistent indexes and documented invalid-index behavior; stable buffer lifetimes; original view for game/mod dependencies and internal resolvers. No empty-name substitution as a stand-alone concealment strategy. |
| Objective-C discovery | Class-list/copy-list, named lookup and image-ownership queries at supported boundaries | Consistent counts/list contents, correct allocation/free contracts and no deregistration of live classes. Target only manifest-identified added classes/images. |
| Process checks | Explicitly selected posix_spawn and supported process/debug query boundaries | No fabricated successful spawn/PID. Match the original API's direct error-code versus errno convention. Never deny an unrelated game helper. |
| SDK adapters | Identified callable Objective-C or imported detector predicates | Exact SDK/image fingerprint, selector/signature and known return contract. Missing, inlined or changed predicates remain unsupported. Side-effecting or server-backed methods are not replaced with unconditional success. |

### Directory details

Associate a selected directory stream with a bounded resolved parent path at open time.
Apply child rules to the complete path. Track only streams created through a supported
entry point; unknown handles pass through. Prevent handle-reuse confusion after close,
double-close changes and cross-thread corruption. Do not log parent or child names.

The native map has at most 256 tracked streams. Capacity exhaustion leaves a new stream
unfiltered and marks coverage incomplete; it cannot invent end-of-directory. Foundation
enumerators need equivalent filtering without changing the concrete enumeration contract.
Descriptor-relative, symlink and normalization cases require dedicated tests.

### Writeability details

Support exact configured probe paths and a constrained profile token for a probe of the
form `/private/<CFBundleDisplayName>`. Expand the token from the actual installed metadata;
reject an empty name, path separators, traversal or an oversized result. This is not a
prefix rule for everything under /private. Unknown destinations use the original API.

If a claimed probe overlaps protected game/mod storage, validation rejects the profile.
Read-only opens retain their original behavior. The implementation must handle variadic
open flags correctly and leave failed writes' output/error objects in the documented state.

### Loader and Objective-C details

Present an immutable filtered snapshot for a stable loader generation. A count operation
pins that thread's enumeration view; name/header/slide operations use that same view.
An image-unload event invalidates affected views before a later query can return a stale
header pointer. Invalid indexes use the API's documented null/zero result. Do not promise
transactional enumeration across arbitrary loader mutations: if consistency cannot be
maintained for a supported contract, keep the module unsupported and use originals.

Bound each snapshot and the lifetime-safe name pool to 4096 images/names, with at most
64 simultaneously pinned thread views. A new view beyond that capacity uses originals
and marks its coverage incomplete. Overflow or
allocation failure is explicit; do not silently truncate the image list. Preserve loader
callbacks and thread-local dlerror behavior. Class filtering must agree with the component
and image view for covered queries, while internal game/mod references remain valid.

Memory scanners and unmediated access to the real runtime metadata remain uncovered.
Query filtering does not make loaded code absent from memory.

### Component selection and conflicts

The builder inventories the base before adding components. Preserve every original
framework/resource. Include only declared added components and their required dependencies.
An undeclared addition fails the strict build check with its role/path identified locally;
it is never removed silently from the base. Record unsigned build identity and validate
the resulting signed artifact separately, without treating signing as publisher attestation.

Before installing each adapter, confirm the expected owner and current pointer/IMP. If
another implementation owns it, leave it intact and report conflict. An optional repair
operation can touch only a slot already recorded as ours and must revalidate its ownership;
it cannot overwrite an unknown foreign hook or an executable page.

## Configuration and compatibility

Keep schema-1 profiles valid and preserve their explicit settings. Introduce schema 2 for
advanced fields: image fingerprints, module-specific probe rules, preference domains,
spawn rules, manifest-owned hidden components and SDK adapter identities. Unknown fields,
API names and unsupported combinations are rejected. No automatic conversion enables
additional entry points in an existing profile.

Retain off/observe/filter and the diagnostics switch. A profile-wide off is authoritative.
Optional per-module modes may reduce the profile-wide mode: filter can contain observing
or disabled modules; an observing profile cannot contain a filtering module. Schema-2
modules in observation mode require diagnostics enabled; reject an observation/quiet
combination rather than install interception with no useful observation. Diagnostics
remain disabled in shared defaults and must not allocate tables or start report/phase
timers in a quiet build. Diagnostic entry points cannot implicitly enable filtering.

Rules are limited to 64 entries per rule kind. Existing protected-root restrictions remain.
Hidden component rules use their dedicated manifest namespace, not an exemption allowing
arbitrary container files to be hidden. Existing game identity/signing choices remain
separate inputs; this expansion does not silently change the installation bundle ID.

## Reporting and internal representation

Current schema 3 packs an API index into three bits and uses fixed native binding arrays.
Do not append adapters to those assumptions. Use an explicit structured attribution key:
entry-point ID, caller-image ID, rule kind/index and phase ID. Store/compare the complete
identity; hashing selects a table slot but never substitutes for equality.

Define append-only entry-point IDs in the registry. Preserve existing counter names as
documented compatibility aggregates. Add schema 4 with entry-point/module identities,
capability evidence and support reasons. New clients read schemas 2/3/4; older clients
reject schema 4 explicitly instead of mislabelling its fields. Session-bound phase requests
and profile identities remain compatible at the semantic level.

Diagnostic builds support at most 4096 attribution rows and 4096 matched callsite rows.
Counters saturate; allocation/table/callsite overflows have separate counters and mark
affected outcome coverage incomplete. Never label dropped outcomes negative. Quiet builds
compile out diagnostic table storage and do no counter/report/phase work.

Keep configured rule IDs, original image/build identity, opaque caller tokens, coarse
outcomes and explicit phase. Do not capture raw query arguments, environment values,
credentials, network bodies, account/device identifiers or raw return addresses. Existing
UUID/relative-offset caller-token derivation stays versioned and reproducible internally.

Separate adapter states (disabled/observing/filtering/unsupported/failed) from evidence:
implemented, statically supported, host-contract-tested, arm64-built, device-compatible
and positive-artifact filtering verified. No aggregate green status means universally safe.

## Verification and rollout

1. Establish registry/schema-4 foundations without changing the quiet default. Test identity
   uniqueness beyond eight APIs, legacy profiles/reports, limits, concurrency and overflow.
2. Add file/directory/write/URL/environment/preferences/process contracts. Cover variadic
   flags, relative paths, symlinks, errno, NSError, null optional outputs, callbacks and
   ordinary forwarding. Use ASan/UBSan for native ownership/state tests where supported.
3. Add coherent loader/class query views with load/unload races, allocation failures,
   callback ordering, snapshot lifetimes and mod-internal dependency resolution tests.
4. Add component-manifest enforcement and supported version-bound SDK adapters. Verify
   transitive dependencies, original-component preservation and foreign-owner conflicts.
5. Build arm64 variants from isolated workspaces. Run the complete suite on the exact
   branch and normal validators/secret/identity checks before a new review PR.
6. Test real games one module at a time against a matching no-runtime control. Start with
   the working original-ID Pocket control; other real subjects can include the previously
   used Nuclear Day and Plague copies. Do not create or install another probe app.

Unit fixtures may use synthetic files and objects; they are not a substitute for real-game
device evidence. Keep app version, bundle choice, signing setup and visible phase matched.
Preserve previous artifacts/reports. Do not reset accounts or collect credentials. Device
compatibility and a demonstrated enforcement bypass are distinct outcomes.

A positive-artifact claim requires an actual selected game caller to receive a positive
original result for a known present artifact in observation mode, followed by a controlled
filter comparison. A self-check from our own unselected image does not satisfy that gate.
If no suitable positive real-game case exists, retain the implementation/contract-test
status and label device filtering unverified; do not invent a positive or block unrelated
module development. Such a module remains disabled by default and experimental.

The next expansion PR is separate from #85. Publishing its branch, merging it or releasing
IPAs requires the corresponding owner approval. No automatic batch rebuild or installation
of all games is part of this design.

## Completion definition

Every applicable approach family has a registry entry, explicit configuration, correct
ordinary forwarding, bounded state, honest unsupported reasons and tests. Each delivered
module has an evidence record. Real-game limitations and unmediated paths remain visible.
Kernel/system-wide injection features and genuine server attestation are documented as
outside this runtime, rather than being represented as implemented protections.
