"""Mod dev loop for personal Unity (IL2CPP) mods: dump the game, find methods, add a tweak, build, sign, log.

Runs on Windows (uv run --no-project --python 3.12 scripts/dev.py ...  or  py -3 scripts/dev.py ...) and in
WSL/Linux (python3 scripts/dev.py ...). Theos builds run in WSL either way; dumping needs .NET (Windows has
it); signing and device logs need the phone on USB and run on Windows (plumesign, pymobiledevice3).

  dev.py new <name> <base.ipa> [--title T] [--category games]   personal mod folder from a decrypted .ipa
  dev.py dump <mod> [--base file.ipa] [--force]                  Il2CppDumper -> work/dump-<mod>-<ver>/dump.cs
  dev.py find <mod> <regex> [--limit N]                          methods whose Class.method matches
  dev.py find <mod> --class <Class>                              every method of one class
  dev.py fields <mod> <Class>                                    the class's fields with offsets
  dev.py add <mod> <Class.method> [--as key] [--title T]         hook a method: hooks.json + tweaks.json + code
         [--action skip|return|multiply|arg|custom] [--value V] [--index I] [--args N]
         [--slider MIN MAX DEFAULT] [--to key]
  dev.py tweak <mod> <key> --title T [--slider MIN MAX DEFAULT]  a switch or slider with no hook (for Tweak.x code)
  dev.py remove <mod> <key or Class.method>                      drop a tweak (and hooks nothing else uses)
  dev.py gen <mod>                                               regenerate AMTweaks.gen.m from tweaks.json
  dev.py check <mod>                                             hooks.json against the current dump
  dev.py build <mod> [--ipa] [--bump] [--base file.ipa]          .dylib (and .ipa) in C:\\AccretionMods\\builds
  dev.py sign <file.ipa> [--install] [-o out.ipa]                Apple ID signing with plumesign (Windows)
  dev.py install <signed.ipa>                                    pymobiledevice3 apps install (USB)
  dev.py log <mod> [--seconds 60]                                device log lines of the mod and the runtime
  dev.py status <mod>                                            base, dump, tweaks, hooks, last build
  dev.py update <mod> [new.ipa]                                  newer game version: re-dump, hooks ok/changed/missing,
                                                                 mod.json base + tested version refreshed
  dev.py pick <mod> <regex>                                      numbered find; type a number, answer 2-3 questions
  dev.py suggest <mod>                                           this game's methods that match what worked before
  dev.py mark <mod> <key> --works|--fails                        the phone result; feeds the suggestions
  dev.py learn <mod>                                             import an existing mod's hooks.json into the patterns

scripts/am.ps1 wraps all of this as `am <command> ...` from PowerShell. With no arguments: this help.

Folders (AM_ROOT, default C:\\AccretionMods): intake/ and ~/Downloads are searched for base .ipa files by
bundle ID; work/ holds dumps; builds/ the .dylib and .ipa files; dev/<mod>.json remembers the base and dump.
Base .ipa files must already be decrypted (personal copies); nothing here decrypts or uploads anything.
"""

import argparse
import hashlib
import json
import os
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import dev_learn
import gen_tweaks
from il2cpp_dump import methods

WINDOWS = os.name == "nt"
WSL = (
    not WINDOWS and "microsoft" in Path("/proc/version").read_text().lower()
    if Path("/proc/version").exists()
    else False
)


class DevError(Exception):
    pass


# ---------------------------------------------------------------- paths


def am_root():
    env = os.environ.get("AM_ROOT")
    if env:
        return Path(env)
    return Path("C:/AccretionMods") if WINDOWS else Path("/mnt/c/AccretionMods")


def wsl_path(path):
    """A Windows path as WSL sees it (C:\\x -> /mnt/c/x); other paths unchanged."""
    p = str(path).replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/(.*)$", p)
    return f"/mnt/{m.group(1).lower()}/{m.group(2)}" if m else p


def win_path(path):
    """A WSL /mnt/<drive>/ path as Windows sees it; other paths unchanged."""
    m = re.match(r"^/mnt/([a-z])/(.*)$", str(path))
    return f"{m.group(1).upper()}:/{m.group(2)}" if m else str(path)


def mod_dir(mod):
    d = ROOT / "mods" / mod
    if not (d / "mod.json").is_file():
        raise DevError(f"no mods/{mod}/mod.json")
    return d


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def state_path(mod):
    return am_root() / "dev" / f"{mod}.json"


def load_state(mod):
    p = state_path(mod)
    return read_json(p) if p.is_file() else {}


def save_state(mod, state):
    state_path(mod).parent.mkdir(parents=True, exist_ok=True)
    write_json(state_path(mod), state)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- .ipa


def ipa_info(path):
    """(Info.plist dict, Payload/<app>.app prefix) of an .ipa."""
    with zipfile.ZipFile(path) as z:
        plists = [
            n
            for n in z.namelist()
            if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", n)
        ]
        if not plists:
            raise DevError(f"{path} has no Payload/*.app: is it an .ipa?")
        info = plistlib.loads(z.read(plists[0]))
    return info, plists[0].rsplit("/", 1)[0]


def find_base(mod, meta, explicit=None):
    """The base .ipa for a mod: given, remembered, or found by bundle ID in intake/ and ~/Downloads."""
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise DevError(f"no file at {explicit}")
        return p
    state = load_state(mod)
    remembered = state.get("base_ipa")
    if remembered and Path(remembered).is_file():
        return Path(remembered)
    want_sha = (meta.get("base") or {}).get("sha256")
    target = meta["target_bundle_id"]
    folders = [am_root() / "intake", Path.home() / "Downloads"]
    if WSL:
        folders.append(
            Path("/mnt/c/Users") / (os.environ.get("WIN_USER") or "") / "Downloads"
        )
    matches = []
    for folder in folders:
        if not folder.is_dir():
            continue
        for ipa in folder.glob("*.ipa"):
            try:
                info, _ = ipa_info(ipa)
            except (DevError, zipfile.BadZipFile, KeyError):
                continue
            if info.get("CFBundleIdentifier") == target:
                matches.append((ipa, info.get("CFBundleShortVersionString", "")))
    if not matches:
        raise DevError(
            f"no .ipa of {target} in {', '.join(str(f) for f in folders)}: pass --base"
        )
    if want_sha and len(matches) > 1:
        for ipa, _ in matches:
            if sha256(ipa) == want_sha:
                return ipa
    matches.sort(key=lambda m: m[1], reverse=True)
    if len(matches) > 1:
        print(
            f"several copies of {target}; using {matches[0][0]} ({matches[0][1]})",
            file=sys.stderr,
        )
    return matches[0][0]


