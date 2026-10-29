"""dev.py sign: certificate signing (zsign, keeps the app's ID) by default, Apple ID signing on request; no phone."""

import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import dev
import dev_signing


class Result:
    def __init__(self, code=0, out=""):
        self.returncode, self.stdout, self.stderr = code, out, ""


class Signing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ipa = Path(self.tmp.name) / "game_accretion.ipa"
        self.ipa.write_bytes(b"ipa")
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_sign(self, apps=(), ready=True, **flags):
        signed = self.ipa.with_name("game_signed.ipa")

        def fake_run(cmd, **kw):
            self.calls.append(cmd)
            if "zsign -k" in " ".join(cmd):
                signed.write_bytes(b"signed")  # what zsign writes to its -o path
            return Result(0 if ready or "apps" in cmd else 1)

        args = argparse.Namespace(ipa=str(self.ipa), install=True, out=None, launch=False, mod=None,
                                  replace_original=False, **flags)
        with mock.patch.object(dev.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(dev, "ipa_info", return_value=({"CFBundleIdentifier": "com.example.game"}, None)), \
                mock.patch.object(dev_signing, "phone_apps", side_effect=[dict.fromkeys(apps), {"com.example.game": {}}]), \
                mock.patch.object(dev_signing, "pymobiledevice3_cli", return_value="pmd3"), \
                mock.patch.object(dev_signing, "plumesign", return_value="plumesign"), \
                mock.patch.object(dev_signing, "usbmux_device_id", return_value="1"):
            return dev.cmd_sign(args)

    def test_the_certificate_is_the_default_and_keeps_the_app_id(self):
        self.assertEqual(self.run_sign(), 0)
        zsign = next(c for c in self.calls if "zsign -k" in " ".join(c))
        script = zsign[zsign.index("-lc") + 1]
        self.assertNotIn(" -b ", script)  # -b would rename the app
        self.assertIn('-p "$(cat password)"', script)  # read inside bash: never a value in this process
        self.assertTrue(any(c[:3] == ["pmd3", "apps", "install"] for c in self.calls))  # the guarded USB install
        self.assertFalse(any(c and c[0] == "plumesign" for c in self.calls))

    def test_apple_id_signing_on_request(self):
        if not dev.WINDOWS:
            self.skipTest("plumesign runs on Windows")
        self.run_sign(apple_id=True)
        self.assertTrue(any(c and c[0] == "plumesign" for c in self.calls))
        self.assertFalse(any("zsign -k" in " ".join(c) for c in self.calls))

    def test_no_certificate_falls_back_to_the_apple_id(self):
        with mock.patch.object(dev_signing, "certificate_ready", return_value=False):
            self.assertEqual(dev.signer_for(argparse.Namespace(apple_id=False)), "apple-id")
        with mock.patch.object(dev_signing, "certificate_ready", return_value=True):
            self.assertEqual(dev.signer_for(argparse.Namespace(apple_id=False)), "certificate")
            self.assertEqual(dev.signer_for(argparse.Namespace(apple_id=True)), "apple-id")

    def test_a_kept_id_never_replaces_an_app_already_on_the_phone_silently(self):
        with self.assertRaises(dev.DevError) as err:
            self.run_sign(apps=["com.example.game"])  # the App Store game, say: no --mod, no --replace-original
        self.assertIn("--replace-original", str(err.exception))
        self.assertFalse(any(c[:3] == ["pmd3", "apps", "install"] for c in self.calls))


if __name__ == "__main__":
    unittest.main()
