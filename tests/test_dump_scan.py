"""scripts/dump_scan.py: no-JIT switch candidates from a small made-up dump.cs."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import dump_scan

DUMP = """// Image 0: mscorlib.dll - 0
// Image 1: Assembly-CSharp.dll - 10
// Image 2: UnityEngine.CoreModule.dll - 20

// Namespace: System
public class Int32 // TypeDefIndex: 1
{
\tpublic int m_value; // 0x10
}

// Namespace:
public class SkillsManager : MonoBehaviour // TypeDefIndex: 11
{
\t// Fields
\tpublic static SkillsManager instance; // 0x0
\tpublic int AccuracyDefault; // 0xA0
\tpublic int Accuracy; // 0xA4
\tpublic bool CanStun; // 0x79
\tprivate float fadeTimer; // 0x130
\tpublic Text SkillPointsText; // 0x40
\tpublic const int MaxLevel = 10;

\t// Methods
\tpublic int get_SkillPoints() { }
\tpublic void set_SkillPoints(int value) { }
\tpublic void set_TokenBalance(int value) { }
}

// Namespace:
[CompilerGenerated]
private sealed class Enemy.<>c // TypeDefIndex: 12
{
\tpublic static readonly Enemy.<>c <>9; // 0x0
}

// Namespace:
public class Enemy : MonoBehaviour // TypeDefIndex: 12
{
\tpublic long EnemyDamage; // 0x60
\tprivate int suppressedAttacksRemaining; // 0x108
}

// Namespace:
public class AdvertHandler : MonoBehaviour // TypeDefIndex: 13
{
\tpublic static AdvertHandler instance; // 0x0
\tpublic bool TestMode; // 0x20
}

// Namespace:
public class SaveData // TypeDefIndex: 14
{
\tpublic int Coins; // 0x10
}

// Namespace: UnityEngine
public class Camera : Behaviour // TypeDefIndex: 21
{
\tpublic static Camera main; // 0x0
\tpublic float fieldOfView; // 0x10
}
"""


HOT = """// Image 0: mscorlib.dll - 0
// Image 1: HotFix.dll - 10
// Image 2: Assembly-CSharp.dll - 30
// Image 3: Framework.dll - 40

// Namespace:
public class Singleton<T> // TypeDefIndex: 41
{
\tprivate static T m_t; // 0x0
\tpublic static T get_Instance() { }
}

// Namespace: HotFix
public class GameManager : Singleton<GameManager> // TypeDefIndex: 11
{
\tpublic int Gold; // 0x10
\tpublic FP MoveSpeed; // 0x18
\tpublic void set_IsSpeedUp(bool value) { }
\tpublic bool get_IsSpeedUp() { }
}

// Namespace: HotFix
public class ListDemoScript : MonoBehaviour // TypeDefIndex: 12
{
\tpublic int mTotalDataCount; // 0x10
}

