"""Publish a mod build privately: upload the .ipa to Accretion's private storage and record it.

The file never goes to a public GitHub release. Signed-in Accretion users (and personal source
links) get it through the store's download server; mods.json points there.

Needs the upload key: the git-ignored file .upload-key in this repo, or ACCRETION_UPLOAD_KEY.

Usage:
  python3 scripts/publish.py --mod swiftradio-plus --ipa out/swiftradio-plus_4.0.0_accretion.ipa \
      --app-version 4.0.0 [--min-os 17.0]
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

from update_source import ROOT, build_source, load, record, save

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
    p.add_argument("--app-version", required=True)
    p.add_argument("--min-os")
    a = p.parse_args()

    meta = load(f"mods/{a.mod}/mod.json", None)
    if meta is None:
        sys.exit(f"mods/{a.mod}/mod.json not found")
    bundle = meta["bundle_id"]
    key = upload_key()
    body = {"bundle_id": bundle, "version": a.app_version, "size": os.path.getsize(a.ipa)}

    signed = call(f"{FUNCTIONS}/upload", key, body)["signedUrl"]
    with open(a.ipa, "rb") as f:
        req = urllib.request.Request(
            signed, data=f.read(), method="PUT", headers={"Content-Type": "application/octet-stream"}
        )
    with urllib.request.urlopen(req, timeout=900):
        pass
    call(f"{FUNCTIONS}/upload?finish=1", key, body)
    print(f"uploaded {bundle} {a.app_version} privately")

    query = urllib.parse.urlencode({"app": bundle, "version": a.app_version})
    url = f"{FUNCTIONS}/download?{query}"
    releases = load("releases.json", {})
    record(releases, a.mod, a.ipa, url, a.app_version, a.min_os)
    save("releases.json", releases)
    save("mods.json", build_source(releases))
    print("releases.json and mods.json updated; commit and push them")


if __name__ == "__main__":
    main()
