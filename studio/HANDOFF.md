# Mod Studio - handoff
Read first when resuming. Project row in `~/PROJECTS.md`. The handbook rules (`../../handbook/CLAUDE.md`) apply:
this lives in the AccretionMods/mods repo, so nothing personal, no secrets, neutral commits, PRs with auto-merge.

## What it is
A local web UI for the mod dev loop (`scripts/dev.py`, README "Dev loop for personal mods"): one page on the PC
that lists the mods, searches a game's dump, adds switches (hooks) and no-JIT pins, builds, signs and installs on
the phone, and streams the device log. Started 2026-10-22 (owner: "make a UI for it and add it as a new project";
chose a local web app in this repo over a desktop app, a Claude panel or an iOS screen).

- `studio/server.py`: stdlib HTTP server (`am ui`): JSON API over dev.py's functions, long commands as jobs
  with streamed output, static page.
- `studio/index.html`: the page (vanilla JS, Accretion's design tokens, dark/light).
- Binds 127.0.0.1 by default. `--host 0.0.0.0` exposes it on the LAN with no login: trusted Wi-Fi only.
- Apple ID prompts (2FA) can't be answered through the page: sign in once from a console (`am sign ... --install`).
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

## State (2026-10-22)
- v1 built and checked in the built-in browser against the Wanderer example mod: mod list, Switches (18 items with
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
