import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import dev
import gen_tweaks

HOOKS = {
    "engine": "unity-il2cpp",
    "hooks": [
        {
            "assembly": "Assembly-CSharp",
            "namespace": "",
            "class": "Enemy",
            "method": "DamagePlayer",
            "args": 2,
            "return_type": "void",
            "signature": "public void DamagePlayer(long dmg, bool crit)",
            "needs_jit": True,
        },
        {
            "assembly": "Assembly-CSharp",
            "namespace": "",
            "class": "Enemy",
            "method": "get_AttackSuccessRate",
            "args": 0,
            "return_type": "float",
            "signature": "public float get_AttackSuccessRate()",
            "needs_jit": True,
        },
        {
            "assembly": "Assembly-CSharp",
            "namespace": "",
            "class": "ItemStack",
            "method": "GetScaledDamage",
            "args": 0,
            "return_type": "long",
            "signature": "public long GetScaledDamage()",
            "needs_jit": True,
        },
        {
            "assembly": "Assembly-CSharp",
            "namespace": "Game.Core",
            "class": "Lock",
            "method": "Update",
            "args": 0,
            "return_type": "void",
            "signature": "private void Update()",
            "needs_jit": False,
        },
        {
            "assembly": "Assembly-CSharp",
            "namespace": "",
            "class": "Util",
            "method": "Roll",
            "args": 2,
            "return_type": "bool",
            "signature": "public static bool Roll(int chance, Kind kind)",
            "enums": ["Kind"],
        },
        {
            "assembly": "Assembly-CSharp",
            "namespace": "",
            "class": "Shop",
            "method": "Buy",
            "args": 1,
            "return_type": "void",
        },
    ],
}

TWEAKS = {
    "title": "Game+",
    "tweaks": [
        {
            "key": "keepAwake",
            "title": "Keep screen awake",
            "kind": "switch",
            "hooks": [],
        },
        {
            "key": "noDamage",
            "title": "No damage",
            "kind": "switch",
            "hooks": [{"method": "Enemy.DamagePlayer", "action": "skip"}],
        },
        {
            "key": "miss",
            "title": "Enemies miss",
            "kind": "switch",
            "hooks": [
                {
                    "method": "Enemy.get_AttackSuccessRate",
                    "action": "return",
                    "value": 0,
                }
            ],
        },
        {
            "key": "damage",
            "title": "Damage ×",
            "kind": "slider",
            "min": 1,
            "max": 20,
            "default": 1,
            "hooks": [{"method": "ItemStack.GetScaledDamage", "action": "multiply"}],
        },
        {
            "key": "lock",
            "title": "Lock tick",
            "kind": "switch",
            "hooks": [{"method": "Game.Core.Lock.Update", "action": "custom"}],
        },
        {
            "key": "roll",
            "title": "Always win",
            "kind": "switch",
            "hooks": [
                {"method": "Util.Roll", "action": "arg", "index": 0, "value": 100}
            ],
        },
    ],
}


class Signatures(unittest.TestCase):
    def test_instance_method_types(self):
        static, ret, args, _cs = gen_tweaks.parse_signature(HOOKS["hooks"][0])
        self.assertFalse(static)
        self.assertEqual(ret, "void")
        self.assertEqual(
            [(c, n) for c, n, _ in args], [("int64_t", "dmg"), ("bool", "crit")]
        )

    def test_static_method_and_enum(self):
        static, ret, args, _ = gen_tweaks.parse_signature(HOOKS["hooks"][4])
        self.assertTrue(static)
        self.assertEqual(ret, "bool")
        self.assertEqual(
            [c for c, _, _ in args], ["int32_t", "int32_t"]
        )  # the enum is an int

    def test_reference_types_are_pointers(self):
        self.assertEqual(gen_tweaks.ctype("Items"), "void *")
        self.assertEqual(gen_tweaks.ctype("int[]"), "void *")
        self.assertEqual(gen_tweaks.ctype("List<Items>"), "void *")
        self.assertEqual(gen_tweaks.ctype("float"), "float")

    def test_missing_signature_is_an_error(self):
        with self.assertRaises(gen_tweaks.TweaksError):
            gen_tweaks.parse_signature(HOOKS["hooks"][5])


