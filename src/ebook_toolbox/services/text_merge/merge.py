"""Safe, non-interactive TXT formatting and author-grouped merging."""

from __future__ import annotations

import re
import tempfile
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from ebook_toolbox.services.text_write_safety import (
    OwnedTemporary,
    cleanup_temporary,
    freeze_path,
    publish_text,
    validate_output_plan,
)

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


def merge_author_texts(
    request: TextMergeRequest,
    *,
    progress_callback: Callable[[TextMergeProgress], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> TextMergeResult:
    """Merge ``[author] title.txt`` files into independently published UTF-8 files."""

    source_directory = freeze_path(request.source_directory)
    output_directory = (
        freeze_path(request.output_directory)
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
    source_paths = list(source_directory.glob("*.txt"))
    for path in source_paths:
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

    outputs = {
        author: output_directory / f"{_safe_file_name(author)}合集.txt" for author in grouped
    }
    try:
        plan = validate_output_plan(source_paths, outputs.values(), overwrite=request.overwrite)
    except OSError as exc:
        result.errors.append(
            TextMergeIssue(
                getattr(exc, "code", type(exc).__name__),
                str(exc),
                getattr(exc, "path", output_directory),
            )
        )
        completed = failed = result.planned_count
        report()
        return result
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
        output = outputs[author]

        sections: list[str] = []
        skipped_empty = 0
        read_failed = False
        for path in author_paths:
            report(path)
            try:
                body = format_plain_text(_read_text(path))
            except (OSError, UnicodeError) as exc:
                result.errors.append(TextMergeIssue(type(exc).__name__, str(exc), path))
                completed += 1
                failed += 1
                read_failed = True
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
            sections.append(
                f"＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝\n{title}\n＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝\n\n{body}"
            )
            completed += 1
            report()

        if read_failed:
            # A successful subset is not a complete author compilation.
            failed += len(sections)
            report()
            continue
        if not sections:
            result.warnings.append(
                TextMergeIssue("author_has_no_content", "该作者没有可写入内容", None)
            )
            continue
        owned_temporary: OwnedTemporary | None = None
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
                owned_temporary = OwnedTemporary.capture(temporary)
                temporary.write("\n\n\n".join(sections))
                temporary.write("\n")
            publish_text(owned_temporary, output, plan=plan)
        except OSError as exc:
            result.errors.append(
                TextMergeIssue(
                    getattr(exc, "code", type(exc).__name__), str(exc), getattr(exc, "path", output)
                )
            )
            failed += len(sections)
            report()
            continue
        finally:
            cleanup_warning = cleanup_temporary(owned_temporary)
            if cleanup_warning:
                result.warnings.append(
                    TextMergeIssue("temp_cleanup_failed", cleanup_warning, output)
                )
        result.files.append(MergedTextFile(author, output, len(sections), skipped_empty))
        succeeded += len(sections)
        report()
    return result


__all__ = ["format_plain_text", "merge_author_texts"]
