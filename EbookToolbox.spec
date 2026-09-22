# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


PROJECT_ROOT = Path(SPECPATH).resolve()
SOURCE_ROOT = PROJECT_ROOT / "src"
RESOURCE_ROOT = SOURCE_ROOT / "ebook_toolbox" / "resources"

datas = collect_data_files("qt_material")
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
