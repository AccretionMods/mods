"""scripts/appstore.py: an .ipa's file list read from its end (no Apple account, no network)."""

import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import appstore


def ipa(names, pad=0):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in names:
            z.writestr(n, b"x" * pad)
    return buf.getvalue()


def reader(data):
    """read(start, end) over bytes, like two HTTP range requests, counting what it fetched."""
    fetched = []

    def read(start, end):
        fetched.append(end - start + 1)
        return data[start:end + 1], len(data)
    return read, fetched


UNITY = ["Payload/Game.app/Game", "Payload/Game.app/Frameworks/UnityFramework.framework/UnityFramework",
         "Payload/Game.app/Data/Managed/Metadata/global-metadata.dat", "Payload/Game.app/Data/hybridclr/HotFix.dll.bytes"]


class FileList(unittest.TestCase):
    def test_names_come_from_the_end_without_the_whole_file(self):
        data = ipa(UNITY + [f"Payload/Game.app/Data/asset{i}" for i in range(50)], pad=4000)
        read, fetched = reader(data)
        names, size = appstore.zip_names(read)
        self.assertEqual(size, len(data))
        self.assertEqual(names[:4], UNITY)
        self.assertLess(sum(fetched), len(data))  # only the tail was read (the 1-byte size probe aside)

    def test_central_directory_before_the_tail_is_fetched_separately(self):
        many = UNITY + [f"Payload/Game.app/Data/StreamingAssets/bundle_{i:05}.ab" for i in range(4000)]
        read, fetched = reader(ipa(many))
        names, _ = appstore.zip_names(read)
        self.assertEqual(len(names), len(many))
        self.assertEqual(len(fetched), 3)  # size probe, tail, central directory

    def test_engines(self):
        self.assertEqual(appstore.engine_of(UNITY), ("Unity IL2CPP", True))
        self.assertEqual(appstore.engine_of(["Payload/A.app/Frameworks/UnityFramework.framework/Info.plist"])[1], False)
        self.assertEqual(appstore.engine_of(["Payload/A.app/cookeddata/game.pak"])[0], "Unreal")
        self.assertEqual(appstore.engine_of(["Payload/A.app/Frameworks/Flutter.framework/Flutter"])[0], "Flutter")
        self.assertEqual(appstore.engine_of(["Payload/A.app/A"]), ("Native or other", False))
        # a sound file named "...statue4.fsb" is not Unreal (Minecraft); Unreal's .pak files live under Paks/
        self.assertEqual(appstore.engine_of(["Payload/A.app/data/sounds/become_statue4.fsb"])[0], "Native or other")
        self.assertEqual(appstore.engine_of(["Payload/A.app/Game/Content/Paks/pakchunk0.pak"])[0], "Unreal")
        # old Unity: the game in the main app, metadata but no UnityFramework (dumpable, no switches)
        self.assertEqual(appstore.engine_of(["Payload/A.app/A", "Payload/A.app/Data/Managed/Metadata/global-metadata.dat"]),
                         ("Unity IL2CPP, old (code in the main app)", False))

    def test_check_remembers_and_asks_before_a_license(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"AM_ROOT": tmp}):
            data = ipa(UNITY)
            with mock.patch.object(appstore, "download_url", side_effect=appstore.NeedsLicense("no")), \
                    mock.patch.object(appstore, "get_license") as lic:
                with self.assertRaises(appstore.NeedsLicense):
                    appstore.check(123456, "com.example.game")
                lic.assert_not_called()  # never without the owner's yes
            where = {"url": "https://example.invalid/a.ipa", "version": "1.2", "bundle": "com.example.game"}
            with mock.patch.object(appstore, "download_url", return_value=where), \
                    mock.patch.object(appstore, "_range", side_effect=lambda url, a, b: (data[a:b + 1], len(data))):
                v = appstore.check(123456, "com.example.game")
            self.assertTrue(v["compatible"] and v["hot_update"])
            self.assertEqual(appstore.known()["com.example.game"]["version"], "1.2")
            self.assertEqual(appstore.account(), {"signed_in": False})


def answers(*replies):
    """subprocess.run stand-in for the helper: each call answers the next reply, and keeps what it was sent."""
    sent = []

    def run(cmd, input=None, **kwargs):
        sent.append(json.loads(input))
        return mock.Mock(stdout=json.dumps(replies[len(sent) - 1]) + "\n", stderr="", returncode=0)
    return run, sent


