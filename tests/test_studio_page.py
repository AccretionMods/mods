"""studio/app.js against what it calls: every job the page starts is one the server runs and dev.py has, with
flags dev.py accepts. A renamed command or flag otherwise breaks only when someone clicks the button."""

import os
import re
import shutil
import subprocess
import sys
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "studio"))

import dev
import server

STUDIO = Path(__file__).resolve().parents[1] / "studio"
APP = (STUDIO / "app.js").read_text(encoding="utf-8")


def dev_commands():
    """dev.py's subcommands and each one's option strings."""
    parser = dev.build_parser()
    sub = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    return {name: {s for action in p._actions for s in action.option_strings} for name, p in sub.choices.items()}


def page_jobs():
    """(command, flags written on the same line) for each job the page starts; runOnPhone starts "run"."""
    found = []
    for line in APP.splitlines():
        for command in re.findall(r'\bjob\("([a-z][a-z-]*)"', line):
            found.append((command, set(re.findall(r'"(--[a-z][a-z-]*)"', line))))
        if "this.runOnPhone(" in line:
            found.append(("run", set(re.findall(r'"(--[a-z][a-z-]*)"', line))))
    return found


class PageContract(unittest.TestCase):
    def test_every_job_is_one_the_server_runs_and_dev_py_has(self):
        commands = dev_commands()
        jobs = page_jobs()
        self.assertGreater(len(jobs), 10)  # the parser still finds the page's jobs
        for command, flags in jobs:
            self.assertIn(command, server.JOB_COMMANDS, f"the page starts {command}, which the server doesn't run")
            self.assertIn(command, commands, f"the page starts {command}, which dev.py doesn't have")
            for flag in flags:
                self.assertIn(flag, commands[command], f"the page passes {flag} to dev.py {command}")

    def test_every_flag_the_page_writes_exists_in_dev_py(self):
        known = set().union(*dev_commands().values())
        for flag in set(re.findall(r'"(--[a-z][a-z-]*)"', APP)):
            self.assertIn(flag, known, f"app.js writes {flag}, which no dev.py command takes")

    @unittest.skipUnless(shutil.which("node"), "node isn't installed")
    def test_app_js_parses(self):
        r = subprocess.run(["node", "--check", str(STUDIO / "app.js")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class PageFiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def fetch(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as r:
            return r.headers.get("Content-Type"), r.read().decode("utf-8")

    def test_page_loads_its_script_and_styles(self):
        ctype, page = self.fetch("/")
        self.assertTrue(ctype.startswith("text/html"))
        self.assertIn('<script src="/app.js"></script>', page)
        self.assertIn('<link rel="stylesheet" href="/app.css">', page)
        ctype, js = self.fetch("/app.js")
        self.assertTrue(ctype.startswith("text/javascript"))
        self.assertEqual(js, APP)
        ctype, css = self.fetch("/app.css")
        self.assertTrue(ctype.startswith("text/css"))
        self.assertTrue(css.strip())


if __name__ == "__main__":
    unittest.main()
