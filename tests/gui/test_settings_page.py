"""Preferences must survive restart without touching task results or user files."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from ebook_toolbox.preferences import PreferencesStore
from ebook_toolbox.services.epub_image_compressor import CompressionResult
from ebook_toolbox.ui.main_window import MainWindow


def test_save_theme_survives_fresh_process(qtbot, tmp_path):
    path = tmp_path / "中文 空格" / "settings.json"
    window = MainWindow(preferences_store=PreferencesStore(path))
    qtbot.addWidget(window)
    page = window.settings_page
    page.theme_combo.setCurrentIndex(1)
    qtbot.mouseClick(page.save_button, Qt.MouseButton.LeftButton)
    assert "已保存" in page.status.text()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "from pathlib import Path; from ebook_toolbox.preferences import PreferencesStore; "
            "import sys; print(PreferencesStore(Path(sys.argv[1])).load().theme)",
            str(path),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert result.stdout.strip() == "dark"


def test_remember_size_and_reset_only_preferences(qtbot, tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    store = PreferencesStore(path)
    window = MainWindow(preferences_store=store)
    qtbot.addWidget(window)
    page = window.settings_page
    page.remember_checkbox.setChecked(True)
    qtbot.mouseClick(page.save_button, Qt.MouseButton.LeftButton)
    window.resize(1000, 680)
    window.close()
    reopened = MainWindow(preferences_store=PreferencesStore(path))
    qtbot.addWidget(reopened)
    assert reopened.width() == 1000
    assert reopened.height() == 680
    marker = tmp_path / "原稿.txt"
    marker.write_text("不要修改", encoding="utf-8")
    reopened.epub_image_compressor_page.compression_finished.emit(
        CompressionResult(source=marker, output=None, success=False)
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    qtbot.mouseClick(reopened.settings_page.reset_button, Qt.MouseButton.LeftButton)
    assert marker.read_text(encoding="utf-8") == "不要修改"
    assert store.load().remember_window is False
    assert store.load().theme == "light"
    assert len(reopened.report_store.entries) == 1


def test_corrupt_config_and_write_failure_are_visible(qtbot, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{bad json", encoding="utf-8")
    window = MainWindow(preferences_store=PreferencesStore(path))
    qtbot.addWidget(window)
    assert "读取" in window.settings_page.status.text()
    assert window.settings_page.theme_combo.currentData() == "light"
    blocked = tmp_path / "blocked"
    blocked.write_text("file, not directory", encoding="utf-8")
    failed = MainWindow(preferences_store=PreferencesStore(blocked / "settings.json"))
    qtbot.addWidget(failed)
    qtbot.mouseClick(failed.settings_page.save_button, Qt.MouseButton.LeftButton)
    assert "失败" in failed.settings_page.status.text()
    assert blocked.read_text(encoding="utf-8") == "file, not directory"


def test_invalid_values_fall_back_and_unknown_keys_are_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "theme": "evil",
                "remember_window": "true",
                "width": -1,
                "height": 999999,
                "unrelated": 1,
            }
        )
    )
    value = PreferencesStore(path).load()
    assert (value.theme, value.remember_window, value.width, value.height) == (
        "light",
        False,
        1100,
        720,
    )


@pytest.mark.parametrize("theme,color", [("light", "#e6e6e6"), ("dark", "#31363b")])
def test_actual_application_startup_applies_saved_theme(tmp_path, theme, color):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"theme": theme, "remember_window": False, "width": 1100, "height": 720})
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import sys; from pathlib import Path; import ebook_toolbox.preferences as p; "
            "p.default_preferences_path=lambda: Path(sys.argv[1]); "
            "from ebook_toolbox.app import create_application; "
            "app=create_application(['theme-probe']); "
            "assert sys.argv[2] in app.styleSheet(); print('theme applied')",
            str(path),
            color,
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "theme applied"
