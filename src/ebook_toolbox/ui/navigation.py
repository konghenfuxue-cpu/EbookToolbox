"""Left-hand tool navigation widget."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QListWidget, QListWidgetItem

NAVIGATION_ITEMS = (
    "首页",
    "EPUB 目录修复",
    "EPUB 图片压缩",
    "EPUB 综合修复",
    "TXT 工具",
    "漫画工具",
    "检测报告",
    "设置",
)


class NavigationList(QListWidget):
    """Navigation list that exposes page indexes without leaking item objects."""

    page_changed = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("toolNavigation")
        self.setFixedWidth(180)
        self.setSpacing(4)

        for label in NAVIGATION_ITEMS:
            QListWidgetItem(label, self)

        self.currentRowChanged.connect(self.page_changed)
        self.setCurrentRow(0)
