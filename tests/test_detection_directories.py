"""Exercise the production directory map with real streams and deterministic reuse."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CC = shutil.which("cc") or shutil.which("clang")


@unittest.skipUnless(CC and os.name != "nt", "requires POSIX compiler")
class DetectionDirectories(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert (ROOT / "runtime/AMDetectionDirectories.h").exists(), "directory adapter missing"
        cls.temp = tempfile.TemporaryDirectory()
        cls.exe = str(Path(cls.temp.name) / "directories")
        result = subprocess.run(
            [CC, "-std=c11", "-D_GNU_SOURCE", "-Wall", "-Wextra", "-Werror", "-pthread",
             "-I", str(ROOT / "runtime"), str(ROOT / "tests/detection_directories_harness.c"),
             str(ROOT / "runtime/AMDetectionDirectories.c"), str(ROOT / "runtime/AMDetectionHooks.c"),
             str(ROOT / "runtime/AMDetectionPolicy.c"), "-o", cls.exe], capture_output=True, text=True,
        )
        if result.returncode: raise AssertionError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_case(self, name):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run([self.exe, name, directory], check=True)

    def test_hidden_child_retains_siblings_stream_position_and_eof_errno(self):
        self.run_case("stream")

    def test_257th_stream_is_unfiltered_and_marks_coverage_incomplete(self):
        self.run_case("capacity")

    def test_cross_thread_close_and_handle_reuse_have_no_stale_parent(self):
        self.run_case("reuse")

    def test_filtered_read_retains_original_positive_result(self):
        self.run_case("outcome")

    def test_relative_stream_without_descriptor_witness_is_unfiltered(self):
        self.run_case("relative")

    @unittest.skip("Apple Foundation execution is unavailable on this POSIX host")
    def test_foundation_enumerator_contract(self):
        pass
