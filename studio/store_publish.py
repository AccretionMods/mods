"""Publish a mod's local version to the store, or take it out, from Mod Studio's Store tab (one Studio job).

  python studio/store_publish.py update <mod>
  python studio/store_publish.py remove <mod>

Every change reaches main as a pull request with auto-merge (the repo's rule); nothing is pushed to main directly.
The work happens in a fresh worktree from origin/main, so the checkout Studio runs from is never switched.

update, a tweak (an io.github.accretionmods.<mod> entry in tweaks.json):
  1. the mod's files from this checkout and the tweak entry's new version, date and download address;
  2. the built .dylib as a GitHub release <mod>-v<version> (builds it first if this version has none);
  3. the pull request, merged by auto-merge once its checks pass;
  4. the previous version's release deleted (its address is gone from tweaks.json).
update, an app mod (one of the Mods: build a mod workflow's choices):
  1. the mod's files from this checkout; 2. the pull request, merged; 3. Mods: build a mod with Publish on, which
  builds in the cloud, uploads privately and records the store entry.
remove: the tweak entry, or the app's releases.json record (mods.json rebuilt from it), in a merged pull request;
  a tweak's GitHub releases are deleted after. An app's uploaded builds stay in private storage.

Personal app mods are never published from here.
"""

import argparse
import datetime
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "scripts"))

import dev  # noqa: E402

REPO = "AccretionMods/mods"
PREFIX = "io.github.accretionmods."
STEPS = 4


def say(text):
    print(text, flush=True)


def run(*cmd, cwd=None, check=True):
    say("$ " + " ".join(str(c) for c in cmd))
    r = subprocess.run([str(c) for c in cmd], cwd=cwd, text=True, encoding="utf-8", errors="replace", capture_output=True)
    for line in (r.stdout + r.stderr).strip().splitlines()[-8:]:
        say("  " + line)
    if check and r.returncode:
        raise SystemExit(f"failed: {cmd[0]} {cmd[1] if len(cmd) > 1 else ''}")
    return r.stdout.strip()


def tweak_entry(tweaks, mod):
    return next((t for t in tweaks.get("tweaks", []) if t.get("id") == PREFIX + mod), None)


def app_choices():
    text = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    m = re.search(r"options:\s*\[([^\]]*)\]", text)
    return [x.strip() for x in m.group(1).split(",")] if m else []


def worktree(branch):
    path = dev.am_root() / "studio" / "publish-worktree"
    if path.exists():
        run("git", "worktree", "remove", "--force", path, cwd=ROOT, check=False)
        shutil.rmtree(path, ignore_errors=True)
    run("git", "fetch", "-q", "origin", "main", cwd=ROOT)
    run("git", "worktree", "add", "-q", "-b", branch, path, "origin/main", cwd=ROOT)
    return path


def copy_mod(mod, wt):
    """The mod's tracked files as they are in this checkout (its version bump included)."""
    files = run("git", "ls-files", f"mods/{mod}", cwd=ROOT).splitlines()
    for rel in files:
        src, dst = ROOT / rel, wt / rel
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def pr_and_merge(wt, branch, title, body):
    run("git", "add", "-A", cwd=wt)
    if not run("git", "status", "--porcelain", cwd=wt):
        say("nothing changed against main")
        return True
    run("git", "commit", "-q", "-m", title, cwd=wt)
    run("git", "push", "-q", "-u", "origin", branch, cwd=wt)
    url = run("gh", "pr", "create", "-R", REPO, "--head", branch, "--title", title, "--body", body, cwd=wt)
    run("gh", "pr", "merge", url, "-R", REPO, "--auto", "--squash", cwd=wt)
    say(f"    waiting for the checks and auto-merge: {url}")
    for _ in range(90):  # 15 minutes
        state = run("gh", "pr", "view", url, "-R", REPO, "--json", "state", "-q", ".state", cwd=wt, check=False)
        if state == "MERGED":
            say("    merged")
            return True
        if state == "CLOSED":
            say("the pull request was closed without merging")
            return False
        time.sleep(10)
    say(f"not merged after 15 minutes: it merges by itself once its checks pass ({url})")
    return False


def dylib_for(mod, version):
    make = (dev.mod_dir(mod) / "Makefile").read_text(encoding="utf-8")
    name = re.search(r"^TWEAK_NAME\s*=\s*(\S+)", make, re.M).group(1)
    path = dev.am_root() / "builds" / f"{name}-{version}.dylib"
    if not path.is_file():
        say(f"    no {path.name} yet: building it")
        run(sys.executable, ROOT / "scripts" / "dev.py", "build", mod, cwd=ROOT)
    if not path.is_file():
        raise SystemExit(f"the build did not leave {path}")
    return path


