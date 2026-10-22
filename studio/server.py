"""Mod Studio: a local web page for the mod dev loop (scripts/dev.py), served by the standard library.

Usage: python3 studio/server.py [--port 8765] [--host 127.0.0.1] [--no-open]
       scripts/am.ps1 ui   (same thing from PowerShell)

Quick commands (find, fields, add, pin, call, tweak, remove, gen, suggest, mark, status) run in this process
through dev.py's functions and answer at once. Long ones (dump, build, sign, install, run, log, update, new,
fetch) run as jobs: a `python dev.py <command> ...` subprocess whose output the page streams. The Apple ID sign-in
(2FA) can't be answered from a page: do it once from a console, plumesign keeps the session.

API (JSON): GET /api/mods, /api/mods/<mod>, /api/mods/<mod>/find?q=&limit=, /api/mods/<mod>/fields?class=,
/api/mods/<mod>/suggest, /api/bases, /api/fetch/account, /api/fetch/search?q=&region=&platform=;
POST /api/fetch/login {"email", "password"} (passed to the decrypt site, never saved), /api/fetch/logout; POST /api/mods/<mod>/<add|pin|call|tweak|remove|gen|mark> with the
command's options as JSON (the same names as dev.py's flags, dashes as underscores); POST /api/jobs
{"command": "build", "mod": "...", "args": ["--ipa"]}, GET /api/jobs/<id>?since=N, POST /api/jobs/<id>/stop.

Binds 127.0.0.1: only this PC. --host 0.0.0.0 shows it to the Wi-Fi with no login, for a trusted network.
"""

import argparse
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "scripts"))

import dev
import dev_learn
import gen_tweaks
import ipa_fetch

LOCK = threading.Lock()  # dev.py writes files and caches; one quick command at a time
JOBS = {}
JOB_COMMANDS = {
    "dump",
    "build",
    "sign",
    "install",
    "run",
    "log",
    "update",
    "new",
    "check",
    "fetch",
}
QUICK = {"add", "pin", "call", "tweak", "remove", "gen", "mark"}
MOD_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class Job:
    def __init__(self, command, argv):
        self.id = f"{int(time.time() * 1000):x}"
        self.command = command
        self.argv = argv
        self.lines = []
        self.status = "running"
        self.code = None
        self.started = time.time()
        self.proc = None

    def run(self):
        cmd = [
            sys.executable,
            str(HERE.parent / "scripts" / "dev.py"),
            self.command,
        ] + self.argv
        env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1", AM_REPO=str(ROOT))
        # a server started outside a login shell may lack uv and pymobiledevice3 on PATH
        local_bin = str(Path.home() / ".local" / "bin")
        if local_bin not in env.get("PATH", ""):
            env["PATH"] = local_bin + os.pathsep + env.get("PATH", "")
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                cwd=str(ROOT),
            )
        except OSError as e:
            self.lines.append(f"could not start: {e}")
            self.status, self.code = "failed", -1
            return
        for line in self.proc.stdout:
            self.lines.append(line.rstrip("\n"))
        self.code = self.proc.wait()
        self.status = (
            "done"
            if self.code == 0
            else ("stopped" if self.status == "stopping" else "failed")
        )

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.status = "stopping"
            self.proc.kill()

    def view(self, since=0):
        return {
            "id": self.id,
            "command": self.command,
            "args": self.argv,
            "status": self.status,
            "code": self.code,
            "started": self.started,
            "lines": self.lines[since:],
            "next": len(self.lines),
        }


def start_job(command, argv):
    if command not in JOB_COMMANDS:
        raise dev.DevError(f"{command} is not a job command")
    for a in argv:
        if not isinstance(a, str) or "\n" in a:
            raise dev.DevError("bad argument")
    job = Job(command, argv)
    JOBS[job.id] = job
    threading.Thread(target=job.run, daemon=True).start()
    return job


