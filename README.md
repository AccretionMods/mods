# Accretion Mods

Ready-to-install modified apps, built locally and listed in [Accretion](https://getaccretion.github.io).
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
mods/<name>/
  Makefile, control   Theos tweak
  Tweak.x             the mod itself
  mod.json            listing details, target app, published bundle ID
scripts/
  build.sh            build one mod's .dylib
  check_ipa.sh        check an .ipa: Payload/*.app present, not FairPlay-encrypted
  local_ipa.sh        build a mod's unsigned .ipa on Linux or WSL
  publish.py          upload a build privately and record it
  update_source.py    regenerate mods.json
  sign.sh             sign an .ipa with your own certificate, for your own devices
  install_zsign.sh    install zsign (the open-source signer sign.sh uses)
  validate_tweaks.py  check tweaks.json
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

There is no GitHub Actions build. Mods are built locally on Linux or WSL, with
[Theos](https://theos.dev/docs/installation) in `~/theos`:

```
scripts/local_ipa.sh swiftradio-plus "" /mnt/c/Users/<you>/Downloads
```

It installs [cyan](https://github.com/asdfzxcvbn/pyzule-rw) into `~/.cyan` the first time, builds the
tweak, downloads `base.ipa_url`, checks it, injects the tweak, checks the result, and writes an unsigned
`<mod>_<appversion>_accretion.ipa`. To test it yourself, sign it first: Sideloadly or AltStore/SideStore
with an Apple ID, or Feather with a developer certificate.

Tweaks that hook methods depend on Cydia Substrate; cyan bundles ElleKit in its place automatically.

Then publish it privately:

```sh
python3 scripts/publish.py --mod swiftradio-plus --ipa swiftradio-plus_4.0.0_accretion.ipa --app-version 4.0.0 [--min-os 15.0]
```

The .ipa is uploaded to Accretion's private storage through the store's upload function, never as a
public GitHub release. `publish.py` then updates `releases.json` and `mods.json` with download-server
links, which signed-in Accretion users (and their personal source links) download through. The upload
key comes from the git-ignored `.upload-key` file or the `ACCRETION_UPLOAD_KEY` variable. Commit and
push the updated `releases.json` and `mods.json`.

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
