"""Check stable release versions and downloaded artifact integrity without imports."""

from __future__ import annotations

import argparse
import ast
import hashlib
import re
import sys
import tomllib
from pathlib import Path


def validate_versions(root: Path, tag: str) -> None:
    if not re.fullmatch(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", tag):
        raise ValueError("Release tag must be a stable version such as v0.1.0")
    version = tag[1:]
    expected_tuple = tuple(int(part) for part in version.split(".")) + (0,)
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    versions: dict[str, object] = {"pyproject.toml": project["project"]["version"]}
    package_tree = ast.parse(
        (root / "src/ebook_toolbox/__init__.py").read_text(encoding="utf-8")
    )
    for node in package_tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        ):
            if "__version__" in versions:
                raise ValueError("Duplicate application version")
            versions["__version__"] = ast.literal_eval(node.value)
    windows_tree = ast.parse(
        (root / "packaging/windows_version_info.txt").read_text(encoding="utf-8")
    )
    for node in ast.walk(windows_tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id == "FixedFileInfo":
            for keyword in node.keywords:
                if keyword.arg in {"filevers", "prodvers"}:
                    if keyword.arg in versions:
                        raise ValueError(f"Duplicate Windows version: {keyword.arg}")
                    versions[keyword.arg] = ast.literal_eval(keyword.value)
        elif node.func.id == "StringStruct" and len(node.args) == 2:
            key = ast.literal_eval(node.args[0])
            if key in {"FileVersion", "ProductVersion"}:
                if key in versions:
                    raise ValueError(f"Duplicate Windows version: {key}")
                versions[key] = ast.literal_eval(node.args[1])
    required = {"pyproject.toml", "__version__", "filevers", "prodvers",
                "FileVersion", "ProductVersion"}
    if versions.keys() != required:
        raise ValueError(f"Missing version sources: {sorted(required - versions.keys())}")
    for name, actual in versions.items():
        expected = expected_tuple if name in {"filevers", "prodvers"} else version
        if actual != expected:
            raise ValueError(f"Version mismatch: {name} is {actual!r}, expected {expected!r}")


def validate_artifacts(directory: Path) -> None:
    archive = directory / "EbookToolbox-portable.zip"
    checksum = directory / "EbookToolbox-portable.zip.sha256"
    parts = checksum.read_text(encoding="utf-8-sig").strip().split()
    if len(parts) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
        raise ValueError("Invalid SHA-256 checksum file")
    if parts[1] != archive.name:
        raise ValueError("Checksum filename does not match the portable ZIP")
    with archive.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != parts[0].lower():
        raise ValueError("Portable ZIP SHA-256 mismatch")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--tag", required=True)
    parser.add_argument("--artifact-directory", type=Path)
    args = parser.parse_args()
    try:
        validate_versions(args.project_root, args.tag)
        if args.artifact_directory is not None:
            validate_artifacts(args.artifact_directory)
    except (OSError, ValueError, SyntaxError, KeyError, TypeError) as exc:
        print(f"Release validation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Release validation passed: {args.tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
