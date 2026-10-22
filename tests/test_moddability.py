"""scripts/moddability.py, dev.py report/start, and Studio's live switch states, on made-up files."""

import contextlib
import io
import json
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
import moddability


def macho(cryptid):
    cmd = struct.pack("<IIIIII", 0x2C, 24, 0x4000, 0x1000, cryptid, 0)
    return struct.pack("<IiiIIIII", 0xFEEDFACF, 0x0100000C, 0, 2, 1, len(cmd), 0, 0) + cmd


def ipa(path, unity=True, cryptid=0, metadata=b"", bundle="com.example.game"):
    with zipfile.ZipFile(path, "w") as z:
        app = "Payload/Game.app"
        z.writestr(f"{app}/Info.plist", plistlib.dumps({"CFBundleIdentifier": bundle, "CFBundleExecutable": "Game",
                                                         "CFBundleShortVersionString": "1.2", "CFBundleName": "Game"}))
        z.writestr(f"{app}/Game", macho(cryptid))
        if unity:
            z.writestr(f"{app}/Frameworks/UnityFramework.framework/Info.plist", b"x")
            z.writestr(f"{app}/Data/Managed/Metadata/global-metadata.dat",
                       struct.pack("<Ii", 0xFAB11BAF, 39) + metadata)
    return path


class Report(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_unity_game_with_ads_and_purchases(self):
        f = moddability.inspect_ipa(ipa(self.dir / "a.ipa", metadata=b"..UnityEngine.Purchasing..GoogleMobileAds..PlayFab.."))
        self.assertEqual((f["engine"], f["metadata_version"], f["decrypted"]), ("Unity IL2CPP", 39, True))
        self.assertEqual(f["markers"]["ads"], ["GoogleMobileAds"])
        self.assertIn("in-app purchases", f["markers"])
        level, lines = moddability.verdict(f)
        self.assertEqual(level, "good")
        self.assertTrue(any("informational" in l for l in lines))
        scan = {"rows": [{"score": 2, "has": False}], "flagged": [], "classes": 3}
        self.assertEqual(moddability.verdict(f, scan)[0], "limited")  # few strong candidates

    def test_multiplayer_is_informational_and_other_engines_or_encrypted_unsupported(self):
        f = moddability.inspect_ipa(ipa(self.dir / "m.ipa", metadata=b"Photon..PhotonNetwork"))
        self.assertEqual(moddability.verdict(f)[0], "good")
        f = moddability.inspect_ipa(ipa(self.dir / "n.ipa", unity=False))
        self.assertEqual((f["engine"], moddability.verdict(f)[0]), ("native or another engine", "not supported"))
        f = moddability.inspect_ipa(ipa(self.dir / "e.ipa", cryptid=1))
        level, lines = moddability.verdict(f)
        self.assertEqual(level, "not supported")
        self.assertIn("encrypted", lines[0])

    def test_dev_report_and_start(self):
        root = self.dir / "am"
        repo = self.dir / "repo"
        (repo / "mods").mkdir(parents=True)
        game = ipa(self.dir / "g.ipa")
        old = (dev.ROOT, os.environ.get("AM_ROOT"))
        dev.ROOT, os.environ["AM_ROOT"] = repo, str(root)
        made = []

        def fake_new(args):  # the real one runs Il2CppDumper
            made.append(args)
            (repo / "mods" / args.name).mkdir()
            (repo / "mods" / args.name / "mod.json").write_text(json.dumps({"name": args.title, "target_bundle_id": "com.example.game"}))

        scan = {"rows": [{"area": "Combat", "class": "Skills", "member": "Accuracy", "object": "Skills.instance",
                          "value": "100", "when": "always", "score": 8, "has": False}] * 6, "flagged": [], "classes": 2}
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out), mock.patch.object(dev, "cmd_new", fake_new), \
                    mock.patch.object(dev, "dump_path", lambda mod: game), \
                    mock.patch.object(dev.dump_scan, "scan", lambda *a: scan):
                self.assertEqual(dev.main(["report", str(game)]), 0)
                self.assertEqual(dev.main(["start", str(game)]), 0)
                self.assertEqual(dev.main(["start", str(game)]), 1)  # a mod for this game exists now
            text = out.getvalue()
            self.assertIn("verdict: GOOD", text)
            self.assertEqual((made[0].name, made[0].title), ("game-plus", "Game+"))
            self.assertIn("making mods/game-plus", text)
            self.assertIn("am pin game-plus Skills.Accuracy", text)
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(dev.main(["start", str(ipa(self.dir / "x.ipa", unity=False, bundle="com.x"))]), 1)
            self.assertIn("NOT SUPPORTED", out.getvalue())
        finally:
            dev.ROOT, env = old
            if env is None:
                os.environ.pop("AM_ROOT", None)
            else:
                os.environ["AM_ROOT"] = env


class Live(unittest.TestCase):
    def test_states_from_log_jobs(self):
        import server

        job = server.Job("log", ["game-plus", "--seconds", "60"])
        job.status = "running"
        job.lines = [
            "2026 TheWanderer[1] <NOTICE>: [AccretionMods] status keepStats ok",
            "2026 TheWanderer[1] <NOTICE>: [AccretionMods] status enemiesMiss waiting: Start a fight to see it work",
            "2026 TheWanderer[1] <NOTICE>: [AccretionMods] status keepStats off",
            "unrelated line",
        ]
        other = server.Job("log", ["other-mod"])
        other.lines = ["[AccretionMods] status keepStats ok"]
        old = dict(server.JOBS)
        server.JOBS.clear()
        server.JOBS.update({"a": job, "b": other})
        try:
            live = server.live_status("game-plus")
        finally:
            server.JOBS.clear()
            server.JOBS.update(old)
        self.assertTrue(live["watching"])
        self.assertEqual(live["switches"]["keepStats"], {"state": "off", "reason": ""})  # the newest line wins
        self.assertEqual(live["switches"]["enemiesMiss"], {"state": "waiting", "reason": "Start a fight to see it work"})


if __name__ == "__main__":
    unittest.main()