// Namespace: SuperScrollView
public class ScrollItem : MonoBehaviour // TypeDefIndex: 31
{
\tpublic int Value; // 0x10
}
"""


class HotUpdate(unittest.TestCase):
    """A HybridCLR game: its code in HotFix, managers behind Singleton<T>, fixed-point FP, demo scripts."""

    def test_scan_reads_the_hot_update_assembly_and_reaches_generic_singletons(self):
        with tempfile.TemporaryDirectory() as tmp:
            dump = Path(tmp) / "dump.cs"
            dump.write_text(HOT, encoding="utf-8")
            rows = {f"{r['class']}.{r['member']}": r for r in dump_scan.scan(dump)["rows"]}
            gold = rows["HotFix:HotFix.GameManager.Gold"]
            self.assertEqual(gold["object"], "HotFix:HotFix.GameManager.m_t")
            self.assertIn("HotFix:HotFix.GameManager.set_IsSpeedUp", rows)
            speed = rows["HotFix:HotFix.GameManager.MoveSpeed"]
            self.assertEqual((speed["ctype"], speed["scale"]), ("int64_t", 65536))
            self.assertFalse(any("Demo" in k for k in rows))  # a package's demo script
            self.assertFalse(any("ScrollItem" in k for k in rows))  # Assembly-CSharp: packages, when HotFix exists
            # mod.json scan_assemblies chooses instead
            only = dump_scan.scan(dump, assemblies=["Assembly-CSharp"])["rows"]
            self.assertEqual({r["class"] for r in only}, {"SuperScrollView.ScrollItem"})


class Scan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dump = Path(self.tmp.name) / "dump.cs"
        self.dump.write_text(DUMP, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self, tweaks=None):
        r = dump_scan.scan(self.dump, tweaks)
        return r, {f"{x['class']}.{x['member']}": x for x in r["rows"]}, {f"{x['class']}.{x['member']}": x for x in r["flagged"]}

    def test_only_the_game_assembly_and_reachable_objects(self):
        r, rows, _ = self.rows()
        self.assertEqual(r["classes"], 4)  # SkillsManager, Enemy, AdvertHandler, SaveData; not System / UnityEngine
        self.assertNotIn("Camera.fieldOfView", rows)
        self.assertNotIn("SaveData.Coins", rows)  # not a MonoBehaviour and no singleton: a pin can't reach it
        self.assertEqual(rows["SkillsManager.Accuracy"]["object"], "SkillsManager.instance")
        self.assertEqual(rows["Enemy.EnemyDamage"]["object"], "Enemy[]")  # not lost to "Enemy.<>c" before it
        self.assertNotIn("SkillsManager.SkillPointsText", rows)  # not a number or a bool

    def test_scores_areas_and_suggestions(self):
        _, rows, _ = self.rows()
        acc, stun, pts = rows["SkillsManager.Accuracy"], rows["SkillsManager.CanStun"], rows["SkillsManager.set_SkillPoints"]
        self.assertEqual((acc["area"], acc["value"], acc["when"]), ("Combat", "100", "always"))
        self.assertGreater(acc["score"], rows["SkillsManager.AccuracyDefault"]["score"])  # has a Default twin
        self.assertEqual((stun["value"], stun["ctype"]), ("true", "bool"))
        self.assertEqual((pts["area"], pts["kind"], pts["when"]), ("Progress", "setter", "lower"))
        self.assertLess(rows["SkillsManager.fadeTimer"]["score"], acc["score"])  # UI noise sinks

    def test_flags_what_is_sold_and_ads(self):
        _, rows, flagged = self.rows()
        self.assertEqual(flagged["SkillsManager.set_TokenBalance"]["flag"], "sold in the shop")
        self.assertEqual(flagged["AdvertHandler.TestMode"]["flag"], "ads")
        self.assertIn("SkillsManager.set_TokenBalance", rows)
        self.assertIn("AdvertHandler.TestMode", rows)
        self.assertIn("score", flagged["SkillsManager.set_TokenBalance"])
        self.assertIn("value", flagged["SkillsManager.set_TokenBalance"])
        self.assertEqual(rows["SkillsManager.set_TokenBalance"]["area"], "In-app purchases")

    def test_marks_what_the_mod_has_and_caches(self):
        tweaks = {"tweaks": [{"key": "pts", "pins": [{"object": "SkillsManager.instance", "setter": "set_SkillPoints"}]},
                             {"key": "miss", "pins": [{"object": "Enemy[]", "field": "suppressedAttacksRemaining"}]}]}
        _, rows, _ = self.rows(tweaks)
        self.assertTrue(rows["SkillsManager.set_SkillPoints"]["has"])
        self.assertTrue(rows["Enemy.suppressedAttacksRemaining"]["has"])
        self.assertFalse(rows["SkillsManager.Accuracy"]["has"])
        cache = json.loads(self.dump.with_name("scan.json").read_text())
        self.assertEqual(cache["stamp"]["version"], dump_scan.VERSION)
        self.dump.write_text(DUMP.replace("public bool CanStun; // 0x79", ""), encoding="utf-8")
        os.utime(self.dump, (1, 1))  # a new dump: the cache is rebuilt
        _, rows, _ = self.rows()
        self.assertNotIn("SkillsManager.CanStun", rows)

    def test_text_for_a_prompt(self):
        r = dump_scan.scan(self.dump)
        text = dump_scan.as_text(r["rows"], 3)
        self.assertEqual(len(text.splitlines()), 4)
        self.assertIn("SkillsManager.instance | Accuracy", text)



class Checks(unittest.TestCase):
    def test_store_names_are_purchases_but_restore_is_not(self):
        self.assertEqual(dump_scan.area_of("GameStore", "IsOpen")[0], "In-app purchases")
        self.assertEqual(dump_scan.area_of("Shop", "BuyProduct")[0], "In-app purchases")
        self.assertNotEqual(dump_scan.area_of("SaveManager", "RestoreBackup")[0], "In-app purchases")

    def test_purchase_inventory_keeps_mod_targets_and_removes_compiler_noise(self):
        with tempfile.TemporaryDirectory() as folder:
            dump = Path(folder) / "dump.cs"
            dump.write_text(DUMP + """
