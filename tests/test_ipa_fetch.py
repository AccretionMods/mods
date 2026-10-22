"""scripts/ipa_fetch.py and `dev.py fetch` against a fake unfaird site (no network, no Apple)."""

import io
import json
import os
import plistlib
import struct
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import dev
import ipa_fetch

ipa_fetch.POLL_SECONDS = 0


def macho(cryptid):
    """A thin arm64 header with one LC_ENCRYPTION_INFO_64 command."""
    cmd = struct.pack("<IIIIII", 0x2C, 24, 0x4000, 0x1000, cryptid, 0)
    return struct.pack("<IiiIIIII", 0xFEEDFACF, 0x0100000C, 0, 2, 1, len(cmd), 0, 0) + cmd


def ipa_bytes(bundle, cryptid=0, version="2.0"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        info = {"CFBundleIdentifier": bundle, "CFBundleExecutable": "Game", "CFBundleShortVersionString": version}
        z.writestr("Payload/Game.app/Info.plist", plistlib.dumps(info))
        z.writestr("Payload/Game.app/Game", macho(cryptid))
    return buf.getvalue()


class FakeSite(BaseHTTPRequestHandler):
    state = {}

    def log_message(self, *a):
        pass

    def reply(self, status, data, cookie=None):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if cookie:
            self.send_header("Set-Cookie", cookie + "; HttpOnly; Path=/")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def signed_in(self):
        return self.headers.get("Cookie") == "session=abc"

    def do_GET(self):
        st, url = self.state, urlparse(self.path)
        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
        st.setdefault("gets", []).append(url.path)
        if url.path == "/health":
            return self.reply(200, {"status": "ok", "paused": st.get("paused", False)})
        if url.path == "/api/v1/me":
            user = {"email": "me@example.com", "approved": True, "limit": 5, "remaining": 4} if self.signed_in() else None
            return self.reply(200, {"user": user, "open_mode": False})
        if url.path == "/api/v1/lookup":
            if st.get("captcha"):
                return self.reply(403, {"error": "Vui lòng xác minh bạn không phải robot (captcha) rồi thử lại."})
            app = st["apps"].get(q["id"])
            if not app:
                return self.reply(200, {"results": [], "error": "App not found for this bundle ID"})
            return self.reply(200, {"results": [dict(app, bundle_id=q["id"])]})
        if url.path == "/api/v1/search":
            rows = [dict(a, bundle_id=b) for b, a in st["apps"].items() if q["q"].lower() in a["name"].lower()]
            return self.reply(200, {"results": rows})
        if url.path.startswith("/api/v1/jobs/"):
            st["polls"] = st.get("polls", 0) + 1
            status = "completed" if st["polls"] > 1 else "decrypting"
            return self.reply(200, {"status": status, "detail": "step", "progress": None, "error": None})
        if url.path.startswith("/api/v1/download/"):
            bundle = url.path.rsplit("/", 1)[1]
            data = st["files"].get(bundle)
            if data is None:
                return self.reply(404, {"error": "File not found"})
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="Game: Deluxe_2.0_decrypted.ipa"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return None
        return self.reply(404, {"error": "not found"})

    def do_POST(self):
        st = self.state
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/api/v1/login":
            if body.get("password") != "right":
                return self.reply(401, {"error": "Sai email hoặc mật khẩu"})
            return self.reply(200, {"user": {"email": body["email"], "approved": True}}, cookie="session=abc")
        if self.path == "/api/v1/logout":
            return self.reply(200, {"ok": True})
        if self.path == "/api/v1/request":
            st["requests"] = st.get("requests", 0) + 1
            if st.get("paused"):
                return self.reply(503, {"error": "Đang tạm dừng"})
            if not self.signed_in():
                return self.reply(401, {"error": "need login", "need_login": True})
            st["files"][body["bundle_id"]] = ipa_bytes(body["bundle_id"])
            return self.reply(200, {"job_id": "j1", "status": "queued"})
        return self.reply(404, {"error": "not found"})


class Fetch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        FakeSite.state = {
            "apps": {
                "com.example.cached": {"name": "Cached Game", "version": "2.0", "is_cached": True, "cached_version": "2.0"},
                "com.example.new": {"name": "New Game", "version": "3.0", "is_cached": False, "cached_version": None},
                "com.example.old": {"name": "Old Game", "version": "3.0", "is_cached": False, "cached_version": "2.0"},
                "com.example.locked": {"name": "Locked Game", "version": "1.0", "is_cached": True, "cached_version": "1.0"},
            },
            "files": {
                "com.example.cached": ipa_bytes("com.example.cached"),
                "com.example.old": ipa_bytes("com.example.old"),
                "com.example.locked": ipa_bytes("com.example.locked", cryptid=1),
            },
        }
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeSite)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.site = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.old_env = {k: os.environ.get(k) for k in ("AM_DECRYPT_URL", "AM_ROOT")}
        os.environ["AM_DECRYPT_URL"] = self.site
        os.environ["AM_ROOT"] = str(self.root)
        self.lines = []

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        for k, v in self.old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def fetch(self, target, **kw):
        return ipa_fetch.fetch(self.root, target, say=self.lines.append, **kw)

    def test_cached_app_downloads_without_account_and_records_origin(self):
        out = self.fetch("com.example.cached")
        self.assertEqual(out.parent, self.root / "intake")
        self.assertEqual(out.name, "Game_Deluxe_2.0_decrypted.ipa")
        record = json.loads(out.with_name(out.name + ".source.json").read_text())
        self.assertEqual(record["site"], self.site)
        self.assertEqual(record["version"], "2.0")
        self.assertEqual(len(record["sha256"]), 64)
        self.assertNotIn("requests", FakeSite.state)
        info, _ = dev.ipa_info(out)  # dev.py's own reader sees it, so find_base picks it up
        self.assertEqual(info["CFBundleIdentifier"], "com.example.cached")

    def test_new_app_needs_sign_in_then_waits_for_the_job(self):
        with self.assertRaisesRegex(ipa_fetch.FetchError, "am fetch --login"):
            self.fetch("com.example.new")
        c = ipa_fetch.Client(self.root)
        with self.assertRaisesRegex(ipa_fetch.FetchError, "sign-in refused"):
            c.login("me@example.com", "wrong")
        c.login("me@example.com", "right")
        saved = json.loads((self.root / "decrypt-session.json").read_text())
        self.assertEqual(saved, {"site": self.site, "cookie": "session=abc"})  # no password
        self.assertTrue(ipa_fetch.Client(self.root).account()["signed_in"])
        out = self.fetch("com.example.new")
        self.assertTrue(out.is_file())
        self.assertGreaterEqual(FakeSite.state["polls"], 2)
        ipa_fetch.Client(self.root).logout()
        self.assertFalse((self.root / "decrypt-session.json").exists())

    def test_paused_site_falls_back_to_an_older_cached_copy(self):
        FakeSite.state["paused"] = True
        out = self.fetch("com.example.old")
        self.assertTrue(out.is_file())
        self.assertTrue(any("older copy 2.0" in line for line in self.lines), self.lines)
        with self.assertRaisesRegex(ipa_fetch.FetchError, "paused"):
            self.fetch("com.example.new")

    def test_encrypted_or_wrong_copies_are_refused_and_removed(self):
        with self.assertRaisesRegex(ipa_fetch.FetchError, "FairPlay"):
            self.fetch("com.example.locked")
        FakeSite.state["files"]["com.example.cached"] = ipa_bytes("com.other.app")
        with self.assertRaisesRegex(ipa_fetch.FetchError, "not com.example.cached"):
            self.fetch("com.example.cached")
        self.assertEqual(list((self.root / "intake").glob("*.ipa")), [])

    def test_names_are_searched_and_must_be_unique(self):
        self.assertTrue(self.fetch("cached game").is_file())
        with self.assertRaisesRegex(ipa_fetch.FetchError, "4 apps match"):
            self.fetch("game")

    def test_dev_cli_fetch_for_a_mod_and_account(self):
        repo = self.root / "repo"
        (repo / "mods" / "game-plus").mkdir(parents=True)
        (repo / "mods" / "game-plus" / "mod.json").write_text(json.dumps({"target_bundle_id": "com.example.cached"}))
        old = dev.ROOT
        dev.ROOT = repo
        try:
            self.assertEqual(dev.main(["fetch", "--mod", "game-plus"]), 0)
            self.assertEqual(dev.main(["fetch", "--account"]), 0)
            self.assertEqual(dev.main(["fetch", "com.example.missing"]), 1)
        finally:
            dev.ROOT = old
        self.assertEqual(len(list((self.root / "intake").glob("*.ipa"))), 1)

    def test_captcha_opens_the_browser_and_takes_the_download(self):
        FakeSite.state["captcha"] = True
        downloads = self.root / "Downloads"
        downloads.mkdir()
        opened = []

        def browser(url):  # the owner downloads on the site: another app first, then the one asked for
            opened.append(url)
            (downloads / "Other.ipa").write_bytes(ipa_bytes("com.other.app"))
            (downloads / "Game 2.0.ipa").write_bytes(ipa_bytes("com.example.new"))

        old = (ipa_fetch.webbrowser.open, ipa_fetch.download_folders, ipa_fetch.WATCH_SECONDS)
        ipa_fetch.webbrowser.open, ipa_fetch.download_folders, ipa_fetch.WATCH_SECONDS = (
            browser, lambda: [downloads], 0.05)
        try:
            with self.assertRaisesRegex(ipa_fetch.CaptchaRequired, "bundle ID"):
                self.fetch("new game")
            out = self.fetch("com.example.new")
        finally:
            ipa_fetch.webbrowser.open, ipa_fetch.download_folders, ipa_fetch.WATCH_SECONDS = old
        self.assertEqual(opened, [self.site + "/"])
        self.assertEqual(out, self.root / "intake" / "Game_2.0.ipa")
        self.assertFalse((downloads / "Game 2.0.ipa").exists())
        self.assertTrue((downloads / "Other.ipa").exists())  # not ours: left alone
        record = json.loads(out.with_name(out.name + ".source.json").read_text())
        self.assertEqual((record["via"], record["bundle_id"]), ("browser", "com.example.new"))
        with self.assertRaises(ipa_fetch.CaptchaRequired):
            self.fetch("com.example.new", browser=False)

    def test_import_by_hand_keeps_a_bad_file_where_it_is(self):
        bad = self.root / "locked.ipa"
        bad.write_bytes(ipa_bytes("com.example.locked", cryptid=1))
        self.assertEqual(dev.main(["fetch", "--import", str(bad)]), 1)
        self.assertTrue(bad.exists())
        good = self.root / "good.ipa"
        good.write_bytes(ipa_bytes("com.example.cached"))
        first = ipa_fetch.import_ipa(self.root, good, say=self.lines.append)
        again = self.root / "good.ipa"
        again.write_bytes(ipa_bytes("com.example.cached"))
        second = ipa_fetch.import_ipa(self.root, again, say=self.lines.append)
        self.assertEqual((first.name, second.name), ("good.ipa", "good~2.ipa"))

    def test_saved_login_signs_in_when_a_decrypt_needs_it(self):
        class FakeVault:
            def __init__(self, base):
                pass

            def load(self):
                return ("me@example.com", "right")

        old = ipa_fetch.Vault
        ipa_fetch.Vault = FakeVault
        try:
            out = self.fetch("com.example.new")
        finally:
            ipa_fetch.Vault = old
        self.assertTrue(out.is_file())
        self.assertEqual(FakeSite.state["requests"], 2)  # refused, signed in, accepted
        self.assertTrue((self.root / "decrypt-session.json").is_file())

    @unittest.skipUnless(os.name == "nt", "Windows Credential Manager")
    def test_vault_round_trip(self):
        v = ipa_fetch.Vault("https://unit-test.invalid")
        v.target += f"/{os.getpid()}"
        try:
            v.save("test@example.invalid", "not-a-real-password-ü")
            self.assertEqual(v.load(), ("test@example.invalid", "not-a-real-password-ü"))
        finally:
            v.forget()
        self.assertIsNone(v.load())

    def test_safe_names(self):
        self.assertEqual(ipa_fetch.safe_name("../../evil"), "evil.ipa")
        self.assertEqual(ipa_fetch.filename_from("attachment; filename*=UTF-8''A%20B.ipa"), "A B.ipa")


if __name__ == "__main__":
    unittest.main()
