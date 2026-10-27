"""Candidates for no-JIT switches in a game's dump.cs: what Mod Studio's Scan tab and `dev.py scan` list.

A switch works without JIT when it is data: a pin writes a field or calls a property setter on a live object
every half second. So the scan looks in the game's own assemblies for
- static reference roots (including generic singletons), live MonoBehaviours and their inherited references;
- bounded paths through reference fields/getters, arrays, lists and dictionary values, with typed casts;
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
from collections import deque
from pathlib import Path

from il2cpp_dump import ADDRESS, FLOATS, INTEGERS, SIGNATURE, TYPE, TYPE_INDEX, Method, split_params

VERSION = 24  # struct values labelled by their own name, package structs skipped, shape structs lower; values inside struct fields ("outer.inner"); sibling families grouped; generic base members specialized per subclass; ScriptableObject data assets as roots; get-only properties through their backing fields; assemblies chosen by content too, bought packages ranked low; object routes cached; presentation, counter and bookkeeping rows ranked lower; the game's own assembly from its bundle ID

IMAGE = re.compile(r"^// Image \d+: (.+?)(?:\.dll)? - (\d+)\s*$")
NAMESPACE = re.compile(r"^\s*// Namespace: ?(\S*)")
CLASS = re.compile(
    r"^(?:(?:public|private|protected|internal|static|sealed|abstract|readonly)\s+)*"
    r"(class|interface|struct)\s+([A-Za-z_]\w*)(<[^>]+>)?(?=[\s:])"
    r"(?:\s*:\s*([^/]+?))?\s*// TypeDefIndex: (\d+)"
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
METHOD = re.compile(r"^\s+(?:(?:public|private|protected|internal|virtual|override|static|sealed|abstract|final)\s+)*([\w.<>\[\], ]+?)\s+(get|set)_(\w+)\(([^)]*)\)")

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
SCRATCH = re.compile(r"^(?:tmp|temp|scratch|help_list)(?:_|$)")
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
                     r"Il2Cpp|__Generated|BestHTTP|VoxelBusters|AdinmoPlugin|IngameDebugConsole|Gametoolkit|glTFast|"
                     r"SwrveUnity)", re.IGNORECASE)
# a struct that holds a position, size, curve or range rather than a stat: its values rank lower
STRUCT_SHAPE = re.compile(r"vector|int[23]$|point|rect|coord|bounds|size|curve|colou?r|quaternion|range", re.IGNORECASE)
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


def _types(text):
    """Split a declaration's base types or generic arguments without splitting nested generics."""
    result, start, depth = [], 0, 0
    for i, c in enumerate(text):
        depth += (c == "<") - (c == ">")
        if c == "," and not depth:
            result.append(text[start:i].strip())
            start = i + 1
    if text[start:].strip():
        result.append(text[start:].strip())
    return result


