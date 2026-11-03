"""The live phone loop, for scripts/dev.py (dev.py push, snap, snap-diff, log, test; Studio's phone panel): pinned
switches pushed into the running game as Documents/live_tweaks.json, snapshots of their values, the mod's
device-log lines, and test rounds that turn switches on one section at a time.

dev.py re-exports every name here, so `dev.cmd_test` and the rest keep working. This module reaches dev.py's
shared basics (mod_dir, read_json, am_root...) as `dev.<name>` and the phone tools as `dev_signing.<name>`
when a function runs, so a test or Studio that patches dev.mod_dir or dev_signing.pymobiledevice3_cli reaches
this code too. Patch this module's own functions (push_live, stream_log...) on dev_live, not on dev.
"""

import json
import re
import subprocess
import time

import dev_learn
import dev_signing
import mod_tweaks


# ---------------------------------------------------------------- push / snap / log / test


def push_live(mod, bundle, test=None, snapshot=None):
    """The mod's pinned tweaks (and an am test / am snap block) to the phone as Documents/live_tweaks.json."""
    folder = dev.mod_dir(mod)
    tweaks = dev.load_tweaks(folder, dev.read_json(folder / "mod.json"))
    live = {"title": tweaks.get("title"), "tweaks": [t for t in tweaks.get("tweaks", []) if t.get("pins")]}
    if test:
        live["test"] = test
    if snapshot:
        live["snapshot"] = snapshot
    tmp = dev.am_root() / "work" / f"live_tweaks-{mod}.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(live), encoding="utf-8")
    r = subprocess.run([dev_signing.pymobiledevice3_cli(), "apps", "push", bundle, str(tmp), "Documents/live_tweaks.json"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise dev.DevError(f"push failed: {(r.stderr or r.stdout).strip()[-300:]}")
    return live


def cmd_push(args):
    """tweaks.json into the installed app as Documents/live_tweaks.json: the build's runtime (AMLivePins.h) reads
    it within 2 s, runs its pins instead of the compiled ones, and adds new switches to the menu. --off removes
    it, so the build's own switches apply again. Needs a build that has AMLivePins (built after it existed)."""
    folder = dev.mod_dir(args.mod)
    meta = dev.read_json(folder / "mod.json")
    found = [b for b in [dev_signing.installed_id(args.mod, meta)] if b]  # the verified installed ID (install mode aware)
    if not found:
        raise dev.DevError(f"{args.mod} is not on the phone (dev.py run {args.mod})")
    cli = dev_signing.pymobiledevice3_cli()
    if args.off:
        r = subprocess.run([cli, "apps", "rm", found[0], "Documents/live_tweaks.json"], capture_output=True, text=True)
        print("==> live switches removed: the build's own switches apply again" if r.returncode == 0
              else f"==> nothing removed ({(r.stderr or r.stdout).strip().splitlines()[-1:] or ['no file']})")
        return 0
    if not args.no_check:
        problems = dev.pin_problems_in_dump(args.mod)
        dev.print_pin_problems(problems)
        if any(p[0] == "error" for p in problems):
            raise dev.DevError("pins that can't work (above): fix them, or push anyway with --no-check")
    live = push_live(args.mod, found[0])
    print(f"==> {len(live['tweaks'])} switches pushed to {found[0]}: the game picks them up within 2 s, no rebuild")
    if args.launch:
        dev_signing.launch_app(found[0])
    return 0


def snaps_dir(mod):
    path = dev.am_root() / "dev" / "snaps" / mod
    path.mkdir(parents=True, exist_ok=True)
    return path


def cmd_snap(args):
    """Reads the current value of every pushed switch's target in the running game (AMLivePins.h snapshot) into
    AM_ROOT/dev/snaps/<mod>/<label>.json. Take one, do something in play (get hit, level up, pick a skill), take
    another, then am snap-diff shows which values the game really uses: the switches worth keeping."""
    folder = dev.mod_dir(args.mod)
    meta = dev.read_json(folder / "mod.json")
    found = [b for b in [dev_signing.installed_id(args.mod, meta)] if b]  # the verified installed ID (install mode aware)
    if not found:
        raise dev.DevError(f"{args.mod} is not on the phone (dev.py run {args.mod})")
    if not re.fullmatch(r"[\w.-]{1,40}", args.label):
        raise dev.DevError("label: letters, digits, dots and dashes")
    snap_id = f"{args.label}-{int(time.time())}"
    push_live(args.mod, found[0], snapshot={"id": snap_id})
    cli, local = dev_signing.pymobiledevice3_cli(), snaps_dir(args.mod) / f"{args.label}.json"
    print(f"==> asked the game for snapshot {snap_id} (the game must be open)")
    for _ in range(int(args.wait / 2)):
        time.sleep(2)
        r = subprocess.run([cli, "apps", "pull", found[0], "Documents/am_snapshot.json", str(local)],
                           capture_output=True, text=True)
        try:
            data = json.loads(local.read_text(encoding="utf-8")) if r.returncode == 0 else {}
        except (OSError, ValueError):
            data = {}
        if data.get("id") == snap_id:
            values = data["values"]
            read = sum(1 for v in values.values() if v["values"])
            print(f"==> {local}: {len(values)} switches, {read} with a value read now")
            return 0
    raise dev.DevError("no snapshot came back: is the game open (with a build from 1.0.12 on)?")


def cmd_snap_diff(args):
    """The switches whose value changed between two snapshots (am snap), most objects changed first."""
    a, b = (json.loads((snaps_dir(args.mod) / f"{x}.json").read_text(encoding="utf-8"))["values"]
            for x in (args.before, args.after))
    tweaks = {t["key"]: t for t in dev.load_tweaks(dev.mod_dir(args.mod), dev.read_json(dev.mod_dir(args.mod) / "mod.json"))["tweaks"]}
    changed, only = [], 0
    for key in sorted(set(a) | set(b)):
        va, vb = (a.get(key) or {}).get("values", []), (b.get(key) or {}).get("values", [])
        if not va and not vb:
            continue
        if va != vb:
            changed.append((key, va[:4], vb[:4]))
        else:
            only += 1
    for key, va, vb in changed:
        title = tweaks.get(key, {}).get("title", "")
        print(f"  {key:<44} {title[:40]:<40} {va} -> {vb}")
    print(f"==> {len(changed)} changed, {only} read but unchanged, between {args.before} and {args.after}")
    out = snaps_dir(args.mod) / f"changed-{args.before}-{args.after}.txt"
    out.write_text("\n".join(k for k, _, _ in changed) + "\n", encoding="utf-8")
    print(f"==> keys in {out}")
    return 0


def cmd_log(args):
    folder = dev.mod_dir(args.mod)
    meta = dev.read_json(folder / "mod.json")
    if getattr(args, "launch", False):
        found = [b for b in [dev_signing.installed_id(args.mod, meta)] if b]  # the verified installed ID (install mode aware)
        if not found:
            raise dev.DevError(
                f"{meta['bundle_id']} is not on the phone (dev.py sign <ipa> --install)"
            )
        dev_signing.launch_app(found[0])
    lines = stream_log(args.mod, args.seconds, print)
    print(
        f"==> {lines} lines"
        + (
            ""
            if lines
            else " (no line from the mod: is the game open? the hook report repeats 20 s after launch)"
        )
    )
    return 0


def stream_log(mod, seconds, on_line, quiet=False, on_ready=None):
    """The mod's device-log lines for `seconds`, each to on_line(text); returns how many. on_ready() runs once the
    log is connected (am test turns switches on only then: a status line logged before that is lost)."""
    folder = dev.mod_dir(mod)
    meta = dev.read_json(folder / "mod.json")
    tag = meta.get("display_name") or meta["name"]
    if (folder / "tweaks.json").is_file():
        tag = (
            mod_tweaks.load(folder).get("log_tag")
            or mod_tweaks.load(folder).get("title")
            or tag
        )
    pattern = f"\\[{re.escape(tag)}\\]|AccretionMods"
    cmd = [
        dev_signing.pymobiledevice3_cli(),
        "syslog",
        "live",
        "-e",
        pattern,
    ]  # case-sensitive: the bundle ID has "accretionmods" too
    if not quiet:
        print(f"==> {' '.join(cmd)}  (for {seconds} s; Ctrl+C stops)")
    try:
        p = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        raise dev.DevError(
            "pymobiledevice3 not on PATH (uv tool install pymobiledevice3)"
        ) from None
    # a reader thread: readline() would block past the deadline while the phone is quiet
    import queue
    import threading

    q = queue.Queue()

    def reader():
        for line in p.stdout:
            q.put(line)
        q.put(None)

    threading.Thread(target=reader, daemon=True).start()
    if on_ready:
        time.sleep(3)  # pymobiledevice3 takes a moment to attach to the device log
        on_ready()
    end = time.time() + seconds
    lines = 0
    try:
        while time.time() < end:
            try:
                line = q.get(timeout=min(1.0, max(0.1, end - time.time())))
            except queue.Empty:
                continue
            if line is None:
                break
            on_line(line.rstrip())
            lines += 1
    except KeyboardInterrupt:
        pass
    finally:
        if p.poll() is None:
            p.kill()
    return lines


STATUS_LINE = re.compile(r"status (\S+) (ok|off|waiting: (.*))$")


def test_results(lines, keys):
    """{key: "reached" | "waiting: <why>" | "no report"} from the device-log lines of one am test round.
    A switch that reached the game once in the round counts as reached."""
    out = {k: "no report" for k in keys}
    for line in lines:
        m = STATUS_LINE.search(line)
        if not m or m.group(1) not in out:
            continue
        key, state = m.group(1), m.group(2)
        if state == "ok":
            out[key] = "reached"
        elif state != "off" and out[key] != "reached":
            out[key] = "waiting: " + m.group(3)
    return out


def test_rounds(tweaks, section=None, keys=None):
    """[(section title, [keys])]: one round per menu section, or the chosen ones."""
    pinned = [t for t in tweaks.get("tweaks", []) if t.get("pins")]
    if keys:
        wanted = [k.strip() for k in keys.split(",") if k.strip()]
        unknown = [k for k in wanted if k not in {t["key"] for t in pinned}]
        if unknown:
            raise dev.DevError(f"no pinned switch {', '.join(unknown)} in tweaks.json")
        return [("chosen", wanted)]
    rounds = {}
    for t in pinned:
        rounds.setdefault(t.get("section") or "Other", []).append(t["key"])
    if section:
        match = [r for r in rounds if r.lower() == section.lower()]
        if not match:
            raise dev.DevError(f"no section {section!r}: {', '.join(rounds)}")
        return [(match[0], rounds[match[0]])]
    return list(rounds.items())


def cmd_test(args):
    """Turns switches on from the PC (live_tweaks.json "test" block) one menu section at a time, reads their status
    lines from the device log, turns them off again, and records which reached the game. Reached means the pin
    found its object and wrote it; whether the cheat does what its title says is still for play (am mark)."""
    import uuid

    folder = dev.mod_dir(args.mod)
    meta = dev.read_json(folder / "mod.json")
    tweaks = dev.load_tweaks(folder, meta)
    found = [b for b in [dev_signing.installed_id(args.mod, meta)] if b]  # the verified installed ID (install mode aware)
    if not found:
        raise dev.DevError(f"{args.mod} is not on the phone (dev.py run {args.mod})")
    rounds = test_rounds(tweaks, args.section, args.keys)
    titles = {t["key"]: t["title"] for t in tweaks["tweaks"]}
    results = {}
    # switches the owner pushed before the test (am push) go back afterwards; none: the file is removed
    prior = dev.am_root() / "work" / f"live_tweaks-{args.mod}.before-test.json"
    prior.parent.mkdir(parents=True, exist_ok=True)
    prior.unlink(missing_ok=True)
    pulled = subprocess.run([dev_signing.pymobiledevice3_cli(), "apps", "pull", found[0], "Documents/live_tweaks.json", str(prior)],
                            capture_output=True, text=True, check=False)
    prior = prior if pulled.returncode == 0 and prior.is_file() else None
    print(f"==> {sum(len(k) for _, k in rounds)} switches in {len(rounds)} round(s) of {args.seconds} s. Keep the game "
          "open in a save (or in a fight, for combat switches); every switch goes off again after its round.")
    try:
        for title, keys in rounds:
            test_id = uuid.uuid4().hex[:8]
            seen = []
            stream_log(args.mod, args.seconds, seen.append, quiet=True,
                       on_ready=lambda: push_live(args.mod, found[0], {"id": test_id, "on": keys}))
            got = test_results(seen, keys)
            results.update(got)
            ok = sum(1 for v in got.values() if v == "reached")
            print(f"  {title}: {ok} of {len(keys)} reached the game")
            for k in keys:
                if got[k] != "reached":
                    print(f"      {k} ({titles.get(k, k)}): {got[k]}")
    finally:
        push_live(args.mod, found[0])  # no test block: the app turns the test switches off and restores values
        # then the phone goes back to how the test found it: the owner's own pushed switches, or the build's own
        # (a left-behind file keeps running every pinned switch live, removed ones included)
        time.sleep(4)  # the app reads the file within 2 s
        cli = dev_signing.pymobiledevice3_cli()
        if prior is not None:
            subprocess.run([cli, "apps", "push", found[0], str(prior), "Documents/live_tweaks.json"],
                           capture_output=True, text=True, check=False)
        else:
            subprocess.run([cli, "apps", "rm", found[0], "Documents/live_tweaks.json"],
                           capture_output=True, text=True, check=False)
    ok = sum(1 for v in results.values() if v == "reached")
    print(f"==> {ok} of {len(results)} reached the game; all test switches are off again")
    store = dev_learn.load(dev.patterns_path())
    for t in tweaks["tweaks"]:
        if t["key"] in results and results[t["key"]] != "no report":
            dev_learn.record_tweak_pins(store, t, meta["target_bundle_id"],
                                        "reached" if results[t["key"]] == "reached" else "unreached")
    dev_learn.save(dev.patterns_path(), store)
    record = dev.am_root() / "dev" / f"{args.mod}-tests.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    past = json.loads(record.read_text(encoding="utf-8")) if record.is_file() else {}
    stamp = time.strftime("%Y-%m-%d %H:%M", time.gmtime())
    for k, v in results.items():
        past[k] = {"result": v, "app_version": meta.get("tested_app_version"), "date": stamp}
    record.write_text(json.dumps(past, indent=2), encoding="utf-8")
    print(f"==> {record}. Reached is not the same as working in play: try them, then am mark <key> --works")
    return 0


# Imported last: dev.py imports this module while it loads, so the names above must exist by then; this
# module only uses dev's names when a function runs.
import dev  # noqa: E402
