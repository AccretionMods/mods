"""Candidates for no-JIT switches in a game's dump.cs: what Mod Studio's Scan tab and `dev.py scan` list.

A switch works without JIT when it is data: a pin writes a field or calls a property setter on a live object
every half second. So the scan looks, in the game's own assembly (Assembly-CSharp), for
- objects a pin can reach: a class with a static field of its own type (StatHandler.instance, Pet.p), or
  every live object of a MonoBehaviour class (Enemy[]);
- on them, number and true/false fields (int, float, long, bool) and property setters with a getter;
then sorts each into an area by its names, scores it, and adds informational topic labels (shop, ads,
accounts and online play). Every topic remains a candidate. Rows are suggestions: the scan reads names and
types, never what the code does, so a phone test (and the menu's status dot) decides.

scan(dump) -> {"rows": [...], "flagged": [...], "classes": N}; cached as scan-<choice>.json next to dump.cs.
"""

import bisect
import hashlib
import json
import os
import re
from pathlib import Path

from il2cpp_dump import ADDRESS, FLOATS, INTEGERS, SIGNATURE, TYPE, TYPE_INDEX, Method, split_params

VERSION = 16  # assemblies chosen by content too, bought packages ranked low; presentation, counter and bookkeeping rows ranked lower; the game's own assembly from its bundle ID