# ---------------------------------------------------------------- dump


def metadata_version(path):
    with open(path, "rb") as f:
        head = f.read(8)
    if len(head) < 8 or head[:4] != b"\xaf\x1b\xb1\xfa":
        raise DevError(f"{path} is not global-metadata.dat")
    return struct.unpack_from("<i", head, 4)[0]


def dumper_for(version):
    """(folder, label) of the Il2CppDumper that reads this metadata version."""
    tools = am_root() / "tools"
    if version <= 31:
        return tools / "il2cppdumper", "Il2CppDumper 6.7.46"
    return tools / "il2cppdumper-v39", "Il2CppDumper fork (v35-v39)"


def run_dumper(folder, binary, metadata, out):
    exe = folder / "Il2CppDumper.exe"
    dll = folder / "Il2CppDumper.dll"
    env = dict(os.environ, DOTNET_ROLL_FORWARD="LatestMajor")
    if WINDOWS and exe.is_file():
        cmd = [str(exe), str(binary), str(metadata), str(out)]
    elif dll.is_file() and shutil.which("dotnet"):
        cmd = ["dotnet", str(dll), str(binary), str(metadata), str(out)]
    else:
        raise DevError(
            f"no Il2CppDumper in {folder} (scripts/install_il2cppdumper.sh), or no dotnet on PATH; dump from Windows"
        )
    print("==> " + " ".join(cmd))
    r = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL, check=False)
    if r.returncode != 0:
        raise DevError(f"Il2CppDumper failed ({r.returncode})")


def dump(mod, base=None, force=False):
    meta = read_json(mod_dir(mod) / "mod.json")
    ipa = find_base(mod, meta, base)
    info, app = ipa_info(ipa)
    version = info.get("CFBundleShortVersionString", "0")
    work = am_root() / "work" / f"dump-{mod}-{version}"
    out = work / "out"
    state = load_state(mod)
    state.update(base_ipa=str(ipa), app_version=version, dump_dir=str(work))
    save_state(mod, state)
    if (out / "dump.cs").is_file() and not force:
        print(f"==> dump exists: {out / 'dump.cs'} (--force to redo)")
        return out / "dump.cs"
    work.mkdir(parents=True, exist_ok=True)
    binary = work / "UnityFramework"
    metadata = work / "global-metadata.dat"
    with zipfile.ZipFile(ipa) as z:
        names = set(z.namelist())
        fw = f"{app}/Frameworks/UnityFramework.framework/UnityFramework"
        md = f"{app}/Data/Managed/Metadata/global-metadata.dat"
        if fw not in names:
            raise DevError(
                f"{ipa.name} has no UnityFramework: not a Unity game (or the code is in the main executable)"
            )
        if md not in names:
            raise DevError(f"{ipa.name} has no global-metadata.dat (not IL2CPP?)")
        print(f"==> Extracting UnityFramework and global-metadata.dat from {ipa.name}")
        for name, dest in ((fw, binary), (md, metadata)):
            with z.open(name) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
    mv = metadata_version(metadata)
    folder, label = dumper_for(mv)
    print(f"==> Metadata v{mv}: {label}")
    out.mkdir(exist_ok=True)
    run_dumper(folder, binary, metadata, out)
    if not (out / "dump.cs").is_file():
        raise DevError("the dumper wrote no dump.cs")
    size = (out / "dump.cs").stat().st_size
    print(f"==> {out / 'dump.cs'} ({size / 1e6:.1f} MB)")
    return out / "dump.cs"


def dump_path(mod):
    state = load_state(mod)
    d = state.get("dump_dir")
    if d and (Path(d) / "out" / "dump.cs").is_file():
        return Path(d) / "out" / "dump.cs"
    raise DevError(f"no dump for {mod}: run dev.py dump {mod}")


_dump_cache = {}


def dump_methods(mod):
    p = dump_path(mod)
    if p not in _dump_cache:
        _dump_cache[p] = methods(str(p))
    return _dump_cache[p]


TYPE_KIND = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|static|sealed|abstract|readonly|partial)\s+)*(class|struct|interface|enum)\s+([\w.]+)"
)
FIELD = re.compile(
    r"^\s*((?:(?:public|private|protected|internal|static|readonly|const)\s+)*)([\w.<>,\[\] ]+?)\s+([\w<>]+)(?:\s*=\s*[^;]+)?; // (0x[0-9A-Fa-f]+)"
)


