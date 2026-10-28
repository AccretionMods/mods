import contextlib
import importlib.util
import io
import plistlib
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def image(chained=True, format_=6):
    data = bytearray(3072)
    commands = []
    for name, offset, sections in (
        (b"__TEXT", 0, 0),
        (b"__DATA", 1024, 1),
        (b"__LINKEDIT", 2048, 0),
    ):
        command = struct.pack(
            "<II16s4Q4I",
            25,
            72 + 80 * sections,
            name,
            0x100000000 + offset,
            1024,
            offset,
            1024,
            3,
            3,
            sections,
            0,
        )
        if sections:
            command += struct.pack(
                "<16s16s2Q8I", b"__got", name, 0x100000400, 8, 1024, 3, 0, 0, 6, 0, 0, 0
            )
        commands.append(command)
    commands += [
        struct.pack("<6I", 2, 24, 2104, 1, 2140, 32),
        struct.pack("<20I", 11, 80, *([0] * 12), 2200, 1, *([0] * 4)),
    ]
    if chained:
        commands.append(struct.pack("<4I", 0x80000034, 16, 2300, 60))
        struct.pack_into("<7I", data, 2300, 0, 28, 0, 0, 0, 0, 0)
        struct.pack_into("<2I", data, 2328, 1, 8)
        struct.pack_into("<IHHQIHH", data, 2336, 24, 4096, format_, 1024, 0, 1, 65535)
    struct.pack_into(
        "<8I",
        data,
        0,
        0xFEEDFACF,
        0x100000C,
        0,
        2,
        len(commands),
        sum(map(len, commands)),
        0,
        0,
    )
    data[32 : 32 + sum(map(len, commands))] = b"".join(commands)
    struct.pack_into("<IBBHQ", data, 2104, 1, 0, 0, 0, 0)
    data[2141:2149] = b"_access\0"
    return bytes(data)


class DetectionPreflight(unittest.TestCase):
    def test_schema2_fingerprint_mismatch_is_rejected_independently_of_import_support(self):
        identity = {"algorithm": "sha256-executable-sections-v1", "uuid": "a" * 32,
                    "sha256": "b" * 64, "ranges": [{"offset": 512, "size": 64}]}
        report = {"app_version": "1.2.0", "complete": True,
                  "images": [{"image": "@executable", "state": "supported", "identity": identity}]}
        profile = {"schema_version": 2, "game_version": "1.2.0", "mode": "filter",
                   "images": ["@executable"], "adapters": ["filesystem"], "apis": ["stat"],
                   "image_fingerprints": {"@executable": identity}}
        self.assertEqual(self.module.profile_problems(report, profile), [])
        changed = dict(identity, sha256="c" * 64)
        self.assertTrue(self.module.profile_problems(report, dict(profile, image_fingerprints={"@executable": changed})))
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "scripts"))
        source = ROOT / "scripts/detection_preflight.py"
        spec = importlib.util.spec_from_file_location("preflight", source)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_classic_and_retained_chained_imports(self):
        for chained in (False, True):
            result = self.module.inspect_macho(image(chained))
            self.assertEqual(result["state"], "supported")
            self.assertEqual(result["imports"], {"access": 1})

    def test_rejected_layouts_do_not_claim_coverage(self):
        for data, reason in (
            (image(format_=12), "authenticated_segments"),
            (image(format_=99), "chained_fixups"),
            (image()[:100], "malformed_layout"),
        ):
            result = self.module.inspect_macho(data)
            self.assertNotEqual(result["state"], "supported")
            self.assertEqual(result["reason"], reason)
        data = bytearray(image())
        struct.pack_into("<I", data, 8, 2)
        self.assertEqual(self.module.inspect_macho(data)["reason"], "architecture")
        data = bytearray(image())
        struct.pack_into("<I", data, 2200, 999)
        self.assertEqual(self.module.inspect_macho(data)["reason"], "malformed_layout")

    def test_unity_inventory_preset_and_profile_validation(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "game.ipa"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    "Payload/Game.app/Info.plist",
                    plistlib.dumps(
                        {
                            "CFBundleExecutable": "Game",
                            "CFBundleShortVersionString": "1.2.0",
                        }
                    ),
                )
                archive.writestr("Payload/Game.app/Game", image(False))
                archive.writestr(
                    "Payload/Game.app/Frameworks/UnityFramework.framework/UnityFramework",
                    image(),
                )
                archive.writestr(
                    "Payload/Game.app/Data/Managed/Metadata/global-metadata.dat",
                    b"metadata",
                )
            report = self.module.inspect_ipa(path)
            self.assertEqual(report["engine"], "unity-il2cpp")
            self.assertEqual(len(report["images"]), 2)
            preset = self.module.observation_profile(report)
            self.assertEqual(preset["mode"], "observe")
            self.assertEqual(preset["game_version"], "1.2.0")
            self.assertIn("@executable", preset["images"])
            self.assertFalse(self.module.profile_problems(report, preset))
            preset["images"].append("Frameworks/Absent.dylib")
            self.assertTrue(self.module.profile_problems(report, preset))
            preset["images"].pop()
            preset["game_version"] = "9.9"
            self.assertTrue(self.module.profile_problems(report, preset))

    def test_malformed_and_unity_without_usable_images(self):
        for data in (b"", b"bad", image()[:-1024]):
            self.assertNotEqual(self.module.inspect_macho(data)["state"], "supported")
        with self.assertRaises(ValueError):
            self.module.observation_profile(
                {
                    "engine": "unity-il2cpp",
                    "app_version": "1.0",
                    "images": [],
                    "complete": True,
                }
            )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.ipa"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    "Payload/Game.app/Info.plist", plistlib.dumps(["invalid root"])
                )
            with self.assertRaises(ValueError):
                self.module.inspect_ipa(path)

    def test_dotted_and_plist_named_framework_executables_are_in_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "game.ipa"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    "Payload/Game.app/Info.plist",
                    plistlib.dumps(
                        {
                            "CFBundleExecutable": "Game",
                            "CFBundleShortVersionString": "1.0",
                        }
                    ),
                )
                archive.writestr("Payload/Game.app/Game", image(False))
                archive.writestr(
                    "Payload/Game.app/Data/Managed/Metadata/global-metadata.dat",
                    b"metadata",
                )
                archive.writestr(
                    "Payload/Game.app/Frameworks/Foo.Bar.framework/Foo.Bar", image()
                )
                archive.writestr(
                    "Payload/Game.app/Frameworks/Odd.framework/Info.plist",
                    plistlib.dumps({"CFBundleExecutable": "Custom.Run"}),
                )
                archive.writestr(
                    "Payload/Game.app/Frameworks/Odd.framework/Custom.Run", image()
                )
            report = self.module.inspect_ipa(path)
            self.assertTrue(report["complete"])
            self.assertEqual(len(report["images"]), 3)
            self.assertIn(
                "Frameworks/Foo.Bar.framework/Foo.Bar",
                self.module.observation_profile(report)["images"],
            )
            before = path.read_bytes()
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(
                    self.module.main([str(path), "--output", str(path)]), 1
                )
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
