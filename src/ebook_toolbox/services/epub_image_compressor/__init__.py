"""Pure-Python core for compressing images in a single EPUB."""

from .compressor import compress_epub
from .models import (
    BALANCED,
    HIGH_QUALITY,
    PROFILES,
    SPACE_SAVER,
    CompressionError,
    CompressionProgress,
    CompressionRequest,
    CompressionResult,
    CompressionWarning,
    ImageResult,
    ImageStatus,
    Profile,
)

__all__ = [
    "BALANCED",
    "HIGH_QUALITY",
    "PROFILES",
    "SPACE_SAVER",
    "CompressionError",
    "CompressionProgress",
    "CompressionRequest",
    "CompressionResult",
    "CompressionWarning",
    "ImageResult",
    "ImageStatus",
    "Profile",
    "compress_epub",
]