class Helper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"AM_ROOT": self.tmp.name})
        self.env.start()
        exe = appstore.helper_path()
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
        self.protect = mock.patch.object(appstore, "_protect", side_effect=lambda data, unprotect=False: data)
        self.protect.start()

    def tearDown(self):
        self.protect.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_sign_in_keeps_the_token_never_the_password(self):
        account = {"email": "a@example.com", "passwordToken": "tok", "directoryServicesIdentifier": "42",
                   "name": "A B", "storeFront": "143441-1,29", "pod": "46"}
        run, sent = answers({"needs_code": True}, {"account": account})
        with mock.patch.object(appstore.subprocess, "run", side_effect=run):
            self.assertEqual(appstore.login("a@example.com", "secret"), {"needs_code": True})
            self.assertTrue(appstore.login("a@example.com", "secret", "123456")["signed_in"])
        self.assertEqual((sent[1]["cmd"], sent[1]["code"]), ("login", "123456"))
        saved = (appstore.state_dir() / "appstore.json").read_text(encoding="utf-8")
        self.assertNotIn("secret", saved)
        self.assertEqual(appstore.session()["token"], "tok")
        self.assertEqual(appstore._account(appstore.session())["pod"], "46")

    def test_download_address_and_license_answers(self):
        (appstore.state_dir() / "appstore.json").write_text(json.dumps(
            {"email": "a@example.com", "dsid": "42", "token_protected": "dG9r"}), encoding="utf-8")
        run, sent = answers({"needs_license": True}, {"url": "https://x.invalid/a.ipa", "version": "1.2",
                                                      "bundle": "com.example.game"}, {"expired": True})
        with mock.patch.object(appstore.subprocess, "run", side_effect=run):
            with self.assertRaises(appstore.NeedsLicense):
                appstore.download_url(7)
            self.assertEqual(appstore.download_url(7)["version"], "1.2")
            with self.assertRaisesRegex(appstore.AppStoreError, "expired"):
                appstore.get_license(7)
        self.assertEqual(sent[0]["account"]["passwordToken"], "tok")
        self.assertNotIn("password", sent[0])

    def test_keep_puts_the_copy_apart_and_remembers_it(self):
        (appstore.state_dir() / "appstore.json").write_text(json.dumps(
            {"email": "a@example.com", "dsid": "42", "token_protected": "dG9r"}), encoding="utf-8")
        with self.assertRaisesRegex(appstore.AppStoreError, "off"):
            appstore.keep(7, "com.example.game")  # the choice starts at "don't keep"
        with self.assertRaisesRegex(appstore.AppStoreError, "isn't built"):
            appstore.keep(7, "com.example.game", where="servers")

        def run(cmd, input=None, **kwargs):
            req = json.loads(input)
            out = Path(req["output"]) / "Game_7_1.2.ipa"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"PK ipa")
            return mock.Mock(stdout=json.dumps({"path": str(out), "version": "1.2", "bundle": "com.example.game"}) + "\n",
                             stderr="", returncode=0)
        appstore.set_keep_choice("pc")
        with mock.patch.object(appstore.subprocess, "run", side_effect=run):
            row = appstore.keep(7, "com.example.game")
        self.assertEqual((row["file"], row["version"], row["size"]), ("com.example.game-1.2.ipa", "1.2", 6))
        self.assertTrue((appstore.kept_dir() / row["file"]).is_file())
        self.assertNotIn("intake", str(appstore.kept_dir()))
        self.assertEqual([r["file"] for r in appstore.kept()], ["com.example.game-1.2.ipa"])
        appstore.forget("com.example.game-1.2.ipa")
        self.assertEqual(appstore.kept(), [])
        with self.assertRaises(appstore.AppStoreError):
            appstore.forget("../appstore.json")

    def test_regions_sorts_sold_missing_and_unknown(self):
        answers = {"us": {"name": "Game", "price": 0}, "jp": {}, "fr": None}

        def lookup(bundle, country):
            return country, answers.get(country, {})
        with mock.patch.object(appstore, "STOREFRONTS", ["us", "jp", "fr"]), \
                mock.patch.object(appstore, "_region_lookup", side_effect=lookup):
            r = appstore.regions("com.example.game", refresh=True)
        self.assertEqual((list(r["sold"]), r["missing"], r["unknown"]), (["us"], ["jp"], ["fr"]))
        with mock.patch.object(appstore, "_region_lookup", side_effect=AssertionError("cached")):
            self.assertEqual(appstore.regions("com.example.game")["missing"], ["jp"])
        with self.assertRaises(appstore.AppStoreError):
            appstore.regions("../x")


if __name__ == "__main__":
    unittest.main()
