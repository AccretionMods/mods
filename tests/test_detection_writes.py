"""Constrained display-name expansion; Apple write contracts stay separate."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WriteProbeExpansion(unittest.TestCase):
    def setUp(self):
        before = sys.path.copy();sys.path.insert(0, str(ROOT / "scripts"))
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), before))
        spec = importlib.util.spec_from_file_location("profile", ROOT / "scripts/detection_profile.py")
        self.module = importlib.util.module_from_spec(spec);spec.loader.exec_module(self.module)

    def test_display_name_expands_only_to_one_exact_private_child(self):
        self.assertEqual(self.module.expand_write_probe("Game Name"), "/private/Game Name")
        self.assertEqual(self.module.expand_write_probe("Pokémon"), "/private/Pokémon")
        for value in ("", ".", "..", "../Game", "Game/Child", "Game\\Child", "a\0b", "var", "TMP", "x" * 4087):
            with self.subTest(value=value):
                with self.assertRaises(ValueError): self.module.expand_write_probe(value)

    @unittest.skipUnless(shutil.which("cc"), "requires C compiler")
    def test_native_expansion_has_same_bounds_and_rejects_embedded_nul(self):
        with tempfile.TemporaryDirectory() as d:
            exe = str(Path(d) / "writes")
            subprocess.run([shutil.which("cc"), "-std=c11", "-Wall", "-Wextra", "-Werror",
                            "-I", str(ROOT / "runtime"), str(ROOT / "tests/detection_writes_harness.c"),
                            str(ROOT / "runtime/AMDetectionWrites.c"), "-o", exe], check=True)
            subprocess.run([exe], check=True)
