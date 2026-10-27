"""Compile the production policy/wrappers and exercise observable libc contracts."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CC = shutil.which("cc") or shutil.which("clang")


@unittest.skipUnless(CC and os.name != "nt", "requires POSIX C compiler")
class DetectionPolicy(unittest.TestCase):
    def test_diagnostics_disabled_still_filters_and_forwards_without_counters(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "disabled.c"
            source.write_text(
                '#include "AMDetectionHooks.h"\n#include <assert.h>\n#include <errno.h>\n'
                "static int called; static int original(const char *p,struct stat *s)"
                "{(void)p;(void)s;called++;errno=E2BIG;return 0;}\n"
                'int main(void){const char *paths[]={"/private/check"};'
                "AMDetectionProfile p={.mode=AM_DETECTION_FILTER,.rules={.paths=paths,.path_count=1}};"
                "AMDetectionOriginals o={.stat_fn=original};struct stat s;"
                "assert(AMDetectionConfigure(&p,&o));assert(AMDetectionStat(paths[0],&s)==-1);"
                'assert(errno==ENOENT && called==0);assert(AMDetectionStat("/ordinary",&s)==0);'
                "assert(errno==E2BIG && called==1);AMDetectionRecord(AM_DETECTION_STAT,true,true);"
                "AMDetectionCounters c=AMDetectionReadCounters(AM_DETECTION_STAT);"
                "assert(c.calls==0 && c.matched==0 && c.filtered==0);"
                "AMDetectionCallsite sites[1];assert(AMDetectionReadCallsites(sites,1)==0);return 0;}\n"
            )
            exe = str(Path(d) / "disabled")
            subprocess.run(
                [
                    CC,
                    "-std=c11",
                    "-D_POSIX_C_SOURCE=200809L",
                    "-DAM_DETECTION_DIAGNOSTICS_ENABLED=0",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(ROOT / "runtime"),
                    str(source),
                    str(ROOT / "runtime/AMDetectionPolicy.c"),
                    str(ROOT / "runtime/AMDetectionHooks.c"),
                    "-o",
                    exe,
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run([exe], check=True)

    @classmethod
    def setUpClass(cls):
        assert (ROOT / "runtime/AMDetectionPolicy.c").is_file(), (
            "production detection policy is missing"
        )
        cls.tmp = tempfile.TemporaryDirectory()
        cls.exe = str(Path(cls.tmp.name) / "policy")
        subprocess.run(
            [
                CC,
                "-std=c11",
                "-D_POSIX_C_SOURCE=200809L",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-I",
                str(ROOT / "runtime"),
                str(ROOT / "tests/detection_policy_harness.c"),
                str(ROOT / "runtime/AMDetectionPolicy.c"),
                str(ROOT / "runtime/AMDetectionHooks.c"),
                "-o",
                cls.exe,
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_path_boundaries_and_ambiguous_inputs(self):
        subprocess.run([self.exe, "paths"], check=True)

    def test_exact_schemes_and_environment_names(self):
        subprocess.run([self.exe, "names"], check=True)

    def test_filter_contracts_and_ordinary_file_operations(self):
        subprocess.run([self.exe, "filter"], check=True)

    def test_observe_preserves_results_and_errno(self):
        subprocess.run([self.exe, "observe"], check=True)

    def test_off_preserves_results_and_errno(self):
        subprocess.run([self.exe, "off"], check=True)

    def test_matched_callers_original_results_and_capacity(self):
        subprocess.run([self.exe, "outcomes"], check=True)
