import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt

from ebook_toolbox.ui.pages import detection_report as page_module
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


def add_report(store: ReportStore, **changes) -> ReportEntry:
    values = {
        "created_at": datetime(2026, 10, 13, 15, 0, 1),
        "tool": "EPUB 综合修复",
        "source": "D:/中文 图书/原稿.epub",
        "output": "D:/中文 图书/原稿_修复.epub",
        "success": True,
    }
    values.update(changes)
    entry = ReportEntry(**values)
    store.add(entry)
    return entry


def test_report_details_show_what_was_checked_without_claiming_standard_validation(qtbot) -> None:
    store = ReportStore()
    page = DetectionReportPage(store)
    qtbot.addWidget(page)
    add_report(
        store,
        checks=(
            ("internal_structure", "passed"),
            ("calibre_metadata", "not_installed"),
            ("epubcheck", "not_run"),
        ),
    )

    page.report_table.selectRow(0)

    details = page.details.toPlainText()
    assert "内部结构检查：通过" in details
    assert "Calibre 元数据读取：未安装" in details
    assert "EPUBCheck 标准校验：未运行" in details


def test_report_details_do_not_translate_unknown_check_status_as_passed(qtbot) -> None:
    store = ReportStore()
    page = DetectionReportPage(store)
    qtbot.addWidget(page)
    add_report(store, checks=(("calibre_metadata", "unexpected_status"),))

    page.report_table.selectRow(0)

    assert "Calibre 元数据读取：unexpected_status" in page.details.toPlainText()


def test_export_is_disabled_for_empty_session_and_after_clear(qtbot) -> None:
    store = ReportStore()
    page = DetectionReportPage(store)
    qtbot.addWidget(page)

    assert not page.export_button.isEnabled()
    add_report(store)
    assert page.export_button.isEnabled()
    store.clear()
    assert not page.export_button.isEnabled()


def test_export_button_writes_current_reports_to_new_chinese_path(
    qtbot, tmp_path, monkeypatch
) -> None:
    store = ReportStore()
    page = DetectionReportPage(store)
    qtbot.addWidget(page)
    entry = add_report(store, checks=(("epubcheck", "not_run"),))
    folder = tmp_path / "中文 空格"
    folder.mkdir()
    destination = folder / "检测 报告.json"
    monkeypatch.setattr(
        page_module.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(destination), "JSON 报告 (*.json)"),
    )

    qtbot.mouseClick(page.export_button, Qt.MouseButton.LeftButton)

    data = json.loads(destination.read_text(encoding="utf-8"))
    assert data["scope"] == "current_session"
    assert data["entries"][0]["source"] == "D:/中文 图书/原稿.epub"
    assert data["entries"][0]["checks"] == {"epubcheck": "not_run"}
    assert store.entries == (entry,)
    assert "已导出" in page.export_status_label.text()
    assert str(destination) in page.export_status_label.text()


def test_cancel_export_leaves_directory_and_reports_unchanged(qtbot, tmp_path, monkeypatch) -> None:
    store = ReportStore()
    page = DetectionReportPage(store)
    qtbot.addWidget(page)
    entry = add_report(store)
    monkeypatch.setattr(
        page_module.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: ("", ""),
    )
    monkeypatch.chdir(tmp_path)

    qtbot.mouseClick(page.export_button, Qt.MouseButton.LeftButton)

    assert list(tmp_path.iterdir()) == []
    assert store.entries == (entry,)
    assert "已导出" not in page.export_status_label.text()


def test_existing_export_path_is_rejected_visibly_without_changing_reports(
    qtbot, tmp_path, monkeypatch
) -> None:
    store = ReportStore()
    page = DetectionReportPage(store)
    qtbot.addWidget(page)
    destination = tmp_path / "中文 原稿.epub"
    destination.write_bytes(b"original ebook bytes")
    entry = add_report(store, source=str(destination))
    monkeypatch.setattr(
        page_module.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(destination), ""),
    )

    qtbot.mouseClick(page.export_button, Qt.MouseButton.LeftButton)

    assert destination.read_bytes() == b"original ebook bytes"
    assert store.entries == (entry,)
    message = page.export_status_label.text()
    assert "已存在" in message
    assert "新文件名" in message
    assert "已导出" not in message


def test_export_write_failure_is_visible_and_keeps_reports(qtbot, tmp_path, monkeypatch) -> None:
    store = ReportStore()
    page = DetectionReportPage(store)
    qtbot.addWidget(page)
    entry = add_report(store)
    destination = tmp_path / "写入失败.json"
    monkeypatch.setattr(
        page_module.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(destination), ""),
    )
    real_open = Path.open

    class FailingWriter:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def write(self, text):
            self.stream.write(text[:10])
            raise OSError("模拟写入中途失败")

        def __exit__(self, *_exc):
            self.stream.close()

    def failing_open(path, *args, **kwargs):
        stream = real_open(path, *args, **kwargs)
        return FailingWriter(stream) if path == destination else stream

    monkeypatch.setattr(Path, "open", failing_open)

    qtbot.mouseClick(page.export_button, Qt.MouseButton.LeftButton)

    assert destination.stat().st_size > 0
    assert store.entries == (entry,)
    assert "导出失败" in page.export_status_label.text()
    assert "模拟写入中途失败" in page.export_status_label.text()
    assert "不完整报告" in page.export_status_label.text()
    assert "已导出" not in page.export_status_label.text()