class Problems(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(gen_tweaks.problems(TWEAKS, HOOKS), [])

    def check(self, tweak, hooks=HOOKS):
        data = {"title": "T", "tweaks": [tweak]}
        return gen_tweaks.problems(data, hooks)

    def test_unknown_method(self):
        found = self.check(
            {
                "key": "a",
                "title": "A",
                "hooks": [{"method": "Nope.Thing", "action": "skip"}],
            }
        )
        self.assertTrue(any("not in hooks.json" in p for p in found))

    def test_skip_needs_void(self):
        found = self.check(
            {
                "key": "a",
                "title": "A",
                "hooks": [{"method": "ItemStack.GetScaledDamage", "action": "skip"}],
            }
        )
        self.assertTrue(any("skip needs a void" in p for p in found))

    def test_return_needs_value_and_a_number(self):
        found = self.check(
            {
                "key": "a",
                "title": "A",
                "hooks": [
                    {"method": "Enemy.get_AttackSuccessRate", "action": "return"}
                ],
            }
        )
        self.assertTrue(any('needs "value"' in p for p in found))
        found = self.check(
            {
                "key": "a",
                "title": "A",
                "hooks": [
                    {"method": "Enemy.DamagePlayer", "action": "return", "value": 1}
                ],
            }
        )
        self.assertTrue(any("return needs a bool or numeric" in p for p in found))

    def test_arg_index_range(self):
        found = self.check(
            {
                "key": "a",
                "title": "A",
                "hooks": [
                    {
                        "method": "Enemy.DamagePlayer",
                        "action": "arg",
                        "index": 2,
                        "value": 0,
                    }
                ],
            }
        )
        self.assertTrue(any("index 0..1" in p for p in found))

    def test_one_method_one_tweak(self):
        data = {
            "title": "T",
            "tweaks": [
                {
                    "key": "a",
                    "title": "A",
                    "hooks": [{"method": "Enemy.DamagePlayer", "action": "skip"}],
                },
                {
                    "key": "b",
                    "title": "B",
                    "hooks": [{"method": "Enemy.DamagePlayer", "action": "skip"}],
                },
            ],
        }
        self.assertTrue(
            any("already hooked" in p for p in gen_tweaks.problems(data, HOOKS))
        )

    def test_bad_key_and_duplicate(self):
        data = {
            "title": "T",
            "tweaks": [
                {"key": "1x", "title": "A"},
                {"key": "ok", "title": "A"},
                {"key": "ok", "title": "B"},
            ],
        }
        found = gen_tweaks.problems(data, HOOKS)
        self.assertTrue(any("C identifier" in p for p in found))
        self.assertTrue(any("duplicate key" in p for p in found))

    def test_signature_without_types_reports_the_entry(self):
        found = self.check(
            {
                "key": "a",
                "title": "A",
                "hooks": [{"method": "Shop.Buy", "action": "skip"}],
            }
        )
        self.assertTrue(any('no "signature"' in p for p in found))


class Render(unittest.TestCase):
    def setUp(self):
        self.h, self.m = gen_tweaks.render(TWEAKS, HOOKS, "game-plus")

    def test_values(self):
        self.assertIn("BOOL am_noDamage = NO;", self.m)
        self.assertIn("float am_damage = 1.0f;", self.m)
        self.assertIn("extern float am_damage;", self.h)

    def test_skip(self):
        self.assertIn(
            "if (am_noDamage) return;\n    orig_Enemy_DamagePlayer(self, dmg, crit, mi);",
            self.m,
        )

    def test_return(self):
        self.assertIn("if (am_miss) return 0.0f;", self.m)

    def test_multiply_by_slider_when_not_default(self):
        self.assertIn(
            "(am_damage != 1.0f) ? (int64_t)(value * am_damage) : value", self.m
        )

    def test_arg_on_static_method_has_no_self(self):
        self.assertIn(
            "static bool hook_Util_Roll(int32_t chance, int32_t kind, const void *mi)",
            self.m,
        )
        self.assertIn("if (am_roll) chance = 100;", self.m)
        self.assertIn("return orig_Util_Roll(chance, kind, mi);", self.m)

    def test_custom_hook_is_declared_not_defined(self):
        self.assertIn(
            "void hook_Game_Core_Lock_Update(void *self, const void *mi);  // defined in Tweak.x",
            self.h,
        )
        self.assertNotIn("static void hook_Game_Core_Lock_Update", self.m)
        self.assertIn(
            'AMIl2cppRegister("Game.Core.Lock.Update", (void *)hook_Game_Core_Lock_Update',
            self.m,
        )

    def test_menu_items_and_jit_label(self):
        self.assertIn('[menu addSwitch:@"keepAwake" title:@"Keep screen awake"', self.m)
        self.assertIn('title:[@"No damage" stringByAppendingString:jit]', self.m)
        self.assertIn(
            'title:@"Lock tick"', self.m
        )  # its hook does not need JIT: no label
        self.assertIn(
            'addSlider:@"damage" title:[@"Damage ×" stringByAppendingString:jit] min:1.0f max:20.0f defaultValue:1.0f',
            self.m,
        )

    def test_registrations_cover_every_hook(self):
        self.assertEqual(self.m.count("AMIl2cppRegister("), 5)


class Generate(unittest.TestCase):
    def test_writes_files_once_and_reports_problems(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "hooks.json"), "w", encoding="utf-8") as f:
                json.dump(HOOKS, f)
            with open(os.path.join(folder, "tweaks.json"), "w", encoding="utf-8") as f:
                json.dump(TWEAKS, f)
            self.assertEqual(gen_tweaks.generate(folder), [])
            path = os.path.join(folder, "AMTweaks.gen.m")
            first = os.path.getmtime(path)
            gen_tweaks.generate(folder)
            self.assertEqual(
                os.path.getmtime(path), first
            )  # unchanged output is not rewritten
            bad = dict(
                TWEAKS,
                tweaks=[
                    {
                        "key": "x",
                        "title": "X",
                        "hooks": [{"method": "Nope.A", "action": "skip"}],
                    }
                ],
            )
            with open(os.path.join(folder, "tweaks.json"), "w", encoding="utf-8") as f:
                json.dump(bad, f)
            self.assertTrue(gen_tweaks.generate(folder))


