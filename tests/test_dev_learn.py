import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import dev_learn
from il2cpp_dump import Method


def method(cls, name, ret="void", params=(), assembly="Assembly-CSharp", ns=""):
    sig = f"public {ret} {name}({', '.join(params)})"
    return Method(assembly, ns, cls, name, ret, list(params), "0x1000", sig)


ENTRY = {
    "assembly": "Assembly-CSharp",
    "namespace": "",
    "class": "Enemy",
    "method": "DamagePlayer",
    "args": 2,
    "return_type": "void",
}
TWEAK = {
    "key": "noDamage",
    "title": "Player takes no damage",
    "kind": "switch",
    "hooks": [{"method": "Enemy.DamagePlayer", "action": "skip"}],
}


class Store(unittest.TestCase):
    def test_record_accumulates_uses_and_marks(self):
        store = dev_learn.load("/nonexistent/patterns.json")
        p = dev_learn.record(store, ENTRY, "skip", None, TWEAK, "com.a.game")
        self.assertEqual(p["method"], "DamagePlayer")
        self.assertEqual(len(p["uses"]), 1)
        dev_learn.record(
            store, ENTRY, "skip", None, TWEAK, "com.a.game"
        )  # same game and class: one use
        self.assertEqual(len(p["uses"]), 1)
        dev_learn.record(
            store, dict(ENTRY, **{"class": "Boss"}), "skip", None, TWEAK, "com.b.game"
        )
        self.assertEqual(len(p["uses"]), 2)
        self.assertEqual(dev_learn.score(p), 2 * dev_learn.SCORE_USE)
        touched = dev_learn.mark(store, TWEAK, [ENTRY], "com.a.game", works=True)
        self.assertEqual(touched, ["DamagePlayer"])
        self.assertEqual(dev_learn.outcomes(p), (1, 0))
        dev_learn.mark(store, TWEAK, [ENTRY], "com.a.game", works=False)
        self.assertEqual(dev_learn.outcomes(p), (0, 1))

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "dev", "patterns.json")
            store = dev_learn.load(path)
            dev_learn.record(store, ENTRY, "skip", None, TWEAK, "com.a.game")
            dev_learn.save(path, store)
            again = dev_learn.load(path)
            self.assertIn(
                dev_learn.pattern_key("DamagePlayer", "void"), again["patterns"]
            )

    def test_learn_imports_hooks_with_actions_from_tweaks(self):
        store = dev_learn.load("/nonexistent")
        hooks = [ENTRY, dict(ENTRY, method="get_Health", return_type="float", args=0)]
        tweaks = {"tweaks": [TWEAK]}
        self.assertEqual(dev_learn.learn(store, hooks, "com.a.game", tweaks), 2)
        self.assertEqual(
            store["patterns"][dev_learn.pattern_key("DamagePlayer", "void")]["action"],
            "skip",
        )
        self.assertEqual(
            store["patterns"][dev_learn.pattern_key("get_Health", "float")]["action"],
            "custom",
        )


class Suggest(unittest.TestCase):
    def setUp(self):
        self.store = dev_learn.load("/nonexistent")
        dev_learn.record(self.store, ENTRY, "skip", None, TWEAK, "com.a.game", "works")
        dev_learn.record(
            self.store,
            dict(ENTRY, method="Update", args=0),
            "custom",
            None,
            {"key": "tick", "title": "Tick", "kind": "switch"},
            "com.a.game",
        )
        shop = dict(ENTRY, **{"class": "Shop", "method": "BuyGems"})
        dev_learn.record(
            self.store,
            shop,
            "skip",
            None,
            {"key": "x", "title": "X", "kind": "switch"},
            "com.a.game",
            "works",
        )

    def test_exact_match_first_then_similar_and_rules(self):
        found = [
            method("Boss", "DamagePlayer", params=("long dmg", "bool crit")),
            method("Boss", "DamagePlayerSlowly"),
            method("Hud", "Update"),  # a Unity callback: never suggested
            method("Store", "BuyGems"),  # topic labels do not exclude learned methods
            method(
                "Boss", "DamagePlayer", ret="int"
            ),  # another result kind: no exact pattern
            method("Boss", "Attack"),
        ]
        rows = dev_learn.suggest(self.store, found)
        names = [(m.full_name, exact) for _, m, _, exact in rows]
        self.assertEqual(names[0], ("Boss.DamagePlayer", True))
        self.assertIn(("Boss.DamagePlayerSlowly", False), names)
        self.assertNotIn("Hud.Update", [n for n, _ in names])
        self.assertIn("Store.BuyGems", [n for n, _ in names])
        self.assertNotIn("Boss.Attack", [n for n, _ in names])

    def test_used_methods_and_failed_patterns_are_skipped(self):
        found = [method("Boss", "DamagePlayer", params=("long dmg", "bool crit"))]
        self.assertEqual(
            dev_learn.suggest(self.store, found, used={"Boss.DamagePlayer"}), []
        )
        p = self.store["patterns"][dev_learn.pattern_key("DamagePlayer", "void")]
        p["uses"][0]["result"] = "fails"
        self.assertEqual(dev_learn.suggest(self.store, found), [])

    def test_plugin_assemblies_rank_below_game_code(self):
        found = [
            method(
                "Ad", "DamagePlayer", params=("long a", "bool b"), assembly="AdPlugin"
            ),
            method("Boss", "DamagePlayer", params=("long a", "bool b")),
        ]
        rows = dev_learn.suggest(self.store, found)
        self.assertEqual(rows[0][1].class_name, "Boss")

    def test_title_for(self):
        m = method("Boss", "get_AttackSuccessRate", ret="float")
        self.assertEqual(dev_learn.title_for(m, {}), "Attack Success Rate")
        self.assertEqual(
            dev_learn.title_for(m, {"title": "Enemies miss"}), "Enemies miss"
        )
        self.assertEqual(
            dev_learn.title_for(m, {"title": "Enemy.Thing"}), "Attack Success Rate"
        )

    def test_one_row_per_method_signature(self):
        store = {"patterns": {}}
        dev_learn.record(store, ENTRY, "skip", None, TWEAK, "game.a", "works")
        found = [method(c, "DamagePlayer", params=("int a", "int b")) for c in ("Boss", "Orc", "Wolf")]
        rows = dev_learn.suggest(store, found)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2]["others"], 2)


