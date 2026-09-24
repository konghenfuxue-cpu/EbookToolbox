from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt

from ebook_toolbox.services.epub_image_compressor import (
    HIGH_QUALITY,
    CompressionError,
    CompressionProgress,
    CompressionResult,
    CompressionWarning,
)
from ebook_toolbox.ui.pages import epub_image_compressor as page_module
from ebook_toolbox.ui.pages.epub_image_compressor import EpubImageCompressorPage


def test_page_initializes_with_safe_defaults(qtbot) -> None:
    page = EpubImageCompressorPage()
    qtbot.addWidget(page)

    assert page.objectName() == "epubImageCompressorPage"
    assert page.source_edit.text() == ""
    assert page.output_edit.text() == ""
    assert page.profile_combo.currentData() == "balanced"
    assert not page.include_cover_check.isChecked()
    assert page.start_button.isEnabled()
    assert not page.cancel_button.isEnabled()
    assert page.progress_bar.minimum() == 0
    assert page.progress_bar.maximum() == 1
    assert page.summary_label.text() == "总数：0　替换：0　跳过：0　失败：0　取消：0"
    assert page.output_result.isReadOnly()
    assert page.result_summary.isReadOnly()


def test_controls_convert_to_public_compression_request(qtbot, tmp_path) -> None:
    page = EpubImageCompressorPage()
    qtbot.addWidget(page)
    source = tmp_path / "source.epub"
    output = tmp_path / "output.epub"
    page.source_edit.setText(str(source))
    page.output_edit.setText(str(output))
    page.profile_combo.setCurrentIndex(page.profile_combo.findData("high_quality"))
    page.include_cover_check.setChecked(True)

    request = page.build_request()

    assert request.source == source
    assert request.output == output
    assert request.profile is HIGH_QUALITY
    assert request.include_cover
    assert not request.overwrite


def test_success_state_runs_in_background_and_restores_button(qtbot, tmp_path, monkeypatch) -> None:
    page = EpubImageCompressorPage()
    qtbot.addWidget(page)
    page.show()
    source = tmp_path / "source.epub"
    output = tmp_path / "output.epub"
    source.write_bytes(b"input")
    page.source_edit.setText(str(source))
    page.output_edit.setText(str(output))
    worker_started = threading.Event()
    release_worker = threading.Event()

    def successful_compression(request, **_kwargs):
        worker_started.set()
        assert release_worker.wait(timeout=5)
        return CompressionResult(
            source=request.source,
            output=request.output or output,
            success=True,
            original_bytes=2048,
            output_bytes=1024,
        )

    monkeypatch.setattr(page_module, "compress_epub", successful_compression)

    qtbot.mouseClick(page.start_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(worker_started.is_set, timeout=5000)
    assert not page.start_button.isEnabled()
    assert page.cancel_button.isEnabled()
    assert page.progress_bar.maximum() == 0

    with qtbot.waitSignal(page.compression_finished, timeout=5000) as completed:
        release_worker.set()

    result = completed.args[0]
    assert result.success
    assert page.start_button.isEnabled()
    assert not page.cancel_button.isEnabled()
    assert page.status_label.text() == "压缩完成。"
    assert page.output_result.text() == str(output)
    assert "原始大小：2.0 KiB" in page.result_summary.toPlainText()
    assert "节省空间：1.0 KiB" in page.result_summary.toPlainText()


def test_failure_state_renders_structured_errors_and_warnings(qtbot, tmp_path, monkeypatch) -> None:
    page = EpubImageCompressorPage()
    qtbot.addWidget(page)
    page.show()
    source = tmp_path / "source.epub"
    output = tmp_path / "output.epub"
    source.write_bytes(b"input")
    page.source_edit.setText(str(source))
    page.output_edit.setText(str(output))

    def failed_compression(request, **_kwargs):
        return CompressionResult(
            source=request.source,
            output=request.output or Path("unused.epub"),
            warnings=[CompressionWarning("cover_warning", "封面保持原样", Path("cover.jpg"))],
            errors=[CompressionError("ArchiveError", "输入 EPUB 已损坏")],
        )

    monkeypatch.setattr(page_module, "compress_epub", failed_compression)

    with qtbot.waitSignal(page.compression_finished, timeout=5000) as completed:
        qtbot.mouseClick(page.start_button, Qt.MouseButton.LeftButton)

    result = completed.args[0]
    summary = page.result_summary.toPlainText()
    assert not result.success
    assert page.start_button.isEnabled()
    assert page.status_label.text() == "压缩失败。"
    assert page.output_result.text() == ""
    assert "[cover_warning] (cover.jpg) 封面保持原样" in summary
    assert "[ArchiveError] 输入 EPUB 已损坏" in summary


def test_cancel_waits_for_safe_boundary_and_does_not_publish_output(
    qtbot, tmp_path, monkeypatch
) -> None:
    page = EpubImageCompressorPage()
    qtbot.addWidget(page)
    page.show()
    source = tmp_path / "source.epub"
    output = tmp_path / "output.epub"
    source.write_bytes(b"input")
    page.source_edit.setText(str(source))
    page.output_edit.setText(str(output))
    worker_started = threading.Event()
    release_stage = threading.Event()

    def cancellable_compression(request, *, progress_callback, should_cancel):
        image = Path("OEBPS/Images/page.jpg")
        progress_callback(CompressionProgress(2, 0, 0, 0, 0, image))
        worker_started.set()
        assert release_stage.wait(timeout=5)
        return CompressionResult(
            source=request.source,
            output=request.output or output,
            planned_count=2,
            processed_count=1,
            cancelled=should_cancel(),
        )

    monkeypatch.setattr(page_module, "compress_epub", cancellable_compression)

    qtbot.mouseClick(page.start_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(worker_started.is_set, timeout=5000)
    qtbot.waitUntil(lambda: page.progress_bar.maximum() == 2, timeout=1000)
    assert page.status_label.text().startswith("正在处理 1/2")

    qtbot.mouseClick(page.cancel_button, Qt.MouseButton.LeftButton)
    assert not page.cancel_button.isEnabled()
    assert page.status_label.text().startswith("正在取消")

    with qtbot.waitSignal(page.compression_finished, timeout=5000):
        release_stage.set()

    assert page.status_label.text() == (
        "图片压缩已取消；已处理 1 张，未处理 1 张，未发布输出。"
    )
    assert page.output_result.text() == ""
    assert "取消未处理：1 张" in page.result_summary.toPlainText()
    assert page.summary_label.text() == "总数：2　替换：0　跳过：0　失败：0　取消：1"

