"""Inbox auto-pipeline: a new base .ipa in the intake folder becomes a build, with no manual steps.

  python3 scripts/intake_auto.py [folder]            report only: what each .ipa would do
  python3 scripts/intake_auto.py --apply [folder]    do it

For each .ipa in the folder (default INTAKE, else C:/AccretionMods/intake):
  1. Find the mod whose target_bundle_id is the .ipa's bundle ID and whose base is private.
     No such mod: the file stays where it is (scripts/intake.py handles other files).
  2. Same sha256 as the mod's current base: already known, moved to the done folder.
  3. Older than mod.json's tested_app_version: held.
  4. The mod has hooks.json (methods its tweak hooks by name, "engine": "unity-il2cpp"): every
     class and method must still be named in the game's IL2CPP metadata
     (Data/Managed/Metadata/global-metadata.dat). A missing one, or no readable metadata: the file
     is held and a report says what to fix. Argument counts can't be read from the metadata; the
     tweak itself skips a hook whose method isn't found at runtime and logs it.
  5. Otherwise: upload the .ipa to private storage (scripts/base.py), update base.sha256 and
     tested_app_version in mod.json, validate, commit only that file to main, push, and start the
     Build mod workflow (gh). A personal mod's build stays in private storage; the run's summary
     prints the command to fetch it.

Held files move to INTAKE_HELD (default C:/AccretionMods/held) next to "<file>.txt" with the reason.
To retry after fixing hooks.json or Tweak.x, move the .ipa back to the intake folder.
Files modified in the last 2 minutes are skipped (still syncing). One run at a time (lock file).
State and log: C:/AccretionMods/auto-state.json and auto.log (override the folder with ACCRETION_DIR).
Uploading uses the upload key through scripts/base.py; this script never reads or prints it.
Schedule it with scripts/register_intake_auto.ps1.
"""

import datetime
import glob
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

WORK = os.environ.get("ACCRETION_DIR") or "C:/AccretionMods"
INTAKE = os.environ.get("INTAKE") or f"{WORK}/intake"
DONE = os.environ.get("INTAKE_DONE") or f"{WORK}/done"
HELD = os.environ.get("INTAKE_HELD") or f"{WORK}/held"
STATE = f"{WORK}/auto-state.json"
LOG = f"{WORK}/auto.log"
LOCK = f"{WORK}/auto.lock"
SETTLE_SECONDS = 120
METADATA = re.compile(r"Payload/[^/]+\.app/Data/Managed/Metadata/global-metadata\.dat")
INFO_PLIST = re.compile(r"Payload/[^/]+\.app/Info\.plist")


def log(msg):
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line)
    try:
        os.makedirs(WORK, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def version_key(v):
    return tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.]", str(v)))


