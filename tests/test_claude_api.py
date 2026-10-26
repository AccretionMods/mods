"""studio/claude_api.py: named accounts, keys only in Credential Manager, streamed answers; faked API."""

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "studio"))

import claude_api

KEY = "sk-ant-test-not-a-real-key-000000000000"


class Stream(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def sse(*texts):
    events = [{"type": "message_start"}] + [{"type": "content_block_delta", "delta": {"type": "text_delta", "text": t}}
                                            for t in texts] + [{"type": "message_stop"}]
    return Stream("".join(f"event: x\ndata: {json.dumps(e)}\n\n" for e in events).encode())


class ClaudeAPI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("AM_ROOT")
        os.environ["AM_ROOT"] = self.tmp.name
        self.saved = {}
        vault = mock.patch.object(claude_api, "_vault", side_effect=lambda name: mock.Mock(
            save=lambda user, key: self.saved.__setitem__(name, key),
            load=lambda: ("apiKey", self.saved[name]) if name in self.saved else None,
            forget=lambda: self.saved.pop(name, None)))
        vault.start()
        self.addCleanup(vault.stop)

    def tearDown(self):
        if self.old is None:
            os.environ.pop("AM_ROOT", None)
        else:
            os.environ["AM_ROOT"] = self.old
        self.tmp.cleanup()

    def test_accounts_keep_names_and_models_on_disk_and_keys_in_the_vault(self):
        claude_api.add("work", KEY)
        claude_api.add("personal", KEY.replace("0", "1"), "claude-haiku-5-5")
        self.assertEqual([(a["name"], a["model"]) for a in claude_api.accounts()],
                         [("work", "claude-sonnet-5-5"), ("personal", "claude-haiku-5-5")])
        self.assertNotIn("sk-ant", claude_api.accounts_path().read_text(encoding="utf-8"))
        with self.assertRaises(claude_api.APIError):
            claude_api.add("bad", "not-a-key")
        claude_api.remove("work")
        self.assertEqual([a["name"] for a in claude_api.accounts()], ["personal"])
        self.assertNotIn("work", self.saved)

    def test_answers_stream_line_by_line_with_the_accounts_key_and_model(self):
        claude_api.add("work", KEY, "claude-opus-5-5")
        sent, lines = [], []

        def opener(req, timeout=None):
            sent.append(req)
            return sse("{\"n\": 1, ", "\"value\": 2}\nsecond", " line")

        text = claude_api.ask("pick switches", "work", say=lines.append, opener=opener)
        self.assertEqual(lines, ['{"n": 1, "value": 2}', "second line"])
        self.assertEqual(text, '{"n": 1, "value": 2}\nsecond line')
        body = json.loads(sent[0].data)
        self.assertEqual((body["model"], body["stream"]), ("claude-opus-5-5", True))
        self.assertEqual(sent[0].get_header("X-api-key"), KEY)
        self.assertIsNone(sent[0].get_header("Anthropic-workspace-id"))
        claude_api.set_workspace("work", "wrkspc_01AbCdEfGh")
        claude_api.ask("again", "work", say=lines.append, opener=opener)
        self.assertEqual(sent[1].get_header("Anthropic-workspace-id"), "wrkspc_01AbCdEfGh")

    def test_api_errors_never_show_the_key(self):
        import urllib.error

        claude_api.add("work", KEY)

        def opener(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {},
                                         io.BytesIO(b'{"error": {"message": "invalid x-api-key"}}'))

        with self.assertRaises(claude_api.APIError) as e:
            claude_api.ask("hi", "work", opener=opener)
        self.assertIn("401", str(e.exception))
        self.assertNotIn(KEY, str(e.exception))

    def test_studio_needs_an_account_before_the_engine_runs(self):
        import dev
        import server

        with self.assertRaises(dev.DevError):
            server.claude_api_command("hi")
        claude_api.add("work", KEY)
        cmd = server.claude_api_command("a prompt", "", "low")
        self.assertEqual(cmd[2:6], ["ask", "--account", "work", "--prompt-file"])
        self.assertEqual(Path(cmd[6]).read_text(encoding="utf-8"), "a prompt")
        self.assertNotIn(KEY, " ".join(cmd))


if __name__ == "__main__":
    unittest.main()
