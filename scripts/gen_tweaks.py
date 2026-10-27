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
    "title": "My Game+",                       the mod's name (the menu shows the game's own name); also the device-log tag
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
title ("Game value 2"). The game keeps running while the menu is open; "pause_menu": true at the top of
tweaks.json pauses it instead (Unity Time.timeScale).

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

from hide import ams_bytes
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


def hidden(text):
    """A game name (class, field, method) as an encoded C expression: never plain text in the .dylib
    (runtime/am_hide.h)."""
    return ams_bytes(text)


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
            if t.get("live_only") and "command" in pin:  # a game command object (AMLivePins.h): pushed switches only
                found = [] if isinstance(pin.get("command"), str) and isinstance(pin.get("run"), str) else [
                    '"command" needs the command class and "run" (its method)']
                out += [f"{where} pin {j}: {p}" for p in found]
                continue
            found = pin_problems(pin, kind)
            out += [f"{where} pin {j}: {p}" for p in found]
    if "watch" in tweaks:
        out += watch_problems(tweaks["watch"], tweaks.get("watch_every", 10))
    return out


PIN_WHEN = ("always", "lower", "higher")
# "HotFix:GameManager.m_t": a class outside Assembly-CSharp carries its assembly (AMIl2cppClass reads it)
OBJECT_SPEC = re.compile(r"^(?:[A-Za-z_][\w.-]*:)?[A-Za-z_][\w.]*(\[\])?$")
# a fixed-point number (Photon's FP: a long holding value * 65536): the pin's "scale"
FP_SCALE = 65536
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


def multiple_objects(pin):
    return pin.get("object", "").endswith("[]") or any(
        isinstance(s, dict) and s.get("each") for s in pin.get("path", [])
    )


MULTIPLY_SLOTS = 512  # objects one list multiply tracks at once; more stay as the game has them


def per_object_multiply(pin):
    """A multiply over every object of a list (Enemy[]): each object keeps its own base value."""
    return pin.get("op") == "multiply" and bool(pin.get("field")) and multiple_objects(pin)


def path_problems(pin):
    if "path" not in pin:
        return ['"root_class" needs a path'] if "root_class" in pin else []
    path = pin["path"]
    cls_name = re.compile(r"^(?:[\w.-]+:)?[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")
    if not isinstance(pin.get("root_class"), str) or not cls_name.fullmatch(pin["root_class"]):
        return ['"path" needs a qualified "root_class"']
    if not isinstance(path, list) or len(path) > 8:
        return ['"path" must be a list of at most 8 steps']
    out = []
    if pin.get("via"):
        out.append('use "path" or "via", not both')
    for step in path:
        if (not isinstance(step, dict) or set(step) - {"field", "getter", "class", "each"}
                or not isinstance(step.get("class"), str) or not cls_name.fullmatch(step["class"])):
            out.append('each path step needs a result "class" and only field/getter/each keys')
            continue
        members = [k for k in ("field", "getter") if k in step]
        if len(members) > 1 or any(not isinstance(step[k], str) or not NAME.fullmatch(step[k]) for k in members):
            out.append('a path step takes one field or getter name (or just class to filter)')
        if "each" in step and (step["each"] not in ("array", "list", "values") or not members):
            out.append('"each" must be array, list or values on a field/getter step')
    if not out and pin.get("class") and pin["class"] != (path[-1]["class"] if path else pin["root_class"]):
        out.append('"class" must match the final path class; add a class-only step to filter another type')
    return out


ATTR_GET, ATTR_SET = "GetAttributeValue", "SetAttributeValue"


