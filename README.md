# Accretion Mods

Ready-to-install modified apps, built locally and listed in [Accretion](https://getaccretion.app).
Each app is a finished .ipa with the mod already inside. Install it from the store, or from the
Accretion app, while signed in, like any other app.

## Source

Downloads need an Accretion account: they go through the store's download server, so the plain
`mods.json` link in this repo no longer installs anything on its own.

For AltStore / SideStore / Feather, use your personal source link from the store:
Edit profile → "Your download links" → Accretion Mods.

## Rules

- **Base apps:** free or open-source apps only. Never a paid app.
- **Allowed:** tweaks (features, settings, UI, themes, ad removal) and cheats for single-player or offline play.
- **Not allowed:** anything that unlocks what the developer sells (subscriptions, in-app purchases, premium currency, paid tiers), and cheats that affect multiplayer or online play.
- Prefer open-source apps built from their own unsigned releases. A closed-source free app ships its developer's code, and they can file a DMCA takedown.

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
  find_cheats.py      find single-player cheat candidates in a Unity game's dump.cs; writes the mod's hooks.json
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
   `[AMMenu.shared install]`. Read values anywhere with `[AMMenu boolForKey:]` / `[AMMenu floatForKey:]`.
4. Push. CI checks every `mod.json` (`scripts/validate_mods.py`), the tweak library, the scripts and
   the version rules.

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
skip the call, or return a constant. Candidates are leads to read in the dump, not working cheats. The
rules above decide what is never offered: anything named for purchases, subscriptions, premium currency
or paid tiers, and anything named for networking, multiplayer, servers or leaderboards. If the game's
combat lives in such classes, it is an online game and not one for this repo.

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

### Inbox auto-pipeline

Drop a new base .ipa in the intake folder (`C:/AccretionMods/intake`, synced from the phone) and
`scripts/intake_auto.py --apply` does the rest: finds the mod with that `target_bundle_id`, checks that
every method in the mod's `hooks.json` is still named in the game's IL2CPP metadata, uploads the .ipa
privately, updates `base.sha256` and `tested_app_version`, commits and pushes only `mod.json`, and starts
**Mods: build a mod**. A new version whose hooks are gone is moved to `C:/AccretionMods/held` with a report
instead. Without `--apply` it only reports. Log: `C:/AccretionMods/auto.log`. Schedule it (every 15
minutes) by running `scripts/register_intake_auto.ps1` once.

## Build and publish

**In the cloud (recommended, works from a phone):** Actions → **Mods: build a mod** → Run workflow → pick the mod from the list (a new public mod is added to the list in build.yml and publish.yml; CI fails until it is).
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
scripts/local_ipa.sh swiftradio-plus "" ~/out
```

It installs [cyan](https://github.com/asdfzxcvbn/pyzule-rw) into `~/.cyan` the first time (pinned
commit), builds the tweak, downloads and checks the base .ipa, injects the tweak and writes an unsigned
`<mod>_<version>_accretion.ipa`. To test it yourself, sign it first: Sideloadly or AltStore/SideStore
with an Apple ID, or Feather with a developer certificate. Tweaks that hook methods depend on Cydia
Substrate; cyan bundles it in automatically.

Publish a local build without the upload key (any machine with `gh` and write access):

```sh
scripts/stage.sh swiftradio-plus ~/out/swiftradio-plus_4.0.0-mod1.0.0_accretion.ipa [15.0]
```

This puts the .ipa in a draft release (visible only to people with write access) and starts the
**Mods: publish a mod** workflow, which checks it, uploads it privately, commits the source and deletes the
draft. Or directly, with the upload key on your machine (`.upload-key` or `ACCRETION_UPLOAD_KEY`):

```sh
python3 scripts/publish.py --mod swiftradio-plus --ipa ~/out/swiftradio-plus_4.0.0-mod1.0.0_accretion.ipa [--min-os 15.0]
```

then commit and push the updated `releases.json` and `mods.json`.

## Keeping mods current

The **Mods: watch base apps** workflow runs every Monday (or on demand). For each mod whose `base.repo` is a
GitHub repository, it compares the app's latest release with `tested_app_version` and opens one issue
per newer version with the update steps: new `base.ipa_url` and `base.sha256`, new
`tested_app_version`, then **Mods: build a mod**. It never builds anything by itself.

## Tweak library

`tweaks.json` is the tweak catalog the Accretion app reads. When someone signs an app listed in a
tweak's `targets`, the app suggests it under Advanced → Modify → Tweaks, and one tap downloads and
injects it. Files download from each tweak's own project; nothing is re-hosted here.

Each entry needs `id`, `name`, `description`, `targets` (bundle IDs), `version`, `debURL` (https
`.deb` or `.dylib`), `license`, and optionally `requires` (extra files it needs), `projectURL` and
`author`. Check changes with:

```
python3 scripts/validate_tweaks.py --check-links
```
