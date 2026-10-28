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
    SENTINEL = "FICTIONAL_REVIEW_SENTINEL"

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "detection_session", ROOT / "scripts/detection_session.py"
        )
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def schema4_report(self):
        entry = {"entry_id": 9, "name": "stat", "module": "foundation", "state": "unsupported",
                 "reason": "fingerprint_mismatch", "calls": 3, "matched": 1, "filtered": 0,
                 "aggregate": False, "attribution_unit": 1,
                 "evidence": {"implemented": True, "statically_supported": "not_recorded",
                              "host_contract_tested": "not_recorded", "arm64_built": True,
                              "device_compatible": False, "positive_artifact_filtering": "not_verified"}}
        row = {"entry_id": 9, "module": "foundation", "image_id": "@executable", "phase": "menu",
               "api": "stat", "rule_id": None, "calls": 3, "matched": 1, "filtered": 0}
        return {"schema_version": 4, "profile_mode": 1, "profile_id": "0" * 16,
                "callsite_id_algorithm": "fnv1a64-uuid-return-offset-le-v1",
                "callsites": [dict(row, build_uuid="unavailable", caller_id="0123456789abcdef",
                                   results={"unknown": 0, "success": 1, "negative": 0, "error": 0,
                                            "original_not_called": 0})],
                "callsite_capacity": 4096, "callsites_dropped": 0, "session_id": "a" * 32,
                "phase": "menu", "phase_request_id": "", "attribution": [row],
                "attribution_capacity": 4096, "attribution_dropped": 0, "elapsed_ms": 31000.5,
                "version_match": True, "fingerprints_status": "not_requested",
                "image_layout_failures": 0, "image_layout_failure_reasons": {"chained_fixups": 0},
                "loaded_image_count": 400, "entries": [entry],
                "coverage": {"incomplete": False, "saturated": False, "allocation_failures": 0,
                             "operational_gaps": 0},
                "adapters": {"stat": entry, "image_concealment": {"state": "unsupported"}}}

    def schema3_report(self, version=3):
        report = self.schema4_report()
        for key in ("entries", "coverage", "fingerprints_status"):
            del report[key]
        for row in report["attribution"] + report["callsites"]:
            del row["entry_id"], row["module"]
        report["adapters"] = {"stat": {"state": "observation-only", "calls": 3, "matched": 1, "filtered": 0}}
        return dict(report, schema_version=version)

    def test_schema4_supports_phase_and_preserves_capability_evidence(self):
        report = self.schema4_report()
        self.assertEqual(self.module.phase_request(report, "fight")["phase"], "fight")
        projection = self.module.validate_report(report)
        self.assertEqual(projection, report)
        self.assertIsNot(projection, report)
        self.assertIsNot(projection["entries"][0]["evidence"], report["entries"][0]["evidence"])
        for version in (2, 3):
            self.assertEqual(self.module.validate_report(self.schema3_report(version))["schema_version"], version)
        with self.assertRaisesRegex(ValueError, "schema"):
            self.module.validate_report(dict(report, schema_version=5))
        with self.assertRaisesRegex(ValueError, "not allowed"):
            self.module.validate_report(dict(report, schema_version=3))

    def test_validation_rejects_unexpected_fields_at_every_level(self):
        clean = self.schema4_report()
        planted = []
        for place in (
            lambda r: r,
            lambda r: r["entries"][0],
            lambda r: r["entries"][0]["evidence"],
            lambda r: r["adapters"]["stat"],
            lambda r: r["adapters"]["image_concealment"],
            lambda r: r["attribution"][0],
            lambda r: r["callsites"][0],
            lambda r: r["callsites"][0]["results"],
            lambda r: r["coverage"],
        ):
            report = json.loads(json.dumps(clean))
            place(report)["private_note"] = self.SENTINEL
            planted.append(report)
        report = json.loads(json.dumps(clean))
        report["image_layout_failure_reasons"][self.SENTINEL] = 1
        planted.append(report)
        report = json.loads(json.dumps(clean))
        report["adapters"]["device name"] = {"state": "failed"}
        planted.append(report)
        for report in planted:
            with self.assertRaises(ValueError) as caught:
                self.module.validate_report(report)
            self.assertNotIn(self.SENTINEL, str(caught.exception))
            self.assertNotIn("device name", str(caught.exception))

    def test_validation_bounds_values(self):
        clean = self.schema4_report()
        for change in (
            {"session_id": "device-serial"},
            {"phase": "guessed"},
            {"profile_mode": 7},
            {"profile_id": self.SENTINEL * 20},
            {"profile_id": "line\nbreak"},
            {"elapsed_ms": -1},
            {"version_match": "yes"},
            {"loaded_image_count": True},
            {"attribution": [{"calls": -1}]},
            {"attribution": [{"rule_id": 5}]},
            {"entries": [{"state": "hidden"}]},
            {"entries": [{"evidence": {"implemented": "maybe"}}]},
            {"callsites": [{"results": {"success": 1.5}}]},
            {"adapters": []},
            {"coverage": [True]},
            {"callsites": [{}] * 4097},
        ):
            with self.assertRaises(ValueError, msg=str(list(change))) as caught:
                self.module.validate_report(dict(clean, **change))
            self.assertNotIn(self.SENTINEL, str(caught.exception))

    def report_command(self, report):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dev = SimpleNamespace(
                Path=Path,
                mod_dir=lambda _: root,
                read_json=lambda _: {"bundle_id": "io.example.game"},
                installed_bundles=lambda _: ["io.example.game"],
                pymobiledevice3_cli=lambda: "phone-tool",
                am_root=lambda: root,
                DevError=RuntimeError,
            )

            def transfer(arguments, **_):
                Path(arguments[-1]).write_text(json.dumps(report))
                return SimpleNamespace(returncode=0)

            with (
                mock.patch.object(self.module.subprocess, "run", side_effect=transfer),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                try:
                    code = self.module.command(dev, SimpleNamespace(action="report", target="example"))
                except RuntimeError as error:
                    return error, output.getvalue()
            return code, output.getvalue()

    def test_report_output_contains_only_allowed_fields(self):
        clean = self.schema4_report()
        code, printed = self.report_command(clean)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(printed), clean)
        for plant in (
            lambda r: r.__setitem__("account", self.SENTINEL),
            lambda r: r["entries"][0]["evidence"].__setitem__("device", self.SENTINEL),
            lambda r: r["callsites"][0].__setitem__("path", self.SENTINEL),
        ):
            report = json.loads(json.dumps(clean))
            plant(report)
            outcome, printed = self.report_command(report)
            self.assertIsInstance(outcome, RuntimeError)
            self.assertNotIn(self.SENTINEL, printed)
            self.assertNotIn(self.SENTINEL, str(outcome))
            self.assertEqual(printed, "")

    def test_summary_names_states_and_gaps_without_raw_fields(self):
        report = self.schema4_report()
        lines = self.module.summarize(report)
        self.assertEqual(lines[0], "schema 4, mode observe, phase menu, elapsed 31 s")
        self.assertIn("unsupported: stat (fingerprint_mismatch)", lines)
        self.assertIn("matched calls 1, filtered 0", lines)
        self.assertFalse(any("gap" in line or "dropped" in line for line in lines))
        report["version_match"] = False
        report["coverage"]["incomplete"] = True
        report["callsites_dropped"] = 3
        report["fingerprints_status"] = "fingerprint_unavailable"
        lines = self.module.summarize(report)
        self.assertIn(
            "game version mismatch: modules disabled; fingerprints fingerprint_unavailable; "
            "telemetry incomplete; rows dropped 3",
            lines,
        )
        self.assertEqual(self.module.summarize({"schema_version": 3})[0],
                         "schema 3, mode unknown, phase unknown, elapsed 0 s")

    def test_report_command_keeps_json_on_stdout_and_summary_on_stderr(self):
        clean = self.schema4_report()
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            code, printed = self.report_command(clean)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(printed), clean)
        self.assertIn("schema 4, mode observe", errors.getvalue())
        self.assertNotIn("schema 4, mode observe", printed)

    def test_report_polls_until_the_file_is_pullable_but_not_past_validation(self):
        clean = self.schema4_report()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dev = SimpleNamespace(
                Path=Path, mod_dir=lambda _: root, read_json=lambda _: {"bundle_id": "io.example.game"},
                installed_bundles=lambda _: ["io.example.game"], pymobiledevice3_cli=lambda: "phone-tool",
                am_root=lambda: root, DevError=RuntimeError,
            )
            pulls = []

            def transfer(arguments, **_):
                pulls.append(1)
                if len(pulls) < 3:
                    return SimpleNamespace(returncode=1)
                Path(arguments[-1]).write_text(json.dumps(clean))
                return SimpleNamespace(returncode=0)

            args = SimpleNamespace(action="report", target="example", wait=30)
            with (
                mock.patch.object(self.module.subprocess, "run", side_effect=transfer),
                mock.patch.object(self.module.time, "sleep") as sleep,
                contextlib.redirect_stdout(io.StringIO()) as output,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(self.module.command(dev, args), 0)
            self.assertEqual(len(pulls), 3)
            self.assertEqual(sleep.call_count, 2)
            self.assertEqual(json.loads(output.getvalue()), clean)
            pulls.clear()
            bad = dict(clean, account=self.SENTINEL)

            def invalid(arguments, **_):
                pulls.append(1)
                Path(arguments[-1]).write_text(json.dumps(bad))
                return SimpleNamespace(returncode=0)

            with (
                mock.patch.object(self.module.subprocess, "run", side_effect=invalid),
                mock.patch.object(self.module.time, "sleep") as sleep,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                with self.assertRaises(RuntimeError):
                    self.module.command(dev, args)
            self.assertEqual(len(pulls), 1)
            self.assertEqual(sleep.call_count, 0)

    def test_validator_vocabularies_match_runtime_sources(self):
        import re
        registry = (ROOT / "runtime/AMDetectionRegistry.c").read_text(encoding="utf-8")
        names = re.findall(r'(?:AM_ENTRY\(|\{)\s*\d+\s*,\s*"([a-z_0-9]+)"', registry)
        self.assertGreaterEqual(len(names), 26)
        expected = set(names) | {"image_concealment", "class_concealment"}
        self.assertEqual(set(self.module.ADAPTER_NAMES), expected)
        bindings = (ROOT / "runtime/AMImportBindings.c").read_text(encoding="utf-8")
        block = re.search(r"AMImportFailureName.*?\n\}", bindings, re.S).group(0)
        reasons = set(re.findall(r'"([a-z_]+)"', block)) - {"none"}
        self.assertEqual(set(self.module.FAILURE_REASONS), reasons)

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