def parse(path, assemblies=None, bundle_id=None):
    """Classes of the game's own assemblies: {name: {"base", "statics": [field], "fields": {...}, "props": {...}}}.
    A class outside Assembly-CSharp is named with its assembly (HotFix:GameManager), as the runtime finds it."""
    images, classes, generics, others = [], {}, {}, {}
    current = generic = None
    namespace = ""
    profile = None if assemblies else assembly_profile(path)  # which assemblies hold game code, by content
    chosen = None
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
                m = CLASS.match(line)
                if m:  # namespaced game classes too (9 Kings keeps its code in Core, Spells, Policies...)
                    declaration, short, args, bases, index = m.groups()
                    index = int(index)
                    image = ""
                    for start, name in images:
                        if start <= index:
                            image = name
                    full = f"{namespace}.{short}" if namespace else short
                    if not image.startswith("Assembly-CSharp"):
                        full = f"{image}:{full}"
                    parents = _types(bases or "")
                    if args:  # retain generic bases' ancestry too, e.g. EntityBase<T> : IEntityBase
                        generic = generics.setdefault(full, {"t": args[1:-1], "statics": [], "parents": parents})
                        continue
                    if image not in chosen:  # kept for inheritance only (a base class in an engine assembly)
                        others.setdefault(full, {"parents": parents, "type_kind": declaration})
                    elif full not in classes:
                        current = classes[full] = {
                            "base": parents[0] if parents else "", "parents": parents,
                            "type_kind": declaration, "statics": [], "fields": {}, "props": {}, "getters": set(),
                            "holds": set(), "references": {}, "object_fields": {}, "object_getters": {},
                        }
                        current_name = short
                continue
            if generic is not None:
                if line.startswith("}"):
                    generic = None
                else:
                    m = FIELD.match(line) if "; // 0x" in line else None
                    if m and "static" in m.group(1) and m.group(2).strip() == generic["t"]:
                        generic["statics"].append(m.group(3))
                    elif m and "static" not in m.group(1):
                        # instance members of a generic base (EntityBase<T>.Data): each subclass gets them with
                        # its own type arguments (_specialize), so routes and pins can go through them
                        mods, ftype, fname, offset = m.group(1), m.group(2).strip(), m.group(3), m.group(4)
                        kind = scalar(ftype)
                        if kind and "readonly" not in mods:
                            generic.setdefault("fields", {})[fname] = {
                                "ctype": kind[0], "offset": offset, "private": "public" not in mods, "readonly": False}
                        elif not kind:
                            generic.setdefault("object_fields", {})[fname] = ftype
                    else:
                        g = METHOD.match(line) if ("get_" in line or "set_" in line) else None
                        if g and g.group(2) == "get" and not g.group(4).strip() and not re.search(r"\bstatic\b", line):
                            rtype = g.group(1).strip()
                            if scalar(rtype):
                                generic.setdefault("getters", set()).add(g.group(3))
                            else:
                                generic.setdefault("object_getters", {})["get_" + g.group(3)] = rtype
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
                    if not kind:
                        current["references"][fname] = ftype
                elif kind:
                    current["fields"][fname] = {"ctype": kind[0], "offset": offset,
                                                "private": "public" not in mods, "readonly": "readonly" in mods}
                    if kind[1]:
                        current["fields"][fname]["scale"] = kind[1]
                else:
                    current["object_fields"][fname] = ftype
                    current["holds"].add(re.sub(r"[<\[].*", "", ftype).rsplit(".", 1)[-1])
                continue
            m = METHOD.match(line) if ("get_" in line or "set_" in line) else None
            if m:
                rtype, kind, prop, arg = m.groups()
                arg = arg.split()[0] if arg.strip() else ""
                if kind == "get" and scalar(rtype):
                    current["getters"].add(prop)
                elif kind == "get" and not arg and not re.search(r"\bstatic\b", line):
                    current["object_getters"]["get_" + prop] = rtype
                elif kind == "set" and scalar(arg) and not scalar(arg)[1]:  # an FP setter takes a struct: fields only
                    current["props"][prop] = NUMERIC[arg]
    _add_ancestors(classes, others)
    resolve_template, resolve_class = _type_resolver(generics), _type_resolver(classes)

    def template(base, context):
        return resolve_template(base.split("<", 1)[0], context)

    for name, info in list(classes.items()):  # GameManager : Singleton<GameManager> (_specialize adds entries)
        b = re.match(r"([\w.]+)<([\w.]+)>$", info["base"])
        short = name.rpartition(":")[2].rsplit(".", 1)[-1]
        if b and b.group(2).rsplit(".", 1)[-1] == short:
            statics = generics.get(template(b.group(1), name), {}).get("statics")
            if statics:
                info["singleton"] = statics[0]
        pending, seen = list(info["parents"]), set()
        while pending:
            base = pending.pop()
            key = template(base, name) if "<" in base else None
            if key and key not in seen:
                seen.add(key)
                special = _specialize(classes, generics, key, base)
                if special and special not in info["parents"]:
                    info["parents"].append(special)
                for parent in generics[key]["parents"]:
                    # Qualify the generic declaration's non-generic parents in their own context.
                    resolved = resolve_class(parent, key)
                    value = resolved or parent
                    if value not in info["parents"]:
                        info["parents"].append(value)
                        pending.append(value)
    return classes


