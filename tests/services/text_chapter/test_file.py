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
