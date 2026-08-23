"""Structured public models for EPUB structural repair."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RenamedResource:
    original_path: str
    repaired_path: str


@dataclass(frozen=True, slots=True)
class RepairWarning:
    code: str
    message: str
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class RepairError:
    code: str
    message: str
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class RepairRequest:
    source: Path
    output: Path | None = None
    overwrite: bool = False
    reference: Path | None = None
    run_calibre_check: bool = False


@dataclass(slots=True)
class RepairResult:
    source: Path
    output: Path
    success: bool = False
    renamed_resources: list[RenamedResource] = field(default_factory=list)
    added_manifest_resources: list[str] = field(default_factory=list)
    removed_manifest_items: list[str] = field(default_factory=list)
    removed_spine_items: list[str] = field(default_factory=list)
    restored_resources: list[str] = field(default_factory=list)
    language_added: bool = False
    calibre_status: str | None = None
    file_count: int = 0
    manifest_count: int = 0
    spine_count: int = 0
    warnings: list[RepairWarning] = field(default_factory=list)
    errors: list[RepairError] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class BatchRepairRequest:
    inputs: tuple[Path, ...]
    output_directory: Path | None = None
    recursive: bool = False
    overwrite: bool = False
    reference: Path | None = None
    run_calibre_check: bool = False


@dataclass(frozen=True, slots=True)
class FileRepairResult:
    source: Path
    output: Path
    result: RepairResult

    @property
    def success(self) -> bool:
        return self.result.success

    @property
    def warnings(self) -> list[RepairWarning]:
        return self.result.warnings

    @property
    def errors(self) -> list[RepairError]:
        return self.result.errors


@dataclass(slots=True)
class BatchRepairResult:
    files: list[FileRepairResult] = field(default_factory=list)
    warnings: list[RepairWarning] = field(default_factory=list)
    errors: list[RepairError] = field(default_factory=list)

    @property
    def succeeded_count(self) -> int:
        return sum(item.success for item in self.files)

    @property
    def failed_count(self) -> int:
        return sum(not item.success for item in self.files)

    @property
    def success(self) -> bool:
        return not self.errors and self.failed_count == 0