def ipa_info(path):
    """(bundle ID, app version, global-metadata.dat bytes or None) of an .ipa."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        plists = [n for n in names if INFO_PLIST.fullmatch(n)]
        if not plists:
            raise ValueError("not an app (no Payload/*.app/Info.plist)")
        info = plistlib.loads(z.read(plists[0]))
        meta = next((n for n in names if METADATA.fullmatch(n)), None)
        data = z.read(meta) if meta else None
    return (
        info.get("CFBundleIdentifier"),
        str(info.get("CFBundleShortVersionString") or "0"),
        data,
    )


def missing_hooks(metadata, hooks):
    """Names (Class.method, Namespace, assembly) from hooks that the metadata no longer contains.
    Strings in global-metadata.dat are NUL-terminated, so a whole-name match is b'\\0name\\0'."""
    if metadata is None:
        return [
            "(no Data/Managed/Metadata/global-metadata.dat in the .ipa: can't check the hooks)"
        ]
    blob = b"\0" + metadata

    def has(name):
        return (b"\0" + name.encode() + b"\0") in blob

    out = []
    for h in hooks:
        if not has(h["class"]):
            out.append(f"class {h['class']} ({h['method']})")
        elif not has(h["method"]):
            out.append(f"method {h['class']}.{h['method']}")
        elif h.get("namespace") and not has(h["namespace"]):
            out.append(f"namespace {h['namespace']} ({h['class']}.{h['method']})")
    return out


def find_mod(bundle):
    for path in sorted(glob.glob(os.path.join(ROOT, "mods", "*", "mod.json"))):
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
        if meta.get("target_bundle_id") == bundle and (meta.get("base") or {}).get(
            "private"
        ):
            return os.path.basename(os.path.dirname(path)), meta
    return None, None


def patch_mod_json(text, old_sha, new_sha, version):
    """Swap base.sha256 and tested_app_version in mod.json's text, leaving the rest untouched."""
    if text.count(f'"sha256": "{old_sha}"') != 1:
        raise ValueError("base sha256 not found exactly once in mod.json")
    text = text.replace(f'"sha256": "{old_sha}"', f'"sha256": "{new_sha}"')
    text, n = re.subn(
        r'("tested_app_version":\s*")[^"]*(")', rf"\g<1>{version}\g<2>", text, count=1
    )
    if n != 1:
        raise ValueError("tested_app_version not found in mod.json")
    return text


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    os.makedirs(WORK, exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def run(cmd, **kw):
    return subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, check=False, **kw
    )


def move(path, folder):
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, os.path.basename(path))
    if os.path.exists(dest):
        os.remove(dest)
    shutil.move(path, dest)
    return dest


def hold(path, reason, apply):
    log(f"HOLD {os.path.basename(path)}: {reason}")
    if apply:
        dest = move(path, HELD)
        with open(dest + ".txt", "w", encoding="utf-8") as f:
            f.write(
                reason
                + "\n\nTo retry: fix the mod (hooks.json / Tweak.x), then move the .ipa back to the intake folder.\n"
            )


def publishable(meta):
    """Only public mods are committed, pushed and built in the cloud."""
    return meta.get("personal") is not True


def git_publish(mod, meta, version):
    if not publishable(meta):
        raise RuntimeError(f"{mod} is personal and is never committed")
    if run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip() != "main":
        raise RuntimeError("the repo isn't on the main branch")
    rel = f"mods/{mod}/mod.json"
    run(["git", "add", "--", rel])
    msg = f"Update {meta.get('display_name') or mod} for {version}"
    r = run(["git", "commit", "--only", "-m", msg, "--", rel])
    if r.returncode:
        raise RuntimeError(f"git commit failed: {r.stdout.strip()} {r.stderr.strip()}")
    # Use the same exact-commit checks as cloud publishing. The local CLI already owns
    # this credential; keep it inside the child process environment, never in logs/files.
    from checked_push import publish

    local_token = not os.environ.get("GH_TOKEN", "").strip()
    if local_token:
        credential = run(["gh", "auth", "token"])
        if credential.returncode or not credential.stdout.strip():
            raise RuntimeError("GitHub sign-in is required for checked publication")
        os.environ["GH_TOKEN"] = credential.stdout.strip()
    try:
        publish("mods", repo="AccretionMods/mods", cwd=ROOT)
    finally:
        if local_token:
            os.environ.pop("GH_TOKEN", None)


def start_build(mod):
    r = run(
        ["gh", "workflow", "run", "build.yml", "-f", f"mod={mod}", "-f", "publish=true"]
    )
    if r.returncode:
        raise RuntimeError(f"gh workflow run failed: {r.stderr.strip()}")
    time.sleep(5)
    r = run(
        [
            "gh",
            "run",
            "list",
            "--workflow",
            "build.yml",
            "--limit",
            "1",
            "--json",
            "url",
            "-q",
            ".[0].url",
        ]
    )
    return r.stdout.strip()


