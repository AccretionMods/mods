"""studio/server.py: the JSON API over dev.py, on a synthetic mod and dump (no game, no phone, no Theos)."""

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "studio"))
sys.path.insert(0, os.path.dirname(__file__))

import dev
import server
from test_dev_cli import DUMP


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        mod = root / "repo" / "mods" / "game-plus"
        mod.mkdir(parents=True)
        (mod / "Makefile").write_text(
            "TWEAK_NAME = GamePlus\nGamePlus_FILES = Tweak.x AMTweaks.gen.m runtime/am_il2cpp.c runtime/AMIl2cpp.m runtime/am_hooks_data.c\n"
        )
        (mod / "mod.json").write_text(
            json.dumps(
                {
                    "name": "Game+",
                    "display_name": "Game+",
                    "version": "1.0.0",
                    "kind": "tweak",
                    "personal": True,
                    "target_bundle_id": "com.example.game",
                    "bundle_id": "io.github.accretionmods.gameplus",
                    "tested_app_version": "1.0",
                    "base": {"private": True, "sha256": "0" * 64},
                }
            )
        )
        dump_dir = root / "am" / "work" / "dump-game-plus-1.0" / "out"
        dump_dir.mkdir(parents=True)
        (dump_dir / "dump.cs").write_text(DUMP)
        (root / "am" / "dev").mkdir(parents=True)
        (root / "am" / "dev" / "game-plus.json").write_text(
            json.dumps({"dump_dir": str(dump_dir.parent)})
        )
        cls.old = (dev.ROOT, server.ROOT, os.environ.get("AM_ROOT"))
        dev.ROOT = server.ROOT = root / "repo"
        os.environ["AM_ROOT"] = str(root / "am")
        dev._dump_cache.clear()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        dev.ROOT, server.ROOT, env = cls.old
        if env is None:
            os.environ.pop("AM_ROOT", None)
        else:
            os.environ["AM_ROOT"] = env
        cls.tmp.cleanup()

    def get(self, path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def post(self, path, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_mods_and_detail(self):
        status, data = self.get("/api/mods")
        self.assertEqual(status, 200)
        self.assertEqual([m["name"] for m in data["mods"]], ["game-plus"])
        self.assertTrue(data["mods"][0]["dump"])
        status, detail = self.get("/api/mods/game-plus")
        self.assertEqual(detail["target"], "com.example.game")
        self.assertIn("hooks_json", detail)

    def test_find_fields_suggest(self):
        status, data = self.get("/api/mods/game-plus/find?q=DamagePlayer&limit=5")
        self.assertEqual(data["count"], 2)
        self.assertTrue(data["methods"][0]["needs_jit"])
        status, data = self.get("/api/mods/game-plus/find?class=Enemy")
        self.assertEqual(data["count"], 3)
        status, data = self.get("/api/mods/game-plus/fields?class=Enemy")
        self.assertEqual(
            data["fields"][0],
            {"offset": 0x60, "static": False, "type": "long", "name": "EnemyDamage"},
        )
        status, data = self.get("/api/mods/game-plus/suggest")
        self.assertEqual(status, 200)
        self.assertIn("rows", data)

    def test_quick_commands_and_errors(self):
        status, data = self.post(
            "/api/mods/game-plus/add",
            {
                "method": "Enemy.DamagePlayer",
                "args": 2,
                "key": "noDamage",
                "title": "No damage",
            },
        )
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"])
        self.assertEqual(data["mod"]["tweaks_json"]["tweaks"][0]["key"], "noDamage")
        status, data = self.post(
            "/api/mods/game-plus/pin",
            {
                "target": "StatHandler.set_Hunger",
                "value": "100",
                "when": "lower",
                "to": "noDamage",
            },
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(
            data["mod"]["tweaks_json"]["tweaks"][0]["pins"][0]["setter"], "set_Hunger"
        )
        status, data = self.post(
            "/api/mods/game-plus/mark", {"key": "noDamage", "works": True}
        )
        self.assertEqual(status, 200, data)
        status, data = self.post("/api/mods/game-plus/add", {"method": "Nope.X"})
        self.assertEqual(status, 400)
        self.assertIn("not in the dump", data["output"])
        status, data = self.post("/api/mods/game-plus/remove", {"what": "noDamage"})
        self.assertEqual(status, 200)
        self.assertEqual(data["mod"]["tweaks_json"]["tweaks"], [])
        status, data = self.get("/api/mods/nope")
        self.assertEqual(status, 400)
        status, data = self.get("/api/nothing")
        self.assertEqual(status, 404)

    def test_jobs(self):
        status, job = self.post("/api/jobs", {"command": "check", "mod": "game-plus"})
        self.assertEqual(status, 200, job)
        for _ in range(100):
            status, job = self.get(f"/api/jobs/{job['id']}")
            if job["status"] != "running":
                break
            import time

            time.sleep(0.1)
        self.assertIn(job["status"], ("done", "failed"))
        self.assertTrue(any("hooks" in line for line in job["lines"]), job)
        status, _data = self.post("/api/jobs", {"command": "rm", "mod": "game-plus"})
        self.assertEqual(status, 400)
        # sign and install take a file: the mod name must not be put in front of it
        status, job = self.post(
            "/api/jobs",
            {
                "command": "sign",
                "mod": "game-plus",
                "args": ["C:/no/such.ipa", "--install"],
            },
        )
        self.assertEqual(status, 200, job)
        self.assertEqual(job["args"], ["C:/no/such.ipa", "--install"])
        self.post(f"/api/jobs/{job['id']}/stop", {})
        status, data = self.get("/api/games?list=top-free&region=../x")
        self.assertEqual(status, 400)
        self.assertIn("region", data["error"])
        # fetch takes an app, not a mod
        os.environ["AM_DECRYPT_URL"] = "http://127.0.0.1:9"  # nothing listens: no network in tests
        try:
            status, job = self.post(
                "/api/jobs",
                {"command": "fetch", "mod": "game-plus", "args": ["com.example.game"]},
            )
            self.assertEqual(status, 200, job)
            self.assertEqual(job["args"], ["com.example.game"])
            self.post(f"/api/jobs/{job['id']}/stop", {})
            status, data = self.get("/api/fetch/account")
            self.assertEqual(status, 400)
            self.assertIn("did not answer", data["error"])
        finally:
            os.environ.pop("AM_DECRYPT_URL", None)


if __name__ == "__main__":
    unittest.main()
