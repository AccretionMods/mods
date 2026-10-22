"""Local Studio workspace state: synthetic data only, no phone or external services."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "studio"))
import workspace


class Versions(unittest.TestCase):
    def test_fingerprint_changes_with_source_and_ignores_runtime_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "studio").mkdir()
            source = root / "studio" / "server.py"
            source.write_text("first")
            first = workspace.source_revision(root)
            (root / "studio" / "state.json").write_text("runtime")
            self.assertEqual(first, workspace.source_revision(root))
            source.write_text("second")
            self.assertNotEqual(first, workspace.source_revision(root))


class Snapshots(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        self.mod = self.root / "mod"
        self.mod.mkdir()
        (self.mod / "tweaks.json").write_text('{"tweaks": []}', encoding="utf-8")
        (self.mod / "Tweak.x").write_text("original\n", encoding="utf-8")

    def create(self):
        return workspace.snapshot_create(self.state, "sample-mod", self.mod)

    def manifest(self, identifier):
        return self.state / "snapshots" / "sample-mod" / (identifier + ".json")

    def test_create_preview_restore_and_recovery_preserve_absence(self):
        snap = self.create()
        (self.mod / "Tweak.x").write_text("changed\n", encoding="utf-8")
        (self.mod / "hooks.json").write_text('{"hooks": []}', encoding="utf-8")
        (self.mod / ".upload-key").write_text("synthetic credential", encoding="utf-8")
        preview = workspace.snapshot_preview(
            self.state, "sample-mod", self.mod, snap["id"]
        )
        diff = next(f for f in preview["files"] if f["name"] == "Tweak.x")
        self.assertIn("-changed", diff["diff"])
        self.assertIn("+original", diff["diff"])
        result = workspace.snapshot_restore(
            self.state, "sample-mod", self.mod, snap["id"], preview["expected"]
        )
        self.assertEqual((self.mod / "Tweak.x").read_text(), "original\n")
        self.assertFalse((self.mod / "hooks.json").exists())
        self.assertTrue((self.mod / ".upload-key").exists())
        saved = self.manifest(snap["id"]).read_text()
        self.assertNotIn("synthetic credential", saved)
        ids = {s["id"] for s in workspace.snapshot_list(self.state, "sample-mod")}
        self.assertEqual(ids, {snap["id"], result["recovery_id"]})
        recovery = workspace.snapshot_preview(
            self.state, "sample-mod", self.mod, result["recovery_id"]
        )
        workspace.snapshot_restore(
            self.state,
            "sample-mod",
            self.mod,
            result["recovery_id"],
            recovery["expected"],
        )
        self.assertEqual((self.mod / "Tweak.x").read_text(), "changed\n")
        self.assertTrue((self.mod / "hooks.json").exists())

    def test_stale_preview_rejected_without_writes_or_recovery(self):
        snap = self.create()
        preview = workspace.snapshot_preview(
            self.state, "sample-mod", self.mod, snap["id"]
        )
        (self.mod / "Tweak.x").write_text("new concurrent edit", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed"):
            workspace.snapshot_restore(
                self.state, "sample-mod", self.mod, snap["id"], preview["expected"]
            )
        self.assertEqual((self.mod / "Tweak.x").read_text(), "new concurrent edit")
        self.assertEqual(len(workspace.snapshot_list(self.state, "sample-mod")), 1)

    def test_path_traversal_rejected(self):
        for mod in ("../escape", "x/y", "", "..", "CON"):
            with self.subTest(mod=mod), self.assertRaises(ValueError):
                workspace.snapshot_create(self.state, mod, self.mod)
        for identifier in ("../escape", "a" * 31, "g" * 32):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                workspace.snapshot_preview(
                    self.state, "sample-mod", self.mod, identifier
                )

    def test_corrupt_manifest_rejected_before_any_write(self):
        for corrupt in ("hash", "extra", "json", "missing", "present"):
            snap = self.create()
            path = self.manifest(snap["id"])
            data = json.loads(path.read_text())
            if corrupt == "hash":
                data["files"]["Tweak.x"]["sha256"] = "0" * 64
            elif corrupt == "extra":
                data["files"]["../outside"] = data["files"]["Tweak.x"]
            elif corrupt == "json":
                content = "invalid JSON"
                data["files"]["tweaks.json"]["content"] = content
                data["files"]["tweaks.json"]["sha256"] = workspace.hashlib.sha256(
                    content.encode()
                ).hexdigest()
            elif corrupt == "missing":
                del data["files"]["Tweak.x"]
            else:
                data["files"]["Tweak.x"]["present"] = "yes"
            path.write_text(json.dumps(data), encoding="utf-8")
            before = sorted(p.name for p in path.parent.iterdir())
            with self.subTest(corrupt=corrupt), self.assertRaises(ValueError):
                workspace.snapshot_restore(
                    self.state, "sample-mod", self.mod, snap["id"], "0" * 64
                )
            self.assertEqual(before, sorted(p.name for p in path.parent.iterdir()))
            self.assertEqual((self.mod / "Tweak.x").read_text(), "original\n")

    def test_oversized_files_rejected(self):
        (self.mod / "Tweak.x").write_bytes(b"x" * (workspace.SNAPSHOT_FILE_LIMIT + 1))
        with self.assertRaisesRegex(ValueError, "large"):
            self.create()

    def test_invalid_current_json_is_preserved_in_recovery(self):
        snap = self.create()
        (self.mod / "tweaks.json").write_text("unfinished edit", encoding="utf-8")
        preview = workspace.snapshot_preview(
            self.state, "sample-mod", self.mod, snap["id"]
        )
        result = workspace.snapshot_restore(
            self.state, "sample-mod", self.mod, snap["id"], preview["expected"]
        )
        recovery = json.loads(self.manifest(result["recovery_id"]).read_text())
        self.assertEqual(recovery["files"]["tweaks.json"]["content"], "unfinished edit")
        self.assertEqual(len(workspace.snapshot_list(self.state, "sample-mod")), 2)
        with self.assertRaisesRegex(ValueError, "Invalid JSON"):
            workspace.snapshot_preview(
                self.state, "sample-mod", self.mod, result["recovery_id"]
            )


if __name__ == "__main__":
    unittest.main()
