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
        cache = json.loads(dump_scan.cache_file(self.dump).read_text())
        self.assertEqual(cache["stamp"]["version"], dump_scan.VERSION)
        # a second mod reading other assemblies of the same shared dump keeps its own cache file
        self.assertNotEqual(dump_scan.cache_file(self.dump), dump_scan.cache_file(self.dump, ["HotFix"]))
        self.dump.write_text(DUMP.replace("public bool CanStun; // 0x79", ""), encoding="utf-8")
        os.utime(self.dump, (1, 1))  # a new dump: the cache is rebuilt
        _, rows, _ = self.rows()
        self.assertNotIn("SkillsManager.CanStun", rows)

    def test_text_for_a_prompt(self):
        r = dump_scan.scan(self.dump)
        text = dump_scan.as_text(r["rows"], 3)
        self.assertEqual(len(text.splitlines()), 4)
        self.assertIn("SkillsManager.instance | Accuracy", text)


GRAPH = """// Image 0: HotFix.dll - 0
// Image 1: HotFixBattle.dll - 100
// Namespace: HotFix
public static class BattleGame // TypeDefIndex: 1
{
    private static WorldBattle _world; // 0x0
}
// Namespace: HotFix
public class WorldBattle // TypeDefIndex: 2
{
    public readonly HotFixBattle.BattleLogicWorld CurLogicWorld; // 0x10
}
// Namespace: HotFixBattle
public class BattleLogicWorld // TypeDefIndex: 101
{
    private EntityManager _entityManager; // 0x10
    public HotFix.WorldBattle get_World() { }
    public List<Player> get_Players() { }
    public EntityCharacter[] Characters; // 0x18
    public Dictionary<int, Player> PlayerMap; // 0x20
    public OrderedDictionary<int, FP> Numbers; // 0x28
    public ValueData Value; // 0x30
    public Duplicate Unclear; // 0x38
    public readonly int ConstantHealth; // 0x40
}
// Namespace: HotFixBattle
internal class EntityManager // TypeDefIndex: 102
{
    private readonly OrderedDictionary<int, IEntityBase> _entityDic; // 0x10
}
// Namespace: HotFixBattle
internal interface IEntityBase // TypeDefIndex: 103
{
}
// Namespace: HotFixBattle
public abstract class EntityBase<T> : IEntityBase // TypeDefIndex: 104
{
}
// Namespace: HotFixBattle
internal class EntityBullet : EntityBase<int> // TypeDefIndex: 105
{
    public FP Speed; // 0x10
}
// Namespace: HotFixBattle
public class Player // TypeDefIndex: 106
{
    public int Health; // 0x10
}
// Namespace: HotFixBattle
public class EntityCharacter : IEntityBase // TypeDefIndex: 107
{
    public int Damage; // 0x10
}
// Namespace: HotFixBattle
public struct ValueData // TypeDefIndex: 108
{
    public int Damage; // 0x0
}
// Namespace: One
public class Duplicate // TypeDefIndex: 109
{
    public int Health; // 0x10
}
// Namespace: Two
public class Duplicate // TypeDefIndex: 110
{
    public int Health; // 0x10
}
"""


