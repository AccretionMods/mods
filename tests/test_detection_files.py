"""Observable libc contracts for selected queries and write probes."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CC = shutil.which("cc") or shutil.which("clang")


@unittest.skipUnless(CC and os.name != "nt", "requires POSIX compiler")
class DetectionFiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert (ROOT / "runtime/AMDetectionFiles.h").exists(), "file adapter contracts missing"
        cls.temp = tempfile.TemporaryDirectory()
        cls.exe = str(Path(cls.temp.name) / "files")
        result = subprocess.run(
            [CC, "-std=c11", "-D_GNU_SOURCE", "-D_POSIX_C_SOURCE=200809L", "-Wall", "-Wextra", "-Werror",
             "-I", str(ROOT / "runtime"), str(ROOT / "tests/detection_files_harness.c"),
             str(ROOT / "runtime/AMDetectionHooks.c"), str(ROOT / "runtime/AMDetectionPolicy.c"),
             "-o", cls.exe], capture_output=True, text=True,
        )
        if result.returncode: raise AssertionError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_case(self, name):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run([self.exe, name, directory], check=True)

    def test_readlink_preserves_buffer_bytes_length_and_errno(self):
        self.run_case("readlink")

    def test_descriptor_relative_queries_and_unknown_context_forwarding(self):
        self.run_case("relative")

    def test_write_open_modes_deny_only_probes_and_preserve_saves(self):
        self.run_case("writes")

    def test_observation_preserves_real_write_and_readlink_results(self):
        self.run_case("observe")

    def test_oversized_rules_reject_before_configuration_without_truncation(self):
        self.run_case("invalid")
