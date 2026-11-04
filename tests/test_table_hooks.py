from scripts.embed_hooks import render
from scripts.table_hooks import header, parse_dump, parse_fields

DUMP = """
// Namespace: Game
public class Foo // TypeDefIndex: 1
{
    // RVA: 0x1234 Offset: 0x1234 VA: 0x1234
    public void Update() { }
    // RVA: 0x5678 Offset: 0x5678 VA: 0x5678
    private void Start() { }
}
// Namespace: 
public class Bar // TypeDefIndex: 2
{
    // RVA: 0x9abc Offset: 0x9abc VA: 0x9abc
    public void Awake() { }
}
"""


FIELDS = """
// Namespace: Game
public class Foo // TypeDefIndex: 1
{
    public int health; // 0x18
    private float speed; // 0x1C
}
"""


def test_parse_fields():
    fields = parse_fields(FIELDS)
    found = {(f.full_name, f.offset) for f in fields}
    assert ("Game.Foo.health", 0x18) in found
    assert ("Game.Foo.speed", 0x1C) in found
    assert len(fields) == 2


def test_parse_dump_methods():
    methods = parse_dump(DUMP)
    found = {(m.full_name, m.rva) for m in methods}
    assert ("Game.Foo.Update", 0x1234) in found
    assert ("Game.Foo.Start", 0x5678) in found
    assert ("Bar.Awake", 0x9ABC) in found
    assert len(methods) == 3


def test_header_contains_table_entries():
    result = {"registration_rva": 0x5593A48, "hooks": [
        {"name": "Game.Foo.Update", "module": 7, "index": 529, "rva": 0x1234, "assembly": "Assembly-CSharp.dll"},
        {"name": "Game.Foo.Start", "module": 7, "index": 534, "rva": 0x5678, "assembly": "Assembly-CSharp.dll"},
    ]}
    text = header(result)
    assert "AM_TABLE_REGISTRATION_RVA 0x5593a48ULL" in text
    assert "AM_TABLE_HOOK_COUNT 2" in text
    assert '{ "Game.Foo.Update", 7, 529, 0, 0 }' in text
    assert '{ "Game.Foo.Start", 7, 534, 0, 0 }' in text


def test_embed_hooks_table_symbols():
    data = {"engine": "unity-il2cpp", "hooks": [
        {"assembly": "Assembly-CSharp", "namespace": "", "class": "GroundEffectPlayer", "method": "Start"}]}
    with_table = render(data, True)
    assert "am_hook_table_specs = AMTableHooks" in with_table
    assert "am_hook_table_count = AM_TABLE_HOOK_COUNT" in with_table
    without_table = render(data, False)
    assert "am_hook_table_specs = 0" in without_table
    assert "am_hook_table_count = 0" in without_table
