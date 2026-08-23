"""GUI smoke tests for the application shell."""

from PySide6.QtCore import Qt

from ebook_toolbox.ui.main_window import MainWindow
from ebook_toolbox.ui.navigation import NAVIGATION_ITEMS
from ebook_toolbox.ui.pages.epub_image_compressor import EpubImageCompressorPage


def test_main_window_starts(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert window.windowTitle() == "电子书工具箱"
    assert window.navigation.count() == len(NAVIGATION_ITEMS)
    assert window.pages.count() == len(NAVIGATION_ITEMS)
    assert window.pages.currentIndex() == 0
    assert window.statusBar().currentMessage() == "就绪"
    assert isinstance(window.pages.widget(2), EpubImageCompressorPage)


def test_navigation_switches_page(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    target_row = 3
    item_rectangle = window.navigation.visualItemRect(window.navigation.item(target_row))
    qtbot.mouseClick(
        window.navigation.viewport(), Qt.MouseButton.LeftButton, pos=item_rectangle.center()
    )

    assert window.navigation.currentRow() == target_row
    assert window.pages.currentIndex() == target_row
