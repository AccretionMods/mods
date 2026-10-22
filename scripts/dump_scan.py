"""Candidates for no-JIT switches in a game's dump.cs: what Mod Studio's Scan tab and `dev.py scan` list.

A switch works without JIT when it is data: a pin writes a field or calls a property setter on a live object
every half second. So the scan looks, in the game's own assembly (Assembly-CSharp), for
- objects a pin can reach: a class with a static field of its own type (StatHandler.instance, Pet.p), or
  every live object of a MonoBehaviour class (Enemy[]);
- on them, number and true/false fields (int, float, long, bool) and property setters with a getter;
then sorts each into an area by its names, scores it, and flags what must not become a switch (what the game
sells, ads, accounts, online play: the repo's mod rules). Rows are suggestions: the scan reads names and
types, never what the code does, so a phone test (and the menu's status dot) decides.

scan(dump) -> {"rows": [...], "flagged": [...], "classes": N}; cached as scan.json next to dump.cs.
"""

import json
import os
import re
from pathlib import Path

VERSION = 5  # bump when the row format or the scoring changes (the cache is rebuilt)

IMAGE = re.compile(r"^// Image \d+: (.+?)(?:\.dll)? - (\d+)\s*$")
NAMESPACE = re.compile(r"^\s*// Namespace: ?(\S*)")
CLASS = re.compile(
    r"^(?:(?:public|private|protected|internal|static|sealed|abstract)\s+)*class\s+([A-Za-z_]\w*)(?=[\s:])"  # whole names: not "Enemy" from "Enemy.<>c"
    r"(?:\s*:\s*([\w.]+))?[^/]*// TypeDefIndex: (\d+)"
)
FIELD = re.compile(r"^\s+((?:(?:public|private|protected|internal|static|readonly)\s+)*)([\w.<>\[\], ]+?)\s+(\w+); // (0x[0-9A-Fa-f]+)")
METHOD = re.compile(r"^\s+(?:(?:public|private|protected|internal|virtual|override|static)\s+)*(\w+)\s+(get|set)_(\w+)\((\w*)[^)]*\)")

NUMERIC = {"int": "int32_t", "float": "float", "long": "int64_t", "bool": "bool", "double": "double"}

AREAS = [  # first match wins; member names count more than class names
    ("Pet & companion", r"pet|companion|dog|animal"),
    ("Survival", r"health|hunger|thirst|morale|\brads?\b|radiation|sleep|stamina|energy|sick|\bill\b|illness|"
                 r"disease|poison|bleed|infect|temperature|\bcold\b|\bheat\b|food|water|drink|\beat|vitality|resil"),
    ("Combat", r"damage|crit|accura|armou?r|attack|enemy|enemies|stun|disarm|dodge|\bhit|ammo|weapon|\bkill|"
               r"fight|combat|defen|bandit|flee|aggro|strength"),
    ("Progress", r"\bexp\b|experience|\bxp\b|level|skill|perk|point|intellig|agility|luck|perception|charisma|"
                 r"attribute|talent|mutation|unlock"),
    ("Items & crafting", r"craft|durab|item|inventory|repair|recipe|loot|scav|lockpick|\btool|material|stack|"
                         r"carry|weight|break|\bwear"),
    ("Exploration", r"\bdays?\b|hour|night|encounter|travel|location|\bmap\b|speed|explor|distance|journey|"
                    r"weather|\btime\b"),
]  # matched against words: "SkillPoints" -> "skill points", so "ill" no longer hits "skill"
# never switches: sold in the game's shop, ads, accounts, online, telemetry (README rules)
FLAGS = [
    ("sold in the shop", r"caps|token|gem|coin|currency|purchas|iap|shop|price|cost|premium|vip|subscri|"
                         r"max_?bag|bag_?(size|capacity)|fuel|petrol|van(crate|warranty)|fast_?travel|revive|"
                         r"supply_?(crate|drop)|boost|daily_?reward|redeem|special_?offer|starter_?pack|"
                         r"(battle|season|survivor|premium)_?pass"),
    ("ads", r"advert|\bads?\b|adinmo|yodo|banner|rewarded|interstitial"),
    ("accounts and online", r"cloud_?sav|ig_cloud|icloud|playfab|account|login|friend|leaderboard|online|multiplayer|pvp|\bserver\b|"
                            r"social|google_?play|game_?center|remote_?config|analytic|telemetry"),
]
NOISE = re.compile(r"cache|temp|test|debug|anim|fade|lerp|ui\b|^ui|sound|volume|music|scroll|alpha|scale|"
                   r"offset|index|count$|timer|delay|duration|color|colour|sprite|font|pos|width|height|"
                   r"tutorial|hint|language|notch|camera|frame|button|panel|popup", re.IGNORECASE)
PERCENT = re.compile(r"chance|accura|crit|percent|resist|rate|bleed|ammo|anything", re.IGNORECASE)


def _split_words(name):
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()


def _words(name):
    return _split_words(name).replace("_", " ")


def area_of(cls, member):
    words = _words(member)
    for area, rx in AREAS:
        if re.search(rx, words):
            return area, True
    cwords = _words(cls)
    for area, rx in AREAS:
        if re.search(rx, cwords):
            return area, False
    return "Other", False


def flag_of(cls, member):
    text = f"{_split_words(cls)} {_split_words(member)}"
    for reason, rx in FLAGS:
        if re.search(rx, text):
            return reason
    return None


