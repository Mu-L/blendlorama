#!/usr/bin/env python3
"""Build the Blender extension with Blender's official CLI and Pixelorama PCK."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


ROOT = Path(__file__).resolve().parent
BLENDER_PART = ROOT / "blender-part"
PIXELORAMA_PART = ROOT / "blender-lorama"
DIST = ROOT / "dist"


def _load_manifest() -> dict:
    with (BLENDER_PART / "blender_manifest.toml").open("rb") as stream:
        return tomllib.load(stream)


def _run(command: list[str], label: str, timeout: int = 300) -> str:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    output = result.stdout + result.stderr
    if result.returncode:
        raise RuntimeError(f"{label} failed:\n{output}")
    return output


def _verify_dependency_free_archive(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    required = {"blender_manifest.toml", "__init__.py"}
    missing = required.difference(names)
    if missing:
        raise RuntimeError(f"Blender package is missing {sorted(missing)}")
    forbidden = [name for name in names if
                 name.startswith(("wheels/", "libs/")) or name.endswith(".whl")]
    if forbidden:
        raise RuntimeError(f"Blender package contains bundled dependencies: {forbidden}")


def build_addon(blender: str = "blender", clean: bool = False) -> Path:
    manifest = _load_manifest()
    if manifest.get("wheels"):
        raise RuntimeError("blender_manifest.toml must not bundle wheels")
    if clean and DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True, exist_ok=True)
    output = DIST / f"{manifest['id']}-{manifest['version']}.zip"
    output.unlink(missing_ok=True)
    _run([
        blender,
        "--factory-startup", "--command", "extension", "build",
        "--source-dir", str(BLENDER_PART),
        "--output-filepath", str(output),
    ], "Blender extension build")
    if not output.is_file():
        raise RuntimeError("Blender did not create the extension archive")
    _verify_dependency_free_archive(output)
    print(f"[build] blender -> {output.relative_to(ROOT)}")
    return output


def validate_addon(path: Path, blender: str = "blender") -> None:
    _run([blender, "--factory-startup", "--command", "extension", "validate", str(path)],
         "Blender extension validation")


def build_source_bundle() -> Path:
    metadata_path = PIXELORAMA_PART / "src/Extensions/BlenderPixelorama/extension.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    output = DIST / f"blender-lorama-{metadata['version']}.zip"
    output.unlink(missing_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(PIXELORAMA_PART.rglob("*")):
            if source.is_dir() or any(part in {".godot", "__pycache__"} for part in source.parts):
                continue
            archive.write(source, source.relative_to(ROOT).as_posix())
    print(f"[build] source  -> {output.relative_to(ROOT)}")
    return output


def build_pck(godot: str = "godot") -> Path:
    DIST.mkdir(parents=True, exist_ok=True)
    output = DIST / "BlenderPixelorama.pck"
    output.unlink(missing_ok=True)
    for options in (["--editor", "--quit"], ["--export-pack", "Linux", str(output)]):
        text = _run([godot, "--headless", "--path", str(PIXELORAMA_PART), *options],
                    "Godot export", 180)
        if "ERROR:" in text or "SCRIPT ERROR:" in text:
            raise RuntimeError(f"Godot export reported an error:\n{text}")
    if not output.is_file() or output.stat().st_size < 32 or output.read_bytes()[:4] != b"GDPC":
        raise RuntimeError("Godot did not produce a valid PCK")
    print(f"[build] pck     -> {output.relative_to(ROOT)}")
    return output


def stamp_release_version(version: str) -> None:
    pattern = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?"
    if not re.fullmatch(pattern, version):
        raise ValueError("Release version must be MAJOR.MINOR.PATCH[-prerelease]")
    manifest_path = BLENDER_PART / "blender_manifest.toml"
    source, count = re.subn(r'^version = "[^"]*"$', f'version = "{version}"',
                            manifest_path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    if count != 1:
        raise ValueError("Manifest must contain exactly one version")
    manifest_path.write_text(source, encoding="utf-8")
    extension_path = PIXELORAMA_PART / "src/Extensions/BlenderPixelorama/extension.json"
    metadata = json.loads(extension_path.read_text(encoding="utf-8"))
    metadata["version"] = version
    extension_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


def write_checksums(paths: list[Path]) -> Path:
    output = DIST / "SHA256SUMS.txt"
    output.write_text("".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in paths
    ), encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="also build Pixelorama packages")
    parser.add_argument("--pck", action="store_true", help="also build Pixelorama packages")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--blender", default="blender")
    parser.add_argument("--godot", default="godot")
    parser.add_argument("--release-version")
    args = parser.parse_args()
    if args.release_version:
        stamp_release_version(args.release_version)
    outputs = [build_addon(args.blender, args.clean)]
    validate_addon(outputs[0], args.blender)
    if args.all or args.pck:
        outputs.extend((build_pck(args.godot), build_source_bundle()))
    write_checksums(outputs)
    print("[build] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
