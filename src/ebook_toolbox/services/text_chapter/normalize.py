"""Conservative recognition of common Chinese TXT volume and chapter headings."""

from __future__ import annotations

import re

from .models import ChapterIssue, ChapterNormalizationResult

_NUMBER_CHARS = "一二三四五六七八九十百千万零〇两0-9"
_VOLUME_PATTERN = re.compile(rf"^\s*(?:=+\s*)?(第\s*[{_NUMBER_CHARS}]+\s*卷)(?:\s+(.+?))?\s*=*\s*$")
_CHAPTER_PATTERN = re.compile(
    rf"^\s*第\s*([{_NUMBER_CHARS}]+)\s*([章回节])(?:\s*[：:]\s*|\s+)?(.*?)\s*$"
)
_BRACKETED_CHAPTER_PATTERN = re.compile(
    r"^\s*【\s*(?:第|都)?\s*([0-9]{1,6})\s*章?\s*】\s*(.*?)\s*$"
)
_SPECIAL_PATTERN = re.compile(
    r"^\s*(序章|楔子|前言|后记|尾声|终章|番外)(?:\s*[：:]\s*|\s+)?(.*?)\s*$"
)
_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}


def chinese_number_to_int(value: str) -> int | None:
    """Return a positive integer for Arabic or conventional Chinese numerals."""

    compact = re.sub(r"\s+", "", value)
    if compact.isascii() and compact.isdigit():
        return int(compact)
    if not compact or any(
        character not in _DIGITS and character not in _UNITS for character in compact
    ):
        return None
    total = section = 0
    digit: int | None = None
    for character in compact:
        if character in _DIGITS:
            digit = _DIGITS[character]
            continue
        unit = _UNITS[character]
        current = 1 if digit is None else digit
        if unit == 10000:
            total += (section + (0 if digit is None else digit)) * unit
            section = 0
        else:
            section += current * unit
        digit = None
    return total + section + (0 if digit is None else digit)


def _canonical(title: str, prefix: str) -> str:
    title = title.strip()
    return f"{prefix} {title}" if title else prefix


def normalize_chapter_headings(text: str) -> ChapterNormalizationResult:
    """Normalize only high-confidence headings and report chapter sequence anomalies.

    This function deliberately does not infer headings from ordinary prose.  It is
    a foundation for the later TXT-to-EPUB workflow and is safe to run repeatedly.
    """

    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    result = ChapterNormalizationResult(text="")
    output: list[str] = []
    previous_chapter: int | None = None

    for index, line in enumerate(lines, start=1):
        volume = _VOLUME_PATTERN.match(line)
        if volume:
            volume_prefix = re.sub(r"\s+", "", volume.group(1))
            output.append(_canonical(volume.group(2) or "", volume_prefix))
            result.volume_count += 1
            previous_chapter = None
            continue

        bracketed = _BRACKETED_CHAPTER_PATTERN.match(line)
        chapter = _CHAPTER_PATTERN.match(line)
        number: int | None = None
        normalized: str | None = None
        if bracketed:
            number = int(bracketed.group(1))
            normalized = _canonical(bracketed.group(2), f"第{number}章")
        elif chapter:
            number = chinese_number_to_int(chapter.group(1))
            if number is not None:
                normalized = _canonical(
                    chapter.group(3), f"第{chapter.group(1).strip()}{chapter.group(2)}"
                )
        if normalized is not None and number is not None:
            if previous_chapter is not None:
                if number == previous_chapter:
                    result.warnings.append(
                        ChapterIssue("duplicate_chapter", f"出现重复章号 {number}", index)
                    )
                elif number == 1 and previous_chapter > 1:
                    result.warnings.append(
                        ChapterIssue("chapter_reset", "章号重置为 1，已开始新分段", index)
                    )
                elif number < previous_chapter:
                    result.warnings.append(
                        ChapterIssue(
                            "chapter_decrease", f"章号倒退：{previous_chapter} → {number}", index
                        )
                    )
                elif number > previous_chapter + 1:
                    result.warnings.append(
                        ChapterIssue(
                            "missing_chapter",
                            f"缺少章号：{previous_chapter + 1} 至 {number - 1}",
                            index,
                        )
                    )
            output.append(normalized)
            result.chapter_count += 1
            previous_chapter = number
            continue

        special = _SPECIAL_PATTERN.match(line)
        if special:
            output.append(_canonical(special.group(2), special.group(1)))
            result.special_heading_count += 1
            continue
        output.append(line)

    result.text = newline.join(output)
    return result


__all__ = ["chinese_number_to_int", "normalize_chapter_headings"]
