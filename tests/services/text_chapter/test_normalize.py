from ebook_toolbox.services.text_chapter import chinese_number_to_int, normalize_chapter_headings


def test_converts_common_chinese_numbers() -> None:
    assert chinese_number_to_int("十二") == 12
    assert chinese_number_to_int("十万") == 100000
    assert chinese_number_to_int("一百零二") == 102
    assert chinese_number_to_int("两千零三") == 2003
    assert chinese_number_to_int("1004") == 1004
    assert chinese_number_to_int("甲") is None


def test_normalizes_standard_volumes_chapters_and_special_headings() -> None:
    source = "=== 第一卷   初入江湖 ===\n第 一 章： 开始\n【第2章】 继续\n终章： 完结"

    result = normalize_chapter_headings(source)

    assert result.text == "第一卷 初入江湖\n第一章 开始\n第2章 继续\n终章 完结"
    assert (result.volume_count, result.chapter_count, result.special_heading_count) == (1, 2, 1)
    assert not result.warnings


def test_preserves_prose_and_reports_sequence_anomalies() -> None:
    source = "正文 第三章 不是标题\n第1章 起点\n第3章 跳过\n第3章 重复\n第1章 新卷"

    result = normalize_chapter_headings(source)

    assert result.text.startswith("正文 第三章 不是标题\n第1章 起点")
    assert [warning.code for warning in result.warnings] == [
        "missing_chapter",
        "duplicate_chapter",
        "chapter_reset",
    ]


def test_keeps_original_newline_style() -> None:
    result = normalize_chapter_headings("第1章 标题\r\n正文")

    assert result.text == "第1章 标题\r\n正文"
