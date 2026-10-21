"""scripts/dev.py add, pin, call, remove, update and status on a synthetic mod and dump (no game, no phone)."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import dev

DUMP = """\
// Image 0: mscorlib.dll - 0
// Image 1: Assembly-CSharp.dll - 100

// Namespace:
public enum Kind // TypeDefIndex: 100
{
	public int value__; // 0x0
	public const Kind A = 0;
}

// Namespace:
public class StatHandler : MonoBehaviour // TypeDefIndex: 101
{
	// Fields
	public static StatHandler instance; // 0x0
	private int <Hunger>k__BackingField; // 0x20
	public float HealthMax; // 0x24

	// Methods

	// RVA: 0x1000 Offset: 0x1000 VA: 0x1000
	public int get_Hunger() { }

	// RVA: 0x1010 Offset: 0x1010 VA: 0x1010
	public void set_Hunger(int value) { }

	// RVA: 0x1020 Offset: 0x1020 VA: 0x1020 Slot: 4
	private void Update() { }
}

// Namespace:
public class Enemy : MonoBehaviour // TypeDefIndex: 102
{
	// Fields
	public long EnemyDamage; // 0x60
	public bool HasHealthPotion; // 0xB8

	// Methods

	// RVA: 0x2000 Offset: 0x2000 VA: 0x2000
	public void DamagePlayer(long dmg, bool crit) { }

	// RVA: 0x2010 Offset: 0x2010 VA: 0x2010
	public void DamagePlayer(long dmg, bool crit, Kind kind) { }

	// RVA: 0x2020 Offset: 0x2020 VA: 0x2020
	public float get_AttackSuccessRate() { }
}

// Namespace:
public class InventoryData // TypeDefIndex: 103
{
	// Fields
	public static InventoryData instance; // 0x0

	// Methods

