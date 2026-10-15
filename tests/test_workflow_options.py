import glob
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import validate_mods


def real_metas():
    metas = {}
    for path in glob.glob(os.path.join(validate_mods.ROOT, "mods", "*", "mod.json")):
        with open(path, encoding="utf-8") as f:
            metas[os.path.basename(os.path.dirname(path))] = json.load(f)
    return metas


class WorkflowOptions(unittest.TestCase):
    def test_choices_match_the_public_mods(self):
        # a new public mod must be added to the "mod" choices of build.yml and publish.yml
        self.assertEqual(validate_mods.check_workflow_options(real_metas()), [])

    def test_a_missing_choice_is_reported(self):
        metas = real_metas()
        metas["brand-new-mod"] = {"name": "Brand new"}
        problems = validate_mods.check_workflow_options(metas)
        self.assertEqual(len(problems), 2)
        self.assertIn("Add the mod to the options", problems[0])

    def test_personal_mods_are_not_choices(self):
        metas = real_metas()
        metas["private-one"] = {"personal": True}
        self.assertEqual(validate_mods.check_workflow_options(metas), [])


if __name__ == "__main__":
    unittest.main()
