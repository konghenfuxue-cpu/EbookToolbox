"""Safe file-level wrapper around chapter-title normalization."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ebook_toolbox.services.text_merge.merge import _read_text

from .models import ChapterIssue, ChapterNormalizationResult
from .normalize import normalize_chapter_headings


@dataclass(frozen=True, slots=True)
class TextChapterRequest:
    source: Path
    output: Path | None = None
    overwrite: bool = False


@dataclass(slots=True)
class TextChapterFileResult:
    source: Path
    output: Path | None
    normalized: ChapterNormalizationResult | None = None
    errors: list[ChapterIssue] = field(default_factory=list)

    @property
    def warnings(self) -> list[ChapterIssue]:
        return [] if self.normalized is None else self.normalized.warnings

    @property
    def success(self) -> bool:
        return self.output is not None and self.normalized is not None and not self.errors


def normalize_text_file(request: TextChapterRequest) -> TextChapterFileResult:
    """Read one TXT and publish a separate normalized copy without touching source."""

    source = Path(request.source)
    output = (
        Path(request.output) if request.output else source.with_stem(f"{source.stem}（卷章版）")
    )
    result = TextChapterFileResult(source=source, output=output)
    if not source.is_file() or source.suffix.casefold() != ".txt":
        result.output = None
        result.errors.append(ChapterIssue("invalid_source", "输入文件不存在或不是 TXT", 0))
        return result
    if output.exists() and not request.overwrite:
        result.errors.append(ChapterIssue("output_exists", "输出文件已存在", 0))
        return result
    try:
        normalized = normalize_chapter_headings(_read_text(source))
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8-sig", newline="", dir=output.parent, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(normalized.text)
        if request.overwrite:
            os.replace(temporary_path, output)
        else:
            os.link(temporary_path, output)
            temporary_path.unlink()
    except (OSError, UnicodeError) as exc:
        if "temporary_path" in locals():
            temporary_path.unlink(missing_ok=True)
        result.errors.append(ChapterIssue(type(exc).__name__, str(exc), 0))
        return result
    result.normalized = normalized
    return result


__all__ = ["TextChapterFileResult", "TextChapterRequest", "normalize_text_file"]