def attr_problems(pin, kind):
    """A named-attribute pin: a stat kept in a by-name table, read and written through the game's own
    GetAttributeValue(string) / SetAttributeValue(string, FP) (runtime/AMAttrPins.h).
    {"object"..., "path"..., "attr": "Attack", "op": "multiply", "value": 2}   attack x2 (the game's value tracked)
    {"attr": "FireCD", "op": "set", "value": 0, "when": "higher"}             a plain number, FP-scaled for the game
    "get"/"set" name other methods when a game calls them differently."""
    out = []
    if any(pin.get(k) for k in ("field", "setter", "call")):
        out.append('"attr" goes alone: no "field", "setter" or "call" beside it')
    if not isinstance(pin.get("attr"), str) or not re.fullmatch(r"[A-Za-z_][\w.]{0,63}", pin["attr"]):
        out.append('"attr" is the stat\'s name in the game\'s table (Attack, CritRate, MoveSpeed...)')
    for k in ("get", "set"):
        if k in pin and not (isinstance(pin[k], str) and NAME.match(pin[k])):
            out.append(f'"{k}" must be a method name')
    op = pin.get("op", "set")
    if op not in ("set", "multiply"):
        out.append('"op" must be set or multiply')
    value = pin.get("value")
    if value == "slider":
        if kind != "slider":
            out.append('"value": "slider" needs a slider tweak')
    elif isinstance(value, bool) or not isinstance(value, (int, float)):
        out.append('"value" must be a number (the factor for multiply, the stat for set) or "slider"')
    elif op == "multiply" and value <= 0:
        out.append('a multiply factor must be above 0')
    if pin.get("when", "always") not in PIN_WHEN:
        out.append(f'"when" must be one of {", ".join(PIN_WHEN)}')
    return out


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
    out += path_problems(pin)
    if out:
        return out
    obj = pin.get("object")
    if not isinstance(obj, str) or not OBJECT_SPEC.match(obj):
        out.append(
            '"object" must be "Class.staticField", "Class" (first live object) or "Class[]" (every live object)'
        )
    for key in ("via", "class"):
        if key in pin and not (
            isinstance(pin[key], str) and NAME.match(pin[key].rpartition(":")[2].replace(".", ""))
        ):
            out.append(f'"{key}" must be a name')
    if "attr" in pin:
        return out + attr_problems(pin, kind)
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
    if "scale" in pin and (not isinstance(pin["scale"], int) or pin["scale"] <= 0 or ctype_ != "int64_t"):
        out.append('"scale" is a whole number above 0 on an int64_t (a fixed-point FP field: 65536)')
    op = pin.get("op", "set")
    if op not in ("set", "multiply"):
        out.append('"op" must be set or multiply')
    if op == "multiply" and (target != "field" or ctype_ not in NUMERIC):
        out.append('"op": "multiply" works on a numeric field')
    return out


def pin_objects(pin):
    """C lines that bind `obj` to each target object and open a block per object: (lines, closers, class)."""
    if "path" in pin:
        steps = "@[" + ", ".join("@{" + ", ".join(
            f'@"{k}": @({hidden(v)})' for k, v in step.items()) + "}" for step in pin["path"]) + "]"
        cls = pin["path"][-1]["class"] if pin["path"] else pin["root_class"]
        return [
            f"        NSArray *targets = AMIl2cppPathObjects(AMIl2cppRootObjects({hidden(pin['object'])}), {hidden(pin['root_class'])}, {steps});",
            "        for (NSValue *target in targets) { void *obj = target.pointerValue; if (obj) {",
        ], ["            }", "        }"], cls
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
            f"        int32_t n = 0; void *arr = AMIl2cppFindObjects({hidden(cls)}, &n);"
        )
        lines.append(
            "        for (int32_t i = 0; i < n; i++) { void *obj = AMIl2cppArrayAt(arr, i); if (!obj) continue;"
        )
    elif static:
        lines.append(
            f"        void *obj = AMIl2cppStaticObject({hidden(cls)}, {hidden(static)}); if (obj) {{"
        )
    else:
        lines.append(
            f"        void *obj = AMIl2cppFindObject({hidden(cls)}); if (obj) {{"
        )
    target_cls = cls
    closers = ["        }"]
    if pin.get("via"):
        lines.append(
            f"            void *via = NULL; obj = AMIl2cppCall(obj, {hidden(cls)}, {hidden(pin['via'])}, 0, NULL, &via) ? via : NULL; if (obj) {{"
        )
        target_cls = pin.get("class") or cls
        closers = ["            }", "        }"]
    elif pin.get("class"):
        target_cls = pin["class"]
    return lines, closers, target_cls


