"""Turn a mod's tweaks.json into the Objective-C that registers its switches, sliders and hooks.

Usage: python3 scripts/gen_tweaks.py <mod folder>        writes AMTweaks.gen.h and AMTweaks.gen.m there
       python3 scripts/gen_tweaks.py <mod folder> --check  only reports problems

scripts/build.sh runs it for a mod that has a tweaks.json, and scripts/dev.py add/gen call it. tweaks.json
says what each menu item does; hooks.json (same folder) says which game methods exist and what they look
like (scripts/dev.py add writes both from the game's dump.cs). The generated file holds, for every tweak,
its current value (am_<key>), and for every hook the (orig_, hook_) pair and its AMIl2cppRegister line, plus
AMTweaksRegister(), which Tweak.x calls in %ctor before AMIl2cppStart().

tweaks.json:
  {
    "title": "My Game+",                       menu title; also the device-log tag
    "tweaks": [
      {"key": "noDamage", "title": "Player takes no damage", "kind": "switch",
       "hooks": [{"method": "PlayerHealth.TakeDamage", "action": "skip"}]},
      {"key": "weaponDamage", "title": "Weapon damage ×", "kind": "slider", "min": 1, "max": 20, "default": 1,
       "hooks": [{"method": "ItemStack.GetScaledDamage", "action": "multiply"}]},
      {"key": "skillPoints", "title": "Unlimited skill points", "kind": "switch",
       "hooks": [{"method": "SkillsManager.get_SkillPoints", "action": "return", "value": 99}]},
      {"key": "vault", "title": "Vault fix", "kind": "switch",
       "hooks": [{"method": "MainMenuScene.OnAwake", "action": "custom"}]}
    ]
  }
A tweak with no hooks is a plain value for code in Tweak.x (a ticker that writes fields, for example).

Menu sections: "section" ("Combat", "Items & crafting"...) groups tweaks under a header, in the order each
section first appears; related tweaks go next to each other in the file. Tweak.x can add to a section with
[AMMenu.shared addSection:@"..."] before its own items.

Menu status (AMMenuStatus): each pinned tweak reports every tick whether it reached its object. "waiting" is
what the player reads under the red dot when the object is not there ("Start a fight to see it work"; the
default is "Waiting for the game: load a save first"). "jit_label": false means the mod has its own no-JIT
code for a hook-only tweak: no "(JIT)" title, and Tweak.x reports its status itself.

Turning a switch off puts the game's own values back where a pin forced them (see restorable(): fields and
setters written "always" or multiplied, on one object); "restore": false on a tweak or pin keeps what the game
has instead (radiation, for example). A slider shows the game's own value of its first pin's target under its
title ("Game value 2"). The game pauses while the menu is open (Unity Time.timeScale); "pause_menu": false at
the top of tweaks.json turns that off.

Actions, while the tweak is on (a slider counts as on when its value is not its default):
  skip      a void method is not called
  return    the method returns "value" (bool, int or float) instead of running
  multiply  the method's numeric result is multiplied by the slider's value (or by "value" for a switch)
  arg       argument "index" is replaced by "value" before the original runs
  custom    the hook is registered, but Tweak.x defines hook_<ident>() itself (the header declares
            orig_<ident> and the prototype); for logic the four actions above can't express
"method" is "Class.method" or "Namespace.Class.method", the name hooks.json and the runtime use. The entry in
hooks.json must carry "signature" (the C# declaration, as dev.py add records it) so the C types are known;
"ctypes" there overrides the argument types when the C# ones can't be mapped (enums are int32_t).
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from il2cpp_dump import SIGNATURE, split_params

ACTIONS = ("skip", "return", "multiply", "arg", "custom")
KINDS = ("switch", "slider")
KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

# C# value types that pass to a C function as is. Anything else is a managed reference (void *): classes,
# strings, arrays, interfaces, delegates. Enums are int32_t; structs by value can't be typed here.
CTYPES = {
    "void": "void",
    "bool": "bool",
    "int": "int32_t",
    "uint": "uint32_t",
    "long": "int64_t",
    "ulong": "uint64_t",
    "short": "int16_t",
    "ushort": "uint16_t",
    "byte": "uint8_t",
    "sbyte": "int8_t",
    "char": "uint16_t",
    "float": "float",
    "double": "double",
    "IntPtr": "void *",
    "UIntPtr": "void *",
}
NUMERIC = {
    "int32_t",
    "uint32_t",
    "int64_t",
    "uint64_t",
    "int16_t",
    "uint16_t",
    "uint8_t",
    "int8_t",
    "float",
    "double",
}


class TweaksError(Exception):
    pass


def ctype(cs_type, enums=()):
    """The C type for a C# type name as dump.cs writes it ("int", "Items", "List<Items>", "int[]")."""
    base = cs_type.split("<", 1)[0].rstrip("[]").rsplit(".", 1)[-1]
    if cs_type.endswith("[]") or "<" in cs_type:
        return "void *"
    if base in enums:
        return "int32_t"
    return CTYPES.get(base, "void *")


def hook_name(h):
    return ".".join(p for p in (h.get("namespace", ""), h["class"], h["method"]) if p)


