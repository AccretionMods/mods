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
        (dump_dir / "dump.cs").write_text(DUMP + """
// Namespace:
public class PurchaseState : MonoBehaviour // TypeDefIndex: 110
{
    public static PurchaseState instance; // 0x0
    public bool FullGameUnlocked; // 0x20
    public int Gems; // 0x24
    // RVA: 0x2000 Offset: 0x2000 VA: 0x2000
    public bool IsPurchased() { }
}
// Namespace:
public class OnlineState : MonoBehaviour // TypeDefIndex: 111
{
    public int Score; // 0x20
}
""")
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
        status, studio = self.get("/api/studio")
        self.assertEqual(status, 200)
        self.assertEqual(studio["protocol"], 1)
        self.assertIn("loaded", studio)
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

    def test_scan_includes_all_topics_and_can_add_a_labelled_pin(self):
        status, data = self.get("/api/mods/game-plus/scan")
        self.assertEqual(status, 200, data)
        rows = {f"{r['class']}.{r['member']}": r for r in data["rows"]}
        for target in ("PurchaseState.FullGameUnlocked", "PurchaseState.Gems", "OnlineState.Score"):
            self.assertIn(target, rows)
            self.assertIn("score", rows[target])
            self.assertIn("value", rows[target])
        status, filtered = self.get("/api/mods/game-plus/scan?flagged=1")
        self.assertEqual(status, 200, filtered)
        self.assertTrue(filtered["rows"])
        self.assertTrue(all(r.get("flag") for r in filtered["rows"]))
        status, purchases = self.get("/api/mods/game-plus/scan?area=In-app%20purchases")
        self.assertEqual(status, 200, purchases)
        self.assertEqual({r["class"] for r in purchases["rows"]}, {"PurchaseState"})
        self.assertIn("FullGameUnlocked", {r["member"] for r in purchases["rows"]})
        status, text = self.get("/api/mods/game-plus/scan?text=1&find=FullGameUnlocked")
        self.assertEqual(status, 200, text)
        self.assertIn("FullGameUnlocked", text["text"])
        status, added = self.post("/api/mods/game-plus/pin", {
            "target": "PurchaseState.FullGameUnlocked", "value": "true",
            "key": "fullGame", "title": "Full game",
        })
        try:
            self.assertEqual(status, 200, added)
            self.assertTrue(added["ok"])
        finally:
            self.post("/api/mods/game-plus/remove", {"what": "fullGame"})

    def test_scan_purchase_method_can_be_added_as_a_hook(self):
        status, data = self.get("/api/mods/game-plus/scan?area=In-app%20purchases")
        self.assertEqual(status, 200, data)
        row = next(r for r in data["rows"] if r["member"] == "IsPurchased")
        m = row["method"]
        self.assertEqual((m["kind"], m["args"]), ("bool", 0))
        status, added = self.post("/api/mods/game-plus/add", {
            "method": m["full_name"], "args": m["args"], "action": "return",
            "value": "true", "key": "purchaseTest", "title": "Purchase test",
        })
        try:
            self.assertEqual(status, 200, added)
            self.assertTrue(added["ok"])
        finally:
            self.post("/api/mods/game-plus/remove", {"what": "purchaseTest"})

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

    def test_snapshot_preview_and_restore_through_api(self):
        status, saved = self.post("/api/mods/game-plus/snapshot", {"action": "create"})
        self.assertEqual(status, 200, saved)
        source = dev.mod_dir("game-plus") / "Tweak.x"
        before = source.read_bytes() if source.exists() else None
        source.write_text("temporary local edit", encoding="utf-8")
        status, preview = self.post("/api/mods/game-plus/snapshot", {"action": "preview", "id": saved["id"]})
        self.assertEqual(status, 200, preview)
        self.assertTrue(any(f["changed"] for f in preview["files"]))
        status, restored = self.post("/api/mods/game-plus/snapshot", {"action": "restore", "id": saved["id"], "expected": preview["expected"]})
        self.assertEqual(status, 200, restored)
        self.assertIn("recovery_id", restored)
        self.assertEqual(source.read_bytes() if source.exists() else None, before)
    def test_restart_waits_for_configuration_actions(self):
        with server.action_scope():
            status, state = self.get('/api/studio')
            self.assertEqual(status, 200)
            self.assertTrue(state['busy'])
            status, response = self.post('/api/studio/restart', {})
            self.assertEqual(status, 400, response)
        self.assertFalse(server.RESTARTING.is_set())
        server.RESTARTING.set()
        try:
            with self.assertRaises(dev.DevError):
                with server.action_scope():
                    pass
            with self.assertRaises(dev.DevError):
                server.start_job('check', ['game-plus'])
        finally:
            server.RESTARTING.clear()

    def test_jobs(self):
        status, job = self.post("/api/jobs", {"command": "check", "mod": "game-plus"})
        self.assertEqual(status, 200, job)
        for _ in range(100):
            status, job = self.get(f"/api/jobs/{job['id']}")
            if job["status"] not in ("queued", "running"):
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
        # Claude Code runs: read-only gets reading tools and read-only dev commands only
        old_exe = server.claude_exe
        server.claude_exe = lambda: "claude"
        try:
            ro = server.claude_command("Goal: explore", True)
            rw = server.claude_command("Goal: fix", False)
        finally:
            server.claude_exe = old_exe
        self.assertEqual(ro[:3], ["claude", "-p", "Goal: explore"])
        allowed_ro = ro[ro.index("--allowedTools") + 1 : ro.index("--permission-mode")]
        self.assertNotIn("Edit", allowed_ro)
        self.assertNotIn(f"Bash({server.DEV}:*)", allowed_ro)
        self.assertIn(f"Bash({server.DEV} find:*)", allowed_ro)
        self.assertIn("--disallowedTools", ro)
        self.assertIn("acceptEdits", rw)
        self.assertIn(f"Bash({server.DEV}:*)", rw)
        self.assertFalse(any(a in ("Bash", "Bash(*)") for a in rw))  # never any shell command
        lines = server.claude_lines(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Found it.\nTwo ways."},
            {"type": "tool_use", "name": "Bash", "input": {"command": "uv run ... find x Hp"}}]}}))
        self.assertEqual(lines[:2], ["Found it.", "Two ways."])
        self.assertTrue(lines[2].startswith("  » Bash uv run"))
        done = server.claude_lines(json.dumps({"type": "result", "subtype": "success", "num_turns": 3,
                                               "duration_ms": 4000, "total_cost_usd": 0.12}))
        self.assertIn("3 turns, 4 s, $0.12", done[-1])
        self.assertEqual(server.claude_lines("not json"), ["not json"])
        status, data = self.post("/api/claude", {"prompt": "hi", "read_only": True})
        self.assertEqual(status, 400)
        # prompt builder: every {fact} a task uses is one the context gives
        status, ctx = self.get("/api/mods/game-plus/context")
        self.assertEqual(status, 200, ctx)
        self.assertTrue(ctx["dump"])
        self.assertIn(ctx["log_seen"], (True, False))
        status, conf = self.get("/api/prompts")
        self.assertEqual(status, 200)
        known = set(ctx) | {"ask", "extra", "where", "limits"}
        import re as _re

        for t in conf["tasks"]:
            self.assertTrue(t["lines"] and t["label"] and t["ask"], t["id"])
            used = set(_re.findall(r"\{(\w+)\}", "\n".join(t["lines"]) + conf["where"] + conf["limits"]))
            self.assertLessEqual(used, known, t["id"])
            self.assertLessEqual(set(t.get("needs", [])), {"ask", "dump", "log"}, t["id"])
        # console: any dev.py command, regexes kept as typed, answers only for console jobs
        status, data = self.get("/api/console/commands")
        self.assertIn("find", data["commands"])
        self.assertIn("dump", data["commands"])
        self.assertEqual(
            server.split_line(r'am find game-plus "Damage|Hp" \d+Hp\b'),
            ["find", "game-plus", "Damage|Hp", r"\d+Hp\b"],
        )
        status, job = self.post("/api/console", {"line": 'am find game-plus "Damage|Player"'})
        self.assertEqual(status, 200, job)
        self.assertTrue(job["interactive"])
        for _ in range(100):
            status, job = self.get(f"/api/jobs/{job['id']}")
            if job["status"] not in ("queued", "running"):
                break
            import time

            time.sleep(0.1)
        self.assertEqual(job["status"], "done", job)
        self.assertTrue(any("DamagePlayer" in line for line in job["lines"]), job)
        status, data = self.post("/api/console", {"line": "rm -rf /"})
        self.assertEqual(status, 400)
        status, data = self.post("/api/console", {"line": 'find "unclosed'})
        self.assertEqual(status, 400)
        status, check = self.post("/api/jobs", {"command": "check", "mod": "game-plus"})
        status, data = self.post(f"/api/jobs/{check['id']}/input", {"text": "1"})
        self.assertEqual(status, 400)  # only console jobs take answers
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
