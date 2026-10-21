"""Find single-player cheat candidates in a Unity IL2CPP game and record the chosen ones in the mod's
hooks.json, the file scripts/intake_auto.py checks against every new version of the game.

Usage:
  python3 scripts/find_cheats.py <dump.cs>                     list the candidates by category
  python3 scripts/find_cheats.py <dump.cs> --mod NAME --pick   choose the ones for mods/NAME/hooks.json
  python3 scripts/find_cheats.py <dump.cs> --mod NAME --auto   take the best one of each category
  python3 scripts/find_cheats.py <dump.cs> --json FILE         save every candidate
  python3 scripts/find_cheats.py <dump.cs> --mod NAME --auto --json FILE   both at once
  python3 scripts/find_cheats.py <dump.cs> --left-out          also list what the rules left out

dump.cs is Il2CppDumper's listing of the game (scripts/il2cpp_dump.py says how to make it).

Scoring: a method whose name contains a category's keywords (god mode, damage, cooldown, speed, ammo)
scores that category's weight per keyword, plus half per keyword in its class's name, and the highest
category wins; a class name alone (Update or .ctor in PlayerHealth) is no candidate. A method is
only a candidate when the category has something to do with its return type: skip the call (void), or
return a constant (a number). The result is a starting point to read, not a list of working cheats.

The rules in README.md decide what is never offered, whatever the score: anything named for purchases,
subscriptions, premium currency or paid tiers, and anything named for networking, multiplayer, servers
or leaderboards. A game whose combat lives in such classes is an online game, and not one for this repo.

Each hook written to hooks.json has the names intake_auto.py checks (namespace, class, method) plus
assembly, args, return_type and signature for the Tweak.x hook, and the category, action and value
this script suggested. Hooks already in the file are kept; one for the same method is replaced.
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass

from il2cpp_dump import methods

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = "unity-il2cpp"

# What a category does to a method of each kind: "skip" leaves the call out, "return" replaces its result.
CATEGORIES = {
    "god_mode": {
        "title": "god mode",
        "weight": 10,
        "keywords": ["TakeDamage", "Damage", "Hurt", "Die", "Kill", "Health", "HP", "Invincib", "GodMode", "Immortal", "Invulnerab"],
        "fits": {"void": ("skip", None), "int": ("return", 9999), "float": ("return", 9999.0)},
    },
    "damage": {
        "title": "damage",
        "weight": 8,
        "keywords": ["GetDamage", "Attack", "Damage", "Power", "Strength", "Atk", "WeaponDamage", "SpellDamage"],
        "fits": {"int": ("return", 9999), "float": ("return", 9999.0)},
    },
    "cooldown": {
        "title": "cooldown",
        "weight": 7,
        "keywords": ["Cooldown", "Wait", "Delay", "Timer", "Interval", "Reload", "Recharge"],
        "fits": {"int": ("return", 0), "float": ("return", 0.0)},
    },
    "speed": {
        "title": "speed",
        "weight": 6,
        "keywords": ["Speed", "Velocity", "MoveSpeed", "WalkSpeed", "RunSpeed", "MovementSpeed"],
        "fits": {"float": ("return", 10.0)},
    },
    "ammo": {
        "title": "ammo",
        "weight": 5,
        "keywords": ["Ammo", "Clip", "Magazine", "Bullet", "Rocket", "Arrow", "Projectile", "Shell"],
        "fits": {"int": ("return", 999), "void": ("skip", None)},
    },
}

# README rules: never what the developer sells, never online play. Matched in the namespace, class and method.
LEFT_OUT = {
    "purchases": ["Purchase", "IAP", "InApp", "Buy", "Shop", "Store", "Price", "Premium", "Subscri", "Vip", "Paywall", "Billing", "Receipt", "Gem", "Diamond"],
    "online play": ["Network", "Multiplayer", "Server", "Online", "Matchmak", "Lobby", "Leaderboard", "Photon", "Netcode", "Rpc", "PvP", "Clan", "Guild", "Ranked", "Socket", "Packet", "Sync"],
}


@dataclass
class Candidate:
    method: object
    category: str
    score: int
    action: str
    value: object

    def hook(self):
        return {**self.method.hook(), "category": self.category, "action": self.action, "value": self.value}

    def describe(self):
        m = self.method
        what = "skip" if self.action == "skip" else f"return {self.value}"
        jit = "  needs JIT" if m.needs_jit else ""
        return f"{m.full_name}({', '.join(m.params)})  {m.return_type}  score {self.score}  {what}{jit}"


def left_out(method):
    """The README rule a method falls under, or None."""
    names = f"{method.namespace} {method.class_name} {method.name}".lower()
    for reason, words in LEFT_OUT.items():
        if any(w.lower() in names for w in words):
            return reason
    return None


def candidate(method):
    """The best-scoring category whose action fits the method's return type, or None."""
    method_name, class_name = method.name.lower(), method.class_name.lower()
    best = None
    for name, cat in CATEGORIES.items():
        if method.kind not in cat["fits"]:
            continue
        words = [w.lower() for w in cat["keywords"]]
        in_method = sum(w in method_name for w in words)
        if not in_method:  # the class alone says nothing about this method (Update, .ctor, ...)
            continue
        score = cat["weight"] * in_method + cat["weight"] // 2 * sum(w in class_name for w in words)
        if best is None or score > best.score:
            action, value = cat["fits"][method.kind]
            best = Candidate(method, name, score, action, value)
    return best


