"""Map managed method names to Il2CppCodeGenModule table entries for a game whose il2cpp_* names are stripped.

Il2CppCodeRegistration (version 24.2+) carries one Il2CppCodeGenModule {moduleName, methodPointerCount,
methodPointers} per assembly. Patching methodPointers[index] before IL2CPP initialises its MethodInfos is a
hook that needs no il2cpp_* API and touches no code page.

This module reads a dump.cs (Il2CppDumper) and the UnityFramework Mach-O, then resolves the requested
`Class.Method` (or `Namespace.Class.Method`) names to (module, index). The codeGenModules fields sit at a
different offset in each IL2CPP version, so the offset is found by reading the binary at each known layout
and keeping the one that yields an assembly table (detect_layout). The runtime counterpart is
runtime/AMTableHook.c; scripts/embed_hooks.py writes the C table for a mod's hooks.json "table" section.

Usage (by hand, for one build):
    python3 scripts/table_hooks.py --dump dump.cs --binary UnityFramework --registration 0x5593a48 \\
        --hook ExperienceLevel.Update --output TableHooks.h
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import lief
except ImportError:  # pragma: no cover - only used for the binary side
    lief = None

# Byte offset of codeGenModulesCount inside Il2CppCodeRegistration (codeGenModules follows it), per layout:
# 29.1+ (two more unresolved*CallPointers), 27.1 to 29.0, and 27.0 (no genericAdjustorThunks). Older
# versions keep methodPointers in the registration itself and are not supported here.
LAYOUTS = (120, 104, 96)
MAX_MODULES = 4096


@dataclass
class Method:
    namespace: str
    klass: str
    name: str
    rva: int
    signature: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.namespace}.{self.klass}.{self.name}" if self.namespace else f"{self.klass}.{self.name}"

    @property
    def is_static(self) -> bool:
        return re.search(r"\bstatic\b", self.signature.split("(")[0]) is not None

    @property
    def returns_void(self) -> bool:
        return re.search(r"\bvoid\s+" + re.escape(self.name) + r"\s*\(", self.signature) is not None

    @property
    def parameter_count(self) -> int:
        inside = self.signature.split("(", 1)[1].rsplit(")", 1)[0] if "(" in self.signature else ""
        return 0 if not inside.strip() else inside.count(",") + 1


@dataclass
class Field:
    namespace: str
    klass: str
    name: str
    offset: int
    type: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.namespace}.{self.klass}.{self.name}" if self.namespace else f"{self.klass}.{self.name}"


_CLASS_RE = re.compile(r"^\s*(?:public|internal|private|protected)?\s*(?:sealed\s+|abstract\s+|static\s+)*"
                       r"(?:class|struct|interface|enum)\s+(\w+)")
_NAMESPACE_RE = re.compile(r"^\s*//\s*Namespace:\s*(\S*)")
_RVA_RE = re.compile(r"//\s*RVA:\s*0x([0-9A-Fa-f]+)")
_METHOD_RE = re.compile(r"(\w+)\s*\(")
_FIELD_RE = re.compile(r"^\s*(?:public|private|protected|internal)\s+(.+?)\s+\b(\w+)\s*;\s*//\s*0x([0-9A-Fa-f]+)")
_STATIC_RE = re.compile(r"\b(?:static|const)\b")


def parse_dump(text: str) -> list[Method]:
    """Every method with an RVA, in dump order. dump.cs puts the RVA comment before the signature."""
    methods: list[Method] = []
    namespace = ""
    klass = ""
    pending: int | None = None
    for line in text.splitlines():
        m = _NAMESPACE_RE.match(line)
        if m:
            namespace = m.group(1)
            continue
        m = _CLASS_RE.match(line)
        if m:
            klass = m.group(1)
            continue
        m = _RVA_RE.search(line)
        if m:
            pending = int(m.group(1), 16)
            continue
        if pending is not None:
            sig = line.strip()
            if sig:
                name = _METHOD_RE.search(sig)
                if name and klass:
                    methods.append(Method(namespace, klass, name.group(1), pending, sig))
                pending = None
    return methods


def parse_fields(text: str) -> list[Field]:
    """Every instance field whose offset dump.cs records (`public int x; // 0x18`). Static and const fields
    are skipped: their offset is into the class's static storage, not the object."""
    fields: list[Field] = []
    namespace = ""
    klass = ""
    for line in text.splitlines():
        m = _NAMESPACE_RE.match(line)
        if m:
            namespace = m.group(1)
            continue
        m = _CLASS_RE.match(line)
        if m:
            klass = m.group(1)
            continue
        m = _FIELD_RE.match(line)
        if m and klass and not _STATIC_RE.search(m.group(1)):
            fields.append(Field(namespace, klass, m.group(2), int(m.group(3), 16), m.group(1).strip()))
    return fields


