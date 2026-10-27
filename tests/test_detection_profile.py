import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DetectionProfile(unittest.TestCase):
    def test_dynamic_write_probe_and_exact_spawn_rules_are_strict(self):
        identity = {"algorithm": "sha256-executable-sections-v1", "uuid": "a" * 32,
                    "sha256": "b" * 64, "ranges": [{"offset": 512, "size": 64}]}
        p = dict(self.profile, schema_version=2, adapters=["filesystem", "process"], apis=["open", "posix_spawn"],
                 write_probes=[{"token": "private_display_name"}], spawn_rules=[{"path": "/bin/sh", "error": "EACCES"}],
                 image_fingerprints={name: identity for name in self.profile["images"]})
        self.assertEqual(self.module.problems(p), [])
        header = self.module.render(p)
        self.assertIn("#define AM_DETECTION_WRITE_DYNAMIC_COUNT 1", header)
        self.assertIn(".spawn_paths = am_detection_spawn_paths", header)
        for probes in ([{"token": "unknown"}], [{"token": "private_display_name"}] * 2):
            self.assertTrue(self.module.problems(dict(p, write_probes=probes)))
        for rules in ([{"path": "/", "error": "EACCES"}], [{"path": "/bin/sh", "error": "success"}],
                      [{"path": "/bin/sh", "argv": ["anything"]}], [{"path": "/bin/sh"}] * 2):
            self.assertTrue(self.module.problems(dict(p, spawn_rules=rules)))

    def test_directory_native_group_requires_all_lifetime_entrypoints(self):
        identity = {"algorithm": "sha256-executable-sections-v1", "uuid": "a" * 32,
                    "sha256": "b" * 64, "ranges": [{"offset": 512, "size": 64}]}
        p = dict(self.profile, schema_version=2, adapters=["directories"],
                 apis=["opendir", "readdir", "closedir"],
                 image_fingerprints={name: identity for name in self.profile["images"]})
        self.assertEqual(self.module.problems(p), [])
        for apis in (["opendir"], ["readdir", "closedir"]):
            self.assertTrue(self.module.problems(dict(p, apis=apis)))
        self.assertTrue(self.module.problems(dict(p, schema_version=1)))
        legacy = self.module.render(self.profile)
        self.assertIn("#define AM_DETECTION_API_OPENDIR 0", legacy)

    def test_new_file_apis_require_schema2_fingerprints_and_are_never_implicit(self):
        p = dict(self.profile, schema_version=2, adapters=["filesystem"], apis=["readlink", "fstatat"])
        self.assertTrue(self.module.problems(p))
        identity = {"algorithm": "sha256-executable-sections-v1", "uuid": "a" * 32,
                    "sha256": "b" * 64, "ranges": [{"offset": 512, "size": 64}]}
        p["image_fingerprints"] = {name: identity for name in p["images"]}
        self.assertEqual(self.module.problems(p), [])
        self.assertTrue(self.module.problems(dict(p, schema_version=1)))
        legacy = dict(self.profile, adapters=["filesystem"]);legacy.pop("apis", None)
        header = self.module.render(legacy)
        self.assertIn("#define AM_DETECTION_API_READLINK 0", header)
        self.assertIn("#define AM_DETECTION_API_FSTATAT 0", header)

    def test_write_probe_validation_protects_game_storage_and_requires_write_entrypoint(self):
        identity = {"algorithm": "sha256-executable-sections-v1", "uuid": "a" * 32,
                    "sha256": "b" * 64, "ranges": [{"offset": 512, "size": 64}]}
        p = dict(self.profile, schema_version=2, adapters=["filesystem"], apis=["open"],
                 write_probes=[{"path": "/private/exact-probe"}],
                 image_fingerprints={name: identity for name in self.profile["images"]})
        self.assertEqual(self.module.problems(p), [])
        self.assertIn(".write_paths = am_detection_write_paths", self.module.render(p))
        for probes in ([{"path": "/private"}], [{"path": "/var/mobile/Containers/Data/save"}],
                       [{"path": "/private/exact-probe"}] * 2, [{"path": "/private/x", "extra": True}]):
            self.assertTrue(self.module.problems(dict(p, write_probes=probes)))
        self.assertTrue(self.module.problems(dict(p, apis=["stat"])))

    def test_schema2_modes_only_reduce_global_mode_and_observation_requires_diagnostics(self):
        p = dict(self.profile, schema_version=2, diagnostics=True,
                 module_modes={"filesystem": "observe"})
        self.assertEqual(self.module.problems(p), [])
        self.assertIn("am_detection_module_modes", self.module.render(p))
        for update in ({"mode": "observe", "module_modes": {"filesystem": "filter"}},
                       {"diagnostics": False}, {"module_modes": {"unknown": "off"}}):
            with self.subTest(update=update): self.assertTrue(self.module.problems(dict(p, **update)))

    def test_schema2_fingerprint_is_strict_and_selected_image_bound(self):
        identity = {"algorithm": "sha256-executable-sections-v1", "uuid": "a" * 32,
                    "sha256": "b" * 64, "ranges": [{"offset": 512, "size": 64}]}
        p = dict(self.profile, schema_version=2,
                 image_fingerprints={self.profile["images"][0]: identity})
        self.assertEqual(self.module.problems(p), [])
        self.assertIn("am_detection_image_fingerprints", self.module.render(p))
        for changed in (dict(identity, uuid="bad"), dict(identity, unexpected=True),
                        dict(identity, ranges=[{"offset": 512, "size": -1}]),
                        dict(identity, ranges=[{"offset": 512, "size": 64}, {"offset": 520, "size": 8}])):
            with self.subTest(changed=changed):
                self.assertTrue(self.module.problems(dict(p, image_fingerprints={p["images"][0]: changed})))
        self.assertTrue(self.module.problems(dict(p, image_fingerprints={"Frameworks/Other.dylib": identity})))

    def test_schema2_rejects_aggregate_fingerprint_read_budget_over_one_gib(self):
        images = ["@executable", "Frameworks/A.dylib", "Frameworks/B.dylib"]
        identity = {"algorithm": "sha256-executable-sections-v1", "uuid": "a" * 32,
                    "sha256": "b" * 64, "ranges": [{"offset": 512, "size": 512 * 1024 * 1024 - 512}]}
        p = dict(self.profile, schema_version=2, images=images,
                 image_fingerprints={name: identity for name in images})
        self.assertTrue(self.module.problems(p))

    def test_shared_default_disables_diagnostics_and_explicit_profiles_keep_them(self):
        default = self.module.default_profile({"tested_app_version": "1.7.5"})
        self.assertFalse(default["diagnostics"])
        self.assertIn(
            "#define AM_DETECTION_DIAGNOSTICS_ENABLED 0", self.module.render(default)
        )
        self.assertIn(
            "#define AM_DETECTION_DIAGNOSTICS_ENABLED 1",
            self.module.render(self.profile),
        )
        for value in (1, "false", None):
            self.assertTrue(self.module.problems(dict(self.profile, diagnostics=value)))

    def test_explicit_profile_accepts_recognized_legacy_source_override(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import validate_mods

        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            (folder / "detection.json").write_text(
                json.dumps(dict(self.profile, mode="off"))
            )
            (folder / "Makefile").write_text(
                "TWEAK_NAME = Test\nTest_FILES = SideloadCompat.m\n"
            )
            shutil.copyfile(
                ROOT / "mods/thewanderer-plus/SideloadCompat.m",
                folder / "SideloadCompat.m",
            )
            self.assertEqual(
                validate_mods.detection_problems(
                    folder, {"tested_app_version": "1.2.0"}
                ),
                [],
            )

    def test_known_legacy_build_sources_migrate_without_writing_the_mod(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            original = (ROOT / "mods/thewanderer-plus/SideloadCompat.m").read_bytes()
            legacy = folder / "SideloadCompat.m"
            legacy.write_bytes(original)
            sources = "Tweak.x SideloadCompat.m menu/AMMenu.m"
            self.assertEqual(
                self.module.build_sources(folder, sources),
                "Tweak.x runtime/SideloadCompat.m menu/AMMenu.m",
            )
            self.assertEqual(legacy.read_bytes(), original)
            legacy.write_bytes(original.replace(b"\n", b"\r\n"))
            self.assertEqual(
                self.module.build_sources(folder, sources),
                "Tweak.x runtime/SideloadCompat.m menu/AMMenu.m",
            )
            legacy.write_text("// custom compatibility code\n")
            self.assertEqual(self.module.build_sources(folder, sources), sources)
            self.assertEqual(
                self.module.build_sources(folder, "runtime/SideloadCompat.m"),
                "runtime/SideloadCompat.m",
            )

    def test_shared_default_is_version_bound_minimal_and_not_game_specific(self):
        for version in ("1.7.5", "2.3"):
            p = self.module.default_profile({"tested_app_version": version})
            self.assertEqual(p["game_version"], version)
            self.assertEqual(p["mode"], "filter")
            self.assertEqual(p["apis"], ["stat", "foundation_exists"])
            self.assertEqual(p["adapters"], ["filesystem", "foundation"])
            self.assertEqual(len(p["paths"]), 22)
            self.assertNotIn("path_prefixes", p)
            self.assertEqual(self.module.problems(p, version), [])
        self.assertIsNone(self.module.default_profile({}))

    def test_explicit_api_selection_rejects_unknown_duplicate_and_disabled_groups(self):
        p = dict(
            self.profile,
            adapters=["filesystem", "foundation"],
            apis=["stat", "foundation_exists"],
        )
        self.assertEqual(self.module.problems(p), [])
        for apis in (["stat", "stat"], ["kernel"], ["getenv"], "stat"):
            with self.subTest(apis=apis):
                self.assertTrue(self.module.problems(dict(p, apis=apis)))

    def test_explicit_api_macros_disable_every_unselected_entry_point(self):
        p = dict(
            self.profile,
            adapters=["filesystem", "foundation"],
            apis=["stat", "foundation_exists"],
        )
        header = self.module.render(p)
        for name in ("stat", "foundation_exists"):
            self.assertIn(f"#define AM_DETECTION_API_{name.upper()} 1", header)
        for name in (
            "access",
            "lstat",
            "open",
            "fopen",
            "getenv",
            "foundation_attributes",
            "urls",
        ):
            self.assertIn(f"#define AM_DETECTION_API_{name.upper()} 0", header)
        legacy = self.module.render(self.profile)
        self.assertIn("#define AM_DETECTION_API_ACCESS 1", legacy)
        self.assertIn("#define AM_DETECTION_API_FOUNDATION_ATTRIBUTES 1", legacy)

    def test_rule_identity_is_stable_and_kind_specific(self):
        detection_profile = self.module
        first = detection_profile.rule_identity("paths", "/private/jailbreak.txt")
        self.assertEqual(
            first, detection_profile.rule_identity("paths", "/private/jailbreak.txt")
        )
        self.assertNotEqual(
            first,
            detection_profile.rule_identity("path_prefixes", "/private/jailbreak.txt"),
        )
        self.assertEqual(
            detection_profile.rule_identity("url_schemes", "CYDIA"),
            detection_profile.rule_identity("url_schemes", "cydia"),
        )

    def setUp(self):
        original_path = sys.path.copy()
        sys.path.insert(0, str(ROOT / "scripts"))
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), original_path))
        file = ROOT / "scripts/detection_profile.py"
        self.assertTrue(file.exists(), "profile generator missing")
        spec = importlib.util.spec_from_file_location("detection_profile", file)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.profile = {
            "schema_version": 1,
            "game_version": "1.2.0",
            "mode": "observe",
            "images": [
                "@executable",
                "Frameworks/UnityFramework.framework/UnityFramework",
            ],
            "adapters": ["filesystem", "foundation", "urls", "environment", "images"],
            "paths": ["/private/jailbreak.txt"],
            "url_schemes": ["cydia"],
        }

    def test_strict_types_and_version_and_unknown_fields(self):
        self.assertEqual(self.module.problems(self.profile, "1.2.0"), [])
        for key, value in [
            ("schema_version", True),
            ("mode", "stealth"),
            ("paths", "bad"),
            ("images", ["../bad"]),
            ("adapters", ["kernel"]),
            ("unknown", 1),
        ]:
            with self.subTest(key=key):
                self.assertTrue(
                    self.module.problems(dict(self.profile, **{key: value}))
                )
        self.assertTrue(self.module.problems(self.profile, "1.2.1"))

    def test_broad_and_ambiguous_rules_and_oversized_lists_rejected(self):
        for key, values in [
            ("path_prefixes", ["/private"]),
            ("paths", ["/"]),
            ("paths", ["/opt/../foo"]),
            ("paths", ["/opt/a\0b"]),
            ("paths", ["/opt/a", "/opt/a"]),
            ("paths", ["/opt/" + "x" * 5000]),
            ("environment_names", ["X"] * 65),
            ("url_schemes", ["CyDiA", "cydia"]),
        ]:
            with self.subTest(key=key, values=str(values)[:50]):
                self.assertTrue(
                    self.module.problems(dict(self.profile, **{key: values}))
                )

    def test_equivalent_private_roots_and_system_frameworks_cannot_be_hidden(self):
        for path in (
            "/private/var",
            "/private/var/mobile",
            "/System/Library/Frameworks",
            "/System/Library/PrivateFrameworks/UIKitCore.framework/UIKitCore",
            "/System/Library",
            "/private/var/containers",
            "/var/containers/Bundle/Application",
            "/var/mobile/Containers/Data/Application",
        ):
            with self.subTest(path=path):
                self.assertTrue(
                    self.module.problems(dict(self.profile, path_prefixes=[path]))
                )

    def test_unavailable_image_filtering_rejected(self):
        p = dict(self.profile, mode="filter", image_basenames=["OurMenu.dylib"])
        self.assertTrue(self.module.problems(p))

    def test_trace_profile_identity_tracks_rules_and_ignores_json_order(self):
        self.assertTrue(
            hasattr(self.module, "profile_identity"), "trace profile identity missing"
        )
        first = self.module.profile_identity(self.profile)
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertEqual(
            first,
            self.module.profile_identity(dict(reversed(list(self.profile.items())))),
        )
        self.assertNotEqual(
            first,
            self.module.profile_identity(dict(self.profile, paths=["/opt/changed"])),
        )

    def test_validator_requires_compatible_runtime_and_version(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import validate_mods

        self.assertTrue(
            hasattr(validate_mods, "detection_problems"),
            "mod validator has no detection integration",
        )
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            (folder / "detection.json").write_text(json.dumps(self.profile))
            self.assertTrue(
                validate_mods.detection_problems(
                    folder, {"tested_app_version": "1.2.0"}
                )
            )
            (folder / "Makefile").write_text("X_FILES = runtime/SideloadCompat.m\n")
            self.assertEqual(
                validate_mods.detection_problems(
                    folder, {"tested_app_version": "1.2.0"}
                ),
                [],
            )
            self.assertTrue(
                validate_mods.detection_problems(
                    folder, {"tested_app_version": "1.2.1"}
                )
            )

    def test_generation_default_and_explicit_off_override(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            (folder / "mod.json").write_text(
                json.dumps({"tested_app_version": "1.2.0"})
            )
            config = folder / "detection.json"
            config.write_text(json.dumps(self.profile))
            self.assertTrue(self.module.generate(folder))
            header = folder / "runtime/AMDetectionProfile.gen.h"
            self.assertTrue(header.exists())
            config.unlink()
            self.assertTrue(self.module.generate(folder))
            self.assertIn(
                "AM_DETECTION_PROFILE_MODE AM_DETECTION_FILTER", header.read_text()
            )
            config.write_text(json.dumps(dict(self.profile, mode="off", apis=[])))
            self.assertTrue(self.module.generate(folder))
            self.assertIn(
                "AM_DETECTION_PROFILE_MODE AM_DETECTION_OFF", header.read_text()
            )
            config.unlink()
            (folder / "mod.json").write_text("{}")
            self.assertFalse(self.module.generate(folder))
            self.assertFalse(header.exists())

    @unittest.skipUnless(shutil.which("cc"), "requires a C compiler")
    def test_generated_unicode_and_quotes_compile_and_match(self):
        p = dict(self.profile, paths=['/opt/é"x'])
        self.assertEqual(self.module.problems(p), [])
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "AMDetectionProfile.gen.h").write_text(self.module.render(p))
            (root / "main.c").write_text(
                '#include "AMDetectionProfile.gen.h"\n#include <assert.h>\n'
                'int main(void){assert(AMDetectionMatchPath(&am_detection_profile,"/opt/\\303\\251\\"x"));return 0;}\n'
            )
            subprocess.run(
                [
                    shutil.which("cc"),
                    "-std=c11",
                    "-Wall",
                    "-Werror",
                    "-I",
                    str(ROOT / "runtime"),
                    str(root / "main.c"),
                    str(ROOT / "runtime/AMDetectionPolicy.c"),
                    "-o",
                    str(root / "test"),
                ],
                check=True,
            )
            subprocess.run([str(root / "test")], check=True)
