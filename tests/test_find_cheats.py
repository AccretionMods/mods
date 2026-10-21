import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import check_hooks
import find_cheats
import validate_mods
from il2cpp_dump import Method, methods, split_params

# dump.cs as Il2CppDumper writes it: image header, "// Namespace:" before each type, an address
# comment before each method, attribute lines with their own addresses, "-1" for methods with no code
DUMP = """\
// Image 0: mscorlib.dll - 0
// Image 1: Assembly-CSharp.dll - 1000

// Namespace: System
public sealed class String // TypeDefIndex: 5
{
	// Methods

	// RVA: 0x1000 Offset: 0x1000 VA: 0x1001000
	public static bool IsNullOrEmpty(string value) { }
}

// Namespace: Game.Combat
[Serializable]
public class PlayerHealth : MonoBehaviour // TypeDefIndex: 1000
{
	// Fields
	[SerializeField] // RVA: 0x2000 Offset: 0x2000 VA: 0x1002000
	public float health; // 0x18

	// Properties
	public float Health { get; }

	// Methods

	// RVA: 0x100000 Offset: 0x100000 VA: 0x1100000
	public void TakeDamage(float amount) { }

	[CompilerGenerated] // RVA: 0x3000 Offset: 0x3000 VA: 0x1003000
	// RVA: 0x100010 Offset: 0x100010 VA: 0x1100010 Slot: 4
	public virtual float get_Health() { }

	// RVA: -1 Offset: -1
	public abstract void Die();

	// RVA: 0x100020 Offset: 0x100020 VA: 0x1100020
	public void .ctor() { }
}

// Namespace: Game.Combat
public sealed class Weapon // TypeDefIndex: 1001
{
	// Methods

	// RVA: 0x100030 Offset: 0x100030 VA: 0x1100030
	public static float GetDamage(int level, Dictionary<string, int> bonus) { }

	// RVA: 0x100040 Offset: 0x100040 VA: 0x1100040
	public int get_Ammo() { }

	// RVA: 0x100050 Offset: 0x100050 VA: 0x1100050
	private T Pick<T>(List<T> from) { }
}

// Namespace:
internal static class Speedy // TypeDefIndex: 1002
{
	// Methods

	// RVA: 0x100060 Offset: 0x100060 VA: 0x1100060
	public static float MoveSpeed() { }
}

// Namespace: Game.Shop
public class Store // TypeDefIndex: 1003
{
	// Methods

	// RVA: 0x100070 Offset: 0x100070 VA: 0x1100070
	public int get_Ammo() { }
}

// Namespace: Game.Network
public class NetworkPlayer // TypeDefIndex: 1004
{
	// Methods

	// RVA: 0x100080 Offset: 0x100080 VA: 0x1100080
	public void TakeDamage(float amount) { }
}

// Namespace: Game.Combat
public class PlayerHealth.Shield<T> : Component // TypeDefIndex: 1005
{
	// Methods

	// RVA: 0x100090 Offset: 0x100090 VA: 0x1100090
	public float GetDamage() { }
}

// Namespace: UnityEngine.Purchasing
public class PurchasingManager // TypeDefIndex: 1006
{
	// Methods

	// RVA: 0x1000a0 Offset: 0x1000a0 VA: 0x11000a0
	public void TakeDamage(float amount) { }
}
"""