def update(mod):
    meta = json.loads((dev.mod_dir(mod) / "mod.json").read_text(encoding="utf-8"))
    version = meta["version"]
    tweaks = json.loads((ROOT / "tweaks.json").read_text(encoding="utf-8"))
    is_tweak = tweak_entry(tweaks, mod) is not None
    if not is_tweak and (meta.get("personal") or mod not in app_choices()):
        raise SystemExit(f"{mod} is personal or not one of the cloud build's mods: not published from Studio")
    branch = f"publish-{mod}-{version}-{int(time.time())}"

    say(f"==> 1/{STEPS} {meta.get('name', mod)} {version}: a fresh copy of main with this checkout's mod files")
    wt = worktree(branch)
    copy_mod(mod, wt)
    old_tag = None
    if is_tweak:
        say(f"==> 2/{STEPS} the .dylib as release {mod}-v{version}")
        lib = dylib_for(mod, version)
        tag = f"{mod}-v{version}"
        if run("gh", "release", "view", tag, "-R", REPO, "--json", "tagName", "-q", ".tagName", check=False) != tag:
            run("gh", "release", "create", tag, lib, "-R", REPO, "--title", f"{meta.get('name', mod)} {version} (tweak)",
                "--notes", f"{meta.get('name', mod)} {version}: the tweak library's download (tweaks.json).")
        path = wt / "tweaks.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        entry = tweak_entry(data, mod)
        old = re.search(r"/releases/download/([^/]+)/", entry.get("debURL", ""))
        old_tag = old.group(1) if old and old.group(1) != tag else None
        entry.update(version=version, date=datetime.date.today().isoformat(),
                     debURL=f"https://github.com/{REPO}/releases/download/{tag}/{lib.name}")
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    else:
        say(f"==> 2/{STEPS} app mod: the cloud build makes the .ipa after the merge")
    say(f"==> 3/{STEPS} pull request with auto-merge")
    if not pr_and_merge(wt, branch, f"Publish: {meta.get('name', mod)} {version}",
                        f"From Mod Studio's Store tab: {meta.get('name', mod)} {version} from the local checkout."):
        return 1
    say(f"==> 4/{STEPS} " + ("the old release" if is_tweak else "Mods: build a mod, with Publish on"))
    if is_tweak:
        if old_tag:
            run("gh", "release", "delete", old_tag, "-R", REPO, "--yes", "--cleanup-tag", check=False)
        say(f"==> done: {meta.get('name', mod)} {version} is in the tweak library")
    else:
        before = run("gh", "run", "list", "-R", REPO, "--workflow", "build.yml", "--limit", "1", "--json", "databaseId",
                     "-q", ".[0].databaseId", check=False)
        run("gh", "workflow", "run", "build.yml", "-R", REPO, "-f", f"mod={mod}", "-f", "publish=true")
        # wait for it: the build commits to main at its end, so the next publish (queued behind this job) must
        # not merge into main while it runs ("Main changed before validation", 2026-10-23)
        run_id = None
        for _ in range(30):
            time.sleep(5)
            latest = run("gh", "run", "list", "-R", REPO, "--workflow", "build.yml", "--limit", "1", "--json", "databaseId",
                         "-q", ".[0].databaseId", check=False)
            if latest and latest != before:
                run_id = latest
                break
        if not run_id:
            say("==> done: the cloud build was started; it didn't show up to watch (Store tab, Publishing)")
            return 0
        say(f"    watching the cloud build (about 10 minutes): https://github.com/{REPO}/actions/runs/{run_id}")
        subprocess.run(["gh", "run", "watch", run_id, "-R", REPO, "--interval", "30"], capture_output=True)
        result = run("gh", "run", "view", run_id, "-R", REPO, "--json", "conclusion", "-q", ".conclusion", check=False)
        if result != "success":
            say(f"the cloud build ended {result or 'without a result'}: https://github.com/{REPO}/actions/runs/{run_id}")
            return 1
        say(f"==> done: {meta.get('name', mod)} {version} is published; the store lists it after its next build")
    return 0


def remove(mod):
    tweaks = json.loads((ROOT / "tweaks.json").read_text(encoding="utf-8"))
    is_tweak = tweak_entry(tweaks, mod) is not None
    branch = f"remove-{mod}-{int(time.time())}"
    say(f"==> 1/{STEPS} a fresh copy of main")
    wt = worktree(branch)
    say(f"==> 2/{STEPS} taking {mod} out of " + ("tweaks.json" if is_tweak else "releases.json and mods.json"))
    if is_tweak:
        path = wt / "tweaks.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["tweaks"] = [t for t in data["tweaks"] if t.get("id") != PREFIX + mod]
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    else:
        path = wt / "releases.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        if mod not in data:
            raise SystemExit(f"{mod} has no store record")
        del data[mod]
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        run(sys.executable, wt / "scripts" / "update_source.py", cwd=wt)
    say(f"==> 3/{STEPS} pull request with auto-merge")
    if not pr_and_merge(wt, branch, f"Store: remove {mod}", f"From Mod Studio's Store tab: {mod} taken out of the store."):
        return 1
    say(f"==> 4/{STEPS} " + ("its GitHub releases" if is_tweak else "done"))
    if is_tweak:
        tags = run("gh", "release", "list", "-R", REPO, "--json", "tagName", "-q", ".[].tagName", check=False).splitlines()
        for tag in tags:
            if tag.startswith(f"{mod}-v"):
                run("gh", "release", "delete", tag, "-R", REPO, "--yes", "--cleanup-tag", check=False)
    say(f"==> done: {mod} is out of the store" + ("" if is_tweak else " (its uploaded builds stay in private storage)"))
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=["update", "remove"])
    p.add_argument("mod")
    a = p.parse_args()
    if not (ROOT / "mods" / a.mod / "mod.json").is_file():
        raise SystemExit(f"no mod {a.mod}")
    try:
        return update(a.mod) if a.action == "update" else remove(a.mod)
    finally:
        path = dev.am_root() / "studio" / "publish-worktree"
        if path.exists():
            subprocess.run(["git", "worktree", "remove", "--force", str(path)], cwd=ROOT, capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