def find_method(methods: list[Method], name: str) -> Method | None:
    """`Namespace.Class.Method` first, then `Class.Method` (the namespace left out)."""
    by_full = {m.full_name: m for m in methods}
    if name in by_full:
        return by_full[name]
    return {f"{m.klass}.{m.name}": m for m in methods}.get(name)


def find_field(fields: list[Field], name: str) -> Field | None:
    by_full = {f.full_name: f for f in fields}
    if name in by_full:
        return by_full[name]
    return {f"{f.klass}.{f.name}": f for f in fields}.get(name)


def pin_problem(method: Method) -> str | None:
    """Why a generated pin replacement (void (*)(self, method)) would not fit this method, or None."""
    if method.is_static:
        return "is static (a pin needs an instance method, its self is the object the field lives in)"
    if method.parameter_count:
        return f"takes {method.parameter_count} parameter(s) (a pin replacement has none)"
    if not method.returns_void:
        return "does not return void (a pin replacement returns nothing)"
    return None


class Reader:
    """Little-endian reads from a Mach-O image whose file offsets equal its addresses (an iOS framework:
    __TEXT at 0, no __PAGEZERO). Pointer slots are decoded through the chained fixups, so a stored
    pointer reads as the address it will have once dyld has rebased the image."""

    def __init__(self, data: bytes, relocations: dict[int, int]):
        self.data = data
        self.relocations = relocations

    @classmethod
    def open(cls, binary: str) -> "Reader":
        if lief is None:
            raise SystemExit("lief is required to read the UnityFramework (pip install lief)")
        parsed = lief.MachO.parse(binary)
        b = parsed.at(0) if isinstance(parsed, lief.MachO.FatBinary) else parsed
        return cls(Path(binary).read_bytes(), {r.address: r.target for r in b.relocations})

    def inside(self, offset: int, size: int = 8) -> bool:
        return 0 <= offset and offset + size <= len(self.data)

    def u64(self, offset: int) -> int:
        if not self.inside(offset):
            raise SystemExit(f"read past the end of the binary at {offset:#x}")
        return struct.unpack_from("<Q", self.data, offset)[0]

    def pointer(self, offset: int) -> int:
        return self.relocations.get(offset, self.u64(offset))

    def cstring(self, offset: int, limit: int = 256) -> str:
        if not self.inside(offset, 1):
            return ""
        end = self.data.find(b"\0", offset, offset + limit)
        return self.data[offset:end if end >= 0 else offset + limit].decode("utf-8", "replace")


@dataclass
class Module:
    name: str
    count: int
    pointers: int
    index: int


