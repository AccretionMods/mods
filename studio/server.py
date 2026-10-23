"""Mod Studio: a local web page for the mod dev loop (scripts/dev.py), served by the standard library.

Usage: python3 studio/server.py [--port 8765] [--host 127.0.0.1] [--no-open]
       scripts/am.ps1 ui   (same thing from PowerShell)

Quick commands (find, fields, add, pin, call, tweak, remove, gen, suggest, mark, status) run in this process
through dev.py's functions and answer at once. Long ones (dump, build, sign, install, run, log, update, new,
fetch) run as jobs: a `python dev.py <command> ...` subprocess whose output the page streams. The Apple ID sign-in
(2FA) can't be answered from a page: do it once from a console, plumesign keeps the session.

API (JSON): GET /api/mods, /api/mods/<mod>, /api/mods/<mod>/find?q=&limit=, /api/mods/<mod>/fields?class=, /api/mods/<mod>/fieldsearch?q=&limit=,
/api/mods/<mod>/suggest, /api/mods/<mod>/scan?area=&find=&flagged=1&text=1 (no-JIT candidates,
scripts/dump_scan.py), /api/mods/<mod>/live (switch states the phone logged),
/api/mods/<mod>/context (facts for the prompt builder), /api/prompts
(studio/prompts.json), /api/bases, /api/fetch/account, /api/fetch/search?q=&region=&platform=,
/api/games?list=top-free|top-paid|top-grossing|new-free|new-paid|similar|search|like&region=&q=&bundle=
(scripts/games.py through dev.games_list; also `dev.py games`), /api/games/info?bundle=&region=;
POST /api/fetch/login {"email", "password"} (passed to the decrypt site, never saved), /api/fetch/logout; POST /api/mods/<mod>/<add|pin|call|tweak|remove|gen|mark> with the
command's options as JSON (the same names as dev.py's flags, dashes as underscores); POST /api/jobs
{"command": "build", "mod": "...", "args": ["--ipa"]}, GET /api/jobs/<id>?since=N, POST /api/jobs/<id>/stop.
Console: POST /api/console {"line": "find my-mod \"Damage|Hp\""} runs any dev.py command as a job (this PC only),
GET /api/console/commands, POST /api/jobs/<id>/input {"text": "2"} answers a console job's prompt.
POST /api/claude {"prompt", "read_only"} runs Claude Code headless (claude -p) on the prompt builder's prompt and
streams its text and tool calls as a job (this PC only); read-only runs get reading tools only.

Binds 127.0.0.1: only this PC. --host 0.0.0.0 shows it to the Wi-Fi with no login, for a trusted network.
"""

import argparse
import contextlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "scripts"))

import dev
import dev_learn
import games
import gen_tweaks
import ipa_fetch
import jobs_state
import menu_image
import scan_view
import shots
import store_view
import workspace

LOCK = threading.Lock()  # dev.py writes files and caches; one quick command at a time
JOBS = {}
COORDINATOR = jobs_state.Coordinator()
SESSION_LOCK = threading.RLock()
RESTARTING = threading.Event()
ACTIVE_ACTIONS = 0
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
    "start",
    "report",
    "post",
    "push",
    "test",
    "share",
}
QUICK = {"add", "pin", "call", "tweak", "remove", "gen", "mark"}
LOADED_REVISION = workspace.source_revision(ROOT)
MOD_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def busy():
    return ACTIVE_ACTIONS > 0 or any(
        j.status in {"queued", "running", "stopping"} for j in list(JOBS.values())
    )


@contextlib.contextmanager
def action_scope():
    global ACTIVE_ACTIONS
    with SESSION_LOCK:
        if RESTARTING.is_set():
            raise dev.DevError(
                "Studio is restarting; reconnect before changing configuration"
            )
        ACTIVE_ACTIONS += 1
    try:
        yield
    finally:
        with SESSION_LOCK:
            ACTIVE_ACTIONS -= 1


def job_resources(command, argv):
    resources = []
    if command in {"sign", "install", "run", "log", "test", "push"}:
        resources.append("device")
    if command in {"new", "fetch", "start"}:
        resources.append("intake")
    if command == "store":
        resources.append("store-publish")  # one publish worktree at a time
    if (
        argv
        and MOD_NAME.fullmatch(argv[0])
        # log, test and push only read the mod (they hold the phone): Add, Pin and a build may run beside them
        and command not in {"games", "fetch", "sign", "install", "log", "test", "push"}
    ):
        resources.append("mod:" + argv[0])
    if command in ("claude", "codex") and argv != ["(read only)"]:
        resources += ["device", "intake"] + [
            "mod:" + p.parent.name for p in (ROOT / "mods").glob("*/mod.json")
        ]
    return sorted(set(resources))


