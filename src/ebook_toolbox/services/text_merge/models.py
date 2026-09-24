"""Public models for safe TXT formatting and author-grouped merging."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TextMergeIssue:
    code: str
    message: str
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class TextMergeRequest:
    source_directory: Path
    output_directory: Path | None = None
    overwrite: bool = False


@dataclass(frozen=True, slots=True)
class TextMergeProgress:
    total: int
    completed: int
    succeeded: int
    failed: int
    skipped: int
    current_source: Path | None = None


@dataclass(frozen=True, slots=True)
class MergedTextFile:
    author: str
    output: Path
    source_count: int
    skipped_empty_count: int = 0


@dataclass(slots=True)
class TextMergeResult:
    source_directory: Path
    output_directory: Path
    files: list[MergedTextFile] = field(default_factory=list)
    skipped_count: int = 0
    warnings: list[TextMergeIssue] = field(default_factory=list)
    errors: list[TextMergeIssue] = field(default_factory=list)
    planned_count: int = 0
    processed_count: int = 0
    failed_source_count: int = 0
    skipped_source_count: int = 0
    cancelled: bool = False

    @property
    def success(self) -> bool:
        return bool(self.files) and not self.cancelled and not self.errors

    @property
    def cancelled_count(self) -> int:
        return max(0, self.planned_count - self.processed_count) if self.cancelled else 0

    @property
    def source_count(self) -> int:
        return sum(item.source_count for item in self.files)


__all__ = [
    "MergedTextFile",
    "TextMergeIssue",
    "TextMergeProgress",
    "TextMergeRequest",
    "TextMergeResult",
]
