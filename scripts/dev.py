"""Mod dev loop for personal Unity (IL2CPP) mods: dump the game, find methods, add a tweak, build, sign, log.

Runs on Windows (uv run --no-project --python 3.12 scripts/dev.py ...  or  py -3 scripts/dev.py ...) and in
WSL/Linux (python3 scripts/dev.py ...). Theos builds run in WSL either way; dumping needs .NET (Windows has
it); signing and device logs need the phone on USB and run on Windows (plumesign, pymobiledevice3).

  dev.py new <name> <base.ipa> [--title T] [--category games]   personal mod folder from a decrypted .ipa
  dev.py dump <mod> [--base file.ipa] [--force]                  Il2CppDumper -> work/dump-<mod>-<ver>/dump.cs
  dev.py find <mod> <regex> [--limit N]                          methods whose Class.method matches
  dev.py find <mod> --class <Class>                              every method of one class
  dev.py find <mod> <regex> --fields                             fields whose Namespace.Class.field matches
  dev.py fields <mod> <Class>                                    the class's fields with offsets
  dev.py add <mod> <Class.method> [--as key] [--title T]         hook a method: hooks.json + tweaks.json + code
         [--action skip|return|multiply|arg|custom] [--value V] [--index I] [--args N]
         [--slider MIN MAX DEFAULT] [--to key]
  dev.py pin <mod> <Class.field | Class.set_X> --value V        no-JIT switch: write a field or call a setter on the
         [--object Class.instance|Class|Class[]] [--when lower]    live object(s) every half second (the validated path)
         [--threshold N] [--via get_X --class Y] [--multiply] [--slider MIN MAX DEFAULT] [--as key | --to key]
  dev.py call <mod> <Class.Method> --args ... --every 10        no-JIT switch: call a method every N s while on
  dev.py tweak <mod> <key> --title T [--slider MIN MAX DEFAULT]  a switch or slider with no hook (for Tweak.x code)
  dev.py remove <mod> <key or Class.method>                      drop a tweak (and hooks nothing else uses)
  dev.py gen <mod>                                               regenerate AMTweaks.gen.m from tweaks.json
  dev.py check <mod>                                             hooks.json against the current dump
  dev.py build <mod> [--ipa] [--bump] [--base file.ipa]          .dylib (and .ipa) in C:\\AccretionMods\\builds
  dev.py sign <file.ipa> [--install] [-o out.ipa]                Apple ID signing with plumesign (Windows)
  dev.py install <signed.ipa>                                    pymobiledevice3 apps install (USB)
  dev.py log <mod> [--seconds 60]                                device log lines of the mod and the runtime
  dev.py status <mod>                                            base, dump, tweaks, hooks, last build
  dev.py run <mod> [--bump] [--seconds 60]                       build .ipa + sign + install + open + log, in one go
  dev.py update <mod> [new.ipa]                                  newer game version: re-dump, hooks ok/changed/missing,
                                                                 mod.json base + tested version refreshed
  dev.py pick <mod> <regex>                                      numbered find; type a number, answer 2-3 questions
  dev.py suggest <mod>                                           this game's methods that match what worked before
  dev.py mark <mod> <key> --works|--fails                        the phone result; feeds the suggestions
  dev.py share <mod> [--months 6|12 | --days N] [--file x.ipa]  private download link (dl.getaccretion.app/s/<id>)
  dev.py share --list | --renew <id> [--months N] | --revoke <id> same link, new expiry; or end it
  dev.py share <mod> --update <id>                               same link, the mod's newest build
  dev.py test <mod> [--section S | --keys a,b] [--seconds 20]    switches on from the PC a section at a time;
                                                                 which reach the game (off again after)
  dev.py learn <mod>                                             import an existing mod's hooks.json into the patterns
  dev.py fetch <bundle-id | name | --mod M> [--region us]        a decrypted .ipa from the decrypt site into intake/
         [--platform iphone] [--force]                           (AM_DECRYPT_URL; default decrypt.34306.lol, a third party)
  dev.py fetch --login [--email E] | --logout | --account        the site account a new decrypt needs
  dev.py fetch --save-login | --forget-login                     keep it in Windows Credential Manager; fetch signs in
  dev.py start <bundle-id | file.ipa> [--name N] [--title T]      new game in one go: decrypted copy, report,
                                                                 mod folder + dump, scan with the best candidates
  dev.py push <mod> [--off] [--launch]                           switches to the phone without rebuilding: the
                                                                 mod's tweaks.json as the app's live_tweaks.json
  dev.py post <mod> [--draft] [-o file]                          the release post (workspace template) once every
                                                                 switch is marked works on the tested game version
  dev.py report <mod | file.ipa> [--json]                        is it worth modding: engine, decrypted, online,
                                                                 ads and purchases; scan counts once dumped
  dev.py scan <mod> [--area Combat] [--find word] [--limit 40]   no-JIT switch candidates in the dump, scored,
         [--flagged] [--json] [--text]                            with what is sold/online flagged (Studio: Scan tab)
  dev.py games [top-free|top-paid|top-grossing|new-free|new-paid|similar] [--region us] [--json] [--all]
  dev.py games search <words or bundle ID> [--games]              the App Store's own search
  dev.py games like <bundle-id>                                   games like that one
  dev.py games info <bundle-id> [--json]                          one app: version, min iOS, size, themes, text
  dev.py fetch <bundle-id> --source decrypt.day|armconverter     open that site in the browser, take the download
  dev.py fetch --import <file.ipa>                               check a copy downloaded by hand, move it to intake/
         (a site that asks for a captcha opens in the browser; fetch waits for the download in ~/Downloads)

scripts/am.ps1 wraps all of this as `am <command> ...` from PowerShell. With no arguments: this help.

Folders (AM_ROOT, default C:\\AccretionMods): intake/ and ~/Downloads are searched for base .ipa files by
bundle ID; work/ holds dumps; builds/ the .dylib and .ipa files; dev/<mod>.json remembers the base and dump.
Base .ipa files must be decrypted. Nothing here decrypts or uploads anything: bring your own copy, or `fetch`
one already decrypted by the decrypt site (another party's Mac; the copy is checked, its origin and SHA-256 are
saved next to it, and mods built on it are for testing on your own phone).
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
ROOT = (
    Path(os.environ["AM_REPO"]) if os.environ.get("AM_REPO") else HERE.parent
)  # AM_REPO: the tests point it at a temp repo
sys.path.insert(0, str(HERE))

import dev_learn
import dump_scan
import moddability
import games
import gen_tweaks
import ipa_fetch
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
    stat = p.stat()
    stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
    if p not in _dump_cache or _dump_cache[p][0] != stamp:
        _dump_cache[p] = (stamp, methods(str(p)))
    return _dump_cache[p][1]


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
    """[(offset, static?, type, name)] of a class, from dump.cs. cls may carry its namespace (Core.PlayerLife)
    and its assembly (HotFix:GameManager)."""
    cls = cls.rpartition(":")[2]
    found, inside, depth, ns = [], False, 0, ""
    with open(dump_path(mod), encoding="utf-8", errors="ignore") as f:
        for line in f:
            if not inside:
                if line.startswith("// Namespace:"):
                    ns = line[13:].strip()
                    continue
                m = TYPE_KIND.match(line)
                name = m.group(2).split("<")[0] if m else None
                if m and (name == cls or (ns and f"{ns}.{name}" == cls)):
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


def field_rows(mod):
    """[(Namespace.Class, static?, type, field)] for every number, bool and object field in dump.cs, one pass."""
    rows, cls, ns, depth = [], None, "", 0
    with open(dump_path(mod), encoding="utf-8", errors="ignore") as f:
        for line in f:
            if cls is None:
                if line.startswith("// Namespace:"):
                    ns = line[13:].strip()
                    continue
                m = TYPE_KIND.match(line)
                if m:
                    name = m.group(2).split("<")[0]
                    cls, depth = (f"{ns}.{name}" if ns else name), 0
                continue
            depth += line.count("{") - line.count("}")
            if depth < 0 or "// Properties" in line or "// Methods" in line:
                cls = None  # fields come first; the rest of the class is not needed
                continue
            m = FIELD.match(line)
            if m:
                rows.append((cls, "static" in m.group(1), m.group(2).strip(), m.group(3)))
    return rows


def cmd_find(args):
    if args.fields:
        rx = re.compile(args.pattern, re.IGNORECASE)
        rows = [r for r in field_rows(args.mod)
                if "<" not in r[0] and not r[3].startswith("<>") and rx.search(f"{r[0]}.{r[3]}")]
        # left out: compiler-made iterator and lambda classes; kept: property backing fields (<Currency>k__...)
        for cls, static, typ, name in rows[: args.limit]:
            print(f"  {cls}.{name}  {'static ' if static else ''}{typ}")
        more = len(rows) - args.limit
        print(f"{len(rows)} fields" + (f" ({more} more: --limit)" if more > 0 else ""))
        return 0
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
    """The dump's Method for "Class.method" (or Namespace.Class.method, or Assembly:Class.method), one overload."""
    name = name.rpartition(":")[2]
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
    if m.needs_jit and action != "custom":
        print(
            f"    without JIT this hook catches nothing (the game calls {m.name} directly). The no-JIT way is a pin:\n"
            f"    dev.py pin {args.mod} {m.class_name}.<field or set_Property> --value V --to {key}   (dev.py fields {args.mod} {m.class_name})"
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


def cmd_pin(args):
    """The no-JIT path: every tick, write a field or call a setter on the live object(s) of a class."""
    folder = mod_dir(args.mod)
    meta = read_json(folder / "mod.json")
    if "." not in args.target:
        raise DevError("target is Class.field or Class.set_Property")
    cls, _, member = args.target.rpartition(".")
    pin = {"object": args.object or "", "when": args.when}
    if member.startswith("set_") and not args.field:
        m = resolve_method(args.mod, args.target, 1)
        cs_type = m.params[0].replace("ref ", "").replace("out ", "").split()[0]
        ctype_ = gen_tweaks.ctype(
            cs_type,
            set(hook_entry(args.mod, m, type_kinds(args.mod))[0].get("enums", [])),
        )
        pin["setter"] = member
        what = f"{m.full_name}({', '.join(m.params)})"
    else:
        rows = [f for f in fields_of(args.mod, cls) if f[3] == member]
        if not rows:
            leads = ", ".join(f[3] for f in fields_of(args.mod, cls)[:12])
            raise DevError(
                f"{cls} has no field {member}"
                + (f" (fields: {leads}...)" if leads else " (class not in the dump?)")
            )
        offset, static, cs_type, _ = rows[0]
        if static:
            raise DevError(
                f"{args.target} is a static field; pin an instance field, or a setter"
            )
        ctype_ = gen_tweaks.ctype(
            cs_type, {k for k, v in type_kinds(args.mod).items() if v == "enum"}
        )
        pin["field"] = member
        what = f"{cls}.{member} ({cs_type} at 0x{offset:X} in 10.x; read by name at run time)"
    if cs_type.rsplit(".", 1)[-1] == "FP":  # Photon's fixed point: a long holding value * 65536
        ctype_ = "int64_t"
        pin["scale"] = gen_tweaks.FP_SCALE
    if ctype_ not in gen_tweaks.NUMERIC and ctype_ != "bool":
        raise DevError(f"{args.target} is {cs_type}: a pin needs a number or bool")
    pin["ctype"] = ctype_
    if not pin["object"]:
        statics = [
            f
            for f in fields_of(args.mod, cls)
            if f[1]
            and f[3] in ("instance", "Instance", "p", "I", "_instance", "singleton")
        ]
        singleton = None
        if not statics:  # GameManager : Singleton<GameManager>: the instance lives on the generic base
            try:
                data = json.loads(dump_path(args.mod).with_name("scan.json").read_text(encoding="utf-8"))["data"]
                singleton = (data.get(cls) or {}).get("singleton")
            except (OSError, ValueError, KeyError):
                singleton = None
        pin["object"] = f"{cls}.{statics[0][3]}" if statics else f"{cls}.{singleton}" if singleton else cls
    raw = args.value
    if (args.slider and raw in (None, "slider")) or (raw == "slider" and args.to):
        value = "slider"  # with --to, the slider is the existing tweak's (checked below)
    elif raw == "peak":
        value = "peak"
    elif raw and raw.startswith("getter:"):
        value = {"getter": raw[7:]}
    elif raw and raw.startswith("field:"):
        value = {"field": raw[6:]}
    else:
        value = parse_value(raw)
    if value is None:
        raise DevError(
            "give --value: a number, true/false, peak, getter:get_X, field:Y, or slider (with --slider)"
        )
    pin["value"] = value
    if args.threshold is not None:
        pin["threshold"] = args.threshold
    if args.via:
        pin["via"] = args.via
    if args.cls:
        pin["class"] = args.cls
    if args.multiply:
        pin["op"] = "multiply"

    tweaks = load_tweaks(folder, meta)
    if args.to:
        tweak = next((t for t in tweaks["tweaks"] if t["key"] == args.to), None)
        if tweak is None:
            raise DevError(f"no tweak {args.to}")
        key = args.to
    else:
        key = args.key or key_for(f"{cls}.{member}")
        if any(t["key"] == key for t in tweaks["tweaks"]):
            raise DevError(
                f"a tweak {key} exists: --to {key} adds this pin to it, --as gives another key"
            )
        tweak = {
            "key": key,
            "title": args.title or f"{cls} {member}",
            "kind": "switch",
            "hooks": [],
        }
        if args.slider:
            lo, hi, default = args.slider
            tweak.update(kind="slider", min=lo, max=hi, default=default)
        tweaks["tweaks"].append(tweak)
    tweak.setdefault("pins", []).append(pin)
    problems = gen_tweaks.problems(tweaks, load_hooks(folder))
    if problems:
        raise DevError("not written:\n  " + "\n  ".join(problems))
    write_json(folder / "tweaks.json", tweaks)
    store = dev_learn.load(patterns_path())
    dev_learn.record_pin(store, pin.get("class") or cls, member, ctype_, pin, tweak, meta["target_bundle_id"])
    dev_learn.save(patterns_path(), store)
    print(f"==> pin: {what}")
    print(
        f"==> tweak {key}: every tick on {pin['object']}: {member} {args.when} {value}  (no JIT needed)"
    )
    return cmd_gen(args)


def cmd_call(args):
    """A pin that calls a method on the live object(s) every N seconds (the game's own debug givers, for example)."""
    folder = mod_dir(args.mod)
    meta = read_json(folder / "mod.json")
    values = [parse_value(a) for a in args.args]
    m = resolve_method(args.mod, args.target, len(values))
    cls = m.class_name
    enums = {k for k, v in type_kinds(args.mod).items() if v == "enum"}
    ctypes_ = []
    for p in m.params:
        cs_type = (
            p.replace("ref ", "").replace("out ", "").split()[0]
        )  # "int level = -1" -> int
        c = gen_tweaks.ctype(cs_type, enums)
        if c not in gen_tweaks.NUMERIC and c != "bool":
            raise DevError(
                f"{m.full_name}: argument {p!r} is not a number or bool; write the call in Tweak.x (AMIl2cppCall)"
            )
        ctypes_.append(c)
    pin = {
        "object": args.object or "",
        "call": m.name,
        "args": values,
        "arg_ctypes": ctypes_,
        "every": args.every,
    }
    if not pin["object"]:
        statics = [
            f
            for f in fields_of(args.mod, cls)
            if f[1]
            and f[3] in ("instance", "Instance", "p", "I", "_instance", "singleton")
        ]
        pin["object"] = f"{cls}.{statics[0][3]}" if statics else cls
    tweaks = load_tweaks(folder, meta)
    if args.to:
        tweak = next((t for t in tweaks["tweaks"] if t["key"] == args.to), None)
        if tweak is None:
            raise DevError(f"no tweak {args.to}")
        key = args.to
    else:
        key = args.key or key_for(f"{cls}.{m.name}")
        if any(t["key"] == key for t in tweaks["tweaks"]):
            raise DevError(
                f"a tweak {key} exists: --to {key} adds this call to it, --as gives another key"
            )
        tweak = {
            "key": key,
            "title": args.title or f"{cls} {m.name}",
            "kind": "switch",
            "hooks": [],
        }
        tweaks["tweaks"].append(tweak)
    tweak.setdefault("pins", []).append(pin)
    problems = gen_tweaks.problems(tweaks, load_hooks(folder))
    if problems:
        raise DevError("not written:\n  " + "\n  ".join(problems))
    write_json(folder / "tweaks.json", tweaks)
    store = dev_learn.load(patterns_path())
    dev_learn.record(store, m.hook(), "call", values, tweak, meta["target_bundle_id"])
    dev_learn.save(patterns_path(), store)
    print(
        f"==> tweak {key}: {pin['object']}.{m.name}({', '.join(map(str, values))}) every {args.every} s while on  (no JIT needed)"
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


def pin_problems_in_dump(mod):
    """Every pin of a mod against its dump, before a build: [(level, key, message)], level "error" or "warn".
    Errors: an object, field, setter, getter, via or call the dump doesn't have. Warnings: a field type that
    differs from the pin's, a via that returns another class, and a pin that reads a getter the mod also
    hooks (without JIT the read can return the hook's value, as skill points did: use the field via the
    object that stores it instead)."""
    folder = mod_dir(mod)
    meta = read_json(folder / "mod.json")
    tweaks = load_tweaks(folder, meta)
    dump_scan.scan(dump_path(mod), tweaks, assemblies=meta.get("scan_assemblies"))  # refreshes scan.json (read below)
    data = json.loads((dump_path(mod).with_name("scan.json")).read_text(encoding="utf-8"))["data"]
    methods = {}
    for m in dump_methods(mod):
        methods.setdefault((m.class_name.split(".")[-1], m.name), []).append(m)
    hooked = {".".join(gen_tweaks.hook_name(h).split(".")[-2:]) for h in load_hooks(folder)["hooks"]}
    out = []

    def has_method(cls, name, argc=None):
        found = methods.get((cls.rpartition(":")[2].split(".")[-1], name), [])  # keyed by short name
        return [m for m in found if argc is None or len(m.params) == argc]

    def getter(key, cls, name, why):
        if not has_method(cls, name, 0):
            out.append(("error", key, f"{cls}.{name}() not in the dump ({why})"))
        elif f"{cls}.{name}" in hooked:
            out.append(("warn", key, f"reads {cls}.{name}, which this mod also hooks: without JIT the read can "
                                     "return the hook's value; pin the field that stores it (--via) instead"))

    for t in tweaks.get("tweaks", []):
        for p in t.get("pins", []):
            key = t["key"]
            obj = p["object"]
            cls = obj[:-2] if obj.endswith("[]") else (obj.rpartition(".")[0] if obj.rpartition(".")[0] in data else obj.split(".")[0])
            info = data.get(cls)
            short = cls.rpartition(":")[2].split(".")[-1]
            if info is None and not methods.get((short, ".ctor")) and not any(k[0] == short for k in methods):
                out.append(("error", key, f"no class {cls} in the dump"))
                continue
            if "." in obj and not obj.endswith("[]") and info is not None and not p.get("class"):
                static = obj.rpartition(".")[2]
                if static not in info.get("statics", []) and static != info.get("singleton"):
                    out.append(("warn", key, f"{obj}: not a static field of {cls}'s own type in the dump"))
            target = p.get("class") or cls
            if p.get("via"):
                vias = has_method(cls, p["via"], 0)
                if not vias:
                    out.append(("error", key, f"via {cls}.{p['via']}() not in the dump"))
                elif p.get("class") and vias[0].return_type.split(".")[-1] != p["class"]:
                    out.append(("warn", key, f"via {cls}.{p['via']}() returns {vias[0].return_type}, not {p['class']}"))
                if f"{cls}.{p['via']}" in hooked:
                    out.append(("warn", key, f"via {cls}.{p['via']} is hooked by this mod"))
            tinfo = data.get(target) or {}
            if p.get("field"):
                f = (tinfo.get("fields") or {}).get(p["field"])
                if f is None:
                    out.append(("error", key, f"{target}.{p['field']} is not a number or bool field in the dump"))
                elif f["ctype"] != p.get("ctype"):
                    out.append(("warn", key, f"{target}.{p['field']} is {f['ctype']} in the dump, the pin says {p.get('ctype')}"))
            elif p.get("setter"):
                if not has_method(target, p["setter"], 1):
                    out.append(("error", key, f"{target}.{p['setter']}(value) not in the dump"))
                if p.get("when", "always") != "always" or p.get("value") == "peak":
                    getter(key, target, "get_" + p["setter"][4:], "read before writing")
            elif p.get("call"):
                if not has_method(target, p["call"], len(p.get("args", []))):
                    out.append(("error", key, f"{target}.{p['call']} with {len(p.get('args', []))} arguments not in the dump"))
            v = p.get("value")
            if isinstance(v, dict) and v.get("getter"):
                getter(key, target, v["getter"], "the value to write")
            elif isinstance(v, dict) and v.get("field") and v["field"] not in (tinfo.get("fields") or {}):
                out.append(("error", key, f"{target}.{v['field']} (the value) is not a number field in the dump"))
    return out


def print_pin_problems(problems):
    for level, key, msg in problems:
        print(f"  {'ERROR' if level == 'error' else 'warn ':5} pin {key}: {msg}")


def cmd_check(args):
    import check_hooks

    hooks = load_hooks(mod_dir(args.mod))["hooks"]
    results = check_hooks.check(hooks, dump_methods(args.mod))
    for status, h, detail in results:
        print(f"  {status:8} {gen_tweaks.hook_name(h)}  {detail}")
    counts = {
        s: sum(1 for r in results if r[0] == s) for s in ("ok", "changed", "missing")
    }
    print(
        f"{counts['ok']} ok, {counts['changed']} changed, {counts['missing']} missing of {len(results)} hooks"
    )
    problems = pin_problems_in_dump(args.mod)
    print_pin_problems(problems)
    errors = sum(1 for p in problems if p[0] == "error")
    print(f"pins: {errors} errors, {len(problems) - errors} warnings")
    return 1 if counts["changed"] or counts["missing"] or errors else 0


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
    AMTweaksTick();  // the pins from tweaks.json (dev.py pin ...): field writes and setter calls, no JIT needed
    // Hand-written no-JIT logic goes here, for example:
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
    # every pin against the dump first: an error here would only show as a red dot on the phone, 2 minutes later
    try:
        problems = pin_problems_in_dump(args.mod)
    except DevError:
        problems = []  # no dump yet: nothing to check against
    if problems:
        print("==> pin check against the dump:")
        print_pin_problems(problems)
    if any(p[0] == "error" for p in problems) and not getattr(args, "no_check", False):
        raise DevError("pins that can't work (above): fix them, or build anyway with --no-check")
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


def tool_path(name):
    """A tool on PATH, or in uv's bin folder (~/.local/bin), which a server started outside a login shell may lack."""
    found = shutil.which(name)
    if found:
        return found
    exe = Path.home() / ".local" / "bin" / (name + (".exe" if WINDOWS else ""))
    return str(exe) if exe.is_file() else None


def pymobiledevice3_cli():
    exe = tool_path("pymobiledevice3")
    if not exe:
        raise DevError("pymobiledevice3 not found (uv tool install pymobiledevice3)")
    return exe


def pymobiledevice3_python():
    """The Python of the installed pymobiledevice3 (uv tool, or the one next to the pymobiledevice3 on PATH)."""
    candidates = []
    uv = tool_path("uv")
    r = (
        subprocess.run([uv, "tool", "dir"], capture_output=True, text=True, check=False)
        if uv
        else None
    )
    if r and r.returncode == 0 and r.stdout.strip():
        candidates.append(
            Path(r.stdout.strip())
            / "pymobiledevice3"
            / ("Scripts" if WINDOWS else "bin")
            / ("python.exe" if WINDOWS else "python")
        )
    if WINDOWS:
        candidates.append(
            Path(os.environ.get("APPDATA", ""))
            / "uv"
            / "tools"
            / "pymobiledevice3"
            / "Scripts"
            / "python.exe"
        )
    exe = tool_path("pymobiledevice3")
    if exe:
        candidates.append(
            Path(exe).resolve().parent / ("python.exe" if WINDOWS else "python")
        )
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def usbmux_device_id():
    """usbmux's numeric DeviceID of the phone on USB (what plumesign --udid wants), via pymobiledevice3."""
    python = pymobiledevice3_python()
    if not python:
        raise DevError("pymobiledevice3 not found (uv tool install pymobiledevice3)")
    # one line on purpose: multi-line string literals have been mangled on the owner's PC (handbook rule 8)
    code = (
        "import asyncio, inspect; from pymobiledevice3 import usbmux; r = usbmux.list_devices(); "
        "r = asyncio.run(r) if inspect.isawaitable(r) else r; "
        "usb = [d for d in r if str(getattr(d, 'connection_type', 'USB')).upper() == 'USB']; "
        "print(usb[0].devid if usb else '')"
    )
    r = subprocess.run(
        [python, "-c", code],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    devid = r.stdout.strip()
    if r.returncode != 0 or not devid:
        detail = (" " + r.stderr.strip().splitlines()[-1]) if r.stderr.strip() else ""
        raise DevError(
            "no iPhone on USB (pymobiledevice3 sees no device: unlock the phone, trust this PC, check the cable)"
            + detail
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
    print(f"==> {out}")
    if args.install:
        # plumesign's install has left the app missing minutes later (2026-10-21, FBSApplicationLibrary NotFound);
        # check the phone's list and install the signed copy over USB if it is not there
        info, _ = ipa_info(out)
        bundle = info["CFBundleIdentifier"]
        if bundle in installed_bundles(bundle.rsplit(".", 2)[0]):
            print(f"==> installed as {bundle}")
        else:
            print(
                f"==> {bundle} is not on the phone after plumesign's install; installing the signed copy over USB"
            )
            if (
                subprocess.run(
                    [pymobiledevice3_cli(), "apps", "install", str(out)],
                    stdin=subprocess.DEVNULL,
                    check=False,
                ).returncode
                != 0
            ):
                raise DevError("pymobiledevice3 apps install failed")
        if getattr(args, "launch", False):
            launch_app(bundle)
    return 0


def installed_bundles(prefix):
    """Bundle IDs on the USB phone that start with prefix (an Apple ID-signed copy carries a team suffix)."""
    r = subprocess.run(
        [pymobiledevice3_cli(), "apps", "list"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if r.returncode != 0:
        detail = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ""
        raise DevError(f"could not list the phone's apps: {detail}")
    try:
        apps = json.loads(r.stdout[r.stdout.index("{") :])
    except ValueError:
        raise DevError("pymobiledevice3 apps list printed no JSON") from None
    return [b for b in apps if b.startswith(prefix)]


def launch_app(bundle):
    """Opens an app on the USB phone through the developer tunnel (Developer Mode on, phone unlocked)."""
    print(f"==> launching {bundle}")
    r = subprocess.run(
        [pymobiledevice3_cli(), "developer", "dvt", "launch", bundle],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        detail = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ""
        raise DevError(
            f"launch failed ({detail}); open the app by hand, then dev.py log"
        )
    return True


def cmd_run(args):
    """The whole loop: build the .ipa, sign and install it, open it, read the log."""
    mod_dir(args.mod)  # fails early on an unknown mod
    code = cmd_build(
        argparse.Namespace(mod=args.mod, ipa=True, bump=args.bump, base=args.base)
    )
    if code:
        return code
    ipa = load_state(args.mod).get("last_ipa")
    if not ipa:
        raise DevError("no .ipa from the build")
    cmd_sign(argparse.Namespace(ipa=ipa, install=True, out=None, launch=False))
    signed = Path(ipa).with_name(
        Path(ipa).stem.replace("_accretion", "") + "_signed.ipa"
    )
    bundle = ipa_info(signed)[0]["CFBundleIdentifier"]
    state = load_state(args.mod)
    state["installed_bundle"] = bundle
    save_state(args.mod, state)
    launch_app(bundle)
    return cmd_log(argparse.Namespace(mod=args.mod, seconds=args.seconds, launch=False))


def cmd_install(args):
    ipa = Path(args.ipa)
    if not ipa.is_file():
        raise DevError(f"no file at {ipa}")
    r = subprocess.run(
        [pymobiledevice3_cli(), "apps", "install", str(ipa)],
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return r.returncode


def push_live(mod, bundle, test=None):
    """The mod's pinned tweaks (and an am test block) to the phone as Documents/live_tweaks.json."""
    folder = mod_dir(mod)
    tweaks = load_tweaks(folder, read_json(folder / "mod.json"))
    live = {"title": tweaks.get("title"), "tweaks": [t for t in tweaks.get("tweaks", []) if t.get("pins")]}
    if test:
        live["test"] = test
    tmp = am_root() / "work" / f"live_tweaks-{mod}.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(live), encoding="utf-8")
    r = subprocess.run([pymobiledevice3_cli(), "apps", "push", bundle, str(tmp), "Documents/live_tweaks.json"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise DevError(f"push failed: {(r.stderr or r.stdout).strip()[-300:]}")
    return live


def cmd_push(args):
    """tweaks.json into the installed app as Documents/live_tweaks.json: the build's runtime (AMLivePins.h) reads
    it within 2 s, runs its pins instead of the compiled ones, and adds new switches to the menu. --off removes
    it, so the build's own switches apply again. Needs a build that has AMLivePins (built after it existed)."""
    folder = mod_dir(args.mod)
    meta = read_json(folder / "mod.json")
    found = installed_bundles(meta["bundle_id"])
    if not found:
        raise DevError(f"{meta['bundle_id']} is not on the phone (dev.py run {args.mod})")
    cli = pymobiledevice3_cli()
    if args.off:
        r = subprocess.run([cli, "apps", "rm", found[0], "Documents/live_tweaks.json"], capture_output=True, text=True)
        print("==> live switches removed: the build's own switches apply again" if r.returncode == 0
              else f"==> nothing removed ({(r.stderr or r.stdout).strip().splitlines()[-1:] or ['no file']})")
        return 0
    if not args.no_check:
        problems = pin_problems_in_dump(args.mod)
        print_pin_problems(problems)
        if any(p[0] == "error" for p in problems):
            raise DevError("pins that can't work (above): fix them, or push anyway with --no-check")
    live = push_live(args.mod, found[0])
    print(f"==> {len(live['tweaks'])} switches pushed to {found[0]}: the game picks them up within 2 s, no rebuild")
    if args.launch:
        launch_app(found[0])
    return 0


def cmd_log(args):
    folder = mod_dir(args.mod)
    meta = read_json(folder / "mod.json")
    if getattr(args, "launch", False):
        found = installed_bundles(meta["bundle_id"])
        if not found:
            raise DevError(
                f"{meta['bundle_id']} is not on the phone (dev.py sign <ipa> --install)"
            )
        launch_app(found[0])
    lines = stream_log(args.mod, args.seconds, print)
    print(
        f"==> {lines} lines"
        + (
            ""
            if lines
            else " (no line from the mod: is the game open? the hook report repeats 20 s after launch)"
        )
    )
    return 0


def stream_log(mod, seconds, on_line, quiet=False, on_ready=None):
    """The mod's device-log lines for `seconds`, each to on_line(text); returns how many. on_ready() runs once the
    log is connected (am test turns switches on only then: a status line logged before that is lost)."""
    folder = mod_dir(mod)
    meta = read_json(folder / "mod.json")
    tag = meta.get("display_name") or meta["name"]
    if (folder / "tweaks.json").is_file():
        tag = (
            read_json(folder / "tweaks.json").get("log_tag")
            or read_json(folder / "tweaks.json").get("title")
            or tag
        )
    pattern = f"\\[{re.escape(tag)}\\]|AccretionMods"
    cmd = [
        pymobiledevice3_cli(),
        "syslog",
        "live",
        "-e",
        pattern,
    ]  # case-sensitive: the bundle ID has "accretionmods" too
    if not quiet:
        print(f"==> {' '.join(cmd)}  (for {seconds} s; Ctrl+C stops)")
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
    # a reader thread: readline() would block past the deadline while the phone is quiet
    import queue
    import threading

    q = queue.Queue()

    def reader():
        for line in p.stdout:
            q.put(line)
        q.put(None)

    threading.Thread(target=reader, daemon=True).start()
    if on_ready:
        time.sleep(3)  # pymobiledevice3 takes a moment to attach to the device log
        on_ready()
    end = time.time() + seconds
    lines = 0
    try:
        while time.time() < end:
            try:
                line = q.get(timeout=min(1.0, max(0.1, end - time.time())))
            except queue.Empty:
                continue
            if line is None:
                break
            on_line(line.rstrip())
            lines += 1
    except KeyboardInterrupt:
        pass
    finally:
        if p.poll() is None:
            p.kill()
    return lines


STATUS_LINE = re.compile(r"status (\S+) (ok|off|waiting: (.*))$")


def test_results(lines, keys):
    """{key: "reached" | "waiting: <why>" | "no report"} from the device-log lines of one am test round.
    A switch that reached the game once in the round counts as reached."""
    out = {k: "no report" for k in keys}
    for line in lines:
        m = STATUS_LINE.search(line)
        if not m or m.group(1) not in out:
            continue
        key, state = m.group(1), m.group(2)
        if state == "ok":
            out[key] = "reached"
        elif state != "off" and out[key] != "reached":
            out[key] = "waiting: " + m.group(3)
    return out


def test_rounds(tweaks, section=None, keys=None):
    """[(section title, [keys])]: one round per menu section, or the chosen ones."""
    pinned = [t for t in tweaks.get("tweaks", []) if t.get("pins")]
    if keys:
        wanted = [k.strip() for k in keys.split(",") if k.strip()]
        unknown = [k for k in wanted if k not in {t["key"] for t in pinned}]
        if unknown:
            raise DevError(f"no pinned switch {', '.join(unknown)} in tweaks.json")
        return [("chosen", wanted)]
    rounds = {}
    for t in pinned:
        rounds.setdefault(t.get("section") or "Other", []).append(t["key"])
    if section:
        match = [r for r in rounds if r.lower() == section.lower()]
        if not match:
            raise DevError(f"no section {section!r}: {', '.join(rounds)}")
        return [(match[0], rounds[match[0]])]
    return list(rounds.items())


def newest_build(mod, signed=False):
    """The newest built .ipa of a mod in the builds folder: <mod>_<game>-mod<version>_accretion.ipa, or the
    _signed copy (that one installs on this PC's phone only)."""
    suffix = "_signed.ipa" if signed else "_accretion.ipa"
    found = sorted((am_root() / "builds").glob(f"{mod}_*{suffix}"), key=lambda f: f.stat().st_mtime)
    if not found:
        raise DevError(f"no {suffix} build of {mod} in {am_root() / 'builds'} (dev.py build {mod} --ipa)")
    return found[-1]


SHARE_ORIGIN = "https://dl.getaccretion.app/s/"


def share_name(meta):
    """The name a shared build saves as: "The-Wanderer-Plus-dev_1.0.15.ipa" (the download proxy's rule:
    letters, digits and dashes, "_", the mod's version, ".ipa")."""
    title = (meta.get("display_name") or meta.get("name") or "Mod").replace("+", " Plus ")
    words = re.findall(r"[A-Za-z0-9]+", title)
    name = "-".join(words)[:64].strip("-") or "Mod"
    version = re.sub(r"[^A-Za-z0-9.-]", "", str(meta.get("version", "0")))[:32] or "0"
    return f"{name}_{version}.ipa"


def share_call(mode, body):
    """The upload function's share modes (upload key); a refusal becomes a DevError with its message."""
    import urllib.error

    from publish import FUNCTIONS, call, upload_key

    try:
        return call(f"{FUNCTIONS}/upload?share={mode}", upload_key(), body)
    except urllib.error.HTTPError as e:
        try:
            message = json.loads(e.read()).get("message", "")
        except ValueError:
            message = ""
        raise DevError(f"share {mode}: {message or e.code}") from None


def share_days(args):
    days = args.days if args.days else round(args.months * 30.44) if args.months else 180
    if not 1 <= days <= 400:
        raise DevError("a link lasts 1 to 400 days (--days) or 1 to 13 months (--months)")
    return days


def cmd_share(args):
    """A private download link for one build, for the owner's own devices: never posted, never in a source.
    The .ipa goes to Accretion's private storage by its sha256 (uploaded once). The link,
    dl.getaccretion.app/s/<id>, saves the file under the mod's name and works until its expiry (180 days unless
    --days/--months, up to 400 days); --renew keeps the same link with a new expiry, --revoke ends it.
    --quick gives a plain 15-minute storage link instead (it saves under the sha256 name)."""
    import urllib.request

    import base

    if args.list:
        links = share_call("list", {})["links"]
        for link in links:
            state = "revoked" if link["revoked"] else ("expired" if link["expires_at"] < time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) else "on")
            print(f"  {SHARE_ORIGIN}{link['id']}  {link['name']}  {state} until {link['expires_at'][:10]}"
                  f"  {link['downloads']} downloads" + (f"  ({link['note']})" if link.get("note") else ""))
        print(f"{len(links)} share links")
        return 0
    if args.renew:
        done = share_call("renew", {"id": args.renew, "days": share_days(args)})
        print(f"==> {SHARE_ORIGIN}{done['id']} renewed: works until {done['expires_at'][:10]} (same link)")
        return 0
    if args.revoke:
        share_call("revoke", {"id": args.revoke})
        print(f"==> {SHARE_ORIGIN}{args.revoke} revoked: it no longer downloads")
        return 0
    if not args.mod:
        raise DevError("which mod? dev.py share <mod> (or --list, --renew <id>, --revoke <id>, <mod> --update <id>)")
    meta = read_json(mod_dir(args.mod) / "mod.json")
    ipa = Path(args.file) if args.file else newest_build(args.mod, args.signed)
    if not ipa.is_file():
        raise DevError(f"{ipa} not found")
    sha = base.sha256_of(ipa)
    stored_link = None
    from publish import FUNCTIONS, call, upload_key

    def storage_link():
        return call(f"{FUNCTIONS}/upload?base=get", upload_key(), {"sha256": sha})["signedUrl"]

    try:
        with urllib.request.urlopen(urllib.request.Request(storage_link(), headers={"Range": "bytes=0-0"}), timeout=60):
            stored_link = True
    except Exception:
        pass
    if not stored_link:
        print(f"==> uploading {ipa.name} ({ipa.stat().st_size / 1e6:.1f} MB) to private storage")
        base.put(str(ipa))
    if args.quick:
        print(f"==> {ipa.name}: 15-minute link (saves as the sha256; rename it to {ipa.name}):")
        print(f"    {storage_link()}")
        return 0
    name = share_name(meta)
    if args.update:  # same link, this build: the address and its expiry stay
        share_call("update", {"id": args.update, "sha256": sha, "name": name})
        print(f"==> {SHARE_ORIGIN}{args.update} now serves {ipa.name} (saves as {name}); same link, same expiry")
        return 0
    days = share_days(args)
    made = share_call("create", {"sha256": sha, "name": name, "days": days, "note": args.note or ipa.name})
    record = am_root() / "dev" / "shares.json"
    past = json.loads(record.read_text(encoding="utf-8")) if record.is_file() else []
    past.append({"id": made["id"], "mod": args.mod, "file": ipa.name, "sha256": sha, "name": name, "expires_at": made["expires_at"]})
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(past, indent=2), encoding="utf-8")
    print(f"==> {SHARE_ORIGIN}{made['id']}")
    print(f"    {ipa.name}, saves as {name}; works until {made['expires_at'][:10]} ({days} days)")
    print(f"    Same link, new expiry: dev.py share --renew {made['id']} --months 12   End it: dev.py share --revoke {made['id']}")
    print("    Private: anyone with the link can download until then, so keep it to your own devices.")
    return 0


def cmd_test(args):
    """Turns switches on from the PC (live_tweaks.json "test" block) one menu section at a time, reads their status
    lines from the device log, turns them off again, and records which reached the game. Reached means the pin
    found its object and wrote it; whether the cheat does what its title says is still for play (am mark)."""
    import uuid

    folder = mod_dir(args.mod)
    meta = read_json(folder / "mod.json")
    tweaks = load_tweaks(folder, meta)
    found = installed_bundles(meta["bundle_id"])
    if not found:
        raise DevError(f"{meta['bundle_id']} is not on the phone (dev.py run {args.mod})")
    rounds = test_rounds(tweaks, args.section, args.keys)
    titles = {t["key"]: t["title"] for t in tweaks["tweaks"]}
    results = {}
    print(f"==> {sum(len(k) for _, k in rounds)} switches in {len(rounds)} round(s) of {args.seconds} s. Keep the game "
          "open in a save (or in a fight, for combat switches); every switch goes off again after its round.")
    try:
        for title, keys in rounds:
            test_id = uuid.uuid4().hex[:8]
            seen = []
            stream_log(args.mod, args.seconds, seen.append, quiet=True,
                       on_ready=lambda: push_live(args.mod, found[0], {"id": test_id, "on": keys}))
            got = test_results(seen, keys)
            results.update(got)
            ok = sum(1 for v in got.values() if v == "reached")
            print(f"  {title}: {ok} of {len(keys)} reached the game")
            for k in keys:
                if got[k] != "reached":
                    print(f"      {k} ({titles.get(k, k)}): {got[k]}")
    finally:
        push_live(args.mod, found[0])  # no test block: the app turns the test switches off and restores values
    ok = sum(1 for v in results.values() if v == "reached")
    print(f"==> {ok} of {len(results)} reached the game; all test switches are off again")
    store = dev_learn.load(patterns_path())
    for t in tweaks["tweaks"]:
        if t["key"] in results and results[t["key"]] != "no report":
            dev_learn.record_tweak_pins(store, t, meta["target_bundle_id"],
                                        "reached" if results[t["key"]] == "reached" else "unreached")
    dev_learn.save(patterns_path(), store)
    record = am_root() / "dev" / f"{args.mod}-tests.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    past = json.loads(record.read_text(encoding="utf-8")) if record.is_file() else {}
    stamp = time.strftime("%Y-%m-%d %H:%M", time.gmtime())
    for k, v in results.items():
        past[k] = {"result": v, "app_version": meta.get("tested_app_version"), "date": stamp}
    record.write_text(json.dumps(past, indent=2), encoding="utf-8")
    print(f"==> {record}. Reached is not the same as working in play: try them, then am mark <key> --works")
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
    if (meta.get("base") or {}).get("ipa_url"):
        print(
            f"    note: base.ipa_url still points at the old file; set it to the {version} link (the build checks the sha256)"
        )
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
    # switches first: candidates the scan matched to pins that worked (or reached the game) in other games
    _result, scanned = scan_rows(args.mod)
    proven = [r for r in scanned if r.get("learned") and not r["has"]][: args.limit]
    if proven:
        print("Switches that worked elsewhere (no JIT):")
        for r in proven:
            lr = r["learned"]
            when = f" --when {r['when']}" if r["when"] != "always" else ""
            print(f"  {r['object']} {r['member']}  ({r['ctype']})  like \"{lr['title']}\" in {lr['class']}: "
                  f"{lr['works']} works, {lr['fails']} fails, {lr['reached']} reached")
            print(f"      am pin {args.mod} {r['class']}.{r['member']} --object \"{r['object']}\" --value {r['value']}{when} "
                  f"--title \"{lr['title']}\"")
        print()
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
    ) + dev_learn.record_tweak_pins(store, tweak, meta["target_bundle_id"], "works" if works else "fails")
    tweak["verified"] = {
        "result": "works" if works else "fails",
        "date": dev_learn.date.today().isoformat(),
        "app_version": meta.get("tested_app_version"),
    }
    write_json(folder / "tweaks.json", tweaks)
    dev_learn.save(patterns_path(), store)
    print(
        f"==> {args.key}: {'works' if works else 'fails'} on {meta.get('tested_app_version')}; patterns updated: {', '.join(touched) or 'none (no hooks or pins)'}"
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
    pins = 0
    for t in (tweaks or {}).get("tweaks", []):
        result = (t.get("verified") or {}).get("result")
        pins += len(dev_learn.record_tweak_pins(store, t, meta["target_bundle_id"], result))
    dev_learn.save(patterns_path(), store)
    print(
        f"==> learned {n} hooks and {pins} pins from {args.mod}; {len(store['patterns'])} method patterns, "
        f"{len(dev_learn.members(store))} member patterns in {patterns_path()}"
    )
    return 0


# ---------------------------------------------------------------- main


def local_ipas():
    """Decrypted-looking .ipa files in intake/ and Downloads: path, bundle, name, version, size."""
    rows = []
    for folder in (am_root() / "intake", Path.home() / "Downloads"):
        if not folder.is_dir():
            continue
        for ipa in sorted(folder.glob("*.ipa")):
            try:
                info, _ = ipa_info(ipa)
            except Exception:  # noqa: BLE001 - a broken file in Downloads is skipped
                continue
            rows.append(
                {
                    "path": str(ipa),
                    "bundle": info.get("CFBundleIdentifier"),
                    "name": info.get("CFBundleDisplayName") or info.get("CFBundleName"),
                    "version": info.get("CFBundleShortVersionString"),
                    "size": ipa.stat().st_size,
                    "fetched": ipa.with_name(ipa.name + ".source.json").is_file(),
                }
            )
    return rows


def mods_by_target():
    """{game bundle ID: [{name, title}]} for every mod folder."""
    out = {}
    for path in sorted((ROOT / "mods").glob("*/mod.json")):
        meta = read_json(path)
        if meta.get("target_bundle_id"):
            out.setdefault(meta["target_bundle_id"], []).append(
                {"name": path.parent.name, "title": meta.get("display_name") or meta["name"]}
            )
    return out


def mark_games(rows):
    """Each game with our mods of it and a decrypted copy waiting, if any."""
    mods = mods_by_target()
    have = {}
    for ipa in local_ipas():
        have.setdefault(ipa["bundle"], ipa)
    for r in rows:
        r["mods"] = mods.get(r["bundle"], [])
        r["ipa"] = have.get(r["bundle"])
    return rows


def games_list(kind, region="us", term=None, bundle=None, games_only=False):
    """A list for the Games tab and `dev.py games`: charts, similar, search, or like one game."""
    if not re.fullmatch(r"[a-z]{2}", region or ""):
        raise DevError("region: a two-letter store code like us")
    try:
        if kind == "similar":
            rows = games.similar({b: ms[0]["title"] for b, ms in mods_by_target().items()}, region)
        elif kind == "search":
            rows = games.search(term, region, games_only=games_only)
        elif kind == "like":
            found = games.lookup(bundles=[bundle], region=region) if bundle else []
            if not found:
                raise DevError(f"like: {bundle or 'a bundle ID'} is not in the {region.upper()} App Store")
            rows = games.similar({bundle: found[0]["trackName"]}, region)
        else:
            rows = games.chart(kind, region)
    except games.GamesError as e:
        raise DevError(str(e)) from e
    return {"list": kind, "region": region, "games": mark_games(rows)}


def games_info(bundle, region="us"):
    if not re.fullmatch(r"[a-z]{2}", region or ""):
        raise DevError("region: a two-letter store code like us")
    try:
        return mark_games([games.info(bundle, region)])[0]
    except games.GamesError as e:
        raise DevError(str(e)) from e


def scan_rows(mod, area=None, find=None, flagged=False):
    folder = mod_dir(mod)
    meta = read_json(folder / "mod.json")
    store = dev_learn.load(patterns_path())
    game = meta.get("target_bundle_id")
    result = dump_scan.scan(dump_path(mod), load_tweaks(folder, meta),
                            learned=lambda name, ctype, cls: dev_learn.member_boost(store, name, ctype, game, cls),
                            assemblies=meta.get("scan_assemblies"))
    rows = (result["flagged"] if flagged else result["rows"]) + result.get("inventory", [])
    if area:
        rows = [r for r in rows if r["area"].lower().startswith(area.lower())]
    if find:
        rx = re.compile(find, re.IGNORECASE)
        rows = [r for r in rows if rx.search(f"{r['class']}.{r['member']}")]
    return result, rows


def report_for(target):
    """(facts, scan or None) for a mod name or an .ipa path."""
    path = Path(target)
    mod = None
    if not path.is_file():
        if not (ROOT / "mods" / target / "mod.json").is_file():
            raise DevError(f"{target} is neither an .ipa nor a mod")
        mod = target
        path = find_base(mod, read_json(mod_dir(mod) / "mod.json"))
    try:
        facts = moddability.inspect_ipa(path)
    except (ValueError, KeyError, zipfile.BadZipFile) as e:
        raise DevError(f"can't read {path}: {e}") from e
    if mod is None:  # an .ipa: use the dump of a mod for the same game, if one exists
        mod = next((m["name"] for m in mods_by_target().get(facts["bundle"], [])), None)
    scan = None
    if mod:
        try:
            meta = read_json(mod_dir(mod) / "mod.json")
            scan = dump_scan.scan(dump_path(mod), load_tweaks(mod_dir(mod), meta), assemblies=meta.get("scan_assemblies"))
        except DevError:
            scan = None
    return facts, scan


POST_CREDITS = ["RaZza_0X", "[url=https://getaccretion.app]Accretion[/url]"]


def cmd_post(args):
    """The release post for a fully validated mod, from <AM_ROOT>/templates/release_post.md, written locally only."""
    folder = mod_dir(args.mod)
    meta = read_json(folder / "mod.json")
    tweaks = load_tweaks(folder, meta)
    app_version = meta.get("tested_app_version", "")
    skip = set(filter(None, (args.skip or "").split(",")))
    items = [t for t in tweaks.get("tweaks", []) if t.get("kind", "switch") in ("switch", "slider") and t["key"] not in skip]
    ok = [t for t in items if (t.get("verified") or {}).get("result") == "works"
          and (t.get("verified") or {}).get("app_version") == app_version]
    left = [t for t in items if t not in ok]
    if left and not (args.draft or args.partial):
        raise DevError(
            f"not every switch is marked works on {app_version} yet ({len(ok)} of {len(items)}): "
            + ", ".join(t["key"] for t in left) + ". Test them (am mark <mod> <key> --works), or --draft"
        )
    template = am_root() / "templates" / "release_post.md"
    if not template.is_file():
        raise DevError(f"no template at {template}: put the release post template there")
    try:
        app = (games.lookup(bundles=[meta["target_bundle_id"]]) or [{}])[0]
    except games.GamesError:
        app = {}
    state = load_state(args.mod)
    before = state.get("posted") or {}
    new_keys = set() if not before else {t["key"] for t in ok} - set(before.get("features", []))
    shown = ok if not args.draft else items
    if args.partial and left:  # owner, 2026-10-22: publish with what is confirmed and what should work, labelled
        lines = [f"- {t['title']}" + (" (NEW)" if t["key"] in new_keys else "") for t in ok]
        lines += ["", "Should work (applies in the game, not yet confirmed in play):"]
        lines += [f"- {t['title']}" for t in left]
        shown = []
    features = "\n".join(f"- {t['title']}" + (" (NEW)" if t["key"] in new_keys else "") for t in shown)
    if args.partial and left:
        features = "Confirmed working:\n" + "\n".join(lines)
    text = template.read_text(encoding="utf-8")
    for k, v in {
        "logo": app.get("artworkUrl512") or "[APP LOGO LINK]",
        "app": app.get("trackName") or meta.get("display_name") or meta["name"],
        "seller": app.get("sellerName") or "?",
        "bundle": meta["target_bundle_id"],
        "store_link": app.get("trackViewUrl") or "?",
        "features": features or "- ",
        "credits": "\n".join(f"- {c}" for c in meta.get("post_credits", POST_CREDITS)),
    }.items():
        text = text.replace("{" + k + "}", v)
    # the mod's screenshot links (Mod Studio: Phone screenshot), the live ones, newest first: {screenshots}, or in
    # place of the template's "LINK ONLY" line
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    links = "\n".join(s["url"] for s in state.get("shots", []) if s.get("expires_at", "") > now)
    if links:
        text = text.replace("{screenshots}", links) if "{screenshots}" in text else re.sub(
            r"(?m)^LINK ONLY\s*$", lambda _m: links, text)
    else:
        text = text.replace("{screenshots}", "LINK ONLY")
    head = []
    if args.draft and left:
        head.append(f"DRAFT, not validated yet: {', '.join(t['key'] for t in left)}")
    if before and before.get("version") != meta["version"]:
        head.append(f"🔄 Update: {meta.get('display_name') or meta['name']} {before.get('version')} → {meta['version']} "
                    f"(game {app_version})" + (f", {len(new_keys)} new" if new_keys else ""))
    if head:
        text = "\n".join(head) + "\n\n" + text
    out = Path(args.output) if args.output else am_root() / "posts" / (
        f"{args.mod}-{meta['version']}" + ("-draft" if args.draft and left else "") + ".txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    if not (args.draft and left):  # the validated post of this version: the next one is an update of it
        state["posted"] = {"version": meta["version"], "app_version": app_version, "features": [t["key"] for t in ok]}
        save_state(args.mod, state)
    print(text)
    print(f"==> written to {out} (local only: nothing is posted)")
    return 0


def cmd_report(args):
    facts, scan = report_for(args.target)
    if args.json:
        level, lines = moddability.verdict(facts, scan)
        print(json.dumps(dict(facts, verdict=level, notes=lines), indent=2))
    else:
        print(moddability.report_text(facts, scan))
    return 0


def cmd_start(args):
    """A new game in one go: a decrypted copy, the report, the mod folder and its dump, then the scan."""
    target = args.target
    if Path(target).is_file():
        ipa = Path(target)
    else:
        have = [r for r in local_ipas() if r["bundle"] == target]
        if have:
            ipa = Path(sorted(have, key=lambda r: version_tuple(r["version"] or "0"))[-1]["path"])
            print(f"==> decrypted copy already here: {ipa}")
        else:
            print(f"==> getting a decrypted copy of {target}")
            try:
                ipa = ipa_fetch.fetch(am_root(), target, args.region, "iphone", source=args.source)
            except ipa_fetch.FetchError as e:
                raise DevError(str(e)) from e
    facts = moddability.inspect_ipa(ipa)
    level, _ = moddability.verdict(facts)
    print(moddability.report_text(facts))
    if level == "not supported":
        raise DevError("stopped: this game can't be modded with our tools (see the verdict)")
    slug = re.sub(r"[^a-z0-9]+", "-", (facts["name"] or facts["bundle"]).lower()).strip("-") or "game"
    name = args.name or f"{slug}-plus"
    existing = [m["name"] for m in mods_by_target().get(facts["bundle"], [])]
    if existing and not args.name:
        raise DevError(f"{facts['bundle']} already has mods/{existing[0]}: open it, or pass --name for another one")
    print(f"==> making mods/{name} and its dump (a minute or two)")
    cmd_new(argparse.Namespace(name=name, ipa=str(ipa), title=args.title or f"{facts['name']}+",
                               category="games", no_dump=False))
    meta_ = read_json(mod_dir(name) / "mod.json")
    scan = dump_scan.scan(dump_path(name), load_tweaks(mod_dir(name), meta_), assemblies=meta_.get("scan_assemblies"))
    level, lines = moddability.verdict(facts, scan)
    print(f"==> verdict with the dump: {level.upper()}")
    for line in lines:
        print(f"    - {line}")
    print("==> best no-JIT candidates (Studio: Scan tab; Explain and rank with Claude):")
    for r in scan["rows"][:12]:
        when = f" --when {r['when']}" if r["when"] != "always" else ""
        print(f"    {r['area']:<16} am pin {name} {r['class']}.{r['member']} --object \"{r['object']}\" --value {r['value']}{when}")
    print(f"==> next: pick switches (am pin ... --title), then am run {name}")
    return 0


def cmd_scan(args):
    result, rows = scan_rows(args.mod, args.area, args.find, args.flagged)
    rows = rows[: args.limit]
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if args.text:
        print(dump_scan.as_text(rows, args.limit))
        return 0
    print(f"{len(result['rows'])} candidates in {result['classes']} game classes; {len(result['flagged'])} flagged")
    for r in rows:
        tag = f"FLAG: {r['flag']}" if r.get("flag") else ("in the mod" if r["has"] else f"score {r['score']}")
        hints = [h for h, on in (("lower is better", r.get("direction") == "lower"), ("kept in the save", r.get("saved"))) if on]
        print(f"  {r['area']:<16} {r['object'] + ' ' + r['member']:<58} {r['ctype']:<8} {tag}"
              + (f"  ({', '.join(hints)})" if hints else ""))
        if r.get("learned"):
            lr = r["learned"]
            print(f"      learned: like \"{lr['title']}\" ({lr['works']} works, {lr['fails']} fails, {lr['reached']} reached elsewhere)")
        if not r.get("flag") and not r["has"]:
            when = f" --when {r['when']}" if r["when"] != "always" else ""
            obj = f' --object "{r["object"]}"'
            print(f"      am pin {args.mod} {r['class']}.{r['member']}{obj} --value {r['value']}{when} --title \"...\"")
    return 0


def cmd_games(args):
    what, rest = args.what, list(args.rest)
    if what == "info":
        if not rest:
            raise DevError("games info <bundle-id>")
        g = games_info(rest[0], args.region)
        if args.json:
            print(json.dumps(g, indent=2, ensure_ascii=False))
            return 0
        print(f"{g['name']}  ({g['bundle']})")
        print(f"  {g['seller']} · {g['price']} · version {g['version']} · iOS {g['min_ios']}+ · {g['age']}"
              + (f" · {g['size'] / 1e6:.0f} MB" if g["size"] else ""))
        print(f"  rating {g['rating']} ({g['ratings']}) · genres {', '.join(g['genres']) or '-'} · updated {g['updated']}")
        print(f"  themes {', '.join(g['themes']) or '-'}" + ("  · ONLINE/multiplayer" if g["online"] else ""))
        for m in g["mods"]:
            print(f"  our mod: {m['title']} (mods/{m['name']})")
        if g["ipa"]:
            print(f"  decrypted copy: {g['ipa']['path']} ({g['ipa']['version']})")
        print(f"  {g['url']}")
        print()
        print("  " + g["description"][:600].replace("\n", "\n  ") + ("…" if len(g["description"]) > 600 else ""))
        return 0
    if what == "search":
        if not rest:
            raise DevError("games search <words or bundle ID>")
        data = games_list("search", args.region, " ".join(rest), games_only=args.games)
    elif what == "like":
        if not rest:
            raise DevError("games like <bundle-id>")
        data = games_list("like", args.region, bundle=rest[0])
    else:
        data = games_list(what, args.region)
    rows = data["games"][: args.limit]
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    hidden = len(data["games"]) - len(rows)
    for i, g in enumerate(rows, 1):
        tags = [f"our mod {m['name']}" for m in g["mods"]] + (["ipa ready"] if g["ipa"] else [])
        tags += ["online"] if g["online"] else []
        tags += [f"like {t}" for t in g["like"]]
        print(f"{i:>3}. {g['name'][:44]:<44} {g['bundle'][:42]:<42} {g['rating']:>3} {g['ratings']:>8}  {g['price']:<6}"
              + (f"  [{', '.join(tags)}]" if tags else ""))
    if not rows:
        print("nothing found")
    if hidden > 0 and what != "search":
        print(f"({hidden} more: raise --limit to see them)")
    return 0


def cmd_fetch(args):
    client = ipa_fetch.Client(am_root())
    try:
        vault = ipa_fetch.Vault(client.base)
        if args.logout or args.forget_login:
            client.logout()
            print(f"signed out of {client.base}")
            if args.forget_login:
                vault.forget()
                print("saved login removed from Windows Credential Manager")
            return 0
        if args.login or args.save_login:
            import getpass

            email = args.email or input(f"{client.base} account e-mail: ").strip()
            password = getpass.getpass(
                "password (kept in Windows Credential Manager): " if args.save_login
                else "password (sent to the site only, not saved): "
            )
            user = client.login(email, password)  # only a login that works is saved
            if args.save_login:
                vault.save(email, password)
                print(f"saved in Windows Credential Manager as {vault.target}; fetch signs in with it when needed")
            print(
                f"signed in as {user.get('email', email)}"
                + ("" if user.get("approved", True) else " (waiting for the site admin's approval)")
            )
            return 0
        if args.import_file:
            ipa_fetch.import_ipa(am_root(), args.import_file, region=args.region)
            return 0
        if args.account or not (args.target or args.mod):
            a = client.account()
            print(f"site:      {a['site']} ({'up' if a['up'] else 'not answering'}"
                  + (", paused: no new decrypts" if a["paused"] else "") + ")")
            u = a["user"]
            if not u:
                print("account:   not signed in (cached apps still download)")
            elif not u.get("approved"):
                print(f"account:   {u.get('email')} (waiting for the site admin's approval)")
            else:
                left = "no daily limit" if u.get("limit") is None else f"{u.get('remaining')} of {u.get('limit')} new decrypts left today"
                print(f"account:   {u.get('email')} ({left})")
            return 0
        target = args.target or read_json(mod_dir(args.mod) / "mod.json")["target_bundle_id"]
        ipa_fetch.fetch(
            am_root(), target, args.region, args.platform, args.force,
            browser=not args.no_browser, source=args.source,
        )
    except ipa_fetch.FetchError as e:
        raise DevError(str(e)) from e
    return 0


def build_parser():
    """dev.py's argument parser (Mod Studio's Console reads the command list from it)."""
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
    s.add_argument("--fields", action="store_true", help="search field names (Namespace.Class.field) instead")
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

    s = sub.add_parser(
        "pin",
        help="no-JIT switch: write a field or call a setter on live objects every tick",
    )
    s.add_argument("mod")
    s.add_argument("target", help="Class.field or Class.set_Property")
    s.add_argument(
        "--object",
        help="Class.staticField (default: the class's instance field), Class, or Class[] for every live object",
    )
    s.add_argument(
        "--value",
        help="number, true/false, peak (highest seen), getter:get_HealthMax, field:HealthMax, or slider (with --slider)",
    )
    s.add_argument(
        "--when",
        choices=gen_tweaks.PIN_WHEN,
        default="always",
        help="write always, or only when the current value is lower/higher",
    )
    s.add_argument(
        "--threshold",
        type=float,
        help="with --when: compare against this instead of the value (set 99 when below 50)",
    )
    s.add_argument(
        "--via",
        help="0-arg getter on the object that returns the real target (StatHandler.instance via get_playerStats)",
    )
    s.add_argument(
        "--class",
        dest="cls",
        help="the target's class when it differs (FightCompanion for PlayerCompanion.Damage)",
    )
    s.add_argument(
        "--multiply",
        action="store_true",
        help="field = base * value (base tracked), for a damage multiplier slider",
    )
    s.add_argument(
        "--field",
        action="store_true",
        help="treat a set_ name as a field, not a setter",
    )
    s.add_argument("--as", dest="key")
    s.add_argument("--title")
    s.add_argument("--slider", nargs=3, type=float, metavar=("MIN", "MAX", "DEFAULT"))
    s.add_argument("--to")
    s.set_defaults(fn=cmd_pin)

    s = sub.add_parser(
        "call", help="no-JIT switch: call a method on live objects every N seconds"
    )
    s.add_argument("mod")
    s.add_argument("target", help="Class.Method")
    s.add_argument(
        "--args", nargs="*", default=[], help="argument values (numbers, true/false)"
    )
    s.add_argument(
        "--every", type=float, default=10, help="seconds between calls (0: every tick)"
    )
    s.add_argument(
        "--object",
        help="Class.staticField (default: the class's instance field), Class, or Class[]",
    )
    s.add_argument("--as", dest="key")
    s.add_argument("--title")
    s.add_argument("--to")
    s.set_defaults(fn=cmd_call)

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
    s.add_argument("--no-check", action="store_true", help="build even when a pin fails the dump check")
    s.set_defaults(fn=cmd_build)

    s = sub.add_parser("sign")
    s.add_argument("ipa")
    s.add_argument("--install", action="store_true")
    s.add_argument(
        "--launch", action="store_true", help="with --install: open the app afterwards"
    )
    s.add_argument("-o", "--out")
    s.set_defaults(fn=cmd_sign)

    s = sub.add_parser("install")
    s.add_argument("ipa")
    s.set_defaults(fn=cmd_install)

    s = sub.add_parser("log")
    s.add_argument("mod")
    s.add_argument("--seconds", type=int, default=60)
    s.add_argument(
        "--launch",
        action="store_true",
        help="(re)open the installed app first, so the hook report is in the capture",
    )
    s.set_defaults(fn=cmd_log)

    s = sub.add_parser(
        "run",
        help="build .ipa, sign + install with the Apple ID, open it, read the log",
    )
    s.add_argument("mod")
    s.add_argument("--bump", action="store_true")
    s.add_argument("--base")
    s.add_argument("--seconds", type=int, default=60)
    s.set_defaults(fn=cmd_run)

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

    s = sub.add_parser("push", help="switches to the phone without rebuilding (live_tweaks.json)")
    s.add_argument("mod")
    s.add_argument("--off", action="store_true", help="remove the pushed file: the build's own switches again")
    s.add_argument("--launch", action="store_true", help="(re)open the game after pushing")
    s.add_argument("--no-check", action="store_true", help="push even when a pin fails the dump check")
    s.set_defaults(fn=cmd_push)

    s = sub.add_parser("share", help="a private, renewable download link for a build (your own devices)")
    s.add_argument("mod", nargs="?")
    s.add_argument("--file", help="this .ipa instead of the newest build")
    s.add_argument("--signed", action="store_true", help="the copy signed for this PC's phone")
    s.add_argument("--days", type=int, help="how long the link works (default 180, at most 400)")
    s.add_argument("--months", type=int, help="the same in months (6, 12)")
    s.add_argument("--note", help="a note kept with the link (default: the file name)")
    s.add_argument("--quick", action="store_true", help="a plain 15-minute storage link instead")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--list", action="store_true", help="every share link, its expiry and downloads")
    g.add_argument("--renew", metavar="ID", help="same link, new expiry (--days or --months, from today)")
    g.add_argument("--revoke", metavar="ID", help="end a link for good")
    g.add_argument("--update", metavar="ID", help="same link, the mod's newest build (or --file)")
    s.set_defaults(fn=cmd_share)

    s = sub.add_parser("test", help="switches on from the PC, a section at a time: which reach the game")
    s.add_argument("mod")
    s.add_argument("--section", help="one menu section only (\"Combat\")")
    s.add_argument("--keys", help="these switches only, comma separated")
    s.add_argument("--seconds", type=int, default=20, help="how long each round listens (default 20)")
    s.set_defaults(fn=cmd_test)

    s = sub.add_parser("post", help="the release post, once every switch is marked works (written locally)")
    s.add_argument("mod")
    s.add_argument("--draft", action="store_true", help="write it anyway, marked DRAFT with what is not validated")
    s.add_argument("--partial", action="store_true", help="publishable now: features as confirmed + should work")
    s.add_argument("--skip", help="keys left out of the post, comma separated (switches kept off on purpose)")
    s.add_argument("-o", "--output")
    s.set_defaults(fn=cmd_post)

    s = sub.add_parser("report", help="is a game worth modding: engine, decrypted, online, ads, purchases")
    s.add_argument("target", help="a mod name or an .ipa file")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("start", help="a new game in one go: copy, report, mod folder + dump, scan")
    s.add_argument("target", help="the game's bundle ID or a decrypted .ipa")
    s.add_argument("--name", help="mod folder name (default: <app>-plus)")
    s.add_argument("--title", help="menu title (default: <app>+)")
    s.add_argument("--region", default="us", choices=ipa_fetch.REGIONS)
    s.add_argument("--source", default="34306", choices=list(ipa_fetch.SOURCES))
    s.set_defaults(fn=cmd_start)

    s = sub.add_parser("scan", help="no-JIT switch candidates in the dump, scored; sold/online flagged")
    s.add_argument("mod")
    s.add_argument("--area", help="Survival, Combat, Progress, Items, Pet, Exploration, Other")
    s.add_argument("--find", help="regex on Class.member")
    s.add_argument("--limit", type=int, default=40)
    s.add_argument("--flagged", action="store_true", help="list what must not become a switch, and why")
    s.add_argument("--json", action="store_true")
    s.add_argument("--text", action="store_true", help="one line per candidate (for a prompt)")
    s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("games", help="App Store games: charts, search, like a game, one game's details")
    s.add_argument(
        "what", nargs="?", default="similar",
        choices=["similar", "search", "like", "info", *games.CHARTS],
    )
    s.add_argument("rest", nargs="*", help="search words, or the bundle ID for like / info")
    s.add_argument("--region", default="us")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--all", action="store_true", help="compatibility flag; all game topics are included by default")
    s.add_argument("--games", action="store_true", help="search: games only")
    s.add_argument("--json", action="store_true", help="machine-readable output")
    s.set_defaults(fn=cmd_games)

    s = sub.add_parser(
        "fetch", help="a decrypted .ipa from the decrypt site (AM_DECRYPT_URL) into intake/"
    )
    s.add_argument("target", nargs="?", help="bundle ID or app name")
    s.add_argument("--mod", help="the app a mod targets (its target_bundle_id)")
    s.add_argument("--region", default="us", choices=ipa_fetch.REGIONS)
    s.add_argument("--platform", default="iphone", choices=ipa_fetch.PLATFORMS)
    s.add_argument("--force", action="store_true", help="decrypt again even if the site has it")
    s.add_argument("--login", action="store_true")
    s.add_argument("--email")
    s.add_argument("--logout", action="store_true")
    s.add_argument("--save-login", action="store_true", help="sign in and keep the login in Credential Manager")
    s.add_argument("--forget-login", action="store_true", help="sign out and remove the saved login")
    s.add_argument("--account", action="store_true")
    s.add_argument("--import", dest="import_file", help="check a downloaded .ipa and move it into intake/")
    s.add_argument("--no-browser", action="store_true", help="fail instead of opening the site for a captcha")
    s.add_argument(
        "--source", default="34306", choices=list(ipa_fetch.SOURCES),
        help="34306: decrypt.34306.lol (API, captcha -> browser); decrypt.day, armconverter: in the browser",
    )
    s.set_defaults(fn=cmd_fetch)
    return p


def main(argv=None):
    p = build_parser()
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