def _specialize(classes, generics, key, base):
    """The generic base `base` (EntityBase<EntityHeroData>) as a class entry with its type arguments filled in
    (T Data -> EntityHeroData Data), named "<template><args>", made once. None when the template has no
    instance members. The entry is "outside" (no rows of its own): build() lists its value fields on the
    subclasses, and routes go through its fields and getters."""
    g = generics.get(key) or {}
    if not (g.get("fields") or g.get("object_fields") or g.get("object_getters") or g.get("getters")):
        return None
    args = _types(base.split("<", 1)[1].rsplit(">", 1)[0]) if "<" in base else []
    names = [p.strip() for p in _types(g.get("t", ""))]
    subst = dict(zip(names, args))

    def fill(ftype):
        return re.sub(r"\b\w+\b", lambda m: subst.get(m.group(0), m.group(0)), ftype)

    name = f"{key}<{','.join(args)}>"
    if name not in classes:
        classes[name] = {
            "base": "", "parents": [], "type_kind": "class", "outside": True, "generic_of": key,
            "statics": [], "fields": dict(g.get("fields") or {}), "props": {}, "getters": set(g.get("getters") or ()),
            "holds": set(), "references": {},
            "object_fields": {n: fill(t) for n, t in (g.get("object_fields") or {}).items()},
            "object_getters": {n: fill(t) for n, t in (g.get("object_getters") or {}).items()},
        }
    return name


def _add_ancestors(classes, others):
    """Base classes from the assemblies the scan skips, as member-less "outside" entries, so a game class whose
    chain runs through them (CharacterController : ArcadeSprite (PhaserPort) : ... : MonoBehaviour) still counts
    as a live MonoBehaviour. Their members are not scanned."""
    resolve = _type_resolver(others)
    pending = [(base, name) for name, info in classes.items() for base in info["parents"]]
    while pending:
        base, context = pending.pop()
        full = resolve(base.split("<", 1)[0], context)
        if not full or full in classes:
            continue
        info = others[full]
        classes[full] = {"base": info["parents"][0] if info["parents"] else "", "parents": info["parents"],
                         "type_kind": info["type_kind"], "outside": True, "statics": [], "fields": {}, "props": {},
                         "getters": set(), "holds": set(), "references": {}, "object_fields": {},
                         "object_getters": {}}
        pending.extend((parent, full) for parent in info["parents"])


def _type_resolver(classes):
    """Prefer the declaring namespace, then assembly, then one unambiguous global name."""
    qualified, short = {}, {}
    for n in classes:
        local = n.rpartition(":")[2]
        qualified.setdefault(local, []).append(n)
        short.setdefault(local.rsplit(".", 1)[-1], []).append(n)

    def resolve(name, context):
        name = name.strip()
        if ":" in name:
            return name if name in classes else None
        assembly, _, local = context.rpartition(":")
        namespace = local.rpartition(".")[0]
        prefix = assembly + ":" if assembly else ""
        near = prefix + (namespace + "." if namespace else "") + name
        if near in classes:
            return near
        candidates = qualified.get(name, ()) if "." in name else short.get(name, ())
        same = [n for n in candidates if n.rpartition(":")[0] == assembly]
        if len(same) == 1:
            return same[0]
        if same:
            return None
        return candidates[0] if len(candidates) == 1 else None

    return resolve


def _reference(ftype, context, classes, resolve=None):
    """A reference class and optional collection operation. Value types are never pointers."""
    ftype, each = ftype.strip(), None
    if ftype.endswith("[]"):
        ftype, each = ftype[:-2], "array"
    elif "<" in ftype and ftype.endswith(">"):
        container, args = ftype.split("<", 1)
        args = _types(args[:-1])
        container = container.rsplit(".", 1)[-1]
        if container == "List" and len(args) == 1:
            ftype, each = args[0], "list"
        elif container in {"Dictionary", "OrderedDictionary"} and len(args) == 2:
            ftype, each = args[1], "values"
        else:
            return None
    if scalar(ftype):
        return None
    cls = (resolve or _type_resolver(classes))(ftype, context)
    if not cls or classes[cls].get("type_kind") == "struct":
        return None
    return cls, each


def _lineage(cls, classes, resolve):
    """Breadth-first declarations, nearest first; visited types bound malformed inheritance cycles."""
    pending, seen = deque([cls]), set()
    while pending:
        owner = pending.popleft()
        if owner in seen:
            continue
        seen.add(owner)
        yield owner
        for base in classes[owner].get("parents", [classes[owner].get("base", "")]):
            parent = resolve(base, owner)
            if parent:
                pending.append(parent)


LIVE_BASES = {"MonoBehaviour", "UnityEngine.MonoBehaviour",
              # data assets (balance tables, item and weapon stats): the runtime finds the loaded ones with
              # Resources.FindObjectsOfTypeAll (AMIl2cppData.m AMIl2cppFindObjects)
              "ScriptableObject", "UnityEngine.ScriptableObject"}


