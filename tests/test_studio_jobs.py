"""Local job history and scheduling behavior, using synthetic output only."""

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "studio"))
from jobs_state import Coordinator, History, redact


class JobHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.history = History(self.root)

    def view(self, identifier="job1", status="done"):
        return {
            "id": identifier,
            "command": "build",
            "args": ["sample"],
            "status": status,
            "code": 0,
            "started": 123.0,
            "interactive": True,
            "lines": ["completed"],
            "next": 1,
        }

    def test_history_keeps_the_newest_records(self):
        self.history.MAX_RECORDS = 3
        for n in range(5):
            self.history.save(self.view(f"job{n}"))
            time.sleep(0.02)
        self.assertEqual(sorted(r["id"] for r in self.history.load()), ["job2", "job3", "job4"])

    def test_recovered_jobs_never_resume_and_completed_output_retained(self):
        for i, status in enumerate(
            ("queued", "running", "stopping", "done", "failed", "stopped")
        ):
            self.history.save(self.view(f"job{i}", status))
        rows = {v["id"]: v for v in History(self.root).load()}
        self.assertEqual(
            [rows[f"job{i}"]["status"] for i in range(6)],
            ["interrupted"] * 3 + ["done", "failed", "stopped"],
        )
        self.assertTrue(all(not row["interactive"] for row in rows.values()))
        self.assertEqual(rows["job3"]["lines"], ["completed"])

    def test_corrupt_files_are_ignored_and_job_ids_validated(self):
        self.history.save(self.view())
        folder = self.root / "jobs"
        (folder / "broken.json").write_text("{broken", encoding="utf-8")
        (folder / "other.json").write_text(
            json.dumps({"id": "../escape"}), encoding="utf-8"
        )
        self.assertEqual(len(self.history.load()), 1)
        for identifier in ("../bad", "x/y", "..", "CON", "", "a" * 100):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.history.save(self.view(identifier))

    def test_secrets_input_and_claude_prompts_are_not_persisted(self):
        row = self.view()
        jwt = "eyJ" + "a" * 60 + "." + "b" * 80 + "." + "c" * 40
        row.update(
            args=[
                "sample",
                "--password",
                "supersecret",
                "--api-key=apiSecret",
                "Authorization: Bearer authsecret",
            ],
            cmd=["claude", "-p", "privatePrompt"],
            prompt="privatePrompt",
            input="typedCode",
            lines=[
                "ordinary output",
                "> typedCode",
                " token=tokenSecret password: passSecret",
                'JSON {"api_key": "jsonSecret"}',
                "Authorization: Bearer authsecret",
                "Cookie: session=cookieSecret; other=value",
                jwt,
                "claude -p privatePrompt",
            ],
        )
        self.history.save(row)
        saved = (self.root / "jobs" / "job1.json").read_text()
        for secret in (
            "supersecret",
            "apiSecret",
            "authsecret",
            "privatePrompt",
            "typedCode",
            "tokenSecret",
            "passSecret",
            "jsonSecret",
            "cookieSecret",
            jwt,
        ):
            self.assertNotIn(secret, saved)
        self.assertIn("ordinary output", saved)
        row.update(id="claude1", command="claude", args=["-p", "arbitrary prompt"])
        self.history.save(row)
        self.assertEqual(
            next(r for r in self.history.load() if r["id"] == "claude1")["args"], []
        )

    def test_console_help_and_public_redactor(self):
        row = self.view()
        row["command"] = "--help"
        self.history.save(row)
        self.assertEqual(self.history.load()[0]["command"], "--help")
        self.assertNotIn("testSecret", redact("password=testSecret"))

    def test_log_storage_is_bounded(self):
        row = self.view()
        row["lines"] = ["x" * 20000] * 5000
        self.history.save(row)
        self.assertLess((self.root / "jobs" / "job1.json").stat().st_size, 2_000_000)
        self.assertTrue(self.history.load()[0]["lines"])


class Scheduling(unittest.TestCase):
    def wait_queued(self, coordinator, count):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with coordinator._condition:
                if len(coordinator._pending) == count:
                    return
            time.sleep(0.005)
        self.fail("workers did not queue")

    def test_intersecting_resources_are_fifo_but_disjoint_work_passes(self):
        coordinator = Coordinator()
        cancelled = threading.Event()
        self.assertTrue(coordinator.acquire(["mod:sample"], cancelled))
        order = []
        threads = []

        def worker(number):
            if coordinator.acquire(["mod:sample", "device"], cancelled):
                order.append(number)
                coordinator.release(["mod:sample", "device"])

        for i in range(3):
            thread = threading.Thread(target=worker, args=(i,), daemon=True)
            thread.start()
            threads.append(thread)
            self.wait_queued(coordinator, i + 1)
        self.assertTrue(coordinator.acquire(["mod:other"], cancelled))
        self.assertEqual(order, [])
        coordinator.release(["mod:other"])
        coordinator.release(["mod:sample"])
        for thread in threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(order, [0, 1, 2])

    def test_queued_cancellation_returns_false_without_release(self):
        coordinator = Coordinator()
        self.assertTrue(coordinator.acquire(["device"], threading.Event()))
        cancelled = threading.Event()
        results = []
        thread = threading.Thread(
            target=lambda: results.append(coordinator.acquire(["device"], cancelled)),
            daemon=True,
        )
        thread.start()
        self.wait_queued(coordinator, 1)
        cancelled.set()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, [False])
        coordinator.release(["device"])
        self.assertTrue(coordinator.acquire(["device"], threading.Event()))
        coordinator.release(["device"])


class Resources(unittest.TestCase):
    def test_log_and_test_hold_only_the_phone(self):
        import server  # studio/server.py (on sys.path above)

        self.assertEqual(server.job_resources("log", ["sample-mod", "--seconds", "60"]), ["device"])
        self.assertEqual(server.job_resources("test", ["sample-mod"]), ["device"])
        self.assertIn("mod:sample-mod", server.job_resources("build", ["sample-mod"]))
        self.assertEqual(server.job_resources("run", ["sample-mod"]), ["device", "mod:sample-mod"])


if __name__ == "__main__":
    unittest.main()
