"""A mod's tweaks.json (its menu): the one place that reads and writes it.

Every writer (dev.py add/pin/call/tweak/remove/mark, Auto-build, one-off scripts) goes through save(), which
- puts back the switches every mod's Tweak.x relies on (REQUIRED, the new-mod template's first item),
- refuses a file that isn't a menu (no "tweaks" list, an item without a key, the same key twice),
- keeps the previous file in a history folder (the newest HISTORY_KEEP), so a bad bulk rewrite can be undone,
- writes atomically with LF line ends.

undeclared() is the check before a build: switch variables (am_<key>) that Tweak.x uses but tweaks.json no
longer has, which otherwise show up only as a compiler error in the generated code.
"""

from __future__ import annotations

import copy
import json
import os
import re
import time
from pathlib import Path

NAME = "tweaks.json"
HISTORY_KEEP = 20

# What the new-mod template puts first (dev.py new); the template's Tweak.x reads am_keepAwake every tick.
REQUIRED = ({"key": "keepAwake", "title": "Keep screen awake", "kind": "switch", "hooks": []},)


class TweaksFileError(ValueError):
    pass


def path(folder) -> Path:
    return Path(folder) / NAME


def load(folder) -> dict:
    with open(path(folder), encoding="utf-8") as f:
        return json.load(f)


def problems(data) -> list[str]:
    """What makes this not a mod menu at all (gen_tweaks.problems checks the items themselves)."""
    if not isinstance(data, dict) or not isinstance(data.get("tweaks"), list):
        return ["no \"tweaks\" list"]
    out, seen = [], set()
    for i, t in enumerate(data["tweaks"]):
        key = t.get("key") if isinstance(t, dict) else None
        if not isinstance(key, str) or not key:
            out.append(f"item {i} has no key")
        elif key in seen:
            out.append(f"key {key} is used twice")
        else:
            seen.add(key)
    return out


def ensure_required(folder, data) -> list[str]:
    """Put back any REQUIRED switch that Tweak.x uses but tweaks.json lost, at the front in template order;
    returns their keys. A mod whose Tweak.x doesn't use one (a hand-written menu) is left as it is."""
    used = set(_used_names(folder))
    keys = {t.get("key") for t in data["tweaks"] if isinstance(t, dict)}
    missing = [copy.deepcopy(t) for t in REQUIRED if t["key"] not in keys and t["key"] in used]
    data["tweaks"][0:0] = missing
    return [t["key"] for t in missing]


def save(folder, data, history=None) -> list[str]:
    """Write tweaks.json; returns the REQUIRED keys it had to put back. history: the folder that keeps the
    previous versions (none kept when None)."""
    found = problems(data)
    if found:
        raise TweaksFileError(f"{path(folder)}: not saved: " + "; ".join(found))
    restored = ensure_required(folder, data)
    target = path(folder)
    if history is not None and target.is_file():
        _keep(target, Path(history))
    tmp = target.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, target)
    return restored


def _keep(target: Path, history: Path) -> None:
    history.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    # zero-padded and one past the highest of this second (an older one may have been pruned), so names sort by age
    same = [int(p.stem.rsplit("-", 1)[1]) for p in history.glob(f"tweaks-{stamp}-*.json")]
    copy_to = history / f"tweaks-{stamp}-{max(same, default=0) + 1:03d}.json"
    copy_to.write_bytes(target.read_bytes())
    for old in sorted(history.glob("tweaks-*.json"))[:-HISTORY_KEEP]:
        old.unlink()


_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_STRINGS = re.compile(r'"(?:\\.|[^"\\\n])*"')
_AM_NAME = re.compile(r"\bam_([A-Za-z_][A-Za-z0-9_]*)\b")


def _used_names(folder) -> list[str]:
    """The <key> of every am_<key> Tweak.x uses outside comments and strings, minus names it defines itself
    (static BOOL am_x = ...; void am_x(void) {...})."""
    source_path = Path(folder) / "Tweak.x"
    if not source_path.is_file():
        return []
    source = _STRINGS.sub('""', _COMMENTS.sub(" ", source_path.read_text(encoding="utf-8")))
    defined = set(re.findall(r"\b(?:BOOL|bool|int|float|double|void|NSString\s*\*|id|static\s+\w+)\s+am_(\w+)", source))
    return sorted(set(_AM_NAME.findall(source)) - defined)


def undeclared(folder, data=None) -> list[str]:
    """Switch variables Tweak.x uses (am_<key>) that tweaks.json doesn't declare."""
    if not path(folder).is_file():
        return []
    data = load(folder) if data is None else data
    keys = {t.get("key") for t in data.get("tweaks", []) if isinstance(t, dict)}
    return [f"am_{name}" for name in _used_names(folder) if name not in keys]