class ObjectGraph(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dump = Path(self.tmp.name) / "dump.cs"
        self.dump.write_text(GRAPH, encoding="utf-8")
        self.classes = dump_scan.parse(self.dump)

    def tearDown(self):
        self.tmp.cleanup()

    def test_parser_keeps_readonly_references_and_qualified_generic_getters(self):
        world = self.classes["HotFixBattle:HotFixBattle.BattleLogicWorld"]
        self.assertEqual(world["object_fields"]["Characters"], "EntityCharacter[]")
        self.assertEqual(world["object_getters"]["get_World"], "HotFix.WorldBattle")
        self.assertEqual(world["object_getters"]["get_Players"], "List<Player>")
        self.assertEqual(self.classes["HotFix:HotFix.WorldBattle"]["object_fields"]["CurLogicWorld"],
                         "HotFixBattle.BattleLogicWorld")

    def test_cross_assembly_static_root_and_derived_dictionary_values(self):
        rows = {(r["class"], r["member"]): r for r in dump_scan.scan(self.dump)["rows"]}
        bullet = rows[("HotFixBattle:HotFixBattle.EntityBullet", "Speed")]
        self.assertEqual(bullet["object"], "HotFix:HotFix.BattleGame._world")
        self.assertEqual(bullet["root_class"], "HotFix:HotFix.WorldBattle")
        self.assertEqual(bullet["path"], [
            {"field": "CurLogicWorld", "class": "HotFixBattle:HotFixBattle.BattleLogicWorld"},
            {"field": "_entityManager", "class": "HotFixBattle:HotFixBattle.EntityManager"},
            {"field": "_entityDic", "class": "HotFixBattle:HotFixBattle.IEntityBase", "each": "values"},
            {"class": "HotFixBattle:HotFixBattle.EntityBullet"},
        ])
        self.assertEqual(bullet["scale"], 65536)
        self.assertFalse(any(r["member"] == "ConstantHealth" for r in rows.values()))
        self.assertEqual(dump_scan.scan(self.dump)["rows"], list(rows.values()))

    def test_shortest_paths_cycles_ambiguity_and_value_types(self):
        paths = dump_scan.object_paths(self.classes)
        character = paths["HotFixBattle:HotFixBattle.EntityCharacter"]
        self.assertEqual(character["path"][-1],
                         {"field": "Characters", "class": "HotFixBattle:HotFixBattle.EntityCharacter", "each": "array"})
        self.assertLessEqual(max(len(p["path"]) for p in paths.values()), 8)
        self.assertNotIn("HotFixBattle:HotFixBattle.ValueData", paths)
        self.assertNotIn("HotFixBattle:One.Duplicate", paths)
        self.assertNotIn("HotFixBattle:Two.Duplicate", paths)
        self.assertFalse(any(s.get("field") == "Numbers" for p in paths.values() for s in p["path"]))

    def test_collection_fields_and_getters(self):
        base = "HotFixBattle:HotFixBattle.BattleLogicWorld"
        for remove, expected in [([], {"field": "PlayerMap", "each": "values"}),
                                 (["PlayerMap"], {"getter": "get_Players", "each": "list"})]:
            for name in remove:
                self.classes[base]["object_fields"].pop(name, None)
            step = dump_scan.object_paths(self.classes)["HotFixBattle:HotFixBattle.Player"]["path"][-1]
            self.assertEqual(step, dict(expected, **{"class": "HotFixBattle:HotFixBattle.Player"}))

    def test_resolution_prefers_same_namespace_then_same_assembly(self):
        self.classes["HotFixBattle:HotFixBattle.BattleLogicWorld"]["object_fields"]["Local"] = "Duplicate"
        self.classes["HotFixBattle:HotFixBattle.Duplicate"] = dict(self.classes["HotFixBattle:One.Duplicate"])
        paths = dump_scan.object_paths(self.classes)
        self.assertIn("HotFixBattle:HotFixBattle.Duplicate", paths)
        self.assertNotIn("HotFixBattle:One.Duplicate", paths)

    def test_direct_singleton_rows_keep_original_shape(self):
        self.dump.write_text(HOT, encoding="utf-8")
        gold = next(r for r in dump_scan.scan(self.dump)["rows"] if r["member"] == "Gold")
        self.assertNotIn("path", gold)
        self.assertNotIn("root_class", gold)

    def test_validation_accepts_noncanonical_paths_and_rejects_wrong_types(self):
        paths = dump_scan.object_paths(self.classes)
        bullet = paths["HotFixBattle:HotFixBattle.EntityBullet"]
        validate = lambda path: dump_scan.path_errors(self.classes, bullet["object"], bullet["root_class"], path)
        self.assertEqual(validate(bullet["path"]), [])
        alternate = bullet["path"][:1] + [
            {"getter": "get_Players", "class": "HotFixBattle:HotFixBattle.Player", "each": "list"},
        ]
        self.assertEqual(validate(alternate), [])
        for bad in [
            [{"field": "CurLogicWorld", "class": "HotFixBattle:HotFixBattle.Player"}],
            bullet["path"][:2] + [{"field": "_entityDic", "class": "HotFixBattle:HotFixBattle.IEntityBase"}],
            alternate[:-1] + [dict(alternate[-1], each="array")],
            bullet["path"][:-1] + [{"class": "HotFixBattle:HotFixBattle.Player"}],
            [{"field": "Missing", "class": "HotFixBattle:HotFixBattle.Player"}],
            [{"field": "CurLogicWorld", "getter": "get_World", "class": "HotFixBattle:HotFixBattle.Player"}],
        ]:
            with self.subTest(path=bad):
                self.assertTrue(validate(bad))
        self.assertTrue(dump_scan.path_errors(self.classes, "HotFix:HotFix.BattleGame.missing", bullet["root_class"], []))
        self.assertTrue(dump_scan.path_errors(self.classes, bullet["object"], "HotFixBattle:HotFixBattle.Player", []))

    def test_automatic_paths_skip_transient_work_lists_but_explicit_paths_validate(self):
        world = self.classes["HotFix:HotFix.WorldBattle"]
        world["object_fields"].update({"_tmpBullets": "List<HotFixBattle.EntityBullet>",
                                       "_tempBullets": "HotFixBattle.EntityBullet[]",
                                       "scratchBullets": "List<HotFixBattle.EntityBullet>"})
        world["object_getters"]["get_HelpList"] = "List<HotFixBattle.EntityBullet>"
        bullet = dump_scan.object_paths(self.classes)["HotFixBattle:HotFixBattle.EntityBullet"]
        self.assertEqual(bullet["path"][-2]["field"], "_entityDic")
        explicit = [{"field": "_tmpBullets", "class": "HotFixBattle:HotFixBattle.EntityBullet", "each": "list"}]
        self.assertEqual(dump_scan.path_errors(self.classes, bullet["object"], bullet["root_class"], explicit), [])

    def test_inherited_references_resolve_in_the_declaring_namespace_and_shadow(self):
        self.dump.write_text(GRAPH + """
// Namespace: Common
public class Base : MonoBehaviour // TypeDefIndex: 111
{
    public Hero hero; // 0x10
    public Hero get_Target() { }
}
// Namespace: Common
public class Hero // TypeDefIndex: 112
{
    public int Health; // 0x10
}
// Namespace: Child
public class Hero // TypeDefIndex: 113
{
    public int Damage; // 0x10
}
// Namespace: Child
public class Manager : Common.Base // TypeDefIndex: 114
{
    public static Manager instance; // 0x0
}
""", encoding="utf-8")
        classes = dump_scan.parse(self.dump)
        # Remove the base's independent component root to prove access from the singleton child.
        classes["HotFixBattle:Common.Base"]["base"] = ""
        classes["HotFixBattle:Common.Base"]["parents"] = []
        hero = dump_scan.object_paths(classes)["HotFixBattle:Common.Hero"]
        self.assertEqual(hero["object"], "HotFixBattle:Child.Manager.instance")
        self.assertEqual(hero["path"], [{"field": "hero", "class": "HotFixBattle:Common.Hero"}])
        self.assertEqual(dump_scan.path_errors(classes, hero["object"], hero["root_class"], hero["path"]), [])
        manager = classes["HotFixBattle:Child.Manager"]
        manager["fields"]["hero"] = {"ctype": "int32_t"}
        manager["getters"].add("Target")
        self.assertNotIn("HotFixBattle:Common.Hero", dump_scan.object_paths(classes))
        self.assertTrue(dump_scan.path_errors(classes, hero["object"], hero["root_class"], hero["path"]))

    def test_transitive_component_roots_are_shared_by_scan_and_validation(self):
        self.classes["HotFixBattle:HotFixBattle.Player"]["base"] = "MonoBehaviour"
        self.classes["HotFixBattle:HotFixBattle.Player"]["parents"] = ["MonoBehaviour"]
        manager = self.classes["HotFixBattle:HotFixBattle.EntityManager"]
        manager["base"], manager["parents"] = "Player", ["Player"]
        paths = dump_scan.object_paths(self.classes)
        key = "HotFixBattle:HotFixBattle.EntityManager"
        self.assertEqual(paths[key]["object"], key + "[]")
        self.assertEqual(dump_scan.path_errors(self.classes, key + "[]", key, []), [])

    def test_validation_rejects_aliases_the_runtime_cannot_find(self):
        bullet = dump_scan.object_paths(self.classes)["HotFixBattle:HotFixBattle.EntityBullet"]
        check = lambda obj, root, path: dump_scan.path_errors(self.classes, obj, root, path)
        self.assertTrue(check(bullet["object"], "WorldBattle", bullet["path"]))
        self.assertTrue(check("HotFix.BattleGame._world", bullet["root_class"], bullet["path"]))
        bad = [dict(bullet["path"][0], **{"class": "BattleLogicWorld"})] + bullet["path"][1:]
        self.assertTrue(check(bullet["object"], bullet["root_class"], bad))
        self.dump.write_text(DUMP, encoding="utf-8")
        classes = dump_scan.parse(self.dump)
        self.assertEqual(dump_scan.path_errors(classes, "SkillsManager.instance", "SkillsManager", []), [])



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
public class Ship : Sprite // TypeDefIndex: 31
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

    def test_named_after_bundle_id_and_bases_followed(self):
        classes = dump_scan.parse(self.path, bundle_id="com.example.SpaceRaid")
        self.assertIn("SpaceRaid.Runtime:SpaceRaid.Ship", classes)
        base = classes["Engine.Port:Engine.Sprite"]
        self.assertTrue(base["outside"])
        self.assertEqual(base["fields"], {})  # a base outside the game's code: inheritance only, never scanned
        resolve = dump_scan._type_resolver(classes)
        self.assertTrue(dump_scan._component("SpaceRaid.Runtime:SpaceRaid.Ship", classes, resolve))
        rows = dump_scan.build(classes)[0]
        self.assertTrue(any(r["member"] == "Shield" for r in rows))
        self.assertFalse(any(r["class"].endswith("Sprite") for r in rows))

    def test_without_bundle_id_only_assembly_csharp(self):
        self.assertEqual(dump_scan.parse(self.path), {})

    def test_scan_assemblies_accept_dll_names(self):
        classes = dump_scan.parse(self.path, assemblies=["SpaceRaid.Runtime.dll"])
        self.assertIn("SpaceRaid.Runtime:SpaceRaid.Ship", classes)

    def test_assembly_sizes_skip_libraries(self):
        self.assertEqual(dump_scan.assembly_sizes(self.path)[0], (10, "Engine.Port"))


if __name__ == "__main__":
    unittest.main()


CONTENT = """// Image 0: mscorlib.dll - 0
// Image 1: Assembly-CSharp.dll - 10
// Image 2: Client.dll - 20

// Namespace: RootMotion.FinalIK
public class AimIK : MonoBehaviour // TypeDefIndex: 11
{
\tpublic float clampWeight; // 0x10
}

// Namespace: Game
public class PlayerController : MonoBehaviour // TypeDefIndex: 21
{
\tpublic static PlayerController instance; // 0x0
\tpublic float health; // 0x10
\tpublic int gold; // 0x14
\tpublic float speed; // 0x18
}
"""


class ContentChoice(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".cs")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            more = "".join(f"\tpublic int ammo{i}; // 0x{0x20 + 4 * i:X}\n" for i in range(12))  # above the minimum
            f.write(CONTENT.replace("\tpublic float speed; // 0x18\n", "\tpublic float speed; // 0x18\n" + more))

    def tearDown(self):
        for p in (self.path, str(Path(self.path).with_name("assemblies.json"))):
            if os.path.exists(p):
                os.remove(p)

    def test_an_assembly_whose_content_is_game_code_is_read_without_any_setting(self):
        profile = dump_scan.assembly_profile(self.path)
        self.assertGreater(profile["Client"]["score"], profile["Assembly-CSharp"]["score"])
        self.assertEqual(dump_scan.auto_assemblies(profile, {"Assembly-CSharp"}), {"Client"})
        classes = dump_scan.parse(self.path)
        self.assertIn("Client:Game.PlayerController", classes)

    def test_bought_package_rows_rank_low_and_are_marked(self):
        rows, _ = dump_scan.build(dump_scan.parse(self.path))
        aim = next(r for r in rows if r["member"] == "clampWeight")
        hp = next(r for r in rows if r["member"] == "health")
        self.assertTrue(aim.get("package"))
        self.assertFalse(hp.get("package"))
        self.assertLess(aim["score"], hp["score"])

    def test_the_scan_reports_what_it_read(self):
        cov = dump_scan.scan(self.path)["coverage"]
        self.assertIn("Client", cov["read"])
        self.assertEqual(cov["package_rows"], 1)


SHAPES = """// Image 0: mscorlib.dll - 0
// Image 1: Assembly-CSharp.dll - 10

// Namespace:
public class Weapon : MonoBehaviour // TypeDefIndex: 11
{
}

// Namespace:
public class Sword : Weapon // TypeDefIndex: 12
{
	public float swing; // 0x10
}

// Namespace:
public class Axe : Weapon // TypeDefIndex: 13
{
	public float swing; // 0x10
}

// Namespace:
public class Bow : Weapon // TypeDefIndex: 14
{
	public float swing; // 0x10
}

// Namespace:
public class Player : MonoBehaviour // TypeDefIndex: 15
{
	private int <Gold>k__BackingField; // 0x18
	public int get_Gold() { }
}

// Namespace:
public class WeaponTable : ScriptableObject // TypeDefIndex: 16
{
	public float baseDamage; // 0x10
}

// Namespace:
public class EntityBase<T> : MonoBehaviour // TypeDefIndex: 17
{
	public float hp; // 0x10
	public T data; // 0x18
}

// Namespace:
public class Hero : EntityBase<HeroData> // TypeDefIndex: 18
{
}

// Namespace:
public class HeroData // TypeDefIndex: 19
{
	public int level; // 0x10
}

// Namespace:
public struct Stats // TypeDefIndex: 20
{
	public int armor; // 0x10
	public readonly int cap; // 0x14
}

// Namespace:
public class Tank : MonoBehaviour // TypeDefIndex: 21
{
	public Stats stats; // 0x18
}
"""


class MoreShapes(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".cs")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(SHAPES)
        self.classes = dump_scan.parse(self.path)
        self.rows = {f"{r['class']}.{r['member']}": r for r in dump_scan.build(self.classes)[0]}

    def tearDown(self):
        for p in (self.path, str(Path(self.path).with_name("assemblies.json"))):
            if os.path.exists(p):
                os.remove(p)

    def test_a_get_only_property_is_reached_through_its_backing_field(self):
        row = self.rows["Player.<Gold>k__BackingField"]
        self.assertEqual(row["name"], "Gold")

    def test_scriptable_object_data_assets_are_roots(self):
        self.assertEqual(self.rows["WeaponTable.baseDamage"]["object"], "WeaponTable[]")

    def test_a_generic_bases_members_reach_each_subclass(self):
        self.assertIn("Hero.hp", self.rows)
        self.assertIn("HeroData.level", self.rows)  # through EntityBase<HeroData>.data
        self.assertIsNotNone(dump_scan.pin_signature(self.classes, {"object": "Hero[]", "path": [
            {"field": "data", "class": "HeroData"}], "field": "level"}))

    def test_sibling_classes_with_the_same_field_become_one_family(self):
        swings = [r for k, r in self.rows.items() if k.endswith(".swing")]
        heads = [r for r in swings if r.get("family_size")]
        self.assertEqual(len(heads), 1)
        self.assertEqual(heads[0]["family_size"], 3)
        self.assertEqual(sum(1 for r in swings if r.get("family_member")), 2)

    def test_values_inside_a_struct_field_are_rows(self):
        row = self.rows["Tank.stats.armor"]
        self.assertEqual((row["kind"], row["ctype"], row["name"]), ("field", "int32_t", "stats armor"))
        self.assertNotIn("Tank.stats.cap", self.rows)  # readonly
        sig = dump_scan.pin_signature(self.classes, {"object": "Tank", "field": "stats.armor"})
        self.assertEqual(sig[-1], ("member", "Tank", "struct-field", "Stats.armor:int32_t"))
        self.assertIsNone(dump_scan.pin_signature(self.classes, {"object": "Tank", "field": "stats.gone"}))
