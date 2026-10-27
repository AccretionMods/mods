# Runtime adapter expansion checkpoint

Date: 2026-10-27. Checkpoint retained to conserve the owner's remaining usage.
The expansion is not complete or ready for publication.

Branch: `codex/runtime-adapter-expansion-design`.
Base: `d9878b4`; draft PR #85 remains separate and unchanged.

## Committed implementation milestones

| Commit | Change |
| --- | --- |
| `12ca70e` | Bounded registry, stable IDs and module publication gates |
| `713448d` | Full diagnostic identities, schema 4, explicit overflow and quiet tables |
| `2959669` | Schema 2, reducing module modes and signing-independent caller fingerprints |
| `864f03f` | File/readlink/fstatat contracts and exact write-opening probes |

The subsequent directory checkpoints add a bounded 256-stream map, per-stream locking,
handle retirement/reuse, original-result reporting after filtering and explicit coverage gaps.
Task 5 now includes a runnable host Foundation fixture. Apple execution is still pending,
so Foundation filtering remains unsupported and disabled.

Latest verification: 283 tests ran without failures, with two skips. The native
directory cases passed ASan/UBSan. Quiet-default and expanded arm64 builds succeeded.
Apple Foundation execution and device behavior were not verified by these builds.

The shared default is still 22 exact paths, stat/file existence and diagnostics disabled.
New APIs are explicit and require schema-2 caller fingerprints. No phone app was installed,
no account was touched, and no branch or IPA was published during these changes.

## Directory limitations and next step

- Relative native directory opens forward unfiltered and mark coverage incomplete: a cwd
  snapshot cannot establish the stream's parent safely. Add a descriptor witness before
  expanding that coverage; retain the regression test for the current safe fallback.
- Foundation listing code compiles but remains unavailable behind
  `AM_DETECTION_APPLE_DIRECTORY_CONTRACTS_VERIFIED=0`. Do not change this without actual
  Apple contract execution. Enumeration entries explicitly report `implemented=false`;
  their concrete contract is unsupported rather than replaced by a guessed proxy.
- Native symlink-parent, ambiguous normalization, cross-thread reuse and a blocked-read /
  concurrent-close case are covered. The concurrency regression found and fixed a close
  waiting on a stream while holding the global map lock; unrelated streams now proceed.
  Slot generations are revalidated after acquiring their locks, and open never waits on
  a slot while holding the map. Unimplemented contracts also fail registry preparation
  without executing installers, even when callbacks have been registered.
- `scripts/detection_apple_contracts.py --output <private-report.json>` runs the actual
  Foundation listing fixture on macOS. It compiles an isolated source snapshot and binds
  the result to LF-normalized source hashes. Windows/Linux return explicit unavailable
  evidence and exit 2. Compiler failures and execution timeouts cannot produce passed evidence.
  The fixture covers path/URL listings, returned-object identity, resource keys/options,
  optional errors, original error results, observation and unselected-call forwarding.
  It compiles for arm64 here but has not executed on an Apple runtime. A host pass never
  enables a profile or claims device/positive-artifact verification. No test app is installed.
- Directory entry counters count wrapper calls. Attribution rows count rule observations,
  which may exceed calls; the registry/report labels this unit. Filtered entries retain
  their actual original positive result instead of claiming the original was never called.
- Review coordinator failure paths before any device trial. Initialization failure must
  keep a module inactive, omit unusable bindings and preserve its unsupported reason.

## Remaining plan

Next implement Tasks 6–11: Foundation write probes/process boundaries;
URL/environment/preferences; loader/class views; component manifests; SDK/ownership checks;
then isolated verification, capability documentation and a fresh whole-branch review.
Do not restart the completed implementation milestones or touch the dirty primary checkout.

Pending verification includes Apple execution, adversarial report serialization/privacy,
real-game comparisons and positive-artifact evidence. Check native resolver provenance
when `dlsym` may itself resolve an interposed implementation during Task 10's ownership work.
Host fingerprint parity tests currently require OpenSSL development headers/libcrypto;
confirm CI/platform availability. These checks are not replaced by passing local C tests.

The native execution ledger and command logs are in the ignored plan workspace:
`.superpowers/sdd/2026-10-27-runtime-adapter-expansion/`. Retain it while work is incomplete.
Use its recorded rulings and commit ranges when resuming the approved plan.

Keep host tests, arm64 compilation, actual device compatibility and demonstrated filtering
as separate evidence. Publishing this expansion, merging it and releasing IPAs remain
separate owner decisions; no publication approval was consumed at this checkpoint.
