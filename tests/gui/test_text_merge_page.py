import threading
from pathlib import Path

from PySide6.QtCore import Qt

from ebook_toolbox.services.text_merge import MergedTextFile, TextMergeProgress, TextMergeResult
from ebook_toolbox.ui.pages import text_merge as page_module
from ebook_toolbox.ui.pages.text_merge import TextMergePage


def test_build_request_requires_source_directory(qtbot) -> None:
    page = TextMergePage()
    qtbot.addWidget(page)

    assert not page.cancel_button.isEnabled()
    assert page.merge_summary_label.text() == "总数：0　成功：0　失败：0　跳过：0　取消：0"

    try:
        page.build_request()
    except ValueError as exc:
        assert str(exc) == "请选择输入文件夹"
    else:  # pragma: no cover - protects the test assertion itself
        raise AssertionError("Expected a missing input error")


def test_result_renders_summary_and_issues(qtbot, tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "[甲] 一本.txt").write_text("正文", encoding="utf-8")
    (source / "未匹配.txt").write_text("跳过", encoding="utf-8")
    page = TextMergePage()
    qtbot.addWidget(page)
    page.source_edit.setText(str(source))

    with qtbot.waitSignal(page.merge_finished, timeout=5_000):
        page.start_merge()

    assert page.status_label.text() == "TXT 合并完成。"
    summary = page.result_summary.toPlainText()
    assert "生成合集：1 个" in summary
    assert "合并 TXT：1 个" in summary
    assert "[unmatched_file_name]" in summary
    assert (source / "EbookToolbox-合集" / "甲合集.txt").is_file()


def test_chapter_result_writes_copy_and_renders_counts(qtbot, tmp_path: Path) -> None:
    source = tmp_path / "小说.txt"
    source.write_text("第一卷 开始\n第1章 标题", encoding="utf-8")
    page = TextMergePage()
    qtbot.addWidget(page)
    page.chapter_source_edit.setText(str(source))

    with qtbot.waitSignal(page.chapter_finished, timeout=5_000):
        page.start_chapter_normalization()

    assert "识别卷：1" in page.chapter_summary.toPlainText()
    assert (tmp_path / "小说（卷章版）.txt").is_file()


def test_merge_cancel_waits_for_current_author_and_renders_remaining_count(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    page = TextMergePage()
    qtbot.addWidget(page)
    page.show()
    page.source_edit.setText(str(source))
    worker_started = threading.Event()
    release_author = threading.Event()

    def cancellable_merge(request, *, progress_callback, should_cancel):
        first = source / "[甲] 一本.txt"
        progress_callback(TextMergeProgress(2, 0, 0, 0, 0, first))
        worker_started.set()
        assert release_author.wait(timeout=5)
        output_directory = source / "EbookToolbox-合集"
        output = output_directory / "甲合集.txt"
        result = TextMergeResult(
            source_directory=source,
            output_directory=output_directory,
            files=[MergedTextFile("甲", output, 1)],
            planned_count=2,
            processed_count=1,
            cancelled=should_cancel(),
        )
        return result

    monkeypatch.setattr(page_module, "merge_author_texts", cancellable_merge)

    qtbot.mouseClick(page.start_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(worker_started.is_set, timeout=5000)
    qtbot.waitUntil(lambda: page.progress_bar.maximum() == 2, timeout=1000)
    assert page.cancel_button.isEnabled()
    assert page.status_label.text().startswith("正在处理 1/2")

    qtbot.mouseClick(page.cancel_button, Qt.MouseButton.LeftButton)
    assert not page.cancel_button.isEnabled()
    assert page.status_label.text().startswith("正在取消")

    with qtbot.waitSignal(page.merge_finished, timeout=5000):
        release_author.set()

    assert page.status_label.text() == "TXT 合并已取消；已处理 1 个，未处理 1 个。"
    assert "取消未处理：1 个" in page.result_summary.toPlainText()
    assert page.progress_bar.maximum() == 2
    assert page.progress_bar.value() == 1
    assert page.merge_summary_label.text() == "总数：2　成功：1　失败：0　跳过：0　取消：1"
