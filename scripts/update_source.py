"""Record a new mod build and rebuild mods.json, the AltStore source the store reads.

mods.json is generated from mods/*/mod.json plus releases.json (one entry per published
build). Never edit either JSON file by hand; run this instead.

Usage:
  python3 scripts/update_source.py                      # just rebuild mods.json
  python3 scripts/update_source.py --mod NAME --ipa FILE --url URL --version X [--min-os Y]
"""

import argparse
import glob
import json
import os
import re
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


def store_version(app_version, mod_version):
    """The version a build is published under: the base app's version plus the mod's, like
    4.0.0-mod1.1.0, so a new mod version for the same app version is still an update. The .ipa's
    CFBundleShortVersionString is set to the same string, so installed copies compare equal."""
    return f"{app_version}-mod{mod_version}"


def app_version_of(version):
    """4.0.0-mod1.1.0 -> 4.0.0 (and 4.0.0 -> 4.0.0, for builds published before mod versions)."""
    return str(version).split("-mod", 1)[0]


def record(releases, mod, ipa, url, version, min_os, allow_personal=False):
    meta = load(f"mods/{mod}/mod.json", None)
    if meta is None:
        raise SystemExit(f"mods/{mod}/mod.json not found")
    entry = {
        # a personal mod the owner chose to publish by hand (publish.py --allow-personal) stays listed
        **({"personalPublished": True} if allow_personal else {}),
        "version": version,
        "appVersion": app_version_of(version),
        "modVersion": meta["version"],
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "size": os.path.getsize(ipa),
        "downloadURL": url,
        "minOSVersion": min_os or meta.get("min_os") or "15.0",
    }
    # One entry per published version: a rebuild of the same version replaces the older build.
    rest = [r for r in releases.get(mod, []) if r["version"] != version]
    # AltStore wants versions newest first, even if an older app version is built later.
    releases[mod] = sorted([entry] + rest, key=lambda r: (version_key(r["version"]), r["date"]), reverse=True)


def version_key(v):
    """'4.0.0-mod1.10.0' -> (4, 0, 0, 1, 10, 0): every number in order, like the app and the store."""
    return tuple(int(n) for n in re.findall(r"\d+", str(v)))


def app_entry(mod, meta, builds):
    versions = [
        {
            "version": b["version"],
            "date": b["date"],
            "size": b["size"],
            "downloadURL": b["downloadURL"],
            "minOSVersion": b["minOSVersion"],
            "localizedDescription": f"Mod {b['modVersion']} for {meta['name']} {b.get('appVersion') or app_version_of(b['version'])}.",
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
            meta = json.load(f)
        if meta.get("personal") and not any(b.get("personalPublished") for b in builds):
            continue
        apps.append(app_entry(mod, meta, builds))
    apps.sort(key=lambda a: a["name"].lower())
    return {
        "name": "Accretion Mods",
        "identifier": "io.github.accretionmods",
        "subtitle": "Modded apps built by Accretion.",
        "description": "Free and open-source apps with mods and single-player cheats built in. "
        "No paid unlocks, no online cheats.\n\nSource and rules: https://github.com/" + REPO,
        "sourceURL": SOURCE_URL,
        "iconURL": "https://getaccretion.app/assets/icon.png",
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
    p.add_argument("--version", help="published version, like 4.0.0-mod1.1.0")
    p.add_argument("--min-os")
    a = p.parse_args()
    releases = load("releases.json", {})
    if a.mod:
        if not (a.ipa and a.url and a.version):
            p.error("--mod needs --ipa, --url and --version")
        record(releases, a.mod, a.ipa, a.url, a.version, a.min_os)
        save("releases.json", releases)
    source = build_source(releases)
    save("mods.json", source)
    print(f"mods.json: {len(source['apps'])} apps")


if __name__ == "__main__":
    main()