def pin_value(pin, var, ctype_, cls, state):
    """(setup lines, C expression) for the value a pin writes."""
    value = pin["value"]
    # a fixed-point field holds value * scale; a multiply factor is not scaled (base * factor stays raw)
    scale = pin.get("scale") if pin.get("op") != "multiply" else None
    if value == "slider":
        return [], f"({ctype_})({var} * {scale})" if scale else f"({ctype_}){var}"
    if value == "peak":
        return [], f"{state}_peak"
    if isinstance(value, dict) and "getter" in value:
        return [
            f"            void *vb = NULL; {ctype_} *vp = AMIl2cppCall(obj, {hidden(cls)}, {hidden(value['getter'])}, 0, NULL, &vb) ? AMIl2cppUnbox(vb) : NULL;",
            f"            if (!vp) goto {state}_next;",
        ], "(*vp)"
    if isinstance(value, dict):
        return [
            f"            {ctype_} *vp = AMIl2cppField(obj, {hidden(cls)}, {hidden(value['field'])});",
            f"            if (!vp) goto {state}_next;",
        ], "(*vp)"
    if scale and not isinstance(value, bool):
        return [], c_value(round(value * scale), ctype_)
    return [], c_value(value, ctype_)


FIGHT_TICKS = 6  # 3 s of the game undoing a pin before its dot turns red


def restorable(pin):
    """Whether turning the switch off puts the game's own value back: a single object whose field or setter the
    pin forces ("when": "always", or a multiply). Floors ("lower"/"higher"), peaks, Class[] and calls keep what
    the game has, and so do refills ({"getter"}/{"field"} values) and pins or tweaks with "restore": false (a
    resource like radiation, where the old value coming back would hurt). The value goes back only while the field still holds what the pin wrote (the game moving it
    since wins)."""
    if "attr" in pin:
        return pin.get("restore") is not False  # AMAttrPins.h keeps each object's own value
    if (multiple_objects(pin) and not per_object_multiply(pin)) or pin.get("call") or pin.get("value") == "peak":
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
    if "attr" in pin:
        return out  # its state lives in AMAttrPins.h, per object
    if per_object_multiply(pin):  # each object's own base and what the pin last wrote, by object
        n = MULTIPLY_SLOTS
        out.append(f"static void *{state}_objs[{n}]; static {ctype_} {state}_bases[{n}], {state}_sets[{n}];"
                   f" static bool {state}_seen[{n}]; static int32_t {state}_used;")
        return out
    if pin.get("op") == "multiply":
        out.append(f"static {ctype_} {state}_base, {state}_set;")
        if restorable(pin):
            out.append(f"static void *{state}_obj;")
    elif restorable(pin):
        out.append(f"static void *{state}_obj; static {ctype_} {state}_orig, {state}_wrote; static bool {state}_has, {state}_wrote_ok;")
        if pin.get("field"):
            out.append(f"static int {state}_fights;  // ticks in a row the game had changed what the pin wrote")
    return out


