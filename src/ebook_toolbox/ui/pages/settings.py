"""User preferences and application capability information."""

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ebook_toolbox import __version__
from ebook_toolbox.build_info import build_description
from ebook_toolbox.preferences import Preferences, PreferencesStore


class SettingsPage(QWidget):
    def __init__(self, store: PreferencesStore) -> None:
        super().__init__()
        self.store = store
        self.setObjectName("settingsPage")
        value = store.load()
        self._saved_options = (value.theme, value.remember_window)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        title = QLabel("设置")
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        layout.addWidget(title)
        appearance = QGroupBox("外观")
        form = QFormLayout(appearance)
        self.theme_combo = QComboBox()
        self.theme_combo.setObjectName("settingsTheme")
        self.theme_combo.addItem("浅色", "light")
        self.theme_combo.addItem("深色", "dark")
        self.theme_combo.setCurrentIndex(1 if value.theme == "dark" else 0)
        form.addRow("主题（保存后，下次启动生效）", self.theme_combo)
        layout.addWidget(appearance)
        behavior = QGroupBox("窗口与偏好")
        behavior_layout = QVBoxLayout(behavior)
        self.remember_checkbox = QCheckBox("记住窗口大小（下次启动恢复，不记住位置）")
        self.remember_checkbox.setObjectName("settingsRememberWindow")
        self.remember_checkbox.setChecked(value.remember_window)
        behavior_layout.addWidget(self.remember_checkbox)
        behavior_layout.addWidget(QLabel("仅保存本应用偏好，不改变覆盖输出等文件安全规则。"))
        buttons = QHBoxLayout()
        self.save_button = QPushButton("保存设置")
        self.save_button.setObjectName("saveSettingsButton")
        self.reset_button = QPushButton("恢复默认设置…")
        self.reset_button.setObjectName("resetSettingsButton")
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.reset_button)
        buttons.addStretch()
        behavior_layout.addLayout(buttons)
        self.status = QLabel(store.warning or "尚未更改设置。")
        self.status.setObjectName("settingsStatus")
        self.status.setWordWrap(True)
        behavior_layout.addWidget(self.status)
        layout.addWidget(behavior)
        about = QGroupBox("关于与检查能力")
        about_layout = QVBoxLayout(about)
        runtime = Path(sys.executable) if getattr(sys, "frozen", False) else Path(sys.argv[0])
        for text in (
            f"EbookToolbox · 版本 {__version__}",
            build_description(),
            f"运行位置：{runtime.resolve()}",
            f"配置保存位置：{store.path}",
            "内部结构检查：由各工具按其检查范围执行，不代表完整标准合规。",
            "Calibre：可选 ebook-meta 元数据读取，需本机另行安装并可被发现。",
            "EPUBCheck：未集成、未执行。",
            "报告：未导出时仅保留当前会话；导出的 JSON 含本机路径。",
        ):
            label = QLabel(text)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            about_layout.addWidget(label)
        layout.addWidget(about)
        layout.addStretch()
        self.save_button.clicked.connect(self._save)
        self.reset_button.clicked.connect(self._reset)
        self.theme_combo.currentIndexChanged.connect(self._options_changed)
        self.remember_checkbox.toggled.connect(self._options_changed)

    def has_unsaved_changes(self) -> bool:
        options = (self.theme_combo.currentData(), self.remember_checkbox.isChecked())
        return options != self._saved_options

    def _options_changed(self) -> None:
        message = "有未保存的设置更改。" if self.has_unsaved_changes() else "设置与已保存值一致。"
        self.status.setText(message)

    def confirm_leave(self) -> bool:
        if not self.has_unsaved_changes():
            return True
        answer = QMessageBox.question(
            self,
            "设置尚未保存",
            "是否保存设置更改？",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self._save()
        if answer == QMessageBox.StandardButton.Discard:
            theme, remember = self._saved_options
            self.theme_combo.setCurrentIndex(1 if theme == "dark" else 0)
            self.remember_checkbox.setChecked(remember)
            self._options_changed()
            return True
        return False

    def _save(self) -> bool:
        current = self.store.load()
        value = Preferences(
            self.theme_combo.currentData(),
            self.remember_checkbox.isChecked(),
            current.width,
            current.height,
        )
        try:
            self.store.save(value)
        except OSError as error:
            self.status.setText(f"设置保存失败，未确认保存成功：{error}")
            return False
        else:
            self._saved_options = (value.theme, value.remember_window)
            self.status.setText("设置已保存。主题下次启动生效；窗口大小在正常关闭时保存。")
            return True

    def _reset(self) -> None:
        answer = QMessageBox.question(
            self,
            "恢复默认设置",
            "仅重置主题和窗口偏好，不删除书籍、输出或报告。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.save(Preferences())
        except OSError as error:
            self.status.setText(f"恢复默认设置失败：{error}")
        else:
            self._saved_options = ("light", False)
            self.theme_combo.setCurrentIndex(0)
            self.remember_checkbox.setChecked(False)
            self.status.setText("已恢复默认设置。主题及默认窗口大小下次启动生效。")
