"""What scripts/dev.py learns from the hooks you add: a pattern store that ranks a new game's methods.

Store (C:\\AccretionMods\\dev\\patterns.json): one pattern per method name and result kind (void, bool, int,
float), with the action and value that was used, the menu title, and every use: the game, the class, the date
and the phone result ("works" or "fails", set by dev.py mark; unknown until then). A pattern's score is
1 per use, +4 per "works", -5 per "fails"; one that failed more than it worked is not suggested.

  record(store, method, action, value, tweak, game)   dev.py add: remember what was hooked and how
  mark(store, tweak, hooks, game, works)             dev.py mark: the phone said works / fails
  learn(store, hooks, game)                          dev.py learn: import an existing hooks.json
  suggest(store, methods, used)                      dev.py suggest: rank a dump's methods against the store
  record_pin / record_tweak_pins                     dev.py pin, mark, learn, test: what a no-JIT switch wrote
  member_boost(store, name, ctype)                   the scan: how much a candidate field looks like one that worked

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

SCORE_USE, SCORE_WORKS, SCORE_FAILS = 1, 4, -5  # a result counts more than being used
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
            migrate(data)
            return data
    return {"version": 2, "patterns": {}, "members": {}}


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
        # an "arg" hook replaces argument N: only a method with the same argument count can take it
        exact = [p for p in by_name.get(name, []) if p["kind"] == kind and (p.get("action") != "arg" or p.get("args") == m.args)]
        jit_only = -3 if getattr(m, "needs_jit", False) else 0  # most phones have no JIT: pins and vtable hooks first
        if exact:
            p = max(exact, key=score)
            sc = score(p) * 2 + (2 if p.get("args") == m.args else 0) + assembly_bonus + jit_only
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
            sc = score(p) + assembly_bonus + jit_only
            if sc > 0:
                rows.append((sc, m, p, False))
    rows.sort(key=lambda r: (-r[0], r[1].full_name))
    # one row per method signature: 40 unit classes that each override Attack(double, bool) are one choice
    # (hooking the base or any one of them is the decision); the row says how many share it
    seen, out = {}, []
    for sc, m, p, exact in rows:
        key = (m.name.lower(), kind_of(m.return_type), m.args, m.slot)
        if key in seen:
            seen[key]["others"] = seen[key].get("others", 0) + 1
            continue
        p = dict(p)
        seen[key] = p
        out.append((sc, m, p, exact))
    return out[:limit]


def describe_pattern(p):
    works, fails = outcomes(p)
    games = sorted({u["game"] for u in p.get("uses", [])})
    how = p["action"] + (f" {p['value']}" if p.get("value") is not None else "")
    if p.get("slider"):
        how += " slider " + " ".join(str(x) for x in p["slider"])
    result = f", works {works}" if works else ""
    result += f", fails {fails}" if fails else ""
    result += f"; {p['others']} other classes have the same method" if p.get("others") else ""
    return f"{how}  ({len(p.get('uses', []))} use(s) in {len(games)} game(s){result})"


def title_for(m, p):
    """A menu title for a suggested method: the learned one, or the method name spelled out."""
    if p.get("title") and not re.fullmatch(r"\w+\.\w+", p["title"]):
        return p["title"]
    words = re.sub(r"^(get_|set_)", "", m.name)
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", words)


# ---------------------------------------------------------------- pins: what no-JIT switches wrote
#
# A pin writes a field or calls a setter, so it is learned by what the member is called, not by a method
# signature: its name split into words ("_skillPoints", "SkillPoints" and "set_SkillPoints" are all
# "point skill"), and whether it holds a number or a true/false. store["members"][key] keeps the value, the
# "when", the title and section used, and every use with its result: works / fails (dev.py mark, the owner's
# play test) and reached / unreached (dev.py test: the pin found its object, which says the target exists, not
# that the cheat does what its title says). The scan (dump_scan) asks member_boost() for each candidate row.

MEMBER_SCORE = {"works": 5, "fails": -6, "reached": 1, "unreached": -1, None: 0.5}
STOPWORDS = {"m", "get", "set", "is", "has", "the", "k", "backing", "field", "value", "current", "cur", "base"}


def tokens(name):
    """Lowercase words of a member name, plurals folded, in a fixed order: "_skillPoints" -> ("point", "skill")."""
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|[_\W]+", " ", name).lower().split()
    out = set()
    for w in words:
        if w in STOPWORDS or w.isdigit():
            continue
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w)
    return tuple(sorted(out))


def value_kind(ctype):
    return "bool" if ctype == "bool" else "number"


# whose value it is: an enemy's damage and the player's damage are opposite switches with the same field name
ROLE = re.compile(r"enem|boss|bandit|monster|zombie|player|hero|pet|companion|ally|allies|troop|unit|king|castle|"
                  r"building|tower")


def role_of(cls):
    m = ROLE.search(" ".join(tokens(cls.rsplit(".", 1)[-1])))
    if not m:
        return ""
    word = m.group(0)
    return {"enem": "enemy", "allies": "ally", "zombie": "enemy", "monster": "enemy", "bandit": "enemy",
            "boss": "enemy", "hero": "player"}.get(word, word)


def member_key(name, ctype, cls=""):
    words = set(tokens(re.sub(r"^set_|^get_", "", name)))
    role = role_of(cls) if cls else ""
    if not words:
        return None
    if role and not any(ROLE.match(w) for w in words):  # the member names its owner already: that wins
        words.add(role)  # Enemy.BaseDamage and Player.EnemyDamage: "damage enemy" both ways
    return " ".join(sorted(words)) + "|" + value_kind(ctype)


def members(store):
    return store.setdefault("members", {})


def record_pin(store, cls, member, ctype, pin, tweak, game, result=None):
    """Remember one pin of a tweak (dev.py pin, learn, mark, test). Returns the member pattern, or None."""
    key = member_key(member, ctype, cls)
    if not key:
        return None
    p = members(store).setdefault(key, {"member": member, "ctype": ctype, "uses": []})
    use = {"game": game, "class": cls, "member": member, "date": date.today().isoformat(), "result": result,
           # how it was used there: suggestions copy it from the best-proven use
           "title": tweak.get("title", ""), "value": pin.get("value"), "when": pin.get("when", "always"),
           "op": pin.get("op", "set"), "section": tweak.get("section", "")}
    if tweak.get("kind") == "slider":
        use["slider"] = [tweak["min"], tweak["max"], tweak.get("default", tweak["min"])]
    same = [u for u in p["uses"] if u["game"] == game and u["class"] == cls and u.get("member") == member]
    if same:
        old = same[-1].get("result")
        # a play result (works / fails) is never replaced by a test result (reached / unreached)
        keep = old in ("works", "fails") and result in ("reached", "unreached")
        same[-1].update({k: v for k, v in use.items() if k != "result"})
        if result and not keep:
            same[-1]["result"] = result
    else:
        p["uses"].append(use)
    return p


def pin_target(pin):
    """(class, member) a pin acts on, as the scan names them."""
    obj = pin["object"]
    cls = pin.get("class") or (obj[:-2] if obj.endswith("[]") else obj.rpartition(".")[0] or obj)
    return cls, pin.get("field") or pin.get("setter")


def record_tweak_pins(store, tweak, game, result=None):
    """Every field / setter pin of a tweak; returns the member names recorded."""
    done = []
    for pin in tweak.get("pins", []):
        if pin.get("call") or not pin.get("ctype"):
            continue
        cls, member = pin_target(pin)
        if record_pin(store, cls, member, pin["ctype"], pin, tweak, game, result):
            done.append(member)
    return done


def member_score(p):
    return sum(MEMBER_SCORE.get(u.get("result"), 0) for u in p.get("uses", []))


def member_outcomes(p):
    count = lambda r: sum(1 for u in p.get("uses", []) if u.get("result") == r)
    return {"works": count("works"), "fails": count("fails"), "reached": count("reached")}


RESULT_RANK = {"works": 3, "reached": 2, None: 1, "unreached": 0, "fails": -1}


def best_use(uses):
    """The use to copy a title and value from: proven in play first, then reached, then the latest."""
    return max(uses, key=lambda u: (RESULT_RANK.get(u.get("result"), 0), u.get("date", ""))) if uses else None


def member_boost(store, name, ctype, game=None, cls=""):
    """(score bonus, pattern) for a scan row from what was learned, or (0, None). The same words count fully;
    sharing most words (two of three) counts half. A pattern that failed more than it worked gives nothing.
    Uses in the game being scanned are left out, so a game never confirms itself."""
    key = member_key(name, ctype, cls)
    if not key:
        return 0, None
    words, kind = key.rsplit("|", 1)
    words = set(words.split())
    best = (0, None)
    for k, p in members(store).items():
        pw, pk = k.rsplit("|", 1)
        if pk != kind:
            continue
        pw = set(pw.split())
        share = len(words & pw) / max(len(words | pw), 1)
        if share < 0.66:
            continue
        uses = [u for u in p.get("uses", []) if u.get("game") != game]
        sc = sum(MEMBER_SCORE.get(u.get("result"), 0) for u in uses)
        if sc <= 0:
            continue
        bonus = round(min(sc, 10) * (1 if share == 1 else 0.5))
        if bonus > best[0]:
            use = best_use(uses)
            works = sum(1 for u in uses if u.get("result") == "works")
            fails = sum(1 for u in uses if u.get("result") == "fails")
            reached = sum(1 for u in uses if u.get("result") == "reached")
            best = (bonus, dict(use, works=works, fails=fails, reached=reached))
    return best


def migrate(store):
    """Version 1 kept pins as fake methods ("accuracy|void", action "pin"): move them out of the method patterns.
    Their uses keep their results; the member and type come from the tweaks again on the next dev.py learn."""
    if store.get("version", 1) >= 2:
        return 0
    moved = 0
    for key, p in list(store["patterns"].items()):
        if p.get("action") == "pin":
            del store["patterns"][key]
            moved += 1
    store["version"] = 2
    members(store)
    return moved
