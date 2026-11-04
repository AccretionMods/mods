#!/usr/bin/env python3
"""Report how each il2cpp_* symbol resolves in a local IPA or Mach-O binary.

The runtime resolves every il2cpp_* export through a short ladder:

  export   dlsym(RTLD_DEFAULT, name)   -- present in the Mach-O export trie
  symtab   LC_SYMTAB lookup             -- defined in the image but not exported (fallback)
  import   undefined in this image      -- imported from elsewhere; dlsym may still find it
  missing  absent                       -- stripped; not resolvable without scanning

Run this on your own builds to see which step each symbol takes. It only reads
load commands; it never loads or executes the binary. This is the offline test
bed for the fallback: run it on an IPA whose ground truth you know, then run the
same IPA through the device build and compare.

Usage:
  python3 scripts/inspect_il2cpp_symbols.py path/to/app.ipa
  python3 scripts/inspect_il2cpp_symbols.py path/to/UnityFramework --json
  python3 scripts/inspect_il2cpp_symbols.py path/to/app.ipa --require-all
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import zipfile
from pathlib import Path

# The il2cpp_* functions the runtime resolves: am_il2cpp.c's required set plus
# the optional hooks and every data-layer call in AMIl2cppData.m.
REQUIRED = (
    "il2cpp_domain_get",
    "il2cpp_domain_get_assemblies",
    "il2cpp_assembly_get_image",
    "il2cpp_image_get_name",
    "il2cpp_class_from_name",
    "il2cpp_class_get_method_from_name",
    "il2cpp_get_corlib",
)

SYMBOLS = (
    *REQUIRED,
    "il2cpp_init",
    "il2cpp_class_get_nested_types",
    "il2cpp_class_get_name",
    "il2cpp_image_get_class_count",
    "il2cpp_image_get_class",
    "il2cpp_class_is_subclass_of",
    "il2cpp_method_get_flags",
    "il2cpp_domain_assembly_open",
    "il2cpp_class_get_field_from_name",
    "il2cpp_field_static_get_value",
    "il2cpp_field_get_offset",
    "il2cpp_class_get_type",
    "il2cpp_type_get_object",
    "il2cpp_runtime_invoke",
    "il2cpp_object_unbox",
    "il2cpp_string_chars",
    "il2cpp_string_length",
    "il2cpp_string_new",
    "il2cpp_class_get_parent",
    "il2cpp_field_get_flags",
    "il2cpp_field_get_parent",
    "il2cpp_runtime_class_init",
    "il2cpp_class_get_static_field_data",
    "il2cpp_object_get_class",
    "il2cpp_class_is_assignable_from",
    "il2cpp_class_is_valuetype",
    "il2cpp_field_get_type",
    "il2cpp_type_get_type",
    "il2cpp_class_from_type",
    "il2cpp_field_get_value",
    "il2cpp_method_get_return_type",
    "il2cpp_object_new",
    "il2cpp_gchandle_new",
    "il2cpp_gchandle_free",
    "il2cpp_class_get_element_class",
)

MH_MAGIC_64 = 0xFEEDFACF
MH_CIGAM_64 = 0xCFFAEDFE
FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF

LC_SYMTAB = 0x02
LC_SEGMENT_64 = 0x19
LC_DYLD_INFO = 0x22
LC_DYLD_INFO_ONLY = 0x80000022
LC_DYLD_EXPORTS_TRIE = 0x80000033

N_STAB = 0xE0
N_TYPE = 0x0E
N_EXT = 0x01
N_UNDF = 0x00
N_SECT = 0x0E

EXPORT_FLAG_REEXPORT = 0x08
EXPORT_FLAG_STUB_AND_RESOLVER = 0x10

CPU_NAMES = {
    0x0100000C: "arm64",
    0x0100000D: "arm64_32",
    0x01000007: "x86_64",
    0x0000000C: "arm",
    0x00000007: "i386",
}


class MachOError(Exception):
    """The file is not a Mach-O image this tool can read."""


def _uleb(data: bytes, off: int) -> tuple[int, int]:
    """Read a ULEB128 and return (value, next_offset)."""
    value = shift = 0
    while True:
        if off >= len(data):
            raise MachOError("truncated ULEB128")
        byte = data[off]
        off += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, off
        shift += 7
        if shift > 63:
            raise MachOError("ULEB128 too long")


def _cstr(data: bytes, off: int, limit: int) -> tuple[str, int]:
    """Read a NUL-terminated UTF-8 string in [off, limit)."""
    end = data.find(b"\x00", off, limit)
    if end < 0:
        raise MachOError("unterminated string")
    return data[off:end].decode("utf-8", "replace"), end + 1


def parse_export_trie(trie: bytes) -> set[str]:
    """Names exported by the dyld export trie."""
    names: set[str] = set()
    if not trie:
        return names

    def walk(node: int, prefix: str) -> None:
        if node >= len(trie):
            raise MachOError("export trie node out of range")
        size, off = _uleb(trie, node)
        if size:
            payload_end = off + size
            if payload_end > len(trie):
                raise MachOError("export trie terminal out of range")
            flags, _ = _uleb(trie, off)
            if flags & EXPORT_FLAG_REEXPORT:
                pass  # ordinal + import name follow; the name is still exported
            elif flags & EXPORT_FLAG_STUB_AND_RESOLVER:
                pass
            names.add(prefix)
            off = payload_end
        if off >= len(trie):
            return
        children = trie[off]
        off += 1
        for _ in range(children):
            edge, off = _cstr(trie, off, len(trie))
            child, off = _uleb(trie, off)
            walk(child, prefix + edge)

    walk(0, "")
    return names


def parse_thin(data: bytes) -> dict:
    """Parse one 64-bit Mach-O image into the facts this tool needs."""
    if len(data) < 32:
        raise MachOError("too small for a Mach-O header")
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic == MH_CIGAM_64:
        raise MachOError("big-endian Mach-O is not supported")
    if magic != MH_MAGIC_64:
        raise MachOError(f"not a 64-bit Mach-O (magic 0x{magic:08x})")
    cputype, _cpusub, filetype, ncmds, _sizeofcmds, _flags, _res = struct.unpack_from(
        "<iiIIIII", data, 4
    )

    symtab = None
    export_range = None
    off = 32
    for _ in range(ncmds):
        if off + 8 > len(data):
            raise MachOError("truncated load commands")
        cmd, cmdsize = struct.unpack_from("<II", data, off)
        if cmdsize < 8 or off + cmdsize > len(data):
            raise MachOError("bad load command size")
        if cmd == LC_SYMTAB:
            symtab = struct.unpack_from("<IIII", data, off + 8)
        elif cmd in (LC_DYLD_INFO, LC_DYLD_INFO_ONLY):
            vals = struct.unpack_from("<10I", data, off + 8)
            if vals[9]:
                export_range = (vals[8], vals[9])
        elif cmd == LC_DYLD_EXPORTS_TRIE:
            dataoff, datasize = struct.unpack_from("<II", data, off + 8)
            if datasize:
                export_range = (dataoff, datasize)
        off += cmdsize

    exported: set[str] = set()
    if export_range:
        start, size = export_range
        exported = parse_export_trie(data[start : start + size])

    defined: set[str] = set()
    undefined: set[str] = set()
    external: set[str] = set()
    if symtab:
        symoff, nsyms, stroff, strsize = symtab
        strlimit = min(len(data), stroff + strsize)
        for i in range(nsyms):
            base = symoff + i * 16
            if base + 16 > len(data):
                break
            n_strx, n_type, _n_sect, _n_desc, _n_value = struct.unpack_from(
                "<IBBHQ", data, base
            )
            if n_type & N_STAB:
                continue
            try:
                name, _ = _cstr(data, stroff + n_strx, strlimit)
            except MachOError:
                continue
            if not name:
                continue
            kind = n_type & N_TYPE
            if kind == N_UNDF:
                undefined.add(name)
            else:
                defined.add(name)
            if n_type & N_EXT:
                external.add(name)

    return {
        "cputype": cputype,
        "filetype": filetype,
        "exported": exported,
        "defined": defined,
        "undefined": undefined,
        "external": external,
        "has_symtab": symtab is not None,
        "has_export_trie": export_range is not None,
    }


def slices(data: bytes):
    """Yield (cputype, slice_bytes) for a thin or fat Mach-O."""
    if len(data) < 8:
        raise MachOError("too small")
    magic = struct.unpack_from(">I", data, 0)[0]
    if magic in (FAT_MAGIC, FAT_MAGIC_64):
        count = struct.unpack_from(">I", data, 4)[0]
        off = 8
        for _ in range(count):
            if magic == FAT_MAGIC:
                cputype, _sub, offset, size, _align = struct.unpack_from(">IIIII", data, off)
                off += 20
            else:
                cputype, _sub, offset, size, _align, _res = struct.unpack_from(
                    ">IIQQII", data, off
                )
                off += 32
            if offset + size > len(data):
                raise MachOError("fat slice out of range")
            yield cputype, data[offset : offset + size]
        return
    yield None, data


def choose_slice(data: bytes):
    """Return (cputype, slice_bytes); prefer arm64."""
    options = list(slices(data))
    for cputype, body in options:
        if cputype in (0x0100000C, None):
            return cputype, body
    return options[0]


def binary_from_ipa(path: Path) -> tuple[bytes, str]:
    """Read the UnityFramework (else the main executable) out of an .ipa."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        unity = [n for n in names if n.endswith("UnityFramework.framework/UnityFramework")]
        if unity:
            return archive.read(unity[0]), unity[0]
        for name in names:
            parts = name.split("/")
            if (
                len(parts) == 3
                and parts[0] == "Payload"
                and parts[1].endswith(".app")
                and parts[2] == parts[1][: -len(".app")]
            ):
                return archive.read(name), name
    raise MachOError("no UnityFramework or app executable found in the IPA")