def _component(cls, classes, resolve):
    """True for a class whose live objects the runtime can collect as Class[]: scene objects and data assets."""
    return any(base in LIVE_BASES
               for owner in _lineage(cls, classes, resolve)
               for base in classes[owner].get("parents", [classes[owner].get("base", "")]))


def _reference_members(cls, kind, classes, resolve):
    """Inherited reference declarations with runtime name shadowing, including scalar shadows."""
    seen = set()
    for owner in _lineage(cls, classes, resolve):
        info = classes[owner]
        for member, ftype in sorted(info.get(kind, {}).items()):
            if member not in seen:
                yield member, ftype, owner
        if kind == "object_getters":
            seen.update(info.get("object_getters", {}))
            seen.update("get_" + prop for prop in info.get("getters", ()))
        else:
            for fields in ("fields", "object_fields", "references"):
                seen.update(info.get(fields, {}))


def object_paths(classes, max_hops=8):
    """One deterministic shortest route per reference class, from singleton, static field or Unity object.

    Steps name a field/getter, the resulting class and optional collection operation. A class-only step
    narrows a base/interface to a concrete subclass; the runtime must check each object's actual type.
    """
    paths, queue = {}, deque()
    resolve = _type_resolver(classes)

    def offer(cls, obj, root, path, reach):
        if cls not in paths and len(path) <= max_hops:
            paths[cls] = {"object": obj, "root_class": root, "path": path, "reach": reach}
            queue.append(cls)

    # Existing direct roots win ties; adding other static roots never alters their shape.
    for cls, info in sorted(classes.items()):
        if DEMO.search(cls) or info.get("type_kind") in {"struct", "interface"}:
            continue
        statics = info.get("statics", [])
        singleton = statics[0] if statics else info.get("singleton")
        if singleton:
            offer(cls, f"{cls}.{singleton}", cls, [], 3)
        elif _component(cls, classes, resolve):
            offer(cls, f"{cls}[]", cls, [], 1)
    for owner, info in sorted(classes.items()):
        if DEMO.search(owner):
            continue
        for field, ftype in sorted(info.get("references", {}).items()):
            if SCRATCH.match(_split_words(field).lstrip("_").removeprefix("m_")):
                continue
            result = _reference(ftype, owner, classes, resolve)
            if result and not result[1]:  # collection roots require a separate root contract
                cls, _ = result
                if not DEMO.search(cls):
                    offer(cls, f"{owner}.{field}", cls, [], 3)

    children = {}
    for cls, info in sorted(classes.items()):
        if DEMO.search(cls) or info.get("type_kind") in {"struct", "interface"}:
            continue
        for base in info.get("parents", [info.get("base", "")]):
            parent = resolve(base, cls)
            if parent:
                children.setdefault(parent, []).append(cls)
    while queue:
        cls = queue.popleft()
        current = paths[cls]
        if len(current["path"]) >= max_hops:
            continue
        edges = []
        for kind, members in (("field", "object_fields"), ("getter", "object_getters")):
            for member, ftype, owner in _reference_members(cls, members, classes, resolve):
                if SCRATCH.match(_split_words(member.removeprefix("get_")).lstrip("_").removeprefix("m_")):
                    continue  # query work buffers may be empty except during a method call
                result = _reference(ftype, owner, classes, resolve)
                if result:
                    target, each = result
                    step = {kind: member, "class": target}
                    if each:
                        step["each"] = each
                    edges.append((target, step))
        edges.extend((target, {"class": target}) for target in children.get(cls, []))
        for target, step in edges:
            if not DEMO.search(target):
                offer(target, current["object"], current["root_class"], current["path"] + [step], current["reach"])
    return paths


def _declaration(classes, resolve, cls, member):
    """(owner, kind, type) of member as cls has it (own or inherited), or None. Offsets are left out: the runtime
    finds fields by name, so a field that only moved is the same target."""
    if cls not in classes:
        return None
    if "." in member and not member.startswith("<"):  # "outer.inner": a value inside a struct field
        outer, _, inner = member.partition(".")
        decl = _declaration(classes, resolve, cls, outer)
        struct = resolve(decl[2], decl[0]) if decl and decl[1] == "ref" else None
        f = (classes.get(struct) or {}).get("fields", {}).get(inner) if struct else None
        if not f or classes[struct].get("type_kind") != "struct":
            return None
        return decl[0], "struct-field", f"{struct}.{inner}:{f['ctype']}"
    for owner in _lineage(cls, classes, resolve):
        info = classes[owner]
        if member in info.get("fields", {}):
            return owner, "field", info["fields"][member]["ctype"]
        if member.startswith("set_") and member[4:] in info.get("props", {}):
            return owner, "setter", info["props"][member[4:]]
        if member in info.get("object_fields", {}):
            return owner, "ref", info["object_fields"][member]
        if member in info.get("object_getters", {}):
            return owner, "getter", info["object_getters"][member]
        if member in info.get("references", {}):
            return owner, "static", info["references"][member]
        if member in info.get("statics", []) or member == info.get("singleton"):
            return owner, "static", owner
    return None


