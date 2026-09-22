from pathlib import Path

from ebook_toolbox.services.text_merge import (
    TextMergeRequest,
    format_plain_text,
    merge_author_texts,
)


def write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    path.write_text(content, encoding=encoding)


def test_format_plain_text_normalizes_invisible_characters_and_blank_lines() -> None:
    source = "\ufeff第一行  \r\n\u200b　　\r\n\r\n\r\n第二行\x00"

    assert format_plain_text(source) == "第一行\n\n第二行"


def test_merge_groups_authors_sorts_naturally_and_writes_utf8_bom(tmp_path: Path) -> None:
    write_text(tmp_path / "[甲] 10.txt", "十\n\n\n\n十续")
    write_text(tmp_path / "[甲] 2.txt", "二")
    write_text(tmp_path / "[乙] 一本.txt", "乙的内容", encoding="gb18030")
    write_text(tmp_path / "未匹配.txt", "跳过")

    result = merge_author_texts(TextMergeRequest(tmp_path))

    assert result.success
    assert result.source_count == 3
    assert result.skipped_count == 1
    assert {warning.code for warning in result.warnings} == {"unmatched_file_name"}
    merged_a = tmp_path / "EbookToolbox-合集" / "甲合集.txt"
    assert merged_a.read_bytes().startswith(b"\xef\xbb\xbf")
    text = merged_a.read_text(encoding="utf-8-sig")
    assert text.index("2") < text.index("10")
    assert "十\n\n十续" in text
    merged_b = tmp_path / "EbookToolbox-合集" / "乙合集.txt"
    assert merged_b.read_text(encoding="utf-8-sig").endswith("乙的内容\n")


def test_merge_does_not_overwrite_existing_output_without_opt_in(tmp_path: Path) -> None:
    write_text(tmp_path / "[甲] 一本.txt", "内容")
    output_directory = tmp_path / "out"
    output_directory.mkdir()
    output = output_directory / "甲合集.txt"
    write_text(output, "原输出")

    result = merge_author_texts(TextMergeRequest(tmp_path, output_directory=output_directory))

    assert not result.success
    assert output.read_text(encoding="utf-8") == "原输出"
    assert result.errors[0].code == "output_exists"


def test_merge_overwrite_replaces_existing_output(tmp_path: Path) -> None:
    write_text(tmp_path / "[甲] 一本.txt", "新内容")
    output_directory = tmp_path / "out"
    output_directory.mkdir()
    output = output_directory / "甲合集.txt"
    write_text(output, "旧内容")

    result = merge_author_texts(
        TextMergeRequest(tmp_path, output_directory=output_directory, overwrite=True)
    )

    assert result.success
    assert "新内容" in output.read_text(encoding="utf-8-sig")


def test_merge_reports_invalid_input_directory(tmp_path: Path) -> None:
    result = merge_author_texts(TextMergeRequest(tmp_path / "missing"))

    assert not result.success
    assert result.errors[0].code == "invalid_source_directory"
