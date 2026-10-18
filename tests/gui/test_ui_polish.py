import pytest
from PySide6.QtWidgets import QMessageBox

from ebook_toolbox.preferences import PreferencesStore
from ebook_toolbox.ui.main_window import MainWindow


@pytest.mark.parametrize(
    "answer,expected_row,saved",
    [
        (QMessageBox.StandardButton.Cancel, 7, False),
        (QMessageBox.StandardButton.Discard, 0, False),
        (QMessageBox.StandardButton.Save, 0, True),
    ],
)
def test_leave_settings(qtbot, tmp_path, monkeypatch, answer, expected_row, saved):
    store = PreferencesStore(tmp_path / "中文 空格" / "settings.json")
    window = MainWindow(store)
    qtbot.addWidget(window)
    window.navigation.setCurrentRow(7)
    page = window.settings_page
    page.theme_combo.setCurrentIndex(1)
    assert "未保存" in page.status.text()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: answer)
    window.navigation.setCurrentRow(0)
    assert window.pages.currentIndex() == expected_row
    assert window.navigation.currentRow() == expected_row
    assert (store.load().theme == "dark") is saved
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    page.confirm_leave()


def test_close_cancel_keeps_window(qtbot, tmp_path, monkeypatch):
    window = MainWindow(PreferencesStore(tmp_path / "settings.json"))
    qtbot.addWidget(window)
    window.settings_page.remember_checkbox.setChecked(True)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    assert window.close() is False
    window.settings_page.remember_checkbox.setChecked(False)


def test_save_failure_blocks_navigation(qtbot, tmp_path, monkeypatch):
    blocked = tmp_path / "blocked"
    blocked.write_text("keep", encoding="utf-8")
    window = MainWindow(PreferencesStore(blocked / "settings.json"))
    qtbot.addWidget(window)
    window.navigation.setCurrentRow(7)
    window.settings_page.theme_combo.setCurrentIndex(1)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Save)
    window.navigation.setCurrentRow(0)
    assert window.pages.currentIndex() == 7
    assert window.settings_page.has_unsaved_changes()
    assert "失败" in window.settings_page.status.text()
    assert blocked.read_text(encoding="utf-8") == "keep"
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    window.settings_page.confirm_leave()
