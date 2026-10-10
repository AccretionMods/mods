"""Record a new mod build and rebuild mods.json, the AltStore source the store reads.

mods.json is generated from mods/*/mod.json plus releases.json (one entry per published
build). Never edit either JSON file by hand; run this instead.

Usage:
  python3 scripts/update_source.py                      # just rebuild mods.json
  python3 scripts/update_source.py --mod NAME --ipa FILE --url URL --app-version X [--min-os Y]
"""

import argparse
import glob
import json
import os
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "AccretionMods/mods"
SOURCE_URL = f"https://raw.githubusercontent.com/{REPO}/main/mods.json"


def load(name, default):
    try:
        with open(os.path.join(ROOT, name), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save(name, data):
    with open(os.path.join(ROOT, name), "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def record(releases, mod, ipa, url, app_version, min_os):
    meta = load(f"mods/{mod}/mod.json", None)
    if meta is None:
        raise SystemExit(f"mods/{mod}/mod.json not found")
    entry = {
        "version": app_version,
        "modVersion": meta["version"],
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "size": os.path.getsize(ipa),
        "downloadURL": url,
        "minOSVersion": min_os or meta.get("min_os") or "15.0",
    }
    # One entry per app version: a rebuild of the same app version replaces the older build.
    rest = [r for r in releases.get(mod, []) if r["version"] != app_version]
    # AltStore wants versions newest first, even if an older app version is built later.
    releases[mod] = sorted([entry] + rest, key=lambda r: (version_key(r["version"]), r["date"]), reverse=True)


def version_key(v):
    return tuple(int(p) if p.isdigit() else 0 for p in str(v).replace("-", ".").split("."))


def app_entry(mod, meta, builds):
    versions = [
        {
            "version": b["version"],
            "date": b["date"],
            "size": b["size"],
            "downloadURL": b["downloadURL"],
            "minOSVersion": b["minOSVersion"],
            "localizedDescription": f"Mod {b['modVersion']} for {meta['name']} {b['version']}.",
        }
        for b in builds
    ]
    newest = versions[0]
    return {
        "name": meta["name"],
        "bundleIdentifier": meta["bundle_id"],
        "developerName": "Accretion",
        "subtitle": meta.get("subtitle", ""),
        "localizedDescription": meta["description"],
        "iconURL": meta["icon_url"],
        "category": meta.get("category", "other"),
        "screenshots": meta.get("screenshots", []),
        "versions": versions,
        # Legacy single-version fields, for older AltStore and SideStore builds.
        "version": newest["version"],
        "versionDate": newest["date"],
        "versionDescription": newest["localizedDescription"],
        "downloadURL": newest["downloadURL"],
        "size": newest["size"],
        "accretionMod": {"id": mod, "kind": meta["kind"], "modVersion": builds[0]["modVersion"]},
    }


def build_source(releases):
    apps = []
    for path in sorted(glob.glob(os.path.join(ROOT, "mods", "*", "mod.json"))):
        mod = os.path.basename(os.path.dirname(path))
        builds = releases.get(mod)
        if not builds:
            continue  # never built yet, so nothing to download
        with open(path, encoding="utf-8") as f:
            apps.append(app_entry(mod, json.load(f), builds))
    apps.sort(key=lambda a: a["name"].lower())
    return {
        "name": "Accretion Mods",
        "identifier": "io.github.accretionmods",
        "subtitle": "Modded apps built by Accretion.",
        "description": "Free and open-source apps with mods and single-player cheats built in. "
        "No paid unlocks, no online cheats.\n\nSource and rules: https://github.com/" + REPO,
        "sourceURL": SOURCE_URL,
        "iconURL": "https://getaccretion.github.io/assets/icon.png",
        "website": "https://github.com/" + REPO,
        "nsfw": False,
        "featuredApps": [a["bundleIdentifier"] for a in apps[:5]],
        "apps": apps,
        "news": [],
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mod")
    p.add_argument("--ipa")
    p.add_argument("--url")
    p.add_argument("--app-version")
    p.add_argument("--min-os")
    a = p.parse_args()
    releases = load("releases.json", {})
    if a.mod:
        if not (a.ipa and a.url and a.app_version):
            p.error("--mod needs --ipa, --url and --app-version")
        record(releases, a.mod, a.ipa, a.url, a.app_version, a.min_os)
        save("releases.json", releases)
    source = build_source(releases)
    save("mods.json", source)
    print(f"mods.json: {len(source['apps'])} apps")


if __name__ == "__main__":
    main()
