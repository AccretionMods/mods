"""Map managed method names to Il2CppCodeGenModule table entries for a game whose il2cpp_* names are stripped.

Il2CppCodeRegistration (version 27+) no longer carries methodPointers directly; each assembly has an
Il2CppCodeGenModule {moduleName, methodPointerCount, methodPointers}. Patching methodPointers[index] before
IL2CPP initialises its MethodInfos is a hook that needs no il2cpp_* API and touches no code page.

This module reads a dump.cs (Il2CppDumper) and the UnityFramework Mach-O, then writes a C header with
(module, index) for the requested `Class.Method` names. The runtime counterpart is runtime/AMTableHook.c.
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


@dataclass
class Method:
    namespace: str
    klass: str
    name: str
    rva: int

    @property
    def full_name(self) -> str:
        return f"{self.namespace}.{self.klass}.{self.name}" if self.namespace else f"{self.klass}.{self.name}"


_CLASS_RE = re.compile(r"^\s*(?:public|internal|private|protected)?\s*(?:sealed\s+|abstract\s+|static\s+)*"
                       r"(?:class|struct|interface|enum)\s+(\w+)")
_NAMESPACE_RE = re.compile(r"^\s*//\s*Namespace:\s*(\S*)")
_RVA_RE = re.compile(r"//\s*RVA:\s*0x([0-9A-Fa-f]+)")
_METHOD_RE = re.compile(r"(\w+)\s*\(")


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
                    methods.append(Method(namespace, klass, name.group(1), pending))
                pending = None
    return methods


@dataclass
class Module:
    name: str
    count: int
    pointers: int
    index: int


def load_modules(binary: str, registration_rva: int) -> list[Module]:
    """Read Il2CppCodeRegistration -> codeGenModules and their methodPointers arrays."""
    if lief is None:
        raise RuntimeError("lief is required to read the UnityFramework")
    parsed = lief.MachO.parse(binary)
    b = parsed.at(0) if isinstance(parsed, lief.MachO.FatBinary) else parsed
    data = Path(binary).read_bytes()
    rels = {r.address: r.target for r in b.relocations}
    reg = registration_rva

    def u64(off: int) -> int:
        return struct.unpack_from("<Q", data, off)[0]

    def dec(off: int) -> int:
        return rels.get(off, u64(off))

    count = u64(reg + 120)
    array = dec(reg + 128)
    modules: list[Module] = []
    for i in range(count):
        module = dec(array + i * 8)
        name_ptr = dec(module)
        end = data.find(b"\0", name_ptr, name_ptr + 256)
        name = data[name_ptr:end].decode("utf-8", "replace") if 0 < name_ptr < len(data) else ""
        modules.append(Module(name, u64(module + 8), dec(module + 16), i))
    return modules


def rva_index(modules: list[Module], binary: str, rva: int, module_name: str | None = None) -> tuple[int, int] | None:
    """(module index, method index) whose methodPointers entry equals rva."""
    if lief is None:
        raise RuntimeError("lief is required to read the UnityFramework")
    parsed = lief.MachO.parse(binary)
    b = parsed.at(0) if isinstance(parsed, lief.MachO.FatBinary) else parsed
    data = Path(binary).read_bytes()
    rels = {r.address: r.target for r in b.relocations}

    def dec(off: int) -> int:
        return rels.get(off, struct.unpack_from("<Q", data, off)[0])

    for module in modules:
        if module_name and module.name != module_name:
            continue
        for idx in range(module.count):
            if dec(module.pointers + idx * 8) == rva:
                return module.index, idx
    return None


def generate(dump: str, binary: str, registration_rva: int, hooks: list[str],
             module_name: str | None = None) -> dict:
    methods = parse_dump(Path(dump).read_text(errors="ignore"))
    modules = load_modules(binary, registration_rva)
    by_full = {m.full_name: m for m in methods}
    by_short = {f"{m.klass}.{m.name}": m for m in methods}
    out = []
    for hook in hooks:
        method = by_full.get(hook) or by_short.get(hook)
        if method is None:
            raise SystemExit(f"method not found in dump: {hook}")
        found = rva_index(modules, binary, method.rva, module_name)
        if found is None:
            raise SystemExit(f"RVA {method.rva:#x} for {hook} is not in any module's methodPointers")
        module, index = found
        out.append({
            "name": hook,
            "module": module,
            "index": index,
            "rva": method.rva,
            "assembly": modules[module].name,
        })
    return {"registration_rva": registration_rva, "hooks": out}


def header(result: dict) -> str:
    lines = [
        "// Generated by scripts/table_hooks.py; do not edit.",
        "#pragma once",
        "#include \"AMTableHook.h\"",
        f"#define AM_TABLE_REGISTRATION_RVA {result['registration_rva']:#x}ULL",
        f"#define AM_TABLE_HOOK_COUNT {len(result['hooks'])}",
        "static AMTableHookSpec AMTableHooks[AM_TABLE_HOOK_COUNT] = {",
    ]
    for h in result["hooks"]:
        lines.append(f'    {{ "{h["name"]}", {h["module"]}, {h["index"]}, 0, 0 }},')
    lines.append("};")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dump", required=True, help="dump.cs from Il2CppDumper")
    p.add_argument("--binary", required=True, help="UnityFramework Mach-O")
    p.add_argument("--registration", required=True, type=lambda s: int(s, 0),
                   help="Il2CppCodeRegistration RVA (Il2CppDumper prints it)")
    p.add_argument("--hook", action="append", default=[], help="Class.Method to resolve (repeatable)")
    p.add_argument("--module", help="restrict to one assembly name, e.g. Assembly-CSharp.dll")
    p.add_argument("--output", help="write the C header here")
    p.add_argument("--json", action="store_true", help="print JSON instead of C")
    args = p.parse_args(argv)
    if not args.hook:
        p.error("at least one --hook is required")
    result = generate(args.dump, args.binary, args.registration, args.hook, args.module)
    text = json.dumps(result, indent=2) if args.json else header(result)
    if args.output:
        Path(args.output).write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
