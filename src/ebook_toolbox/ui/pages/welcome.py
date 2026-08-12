"""Application welcome page."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


class WelcomePage(QWidget):
    """Introduce the application and reserve space for recent tasks."""

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
            "集中管理 EPUB、TXT 与漫画处理工作流。当前版本已完成应用基础架构，"
            "实际处理功能将在后续阶段提供。"
        )
        description.setWordWrap(True)

        recent_tasks = QFrame()
        recent_tasks.setObjectName("recentTasks")
        recent_tasks.setFrameShape(QFrame.Shape.StyledPanel)
        recent_layout = QVBoxLayout(recent_tasks)
        recent_title = QLabel("最近任务")
        recent_title.setStyleSheet("font-size: 18px; font-weight: 600;")
        empty_message = QLabel("暂无任务")
        empty_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        recent_layout.addWidget(recent_title)
        recent_layout.addWidget(empty_message, 1)

        layout.addWidget(title)
        layout.addWidget(description)
        layout.addWidget(recent_tasks, 1)
