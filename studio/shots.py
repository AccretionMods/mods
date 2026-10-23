"""Screenshot links for posts: the store's shots function (getaccretion.github.io supabase/functions/shots).

A phone screenshot comes from Accretion Link's own tool server (accretion-link-cli serve, which goes through the
running app, so there is one phone tunnel), is uploaded with the upload key, and its link is kept with the mod
(dev.py state "shots"); dev.py post puts the mod's live links in the post's Screenshots section. ShareX on the
PC uploads to the same function. Each link lives 3 months, 6 months or 1 year and can be renewed.
"""

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

FUNCTION = "https://bbcjpinhvlxocjxlyplt.supabase.co/functions/v1/shots"
TTLS = ("3m", "6m", "1y")
LINK_CLI = Path.home() / "GitHub" / "claude-plugins" / "ios-device" / "packaging" / "dist" / "AccretionLink" / "accretion-link-cli.exe"


class ShotsError(Exception):
    pass


def upload_key(repo_root):
    key = os.environ.get("ACCRETION_UPLOAD_KEY", "").strip()
    if not key:
        try:
            key = Path(repo_root, ".upload-key").read_text(encoding="utf-8").strip()
        except OSError:
            pass
    if not key:
        raise ShotsError("no upload key on this PC (.upload-key or ACCRETION_UPLOAD_KEY)")
    return key


def _call(repo_root, op, body=None, data=None, content_type="application/json", query="", timeout=60):
    payload = data if data is not None else json.dumps(body or {}).encode()
    req = urllib.request.Request(f"{FUNCTION}?op={op}{query}", data=payload, method="POST",
                                 headers={"x-upload-key": upload_key(repo_root), "Content-Type": content_type,
                                          "User-Agent": "AccretionModStudio/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            message = json.loads(e.read()).get("message")
        except ValueError:
            message = None
        raise ShotsError(message or f"the store answered {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ShotsError(f"the store didn't answer: {getattr(e, 'reason', e)}") from e


def listing(repo_root):
    return _call(repo_root, "list").get("shots", [])


def renew(repo_root, shot_id, ttl):
    if ttl not in TTLS:
        raise ShotsError("pick 3 months, 6 months or 1 year")
    return _call(repo_root, "renew", {"id": shot_id, "ttl": ttl})


def delete(repo_root, shot_id):
    return _call(repo_root, "delete", {"id": shot_id})


def upload_file(repo_root, path, ttl, label=""):
    if ttl not in TTLS:
        raise ShotsError("pick 3 months, 6 months or 1 year")
    types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
             ".webp": "image/webp", ".mp4": "video/mp4", ".mov": "video/quicktime"}
    kind = types.get(Path(path).suffix.lower())
    if not kind:
        raise ShotsError("only PNG, JPEG, GIF, WebP, MP4 or MOV")
    from urllib.parse import quote
    return _call(repo_root, "upload", data=Path(path).read_bytes(), content_type=kind,
                 query=f"&ttl={ttl}&label={quote(label[:120])}", timeout=180)


def phone_screenshot():
    """A full-size PNG of the phone's screen, through Accretion Link's tool server; its path."""
    if not LINK_CLI.is_file():
        raise ShotsError(f"Accretion Link isn't built at {LINK_CLI}")
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "mod-studio", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "screenshot", "arguments": {"return_image": False}}},
    ]
    proc = subprocess.Popen([str(LINK_CLI), "serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
    try:
        for m in messages:
            proc.stdin.write(json.dumps(m) + "\n")
            proc.stdin.flush()
        deadline = time.monotonic() + 60
        for line in proc.stdout:
            if time.monotonic() > deadline:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") != 2:
                continue
            result = msg.get("result") or {}
            text = " ".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
            if msg.get("error") or result.get("isError"):
                raise ShotsError((msg.get("error") or {}).get("message") or text or "the screenshot failed")
            if text.startswith("Saved "):
                path = text[len("Saved "):].split(" (")[0].strip()
                if Path(path).is_file():
                    return path
            raise ShotsError(f"no screenshot file in the answer: {text[:160]}")
        raise ShotsError("Accretion Link didn't answer (is the phone connected?)")
    finally:
        proc.kill()


def phone_shot_for(repo_root, mod, ttl, load_state, save_state):
    """Screenshot the phone, upload it, keep the link with the mod; the new shot."""
    path = phone_screenshot()
    shot = upload_file(repo_root, path, ttl, label=f"{mod} phone")
    state = load_state(mod)
    state.setdefault("shots", []).insert(0, {"id": shot["id"], "url": shot["url"], "expires_at": shot["expires_at"],
                                            "taken": int(time.time())})
    save_state(mod, state)
    return shot
