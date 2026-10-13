"""Move files from the intake folder into Accretion's private storage, and find stored files that no
mod uses any more.

  python3 scripts/intake.py [folder]          upload every .ipa / .deb in the folder (default: the
                                              INTAKE environment variable, else C:/AccretionMods/intake),
                                              print each file's sha256, bundle ID and version, and move
                                              it to <folder>/done
  python3 scripts/intake.py --unused          list private files (bases/<sha256>) no mod.json names
                                              (needs BUILDS_BUCKET and AWS credentials that can list it)

Uploading uses the upload key, like scripts/base.py.
"""

import glob
import json
import os
import plistlib
import re
import shutil
import sys
import zipfile

from base import put, sha256_of

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = os.environ.get("INTAKE") or "C:/AccretionMods/intake"


def describe(path):
    """'<bundle ID> <version>' for an .ipa, 'tweak' for a .deb"""
    if not path.lower().endswith(".ipa"):
        return "tweak .deb"
    with zipfile.ZipFile(path) as z:
        plists = [n for n in z.namelist() if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", n)]
        if not plists:
            return "not an app (.ipa without Payload/*.app)"
        info = plistlib.loads(z.read(plists[0]))
    return f"{info.get('CFBundleIdentifier')} {info.get('CFBundleShortVersionString')}"


def upload_folder(folder):
    files = sorted(
        p for p in glob.glob(os.path.join(folder, "*")) if p.lower().endswith((".ipa", ".deb"))
    )
    if not files:
        print(f"nothing to upload in {folder}")
        return
    done = os.path.join(folder, "done")
    os.makedirs(done, exist_ok=True)
    for path in files:
        what = describe(path)
        put(path)  # prints the sha256
        print(f"  {os.path.basename(path)}: {what}")
        shutil.move(path, os.path.join(done, os.path.basename(path)))
    print('Put the sha256 in mod.json: "base": {"private": true, "sha256": ...} or "deb": {"sha256": ...}')


def used_hashes():
    used = set()
    for path in glob.glob(os.path.join(ROOT, "mods", "*", "mod.json")):
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
        for part in ("base", "deb"):
            sha = (meta.get(part) or {}).get("sha256")
            if sha:
                used.add(sha)
    return used


def unused():
    import boto3  # only this mode needs it

    bucket = os.environ.get("BUILDS_BUCKET") or sys.exit("set BUILDS_BUCKET")
    s3 = boto3.client("s3", region_name=os.environ.get("BUILDS_REGION", "us-east-2"))
    used = used_hashes()
    total = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="bases/"):
        for obj in page.get("Contents", []):
            if obj["Key"].removeprefix("bases/") not in used:
                total += obj["Size"]
                print(f"{obj['Key']}  {obj['Size'] / 1e6:.1f} MB  {obj['LastModified']:%Y-%m-%d}")
    print(f"unused: {total / 1e6:.1f} MB (delete with: aws s3 rm s3://{bucket}/<key>)")


def main():
    args = sys.argv[1:]
    if args == ["--unused"]:
        unused()
    elif len(args) <= 1 and not (args and args[0].startswith("-")):
        upload_folder(args[0] if args else DEFAULT)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
