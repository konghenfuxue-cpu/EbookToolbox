"""Exercise release validation through its actual command-line entry point."""

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts" / "validate_release.py"


@pytest.fixture
def release_root(tmp_path):
    root = tmp_path / "发布 验证目录"
    for relative in (
        "pyproject.toml",
        "src/ebook_toolbox/__init__.py",
        "packaging/windows_version_info.txt",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT / relative, target)
    return root


def run_validation(root, tag="v0.1.0", *, artifact=False):
    args = [sys.executable, "-X", "utf8", str(SCRIPT), "--project-root", str(root), "--tag", tag]
    if artifact:
        args.extend(["--artifact-directory", str(root / "产物 副本")])
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=15)


def test_matching_release_versions_pass_in_chinese_space_path(release_root):
    result = run_validation(release_root)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("tag", ["v0.2.0", "0.1.0", "v0.1.0-rc1", "v01.1.0"])
def test_mismatched_or_nonstable_tag_is_rejected(release_root, tag):
    assert run_validation(release_root, tag).returncode == 1


@pytest.mark.parametrize(
    "relative,old,new",
    [
        ("pyproject.toml", 'version = "0.1.0"', 'version = "0.2.0"'),
        ("src/ebook_toolbox/__init__.py", '"0.1.0"', '"0.2.0"'),
        ("packaging/windows_version_info.txt", "filevers=(0, 1, 0, 0)",
         "filevers=(0, 2, 0, 0)"),
        ("packaging/windows_version_info.txt", "prodvers=(0, 1, 0, 0)",
         "prodvers=(0, 2, 0, 0)"),
        ("packaging/windows_version_info.txt", "'FileVersion', '0.1.0'",
         "'FileVersion', '0.2.0'"),
        ("packaging/windows_version_info.txt", "'ProductVersion', '0.1.0'",
         "'ProductVersion', '0.2.0'"),
    ],
)
def test_any_version_source_mismatch_blocks_release(release_root, relative, old, new):
    target = release_root / relative
    target.write_text(target.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    assert run_validation(release_root).returncode == 1


def test_missing_version_source_blocks_release(release_root):
    target = release_root / "src/ebook_toolbox/__init__.py"
    target.write_text('"""No version declared."""', encoding="utf-8")
    result = run_validation(release_root)
    assert result.returncode == 1
    assert "Missing version sources" in result.stderr


@pytest.mark.parametrize("mutation", [None, "tampered", "missing", "wrong_filename"])
def test_artifact_checksum_blocks_corrupt_or_missing_asset(release_root, mutation):
    artifact = release_root / "产物 副本"
    artifact.mkdir()
    archive = artifact / "EbookToolbox-portable.zip"
    archive.write_bytes(b"verified portable build")
    digest = hashlib.sha256(b"verified portable build").hexdigest()
    name = "wrong.zip" if mutation == "wrong_filename" else archive.name
    (artifact / f"{archive.name}.sha256").write_text(f"{digest}  {name}", encoding="utf-8")
    if mutation == "tampered":
        archive.write_bytes(b"corrupted build")
    elif mutation == "missing":
        archive.unlink()
    result = run_validation(release_root, artifact=True)
    assert result.returncode == (0 if mutation is None else 1), result.stderr
