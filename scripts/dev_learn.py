"""What scripts/dev.py learns from the hooks you add: a pattern store that ranks a new game's methods.

Store (C:\\AccretionMods\\dev\\patterns.json): one pattern per method name and result kind (void, bool, int,
float), with the action and value that was used, the menu title, and every use: the game, the class, the date
and the phone result ("works" or "fails", set by dev.py mark; unknown until then). A pattern's score is
2 per use, +3 per "works", -4 per "fails"; one that failed more than it worked is not suggested.

  record(store, method, action, value, tweak, game)   dev.py add: remember what was hooked and how
  mark(store, tweak, hooks, game, works)             dev.py mark: the phone said works / fails
  learn(store, hooks, game)                          dev.py learn: import an existing hooks.json
  suggest(store, methods, used)                      dev.py suggest: rank a dump's methods against the store

Nothing here reads the game or the phone; it only keeps what the owner already did, so the next game starts
from the methods that worked in the last ones. The README rules still apply: methods named for purchases or
online play are never suggested (find_cheats.LEFT_OUT).
"""

import json
import os
import re
import sys
from datetime import date

from find_cheats import left_out
from il2cpp_dump import UNITY_MESSAGES

SCORE_USE, SCORE_WORKS, SCORE_FAILS = 2, 3, -4
UNITY_MESSAGES_HINT = (
    "Update",
    "Awake",
    "Start",
    "LateUpdate",
    "FixedUpdate",
    "OnEnable",
)


def load(path):
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except ValueError:
            bad = str(path) + ".bad"
            os.replace(path, bad)
            print(f"patterns file was not valid JSON; moved to {bad} and starting empty", file=sys.stderr)
            data = None
        if isinstance(data, dict) and isinstance(data.get("patterns"), dict):
            return data
    return {"version": 1, "patterns": {}}