def parse(path):
    """Classes of the game's own assembly: {name: {"base", "statics": [field], "fields": {...}, "props": {...}}}."""
    images, classes = [], {}
    current = None
    namespace = ""
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("// Image "):
                m = IMAGE.match(line)
                if m:
                    images.append((int(m.group(2)), m.group(1)))
                continue
            m = NAMESPACE.match(line)
            if m:
                namespace = m.group(1)
                continue
            if "// TypeDefIndex:" in line:
                m = CLASS.match(line)
                current = None
                if m:  # namespaced game classes too (9 Kings keeps its code in Core, Spells, Policies...)
                    index = int(m.group(3))
                    image = ""
                    for start, name in images:
                        if start <= index:
                            image = name
                    full = f"{namespace}.{m.group(1)}" if namespace else m.group(1)
                    if image.startswith("Assembly-CSharp") and full not in classes:
                        current = classes[full] = {
                            "base": m.group(2) or "", "statics": [], "fields": {}, "props": {}, "getters": set()
                        }
                        current_name = m.group(1)
                continue
            if current is None:
                continue
            if line.startswith("}"):
                current = None
                continue
            m = FIELD.match(line)
            if m:
                mods, ftype, fname, offset = m.group(1), m.group(2).strip(), m.group(3), m.group(4)
                if "static" in mods:
                    if ftype == current_name:
                        current["statics"].append(fname)
                elif ftype in NUMERIC:
                    current["fields"][fname] = {"ctype": NUMERIC[ftype], "offset": offset,
                                                "private": "public" not in mods}
                continue
            m = METHOD.match(line)
            if m:
                rtype, kind, prop, arg = m.groups()
                if kind == "get" and rtype in NUMERIC:
                    current["getters"].add(prop)
                elif kind == "set" and arg in NUMERIC:
                    current["props"][prop] = NUMERIC[arg]
    return classes


def _suggest(member, ctype, has_getter):
    if ctype == "bool":
        return {"value": "true", "when": "always"}
    value = "100" if PERCENT.search(member) else "99"
    return {"value": value, "when": "lower" if has_getter else "always"}


def build(classes, existing=frozenset()):
    rows, flagged = [], []
    for cls, info in classes.items():
        if info["statics"]:
            obj, reach = f"{cls}.{info['statics'][0]}", 3
        elif info["base"] == "MonoBehaviour":
            obj, reach = f"{cls}[]", 1
        else:
            continue
        members = []
        for prop, ctype in info["props"].items():
            members.append((f"set_{prop}", prop, ctype, "setter", None, prop in info["getters"], False))
        for fname, f in info["fields"].items():
            if fname.startswith("<"):
                continue  # compiler backing fields: the setter covers them
            members.append((fname, fname, f["ctype"], "field", f["offset"], False, f["private"]))
        for target, name, ctype, kind, offset, getter, private in members:
            area, by_member = area_of(cls, name)
            row = {
                "class": cls, "object": obj, "member": target, "name": name, "kind": kind, "ctype": ctype,
                "offset": offset, "area": area, "has": f"{cls}.{target}" in existing,
            }
            reason = flag_of(cls, name)
            if reason:
                row["flag"] = reason
                flagged.append(row)
                continue
            score = reach + (2 if kind == "setter" and getter else 1)
            score += 2 if by_member else (1 if area != "Other" else 0)
            if kind == "field" and (name + "Default") in info["fields"]:
                score += 2  # "Accuracy" next to "AccuracyDefault": a skill value the game reads
            if NOISE.search(name):
                score -= 4
            if private:
                score -= 1
            if obj.endswith("[]") and area == "Other":
                score -= 2
            row["score"] = score
            row.update(_suggest(name, ctype, getter))
            rows.append(row)
    rows.sort(key=lambda r: (-r["score"], r["area"], r["class"], r["member"]))
    flagged.sort(key=lambda r: (r["flag"], r["class"], r["member"]))
    return rows, flagged


def existing_targets(tweaks):
    """Class.member pairs a mod's tweaks.json already pins or hooks."""
    out = set()
    for t in (tweaks or {}).get("tweaks", []):
        for p in t.get("pins", []):
            obj = p["object"]
            cls = p.get("class") or (obj[:-2] if obj.endswith("[]") else obj.rpartition(".")[0] or obj)
            for k in ("field", "setter", "call"):
                if p.get(k):
                    out.add(f"{cls}.{p[k]}")
                    if k == "setter" and p[k].startswith("set_"):
                        out.add(f"{cls}.{p[k][4:]}")  # the field behind it counts as done too
        for h in t.get("hooks", []):
            out.add(h["method"].rsplit(".", 2)[-2] + "." + h["method"].rsplit(".", 1)[-1])
    return out


def scan(dump, tweaks=None):
    """Rows for a dump.cs, cached in scan.json beside it (rebuilt when the dump or VERSION changes)."""
    dump = Path(dump)
    cache = dump.with_name("scan.json")
    st = dump.stat()
    stamp = {"version": VERSION, "size": st.st_size, "mtime": int(st.st_mtime)}
    data = None
    if cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
        except ValueError:
            data = None
    if not data or data.get("stamp") != stamp:
        classes = parse(dump)
        data = {"stamp": stamp, "classes": len(classes),
                "data": {k: dict(v, getters=sorted(v["getters"])) for k, v in classes.items()}}
        tmp = cache.with_name(cache.name + ".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, cache)
    classes = {k: dict(v, getters=set(v["getters"])) for k, v in data["data"].items()}
    rows, flagged = build(classes, existing_targets(tweaks))
    return {"rows": rows, "flagged": flagged, "classes": data["classes"]}


def as_text(rows, limit=60):
    """A compact list for a prompt: one candidate per line."""
    lines = ["area | object | member | type | kind | suggested value | already in the mod"]
    for r in rows[:limit]:
        lines.append(f"{r['area']} | {r['object']} | {r['member']} | {r['ctype']} | {r['kind']} | "
                     f"{r.get('value', '')} {r.get('when', '')} | {'yes' if r['has'] else 'no'}")
    return "\n".join(lines)
