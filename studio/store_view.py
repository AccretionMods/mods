"""Mod Studio's AccretionWeb tab: the store, our mods in it, the admin numbers, our endpoints and our keys, in one place.

- Admin link: Studio links once like a game's cloud saves (the store's `saves` function, bundle "accretion.studio";
  the owner approves the code at getaccretion.app/#/link signed in as an admin). The token stays in
  AM_ROOT/studio/admin.json on this PC and goes only to the store's own functions.
- Public data: the store's data/stats.json, the mods catalogue (mods.json) and the tweak library.
- Admin numbers: the store's `studio` function (accounts, downloads, app stats, community, searches, share links,
  cloud saves, builds).
- Endpoints: status and time of every service the mods, the app and the store depend on.
- Keys: GitHub secrets' names and ages (never values), and live checks of the upload key, GitHub, plumesign, the
  App Store account and the admin link.

Everything is read only; nothing here changes the store.
"""

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SUPABASE = "https://bbcjpinhvlxocjxlyplt.supabase.co"
FUNCTIONS = SUPABASE + "/functions/v1"
SITE = "https://getaccretion.app"
STUDIO_BUNDLE = "accretion.studio"
REPOS = ["GETAccretion/getaccretion.github.io", "AccretionMods/mods", "GETAccretion/accretion-app"]
SECRET_OLD_DAYS = 180  # a secret not rotated for half a year is flagged
WORKFLOWS = [  # (repo, workflow file, label)
    ("GETAccretion/getaccretion.github.io", "build.yml", "Store: build and publish"),
    ("GETAccretion/getaccretion.github.io", "community-setup.yml", "Store: set up backend"),
    ("GETAccretion/getaccretion.github.io", "health.yml", "Store: health check"),
    ("GETAccretion/getaccretion.github.io", "links.yml", "Store: check links"),
    ("GETAccretion/getaccretion.github.io", "discover.yml", "Store: discover sources"),
    ("GETAccretion/getaccretion.github.io", "proxy.yml", "Store: download proxy"),
    ("GETAccretion/getaccretion.github.io", "ci.yml", "Store: checks"),
    ("AccretionMods/mods", "build.yml", "Mods: build a mod"),
    ("AccretionMods/mods", "publish.yml", "Mods: publish a mod"),
    ("AccretionMods/mods", "watch.yml", "Mods: watch base apps"),
    ("AccretionMods/mods", "inventory.yml", "Mods: credential snapshot"),
    ("GETAccretion/accretion-app", "release.yml", "App: build"),
    ("GETAccretion/accretion-app", "ship.yml", "App: ship"),
]


