"""Scan presentation and alternatives: synthetic metadata, no phone."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "studio"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import scan_view


class Filters(unittest.TestCase):
    def test_unity_callback_requires_instance_and_correct_signature(self):
        method = {
            "method": "Update",
            "return_type": "void",
            "signature": "public void Update()",
            "params": [],
        }
        self.assertTrue(scan_view.unity_callback(method, True))
        self.assertFalse(scan_view.unity_callback(method, False))
        self.assertFalse(
            scan_view.unity_callback(
                dict(method, signature="public static void Update()"), True
            )
        )
        self.assertFalse(
            scan_view.unity_callback(dict(method, params=["int value"]), True)
        )
        collision = dict(method, method="OnCollisionEnter", params=["Collision other"])
        self.assertTrue(scan_view.unity_callback(collision, True))

    def test_verification_is_exact_target_and_game_version(self):
        rows = [
            {"class": "Stats", "member": "set_Health", "object": "Stats.instance"},
            {"class": "Stats", "member": "get_Health", "object": "Stats"},
        ]
        tweaks = {
            "tweaks": [
                {
                    "key": "health",
                    "pins": [{"object": "Stats.instance", "setter": "set_Health"}],
                    "verified": {"result": "works", "app_version": "1.0"},
                }
            ]
        }
        current = scan_view.attach_verification(rows, tweaks, "1.0")
        self.assertEqual(current[0]["verification"]["state"], "works")
        self.assertEqual(current[1]["verification"]["state"], "untested")
        self.assertEqual(
            scan_view.attach_verification(rows, tweaks, "2.0")[0]["verification"][
                "state"
            ],
            "stale",
        )

    def test_nojit_property_alternative_does_not_relabel_direct_hook(self):
        from il2cpp_dump import Method

        get = Method(
            "Assembly-CSharp",
            "",
            "Stats",
            "get_Health",
            "float",
            [],
            "0x10",
            "public float get_Health()",
        )
        set_ = Method(
            "Assembly-CSharp",
            "",
            "Stats",
            "set_Health",
            "void",
            ["float value"],
            "0x20",
            "public void set_Health(float value)",
        )
        classes = {
            "Stats": {
                "statics": ["instance"],
                "fields": {},
                "base": "",
                "object_getters": {},
            }
        }
        method = {
            **get.hook(),
            "full_name": get.full_name,
            "class": "Stats",
            "method": get.name,
            "params": [],
            "kind": "float",
            "slot": None,
        }
        rows = scan_view.annotate(
            [
                {
                    "class": "Stats",
                    "member": get.name,
                    "method": method,
                    "inspect_only": True,
                }
            ]
        )
        result = scan_view.add_alternatives(rows, classes, [get, set_])[0]
        self.assertTrue(result["method"]["needs_jit"])
        self.assertEqual(result["nojit_alternatives"][0]["member"], "set_Health")
        self.assertEqual(result["nojit_alternatives"][0]["object"], "Stats.instance")
        self.assertEqual(result["nojit_alternatives"][0]["target_class"], "Stats")
        self.assertTrue(result["nojit"])
        cross = {"Owner": {"references": {"Data": "Stats"}}, "Stats": {"fields": {}}}
        alternative = scan_view.add_alternatives(rows, cross, [get, set_])[0][
            "nojit_alternatives"
        ][0]
        self.assertEqual(
            (alternative["object"], alternative["target_class"]),
            ("Owner.Data", "Stats"),
        )
        self.assertEqual(
            scan_view.add_alternatives(rows, classes, [get, get, set_])[0][
                "nojit_alternatives"
            ],
            [],
        )
        classes["Stats"]["statics"] = []
        self.assertEqual(
            scan_view.add_alternatives(rows, classes, [get, set_])[0][
                "nojit_alternatives"
            ],
            [],
        )

    def test_cross_class_reference_and_one_hop_getter_paths(self):
        classes = {
            "Core.Wave": {"references": {"Data": "GameplayData"}},
            "Core.GameplayData": {"object_getters": {"get_Save": "Save"}},
            "Core.Save": {},
        }
        paths = scan_view.object_paths(classes)
        self.assertEqual(paths["Core.GameplayData"]["object"], "Core.Wave.Data")
        self.assertEqual(paths["Core.Save"]["via"], "get_Save")
        self.assertEqual(paths["Core.Save"]["target_class"], "Core.Save")
        inherited = scan_view.object_paths(
            {"Core.Base": {"base": "MonoBehaviour"}, "Core.Child": {"base": "Base"}}
        )
        self.assertEqual(inherited["Core.Child"]["object"], "Core.Child[]")

    def test_groups_property_variants_without_losing_overloads(self):
        rows = [
            dict(
                {"class": "Store", "member": name, "capability": cap, "has": False},
                signature=signature,
            )
            for name, cap, signature in (
                ("Owned", "pin", "bool Owned"),
                ("get_Owned", "hook", "bool get_Owned()"),
                ("set_Owned", "hook", "void set_Owned(bool)"),
                ("set_Owned", "hook", "void set_Owned(int)"),
            )
        ]
        grouped = scan_view.group_rows(rows)
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["member"], "Owned")
        self.assertEqual(len(grouped[0]["variants"]), 4)

    def test_pages_do_not_repeat_rows(self):
        rows = [{"member": str(i)} for i in range(7)]
        first = scan_view.page(rows, 0, 3)
        second = scan_view.page(rows, first["next_offset"], 3)
        self.assertEqual([r["member"] for r in second["rows"]], ["3", "4", "5"])
        self.assertTrue(second["has_more"])

    def test_capability_filters_keep_direct_hooks_out_of_nojit(self):
        rows = [
            {"class": "A", "member": "Health", "inspect_only": False},
            {
                "class": "A",
                "member": "get_Health",
                "inspect_only": True,
                "method": {"needs_jit": True, "slot": None},
            },
            {"class": "A", "member": "Owned", "inspect_only": True},
        ]
        prepared = scan_view.annotate(rows)
        self.assertEqual(
            [r["capability"] for r in prepared], ["pin", "hook", "investigate"]
        )
        self.assertEqual(len(scan_view.filter_rows(prepared, "no-jit")), 1)
        self.assertEqual(len(scan_view.filter_rows(prepared, "hook")), 1)