def render_restore(pin, key, index):
    """C lines (in am_pin_<key>, when the switch goes off) that put the game's value back on the same object."""
    state = f"s_{key}_{index}"
    if "attr" in pin:
        objects, closers, cls = pin_objects(pin)
        names = f"{hidden(pin.get('get', ATTR_GET))}, {hidden(pin.get('set', ATTR_SET))}, {hidden(pin['attr'])}"
        return ([f"        {{  // restore pin {index}: attribute {pin['attr']}"] + ["    " + line for line in objects]
                + [f"                if (AMAttrRestore({c_string(f'{key}/{index}')}, obj, {hidden(cls)}, {names})) restored = true;"]
                + ["    " + c for c in closers]
                + [f"            AMAttrForget({c_string(f'{key}/{index}')});", "        }"])
    ctype_ = pin["ctype"]
    objects, closers, cls = pin_objects(pin)
    if per_object_multiply(pin):  # every object gets its own base back, while it still holds what the pin wrote
        lines = [f"        {{  // restore pin {index}: each object's own base"] + ["    " + line for line in objects]
        lines.append(f"                int32_t slot = 0; while (slot < {state}_used && {state}_objs[slot] != obj) slot++;")
        lines.append(f"                {ctype_} *p = slot < {state}_used ? AMIl2cppField(obj, {hidden(cls)}, {hidden(pin['field'])}) : NULL;")
        lines.append(f"                if (p && *p == {state}_sets[slot]) {{ *p = {state}_bases[slot]; restored = true; }}")
        lines += ["    " + c for c in closers]
        lines.append(f"            {state}_used = 0;")
        lines.append("        }")
        return lines
    lines = [f"        {{  // restore pin {index}"] + ["    " + line for line in objects]
    lines.append(f"                if (obj == {state}_obj) {{")
    if pin.get("op") == "multiply":
        lines.append(f"                    {ctype_} *p = AMIl2cppField(obj, {hidden(cls)}, {hidden(pin['field'])});")
        lines.append(f"                    if (p && *p == {state}_set) {{ *p = {state}_base; restored = true; }}")
    elif pin.get("field"):
        lines.append(f"                    {ctype_} *p = AMIl2cppField(obj, {hidden(cls)}, {hidden(pin['field'])});")
        lines.append(f"                    if (p && {state}_has && {state}_wrote_ok && *p == {state}_wrote) {{ *p = {state}_orig; restored = true; }}")
    else:
        getter = "get_" + pin["setter"][4:]
        lines.append(f"                    void *b = NULL; {ctype_} *cur = {state}_has && {state}_wrote_ok && AMIl2cppCall(obj, {hidden(cls)}, {hidden(getter)}, 0, NULL, &b) ? AMIl2cppUnbox(b) : NULL;")
        lines.append(f"                    if (cur && *cur == {state}_wrote) {{ {ctype_} v = {state}_orig; void *argv[1] = {{&v}}; if (AMIl2cppCall(obj, {hidden(cls)}, {hidden(pin['setter'])}, 1, argv, NULL)) restored = true; }}")
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
    if "attr" in pin or multiple_objects(pin) or pin.get("call") or not (pin.get("field") or str(pin.get("setter", "")).startswith("set_")):
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
        lines.append(f"    if ({on}) {{ if ({state}_has) AMNoteValue({c_string(key)}, (double){state}_orig{' / ' + str(pin['scale']) if pin.get('scale') else ''}); return; }}")
    else:
        lines.append(f"    if ({on}) return;")
    lines.append("    {")
    lines += objects
    if pin.get("field"):
        lines.append(f"            {ctype_} *v = AMIl2cppField(obj, {hidden(cls)}, {hidden(pin['field'])});")
    else:
        lines.append(f"            void *b = NULL; {ctype_} *v = AMIl2cppCall(obj, {hidden(cls)}, {hidden('get_' + pin['setter'][4:])}, 0, NULL, &b) ? AMIl2cppUnbox(b) : NULL;")
    lines.append(f"            if (v) AMNoteValue({c_string(key)}, (double)*v{' / ' + str(pin['scale']) if pin.get('scale') else ''});")
    lines += closers
    lines.append("    }")
    lines.append("}")
    lines.append("")
    return lines