class Job:
    def __init__(self, command, argv, interactive=False, cmd=None, parse=None):
        self.id = f"{int(time.time() * 1000):x}{len(JOBS):x}"
        self.interactive = (
            interactive  # console jobs take typed answers (pick, plumesign's 2FA code)
        )
        self.cmd = cmd  # a whole command line instead of dev.py (the Claude Code runs)
        self.parse = (
            parse  # turns each output line into the lines shown (Claude's stream-json)
        )
        self.command = command
        self.argv = argv
        self.lines = []
        self.status = "queued"
        self.code = None
        self.started = time.time()
        self.ended = None  # when it finished: the auto-build bar shows the run time
        self.proc = None
        self.cancelled = threading.Event()
        self.resources = job_resources(command, argv)
        self.history = jobs_state.History(dev.am_root() / "studio")
        self.last_saved = 0
        self.private_inputs = []

    def persist(self):
        try:
            self.history.save(self.view())
            self.last_saved = time.monotonic()
        except (OSError, ValueError):
            pass  # A full or unwritable history disk must not stop a running build.

    def run(self):
        if not COORDINATOR.acquire(self.resources, self.cancelled):
            self.status = "stopped"
            self.persist()
            return
        try:
            if self.cancelled.is_set():
                self.status = "stopped"
                return
            self.status = "running"
            self.persist()
            if (
                self.argv
                and "mod:" + self.argv[0] in self.resources
                and self.command in {"build", "run", "dump", "update", "new", "aider", "gemini", "autobuild"}
            ):
                folder = ROOT / "mods" / self.argv[0]
                if folder.is_dir():
                    try:
                        workspace.snapshot_create(
                            dev.am_root() / "studio",
                            self.argv[0],
                            folder,
                            "before " + self.command,
                        )
                    except (OSError, ValueError) as error:  # a snapshot problem never stops the job
                        self.lines.append(f"(no snapshot: {error})")
            self._execute()
        except Exception as error:  # noqa: BLE001 - report a worker failure and always release its resources
            self.lines.append(jobs_state.redact(f"Job failed: {error}"))
            self.status, self.code = "failed", -1
        finally:
            self.ended = time.time()
            COORDINATOR.release(self.resources)
            self.persist()

    def _execute(self):
        if self.cancelled.is_set():
            self.status = "stopped"
            return
        cmd = (
            self.cmd
            or [
                sys.executable,
                str(HERE.parent / "scripts" / "dev.py"),
                self.command,
            ]
            + self.argv
        )
        env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1", AM_REPO=str(ROOT))
        env.setdefault("COLUMNS", "4000")  # aider wraps its answer (and its am lines) at the terminal width
        if not env.get("GEMINI_API_KEY") and gemini_key():  # saved after this server started
            env["GEMINI_API_KEY"] = gemini_key()
        # a server started outside a login shell may lack uv and pymobiledevice3 on PATH
        local_bin = str(Path.home() / ".local" / "bin")
        if local_bin not in env.get("PATH", ""):
            env["PATH"] = local_bin + os.pathsep + env.get("PATH", "")
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE if self.interactive else subprocess.DEVNULL,
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
            line = line.rstrip("\n")
            for value in self.private_inputs:
                line = line.replace(value, "[redacted input]")
            self.lines.extend(
                jobs_state.redact(part)
                for part in (self.parse(line) if self.parse else [line])
            )
            if time.monotonic() - self.last_saved > 1:
                self.persist()
        self.code = self.proc.wait()
        self.status = (
            "done"
            if self.code == 0
            else ("stopped" if self.status == "stopping" else "failed")
        )

    def send(self, text):
        """A typed answer for a console job's prompt."""
        if not (
            self.interactive
            and self.proc
            and self.proc.poll() is None
            and self.proc.stdin
        ):
            raise dev.DevError("this job is not waiting for input")
        self.lines.append("> [input sent]")
        if len(text) >= 4:
            self.private_inputs = (self.private_inputs + [text])[-20:]
        self.proc.stdin.write(text + "\n")
        self.proc.stdin.flush()

    def stop(self):
        self.cancelled.set()
        if self.status == "queued":
            self.status = "stopped"
            self.persist()
        if self.proc and self.proc.poll() is None:
            self.status = "stopping"
            if sys.platform == "win32":  # the whole tree: auto-build runs several model processes at once
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True, check=False)
            self.proc.kill()

    def view(self, since=0):
        return {
            "id": self.id,
            "command": self.command,
            "args": self.argv,
            "status": self.status,
            "code": self.code,
            "started": self.started,
            **({"ended": self.ended} if self.ended else {}),
            "interactive": self.interactive,
            "resources": self.resources,
            "lines": self.lines[since:],
            "next": len(self.lines),
        }


class RecoveredJob:
    """An archived record is display-only; it never owns or recreates a subprocess."""

    def __init__(self, record):
        self.record = record
        self.id, self.command, self.argv = (
            record["id"],
            record["command"],
            record["args"],
        )
        self.status, self.started, self.lines = (
            record["status"],
            record["started"],
            record["lines"],
        )

    def view(self, since=0):
        return dict(
            self.record, lines=self.lines[max(0, since) :], next=len(self.lines)
        )

    def send(self, _text):
        raise dev.DevError("Archived jobs cannot accept input or resume")

    def stop(self):
        raise dev.DevError("This job is already archived")


def start_job(command, argv, interactive=False):
    if command not in JOB_COMMANDS and not (
        interactive and command in console_commands()
    ):
        raise dev.DevError(f"{command} is not a job command")
    for a in argv:
        if not isinstance(a, str) or "\n" in a:
            raise dev.DevError("bad argument")
    with SESSION_LOCK:
        if RESTARTING.is_set():
            raise dev.DevError("Studio is restarting; reconnect before starting a job")
        job = Job(command, argv, interactive)
        JOBS[job.id] = job
    job.persist()
    threading.Thread(target=job.run, daemon=True).start()
    return job


# Claude Code runs from the prompt builder. dev.py through uv: python on this PC can be the Store stub.
DEV = "uv run --no-project --python 3.12 scripts/dev.py"
READ_ONLY_DEV = (
    "status",
    "find",
    "fields",
    "scan",
    "report",
    "suggest",
    "check",
    "games",
    "help",
)
CLAUDE_NOTE = (
    "This is a headless run started from Mod Studio's prompt builder in the AccretionMods/mods repo. "
    'Project: Accretion (record it with: echo "[[project: Accretion]]"); don\'t ask which project. '
    f"Run the mod commands written as `am <command>` as `{DEV} <command>`. "
    "Nobody can answer questions during this run: when a choice is the owner's, stop and list the options. "
    "You already run in the repo: one command per Bash call, no cd, no && or ; chains, no pipes (| head): "
    "anything else is refused and wastes a turn. Shorten output with the commands' own options "
    "(am find --limit N, am scan --limit N, am fields <mod> <Class>). Which class holds a value: "
    "am find <mod> <regex> --fields (Namespace.Class.field, about a second). Class names may carry a namespace "
    "(Core.PlayerLife); am fields and am pin take them. Grep or Read only dump.cs itself, never its folder: "
    "il2cpp.h and script.json there are 200 MB and time the search out."
)


def claude_exe():
    found = shutil.which("claude") or shutil.which(
        "claude", path=str(Path.home() / ".local" / "bin")
    )
    if not found:
        raise dev.DevError("Claude Code (claude) is not installed or not on PATH")
    return found


