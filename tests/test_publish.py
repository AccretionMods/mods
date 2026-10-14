import contextlib
import hashlib
import io
import json
import plistlib
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import publish
import update_source


class PublishTests(unittest.TestCase):
    def test_personal_mod_refuses_publication_before_credentials_or_network(self):
        meta = {"personal": True, "bundle_id": "io.github.accretionmods.fixture"}
        with (
            patch.object(publish, "load", return_value=meta),
            patch.object(publish, "ipa_version", return_value="1.0"),
            patch.object(
                publish,
                "upload_key",
                side_effect=AssertionError("Credentials must not be read"),
            ) as key,
            patch.object(publish, "call") as network,
            patch.object(publish, "record") as record,
            patch.object(
                sys,
                "argv",
                ["publish.py", "--mod", "fixture", "--ipa", "fictional.ipa"],
            ),
            self.assertRaises(SystemExit) as caught,
        ):
            publish.main()
        self.assertIn("personal", str(caught.exception))
        key.assert_not_called()
        network.assert_not_called()
        record.assert_not_called()

    def test_source_builder_omits_personal_mod_even_if_a_release_entry_exists(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "mods/fixture").mkdir(parents=True)
            metadata = {
                "personal": True,
                "name": "Fixture",
                "bundle_id": "io.github.accretionmods.fixture",
                "description": "Fixture",
                "icon_url": "https://example.test/icon.png",
                "kind": "tweak",
            }
            (root / "mods/fixture/mod.json").write_text(json.dumps(metadata))
            builds = {
                "fixture": [
                    {
                        "version": "1.0",
                        "date": "2026-10-14T00:00:00Z",
                        "size": 1,
                        "downloadURL": "https://example.test/download",
                        "minOSVersion": "16.0",
                        "modVersion": "1.0",
                    }
                ]
            }
            with patch.object(update_source, "ROOT", str(root)):
                self.assertEqual(update_source.build_source(builds)["apps"], [])

    def test_retry_after_source_failure_does_not_upload_published_bytes_again(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "mods/demo").mkdir(parents=True)
            (root / "mods/demo/mod.json").write_text(
                json.dumps(
                    {
                        "bundle_id": "io.github.accretionmods.demo",
                        "name": "Demo",
                        "version": "1.0",
                        "description": "Fictional fixture",
                        "icon_url": "https://example.test/icon.png",
                        "kind": "tweak",
                    }
                )
            )
            ipa = root / "demo.ipa"
            with zipfile.ZipFile(ipa, "w") as archive:
                archive.writestr(
                    "Payload/Demo.app/Info.plist",
                    plistlib.dumps({"CFBundleShortVersionString": "1.0-mod1.0"}),
                )
            digest = hashlib.sha256(ipa.read_bytes()).hexdigest()
            state = {"published": False}
            requests = []
            uploads = []

            class Storage(BaseHTTPRequestHandler):
                def do_PUT(self):
                    uploads.append(self.rfile.read(int(self.headers["Content-Length"])))
                    self.send_response(200)
                    self.end_headers()

                def log_message(self, *args):
                    pass

            storage = HTTPServer(("127.0.0.1", 0), Storage)
            worker = threading.Thread(target=storage.serve_forever, daemon=True)
            worker.start()
            self.addCleanup(storage.server_close)
            self.addCleanup(storage.shutdown)

            def server(url, key, body):
                requests.append((url, dict(body)))
                self.assertEqual(body.get("sha256"), digest)
                if "finish=1" in url:
                    self.assertEqual(
                        body.get("upload_id"), "12345678-1234-4234-8234-123456789abc"
                    )
                    state["published"] = True
                    return {"ok": True}
                if state["published"]:
                    return {"published": True, "sha256": digest, "path": "private/path"}
                return {
                    "signedUrl": f"http://127.0.0.1:{storage.server_port}/staging",
                    "upload_id": "12345678-1234-4234-8234-123456789abc",
                }

            original_save = update_source.save
            failed = False

            def interrupted_save(name, data):
                nonlocal failed
                if name == "mods.json" and not failed:
                    failed = True
                    raise OSError("fictional source-write interruption")
                original_save(name, data)

            with (
                patch.object(update_source, "ROOT", str(root)),
                patch.object(publish, "ROOT", str(root)),
                patch.object(
                    publish, "upload_key", return_value="FICTIONAL_UPLOAD_KEY"
                ),
                patch.object(publish, "call", side_effect=server),
                patch.object(publish, "save", side_effect=interrupted_save),
                patch.object(
                    sys, "argv", ["publish.py", "--mod", "demo", "--ipa", str(ipa)]
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                with self.assertRaises(OSError):
                    publish.main()
                publish.main()
                self.assertEqual(uploads, [ipa.read_bytes()])
            self.assertEqual(
                len(requests), 3, "prepare, finish, then idempotent prepare on retry"
            )
            self.assertEqual(
                len(json.loads((root / "releases.json").read_text())["demo"]), 1
            )
            self.assertEqual(
                len(json.loads((root / "mods.json").read_text())["apps"]), 1
            )


if __name__ == "__main__":
    unittest.main()
