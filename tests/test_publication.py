import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BASH = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
if not BASH.exists():
    BASH = shutil.which("bash")


class StageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo with spaces"
        (self.repo / "scripts").mkdir(parents=True)
        (self.repo / "mods/demo").mkdir(parents=True)
        (self.repo / "mods/demo/mod.json").write_text("{}")
        shutil.copyfile(ROOT / "scripts/stage.sh", self.repo / "scripts/stage.sh")
        self.calls = self.base / "calls.txt"
        self.ipa = self.base / "test app.ipa"
        self.ipa.write_bytes(b"fictional")

    def run_stage(self, *args, dispatch_fails=False):
        env = {
            **os.environ,
            "TEST_CALLS": self.calls.as_posix(),
            "TEST_DISPATCH_FAILS": "1" if dispatch_fails else "",
        }
        command = 'gh() { printf "%s\\n" "$*" >> "$TEST_CALLS"; if [ "$1" = workflow ] && [ -n "$TEST_DISPATCH_FAILS" ]; then return 1; fi; }; python3() { printf "%s\\n" "$*" >> "$TEST_CALLS"; }; export -f gh python3; exec /bin/bash "$@"'
        return subprocess.run(
            [
                str(BASH),
                "-c",
                command,
                "stage-test",
                (self.repo / "scripts/stage.sh").as_posix(),
                *args,
            ],
            cwd=self.base,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_relative_ipa_is_resolved_before_entering_repository(self):
        result = self.run_stage("demo", self.ipa.name, "17.0")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn(self.ipa.name, self.calls.read_text())
        self.assertIn(self.base.name, self.calls.read_text())

    def test_absolute_ipa_and_default_os(self):
        result = self.run_stage("demo", self.ipa.as_posix())
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertNotIn("min_os=", self.calls.read_text())

    def test_invalid_arguments_never_create_draft(self):
        for tail in (("17.0", "extra"), ("17.bad",), ("-1",)):
            with self.subTest(tail=tail):
                self.calls.unlink(missing_ok=True)
                result = self.run_stage("demo", self.ipa.as_posix(), *tail)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(
                    self.calls.exists(), "Invalid inputs reached external commands"
                )

    def test_dispatch_failure_retains_private_draft_for_retry(self):
        result = self.run_stage("demo", self.ipa.as_posix(), dispatch_fails=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("retained", result.stdout)
        calls = self.calls.read_text()
        self.assertIn("--draft", calls)
        self.assertNotIn("DELETE", calls)


class PublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        file = ROOT / "scripts/publication_status.py"
        cls.module = None
        if file.exists():
            spec = importlib.util.spec_from_file_location("publication_status", file)
            cls.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.module)

    def module_ready(self):
        self.assertIsNotNone(
            self.module, "Publication outcomes need a tested success gate"
        )
        return self.module

    def test_failed_publication_or_commit_retains_draft(self):
        mod = self.module_ready()
        for upload, source in (
            ("failure", "skipped"),
            ("success", "failure"),
            ("cancelled", "skipped"),
        ):
            with patch.object(mod.subprocess, "run") as run:
                self.assertFalse(mod.cleanup_draft("Org/repo", "123", upload, source))
                run.assert_not_called()

    def test_complete_publication_deletes_only_its_draft(self):
        mod = self.module_ready()
        with patch.object(mod.subprocess, "run") as run:
            self.assertTrue(mod.cleanup_draft("Org/repo", "123", "success", "success"))
            self.assertEqual(
                run.call_args.args[0],
                ["gh", "api", "-X", "DELETE", "repos/Org/repo/releases/123"],
            )

    def test_summary_uses_results_not_requested_publish_flag(self):
        mod = self.module_ready()
        self.assertIn(
            "Published privately", mod.summary("success", "success", "success", True)
        )
        self.assertIn(
            "Built and checked only",
            mod.summary("success", "skipped", "skipped", False),
        )
        self.assertNotIn(
            "Published privately", mod.summary("success", "failure", "skipped", True)
        )
        self.assertIn(
            "source update failed", mod.summary("success", "success", "failure", True)
        )
        self.assertIn(
            "not completed", mod.summary("failure", "skipped", "skipped", False)
        )


if __name__ == "__main__":
    unittest.main()
