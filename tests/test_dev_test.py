"""dev.py test: rounds per menu section, and which switches reached the game from the device-log lines."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import dev

TWEAKS = {
    "tweaks": [
        {"key": "a", "title": "A", "section": "Combat", "pins": [{"object": "X.i", "field": "f"}]},
        {"key": "b", "title": "B", "section": "Combat", "pins": [{"object": "X.i", "field": "g"}]},
        {"key": "c", "title": "C", "section": "Items", "pins": [{"object": "X.i", "field": "h"}]},
        {"key": "awake", "title": "Keep awake", "section": "Other"},  # no pins: nothing to test from the PC
    ]
}


class Rounds(unittest.TestCase):
    def test_one_round_per_section_or_the_chosen_ones(self):
        self.assertEqual(dev.test_rounds(TWEAKS), [("Combat", ["a", "b"]), ("Items", ["c"])])
        self.assertEqual(dev.test_rounds(TWEAKS, section="items"), [("Items", ["c"])])
        self.assertEqual(dev.test_rounds(TWEAKS, keys="c, a"), [("chosen", ["c", "a"])])
        with self.assertRaises(dev.DevError):
            dev.test_rounds(TWEAKS, keys="awake")
        with self.assertRaises(dev.DevError):
            dev.test_rounds(TWEAKS, section="Nope")


class Results(unittest.TestCase):
    def test_reached_once_wins_and_the_last_reason_otherwise(self):
        lines = [
            "2026-10-22 9Kings[1] <NOTICE>: [AccretionMods] test 1a2b on 3",
            "2026-10-22 9Kings[1] <NOTICE>: [AccretionMods] status a waiting: Start a run to see it work",
            "2026-10-22 9Kings[1] <NOTICE>: [AccretionMods] status a ok",
            "2026-10-22 9Kings[1] <NOTICE>: [AccretionMods] status a waiting: Start a run to see it work",
            "2026-10-22 9Kings[1] <NOTICE>: [AccretionMods] status b waiting: Needs an update (X.g): run am check",
            "2026-10-22 9Kings[1] <NOTICE>: [AccretionMods] status zz ok",
        ]
        got = dev.test_results(lines, ["a", "b", "c"])
        self.assertEqual(got["a"], "reached")
        self.assertEqual(got["b"], "waiting: Needs an update (X.g): run am check")
        self.assertEqual(got["c"], "no report")
        self.assertNotIn("zz", got)


if __name__ == "__main__":
    unittest.main()
