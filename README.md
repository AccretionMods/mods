# Accretion Mods

Ready-to-install modified apps, built in GitHub Actions and listed in [Accretion](https://getaccretion.github.io).
Each app is a finished .ipa with the mod already inside. Install it from the store like any other app.

AltStore / SideStore / Feather source:

```
https://raw.githubusercontent.com/AccretionMods/mods/main/mods.json
```

## Rules

- **Base apps:** free or open-source apps only. Never a paid app.
- **Allowed:** tweaks (features, settings, UI, themes, ad removal) and cheats for single-player or offline play.
- **Not allowed:** anything that unlocks what the developer sells (subscriptions, in-app purchases, premium currency, paid tiers), and cheats that affect multiplayer or online play.
- Prefer open-source apps built from their own unsigned releases. A closed-source free app ships its developer's code, and they can file a DMCA takedown.

## Layout

```
menu/                 shared floating mod menu (AMMenu), compiled into every mod
mods/<name>/
  Makefile, control   Theos tweak
  Tweak.x             the mod itself
  mod.json            listing details, target app, published bundle ID
scripts/
  build.sh            build one mod's .dylib
  check_ipa.sh        check an .ipa: Payload/*.app present, not FairPlay-encrypted
  publish.py          upload a build privately and record it
  update_source.py    regenerate mods.json
mods.json             generated AltStore source (do not edit)
releases.json         generated list of published builds (do not edit)
```

## Add a mod

1. Copy `mods/swiftradio-plus` to `mods/<name>` (lowercase letters, digits and dashes).
2. Edit `mod.json`:
   - `target_bundle_id`: the base app's bundle ID. The build stops if the .ipa doesn't match.
   - `bundle_id`: a new ID under `io.github.accretionmods.`, so the mod installs next to the original
     and doesn't replace it in the store (the store keeps one app per bundle ID).
   - `display_name`, `description`, `category`, `icon_url`, `tested_app_version`.
   - `base.ipa_url`: where the build downloads the base .ipa.
   - `remove_extensions`: `encrypted` (default) or `all`.
3. Write `Tweak.x`. Register switches and sliders with `AMMenu.shared` in `%ctor`, then call `[AMMenu.shared install]`.
   Read values anywhere with `[AMMenu boolForKey:]` / `[AMMenu floatForKey:]`.
4. Bump `version` in `mod.json` for every change you publish.

## Build and publish

Build locally (WSL with Theos): `scripts/local_ipa.sh <mod>` builds the tweak, checks the base
.ipa, injects the tweak with [cyan](https://github.com/asdfzxcvbn/pyzule-rw) and checks the result.

Then publish it privately:

```sh
python3 scripts/publish.py --mod swiftradio-plus --ipa out/swiftradio-plus_4.0.0_accretion.ipa --app-version 4.0.0
```

The .ipa goes to Accretion's private storage, never a public release. Signed-in Accretion users
(and their personal source links) download it through the store's download server, which is
where `mods.json` points. The upload key lives in the git-ignored `.upload-key` file (or the
`ACCRETION_UPLOAD_KEY` variable). Commit and push the updated `releases.json` and `mods.json`.

Tweaks that hook methods depend on Cydia Substrate; cyan bundles ElleKit in its place automatically.

## Build locally (Linux or WSL)

With [Theos](https://theos.dev/docs/installation) in `~/theos`:

```
scripts/local_ipa.sh swiftradio-plus "" /mnt/c/Users/<you>/Downloads
```

It installs cyan into `~/.cyan` the first time, builds the tweak, downloads `base.ipa_url`, checks it,
and writes an unsigned `<mod>_<appversion>_accretion.ipa`. Sign it before installing: Sideloadly or
AltStore/SideStore with an Apple ID, or Feather with a developer certificate.

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