def claude_command(prompt, read_only, effort=""):
    """The claude -p command line for a builder prompt. Read-only: reading tools and the read-only dev.py
    commands only, no edits. Otherwise: edits accepted, any dev.py command, no other shell commands."""
    if read_only:
        allowed = ["Read", "Grep", "Glob"] + [
            f"Bash({DEV} {c}:*)" for c in READ_ONLY_DEV
        ]
        mode = [
            "--permission-mode",
            "default",
            "--disallowedTools",
            "Edit",
            "Write",
            "NotebookEdit",
        ]
    else:
        allowed = [
            "Read",
            "Grep",
            "Glob",
            "Edit",
            "Write",
            f"Bash({DEV}:*)",
            'Bash(echo "[[project:*)',
        ]
        mode = ["--permission-mode", "acceptEdits"]
    if read_only:
        allowed.append('Bash(echo "[[project:*)')
    return [
        claude_exe(),
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--append-system-prompt",
        CLAUDE_NOTE,
        "--allowedTools",
        *allowed,
        *mode,
        *(["--effort", effort] if effort else []),
    ]


def claude_lines(raw):
    """One stream-json event from claude -p as readable lines: Claude's text, the tools it uses, the result."""
    try:
        ev = json.loads(raw)
    except ValueError:
        return [raw] if raw.strip() else []
    kind = ev.get("type")
    if kind == "assistant":
        out = []
        for part in (ev.get("message") or {}).get("content") or []:
            if part.get("type") == "text" and part.get("text", "").strip():
                out += part["text"].rstrip().split("\n")
            elif part.get("type") == "tool_use":
                args = part.get("input") or {}
                what = (
                    args.get("command")
                    or args.get("file_path")
                    or args.get("pattern")
                    or ""
                )
                out.append(f"  » {part.get('name')} {str(what)[:160]}")
        return out
    if kind == "user":
        out = []
        for part in (ev.get("message") or {}).get("content") or []:
            if (
                isinstance(part, dict)
                and part.get("type") == "tool_result"
                and part.get("is_error")
            ):
                text = part.get("content")
                text = text if isinstance(text, str) else json.dumps(text)[:300]
                out.append(f"  ! {text.strip()[:300]}")
        return out
    if kind == "result":
        cost = ev.get("total_cost_usd")
        secs = (ev.get("duration_ms") or 0) / 1000
        return [
            "",
            f"== Claude finished: {ev.get('subtype', '')}, {ev.get('num_turns', '?')} turns, {secs:.0f} s"
            + (f", ${cost:.2f}" if isinstance(cost, (int, float)) else ""),
        ]
    if kind == "system" and ev.get("subtype") == "init":
        return [f"== Claude Code ({ev.get('model', '')}) in {ev.get('cwd', '')}"]
    return []


# Other models: aider with the model in ~/.aider.conf.yml (the free OpenRouter Nemotron, ~/AI-MODELS.md). No git,
# no commits, no repo map, no shell commands. Read only: ask mode, only the prompt leaves this PC. Changing files:
# it edits the selected mod's own files (tweaks.json, hooks.json, mod.json, Tweak.x), which are sent with the
# prompt; a snapshot is taken first (Files tab restores it). The am commands it proposes become buttons in Jobs.
ENGINES = ("claude", "aider", "codex", "gemini")
# how hard the model thinks; "" keeps each engine's own default (Claude Code's, ~/.codex/config.toml's, the model's)
EFFORTS = ("", "low", "medium", "high")
# Gemini: aider with Google's free API tier, key GEMINI_API_KEY in the Windows user environment (gcloud key
# accretion-local-gemini, project accreation). The free tier may use prompts to improve Google's products: game
# scan rows only, no secrets. AM_GEMINI_MODEL picks another model. Checked 2026-10-22 on the free key:
# 3.6-flash answers in ~2 s; 3.8-flash is free but was overloaded (503, one answer took 138 s); 4-argon is paid only.
GEMINI_MODEL = os.environ.get("AM_GEMINI_MODEL", "gemini-3.6-flash")
AIDER_FILES = ("tweaks.json", "hooks.json", "mod.json", "Tweak.x")
AIDER_NOTE = (
    "\n\nYou cannot run commands or read other files. Where the task needs an am command (am pin, am add, am gen, "
    "am build, am new, am find...), write the exact command on its own line starting with `am ` and Studio shows "
    "a button to run it."
)
AIDER_EDIT_NOTE = (
    " You may edit the files you were given; keep tweaks.json valid JSON and run nothing yourself. After an edit "
    "of tweaks.json, end with the line: am gen {mod}"
)


# Codex (GPT): OpenAI's CLI (npm @openai/codex), signed in with the owner's ChatGPT account like the Codex app.
# codex exec runs headless in the repo like Claude Code: read only in its read-only sandbox; for a task that
# changes files, full access (the owner's choice, 2026-10-22: the whole loop start to finish, signing and
# installing on the phone included), so it can run any command on this PC with the network. Studio asks before
# every such run. The prompt goes as one argument to node, not through codex.cmd (cmd.exe cuts at 8191 chars).
def codex_command(prompt, read_only, effort=""):
    node = shutil.which("node")
    script = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    if node and script.is_file():
        head = [node, str(script)]
    elif shutil.which("codex"):
        head = [shutil.which("codex")]
    else:
        raise dev.DevError("Codex CLI is not installed (npm install -g @openai/codex, then codex login)")
    note = CLAUDE_NOTE + (" Read only: change no files." if read_only else
                          " You have full access for the whole loop: edit the mod, am gen, then am run <mod> --bump "
                          "(builds, signs, installs on the USB phone, opens the game and streams the log), and read "
                          "the log's status lines to see which switches work. Stay in this repo and the mod workflow: "
                          "no git push, no publishing, no deleting outside the mod, no other installs.")
    mode = ["--sandbox", "read-only"] if read_only else ["--dangerously-bypass-approvals-and-sandbox"]
    level = ["-c", f'model_reasoning_effort="{effort}"'] if effort else []
    return [*head, "exec", *mode, *level, "--skip-git-repo-check", "--ephemeral", "-C", str(ROOT),
            note + "\n\n" + prompt]