def _modules_at(reader: Reader, registration_rva: int, offset: int) -> list[Module] | None:
    """The codeGenModules table read with codeGenModulesCount at `offset`, or None when the bytes there
    don't look like one (so another layout is tried)."""
    if not reader.inside(registration_rva + offset, 16):
        return None
    count = reader.u64(registration_rva + offset)
    if not 1 <= count <= MAX_MODULES:
        return None
    array = reader.pointer(registration_rva + offset + 8)
    if not reader.inside(array, count * 8):
        return None
    modules: list[Module] = []
    for i in range(count):
        module = reader.pointer(array + i * 8)
        if not reader.inside(module, 24):
            return None
        name = reader.cstring(reader.pointer(module))
        if not name.endswith(".dll"):
            return None
        modules.append(Module(name, reader.u64(module + 8), reader.pointer(module + 16), i))
    return modules


def detect_layout(reader: Reader, registration_rva: int) -> tuple[int, list[Module]]:
    """(codeGenModulesCount offset, modules) for the layout whose table reads as assemblies."""
    for offset in LAYOUTS:
        modules = _modules_at(reader, registration_rva, offset)
        if modules is not None:
            return offset, modules
    raise SystemExit(f"no codeGenModules table at registration {registration_rva:#x} with any known layout "
                     f"(offsets {', '.join(map(str, LAYOUTS))}): wrong RVA, or an IL2CPP older than 27")


def load_modules(binary: str, registration_rva: int) -> list[Module]:
    """Read Il2CppCodeRegistration -> codeGenModules and their methodPointers arrays."""
    return detect_layout(Reader.open(binary), registration_rva)[1]


def pointer_map(reader: Reader, modules: list[Module], module_name: str | None = None) -> dict[int, tuple[int, int]]:
    """{method RVA: (module index, methodPointers index)} over every module (or the one named), read once."""
    found: dict[int, tuple[int, int]] = {}
    for module in modules:
        if module_name and module.name != module_name:
            continue
        for idx in range(module.count):
            found.setdefault(reader.pointer(module.pointers + idx * 8), (module.index, idx))
    return found


def generate(dump: str, binary: str, registration_rva: int, hooks: list[str],
             module_name: str | None = None, reader: Reader | None = None) -> dict:
    """The table entries for `hooks`: {"registration_rva", "modules_offset", "hooks": [{name, module, index,
    rva, assembly, method}]}. Exits with the name of the first hook the dump or the binary does not have."""
    methods = parse_dump(Path(dump).read_text(errors="ignore"))
    reader = reader or Reader.open(binary)
    offset, modules = detect_layout(reader, registration_rva)
    if module_name and not any(m.name == module_name for m in modules):
        raise SystemExit(f"module {module_name!r} is not in this build; modules: {', '.join(m.name for m in modules)}")
    rvas = pointer_map(reader, modules, module_name)
    out = []
    for hook in hooks:
        method = find_method(methods, hook)
        if method is None:
            raise SystemExit(f"method not found in dump: {hook}")
        found = rvas.get(method.rva)
        if found is None:
            raise SystemExit(f"RVA {method.rva:#x} for {hook} is not in any module's methodPointers")
        module, index = found
        out.append({
            "name": hook,
            "module": module,
            "index": index,
            "rva": method.rva,
            "assembly": modules[module].name,
            "method": method,
        })
    return {"registration_rva": registration_rva, "modules_offset": offset, "hooks": out}


_PIN_CTYPE = {"int": "int32_t", "float": "float", "bool": "bool", "int64": "int64_t"}


def pin_value(pin: dict) -> str:
    """The C literal a pin writes."""
    ctype = _PIN_CTYPE.get(pin["type"])
    if not ctype:
        raise SystemExit(f"pin {pin['name']}: type {pin['type']!r} must be int, float, bool or int64")
    value = pin["value"]
    if pin["type"] == "float":
        return f"{float(value):g}f"
    if pin["type"] == "bool":
        return "1" if value else "0"
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise SystemExit(f"pin {pin['name']}: value {value!r} is not a whole number")
    return str(int(value)) + ("LL" if pin["type"] == "int64" else "")


