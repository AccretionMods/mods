"""Auto-build a mod's menu with the model picked in Mod Studio (Claude Code, Codex, aider or Gemini), as one Studio job.

  python studio/autobuild.py <mod> [--engine claude|codex|aider|gemini] [--effort low|medium|high] [--limit N]
                                   [--workers N] [--no-install]

1. the scan's best no-JIT pin candidates not yet in the mod, across all topics, without inspect-only rows;
2. the model goes through every candidate, BATCH rows at a time, several batches at once (WORKERS per engine), and
   answers with one JSON line per row worth a switch: the row's number, a value, when, a title (read only: it
   changes nothing). A batch that fails or answers nothing is asked once more. No top N unless --limit caps it;
3. Studio builds each pin from the scan row itself (class, member and object can't be mistyped), checks the value
   against the row's type and runs dev.py pin in this process (which checks it against the dump); the new
   switches get the scan's area as their menu section;
4. dev.py gen, then dev.py build <mod> --ipa --bump;
5. only when all of that went well: dev.py sign <the .ipa> --install, then 45 s of the device log with the game
   opened, so each new switch's status line shows in the job (the owner's choice, 2026-10-22).

Studio snapshots the mod's files first (Files tab restores them).
"""

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(HERE))

import dev  # noqa: E402
import dump_scan  # noqa: E402
import server  # noqa: E402  (the model command lines and the Claude stream parser)

PIN = re.compile(r"^am pin (\S+) (.+)$")  # older answers: whole commands, still taken
OBJECT = re.compile(r"\{[^{}]*\}")  # one flat JSON object; models wrap them in fences, lists or prose
# batches asked at once: the paid plans take more; the free tiers allow about 20 requests a minute (OpenRouter)
# or a day per model (Gemini), so they get two
WORKERS = {"claude": 3, "claude-api": 3, "codex": 3, "aider": 2, "gemini": 2}
RATE_LIMITED = re.compile(r"rate.?limit|RESOURCE_EXHAUSTED|\b429\b|quota|overloaded|high demand|\b503\b",
                          re.IGNORECASE)
# a subscription out of its weekly or monthly allowance: asking again only wastes the batch
OUT_OF_ALLOWANCE = re.compile(r"(weekly|monthly|daily|usage|session) limit|hit your .*limit|limit .*resets",
                              re.IGNORECASE)
OUT = threading.Lock()


def say(text):
    with OUT:
        print(text, flush=True)


BATCH = 50  # candidate rows per question: the whole scan in one prompt is too long for a model to read well
# Gemini reads long prompts well and its free tier counts questions, not length: fewer, bigger questions
BATCHES = {"gemini": 150}


def candidates(mod):
    """Every scan candidate a pin can reach, best first, excluding existing and inspect-only targets."""
    _, rows = dev.scan_rows(mod)
    # a family's other members ride on its representative (dump_scan._families): asked about once
    return [r for r in rows if not r.get("has") and not r.get("inspect_only") and not r.get("family_member")]


def fingerprint(row):
    """A candidate's identity across runs and game versions: what it reaches, not where it sits in the dump."""
    key = [row.get("class"), row.get("member"), row.get("ctype"), row.get("kind"), row.get("object"), row.get("path")]
    return hashlib.sha1(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]


def reviewed_path(mod):
    return dev.am_root() / "dev" / f"{mod}.reviewed.json"


