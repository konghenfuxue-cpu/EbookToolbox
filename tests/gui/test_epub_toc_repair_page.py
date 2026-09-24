from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer

from ebook_toolbox.services.epub_toc_repair import (
    TocEntry,
    TocRepairError,
    TocRepairProgress,
    TocRepairResult,
    TocRepairWarning,
)
from ebook_toolbox.ui.pages import epub_toc_repair as page_module
from ebook_toolbox.ui.pages.epub_toc_repair import EpubTocRepairPage


def test_page_initializes_with_safe_defaults(qtbot) -> None:
    page = EpubTocRepairPage()
    qtbot.addWidget(page)

    assert page.objectName() == "epubTocRepairPage"
    assert page.source_edit.text() == ""
    assert page.output_edit.text() == ""
    assert page.replace_existing_check.isChecked()
    assert page.start_button.isEnabled()
    assert not page.cancel_button.isEnabled()
    assert page.progress_bar.minimum() == 0
    assert page.progress_bar.maximum() == 1
    assert page.output_result.isReadOnly()
    assert page.result_summary.isReadOnly()


def test_source_change_suggests_core_default_output(qtbot, tmp_path) -> None:
    page = EpubTocRepairPage()
    qtbot.addWidget(page)
    source = tmp_path / "合集.epub"

    page.source_edit.setText(str(source))

    assert page.output_edit.text() == str(tmp_path / "合集_toc_fixed.epub")
    assert page.build_request().output is None

    custom_output = tmp_path / "自定义.epub"
    page.output_edit.setText(str(custom_output))
    page.source_edit.setText(str(tmp_path / "另一本.epub"))
    assert page.output_edit.text() == str(custom_output)
    assert page.build_request().output == custom_output


def test_replace_existing_control_maps_to_public_request(qtbot, tmp_path) -> None:
    page = EpubTocRepairPage()
    qtbot.addWidget(page)
    source = tmp_path / "source.epub"
    page.source_edit.setText(str(source))
    page.replace_existing_check.setChecked(False)

    request = page.build_request()

    assert request.source == source
    assert request.output is None
    assert not request.replace_existing
    assert not request.overwrite


def test_missing_source_cannot_start_worker(qtbot, monkeypatch) -> None:
    page = EpubTocRepairPage()
    qtbot.addWidget(page)
    called = False

    def unexpected_call(_request):
        nonlocal called
        called = True
        raise AssertionError("service must not be called")

    monkeypatch.setattr(page_module, "repair_epub_toc", unexpected_call)

    page.start_repair()

    assert not called
    assert page._worker is None
    assert page.status_label.text() == "无法开始修复。"
    assert "[input_error] 请选择 EPUB 文件" in page.result_summary.toPlainText()