def aider_exe():
    found = shutil.which("aider") or shutil.which(
        "aider", path=str(Path.home() / ".local" / "bin")
    )
    if not found:
        raise dev.DevError("aider is not installed or not on PATH (uv tool install aider-chat)")
    return found


def gemini_key():
    """GEMINI_API_KEY from this process, else from the Windows user environment (saved after Studio started)."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key and sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as reg:
                key = winreg.QueryValueEx(reg, "GEMINI_API_KEY")[0]
        except OSError:
            key = ""
    return key


def gemini_command(prompt, mod=None, effort=""):
    """aider with Gemini instead of the model in ~/.aider.conf.yml; the key goes through the environment."""
    if not gemini_key():
        raise dev.DevError("no GEMINI_API_KEY on this PC (a free key from aistudio.google.com, saved as a user "
                           "environment variable)")
    return aider_command(prompt, mod, effort, model="gemini/" + GEMINI_MODEL)


def aider_command(prompt, mod=None, effort="", model=None):
    files = []
    if mod:
        folder = dev.mod_dir(mod)
        for name in AIDER_FILES:
            if (folder / name).is_file():
                files += ["--file", str(folder / name)]
    note = AIDER_NOTE + (AIDER_EDIT_NOTE.format(mod=mod) if mod else "")
    return [
        aider_exe(),
        *(["--model", model] if model else []),
        *(["--reasoning-effort", effort] if effort else []),
        *(["--chat-mode", "ask"] if not mod else []),
        *files,
        "--message",
        prompt + note,
        "--no-suggest-shell-commands",
        "--map-tokens",
        "0",
        "--no-git",
        "--no-auto-commits",
        "--yes-always",
        "--no-pretty",
        "--no-stream",
        "--no-fancy-input",
        "--no-check-update",
        "--no-show-release-notes",
        "--no-detect-urls",
        "--no-analytics",
    ]


# aider cannot search the dump, so its first answer to a new feature is often only `am find` / `am fields`
# lines. A follow-up runs those searches here (read only, from the cached dump) and asks aider again with the
# results under its own prompt.
SEARCH_LINE = re.compile(r"^am (find|fields) ([a-z0-9]+(?:-[a-z0-9]+)*)\s+(.+)$")
SEARCH_ROWS = 40  # per search, so a broad regex cannot fill the prompt


def run_searches(mod, lines, budget):
    blocks, seen = [], set()
    for line in lines:
        cmd = line.strip().strip("`").lstrip("$> ").strip()
        m = SEARCH_LINE.match(cmd)
        if not m or m.group(2) != mod or cmd in seen:
            continue
        seen.add(cmd)
        try:
            words = shlex.split(m.group(3))
            terms = [w for w in words if not w.startswith("--")]
            if m.group(1) == "fields":
                rows = [f"{o if isinstance(o, str) else hex(o)} {'static ' if s else ''}{t} {n}" for o, s, t, n in dev.fields_of(mod, terms[0])]
            elif "--class" in words:
                cls = words[words.index("--class") + 1]
                rows = [f"{x.full_name}({', '.join(x.params)}) -> {x.return_type}"
                        for x in dev.dump_methods(mod) if x.class_name == cls or x.full_name.rsplit(".", 1)[0] == cls]
            elif "--fields" in words:
                rx = re.compile(terms[0], re.IGNORECASE)
                rows = [f"{c}.{n}  {'static ' if st else ''}{t}" for c, st, t, n in dev.field_rows(mod)
                        if "<" not in c and rx.search(f"{c}.{n}")]
            else:
                rx = re.compile(terms[0], re.IGNORECASE)
                rows = [f"{x.full_name}({', '.join(x.params)}) -> {x.return_type}"
                        for x in dev.dump_methods(mod) if rx.search(x.full_name)]
        except (re.error, IndexError, ValueError, OSError, dev.DevError) as error:
            rows = [f"(this search failed: {error})"]
        more = f"\n... {len(rows) - SEARCH_ROWS} more" if len(rows) > SEARCH_ROWS else ""
        blocks.append(f"$ {cmd}\n" + ("\n".join(rows[:SEARCH_ROWS]) or "(nothing found)") + more)
    text = "\n\n".join(blocks)
    return text if len(text) <= budget else text[:budget] + "\n... (cut to fit the prompt)"


def start_autobuild(mod, engine, limit, effort=""):
    """studio/autobuild.py as a job: the model picks switches from the scan, Studio adds them and builds."""
    if engine not in ENGINES:
        raise dev.DevError(f"unknown model engine {engine!r}")
    if effort not in EFFORTS:
        raise dev.DevError(f"unknown effort {effort!r}; use low, medium or high")
    if not (MOD_NAME.fullmatch(mod or "") and dev.mod_dir(mod).is_dir()):
        raise dev.DevError("pick a mod first")
    with SESSION_LOCK:
        if RESTARTING.is_set():
            raise dev.DevError("Studio is restarting; reconnect before starting jobs")
        job = Job("autobuild", [mod, engine], cmd=[sys.executable, str(HERE / "autobuild.py"), mod, "--engine", engine,
                                                   "--limit", str(max(0, int(limit))),  # 0: every usable switch
                                                   *(["--effort", effort] if effort else [])])
        JOBS[job.id] = job
    job.persist()
    threading.Thread(target=job.run, daemon=True).start()
    return job


def start_store_publish(mod, action, confirm):
    """studio/store_publish.py as a job: publish this checkout's version of a mod to the store, or take it out."""
    if action not in ("update", "remove"):
        raise dev.DevError("unknown store action")
    if not (MOD_NAME.fullmatch(mod or "") and dev.mod_dir(mod).is_dir()):
        raise dev.DevError("pick a mod first")
    if action == "remove" and confirm != mod:
        raise dev.DevError(f"type {mod} to confirm")
    with SESSION_LOCK:
        if RESTARTING.is_set():
            raise dev.DevError("Studio is restarting; reconnect before starting jobs")
        job = Job("store", [mod, action], cmd=[sys.executable, str(HERE / "store_publish.py"), action, mod])
        JOBS[job.id] = job
    job.persist()
    threading.Thread(target=job.run, daemon=True).start()
    return job


