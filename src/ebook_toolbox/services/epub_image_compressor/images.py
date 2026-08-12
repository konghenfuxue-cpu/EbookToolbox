"""Image inspection and encoding without archive or user-interface concerns."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from .models import (
    MINIMUM_REDUCTION_RATIO,
    MINIMUM_SAVINGS_BYTES,
    CompressionError,
    CompressionWarning,
    ImageResult,
    ImageStatus,
    Profile,
)

SUPPORTED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
PASSTHROUGH_EXTENSIONS = frozenset({".gif"})
_EXPECTED_FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}


@dataclass(frozen=True, slots=True)
class EncodedImage:
    data: bytes | None
    result: ImageResult


def _resized(image: Image.Image, max_long_edge: int) -> Image.Image:
    long_edge = max(image.size)
    if long_edge <= max_long_edge:
        return image
    scale = max_long_edge / long_edge
    return image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )


def _flatten_for_jpeg(image: Image.Image) -> Image.Image:
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        return Image.alpha_composite(background, rgba).convert("RGB")
    return image.convert("RGB")


def encode_candidate(
    path: Path, profile: Profile, *, display_path: Path | None = None
) -> EncodedImage:
    """Inspect and encode one supported static image, without changing *path*."""

    shown_path = display_path or path
    old_size = path.stat().st_size
    if old_size < profile.min_bytes:
        return EncodedImage(
            None,
            ImageResult(shown_path, ImageStatus.SKIPPED, old_size, reason="below_min_bytes"),
        )

    try:
        with Image.open(path) as source:
            expected = _EXPECTED_FORMATS.get(path.suffix.lower())
            if source.format != expected:
                warning = CompressionWarning(
                    "format_mismatch",
                    f"File extension expects {expected}, but image is {source.format}.",
                    shown_path,
                )
                return EncodedImage(
                    None,
                    ImageResult(
                        shown_path,
                        ImageStatus.SKIPPED,
                        old_size,
                        reason="format_mismatch",
                        warnings=(warning,),
                    ),
                )
            if getattr(source, "n_frames", 1) > 1:
                warning = CompressionWarning(
                    "animated_image_skipped", "Multi-frame PNG/WebP was left unchanged.", shown_path
                )
                return EncodedImage(
                    None,
                    ImageResult(
                        shown_path,
                        ImageStatus.SKIPPED,
                        old_size,
                        reason="multiple_frames",
                        warnings=(warning,),
                    ),
                )
            source.load()
            image = ImageOps.exif_transpose(source)
            original_dimensions = image.size
            image = _resized(image, profile.max_long_edge)
            output_dimensions = image.size
            output = io.BytesIO()
            extension = path.suffix.lower()
            if extension in {".jpg", ".jpeg"}:
                image = _flatten_for_jpeg(image)
                image.save(
                    output,
                    "JPEG",
                    quality=profile.quality,
                    optimize=True,
                    progressive=True,
                    subsampling=0 if profile.quality >= 80 else 2,
                )
            elif extension == ".png":
                image.save(output, "PNG", optimize=True, compress_level=9)
            else:
                image.save(output, "WEBP", quality=profile.quality, method=6)
    except Exception as exc:
        error = CompressionError("image_processing_failed", str(exc), shown_path)
        return EncodedImage(
            None,
            ImageResult(
                shown_path, ImageStatus.FAILED, old_size, reason="processing_failed", error=error
            ),
        )

    data = output.getvalue()
    savings = old_size - len(data)
    if len(data) > old_size * (1 - MINIMUM_REDUCTION_RATIO) or savings < MINIMUM_SAVINGS_BYTES:
        return EncodedImage(
            None,
            ImageResult(
                shown_path,
                ImageStatus.SKIPPED,
                old_size,
                output_bytes=len(data),
                original_size=original_dimensions,
                output_size=output_dimensions,
                reason="insufficient_savings",
            ),
        )
    return EncodedImage(
        data,
        ImageResult(
            shown_path,
            ImageStatus.REPLACED,
            old_size,
            output_bytes=len(data),
            original_size=original_dimensions,
            output_size=output_dimensions,
        ),
    )
