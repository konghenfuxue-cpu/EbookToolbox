"""Models returned by the conservative TXT chapter-title normalizer."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ChapterIssue:
    code: str
    message: str
    line_number: int


@dataclass(slots=True)
class ChapterNormalizationResult:
    text: str
    volume_count: int = 0
    chapter_count: int = 0
    special_heading_count: int = 0
    warnings: list[ChapterIssue] = field(default_factory=list)

    @property
    def heading_count(self) -> int:
        return self.volume_count + self.chapter_count + self.special_heading_count


__all__ = ["ChapterIssue", "ChapterNormalizationResult"]
