"""Auto-build a mod's menu with the model picked in Mod Studio (Claude Code, Codex or aider), as one Studio job.

  python studio/autobuild.py <mod> [--engine claude|codex|aider] [--limit 10] [--no-install]

1. the scan's best no-JIT candidates not yet in the mod, without the topic-labelled ones (what the game sells,
   ads, accounts, online play) and without inspect-only rows;
2. the model picks up to --limit and answers with `am pin <mod> ...` lines only (read only: it changes nothing);
3. each line runs through dev.py pin (which checks it against the dump); the new switches get the scan's area as
   their menu section;
4. dev.py gen, then dev.py build <mod> --ipa --bump;
5. only when all of that went well: dev.py sign <the .ipa> --install, then 45 s of the device log with the game
   opened, so each new switch's status line shows in the job (the owner's choice, 2026-10-22).

Studio snapshots the mod's files first (Files tab restores them).
"""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(HERE))

import dev  # noqa: E402
import server  # noqa: E402  (the model command lines and the Claude stream parser)

PIN = re.compile(r"^am pin (\S+) (.+)$")
# networked code: a switch there could reach other players (no online cheats). FistFightingController is
# NuclearDay's online fight club (its offline twin, FistFighting_offline_Controller, stays a candidate).
ONLINE = re.compile(r"network|photon|mirror|online|multiplayer|pvp|lobby|matchmak|server|netcode|\brpc\b|fightclub"
                    r"|^fistfightingcontroller\b",
                    re.IGNORECASE)


def say(text):
    print(text, flush=True)


def candidates(mod, limit):
    _, rows = dev.scan_rows(mod)
    fresh = [r for r in rows if not r.get("has") and not r.get("flag") and not r.get("inspect_only")
             and r.get("area") != "In-app purchases" and not ONLINE.search(f"{r['class']} {r['object']}")]
    return fresh[:limit]


def prompt_for(mod, meta, rows, limit):
    return (
        f"Goal: pick the {limit} most useful new menu switches for {meta.get('display_name') or meta['name']} "
        f"(mod {mod}, game {meta['target_bundle_id']} {meta.get('tested_app_version', '')}) from Mod Studio's dump "
        "scan below. The phone has no JIT: every switch is a pin, a field write or setter call on live objects "
        "every half second.\n"
        f"Candidates (scored by names and types only):\n{dev.dump_scan.as_text(rows, len(rows))}\n\n"
        f"Answer with up to {limit} lines and nothing else, each exactly:\n"
        f"am pin {mod} <Class.member or Class.set_X> --object <object column> --value <value> "
        "[--when lower|higher] --as <camelCaseKey> --title \"<short menu title>\"\n"
        "Use only rows from the list, the object column as --object, a value that helps the player (a cap, a "
        "zero cost, a full stat), --when lower for stats that should stay at least that high. Skip anything about "
        "purchases, store currency, ads, accounts or online play, and skip rows that only look like UI or "
        "camera settings. No explanations, no other commands."
    )


def ask(engine, prompt):
    """The model's answer, line by line, echoed into the job as it comes."""
    if engine == "aider":
        cmd, parse = server.aider_command(prompt), None
    elif engine == "codex":
        cmd, parse = server.codex_command(prompt, True), None
    else:
        cmd, parse = server.claude_command(prompt, True), server.claude_lines
    env = dict(os.environ, COLUMNS="4000", PYTHONUTF8="1")  # aider wraps its answer at the terminal width
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", env=env)
    answer = []
    for raw in proc.stdout:
        for line in (parse(raw.rstrip("\n")) if parse else [raw.rstrip("\n")]):
            say(line)
            answer.append(line)
    proc.wait()
    return answer


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


def dev_py(*args):
    say("$ am " + " ".join(shlex.quote(a) for a in args))
    run = subprocess.run([sys.executable, str(ROOT / "scripts" / "dev.py"), *args], cwd=ROOT, text=True,
                         encoding="utf-8", errors="replace", capture_output=True)
    for line in (run.stdout + run.stderr).splitlines()[-12:]:
        say("  " + line)
    return run.returncode == 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mod")
    p.add_argument("--engine", choices=server.ENGINES, default="claude")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--no-install", action="store_true", help="stop after the build")
    args = p.parse_args(argv)
    mod, limit = args.mod, max(1, min(args.limit, 25))
    folder = dev.mod_dir(mod)
    meta = dev.read_json(folder / "mod.json")
    if not (folder / "tweaks.json").is_file():
        say(f"{mod} has a hand-written menu (no tweaks.json): auto-build adds pins to tweaks.json mods only")
        return 1

    say(f"==> 1/5 scan: the best no-JIT candidates for {mod}")
    rows = candidates(mod, 40)
    if not rows:
        say("nothing new to add: every scan candidate is in the mod already, labelled, or inspect-only")
        return 1
    say(f"    {len(rows)} candidates")

    who = {"aider": "aider (free model)", "codex": "Codex (GPT)"}.get(args.engine, "Claude Code")
    say(f"==> 2/5 {who} picks up to {limit}")
    answer = ask(args.engine, prompt_for(mod, meta, rows, limit))
    lines = []
    for line in answer:
        cmd = line.strip().strip("`").lstrip("$> ").strip()
        m = PIN.match(cmd)
        if m and m.group(1) == mod and "<" not in m.group(2).split(" --")[0] and "--object <" not in cmd and cmd not in lines:
            lines.append(cmd)
    lines = lines[:limit]
    if not lines:
        say("the model gave no am pin line for this mod: nothing added, nothing built")
        return 1

    say(f"==> 3/5 adding {len(lines)} switches")
    area = {f"{r['class']}.{r['member']}": r["area"] for r in rows}  # the scan's area becomes the menu section
    sections, added = {}, 0
    for cmd in lines:
        try:
            words = shlex.split(cmd)[1:]
        except ValueError as error:
            say(f"  skipped (bad quoting: {error}): {cmd}")
            continue
        if dev_py(*words):
            added += 1
            key = words[words.index("--as") + 1] if "--as" in words[:-1] else None
            if key and len(words) > 2 and words[2] in area:
                sections[key] = area[words[2]]
    path = folder / "tweaks.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for t in data["tweaks"]:
        if t["key"] in sections and not t.get("section"):
            t["section"] = sections[t["key"]]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    say(f"    {added} of {len(lines)} added")
    if not added:
        say("none of the model's lines passed dev.py pin: nothing to build")
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
    if not dev_py("sign", ipa, "--install"):
        say(f"==> built but not installed (is the phone on USB and unlocked?): {ipa}. Run installs it.")
        return 1
    stream_log(mod, 45)
    say(f"==> done: {added} new switches built and installed. Turn them on in the game: green dots work, red say why.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