def scan(path):
    """(candidates best first, {reason: [methods left out]})"""
    found, out = [], {reason: [] for reason in LEFT_OUT}
    for m in methods(path):
        reason = left_out(m)
        if reason:
            out[reason].append(m)
            continue
        c = candidate(m)
        if c:
            found.append(c)
    # at equal scores, a hook that also works without JIT first
    found.sort(key=lambda c: (-c.score, c.method.needs_jit, c.method.full_name))
    return found, out


def show(candidates, out, left_out_too=False):
    number = 0
    for name, cat in CATEGORIES.items():
        group = [c for c in candidates if c.category == name]
        if not group:
            continue
        print(f"\n{cat['title']}")
        for c in group:
            number += 1
            print(f"  {number:3}  {c.describe()}")
    if not candidates:
        print("no candidates")
    skipped = ", ".join(f"{len(ms)} named for {reason}" for reason, ms in out.items() if ms)
    if skipped:
        print(f"\nleft out by the rules in README.md: {skipped}" + ("" if left_out_too else " (--left-out lists them)"))
    if left_out_too:
        for reason, ms in out.items():
            for m in ms:
                print(f"  {reason}: {m.full_name}({', '.join(m.params)})")


def in_display_order(candidates):
    return [c for name in CATEGORIES for c in candidates if c.category == name]


def pick(candidates):
    """Ask for numbers from the list show() printed: "1 3 5-7", "all" or "none"."""
    ordered = in_display_order(candidates)
    print("\nWhich ones? Numbers or ranges (1 3 5-7), all, or none:")
    answer = input("> ").strip().lower()
    if answer in ("", "none"):
        return []
    if answer == "all":
        return ordered
    chosen = []
    for part in answer.replace(",", " ").split():
        lo, _, hi = part.partition("-")
        try:
            for n in range(int(lo), int(hi or lo) + 1):
                if 1 <= n <= len(ordered) and ordered[n - 1] not in chosen:
                    chosen.append(ordered[n - 1])
        except ValueError:
            print(f"ignored {part!r}")
    return chosen


def best_per_category(candidates):
    seen, chosen = set(), []
    for c in in_display_order(candidates):
        if c.category not in seen:
            seen.add(c.category)
            chosen.append(c)
    return chosen


def write_hooks(path, chosen):
    """Add the chosen candidates to a hooks.json, replacing entries for the same methods."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {"engine": ENGINE, "hooks": []}
    new = {c.method.key: c.hook() for c in chosen}
    hooks = [h for h in data.get("hooks", []) if (h.get("namespace", ""), h.get("class"), h.get("method")) not in new]
    data["engine"] = data.get("engine") or ENGINE
    data["hooks"] = hooks + list(new.values())
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return len(data["hooks"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dump", help="dump.cs from Il2CppDumper")
    parser.add_argument("--mod", help="the mod whose hooks.json gets the chosen candidates")
    parser.add_argument("--pick", action="store_true", help="choose candidates from the list")
    parser.add_argument("--auto", action="store_true", help="take the best candidate of each category")
    parser.add_argument("--json", metavar="FILE", help="save every candidate as JSON")
    parser.add_argument("--left-out", action="store_true", help="also list what the rules left out")
    args = parser.parse_args(argv)
    if (args.pick or args.auto) and not args.mod:
        parser.error("--pick and --auto need --mod NAME, the mod whose hooks.json to write")
    if args.mod and not os.path.isdir(os.path.join(ROOT, "mods", args.mod)):
        parser.error(f"mods/{args.mod} does not exist (scripts/new_mod.sh makes it)")

    candidates, out = scan(args.dump)
    show(candidates, out, args.left_out)
    if args.json:
        with open(args.json, "w", encoding="utf-8", newline="\n") as f:
            json.dump([{**c.hook(), "score": c.score, "rva": c.method.rva} for c in candidates], f, indent=2)
            f.write("\n")
        print(f"\nsaved {len(candidates)} candidates to {args.json}")
    if not (args.pick or args.auto):
        return 0
    chosen = pick(candidates) if args.pick else best_per_category(candidates)
    if not chosen:
        print("nothing chosen; hooks.json unchanged")
        return 0
    path = os.path.join(ROOT, "mods", args.mod, "hooks.json")
    total = write_hooks(path, chosen)
    print(f"\nmods/{args.mod}/hooks.json: {len(chosen)} added or replaced, {total} hooks in all")
    print("Next: hook them in Tweak.x. scripts/intake_auto.py checks these names in every new version;")
    print(f"scripts/check_hooks.py {args.mod} <new dump.cs> checks their signatures too.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
