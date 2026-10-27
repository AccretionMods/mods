"""Install modes: the game's own ID or a separate copy; the signer's ID check; the installed ID; replacing the
original game only on an explicit yes; mod.json (the catalogue identity) never changed."""

import argparse
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import dev

META = {"target_bundle_id": "com.example.game", "bundle_id": "io.github.accretionmods.gameplus"}


class InstallModes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old = os.environ.get("AM_ROOT")
        os.environ["AM_ROOT"] = str(self.root)
        folder = self.root / "mods" / "game-plus"
        folder.mkdir(parents=True)
        self.meta_path = folder / "mod.json"
        self.meta_path.write_text(json.dumps(META), encoding="utf-8")
        p = mock.patch.object(dev, "mod_dir", lambda mod: self.root / "mods" / mod)
        p.start()
        self.addCleanup(p.stop)
        (self.root / "a.ipa").write_bytes(b"unsigned")

    def tearDown(self):
        if self.old is None:
            os.environ.pop("AM_ROOT", None)
        else:
            os.environ["AM_ROOT"] = self.old
        self.tmp.cleanup()

    def set_mode(self, mode):
        with redirect_stdout(io.StringIO()):
            dev.cmd_install_mode(argparse.Namespace(mod="game-plus", mode=mode))

    def test_projects_from_before_keep_separate_and_the_setting_never_touches_mod_json(self):
        self.assertEqual(dev.install_mode("game-plus"), "separate")
        self.assertEqual(dev.install_bundle("game-plus"), META["bundle_id"])
        before = self.meta_path.read_bytes()
        self.set_mode("original")
        self.assertEqual(dev.install_bundle("game-plus"), META["target_bundle_id"])
        self.assertEqual(self.meta_path.read_bytes(), before)  # store entries and releases keep bundle_id

    def test_new_mods_start_on_the_original_id(self):
        source = Path(dev.__file__).read_text(encoding="utf-8")
        self.assertIn('"install_mode": "original"', source.split("def cmd_new", 1)[1].split("\ndef ", 1)[0])

    def test_local_ipa_uses_bundle_id_unless_dev_py_says_otherwise(self):
        script = (Path(dev.__file__).parent / "local_ipa.sh").read_text(encoding="utf-8")
        self.assertIn('-b "${AM_INSTALL_BUNDLE:-$(field bundle_id)}"', script)

    def test_installed_id_prefers_the_verified_one_and_never_takes_the_original_game(self):
        apps = {"com.example.game": {}, "com.example.game.TEAM123": {}, "io.github.accretionmods.gameplus": {}}
        self.set_mode("original")
        self.assertEqual(dev.installed_id("game-plus", META, apps), "com.example.game.TEAM123")
        self.assertEqual(dev.installed_id("game-plus", META, {"com.example.game": {}}), None)  # the store's game
        state = dev.load_state("game-plus")
        state["installed_bundle"] = "com.example.game"  # Studio installed it with the game's ID
        dev.save_state("game-plus", state)
        self.assertEqual(dev.installed_id("game-plus", META, apps), "com.example.game")
        self.set_mode("separate")
        self.assertEqual(dev.installed_id("game-plus", META, {"io.github.accretionmods.gameplus": {}}),
                         "io.github.accretionmods.gameplus")

    def sign(self, signed_id, apps, replace=False, installs=None):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            if "apps" in cmd and "install" in cmd:
                apps[signed_id] = {}
            return mock.Mock(returncode=0)

        infos = {str(self.root / "a.ipa"): META["target_bundle_id"] if dev.install_mode("game-plus") == "original"
                 else META["bundle_id"], str(self.root / "a_signed.ipa"): signed_id}
        with mock.patch.object(dev, "WINDOWS", True), mock.patch.object(dev, "plumesign", return_value="plumesign"), \
                mock.patch.object(dev, "usbmux_device_id", return_value="dev"), \
                mock.patch.object(dev, "pymobiledevice3_cli", return_value="pmd3"), \
                mock.patch.object(dev, "ipa_info", lambda p: ({"CFBundleIdentifier": infos[str(p)]}, "Payload/G.app")), \
                mock.patch.object(dev, "phone_apps", lambda: dict(apps)), \
                mock.patch.object(dev.subprocess, "run", run), redirect_stdout(io.StringIO()) as out:
            try:
                dev.cmd_sign(argparse.Namespace(ipa=str(self.root / "a.ipa"), install=True, out=None, launch=False,
                                                mod="game-plus", replace_original=replace,
                                                apple_id=True))  # these cases are the Apple ID signer's
                error = None
            except dev.DevError as e:
                error = str(e)
        return calls, out.getvalue(), error

    def test_the_signer_changing_the_id_is_reported_and_the_phone_tools_follow_the_signed_id(self):
        self.set_mode("original")
        calls, out, error = self.sign("com.example.game.TEAM123", {})
        self.assertIsNone(error)
        self.assertIn("the signer changed the app ID: com.example.game -> com.example.game.TEAM123", out)
        state = dev.load_state("game-plus")
        self.assertEqual((state["signed_bundle"], state["installed_bundle"]), ("com.example.game.TEAM123",) * 2)
        self.assertFalse(any("--register-and-install" in c for c in calls))  # original mode: sign, look, install

    def test_replacing_the_original_game_needs_an_explicit_yes(self):
        self.set_mode("original")
        calls, out, error = self.sign("com.example.game", {"com.example.game": {}})
        self.assertIn("--replace-original", error)
        self.assertFalse(any("install" in c for c in calls if "pmd3" in c))  # nothing installed
        calls, out, error = self.sign("com.example.game", {"com.example.game": {}}, replace=True)
        self.assertIsNone(error)
        self.assertIn("replacing com.example.game on the phone (confirmed)", out)
        self.assertEqual(dev.load_state("game-plus")["installed_bundle"], "com.example.game")
        # the next install over Studio's own copy needs no question
        calls, out, error = self.sign("com.example.game", {"com.example.game": {}})
        self.assertIsNone(error)

    def test_separate_copies_keep_the_registered_install(self):
        calls, out, error = self.sign("io.github.accretionmods.gameplus.TEAM123", {})
        self.assertIsNone(error)
        self.assertTrue(any("--register-and-install" in c for c in calls))
        self.assertEqual(dev.load_state("game-plus")["installed_bundle"], "io.github.accretionmods.gameplus.TEAM123")


if __name__ == "__main__":
    unittest.main()
