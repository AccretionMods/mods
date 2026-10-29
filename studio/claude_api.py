"""Claude through Anthropic's Messages API, for Mod Studio's "Claude API" engine (Auto-build and prompts).

Answers only: no tools, no files, no commands; the prompt is the whole job (the am commands it writes become
buttons in Studio's Jobs, as with aider). The answer streams to stdout as it is written.

Accounts: each is a name with an Anthropic API key in Windows Credential Manager ("AccretionMods/ai/<name>"),
never in a file or the repo; the list of names (and each one's model) is <AM_ROOT>/dev/ai-accounts.json.

  python studio/claude_api.py add <name> [--model claude-sonnet-5-5] [--workspace wrkspc_...]   key: hidden input
  python studio/claude_api.py workspace <name> <wrkspc_...>    for a key not scoped to a workspace
  python studio/claude_api.py list | remove <name>
  python studio/claude_api.py ask --account <name> --prompt-file <file> [--model M] [--effort low|medium|high]
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import dev  # noqa: E402
import ipa_fetch  # noqa: E402  (its Vault is the Windows Credential Manager wrapper)

API = "https://api.anthropic.com/v1/messages"
MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5")
DEFAULT_MODEL = "claude-sonnet-5-5"  # owner's choice, 2026-10-26: good at picking switches, cheaper per batch
MAX_TOKENS = {"low": 4000, "medium": 8000, "high": 16000}
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,39}$")
KEY = re.compile(r"^sk-ant-[A-Za-z0-9_-]{20,200}$")
WORKSPACE = re.compile(r"^wrkspc_[A-Za-z0-9]{8,64}$")


class APIError(Exception):
    pass


def accounts_path():
    return dev.am_root() / "dev" / "ai-accounts.json"


def accounts():
    """[{"name", "engine": "claude-api", "model"}], in the order they were added."""
    try:
        return json.loads(accounts_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(rows):
    accounts_path().parent.mkdir(parents=True, exist_ok=True)
    accounts_path().write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")


def _vault(name):
    vault = ipa_fetch.Vault("x")
    vault.target = f"AccretionMods/ai/{name}"
    return vault


def add(name, key, model=DEFAULT_MODEL, workspace=""):
    if not NAME.match(name or ""):
        raise APIError("an account name: letters, digits, spaces, . _ - (up to 40)")
    if not KEY.match(key or ""):
        raise APIError("that doesn't look like an Anthropic API key (sk-ant-...): nothing saved")
    if model not in MODELS:
        raise APIError(f"model: one of {', '.join(MODELS)}")
    if workspace and not WORKSPACE.match(workspace):
        raise APIError("a workspace ID looks like wrkspc_... (Console > Settings > Workspaces)")
    _vault(name).save("apiKey", key)
    row = {"name": name, "engine": "claude-api", "model": model}
    if workspace:
        row["workspace"] = workspace  # an ID, not a secret: keys not scoped to one must name it
    _save([r for r in accounts() if r["name"] != name] + [row])


def set_workspace(name, workspace):
    if not WORKSPACE.match(workspace or ""):
        raise APIError("a workspace ID looks like wrkspc_... (Console > Settings > Workspaces)")
    rows = accounts()
    if not any(r["name"] == name for r in rows):
        raise APIError(f"no account {name!r}")
    for r in rows:
        if r["name"] == name:
            r["workspace"] = workspace
    _save(rows)


def remove(name):
    _vault(name).forget()
    _save([r for r in accounts() if r["name"] != name])


def key_of(name):
    saved = _vault(name).load()
    if not saved or not saved[1]:
        raise APIError(f"no API key saved for account {name!r}: python studio/claude_api.py add {name!r}")
    return saved[1]


def ask(prompt, account, model=None, effort="", say=print, opener=None):
    """Streams Claude's answer to say(), line by line; returns the whole text."""
    row = next((r for r in accounts() if r["name"] == account), None)
    if row is None:
        raise APIError(f"no account {account!r} (python studio/claude_api.py list)")
    body = {"model": model or row.get("model") or DEFAULT_MODEL, "max_tokens": MAX_TOKENS.get(effort, 8000),
            "stream": True, "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(API, data=json.dumps(body).encode(), method="POST", headers={
        "x-api-key": key_of(account), "anthropic-version": "2023-06-01", "content-type": "application/json",
        **({"anthropic-workspace-id": row["workspace"]} if row.get("workspace") else {})})
    text, line = [], ""
    try:  # `from None`: the request carries the key and must not end up in a traceback
        with (opener or urllib.request.urlopen)(req, timeout=600) as r:
            for raw in r:
                raw = raw.decode("utf-8", "replace").strip()
                if not raw.startswith("data:"):
                    continue
                ev = json.loads(raw[5:].strip() or "{}")
                if ev.get("type") == "error":
                    raise APIError((ev.get("error") or {}).get("message") or "the API sent an error")
                delta = (ev.get("delta") or {}).get("text") if ev.get("type") == "content_block_delta" else None
                if delta:
                    text.append(delta)
                    line += delta
                    while "\n" in line:
                        out, line = line.split("\n", 1)
                        say(out)
    except urllib.error.HTTPError as e:
        try:
            why = (json.loads(e.read() or b"{}").get("error") or {}).get("message")
        except ValueError:
            why = None
        raise APIError(f"Anthropic API answered {e.code}: {why or e.reason}") from None
    except (urllib.error.URLError, OSError) as e:
        raise APIError(f"couldn't reach the Anthropic API ({getattr(e, 'reason', e)})") from None
    if line:
        say(line)
    return "".join(text)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("add")
    s.add_argument("name")
    s.add_argument("--model", default=DEFAULT_MODEL, choices=MODELS)
    s.add_argument("--workspace", default="", help="wrkspc_...: for a key not scoped to a workspace")
    s = sub.add_parser("workspace")
    s.add_argument("name")
    s.add_argument("workspace")
    sub.add_parser("list")
    s = sub.add_parser("remove")
    s.add_argument("name")
    s = sub.add_parser("ask")
    s.add_argument("--account", required=True)
    s.add_argument("--prompt-file", required=True)
    s.add_argument("--model", choices=MODELS)
    s.add_argument("--effort", default="")
    args = p.parse_args(argv)
    try:
        if args.cmd == "add":
            import getpass

            add(args.name, getpass.getpass("Anthropic API key (hidden, kept in Windows Credential Manager): ").strip(),
                args.model, args.workspace)
            print(f"saved account {args.name!r} ({args.model}); Studio's Claude API engine can use it")
        elif args.cmd == "workspace":
            set_workspace(args.name, args.workspace)
            print(f"account {args.name!r} now names workspace {args.workspace}")
        elif args.cmd == "list":
            for r in accounts():
                print(f"{r['name']}  {r['model']}" + (f"  {r['workspace']}" if r.get("workspace") else ""))
        elif args.cmd == "remove":
            remove(args.name)
            print(f"removed {args.name!r} and its key")
        else:
            prompt = Path(args.prompt_file).read_text(encoding="utf-8")
            try:
                os.remove(args.prompt_file)  # a one-off file Studio wrote for this run
            except OSError:
                pass
            ask(prompt, args.account, args.model, args.effort, say=lambda s: print(s, flush=True))
    except (APIError, ipa_fetch.FetchError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
