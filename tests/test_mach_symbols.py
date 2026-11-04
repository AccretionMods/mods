"""AMMachSymbols.c resolves defined symbols from a mapped Mach-O symbol table."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")


@unittest.skipUnless(CC, "requires a C compiler")
class MachSymbols(unittest.TestCase):
    def test_symbol_table_lookup(self):
        with tempfile.TemporaryDirectory() as d:
            exe = str(Path(d) / "mach_symbols")
            subprocess.run(
                [
                    CC,
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(ROOT / "runtime"),
                    str(ROOT / "tests" / "mach_symbols_harness.c"),
                    str(ROOT / "runtime" / "AMMachSymbols.c"),
                    "-o",
                    exe,
                ],
                check=True,
            )
            out = subprocess.run([exe], check=True, capture_output=True, text=True).stdout
            for line in out.splitlines():
                if line.startswith("FAIL"):
                    self.fail(line)
            self.assertIn("failures=0", out)


if __name__ == "__main__":
    unittest.main()
