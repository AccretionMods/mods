"""The IL2CPP symbol inspector classifies export-trie, symtab and undefined symbols.

Fixtures are synthetic 64-bit arm64 Mach-O images (thin, fat, and zipped as an
.ipa), so the test needs no real IPA.
"""

import json
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "inspect_il2cpp_symbols.py"
PY = sys.executable

MH_MAGIC_64 = 0xFEEDFACF
MH_DYLIB = 6
LC_SEGMENT_64 = 0x19
LC_SYMTAB = 0x02
LC_DYLD_EXPORTS_TRIE = 0x80000033
N_SECT = 0x0E
N_EXT = 0x01
N_UNDF = 0x00
ARM64 = 0x0100000C
X86_64 = 0x01000007


def _uleb(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _thin():
    symoff, nsyms = 0x4100, 3
    stroff, strsize = 0x4200, 0x200
    dataoff = 0x4400

    name = b"il2cpp_domain_get"
    payload = _uleb(0) + _uleb(0x4000)          # flags=0, address
    child = _uleb(len(payload)) + payload + b"\x00"
    root_len = 1 + 1 + len(name) + 1 + 1
    trie = b"\x00\x01" + name + b"\x00" + _uleb(root_len) + child

    strings = b"\x00il2cpp_domain_get\x00il2cpp_class_from_name\x00il2cpp_runtime_invoke\x00"
    strx = {
        "il2cpp_domain_get": 1,
        "il2cpp_class_from_name": 1 + len("il2cpp_domain_get") + 1,
        "il2cpp_runtime_invoke": 1 + len("il2cpp_domain_get") + 1 + len("il2cpp_class_from_name") + 1,
    }

    def nlist(key, ntype, value):
        return struct.pack("<IBBHQ", strx[key], ntype, 1, 0, value)

    data = bytearray(0x8000)
    data[stroff:stroff + len(strings)] = strings
    data[symoff:symoff + 48] = b"".join([
        nlist("il2cpp_domain_get", N_SECT | N_EXT, 0x4000),
        nlist("il2cpp_class_from_name", N_SECT, 0x4100),
        nlist("il2cpp_runtime_invoke", N_UNDF | N_EXT, 0),
    ])
    data[dataoff:dataoff + len(trie)] = trie

    cmds = bytearray()
    cmds += struct.pack("<II16sQQQQIIII", LC_SEGMENT_64, 72, b"__TEXT", 0, 0x4000, 0, 0x4000, 5, 5, 0, 0)
    cmds += struct.pack("<II16sQQQQIIII", LC_SEGMENT_64, 72, b"__LINKEDIT", 0x4000, 0x4000, 0x4000, 0x4000, 1, 1, 0, 0)
    cmds += struct.pack("<IIIIII", LC_SYMTAB, 24, symoff, nsyms, stroff, strsize)
    cmds += struct.pack("<IIII", LC_DYLD_EXPORTS_TRIE, 16, dataoff, len(trie))
    header = struct.pack("<IiiIIIII", MH_MAGIC_64, ARM64, 0, MH_DYLIB, 4, len(cmds), 0, 0)
    data[0:len(header)] = header
    data[len(header):len(header) + len(cmds)] = cmds
    return bytes(data)


def _fat(thin):
    align = 0x1000
    off_x = align
    off_a = off_x + ((len(thin) + align - 1) // align) * align
    out = bytearray(off_a + len(thin))
    struct.pack_into(">II", out, 0, 0xCAFEBABE, 2)
    struct.pack_into(">IIIII", out, 8, X86_64, 0, off_x, len(thin), 12)
    struct.pack_into(">IIIII", out, 28, ARM64, 0, off_a, len(thin), 12)
    out[off_x:off_x + len(thin)] = thin
    out[off_a:off_a + len(thin)] = thin
    return bytes(out)


class Inspector(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.thin = _thin()
        (self.dir / "UnityFramework").write_bytes(self.thin)
        (self.dir / "fat.bin").write_bytes(_fat(self.thin))
        with zipfile.ZipFile(self.dir / "Test.ipa", "w") as archive:
            archive.writestr("Payload/Test.app/Test", b"dummy")
            archive.writestr(
                "Payload/Test.app/Frameworks/UnityFramework.framework/UnityFramework", self.thin
            )

    def run_tool(self, *args):
        return subprocess.run([PY, str(SCRIPT), *args], capture_output=True, text=True)

    def test_classifies_export_symtab_and_import(self):
        result = self.run_tool(str(self.dir / "UnityFramework"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("export  il2cpp_domain_get", result.stdout)
        self.assertIn("symtab  il2cpp_class_from_name", result.stdout)
        self.assertIn("import  il2cpp_runtime_invoke", result.stdout)
        self.assertIn("resolved 2/42", result.stdout)

    def test_fat_prefers_arm64(self):
        result = self.run_tool(str(self.dir / "fat.bin"), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["arch"], "arm64")

    def test_ipa_extracts_unityframework(self):
        result = self.run_tool(str(self.dir / "Test.ipa"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("UnityFramework.framework/UnityFramework", result.stdout)
        self.assertIn("symtab  il2cpp_class_from_name", result.stdout)

    def test_json_counts(self):
        result = self.run_tool(str(self.dir / "UnityFramework"), "--json")
        report = json.loads(result.stdout)
        self.assertEqual(report["counts"], {"export": 1, "symtab": 1, "import": 1, "missing": 39})
        self.assertIn("il2cpp_get_corlib", report["required_missing"])

    def test_require_all_exit_code(self):
        result = self.run_tool(str(self.dir / "UnityFramework"), "--require-all")
        self.assertEqual(result.returncode, 1)

    def test_unknown_file_is_an_error(self):
        result = self.run_tool(str(self.dir / "does-not-exist.ipa"))
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