def load_reviewed(mod):
    """{fingerprint: {"decision": "picked"|"passed", "target", "version", "engine", "at"}} from earlier runs."""
    try:
        return json.loads(reviewed_path(mod).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remember_reviewed(mod, reviewed, batches, answers, results, version, engine):
    """Every row of a batch the model really answered: picked or passed. A batch that failed, hit a rate limit or
    ran out of allowance is not recorded, so its rows are asked again next time."""
    today = time.strftime("%Y-%m-%d")
    for n, batch in enumerate(batches, 1):
        answer = answers.get(n) or []
        found, skipped = results.get(n, ([], []))
        if (not answer or "out of allowance" in skipped or answer[0].startswith("could not start")
                or any(RATE_LIMITED.search(a) or OUT_OF_ALLOWANCE.search(a) for a in answer)):
            continue
        chosen = {fingerprint(item[0]) for kind, item in found if kind == "row"}
        named = {item[3] for kind, item in found if kind == "cmd" and len(item) > 3}  # older "am pin" answers
        for row in batch:
            fp, target = fingerprint(row), f"{row['class']}.{row['member']}"
            reviewed[fp] = {"decision": "picked" if fp in chosen or target in named else "passed", "target": target,
                            "version": version, "engine": engine, "at": today}
    path = reviewed_path(mod)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reviewed, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def table(rows):
    """The batch as numbered rows: the model answers with the number, never retypes a name."""
    lines = ["# | area | object | member | type | kind | suggested value | learned elsewhere"]
    for n, r in enumerate(rows, 1):
        lr = r.get("learned")
        seen = f"like \"{lr['title']}\": {lr['works']} works, {lr['fails']} fails" if lr else ""
        typ = "FP (fixed point: write the plain number)" if r.get("scale") else r.get("ctype", "")
        fam = f" (and {r['family_size'] - 1} sibling classes)" if r.get("family_size") else ""
        lines.append(f"{n} | {r['area']} | {r['object']} | {r['class']}.{r['member']}{fam} | {typ} | {r.get('kind', '')} | "
                     f"{r.get('value', '')} {r.get('when', '')} | {seen}")
    return "\n".join(lines)


def prompt_for(mod, meta, rows):
    return (
        f"Goal: make a menu switch from every candidate below that is worth one, for {meta.get('display_name') or meta['name']} "
        f"(mod {mod}, game {meta['target_bundle_id']} {meta.get('tested_app_version', '')}) from Mod Studio's dump "
        "scan below. The phone has no JIT: every switch is a pin, a field write or setter call on live objects "
        "every half second.\n"
        f"Candidates (scored by names and types only):\n{table(rows)}\n\n"
        "Answer with one JSON object per line for each useful row (as many as there are, none when none are) and "
        "nothing else:\n"
        '{"row": <number from the # column>, "value": <number, or true/false for bool>, '
        '"when": "always"|"lower"|"higher", "title": "<short menu title>"}\n'
        "A value that helps the player (a cap, a zero cost, a full stat); \"lower\" for stats that should stay at "
        "least that high. For a value the game spends or lowers during play (current health, shield, ammo, energy "
        "left) answer \"value\": \"peak\" with \"when\": \"lower\": it holds the highest value seen, so health stays "
        "full whatever the game's maximum is. Use the stat's own scale, never 99 by habit: a multiplier 2, a "
        "cooldown, delay or cost 0 with \"higher\", a chance 1. Rows reached through the fight's objects (a path "
        "through the battle world) are the ones that change a fight. Topic labels are informational: purchases, store currency, ads, accounts and online "
        "play are in scope. Skip a row only when it lacks a useful effect or a technically supported pin path; "
        "names alone do not prove behaviour. No explanations, no commands."
    )


ACCOUNT = [""]  # claude-api: the account this run uses (--account)


def ask(engine, prompt, effort="", model=None):
    """The model's whole answer as lines, and whether the run went well (nothing printed: batches run at once)."""
    if engine == "aider":
        cmd, parse = server.aider_command(prompt, None, effort), None
    elif engine == "claude-api":
        cmd, parse = server.claude_api_command(prompt, ACCOUNT[0], effort), None
    elif engine == "gemini":
        cmd, parse = server.gemini_command(prompt, None, effort, model), None
    elif engine == "codex":
        cmd, parse = server.codex_command(prompt, True, effort, answer_only=True), None
    else:
        cmd, parse = server.claude_command(prompt, True, effort, answer_only=True), server.claude_lines
    env = dict(os.environ, COLUMNS="4000", PYTHONUTF8="1")  # aider wraps its answer at the terminal width
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", env=env)
    answer = []
    for raw in proc.stdout:
        answer += parse(raw.rstrip("\n")) if parse else [raw.rstrip("\n")]
    return answer, proc.wait() == 0


def picks(mod, rows, answer):
    """The model's choices in one batch's answer: (row, value, when, title) from JSON lines, or whole am pin
    commands (older answers). Returns the picks and the reasons lines were skipped."""
    found, skipped = [], []
    for line in answer:
        text = line.strip().strip("`").lstrip("$> ").strip()
        m = PIN.match(text)
        if m and m.group(1) == mod:
            try:
                argv = shlex.split(text)
                row = next((r for r in rows if f"{r['class']}.{r['member']}" == argv[3]), None)
                if row and "path" in row:
                    if "--via" in argv:
                        skipped.append("nested path answer cannot also use --via")
                        continue
                    argv += ["--object", row["object"], "--root-class", row["root_class"], "--path", json.dumps(row["path"])]
                found.append(("cmd", argv))
            except ValueError:
                skipped.append(f"bad quoting: {text[:120]}")
            continue
        for raw in OBJECT.findall(line):
            try:
                item = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(item, dict) or "row" not in item:
                continue
            try:
                n = int(item["row"])
            except (TypeError, ValueError):
                skipped.append(f"row is not a number: {raw[:120]}")
                continue
            if not 1 <= n <= len(rows):
                skipped.append(f"row {n} is not in this batch")
                continue
            if any(k == "row" and r is rows[n - 1] for k, (r, *_) in found if k == "row"):
                continue  # Codex prints its final answer twice
            found.append(("row", (rows[n - 1], item)))
    return found, skipped


def value_for(row, value):
    """The pin's --value for the row's type, or None when it doesn't fit (a word for a number, 2 for a bool)."""
    if row["ctype"] == "bool":
        if isinstance(value, bool):
            return "true" if value else "false"
        if str(value).strip().lower() in ("true", "false", "1", "0"):
            return "true" if str(value).strip().lower() in ("true", "1") else "false"
        return None
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "peak" if str(value).strip().lower() == "peak" else None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    if row["ctype"] not in ("float", "double") and not row.get("scale") and number != int(number):
        number = round(number)  # an int field: 99.5 -> 100
    return str(int(number)) if number == int(number) else repr(number)


def key_for(title, member, taken):
    """camelCase from the title (Full health -> fullHealth), numbered when it is taken."""
    words = re.findall(r"[A-Za-z0-9]+", title) or re.findall(r"[A-Za-z0-9]+", member)
    base = (words[0].lower() + "".join(w[:1].upper() + w[1:] for w in words[1:]))[:40] if words else "tweak"
    if not base[0].isalpha():
        base = "t" + base
    key, k = base, 2
    while key in taken:
        key, k = f"{base}{k}", k + 1
    taken.add(key)
    return key


def pin_argv(mod, row, item, taken):
    """dev.py pin's arguments for a picked row, or the reason it can't be one."""
    value = value_for(row, item.get("value"))
    if value is None:
        return None, f"{row['class']}.{row['member']}: {item.get('value')!r} doesn't fit {row['ctype']}"
    when = item.get("when") if item.get("when") in ("always", "lower", "higher") else "always"
    title = str(item.get("title") or row["name"]).strip()[:48]
    argv = ["pin", mod, f"{row['class']}.{row['member']}", "--object", row["object"], "--value", value,
            "--when", when, "--as", key_for(title, row["member"], taken), "--title", title]
    if row["kind"] == "field" and row["member"].startswith("set_"):
        argv.append("--field")
    if "path" in row:
        argv += ["--root-class", row["root_class"], "--path", json.dumps(row["path"])]
    return argv, None


def pin_here(argv):
    """dev.py pin in this process: no new Python per switch, and no gen per switch (step 4 runs it once)."""
    out = io.StringIO()
    real_gen = dev.cmd_gen
    dev.cmd_gen = lambda args: 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            args = dev.build_parser().parse_args(argv)
            args.fn(args)
        return True, out.getvalue()
    except dev.DevError as error:
        return False, str(error)
    except SystemExit:  # argparse refused the arguments; its message is in out
        return False, out.getvalue().strip() or "bad arguments"
    finally:
        dev.cmd_gen = real_gen


GEMINI = {"at": 0}  # which of server.GEMINI_FALLBACKS answers now; a model out of free quota moves it on


def ask_batch(engine, effort, mod, meta, batch):
    """One batch, asked once more when the run fails or nothing usable comes back (after a pause on a rate limit).
    Gemini: a model out of its free daily quota hands over to the next free model, as often as there are models."""
    prompt = prompt_for(mod, meta, batch)
    tries = []
    attempts = 2 + (len(server.GEMINI_FALLBACKS) if engine == "gemini" else 0)
    for attempt in range(1, attempts + 1):
        model = None
        if engine == "gemini":
            with OUT:
                at = GEMINI["at"]
            if at >= len(server.GEMINI_FALLBACKS):
                return [f"every free Gemini model is out of today's quota ({', '.join(server.GEMINI_FALLBACKS)})"], [], [], attempt
            model = server.GEMINI_FALLBACKS[at]
        try:
            answer, ok = ask(engine, prompt, effort, model)
        except (OSError, dev.DevError) as error:
            answer, ok = [f"could not start the model: {error}"], False
        found, skipped = picks(mod, batch, answer)
        tries.append(answer)
        limited = any(RATE_LIMITED.search(a) for a in answer)
        if engine != "gemini" and any(OUT_OF_ALLOWANCE.search(a) for a in answer):
            return answer, [], ["out of allowance"], attempt
        if ok and (found or not limited):
            return answer, found, skipped, attempt
        if engine == "gemini" and limited and any(re.search(r"PerDay|per day|quota", a, re.I) for a in answer):
            with OUT:
                if GEMINI["at"] == at:  # another batch may have moved it on already
                    GEMINI["at"] += 1
                    print(f"  ({model} is out of today's free quota: next {server.GEMINI_FALLBACKS[GEMINI['at']] if GEMINI['at'] < len(server.GEMINI_FALLBACKS) else 'none'})", flush=True)
            continue
        if attempt >= 2:
            break
        time.sleep(30 if limited else 5)
    return tries[-1], found, skipped, len(tries)


def stream_log(mod, seconds):
    """The device log with the game (re)opened, its status and hook-report lines echoed as they come."""
    say(f"$ am log {mod} --seconds {seconds} --launch")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "dev.py"), "log", mod, "--seconds", str(seconds),
                             "--launch"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", env=env)
    for line in proc.stdout:
        if re.search(r"status |hooks:|pin .* applied|error|crash", line, re.IGNORECASE):
            say("  " + line.rstrip()[-200:])
    return proc.wait() == 0


