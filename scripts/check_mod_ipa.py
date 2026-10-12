"""Check a built mod .ipa before it's published: an app whose bundle ID is the mod's own.

Usage: python3 scripts/check_mod_ipa.py <mod> <file.ipa>
Prints "ok: <bundle id> <version>" or exits with an ::error:: line (GitHub Actions shows it).
"""

import json
import os
import plistlib
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(mod, ipa):
    with open(os.path.join(ROOT, "mods", mod, "mod.json"), encoding="utf-8") as f:
        meta = json.load(f)
    try:
        z = zipfile.ZipFile(ipa)
    except zipfile.BadZipFile:
        sys.exit("::error::The file isn't an .ipa (not a zip)")
    plists = [n for n in z.namelist() if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", n)]
    if len(plists) != 1:
        sys.exit("::error::The .ipa needs exactly one Payload/<name>.app/Info.plist")
    info = plistlib.loads(z.read(plists[0]))
    for key in ("CFBundleIdentifier", "CFBundleExecutable", "CFBundleShortVersionString"):
        if not info.get(key):
            sys.exit(f"::error::Info.plist has no {key}")
    app = plists[0].rsplit("/", 1)[0]
    if f"{app}/{info['CFBundleExecutable']}" not in z.namelist():
        sys.exit("::error::The app's executable is missing")
    if info["CFBundleIdentifier"] != meta["bundle_id"]:
        sys.exit(f"::error::Bundle ID {info['CFBundleIdentifier']} isn't the mod's {meta['bundle_id']}")
    print(f"ok: {info['CFBundleIdentifier']} {info['CFBundleShortVersionString']}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
