"""scripts/mod_tweaks.py: the one reader and writer of a mod's tweaks.json."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import mod_tweaks

TWEAK_X = """// The template's comment mentions am_godMode, which must not count.
static BOOL am_localFlag = NO;
static void tick(void) {
    UIApplication.sharedApplication.idleTimerDisabled = am_keepAwake;
    NSLog(@"am_notASwitch");
    if (am_fly && am_localFlag) { }
}
"""


class ModTweaks(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.mod = Path(self._tmp.name) / "game-plus"
        self.mod.mkdir()
        self.history = Path(self._tmp.name) / "history"
        (self.mod / "Tweak.x").write_text(TWEAK_X, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def menu(self, *keys):
        return {"title": "Game+", "tweaks": [{"key": k, "title": k, "kind": "switch", "hooks": []} for k in keys]}

    def test_save_puts_back_a_template_switch_tweak_x_uses(self):
        restored = mod_tweaks.save(self.mod, self.menu("fly"), history=self.history)
        self.assertEqual(restored, ["keepAwake"])
        keys = [t["key"] for t in mod_tweaks.load(self.mod)["tweaks"]]
        self.assertEqual(keys, ["keepAwake", "fly"])

    def test_hand_written_menu_without_it_is_left_alone(self):
        (self.mod / "Tweak.x").write_text("static void tick(void) { if (am_fly) {} }\n", encoding="utf-8")
        self.assertEqual(mod_tweaks.save(self.mod, self.menu("fly")), [])
        self.assertEqual([t["key"] for t in mod_tweaks.load(self.mod)["tweaks"]], ["fly"])

    def test_not_a_menu_is_refused_and_the_file_is_untouched(self):
        mod_tweaks.save(self.mod, self.menu("keepAwake", "fly"))
        before = mod_tweaks.path(self.mod).read_bytes()
        for bad in ({}, {"tweaks": {}}, {"tweaks": [{"title": "no key"}]}, self.menu("fly", "fly")):
            with self.assertRaises(mod_tweaks.TweaksFileError):
                mod_tweaks.save(self.mod, bad)
        self.assertEqual(mod_tweaks.path(self.mod).read_bytes(), before)

    def test_history_keeps_previous_versions_up_to_the_limit(self):
        for i in range(mod_tweaks.HISTORY_KEEP + 3):
            mod_tweaks.save(self.mod, self.menu("keepAwake", f"s{i}"), history=self.history)
        kept = sorted(self.history.glob("tweaks-*.json"))
        self.assertEqual(len(kept), mod_tweaks.HISTORY_KEEP)
        newest = json.loads(kept[-1].read_text(encoding="utf-8"))
        self.assertEqual(newest["tweaks"][1]["key"], f"s{mod_tweaks.HISTORY_KEEP + 1}")  # the one before the last save

    def test_written_with_lf_and_a_final_newline(self):
        mod_tweaks.save(self.mod, self.menu("keepAwake"))
        raw = mod_tweaks.path(self.mod).read_bytes()
        self.assertNotIn(b"\r\n", raw)
        self.assertTrue(raw.endswith(b"}\n"))

    def test_undeclared_ignores_comments_strings_and_own_definitions(self):
        mod_tweaks.save(self.mod, self.menu("keepAwake"))
        self.assertEqual(mod_tweaks.undeclared(self.mod), ["am_fly"])
        data = self.menu("keepAwake", "fly")
        self.assertEqual(mod_tweaks.undeclared(self.mod, data), [])


if __name__ == "__main__":
    unittest.main()