IMAGE = re.compile(r"^// Image \d+: (.+?)(?:\.dll)? - (\d+)\s*$")
NAMESPACE = re.compile(r"^\s*// Namespace: ?(\S*)")
CLASS = re.compile(
    r"^(?:(?:public|private|protected|internal|static|sealed|abstract)\s+)*class\s+([A-Za-z_]\w*)(?=[\s:])"  # whole names: not "Enemy" from "Enemy.<>c"
    r"(?:\s*:\s*([\w.]+(?:<[\w.]+>)?))?[^/]*// TypeDefIndex: (\d+)"
)
# a generic singleton base (Singleton<T>, MonoSingleton<T>): its static field of type T holds each subclass's instance
GENERIC = re.compile(r"^(?:(?:public|private|protected|internal|static|sealed|abstract)\s+)*class\s+([A-Za-z_]\w*)<(\w+)>")
# games built with HybridCLR/ILRuntime keep their code in hot-update assemblies, and Assembly-CSharp then holds
# mostly bought packages and their demos (Survivor: Ace: HotFix, HotFixBattle); mod.json "scan_assemblies" overrides
HOT_UPDATE = re.compile(r"^(hot_?fix|hot_?update)", re.IGNORECASE)
# store-bought packages ship demo and sample scripts: never game logic (a scroll list's demo item count)
DEMO = re.compile(r"demo|sample|example|tutorial|showcase|editor|\btest", re.IGNORECASE)
FIXED = {"FP": 65536}  # Photon Quantum's fixed point: a long holding value * 65536
FIELD = re.compile(r"^\s+((?:(?:public|private|protected|internal|static|readonly)\s+)*)([\w.<>\[\], ]+?)\s+([\w<>]+); // (0x[0-9A-Fa-f]+)")
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
# Informational labels only: no topic excludes a candidate.
FLAGS = [
    ("sold in the shop", r"caps|token|gem|coin|currency|purchas|iap|in_?app|full_?game|paywall|billing|receipt|entitlement|shop|storekit|storefront|payment|(?:^|[._ ])(?:store|buy)(?:$|[._ ])|price|cost|premium|vip|subscri|"
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
# values that help when they go down: waits, costs, what enemies do
LOWER = re.compile(r"delay|cool_?down|wait|reload|penalt|drain|decay|enem\w*_?(speed|damage|strength|hp|health|spawn)",
                   re.IGNORECASE)
MULTIPLIER = re.compile(r"multiplier|\bmult\b|factor|scale$", re.IGNORECASE)
PROBABILITY = re.compile(r"chance|probab|odds", re.IGNORECASE)
# classes the game writes to its save file: a field of theirs stays changed after the switch is off unless the
# switch restores it (gen_tweaks restorable pins do)
SAVED = re.compile(r"save|snapshot|serializ|persist|container|profile", re.IGNORECASE)
UI_CLASS = re.compile(r"(button|panel|popup|screen|menu|view|toggle|ui)$", re.IGNORECASE)  # "SaveLoadButton" is UI
# classes whose values are presentation, not play: player options (damage numbers on/off), UI widgets and visual
# effects. Their rows stay in the scan, ranked lower (Vampire Survivors: 160 option rows, 600 on UI objects)
PRESENTATION = re.compile(r"(?:options|settings|preferences|prefs|accessibility|localization)(?:data)?$|"
                          r"(?:button|panel|popup|screen|menu|view|toggle|widget|tooltip|hud|ui)$|^ui_?|"
                          r"reflection|shadow|shader|particle|renderer|vfx|tween|animator|camera|lighting|material",
                          re.IGNORECASE)
UI_NAMESPACE = re.compile(r"(?:^|\.)(?:UI|Ui|Views?|Menus?|Graphics|Rendering|Audio)(?:\.|$)")
# statistics the game counts (kills, totals, play time): changing them changes a number on a results screen
COUNTER = re.compile(r"killed|kills$|total|play_?time|direct_?hits|hits_?taken|times_?(?:played|died)|statistic",
                     re.IGNORECASE)
# the code's own bookkeeping: set-up done, something changed, shown or hidden
BOOKKEEPING = re.compile(r"init|complete|changed|dirty|loaded|ready|initiali[sz]ed|hide|show|visible|icon|collider|"
                         r"for_?online|synced?", re.IGNORECASE)


def _split_words(name):
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()


def _words(name):
    return _split_words(name).replace("_", " ")


def area_of(cls, member):
    if flag_of(cls, member) == "sold in the shop":
        return "In-app purchases", flag_of("", member) == "sold in the shop"
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


def _compact(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


PROFILE_VERSION = 1
# value fields whose names say gameplay: what the content score counts (not the scan's ranking)
GAMEPLAY_FIELD = re.compile(r"health|\bhp\b|damage|attack|armou?r|speed|gold|coin|gem|\bxp\b|\bexp\b|level|ammo|"
                            r"stamina|hunger|thirst|energy|inventory|craft|resource|wood|stone|food|water|weapon|"
                            r"skill|cooldown|reward|score|durab", re.IGNORECASE)
BEHAVIOUR_BASE = re.compile(r":\s*[\w.]*(?:MonoBehaviour|Behaviour|Controller|Manager|Singleton<)")
TYPE_NAME = re.compile(r"\b(?:class|struct|interface)\s+([A-Za-z_]\w*)")  # its own: CLASS groups differ by version


def assembly_profile(path):
    """Per assembly of a dump.cs, what makes it game code: {name: {types, behaviours, singletons, gameplay, score}}.
    One pass of substring tests (about a second for 100 MB), cached beside dump.cs as assemblies.json."""
    path = Path(path)
    cache = path.with_name("assemblies.json")
    st = path.stat()
    stamp = [PROFILE_VERSION, st.st_size, st.st_mtime_ns]
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("stamp") == stamp:
            return data["assemblies"]
    except (OSError, ValueError):
        pass
    images, starts, out = [], [], {}
    current = None  # (assembly, short class name)
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("// Image "):
                m = IMAGE.match(line)
                if m:
                    images.append((int(m.group(2)), m.group(1)))
                continue
            if "// TypeDefIndex:" in line:
                if not starts:
                    images.sort()
                    starts = [s for s, _ in images]
                m = TYPE_INDEX.search(line)
                i = bisect.bisect_right(starts, int(m.group(1))) - 1 if m else -1
                name = images[i][1] if i >= 0 else ""
                e = out.setdefault(name, {"types": 0, "behaviours": 0, "singletons": 0, "gameplay": 0})
                e["types"] += 1
                if BEHAVIOUR_BASE.search(line):
                    e["behaviours"] += 1
                c = TYPE_NAME.search(line)
                current = (name, c.group(1)) if c else None
                continue
            if current and "; // 0x" in line:
                e = out[current[0]]
                if " static " in line and f" {current[1]} " in line:
                    e["singletons"] += 1
                elif not line.lstrip().startswith(("public static", "private static")):
                    m = FIELD.match(line)
                    if m and scalar(m.group(2).strip()) and GAMEPLAY_FIELD.search(_split_words(m.group(3))):
                        e["gameplay"] += 1
    for e in out.values():
        e["score"] = e["behaviours"] * 2 + e["singletons"] * 5 + e["gameplay"]
    try:
        cache.write_text(json.dumps({"stamp": stamp, "assemblies": out}), encoding="utf-8")
    except OSError:
        pass
    return out


def auto_assemblies(profile, already=()):
    """The assemblies whose content scores as game code, beyond the ones chosen by name: every non-library one
    scoring at least an eighth of the best (and 20), six at most (a game's data models beside its client). Last Day on Earth's Client, beside the bought
    animation package in its Assembly-CSharp."""
    rows = {n: e for n, e in (profile or {}).items() if n and not LIBRARY.match(n) and not PACKAGE.match(n)}
    if not rows:
        return set()
    best = max(e["score"] for e in rows.values())
    picked = sorted((n for n, e in rows.items() if e["score"] >= max(20, best / 8) and n not in already),
                    key=lambda n: -rows[n]["score"])
    return set(picked[:6])


def game_assemblies(images, wanted=None, bundle_id=None, profile=None):
    """The assemblies holding the game's own code: mod.json "scan_assemblies", else the hot-update ones when the
    game has them, else Assembly-CSharp plus any assembly named after the game (Vampire Survivors keeps its code
    in VampireSurvivors.Runtime: com.poncle.VampireSurvivors -> "vampiresurvivors"), plus the ones whose content
    scores as game code (auto_assemblies; profile from assembly_profile)."""
    names = [name for _, name in images]
    if wanted:
        return {n.removesuffix(".dll") for n in wanted}
    hot = {n for n in names if HOT_UPDATE.match(n)}
    if hot:
        return hot
    chosen = {n for n in names if n.startswith("Assembly-CSharp")}
    game = _compact(bundle_id.rsplit(".", 1)[-1]) if bundle_id else ""
    if len(game) >= 4:
        chosen |= {n for n in names if _compact(n).startswith(game)}
    return chosen | auto_assemblies(profile, chosen)


# engine, platform and store-bought libraries: never where a game keeps its own code
LIBRARY = re.compile(r"^(Unity|UnityEngine|System|Mono|mscorlib|Microsoft|netstandard|Newtonsoft|DOTween|DG\.|"
                     r"Coherence|Photon|PlayFab|Firebase|Google|Facebook|AppsFlyer|Apple|Sirenix|TMPro|Rewired|Zenject|"
                     r"UniTask|UniRx|Cysharp|MessagePack|Spine|Cinemachine|Doozy|Sentry|Amazon|AWS|Lean|I2|l2local|"
                     r"ICSharpCode|Assembly-CSharp-firstpass|Mirror|FishNet|Fusion|Steamworks|Dreamteck|ProCamera|"
                     r"Il2Cpp|__Generated|BestHTTP|VoxelBusters|AdinmoPlugin|IngameDebugConsole|Gametoolkit|glTFast)", re.IGNORECASE)
# store-bought packages that games compile into their own assemblies (or ship as their own): animation, tweening,
# text, meshes, cameras, editors. Their classes rank low and Auto-build leaves them out with the presentation rows
PACKAGE = re.compile(r"^(?:RootMotion|DG\.Tweening|TMPro|DigitalOpus|MeshBaker|Sirenix|Spine|Cinemachine|"
                     r"UnityStandardAssets|Lean|Obi|Opsive|PathologicalGames|Febucci|I2|Rewired|Doozy|Dreamteck|"
                     r"ProCamera2D|Com\.LuisPedroFonseca|Kino|AmplifyShaderEditor|BeautifyEffect|EasyMobile|"
                     r"Pathfinding|Polyperfect|Invector|MoreMountains|Coffee\.UIExtensions|SA\.)", re.IGNORECASE)


def assembly_sizes(dump):
    """[(types, assembly)] largest first, from dump.cs's image table (its first lines), libraries left out."""
    starts = []
    with open(dump, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = IMAGE.match(line)
            if m:
                starts.append((int(m.group(2)), m.group(1)))
            elif starts:
                break
    starts.sort()
    sizes = [(b[0] - a[0], a[1]) for a, b in zip(starts, starts[1:])]
    return sorted(((n, name) for n, name in sizes if not LIBRARY.match(name)), reverse=True)


def scalar(ftype):
    """(C type, fixed-point scale or None) for a pin-able field or property type, or None."""
    if ftype in NUMERIC:
        return NUMERIC[ftype], None
    base = ftype.rsplit(".", 1)[-1]
    if base in FIXED:
        return "int64_t", FIXED[base]
    return None


def parse(path, assemblies=None, bundle_id=None):
    """Classes of the game's own assemblies: {name: {"base", "statics": [field], "fields": {...}, "props": {...}}}.
    A class outside Assembly-CSharp is named with its assembly (HotFix:GameManager), as the runtime finds it."""
    images, classes, generics = [], {}, {}
    current = generic = None
    namespace = ""
    chosen = None
    profile = None if assemblies else assembly_profile(path)  # which assemblies hold game code, by content
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("// Image "):
                m = IMAGE.match(line)
                if m:
                    images.append((int(m.group(2)), m.group(1)))
                continue
            m = NAMESPACE.match(line) if "// Namespace" in line else None  # substring first: ~2M lines
            if m:
                namespace = m.group(1)
                continue
            if "// TypeDefIndex:" in line:
                if chosen is None:
                    chosen = game_assemblies(images, assemblies, bundle_id, profile)
                current = generic = None
                g = GENERIC.match(line)
                if g:  # Singleton<T>: which of its static fields holds the T
                    generic = generics.setdefault(g.group(1), {"t": g.group(2), "statics": []})
                    continue
                m = CLASS.match(line)
                if m:  # namespaced game classes too (9 Kings keeps its code in Core, Spells, Policies...)
                    index = int(m.group(3))
                    image = ""
                    for start, name in images:
                        if start <= index:
                            image = name
                    full = f"{namespace}.{m.group(1)}" if namespace else m.group(1)
                    if not image.startswith("Assembly-CSharp"):
                        full = f"{image}:{full}"
                    if image in chosen and full not in classes:
                        current = classes[full] = {
                            "base": m.group(2) or "", "statics": [], "fields": {}, "props": {}, "getters": set(),
                            "holds": set(), "references": {}, "object_getters": {},
                        }
                        current_name = m.group(1)
                continue
            if generic is not None:
                if line.startswith("}"):
                    generic = None
                else:
                    m = FIELD.match(line)
                    if m and "static" in m.group(1) and m.group(2).strip() == generic["t"]:
                        generic["statics"].append(m.group(3))
                continue
            if current is None:
                continue
            if line.startswith("}"):
                current = None
                continue
            m = FIELD.match(line) if "; // 0x" in line else None
            if m:
                mods, ftype, fname, offset = m.group(1), m.group(2).strip(), m.group(3), m.group(4)
                kind = scalar(ftype)
                if "static" in mods:
                    if ftype == current_name:
                        current["statics"].append(fname)
                    if ftype not in NUMERIC and "<" not in ftype and "[" not in ftype:
                        current["references"][fname] = ftype
                elif kind:
                    current["fields"][fname] = {"ctype": kind[0], "offset": offset,
                                                "private": "public" not in mods, "readonly": "readonly" in mods}
                    if kind[1]:
                        current["fields"][fname]["scale"] = kind[1]
                else:
                    current["holds"].add(re.sub(r"[<\[].*", "", ftype).rsplit(".", 1)[-1])
                continue
            m = METHOD.match(line) if ("get_" in line or "set_" in line) else None
            if m:
                rtype, kind, prop, arg = m.groups()
                if kind == "get" and scalar(rtype):
                    current["getters"].add(prop)
                elif kind == "get" and not arg:
                    current["object_getters"]["get_" + prop] = rtype
                elif kind == "set" and scalar(arg) and not scalar(arg)[1]:  # an FP setter takes a struct: fields only
                    current["props"][prop] = NUMERIC[arg]
    for name, info in classes.items():  # GameManager : Singleton<GameManager>
        b = re.match(r"([\w.]+)<([\w.]+)>$", info["base"])
        short = name.rpartition(":")[2].rsplit(".", 1)[-1]
        if b and b.group(2).rsplit(".", 1)[-1] == short:
            statics = generics.get(b.group(1).rsplit(".", 1)[-1], {}).get("statics")
            if statics:
                info["singleton"] = statics[0]
    return classes


def _suggest(member, ctype, has_getter):
    """A first value for a pin, from the member's name and type (the learned value from other games wins)."""
    if ctype == "bool":
        return {"value": "true", "when": "always"}
    floating = ctype in ("float", "double")
    if LOWER.search(member):
        return {"value": "0.5" if MULTIPLIER.search(member) else "0", "when": "higher" if has_getter or not floating else "always",
                "direction": "lower"}
    if MULTIPLIER.search(member):
        return {"value": "2", "when": "always", "direction": "higher"}  # x2: 99 times anything overflows or breaks
    if PROBABILITY.search(member) and floating:
        return {"value": "1", "when": "lower", "direction": "higher"}  # a 0-1 chance: 1 is always
    value = "100" if PERCENT.search(member) else "99"
    return {"value": value, "when": "lower" if has_getter else "always", "direction": "higher"}


def saved_classes(classes):
    """Short names of the classes a save holds: named like a save, or held by one, two levels down."""
    short = {k.rsplit(".", 1)[-1]: v for k, v in classes.items()}
    saved = {n for n in short if SAVED.search(n) and not UI_CLASS.search(n)}
    for _ in range(2):
        saved |= {h for n in list(saved) for h in short.get(n, {}).get("holds", ()) if h in short}
    return saved


def build(classes, existing=frozenset(), learned=None):
    """learned(name, ctype, cls) -> (bonus, use or None): dev_learn.member_boost, what worked in other games."""
    rows, flagged = [], []
    saved = saved_classes(classes)
    for cls, info in classes.items():
        if DEMO.search(cls):
            continue  # a package's demo or sample script, not the game
        if info["statics"]:
            obj, reach = f"{cls}.{info['statics'][0]}", 3
        elif info.get("singleton"):
            obj, reach = f"{cls}.{info['singleton']}", 3
        elif info["base"] == "MonoBehaviour":
            obj, reach = f"{cls}[]", 1
        else:
            continue
        local = cls.rpartition(":")[2]
        presentation = bool(PRESENTATION.search(local.rsplit(".", 1)[-1]) or UI_NAMESPACE.search(local.rpartition(".")[0]))
        package = bool(PACKAGE.match(local.rpartition(".")[0] or local))
        members = []
        for prop, ctype in info["props"].items():
            members.append((f"set_{prop}", prop, ctype, "setter", None, prop in info["getters"], False))
        for fname, f in info["fields"].items():
            if fname.startswith("<") or f.get("readonly"):
                continue  # compiler backing fields: the setter covers them
            members.append((fname, fname, f["ctype"], "field", f["offset"], False, f["private"]))
        for target, name, ctype, kind, offset, getter, private in members:
            area, by_member = area_of(cls.rpartition(":")[2], name)
            row = {
                "class": cls, "object": obj, "member": target, "name": name, "kind": kind, "ctype": ctype,
                "offset": offset, "area": area, "has": f"{cls}.{target}" in existing,
            }
            scale = info["fields"].get(name, {}).get("scale") if kind == "field" else None
            if scale:
                row["scale"] = scale  # fixed point: the value is written as value * scale
            reason = flag_of(cls, name)
            if reason:
                row["flag"] = reason
                flagged.append(row)
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
            if presentation:
                score -= 3
                row["presentation"] = True
            elif package:
                score -= 3
                row["package"] = True  # a bought package (animation, tweening, text...): Auto-build leaves it out
            words = _split_words(name)
            if COUNTER.search(words):
                score -= 2
            if ctype == "bool" and BOOKKEEPING.search(words):
                score -= 2
            row.update(_suggest(name, "float" if scale else ctype, getter))
            if cls.rpartition(":")[2].rsplit(".", 1)[-1] in saved:
                row["saved"] = True  # changes stay in the save: keep restore on for its switch
            if learned:
                bonus, p = learned(name, ctype, cls)
                if p:  # the best-proven use elsewhere: its title and value, and how it went
                    score += bonus
                    row["learned"] = {k: p.get(k) for k in ("title", "member", "class", "game", "works", "fails", "reached")}
                    if isinstance(p.get("value"), (int, float)):  # bools too: "true" / "false"
                        row["value"] = str(p["value"]).lower()  # the value that was used there
                    if p.get("when") in ("always", "lower", "higher") and (kind == "field" or getter):
                        row["when"] = p["when"]
            row["score"] = score
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


def cache_file(dump, assemblies=None, bundle_id=None):
    """scan(dump)'s cache beside dump.cs: one file per assembly choice, since mods share a game build's dump
    (scripts/dump_cache.py) and may read different assemblies of it."""
    choice = json.dumps([sorted(assemblies or []), bundle_id or ""])
    return Path(dump).with_name(f"scan-{hashlib.sha1(choice.encode()).hexdigest()[:8]}.json")


def scan(dump, tweaks=None, learned=None, assemblies=None, bundle_id=None):
    """Rows for a dump.cs, cached beside it (cache_file; rebuilt when the dump, VERSION or the chosen assemblies
    change). assemblies: mod.json "scan_assemblies" (default: hot-update assemblies, else Assembly-CSharp)."""
    dump = Path(dump)
    cache = cache_file(dump, assemblies, bundle_id)
    st = dump.stat()
    stamp = {"version": VERSION, "size": st.st_size, "mtime": st.st_mtime_ns, "assemblies": sorted(assemblies or []),
             "bundle_id": bundle_id or ""}
    data = None
    if cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
        except ValueError:
            data = None
    if not data or data.get("stamp") != stamp:
        classes = parse(dump, assemblies, bundle_id)
        data = {"stamp": stamp, "classes": len(classes), "inventory": purchase_inventory(dump),
                "data": {k: dict(v, getters=sorted(v["getters"]), holds=sorted(v["holds"])) for k, v in classes.items()}}
        tmp = cache.with_name(cache.name + ".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, cache)
    classes = {k: dict(v, getters=set(v["getters"]), holds=set(v.get("holds", ()))) for k, v in data["data"].items()}
    existing = existing_targets(tweaks)
    rows, flagged = build(classes, existing, learned)
    pins = {(r["class"], r["member"]) for r in rows}
    inventory = [dict(r, has=f"{r['class']}.{r['member']}" in existing)
                 for r in data["inventory"] if (r["class"], r["member"]) not in pins]
    # where the rows come from, and the big assemblies left unread: so nothing large is skipped silently
    profile = assembly_profile(dump)
    read = {r["class"].split(":", 1)[0] if ":" in r["class"] else "Assembly-CSharp" for r in rows}
    unread = sorted(((e["score"], n) for n, e in profile.items()
                     if n and n not in read and not LIBRARY.match(n) and not PACKAGE.match(n) and e["score"] >= 20),
                    reverse=True)[:6]
    packaged = sum(1 for r in rows if r.get("package"))
    return {"rows": rows, "flagged": flagged, "inventory": inventory, "classes": data["classes"],
            "coverage": {"read": sorted(read), "unread": [{"assembly": n, "score": s} for s, n in unread],
                         "package_rows": packaged}}


def purchase_inventory(path):
    """Mutable scalar Store/IAP fields and concrete methods supported by the hook tool.

    These are inspection rows, not pin suggestions. Keep them separate so automatic pin generation
    and the learner never treat an arbitrary method or a string field as a writable numeric value.
    """
    field = re.compile(r"^\s*(?:(?:public|private|protected|internal|static|readonly|const)\s+)*"
                       r"([\w.<>\[\], ]+?)\s+([\w<>]+)(?:\s*=\s*[^;]+)?; // (0x[0-9A-Fa-f]+)")
    images, rows = [], []
    starts = None
    namespace = cls = assembly = ""
    code = None
    scalar = INTEGERS | FLOATS | {"bool"}
    action = re.compile(r"buy|purchas|unlock|own|grant|reward|spend|consume|refund|restore|"
                        r"premium|currency|coins?|gems?|subscri|entitle|payment|(?:^|_)ads?(?:_|$)|price|cost|"
                        r"^(get_|set_|is_|has_|can_|allow|enable|disable)", re.IGNORECASE)
    topics = re.compile(r"purchas|iap|in_?app|full_?game|paywall|billing|receipt|entitlement|shop|"
                        r"storekit|storefront|payment|premium|vip|subscri|token_?(balance|currency)|"
                        r"(?:^|[._ ])(?:store|buy|currency|coins?|gems?|diamonds?)(?:$|[._ ])")

    # the topic test runs once per class and once per member name: _split_words never joins across the dots, so
    # "assembly.class" and "member" tested apart match exactly what the joined name does (2.5x faster on 100 MB)
    class_hit, member_hits = False, {}

    def matches(member):
        if class_hit:
            return True
        hit = member_hits.get(member)
        if hit is None:
            hit = member_hits[member] = topics.search(_split_words(member)) is not None
        return hit



    def add(member, ctype, kind, signature, offset=None, method=None):
        row = {"class": cls, "object": cls, "member": member, "name": member,
                     "ctype": ctype, "kind": kind, "signature": signature.strip(),
                     "assembly": assembly, "offset": offset, "area": "In-app purchases",
                     "flag": "sold in the shop", "score": 0, "has": False, "inspect_only": True}
        if method:
            row["method"] = method
        rows.append(row)

    with open(path, encoding="utf-8", errors="replace") as source:
        for line in source:
            # cheap substring tests first: three regexes on each of ~2 million lines were most of this pass
            image = IMAGE.match(line) if line.startswith("// Image ") else None
            if image:
                images.append((int(image.group(2)), image.group(1)))
                starts = None
                continue
            ns = NAMESPACE.match(line) if "// Namespace" in line else None
            if ns:
                namespace, cls = ns.group(1), ""
                code = None
                continue
            index = TYPE_INDEX.search(line) if "// TypeDefIndex:" in line else None
            typ = TYPE.match(line) if index else None
            if typ and index:
                cls = f"{namespace}.{typ.group(1)}" if namespace else typ.group(1)
                if starts is None:  # the image table is in order: the last image starting at or before the index
                    starts = [s for s, _ in images] if all(a[0] <= b[0] for a, b in zip(images, images[1:])) else []
                at = int(index.group(1))
                if starts:
                    i = bisect.bisect_right(starts, at) - 1
                    assembly = images[i][1] if i >= 0 else ""
                else:
                    assembly = next((name for start, name in reversed(images) if start <= at), "")
                code = None
                if "<" in cls:  # closures, delegates, async/iterator state machines and unsupported generics
                    cls = ""
                class_hit = bool(cls) and topics.search(_split_words(f"{assembly}.{cls}")) is not None
                continue
            if not cls:
                continue
            if line.lstrip().startswith("// RVA:"):
                code = ADDRESS.match(line)  # -1 means abstract/no implementation; never reuse the previous RVA
                continue
            # only field lines ("...; // 0x10") and method lines ("...(...)") can be rows: skip the regexes otherwise
            f = field.match(line) if "; // 0x" in line else None
            sig = SIGNATURE.match(line.strip()) if not f and "(" in line else None
            if f:
                ctype, member, offset = f.groups()
                if ctype in scalar and "<" not in member and not re.search(r"\b(const|readonly)\b", line) and matches(member):
                    add(member, ctype, "field", line, offset)
            elif sig:
                ctype, member, params = sig.groups()
                if (code and ctype in scalar | {"void"} and not member.startswith(".") and "<" not in member
                        and member not in {"ToString", "GetHashCode", "Equals"}
                        and (ctype != "void" or action.search(_split_words(member))) and matches(member)):
                    m = Method(assembly, namespace, cls[len(namespace) + 1:] if namespace else cls,
                               member, ctype, split_params(params), code.group(1),
                               line.strip().split("{")[0].rstrip().rstrip(";"),
                               int(code.group(2)) if code.group(2) else None)
                    metadata = {"full_name": m.full_name, "class": m.class_name, "namespace": namespace,
                                "method": member, "params": m.params, "args": m.args, "return_type": ctype,
                                "kind": m.kind, "assembly": assembly, "rva": m.rva, "slot": m.slot,
                                "needs_jit": m.needs_jit, "static": bool(re.search(r"\bstatic\b", m.signature)),
                                "signature": m.signature}
                    add(member, ctype, "method", m.signature, method=metadata)
                code = None
    return rows


def as_text(rows, limit=60):
    """A compact list for a prompt: one candidate per line."""
    lines = ["area | object | member | type | kind | suggested value | already in the mod | learned elsewhere"]
    for r in rows[:limit]:
        lr = r.get("learned")
        seen = f"like \"{lr['title']}\": {lr['works']} works, {lr['fails']} fails" if lr else ""
        lines.append(f"{r['area']} | {r['object']} | {r['member']} | {r['ctype']} | {r['kind']} | "
                     f"{r.get('value', '')} {r.get('when', '')} | {'yes' if r['has'] else 'no'} | {seen}")
    return "\n".join(lines)
