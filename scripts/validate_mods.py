"""Check every mod's folder and mod.json (or only the ones named) before anything is built.

Usage: python3 scripts/validate_mods.py [mod ...]
A mod's hooks.json, when it has one, is checked too.
Exits 1 with one line per problem. CI runs it on every push; the "Mods: build a mod" workflow runs it first.
A full run (no names) also checks that the mod choices in build.yml and publish.yml list exactly the
public mods (mod.json without "personal": true).
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
    if os.path.isfile(os.path.join(folder, "hooks.json")):
        problems += hooks_problems(os.path.join(folder, "hooks.json"))
        problems += runtime_problems(folder)
    if os.path.isfile(os.path.join(folder, "tweaks.json")):
        problems += tweaks_problems(folder)
    return problems


def tweaks_problems(folder):
    """Problems in a tweaks.json (the menu items scripts/gen_tweaks.py turns into AMTweaks.gen.m) and the
    Makefile that must compile the generated file."""
    import gen_tweaks

    try:
        tweaks, hooks = gen_tweaks.load(folder)
    except json.JSONDecodeError as e:
        return [f"tweaks.json isn't valid JSON ({e})"]
    problems = [f"tweaks.json: {p}" for p in gen_tweaks.problems(tweaks, hooks)]
    makefile = os.path.join(folder, "Makefile")
    if os.path.isfile(makefile):
        with open(makefile, encoding="utf-8") as f:
            if "AMTweaks.gen.m" not in f.read():
                problems.append("Makefile must list AMTweaks.gen.m (the mod has a tweaks.json)")
    return problems


RUNTIME_FILES = ("runtime/am_il2cpp.c", "runtime/AMIl2cpp.m", "runtime/am_hooks_data.c")


def runtime_problems(folder):
    """A mod with a hooks.json must compile the IL2CPP runtime (runtime/), or its hooks are never installed."""
    path = os.path.join(folder, "Makefile")
    if not os.path.isfile(path):
        return []  # a mod built from a ready-made .deb has no Makefile
    with open(path, encoding="utf-8") as f:
        makefile = f.read()
    missing = [name for name in RUNTIME_FILES if name not in makefile]
    if missing:
        return [f"Makefile must list {', '.join(missing)} (the mod has a hooks.json; README.md: Hooking at runtime)"]
    return []


def hooks_problems(path):
    """Problems in a hooks.json, the methods a Unity mod hooks by name (scripts/find_cheats.py writes it,
    scripts/intake_auto.py checks them in every new version of the game)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return [f"hooks.json isn't valid JSON ({e})"]
    if not isinstance(data, dict) or data.get("engine") != "unity-il2cpp":
        return ['hooks.json needs "engine": "unity-il2cpp"']
    hooks = data.get("hooks")
    if not isinstance(hooks, list):
        return ['hooks.json needs a "hooks" list']
    problems = []
    for i, h in enumerate(hooks):
        where = f"hooks.json hook {i}"
        if not isinstance(h, dict):
            problems.append(f"{where} must be an object")
            continue
        for key in ("class", "method"):
            if not isinstance(h.get(key), str) or not h[key]:
                problems.append(f"{where} needs {key}")
        if not isinstance(h.get("namespace"), str):
            problems.append(f'{where} needs namespace (use "" for the global namespace)')
        if "args" in h and (not isinstance(h["args"], int) or h["args"] < 0):
            problems.append(f"{where}: args must be a count")
    return problems


WORKFLOWS = ("build.yml", "publish.yml")
# the "options: [a, b]" line of the "mod:" input in a workflow file
MOD_OPTIONS = re.compile(r"^      mod:\n(?:        [^\n]*\n)*?        options: \[([^\]]*)\]", re.M)


def workflow_mod_options(name):
    """The mod choices of a workflow's manual run form, sorted; None if the input has no choices."""
    with open(os.path.join(ROOT, ".github", "workflows", name), encoding="utf-8", newline="") as f:
        text = f.read().replace("\r\n", "\n")
    found = MOD_OPTIONS.search(text)
    if not found:
        return None
    return sorted(o.strip() for o in found.group(1).split(",") if o.strip())


def check_workflow_options(metas):
    """Problems when a workflow's mod choices differ from the public mods."""
    public = sorted(mod for mod, meta in metas.items() if meta.get("personal") is not True)
    problems = []
    for name in WORKFLOWS:
        if workflow_mod_options(name) != public:
            problems.append(
                f"{name}: the mod choices {workflow_mod_options(name)} differ from the public mods {public}. "
                "Add the mod to the options of the mod input in both build.yml and publish.yml."
            )
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
    if not names:
        for problem in check_workflow_options(metas):
            print(problem)
            failed = True
    if failed:
        return 1
    print(f"ok: {len(names) or len(metas)} mod(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
