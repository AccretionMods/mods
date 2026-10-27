"""Exercise bounded selection and publication against the production registry."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CC = shutil.which("cc") or shutil.which("clang")


@unittest.skipUnless(CC and os.name != "nt", "requires POSIX C compiler")
class DetectionRegistry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert (ROOT / "runtime/AMDetectionRegistry.h").exists(), "capability registry missing"
        cls.temp = tempfile.TemporaryDirectory()
        cls.exe = str(Path(cls.temp.name) / "registry")
        result = subprocess.run(
            [CC, "-std=c11", "-Wall", "-Wextra", "-Werror", "-pthread",
             "-I", str(ROOT / "runtime"), str(ROOT / "tests/detection_registry_harness.c"),
             str(ROOT / "runtime/AMDetectionRegistry.c"),
             str(ROOT / "runtime/AMDetectionPolicy.c"), str(ROOT / "runtime/AMDetectionHooks.c"),
             "-o", cls.exe],
            capture_output=True, text=True,
        )
        if result.returncode:
            raise AssertionError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_stable_names_ids_and_legacy_aggregate_selection(self):
        subprocess.run([self.exe, "identity"], check=True)

    def test_selection_rejects_duplicates_unknowns_and_binding_overflow(self):
        subprocess.run([self.exe, "limits"], check=True)

    def test_partial_publish_and_prepare_failure_never_activate_group(self):
        subprocess.run([self.exe, "transaction"], check=True)

    def test_foreign_owner_failure_leaves_module_inactive(self):
        subprocess.run([self.exe, "conflict"], check=True)

    def test_global_off_runs_no_installers(self):
        subprocess.run([self.exe, "off"], check=True)

    def test_invalid_configuration_has_no_installation_side_effects(self):
        subprocess.run([self.exe, "invalid"], check=True)

    def test_installed_file_wrapper_forwards_until_module_published(self):
        subprocess.run([self.exe, "gate"], check=True)

    def test_module_observe_reduces_filter_and_cannot_escalate_observing_parent(self):
        subprocess.run([self.exe, "reduced"], check=True)
        subprocess.run([self.exe, "escalation"], check=True)

    def test_unimplemented_contract_cannot_activate_via_registered_callbacks(self):
        subprocess.run([self.exe, "unimplemented"], check=True)

    def test_unknown_process_query_contract_never_installs(self):
        subprocess.run([self.exe, "process_query"], check=True)