def inspect(data: bytes, symbols: tuple[str, ...]):
    _cputype, body = choose_slice(data)
    image = parse_thin(body)
    cputype = image["cputype"]
    rows = []
    for symbol in symbols:
        if symbol in image["exported"]:
            status = "export"
        elif symbol in image["defined"]:
            status = "symtab"
        elif symbol in image["undefined"]:
            status = "import"
        else:
            status = "missing"
        rows.append({"symbol": symbol, "status": status, "required": symbol in REQUIRED})
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in ("export", "symtab", "import", "missing")}
    return {
        "arch": CPU_NAMES.get(cputype, f"cputype(0x{cputype:08x})") if cputype else "unknown",
        "filetype": image["filetype"],
        "has_symtab": image["has_symtab"],
        "has_export_trie": image["has_export_trie"],
        "counts": counts,
        "rows": rows,
        "required_missing": [r["symbol"] for r in rows if r["required"] and r["status"] in ("import", "missing")],
    }


def print_report(source: str, report: dict) -> None:
    print(f"Image: {source}")
    print(
        f"  arch={report['arch']}  export-trie={'yes' if report['has_export_trie'] else 'no'}  "
        f"symtab={'yes' if report['has_symtab'] else 'no'}"
    )
    total = len(report["rows"])
    c = report["counts"]
    resolved = c["export"] + c["symtab"]
    print(
        f"  resolved {resolved}/{total}  (export {c['export']}, symtab {c['symtab']}, "
        f"import {c['import']}, missing {c['missing']})"
    )
    print()
    for row in report["rows"]:
        flag = " " if row["status"] in ("export", "symtab") else "!"
        req = "R" if row["required"] else " "
        print(f"  {flag} {req} {row['status']:<7} {row['symbol']}")
    if report["required_missing"]:
        print()
        print("Required symbols that will not resolve in this image:")
        for name in report["required_missing"]:
            print(f"  - {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", help="an .ipa, a Mach-O binary, or a .framework")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--require-all", action="store_true", help="exit 1 if a required symbol is missing")
    parser.add_argument("--symbol", action="append", default=None, help="inspect only this symbol (repeatable)")
    args = parser.parse_args(argv)

    path = Path(args.path)
    if not path.exists():
        print(f"error: {path} does not exist", file=sys.stderr)
        return 2
    symbols = tuple(args.symbol) if args.symbol else SYMBOLS

    try:
        if path.suffix.lower() == ".ipa":
            data, source = binary_from_ipa(path)
        else:
            data, source = path.read_bytes(), str(path)
        report = inspect(data, symbols)
    except MachOError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"source": source, **report}, indent=2))
    else:
        print_report(source, report)

    if args.require_all and report["required_missing"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
