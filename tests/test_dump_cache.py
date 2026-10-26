"""scripts/dump_cache.py and dev.py dump: one dump per game build, shared by mods; Auto-build's review memory."""

import io
import os
import plistlib
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "studio"))

import dev
import dump_cache
import dump_scan

METADATA = b"\xaf\x1b\xb1\xfa" + struct.pack("<i", 29) + b"metadata"


def ipa(path, bundle="com.example.game", version="1.0", binary=b"\xcf\xfa\xed\xfe build 1"):
    with zipfile.ZipFile(path, "w") as z:
        app = "Payload/Game.app"
        z.writestr(f"{app}/Info.plist", plistlib.dumps({"CFBundleIdentifier": bundle, "CFBundleShortVersionString": version}))
        z.writestr(f"{app}/{dump_cache.FRAMEWORK}", binary)
        z.writestr(f"{app}/{dump_cache.METADATA}", METADATA)
    return path


class DumpCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old = os.environ.get("AM_ROOT")
        os.environ["AM_ROOT"] = str(self.root)
        self.runs = []
        # throwaway mods (the real ones are personal and not in git)
        for mod in ("game-a", "game-b"):
            (self.root / "mods" / mod).mkdir(parents=True)
            (self.root / "mods" / mod / "mod.json").write_text('{"target_bundle_id": "com.example.game"}', encoding="utf-8")
        patcher = mock.patch.object(dev, "mod_dir", lambda mod: self.root / "mods" / mod)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        if self.old is None:
            os.environ.pop("AM_ROOT", None)
        else:
            os.environ["AM_ROOT"] = self.old
        self.tmp.cleanup()

    def fake_dumper(self, folder, binary, metadata, out):
        self.runs.append(Path(folder).name)
        (Path(out) / "dump.cs").write_text("// Image 0: Assembly-CSharp.dll - 0\n", encoding="utf-8")

    def dump(self, mod, base, **kw):
        with mock.patch.object(dev, "run_dumper", self.fake_dumper), mock.patch("builtins.print"):
            return dev.dump(mod, str(base), **kw)

    def test_key_from_the_ipa_equals_the_key_from_extracted_files(self):
        path = ipa(self.root / "a.ipa")
        work = self.root / "x"
        work.mkdir()
        with zipfile.ZipFile(path) as z:
            (work / "UnityFramework").write_bytes(z.read(f"Payload/Game.app/{dump_cache.FRAMEWORK}"))
            (work / "global-metadata.dat").write_bytes(z.read(f"Payload/Game.app/{dump_cache.METADATA}"))
        self.assertEqual(dump_cache.key_of_ipa(path), dump_cache.key_of_files(work / "UnityFramework", work / "global-metadata.dat"))
        other = ipa(self.root / "b.ipa", binary=b"\xcf\xfa\xed\xfe build 2")
        self.assertNotEqual(dump_cache.key_of_ipa(path), dump_cache.key_of_ipa(other))

    def test_a_build_is_dumped_once_for_every_mod_and_copy(self):
        first = ipa(self.root / "game-1.0.ipa")
        copy = ipa(self.root / "game-1.0-again.ipa")  # the same build fetched again
        out1 = self.dump("game-a", first)
        out2 = self.dump("game-b", copy)  # a second mod on the same build
        self.assertEqual(out1, out2)
        self.assertEqual(len(self.runs), 1)
        self.assertTrue(self.runs[0].endswith("-lean") or self.runs[0] == "il2cppdumper")  # lean unless --structs
        self.assertEqual(dev.load_state("game-b")["dump_dir"], str(out1.parent.parent))
        self.dump("game-b", copy, force=True)
        self.assertEqual(len(self.runs), 2)
        newer = ipa(self.root / "game-1.1.ipa", version="1.1", binary=b"\xcf\xfa\xed\xfe build 2")
        self.assertNotEqual(self.dump("game-b", newer), out1)

    def test_structs_runs_the_full_dumper_once(self):
        base = ipa(self.root / "game.ipa")
        tools = self.root / "tools" / "il2cppdumper"
        tools.mkdir(parents=True)
        (tools / "config.json").write_text('{"GenerateStruct": true, "DumpMethod": true}', encoding="utf-8")
        (tools / "Il2CppDumper.dll").write_bytes(b"dll")
        self.dump("game-b", base)
        self.dump("game-b", base, structs=True)
        self.dump("game-b", base, structs=True)
        self.assertEqual(self.runs, ["il2cppdumper-lean", "il2cppdumper"])
        lean = (self.root / "tools" / "il2cppdumper-lean" / "config.json").read_text(encoding="utf-8")
        self.assertIn('"GenerateStruct": false', lean)
        self.assertIn('"GenerateStruct": true', (tools / "config.json").read_text(encoding="utf-8"))  # tool untouched


class Reviewed(unittest.TestCase):
    def test_answered_batches_are_remembered_and_failed_ones_are_not(self):
        import autobuild

        a = {"class": "Player", "member": "Gold", "ctype": "int", "kind": "field", "object": "Player[]"}
        b = dict(a, member="Health", ctype="float")
        c = dict(a, member="Speed", ctype="float")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(dev, "am_root", return_value=Path(tmp)):
            store = {}
            autobuild.remember_reviewed("m", store, [[a, b], [c]],
                                        {1: ['{"n": 1}'], 2: ["You've hit your weekly limit"]},
                                        {1: ([("row", (a, {}))], []), 2: ([], ["out of allowance"])}, "1.0", "claude")
            self.assertEqual(store[autobuild.fingerprint(a)]["decision"], "picked")
            self.assertEqual(store[autobuild.fingerprint(b)]["decision"], "passed")
            self.assertNotIn(autobuild.fingerprint(c), store)  # asked again next time
            self.assertEqual(autobuild.load_reviewed("m"), store)
        self.assertNotEqual(autobuild.fingerprint(a), autobuild.fingerprint(b))


if __name__ == "__main__":
    unittest.main()
