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
