"""Contract tests for full diagnostic identities, bounds and quiet builds."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CC = shutil.which("cc") or shutil.which("clang")


@unittest.skipUnless(CC and os.name != "nt", "requires POSIX C compiler")
class DetectionDiagnostics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert (ROOT / "runtime/AMDetectionDiagnostics.h").exists(), "structured diagnostics missing"
        cls.temp = tempfile.TemporaryDirectory()
        cls.exe = str(Path(cls.temp.name) / "diagnostics")
        cls.compile([str(ROOT / "tests/detection_diagnostics_harness.c")], cls.exe)

    @classmethod
    def compile(cls, sources, output, *flags):
        result = subprocess.run(
            [CC, "-std=c11", "-Wall", "-Wextra", "-Werror", "-pthread",
             "-I", str(ROOT / "runtime"), *flags, *sources, "-o", output],
            capture_output=True, text=True,
        )
        if result.returncode:
            raise AssertionError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_wide_entry_ids_and_unknown_image_do_not_alias_on_hash_collision(self):
        subprocess.run([self.exe, "identity"], check=True)

    def test_table_capacity_drops_are_separate_and_never_negative_evidence(self):
        subprocess.run([self.exe, "capacity"], check=True)

    def test_saturating_counters_and_allocation_failure_mark_incomplete(self):
        subprocess.run([self.exe, "saturation"], check=True)

    def test_concurrent_writers_keep_complete_identity_and_account_for_drops(self):
        subprocess.run([self.exe, "threads"], check=True)

    def test_quiet_object_has_no_tables_and_readers_return_no_data(self):
        source = Path(self.temp.name) / "quiet.c"
        source.write_text(
            '#include "AMDetectionDiagnostics.h"\n#include <assert.h>\n'
            'int main(void){AMDetectionDiagnosticsConfigure(true);'
            'AMDetectionEventKey k={.entry=1,.rule={AM_DETECTION_RULE_PATH,0}};'
            'AMDetectionRecordEvent(k,1,true,AM_DETECTION_RESULT_SUCCESS);'
            'assert(AMDetectionReadEvents(0,4096)==0);'
            'assert(AMDetectionReadEntryCounters(1).calls==0);'
            'assert(!AMDetectionSetPhase(AM_DETECTION_PHASE_FIGHT));'
            'assert(!AMDetectionReadCoverage().incomplete);return 0;}\n'
        )
        exe = str(Path(self.temp.name) / "quiet")
        self.compile([str(source), str(ROOT / "runtime/AMDetectionDiagnostics.c")],
                     exe, "-DAM_DETECTION_DIAGNOSTICS_ENABLED=0")
        subprocess.run([exe], check=True)
        symbols = subprocess.run(["nm", "-a", exe], capture_output=True, text=True, check=True).stdout
        self.assertNotIn("am_diag_attribution", symbols)
        self.assertNotIn("am_diag_callsites", symbols)
