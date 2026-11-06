# Mod Studio - handoff
Read first when resuming. Project row in `~/PROJECTS.md`. The handbook rules (`../../handbook/CLAUDE.md`) apply:
this lives in the AccretionMods/mods repo, so nothing personal, no secrets, neutral commits, PRs with auto-merge.

## What it is
Workspace layout (2026-11-03, owner's choices): the Mods list on the left; tabs on top in two groups, "This mod"
(Switches, Find, Suggest, Scan, Build & phone, Files) and "Studio" (AppStore, AccretionWeb, Console), in normal case;
on the mod's tabs its name, branding switch and Install as sit above the status cards (dump, last build, runtime on the
phone, switches confirmed); Studio's tabs show neither. The header holds only Studio-wide actions plus "Build and run
on the phone", which also stays in the Switches tab's "Try it on the phone" box; Build & phone has the single steps
(sign + install, read the log for N s). An "On the phone" panel (live switch states, Watch / Push / Screenshot) and the
jobs list as a bottom dock (newest chips, one on a phone; open state in `studio.jobs.open`; the page leaves room for it
through CSS `--dock-h`). Job rows: Stop only while running, start time and duration, a failed job's last line (the jobs
list sends it as `last`). Remove is under each switch's ⋯ menu. Tab help is one line with More. (The 2026-10-26 stage
nav was replaced by top tabs the same day, `cb217ee`.) Checked in the browser at 1440 px (dark, light) and 375 px.
`/api/mods/<mod>/live` has `runtime` ("attached" / "not found" / null). Design canvas with the next steps (guided phone
test, one-switch editor): handbook HANDOFF.
Auto-build includes all topics in its candidate selection, model prompt and confirmation dialog.
Existing targets and inspect-only entries remain excluded; automatic additions still require a valid no-JIT pin.
Custom always-on features can be recorded in local `AM_ROOT/dev/<mod>.json` under `verified_features`.
Studio shows their evidence separately from switches and marks results stale if either game or mod
version changes. Local `patterns.json` may contain `workflows`: topic words, a proven-on scope and
review criteria. Relevant prompt-builder requests include these checklists for every engine, explicitly
as candidates requiring fresh analysis in the next game. Personal evidence stays outside Git.

The eight approved workflow improvements are implemented (2026-10-22): managed instance reuse and
source-version detection, capability/result filters, cached offset paging, grouped property variants,
no-JIT candidate explanations, configuration snapshots with preview/recovery, persistent redacted job
history with resource queues, and exact game-version verification with stale evidence labels.

Store/IAP discovery includes mutable scalar fields and supported methods across assemblies while
hiding generated metadata. Group expansion preserves overload signatures. Ambiguous name/arity
methods cannot be added directly because the runtime cannot resolve them safely.

No-JIT alternatives are structural candidates, not equivalent replacements for direct interception.
The read-only configured-hook audit found one property/field alternative and nine timed-call candidates
among Wanderer's 19 matched JIT methods. No personal game configuration was changed or phone-tested.
The managed local instance uses port 8768; the launcher reuses its registered port. Older instances
were left untouched. Local history and snapshots live under AM_ROOT/studio, outside Git.

Validation: 206 unittest tests, 11 skipped; synthetic snapshot/queue/version/scan regression coverage,
mod/tweak validators, Python lint, JavaScript parsing and read-only local API checks. The current work
has no new device/signing result. Earlier phone results below describe earlier versions only.

A local web UI for the mod dev loop (`scripts/dev.py`, README "Dev loop for personal mods"): one page on the PC
that lists the mods, searches a game's dump, adds switches (hooks) and no-JIT pins, builds, signs and installs on
the phone, and streams the device log. Started 2026-10-22 (owner: "make a UI for it and add it as a new project";
chose a local web app in this repo over a desktop app, a Claude panel or an iOS screen).

- `studio/server.py`: stdlib HTTP server (`am ui`): JSON API over dev.py's functions, long commands as jobs
  with streamed output, static page.
