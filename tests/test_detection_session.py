import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


class DetectionSession(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "detection_session", ROOT / "scripts/detection_session.py"
        )
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_schema4_supports_phase_and_preserves_capability_evidence(self):
        report = {"schema_version": 4, "session_id": "a" * 32, "profile_mode": 1,
                  "entries": [{"entry_id": 9, "module": "foundation", "state": "unsupported",
                               "reason": "fingerprint_mismatch", "evidence": {"arm64_built": True,
                                                                            "device_compatible": False}}]}
        self.assertEqual(self.module.phase_request(report, "fight")["phase"], "fight")
        self.assertEqual(self.module.validate_report(report), report)
        for version in (2, 3):
            self.assertEqual(self.module.validate_report(dict(report, schema_version=version))["schema_version"], version)
        with self.assertRaisesRegex(ValueError, "schema"):
            self.module.validate_report(dict(report, schema_version=5))

    def test_phase_requires_current_session_and_active_observation(self):
        report = {"schema_version": 3, "session_id": "a" * 32, "profile_mode": 1}
        request = self.module.phase_request(report, "fight")
        self.assertEqual(request["session_id"], report["session_id"])
        self.assertEqual(request["phase"], "fight")
        self.assertEqual(len(request["request_id"]), 32)
        for changed in (
            {"session_id": "device-id"},
            {"profile_mode": 0},
            {"schema_version": 1},
        ):
            with self.assertRaises(ValueError):
                self.module.phase_request(dict(report, **changed), "fight")
        with self.assertRaises(ValueError):
            self.module.phase_request(report, "guessed-phase")

    def test_acknowledgement_rejects_stale_report_and_other_launch(self):
        request = self.module.phase_request(
            {"schema_version": 2, "session_id": "a" * 32, "profile_mode": 1}, "login"
        )
        report = {
            "session_id": request["session_id"],
            "phase_request_id": request["request_id"],
            "phase": "login",
        }
        self.assertTrue(self.module.acknowledged(report, request))
        for key, value in (
            ("session_id", "b" * 32),
            ("phase_request_id", "c" * 32),
            ("phase", "startup"),
        ):
            self.assertFalse(
                self.module.acknowledged(dict(report, **{key: value}), request)
            )

    def test_usb_command_waits_for_matching_acknowledgement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = {
                "schema_version": 2,
                "session_id": "a" * 32,
                "profile_mode": 1,
                "phase": "startup",
            }
            dev = SimpleNamespace(
                Path=Path,
                mod_dir=lambda _: root,
                read_json=lambda _: {"bundle_id": "io.example.game"},
                installed_bundles=lambda _: ["io.example.game"],
                pymobiledevice3_cli=lambda: "phone-tool",
                am_root=lambda: root,
                DevError=RuntimeError,
            )
            args = SimpleNamespace(
                action="phase", target="example", phase="menu", wait=2
            )
            transfers = []

            def transfer(arguments, **_):
                transfers.append(arguments[2])
                if arguments[2] == "push":
                    request = json.loads(Path(arguments[-2]).read_text())
                    report.update(
                        phase=request["phase"], phase_request_id=request["request_id"]
                    )
                else:
                    Path(arguments[-1]).write_text(json.dumps(report))
                return SimpleNamespace(returncode=0)

            with (
                mock.patch.object(self.module.subprocess, "run", side_effect=transfer),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(self.module.command(dev, args), 0)
            self.assertEqual(transfers, ["pull", "push", "pull"])
            self.assertIn("acknowledged: menu", output.getvalue())


if __name__ == "__main__":
    unittest.main()
