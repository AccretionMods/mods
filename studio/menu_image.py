"""A mod's menu as data for Mod Studio's Menu image page (studio/menu_image.html): the categories and their rows
(switch, slider, list) in the order the game shows them, from the hand-written Tweak.x and tweaks.json.

Hand-written items come first (Tweak.x's addSection/addSwitch/addSlider/addList, read with a regex: a menu built
in a loop or with computed titles shows only what is written out); tweaks.json's follow, in their sections.
"""

import json
import re
from pathlib import Path

ITEM = re.compile(
    r'addSection:\s*@"((?:[^"\\]|\\.)*)"'
    r'|addSwitch:\s*@"[^"]*"\s+title:\s*@"((?:[^"\\]|\\.)*)"'
    r'|addSlider:\s*@"[^"]*"\s+title:\s*@"((?:[^"\\]|\\.)*)"\s+min:\s*([-\d.]+)\s+max:\s*([-\d.]+)\s+defaultValue:\s*([-\d.]+)'
    r'|addList:\s*@"((?:[^"\\]|\\.)*)"'
)


def _section(sections, name):
    for s in sections:
        if s["name"] == name:
            return s
    s = {"name": name, "rows": []}
    sections.append(s)
    return s


def read(mod_folder):
    folder = Path(mod_folder)
    meta = json.loads((folder / "mod.json").read_text(encoding="utf-8"))
    title = meta.get("display_name") or meta.get("name") or folder.name
    sections = []
    current = _section(sections, "General")
    tweak = folder / "Tweak.x"
    if tweak.is_file():
        for m in ITEM.finditer(tweak.read_text(encoding="utf-8", errors="replace")):
            if m.group(1) is not None:
                current = _section(sections, m.group(1))
            elif m.group(2) is not None:
                current["rows"].append({"kind": "switch", "title": m.group(2), "on": False})
            elif m.group(3) is not None:
                lo, hi, default = float(m.group(4)), float(m.group(5)), float(m.group(6))
                current["rows"].append({"kind": "slider", "title": m.group(3), "min": lo, "max": hi, "value": default,
                                        "default": default})
            elif m.group(7) is not None:
                current["rows"].append({"kind": "list", "title": m.group(7)})
    tj = folder / "tweaks.json"
    if tj.is_file():
        data = json.loads(tj.read_text(encoding="utf-8"))
        title = data.get("title") or title
        for t in data.get("tweaks", []):
            s = _section(sections, t.get("section") or "General")
            if t.get("kind") == "slider":
                s["rows"].append({"kind": "slider", "title": t["title"], "min": t.get("min", 0), "max": t.get("max", 1),
                                  "value": t.get("default", t.get("min", 0)), "default": t.get("default", t.get("min", 0))})
            else:
                s["rows"].append({"kind": "switch", "title": t["title"], "on": False})
    # the menu shows the game's own name (as under its icon), not the mod's
    title = re.sub(r"\+$", "", re.sub(r"\s*\(dev\)$", "", title.strip())).strip()
    return {"title": title, "sections": [s for s in sections if s["rows"]]}
