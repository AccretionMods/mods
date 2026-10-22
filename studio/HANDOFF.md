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

## State (2026-10-22)
- v1 built and checked in the built-in browser against the Wanderer example mod: mod list, Switches (18 items with
  hook/pin chips, watch line), Find (regex search, class methods, fields, Hook/Pin/Call forms: a pin added through
  the page landed in tweaks.json and the generated code), Suggest (36 rows from 58 learned patterns), Build & Phone
  (check-hooks job streamed into the console), Files; phone width (375 px) with no horizontal scroll; dark/light.
- `tests/test_studio.py`: the API on a synthetic mod and dump (quick commands, errors, a streamed job).
- Not tried from the page yet: dump, build, sign + install, run, log, update, New mod (they run the same dev.py
  commands the CLI already exercised; the page only starts them and shows the output).
- The Sonnet worker could not write the page (context thrash, as before in this repo); the page is hand-written.

## Open
- Phone-side use (`--host 0.0.0.0`): no login; decide whether a token is wanted before using it off the PC.
- Nice to have: live log tail as a dedicated view, edit tweaks.json in place, drag to reorder the menu.
