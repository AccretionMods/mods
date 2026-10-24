"""Check tweaks.json, the Accretion tweak library the app reads.

Usage: python3 scripts/validate_tweaks.py [--check-links]
"""

import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]*$")
REQUIRED = ("id", "name", "description", "targets", "version", "debURL", "license")


def problems(catalog):
    errors = []
    if catalog.get("version") != 1:
        errors.append("version must be 1")
    seen = set()
    for i, t in enumerate(catalog.get("tweaks", [])):
        where = f"tweak {i} ({t.get('id')})"
        for k in REQUIRED:
            if not t.get(k):
                errors.append(f"{where}: missing {k}")
        if t.get("id") in seen:
            errors.append(f"{where}: duplicate id")
        seen.add(t.get("id"))
        for target in t.get("targets") or []:
            if not BUNDLE_ID.match(target):
                errors.append(f"{where}: bad target bundle ID {target!r}")
        # iconURL: optional, the game's App Store icon the store shows for the tweak
        for url in [t.get("debURL")] + list(t.get("requires") or []) + [t.get("projectURL"), t.get("iconURL")]:
            if url is not None and not str(url).startswith("https://"):
                errors.append(f"{where}: not an https link: {url}")
        for url in [t.get("debURL")] + list(t.get("requires") or []):
            if url and not re.search(r"\.(deb|dylib)$", url.split("?")[0]):
                errors.append(f"{where}: file must be a .deb or .dylib: {url}")
    return errors


def dead_links(catalog):
    dead = []
    for t in catalog.get("tweaks", []):
        for url in [t.get("debURL")] + list(t.get("requires") or []):
            try:
                req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "accretion-mods"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    if r.status >= 400:
                        dead.append(f"{t['id']}: HTTP {r.status} {url}")
            except Exception as e:  # report every kind of failure, then keep going
                dead.append(f"{t['id']}: {e} {url}")
    return dead


def main():
    with open(os.path.join(ROOT, "tweaks.json"), encoding="utf-8") as f:
        catalog = json.load(f)
    errors = problems(catalog)
    if "--check-links" in sys.argv:
        errors += dead_links(catalog)
    if errors:
        print(f"tweaks.json: {len(errors)} problems", *errors, sep="\n  ")
        return 1
    print(f"tweaks.json: ok ({len(catalog['tweaks'])} tweaks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
