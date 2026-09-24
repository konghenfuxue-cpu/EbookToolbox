"""TXT formatting and author-grouped merge service."""

from .merge import format_plain_text, merge_author_texts
from .models import (
    MergedTextFile,
    TextMergeIssue,
    TextMergeProgress,
    TextMergeRequest,
    TextMergeResult,
)

__all__ = [
    "MergedTextFile",
    "TextMergeIssue",
    "TextMergeProgress",
    "TextMergeRequest",
    "TextMergeResult",
    "format_plain_text",
    "merge_author_texts",
]
