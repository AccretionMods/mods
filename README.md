# Accretion Mods

Ready-to-install modified apps, built locally and listed in [Accretion](https://getaccretion.app).
Each app is a finished .ipa with the mod already inside. Install it from the store, or from the
Accretion app, while signed in, like any other app.

## Source

Downloads need an Accretion account: they go through the store's download server, so the plain
`mods.json` link in this repo no longer installs anything on its own.

For AltStore / SideStore / Feather, use your personal source link from the store:
Edit profile â†’ "Your download links" â†’ Accretion Mods.

## Rules

- **Feature scope:** all tweak and cheat topics are available, including IAP, full-game unlocks,
  subscriptions, premium currency, paid tiers, ads, accounts, and online or multiplayer features.
- Scan topic labels are informational. Technical compatibility, signing requirements, private-file
  handling, authentication and publication approval still apply.

## Layout

```
menu/                 shared floating mod menu (AMMenu), compiled into every mod
runtime/              shared IL2CPP runtime (AMIl2cpp), compiled into mods that have a hooks.json
  probe/              runtime probe .dylib: checks the runtime in any Unity game on a phone
mods/<name>/
  Makefile, control   Theos tweak
  Tweak.x             the mod itself
  hooks.json          Unity games only: the C# methods the mod hooks (see Cheats for Unity games)
  mod.json            listing details, target app, base .ipa (link or private + sha256), published bundle ID
scripts/
  new_mod.sh          start a new mod from the template (reads the base .ipa, records its sha256)
  base.py             keep a base .ipa or tweak .deb in private storage, found by its sha256
  intake.py           upload everything in the intake folder privately; --unused lists stored files no mod uses
  find_cheats.py      find cheat candidates in a Unity game's dump.cs; writes the mod's hooks.json
  check_hooks.py      check a mod's hooks.json against a new version's dump.cs (names, return types, arg counts)
  embed_hooks.py      turn a mod's hooks.json into the C table the IL2CPP runtime reads (build.sh runs it)
  il2cpp_dump.py      read Il2CppDumper's dump.cs (used by find_cheats.py and check_hooks.py)
  build.sh            build one mod's .dylib
  build_probe.sh      build the runtime probe .dylib (the Mods: runtime probe workflow runs it)
  local_ipa.sh        build a mod's unsigned .ipa (Linux, WSL or macOS; the Mods: build a mod workflow runs it)
  check_mod_ipa.py    check a built .ipa before publishing (an app with the mod's bundle ID)
  check_ipa.sh        check an .ipa on macOS: Payload/*.app present, not FairPlay-encrypted
  publish.py          upload a build privately and record it
  stage.sh            publish a build from your machine without the upload key
  update_source.py    regenerate mods.json
  validate_mods.py    check every mod folder and mod.json
  watch_bases.py      find base apps with a newer release (the Mods: watch base apps workflow)
  sign.sh             sign an .ipa with your own certificate, for your own devices
  install_zsign.sh    install zsign (the open-source signer sign.sh uses)
  install_il2cppdumper.sh  build the pinned Il2CppDumper that reads Unity 6 metadata (v39)
  validate_tweaks.py  check tweaks.json
tests/                version rules
mods.json             generated AltStore source (do not edit)
releases.json         generated list of published builds (do not edit)
```

## Versions

A build is published as `<base app version>-mod<mod version>`, like `4.0.0-mod1.1.0`, and the .ipa's
own version is set to the same string. So a new mod version for the same app version is still an
update in the store and in Accretion, and installed copies match what's published. Versions compare
number by number (`4.0.0-mod1.10.0` is newer than `4.0.0-mod1.9.0`; `4.1.0-mod1.0.0` is newer than
both). Bump `version` in `mod.json` for every change you publish.

## Add a mod

1. Start it from the template:

   ```sh
   scripts/new_mod.sh my-app-plus https://github.com/<owner>/<app>/releases/download/<tag>/<app>.ipa games
   ```

   It downloads the base .ipa once, reads its bundle ID, name and version, records its sha256, and
   writes `mods/my-app-plus/` with a Makefile, control, filter plist, a `Tweak.x` with one example
   switch, and `mod.json`.
2. Edit `mod.json`: `description`, `subtitle`, `icon_url`, `base.repo` (the app's GitHub repository,
   so the weekly watch can track it) and `base.license`. The rest is filled in:
   - `bundle_id` is a new ID under `io.github.accretionmods.`, so the mod installs next to the
     original app (the store keeps one app per bundle ID).
   - `base.ipa_url` and `base.sha256`: the build refuses any other file.
   - `remove_extensions`: `encrypted` (default) or `all`.
3. Write `Tweak.x`. Register switches and sliders with `AMMenu.shared` in `%ctor`, then call
   `[AMMenu.shared install]` (the menu is the shared template: see "The mod menu" below). Read values anywhere with `[AMMenu boolForKey:]` / `[AMMenu floatForKey:]`.
4. Push. CI checks every `mod.json` (`scripts/validate_mods.py`), the tweak library, the scripts and
   the version rules.

### The mod menu (official template)

`menu/AMMenu` is the one mod menu every mod uses, as approved by the owner on 2026-10-22. Mods add items to it; they
never copy, restyle or replace it. Change the look only in `menu/AMMenu.m`, so every mod changes together.

- **Title and footer:** the game's own name (as under its icon) with the Accretion mark; a plain Done and All off;
  a small "More at getaccretion.app" link under the note.
- **Categories:** one outlined box per category, the label above it with "N on" and a plain Off. Every
  category starts expanded; tapping a label folds it. The box is see-through (60% fill). A violet-into-cyan
  light with a bright glint runs around each open box's edge every 6 s. Faint hairlines (7%) sit between rows.
- **Switch rows:** 13 pt text, about 30 pt tall. A small outlined line toggle: grey off, cyan on, red when on and
  not reaching the game. Tap anywhere on the row. No checkmarks anywhere.
- **Slider rows:** the value (cyan while on) and the same line toggle on the title line, the slider below. Tapping
  the row turns it off (the game's own value comes back, the slider dims and keeps its value) and on again (that
  value comes back); dragging turns it on. Within 3% of the range from its default it snaps to the default (off)
  with a tick.
- **The game keeps running** while the menu is open. A mod pauses it only with `"pause_menu": true` in tweaks.json.
- **Status:** the dot left of the title while a switch is on: steady green when it works, red pulsing when not,
  with the reason in small red text under the title ("Start a fight to see it work"). Report it with
  `AMMenuStatus`.
- **Confirmation:** every tap glows its row: a switch cyan on and line colour off; a slider when released; a list
  pick cyan with "âœ“ Addedâ€¦", or red with the reason. All come with a haptic. A list pick reports what the game did
  with `AMMenuPickResult(ok, text)` inside `onPick`.
- **Lists (`addList`):** the menu's look with search on long lists. Give each entry a `group`, and list the best
  first: by kind (Weapons, Ammo, Wearables, Medical, Food & drink, Water, Materials, Misc) and by quality tier
  (Legendary, Epic, Rare, Normal, Common) when the game has tiers. Never one flat list.
- **Display category (last):** Pulsing dot and Toggles, both on by default, kept per game. They are not cheats:
  All off and the counts ignore them.
- **Background:** only the two slow drifting lights and the moving line under the title.
- **Cloud saves:** a "Log in Â· back up your saves" capsule under the title (moving edge) opens Accretion's sign-in in
  iOS's sign-in sheet with a one-time code; once linked (the sheet closes by itself) a Cloud saves category above
  Display has Back up now, Restore a backup (newest first, any device), Back up when the menu opens (only after a
  first manual backup or restore) and Unlink. Back up now shows "Last saved: 5 min ago" ("Savingâ€¦" while a backup
  runs, including the one when the menu opens). A backup is a zip of the game's saved settings, Documents and
  Application Support (50 MB at most, the newest 5 per game, unchanged saves not uploaded again); a restore keeps
  the current save first and is put in place at the next launch. The store's `saves` function and #/link page
  (getaccretion.github.io) are the other side.

### Hidden names in builds

A built `.dylib` exports no symbols (`-fvisibility=hidden`) and carries no game class, method or field name as
plain text: `scripts/embed_hooks.py` and `scripts/gen_tweaks.py` write them encoded, and a hand-written `Tweak.x`
marks each one `AMS("Name")`, which `scripts/build.sh` encodes in a compiled copy (`Tweak.hidden.x`, git-ignored).
They are decoded in memory when first used (`runtime/am_hide.h`). The source stays readable here; this only keeps
the names out of a quick `strings` look at the binary.

### Private bases and ready-made tweaks

When the base .ipa can't be at a public link, pass a local file instead of the link:

```sh
scripts/new_mod.sh my-app-plus ~/intake/MyApp.ipa games
```

It uploads the file to Accretion's private storage (`scripts/base.py put`, needs the upload key) and
writes `"base": {"private": true, "sha256": "..."}` with no `ipa_url`. Builds fetch it by its sha256.

To inject a ready-made tweak `.deb` instead of building one, upload it the same way and add its
checksum to `mod.json`; the mod then needs no Makefile, control or Tweak.x:

```sh
python3 scripts/base.py put ~/intake/tweak.deb      # prints the sha256
# mod.json: "deb": {"sha256": "<that sha256>"}
```

`scripts/local_ipa.sh` and **Mods: build a mod** fetch both and check their checksums. Locally,
`DEB=<file>` uses a local copy of the .deb instead of fetching it.

### Cheats for Unity games

A Unity game ships its code as IL2CPP, so a cheat hooks C# methods by name. Run
[Il2CppDumper](https://github.com/Perfare/Il2CppDumper) on the game's `UnityFramework` binary and
`Data/Managed/Metadata/global-metadata.dat` (both inside `Payload/*.app`) to get `dump.cs`. Perfare's
6.7.46 reads metadata up to v31 (Unity 2022); Unity 6 games (metadata v35, v38, v39; the version is
the little-endian int at byte 4 of global-metadata.dat) need the pinned fork that
`scripts/install_il2cppdumper.sh` builds (git and the .NET SDK required):

```sh
scripts/install_il2cppdumper.sh                             # ~/.local/il2cppdumper
dotnet ~/.local/il2cppdumper/Il2CppDumper.dll UnityFramework global-metadata.dat dump/
python3 scripts/find_cheats.py dump/dump.cs                 # candidates by category, with scores
python3 scripts/find_cheats.py dump/dump.cs --mod my-game --pick # choose; writes mods/my-game/hooks.json
```

It scores methods by name (god mode, damage, cooldown, speed, ammo) and suggests what the hook does:
skip the call, or return a constant. Candidates are leads to read in the dump, not working cheats.
Purchase and online topic matches are informational and do not exclude candidates.

`hooks.json` records each hook's namespace, class, method, argument count and return type, and is what
the inbox auto-pipeline checks against every new version of the game. When a new version arrives, dump
it and run `scripts/check_hooks.py my-game dump.cs`: each hook is ok, changed (the return type or
argument count differs, so the `Tweak.x` hook needs a look) or missing, with same-named methods in other
classes as leads. CI validates every `hooks.json` with `scripts/validate_mods.py`.

#### Hooking at runtime

`runtime/` loads `hooks.json` in the running game. `scripts/build.sh` converts it to a C table
(`scripts/embed_hooks.py`) and compiles it into the tweak, so nothing extra ships with the .ipa. In
`Tweak.x`, register a replacement per hook, then start the runtime (usage is in `runtime/AMIl2cpp.h`):

```objc
#import "runtime/AMIl2cpp.h"
%ctor {
    AMIl2cppRegister("PlayerHealth.TakeDamage", (void *)rep_TakeDamage, (void **)&orig_TakeDamage);
    AMIl2cppStart();
}
```

and list the runtime in the mod's Makefile:
`<Tweak>_FILES = Tweak.x menu/AMMenu.m runtime/am_il2cpp.c runtime/AMIl2cpp.m runtime/am_hooks_data.c`.

The runtime waits for Unity to start and finds each method through the game's own `il2cpp_*` exports (class
by namespace and name, method by name and argument count). A method that is gone in this version, or has
no replacement, is skipped and logged to the device log (`[AccretionMods] skipped Class.method: ...`);
the other hooks and the app carry on. This is what `scripts/intake_auto.py` relies on when it only checks
names. A nested class is named `Outer.Inner`, as in dump.cs (generic classes can't be hooked). `AMIl2cppSetReportHandler` hands the mod the same lines, for example to show them in its menu.

How it hooks depends on the phone:

- **JIT on** (SideStore or AltStore with StikDebug): code hooks with Substrate. Every call is caught.
- **No JIT** (most signing services): iOS kills an app that changes its own code, so the runtime swaps
  the pointers IL2CPP keeps in memory instead (the method's MethodInfo and the vtables of its class and
  subclasses). That catches virtual and override methods, interface methods, Unity callbacks (`Update`,
  `OnTriggerEnter`, ...), delegates and reflection, but not a direct call from other game code.

`AMIl2cppHasJIT()` says which mode is active, so a mod can label its JIT-only switches. `find_cheats.py` marks a candidate "needs JIT" when dump.cs gives it no vtable `Slot:` and it isn't a
Unity callback; hooks.json records it as `needs_jit`. Prefer hooks that don't need JIT.

The runtime probe (`runtime/probe/`) checks all of this on a real phone without writing a mod. The
**Mods: runtime probe** workflow builds `AMIl2cppProbe.dylib` for arm64 on every runtime change: download
it from the run's artifacts, add it as a tweak when signing any Unity game in Accretion, install and open
the game. After about 20 s an alert shows the mode, each probe hook and how often it was called.

#### Mod Studio: the same loop as a page (`studio/`)

`scripts\am.ps1 ui` (or `python3 studio/server.py`) starts a small server on the PC and opens
http://localhost:8765/: the mods on the left; tabs for the switches (hooks and pins as chips, phone
results, remove), Find (search the dump, fields of a class, Hook / Pin / Call buttons that open a
prefilled form), Suggest (what worked in earlier games), Build & Phone (dump, build, sign + install,
run, log, update to a newer base, with the job output streamed into the page), Files and Games. Games
shows App Store games as cards (top free, paid and grossing, new free and paid, or "like the games we
mod": ranked by the themes and genres they share with our mods' games, from `scripts/games.py` over
Apple's public RSS, Lookup and Search APIs, cached half an hour), marks the ones we already mod and the
ones with a decrypted copy waiting, labels online or multiplayer games (shown by default), and has
Get .ipa, New mod (or Open mod) and Like this on each card. Enter in its box searches the
whole App Store (a name or a bundle ID); `am games` gives the same lists in a console (below). Console
runs any `dev.py` command typed as `am ...` (dump, `find` with any regex, fields, add, pin, build, sign, run,
log, update, games, fetchâ€¦), with buttons that fill in the selected mod, â†‘/â†“ history, an answer box under a
job for its questions (pick's number, the Apple ID 2FA code), and a prompt builder: pick a task (add a
feature, narrow down the best approach, explore the dump, fix a switch or crash, update to the newest game,
start a mod for another game, check before publishing), type one line, and it writes a prompt for Claude
Code with the mod's facts (versions, dump, switches and their phone results, the last log errors Studio saw)
in the goal / target / done when / limits shape, says which detail is missing (with a button that gets it),
and copies it, or **Run with Claude Code** runs it headless on this PC (`claude -p`) and streams Claude's text and
tool calls into Jobs. Read-only tasks get reading tools and the read-only `am` commands (status, find,
fields, suggest, check, games) and no edits; other tasks ask first, then may edit files and run any `am`
command, but no other shell command (no push, no publish). The tasks are in `studio/prompts.json`, editable. The console answers only this PC, even with `--host 0.0.0.0`. The Switches tab has
Build .ipa, Sign + install and Build, sign, install, open, log; job output shows under every tab. Quick commands
run through `dev.py`'s own functions; long ones run `dev.py` as a subprocess. The first Apple ID
sign-in (2FA) must be done once from a console, then the page's phone buttons work. The server binds
127.0.0.1; `--host 0.0.0.0` shows the page on the Wi-Fi with no login, for a trusted network only.
Tests: `tests/test_studio.py`, `tests/test_games.py`.

#### Dev loop for personal mods (`scripts/dev.py`)

`scripts/dev.py` runs the whole loop for a personal mod of a Unity game from one place: dump, search, add a
switch, build, sign, read the device log. It needs a decrypted copy of the game (your own, or one from `am fetch` below), WSL with Theos
for the build, and on Windows the .NET runtime for the dumper and the phone on USB for signing and logs.
Run it as `uv run --no-project --python 3.12 scripts/dev.py ...` (Windows) or `python3 scripts/dev.py ...`
(WSL). Base `.ipa` files are found by bundle ID in `C:\AccretionMods\intake` and `~/Downloads`; dumps go
to `C:\AccretionMods\work`, builds to `C:\AccretionMods\builds` (`AM_ROOT` moves the folder).

```sh
dev.py new my-game-plus ~/Downloads/MyGame-1.2-Decrypted.ipa      # personal mod folder, then the dump
dev.py find my-game-plus "Damage|Health"                             # methods, with signature and whether they need JIT
dev.py fields my-game-plus Enemy                                      # a class's fields and offsets
dev.py add my-game-plus Enemy.DamagePlayer --as noDamage --title "Player takes no damage"
dev.py add my-game-plus Enemy.UseHealthPotion --to noDamage           # a second hook on the same switch
dev.py add my-game-plus Enemy.get_AttackSuccessRate --title "Enemies miss" --value 0
dev.py add my-game-plus ItemStack.GetScaledDamage --title "Weapon damage Ã—" --action multiply --slider 1 20 1
dev.py add my-game-plus StatHandler.set_Hunger --title "Hunger never drops" --action custom
dev.py build my-game-plus --ipa [--bump]                             # .dylib and unsigned .ipa in builds/
dev.py sign builds/my-game-plus_1.2-mod1.0.0_accretion.ipa --install  # Apple ID signing (plumesign), onto the phone
dev.py log my-game-plus --seconds 90                                  # the mod's and the runtime's log lines
```

`add` looks the method up in the dump (overloads need `--args N`), records it in `hooks.json` with its
signature and whether it needs JIT, and adds a menu item to `tweaks.json`. `scripts/gen_tweaks.py` turns
`tweaks.json` into `AMTweaks.gen.m` (`build.sh` runs it): the value of each item (`am_<key>`), a hook
function per method, and `AMTweaksRegister()`, which `Tweak.x` calls in `%ctor`. Actions: `skip` (a void
method is not called), `return` (a bool or number instead), `multiply` (the result times the slider), `arg`
(an argument replaced), and `custom` (the hook is registered; `Tweak.x` defines `hook_<Class_method>()`
and may call `orig_<Class_method>()`). Items whose hooks all need JIT get the " (JIT)" label without it
(`"jit_label": false` turns that off once a no-JIT path exists). `dev.py tweak` adds a switch or slider
with no hook, for hand-written code; `dev.py remove` drops an item; `dev.py check` compares `hooks.json`
with the current dump; `dev.py status` shows base, dump, items and the last build.

Shorter from PowerShell: `scripts\am.ps1 <command> ...` (the header of that file shows the one-line profile
function that makes it plain `am`). With no command it prints the help. Three more commands keep the loop short:

- `am pick my-game-plus Damage` lists the matching methods numbered; type a number and answer two or three
  questions (title, action, value or slider) and the switch is added. Methods you hooked before are marked
  with what was done then.
- `am update my-game-plus [new.ipa]` is for a newer version of the game: it takes the newest copy of the
  app in intake/ or Downloads (or the file you name), dumps it, reports every hook as ok, changed or missing,
  refreshes the signatures in `hooks.json` and the base checksum and tested version in `mod.json`, and
  regenerates the code. `build` and `status` say when such a copy is waiting. Missing hooks stay listed and
  are skipped at run time; replace them with `find` and `add`.
- `am suggest my-game-plus` ranks this game's methods against what you hooked in earlier games
  (`C:\AccretionMods\dev\patterns.json`, filled by every `add` and by `am learn <mod>` for an existing
  mod's `hooks.json`). `am mark my-game-plus noDamage --works` (or `--fails`) records the phone result on
  the switch and on the pattern, so what worked ranks higher next time and what failed drops out. Methods
  are suggested regardless of topic; Unity callbacks and constructors remain excluded from pattern matching.

#### A new game (`am start`, `am report`)

`am report <mod | file.ipa>` says in a second whether a game is worth modding with these tools: decrypted
or not, the engine (Unity IL2CPP needed), and the multiplayer, online, ads and purchase code it ships; with
a dump, the scan's candidate counts. `am start <bundle-id | file.ipa>` does a new game in one go: a
decrypted copy (am fetch) if none is here, the report (it stops when the game can't be modded), the mod
folder and its dump (am new), then the best no-JIT candidates. In Studio: Start a mod and Report on a
Games card; Start ends on the new mod's Scan tab.

Studio reuses its registered local instance and shows **Load update** when the source changes. Updates
wait for active jobs and configuration changes to finish. Scan filters distinguish Pin, Hook, No JIT,
investigation and test status; related fields/getters/setters expand into exact signature variants.
Cached scans page through all matching groups. **Review no-JIT alternatives** identifies compatible
property pins and explicit timed calls where the runtime supports them; these require testing and do
not replace direct interception automatically. Results from another game version appear as stale.

Files includes configuration snapshots, diff preview and restore with a recovery snapshot. Job history
survives restarts with redacted output; interrupted jobs remain interrupted. Conflicting mod mutations
and device operations queue. Runtime history/snapshots stay under AM_ROOT/studio outside the repo.

Scan's **In-app purchases** area keeps Store/IAP mod targets: pin-compatible values, mutable numeric or
boolean fields, and concrete methods supported by the hook tool. Generated classes and delegate caches,
type-only rows, constructors, constants and metadata are hidden. **Add** creates a pin; **Hook** uses a
method with its JIT requirement shown; **Inspect** shows signatures and fields needing an object path.
**Show next 300** loads more matches. Names suggest candidates; phone tests establish their effect.

While the game runs, the mod's menu logs each switch's state (`[AccretionMods] status <key> ok|off|waiting:
...`), on change and every 20 s. Studio's Switches tab shows them from its log and run jobs ("on the phone:
working"), and Watch the phone (5 min) keeps them current.

#### App Store games from a console (`am games`)

The Games tab's lists, for a console or a script (`--json` prints the rows the page gets from
`/api/games`):

```sh
am games                          # like the games we mod, including online / multiplayer
am games top-free                 # also top-paid, top-grossing, new-free, new-paid; --region jp, --limit 20
am games search 9 kings           # the App Store's own search; a bundle ID looks that app up; --games: games only
am games like com.playdigious.9kings    # games like that one
am games info com.playdigious.9kings    # version, minimum iOS, size, age rating, themes, description,
                                        # our mods of it and a decrypted copy waiting, if any
```

Each row says when we already mod the game (`our mod <folder>`), when a decrypted copy waits in `intake/`
or Downloads (`ipa ready`), and when it looks online or multiplayer (`online`).

#### Getting a decrypted copy (`am fetch`)

Nothing in this repo decrypts. When you have no decrypted copy of a game, `am fetch` downloads one that
an unfaird site ([macOSAppstoreDecrypter](https://github.com/34306/macOSAppstoreDecrypter)) already
decrypted, into `intake/`, where `new`, `dump` and `update` find it. The default site,
`https://decrypt.34306.lol`, is another party's M1 Mac: free, can be paused or gone, and not ours.
`AM_DECRYPT_URL` points at another copy of the same software.

```sh
am fetch com.JamieParish.TheWanderer [--region us|jp|cn|vn] [--platform iphone]   # or an app name
am fetch --mod wanderer-dev        # the app a mod targets, for example a newer version before `am update`
am fetch --account                 # site up or paused, signed in or not, decrypts left today
am fetch --login | --logout        # site account; the password is sent to the site only, never saved
am fetch --save-login              # same, and keeps the login in Windows Credential Manager
                                   # (AccretionMods/decrypt/<host>); fetch signs in with it when a decrypt
                                   # needs it or the session ran out. --forget-login removes it.
am fetch com.JamieParish.TheWanderer --source decrypt.day      # or --source armconverter (store/<region>)
```

`--source decrypt.day` and `--source armconverter` have no API here: decrypt.day sits behind Cloudflare's bot
check and armconverter's store needs your sign-in, so `fetch` opens the site in your browser (sign in there
once; the browser keeps it), you download the app, and `fetch` takes it from Downloads as below. Their
logins are not saved by this tool and no script signs in to them.

decrypt.34306.lol asks scripts for a captcha, and nothing here tries to pass one: `fetch` then opens the site
in your browser, you search the bundle ID there and press Decrypt or Download, and `fetch` waits (up to 30
min) for that app's `.ipa` in Downloads, checks it and moves it into `intake/`. `am fetch --import file.ipa`
does the check and move for a copy you downloaded yourself; `--no-browser` fails instead of opening the site.
The direct path (lookup, decrypt, download, all from the script) works with a copy of the site that has no
captcha. Apps the site already has download with no account. A new decrypt needs an account made on the site and
approved by its admin (a few a day); while the site is paused it takes none, and `fetch` falls back to an
older copy the site still has. Every copy is checked (the app asked for, main binary not FairPlay-encrypted;
otherwise it is deleted) and `<file>.source.json` records the site, version and SHA-256. Nothing proves the
site did not change the app, so mods built on a fetched copy are for testing on your own phone. In Mod
Studio: **Get .ipa** in the top bar (search, sign in, Get), and **Get the newest from the decrypt site** on a
mod's Build & phone tab. Tests: `tests/test_ipa_fetch.py` (a fake site, no network).

**Without JIT, hooks on plain methods catch nothing** (the game calls them directly; only virtual methods and
Unity callbacks go through the swapped pointers), so a switch for any phone is a *pin*: every half second the
ticker writes a field or calls a setter on the live objects, the way The Wanderer+ 1.0.7 does it by hand.
`am pin` and `am call` write one into `tweaks.json` and the generator turns it into code on the shared data
helpers (`runtime/AMIl2cppData.h`: objects by static field or `FindObjectsByType`, field addresses by name
from the running game, `il2cpp_runtime_invoke` calls, a main-queue ticker):

```sh
am pin my-game-plus StatHandler.set_Hunger --value 100 --when lower --as keepStats --title "Hunger never drops"
am pin my-game-plus StatHandler.set_Health --value getter:get_HealthMax --when lower --to keepStats
am pin my-game-plus Lock.set_LockPickHealth --object Lock --value peak --when lower --title "Lockpicks never break"
am pin my-game-plus Enemy.EnemyDamage --object "Enemy[]" --value 0 --title "Enemies deal no damage"
am pin my-game-plus PlayerCompanion.Damage --class FightCompanion --multiply --slider 1 20 1 --title "Weapon damage Ã—"
am pin my-game-plus PlayerStats.Rads --object StatHandler.instance --via get_playerStats --value 0 --title "No rads"
am call my-game-plus InventoryData.GiveRandomItem --args -1 --every 10 --title "Give a random item every 10 s"
```

The object is `Class.staticField` (a singleton; the default when the class has an `instance` field), `Class`
(the first live object) or `Class[]` (every live object). Values: a number, `true`/`false`, `peak` (the highest
value seen), `getter:get_X` or `field:Y` read from the same object, or the slider. `--when lower` writes only
when the current value is below the value (or `--threshold`). A top-level `"watch"` list in `tweaks.json`
logs chosen getters or fields every 10 s (`"watch_every"`), which is how a pin is checked on the phone:
`am log` shows `watch: health 100.0 hunger 100 thirst 55 ...`, then `pin keepStats applied`, then the values
holding. A switch with pins loses the " (JIT)" label; its JIT hooks still install on phones that have JIT.
The `Tweak.x` that `am new` writes calls `AMTweaksTick()` from the ticker; `runtime/SideloadCompat.m`
(keychain, iCloud, Game Center and age-range answers for re-signed copies) is in its Makefile too.
Nothing in this loop uploads or publishes: personal mods have `"personal": true` and stay out of the catalogue.

The reference is The Wanderer+ 1.0.7 rebuilt this way, kept outside the repo at
`C:\AccretionMods\dev\wanderer-dev-example\` (18 menu items, 17 JIT hooks, 19 pins, a watch line; the item
spawner list stays hand-written in its `Tweak.x`): phone-checked 2026-10-21, hunger and thirst held at 100
through rests with "Health, hunger, thirst, morale never drop" on.

### Inbox auto-pipeline

Drop a new base .ipa in the intake folder (`C:/AccretionMods/intake`, synced from the phone) and
`scripts/intake_auto.py --apply` does the rest: finds the mod with that `target_bundle_id`, checks that
every method in the mod's `hooks.json` is still named in the game's IL2CPP metadata, uploads the .ipa
privately, updates `base.sha256` and `tested_app_version`, commits and pushes only `mod.json`, and starts
**Mods: build a mod**. A new version whose hooks are gone is moved to `C:/AccretionMods/held` with a report
instead. Without `--apply` it only reports. Log: `C:/AccretionMods/auto.log`. Schedule it (every 15
minutes) by running `scripts/register_intake_auto.ps1` once.

## Build and publish

**In the cloud (recommended, works from a phone):** Actions â†’ **Mods: build a mod** â†’ Run workflow â†’ pick the mod from the list (a new public mod is added to the list in build.yml and publish.yml; CI fails until it is).
It runs on GitHub's macOS runner with Theos and cyan pinned to exact commits, downloads
the base .ipa from `base.ipa_url` (or private storage) and refuses it unless it matches `base.sha256`, builds and injects
the tweak, sets the version, checks the result, uploads it straight to Accretion's private storage
(the repo's `ACCRETION_UPLOAD_KEY` secret) and commits `releases.json` and `mods.json`. The .ipa is
never kept as a workflow artifact or release asset (anyone signed in to GitHub can download those).
Turn off **Publish** to build and check only.

**On your machine:** Linux, WSL or macOS with [Theos](https://theos.dev/docs/installation) in
`~/theos`. Theos can't build from a folder whose path has spaces, so work from a clone outside such
folders (in WSL, for example `~/mods`):

```sh
scripts/local_ipa.sh thewanderer-plus "" ~/out
```

It installs [cyan](https://github.com/asdfzxcvbn/pyzule-rw) into `~/.cyan` the first time (pinned
commit), builds the tweak, downloads and checks the base .ipa, injects the tweak and writes an unsigned
`<mod>_<version>_accretion.ipa`. To test it yourself, sign it first: Sideloadly or AltStore/SideStore
with an Apple ID, or Feather with a developer certificate. Tweaks that hook methods depend on Cydia
Substrate; cyan bundles it in automatically.

Publish a local build without the upload key (any machine with `gh` and write access):

```sh
scripts/stage.sh thewanderer-plus ~/out/thewanderer-plus_10.1-mod1.0.9_accretion.ipa [15.0]
```

This puts the .ipa in a draft release (visible only to people with write access) and starts the
**Mods: publish a mod** workflow, which checks it, uploads it privately, commits the source and deletes the
draft. Or directly, with the upload key on your machine (`.upload-key` or `ACCRETION_UPLOAD_KEY`):

```sh
python3 scripts/publish.py --mod thewanderer-plus --ipa ~/out/thewanderer-plus_10.1-mod1.0.9_accretion.ipa [--min-os 15.0]
```

then commit and push the updated `releases.json` and `mods.json`.

## Keeping mods current

The **Mods: watch base apps** workflow runs every Monday (or on demand). For each mod whose `base.repo` is a
GitHub repository, it compares the app's latest release with `tested_app_version` and opens one issue
per newer version with the update steps: new `base.ipa_url` and `base.sha256`, new
`tested_app_version`, then **Mods: build a mod**. It never builds anything by itself.

## Tweak library

`tweaks.json` is the tweak catalog the Accretion app reads. When someone signs an app listed in a
tweak's `targets`, the app suggests it under Advanced â†’ Modify â†’ Tweaks, and one tap downloads and
injects it. Files download from each tweak's own project; nothing is re-hosted here.

Each entry needs `id`, `name`, `description`, `targets` (bundle IDs), `version`, `debURL` (https
`.deb` or `.dylib`), `license`, and optionally `requires` (extra files it needs), `projectURL` and
`author`. Check changes with:

```
python3 scripts/validate_tweaks.py --check-links
```

## Local detection profiles (no JIT)

Compatible builds using the shared `runtime/SideloadCompat.m` receive a minimal filtering
default, bound to `mod.json`'s `tested_app_version`: 22 exact artifact paths, native `stat`
and Foundation `fileExistsAtPath:`, scoped to the app executable and UnityFramework. Shared
defaults disable diagnostic counters, report files and phase polling. A missing
or invalid tested version leaves defaults unavailable. Other engine frameworks require an
explicit image selection; importing this policy does not establish runtime coverage.

An optional `mods/<name>/detection.json` overrides the default, including `mode: "off"` to
disable it or `observe` to preserve results while collecting counters. The game version must
match the tested version; changes require a rebuild and restart. Existing installed copies
are unchanged. The builder redirects the recognized, uncustomized legacy compatibility
copy to the shared source through a Make override, without rewriting the game's files.
The normalized source hash must match; custom copies are preserved and require integration.
Ready-made external tweaks that do not compile this runtime cannot receive these hooks
automatically. Existing per-game profiles retain their explicit settings.

```json
{
  "schema_version": 1,
  "game_version": "1.2.0",
  "mode": "observe",
  "diagnostics": true,
  "images": ["@executable", "Frameworks/UnityFramework.framework/UnityFramework"],
  "adapters": ["filesystem", "foundation", "urls", "environment", "images"],
  "paths": ["/private/jailbreak.txt"],
  "path_prefixes": [],
  "url_schemes": ["cydia"],
  "environment_names": ["DYLD_INSERT_LIBRARIES"],
  "image_basenames": []
}
```

For a minimal profile, select only the observed caller image and required adapters, then
set `"apis": ["stat", "foundation_exists"]` to install only native `stat` and Foundation
`fileExistsAtPath:` interception. Other supported API names are `access`, `lstat`, `open`,
`fopen`, `getenv`, `foundation_attributes` and `urls`. Each API requires its adapter;
unknown or duplicate entries are rejected. An explicit empty list installs no detection
entry points. Omit `apis` only to retain the older adapter-wide selection. Unselected entry
points are not resolved or installed by this detection layer and report as disabled.

Use exact paths for known checks. Requests outside the configured caller images or path
rules reach the original functions; these rules are not a filesystem-wide deny policy.
Selecting a path in filter mode returns the existing missing-file result for that rule.
It does not prove the caller would have detected a present artifact or accepted a modified game.

Supported filtering covers selected imported `access`, `stat`, `lstat`, `open`, `fopen` and
`getenv` calls, plus selected Foundation path queries and `canOpenURL:` calls whose caller
belongs to a configured game image. The adapters preserve ordinary calls, ABI and error
conventions. Existing foreign hooks, unresolved imports, unsupported layouts and failed writes
are reported; they do not count as working coverage. Reports distinguish architecture,
chained fixups, authenticated segments, missing classic tables, and malformed layout failures
with fixed counters, without recording image paths. Direct syscalls and other lookup paths
remain outside this coverage. Profile rules reject broad roots and ambiguous paths.
Already resolved arm64 images with chained pointer formats 2/6 are supported when they
retain classic indirect import metadata. Encoded chains are never traversed; authenticated
pointers, unknown formats and images without retained metadata remain unsupported.

Modes are `off`, `observe`, and `filter`. Observation still changes import/method pointers;
it is not an invisible operation. Explicit profiles retain diagnostics by default; set
`"diagnostics": false` to disable counters, report writes and phase polling. A diagnostic
profile writes one bounded counter/status report
to the app's `Documents/am_detection.json` after 30 seconds, then refreshes it every 15 seconds
while the app runs. It contains a profile hash and elapsed time, with no raw query arguments,
environment values, credentials, device identifiers or network data. It stays local; check its elapsed time and
profile identity before treating an old file as a new observation.
Image/class concealment, kernel helpers, executable patches, attestation and server acceptance
are unsupported. A local filter test does not prove that a game accepts modified gameplay.

Static preflight can inspect a private IPA without executing it. Unsupported layouts and
inspection limits remain explicit; static support does not prove a runtime pointer write.
Use an explicit observation profile when collecting diagnostics:

```sh
am detection preflight /private/game.ipa --output /private/preflight.json --profile /private/detection.json
am detection report my-mod
am detection phase my-mod menu
```

Generated observation presets never overwrite an existing file. Report/phase commands also
accept a private trial folder containing mod.json. Phase requests are bound to the current
launch and require acknowledgement. They classify subsequent calls and never drive gameplay.
Schema 3 reports include bounded attribution and matched-call outcomes with explicit drop
counts. A negative result is different from a filtered call whose original was not invoked.