def start_followup(job, mod):
    if getattr(job, "engine", None) not in ("aider", "gemini") or not getattr(job, "prompt", None):
        raise dev.DevError("only an aider or Gemini job from this Studio session can be followed up")
    if job.status in ("queued", "running", "stopping"):
        raise dev.DevError("wait for it to finish first")
    mod = job.mod or mod
    if not (MOD_NAME.fullmatch(mod or "") and dev.mod_dir(mod).is_dir()):
        raise dev.DevError("pick a mod first")
    budget = 19000 - len(job.prompt) - 400
    with LOCK:
        results = run_searches(mod, job.lines, budget)
    if not results:
        raise dev.DevError(f"aider asked for no am find / am fields search on {mod}")
    prompt = (job.prompt + "\n\nResults of the searches you asked for (run by Mod Studio on the game's dump):\n"
              + results + "\n\nUse them now: make the change, or give the exact am pin / am add lines. Ask for "
              "more searches only if these leave it open.")
    return start_claude(prompt, job.read_only, job.engine, mod if not job.read_only else "", job.effort)


def start_claude(prompt, read_only, engine="claude", mod="", effort=""):
    if engine not in ENGINES:
        raise dev.DevError(f"unknown model engine {engine!r}; use one of: {', '.join(ENGINES)}")
    if effort not in EFFORTS:
        raise dev.DevError(f"unknown effort {effort!r}; use low, medium or high")
    if engine in ("aider", "gemini") and not read_only and not (
            MOD_NAME.fullmatch(mod or "") and dev.mod_dir(mod).is_dir()):
        raise dev.DevError(f"pick a mod first: {engine} edits that mod's files")
    prompt = (prompt or "").strip()
    if len(prompt) < 10:
        raise dev.DevError("the prompt is empty")
    if len(prompt) > 20000:
        raise dev.DevError("the prompt is too long (20,000 characters at most)")
    with SESSION_LOCK:
        if RESTARTING.is_set():
            raise dev.DevError("Studio is restarting; reconnect before starting jobs")
        if engine == "codex":
            job = Job("codex", ["(read only)" if read_only else "(may change files)"],
                      cmd=codex_command(prompt, read_only, effort))
        elif engine in ("aider", "gemini"):
            command = gemini_command if engine == "gemini" else aider_command
            if read_only:
                job = Job(engine, ["(read only)"], cmd=command(prompt, None, effort))
            else:
                job = Job(engine, [mod, "(may change files)"], cmd=command(prompt, mod, effort))
        else:
            job = Job(
                "claude",
                ["(read only)" if read_only else "(may change files)"],
                cmd=claude_command(prompt, read_only, effort),
                parse=claude_lines,
            )
        # kept in memory only (never in the saved history) for an aider follow-up
        job.prompt, job.engine, job.read_only, job.mod = prompt, engine, read_only, mod if not read_only else ""
        job.effort = effort
        JOBS[job.id] = job
    job.persist()
    threading.Thread(target=job.run, daemon=True).start()
    return job


def console_commands():
    """Every dev.py command, for the Console tab."""
    return sorted(dev.build_parser()._subparsers._group_actions[0].choices)


def split_line(line):
    """A console line as arguments: quotes group words, backslashes stay (regexes like \\d+|Hp\\b)."""
    lex = shlex.shlex(line, posix=True)
    lex.whitespace_split = True
    lex.escape = ""
    try:
        words = list(lex)
    except ValueError as e:
        raise dev.DevError(f"console: {e}") from e
    while words and words[0].lower() in (
        "am",
        "dev.py",
        "python",
        "py",
        "scripts\\am.ps1",
        "scripts/dev.py",
    ):
        words.pop(0)
    return words


def console(line):
    """Run a typed `am ...` line as a streamed job: any dev.py command, or help [command]."""
    words = split_line(line)
    if not words:
        raise dev.DevError("console: type a command, or help")
    if words[0] in ("help", "-h", "--help"):
        return (
            start_job("--help", [], True)
            if len(words) == 1
            else start_job(words[1], ["--help"], True)
        )
    if words[0] not in console_commands():
        raise dev.DevError(
            f"console: no command {words[0]!r}; commands: {', '.join(console_commands())}"
        )
    return start_job(words[0], words[1:], True)


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
        "built": last_built(state),
    }


def last_built(state):
    """When the mod was last built: the newest of its last .ipa and .dylib that still exist (epoch seconds), or None."""
    times = []
    for key in ("last_ipa", "last_dylib"):
        try:
            times.append(Path(state[key]).stat().st_mtime)
        except (KeyError, TypeError, OSError):
            pass
    return max(times) if times else None


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
    data["features"] = []
    for saved in dev.load_state(name).get("verified_features", []):
        feature = dict(saved)
        result = feature.get("verified") or {}
        status = scan_view.verification_state(result, data["app_version"])
        if status in {"works", "fails"} and result.get("mod_version") != data["version"]:
            status = "stale"
        feature["verification_state"] = status
        data["features"].append(feature)
    for tweak in (data["tweaks_json"] or {}).get("tweaks", []):
        tweak["verification_state"] = scan_view.verification_state(
            tweak.get("verified"), data["app_version"]
        )
    try:
        data["newer_bases"] = [
            {"path": str(p), "version": v} for p, v in dev.newer_bases(meta)
        ]
    except Exception:  # noqa: BLE001 - a bad .ipa in Downloads must not break the page
        data["newer_bases"] = []
    return data


LOG_ERROR = re.compile(
    r"error|exception|crash|fault|fail|not found|null|abort|terminat", re.IGNORECASE
)


LIVE_STATUS = re.compile(r"\[AccretionMods\] status (\w+) (ok|off|waiting: (.*))$")


def live_status(name):
    """Each switch's last state on the phone, from the menu's "status" log lines in Studio's log and run jobs
    for this mod (oldest job first, so the newest line wins): {key: {"state", "reason"}}, and whether one runs."""
    states, watching = {}, False
    for job in sorted(JOBS.values(), key=lambda j: j.started):
        if job.command not in ("log", "run") or job.argv[:1] != [name]:
            continue
        watching = watching or job.status == "running"
        for line in job.lines:
            m = LIVE_STATUS.search(line.rstrip())
            if m:
                state = "waiting" if m.group(2).startswith("waiting") else m.group(2)
                states[m.group(1)] = {
                    "state": state,
                    "reason": (m.group(3) or "").strip(),
                }
    return {"switches": states, "watching": watching}


