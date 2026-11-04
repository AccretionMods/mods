import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import embed_hooks
import table_hooks

DUMP = """
// Namespace: Game
public class Foo // TypeDefIndex: 1
{
    public int health; // 0x18
    private float speed; // 0x1C
    public static int count; // 0x0
    public const int MAX = 5; // 0x0
    // RVA: 0x1234 Offset: 0x1234 VA: 0x1234
    public void Update() { }
    // RVA: 0x5678 Offset: 0x5678 VA: 0x5678
    private void Start() { }
    // RVA: 0x5680 Offset: 0x5680 VA: 0x5680
    public static void Reset() { }
    // RVA: 0x5690 Offset: 0x5690 VA: 0x5690
    public int TakeDamage(int amount, bool crit) { }
}
// Namespace:
public class Bar // TypeDefIndex: 2
{
    // RVA: 0x9abc Offset: 0x9abc VA: 0x9abc
    public void Awake() { }
}
"""

REGISTRATION = 0x1000


def image(layout, module_name=b"Assembly-CSharp.dll", pointers=(0x1234, 0x5678, 0x9ABC)):
    """A flat binary with one Il2CppCodeGenModule reachable from the registration at `layout` (no fixups)."""
    data = bytearray(0x6000)
    struct.pack_into("<Q", data, REGISTRATION + layout, 1)  # codeGenModulesCount
    struct.pack_into("<Q", data, REGISTRATION + layout + 8, 0x2000)  # codeGenModules
    struct.pack_into("<Q", data, 0x2000, 0x3000)  # modules[0]
    struct.pack_into("<QQQ", data, 0x3000, 0x4000, len(pointers), 0x5000)  # moduleName, count, methodPointers
    data[0x4000:0x4000 + len(module_name)] = module_name
    for i, p in enumerate(pointers):
        struct.pack_into("<Q", data, 0x5000 + i * 8, p)
    return table_hooks.Reader(bytes(data), {})


class ParseDump(unittest.TestCase):
    def test_methods_with_namespace_and_without(self):
        methods = table_hooks.parse_dump(DUMP)
        found = {(m.full_name, m.rva) for m in methods}
        self.assertIn(("Game.Foo.Update", 0x1234), found)
        self.assertIn(("Game.Foo.Start", 0x5678), found)
        self.assertIn(("Bar.Awake", 0x9ABC), found)
        self.assertEqual(len(methods), 5)

    def test_find_method_by_full_or_short_name(self):
        methods = table_hooks.parse_dump(DUMP)
        self.assertEqual(table_hooks.find_method(methods, "Game.Foo.Update").rva, 0x1234)
        self.assertEqual(table_hooks.find_method(methods, "Foo.Update").rva, 0x1234)
        self.assertIsNone(table_hooks.find_method(methods, "Foo.Gone"))

    def test_signature_facts(self):
        methods = {m.name: m for m in table_hooks.parse_dump(DUMP)}
        self.assertIsNone(table_hooks.pin_problem(methods["Update"]))
        self.assertIn("static", table_hooks.pin_problem(methods["Reset"]))
        self.assertIn("2 parameter", table_hooks.pin_problem(methods["TakeDamage"]))
        self.assertIn("void", table_hooks.pin_problem(table_hooks.Method("", "C", "Get", 1, "public int Get() { }")))

    def test_fields_skip_static_and_const(self):
        fields = table_hooks.parse_fields(DUMP)
        self.assertEqual({(f.full_name, f.offset) for f in fields}, {("Game.Foo.health", 0x18), ("Game.Foo.speed", 0x1C)})
        self.assertEqual(table_hooks.find_field(fields, "Foo.speed").offset, 0x1C)


class Layout(unittest.TestCase):
    def test_each_known_layout_is_detected(self):
        for layout in table_hooks.LAYOUTS:
            offset, modules = table_hooks.detect_layout(image(layout), REGISTRATION)
            self.assertEqual(offset, layout)
            self.assertEqual([(m.name, m.count, m.pointers) for m in modules], [("Assembly-CSharp.dll", 3, 0x5000)])

    def test_no_table_is_an_error(self):
        with self.assertRaises(SystemExit):
            table_hooks.detect_layout(table_hooks.Reader(bytes(0x6000), {}), REGISTRATION)
        with self.assertRaises(SystemExit):  # the module name is not an assembly
            table_hooks.detect_layout(image(104, module_name=b"junk"), REGISTRATION)

    def test_pointer_map_reads_every_slot_once(self):
        reader = image(120)
        _, modules = table_hooks.detect_layout(reader, REGISTRATION)
        self.assertEqual(table_hooks.pointer_map(reader, modules), {0x1234: (0, 0), 0x5678: (0, 1), 0x9ABC: (0, 2)})
        self.assertEqual(table_hooks.pointer_map(reader, modules, "Other.dll"), {})

    def test_fixups_decode_stored_pointers(self):
        reader = image(104)
        reader.relocations[0x5000] = 0x1234  # a chained fixup for slot 0 (the file bytes hold something else)
        reader.data = bytes(reader.data[:0x5000]) + struct.pack("<Q", 0xDEAD) + bytes(reader.data[0x5008:])
        _, modules = table_hooks.detect_layout(reader, REGISTRATION)
        self.assertIn(0x1234, table_hooks.pointer_map(reader, modules))


