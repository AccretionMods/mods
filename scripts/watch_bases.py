"""Find base apps with a newer release than their mod was tested on, and open an issue for each.

For every mod whose base.repo is a GitHub repository, reads the repository's latest release and
compares its version with mod.json's tested_app_version. A newer release gets one issue (never a
duplicate while it's open) listing the steps to update the mod. Nothing is built or published.

Usage: GITHUB_TOKEN=... GITHUB_REPOSITORY=AccretionMods/mods python3 scripts/watch_bases.py [--dry-run]
"""

import glob
import json
import os
import re
import sys
import urllib.error
import urllib.request

from update_source import ROOT, version_key

API = "https://api.github.com"


def api(path, token, method="GET", body=None):
    req = urllib.request.Request(
        f"{API}{path}",
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "accretion-mods-watch",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"null")


def ipa_assets(release):
    return [a["browser_download_url"] for a in release.get("assets", []) if a["name"].lower().endswith(".ipa")]


def main():
    dry = "--dry-run" in sys.argv
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "AccretionMods/mods")
    if not token:
        sys.exit("GITHUB_TOKEN isn't set")
    open_titles = {i["title"] for i in api(f"/repos/{repo}/issues?state=open&per_page=100", token) or []}
    found = 0
    for path in sorted(glob.glob(os.path.join(ROOT, "mods", "*", "mod.json"))):
        mod = os.path.basename(os.path.dirname(path))
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
        match = re.fullmatch(r"https://github\.com/([^/]+/[^/]+?)/?", str(meta.get("base", {}).get("repo", "")))
        if not match:
            print(f"{mod}: base.repo isn't a GitHub repository; skipped")
            continue
        try:
            release = api(f"/repos/{match.group(1)}/releases/latest", token)
        except urllib.error.HTTPError as e:
            print(f"{mod}: no latest release for {match.group(1)} ({e.code})")
            continue
        tag = release["tag_name"]
        latest = tag.lstrip("vV")
        tested = str(meta.get("tested_app_version", "0"))
        if version_key(latest) <= version_key(tested):
            print(f"{mod}: {match.group(1)} {latest} is what the mod was tested on ({tested})")
            continue
        found += 1
        title = f"{mod}: {meta['name']} base app {latest} is out"
        if title in open_titles:
            print(f"{mod}: {latest} is newer; issue already open")
            continue
        links = "\n".join(f"- {u}" for u in ipa_assets(release)) or "- (no .ipa attached to the release)"
        body = (
            f"[{match.group(1)} {tag}]({release['html_url']}) is newer than the version this mod was "
            f"tested on ({tested}).\n\n.ipa files in that release:\n{links}\n\n"
            "To update the mod:\n"
            "1. Set `base.ipa_url` in `mods/" + mod + "/mod.json` to the new unsigned .ipa, and "
            "`base.sha256` to its checksum (`sha256sum` it, or start from `scripts/new_mod.sh` output).\n"
            "2. Set `tested_app_version` to " + latest + " and check the mod still works with it.\n"
            "3. Run **Actions → Build mod** for `" + mod + "`. It publishes as "
            f"`{latest}-mod{meta['version']}`.\n\n"
            "_Opened by the weekly Watch base apps workflow._"
        )
        if dry:
            print(f"{mod}: would open “{title}”")
        else:
            api(f"/repos/{repo}/issues", token, "POST", {"title": title, "body": body})
            print(f"{mod}: opened “{title}”")
    print(f"{found} base app(s) with a newer release")


if __name__ == "__main__":
    main()