def pin_signature(classes, pin):
    """What a pin reaches, declaration by declaration (root object, each path step, the field or setter), from one
    dump's classes; None when any of it is gone. Equal signatures in two game versions mean the pin's target did
    not change, so a works/fails mark made on the old version still describes the new one (dev.py update)."""
    resolve = _type_resolver(classes)
    obj = pin.get("object") or ""
    parts = []
    whole = resolve(obj, obj) if obj and not obj.endswith("[]") else None
    if whole:  # a bare class ("Lock"): its live objects
        parts.append(("class", whole))
        target = whole
    elif obj.endswith("[]"):
        cls = resolve(obj[:-2], obj[:-2])
        if not cls:
            return None
        parts.append(("each", cls))
        target = cls
    else:
        name, _, member = obj.rpartition(".")
        cls = resolve(name, name)
        decl = _declaration(classes, resolve, cls, member) if cls else None
        if not decl:
            return None
        parts.append(("root",) + decl)
        found = _reference(decl[2], decl[0], classes, resolve) if decl[1] != "static" or decl[2] != decl[0] else (cls, None)
        target = found[0] if found else cls
    if pin.get("root_class"):  # the root narrowed to the class the path starts from
        target = resolve(pin["root_class"], target) or pin["root_class"]
        if target not in classes:
            return None
        parts.append(("class", target))
    for step in pin.get("path") or []:
        member = step.get("field") or step.get("getter")
        if member:
            decl = _declaration(classes, resolve, target, member)
            if not decl:
                return None
            parts.append(("step",) + decl)
        target = resolve(step.get("class", ""), target) or step.get("class")
        if target not in classes:
            return None
        parts.append(("class", target))
    if pin.get("via"):  # the object's getter hands over the instance that holds the field ("class")
        decl = _declaration(classes, resolve, target, pin["via"])
        if not decl:
            return None
        parts.append(("via",) + decl)
        target = resolve(pin.get("class", ""), target) or pin.get("class")
        if target not in classes:
            return None
        parts.append(("class", target))
    member = pin.get("field") or pin.get("setter")
    if member:
        decl = _declaration(classes, resolve, target, member)
        if not decl:
            return None
        parts.append(("member",) + decl)
    return tuple(parts)


