"""Safe, non-interactive TXT formatting and author-grouped merging."""

from __future__ import annotations

import os
import re
import tempfile
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from .models import (
    MergedTextFile,
    TextMergeIssue,
    TextMergeProgress,
    TextMergeRequest,
    TextMergeResult,
)

_AUTHOR_PATTERN = re.compile(r"^\[([^\]]+)\]")
_NUMBER_PATTERN = re.compile(r"(\d+)")
_ZERO_WIDTH_PATTERN = re.compile(r"[\x00\ufeff\u200b\u200c\u200d]")
_BLANK_LINE_PATTERN = re.compile(r"\n{3,}")


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8")
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le")
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be")
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return raw.decode("gb18030", errors="strict")


def format_plain_text(text: str) -> str:
    """Normalize line endings and invisible characters without changing prose."""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _ZERO_WIDTH_PATTERN.sub("", text)
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join("" if line.strip("\t \u3000") == "" else line for line in lines)
    return _BLANK_LINE_PATTERN.sub("\n\n", text).strip()


def _natural_sort_key(name: str) -> tuple[object, ...]:
    return tuple(
        int(part) if part.isdigit() else part.casefold() for part in _NUMBER_PATTERN.split(name)
    )


def _safe_file_name(name: str) -> str:
    invalid = '<>:"/\\|?*'
    sanitized = "".join(
        "_" if character in invalid or ord(character) < 32 else character for character in name
    )
    sanitized = sanitized.rstrip(". ").strip()
    return sanitized or "未命名作者"


def _publish_text(temporary: Path, output: Path, overwrite: bool) -> None:
    if overwrite:
        os.replace(temporary, output)
        return
    try:
        os.link(temporary, output)
    except FileExistsError:
        raise
    except OSError as exc:
        raise OSError(f"Atomic no-clobber publish failed for {output}: {exc}") from exc
    temporary.unlink()


def merge_author_texts(
    request: TextMergeRequest,
    *,
    progress_callback: Callable[[TextMergeProgress], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> TextMergeResult:
    """Merge ``[author] title.txt`` files into independently published UTF-8 files."""

    source_directory = Path(request.source_directory)
    output_directory = (
        Path(request.output_directory)
        if request.output_directory is not None
        else source_directory / "EbookToolbox-合集"
    )
    result = TextMergeResult(source_directory=source_directory, output_directory=output_directory)
    if not source_directory.is_dir():
        result.errors.append(
            TextMergeIssue(
                "invalid_source_directory", "输入目录不存在或不是文件夹", source_directory
            )
        )
        return result

    grouped: defaultdict[str, list[Path]] = defaultdict(list)
    for path in source_directory.glob("*.txt"):
        match = _AUTHOR_PATTERN.match(path.stem)
        if match is None:
            result.skipped_count += 1
            result.warnings.append(
                TextMergeIssue("unmatched_file_name", "文件名未以 [作者] 开头，已跳过", path)
            )
            continue
        author = match.group(1).strip()
        if not author:
            result.skipped_count += 1
            result.warnings.append(TextMergeIssue("empty_author", "作者名为空，已跳过", path))
            continue
        grouped[author].append(path)

    if not grouped:
        result.errors.append(
            TextMergeIssue(
                "no_matching_files", "没有找到文件名以 [作者] 开头的 TXT 文件", source_directory
            )
        )
        return result

    result.planned_count = sum(len(paths) for paths in grouped.values())
    completed = 0
    succeeded = 0
    failed = 0
    skipped = 0

    def report(current_source: Path | None = None) -> None:
        result.processed_count = completed
        result.failed_source_count = failed
        result.skipped_source_count = skipped
        if progress_callback is not None:
            progress_callback(
                TextMergeProgress(
                    result.planned_count,
                    completed,
                    succeeded,
                    failed,
                    skipped,
                    current_source,
                )
            )

    report()

    try:
        output_directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result.errors.append(TextMergeIssue(type(exc).__name__, str(exc), output_directory))
        return result

    for author in sorted(grouped, key=_natural_sort_key):
        if should_cancel is not None and should_cancel():
            result.cancelled = True
            break
        author_paths = sorted(grouped[author], key=lambda item: _natural_sort_key(item.name))
        output = output_directory / f"{_safe_file_name(author)}合集.txt"
        if output.exists() and not request.overwrite:
            result.errors.append(TextMergeIssue("output_exists", "输出文件已存在", output))
            completed += len(author_paths)
            failed += len(author_paths)
            report()
            continue

        sections: list[str] = []
        skipped_empty = 0
        for path in author_paths:
            report(path)
            try:
                body = format_plain_text(_read_text(path))
            except (OSError, UnicodeError) as exc:
                result.errors.append(TextMergeIssue(type(exc).__name__, str(exc), path))
                completed += 1
                failed += 1
                report()
                continue
            if not body:
                skipped_empty += 1
                completed += 1
                skipped += 1
                result.warnings.append(TextMergeIssue("empty_file", "文件内容为空，已跳过", path))
                report()
                continue
            title = _AUTHOR_PATTERN.sub("", path.stem, count=1).strip() or path.stem
            sections.append(f"＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝\n{title}\n＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝\n\n{body}")
            completed += 1
            report()

        if not sections:
            result.warnings.append(
                TextMergeIssue("author_has_no_content", "该作者没有可写入内容", None)
            )
            continue
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8-sig",
                newline="\r\n",
                suffix=".tmp",
                dir=output_directory,
                prefix=f".{_safe_file_name(author)}-",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write("\n\n\n".join(sections))
                temporary.write("\n")
            _publish_text(temporary_path, output, request.overwrite)
        except OSError as exc:
            result.errors.append(TextMergeIssue(type(exc).__name__, str(exc), output))
            failed += len(sections)
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            report()
            continue
        result.files.append(MergedTextFile(author, output, len(sections), skipped_empty))
        succeeded += len(sections)
        report()
    return result


__all__ = ["format_plain_text", "merge_author_texts"]