def mod_context(name):
    """Facts about a mod for the prompt builder: versions, dump, switches and their phone results, the last
    device-log errors Studio saw in a log or run job, and whether a newer decrypted copy is waiting."""
    d = mod_detail(name)
    state = dev.load_state(name)
    tweaks = (d.get("tweaks_json") or {}).get("tweaks", [])
    works = [t["key"] for t in tweaks if t["verification_state"] == "works"]
    fails = [t["key"] for t in tweaks if t["verification_state"] == "fails"]
    untested = [
        t["key"] for t in tweaks if t["verification_state"] in ("untested", "stale")
    ]
    errors, seen_at = [], None
    for job in sorted(JOBS.values(), key=lambda j: j.started, reverse=True):
        if (
            job.command in ("log", "run")
            and job.argv[:1] == [name]
            and job.status != "running"
        ):
            errors = [line.strip() for line in job.lines if LOG_ERROR.search(line)][
                -12:
            ]
            seen_at = job.started
            break
    switches = (
        f"{len(tweaks)} ({len(works)} work, {len(fails)} fail, {len(untested)} not checked)"
        if tweaks
        else "none yet"
    )
    return {
        "mod": name,
        "title": d["title"],
        "version": d["version"],
        "target": d["target"],
        "app_version": d["app_version"] or "?",
        "personal": d["personal"],
        "dump": d["dump"],
        "dump_state": f"made ({state.get('dump_dir')})"
        if d["dump"]
        else f"not made yet (am dump {name})",
        "hooks": f"{d['hooks']} hooks, {d['hooks_jit']} need JIT",
        "switches": switches,
        "works": ", ".join(works + [f["title"] for f in d["features"] if f["verification_state"] == "works"]) or "none",
        "fails": ", ".join(fails) or "none",
        "untested": ", ".join(untested) or "none",
        "log_errors": ("\n" + "\n".join("  " + e for e in errors))
        if errors
        else "none seen (run Log or Run in Studio first)",
        "log_seen": bool(seen_at),
        "newer_bases": d["newer_bases"],
        "last_ipa": d["last_ipa"],
        "verified_features": d["features"],
        "learned_workflows": dev.dev_learn.load(dev.patterns_path()).get("workflows", []),
    }


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
        rows = [
            m
            for m in found
            if m.class_name == class_name or m.full_name.rsplit(".", 1)[0] == class_name
        ]
    else:
        rx = re.compile(query, re.IGNORECASE)
        rows = [m for m in found if rx.search(m.full_name)]
    return {
        "count": len(rows),
        "methods": scan_view.review_methods(dev, name, rows[:limit], method_row),
    }


def fields(name, class_name):
    return {
        "fields": [
            {"offset": o, "static": s, "type": t, "name": n}
            for o, s, t, n in dev.fields_of(name, class_name)
        ]
    }