def capture(fn, *args):
    """Run a dev.py function, returning (exit code, printed text); DevError becomes code 1 with its message."""
    out = io.StringIO()
    with LOCK, contextlib.redirect_stdout(out):
        try:
            code = fn(*args) or 0
        except dev.DevError as e:
            print(f"error: {e}")
            code = 1
    return code, out.getvalue()


def mod_summary(name):
    folder = ROOT / "mods" / name
    meta = dev.read_json(folder / "mod.json")
    state = dev.load_state(name)
    tweaks = (
        dev.read_json(folder / "tweaks.json")
        if (folder / "tweaks.json").is_file()
        else None
    )
    hooks = dev.load_hooks(folder)["hooks"]
    dump_dir = state.get("dump_dir")
    return {
        "name": name,
        "title": meta.get("display_name") or meta["name"],
        "version": meta["version"],
        "target": meta["target_bundle_id"],
        "app_version": meta.get("tested_app_version", ""),
        "personal": bool(meta.get("personal")),
        "has_tweaks": tweaks is not None,
        "tweaks": len(tweaks["tweaks"]) if tweaks else 0,
        "hooks": len(hooks),
        "hooks_jit": sum(1 for h in hooks if h.get("needs_jit")),
        "base_ipa": state.get("base_ipa"),
        "dump": bool(dump_dir and (Path(dump_dir) / "out" / "dump.cs").is_file()),
        "last_dylib": state.get("last_dylib"),
        "last_ipa": state.get("last_ipa"),
    }


def mod_detail(name):
    folder = ROOT / "mods" / name
    meta = dev.read_json(folder / "mod.json")
    data = mod_summary(name)
    data["tweaks_json"] = (
        dev.read_json(folder / "tweaks.json")
        if (folder / "tweaks.json").is_file()
        else None
    )
    data["hooks_json"] = dev.load_hooks(folder)["hooks"]
    try:
        data["newer_bases"] = [
            {"path": str(p), "version": v} for p, v in dev.newer_bases(meta)
        ]
    except Exception:  # noqa: BLE001 - a bad .ipa in Downloads must not break the page
        data["newer_bases"] = []
    return data


def method_row(m):
    return {
        "full_name": m.full_name,
        "class": m.class_name,
        "method": m.name,
        "namespace": m.namespace,
        "params": m.params,
        "args": m.args,
        "return_type": m.return_type,
        "kind": m.kind,
        "assembly": m.assembly,
        "rva": m.rva,
        "slot": m.slot,
        "needs_jit": m.needs_jit,
        "static": bool(re.match(r"^(?:\w+\s+)*static\s", m.signature)),
        "signature": m.signature,
    }


def find(name, query, limit, class_name=None):
    found = dev.dump_methods(name)
    if class_name:
        rows = [m for m in found if m.class_name == class_name]
    else:
        rx = re.compile(query, re.IGNORECASE)
        rows = [m for m in found if rx.search(m.full_name)]
    return {"count": len(rows), "methods": [method_row(m) for m in rows[:limit]]}


def fields(name, class_name):
    return {
        "fields": [
            {"offset": o, "static": s, "type": t, "name": n}
            for o, s, t, n in dev.fields_of(name, class_name)
        ]
    }


def suggest(name, limit):
    folder = ROOT / "mods" / name
    store = dev_learn.load(dev.patterns_path())
    used = {gen_tweaks.hook_name(h) for h in dev.load_hooks(folder)["hooks"]}
    rows = dev_learn.suggest(store, dev.dump_methods(name), used, limit)
    return {
        "learned": len(store["patterns"]),
        "rows": [
            {
                "score": sc,
                "method": method_row(m),
                "exact": exact,
                "pattern": dev_learn.describe_pattern(p),
                "title": dev_learn.title_for(m, p),
                "action": p.get("action"),
                "value": p.get("value"),
                "slider": p.get("slider"),
            }
            for sc, m, p, exact in rows
        ],
    }


