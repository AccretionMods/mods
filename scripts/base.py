"""Keep a mod's base .ipa private: in Accretion's private S3 storage, found by its sha256, instead of
at a public link. For apps whose .ipa can't be shared publicly (mod.json: "base": {"private": true,
"sha256": ...} and no ipa_url).

Uses the same upload key as scripts/publish.py (.upload-key or ACCRETION_UPLOAD_KEY).

Usage:
  python3 scripts/base.py put <base.ipa>          uploads it, prints its sha256
  python3 scripts/base.py get <sha256> <out.ipa>  downloads it and checks the sha256
"""

import hashlib
import sys
import urllib.error
import urllib.request

from publish import FUNCTIONS, call, upload_key


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def put(path):
    sha = sha256_of(path)
    signed = call(f"{FUNCTIONS}/upload?base=put", upload_key(), {"sha256": sha})["signedUrl"]
    with open(path, "rb") as f:
        req = urllib.request.Request(
            signed, data=f.read(), method="PUT", headers={"Content-Type": "application/octet-stream"}
        )
    with urllib.request.urlopen(req, timeout=900):
        pass
    print(sha)


def get(sha, out):
    signed = call(f"{FUNCTIONS}/upload?base=get", upload_key(), {"sha256": sha})["signedUrl"]
    try:
        with urllib.request.urlopen(signed, timeout=900) as r, open(out, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
    except urllib.error.HTTPError as e:
        sys.exit(f"no private file with sha256 {sha} ({e.code}); upload it with: scripts/base.py put <file>")
    got = sha256_of(out)
    if got != sha:
        sys.exit(f"downloaded base is {got}, expected {sha}")


def main():
    args = sys.argv[1:]
    if args[:1] == ["put"] and len(args) == 2:
        put(args[1])
    elif args[:1] == ["get"] and len(args) == 3:
        get(args[1], args[2])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
