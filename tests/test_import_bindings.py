"""Synthetic mapped Mach-O fixtures exercise the actual import parser."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CC = shutil.which("cc") or shutil.which("clang")


@unittest.skipUnless(CC, "requires a C compiler")
class ImportBindings(unittest.TestCase):
    def test_mapped_images_and_failure_states(self):
        self.assertTrue(
            (ROOT / "runtime/AMImportBindings.c").exists(),
            "production import walker missing",
        )
        with tempfile.TemporaryDirectory() as d:
            exe = str(Path(d) / "bindings")
            subprocess.run(
                [
                    CC,
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(ROOT / "runtime"),
                    str(ROOT / "tests/import_bindings_harness.c"),
                    str(ROOT / "runtime/AMImportBindings.c"),
                    "-o",
                    exe,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run([exe], check=True)
