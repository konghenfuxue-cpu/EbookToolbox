"""Image inspection and encoding without archive or user-interface concerns."""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageCms, ImageOps

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

# Large comic pages and scrolling screenshots are legitimate, so the edge limit is
# deliberately generous while the pixel limit still bounds decoded memory.
MAX_IMAGE_EDGE = 50_000
MAX_IMAGE_PIXELS = 100_000_000
_SRGB_PROFILE = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
_SRGB_PROFILE_BYTES = _SRGB_PROFILE.tobytes()
_PROFILE_MODES = {
    "RGB": frozenset({"P", "PA", "RGB", "RGBA", "RGBX"}),
    "GRAY": frozenset({"1", "L", "LA"}),
    "CMYK": frozenset({"CMYK"}),
    "LAB": frozenset({"LAB"}),
}


@dataclass(frozen=True, slots=True)
class EncodedImage:
    data: bytes | None
    result: ImageResult


def _resized(image: Image.Image, max_long_edge: int) -> Image.Image:
    long_edge = max(image.size)
    if long_edge <= max_long_edge:
        return image
    scale = max_long_edge / long_edge
    if image.mode in {"1", "P"}:
        image = image.convert("RGBA" if "transparency" in image.info else "RGB")
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


def _icc_warning(code: str, message: str, path: Path) -> CompressionWarning:
    return CompressionWarning(code, message, path)


def _prepare_profiled_image(
    image: Image.Image,
    original_mode: str,
    raw_icc_profile: object,
    output_format: str,
    path: Path,
) -> tuple[Image.Image, bytes | None, CompressionWarning | None]:
    """Keep compatible ICC data or convert profiled pixels safely to sRGB."""

    if raw_icc_profile is None:
        return image, None, None
    if not isinstance(raw_icc_profile, (bytes, bytearray, memoryview)):
        return image, None, _icc_warning(
            "icc_profile_invalid", "The embedded ICC profile is not valid binary data.", path
        )
    icc_profile = bytes(raw_icc_profile)
    try:
        source_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc_profile))
        color_space = str(source_profile.profile.xcolor_space).strip()
    except Exception as exc:
        return image, None, _icc_warning(
            "icc_profile_invalid", f"The embedded ICC profile could not be parsed: {exc}", path
        )

    compatible_modes = _PROFILE_MODES.get(color_space, frozenset())
    if original_mode not in compatible_modes or image.mode not in compatible_modes:
        return image, None, _icc_warning(
            "icc_profile_incompatible",
            f"ICC color space {color_space!r} is incompatible with image modes "
            f"{original_mode!r} -> {image.mode!r}.",
            path,
        )

    if output_format not in {"JPEG", "WEBP"} or color_space == "RGB":
        return image, icc_profile, None

    alpha = image.getchannel("A") if "A" in image.getbands() else None
    color_image = image.getchannel("L") if image.mode == "LA" else image
    try:
        converted = ImageCms.profileToProfile(
            color_image, source_profile, _SRGB_PROFILE, outputMode="RGB"
        )
    except Exception as exc:
        return image, None, _icc_warning(
            "icc_profile_conversion_failed",
            f"The embedded ICC profile could not be safely converted to sRGB: {exc}",
            path,
        )
    if alpha is not None:
        converted.putalpha(alpha)
    return converted, _SRGB_PROFILE_BYTES, None


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
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            source = Image.open(path)
        with source:
            extension = path.suffix.lower()
            expected = _EXPECTED_FORMATS.get(extension)
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
            width, height = source.size
            if (
                width > MAX_IMAGE_EDGE
                or height > MAX_IMAGE_EDGE
                or width * height > MAX_IMAGE_PIXELS
            ):
                warning = CompressionWarning(
                    "image_dimensions_exceeded",
                    f"Image dimensions {width}x{height} exceed safe decoding limits.",
                    shown_path,
                )
                return EncodedImage(
                    None,
                    ImageResult(
                        shown_path,
                        ImageStatus.SKIPPED,
                        old_size,
                        original_size=(width, height),
                        reason="unsafe_dimensions",
                        warnings=(warning,),
                    ),
                )
            original_mode = source.mode
            raw_icc_profile = source.info.get("icc_profile")
            source.load()
            image = ImageOps.exif_transpose(source)
            original_dimensions = image.size
            raw_exif = image.info.get("exif")
            exif = (
                bytes(raw_exif)
                if isinstance(raw_exif, (bytes, bytearray, memoryview))
                else None
            )
            image = _resized(image, profile.max_long_edge)
            image, icc_profile, icc_warning = _prepare_profiled_image(
                image, original_mode, raw_icc_profile, expected, shown_path
            )
            if icc_warning is not None:
                return EncodedImage(
                    None,
                    ImageResult(
                        shown_path,
                        ImageStatus.SKIPPED,
                        old_size,
                        original_size=original_dimensions,
                        reason=icc_warning.code,
                        warnings=(icc_warning,),
                    ),
                )
            output_dimensions = image.size
            output = io.BytesIO()
            if extension in {".jpg", ".jpeg"}:
                image = _flatten_for_jpeg(image)
                image.save(
                    output,
                    "JPEG",
                    quality=profile.quality,
                    optimize=True,
                    progressive=True,
                    subsampling=0 if profile.quality >= 80 else 2,
                    **({"icc_profile": icc_profile} if icc_profile is not None else {}),
                    **({"exif": exif} if exif is not None else {}),
                )
            elif extension == ".png":
                image.save(
                    output,
                    "PNG",
                    optimize=True,
                    compress_level=9,
                    **({"icc_profile": icc_profile} if icc_profile is not None else {}),
                    **({"exif": exif} if exif is not None else {}),
                )
            else:
                image.save(
                    output,
                    "WEBP",
                    quality=profile.quality,
                    method=6,
                    **({"icc_profile": icc_profile} if icc_profile is not None else {}),
                    **({"exif": exif} if exif is not None else {}),
                )
    except Image.DecompressionBombWarning as exc:
        warning = CompressionWarning("decompression_bomb", str(exc), shown_path)
        return EncodedImage(
            None,
            ImageResult(
                shown_path,
                ImageStatus.SKIPPED,
                old_size,
                reason="decompression_bomb",
                warnings=(warning,),
            ),
        )
    except Image.DecompressionBombError as exc:
        error = CompressionError("decompression_bomb", str(exc), shown_path)
        return EncodedImage(
            None,
            ImageResult(
                shown_path,
                ImageStatus.FAILED,
                old_size,
                reason="decompression_bomb",
                error=error,
            ),
        )
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