def type_kinds(mod):
    """{type name: class|struct|interface|enum} from dump.cs (nested as Outer.Inner)."""
    kinds = {}
    with open(dump_path(mod), encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = TYPE_KIND.match(line)
            if m:
                kinds[m.group(2).split("<")[0]] = m.group(1)
    return kinds


def fields_of(mod, cls):
    """[(offset, static?, type, name)] of a class, from dump.cs."""
    found, inside, depth = [], False, 0
    with open(dump_path(mod), encoding="utf-8", errors="ignore") as f:
        for line in f:
            if not inside:
                m = TYPE_KIND.match(line)
                if m and m.group(2).split("<")[0] == cls:
                    inside, depth = True, 0
                continue
            depth += line.count("{") - line.count("}")
            if depth < 0:
                break
            if "// Methods" in line:
                break
            m = FIELD.match(line)
            if m:
                found.append(
                    (
                        int(m.group(4), 16),
                        "static" in m.group(1),
                        m.group(2).strip(),
                        m.group(3),
                    )
                )
    return found


# ---------------------------------------------------------------- find / fields


def describe(m):
    jit = "" if not m.needs_jit else "  needs JIT"
    slot = f"  slot {m.slot}" if m.slot is not None else ""
    static = "static " if re.match(r"^(?:\w+\s+)*static\s", m.signature) else ""
    return f"{m.full_name}({', '.join(m.params)}) -> {static}{m.return_type}  [{m.assembly}]  RVA {m.rva}{slot}{jit}"


def cmd_find(args):
    found = dump_methods(args.mod)
    if args.class_name:
        rows = [
            m
            for m in found
            if m.class_name == args.class_name
            or m.full_name.rsplit(".", 1)[0] == args.class_name
        ]
    else:
        rx = re.compile(args.pattern, re.IGNORECASE)
        rows = [m for m in found if rx.search(m.full_name)]
    for m in rows[: args.limit]:
        print("  " + describe(m))
    more = len(rows) - args.limit
    print(f"{len(rows)} methods" + (f" ({more} more: --limit)" if more > 0 else ""))
    return 0


def cmd_fields(args):
    rows = fields_of(args.mod, args.class_name)
    if not rows:
        print(f"no class {args.class_name} in the dump (or it has no fields)")
        return 1
    for offset, static, typ, name in rows:
        print(f"  0x{offset:<5X} {'static ' if static else ''}{typ} {name}")
    print(
        f'{len(rows)} fields of {args.class_name} (AMIl2cppField(obj, "{args.class_name}", "<name>") reads them at run time)'
    )
    return 0


# ---------------------------------------------------------------- add / tweak / remove / gen


def load_hooks(folder):
    p = folder / "hooks.json"
    return read_json(p) if p.is_file() else {"engine": "unity-il2cpp", "hooks": []}


def load_tweaks(folder, meta):
    p = folder / "tweaks.json"
    return (
        read_json(p)
        if p.is_file()
        else {"title": meta.get("display_name") or meta["name"], "tweaks": []}
    )


def parse_value(text):
    if text is None:
        return None
    low = text.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    try:
        return int(text)
    except ValueError:
        return float(text)


def key_for(method_name):
    cls, name = method_name.rsplit(".", 1)
    name = re.sub(r"^(get_|set_)", "", name)
    base = cls.rsplit(".", 1)[-1] + name[:1].upper() + name[1:]
    base = re.sub(r"\W", "", base)
    return base[:1].lower() + base[1:]


def resolve_method(mod, name, argc=None):
    """The dump's Method for "Class.method" (or Namespace.Class.method), one overload."""
    found = dump_methods(mod)
    rows = [
        m for m in found if m.full_name == name or f"{m.class_name}.{m.name}" == name
    ]
    if argc is not None:
        rows = [m for m in rows if m.args == argc]
    if not rows:
        leads = [m for m in found if m.name == name.rsplit(".", 1)[-1]][:5]
        hint = (
            ("; same method name in: " + ", ".join(m.full_name for m in leads))
            if leads
            else ""
        )
        raise DevError(f"{name} is not in the dump{hint}")
    if len(rows) > 1:
        lines = "\n".join("  " + describe(m) for m in rows)
        raise DevError(
            f"{name} has {len(rows)} overloads; pick one with --args N:\n{lines}"
        )
    return rows[0]


def hook_entry(mod, m, kinds):
    entry = m.hook()
    enums, structs = [], []
    for p in m.params:
        typ = p.replace("ref ", "").replace("out ", "").split()[0]
        base = typ.split("<")[0].rstrip("[]").rsplit(".", 1)[-1]
        if typ.endswith("[]") or "<" in typ or base in gen_tweaks.CTYPES:
            continue
        kind = kinds.get(base) or kinds.get(typ)
        if kind == "enum":
            enums.append(base)
        elif kind == "struct":
            structs.append(base)
    if enums:
        entry["enums"] = sorted(set(enums))
    return entry, structs


def cmd_add(args):
    folder = mod_dir(args.mod)
    meta = read_json(folder / "mod.json")
    m = resolve_method(args.mod, args.method, args.args)
    kinds = type_kinds(args.mod)
    entry, structs = hook_entry(args.mod, m, kinds)
    action = args.action
    if action is None:
        action = "skip" if m.kind == "void" else "return"
    if structs and action != "custom":
        raise DevError(
            f"{m.full_name} takes {', '.join(structs)} by value; generated hooks can't type it. Use --action custom and "
            "write hook_...() in Tweak.x (or hook another method)."
        )
    value = parse_value(args.value)
    if action == "return" and value is None:
        if m.kind == "bool":
            value = True
        else:
            raise DevError(
                f"{m.full_name} returns {m.return_type}: give --value (or --action multiply with --slider)"
            )
    if action == "multiply" and args.slider is None and value is None and not args.to:
        raise DevError(
            "multiply needs --slider MIN MAX DEFAULT (a slider) or --value (a fixed factor)"
        )

    hooks = load_hooks(folder)
    full = gen_tweaks.hook_name(entry)
    hooks["hooks"] = [h for h in hooks["hooks"] if gen_tweaks.hook_name(h) != full] + [
        entry
    ]
    tweaks = load_tweaks(folder, meta)
    hook = {"method": full, "action": action}
    if action in ("return", "arg") or (action == "multiply" and value is not None):
        hook["value"] = value
    if action == "arg":
        if args.index is None:
            raise DevError("arg needs --index I (0-based argument)")
        hook["index"] = args.index
    if args.to:
        target = next((t for t in tweaks["tweaks"] if t["key"] == args.to), None)
        if target is None:
            raise DevError(
                f"no tweak with key {args.to} (dev.py tweak {args.mod} {args.to} --title ...)"
            )
        target.setdefault("hooks", []).append(hook)
        key = args.to
    else:
        key = args.key or key_for(full)
        if any(t["key"] == key for t in tweaks["tweaks"]):
            raise DevError(
                f"a tweak {key} exists: --to {key} adds this hook to it, --as gives another key"
            )
        tweak = {
            "key": key,
            "title": args.title or f"{m.class_name}.{m.name}",
            "kind": "switch",
            "hooks": [hook],
        }
        if args.slider:
            lo, hi, default = args.slider
            tweak.update(kind="slider", min=lo, max=hi, default=default)
        tweaks["tweaks"].append(tweak)
    problems = gen_tweaks.problems(tweaks, hooks)
    if problems:
        raise DevError("not written:\n  " + "\n  ".join(problems))
    write_json(folder / "hooks.json", hooks)
    write_json(folder / "tweaks.json", tweaks)
    # remember the choice, so the next game's `suggest` starts from it
    store = dev_learn.load(patterns_path())
    tweak_item = next(t for t in tweaks["tweaks"] if t["key"] == key)
    dev_learn.record(store, entry, action, value, tweak_item, meta["target_bundle_id"])
    dev_learn.save(patterns_path(), store)
    print(f"==> {full}: {describe(m)}")
    print(
        f"==> tweak {key}: {action}"
        + (f" {value}" if value is not None else "")
        + ("  (needs JIT)" if m.needs_jit else "")
    )
    if action == "custom":
        _static, ret, cargs, _ = gen_tweaks.parse_signature(entry)
        params = (
            ([] if _static else ["void *self"])
            + [f"{c} {n}" for c, n, _ in cargs]
            + ["const void *mi"]
        )
        print(
            f"    define in Tweak.x:  {ret} hook_{gen_tweaks.ident(full)}({', '.join(params)}) {{ ... orig_{gen_tweaks.ident(full)}(...) }}"
        )
    return cmd_gen(args)


def cmd_tweak(args):
    folder = mod_dir(args.mod)
    meta = read_json(folder / "mod.json")
    tweaks = load_tweaks(folder, meta)
    if any(t["key"] == args.key for t in tweaks["tweaks"]):
        raise DevError(f"a tweak {args.key} exists")
    tweak = {"key": args.key, "title": args.title, "kind": "switch", "hooks": []}
    if args.slider:
        lo, hi, default = args.slider
        tweak.update(kind="slider", min=lo, max=hi, default=default)
    tweaks["tweaks"].append(tweak)
    problems = gen_tweaks.problems(tweaks, load_hooks(folder))
    if problems:
        raise DevError("not written:\n  " + "\n  ".join(problems))
    write_json(folder / "tweaks.json", tweaks)
    print(f"==> tweak {args.key} ({tweak['kind']}): read am_{args.key} in Tweak.x")
    return cmd_gen(args)


def cmd_remove(args):
    folder = mod_dir(args.mod)
    meta = read_json(folder / "mod.json")
    tweaks, hooks = load_tweaks(folder, meta), load_hooks(folder)
    before = len(tweaks["tweaks"])
    tweaks["tweaks"] = [t for t in tweaks["tweaks"] if t["key"] != args.what]
    for t in tweaks["tweaks"]:
        t["hooks"] = [h for h in t.get("hooks", []) if h["method"] != args.what]
    used = {h["method"] for t in tweaks["tweaks"] for h in t.get("hooks", [])}
    short = {
        m.rsplit(".", 1)[0].rsplit(".", 1)[-1] + "." + m.rsplit(".", 1)[1] for m in used
    }
    kept = [
        h
        for h in hooks["hooks"]
        if gen_tweaks.hook_name(h) in used
        or f"{h['class']}.{h['method']}" in used | short
    ]
    removed_hooks = len(hooks["hooks"]) - len(kept)
    hooks["hooks"] = kept
    write_json(folder / "tweaks.json", tweaks)
    write_json(folder / "hooks.json", hooks)
    print(
        f"==> removed {before - len(tweaks['tweaks'])} tweak(s) and {removed_hooks} unused hook(s)"
    )
    return cmd_gen(args)


def cmd_gen(args):
    folder = mod_dir(args.mod)
    if not (folder / "tweaks.json").is_file():
        print(f"mods/{args.mod} has no tweaks.json (dev.py add writes one)")
        return 1
    problems = gen_tweaks.generate(str(folder))
    for p in problems:
        print(f"tweaks.json: {p}")
    if problems:
        return 1
    tweaks = read_json(folder / "tweaks.json")
    n = sum(len(t.get("hooks", [])) for t in tweaks["tweaks"])
    print(f"==> AMTweaks.gen.m: {len(tweaks['tweaks'])} tweaks, {n} hooks")
    return 0


def cmd_check(args):
    import check_hooks

    return check_hooks.main([args.mod, str(dump_path(args.mod))])


# ---------------------------------------------------------------- new


def camel(name):
    return "".join(p.capitalize() for p in name.split("-"))


TWEAK_X = """// {title}: a personal Accretion mod for {app} ({target}).
//
// The menu items and their hooks live in tweaks.json (scripts/dev.py add {name} <Class.method> ...) and are
// generated into AMTweaks.gen.m. This file holds what the generator can't express: hook_<ident>() bodies for
// "custom" hooks, and the ticker below for no-JIT switches that write the game's data (runtime/AMIl2cppData.h).
// Without JIT, hooks catch only virtual methods, Unity callbacks and delegates; a plain method the game calls
// directly needs a data path here instead (find the object, set the field or call the setter twice a second).

#import <UIKit/UIKit.h>
#import "menu/AMMenu.h"
#import "runtime/AMIl2cpp.h"
#import "runtime/AMIl2cppData.h"
#import "AMTweaks.gen.h"

// Runs twice a second once the runtime is up (main thread).
static void tick(void) {{
    UIApplication.sharedApplication.idleTimerDisabled = am_keepAwake;
    // Example of a no-JIT switch:
    // void *player = AMIl2cppStaticObject("Player", "instance");
    // if (am_godMode && player) *(float *)AMIl2cppField(player, "Player", "health") = 100;
}}

%ctor {{
    AMIl2cppDataSetLogTag(AMTweaksLogTag.UTF8String);
    AMTweaksRegister();
    AMIl2cppSetReportHandler(^(NSString *report) {{
        AMIl2cppLog(@"%@", report);
        AMIl2cppEvery(0.5, ^{{ tick(); }});
        // again later, so a device-log capture started after launch still sees which hooks installed
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 20 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{{
            AMIl2cppLog(@"report again: %@", report);
        }});
    }});
    AMIl2cppStart();
    [AMMenu.shared install];
}}
"""

MAKEFILE = """TARGET := iphone:clang:latest:15.0
ARCHS = arm64

include $(THEOS)/makefiles/common.mk

TWEAK_NAME = {tweak}

# menu/ and runtime/ (with hooks.json as runtime/am_hooks_data.c) are copied in, and AMTweaks.gen.m is
# generated from tweaks.json, by scripts/build.sh. No %hook and no Substrate: hooks go through the runtime.
{tweak}_FILES = Tweak.x AMTweaks.gen.m menu/AMMenu.m runtime/SideloadCompat.m runtime/am_il2cpp.c runtime/AMIl2cpp.m runtime/AMIl2cppData.m runtime/am_hooks_data.c
{tweak}_CFLAGS = -fobjc-arc
{tweak}_FRAMEWORKS = UIKit Security
{tweak}_LOGOS_DEFAULT_GENERATOR = internal

include $(THEOS_MAKE_PATH)/tweak.mk
"""

CONTROL = """Package: io.github.accretionmods.{name}
Name: {title}
Version: 1.0.0
Architecture: iphoneos-arm
Description: {app} with an Accretion mod menu (personal build).
Maintainer: Accretion
Author: Accretion
Section: Tweaks
Depends: mobilesubstrate
"""


def cmd_new(args):
    name = args.name
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name):
        raise DevError(
            "name must be lowercase letters, digits and dashes, like my-game-plus"
        )
    folder = ROOT / "mods" / name
    if folder.exists():
        raise DevError(f"mods/{name} already exists")
    ipa = Path(args.ipa)
    if not ipa.is_file():
        raise DevError(f"no file at {ipa}")
    info, _ = ipa_info(ipa)
    app = info.get("CFBundleDisplayName") or info.get("CFBundleName") or "App"
    target = info["CFBundleIdentifier"]
    version = info.get("CFBundleShortVersionString") or "1.0"
    title = args.title or f"{app}+"
    tweak = camel(name)
    print(f"==> {app} {version} ({target}); sha256 of the base...")
    sha = sha256(ipa)
    folder.mkdir(parents=True)
    (folder / "Makefile").write_text(
        MAKEFILE.format(tweak=tweak), encoding="utf-8", newline="\n"
    )
    (folder / "control").write_text(
        CONTROL.format(name=name, title=title, app=app), encoding="utf-8", newline="\n"
    )
    (folder / f"{tweak}.plist").write_text(
        f'{{ Filter = {{ Bundles = ( "{target}" ); }}; }}\n',
        encoding="utf-8",
        newline="\n",
    )
    (folder / "Tweak.x").write_text(
        TWEAK_X.format(title=title, app=app, target=target, name=name),
        encoding="utf-8",
        newline="\n",
    )
    write_json(folder / "hooks.json", {"engine": "unity-il2cpp", "hooks": []})
    write_json(
        folder / "tweaks.json",
        {
            "title": title,
            "tweaks": [
                {
                    "key": "keepAwake",
                    "title": "Keep screen awake",
                    "kind": "switch",
                    "hooks": [],
                }
            ],
        },
    )
    write_json(
        folder / "mod.json",
        {
            "name": title,
            "version": "1.0.0",
            "kind": "tweak",
            "personal": True,
            "category": args.category,
            "subtitle": f"{app} with an Accretion mod menu (personal build).",
            "description": f"{app} with the Accretion mod menu built in (personal build, never published).\n\nMod menu (tap the floating button):\n• Keep screen awake",
            "target_bundle_id": target,
            "bundle_id": f"io.github.accretionmods.{name.replace('-', '')}",
            "display_name": title,
            "tested_app_version": version,
            "min_os": "15.0",
            "remove_extensions": "encrypted",
            "icon_url": "https://getaccretion.github.io/assets/icon.png",
            "screenshots": [],
            "base": {"private": True, "open_source": False, "sha256": sha},
        },
    )
    gen_tweaks.generate(str(folder))
    save_state(name, {"base_ipa": str(ipa), "app_version": version})
    r = subprocess.run(
        [sys.executable, str(HERE / "validate_mods.py"), name], check=False
    )
    if r.returncode != 0:
        raise DevError("validate_mods.py failed")
    print(f"==> Made mods/{name} for {app} {version}")
    print(
        f'    Next: dev.py dump {name}; dev.py find {name} Damage; dev.py add {name} Player.TakeDamage --title "No damage"'
    )
    print(
        f"    Then: dev.py build {name} --ipa; dev.py sign <ipa> --install; dev.py log {name}"
    )
    if not args.no_dump:
        return 0 if dump(name, str(ipa)) else 1
    return 0


