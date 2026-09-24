"""Structured public models for EPUB table-of-contents repair."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TocEntry:
    """One navigation entry, with optional nested child entries."""

    label: str
    href: str
    children: tuple[TocEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class TocRepairWarning:
    """A non-fatal condition encountered during repair."""

    code: str
    message: str
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class TocRepairError:
    """A fatal condition that prevented publication."""

    code: str
    message: str
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class TocRepairRequest:
    """Options for repairing one EPUB.

    ``replace_existing=False`` preserves an existing, usable primary TOC. If the
    format-required TOC is absent or unusable, repair still proceeds and records a
    warning because there is nothing safe to preserve.
    """

    source: Path
    output: Path | None = None
    overwrite: bool = False
    replace_existing: bool = True


@dataclass(frozen=True, slots=True)
class TocRepairProgress:
    stage: str
    completed: int
    total: int = 5


@dataclass(slots=True)
class TocRepairResult:
    """Structured outcome of a single-book TOC repair."""

    source: Path
    output: Path
    success: bool = False
    entries: list[TocEntry] = field(default_factory=list)
    modified_files: list[Path] = field(default_factory=list)
    warnings: list[TocRepairWarning] = field(default_factory=list)
    errors: list[TocRepairError] = field(default_factory=list)
    completed_stages: int = 0
    total_stages: int = 5
    cancelled: bool = False