def _http(method, url, body=None, headers=None, timeout=15):
    """(status, seconds, json or text or None); status 0 when the network failed."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"User-Agent": "AccretionModStudio/1",
                                                                       **({"Content-Type": "application/json"} if data else {}),
                                                                       **(headers or {})})
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, time.monotonic() - start, str(getattr(e, "reason", e))
    took = time.monotonic() - start
    try:
        return status, took, json.loads(raw)
    except ValueError:
        return status, took, raw[:2000].decode("utf-8", "replace")


# ---------------------------------------------------------------- the admin link

def _admin_file(root):
    return Path(root) / "studio" / "admin.json"


def admin_state(root):
    try:
        return json.loads(_admin_file(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_admin(root, state):
    path = _admin_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, path)


def link_start(root):
    status, _, body = _http("POST", FUNCTIONS + "/saves?op=link-start",
                            {"bundle": STUDIO_BUNDLE, "game": "Mod Studio", "device": os.environ.get("COMPUTERNAME", "this PC")})
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError((body or {}).get("error") if isinstance(body, dict) else "Accretion did not answer")
    state = admin_state(root)
    state["pending"] = body["code"]
    _save_admin(root, state)
    return {"code": body["code"], "approve_url": body["approve_url"]}


def link_poll(root):
    state = admin_state(root)
    code = state.get("pending")
    if not code:
        return {"status": "linked" if state.get("token") else "none", "account": state.get("account")}
    status, _, body = _http("POST", FUNCTIONS + "/saves?op=link-poll", {"code": code})
    if status == 200 and isinstance(body, dict) and body.get("status") == "linked":
        state = {"token": body["token"], "account": body.get("account"), "linked_at": int(time.time())}
        _save_admin(root, state)
        return {"status": "linked", "account": state["account"]}
    if isinstance(body, dict) and body.get("status") == "expired":
        state.pop("pending", None)
        _save_admin(root, state)
        return {"status": "expired"}
    return {"status": "waiting", "code": code}


def unlink(root):
    state = admin_state(root)
    if state.get("token"):
        _http("POST", FUNCTIONS + "/saves?op=unlink", {}, {"x-save-token": state["token"]})
    _save_admin(root, {})
    return {"status": "none"}


def admin_summary(root, apps):
    token = admin_state(root).get("token")
    if not token:
        return {"linked": False}
    status, took, body = _http("POST", FUNCTIONS + "/studio?op=summary", {"apps": apps}, {"x-save-token": token}, timeout=40)
    if status == 401:  # forgotten on the site: forget it here too
        _save_admin(root, {})
        return {"linked": False, "error": "The admin link was removed: link again"}
    if status != 200 or not isinstance(body, dict):
        return {"linked": True, "error": (body.get("error") if isinstance(body, dict) else None) or f"Accretion answered {status}"}
    return {"linked": True, "took": round(took, 2), **body}


def _studio(root, op, body, timeout=40):
    token = admin_state(root).get("token")
    if not token:
        raise RuntimeError("Link Mod Studio first (AccretionWeb tab)")
    status, _, answer = _http("POST", f"{FUNCTIONS}/studio?op={op}", body, {"x-save-token": token}, timeout=timeout)
    if status != 200 or not isinstance(answer, dict):
        raise RuntimeError((answer.get("error") if isinstance(answer, dict) else None) or f"Accretion answered {status}")
    return answer


def moderation(root):
    """The open reports, recent and hidden posts, banned users and the team (admin link only)."""
    return _studio(root, "moderation", {})


ACTIONS = {"hide", "resolve", "ban", "role", "supporter"}


def act(root, body):
    """One moderation action: hide/unhide a post, resolve or dismiss a report, ban/unban, role, supporter."""
    action = str(body.get("action", ""))
    if action not in ACTIONS or not str(body.get("target", "")):
        raise RuntimeError("Unknown moderation action")
    clean = {"action": action, "target": str(body["target"])[:100]}
    if isinstance(body.get("flag"), bool):
        clean["flag"] = body["flag"]
    if isinstance(body.get("value"), str):
        clean["value"] = body["value"][:20]
    return _studio(root, "act", clean, timeout=20)


# ---------------------------------------------------------------- the store's public data

def public(repo_root):
    def get(url):
        status, _, body = _http("GET", url)
        return body if status == 200 and not isinstance(body, str) else None
    with ThreadPoolExecutor(3) as pool:
        stats, catalogue, tweaks = pool.map(get, [
            SITE + "/data/stats.json",
            "https://raw.githubusercontent.com/AccretionMods/mods/main/mods.json",
            "https://raw.githubusercontent.com/AccretionMods/mods/main/tweaks.json"])
    ours = []
    for app in (catalogue or {}).get("apps", []):
        mod = app.get("accretionMod") or {}
        ours.append({"kind": "app", "id": app.get("bundleIdentifier"), "name": app.get("name"), "mod": mod.get("id"),
                     "live": mod.get("modVersion") or app.get("version"), "date": app.get("versionDate"), "size": app.get("size")})
    prefix = "io.github.accretionmods."
    for t in (tweaks or {}).get("tweaks", []):
        if str(t.get("id", "")).startswith(prefix):
            ours.append({"kind": "tweak", "id": t["id"], "name": t.get("name"), "mod": t["id"][len(prefix):],
                         "live": t.get("version"), "date": t.get("date"), "size": t.get("size")})
    local = {}
    for meta_path in Path(repo_root, "mods").glob("*/mod.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        local[meta_path.parent.name] = {"version": meta.get("version"), "title": meta.get("name") or meta_path.parent.name,
                                        "personal": bool(meta.get("personal"))}
    listed = {o["mod"] for o in ours}
    for o in ours:
        o["here"] = (local.get(o["mod"]) or {}).get("version")
    unlisted = [{"mod": m, **v} for m, v in sorted(local.items()) if m not in listed]
    return {"stats": stats, "ours": ours, "unlisted": unlisted}


# ---------------------------------------------------------------- endpoints

def _anon_key():
    status, _, body = _http("GET", SITE + "/config.js")
    if status == 200 and isinstance(body, str) and '"supabaseKey": "' in body:
        return body.split('"supabaseKey": "', 1)[1].split('"', 1)[0]
    return ""


def endpoints(root):
    """Every service, its answer and time. ok: the answer the service gives when it works."""
    anon = _anon_key()
    token = admin_state(root).get("token")
    checks = [
        ("Store site", "GET", SITE + "/", None, {}, {200}),
        ("Store data (stats.json)", "GET", SITE + "/data/stats.json", None, {}, {200}),
        ("Supabase database (REST)", "GET", SUPABASE + "/rest/v1/app_trending?select=app_id&limit=1", None, {"apikey": anon}, {200}),
        ("Supabase sign-in (Auth)", "GET", SUPABASE + "/auth/v1/health", None, {"apikey": anon}, {200}),
        ("Function: saves", "POST", FUNCTIONS + "/saves?op=link-poll", {"code": "AAA-AAAA"}, {}, {200}),
        ("Function: studio", "POST", FUNCTIONS + "/studio?op=summary", {}, {"x-save-token": token} if token else {}, {200, 401}),
        ("Function: download", "GET", FUNCTIONS + "/download", None, {}, {400, 401, 404}),
        ("Function: upload", "POST", FUNCTIONS + "/upload?share=list", {}, {}, {401, 403}),
        ("Function: source", "GET", FUNCTIONS + "/source?name=store", None, {}, {200, 400, 401, 403}),
        ("Function: manifest", "GET", FUNCTIONS + "/manifest", None, {}, {200, 400, 404}),
        ("Download proxy (dl.getaccretion.app)", "GET", "https://dl.getaccretion.app/robots.txt", None, {}, {200}),
        ("Mods catalogue (GitHub raw)", "GET", "https://raw.githubusercontent.com/AccretionMods/mods/main/mods.json", None, {}, {200}),
        ("Tweak library (GitHub raw)", "GET", "https://raw.githubusercontent.com/AccretionMods/mods/main/tweaks.json", None, {}, {200}),
        ("App source (GitHub raw)", "GET", "https://raw.githubusercontent.com/GETAccretion/getaccretion.github.io/main/source.json", None, {}, {200}),
    ]

    def run(check):
        name, method, url, body, headers, good = check
        status, took, _ = _http(method, url, body, headers, timeout=12)
        return {"name": name, "url": url.split("?")[0], "status": status, "ms": round(took * 1000),
                "ok": status in good, "slow": took > 2.5}
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(run, checks))
    return {"checked_at": int(time.time()), "endpoints": results}


# ---------------------------------------------------------------- keys and sign-ins

def _gh(*args, timeout=30):
    gh = shutil.which("gh")
    if not gh:
        return None, "gh is not installed"
    try:
        r = subprocess.run([gh, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)
    return (r.stdout if r.returncode == 0 else None), (r.stderr.strip() or None)


def keys(root, repo_root, plumesign_path=None, fetch_account=None):
    out = {"checked_at": int(time.time()), "secrets": [], "checks": []}
    now = time.time()
    for repo in REPOS:  # names and dates only: values never leave GitHub
        text, err = _gh("secret", "list", "-R", repo, "--json", "name,updatedAt")
        if text is None:
            out["secrets"].append({"repo": repo, "error": err})
            continue
        for s in json.loads(text):
            try:
                age = int((now - time.mktime(time.strptime(s["updatedAt"][:19], "%Y-%m-%dT%H:%M:%S"))) / 86400)
            except (KeyError, ValueError):
                age = None
            out["secrets"].append({"repo": repo, "name": s["name"], "updated": s.get("updatedAt"), "age_days": age,
                                   "old": age is not None and age > SECRET_OLD_DAYS})

    def check(name, ok, detail):
        out["checks"].append({"name": name, "ok": bool(ok), "detail": detail})

    text, err = _gh("auth", "status")
    lines = [ln.strip().lstrip("✓-* ").strip() for ln in (text or err or "").splitlines() if "account" in ln.lower() or "token scopes" in ln.lower()]
    check("GitHub CLI sign-in", text is not None, " · ".join(lines[:2]) or (err or "signed in"))
    key = os.environ.get("ACCRETION_UPLOAD_KEY", "").strip()
    try:
        key = key or Path(repo_root, ".upload-key").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    if key:
        status, _, _ = _http("POST", FUNCTIONS + "/upload?share=list", {}, {"x-upload-key": key})
        check("Upload key (.upload-key)", status == 200, "accepted by the store" if status == 200 else f"refused ({status})")
    else:
        check("Upload key (.upload-key)", False, "not on this PC (publishing goes through GitHub's secret)")
    check("plumesign (Apple ID signing)", bool(plumesign_path and Path(plumesign_path).is_file()),
          str(plumesign_path) if plumesign_path else "not found")
    if fetch_account:
        try:
            account = fetch_account()
            signed = bool(account and account.get("signed_in"))
            user = (account or {}).get("user")
            detail = f"signed in{' as ' + str(user) if user else ''}" if signed else (
                "signed out (the decrypt site is up)" if (account or {}).get("up") else "signed out; the decrypt site is not answering")
            check("App Store account (fetch)", signed, detail)
        except Exception as e:  # noqa: BLE001 - any failure is a failed check, shown as text
            check("App Store account (fetch)", False, str(e)[:160])
    state = admin_state(root)
    if state.get("token"):
        status, _, body = _http("POST", FUNCTIONS + "/studio?op=summary", {"apps": []}, {"x-save-token": state["token"]}, timeout=40)
        check("Accretion admin link", status == 200, f"{state.get('account')} · {'admin' if status == 200 else (body.get('error') if isinstance(body, dict) else status)}")
    else:
        check("Accretion admin link", False, "not linked: Link on the AccretionWeb tab")
    return out


def runs():
    out = []

    def one(w):
        repo, file, label = w
        text, err = _gh("run", "list", "-R", repo, "--workflow", file, "--limit", "1",
                        "--json", "status,conclusion,createdAt,displayTitle,url")
        rows = json.loads(text) if text else []
        r = rows[0] if rows else {}
        return {"label": label, "repo": repo, "status": r.get("status"), "conclusion": r.get("conclusion"),
                "at": r.get("createdAt"), "title": r.get("displayTitle"), "url": r.get("url"), "error": None if text else err}
    with ThreadPoolExecutor(8) as pool:
        out = list(pool.map(one, WORKFLOWS))
    return {"runs": out}