def fetch_client():
    return ipa_fetch.Client(dev.am_root())


def bases():
    """Decrypted .ipa files in intake/ and Downloads, for the New mod form."""
    rows = []
    for folder in (dev.am_root() / "intake", Path.home() / "Downloads"):
        if not folder.is_dir():
            continue
        for ipa in sorted(folder.glob("*.ipa")):
            try:
                info, _ = dev.ipa_info(ipa)
            except Exception:  # noqa: BLE001
                continue
            rows.append(
                {
                    "path": str(ipa),
                    "bundle": info.get("CFBundleIdentifier"),
                    "name": info.get("CFBundleDisplayName") or info.get("CFBundleName"),
                    "version": info.get("CFBundleShortVersionString"),
                    "size": ipa.stat().st_size,
                    "fetched": ipa.with_name(ipa.name + ".source.json").is_file(),
                }
            )
    return {"ipas": rows}


def quick(name, command, body):
    """add/pin/call/tweak/remove/gen/mark through dev.py's own command functions."""
    ns = argparse.Namespace(mod=name)
    defaults = {
        "add": {
            "method": None,
            "key": None,
            "title": None,
            "action": None,
            "value": None,
            "index": None,
            "args": None,
            "slider": None,
            "to": None,
        },
        "pin": {
            "target": None,
            "object": None,
            "value": None,
            "when": "always",
            "threshold": None,
            "via": None,
            "cls": None,
            "multiply": False,
            "field": False,
            "key": None,
            "title": None,
            "slider": None,
            "to": None,
        },
        "call": {
            "target": None,
            "args": [],
            "every": 10.0,
            "object": None,
            "key": None,
            "title": None,
            "to": None,
        },
        "tweak": {"key": None, "title": None, "slider": None},
        "remove": {"what": None},
        "gen": {},
        "mark": {"key": None, "works": False, "fails": False},
    }[command]
    for k, default in defaults.items():
        v = body.get(k, default)
        if k in ("value", "threshold") and v is not None and not isinstance(v, str):
            v = str(v)
        if k == "args" and command == "call":
            v = [str(a) for a in (v or [])]
        if k == "slider" and v:
            v = [float(x) for x in v]
        setattr(ns, k, v)
    if command == "pin" and body.get("class"):
        ns.cls = body["class"]
    fn = {
        "add": dev.cmd_add,
        "pin": dev.cmd_pin,
        "call": dev.cmd_call,
        "tweak": dev.cmd_tweak,
        "remove": dev.cmd_remove,
        "gen": dev.cmd_gen,
        "mark": dev.cmd_mark,
    }[command]
    dev._dump_cache.clear() if command == "gen" else None
    code, text = capture(fn, ns)
    return {"ok": code == 0, "output": text}