def path_errors(classes, obj, root_class, path):
    """Validate a user-picked route against dump metadata, independently of the scan's shortest route.

    Returns readable errors (empty on success). The metadata proves the declared pointer types only;
    it cannot prove a root or collection currently contains any live objects.
    """
    resolve = _type_resolver(classes)

    def parents(cls):
        return [p for base in classes[cls].get("parents", [classes[cls].get("base", "")])
                if (p := resolve(base, cls))]

    def derives(child, base):
        pending, seen = [child], set()
        while pending:
            cls = pending.pop()
            if cls == base:
                return True
            if cls not in seen:
                seen.add(cls)
                pending.extend(parents(cls))
        return False

    def member_type(cls, member, kind):
        for name, ftype, owner in _reference_members(cls, kind, classes, resolve):
            if name == member:
                return _reference(ftype, owner, classes, resolve)
        return None

    if not isinstance(obj, str) or not obj:
        return ["Object root must be a nonempty string"]
    if obj.endswith("[]"):
        owner = resolve(obj[:-2], obj[:-2])
        if owner and owner != obj[:-2]:
            return [f"Object root must use canonical class {owner}"]
        result = (owner, None) if owner and _component(owner, classes, resolve) else None
    else:
        root_name, _, field = obj.rpartition(".")
        owner = resolve(root_name, root_name)
        if owner and owner != root_name:
            return [f"Object root must use canonical class {owner}"]
        result = member_type(owner, field, "references") if owner else None
        if owner and (field in classes[owner].get("statics", []) or field == classes[owner].get("singleton")):
            result = owner, None
    if not result or result[1]:
        return [f"Object root {obj} is not a known static reference or MonoBehaviour collection"]
    cls = result[0]
    root = resolve(root_class, cls) if isinstance(root_class, str) else cls
    if root != cls or (root_class is not None and root_class != cls):
        return [f"Root class must be {cls}, the declared type of {obj}"]
    if not isinstance(path, list) or len(path) > 8:
        return ["Object path must be a list of at most 8 steps"]
    for i, step in enumerate(path, 1):
        if not isinstance(step, dict) or set(step) - {"class", "field", "getter", "each"}:
            return [f"Path step {i} has an invalid shape"]
        name = step.get("class")
        target = resolve(name, cls) if isinstance(name, str) else None
        if not target or classes[target].get("type_kind") == "struct":
            return [f"Path step {i} needs a known reference class"]
        if name != target:
            return [f"Path step {i} must use canonical class {target}"]
        selectors = [kind for kind in ("field", "getter") if kind in step]
        if not selectors:
            if "each" in step or not derives(target, cls):
                return [f"Path step {i} cannot narrow {cls} to {target}"]
        elif len(selectors) != 1 or not isinstance(step[selectors[0]], str):
            return [f"Path step {i} needs exactly one field or getter"]
        else:
            selector = selectors[0]
            member = step[selector]
            result = member_type(cls, member, "object_fields" if selector == "field" else "object_getters")
            if not result:
                return [f"Path step {i}: {cls}.{member} is not a known object reference"]
            expected, each = result
            if expected != target or step.get("each") != each:
                return [f"Path step {i}: {cls}.{member} requires class {expected}" +
                        (f" and each={each}" if each else " without collection expansion")]
        cls = target
    return []


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


FAMILY_MIN = 3  # siblings declaring the same field: this many and they become one family


def _families(rows, classes):
    """The same value field declared by sibling classes (each projectile's _radius, each weapon's _speed: same
    name, same type, same base class) as one family: the best-scoring row stands for it ("family_size": N,
    "family": the others' targets), the rest are marked "family_member" and ranked lower. Auto-build asks about
    the representative only and makes one switch for the whole family."""
    groups = {}
    for r in rows:
        base = (classes.get(r["class"]) or {}).get("base", "")
        if base and r["kind"] in ("field", "setter"):
            groups.setdefault((r["member"], r["ctype"], base), []).append(r)
    for members in groups.values():
        if len(members) < FAMILY_MIN:
            continue
        members.sort(key=lambda r: -r["score"])
        head, rest = members[0], members[1:]
        head["family_size"] = len(members)
        head["family"] = [{"class": r["class"], "member": r["member"], "object": r["object"],
                           **({"root_class": r["root_class"], "path": r["path"]} if "path" in r else {})} for r in rest]
        for r in rest:
            r["family_member"] = head["class"]
            r["score"] -= 2


