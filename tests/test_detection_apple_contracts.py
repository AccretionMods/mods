"""Run actual Foundation contracts on Apple hosts; never substitute a simulated pass."""
import json
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


class AppleContracts(unittest.TestCase):
    def load_runner(self):
        spec = importlib.util.spec_from_file_location("apple_contracts", ROOT / "scripts/detection_apple_contracts.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_compile_failure_never_becomes_execution_evidence(self):
        module = self.load_runner()
        with tempfile.TemporaryDirectory() as d, mock.patch.object(module.sys, "platform", "darwin"), \
                mock.patch.object(module.shutil, "which", return_value="/fixture/clang"), \
                mock.patch.object(module.platform, "mac_ver", return_value=("fixture", (), "")), \
                mock.patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess([], 7)):
            report, code = module.run_contracts(Path(d) / "report.json")
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["reason"], "compile_failed")
        self.assertFalse(report["executed"])
        self.assertEqual(report["passed_modes"], [])

    def test_execution_timeout_preserves_source_identity_and_failed_status(self):
        module = self.load_runner()
        results = [subprocess.CompletedProcess([], 0)] * 4 + [subprocess.TimeoutExpired("fixture", 30)]
        with tempfile.TemporaryDirectory() as d, mock.patch.object(module.sys, "platform", "darwin"), \
                mock.patch.object(module.shutil, "which", return_value="/fixture/clang"), \
                mock.patch.object(module.platform, "mac_ver", return_value=("fixture", (), "")), \
                mock.patch.object(module.subprocess, "run", side_effect=results):
            report, code = module.run_contracts(Path(d) / "report.json")
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["reason"], "execution_timeout")
        self.assertTrue(report["executed"])
        self.assertFalse(report["device_verified"])
        self.assertFalse(report["positive_artifact_verified"])
        self.assertEqual(len(report["source_digest"]), 64)
        self.assertEqual(report["passed_modes"], [])

    @unittest.skipIf(sys.platform == "darwin", "requires a non-Apple host")
    def test_unavailable_runtime_writes_explicit_nonpassing_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            output = Path(d) / "report.json"
            result = subprocess.run([sys.executable, str(ROOT / "scripts/detection_apple_contracts.py"),
                                     "--output", str(output)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertTrue(output.is_file(), result.stderr)
            report = json.loads(output.read_text())
            self.assertEqual(report["status"], "unavailable")
            self.assertFalse(report["executed"])
            self.assertFalse(report["device_verified"])
            self.assertEqual(len(report["source_digest"]), 64)
            self.assertNotIn(str(ROOT), output.read_text())
            self.assertNotIn(str(Path(d)), output.read_text())

    @unittest.skipUnless(sys.platform == "darwin", "requires actual Apple Foundation execution")
    def test_real_foundation_listing_contracts(self):
        with tempfile.TemporaryDirectory() as d:
            output = Path(d) / "report.json"
            result = subprocess.run([sys.executable, str(ROOT / "scripts/detection_apple_contracts.py"),
                                     "--output", str(output)], capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(output.read_text())
            self.assertEqual(report["status"], "passed")
            self.assertTrue(report["executed"])
            self.assertEqual(report["passed_modes"], ["filter", "observe"])
            self.assertFalse(report["device_verified"])
            self.assertFalse(report["positive_artifact_verified"])