class Generate(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.dump = os.path.join(self.dir.name, "dump.cs")
        with open(self.dump, "w", encoding="utf-8") as f:
            f.write(DUMP)

    def tearDown(self):
        self.dir.cleanup()

    def test_resolves_names_to_module_and_index(self):
        result = table_hooks.generate(self.dump, "unused", REGISTRATION, ["Game.Foo.Update", "Bar.Awake"], reader=image(120))
        self.assertEqual(result["modules_offset"], 120)
        self.assertEqual([(h["name"], h["module"], h["index"]) for h in result["hooks"]],
                         [("Game.Foo.Update", 0, 0), ("Bar.Awake", 0, 2)])

    def test_missing_method_or_pointer_is_an_error(self):
        with self.assertRaises(SystemExit):
            table_hooks.generate(self.dump, "unused", REGISTRATION, ["Foo.Gone"], reader=image(120))
        with self.assertRaises(SystemExit):  # Reset's RVA is in the dump but not in methodPointers
            table_hooks.generate(self.dump, "unused", REGISTRATION, ["Foo.Reset"], reader=image(120))

    def test_header(self):
        result = table_hooks.generate(self.dump, "unused", REGISTRATION, ["Game.Foo.Update"], reader=image(104))
        text = table_hooks.header(result)
        self.assertIn("AM_TABLE_REGISTRATION_RVA 0x1000ULL", text)
        self.assertIn("AM_TABLE_MODULES_OFFSET 104", text)
        self.assertIn("AM_TABLE_HOOK_COUNT 1", text)
        self.assertIn('{ "Game.Foo.Update", 0, 0, 0, 0 }', text)

    def test_pin_stubs(self):
        pins = [{"name": "Game.Foo.Update", "module": 0, "index": 0, "offset": 0x18, "type": "int", "value": 100},
                {"name": "Game.Foo.Update", "module": 0, "index": 0, "offset": 0x1C, "type": "float", "value": 2.5},
                {"name": "Game.Foo.Update", "module": 0, "index": 0, "offset": 0x20, "type": "int64", "value": 7}]
        text = "\n".join(table_hooks.pin_stubs(pins))
        self.assertIn("*(int32_t *)((char *)self + 24) = 100;", text)
        self.assertIn("*(float *)((char *)self + 28) = 2.5f;", text)
        self.assertIn("*(int64_t *)((char *)self + 32) = 7LL;", text)
        with self.assertRaises(SystemExit):
            table_hooks.pin_value({"name": "x", "type": "int", "value": "many"})

    # embed_hooks on top of it: the mod's hooks.json "table" section

    def hooks(self, pins=None):
        return {"engine": "unity-il2cpp",
                "hooks": [{"assembly": "Assembly-CSharp", "namespace": "Game", "class": "Foo", "method": "Start"}],
                "pins": pins or []}

    def test_spec_names_keep_the_namespace(self):
        self.assertEqual(embed_hooks.spec_name({"namespace": "Game", "class": "Foo", "method": "Start"}), "Game.Foo.Start")
        self.assertEqual(embed_hooks.spec_name({"namespace": "", "class": "Bar", "method": "Awake"}), "Bar.Awake")

    def test_resolve_table_with_pins(self):
        pin = {"class": "Foo", "method": "Update", "field": "health", "type": "int", "value": 100}
        table, pins = embed_hooks.resolve_table(self.hooks([pin]), self.dump, "unused", REGISTRATION, reader=image(120))
        self.assertEqual([h["name"] for h in table["hooks"]], ["Game.Foo.Start"])
        self.assertEqual(pins, [{"name": "Foo.Update", "module": 0, "index": 0, "offset": 0x18, "type": "int", "value": 100}])

    def test_pin_on_a_method_with_parameters_is_refused(self):
        pin = {"class": "Foo", "method": "TakeDamage", "field": "health", "type": "int", "value": 100}
        with self.assertRaises(SystemExit):
            embed_hooks.resolve_table(self.hooks([pin]), self.dump, "unused", REGISTRATION, reader=image(120))
        pin = {"class": "Foo", "method": "Update", "field": "count", "type": "int", "value": 1}  # static field
        with self.assertRaises(SystemExit):
            embed_hooks.resolve_table(self.hooks([pin]), self.dump, "unused", REGISTRATION, reader=image(120))

    def test_render_table_hides_names(self):
        pin = {"class": "Foo", "method": "Update", "field": "health", "type": "int", "value": 100}
        table, pins = embed_hooks.resolve_table(self.hooks([pin]), self.dump, "unused", REGISTRATION, reader=image(120))
        out = embed_hooks.render(self.hooks([pin]), table, pins)
        self.assertIn("am_hook_table_specs = am_table_hooks;", out)
        self.assertIn("am_hook_table_count = 2;", out)
        self.assertIn("am_hook_table_registration = 0x1000ULL;", out)
        self.assertIn("am_hook_table_modules_offset = 120;", out)
        self.assertIn("{ t0, 0, 1, 0, 0 },", out)
        self.assertIn("{ t1, 0, 0, (void *)rep_pin0, (void **)&orig_pin0 },", out)
        self.assertIn("am_unhide(t1, (const unsigned char *)t1, 10);", out)
        for name in ("Game.Foo.Start", "Foo.Update", '"Foo', "Start\""):
            self.assertNotIn(name, out)

    def test_render_without_table(self):
        out = embed_hooks.render(self.hooks())
        self.assertIn("am_hook_table_specs = 0;", out)
        self.assertIn("am_hook_table_count = 0;", out)
        self.assertIn("am_hook_table_modules_offset = 0;", out)


if __name__ == "__main__":
    unittest.main()