def build(classes, existing=frozenset(), learned=None, paths=None):
    """learned(name, ctype, cls) -> (bonus, use or None): dev_learn.member_boost, what worked in other games.
    paths: object_paths(classes) when the caller has it (scan() keeps it in its cache)."""
    rows, flagged = [], []
    saved = saved_classes(classes)
    paths = object_paths(classes) if paths is None else paths
    resolve = _type_resolver(classes)
    for cls, info in classes.items():
        if DEMO.search(cls) or cls not in paths or info.get("outside") or info.get("type_kind") in {"struct", "interface"}:
            continue  # a package's demo or sample script, not the game
        route = paths[cls]
        obj, reach = route["object"], route["reach"]
        local = cls.rpartition(":")[2]
        presentation = bool(PRESENTATION.search(local.rsplit(".", 1)[-1]) or UI_NAMESPACE.search(local.rpartition(".")[0]))
        package = bool(PACKAGE.match(local.rpartition(".")[0] or local))
        root = route["root_class"] if route.get("root_class") else obj[:-2] if obj.endswith("[]") else obj.rpartition(".")[0]
        root_local = (root or "").rpartition(":")[2]
        via_ui = bool(root_local) and root_local != local and bool(
            PRESENTATION.search(root_local.rsplit(".", 1)[-1]) or UI_NAMESPACE.search(root_local.rpartition(".")[0]))
        members = []
        for prop, ctype in info["props"].items():
            members.append((f"set_{prop}", prop, ctype, "setter", None, prop in info["getters"], False))
        for fname, f in info["fields"].items():
            if f.get("readonly"):
                continue
            if fname.startswith("<"):
                # a compiler backing field: its setter covers it, but a get-only property ({ get; }) has none, so
                # the field is the only way to its value (shown under the property's name)
                prop = fname[1:].split(">", 1)[0]
                if not fname.endswith(">k__BackingField") or prop in info["props"]:
                    continue
                members.append((fname, prop, f["ctype"], "field", f["offset"], prop in info["getters"], True))
                continue
            members.append((fname, fname, f["ctype"], "field", f["offset"], False, f["private"]))
        struct_of = {}  # "outer.inner" -> the struct's short name, for its label and rank
        for outer, ftype in info.get("object_fields", {}).items():  # values inside a struct it holds: "outer.inner"
            inner_cls = resolve(ftype, cls) if not ftype.endswith("]") and "<" not in ftype else None
            inner = classes.get(inner_cls) if inner_cls else None
            struct_ns = (inner_cls or "").rpartition(":")[2].rpartition(".")[0]
            if inner and inner.get("type_kind") == "struct" and not outer.startswith("<") and not (
                    struct_ns and (PACKAGE.match(struct_ns) or LIBRARY.match(struct_ns))):  # text, tween, SDK types
                for fname, f in inner.get("fields", {}).items():
                    if not fname.startswith("<") and not f.get("readonly"):
                        members.append((f"{outer}.{fname}", f"{outer} {fname}", f["ctype"], "field", None, False,
                                        f["private"]))
                        struct_of[f"{outer}.{fname}"] = inner_cls.rpartition(":")[2].rsplit(".", 1)[-1]
        own = {m[0] for m in members}
        for owner in _lineage(cls, classes, resolve):  # value fields a generic base declares (EntityBase<T>.Hp)
            g = classes[owner]
            if not g.get("generic_of"):
                continue
            for fname, f in g["fields"].items():
                if fname not in own and not fname.startswith("<"):
                    own.add(fname)
                    members.append((fname, fname, f["ctype"], "field", f["offset"], fname in g["getters"], f["private"]))
        for target, name, ctype, kind, offset, getter, private in members:
            # a struct value is labelled by its own name; in a struct of stat modifiers (Boost.AttackStrengthBoost)
            # "boost" says modifier, not a shop item, so it doesn't make the value an in-app purchase
            label = re.sub(r"(?i)boost", "", target.rpartition(".")[2]) or target if target in struct_of else name
            area, by_member = area_of(cls.rpartition(":")[2], label)
            row = {
                "class": cls, "object": obj, "member": target, "name": name, "kind": kind, "ctype": ctype,
                "offset": offset, "area": area, "has": f"{cls}.{target}" in existing,
            }
            if route["path"] or (not obj.endswith("[]") and obj.rpartition(".")[0] != cls):
                row.update(root_class=route["root_class"], path=route["path"])
            scale = info["fields"].get(name, {}).get("scale") if kind == "field" else None
            if scale:
                row["scale"] = scale  # fixed point: the value is written as value * scale
            reason = flag_of(cls, label)
            if reason:
                row["flag"] = reason
                flagged.append(row)
            score = reach + (2 if kind == "setter" and getter else 1)
            score += 2 if by_member else (1 if area != "Other" else 0)
            if kind == "field" and (name + "Default") in info["fields"]:
                score += 2  # "Accuracy" next to "AccuracyDefault": a skill value the game reads
            if NOISE.search(name):
                score -= 4
            if target in struct_of and STRUCT_SHAPE.search(struct_of[target]):
                score -= 2  # a position, size or curve, not a stat
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
            elif via_ui:
                score -= 2  # a game value, but reached through a UI object: often stale or a copy
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
    _families(rows, classes)
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
                "data": {k: dict(v, getters=sorted(v["getters"]), holds=sorted(v["holds"])) for k, v in classes.items()},
                "paths": object_paths(classes)}  # routes depend on the dump only: kept with it (1.7 s per scan)
        tmp = cache.with_name(cache.name + ".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, cache)
    classes = {k: dict(v, getters=set(v["getters"]), holds=set(v.get("holds", ()))) for k, v in data["data"].items()}
    existing = existing_targets(tweaks)
    rows, flagged = build(classes, existing, learned, data.get("paths"))
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
