"""Collect qt-material runtime data without package cache files."""

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("qt_material", excludes=["**/__pycache__"])