	// RVA: 0x3000 Offset: 0x3000 VA: 0x3000
	public void GiveRandomItem(int level = -1) { }
}
"""


def run(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        code = dev.main(argv)
    return code, out.getvalue()


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.mod = root / "repo" / "mods" / "game-plus"
        self.mod.mkdir(parents=True)
        (self.mod / "Makefile").write_text("TWEAK_NAME = GamePlus\nGamePlus_FILES = Tweak.x AMTweaks.gen.m runtime/am_il2cpp.c runtime/AMIl2cpp.m runtime/am_hooks_data.c\n")
        (self.mod / "mod.json").write_text(json.dumps({
            "name": "Game+", "display_name": "Game+", "version": "1.0.0", "kind": "tweak", "personal": True,
            "target_bundle_id": "com.example.game", "bundle_id": "io.github.accretionmods.gameplus",
            "tested_app_version": "1.0", "base": {"private": True, "sha256": "0" * 64},
        }))
        dump_dir = root / "am" / "work" / "dump-game-plus-1.0" / "out"
        dump_dir.mkdir(parents=True)
        (dump_dir / "dump.cs").write_text(DUMP)
        (root / "am" / "dev").mkdir(parents=True)
        (root / "am" / "dev" / "game-plus.json").write_text(json.dumps({"dump_dir": str(dump_dir.parent)}))
        self.old_root, self.old_env = dev.ROOT, os.environ.get("AM_ROOT")
        dev.ROOT = root / "repo"
        os.environ["AM_ROOT"] = str(root / "am")
        dev._dump_cache.clear()

    def tearDown(self):
        dev.ROOT = self.old_root
        if self.old_env is None:
            os.environ.pop("AM_ROOT", None)
        else:
            os.environ["AM_ROOT"] = self.old_env
        self.tmp.cleanup()

    def tweaks(self):
        return json.loads((self.mod / "tweaks.json").read_text())

    def hooks(self):
        return json.loads((self.mod / "hooks.json").read_text())["hooks"]

    def test_add_needs_an_overload_choice_then_writes_both_files(self):
        code, out = run(["add", "game-plus", "Enemy.DamagePlayer", "--as", "noDamage", "--title", "No damage"])
        self.assertEqual(code, 1)
        code, out = run(["add", "game-plus", "Enemy.DamagePlayer", "--args", "2", "--as", "noDamage", "--title", "No damage"])
        self.assertEqual(code, 0, out)
        h = self.hooks()[0]
        self.assertEqual((h["class"], h["method"], h["args"], h["signature"]), ("Enemy", "DamagePlayer", 2, "public void DamagePlayer(long dmg, bool crit)"))
        self.assertTrue(h["needs_jit"])
        self.assertEqual(self.tweaks()["tweaks"][0]["hooks"], [{"method": "Enemy.DamagePlayer", "action": "skip"}])
        self.assertIn("without JIT this hook catches nothing", out)
        self.assertIn("static void hook_Enemy_DamagePlayer(void *self, int64_t dmg, bool crit, const void *mi)", (self.mod / "AMTweaks.gen.m").read_text())

    def test_add_records_enums_and_unity_callbacks_need_no_jit(self):
        code, _ = run(["add", "game-plus", "Enemy.DamagePlayer", "--args", "3", "--as", "a", "--title", "A"])
        self.assertEqual(code, 0)
        self.assertEqual(self.hooks()[0]["enums"], ["Kind"])
        code, _ = run(["add", "game-plus", "StatHandler.Update", "--action", "custom", "--as", "tick", "--title", "Tick"])
        self.assertEqual(code, 0)
        self.assertFalse(self.hooks()[1]["needs_jit"])

    def test_pin_setter_default_object_and_field_types(self):
        code, out = run(["pin", "game-plus", "StatHandler.set_Hunger", "--value", "100", "--when", "lower", "--as", "keep", "--title", "Keep"])
        self.assertEqual(code, 0, out)
        pin = self.tweaks()["tweaks"][0]["pins"][0]
        self.assertEqual(pin, {"object": "StatHandler.instance", "when": "lower", "setter": "set_Hunger", "ctype": "int32_t", "value": 100})
        code, out = run(["pin", "game-plus", "Enemy.EnemyDamage", "--object", "Enemy[]", "--value", "0", "--to", "keep"])
        self.assertEqual(code, 0, out)
        self.assertEqual(self.tweaks()["tweaks"][0]["pins"][1]["ctype"], "int64_t")
        code, out = run(["pin", "game-plus", "Enemy.HasHealthPotion", "--object", "Enemy[]", "--value", "false", "--to", "keep"])
        self.assertEqual(code, 0, out)
        self.assertIs(self.tweaks()["tweaks"][0]["pins"][2]["value"], False)
        code, out = run(["pin", "game-plus", "StatHandler.set_Health", "--value", "getter:get_HealthMax", "--to", "keep"])
        self.assertEqual(code, 1)  # no such setter in the dump
        code, out = run(["pin", "game-plus", "StatHandler.instance", "--value", "1", "--to", "keep"])
        self.assertEqual(code, 1)  # static field
        gen = (self.mod / "AMTweaks.gen.m").read_text()
        self.assertIn('AMIl2cppCall(obj, "StatHandler", "get_Hunger", 0, NULL, &boxed)', gen)
        self.assertNotIn("stringByAppendingString:jit", gen)  # pins: no (JIT) label

    def test_call_with_default_argument(self):
        code, out = run(["call", "game-plus", "InventoryData.GiveRandomItem", "--args", "-1", "--every", "10", "--as", "give", "--title", "Give"])
        self.assertEqual(code, 0, out)
        pin = self.tweaks()["tweaks"][0]["pins"][0]
        self.assertEqual((pin["object"], pin["call"], pin["args"], pin["arg_ctypes"], pin["every"]), ("InventoryData.instance", "GiveRandomItem", [-1], ["int32_t"], 10.0))

    def test_remove_drops_unused_hooks_only(self):
        run(["add", "game-plus", "Enemy.get_AttackSuccessRate", "--as", "miss", "--title", "Miss", "--value", "0"])
        run(["add", "game-plus", "Enemy.DamagePlayer", "--args", "2", "--as", "noDamage", "--title", "No damage"])
        code, out = run(["remove", "game-plus", "miss"])
        self.assertEqual(code, 0, out)
        self.assertEqual([t["key"] for t in self.tweaks()["tweaks"]], ["noDamage"])
        self.assertEqual([h["method"] for h in self.hooks()], ["DamagePlayer"])

    def test_status_and_check(self):
        run(["add", "game-plus", "Enemy.get_AttackSuccessRate", "--as", "miss", "--title", "Miss", "--value", "0"])
        code, out = run(["status", "game-plus"])
        self.assertEqual(code, 0)
        self.assertIn("miss (switch): Miss  <- Enemy.get_AttackSuccessRate return", out)
        code, out = run(["check", "game-plus"])
        self.assertEqual(code, 0)
        self.assertIn("1 ok, 0 changed, 0 missing", out)

    def test_find_and_fields(self):
        code, out = run(["find", "game-plus", "Damage"])
        self.assertEqual(code, 0)
        self.assertIn("Enemy.DamagePlayer(long dmg, bool crit) -> void", out)
        self.assertIn("needs JIT", out)
        code, out = run(["fields", "game-plus", "Enemy"])
        self.assertIn("0x60    long EnemyDamage", out)


if __name__ == "__main__":
    unittest.main()