def save(path, store):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    path = str(path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(store, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def kind_of(return_type):
    if return_type == "void":
        return "void"
    if return_type == "bool":
        return "bool"
    if return_type in (
        "int",
        "long",
        "short",
        "byte",
        "sbyte",
        "uint",
        "ulong",
        "ushort",
    ):
        return "int"
    if return_type in ("float", "double"):
        return "float"
    return "other"


def pattern_key(method, return_type):
    return f"{method.lower()}|{kind_of(return_type)}"


def score(pattern):
    total = 0
    for use in pattern.get("uses", []):
        total += SCORE_USE
        if use.get("result") == "works":
            total += SCORE_WORKS
        elif use.get("result") == "fails":
            total += SCORE_FAILS
    return total


def outcomes(pattern):
    works = sum(1 for u in pattern.get("uses", []) if u.get("result") == "works")
    fails = sum(1 for u in pattern.get("uses", []) if u.get("result") == "fails")
    return works, fails


def record(store, entry, action, value, tweak, game, result=None):
    """Remember one hook: entry is the hooks.json entry, tweak the tweaks.json item it belongs to."""
    key = pattern_key(entry["method"], entry.get("return_type", ""))
    p = store["patterns"].setdefault(
        key,
        {
            "method": entry["method"],
            "kind": kind_of(entry.get("return_type", "")),
            "return_type": entry.get("return_type", ""),
            "args": entry.get("args"),
            "action": action,
            "value": value,
            "title": tweak.get("title", ""),
            "slider": [tweak["min"], tweak["max"], tweak.get("default", tweak["min"])]
            if tweak.get("kind") == "slider"
            else None,
            "uses": [],
        },
    )
    # the latest choice wins for how to hook it; uses accumulate
    p.update(action=action, value=value, args=entry.get("args"))
    if tweak.get("title"):
        p["title"] = tweak["title"]
    if tweak.get("kind") == "slider":
        p["slider"] = [tweak["min"], tweak["max"], tweak.get("default", tweak["min"])]
    use = {
        "game": game,
        "class": entry.get("class", ""),
        "namespace": entry.get("namespace", ""),
        "date": date.today().isoformat(),
        "result": result,
    }
    same = [u for u in p["uses"] if u["game"] == game and u["class"] == use["class"]]
    if same:
        same[-1].update(date=use["date"], **({"result": result} if result else {}))
    else:
        p["uses"].append(use)
    return p


def mark(store, tweak, hooks, game, works):
    """Set the phone result on every pattern a tweak's hooks belong to; returns the pattern methods touched."""
    by_name = {}
    for h in hooks:
        full = ".".join(
            x for x in (h.get("namespace", ""), h["class"], h["method"]) if x
        )
        by_name[full] = h
        by_name.setdefault(f"{h['class']}.{h['method']}", h)
    touched = []
    for hk in tweak.get("hooks", []):
        entry = by_name.get(hk["method"])
        if not entry:
            continue
        p = record(
            store,
            entry,
            hk.get("action", "custom"),
            hk.get("value"),
            tweak,
            game,
            "works" if works else "fails",
        )
        touched.append(p["method"])
    return touched


def learn(store, hooks, game, tweaks=None):
    """Import a hooks.json (and the tweaks.json that uses it, when there is one) into the store."""
    actions = {}
    for t in tweaks.get("tweaks", []) if tweaks else []:
        for hk in t.get("hooks", []):
            actions[hk["method"]] = (hk.get("action", "custom"), hk.get("value"), t)
    count = 0
    for h in hooks:
        full = ".".join(
            x for x in (h.get("namespace", ""), h["class"], h["method"]) if x
        )
        short = f"{h['class']}.{h['method']}"
        action, value, tweak = (
            actions.get(full)
            or actions.get(short)
            or (
                h.get("action", "custom"),
                h.get("value"),
                {"title": h.get("category", "")},
            )
        )
        record(store, h, action, value, tweak, game)
        count += 1
    return count


def suggest(store, methods, used=(), limit=30):
    """Methods of a dump that match a learned pattern, best first: (score, method, pattern, exact?)."""
    patterns = {k: p for k, p in store["patterns"].items() if score(p) > 0}
    by_name = {}
    for p in patterns.values():
        by_name.setdefault(p["method"].lower(), []).append(p)
    used = set(used)
    rows = []
    for m in methods:
        if (
            m.full_name in used
            or f"{m.class_name}.{m.name}" in used
            or "<" in m.class_name
        ):
            continue
        if left_out(m) or m.name in UNITY_MESSAGES or m.name.startswith("."):
            continue  # Unity callbacks and constructors are hooked for a game's own reasons, not as a pattern
        name = m.name.lower()
        kind = kind_of(m.return_type)
        # game code first: a plugin's method of the same name is rarely the one that matters
        assembly_bonus = 0 if m.assembly == "Assembly-CSharp" else -3
        exact = [p for p in by_name.get(name, []) if p["kind"] == kind]
        if exact:
            p = max(exact, key=score)
            sc = score(p) * 2 + (2 if p.get("args") == m.args else 0) + assembly_bonus
            if p.get("action") == "custom":
                sc //= 2  # a custom hook says the method mattered, not how to use it
            rows.append((sc, m, p, True))
            continue
        # a learned name inside a longer one (TakeDamage in TakeDamageFromFire), same result kind; Unity
        # callbacks (Update, Awake) and other short words would match half the game, so they are left out
        related = [
            p
            for n, ps in by_name.items()
            if len(n) >= 6 and n in name and ps[0]["method"] not in UNITY_MESSAGES
            for p in ps
            if p["kind"] == kind and p.get("action") != "custom"
        ]
        if related:
            p = max(related, key=score)
            sc = score(p) + assembly_bonus
            if sc > 0:
                rows.append((sc, m, p, False))
    rows.sort(key=lambda r: (-r[0], r[1].full_name))
    return rows[:limit]


def describe_pattern(p):
    works, fails = outcomes(p)
    games = sorted({u["game"] for u in p.get("uses", [])})
    how = p["action"] + (f" {p['value']}" if p.get("value") is not None else "")
    if p.get("slider"):
        how += " slider " + " ".join(str(x) for x in p["slider"])
    result = f", works {works}" if works else ""
    result += f", fails {fails}" if fails else ""
    return f"{how}  ({len(p.get('uses', []))} use(s) in {len(games)} game(s){result})"


def title_for(m, p):
    """A menu title for a suggested method: the learned one, or the method name spelled out."""
    if p.get("title") and not re.fullmatch(r"\w+\.\w+", p["title"]):
        return p["title"]
    words = re.sub(r"^(get_|set_)", "", m.name)
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", words)
