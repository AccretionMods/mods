import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from intake_auto import missing_hooks, patch_mod_json, publishable, version_key

HOOKS = [
    {
        "assembly": "Assembly-CSharp",
        "namespace": "",
        "class": "ExampleRoom",
        "method": "GetOutputWithBonus",
        "args": 1,
    },
    {
        "assembly": "UnityEngine.CoreModule",
        "namespace": "UnityEngine",
        "class": "Time",
        "method": "set_timeScale",
        "args": 1,
    },
]
ROOT = os.path.join(os.path.dirname(__file__), "..")


class Hooks(unittest.TestCase):
    def test_all_present(self):
        meta = b"x\0ExampleRoom\0GetOutputWithBonus\0Time\0set_timeScale\0UnityEngine\0y"
        self.assertEqual(missing_hooks(meta, HOOKS), [])

    def test_renamed_method_is_reported(self):
        meta = b"\0ExampleRoom\0GetOutput\0Time\0set_timeScale\0UnityEngine\0"
        self.assertEqual(
            missing_hooks(meta, HOOKS), ["method ExampleRoom.GetOutputWithBonus"]
        )

    def test_whole_name_only(self):
        meta = b"\0MyExampleRoomX\0GetOutputWithBonus\0Time\0set_timeScale\0UnityEngine\0"
        self.assertTrue(missing_hooks(meta, HOOKS))

    def test_no_metadata(self):
        self.assertEqual(len(missing_hooks(None, HOOKS)), 1)

    def test_every_hooks_file_in_the_checkout_parses(self):
        # Personal mods stay out of git, so CI may find none; local checkouts check theirs.
        folder = os.path.join(ROOT, "mods")
        for mod in sorted(os.listdir(folder)):
            path = os.path.join(folder, mod, "hooks.json")
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as f:
                hooks = json.load(f)["hooks"]
            self.assertTrue(
                all({"class", "method", "namespace"} <= set(h) for h in hooks), mod
            )


class Personal(unittest.TestCase):
    def test_personal_mods_are_never_published(self):
        self.assertFalse(publishable({"personal": True}))
        self.assertTrue(publishable({}))
        self.assertTrue(publishable({"personal": False}))


class Patch(unittest.TestCase):
    TEXT = (
        '{\n  "tested_app_version": "2.6.0",\n  "base": {\n    "sha256": "'
        + "a" * 64
        + '"\n  }\n}\n'
    )

    def test_patch(self):
        out = patch_mod_json(self.TEXT, "a" * 64, "b" * 64, "2.7.0")
        self.assertEqual(json.loads(out)["base"]["sha256"], "b" * 64)
        self.assertEqual(json.loads(out)["tested_app_version"], "2.7.0")
        self.assertEqual(out.count("\n"), self.TEXT.count("\n"))

    def test_wrong_old_sha(self):
        with self.assertRaises(ValueError):
            patch_mod_json(self.TEXT, "c" * 64, "b" * 64, "2.7.0")

    def test_version_order(self):
        self.assertLess(version_key("2.6.0"), version_key("2.10.0"))


if __name__ == "__main__":
    unittest.main()
