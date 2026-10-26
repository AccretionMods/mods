"""One dump per game build, shared by every mod and every copy of that build.

A dump is keyed by the two files Il2CppDumper reads, UnityFramework and global-metadata.dat, as the .ipa's zip
directory lists them (CRC-32 and size of the uncompressed bytes): a copy of the same build fetched again, from
another source, or used by a second mod finds its dump without extracting anything or running the dumper.
Already extracted files give the same key (zlib.crc32 of the bytes), so dumps made before this cache existed are
registered where they are (`dev.py dumps --index`).

The index is <AM_ROOT>/work/dumps/index.json: {key: {"dir", "bundle", "version", "dumper", "structs", "made"}}.
New dumps go to <AM_ROOT>/work/dumps/<bundle>-<version>-<key8>/ (UnityFramework, global-metadata.dat, out/).

Dumps are lean by default: Il2CppDumper runs from a copy of its folder whose config.json has GenerateStruct off,
so il2cpp.h and script.json (about 500 MB, read by nothing here) are not written; dump.cs is byte-for-byte the same
(Vampire Survivors 1.16.107: 31 s and 184 MB instead of 48 s and 678 MB). `dump --structs` writes them.
"""

import hashlib
import json
import os
import re
import shutil
import time
import zipfile
import zlib
from pathlib import Path

FRAMEWORK = "Frameworks/UnityFramework.framework/UnityFramework"
METADATA = "Data/Managed/Metadata/global-metadata.dat"


def root_dir(am_root):
    return Path(am_root) / "work" / "dumps"


def index_path(am_root):
    return root_dir(am_root) / "index.json"


def load(am_root):
    p = index_path(am_root)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except ValueError:
        return {}


def save(am_root, index):
    p = index_path(am_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def make_key(fw_crc, fw_size, md_crc, md_size):
    """The dumper follows from the metadata's version, which these bytes already fix."""
    text = f"{fw_crc:08x}:{fw_size}:{md_crc:08x}:{md_size}"
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def app_of(z):
    """The .ipa's "Payload/X.app" folder."""
    return next((n.split("/", 2)[0] + "/" + n.split("/", 2)[1] for n in z.namelist()
                 if n.startswith("Payload/") and n.split("/")[1].endswith(".app")), None)


def key_of_ipa(ipa, app=None):
    """The build's key from the .ipa's zip directory (no extraction), or None when it is not a Unity game."""
    with zipfile.ZipFile(ipa) as z:
        app = app or app_of(z)
        try:
            fw, md = z.getinfo(f"{app}/{FRAMEWORK}"), z.getinfo(f"{app}/{METADATA}")
        except KeyError:
            return None
    return make_key(fw.CRC, fw.file_size, md.CRC, md.file_size)


def _crc(path):
    crc, size = 0, 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            crc, size = zlib.crc32(chunk, crc), size + len(chunk)
    return crc & 0xFFFFFFFF, size


def key_of_files(binary, metadata):
    """The same key from extracted UnityFramework and global-metadata.dat (dumps made before the cache)."""
    return make_key(*_crc(binary), *_crc(metadata))


def find(am_root, key):
    """The cached dump folder for key, when its dump.cs is still there."""
    entry = load(am_root).get(key)
    if entry and (Path(entry["dir"]) / "out" / "dump.cs").is_file():
        return entry
    return None


def register(am_root, key, folder, bundle, version, dumper, structs):
    index = load(am_root)
    index[key] = {"dir": str(folder), "bundle": bundle, "version": version, "dumper": dumper,
                  "structs": bool(structs), "made": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    save(am_root, index)
    return index[key]


def new_dir(am_root, bundle, version, key):
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", f"{bundle}-{version}").strip("-") or "game"
    return root_dir(am_root) / f"{safe}-{key[:8]}"


def lean_dumper(folder):
    """A copy of the dumper folder with GenerateStruct off, made once next to it (the tool itself is untouched)."""
    folder = Path(folder)
    lean = folder.with_name(folder.name + "-lean")
    config = folder / "config.json"
    if not config.is_file():
        return folder  # an install without config.json: run it as it is
    source = json.loads(config.read_text(encoding="utf-8-sig"))
    want = dict(source, GenerateStruct=False)
    current = lean / "config.json"
    stale = not current.is_file() or (folder / "Il2CppDumper.dll").stat().st_mtime > current.stat().st_mtime
    if stale:
        if lean.exists():
            shutil.rmtree(lean)
        shutil.copytree(folder, lean)
        current.write_text(json.dumps(want, indent=2) + "\n", encoding="utf-8")
    return lean


def index_existing(am_root, states):
    """Register dumps made before the cache, in place (nothing moved or deleted). states: {mod: dev state}.
    Returns [(key, folder, how)]: how is "extracted files", "the base .ipa" or "same build as <folder>"."""
    index, added = load(am_root), []
    for mod, state in sorted(states.items()):
        folder = Path(state.get("dump_dir") or "")
        if not state.get("dump_dir") or not (folder / "out" / "dump.cs").is_file():
            continue
        binary, metadata = folder / "UnityFramework", folder / "global-metadata.dat"
        key = how = None
        if binary.is_file() and metadata.is_file():
            key, how = key_of_files(binary, metadata), "extracted files"
        elif state.get("base_ipa") and Path(state["base_ipa"]).is_file():
            key, how = key_of_ipa(state["base_ipa"]), "the base .ipa"
        if not key:
            continue
        known = index.get(key)
        if known and Path(known["dir"]) != folder and (Path(known["dir"]) / "out" / "dump.cs").is_file():
            added.append((key, folder, f"same build as {known['dir']}"))
            continue
        if not known:
            index[key] = {"dir": str(folder), "bundle": "", "version": state.get("app_version", ""), "dumper": "",
                          "structs": (folder / "out" / "il2cpp.h").is_file(), "made": "before the cache"}
            added.append((key, folder, how))
    save(am_root, index)
    return added