class Members(unittest.TestCase):
    """Pins are learned by the words of what they write, with whose value it is, and by their results."""

    PIN = {"object": "SkillsManager.instance", "field": "SkillPoints", "ctype": "int32_t", "value": 99, "when": "lower"}
    SKILL = {"key": "sp", "title": "Unlimited skill points", "kind": "switch", "pins": [PIN]}

    def test_names_fold_to_the_same_words(self):
        key = dev_learn.member_key
        self.assertEqual(key("_skillPoints", "int32_t"), key("set_SkillPoints", "int32_t"))
        self.assertEqual(key("<Currency>k__BackingField", "int32_t"), key("m_Currency", "int32_t"))
        self.assertNotEqual(key("CanStun", "bool"), key("CanStun", "int32_t"))
        # whose value: an enemy's damage is not the companion's
        self.assertEqual(key("BaseDamage", "int64_t", "Enemy"), key("EnemyDamage", "int64_t", "Player"))
        self.assertNotEqual(key("Damage", "int64_t", "FightCompanion"), key("Damage", "int64_t", "Enemy"))

    def test_results_rank_and_a_game_never_confirms_itself(self):
        store = {"patterns": {}, "members": {}}
        dev_learn.record_tweak_pins(store, self.SKILL, "game.a", "works")
        bonus, use = dev_learn.member_boost(store, "_skillPoints", "int32_t", "game.b", "SkillsSave")
        self.assertGreater(bonus, 0)
        self.assertEqual((use["title"], use["value"], use["works"]), ("Unlimited skill points", 99, 1))
        self.assertEqual(dev_learn.member_boost(store, "_skillPoints", "int32_t", "game.a"), (0, None))
        # a test result never replaces the play result
        dev_learn.record_tweak_pins(store, self.SKILL, "game.a", "unreached")
        self.assertEqual(dev_learn.member_boost(store, "SkillPoints", "int32_t", "game.b")[1]["works"], 1)
        # failing more than working: no suggestion
        for game in ("game.c", "game.d"):
            dev_learn.record_tweak_pins(store, self.SKILL, game, "fails")
        self.assertEqual(dev_learn.member_boost(store, "SkillPoints", "int32_t", "game.b"), (0, None))

    def test_the_scan_booster_answers_like_member_boost(self):
        store = {"patterns": {}, "members": {}}
        dev_learn.record_tweak_pins(store, self.SKILL, "game.a", "works")
        for args in [("_skillPoints", "int32_t", "SkillsSave"), ("SkillPoints", "int32_t", ""), ("Points", "int32_t", ""),
                     ("_skillPoints", "float", "SkillsSave"), ("Gold", "int32_t", "Bag")]:
            for game in ("game.a", "game.b"):
                self.assertEqual(dev_learn.booster(store, game)(*args), dev_learn.member_boost(store, args[0], args[1], game, args[2]))

    def test_version_1_pins_move_out_of_the_method_patterns(self):
        store = {"version": 1, "patterns": {"accuracy|void": {"action": "pin", "method": "Accuracy", "uses": []},
                                            "attack|void": {"action": "arg", "method": "Attack", "uses": []}}}
        self.assertEqual(dev_learn.migrate(store), 1)
        self.assertEqual(list(store["patterns"]), ["attack|void"])
        self.assertEqual(store["version"], 2)


if __name__ == "__main__":
    unittest.main()