def field_search(name, query, limit):
    """Fields whose Namespace.Class.field matches, across every class (am find --fields): which class holds a value."""
    rx = re.compile(query or ".", re.IGNORECASE)
    rows = [
        r
        for r in dev.field_rows(name)
        if "<" not in r[0] and not r[3].startswith("<>") and rx.search(f"{r[0]}.{r[3]}")
    ]
    return {
        "count": len(rows),
        "fields": [
            {"class": c, "static": st, "type": t, "name": n}
            for c, st, t, n in rows[:limit]
        ],
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


def store_apps():
    """Our mods' store ids: the catalogue's and every local mod's bundle id."""
    ids = []
    for meta in (ROOT / "mods").glob("*/mod.json"):
        try:
            bundle = json.loads(meta.read_text(encoding="utf-8")).get("bundle_id")
        except (OSError, ValueError):
            continue
        for value in (bundle, "io.github.accretionmods." + meta.parent.name):
            if value and value not in ids:
                ids.append(value)
    return ids


def store_get(rest):
    """The Store tab's reads: public, admin, endpoints, keys, runs, link, moderation."""
    root = dev.am_root()
    if rest == ["public"]:
        return store_view.public(ROOT)
    if rest == ["admin"]:
        return store_view.admin_summary(root, store_apps())
    if rest == ["endpoints"]:
        return store_view.endpoints(root)
    if rest == ["keys"]:
        try:
            sign = dev.plumesign()
        except dev.DevError:
            sign = None
        return store_view.keys(root, ROOT, sign, lambda: fetch_client().account())
    if rest == ["runs"]:
        return store_view.runs()
    if rest == ["shots"]:
        try:
            return {"shots": shots.listing(ROOT)}
        except shots.ShotsError as e:
            raise dev.DevError(str(e)) from e
    if rest == ["moderation"]:
        try:
            return store_view.moderation(root)
        except RuntimeError as e:
            raise dev.DevError(str(e)) from e
    if rest == ["link"]:
        state = store_view.admin_state(root)
        return {"status": "linked" if state.get("token") else ("waiting" if state.get("pending") else "none"),
                "account": state.get("account"), "code": state.get("pending")}
    raise dev.DevError("unknown store request")


def fetch_client():
    return ipa_fetch.Client(dev.am_root())


def bases():
    """Decrypted .ipa files in intake/ and Downloads, for the New mod form."""
    return {"ipas": dev.local_ipas()}


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

    def change():
        try:
            workspace.snapshot_create(
                dev.am_root() / "studio", name, dev.mod_dir(name), "before " + command
            )
        except (OSError, ValueError) as error:  # a snapshot problem never stops the change
            print(f"(no snapshot: {error})")
        return fn(ns)

    cancelled = threading.Event()
    with action_scope():
        COORDINATOR.acquire(["mod:" + name], cancelled)
        try:
            code, text = capture(change)
        finally:
            COORDINATOR.release(["mod:" + name])
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

    def local_only(self):
        """The console runs commands on this PC: only this PC may use it, even with --host 0.0.0.0."""
        if self.client_address[0] not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            raise dev.DevError("the console only answers this PC (127.0.0.1)")
        host = self.headers.get("Host", "")
        if urlparse("http://" + host).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise dev.DevError("Use Studio's localhost address")
        if self.headers.get("Origin") and self.headers["Origin"] != "http://" + host:
            raise dev.DevError("This action must come from the Studio page")

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
        except (dev.DevError, ipa_fetch.FetchError, games.GamesError) as e:
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
        if parts == ["api", "studio"]:
            current = workspace.source_revision(ROOT)
            return self.send_json(
                {
                    "protocol": 1,
                    "root": str(ROOT),
                    "pid": os.getpid(),
                    "loaded": LOADED_REVISION,
                    "current": current,
                    "stale": current != LOADED_REVISION,
                    "busy": busy(),
                }
            )
        if not parts:
            return self.send_file(HERE / "index.html", "text/html; charset=utf-8")
        if parts == ["menu-image"]:
            return self.send_file(HERE / "menu_image.html", "text/html; charset=utf-8")
        if parts[0] != "api":
            return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        if parts[1:] == ["mods"]:
            names = sorted(p.parent.name for p in (ROOT / "mods").glob("*/mod.json"))
            return self.send_json(
                {"mods": [mod_summary(n) for n in names], "am_root": str(dev.am_root())}
            )
        if parts[1:] == ["bases"]:
            return self.send_json(bases())
        if parts[1:] == ["prompts"]:
            return self.send_json(
                json.loads((HERE / "prompts.json").read_text(encoding="utf-8"))
            )
        if parts[1:] == ["console", "commands"]:
            return self.send_json({"commands": console_commands()})
        if parts[1:] == ["games"]:
            return self.send_json(
                dev.games_list(
                    q.get("list", "top-free"),
                    q.get("region", "us"),
                    q.get("q"),
                    q.get("bundle"),
                )
            )
        if parts[1:] == ["games", "info"]:
            return self.send_json(
                dev.games_info(q.get("bundle", ""), q.get("region", "us"))
            )
        if parts[1:2] == ["store"]:
            return self.send_json(store_get(parts[2:]))
        if parts[1:] == ["fetch", "account"]:
            return self.send_json(fetch_client().account())
        if parts[1:] == ["fetch", "search"]:
            rows = fetch_client().search(
                q.get("q", "").strip(),
                q.get("region", "us"),
                q.get("platform", "iphone"),
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
                if rest == ["menu"]:
                    return self.send_json(menu_image.read(ROOT / "mods" / name))
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
                if rest == ["snapshots"]:
                    self.local_only()
                    return self.send_json(
                        {
                            "snapshots": workspace.snapshot_list(
                                dev.am_root() / "studio", name
                            )
                        }
                    )
                if rest == ["fieldsearch"]:
                    return self.send_json(
                        field_search(name, q.get("q", "."), int(q.get("limit", 60)))
                    )
                if rest == ["scan"]:
                    result, rows = scan_view.query(dev, name, q)
                    if (
                        q.get("text") == "1"
                    ):  # for the Claude prompt: not yet in the mod, best first
                        fresh = [r for r in rows if not r["has"]]
                        return self.send_json(
                            {
                                "text": dev.dump_scan.as_text(
                                    fresh, int(q.get("limit", 60))
                                )
                            }
                        )
                    return self.send_json(
                        {
                            "classes": result["classes"],
                            "total": len(result["rows"])
                            + len(result.get("inventory", [])),
                            "flagged": len(result["flagged"])
                            + len(result.get("inventory", [])),
                            **scan_view.page(
                                rows, q.get("offset", 0), q.get("limit", 300)
                            ),
                        }
                    )
                if rest == ["live"]:
                    return self.send_json(live_status(name))
                if rest == ["context"]:
                    return self.send_json(mod_context(name))
                if rest == ["suggest"]:
                    return self.send_json(suggest(name, int(q.get("limit", 30))))
        return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def handle_post(self):
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        body = self.body_json()
        if len(parts) == 4 and parts[:2] == ["api", "mods"] and parts[3] == "snapshot":
            self.local_only()
            name = self.mod_name(parts[2])
            with action_scope():
                COORDINATOR.acquire(["mod:" + name], threading.Event())
                try:
                    with LOCK:
                        try:
                            arguments = (
                                dev.am_root() / "studio",
                                name,
                                dev.mod_dir(name),
                            )
                            action = body.get("action", "create")
                            if action == "create":
                                result = workspace.snapshot_create(
                                    *arguments, str(body.get("label", "manual"))
                                )
                            elif action == "preview":
                                result = workspace.snapshot_preview(
                                    *arguments, body.get("id")
                                )
                            elif action == "restore":
                                result = workspace.snapshot_restore(
                                    *arguments, body.get("id"), body.get("expected")
                                )
                                dev._dump_cache.clear()
                            else:
                                raise ValueError("Unknown snapshot action")
                        except (ValueError, OSError) as error:
                            raise dev.DevError(str(error)) from error
                finally:
                    COORDINATOR.release(["mod:" + name])
            return self.send_json(result)
        if parts in (["api", "studio", "restart"], ["api", "studio", "stop"]):
            self.local_only()
            with SESSION_LOCK:
                if busy():
                    raise dev.DevError(
                        "Finish or stop active jobs and configuration changes before restarting Studio"
                    )
                RESTARTING.set()
                self.server.restart_requested = parts[-1] == "restart"
            self.send_json({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if parts == ["api", "claude"]:
            self.local_only()
            return self.send_json(
                start_claude(
                    str(body.get("prompt", "")),
                    bool(body.get("read_only", True)),
                    str(body.get("engine", "claude")),
                    str(body.get("mod", "")),
                    str(body.get("effort", "")),
                ).view()
            )
        if parts == ["api", "console"]:
            self.local_only()
            return self.send_json(console(str(body.get("line", ""))).view())
        if parts == ["api", "store", "publish"]:
            self.local_only()
            return self.send_json(start_store_publish(str(body.get("mod", "")), str(body.get("action", "")),
                                                      str(body.get("confirm", ""))).view())
        if parts == ["api", "store", "shots", "image"]:
            # the Menu image page's PNG, as a link for the mod's post
            self.local_only()
            import base64
            import tempfile
            mod = self.mod_name(str(body.get("mod", "")))
            data = base64.b64decode(str(body.get("png", "")), validate=True)
            if not data.startswith(b"\x89PNG"):
                raise dev.DevError("not a PNG")
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / f"{mod}-menu.png"
                path.write_bytes(data)
                try:
                    shot = shots.upload_file(ROOT, path, str(body.get("ttl", "1y")), label=f"{mod} menu image")
                except shots.ShotsError as e:
                    raise dev.DevError(str(e)) from e
            state = dev.load_state(mod)
            state.setdefault("shots", []).insert(0, {"id": shot["id"], "url": shot["url"], "expires_at": shot["expires_at"],
                                                    "taken": int(time.time())})
            dev.save_state(mod, state)
            return self.send_json(shot)
        if len(parts) == 4 and parts[:3] == ["api", "store", "shots"] and parts[3] in ("phone", "renew", "delete"):
            self.local_only()
            try:
                if parts[3] == "phone":
                    return self.send_json(shots.phone_shot_for(ROOT, self.mod_name(str(body.get("mod", ""))),
                                                               str(body.get("ttl", "1y")), dev.load_state, dev.save_state))
                if parts[3] == "renew":
                    return self.send_json(shots.renew(ROOT, str(body.get("id", "")), str(body.get("ttl", "1y"))))
                result = shots.delete(ROOT, str(body.get("id", "")))
                for meta in (ROOT / "mods").glob("*/mod.json"):  # out of any mod's post links too
                    state = dev.load_state(meta.parent.name)
                    if any(s.get("id") == body.get("id") for s in state.get("shots", [])):
                        state["shots"] = [s for s in state["shots"] if s.get("id") != body.get("id")]
                        dev.save_state(meta.parent.name, state)
                return self.send_json(result)
            except shots.ShotsError as e:
                raise dev.DevError(str(e)) from e
        if parts == ["api", "store", "act"]:
            self.local_only()
            try:
                return self.send_json(store_view.act(dev.am_root(), body))
            except RuntimeError as e:
                raise dev.DevError(str(e)) from e
        if len(parts) == 4 and parts[:3] == ["api", "store", "link"] and parts[3] in ("start", "poll", "unlink"):
            self.local_only()
            act = {"start": store_view.link_start, "poll": store_view.link_poll, "unlink": store_view.unlink}[parts[3]]
            try:
                return self.send_json(act(dev.am_root()))
            except RuntimeError as e:
                raise dev.DevError(str(e)) from e
        if len(parts) == 4 and parts[:2] == ["api", "mods"] and parts[3] == "autobuild":
            self.local_only()
            return self.send_json(start_autobuild(self.mod_name(parts[2]), str(body.get("engine", "claude")),
                                                  int(body.get("limit", 10)), str(body.get("effort", ""))).view())
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "followup":
            self.local_only()
            job = JOBS.get(parts[2])
            if not job:
                return self.send_json({"error": "no such job"}, HTTPStatus.NOT_FOUND)
            return self.send_json(start_followup(job, str(body.get("mod", ""))).view())
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "input":
            self.local_only()
            job = JOBS.get(parts[2])
            if not job:
                return self.send_json({"error": "no such job"}, HTTPStatus.NOT_FOUND)
            job.send(str(body.get("text", "")))
            return self.send_json(job.view(len(job.lines)))
        if parts == ["api", "jobs"]:
            self.local_only()  # builds, signs and installs run on this PC: this PC's Studio page only
            command = body.get("command", "")
            argv = []
            # sign and install take a file, not a mod; new makes the mod; fetch takes an app
            if command not in ("new", "sign", "install", "fetch", "start", "report"):
                argv.append(self.mod_name(str(body.get("mod", ""))))
            argv += [str(a) for a in body.get("args", [])]
            job = start_job(command, argv)
            return self.send_json(job.view())
        if parts == ["api", "fetch", "login"]:
            self.local_only()
            user = fetch_client().login(
                str(body.get("email", "")), str(body.get("password", ""))
            )
            return self.send_json({"user": user})
        if parts == ["api", "fetch", "logout"]:
            self.local_only()
            fetch_client().logout()
            return self.send_json({"ok": True})
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "stop":
            self.local_only()
            job = JOBS.get(parts[2])
            if not job:
                return self.send_json({"error": "no such job"}, HTTPStatus.NOT_FOUND)
            job.stop()
            return self.send_json(job.view(len(job.lines)))
        if len(parts) == 4 and parts[:2] == ["api", "mods"] and parts[3] in QUICK:
            self.local_only()
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
    registry = dev.am_root() / "studio" / "instance.json"
    if registry.is_file():
        try:
            instance = json.loads(registry.read_text(encoding="utf-8"))
            port = int(instance["port"])
            if not 1 <= port <= 65535:
                raise ValueError("bad port")
            with urlopen(f"http://127.0.0.1:{port}/api/studio", timeout=2) as response:
                running = json.load(response)
            if running.get("protocol") == 1 and running.get("root") == str(ROOT):
                url = f"http://127.0.0.1:{port}/"
                print(f"Mod Studio is already running at {url}")
                if not args.no_open:
                    webbrowser.open(url)
                return 0
        except (OSError, ValueError, KeyError):
            pass
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    for record in jobs_state.History(dev.am_root() / "studio").load():
        JOBS[record["id"]] = RecoveredJob(record)
    workspace.atomic_json(
        registry, {"port": args.port, "pid": os.getpid(), "root": str(ROOT)}
    )
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
    server.server_close()
    # Remove only our own registration, without touching other applications or legacy servers.
    if registry.is_file() and json.loads(registry.read_text())["pid"] == os.getpid():
        registry.unlink()
    if getattr(server, "restart_requested", False):
        os.execv(
            sys.executable,
            [
                sys.executable,
                str(HERE / "server.py"),
                *(argv if argv is not None else sys.argv[1:]),
            ],
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