def ident(name):
    """A C identifier for "Namespace.Class.method": letters, digits and underscores."""
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def parse_signature(entry):
    """(is_static, return C type, [(C type, name), ...]) from a hooks.json entry's signature and ctypes."""
    sig = entry.get("signature")
    if not sig:
        raise TweaksError(
            f'hooks.json entry {hook_name(entry)} has no "signature": add it with scripts/dev.py add, or write '
            'the C# declaration (like "public void TakeDamage(int amount, bool crit)")'
        )
    m = SIGNATURE.match(sig.strip())
    if not m:
        raise TweaksError(f"can't read the signature of {hook_name(entry)}: {sig}")
    return_type, _name, params = m.groups()
    is_static = bool(re.match(r"^(?:\w+\s+)*static\s", sig))
    enums = set(entry.get("enums", []))
    args = []
    for i, p in enumerate(split_params(params)):
        p = p.split("=", 1)[0].strip()  # "int level = -1": the default is the caller's business
        by_ref = bool(re.match(r"^(ref|out)\s", p))
        words = re.sub(r"^(?:(?:ref|out|in|params|this)\s+)+", "", p).split()
        if len(words) < 2:
            raise TweaksError(f"can't read parameter {i} of {hook_name(entry)}: {p!r}")
        cs_type, pname = words[-2], words[-1]
        pname = re.sub(r"\W", "_", pname) or f"a{i}"
        if pname in ("self", "mi", "method", "default", "class", "new", "this", "value", "v", "p", "obj"):
            pname = f"a{i}"
        c = ctype(cs_type, enums)
        if cs_type.endswith("?"):
            raise TweaksError(f"{hook_name(entry)}: parameter {pname} is a Nullable ({cs_type}), passed by value; hook it as custom")
        args.append((c + " *" if by_ref else c, pname, cs_type))
    overrides = entry.get("ctypes")
    if overrides:
        if len(overrides) != len(args):
            raise TweaksError(
                f"{hook_name(entry)}: ctypes lists {len(overrides)} types for {len(args)} parameters"
            )
        args = [(c, n, t) for c, (_, n, t) in zip(overrides, args)]
    ret = entry.get("return_ctype") or ctype(return_type, enums)
    return is_static, ret, args, return_type


def c_value(value, ctype_):
    if ctype_ == "bool":
        return "true" if value else "false"
    if ctype_ in ("float", "double"):
        return repr(float(value)) + ("f" if ctype_ == "float" else "")
    if ctype_ in NUMERIC:
        return str(int(value))
    raise TweaksError(f"a value of C type {ctype_} can't be written as a constant")


