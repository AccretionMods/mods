"""Check every mod's folder and mod.json (or only the ones named) before anything is built.

Usage: python3 scripts/validate_mods.py [mod ...]
Exits 1 with one line per problem. CI runs it on every push; the Build mod workflow runs it first.
"""

import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREFIX = "io.github.accretionmods."
VERSION = re.compile(r"^\d+(\.\d+){0,3}$")
BUNDLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$")
CATEGORIES = {"games", "entertainment", "utilities", "photo-video", "social", "lifestyle", "developer", "other"}
REQUIRED = ("name", "version", "kind", "category", "description", "target_bundle_id", "bundle_id", "display_name", "icon_url", "base")


def https(value):
    return isinstance(value, str) and value.startswith("https://") and " " not in value


def check(mod, meta):
    problems = []
    folder = os.path.join(ROOT, "mods", mod)
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", mod):
        problems.append("folder name must be lowercase letters, digits and dashes")
    deb = meta.get("deb")
    if deb is not None:
        # a ready-made tweak .deb, kept privately like a private base (scripts/base.py)
        if not re.fullmatch(r"[0-9a-f]{64}", str((deb or {}).get("sha256", ""))):
            problems.append("deb.sha256 must be the .deb's sha256 (64 lowercase hex characters)")
    else:
        for name in ("Makefile", "control", "Tweak.x"):
            if not os.path.isfile(os.path.join(folder, name)):
                problems.append(f"missing {name}")
    for key in REQUIRED:
        if not meta.get(key):
            problems.append(f"mod.json needs {key}")
    if meta.get("version") and not VERSION.match(str(meta["version"])):
        problems.append(f"version {meta['version']!r} must be numbers like 1.2.0")
    if meta.get("kind") and meta["kind"] != "tweak":
        problems.append("kind must be tweak")
    if meta.get("category") and meta["category"] not in CATEGORIES:
        problems.append(f"category must be one of {', '.join(sorted(CATEGORIES))}")
    bundle = str(meta.get("bundle_id", ""))
    if bundle and not (bundle.startswith(PREFIX) and BUNDLE.match(bundle)):
        problems.append(f"bundle_id must start with {PREFIX}")
    target = str(meta.get("target_bundle_id", ""))
    if target and (not BUNDLE.match(target) or target.startswith(PREFIX)):
        problems.append("target_bundle_id must be the base app's own bundle ID")
    if meta.get("min_os") and not VERSION.match(str(meta["min_os"])):
        problems.append("min_os must be like 15.0")
    if meta.get("remove_extensions", "encrypted") not in ("encrypted", "all"):
        problems.append("remove_extensions must be encrypted or all")
    if meta.get("icon_url") and not https(meta["icon_url"]):
        problems.append("icon_url must be an https link")
    base = meta.get("base") or {}
    if base.get("private") is True:
        if base.get("ipa_url"):
            problems.append("a private base has no ipa_url (scripts/base.py keeps it by sha256)")
    elif not https(base.get("ipa_url")):
        problems.append("base.ipa_url must be an https link to the base .ipa (or set base.private)")
    if not re.fullmatch(r"[0-9a-f]{64}", str(base.get("sha256", ""))):
        problems.append("base.sha256 must be the base .ipa's sha256 (64 lowercase hex characters)")
    if base.get("repo") and not https(base["repo"]):
        problems.append("base.repo must be an https link")
    for shot in meta.get("screenshots", []):
        if not https(shot):
            problems.append(f"screenshot {shot!r} must be an https link")
    return problems


def main(names):
    paths = sorted(glob.glob(os.path.join(ROOT, "mods", "*", "mod.json")))
    metas = {}
    failed = False
    for path in paths:
        mod = os.path.basename(os.path.dirname(path))
        try:
            with open(path, encoding="utf-8") as f:
                metas[mod] = json.load(f)
        except json.JSONDecodeError as e:
            print(f"{mod}: mod.json isn't valid JSON ({e})")
            failed = True
    for name in names:
        if name not in metas:
            print(f"{name}: no mods/{name}/mod.json")
            failed = True
    seen = {}
    for mod, meta in metas.items():
        bundle = meta.get("bundle_id")
        if bundle in seen:
            print(f"{mod}: bundle_id {bundle} is already used by {seen[bundle]}")
            failed = True
        seen[bundle] = mod
        if names and mod not in names:
            continue
        for problem in check(mod, meta):
            print(f"{mod}: {problem}")
            failed = True
    if failed:
        return 1
    print(f"ok: {len(names) or len(metas)} mod(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