class Handler(BaseHTTPRequestHandler):
    server_version = "ModStudio/1"

    def log_message(self, fmt, *args):  # quieter console
        # send_error logs with an HTTPStatus as the first argument, not the request line
        if "/api/jobs/" not in str(args[0] if args else ""):
            sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")

    def send_json(self, data, status=HTTPStatus.OK):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, ctype):
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def body_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1 << 20:
            raise dev.DevError("request too large")
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            raise dev.DevError("a JSON object is expected")
        return data

    def mod_name(self, part):
        if (
            not MOD_NAME.match(part)
            or not (ROOT / "mods" / part / "mod.json").is_file()
        ):
            raise dev.DevError(f"no mod {part}")
        return part

    def do_GET(self):
        try:
            self.handle_get()
        except (dev.DevError, ipa_fetch.FetchError) as e:
            self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
        except Exception as e:  # noqa: BLE001
            self.send_json(
                {"error": f"{e.__class__.__name__}: {e}"},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def do_POST(self):
        try:
            self.handle_post()
        except (dev.DevError, ipa_fetch.FetchError) as e:
            self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
        except Exception as e:  # noqa: BLE001
            self.send_json(
                {"error": f"{e.__class__.__name__}: {e}"},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def handle_get(self):
        url = urlparse(self.path)
        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
        parts = [p for p in url.path.split("/") if p]
        if not parts:
            return self.send_file(HERE / "index.html", "text/html; charset=utf-8")
        if parts[0] != "api":
            return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        if parts[1:] == ["mods"]:
            names = sorted(p.parent.name for p in (ROOT / "mods").glob("*/mod.json"))
            return self.send_json(
                {"mods": [mod_summary(n) for n in names], "am_root": str(dev.am_root())}
            )
        if parts[1:] == ["bases"]:
            return self.send_json(bases())
        if parts[1:] == ["fetch", "account"]:
            return self.send_json(fetch_client().account())
        if parts[1:] == ["fetch", "search"]:
            rows = fetch_client().search(
                q.get("q", "").strip(), q.get("region", "us"), q.get("platform", "iphone")
            )
            return self.send_json({"results": rows[:30]})
        if parts[1:2] == ["jobs"]:
            if len(parts) == 2:
                return self.send_json(
                    {
                        "jobs": [
                            j.view(len(j.lines))
                            for j in sorted(JOBS.values(), key=lambda j: j.started)
                        ]
                    }
                )
            job = JOBS.get(parts[2])
            if not job:
                return self.send_json({"error": "no such job"}, HTTPStatus.NOT_FOUND)
            return self.send_json(job.view(int(q.get("since", 0))))
        if parts[1] == "mods" and len(parts) >= 3:
            name = self.mod_name(parts[2])
            rest = parts[3:]
            with LOCK:
                if not rest:
                    return self.send_json(mod_detail(name))
                if rest == ["find"]:
                    return self.send_json(
                        find(
                            name,
                            q.get("q", "."),
                            int(q.get("limit", 60)),
                            q.get("class"),
                        )
                    )
                if rest == ["fields"]:
                    return self.send_json(fields(name, q.get("class", "")))
                if rest == ["suggest"]:
                    return self.send_json(suggest(name, int(q.get("limit", 30))))
        return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def handle_post(self):
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        body = self.body_json()
        if parts == ["api", "jobs"]:
            command = body.get("command", "")
            argv = []
            # sign and install take a file, not a mod; new makes the mod; fetch takes an app
            if command not in ("new", "sign", "install", "fetch"):
                argv.append(self.mod_name(str(body.get("mod", ""))))
            argv += [str(a) for a in body.get("args", [])]
            job = start_job(command, argv)
            return self.send_json(job.view())
        if parts == ["api", "fetch", "login"]:
            user = fetch_client().login(str(body.get("email", "")), str(body.get("password", "")))
            return self.send_json({"user": user})
        if parts == ["api", "fetch", "logout"]:
            fetch_client().logout()
            return self.send_json({"ok": True})
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "stop":
            job = JOBS.get(parts[2])
            if not job:
                return self.send_json({"error": "no such job"}, HTTPStatus.NOT_FOUND)
            job.stop()
            return self.send_json(job.view(len(job.lines)))
        if len(parts) == 4 and parts[:2] == ["api", "mods"] and parts[3] in QUICK:
            name = self.mod_name(parts[2])
            result = quick(name, parts[3], body)
            result["mod"] = mod_detail(name)
            return self.send_json(
                result, HTTPStatus.OK if result["ok"] else HTTPStatus.BAD_REQUEST
            )
        return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)


def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--port", type=int, default=8765)
    p.add_argument(
        "--host",
        default="127.0.0.1",
        help="0.0.0.0 shows the page to the Wi-Fi (no login: trusted network only)",
    )
    p.add_argument("--no-open", action="store_true")
    args = p.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}/"
    print(f"Mod Studio at {url}  (Ctrl+C stops)")
    if args.host == "0.0.0.0":
        print(
            "Shown to the whole network, with no login: use it on a trusted Wi-Fi only."
        )
    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
