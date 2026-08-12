"""Reusable placeholder for tools planned in later phases."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class PlaceholderPage(QWidget):
    """Explain that a selected tool is not implemented yet."""

    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("placeholderPage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)

        heading = QLabel(title)
        heading.setStyleSheet("font-size: 24px; font-weight: 600;")
        message = QLabel("该功能将在后续阶段实现。")

        layout.addStretch()
        layout.addWidget(heading, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(message, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()
