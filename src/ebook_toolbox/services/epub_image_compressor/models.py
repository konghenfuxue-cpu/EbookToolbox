"""Data models for the EPUB image compressor core."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Profile:
    """Image encoding limits for a compression run."""

    name: str
    quality: int
    max_long_edge: int
    min_bytes: int


BALANCED = Profile("balanced", quality=72, max_long_edge=2400, min_bytes=80 * 1024)
HIGH_QUALITY = Profile("high_quality", quality=82, max_long_edge=3000, min_bytes=120 * 1024)
SPACE_SAVER = Profile("space_saver", quality=58, max_long_edge=2000, min_bytes=60 * 1024)
PROFILES = {profile.name: profile for profile in (BALANCED, HIGH_QUALITY, SPACE_SAVER)}

MINIMUM_REDUCTION_RATIO = 0.05
MINIMUM_SAVINGS_BYTES = 8 * 1024


class ImageStatus(StrEnum):
    REPLACED = "replaced"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CompressionWarning:
    code: str
    message: str
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class CompressionError:
    code: str
    message: str
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class ImageResult:
    path: Path
    status: ImageStatus
    original_bytes: int
    output_bytes: int | None = None
    original_size: tuple[int, int] | None = None
    output_size: tuple[int, int] | None = None
    reason: str | None = None
    warnings: tuple[CompressionWarning, ...] = ()
    error: CompressionError | None = None

    @property
    def saved_bytes(self) -> int:
        if self.output_bytes is None or self.status is not ImageStatus.REPLACED:
            return 0
        return self.original_bytes - self.output_bytes


@dataclass(frozen=True, slots=True)
class CompressionRequest:
    source: Path
    output: Path | None = None
    profile: Profile = BALANCED
    include_cover: bool = False
    overwrite: bool = False


@dataclass(slots=True)
class CompressionResult:
    source: Path
    output: Path
    success: bool = False
    original_bytes: int = 0
    output_bytes: int = 0
    images: list[ImageResult] = field(default_factory=list)
    warnings: list[CompressionWarning] = field(default_factory=list)
    errors: list[CompressionError] = field(default_factory=list)

    @property
    def replaced_count(self) -> int:
        return sum(item.status is ImageStatus.REPLACED for item in self.images)

    @property
    def skipped_count(self) -> int:
        return sum(item.status is ImageStatus.SKIPPED for item in self.images)

    @property
    def failed_count(self) -> int:
        return sum(item.status is ImageStatus.FAILED for item in self.images)

    @property
    def saved_bytes(self) -> int:
        return self.original_bytes - self.output_bytes if self.success else 0