// Image 3: UnityEngine.Purchasing.dll - 30
// Namespace: UnityEngine.Purchasing
public class Product // TypeDefIndex: 30
{
    public static string Receipt; // 0x0
    public bool Owned; // 0x20
    public const int ProductType = 1; // 0x0
    public Action OnPurchased; // 0x28
    // RVA: 0x1000 Offset: 0x1000 VA: 0x1000
    public bool get_IsOwned() { }
    // RVA: 0x1010 Offset: 0x1010 VA: 0x1010
    public void Buy(string id) { }
    // RVA: 0x1020 Offset: 0x1020 VA: 0x1020
    public void .ctor() { }
    // RVA: 0x1030 Offset: 0x1030 VA: 0x1030
    public void Initialize() { }
    // RVA: 0x1040 Offset: 0x1040 VA: 0x1040
    public string GetReceipt() { }
    // RVA: -1 Offset: -1 VA: -1
    public bool HasNoCode() { }
}
// Namespace: UnityEngine.Purchasing
private sealed class IAPManager.<>c // TypeDefIndex: 31
{
    public static IAPManager.<>c <>9; // 0x0
    public Func<CartItem, bool> <>9__28_2; // 0x8
    public bool Cached; // 0x10
    // RVA: 0x1050 Offset: 0x1050 VA: 0x1050
    public bool FilterItem(CartItem item) { }
}
""", encoding="utf-8")
            result = dump_scan.scan(dump)
            inventory = {(r["class"], r["member"]): r for r in result["inventory"]}
            for member in ("Owned", "get_IsOwned", "Buy"):
                row = inventory[("UnityEngine.Purchasing.Product", member)]
                self.assertEqual(row["area"], "In-app purchases")
                self.assertTrue(row["inspect_only"])
                self.assertIn("signature", row)
            self.assertTrue(all("<" not in r["class"] for r in inventory.values()))
            for member in ("Receipt", "(type)", "ProductType", "OnPurchased", ".ctor", "Initialize", "GetReceipt", "HasNoCode"):
                self.assertNotIn(("UnityEngine.Purchasing.Product", member), inventory)
            method = inventory[("UnityEngine.Purchasing.Product", "get_IsOwned")]["method"]
            self.assertEqual(method["full_name"], "UnityEngine.Purchasing.Product.get_IsOwned")
            self.assertTrue(method["needs_jit"])
            self.assertIn(("SaveData", "Coins"), inventory)
            self.assertEqual(result["inventory"], dump_scan.scan(dump)["inventory"])

    def test_direction_multipliers_and_chances(self):
        sug = dump_scan._suggest
        self.assertEqual((sug("NEW_TROOPS_ATTACK_DELAY_MULTIPLIER", "float", False)["value"],
                          sug("NEW_TROOPS_ATTACK_DELAY_MULTIPLIER", "float", False)["direction"]), ("0.5", "lower"))
        self.assertEqual(sug("ENEMY_TROOPS_MOVE_SPEED_MULTIPLIER", "double", False)["direction"], "lower")
        self.assertEqual(sug("attackDelay", "float", False)["value"], "0")
        self.assertEqual(sug("BUILDINGS_HP_MULTIPLIER", "float", False)["value"], "2")  # not 99
        self.assertEqual(sug("CASTLE_INSTAKILL_CHANCE", "float", False)["value"], "1")   # a 0-1 chance
        self.assertEqual(sug("CritChance", "int32_t", True)["value"], "100")            # a percent
        self.assertEqual(sug("SkillPoints", "int32_t", True), {"value": "99", "when": "lower", "direction": "higher"})
        self.assertIsNone(dump_scan.MULTIPLIER.search("MULTIPLE_TARGETS"))

    def test_what_a_save_holds(self):
        classes = {
            "GameContainer": {"holds": {"PolicyVariables", "String"}},
            "Policies.PolicyVariables": {"holds": {"Boost"}},
            "Boost": {"holds": set()},
            "SaveLoadButton": {"holds": {"Enemy"}},
            "Enemy": {"holds": set()},
        }
        saved = dump_scan.saved_classes(classes)
        self.assertTrue({"GameContainer", "PolicyVariables", "Boost"} <= saved)
        self.assertNotIn("Enemy", saved)  # a UI button named Save... holds no save


SPLIT = """// Image 0: mscorlib.dll - 0
// Image 1: Assembly-CSharp.dll - 10
// Image 2: Engine.Port.dll - 20
// Image 3: SpaceRaid.Runtime.dll - 30

// Namespace: Engine
public class Sprite : MonoBehaviour // TypeDefIndex: 21
{
	public float alpha; // 0x10
}

// Namespace: SpaceRaid
public class Ship : MonoBehaviour // TypeDefIndex: 31
{
	public float Shield; // 0x20
}
"""


class GameAssemblyTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".cs")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(SPLIT)

    def tearDown(self):
        os.remove(self.path)

    def test_assembly_named_after_bundle_id(self):
        classes = dump_scan.parse(self.path, bundle_id="com.example.SpaceRaid")
        self.assertEqual([c for c in classes if c.endswith("Ship")], ["SpaceRaid.Runtime:SpaceRaid.Ship"])
        self.assertFalse(any(c.endswith("Sprite") for c in classes))  # an engine assembly stays unread

    def test_without_bundle_id_only_assembly_csharp(self):
        self.assertEqual(dump_scan.parse(self.path), {})

    def test_scan_assemblies_accept_dll_names(self):
        classes = dump_scan.parse(self.path, assemblies=["SpaceRaid.Runtime.dll"])
        self.assertIn("SpaceRaid.Runtime:SpaceRaid.Ship", classes)

    def test_assembly_sizes_skip_libraries(self):
        self.assertEqual(dump_scan.assembly_sizes(self.path)[0], (10, "Engine.Port"))


if __name__ == "__main__":
    unittest.main()