def pin_stubs(pins: list[dict]) -> list[str]:
    """One replacement per pin: writes the field at its dump offset, then calls the original.
    pins entries: {name, module, index, offset, type, value}."""
    lines = []
    for i, pin in enumerate(pins):
        ctype = _PIN_CTYPE[pin["type"]]
        value = pin_value(pin)
        lines.append(f"static void (*orig_pin{i})(void *, const void *);")
        lines.append(f"static void rep_pin{i}(void *self, const void *method) {{")
        lines.append(f"    *({ctype} *)((char *)self + {int(pin['offset'])}) = {value};")
        lines.append(f"    if (orig_pin{i}) orig_pin{i}(self, method);")
        lines.append("}")
    return lines


def spec_lines(result: dict, pins: list[dict], name_of) -> list[str]:
    lines = []
    for i, h in enumerate(result["hooks"]):
        lines.append(f"    {{ {name_of(i, h['name'])}, {h['module']}, {h['index']}, 0, 0 }},")
    for i, pin in enumerate(pins):
        lines.append(f"    {{ {name_of(len(result['hooks']) + i, pin['name'])}, {pin['module']}, {pin['index']}, "
                     f"(void *)rep_pin{i}, (void **)&orig_pin{i} }},")
    return lines


def header(result: dict, pins: list[dict] | None = None) -> str:
    """A stand-alone C header (plain names; scripts/embed_hooks.py writes the mod's table with hidden names)."""
    pins = pins or []
    lines = [
        "// Generated by scripts/table_hooks.py; do not edit.",
        "#pragma once",
        "#include \"AMTableHook.h\"",
        f"#define AM_TABLE_REGISTRATION_RVA {result['registration_rva']:#x}ULL",
        f"#define AM_TABLE_MODULES_OFFSET {result['modules_offset']}",
    ]
    lines += pin_stubs(pins)
    lines.append(f"#define AM_TABLE_HOOK_COUNT {len(result['hooks']) + len(pins)}")
    lines.append("static AMTableHookSpec AMTableHooks[AM_TABLE_HOOK_COUNT] = {")
    lines += spec_lines(result, pins, lambda i, name: json.dumps(name))
    lines.append("};")
    return "\n".join(lines) + "\n"


def header_with_pins(result: dict, pins: list[dict]) -> str:
    return header(result, pins)


def _json_ready(result: dict) -> dict:
    return {**result, "hooks": [{k: v for k, v in h.items() if k != "method"} for h in result["hooks"]]}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dump", required=True, help="dump.cs from Il2CppDumper")
    p.add_argument("--binary", required=True, help="UnityFramework Mach-O")
    p.add_argument("--registration", required=True, type=lambda s: int(s, 0),
                   help="Il2CppCodeRegistration RVA (Il2CppDumper prints it)")
    p.add_argument("--hook", action="append", default=[], help="Class.Method to resolve (repeatable)")
    p.add_argument("--field", action="append", default=[], help="Class.Field to read its dump offset (repeatable)")
    p.add_argument("--module", help="restrict to one assembly name, e.g. Assembly-CSharp.dll")
    p.add_argument("--output", help="write the C header here")
    p.add_argument("--json", action="store_true", help="print JSON instead of C")
    args = p.parse_args(argv)
    if not args.hook and not args.field:
        p.error("at least one --hook or --field is required")
    if args.field and not args.hook:
        fields = parse_fields(Path(args.dump).read_text(errors="ignore"))
        out = []
        for name in args.field:
            found = find_field(fields, name)
            if found is None:
                raise SystemExit(f"field not found in dump: {name}")
            out.append({"name": name, "offset": found.offset})
        text = json.dumps(out, indent=2)
        if args.output:
            Path(args.output).write_text(text)
        else:
            sys.stdout.write(text)
        return 0
    result = generate(args.dump, args.binary, args.registration, args.hook, args.module)
    text = json.dumps(_json_ready(result), indent=2) if args.json else header(result)
    if args.output:
        Path(args.output).write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
