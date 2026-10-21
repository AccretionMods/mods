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
        words = p.replace("ref ", "").replace("out ", "").replace("in ", "").split()
        if len(words) < 2:
            raise TweaksError(f"can't read parameter {i} of {hook_name(entry)}: {p!r}")
        cs_type, pname = words[-2], words[-1].split("=")[0]
        pname = re.sub(r"\W", "_", pname) or f"a{i}"
        if pname in ("self", "mi", "method", "default", "class", "new", "this"):
            pname = f"a{i}"
        args.append((ctype(cs_type, enums), pname, cs_type))
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
            if hk["method"] in used and action != "custom":
                out.append(
                    f"{hwhere}: {hk['method']} is already hooked by tweak {used[hk['method']]}; one method, one tweak"
                )
            used.setdefault(hk["method"], key)
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
    return out


def render(tweaks, hooks, mod_name):
    """(header, implementation) for a valid tweaks.json."""
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
                body = []
                orig_call = f"orig_{name}({', '.join(call_args)})"
                if action == "skip":
                    body.append(f"    if ({on}) return;")
                    body.append(f"    {orig_call};")
                elif action == "return":
                    body.append(f"    if ({on}) return {c_value(hk['value'], ret)};")
                    body.append(f"    return {orig_call};")
                elif action == "multiply":
                    factor = var if kind == "slider" else c_value(hk["value"], "float")
                    body.append(f"    {ret} value = {orig_call};")
                    body.append(f"    return {on} ? ({ret})(value * {factor}) : value;")
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
        "",
        "#ifdef __cplusplus",
        "}",
        "#endif",
    ]
    m += [
        "void AMTweaksRegister(void) {",
        "    AMMenu *menu = AMMenu.shared;",
        "    menu.title = AMTweaksTitle;",
        '    NSString *jit = AMIl2cppHasJIT() ? @"" : @" (JIT)";',
    ]
    for t in tweaks["tweaks"]:
        kind = t.get("kind", "switch")
        var = f"am_{t['key']}"
        key = c_string(t["key"])
        needs_jit = [
            bool(by_name[hk["method"]].get("needs_jit")) for hk in t.get("hooks", [])
        ]
        label = f"@{c_string(t['title'])}"
        if needs_jit and all(needs_jit) and t.get("jit_label", True):
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
