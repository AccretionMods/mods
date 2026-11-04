"""The IL2CPP symbol inspector classifies export-trie, symtab and undefined symbols.

Fixtures are synthetic 64-bit Mach-O images (tests/macho_fixtures.py: thin, fat, executable, and zipped as
an .ipa) whose names carry Darwin's leading underscore, as a real UnityFramework's do.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import inspect_il2cpp_symbols as inspector
import macho_fixtures as fx

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "inspect_il2cpp_symbols.py"
PY = sys.executable
TOTAL = len(inspector.SYMBOLS)


def image(**kw):
    return fx.thin_image(exported=("_il2cpp_domain_get",), defined=("_il2cpp_class_from_name",),
                         undefined=("_il2cpp_runtime_invoke",), **kw)


class Inspector(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.thin = image()
        (self.dir / "UnityFramework").write_bytes(self.thin)
        (self.dir / "fat.bin").write_bytes(fx.fat_image([(fx.X86_64, self.thin), (fx.ARM64, self.thin)]))
        (self.dir / "Game").write_bytes(image(executable=True))
        framework = self.dir / "UnityFramework.framework"
        framework.mkdir()
        (framework / "UnityFramework").write_bytes(self.thin)
        with zipfile.ZipFile(self.dir / "Test.ipa", "w") as archive:
            archive.writestr("Payload/Test.app/Test", b"dummy")
            archive.writestr(
                "Payload/Test.app/Frameworks/UnityFramework.framework/UnityFramework", self.thin
            )

    def run_tool(self, *args):
        return subprocess.run([PY, str(SCRIPT), *args], check=False, capture_output=True, text=True)

    def test_classifies_export_symtab_and_import(self):
        result = self.run_tool(str(self.dir / "UnityFramework"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("export  il2cpp_domain_get", result.stdout)
        self.assertIn("symtab  il2cpp_class_from_name", result.stdout)
        self.assertIn("import  il2cpp_runtime_invoke", result.stdout)
        self.assertIn(f"resolved 2/{TOTAL}", result.stdout)

    def test_names_match_with_or_without_the_underscore(self):
        parsed = inspector.parse_thin(self.thin)
        self.assertEqual(inspector.classify(parsed, "il2cpp_domain_get"), "export")
        self.assertEqual(inspector.classify(parsed, "_il2cpp_domain_get"), "export")
        self.assertEqual(inspector.classify(parsed, "_il2cpp_class_from_name"), "symtab")
        result = self.run_tool(str(self.dir / "UnityFramework"), "--symbol", "_il2cpp_domain_get", "--json")
        self.assertEqual(json.loads(result.stdout)["rows"], [
            {"symbol": "il2cpp_domain_get", "status": "export", "required": True}])

    def test_executable_and_framework_folder(self):
        result = self.run_tool(str(self.dir / "Game"), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["counts"]["export"], 1)
        result = self.run_tool(str(self.dir / "UnityFramework.framework"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("UnityFramework.framework", result.stdout)
        self.assertIn("symtab  il2cpp_class_from_name", result.stdout)

    def test_fat_prefers_arm64(self):
        result = self.run_tool(str(self.dir / "fat.bin"), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["arch"], "arm64")

    def test_ipa_extracts_unityframework(self):
        result = self.run_tool(str(self.dir / "Test.ipa"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("UnityFramework.framework/UnityFramework", result.stdout)
        self.assertIn("symtab  il2cpp_class_from_name", result.stdout)

    def test_json_counts(self):
        result = self.run_tool(str(self.dir / "UnityFramework"), "--json")
        report = json.loads(result.stdout)
        self.assertEqual(report["counts"], {"export": 1, "symtab": 1, "import": 1, "missing": TOTAL - 3})
        self.assertIn("il2cpp_get_corlib", report["required_missing"])

    def test_require_all_exit_code(self):
        result = self.run_tool(str(self.dir / "UnityFramework"), "--require-all")
        self.assertEqual(result.returncode, 1)

    def test_bad_inputs_are_errors_not_tracebacks(self):
        self.assertEqual(self.run_tool(str(self.dir / "does-not-exist.ipa")).returncode, 2)
        (self.dir / "not.ipa").write_bytes(b"not a zip")
        result = self.run_tool(str(self.dir / "not.ipa"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("error:", result.stderr)
        (self.dir / "empty").mkdir()
        result = self.run_tool(str(self.dir / "empty"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("error:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