def render_pin(pin, var, kind, key, index, waiting=None):
    """C lines (inside am_pin_<key>) that apply one pin to its object(s) once."""
    state = f"s_{key}_{index}"
    what = pin.get("field") or pin.get("setter") or pin.get("call") or pin.get("attr")
    lines = [f"    {{  // pin {index}: {pin['object']} {what}"]
    objects, closers, cls = pin_objects(pin)
    if "attr" in pin:
        missing = c_string(waiting or "Waiting for the game: load a save first")
        unreachable = hidden(f"Needs an update for this game version ({cls} {pin['attr']}): run am check")
        value = f"(double){var}" if pin.get("value") == "slider" else repr(float(pin["value"]))
        op = 1 if pin.get("op") == "multiply" else 0
        when = {"always": 0, "lower": 1, "higher": 2}[pin.get("when", "always")]
        names = f"{hidden(pin.get('get', ATTR_GET))}, {hidden(pin.get('set', ATTR_SET))}, {hidden(pin['attr'])}"
        lines.append(f"            tried = true; if (!why) why = {missing};")
        lines += objects
        lines.append(f"            found = true; why = {unreachable};")
        lines.append(f"            int r = AMAttrApply({c_string(f'{key}/{index}')}, obj, {hidden(cls)}, {names}, {op}, {value}, {when});")
        lines.append("            if (r >= 0) reached = true; if (r > 0) applied = true;")
        lines += closers
        lines.append("    }")
        return lines
    # the menu's status dot (AMMenuStatus): tried this tick, found the object, reached (read or wrote) it
    # what the player reads under a red dot: the tweak's own hint ("Start a fight to see it work"), or a default
    missing = c_string(waiting or "Waiting for the game: load a save first")
    unreachable = hidden(f"Needs an update for this game version ({cls}.{what}): run am check")
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
            f"            if (AMIl2cppCall(obj, {hidden(cls)}, {hidden(pin['call'])}, {len(pairs)}, argv, NULL)) applied = reached = true;"
        )
        lines += closers
        if every:
            lines.append("        }")
        lines.append("    }")
        return lines
    lines.append(status_start)
    per_object = per_object_multiply(pin)
    if per_object:  # which tracked objects are still in the list this tick
        lines.append(f"        for (int32_t slot = 0; slot < {state}_used; slot++) {state}_seen[slot] = false;")
        lines.append("        bool any_seen = false;")
    lines += objects
    lines.append(status_found)
    ctype_ = pin["ctype"]
    when = pin.get("when", "always")
    cmp_ = {"always": None, "lower": "<", "higher": ">"}[when]
    setup, value = pin_value(pin, var, ctype_, cls, state)
    if pin["value"] == "peak":
        lines.insert(1, f"    static {ctype_} {state}_peak;")
    lines += setup
    limit = c_value(round(pin["threshold"] * pin.get("scale", 1)), ctype_) if "threshold" in pin and pin.get("scale") \
        else c_value(pin["threshold"], ctype_) if "threshold" in pin else value
    if pin.get("field"):
        lines.append(
            f"            {ctype_} *p = AMIl2cppField(obj, {hidden(cls)}, {hidden(pin['field'])});"
        )
        lines.append(f"            if (!p) goto {state}_next;")
        lines.append("            reached = true;")
        if per_object:
            # this object's slot; a new one starts from the game's value (a full table leaves it as the game has it)
            lines.append(f"            int32_t slot = 0; while (slot < {state}_used && {state}_objs[slot] != obj) slot++;")
            lines.append(f"            if (slot == {state}_used) {{ if (slot == {MULTIPLY_SLOTS}) goto {state}_next;"
                         f" {state}_objs[slot] = obj; {state}_bases[slot] = {state}_sets[slot] = *p; {state}_used++; }}")
            lines.append(f"            {state}_seen[slot] = any_seen = true;")
            lines.append(f"            if (*p != {state}_sets[slot]) {state}_bases[slot] = *p;  // the game recalculated it: a new base")
            lines.append(
                f"            {ctype_} want = ({ctype_})({state}_bases[slot] * {value}); if (want > 0 && *p != want) {{ *p = want; applied = true; }} {state}_sets[slot] = *p;"
            )
        elif pin.get("op") == "multiply":
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
                    f"            if ({state}_obj != obj) {{ {state}_obj = obj; {state}_orig = *p; {state}_has = true; {state}_wrote_ok = false; {state}_fights = 0; }}"
                )
                # the game fights back: what the pin wrote half a second ago is gone again, tick after tick
                fights = hidden(f"The game resets {pin['field']} faster than this switch writes it: it may not hold")
                lines.append(
                    f"            if ({state}_wrote_ok && *p != {state}_wrote) {{ if (++{state}_fights >= {FIGHT_TICKS}) fought = {fights}; }} else {state}_fights = 0;"
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
                    f"            if ({state}_obj != obj) {{ {state}_obj = obj; void *ob = NULL; {ctype_} *o = AMIl2cppCall(obj, {hidden(cls)}, {hidden(getter)}, 0, NULL, &ob) ? AMIl2cppUnbox(ob) : NULL;"
                    f" {state}_has = o != NULL; if (o) {state}_orig = *o; {state}_wrote_ok = false; }}"
                )
            lines.append(f"            {ctype_} v = {value}; void *argv[1] = {{&v}};")
            lines.append(
                f"            if (AMIl2cppCall(obj, {hidden(cls)}, {hidden(setter)}, 1, argv, NULL)) {{ applied = reached = true;"
                + (f" {state}_wrote = v; {state}_wrote_ok = true;" if restorable(pin) else "")
                + " }"
            )
        else:
            if not getter:
                raise TweaksError(
                    f"pin {key}/{index}: when/peak on a setter needs a set_ name (its get_ is read)"
                )
            lines.append(
                f"            void *boxed = NULL; {ctype_} *cur = AMIl2cppCall(obj, {hidden(cls)}, {hidden(getter)}, 0, NULL, &boxed) ? AMIl2cppUnbox(boxed) : NULL;"
            )
            lines.append(f"            if (!cur) goto {state}_next;")
            lines.append("            reached = true;")
            if pin["value"] == "peak":
                lines.append(
                    f"            if (*cur > {state}_peak) {state}_peak = *cur;"
                )
            cond = "true" if cmp_ is None else f"*cur {cmp_} {limit}"
            # read back right after the setter: one that ignores or clamps the value turns the dot red
            took = hidden(f"{cls}.{setter} didn't take the value (the game clamps or recomputes it)")
            lines.append(
                f"            if ({cond}) {{ {ctype_} v = {value}; void *argv[1] = {{&v}}; if (AMIl2cppCall(obj, {hidden(cls)}, {hidden(setter)}, 1, argv, NULL)) {{ applied = true;"
                f" void *rb = NULL; {ctype_} *back = AMIl2cppCall(obj, {hidden(cls)}, {hidden(getter)}, 0, NULL, &rb) ? AMIl2cppUnbox(rb) : NULL;"
                f" if (back && *back != v) {{ reached = false; why = {took}; }} }} }}"
            )
    if any(f"goto {state}_next" in line for line in lines):
        lines.append(f"            {state}_next:;")
    lines += closers
    if pin.get("field") and per_object_multiply(pin):
        # objects gone from the list free their slots; a tick that found none (a scene loading) keeps them all, so a
        # unit that comes back doesn't take what the pin wrote as its new base
        lines.append(f"        if (any_seen) {{ int32_t kept = 0; for (int32_t slot = 0; slot < {state}_used; slot++) if ({state}_seen[slot]) {{"
                     f" {state}_objs[kept] = {state}_objs[slot]; {state}_bases[kept] = {state}_bases[slot]; {state}_sets[kept] = {state}_sets[slot]; kept++; }}"
                     f" {state}_used = kept; }}")
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
                f"            void *b = NULL; {ctype_} *v = AMIl2cppCall(obj, {hidden(cls)}, {hidden(w['getter'])}, 0, NULL, &b) ? AMIl2cppUnbox(b) : NULL;"
            )
        else:
            body.append(
                f"            {ctype_} *v = AMIl2cppField(obj, {hidden(cls)}, {hidden(w['field'])});"
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
    # "live_only": true runs from the pushed live_tweaks.json alone (am push), never compiled: the live loader adds
    # its menu item. Lets a mod carry thousands of trial switches without building them in.
    tweaks["tweaks"] = [t for t in tweaks["tweaks"] if not t.get("live_only")]
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
        '#import "runtime/AMAttrPins.h"',
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
        m.append("    const char *fought = NULL;  // a pin the game keeps undoing turns the dot red whatever the others do")
        for i, pin in enumerate(t["pins"]):
            m += render_pin(pin, var, kind, t["key"], i, t.get("waiting"))
        m.append(
            f'    if (applied && !logged) {{ logged = true; AMIl2cppLog(@"pin {t["key"]} applied"); }}'
        )
        m.append("    if (fought) { reached = false; why = fought; }")
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
    if tweaks.get("pause_menu", False):  # the game keeps running unless a mod asks (owner's choice, 2026-10-22)
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