def write(folder, name, text):
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class Dump(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.found = methods(write(self.tmp.name, "dump.cs", DUMP))
        self.by_name = {m.full_name: m for m in self.found}

    def test_every_method_with_code_is_read(self):
        self.assertEqual(
            sorted(self.by_name),
            [
                "Game.Combat.PlayerHealth..ctor",
                "Game.Combat.PlayerHealth.Shield<T>.GetDamage",
                "Game.Combat.PlayerHealth.TakeDamage",
                "Game.Combat.PlayerHealth.get_Health",
                "Game.Combat.Weapon.GetDamage",
                "Game.Combat.Weapon.Pick<T>",
                "Game.Combat.Weapon.get_Ammo",
                "Game.Network.NetworkPlayer.TakeDamage",
                "Game.Shop.Store.get_Ammo",
                "Speedy.MoveSpeed",
                "System.String.IsNullOrEmpty",
                "UnityEngine.Purchasing.PurchasingManager.TakeDamage",
            ],
        )

    def test_nested_type_keeps_its_own_name(self):
        self.assertEqual(self.by_name["Game.Combat.PlayerHealth.Shield<T>.GetDamage"].class_name, "PlayerHealth.Shield<T>")

    def test_names_addresses_and_signatures(self):
        m = self.by_name["Game.Combat.PlayerHealth.TakeDamage"]
        self.assertEqual(m.key, ("Game.Combat", "PlayerHealth", "TakeDamage"))
        self.assertEqual((m.rva, m.return_type, m.params, m.kind), ("0x100000", "void", ["float amount"], "void"))
        self.assertEqual(m.signature, "public void TakeDamage(float amount)")
        # the method's own address, not the attribute's, and the Slot suffix is fine
        health = self.by_name["Game.Combat.PlayerHealth.get_Health"]
        self.assertEqual((health.rva, health.kind), ("0x100010", "float"))
        damage = self.by_name["Game.Combat.Weapon.GetDamage"]
        self.assertEqual(damage.params, ["int level", "Dictionary<string, int> bonus"])
        self.assertEqual(damage.args, 2)
        self.assertEqual(self.by_name["Speedy.MoveSpeed"].namespace, "")

    def test_assembly_from_type_index(self):
        self.assertEqual(self.by_name["System.String.IsNullOrEmpty"].assembly, "mscorlib")
        self.assertEqual(self.by_name["Game.Combat.Weapon.get_Ammo"].assembly, "Assembly-CSharp")

    def test_hook_has_what_intake_auto_checks(self):
        hook = self.by_name["Game.Combat.Weapon.get_Ammo"].hook()
        self.assertEqual(
            hook,
            {
                "assembly": "Assembly-CSharp",
                "namespace": "Game.Combat",
                "class": "Weapon",
                "method": "get_Ammo",
                "args": 0,
                "return_type": "int",
                "signature": "public int get_Ammo()",
                "needs_jit": True,
            },
        )

    def test_vtable_slot_and_unity_messages_need_no_jit(self):
        by_name = {m.name: m for m in self.found}
        self.assertEqual(by_name["get_Health"].slot, 4)
        self.assertFalse(by_name["get_Health"].needs_jit)
        self.assertIsNone(by_name["TakeDamage"].slot)
        self.assertTrue(by_name["TakeDamage"].needs_jit)
        self.assertFalse(Method("A", "", "C", "Update", "void", [], "0x1", "void Update()").needs_jit)

    def test_split_params(self):
        self.assertEqual(split_params(""), [])
        self.assertEqual(split_params("int a, Dictionary<string, int> b, float c"), ["int a", "Dictionary<string, int> b", "float c"])


class Scoring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.candidates, self.out = find_cheats.scan(write(self.tmp.name, "dump.cs", DUMP))
        self.by_name = {c.method.full_name: c for c in self.candidates}

    def test_categories_fit_return_types(self):
        take = self.by_name["Game.Combat.PlayerHealth.TakeDamage"]
        self.assertEqual((take.category, take.action, take.value), ("god_mode", "skip", None))
        # a float getter named Health is god mode by value, never a skipped call
        health = self.by_name["Game.Combat.PlayerHealth.get_Health"]
        self.assertEqual((health.category, health.action, health.value), ("god_mode", "return", 9999.0))
        damage = self.by_name["Game.Combat.Weapon.GetDamage"]
        self.assertEqual((damage.category, damage.action, damage.value), ("damage", "return", 9999.0))
        self.assertEqual(self.by_name["Game.Combat.Weapon.get_Ammo"].value, 999)
        self.assertEqual(self.by_name["Speedy.MoveSpeed"].category, "speed")
        self.assertNotIn("Game.Combat.PlayerHealth..ctor", self.by_name)
        self.assertNotIn("System.String.IsNullOrEmpty", self.by_name)

    def test_readme_rules_leave_out_purchases_and_online_play(self):
        self.assertNotIn("Game.Shop.Store.get_Ammo", self.by_name)
        self.assertNotIn("Game.Network.NetworkPlayer.TakeDamage", self.by_name)
        self.assertEqual(
            [m.full_name for m in self.out["purchases"]],
            ["Game.Shop.Store.get_Ammo", "UnityEngine.Purchasing.PurchasingManager.TakeDamage"],
        )

    def test_generic_types_are_no_candidates(self):
        # the runtime can't look them up by namespace and class name
        self.assertNotIn("Game.Combat.PlayerHealth.Shield<T>.GetDamage", self.by_name)
        self.assertEqual([m.full_name for m in self.out["online play"]], ["Game.Network.NetworkPlayer.TakeDamage"])

    def test_best_per_category_and_display_order(self):
        best = find_cheats.best_per_category(self.candidates)
        self.assertEqual([c.category for c in best], ["god_mode", "damage", "speed", "ammo"])
        self.assertEqual(best[0].method.name, "TakeDamage")
        # the numbers show() prints run down the categories in order, so pick() uses the same order
        ordered = find_cheats.in_display_order(self.candidates)
        self.assertEqual([c.category for c in ordered], ["god_mode", "god_mode", "damage", "speed", "ammo"])

    def test_write_hooks_keeps_others_and_replaces_same_method(self):
        path = os.path.join(self.tmp.name, "hooks.json")
        existing = {
            "engine": "unity-il2cpp",
            "hooks": [
                {"namespace": "UnityEngine", "class": "Time", "method": "set_timeScale", "args": 1},
                {"namespace": "Game.Combat", "class": "PlayerHealth", "method": "TakeDamage", "args": 7},
            ],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(existing, f)
        total = find_cheats.write_hooks(path, find_cheats.best_per_category(self.candidates))
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(total, 5)
        self.assertEqual(data["engine"], "unity-il2cpp")
        self.assertEqual(data["hooks"][0]["method"], "set_timeScale")
        take = [h for h in data["hooks"] if h["method"] == "TakeDamage"]
        self.assertEqual(len(take), 1)
        self.assertEqual(take[0]["args"], 1)
        self.assertEqual((take[0]["category"], take[0]["action"], take[0]["value"]), ("god_mode", "skip", None))
        self.assertTrue(all({"class", "method", "namespace"} <= set(h) for h in data["hooks"]))


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.found = methods(write(self.tmp.name, "dump.cs", DUMP))

    def test_ok_changed_and_missing(self):
        hooks = [
            {"namespace": "Game.Combat", "class": "PlayerHealth", "method": "TakeDamage", "args": 1, "return_type": "void"},
            {"namespace": "Game.Combat", "class": "Weapon", "method": "GetDamage", "args": 1, "return_type": "int"},
            {"namespace": "Game.Combat", "class": "Gun", "method": "get_Ammo"},
            {"namespace": "", "class": "Speedy", "method": "movespeed"},
        ]
        results = check_hooks.check(hooks, self.found)
        self.assertEqual([r[0] for r in results], ["ok", "changed", "missing", "missing"])
        self.assertIn("RVA 0x100000", results[0][2])
        self.assertEqual(results[1][2], "returned int, now float; took 1 args, now 2  (public static float GetDamage(int level, Dictionary<string, int> bonus))")
        self.assertEqual(results[2][2], "same name in: Game.Combat.Weapon.get_Ammo, Game.Shop.Store.get_Ammo")
        self.assertEqual(results[3][2], "same name in: Speedy.MoveSpeed")

    def test_names_only_when_hooks_json_has_no_signature(self):
        hooks = [{"namespace": "Game.Combat", "class": "Weapon", "method": "GetDamage"}]
        self.assertEqual(check_hooks.check(hooks, self.found)[0][0], "ok")


class Validate(unittest.TestCase):
    def problems(self, data):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "hooks.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write(data if isinstance(data, str) else json.dumps(data))
            return validate_mods.hooks_problems(path)

    def test_written_hooks_validate(self):
        with tempfile.TemporaryDirectory() as folder:
            candidates, _ = find_cheats.scan(write(folder, "dump.cs", DUMP))
            path = os.path.join(folder, "hooks.json")
            find_cheats.write_hooks(path, candidates)
            self.assertEqual(validate_mods.hooks_problems(path), [])

    def test_problems_are_named(self):
        self.assertEqual(len(self.problems("{")), 1)
        self.assertEqual(self.problems({"hooks": []}), ['hooks.json needs "engine": "unity-il2cpp"'])
        self.assertEqual(self.problems({"engine": "unity-il2cpp"}), ['hooks.json needs a "hooks" list'])
        bad = {"engine": "unity-il2cpp", "hooks": [{"class": "A", "method": "m", "args": -1}, "x"]}
        self.assertEqual(
            self.problems(bad),
            ['hooks.json hook 0 needs namespace (use "" for the global namespace)', "hooks.json hook 0: args must be a count", "hooks.json hook 1 must be an object"],
        )


if __name__ == "__main__":
    unittest.main()