class DevHelpers(unittest.TestCase):
    def test_paths(self):
        self.assertEqual(
            dev.wsl_path("C:\\AccretionMods\\builds"), "/mnt/c/AccretionMods/builds"
        )
        self.assertEqual(dev.wsl_path("/home/x/mods"), "/home/x/mods")
        self.assertEqual(
            dev.win_path("/mnt/c/AccretionMods/builds/a.ipa"),
            "C:/AccretionMods/builds/a.ipa",
        )

    def test_key_for(self):
        self.assertEqual(dev.key_for("Enemy.DamagePlayer"), "enemyDamagePlayer")
        self.assertEqual(dev.key_for("StatHandler.get_Hunger"), "statHandlerHunger")
        self.assertEqual(dev.key_for("Game.Core.Lock.Update"), "lockUpdate")

    def test_parse_value(self):
        self.assertIs(dev.parse_value("true"), True)
        self.assertEqual(dev.parse_value("99"), 99)
        self.assertEqual(dev.parse_value("1.5"), 1.5)
        self.assertIsNone(dev.parse_value(None))

    def test_fields_regex(self):
        m = dev.FIELD.match("\tpublic static AdinmoHandler instance; // 0x0")
        self.assertEqual((m.group(3), m.group(4)), ("instance", "0x0"))
        self.assertIn("static", m.group(1))
        m = dev.FIELD.match("\tprivate long <Damage>k__BackingField; // 0x38")
        self.assertEqual(m.group(3), "<Damage>k__BackingField")


if __name__ == "__main__":
    unittest.main()
