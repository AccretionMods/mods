"""Signing-independent executable identity, including custom Unity code sections."""
import importlib.util
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def identity_image():
    data = bytearray(2048)
    section = struct.pack("<16s16s2Q8I", b"__il2cpp", b"__CUSTOM", 0x100000200,
                          64, 512, 2, 0, 0, 0x80000400, 0, 0, 0)
    segment = struct.pack("<II16s4Q4I", 25, 152, b"__CUSTOM", 0x100000000,
                          1024, 0, 1024, 5, 5, 1, 0) + section
    uuid = struct.pack("<II16s", 0x1B, 24, bytes(range(16)))
    signature = struct.pack("<4I", 0x1D, 16, 1536, 128)
    commands = segment + uuid + signature
    struct.pack_into("<8I", data, 0, 0xFEEDFACF, 0x100000C, 0, 2, 3, len(commands), 0, 0)
    data[32:32 + len(commands)] = commands
    data[512:576] = bytes(range(64))
    return bytes(data)


class DetectionIdentity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "scripts/detection_identity.py"
        assert path.exists(), "executable fingerprint support missing"
        spec = importlib.util.spec_from_file_location("identity", path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_custom_executable_section_is_hashed_and_changes_code_identity(self):
        data = identity_image()
        identity = self.module.executable_identity(data)
        self.assertEqual(identity["uuid"], "000102030405060708090a0b0c0d0e0f")
        self.assertEqual(identity["ranges"], [{"offset": 512, "size": 64}])
        changed = bytearray(data); changed[512] ^= 1
        self.assertNotEqual(identity["sha256"], self.module.executable_identity(changed)["sha256"])

    def test_signature_blob_and_signature_command_are_excluded(self):
        data = identity_image()
        changed = bytearray(data); changed[1536:1664] = b"x" * 128
        struct.pack_into("<II", changed, 32 + 152 + 24 + 8, 1664, 64)
        self.assertEqual(self.module.executable_identity(data), self.module.executable_identity(changed))

    @unittest.skipUnless(os.name != "nt" and shutil.which("cc"), "requires POSIX compiler and libcrypto")
    def test_native_reader_matches_python_for_thin_fat_and_changed_code(self):
        with tempfile.TemporaryDirectory() as d:
            exe = str(Path(d) / "identity")
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "runtime"),
                            str(ROOT / "tests/detection_identity_harness.c"),
                            str(ROOT / "runtime/AMDetectionIdentity.c"), "-lcrypto", "-o", exe], check=True)
            thin = identity_image()
            fat = bytearray(4096 + len(thin))
            struct.pack_into(">7I", fat, 0, 0xCAFEBABE, 1, 0x100000C, 0, 4096, len(thin), 12)
            fat[4096:] = thin
            code_changed = bytearray(thin);code_changed[520] ^= 1
            malformed = bytearray(thin);struct.pack_into("<I", malformed, 32 + 72 + 48, 2040)
            for i, data in enumerate((thin, fat, code_changed, malformed)):
                file = Path(d) / f"{i}.macho";file.write_bytes(data)
                result = subprocess.run([exe, str(file)], capture_output=True, text=True)
                if i == 3:
                    self.assertEqual(result.returncode, 1)
                    continue
                self.assertEqual(result.returncode, 0, result.stderr)
                identity = self.module.executable_identity(data)
                self.assertEqual(result.stdout.strip(), identity["uuid"] + ":" + identity["sha256"] + ":1:512,64")

    def test_malformed_section_ranges_uuid_and_encryption_fail_closed(self):
        for kind in ("outside", "uuid", "truncated", "overlap_signature", "encrypted"):
            with self.subTest(kind=kind):
                data = bytearray(identity_image())
                if kind == "outside": struct.pack_into("<I", data, 32 + 72 + 48, 2040)
                elif kind == "uuid": struct.pack_into("<I", data, 32 + 152, 0)
                elif kind == "truncated": data = data[:550]
                elif kind == "overlap_signature": struct.pack_into("<II", data, 32 + 152 + 24 + 8, 512, 64)
                else:
                    data[224:248] = struct.pack("<6I", 0x2C, 24, 512, 64, 1, 0)
                    struct.pack_into("<II", data, 16, 4, 216)
                with self.assertRaises(ValueError): self.module.executable_identity(data)