def c_string(text):
    return (
        '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    )


def load(folder):
    tweaks_path = os.path.join(folder, "tweaks.json")
    hooks_path = os.path.join(folder, "hooks.json")
    with open(tweaks_path, encoding="utf-8") as f:
        tweaks = json.load(f)
    hooks = {"hooks": []}
    if os.path.isfile(hooks_path):
        with open(hooks_path, encoding="utf-8") as f:
            hooks = json.load(f)
    return tweaks, hooks


def problems(tweaks, hooks):
    """Everything wrong with a tweaks.json against its hooks.json, as messages."""
    out = []
    if not isinstance(tweaks, dict) or not isinstance(tweaks.get("tweaks"), list):
        return ['tweaks.json needs a "tweaks" list']
    if not isinstance(tweaks.get("title"), str) or not tweaks["title"]:
        out.append('tweaks.json needs a "title"')
    by_name = {}
    for h in hooks.get("hooks", []):
        full = hook_name(h)
        by_name[full] = h
        by_name.setdefault(f"{h['class']}.{h['method']}", h)
    keys, used = set(), {}
    for i, t in enumerate(tweaks["tweaks"]):
        where = f"tweak {i}"
        if not isinstance(t, dict):
            out.append(f"{where} must be an object")
            continue
        key = t.get("key")
        if not isinstance(key, str) or not KEY.match(key):
            out.append(
                f"{where}: key must be a C identifier (letters, digits, underscores)"
            )
            continue
        where = f"tweak {key}"
        if key in keys:
            out.append(f"{where}: duplicate key")
        keys.add(key)
        if not isinstance(t.get("title"), str) or not t["title"]:
            out.append(f"{where} needs a title")
        kind = t.get("kind", "switch")
        if kind not in KINDS:
            out.append(f"{where}: kind must be one of {', '.join(KINDS)}")
        if kind == "slider":
            for k in ("min", "max"):
                if not isinstance(t.get(k), (int, float)):
                    out.append(f"{where}: a slider needs {k}")
        for j, hk in enumerate(t.get("hooks", [])):
            hwhere = f"{where} hook {j}"
            if not isinstance(hk, dict) or not isinstance(hk.get("method"), str):
                out.append(f'{hwhere} needs "method"')
                continue
            action = hk.get("action", "custom")
            if action not in ACTIONS:
                out.append(f"{hwhere}: action must be one of {', '.join(ACTIONS)}")
                continue
            entry = by_name.get(hk["method"])
            if entry is None:
                out.append(
                    f"{hwhere}: {hk['method']} is not in hooks.json (scripts/dev.py add <mod> {hk['method']})"
                )
                continue
            full = hook_name(entry)
            if full in used and action != "custom":
                out.append(
                    f"{hwhere}: {hk['method']} is already hooked by tweak {used[full]}; one method, one tweak"
                )
            used.setdefault(full, key)
            if action == "custom":
                continue
            try:
                _static, ret, args, cs_ret = parse_signature(entry)
            except TweaksError as e:
                out.append(f"{hwhere}: {e}")
                continue
            if action == "skip" and ret != "void":
                out.append(
                    f"{hwhere}: skip needs a void method ({hk['method']} returns {cs_ret})"
                )
            if action == "return":
                if ret == "void" or ret == "void *":
                    out.append(
                        f"{hwhere}: return needs a bool or numeric method ({hk['method']} returns {cs_ret})"
                    )
                elif "value" not in hk:
                    out.append(f'{hwhere}: return needs "value"')
            if action == "multiply":
                if ret not in NUMERIC:
                    out.append(
                        f"{hwhere}: multiply needs a numeric method ({hk['method']} returns {cs_ret})"
                    )
                if kind == "switch" and "value" not in hk:
                    out.append(
                        f'{hwhere}: multiply on a switch needs "value" (a slider uses its own value)'
                    )
            if action == "arg":
                idx = hk.get("index")
                if not isinstance(idx, int) or idx < 0 or idx >= len(args):
                    out.append(f"{hwhere}: arg needs an index 0..{len(args) - 1}")
                elif args[idx][0] not in NUMERIC and args[idx][0] != "bool":
                    out.append(
                        f"{hwhere}: argument {idx} ({args[idx][2]}) is not a number or bool"
                    )
                if "value" not in hk:
                    out.append(f'{hwhere}: arg needs "value"')
        for j, pin in enumerate(t.get("pins", [])):
            out += [f"{where} pin {j}: {p}" for p in pin_problems(pin, kind)]
    if "watch" in tweaks:
        out += watch_problems(tweaks["watch"], tweaks.get("watch_every", 10))
    return out


PIN_WHEN = ("always", "lower", "higher")
OBJECT_SPEC = re.compile(r"^[A-Za-z_][\w.]*(\[\])?$")
NAME = re.compile(r"^[A-Za-z_<][\w<>]*$")


def value_problems(value, ctype_, kind, what="value"):
    """A pin value: a number or bool, "slider", "peak" (highest value seen), {"getter": "get_X"} or
    {"field": "Y"} read from the same object each tick."""
    if value == "slider":
        return (
            [] if kind == "slider" else [f'"{what}": "slider" only on a slider tweak']
        )
    if value == "peak":
        return (
            []
            if ctype_ in NUMERIC
            else [f'"{what}": "peak" needs a numeric field or setter']
        )
    if isinstance(value, dict):
        keys = set(value)
        if keys not in ({"getter"}, {"field"}):
            return [f'"{what}" as an object needs exactly one of "getter" or "field"']
        name = next(iter(value.values()))
        return (
            []
            if isinstance(name, str) and NAME.match(name)
            else [f'"{what}": bad name {name!r}']
        )
    if isinstance(value, (bool, int, float)):
        return []
    return [
        f'needs a "{what}" (number, bool, "slider", "peak", {{"getter": ...}} or {{"field": ...}})'
    ]


def pin_problems(pin, kind):
    """A pin runs every tick on the live object(s) of a class: the no-JIT path.
    {"object": "StatHandler.instance" | "Enemy" | "Enemy[]",   static field, first live object, every live object
     "via": "get_playerStats", "class": "PlayerStats",         optional: a 0-arg getter giving the real target
     "field": "Hunger" | "setter": "set_Hunger" | "call": "GiveRandomItem",
     "ctype": "int32_t", "value": 100 | true | "slider" | "peak" | {"getter": "get_HealthMax"} | {"field": "Max"},
     "when": "always" | "lower" | "higher", "threshold": 50,   write only when the current value is lower/higher
     "op": "set" | "multiply",                                  multiply: field = base * value, base tracked
     "args": [-1], "arg_ctypes": ["int32_t"], "every": 10}     call: arguments and seconds between calls"""
    out = []
    if not isinstance(pin, dict):
        return ["must be an object"]
    obj = pin.get("object")
    if not isinstance(obj, str) or not OBJECT_SPEC.match(obj):
        out.append(
            '"object" must be "Class.staticField", "Class" (first live object) or "Class[]" (every live object)'
        )
    for key in ("via", "class"):
        if key in pin and not (
            isinstance(pin[key], str) and NAME.match(pin[key].replace(".", ""))
        ):
            out.append(f'"{key}" must be a name')
    targets = [k for k in ("field", "setter", "call") if pin.get(k)]
    if len(targets) != 1:
        out.append('needs exactly one of "field", "setter" or "call"')
        return out
    target = targets[0]
    if target == "call":
        args = pin.get("args", [])
        ctypes_ = pin.get("arg_ctypes", [])
        if (
            not isinstance(args, list)
            or not isinstance(ctypes_, list)
            or len(args) != len(ctypes_)
        ):
            out.append('"call" needs "args" and "arg_ctypes" lists of the same length')
        else:
            for a, c in zip(args, ctypes_):
                if c not in NUMERIC and c != "bool":
                    out.append(f'"arg_ctypes": {c} is not a C number type or bool')
                elif not isinstance(a, (int, float, bool)):
                    out.append(f'"args": {a!r} is not a number or bool')
        every = pin.get("every", 0)
        if not isinstance(every, (int, float)) or every < 0:
            out.append('"every" must be seconds (0: every tick)')
        return out
    ctype_ = pin.get("ctype")
    if ctype_ not in NUMERIC and ctype_ != "bool":
        out.append(
            '"ctype" must be a C number type or bool (dev.py pin reads it from the dump)'
        )
        return out
    out += value_problems(pin.get("value"), ctype_, kind)
    when = pin.get("when", "always")
    if when not in PIN_WHEN:
        out.append(f'"when" must be one of {", ".join(PIN_WHEN)}')
    if when != "always" and ctype_ == "bool":
        out.append('"when" lower/higher needs a numeric field or setter')
    if "threshold" in pin and not isinstance(pin["threshold"], (int, float)):
        out.append('"threshold" must be a number')
    op = pin.get("op", "set")
    if op not in ("set", "multiply"):
        out.append('"op" must be set or multiply')
    if op == "multiply" and (target != "field" or ctype_ not in NUMERIC):
        out.append('"op": "multiply" works on a numeric field')
    if op == "multiply" and isinstance(obj, str) and obj.endswith("[]"):
        out.append('"op": "multiply" tracks one base value, so it needs a single object (not Class[])')
    return out


def pin_objects(pin):
    """C lines that bind `obj` to each target object and open a block per object: (lines, closers, class)."""
    obj = pin["object"]
    every = obj.endswith("[]")
    obj = obj[:-2] if every else obj
    if pin.get("static_field"):
        cls, static = obj, pin["static_field"]
    else:
        cls, _, static = obj.rpartition(".") if ("." in obj and not every) else ("", "", "")
        if not cls:
            cls, static = obj, ""
    lines = []
    if every:
        lines.append(
            f"        int32_t n = 0; void *arr = AMIl2cppFindObjects({c_string(cls)}, &n);"
        )
        lines.append(
            "        for (int32_t i = 0; i < n; i++) { void *obj = AMIl2cppArrayAt(arr, i); if (!obj) continue;"
        )
    elif static:
        lines.append(
            f"        void *obj = AMIl2cppStaticObject({c_string(cls)}, {c_string(static)}); if (obj) {{"
        )
    else:
        lines.append(
            f"        void *obj = AMIl2cppFindObject({c_string(cls)}); if (obj) {{"
        )
    target_cls = cls
    closers = ["        }"]
    if pin.get("via"):
        lines.append(
            f"            void *via = NULL; obj = AMIl2cppCall(obj, {c_string(cls)}, {c_string(pin['via'])}, 0, NULL, &via) ? via : NULL; if (obj) {{"
        )
        target_cls = pin.get("class") or cls
        closers = ["            }", "        }"]
    elif pin.get("class"):
        target_cls = pin["class"]
    return lines, closers, target_cls


def pin_value(pin, var, ctype_, cls, state):
    """(setup lines, C expression) for the value a pin writes."""
    value = pin["value"]
    if value == "slider":
        return [], f"({ctype_}){var}"
    if value == "peak":
        return [], f"{state}_peak"
    if isinstance(value, dict) and "getter" in value:
        return [
            f"            void *vb = NULL; {ctype_} *vp = AMIl2cppCall(obj, {c_string(cls)}, {c_string(value['getter'])}, 0, NULL, &vb) ? AMIl2cppUnbox(vb) : NULL;",
            f"            if (!vp) goto {state}_next;",
        ], "(*vp)"
    if isinstance(value, dict):
        return [
            f"            {ctype_} *vp = AMIl2cppField(obj, {c_string(cls)}, {c_string(value['field'])});",
            f"            if (!vp) goto {state}_next;",
        ], "(*vp)"
    return [], c_value(value, ctype_)


def restorable(pin):
    """Whether turning the switch off puts the game's own value back: a single object whose field or setter the
    pin forces ("when": "always", or a multiply). Floors ("lower"/"higher"), peaks, Class[] and calls keep what
    the game has, and so do refills ({"getter"}/{"field"} values) and pins or tweaks with "restore": false (a
    resource like radiation, where the old value coming back would hurt). The value goes back only while the field still holds what the pin wrote (the game moving it
    since wins)."""
    if pin["object"].endswith("[]") or pin.get("call") or pin.get("value") == "peak":
        return False
    if pin.get("restore") is False or isinstance(pin.get("value"), dict):  # opted out, or a refill (health = max)
        return False
    if pin.get("setter") and not pin["setter"].startswith("set_"):
        return False
    return pin.get("when", "always") == "always" or pin.get("op") == "multiply"


def pin_globals(pin, key, index):
    """File-scope state of one pin: the object it acts on, the game's value before it, what it wrote last."""
    state = f"s_{key}_{index}"
    ctype_ = pin.get("ctype")
    out = []
    if pin.get("op") == "multiply":
        out.append(f"static {ctype_} {state}_base, {state}_set;")
        if restorable(pin):
            out.append(f"static void *{state}_obj;")
    elif restorable(pin):
        out.append(f"static void *{state}_obj; static {ctype_} {state}_orig, {state}_wrote; static bool {state}_has, {state}_wrote_ok;")
    return out


def render_restore(pin, key, index):
    """C lines (in am_pin_<key>, when the switch goes off) that put the game's value back on the same object."""
    state = f"s_{key}_{index}"
    ctype_ = pin["ctype"]
    objects, closers, cls = pin_objects(pin)
    lines = [f"        {{  // restore pin {index}"] + ["    " + line for line in objects]
    lines.append(f"                if (obj == {state}_obj) {{")
    if pin.get("op") == "multiply":
        lines.append(f"                    {ctype_} *p = AMIl2cppField(obj, {c_string(cls)}, {c_string(pin['field'])});")
        lines.append(f"                    if (p && *p == {state}_set) {{ *p = {state}_base; restored = true; }}")
    elif pin.get("field"):
        lines.append(f"                    {ctype_} *p = AMIl2cppField(obj, {c_string(cls)}, {c_string(pin['field'])});")
        lines.append(f"                    if (p && {state}_has && {state}_wrote_ok && *p == {state}_wrote) {{ *p = {state}_orig; restored = true; }}")
    else:
        getter = "get_" + pin["setter"][4:]
        lines.append(f"                    void *b = NULL; {ctype_} *cur = {state}_has && {state}_wrote_ok && AMIl2cppCall(obj, {c_string(cls)}, {c_string(getter)}, 0, NULL, &b) ? AMIl2cppUnbox(b) : NULL;")
        lines.append(f"                    if (cur && *cur == {state}_wrote) {{ {ctype_} v = {state}_orig; void *argv[1] = {{&v}}; if (AMIl2cppCall(obj, {c_string(cls)}, {c_string(pin['setter'])}, 1, argv, NULL)) restored = true; }}")
    lines.append("                }")
    lines += ["    " + c for c in closers]
    if pin.get("op") == "multiply":
        lines.append(f"            {state}_set = 0; {state}_obj = NULL;")
    else:
        lines.append(f"            {state}_obj = NULL; {state}_has = {state}_wrote_ok = false;")
    lines.append("        }")
    return lines


def note_pin(t):
    """The pin whose target shows the game's own value under a slider ("Game value 2"), or None."""
    if t.get("kind") != "slider" or not t.get("pins"):
        return None
    pin = t["pins"][0]
    if pin["object"].endswith("[]") or pin.get("call") or not (pin.get("field") or str(pin.get("setter", "")).startswith("set_")):
        return None
    return pin


def render_note(t):
    """static void am_note_<key>(void): the game's own value of a slider's target, for the menu (AMMenuNote).
    While the slider is on, the value the game had before it (or the base of a multiply)."""
    key = t["key"]
    pin = note_pin(t)
    state = f"s_{key}_0"
    ctype_ = pin["ctype"]
    on = f"(am_{key} != {c_value(t.get('default', t['min']), 'float')})"
    objects, closers, cls = pin_objects(pin)
    lines = [f"static void am_note_{key}(void) {{", f"    if (AMLivePinsHas({c_string(key)})) return;"]
    if pin.get("op") == "multiply":
        lines.append(f"    if ({on}) {{ if ({state}_set) AMNoteValue({c_string(key)}, (double){state}_base); return; }}")
    elif restorable(pin):
        lines.append(f"    if ({on}) {{ if ({state}_has) AMNoteValue({c_string(key)}, (double){state}_orig); return; }}")
    else:
        lines.append(f"    if ({on}) return;")
    lines.append("    {")
    lines += objects
    if pin.get("field"):
        lines.append(f"            {ctype_} *v = AMIl2cppField(obj, {c_string(cls)}, {c_string(pin['field'])});")
    else:
        lines.append(f"            void *b = NULL; {ctype_} *v = AMIl2cppCall(obj, {c_string(cls)}, {c_string('get_' + pin['setter'][4:])}, 0, NULL, &b) ? AMIl2cppUnbox(b) : NULL;")
    lines.append(f"            if (v) AMNoteValue({c_string(key)}, (double)*v);")
    lines += closers
    lines.append("    }")
    lines.append("}")
    lines.append("")
    return lines


def render_pin(pin, var, kind, key, index, waiting=None):
    """C lines (inside am_pin_<key>) that apply one pin to its object(s) once."""
    state = f"s_{key}_{index}"
    what = pin.get("field") or pin.get("setter") or pin.get("call")
    lines = [f"    {{  // pin {index}: {pin['object']} {what}"]
    objects, closers, cls = pin_objects(pin)
    # the menu's status dot (AMMenuStatus): tried this tick, found the object, reached (read or wrote) it
    # what the player reads under a red dot: the tweak's own hint ("Start a fight to see it work"), or a default
    missing = c_string(waiting or "Waiting for the game: load a save first")
    unreachable = c_string(f"Needs an update for this game version ({cls}.{what}): run am check")
    status_start = f"            tried = true; if (!why) why = {missing};"
    status_found = f"            found = true; why = {unreachable};"
    if pin.get("call"):
        every = pin.get("every", 0)
        if every:
            lines.append(
                f"        static CFAbsoluteTime {state}_at; CFAbsoluteTime now = CFAbsoluteTimeGetCurrent();"
            )
            lines.append(f"        if (now - {state}_at >= {float(every)!r}) {{ {state}_at = now;")
        lines.append(status_start)
        lines += objects
        lines.append(status_found)
        pairs = list(zip(pin.get("args", []), pin.get("arg_ctypes", [])))
        decl = "; ".join(
            f"{c} a{i} = {c_value(a, c)}" for i, (a, c) in enumerate(pairs)
        )
        argv = ", ".join(f"&a{i}" for i in range(len(pairs))) or "NULL"
        lines.append(
            f"            {decl + '; ' if decl else ''}void *argv[{max(len(pairs), 1)}] = {{{argv}}};"
        )
        lines.append(
            f"            if (AMIl2cppCall(obj, {c_string(cls)}, {c_string(pin['call'])}, {len(pairs)}, argv, NULL)) applied = reached = true;"
        )
        lines += closers
        if every:
            lines.append("        }")
        lines.append("    }")
        return lines
    lines.append(status_start)
    lines += objects
    lines.append(status_found)
    ctype_ = pin["ctype"]
    when = pin.get("when", "always")
    cmp_ = {"always": None, "lower": "<", "higher": ">"}[when]
    setup, value = pin_value(pin, var, ctype_, cls, state)
    if pin["value"] == "peak":
        lines.insert(1, f"    static {ctype_} {state}_peak;")
    lines += setup
    limit = c_value(pin["threshold"], ctype_) if "threshold" in pin else value
    if pin.get("field"):
        lines.append(
            f"            {ctype_} *p = AMIl2cppField(obj, {c_string(cls)}, {c_string(pin['field'])});"
        )
        lines.append(f"            if (!p) goto {state}_next;")
        lines.append("            reached = true;")
        if pin.get("op") == "multiply":
            lines.append(
                f"            if (*p != {state}_set) {state}_base = *p; {state}_obj = obj;"
            )
            lines.append(
                f"            {ctype_} want = ({ctype_})({state}_base * {value}); if (want > 0 && *p != want) {{ *p = want; applied = true; }} {state}_set = want;"
            )
        else:
            if pin["value"] == "peak":
                lines.append(f"            if (*p > {state}_peak) {state}_peak = *p;")
            cond = "true" if cmp_ is None else f"*p {cmp_} {limit}"
            if restorable(pin):
                lines.append(
                    f"            if ({state}_obj != obj) {{ {state}_obj = obj; {state}_orig = *p; {state}_has = true; {state}_wrote_ok = false; }}"
                )
                lines.append(f"            if ({cond}) {{ *p = {value}; {state}_wrote = *p; {state}_wrote_ok = true; applied = true; }}")
            else:
                lines.append(f"            if ({cond}) {{ *p = {value}; applied = true; }}")
    else:
        setter = pin["setter"]
        getter = "get_" + setter[4:] if setter.startswith("set_") else None
        if cmp_ is None and pin["value"] != "peak":
            if restorable(pin):
                lines.append(
                    f"            if ({state}_obj != obj) {{ {state}_obj = obj; void *ob = NULL; {ctype_} *o = AMIl2cppCall(obj, {c_string(cls)}, {c_string(getter)}, 0, NULL, &ob) ? AMIl2cppUnbox(ob) : NULL;"
                    f" {state}_has = o != NULL; if (o) {state}_orig = *o; {state}_wrote_ok = false; }}"
                )
            lines.append(f"            {ctype_} v = {value}; void *argv[1] = {{&v}};")
            lines.append(
                f"            if (AMIl2cppCall(obj, {c_string(cls)}, {c_string(setter)}, 1, argv, NULL)) {{ applied = reached = true;"
                + (f" {state}_wrote = v; {state}_wrote_ok = true;" if restorable(pin) else "")
                + " }"
            )
        else:
            if not getter:
                raise TweaksError(
                    f"pin {key}/{index}: when/peak on a setter needs a set_ name (its get_ is read)"
                )
            lines.append(
                f"            void *boxed = NULL; {ctype_} *cur = AMIl2cppCall(obj, {c_string(cls)}, {c_string(getter)}, 0, NULL, &boxed) ? AMIl2cppUnbox(boxed) : NULL;"
            )
            lines.append(f"            if (!cur) goto {state}_next;")
            lines.append("            reached = true;")
            if pin["value"] == "peak":
                lines.append(
                    f"            if (*cur > {state}_peak) {state}_peak = *cur;"
                )
            cond = "true" if cmp_ is None else f"*cur {cmp_} {limit}"
            # read back right after the setter: one that ignores or clamps the value turns the dot red
            took = c_string(f"{cls}.{setter} didn't take the value (the game clamps or recomputes it)")
            lines.append(
                f"            if ({cond}) {{ {ctype_} v = {value}; void *argv[1] = {{&v}}; if (AMIl2cppCall(obj, {c_string(cls)}, {c_string(setter)}, 1, argv, NULL)) {{ applied = true;"
                f" void *rb = NULL; {ctype_} *back = AMIl2cppCall(obj, {c_string(cls)}, {c_string(getter)}, 0, NULL, &rb) ? AMIl2cppUnbox(rb) : NULL;"
                f" if (back && *back != v) {{ reached = false; why = {took}; }} }} }}"
            )
    if any(f"goto {state}_next" in line for line in lines):
        lines.append(f"            {state}_next:;")
    lines += closers
    lines.append("    }")
    return lines


def watch_problems(watch, every):
    out = []
    if not isinstance(every, (int, float)) or every <= 0:
        out.append('"watch_every" must be seconds')
    if not isinstance(watch, list):
        return out + ['"watch" must be a list']
    for i, w in enumerate(watch):
        if (
            not isinstance(w, dict)
            or not isinstance(w.get("object"), str)
            or not OBJECT_SPEC.match(w["object"])
            or w["object"].endswith("[]")
        ):
            out.append(f'watch {i}: "object" must be "Class.staticField" or "Class"')
            continue
        if bool(w.get("getter")) == bool(w.get("field")):
            out.append(f'watch {i}: needs exactly one of "getter" or "field"')
        if w.get("ctype") not in NUMERIC and w.get("ctype") != "bool":
            out.append(f'watch {i}: "ctype" must be a C number type or bool')
    return out


def render_watch(watch, every):
    """static void am_watch(void): one log line with every watched value, every N seconds."""
    fmt, args, body = [], [], []
    for i, w in enumerate(watch):
        objects, closers, cls = pin_objects(
            {"object": w["object"], "via": w.get("via"), "class": w.get("class")}
        )
        name = (w.get("name") or (w.get("getter") or w.get("field")).replace("get_", "")).replace("%", "%%")
        ctype_ = w["ctype"]
        if ctype_ in ("int32_t", "int16_t", "int8_t", "uint8_t", "uint16_t", "bool"):
            spec, cast = "%d", "(int)"
        elif ctype_ == "int64_t":
            spec, cast = "%lld", "(long long)"
        elif ctype_ in ("uint64_t", "uint32_t"):
            spec, cast = "%llu", "(unsigned long long)"
        else:
            spec, cast = "%.1f", "(double)"
        body.append(f'    NSString *w{i} = @"?";')
        body.append("    {")
        body += objects
        if w.get("getter"):
            body.append(
                f"            void *b = NULL; {ctype_} *v = AMIl2cppCall(obj, {c_string(cls)}, {c_string(w['getter'])}, 0, NULL, &b) ? AMIl2cppUnbox(b) : NULL;"
            )
        else:
            body.append(
                f"            {ctype_} *v = AMIl2cppField(obj, {c_string(cls)}, {c_string(w['field'])});"
            )
        body.append(
            f'            if (v) w{i} = [NSString stringWithFormat:@"{spec}", {cast}*v];'
        )
        body += closers
        body.append("    }")
        fmt.append(f"{name} %@")
        args.append(f"w{i}")
    lines = [
        "static void am_watch(void) {",
        "    static CFAbsoluteTime at; CFAbsoluteTime now = CFAbsoluteTimeGetCurrent();",
        f"    if (now - at < {float(every)!r}) return; at = now;",
    ]
    lines += body
    lines.append(
        f"    AMIl2cppLog(@{c_string('watch: ' + ' '.join(fmt))}, {', '.join(args)});"
    )
    lines.append("}")
    lines.append("")
    return lines


def render(tweaks, hooks, mod_name):
    """(header, implementation) for a valid tweaks.json."""
    tweaks = json.loads(json.dumps(tweaks))  # a copy: "restore": false moves from the tweak to its pins
    for t in tweaks["tweaks"]:
        if t.get("restore") is False:
            for pin in t.get("pins", []):
                pin.setdefault("restore", False)
    by_name = {}
    for h in hooks.get("hooks", []):
        by_name[hook_name(h)] = h
        by_name.setdefault(f"{h['class']}.{h['method']}", h)
    title = tweaks["title"]
    tag = tweaks.get("log_tag", title)
    h, m = [], []
    h += [
        "// Generated by scripts/gen_tweaks.py from tweaks.json. Do not edit; edit tweaks.json and run",
        f"// scripts/dev.py gen {mod_name} (scripts/build.sh does it too).",
        "#import <Foundation/Foundation.h>",
        "#include <stdbool.h>",
        "#include <stdint.h>",
        "",
        "#ifdef __cplusplus",
        'extern "C" {',
        "#endif",
        "",
        "// Current value of each tweak (saved in the app's NSUserDefaults by AMMenu, loaded at start).",
    ]
    m += [
        "// Generated by scripts/gen_tweaks.py from tweaks.json. Do not edit.",
        "#import <UIKit/UIKit.h>",
        "#import <os/log.h>",
        '#import "AMTweaks.gen.h"',
        '#import "menu/AMMenu.h"',
        '#import "runtime/AMIl2cpp.h"',
        '#import "runtime/AMIl2cppData.h"',
        '#import "runtime/AMLivePins.h"',
        "",
        f"NSString *const AMTweaksTitle = @{c_string(title)};",
        f"NSString *const AMTweaksLogTag = @{c_string(tag)};",
        "",
    ]
    for t in tweaks["tweaks"]:
        kind = t.get("kind", "switch")
        var = f"am_{t['key']}"
        if kind == "slider":
            h.append(f"extern float {var};  // {t['title']}")
            m.append(f"float {var} = {c_value(t.get('default', t['min']), 'float')};")
        else:
            h.append(f"extern BOOL {var};  // {t['title']}")
            m.append(f"BOOL {var} = {'YES' if t.get('default') else 'NO'};")
    h.append("")
    m.append("")
    h.append(
        "// Originals of the hooked methods (filled by the runtime when a hook installs; NULL until then)."
    )
    registrations = []
    customs = []
    for t in tweaks["tweaks"]:
        kind = t.get("kind", "switch")
        var = f"am_{t['key']}"
        for hk in t.get("hooks", []):
            entry = by_name[hk["method"]]
            full = hook_name(entry)
            name = ident(full)
            action = hk.get("action", "custom")
            is_static, ret, args, _cs_ret = parse_signature(entry)
            params = (
                ([] if is_static else ["void *self"])
                + [f"{c} {n}" for c, n, _ in args]
                + ["const void *mi"]
            )
            call_args = (
                ([] if is_static else ["self"]) + [n for _, n, _ in args] + ["mi"]
            )
            fn_ptr = f"{ret} (*orig_{name})({', '.join(params)})"
            proto = f"{ret} hook_{name}({', '.join(params)})"
            comment = f"// {full}: {entry.get('signature', '')}".rstrip()
            if action == "custom":
                h += [comment, f"extern {fn_ptr};", f"{proto};  // defined in Tweak.x"]
                m.append(f"{fn_ptr};")
                customs.append(full)
            else:
                h += [comment, f"extern {fn_ptr};"]
                m.append(f"{fn_ptr};")
                on = (
                    var
                    if kind == "switch"
                    else f"({var} != {c_value(t.get('default', t['min']), 'float')})"
                )
                body = [f"    if ({on}) AMMenuStatus({c_string(t['key'])}, true, NULL);"]
                orig_call = f"orig_{name}({', '.join(call_args)})"
                if action == "skip":
                    body.append(f"    if ({on}) return;")
                    body.append(f"    {orig_call};")
                elif action == "return":
                    body.append(f"    if ({on}) return {c_value(hk['value'], ret)};")
                    body.append(f"    return {orig_call};")
                elif action == "multiply":
                    factor = var if kind == "slider" else c_value(hk["value"], "float")
                    body.append(f"    {ret} am_result = {orig_call};")
                    body.append(f"    return {on} ? ({ret})(am_result * {factor}) : am_result;")
                elif action == "arg":
                    c, n, _ = args[hk["index"]]
                    body.append(f"    if ({on}) {n} = {c_value(hk['value'], c)};")
                    body.append(f"    {'return ' if ret != 'void' else ''}{orig_call};")
                m += [f"static {proto} {{"] + body + ["}"]
            m.append("")
            registrations.append((full, name))
    h += [
        "",
        "extern NSString *const AMTweaksTitle;",
        "extern NSString *const AMTweaksLogTag;",
        "",
        "// Adds every tweak to AMMenu.shared, loads the saved values and registers the hooks with the runtime.",
        "// Call it in %ctor, then AMIl2cppStart() and [AMMenu.shared install].",
        "void AMTweaksRegister(void);",
        "// Applies every tweak's pins (field writes and setter calls on live objects: the no-JIT path) once.",
        "// Call it from a ticker on the main thread, like AMIl2cppEvery(0.5, ^{ AMTweaksTick(); }).",
        "void AMTweaksTick(void);",
        "",
        "#ifdef __cplusplus",
        "}",
        "#endif",
    ]
    pinned = []
    for t in tweaks["tweaks"]:
        if not t.get("pins"):
            continue
        kind = t.get("kind", "switch")
        var = f"am_{t['key']}"
        on = (
            var
            if kind == "switch"
            else f"({var} != {c_value(t.get('default', t['min']), 'float')})"
        )
        for i, pin in enumerate(t["pins"]):
            m += pin_globals(pin, t["key"], i)
        m.append(f"static void am_pin_{t['key']}(void) {{")
        restores = [i for i, pin in enumerate(t["pins"]) if restorable(pin)]
        if restores:
            # turned off: the game's own values back (restorable), on the objects the pins wrote
            m.append("    static bool was_on;")
            m.append(f"    if (!{on}) {{")
            m.append("        if (!was_on) return;")
            m.append("        was_on = false; bool restored = false;")
            for i in restores:
                m += render_restore(t["pins"][i], t["key"], i)
            m.append(f'        if (restored) AMIl2cppLog(@"pin {t["key"]} off: the game\'s values are back");')
            m.append("        return;")
            m.append("    }")
        else:
            m.append(f"    if (!{on}) return;")
        m.append(f"    if (AMLivePinsHas({c_string(t['key'])})) return;  // the pushed live_tweaks.json has this switch")
        if restores:
            m.append("    was_on = true;")
        m.append(
            "    static bool logged; bool applied = false, tried = false, found = false, reached = false; const char *why = NULL;"
        )
        for i, pin in enumerate(t["pins"]):
            m += render_pin(pin, var, kind, t["key"], i, t.get("waiting"))
        m.append(
            f'    if (applied && !logged) {{ logged = true; AMIl2cppLog(@"pin {t["key"]} applied"); }}'
        )
        m.append(f"    if (tried) AMMenuStatus({c_string(t['key'])}, reached, why);")
        m.append("    (void)found;")
        m.append("}")
        m.append("")
        pinned.append(t["key"])
    noted = [t for t in tweaks["tweaks"] if note_pin(t)]
    for t in noted:
        m += render_note(t)
    if tweaks.get("watch"):
        m += render_watch(tweaks["watch"], tweaks.get("watch_every", 10))
    m.append("void AMTweaksTick(void) {")
    for key in pinned:
        m.append(f"    am_pin_{key}();")
    for t in tweaks["tweaks"]:
        if (t.get("hooks") and not t.get("pins") and t.get("kind", "switch") == "switch"
                and t.get("jit_label", True)):
            m.append(
                f"    if (am_{t['key']} && !AMLivePinsHas({c_string(t['key'])})) AMMenuStatus({c_string(t['key'])}, AMIl2cppHasJIT(), "
                f"\"Needs JIT, and this phone has none\");"
            )
    if noted:
        # every 3 s: the game's own value under each slider ("Game value 2")
        m.append("    static CFAbsoluteTime notes_at; CFAbsoluteTime now = CFAbsoluteTimeGetCurrent();")
        m.append("    if (now - notes_at >= 3) {")
        m.append("        notes_at = now;")
        for t in noted:
            m.append(f"        am_note_{t['key']}();")
        m.append("    }")
    m.append("    AMLivePinsTick();  // switches pushed as live_tweaks.json (dev.py push): no rebuild")
    if tweaks.get("watch"):
        m.append("    am_watch();")
    m.append("}")
    m.append("")
    m += [
        "void AMTweaksRegister(void) {",
        "    AMMenu *menu = AMMenu.shared;",
        "    menu.title = AMTweaksTitle;",
    ]
    if tweaks.get("pause_menu", True):
        m.append("    menu.onOpenChange = ^(BOOL open) { AMIl2cppPauseGame(open); };  // the game stops while the menu is open")
    m += [
        '    NSString *jit = AMIl2cppHasJIT() ? @"" : @" (JIT)";',
        "    (void)jit;",
    ]
    section = None
    for t in tweaks["tweaks"]:
        kind = t.get("kind", "switch")
        var = f"am_{t['key']}"
        key = c_string(t["key"])
        # menu groups: the menu keeps a section's items together in the order they come, wherever they are
        if t.get("section", "") != section:
            section = t.get("section", "")
            m.append(f"    [menu addSection:@{c_string(section)}];")
        needs_jit = [
            bool(by_name[hk["method"]].get("needs_jit")) for hk in t.get("hooks", [])
        ]
        label = f"@{c_string(t['title'])}"
        # a tweak with pins works without JIT whatever its hooks need
        if (
            needs_jit
            and all(needs_jit)
            and not t.get("pins")
            and t.get("jit_label", True)
        ):
            label = f"[{label} stringByAppendingString:jit]"
        if kind == "slider":
            default = c_value(t.get("default", t["min"]), "float")
            m.append(
                f"    [menu addSlider:@{key} title:{label} min:{c_value(t['min'], 'float')} max:{c_value(t['max'], 'float')} "
                f"defaultValue:{default} onChange:^(float v) {{ {var} = v; }}];"
            )
            m.append(f"    {var} = [AMMenu floatForKey:@{key}];")
        else:
            m.append(
                f"    [menu addSwitch:@{key} title:{label} defaultValue:{'YES' if t.get('default') else 'NO'} "
                f"onChange:^(BOOL on) {{ {var} = on; }}];"
            )
            m.append(f"    {var} = [AMMenu boolForKey:@{key}];")
    m.append("")
    for full, name in registrations:
        m.append(
            f"    AMIl2cppRegister({c_string(full)}, (void *)hook_{name}, (void **)&orig_{name});"
        )
    m.append("    AMLivePinsLoad();  // a pushed live_tweaks.json: its new switches join the menu")
    m.append("}")
    m.append("")
    if customs:
        h.insert(
            2,
            "// Custom hooks Tweak.x must define: "
            + ", ".join(f"hook_{ident(c)}" for c in customs),
        )
    return "\n".join(h) + "\n", "\n".join(m) + "\n"


def generate(folder, check_only=False):
    """Writes AMTweaks.gen.h/.m into the mod folder; returns the problem list (empty when written)."""
    tweaks, hooks = load(folder)
    found = problems(tweaks, hooks)
    if found or check_only:
        return found
    header, impl = render(tweaks, hooks, os.path.basename(os.path.normpath(folder)))
    for name, text in (("AMTweaks.gen.h", header), ("AMTweaks.gen.m", impl)):
        path = os.path.join(folder, name)
        old = None
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                old = f.read()
        if old != text:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
    return []


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    check_only = "--check" in argv
    argv = [a for a in argv if a != "--check"]
    if len(argv) != 1:
        sys.exit(__doc__)
    folder = argv[0]
    if not os.path.isdir(folder) and os.path.isdir(os.path.join(ROOT, "mods", folder)):
        folder = os.path.join(ROOT, "mods", folder)
    if not os.path.isfile(os.path.join(folder, "tweaks.json")):
        sys.exit(f"{folder} has no tweaks.json")
    found = generate(folder, check_only)
    for p in found:
        print(f"tweaks.json: {p}")
    if found:
        return 1
    tweaks, _ = load(folder)
    hooks = sum(len(t.get("hooks", [])) for t in tweaks["tweaks"])
    print(
        f"{len(tweaks['tweaks'])} tweaks, {hooks} hooks -> {os.path.join(folder, 'AMTweaks.gen.m')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