NUMBERS = {"int32_t", "int64_t", "float", "double"}


def probe(row):
    """A live pin that only reads a scan row's target (never turned on): what am snap reads for it."""
    pin = {"object": row["object"], "ctype": row["ctype"], "value": 0, "when": "always"}
    member = row["member"]
    pin["setter" if row["kind"] == "setter" else "field"] = member
    if row.get("path") is not None and row.get("root_class"):
        pin.update(root_class=row["root_class"], path=row["path"], **{"class": row["class"]})
    return pin


def measure(mod, rows, wait=8):
    """The live values of rows' targets in the running game: {target: (objects, values from two readings)}, read
    by the build's AMLivePins snapshot (am snap) through a live file of read-only probes. The phone's own live file
    goes back afterwards. Needs the game open in play on the USB phone."""
    bundle = dev.installed_id(mod)
    if not bundle:
        raise dev.DevError(f"{mod} is not on the phone: install it first (Run), then open a game")
    cli, work = dev.pymobiledevice3_cli(), dev.am_root() / "work"
    work.mkdir(parents=True, exist_ok=True)
    keys = {f"measure{i}": f"{r['class']}.{r['member']}" for i, r in enumerate(rows)}
    tweaks = [{"key": k, "title": k, "kind": "switch", "hooks": [], "pins": [probe(r)]} for k, r in zip(keys, rows)]
    prior, live, snap = (work / f"measure-{mod}-{name}.json" for name in ("before", "live", "snap"))
    prior.unlink(missing_ok=True)
    had = subprocess.run([cli, "apps", "pull", bundle, "Documents/live_tweaks.json", str(prior)],
                         capture_output=True, text=True, check=False).returncode == 0 and prior.is_file()
    readings = []
    try:
        for n in range(2):
            if n:
                time.sleep(wait)  # the game moves on between the readings
            sid = f"measure-{int(time.time())}-{n}"
            live.write_text(json.dumps({"title": "measure", "tweaks": tweaks, "snapshot": {"id": sid}}), encoding="utf-8")
            subprocess.run([cli, "apps", "push", bundle, str(live), "Documents/live_tweaks.json"],
                           capture_output=True, text=True, check=True)
            for _ in range(10):
                time.sleep(2)
                snap.unlink(missing_ok=True)
                subprocess.run([cli, "apps", "pull", bundle, "Documents/am_snapshot.json", str(snap)],
                               capture_output=True, text=True, check=False)
                try:
                    data = json.loads(snap.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if data.get("id") == sid:
                    readings.append(data["values"])
                    break
            else:
                raise dev.DevError("the game sent no snapshot: is it open (a build from 1.0.12 on)?")
    finally:  # the phone's own live switches back, or none
        if had:
            subprocess.run([cli, "apps", "push", bundle, str(prior), "Documents/live_tweaks.json"],
                           capture_output=True, text=True, check=False)
        else:
            subprocess.run([cli, "apps", "rm", bundle, "Documents/live_tweaks.json"],
                           capture_output=True, text=True, check=False)
    return {target: (max(r.get(k, {}).get("objects", 0) for r in readings),
                     [v for r in readings for v in r.get(k, {}).get("values", [])]) for k, target in keys.items()}


def dead(row, objects, values):
    """A number that reads exactly 0 on every object in both readings: a value the game doesn't use (9 Kings'
    Entity._data.speed), or one it sets only in another phase: 9 Kings' PlayData.walkSpeed is 0 between fights
    and 1.1-16 during one, so measure where the switch matters. Booleans are often false; a target with no object
    now can't be judged."""
    return row["ctype"] in NUMBERS and objects > 0 and bool(values) and all(v == 0 for v in values)


def dev_py(*args):
    say("$ am " + " ".join(shlex.quote(a) for a in args))
    run = subprocess.run([sys.executable, str(ROOT / "scripts" / "dev.py"), *args], cwd=ROOT, text=True,
                         encoding="utf-8", errors="replace", capture_output=True, check=False)
    for line in (run.stdout + run.stderr).splitlines()[-12:]:
        say("  " + line)
    return run.returncode == 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mod")
    p.add_argument("--engine", choices=server.ENGINES, default="claude")
    p.add_argument("--effort", choices=[e for e in server.EFFORTS if e], help="how hard the model thinks")
    p.add_argument("--account", default="", help="claude-api: which saved account (default: the first)")
    p.add_argument("--limit", type=int, default=0, help="at most this many switches (0: every usable one)")
    p.add_argument("--workers", type=int, default=0, help="batches asked at once (default: per engine)")
    p.add_argument("--no-install", action="store_true", help="stop after the build")
    p.add_argument("--measure", action="store_true",
                   help="read the picks' live values in the running game first (phone on USB, game open in play) "
                        "and list numbers that read 0 on every object")
    p.add_argument("--measure-drop", action="store_true",
                   help="like --measure, and leave those out: measure where the switches matter (in a fight for "
                        "combat values), since some games zero a value between phases")
    p.add_argument("--skip-flagged", action="store_true",
                   help="leave out rows labelled shop, ads, accounts or online (and the In-app purchases area)")
    p.add_argument("--areas", help="only these scan areas, comma-separated (Survival,Combat,Progress...)")
    p.add_argument("--with-presentation", action="store_true",
                   help="also ask about option, UI, camera and effect classes (left out by default)")
    p.add_argument("--again", action="store_true",
                   help="also ask about candidates a model already passed on (same target, any earlier version)")
    args = p.parse_args(argv)
    mod, limit = args.mod, max(0, args.limit)
    folder = dev.mod_dir(mod)
    meta = dev.read_json(folder / "mod.json")
    if not (folder / "tweaks.json").is_file():
        say(f"{mod} has a hand-written menu (no tweaks.json): auto-build adds pins to tweaks.json mods only")
        return 1

    say(f"==> 1/5 scan: every no-JIT candidate for {mod}")
    rows = candidates(mod)
    if args.skip_flagged:
        rows = [r for r in rows if not r.get("flag") and r.get("area") != "In-app purchases"]
    if args.areas:
        wanted = {a.strip().lower() for a in args.areas.split(",") if a.strip()}
        rows = [r for r in rows if r.get("area", "").lower() in wanted]
    if not args.with_presentation:  # options, UI widgets, cameras, effects: the scan ranks them low, the model skips them
        before = len(rows)
        package = sum(1 for r in rows if r.get("package"))
        if package > len(rows) * 0.6:
            say(f"    note: {package} of {len(rows)} candidates are a bought package (animation, tweening...): the game's "
                "own code is probably in an assembly the scan did not read (dev.py scan shows the unread ones)")
        rows = [r for r in rows if not r.get("presentation") and not r.get("package")]
        if before - len(rows):
            say(f"    {before - len(rows)} presentation and package rows (options, UI, camera, effects, bought packages) "
                "left out (--with-presentation asks)")
    reviewed = load_reviewed(mod)
    if reviewed and not args.again:  # a new game version or a second run: only what no model has looked at yet
        before = len(rows)
        rows = [r for r in rows if fingerprint(r) not in reviewed]
        if before - len(rows):
            say(f"    {before - len(rows)} reviewed in an earlier run (same target): not asked again (--again asks)")
    if not rows:
        say("nothing new to add: every scan candidate is in the mod already, inspect-only or reviewed before")
        return 1
    say(f"    {len(rows)} candidates")
    # where they come from, so a scan of the wrong code shows before any model time is spent (Survivor: Ace's first
    # run read Assembly-CSharp, which held only bought packages and their demos: 80 switches that did nothing)
    where = {}
    for r in rows:
        asm = r["class"].split(":", 1)[0] if ":" in r["class"] else "Assembly-CSharp"
        where[asm] = where.get(asm, 0) + 1
    say("    from " + ", ".join(f"{a} {n}" for a, n in sorted(where.items(), key=lambda x: -x[1])))
    if len(rows) < 100:  # few rows: the game's code may sit in an assembly the scan didn't read
        read = set(where)
        big = [f"{name} ({n} types)" for n, name in dump_scan.assembly_sizes(dev.dump_path(mod))[:6] if name not in read]
        if big:
            say("    note: only a few candidates. Largest assemblies the scan did not read: " + ", ".join(big)
                + '. If the game code is in one of them, add it to mod.json "scan_assemblies" and run again')
    other = sum(1 for r in rows if r["area"] == "Other")
    if other > len(rows) * 0.7:
        say(f"    note: {other} of {len(rows)} are in no gameplay area (names only): check the scan's assemblies "
            "(mod.json scan_assemblies) if the picks look like effects or UI")

    ACCOUNT[0] = args.account
    who = {"aider": "aider (free model)", "codex": "Codex (GPT)", "claude-api": f"Claude API ({args.account or 'first account'})",
           "gemini": f"Gemini ({server.GEMINI_MODEL})"}.get(args.engine, "Claude Code")
    if args.effort:
        who += f", {args.effort} effort"
    size = BATCHES.get(args.engine, BATCH)
    batches = [rows[i:i + size] for i in range(0, len(rows), size)]
    workers = max(1, min(args.workers or WORKERS.get(args.engine, 2), len(batches)))
    say(f"==> 2/5 {who} goes through all {len(rows)} in {len(batches)} batch(es) of up to {size}, {workers} at once")
    results, answers = {}, {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = {pool.submit(ask_batch, args.engine, args.effort or "", mod, meta, b): n
                for n, b in enumerate(batches, 1)}
        for done, future in enumerate(as_completed(jobs), 1):
            n = jobs[future]
            answer, found, skipped, attempts = future.result()
            results[n] = (found, skipped)
            answers[n] = answer
            with OUT:  # the whole batch together, so batches running at once don't interleave
                print(f"--- batch {done}/{len(batches)} done: batch {n}, {len(found)} picked"
                      + (f", {len(skipped)} skipped" if skipped else "")
                      + (" (asked twice)" if attempts > 1 else ""), flush=True)
                for line in answer:
                    print("  " + line, flush=True)
                for why in skipped:
                    print(f"  skipped: {why}", flush=True)

    remember_reviewed(mod, reviewed, batches, answers, results, meta.get("tested_app_version"), args.engine)
    say("==> 3/5 adding the picked switches")
    tweaks = dev.mod_tweaks.load(folder)
    taken = {t["key"] for t in tweaks.get("tweaks", [])}
    pins, targets, picked = [], set(), {}
    for n in sorted(results):  # batch order: the scan's best rows first
        for kind, what in results[n][0]:
            if kind == "row":
                row, item = what
                argv_, why = pin_argv(mod, row, item, taken)
                if why:
                    say(f"  skipped: {why}")
                    continue
                target = row
            else:
                argv_ = what[1:]  # an older-style `am pin` line: its own words
                if "--as" in argv_[:-1]:  # the same key from two batches: number the second
                    i = argv_.index("--as") + 1
                    base, k = argv_[i], 2
                    while argv_[i] in taken:
                        argv_[i], k = f"{base}{k}", k + 1
                    taken.add(argv_[i])
                target = {"class": "", "member": argv_[2] if len(argv_) > 2 else "", "area": None}
            name = argv_[2] if len(argv_) > 2 else ""
            if name in targets:
                continue  # one switch per field or setter
            targets.add(name)
            pins.append((argv_, target.get("area")))
            if kind == "row":
                picked[name] = target
            for sib in target.get("family") or []:  # the same field on its sibling classes: one switch for all
                if "--as" not in argv_[:-1]:
                    break
                more = ["pin", mod, f"{sib['class']}.{sib['member']}", "--object", sib["object"],
                        "--value", argv_[argv_.index("--value") + 1], "--when", argv_[argv_.index("--when") + 1],
                        "--to", argv_[argv_.index("--as") + 1]]
                if argv_[-1] == "--field" or "--field" in argv_:
                    more.append("--field")
                if "path" in sib:
                    more += ["--root-class", sib["root_class"], "--path", json.dumps(sib["path"])]
                targets.add(more[2])
                pins.append((more, None))
    if (args.measure or args.measure_drop) and picked:
        say(f"==> measuring {len(picked)} picks in the running game (two readings 8 s apart)")
        try:
            live = measure(mod, list(picked.values()))
        except (dev.DevError, OSError, subprocess.CalledProcessError) as error:
            say(f"  not measured ({error}): every pick is kept")
            live = {}
        zero = {name for name, row in picked.items() if name in live and dead(row, *live[name])}
        unseen = sum(1 for name in picked if live.get(name, (0, []))[0] == 0)
        for name in sorted(zero):
            say(f"  reads 0 on every object right now: {name} (unused, or set only in another phase such as a fight)"
                + (": left out" if args.measure_drop else ": kept"))
        if args.measure_drop:  # measured where the switches matter: zeros are values the game doesn't use
            dropped_keys = {a[a.index("--as") + 1] for a, _ in pins if a[2] in zero and "--as" in a[:-1]}
            pins = [(a, s) for a, s in pins
                    if a[2] not in zero and not ("--to" in a and a[a.index("--to") + 1] in dropped_keys)]
        if live:
            say(f"    {len(zero)} read 0 ({'left out' if args.measure_drop else 'kept'}), {len(picked) - len(zero)} live"
                + (f"; {unseen} had no object in play right now (kept, not judged)" if unseen else ""))
    if limit:
        pins = pins[:limit]
    if not pins:
        if results and all("out of allowance" in skipped for _, skipped in results.values()):
            say(f"{who} is out of its usage allowance (see its message above): nothing added. Run again later or "
                "with another engine (--engine gemini, codex or aider)")
            return 1
        say("the model picked nothing usable for this mod: nothing added, nothing built")
        return 1
    area = {f"{r['class']}.{r['member']}": r["area"] for r in rows}  # the scan's area becomes the menu section
    sections, added = {}, 0
    for i, (argv_, row_area) in enumerate(pins, 1):
        say(f"--- switch {i}/{len(pins)}")
        say("$ am " + shlex.join(argv_))
        ok, text = pin_here(argv_)
        for line in text.splitlines()[-4:]:
            say("  " + line)
        if ok:
            added += 1
            key = argv_[argv_.index("--as") + 1] if "--as" in argv_[:-1] else None
            section = row_area or area.get(argv_[2] if len(argv_) > 2 else "")
            if key and section:
                sections[key] = section
    data = dev.mod_tweaks.load(folder)
    for t in data["tweaks"]:
        if t["key"] in sections and not t.get("section"):
            t["section"] = sections[t["key"]]
    dev.save_tweaks(folder, data)
    say(f"    {added} of {len(pins)} added")
    if not added:
        say("none of the picks passed dev.py pin: nothing to build")
        return 1

    say("==> 4/5 gen and build")
    if not (dev_py("gen", mod) and dev_py("build", mod, "--ipa", "--bump")):
        say("the build failed: the switches are in tweaks.json; fix or remove them (Files tab restores the snapshot)")
        return 1
    ipa = dev.load_state(mod).get("last_ipa")
    if args.no_install or not ipa:
        say(f"==> done: {added} new switches built. Run installs it on the phone; the dots show which ones work.")
        return 0
    say("==> 5/5 install on the phone and watch the new switches")
    if not dev_py("sign", ipa, "--install", "--mod", mod):
        say(f"==> built but not installed (is the phone on USB and unlocked?): {ipa}. Run installs it.")
        return 1
    stream_log(mod, 45)
    say(f"==> done: {added} new switches built and installed. Turn them on in the game: green dots work, red say why.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