- `studio/index.html`: the page (vanilla JS, Accretion's design tokens, dark/light).
- Binds 127.0.0.1 by default. `--host 0.0.0.0` exposes it on the LAN with no login: trusted Wi-Fi only.
- Apple ID prompts (2FA) can't be answered through the page: sign in once from a console (`am sign ... --install`).
- **Run with Claude Code** (2026-10-22): `/api/claude` -> `claude -p --output-format stream-json` job (this PC only),
  read-only = Read/Grep/Glob + read-only dev.py via uv, no edits; else acceptEdits + any dev.py, no other shell.
  Checked for real: narrow task on Wanderer Dev, 107 s, $1.44, recommendation with commands, no files changed.
- **Prompt builder** (2026-10-22, owner chose it over a library / run-Claude / context pack): `studio/prompts.json` tasks
  + `/api/mods/<mod>/context` facts (switch results from tweaks.json `verified`, log errors from Studio's last log/run job)
  -> prompt with missing-detail hints. Replaced the three fixed prompts (they were the owner's examples). 
- **Console tab** (2026-10-22): `/api/console` runs any dev.py command as an interactive job (`/api/jobs/<id>/input`
  answers prompts), this PC only; regexes kept (shlex, no escapes); `dev.build_parser()`. Prompts for Claude (update,
  explore dump, narrow approach). Switches tab: build / sign + install / run. Jobs panel under every tab. Checked in
  the page: find with a regex on the Wanderer dump, a prompt filled in, the Switches buttons.
- **Games search + CLI** (2026-10-22): `scripts/games.py` (moved from studio/) adds search, info, like one game;
  `dev.py games` (`am games ...`, `--json`) and `/api/games?list=search|like`, `/api/games/info`; Enter in the tab's
  box searches the App Store, Like this on cards; newer list wins over a slow older one. Checked: 9 Kings found and liked.
- **Games tab** (2026-10-22): `scripts/games.py` + `/api/games?list=&region=`: App Store charts (RSS genre 6014,
  new feeds filtered to games), "like the games we mod" (themes from name/description + sub-genres, iTunes
  Search), cards with our-mod / .ipa-ready / online badges, Get .ipa and New mod (no copy: opens Get .ipa).
  Checked in the page: similar list (wasteland/survival RPGs), Top free, New mod -> Get .ipa. First load ~10 s.
- **Get .ipa** (2026-10-22, merged in mods #20): top-bar dialog over `scripts/ipa_fetch.py` (`am fetch`): source
  decrypt.34306.lol (search, site account; captcha -> browser hand-off), decrypt.day and armconverter (browser
  only); the `fetch` job waits for the download in Downloads and files it in intake/. Build & phone tab: "Get the
  newest from the decrypt site". Checked in the page against the live site (paused, captcha shown); no real download yet.

## Runtime: il2cpp_* names from the symbol table (2026-11-03)
- `am_il2cpp_lookup` is the one ladder for every `il2cpp_*` name: `dlsym`, then `runtime/AMMachSymbols.c` reads
  `LC_SYMTAB` of UnityFramework (or the app binary when there is no framework) for a build with hidden exports.
  Hooks (`am_il2cpp_api_load`) and the data layer's pins (`AMIl2cppData.m` `IL2CPP()`) both use it. The
  reference is weak: a Makefile without `AMMachSymbols.c` links and stays dlsym-only; `validate_mods.py` now
  requires the file, `dev.py new` lists it, and the local personal mods' Makefiles were updated on this PC.
- Checks: `tests/mach_symbols_harness.c` (framework and executable shapes, malformed tables; the reader used to
  segfault on an executable behind `__PAGEZERO`), `tests/test_il2cpp_runtime.py` resolver cases,
  `scripts/inspect_il2cpp_symbols.py` on a real decrypted IPA (Fallout Shelter 2.7.0: 42/42 exported; the first
  version reported 0/42 because Darwin names carry a leading underscore). `am report` shows `il2cpp API:
  exported / symbol table only / not found`; symbol-table-only is "good" with a note, not found is "not supported".
  Shared Mach-O fixtures: `tests/macho_fixtures.py`.
- Verified on a phone (2026-11-04): the weak import loads and `API found` with the export trie stripped (the
  symbol-table-only binary is The Wanderer 10.1 with its `il2cpp_*` export-trie edge renamed, `LC_SYMTAB`
  untouched; see the next section). BLEACH Mirrors High and Pokémon TCG Pocket strip both tables and stay
  unsupported.

## Runtime: data-table hooks for games that strip every il2cpp_* name (2026-11-04)
- hooks.json `"table": {registration_rva, dump, binary, module}` switches a mod to `AMIl2cppStartTable`: the
  build resolves each hook and pin method to (Il2CppCodeGenModule, methodPointers index) with
  `scripts/table_hooks.py` and `runtime/AMTableHook.c` swaps the slot from a dyld add-image callback, before
  IL2CPP builds its MethodInfos. `am dump` writes `il2cpp.json` (`registration_rva`) next to the dump from the
  dumper's `CodeRegistration :` line; `scripts/build.sh` passes it with `AM_DUMP_DIR` (`am build` sets it). A
  cached dump made before this has no `il2cpp.json`; `am dump --force` makes one.
- Review fixes (same day): `runtime/AMTableHook.c` is now in every Makefile and in the validator's
  `RUNTIME_FILES` (the probe build was red: undefined `_AMTableHookInstall`). The codeGenModules offset is no
  longer fixed at 120: `table_hooks.detect_layout` tries 120 / 104 / 96 (IL2CPP 29.1+, 27.1 to 29.0, 27.0) and
  keeps the one whose table reads as `*.dll` assemblies; the generated C carries it as
  `am_hook_table_modules_offset`. The runtime reads the registration and modules through `vm_read_overwrite` with
  sanity caps, so a wrong RVA logs `table: no codeGenModules ...` instead of crashing the game. Spec names keep
  the namespace (`Namespace.Class.Method`, what `AMIl2cppRegister` uses); before, a namespaced hook never got its
  replacement. Table names are encoded like the hook names (no plain `Class.Method` in the .dylib). Pins:
  static and const fields are skipped (their dump offset is into static storage) and a pin's method must be a
  parameterless void instance method, else the build stops with the signature. One pointer map per build
  instead of a lief parse per hook. `AMIl2cppStartTable` calls the report handler after installing.
  `tests/test_table_hooks.py` is a unittest module now (the pytest-style file never ran under CI's
  `unittest discover`).
- Not verified on a phone: the table path itself (The Wanderer 10.1 keeps `LC_SYMTAB`, so the probe used the
  symbol-table path). The flat-image fixtures in the tests cover the layouts and the fixup decoding.

## Runtime: JIT code hooks fault on iOS 27; pointer hooks are the default (2026-11-04)
- Device test (Apple-ID signed The Wanderer 10.1, symbol-table-only build, StikDebug JIT): no JIT → pointer hooks,
  `ExperienceLevel.Update` counts (1,854+). JIT → `JIT is on: code hooks`; all four probe hooks install through
  `MSHookFunction`, then the app dies on the first hooked call: `EXC_BAD_ACCESS KERN_PROTECTION_FAILURE`,
  `ESR 0x8200000f`, PC in a 16 KB `mapped file ... rw-/rw-` page beside UnityFramework (`r-x/rwx` text before it).
  The patched code page's maximum protection never regained execute.
- Root cause is ElleKit 1.2's page protection, not signing: every `mach_vm_protect` there passes
  `set_maximum=0` (`ellekitc.c` `EKHookMemoryRaw_impl`, `Orig.swift`, `Batch.swift`, `SVC.swift`, `Libhooker.swift`,
  `Execute.swift`), so the COW page made to patch code keeps max `rw-`; the restore to `r-x` fails and the next
  call faults. `EKJITLessHook` (hardware breakpoints) is skipped on arm64 because its original stub needs a
  `pacibsp` prologue. Patching the restore call in the prebuilt dylib to `set_maximum=1` did not change the page
  (the trampoline path is separate): a real fix needs ElleKit rebuilt with `set_maximum` at every site, or a
  different hooking library.
- Runtime: pointer hooks are the default even with JIT; `AMIl2cppSetCodeHooks(YES)` opts in. The probe calls it,
  hooks two game `Update`s, logs counts every 5 s and logs `hook %p via EKJITLessHook|MSHookFunction`. Crash
  reports: `/TheWanderer-2026-11-04-001806.ips` and `/TheWanderer-2026-11-04-002211.ips`. Code hooks stay
  unusable on iOS 27, so `needs_jit` switches cannot work there.
- Not verified: a certificate-signed JIT build (the Signulous profile has no `get-task-allow`, so no debugger);
  the Apple-ID copy was used.

## Working on Studio: run, test, change safely (2026-10-28)
- Run: `uv run --no-project --python 3.12 studio/server.py` (port 8765; `.claude/launch.json` "mod-studio"). After a
  code change the header says "Studio update available": Load update restarts it (it watches `studio/*.py|html|js|css`
  and `scripts/*.py`).
- Tests, full (C runtime harnesses too, ~20 s): in WSL, `python3 -m unittest discover -s tests`. On Windows:
  `uv run --no-project --python 3.12 --with pytest --with pyyaml python -m pytest -q tests` (the C harnesses skip:
  no POSIX `-ldl`). Every test file must also pass on its own; a module-level patch that leaks into other files is a bug.
- Undefined names before a commit: `uv run --no-project --python 3.12 --with pyflakes python -m pyflakes <files>`.
- Files are LF (`.gitattributes`). Shell heredocs on this PC can turn `\n` in a string into a real newline: edit
  with the Edit tool or a script file.
- A mod's `tweaks.json` is read and written only through `scripts/mod_tweaks.py` (`dev.save_tweaks`): it keeps the
  previous file in `AM_ROOT/dev/history/<mod>/`, puts back a template switch `Tweak.x` uses, and builds stop on a
  switch `Tweak.x` uses that the menu lost. Every change to a mod (Studio or command line) is snapshotted first:
  `dev.py snapshots <mod> [--restore ID]`. Personal mods are never committed; `dev.py new` lists them in
  `.git/info/exclude`.
- The page is `studio/index.html` + `app.js` + `app.css`; `tests/test_studio_page.py` holds `app.js` to the job
  commands and flags the server and `dev.py` have, and CI runs `node --check studio/app.js`.
- Splitting `dev.py`: signing, installing and the phone are in `scripts/dev_signing.py`; the live phone loop
  (push, snap, snap-diff, log, test) in `scripts/dev_live.py`. Pattern for the next piece: the module calls its own
  functions directly, reaches dev.py's basics (`mod_dir`, `load_state`, `am_root`, `WINDOWS`...) as `dev.<name>`
  and another split-out module's as `dev_signing.<name>` when a function runs, and does `import dev` on its last
  line; dev.py re-exports its names; tests patch its functions on the new module. Run as a script, dev.py registers
  itself as `dev` first, so the modules' errors reach main() (`tests/test_dev_cli.py` ScriptErrors). Next
  candidates: post/share/report (~200 lines), dump/scan/find (~390, most test patches).
- One task, one branch: off `codex/studio-feature-scope` until PR #83 merges, then off `main`. The detection
  layer's older local copy (design docs, probe app) is on the local-only branch `detection-layer`; the push guard
  refuses it (`git config --get-all pushguard.localOnly`).

## State (2026-10-23)
- Local nested pin paths: scanner follows reference fields/getters, arrays/lists/dictionary values and typed
  casts across assemblies. Add and Auto-build preserve `root_class`/`path`; CLI validates metadata before
  writing, compiled/live pins share the bounded resolver. Survivor: Ace 1.2.0 exposes HotFixBattle candidates
  through BattleGame._world -> CurLogicWorld -> entity manager/context. Players use _playerSlot; bullets use
  _entityDic values with a concrete type filter. Scratch work lists are excluded from automatic routes.
  An isolated arm64 tweak with three battle paths compiled. No personal mod or phone install changed;
  runtime object access and gameplay effects still need a phone test. Rebuild before pushing nested pins.
  Final scan: 3,103 HotFixBattle candidates (5,300 total). Validation: 229 tests passed, 11 skipped;
  JavaScript parses, Survivor Ace+ and tweak-library validators pass. Full mod validation still reports
  the existing Minecraft add-on folder as incompatible with the tweak-only schema. Changes are uncommitted.
- Branch codex/studio-feature-scope (pushed, main merged in). Added this session, all checked in a test copy of Studio
  (AM_ROOT C:\AccretionMods-storetest, port 8779, launch config `mod-studio-storetest`) and against the live store:
  - Store tab (`store_view.py`): store numbers, admin link (linked as b•••@me.com: token in AM_ROOT/studio/admin.json),
    moderation (store `studio` function, `op=moderation|act`), our mods with Publish/Remove jobs (`store_publish.py`:
    PR with auto-merge, then the tweak release or the cloud build, which the job now waits for), endpoints, keys and
    GitHub secret ages, workflow runs, screenshot links.
  - Auto-build progress bar (step, %, time) and a step bar for publish jobs.
  - Screenshot links (`shots.py`, store `shots` function): phone screenshot through Accretion Link's tool server
    (`accretion-link-cli serve`), 3m/6m/1y, renew/delete; post drafts put the mod's live links in place of LINK ONLY.
  - Menu image page (`/menu-image`, `menu_image.py`): the menu drawn as on the phone (sheet, nav, search, 30 pt rows,
    green/red/off states, Mix), PNG or upload as a post link. Measured against a phone screenshot; fonts differ
    (Segoe UI here, SF Pro on the phone).
  - Not tried by hand: the phone-screenshot and Menu image upload buttons.
  - Effort picker (default/low/medium/high) next to the model picker, for every engine: Claude `--effort`, Codex
    `model_reasoning_effort`, aider/Gemini `--reasoning-effort`. Gemini engine: aider with `gemini/gemini-3.6-flash`
    (AM_GEMINI_MODEL changes it), key GEMINI_API_KEY in the Windows user environment (gcloud key accretion-local-gemini,
    project accreation; Studio reads it from the registry when it started before the key existed). Free tier: 20
    requests a day per model per project (seen on 3.6-flash), so a 9-batch Auto-build fits once or twice a day; CI
    uses 3.5-flash(-lite), a separate quota. Checked: a real Gemini answer through aider in 7 s, pickers in the page.
    Not tried: a full Auto-build with Gemini.
  - Auto-build asks several batches at once (Claude/Codex 3, aider/Gemini 2, `--workers`), retries a failed or
    rate-limited batch once, and reads JSON answers (`{"row", "value", "when", "title"}`): Studio builds each pin from
    the scan row, checks the value against its type and runs dev.py pin in-process (gen once in step 4). Older
    `am pin` lines still count. Stop kills the model processes too (taskkill /T). Unit-tested; not yet run end to end.
  - Scan and pins reach hot-update games (2026-10-23, after Survivor: Ace+'s 80 Auto-build switches all landed on
    bought packages' demo/effect scripts): the scan reads hot-update assemblies (HotFix*, HotUpdate*) instead of
    Assembly-CSharp when a game has them (mod.json `scan_assemblies` overrides), names those classes with their
    assembly (`HotFix:HotFix.GameManager`), reaches `Singleton<T>` managers through the base's static
    (`GameManager.m_t`: the runtime looks up the parent chain and initialises the generic class), treats Photon `FP`
    fields as int64 with `"scale": 65536`, and skips demo/sample/example/editor/test classes. Auto-build prints where
    its candidates come from. Survivor: Ace: 647 candidates in HotFix (98 managers). Not reached yet: the fight's
    own objects (HotFixBattle entities have no singleton or MonoBehaviour: needs a path through a manager).
    Survivor: Ace+ restored to Keep screen awake, 1.0.3 built with the new runtime. Not phone-tested: a Singleton or
    FP pin.
- Earlier (v1, 2026-10-22): built and checked in the built-in browser against the Wanderer example mod: mod list, Switches (18 items with and checked in the built-in browser against the Wanderer example mod: mod list, Switches (18 items with
  hook/pin chips, watch line), Find (regex search, class methods, fields, Hook/Pin/Call forms: a pin added through
  the page landed in tweaks.json and the generated code), Suggest (36 rows from 58 learned patterns), Build & Phone
  (check-hooks job streamed into the console), Files; phone width (375 px) with no horizontal scroll; dark/light.
- `tests/test_studio.py`: the API on a synthetic mod and dump (quick commands, errors, a streamed job).
- Phone run from the page (2026-10-22, iPhone on USB, Wanderer example as `mods/wanderer-dev`, a scratch copy of
  `C:\AccretionMods\dev\wanderer-dev-example`): Build .ipa (dylib 141 KB + 81.6 MB ipa streamed into the console),
  Sign + install (Apple ID session reused, installed as `...wandererdev.2XN3V474V3`, listed on the phone), Log with
  "open the app first" (hook report, `watch:` lines, `pin keepStats applied`, `catalogue: 145 items`). Three bugs
  found and fixed on the way: `send_error` crashed the quiet `log_message` (HTTPStatus as first arg); sign/install
  jobs got the mod name prepended where the file path goes; a server started outside a login shell had no `uv` or
  `pymobiledevice3` on PATH (dev.py now looks in `~/.local/bin` and uv's tool folder; the job env adds `~/.local/bin`).
  Seen in the log afterwards: health 100 -> 90 that stayed for two watch lines; the owner was tapping switches on
  the phone during the capture (keepStats off at that moment), so nothing to chase. Clicking the "open the app first" label with a
  tap near the Log button did not toggle it; setting the checkbox directly did.
- Not tried from the page yet: dump, run, update, New mod.
- The built-in browser launches the server from `.claude/launch.json` (local, not committed: it holds the user's uv
  path); the configured port 8765 was busy (an older `am ui`), so the preview ran on 8766.
- The Sonnet worker could not write the page (context thrash, as before in this repo); the page is hand-written.

## Open
- Phone-side use (`--host 0.0.0.0`): no login; decide whether a token is wanted before using it off the PC.
- Nice to have: live log tail as a dedicated view, edit tweaks.json in place, drag to reorder the menu.

## 2026-11-06: mod rebuilds on newer bases, plain app version
- New mod.json key `keep_app_version: true` (scripts/local_ipa.sh, validated in scripts/validate_mods.py, personal
  mods only): the built .ipa keeps the game's own CFBundleShortVersionString instead of `<app>-mod<mod version>`.
  The build file name keeps the -mod tag (dev.py's newest-build lookup relies on it). Set on bleach-plus,
  mirrors-high-plus and last-day-on-earth-survival-plus. PR #97, merged (checks green).
- Mirrors High+ 1.0.2 on base 1.0.3 (store-current): app reports 1.0.3, installed. The 1.0.1-mod1.0.1 build files
  were deleted from builds/ by mistake (misread request); superseded, nothing current lost.
- Bleach+ 1.0.3 on base 18.1.12 (store-current): app reports 18.1.12, installed. Pin `accessoryATK` fixed for
  18.1.12: the game inlined `BaseCharacterModel.get_AccessoryAtk()` (RVA -1), so the pin is `when: always`
  (no read before the write). dev.py's pin check is what caught it.
- Last Day On Earth+ 1.0.4 on base 1.53.0 (store-current): app reports 1.53.0, installed. First fetch failed: two
  fetches ran in parallel and the decrypt site allows one at a time, so it fell to the Telegram bot and got nothing in
  30 min; the retry alone worked. dev.py's pin check then rejected 11 pins. 9 were Auto-build artifacts that never
  worked: 8 routed through `InputController._inputSystemSubscription` (an IDisposable subscription handle) and
  narrowed it to game models, valid only while BaseCoreController implemented IDisposable (1.53.0 dropped it);
  `noDeathAnimationDelay` narrowed a UI player-bar IController to a world-entity controller. All 9 removed with
  `dev.py remove` (snapshots taken): trophyRoadPremiumUnlocked, trophyRoadPremiumRewardsClaimed,
  unlockTrophyRoadSections, superVIPStatus, vipStatus, dogEnergyBoost, encounterStageAlwaysComplete,
  moreConfigLoadAttempts, noDeathAnimationDelay. The 2 kitchen pins (instantCooking, instantKitchenCooking) were real:
  KitchenStructureController lost its `_kitchenWorkbench` field; re-routed via `_workbench` (IWorkbench) narrowed to
  KitchenWorkbench (fields `_cookingEndTs` 0xE8, `_cookingTime` 0xF0 unchanged). 196 tweaks remain.
- Done the same day (PR #99): dump_scan's path finder and validator no longer narrow through engine or platform
  types (`_library_type`: the class key's assembly against `LIBRARY`), which is how the 9 bogus paths were generated.
  Game interfaces (IEntityBase, IWorkbench) still narrow; `tests/test_dump_scan.py` covers both.
- Tests: `python -m unittest discover -s tests` on Windows died mid-run with no summary (a test killed the process
  tree); the WSL run (CI's command) passed: 434 tests OK, 4 skipped. Use WSL for the suite.
- Lesson: run `dev.py fetch` one at a time. Piping dev.py through `tail` hides its exit code; capture to a file.
