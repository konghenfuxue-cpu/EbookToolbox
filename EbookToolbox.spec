# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from datetime import datetime
import json
import subprocess

from PyInstaller.utils.hooks import collect_data_files


PROJECT_ROOT = Path(SPECPATH).resolve()
SOURCE_ROOT = PROJECT_ROOT / "src"
RESOURCE_ROOT = SOURCE_ROOT / "ebook_toolbox" / "resources"

datas = collect_data_files("qt_material")
identity_path = Path(workpath) / "build-info.json"
identity_path.parent.mkdir(parents=True, exist_ok=True)
try:
    revision = subprocess.check_output(
        ["git", "-C", str(PROJECT_ROOT), "rev-parse", "--short=12", "HEAD"], text=True
    ).strip()
    dirty = bool(subprocess.check_output(
        ["git", "-C", str(PROJECT_ROOT), "status", "--porcelain"], text=True
    ).strip())
except (OSError, subprocess.SubprocessError):
    revision, dirty = "unknown", True
identity_path.write_text(json.dumps({
    "revision": revision, "dirty": dirty,
    "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
}), encoding="utf-8")
datas += [(str(identity_path), "ebook_toolbox")]
datas += [
    (str(RESOURCE_ROOT / "app_icon.ico"), "ebook_toolbox/resources"),
    (str(RESOURCE_ROOT / "app_icon.svg"), "ebook_toolbox/resources"),
]

hiddenimports = [
    "PIL.ImageCms",
    "PIL.JpegImagePlugin",
    "PIL.PngImagePlugin",
    "PIL.WebPImagePlugin",
    "PySide6.QtSvg",
    "PySide6.QtUiTools",
]

excluded_modules = [
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PyInstaller",
    "migration_sources",
    "pytest",
    "pytestqt",
    "ruff",
    "tests",
]

analysis = Analysis(
    [str(PROJECT_ROOT / "main.py")],
    pathex=[str(SOURCE_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(PROJECT_ROOT / "packaging" / "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_modules,
    noarchive=False,
    optimize=0,
)

# The desktop host can add unrelated Poppler/ICU directories to PATH. PyInstaller's
# dependency scanner may then collect those optional ICU DLLs for Qt6Core, even though
# the PySide6 wheel does not ship or require them. The mismatched DLLs make QtGui fail
# with WinError 127. Keep the portable build independent from the host PATH.
forbidden_host_binaries = {"icuuc.dll", "icudt78.dll"}
analysis.binaries = [
    item for item in analysis.binaries if Path(item[0]).name.casefold() not in forbidden_host_binaries
]

pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="EbookToolbox",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    uac_admin=False,
    uac_uiaccess=False,
    icon=str(RESOURCE_ROOT / "app_icon.ico"),
    version=str(PROJECT_ROOT / "packaging" / "windows_version_info.txt"),
)

portable = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="EbookToolbox",
)
