import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import embed_hooks
import validate_mods

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")

HOOKS = {
    "engine": "unity-il2cpp",
    "hooks": [
        {"assembly": "Assembly-CSharp", "namespace": "Game", "class": "Player", "method": "TakeDamage", "args": 2},
        {"namespace": "", "class": "Weird\"Name", "method": "Go"},
    ],
}


class EmbedHooks(unittest.TestCase):
    def test_renders_table(self):
        out = embed_hooks.render(HOOKS)
        self.assertIn('{"Assembly-CSharp", "Game", "Player", "TakeDamage", 2},', out)
        self.assertIn('{"", "", "Weird\\"Name", "Go", -1},', out)
        self.assertIn("am_hook_spec_count = 2;", out)

    def test_non_ascii_is_escaped(self):
        data = {"hooks": [{"namespace": "", "class": "Ü", "method": "Go"}]}
        self.assertIn('"\\303\\234"', embed_hooks.render(data))

    def test_empty_hooks_still_valid_c(self):
        self.assertIn("am_hook_spec_count = 0;", embed_hooks.render({"hooks": []}))

    def test_invalid_hooks_file_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "hooks.json")
            with open(src, "w") as f:
                json.dump({"engine": "other", "hooks": []}, f)
            with self.assertRaises(SystemExit):
                embed_hooks.main([src, os.path.join(d, "out.c")])

    def test_writes_file(self):
        with tempfile.TemporaryDirectory() as d:
            src, dest = os.path.join(d, "hooks.json"), os.path.join(d, "out.c")
            with open(src, "w") as f:
                json.dump(HOOKS, f)
            embed_hooks.main([src, dest])
            with open(dest) as f:
                self.assertIn("am_hook_specs", f.read())


class MakefileCheck(unittest.TestCase):
    def check(self, makefile):
        with tempfile.TemporaryDirectory() as d:
            if makefile is not None:
                with open(os.path.join(d, "Makefile"), "w") as f:
                    f.write(makefile)
            return validate_mods.runtime_problems(d)

    def test_full_file_list_passes(self):
        files = " ".join(validate_mods.RUNTIME_FILES)
        self.assertEqual(self.check(f"Game_FILES = Tweak.x menu/AMMenu.m {files}\n"), [])

    def test_missing_runtime_is_reported(self):
        problems = self.check("Game_FILES = Tweak.x menu/AMMenu.m\n")
        self.assertEqual(len(problems), 1)
        self.assertIn("runtime/am_hooks_data.c", problems[0])

    def test_mod_without_makefile_is_not_checked(self):
        self.assertEqual(self.check(None), [])


@unittest.skipUnless(CC, "needs a C compiler")
class Runtime(unittest.TestCase):
    """Compiles runtime/am_il2cpp.c against a fake IL2CPP VM and runs it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.exe = os.path.join(cls.tmp, "harness")
        subprocess.run(
            [CC, "-Wall", "-Werror", "-I", os.path.join(ROOT, "runtime"),
             os.path.join(ROOT, "tests", "il2cpp_runtime_harness.c"),
             os.path.join(ROOT, "runtime", "am_il2cpp.c"), "-o", cls.exe, "-ldl"],
            check=True,
        )
        cls.out = subprocess.run([cls.exe], check=True, capture_output=True, text=True).stdout.splitlines()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_ready(self):
        self.assertIn("ready=1", self.out)

    def test_found_methods_are_hooked(self):
        self.assertIn("hooked Game.Player.TakeDamage", self.out)
        self.assertIn("hooked Game.Player.GetSpeed", self.out)

    def test_missing_methods_are_skipped_and_logged(self):
        for name in ("Game.Player.Gone", "Game.Gone.TakeDamage"):
            self.assertIn(f"skipped {name}: method not found in this version", self.out)

    def test_wrong_assembly_and_arg_count_are_missing(self):
        self.assertEqual(
            self.out.count("skipped Game.Player.TakeDamage: method not found in this version"), 2
        )

    def test_unregistered_and_unused_are_logged(self):
        self.assertIn("skipped Game.Player.Unregistered: no replacement registered", self.out)
        self.assertIn("ignored replacement Player.Gone2: not in hooks.json", self.out)

    def test_report_counts_and_originals(self):
        self.assertIn("hooked=2 missing=4 unregistered=1 unused=1 failed=0", self.out)
        self.assertIn("orig_a=1 orig_b=1", self.out)


if __name__ == "__main__":
    unittest.main()