def process(path, state, apply):
    name = os.path.basename(path)
    if time.time() - os.path.getmtime(path) < SETTLE_SECONDS:
        log(f"SKIP {name}: modified in the last {SETTLE_SECONDS}s, still syncing")
        return
    try:
        bundle, version, metadata = ipa_info(path)
    except (zipfile.BadZipFile, ValueError, OSError) as e:
        log(f"SKIP {name}: {e}")
        return
    mod, meta = find_mod(bundle)
    if not mod:
        log(
            f"SKIP {name}: {bundle} {version} has no mod with a private base (leaving it)"
        )
        return
    from base import sha256_of

    sha = sha256_of(path)
    if state.get(sha, {}).get("status") == "built":
        log(f"DONE {name}: already built ({state[sha]['mod']} {version})")
        if apply:
            move(path, DONE)
        return
    old = meta["base"]["sha256"]
    if sha == old:
        log(f"KNOWN {name}: it is {mod}'s current base")
        if apply:
            move(path, DONE)
        return
    tested = str(meta.get("tested_app_version") or "0")
    if version_key(version) < version_key(tested):
        return hold(
            path,
            f"{name} is app {version}, older than {mod}'s tested_app_version {tested}",
            apply,
        )
    hooks_file = os.path.join(ROOT, "mods", mod, "hooks.json")
    if os.path.isfile(hooks_file):
        with open(hooks_file, encoding="utf-8") as f:
            hooks = json.load(f).get("hooks", [])
        gone = missing_hooks(metadata, hooks)
        if gone:
            return hold(
                path,
                f"{mod} {version}: hooks missing in the new version:\n  "
                + "\n  ".join(gone),
                apply,
            )
    else:
        log(f"NOTE {mod} has no hooks.json: nothing to check")
    if not publishable(meta):
        # A personal mod is never committed, pushed or built in the cloud: its folder stays
        # out of git, so the new base is only reported for a local build.
        log(
            f"PERSONAL {name}: {mod} is personal; build it locally with "
            f"scripts/local_ipa.sh {mod} <this file> (nothing committed or uploaded)"
        )
        return
    log(f"READY {name}: {mod} {tested} -> {version}")
    if not apply:
        return
    from base import put

    put(path)  # uploads by sha256 and prints it (idempotent)
    mod_json = os.path.join(ROOT, "mods", mod, "mod.json")
    with open(mod_json, encoding="utf-8", newline="") as f:
        text = f.read()
    with open(mod_json, "w", encoding="utf-8", newline="") as f:
        f.write(patch_mod_json(text, old, sha, version))
    check = run([sys.executable, os.path.join("scripts", "validate_mods.py"), mod])
    if check.returncode:
        with open(mod_json, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        raise RuntimeError(
            f"validation failed, mod.json restored: {check.stdout.strip()}"
        )
    git_publish(mod, meta, version)
    url = start_build(mod)
    log(f"BUILD {mod} {version} started: {url or 'see the Actions tab'}")
    state[sha] = {
        "status": "built",
        "mod": mod,
        "version": version,
        "run": url,
        "at": f"{datetime.datetime.now():%Y-%m-%d %H:%M}",
    }
    save_state(state)
    move(path, DONE)


def lock():
    os.makedirs(WORK, exist_ok=True)
    if os.path.exists(LOCK) and time.time() - os.path.getmtime(LOCK) < 2 * 3600:
        return False
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    return True


def main(argv):
    apply = "--apply" in argv
    args = [a for a in argv if not a.startswith("--")]
    if len(args) > 1 or any(a.startswith("--") and a != "--apply" for a in argv):
        sys.exit(__doc__)
    folder = args[0] if args else INTAKE
    if not lock():
        log("another run is in progress")
        return 0
    status = 0
    try:
        files = sorted(
            p
            for p in glob.glob(os.path.join(folder, "*"))
            if p.lower().endswith(".ipa")
        )
        if not files:
            return 0
        state = load_state()
        for path in files:
            try:
                process(path, state, apply)
            except Exception as e:  # noqa: BLE001 one bad file must not stop the rest
                log(f"ERROR {os.path.basename(path)}: {e}")
                status = 1
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