def test_background_success_disables_controls_then_renders_result(
    qtbot, tmp_path, monkeypatch
) -> None:
    page = EpubTocRepairPage()
    qtbot.addWidget(page)
    page.show()
    source = tmp_path / "source.epub"
    output = tmp_path / "source_toc_fixed.epub"
    source.write_bytes(b"input")
    page.source_edit.setText(str(source))
    worker_started = threading.Event()
    release_worker = threading.Event()
    main_thread = threading.get_ident()

    def successful_repair(request, **kwargs):
        assert threading.get_ident() != main_thread
        assert request.output is None
        kwargs["progress_callback"](TocRepairProgress("analyzing", 1, 5))
        worker_started.set()
        assert release_worker.wait(timeout=5)
        return TocRepairResult(
            source=request.source,
            output=output,
            success=True,
            entries=[
                TocEntry(
                    "第一卷",
                    "Text/v1.xhtml",
                    (
                        TocEntry("第一章", "Text/c1.xhtml"),
                        TocEntry("第二章", "Text/c2.xhtml"),
                    ),
                ),
                TocEntry(
                    "第二卷",
                    "Text/v2.xhtml",
                    (TocEntry("第一章", "Text/c3.xhtml"),),
                ),
            ],
            modified_files=[Path("OEBPS/toc.ncx"), Path("OEBPS/nav.xhtml")],
            warnings=[
                TocRepairWarning("used_visible_toc", "从可见目录恢复", Path("Text/toc.xhtml"))
            ],
        )

    monkeypatch.setattr(page_module, "repair_epub_toc", successful_repair)

    qtbot.mouseClick(page.start_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(worker_started.is_set, timeout=5000)
    assert page._worker is not None
    assert page.progress_bar.maximum() == 5
    assert page.cancel_button.isEnabled()
    for control in (
        page.source_edit,
        page.source_button,
        page.output_edit,
        page.output_button,
        page.replace_existing_check,
        page.start_button,
    ):
        assert not control.isEnabled()

    gui_responded = threading.Event()
    QTimer.singleShot(0, gui_responded.set)
    qtbot.waitUntil(gui_responded.is_set, timeout=1000)

    with qtbot.waitSignal(page.repair_finished, timeout=5000) as completed:
        release_worker.set()

    assert completed.args[0].success
    assert page._worker is None
    assert not page.cancel_button.isEnabled()
    for control in (
        page.source_edit,
        page.source_button,
        page.output_edit,
        page.output_button,
        page.replace_existing_check,
        page.start_button,
    ):
        assert control.isEnabled()
    assert page.status_label.text() == "目录修复完成。"
    assert page.output_result.text() == str(output)
    summary = page.result_summary.toPlainText()
    assert "识别卷数：2" in summary
    assert "识别章节数：3" in summary
    assert "NCX：已更新" in summary
    assert "EPUB3 nav：已更新" in summary
    assert f"[used_visible_toc] ({Path('Text/toc.xhtml')}) 从可见目录恢复" in summary


def test_failure_renders_structured_errors_and_warnings(qtbot, tmp_path, monkeypatch) -> None:
    page = EpubTocRepairPage()
    qtbot.addWidget(page)
    page.show()
    source = tmp_path / "source.epub"
    source.write_bytes(b"input")
    page.source_edit.setText(str(source))

    def failed_repair(request, **_kwargs):
        return TocRepairResult(
            source=request.source,
            output=tmp_path / "source_toc_fixed.epub",
            warnings=[TocRepairWarning("existing_toc_unusable", "原目录不可用")],
            errors=[TocRepairError("chapters_not_recognized", "无法可靠识别章节")],
        )

    monkeypatch.setattr(page_module, "repair_epub_toc", failed_repair)

    with qtbot.waitSignal(page.repair_finished, timeout=5000) as completed:
        qtbot.mouseClick(page.start_button, Qt.MouseButton.LeftButton)

    assert not completed.args[0].success
    assert page._worker is None
    assert page.start_button.isEnabled()
    assert page.source_edit.isEnabled()
    assert page.status_label.text() == "目录修复失败。"
    assert page.output_result.text() == ""
    summary = page.result_summary.toPlainText()
    assert "[existing_toc_unusable] 原目录不可用" in summary
    assert "[chapters_not_recognized] 无法可靠识别章节" in summary


def test_cancel_requests_cooperative_stop_and_renders_cancelled_result(
    qtbot, tmp_path, monkeypatch
) -> None:
    page = EpubTocRepairPage()
    qtbot.addWidget(page)
    page.show()
    source = tmp_path / "source.epub"
    source.write_bytes(b"input")
    page.source_edit.setText(str(source))
    safe_stage = threading.Event()

    def cancellable_repair(request, *, progress_callback, should_cancel):
        progress_callback(TocRepairProgress("analyzing", 1, 5))
        safe_stage.set()
        while not should_cancel():
            threading.Event().wait(0.01)
        return TocRepairResult(
            source=request.source,
            output=tmp_path / "source_toc_fixed.epub",
            completed_stages=2,
            cancelled=True,
        )

    monkeypatch.setattr(page_module, "repair_epub_toc", cancellable_repair)

    qtbot.mouseClick(page.start_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(safe_stage.is_set, timeout=5000)
    assert page.cancel_button.isEnabled()
    qtbot.mouseClick(page.cancel_button, Qt.MouseButton.LeftButton)

    with qtbot.waitSignal(page.repair_finished, timeout=5000) as completed:
        pass

    assert completed.args[0].cancelled
    assert not page.cancel_button.isEnabled()
    assert page.progress_bar.value() == 2
    assert "已完成 2/5 个阶段" in page.status_label.text()
    assert "原文件和已有输出未被修改" in page.result_summary.toPlainText()