# ---------------------------------------------------------------- build


def bump_version(folder):
    meta = read_json(folder / "mod.json")
    parts = meta["version"].split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    new = ".".join(parts)
    meta["version"] = new
    write_json(folder / "mod.json", meta)
    control = folder / "control"
    if control.is_file():
        text = control.read_text(encoding="utf-8")
        text = re.sub(r"^Version: .*$", f"Version: {new}", text, flags=re.MULTILINE)
        control.write_text(text, encoding="utf-8", newline="\n")
    print(f"==> version {new}")
    return new


def run_in_wsl(script, cwd=None):
    """Runs a bash script in WSL from Windows, or directly on Linux; returns (code, stdout)."""
    if WINDOWS:
        cmd = ["wsl", "-e", "bash", "-lc", script]
    else:
        cmd = ["bash", "-lc", script]
    r = subprocess.run(
        cmd,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if r.stderr:
        sys.stderr.write(r.stderr)
    return r.returncode, r.stdout


def cmd_build(args):
    folder = mod_dir(args.mod)
    if args.bump:
        bump_version(folder)
    meta = read_json(folder / "mod.json")
    newer = newer_bases(meta)
    if newer:
        print(
            f"==> Note: {newer[0][0].name} is {newer[0][1]}, newer than the {meta.get('tested_app_version')} this mod is built for (dev.py update {args.mod})"
        )
    if (folder / "tweaks.json").is_file():
        problems = gen_tweaks.generate(str(folder))
        if problems:
            raise DevError("tweaks.json:\n  " + "\n  ".join(problems))
    repo = wsl_path(ROOT)
    builds = am_root() / "builds"
    builds.mkdir(parents=True, exist_ok=True)
    tweak = camel(args.mod)
    makefile = (folder / "Makefile").read_text(encoding="utf-8")
    m = re.search(r"^TWEAK_NAME\s*=\s*(\S+)", makefile, re.MULTILINE)
    if m:
        tweak = m.group(1)
    t0 = time.time()
    print(f"==> Building {args.mod} {meta['version']} in WSL (Theos)")
    code, out = run_in_wsl(f'cd "{repo}" && scripts/build.sh {args.mod}')
    if code != 0:
        raise DevError("build failed (Theos output above)")
    dylib_wsl = out.strip().splitlines()[-1]
    dylib_src = Path(win_path(dylib_wsl)) if WINDOWS else Path(dylib_wsl)
    dylib = builds / f"{tweak}-{meta['version']}.dylib"
    shutil.copyfile(dylib_src, dylib)
    print(f"==> {dylib} ({dylib.stat().st_size // 1024} KB, {time.time() - t0:.0f} s)")
    state = load_state(args.mod)
    state["last_dylib"] = str(dylib)
    if args.ipa:
        base = find_base(args.mod, meta, args.base)
        want = (meta.get("base") or {}).get("sha256")
        if want and sha256(base) != want:
            raise DevError(
                f"{base.name} is not the base mod.json names (sha256 differs); pass --base or update mod.json"
            )
        print(f"==> Injecting into {base.name}")
        code, out = run_in_wsl(
            f'cd "{repo}" && scripts/local_ipa.sh {args.mod} "{wsl_path(base)}" "{wsl_path(builds)}"'
        )
        if code != 0:
            raise DevError("local_ipa.sh failed")
        built = [
            line.split("Built: ", 1)[1].split(" (")[0]
            for line in out.splitlines()
            if "Built: " in line
        ]
        ipa = (
            Path(win_path(built[-1]))
            if built and WINDOWS
            else (Path(built[-1]) if built else None)
        )
        if ipa:
            print(f"==> {ipa} ({ipa.stat().st_size / 1e6:.1f} MB, unsigned)")
            state["last_ipa"] = str(ipa)
            print(f'    Next: dev.py sign "{ipa}" --install')
    save_state(args.mod, state)
    return 0


# ---------------------------------------------------------------- sign / install / log


def plumesign():
    for candidate in (
        os.environ.get("PLUMESIGN_PATH"),
        Path(os.environ.get("APPDATA", "")) / "AccretionLink" / "bin" / "plumesign.exe",
        shutil.which("plumesign"),
    ):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise DevError(
        "plumesign not found: set PLUMESIGN_PATH (Accretion Link keeps one in %APPDATA%\\AccretionLink\\bin)"
    )


def usbmux_device_id():
    """usbmux's numeric DeviceID of the phone on USB (what plumesign --udid wants), via pymobiledevice3."""
    code = (
        "import asyncio, inspect\n"
        "from pymobiledevice3 import usbmux\n"
        "r = usbmux.list_devices()\n"
        "r = asyncio.run(r) if inspect.isawaitable(r) else r\n"
        "usb = [d for d in r if getattr(d, 'connection_type', 'USB') == 'USB']\n"
        "print(usb[0].devid if usb else '')\n"
    )
    runner = (
        ["uv", "run", "--no-project", "--with", "pymobiledevice3", "python", "-c", code]
        if shutil.which("uv")
        else [sys.executable, "-c", code]
    )
    r = subprocess.run(
        runner, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False
    )
    devid = r.stdout.strip()
    if r.returncode != 0 or not devid:
        raise DevError(
            "no iPhone on USB (pymobiledevice3 usbmux list is empty)"
            + (f"\n{r.stderr.strip()}" if r.stderr.strip() else "")
        )
    return devid


def cmd_sign(args):
    if not WINDOWS:
        raise DevError(
            "sign runs on Windows (plumesign with the saved Apple ID); from WSL use scripts/sign.sh with a certificate"
        )
    ipa = Path(args.ipa)
    if not ipa.is_file():
        raise DevError(f"no file at {ipa}")
    out = (
        Path(args.out)
        if args.out
        else ipa.with_name(ipa.stem.replace("_accretion", "") + "_signed.ipa")
    )
    cmd = [plumesign(), "sign", "-p", str(ipa), "--apple-id", "-o", str(out)]
    if args.install:
        cmd += ["--register-and-install", "--udid", usbmux_device_id()]
    print("==> " + " ".join(cmd))
    r = subprocess.run(
        cmd, check=False
    )  # interactive: Apple ID prompts (2FA) go to this console
    if r.returncode != 0:
        raise DevError(f"plumesign failed ({r.returncode})")
    print(f"==> {out}" + (" installed" if args.install else ""))
    return 0


def cmd_install(args):
    ipa = Path(args.ipa)
    if not ipa.is_file():
        raise DevError(f"no file at {ipa}")
    r = subprocess.run(
        ["pymobiledevice3", "apps", "install", str(ipa)],
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return r.returncode


def cmd_log(args):
    folder = mod_dir(args.mod)
    meta = read_json(folder / "mod.json")
    tag = meta.get("display_name") or meta["name"]
    if (folder / "tweaks.json").is_file():
        tag = (
            read_json(folder / "tweaks.json").get("log_tag")
            or read_json(folder / "tweaks.json").get("title")
            or tag
        )
    pattern = f"\\[{re.escape(tag)}\\]|AccretionMods"
    cmd = ["pymobiledevice3", "syslog", "live", "-ei", pattern]
    print(f"==> {' '.join(cmd)}  (for {args.seconds} s; Ctrl+C stops)")
    try:
        p = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        raise DevError(
            "pymobiledevice3 not on PATH (uv tool install pymobiledevice3)"
        ) from None
    end = time.time() + args.seconds
    lines = 0
    try:
        while time.time() < end:
            line = p.stdout.readline()
            if not line:
                if p.poll() is not None:
                    break
                continue
            print(line.rstrip())
            lines += 1
    except KeyboardInterrupt:
        pass
    finally:
        if p.poll() is None:
            p.kill()
    print(f"==> {lines} lines")
    return 0


def cmd_status(args):
    folder = mod_dir(args.mod)
    meta = read_json(folder / "mod.json")
    state = load_state(args.mod)
    print(
        f"{args.mod}: {meta.get('display_name') or meta['name']} {meta['version']} for {meta['target_bundle_id']} {meta.get('tested_app_version', '')}"
    )
    print(f"  personal: {bool(meta.get('personal'))}")
    print(f"  base .ipa: {state.get('base_ipa', '(not remembered: dev.py dump)')}")
    d = state.get("dump_dir")
    print(
        f"  dump: {d + '/out/dump.cs' if d else '(none)'}"
        + (
            ""
            if d and (Path(d) / "out" / "dump.cs").is_file()
            else "  MISSING"
            if d
            else ""
        )
    )
    hooks = load_hooks(folder)["hooks"]
    jit = sum(1 for h in hooks if h.get("needs_jit"))
    print(f"  hooks.json: {len(hooks)} hooks ({jit} need JIT)")
    if (folder / "tweaks.json").is_file():
        tweaks = read_json(folder / "tweaks.json")["tweaks"]
        print(f"  tweaks.json: {len(tweaks)} tweaks")
        for t in tweaks:
            hs = (
                ", ".join(
                    f"{h['method']} {h.get('action', 'custom')}"
                    for h in t.get("hooks", [])
                )
                or "no hook (Tweak.x)"
            )
            print(f"    {t['key']} ({t.get('kind', 'switch')}): {t['title']}  <- {hs}")
    else:
        print("  tweaks.json: none (hand-written Tweak.x)")
    for k in ("last_dylib", "last_ipa"):
        if state.get(k):
            print(f"  {k.replace('_', ' ')}: {state[k]}")
    newer = newer_bases(meta)
    if newer:
        print(
            f"  newer base available: {newer[0][0]} ({newer[0][1]}): dev.py update {args.mod}"
        )
    return 0


# ---------------------------------------------------------------- update (a newer base .ipa)


def version_tuple(text):
    return tuple(
        int(x) if x.isdigit() else 0 for x in re.split(r"[.\-]", str(text or "0"))
    )


def newer_bases(meta):
    """Copies of the target app in intake/ and ~/Downloads newer than mod.json's tested_app_version: [(ipa, version)]."""
    target, tested = meta["target_bundle_id"], meta.get("tested_app_version", "0")
    found = []
    for folder in (am_root() / "intake", Path.home() / "Downloads"):
        if not folder.is_dir():
            continue
        for ipa in folder.glob("*.ipa"):
            try:
                info, _ = ipa_info(ipa)
            except (DevError, zipfile.BadZipFile, KeyError, OSError):
                continue
            version = info.get("CFBundleShortVersionString", "0")
            if info.get("CFBundleIdentifier") == target and version_tuple(
                version
            ) > version_tuple(tested):
                found.append((ipa, version))
    found.sort(key=lambda f: version_tuple(f[1]), reverse=True)
    return found


def cmd_update(args):
    """A newer base .ipa: dump it, check every hook, refresh hooks.json and mod.json, say what needs a look."""
    import check_hooks

    folder = mod_dir(args.mod)
    meta = read_json(folder / "mod.json")
    if args.ipa:
        ipa = Path(args.ipa)
        if not ipa.is_file():
            raise DevError(f"no file at {ipa}")
    else:
        newer = newer_bases(meta)
        if not newer:
            print(
                f"==> no copy of {meta['target_bundle_id']} newer than {meta.get('tested_app_version')} in intake/ or Downloads"
            )
            return 0
        ipa = newer[0][0]
    info, _ = ipa_info(ipa)
    if info.get("CFBundleIdentifier") != meta["target_bundle_id"]:
        raise DevError(
            f"{ipa.name} is {info.get('CFBundleIdentifier')}, not {meta['target_bundle_id']}"
        )
    version = info.get("CFBundleShortVersionString", "0")
    sha = sha256(ipa)
    old_sha = (meta.get("base") or {}).get("sha256")
    if sha == old_sha:
        print(f"==> {ipa.name} is already the base of {args.mod} ({version})")
        return 0
    print(f"==> {args.mod}: {meta.get('tested_app_version')} -> {version} ({ipa.name})")
    dump(args.mod, str(ipa), False)
    _dump_cache.clear()
    found = dump_methods(args.mod)
    hooks = load_hooks(folder)
    results = check_hooks.check(hooks["hooks"], found)
    counts = {"ok": 0, "changed": 0, "missing": 0}
    for status, h, detail in results:
        counts[status] += 1
        name = gen_tweaks.hook_name(h)
        print(f"  {status:8} {name}  {detail}")
        m = check_hooks.match(h, found)
        if m is not None:
            # the new version's signature, so the generated hook matches it (changed ones need a look anyway)
            fresh = m.hook()
            for k in ("args", "return_type", "signature", "needs_jit", "assembly"):
                h[k] = fresh[k]
    meta.setdefault("base", {})["sha256"] = sha
    meta["tested_app_version"] = version
    write_json(folder / "mod.json", meta)
    write_json(folder / "hooks.json", hooks)
    state = load_state(args.mod)
    state.update(base_ipa=str(ipa), app_version=version)
    save_state(args.mod, state)
    if (folder / "tweaks.json").is_file():
        problems = gen_tweaks.generate(str(folder))
        for p in problems:
            print(f"  tweaks.json: {p}")
    print(
        f"==> {counts['ok']} ok, {counts['changed']} changed, {counts['missing']} missing of {len(results)} hooks; mod.json now names {version}"
    )
    if counts["missing"]:
        print(
            "    missing hooks stay in hooks.json and are skipped at run time; `find` the new name and `remove`/`add` to replace them"
        )
    if counts["changed"]:
        print(
            "    changed hooks: the generated code follows the new signature; check the tweak still makes sense (value, index)"
        )
    print(f"    Next: dev.py build {args.mod} --ipa --bump")
    return 1 if counts["changed"] or counts["missing"] else 0


# ---------------------------------------------------------------- pick / suggest / mark / learn (patterns)


def patterns_path():
    return am_root() / "dev" / "patterns.json"


def ask(prompt, default=None):
    text = input(
        f"{prompt}" + (f" [{default}]" if default not in (None, "") else "") + ": "
    ).strip()
    return text or default


def add_interactively(mod, m, pattern=None):
    """Ask for title, action and value, then run `add` for one method (pick and suggest use it)."""
    folder = mod_dir(mod)
    meta = read_json(folder / "mod.json")
    tweaks = load_tweaks(folder, meta)
    default_action = (pattern or {}).get("action") or (
        "skip"
        if m.kind == "void"
        else "return"
        if m.kind == "bool"
        else "multiply"
        if m.kind in ("int", "float")
        else "custom"
    )
    title = ask("Menu title", dev_learn.title_for(m, pattern or {}))
    keys = ", ".join(t["key"] for t in tweaks["tweaks"]) or "none yet"
    to = ask(
        f"Add to an existing switch? key or Enter for a new one (have: {keys})", ""
    )
    action = ask(f"Action ({'/'.join(gen_tweaks.ACTIONS)})", default_action)
    value, index, slider = None, None, None
    if action in ("return", "arg"):
        default_value = (pattern or {}).get("value")
        if default_value is None and m.kind == "bool":
            default_value = True
        value = ask(
            "Value", "" if default_value is None else str(default_value).lower()
        )
        if action == "arg":
            index = int(ask("Argument index (0-based)", "0"))
    elif action == "multiply" and not to:
        default_slider = " ".join(
            str(x) for x in (pattern or {}).get("slider") or (1, 10, 1)
        )
        text = ask(
            "Slider MIN MAX DEFAULT (Enter for a fixed --value instead)", default_slider
        )
        if text:
            slider = [float(x) for x in text.split()]
        else:
            value = ask("Fixed factor", "2")
    ns = argparse.Namespace(
        mod=mod,
        method=m.full_name,
        key=None,
        title=title,
        action=action,
        value=value,
        index=index,
        args=m.args,
        slider=slider,
        to=to or None,
    )
    return cmd_add(ns)


def cmd_pick(args):
    found = dump_methods(args.mod)
    rx = re.compile(args.pattern, re.IGNORECASE)
    folder = mod_dir(args.mod)
    used = {gen_tweaks.hook_name(h) for h in load_hooks(folder)["hooks"]}
    rows = [m for m in found if rx.search(m.full_name) and m.full_name not in used][
        : args.limit
    ]
    if not rows:
        print("no methods match (or all of them are hooked already)")
        return 1
    store = dev_learn.load(patterns_path())
    for i, m in enumerate(rows, 1):
        p = store["patterns"].get(dev_learn.pattern_key(m.name, m.return_type))
        known = f"   <- known: {dev_learn.describe_pattern(p)}" if p else ""
        print(f"  [{i:2}] {describe(m)}{known}")
    choice = ask("Number to hook (Enter to stop)", "")
    if not choice:
        return 0
    try:
        m = rows[int(choice) - 1]
    except (ValueError, IndexError):
        raise DevError(f"no row {choice}") from None
    p = store["patterns"].get(dev_learn.pattern_key(m.name, m.return_type))
    return add_interactively(args.mod, m, p)


def cmd_suggest(args):
    folder = mod_dir(args.mod)
    store = dev_learn.load(patterns_path())
    if not store["patterns"]:
        print(
            f"nothing learned yet: {patterns_path()} fills with every `add`; `dev.py learn <mod>` imports an existing mod's hooks.json"
        )
        return 1
    used = {gen_tweaks.hook_name(h) for h in load_hooks(folder)["hooks"]}
    rows = dev_learn.suggest(store, dump_methods(args.mod), used, args.limit)
    if not rows:
        print(
            "no method of this game matches a learned pattern (dev.py find <mod> <regex> searches by name)"
        )
        return 0
    for i, (sc, m, p, exact) in enumerate(rows, 1):
        print(f"  [{i:2}] {describe(m)}")
        print(
            f'        {"same name" if exact else "similar name"} as learned: {dev_learn.describe_pattern(p)}; title "{dev_learn.title_for(m, p)}"'
        )
    if args.no_pick:
        return 0
    choice = ask("Number to hook (Enter to stop)", "")
    if not choice:
        return 0
    try:
        _sc, m, p, _exact = rows[int(choice) - 1]
    except (ValueError, IndexError):
        raise DevError(f"no row {choice}") from None
    return add_interactively(args.mod, m, p)


def cmd_mark(args):
    folder = mod_dir(args.mod)
    meta = read_json(folder / "mod.json")
    tweaks = load_tweaks(folder, meta)
    tweak = next((t for t in tweaks["tweaks"] if t["key"] == args.key), None)
    if tweak is None:
        raise DevError(f"no tweak {args.key} in mods/{args.mod}/tweaks.json")
    works = bool(args.works) and not args.fails
    store = dev_learn.load(patterns_path())
    touched = dev_learn.mark(
        store, tweak, load_hooks(folder)["hooks"], meta["target_bundle_id"], works
    )
    tweak["verified"] = {
        "result": "works" if works else "fails",
        "date": dev_learn.date.today().isoformat(),
        "app_version": meta.get("tested_app_version"),
    }
    write_json(folder / "tweaks.json", tweaks)
    dev_learn.save(patterns_path(), store)
    print(
        f"==> {args.key}: {'works' if works else 'fails'} on {meta.get('tested_app_version')}; patterns updated: {', '.join(touched) or 'none (no hooks)'}"
    )
    return 0


def cmd_learn(args):
    folder = mod_dir(args.mod)
    meta = read_json(folder / "mod.json")
    hooks = load_hooks(folder)["hooks"]
    tweaks = (
        read_json(folder / "tweaks.json")
        if (folder / "tweaks.json").is_file()
        else None
    )
    store = dev_learn.load(patterns_path())
    n = dev_learn.learn(store, hooks, meta["target_bundle_id"], tweaks)
    dev_learn.save(patterns_path(), store)
    print(
        f"==> learned {n} hooks from {args.mod}; {len(store['patterns'])} patterns in {patterns_path()}"
    )
    return 0


# ---------------------------------------------------------------- main


def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("new")
    s.add_argument("name")
    s.add_argument("ipa")
    s.add_argument("--title")
    s.add_argument("--category", default="games")
    s.add_argument("--no-dump", action="store_true")
    s.set_defaults(fn=cmd_new)

    s = sub.add_parser("dump")
    s.add_argument("mod")
    s.add_argument("--base")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=lambda a: 0 if dump(a.mod, a.base, a.force) else 1)

    s = sub.add_parser("find")
    s.add_argument("mod")
    s.add_argument("pattern", nargs="?", default=".")
    s.add_argument("--class", dest="class_name")
    s.add_argument("--limit", type=int, default=40)
    s.set_defaults(fn=cmd_find)

    s = sub.add_parser("fields")
    s.add_argument("mod")
    s.add_argument("class_name")
    s.set_defaults(fn=cmd_fields)

    s = sub.add_parser("add")
    s.add_argument("mod")
    s.add_argument("method")
    s.add_argument("--as", dest="key")
    s.add_argument("--title")
    s.add_argument("--action", choices=gen_tweaks.ACTIONS)
    s.add_argument("--value")
    s.add_argument("--index", type=int)
    s.add_argument("--args", type=int)
    s.add_argument("--slider", nargs=3, type=float, metavar=("MIN", "MAX", "DEFAULT"))
    s.add_argument("--to")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("tweak")
    s.add_argument("mod")
    s.add_argument("key")
    s.add_argument("--title", required=True)
    s.add_argument("--slider", nargs=3, type=float, metavar=("MIN", "MAX", "DEFAULT"))
    s.set_defaults(fn=cmd_tweak)

    s = sub.add_parser("remove")
    s.add_argument("mod")
    s.add_argument("what")
    s.set_defaults(fn=cmd_remove)

    s = sub.add_parser("gen")
    s.add_argument("mod")
    s.set_defaults(fn=cmd_gen)

    s = sub.add_parser("check")
    s.add_argument("mod")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("build")
    s.add_argument("mod")
    s.add_argument("--ipa", action="store_true")
    s.add_argument("--bump", action="store_true")
    s.add_argument("--base")
    s.set_defaults(fn=cmd_build)

    s = sub.add_parser("sign")
    s.add_argument("ipa")
    s.add_argument("--install", action="store_true")
    s.add_argument("-o", "--out")
    s.set_defaults(fn=cmd_sign)

    s = sub.add_parser("install")
    s.add_argument("ipa")
    s.set_defaults(fn=cmd_install)

    s = sub.add_parser("log")
    s.add_argument("mod")
    s.add_argument("--seconds", type=int, default=60)
    s.set_defaults(fn=cmd_log)

    s = sub.add_parser("status")
    s.add_argument("mod")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser(
        "update", help="a newer base .ipa: re-dump, check hooks, refresh mod.json"
    )
    s.add_argument("mod")
    s.add_argument("ipa", nargs="?")
    s.set_defaults(fn=cmd_update)

    s = sub.add_parser("pick", help="numbered find, then add one with a few questions")
    s.add_argument("mod")
    s.add_argument("pattern")
    s.add_argument("--limit", type=int, default=30)
    s.set_defaults(fn=cmd_pick)

    s = sub.add_parser(
        "suggest", help="methods of this game that match what worked before"
    )
    s.add_argument("mod")
    s.add_argument("--limit", type=int, default=30)
    s.add_argument("--no-pick", action="store_true")
    s.set_defaults(fn=cmd_suggest)

    s = sub.add_parser("mark", help="record the phone result of a tweak")
    s.add_argument("mod")
    s.add_argument("key")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--works", action="store_true")
    g.add_argument("--fails", action="store_true")
    s.set_defaults(fn=cmd_mark)

    s = sub.add_parser("learn", help="import a mod's hooks.json into the pattern store")
    s.add_argument("mod")
    s.set_defaults(fn=cmd_learn)

    if not argv and len(sys.argv) == 1:
        p.print_help()
        return 0
    args = p.parse_args(argv)
    try:
        return args.fn(args) or 0
    except DevError as e:
        print(f"dev.py: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
