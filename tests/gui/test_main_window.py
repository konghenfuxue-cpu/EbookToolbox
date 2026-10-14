"""GUI smoke tests for the application shell."""

from PySide6.QtCore import Qt

from ebook_toolbox.services.epub_image_compressor import CompressionResult
from ebook_toolbox.services.epub_structural_repair.models import (
    BatchRepairResult,
    FileRepairResult,
    RepairResult,
)
from ebook_toolbox.ui.main_window import MainWindow
from ebook_toolbox.ui.navigation import NAVIGATION_ITEMS
from ebook_toolbox.ui.pages.detection_report import DetectionReportPage
from ebook_toolbox.ui.pages.epub_image_compressor import EpubImageCompressorPage
from ebook_toolbox.ui.pages.epub_structural_repair import EpubStructuralRepairPage
from ebook_toolbox.ui.pages.epub_toc_repair import EpubTocRepairPage
from ebook_toolbox.ui.pages.text_merge import TextMergePage


def test_main_window_starts(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert window.windowTitle() == "电子书工具箱"
    assert window.navigation.count() == len(NAVIGATION_ITEMS)
    assert window.pages.count() == len(NAVIGATION_ITEMS)
    assert window.pages.currentIndex() == 0
    assert window.statusBar().currentMessage() == "就绪"
    assert NAVIGATION_ITEMS[1] == "EPUB 目录修复"
    assert isinstance(window.pages.widget(1), EpubTocRepairPage)
    assert window.epub_toc_repair_page is window.pages.widget(1)
    assert isinstance(window.pages.widget(2), EpubImageCompressorPage)
    assert NAVIGATION_ITEMS[2] == "EPUB 图片压缩"
    assert window.epub_image_compressor_page is window.pages.widget(2)
    assert NAVIGATION_ITEMS[3] == "EPUB 综合修复"
    assert isinstance(window.pages.widget(3), EpubStructuralRepairPage)
    assert window.epub_structural_repair_page is window.pages.widget(3)
    assert NAVIGATION_ITEMS[4] == "TXT 工具"
    assert isinstance(window.pages.widget(4), TextMergePage)
    assert window.text_merge_page is window.pages.widget(4)
    assert NAVIGATION_ITEMS[6] == "检测报告"
    assert isinstance(window.pages.widget(6), DetectionReportPage)
    assert window.detection_report_page is window.pages.widget(6)


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


def test_completed_task_is_added_to_detection_reports(qtbot, tmp_path) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    result = CompressionResult(
        source=tmp_path / "source.epub",
        output=tmp_path / "source_optimized.epub",
        success=True,
    )

    window.epub_image_compressor_page.compression_finished.emit(result)

    assert window.detection_report_page.report_table.rowCount() == 1
    assert window.detection_report_page.report_table.item(0, 1).text() == "EPUB 图片压缩"


def test_structural_report_keeps_metadata_and_standard_checks_separate(qtbot, tmp_path) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    source, output = tmp_path / "source.epub", tmp_path / "fixed.epub"
    repaired = RepairResult(source=source, output=output, success=True, calibre_status="passed")
    repaired.internal_validation_status = "passed"
    batch = BatchRepairResult(files=[FileRepairResult(source, output, repaired)])

    window.epub_structural_repair_page.repair_finished.emit(batch)

    entry = window.report_store.entries[0]
    assert dict(entry.checks) == {
        "internal_structure": "passed",
        "calibre_metadata": "passed",
        "epubcheck": "not_run",
    }
    window.detection_report_page.report_table.selectRow(0)
    details = window.detection_report_page.details.toPlainText()
    assert "EPUBCheck" in details
    assert "未运行" in details
