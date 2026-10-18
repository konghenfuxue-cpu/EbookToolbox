"""Application welcome page."""

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class WelcomePage(QWidget):
    """Introduce available tools and explain the task report boundary."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("welcomePage")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(16)

        title = QLabel("欢迎使用电子书工具箱")
        title.setObjectName("welcomeTitle")
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        description = QLabel(
            "已提供 EPUB 目录修复、图片压缩、综合修复与 TXT 工具。"
            "漫画工具暂未开放。处理记录请到“检测报告”查看；未导出的记录仅保留当前会话。"
        )
        description.setWordWrap(True)

        layout.addWidget(title)
        layout.addWidget(description)
        layout.addStretch()
