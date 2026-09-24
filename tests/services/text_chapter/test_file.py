from pathlib import Path

from ebook_toolbox.services.text_chapter import TextChapterRequest, normalize_text_file


def test_normalize_text_file_writes_separate_utf8_bom_copy(tmp_path: Path) -> None:
    source = tmp_path / "小说.txt"
    source.write_text("第一卷 开始\n【第1章】 标题", encoding="gb18030")

    result = normalize_text_file(TextChapterRequest(source))

    assert result.success
    assert result.output == tmp_path / "小说（卷章版）.txt"
    assert result.output.read_bytes().startswith(b"\xef\xbb\xbf")
    assert result.output.read_text(encoding="utf-8-sig") == "第一卷 开始\n第1章 标题"
    assert source.read_text(encoding="gb18030") == "第一卷 开始\n【第1章】 标题"


def test_normalize_text_file_rejects_existing_output_by_default(tmp_path: Path) -> None:
    source = tmp_path / "小说.txt"
    source.write_text("第1章 标题", encoding="utf-8")
    output = tmp_path / "输出.txt"
    output.write_text("原内容", encoding="utf-8")

    result = normalize_text_file(TextChapterRequest(source, output))

    assert not result.success
    assert output.read_text(encoding="utf-8") == "原内容"
    assert result.errors[0].code == "output_exists"


def test_normalize_text_file_reports_real_stages(tmp_path: Path) -> None:
    source = tmp_path / "小说.txt"
    source.write_text("第一卷 开始\n第1章 标题", encoding="utf-8")
    updates = []

    result = normalize_text_file(
        TextChapterRequest(source), progress_callback=updates.append
    )

    assert result.success
    assert result.completed_stages == result.total_stages == 4
    assert [(update.stage, update.completed) for update in updates] == [
        ("reading", 0),
        ("normalizing", 1),
        ("writing", 2),
        ("publishing", 3),
        ("completed", 4),
    ]


def test_cancel_after_normalizing_does_not_create_output(tmp_path: Path) -> None:
    source = tmp_path / "小说.txt"
    source.write_text("第一卷 开始\n第1章 标题", encoding="utf-8")
    source_before = source.read_bytes()
    cancel_requested = False

    def record_progress(update) -> None:
        nonlocal cancel_requested
        if update.stage == "normalizing":
            cancel_requested = True

    result = normalize_text_file(
        TextChapterRequest(source),
        progress_callback=record_progress,
        should_cancel=lambda: cancel_requested,
    )

    assert result.cancelled
    assert not result.success
    assert result.completed_stages == 2
    assert source.read_bytes() == source_before
    assert not (tmp_path / "小说（卷章版）.txt").exists()


def test_cancel_after_writing_preserves_existing_output_and_cleans_temp(
    tmp_path: Path,
) -> None:
    source = tmp_path / "小说.txt"
    output = tmp_path / "输出.txt"
    source.write_text("第一卷 开始\n第1章 标题", encoding="utf-8")
    output.write_text("原内容", encoding="utf-8")
    cancel_requested = False

    def record_progress(update) -> None:
        nonlocal cancel_requested
        if update.stage == "writing":
            cancel_requested = True

    result = normalize_text_file(
        TextChapterRequest(source, output, overwrite=True),
        progress_callback=record_progress,
        should_cancel=lambda: cancel_requested,
    )

    assert result.cancelled
    assert result.completed_stages == 3
    assert output.read_text(encoding="utf-8") == "原内容"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["小说.txt", "输出.txt"]
