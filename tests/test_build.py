"""Dependency-free packaging checks that do not require Blender or Godot."""

import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("packaging_script", Path(__file__).resolve().parents[1] / "build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        addon = root / "blender-part"
        pixel = root / "blender-lorama"
        addon.mkdir()
        extension = pixel / "src/Extensions/BlenderPixelorama"
        extension.mkdir(parents=True)
        (addon / "blender_manifest.toml").write_text(
            'schema_version = "1.0.0"\nid = "pixelorama_sync"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        (addon / "__init__.py").write_text("", encoding="utf-8")
        (extension / "extension.json").write_text(
            '{"version":"0.1.0", "name":"BlenderPixelorama"}', encoding="utf-8"
        )
        for name, value in (("ROOT", root), ("BLENDER_PART", addon),
                            ("PIXELORAMA_PART", pixel), ("DIST", root / "dist")):
            patcher = patch.object(build, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.root, self.addon, self.pixel = root, addon, pixel

    def test_build_uses_blender_official_cli_and_rejects_dependencies(self):
        def fake_run(command, **_kwargs):
            output = Path(command[command.index("--output-filepath") + 1])
            output.parent.mkdir(exist_ok=True)
            with zipfile.ZipFile(output, "w") as archive:
                archive.write(self.addon / "blender_manifest.toml", "blender_manifest.toml")
                archive.write(self.addon / "__init__.py", "__init__.py")
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with patch.object(build.subprocess, "run", side_effect=fake_run) as run:
            archive = build.build_addon("/opt/blender")
        command = run.call_args.args[0]
        self.assertEqual(command[:5], ["/opt/blender", "--factory-startup", "--command", "extension", "build"])
        self.assertTrue(archive.is_file())
        with zipfile.ZipFile(archive, "a") as package:
            package.writestr("wheels/dependency.whl", b"x")
        with self.assertRaisesRegex(RuntimeError, "bundled dependencies"):
            build._verify_dependency_free_archive(archive)

    def test_manifest_wheels_are_rejected_before_build(self):
        path = self.addon / "blender_manifest.toml"
        path.write_text(path.read_text() + 'wheels = ["./wheels/x.whl"]\n')
        with self.assertRaisesRegex(RuntimeError, "must not bundle wheels"):
            build.build_addon()

    def test_release_versions_match(self):
        build.stamp_release_version("1.2.3-rc.1")
        self.assertEqual(build._load_manifest()["version"], "1.2.3-rc.1")
        metadata = json.loads((self.pixel / "src/Extensions/BlenderPixelorama/extension.json").read_text())
        self.assertEqual(metadata["version"], "1.2.3-rc.1")
        self.assertEqual(metadata["name"], "BlenderPixelorama")

    def test_invalid_tag_does_not_modify_files(self):
        path = self.addon / "blender_manifest.toml"
        before = path.read_bytes()
        for value in ("latest", "1.2", "1.2.3/../../", "1.2.3\n", "01.2.3"):
            with self.assertRaises(ValueError):
                build.stamp_release_version(value)
            self.assertEqual(path.read_bytes(), before)

    def test_godot_errors_even_with_zero_exit_stop_build(self):
        result = type("Result", (), {"returncode": 0, "stdout": "SCRIPT ERROR: Parse failed", "stderr": ""})()
        with patch.object(build.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "reported an error"):
                build.build_pck()

    def test_no_pck_stops_build(self):
        result = type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        with patch.object(build.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "valid PCK"):
                build.build_pck()


if __name__ == "__main__":
    unittest.main()
