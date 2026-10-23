"""dev.py post: the release post only for a fully validated mod, written locally, later posts as updates."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import dev

TEMPLATE = "{logo}\nModded/Hacked App: {app} By {seller}\nBundle ID: {bundle}\nLink: {store_link}\nFeatures\n{features}\nCredits\n{credits}\n"


def tweak(key, title, works=True, version="1.0"):
    t = {"key": key, "title": title, "kind": "switch", "hooks": [], "pins": []}
    if works is not None:
        t["verified"] = {"result": "works" if works else "fails", "app_version": version, "date": "2026-10-22"}
    return t


class Post(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repo, self.am = root / "repo", root / "am"
        self.mod = self.repo / "mods" / "game-plus"
        self.mod.mkdir(parents=True)
        (self.am / "templates").mkdir(parents=True)
        (self.am / "templates" / "release_post.md").write_text(TEMPLATE, encoding="utf-8")
        self.meta = {"name": "Game+", "display_name": "Game+", "version": "1.0.0", "target_bundle_id": "com.example.game",
                     "tested_app_version": "1.0"}
        self.write([tweak("a", "Alpha"), tweak("b", "Beta", works=None)])
        self.old = (dev.ROOT, os.environ.get("AM_ROOT"))
        dev.ROOT, os.environ["AM_ROOT"] = self.repo, str(self.am)
        app = {"artworkUrl512": "https://x/icon.png", "trackName": "Game", "sellerName": "Maker", "trackViewUrl": "https://apps/1"}
        self.lookup = mock.patch.object(dev.games, "lookup", lambda **kw: [app])
        self.lookup.start()

    def tearDown(self):
        self.lookup.stop()
        dev.ROOT, env = self.old
        if env is None:
            os.environ.pop("AM_ROOT", None)
        else:
            os.environ["AM_ROOT"] = env
        self.tmp.cleanup()

    def write(self, tweaks):
        (self.mod / "mod.json").write_text(json.dumps(self.meta))
        (self.mod / "tweaks.json").write_text(json.dumps({"title": "Game+", "tweaks": tweaks}))

    def post(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dev.main(["post", "game-plus", *extra])
        return code, out.getvalue() + err.getvalue()

    def test_only_when_every_switch_works_then_updates(self):
        code, text = self.post()
        self.assertEqual(code, 1)
        self.assertIn("1 of 2", text)
        code, text = self.post("--draft")
        self.assertEqual(code, 0)
        self.assertIn("DRAFT, not validated yet: b", text)
        self.assertNotIn("posted", json.loads((self.am / "dev" / "game-plus.json").read_text()) if (self.am / "dev" / "game-plus.json").exists() else {})
        self.write([tweak("a", "Alpha"), tweak("b", "Beta")])
        code, text = self.post()
        self.assertEqual(code, 0)
        posted = (self.am / "posts" / "game-plus-1.0.0.txt").read_text(encoding="utf-8")
        self.assertIn("Modded/Hacked App: Game By Maker", posted)
        self.assertIn("- Alpha\n- Beta", posted)
        self.assertIn("- RaZza_0X\n- [url=https://getaccretion.app]Accretion[/url]", posted)
        self.assertNotIn("Update", posted)
        # a new version with one more validated switch: an update post that marks it
        self.meta["version"] = "1.1.0"
        self.write([tweak("a", "Alpha"), tweak("b", "Beta"), tweak("c", "Gamma")])
        code, text = self.post()
        update = (self.am / "posts" / "game-plus-1.1.0.txt").read_text(encoding="utf-8")
        self.assertTrue(update.startswith("🔄 Update: Game+ 1.0.0 → 1.1.0 (game 1.0), 1 new"))
        self.assertIn("- Gamma (NEW)", update)
        self.assertIn("- Alpha\n", update)

    def test_screenshot_links_fill_the_link_only_line(self):
        (self.am / "templates" / "release_post.md").write_text(TEMPLATE + "Screenshots\nLINK ONLY\n", encoding="utf-8")
        state = {"shots": [{"id": "a", "url": "https://shots/new", "expires_at": "2099-01-01T00:00:00"},
                           {"id": "b", "url": "https://shots/old", "expires_at": "2000-01-01T00:00:00"}]}
        dev.save_state("game-plus", state)
        code, text = self.post("--draft")
        self.assertEqual(code, 0)
        self.assertIn("Screenshots\nhttps://shots/new\n", text)
        self.assertNotIn("https://shots/old", text, "an expired link stays out")
        dev.save_state("game-plus", {})
        code, text = self.post("--draft")
        self.assertIn("Screenshots\nLINK ONLY\n", text, "no links: the template's line stays")

    def test_a_switch_validated_on_an_older_game_version_does_not_count(self):
        self.write([tweak("a", "Alpha", version="0.9")])
        code, text = self.post()
        self.assertEqual(code, 1)
        self.assertIn("(0 of 1): a.", text)


if __name__ == "__main__":
    unittest.main()
