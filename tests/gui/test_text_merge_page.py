from pathlib import Path

from ebook_toolbox.ui.pages.text_merge import TextMergePage


def test_build_request_requires_source_directory(qtbot) -> None:
    page = TextMergePage()
    qtbot.addWidget(page)

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
