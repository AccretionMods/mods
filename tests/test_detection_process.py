"""Validate posix_spawn's direct-error and output-ownership contracts."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]
CC = shutil.which("cc") or shutil.which("clang")


@unittest.skipUnless(CC and os.name != "nt", "requires POSIX compiler")
class DetectionProcess(unittest.TestCase):
    def test_spawn_denial_and_ordinary_forwarding(self):
        with tempfile.TemporaryDirectory() as d:
            exe = str(Path(d) / "process")
            result = subprocess.run([CC, "-std=c11", "-D_POSIX_C_SOURCE=200809L", "-Wall", "-Wextra", "-Werror",
                                     "-I", str(ROOT / "runtime"), str(ROOT / "tests/detection_process_harness.c"),
                                     str(ROOT / "runtime/AMDetectionProcess.c"), str(ROOT / "runtime/AMDetectionPolicy.c"),
                                     str(ROOT / "runtime/AMDetectionHooks.c"), "-o", exe], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for mode in ("filter", "observe", "off", "inactive"):
                subprocess.run([exe, mode], check=True)
