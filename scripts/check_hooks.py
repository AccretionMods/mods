"""Check a mod's hooks.json against the dump.cs of a new version of the game, before updating the mod.

Usage: python3 scripts/check_hooks.py <mod> <dump.cs>

scripts/intake_auto.py only checks that each hooked name is still in global-metadata.dat. This reads
Il2CppDumper's listing of the new version (scripts/il2cpp_dump.py) and reports each hook as
  ok        found, same return type and parameter count as hooks.json records
  changed   found by name, but the return type or parameter count differs: the Tweak.x hook needs a look
  missing   no such method; methods with the same name in other classes are listed as leads
and exits 1 when any hook is changed or missing. Hooks without args or return_type are only checked by name.
"""

import json
import os
import sys

from il2cpp_dump import methods

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check(hooks, found):
    """(status, hook, detail) for each hook, against the methods of the new dump."""
    by_key = {m.key: m for m in found}
    results = []
    for h in hooks:
        key = (h.get("namespace", ""), h["class"], h["method"])
        m = by_key.get(key)
        if m is None:
            leads = [x.full_name for x in found if x.name == h["method"] and x.class_name != h["class"]]
            leads += [x.full_name for x in found if x.class_name == h["class"] and x.name.lower() == h["method"].lower() and x.name != h["method"]]
            detail = f"same name in: {', '.join(leads[:3])}" if leads else "no lead"
            results.append(("missing", h, detail))
            continue
        was = []
        if "return_type" in h and h["return_type"] != m.return_type:
            was.append(f"returned {h['return_type']}, now {m.return_type}")
        if "args" in h and h["args"] != m.args:
            was.append(f"took {h['args']} args, now {m.args}")
        if was:
            results.append(("changed", h, "; ".join(was) + f"  ({m.signature})"))
        else:
            results.append(("ok", h, f"RVA {m.rva}  {m.signature}"))
    return results


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        sys.exit(__doc__)
    mod, dump = argv
    path = os.path.join(ROOT, "mods", mod, "hooks.json")
    if not os.path.isfile(path):
        sys.exit(f"mods/{mod}/hooks.json not found (scripts/find_cheats.py --mod {mod} writes it)")
    with open(path, encoding="utf-8") as f:
        hooks = json.load(f).get("hooks", [])
    results = check(hooks, methods(dump))
    for status, h, detail in results:
        name = ".".join(p for p in (h.get("namespace", ""), h["class"], h["method"]) if p)
        print(f"  {status:8} {name}  {detail}")
    counts = {s: sum(1 for r in results if r[0] == s) for s in ("ok", "changed", "missing")}
    print(f"{counts['ok']} ok, {counts['changed']} changed, {counts['missing']} missing of {len(results)} hooks")
    return 1 if counts["changed"] or counts["missing"] else 0


if __name__ == "__main__":
    sys.exit(main())
