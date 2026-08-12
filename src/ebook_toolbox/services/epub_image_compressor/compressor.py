"""Single-book EPUB image compression orchestration."""

from __future__ import annotations

import tempfile
from pathlib import Path

from .archive import ArchiveError, build_epub, publish_epub, safe_extract
from .covers import find_cover_paths
from .images import PASSTHROUGH_EXTENSIONS, SUPPORTED_EXTENSIONS, encode_candidate
from .models import (
    CompressionError,
    CompressionRequest,
    CompressionResult,
    ImageResult,
    ImageStatus,
)


def _default_output(source: Path) -> Path:
    return source.with_name(f"{source.stem}_optimized.epub")


def compress_epub(request: CompressionRequest) -> CompressionResult:
    """Compress supported images in one EPUB and return a structured result.

    Fatal errors are captured in ``result.errors``. The input is never written to.
    """

    source = Path(request.source)
    output = Path(request.output) if request.output is not None else _default_output(source)
    result = CompressionResult(source=source, output=output)

    try:
        if not source.is_file() or source.suffix.lower() != ".epub":
            raise ValueError("Source must be an existing .epub file")
        source = source.resolve()
        output = output.resolve()
        result.source = source
        result.output = output
        if source == output:
            raise ValueError("Output must not be the source EPUB")
        if output.exists() and not request.overwrite:
            raise FileExistsError(f"Output already exists: {output}")
        if not output.parent.is_dir():
            raise ValueError(f"Output directory does not exist: {output.parent}")

        result.original_bytes = source.stat().st_size
        with tempfile.TemporaryDirectory(prefix="ebook_toolbox_epub_") as directory:
            root = Path(directory) / "unpacked"
            root.mkdir()
            opf_paths = safe_extract(source, root)
            covers, cover_warnings = find_cover_paths(root, opf_paths)
            result.warnings.extend(cover_warnings)

            for image in sorted(root.rglob("*")):
                if not image.is_file():
                    continue
                extension = image.suffix.lower()
                relative_path = image.relative_to(root)
                if extension in PASSTHROUGH_EXTENSIONS:
                    continue
                if extension not in SUPPORTED_EXTENSIONS:
                    continue
                if not request.include_cover and image.resolve() in covers:
                    result.images.append(
                        ImageResult(
                            relative_path,
                            ImageStatus.SKIPPED,
                            image.stat().st_size,
                            reason="cover",
                        )
                    )
                    continue
                encoded = encode_candidate(image, request.profile, display_path=relative_path)
                result.images.append(encoded.result)
                result.warnings.extend(encoded.result.warnings)
                if encoded.result.error is not None:
                    result.errors.append(encoded.result.error)
                if encoded.data is not None:
                    image.write_bytes(encoded.data)

            temporary_file = tempfile.NamedTemporaryFile(
                prefix=f".{output.name}.", suffix=".tmp", dir=output.parent, delete=False
            )
            temporary_file.close()
            temporary_output = Path(temporary_file.name)
            temporary_output.unlink()
            try:
                build_epub(root, temporary_output)
                publish_epub(temporary_output, output, overwrite=request.overwrite)
            finally:
                temporary_output.unlink(missing_ok=True)
        result.output_bytes = output.stat().st_size
        result.success = True
    except (ArchiveError, FileExistsError, OSError, ValueError) as exc:
        result.errors.append(CompressionError(type(exc).__name__, str(exc)))
    # Core boundary: return unexpected failures without publishing partial output.
    except Exception as exc:
        result.errors.append(CompressionError("unexpected_error", str(exc)))
    return result
