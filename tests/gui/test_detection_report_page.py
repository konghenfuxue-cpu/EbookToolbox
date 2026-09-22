from datetime import datetime

from PySide6.QtCore import Qt

from ebook_toolbox.ui.pages.detection_report import (
    DetectionReportPage,
    ReportEntry,
    ReportStore,
)


def test_report_page_renders_entry_details_and_can_clear(qtbot) -> None:
    store = ReportStore()
    page = DetectionReportPage(store)
    qtbot.addWidget(page)
    page.show()

    store.add(
        ReportEntry(
            created_at=datetime(2026, 9, 22, 15, 0, 1),
            tool="EPUB 图片压缩",
            source="C:/books/source.epub",
            output="C:/books/source_optimized.epub",
            success=True,
            warnings=("[cover_warning] 封面保持原样",),
        )
    )

    assert page.report_table.rowCount() == 1
    assert page.report_table.item(0, 1).text() == "EPUB 图片压缩"
    assert page.report_table.item(0, 3).text() == "成功"
    assert page.report_table.item(0, 4).text() == "1"
    assert "任务：1　成功：1　失败：0　警告：1　错误：0" == page.summary_label.text()

    page.report_table.selectRow(0)
    assert "输出：C:/books/source_optimized.epub" in page.details.toPlainText()
    assert "[cover_warning] 封面保持原样" in page.details.toPlainText()

    qtbot.mouseClick(page.clear_button, Qt.MouseButton.LeftButton)

    assert store.entries == ()
    assert page.report_table.rowCount() == 0
    assert page.summary_label.text() == "本次会话暂无报告。"


def test_report_page_renders_failure_count(qtbot) -> None:
    store = ReportStore()
    page = DetectionReportPage(store)
    qtbot.addWidget(page)

    store.add(
        ReportEntry(
            created_at=datetime(2026, 9, 22, 15, 0, 1),
            tool="EPUB 目录修复",
            source="C:/books/broken.epub",
            output=None,
            success=False,
            errors=("[archive_error] 输入 EPUB 已损坏",),
        )
    )

    assert page.report_table.item(0, 3).text() == "失败"
    assert page.report_table.item(0, 5).text() == "1"
    page.report_table.selectRow(0)
    assert "[archive_error] 输入 EPUB 已损坏" in page.details.toPlainText()
