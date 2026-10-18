"""Safe file-level wrapper around chapter-title normalization."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ebook_toolbox.services.text_merge.merge import _read_text
from ebook_toolbox.services.text_write_safety import (
    OwnedTemporary,
    cleanup_temporary,
    freeze_path,
    publish_text,
    validate_output_plan,
)

from .models import ChapterIssue, ChapterNormalizationResult
from .normalize import normalize_chapter_headings


@dataclass(frozen=True, slots=True)
class TextChapterRequest:
    source: Path
    output: Path | None = None
    overwrite: bool = False


@dataclass(frozen=True, slots=True)
class TextChapterProgress:
    stage: str
    completed: int
    total: int = 4


@dataclass(slots=True)
class TextChapterFileResult:
    source: Path
    output: Path | None
    normalized: ChapterNormalizationResult | None = None
    errors: list[ChapterIssue] = field(default_factory=list)
    completed_stages: int = 0
    total_stages: int = 4
    cancelled: bool = False

    @property
    def warnings(self) -> list[ChapterIssue]:
        return [] if self.normalized is None else self.normalized.warnings

    @property
    def success(self) -> bool:
        return self.output is not None and self.normalized is not None and not self.errors


class _TextChapterCancelled(Exception):
    pass


def normalize_text_file(
    request: TextChapterRequest,
    *,
    progress_callback: Callable[[TextChapterProgress], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> TextChapterFileResult:
    """Read one TXT and publish a separate normalized copy without touching source."""

    source = freeze_path(request.source)
    output = (
        freeze_path(request.output)
        if request.output
        else source.with_stem(f"{source.stem}（卷章版）")
    )
    result = TextChapterFileResult(source=source, output=output)

    def report(stage: str, completed: int) -> None:
        result.completed_stages = completed
        if progress_callback is not None:
            progress_callback(TextChapterProgress(stage, completed, result.total_stages))

    def check_cancelled() -> None:
        if should_cancel is not None and should_cancel():
            raise _TextChapterCancelled

    if source.suffix.casefold() != ".txt":
        result.output = None
        result.errors.append(ChapterIssue("invalid_source", "输入文件不存在或不是 TXT", 0))
        return result
    owned_temporary: OwnedTemporary | None = None
    published = False
    try:
        plan = validate_output_plan([source], [output], overwrite=request.overwrite)
        check_cancelled()
        report("reading", 0)
        text = _read_text(source)
        result.completed_stages = 1
        check_cancelled()
        report("normalizing", 1)
        normalized = normalize_chapter_headings(text)
        result.completed_stages = 2
        check_cancelled()
        report("writing", 2)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="",
            dir=output.parent,
            suffix=".tmp",
            delete=False,
        ) as temporary:
            owned_temporary = OwnedTemporary.capture(temporary)
            temporary.write(normalized.text)
        result.completed_stages = 3
        check_cancelled()
        report("publishing", 3)
        check_cancelled()
        publish_text(owned_temporary, output, plan=plan)
        published = True
        report("completed", 4)
    except _TextChapterCancelled:
        result.cancelled = True
        return result
    except (OSError, UnicodeError) as exc:
        if getattr(exc, "code", None) == "invalid_source":
            result.output = None
        result.errors.append(ChapterIssue(getattr(exc, "code", type(exc).__name__), str(exc), 0))
        return result
    finally:
        cleanup_warning = cleanup_temporary(owned_temporary)
        if cleanup_warning:
            issue = ChapterIssue("temp_cleanup_failed", cleanup_warning, 0)
            if published:
                normalized.warnings.append(issue)
            else:
                # A failed/cancelled conversion has no normalized result to carry warnings.
                # Keep its primary issue and explicitly report the preserved temporary.
                result.errors.append(issue)
    result.normalized = normalized
    return result


__all__ = [
    "TextChapterFileResult",
    "TextChapterProgress",
    "TextChapterRequest",
    "normalize_text_file",
]
