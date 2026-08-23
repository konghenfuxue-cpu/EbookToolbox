"""Main application window."""

from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QStackedWidget, QWidget

from ebook_toolbox.ui.navigation import NAVIGATION_ITEMS, NavigationList
from ebook_toolbox.ui.pages import (
    EpubImageCompressorPage,
    EpubStructuralRepairPage,
    EpubTocRepairPage,
    PlaceholderPage,
    WelcomePage,
)


class MainWindow(QMainWindow):
    """Top-level window containing navigation and tool pages."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("mainWindow")
        self.setWindowTitle("电子书工具箱")
        self.resize(1100, 720)

        central_widget = QWidget()
        central_layout = QHBoxLayout(central_widget)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)

        self.navigation = NavigationList()
        self.pages = QStackedWidget()
        self.pages.setObjectName("pageStack")
        self.pages.addWidget(WelcomePage())
        for title in NAVIGATION_ITEMS[1:]:
            if title == "EPUB 目录修复":
                self.epub_toc_repair_page = EpubTocRepairPage()
                self.pages.addWidget(self.epub_toc_repair_page)
            elif title == "EPUB 图片压缩":
                self.epub_image_compressor_page = EpubImageCompressorPage()
                self.pages.addWidget(self.epub_image_compressor_page)
            elif title == "EPUB 综合修复":
                self.epub_structural_repair_page = EpubStructuralRepairPage()
                self.pages.addWidget(self.epub_structural_repair_page)
            else:
                self.pages.addWidget(PlaceholderPage(title))

        self.navigation.page_changed.connect(self.pages.setCurrentIndex)
        central_layout.addWidget(self.navigation)
        central_layout.addWidget(self.pages, 1)
        self.setCentralWidget(central_widget)

        self.statusBar().showMessage("就绪")
