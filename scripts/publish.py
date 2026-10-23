"""Publish a mod build privately: upload the .ipa to Accretion's private storage and record it.

The file never goes to a public GitHub release. Signed-in Accretion users (and personal source
links) get it through the store's download server; mods.json points there.

The published version is the .ipa's own CFBundleShortVersionString, which scripts/local_ipa.sh sets
to "<app version>-mod<mod version>" (like 4.0.0-mod1.1.0), so installed copies match the store.

Needs the upload key: the git-ignored file .upload-key in this repo, or ACCRETION_UPLOAD_KEY.

Usage:
  python3 scripts/publish.py --mod thewanderer-plus --ipa out/thewanderer-plus_10.1-mod1.0.9_accretion.ipa \
      [--min-os 17.0] [--allow-personal]

Mods marked "personal": true are refused unless --allow-personal is given (a manual upload the owner chose);
their release entry then carries "personalPublished" and mods.json lists them.
"""

import argparse
import hashlib
import json
import os
import plistlib
import re
import sys
import urllib.parse
import urllib.request
import zipfile

from update_source import ROOT, app_version_of, build_source, load, record, save

FUNCTIONS = "https://bbcjpinhvlxocjxlyplt.supabase.co/functions/v1"


def upload_key():
    key = os.environ.get("ACCRETION_UPLOAD_KEY", "").strip()
    if not key:
        try:
            with open(os.path.join(ROOT, ".upload-key"), encoding="utf-8") as f:
                key = f.read().strip()
        except FileNotFoundError:
            pass
    if not key:
        sys.exit("No upload key: put it in .upload-key or set ACCRETION_UPLOAD_KEY")
    return key


def ipa_version(path):
    """The app's CFBundleShortVersionString inside an .ipa"""
    with zipfile.ZipFile(path) as z:
        plists = [n for n in z.namelist() if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", n)]
        if len(plists) != 1:
            sys.exit(f"{path} needs exactly one Payload/<name>.app/Info.plist")
        version = plistlib.loads(z.read(plists[0])).get("CFBundleShortVersionString")
    if not version or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}", version):
        sys.exit(f"{path} has no usable version ({version!r})")
    return version


def call(url, key, body):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"x-upload-key": key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mod", required=True)
    p.add_argument("--ipa", required=True)
    p.add_argument("--app-version", help="ignored: the version comes from the .ipa (kept for old commands)")
    p.add_argument("--min-os")
    p.add_argument(
        "--allow-personal",
        action="store_true",
        help='publish a mod marked "personal": true anyway (manual uploads only; the owner\'s choice)',
    )
    a = p.parse_args()

    meta = load(f"mods/{a.mod}/mod.json", None)
    if meta is None:
        sys.exit(f"mods/{a.mod}/mod.json not found")
    if meta.get("personal") and not a.allow_personal:
        sys.exit(
            f"{a.mod} is a personal mod: use Mods: build a mod to keep a private copy, "
            "or publish it by hand with --allow-personal (upload_ipa.ps1 -AllowPersonal)"
        )
    if meta.get("personal"):
        print(f"{a.mod} is personal: publishing it because --allow-personal was given")
    bundle = meta["bundle_id"]
    version = ipa_version(a.ipa)
    key = upload_key()
    digest = hashlib.sha256()
    with open(a.ipa, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    body = {
        "bundle_id": bundle,
        "version": version,
        "size": os.path.getsize(a.ipa),
        "sha256": digest.hexdigest(),
        # the What's new post: "The Wanderer+ 10.1-mod1.0.9" with what the mod does
        "name": meta.get("display_name") or meta["name"],
        "notes": f"Mod {meta['version']} for {meta['name']} {app_version_of(version)}.\n\n"
        f"{meta.get('description', '')}"[:4000],
    }

    prepared = call(f"{FUNCTIONS}/upload", key, body)
    if not prepared.get("published"):
        with open(a.ipa, "rb") as stream:
            req = urllib.request.Request(
                prepared["signedUrl"], data=stream, method="PUT",
                headers={"Content-Type": "application/octet-stream", "Content-Length": str(body["size"])},
            )
            with urllib.request.urlopen(req, timeout=900):
                pass
        body["upload_id"] = prepared["upload_id"]
        call(f"{FUNCTIONS}/upload?finish=1", key, body)
    print(f"uploaded {bundle} {version} privately")

    query = urllib.parse.urlencode({"app": bundle, "version": version})
    url = f"{FUNCTIONS}/download?{query}"
    releases = load("releases.json", {})
    record(releases, a.mod, a.ipa, url, version, a.min_os, allow_personal=bool(meta.get("personal")))
    save("releases.json", releases)
    save("mods.json", build_source(releases))
    print("releases.json and mods.json updated; commit and push them")


if __name__ == "__main__":
    main()
