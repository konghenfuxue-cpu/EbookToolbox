"""Single-book EPUB image compression orchestration."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .archive import (
    ArchiveError,
    build_epub,
    ensure_owned_temporary,
    publish_epub,
    safe_extract,
    unlink_owned_temporary,
    validate_output,
)
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


def _paths_refer_to_same_file(source: Path, output: Path) -> bool:
    """Compare path identities without resolving the final output component."""

    try:
        return source.samefile(output)
    except OSError:
        source_identity = os.path.normcase(os.path.abspath(source))
        output_identity = os.path.normcase(
            os.path.abspath(output.parent.resolve() / output.name)
        )
        return source_identity == output_identity


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
        result.source = source
        result.output = output
        if _paths_refer_to_same_file(source, output):
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
            temporary_output = Path(temporary_file.name)
            temporary_identity = None
            try:
                temporary_identity = os.fstat(temporary_file.fileno())
                with temporary_file:
                    ensure_owned_temporary(temporary_output, temporary_identity)
                    build_epub(root, temporary_file)
                    ensure_owned_temporary(temporary_output, temporary_identity)
                    validate_output(temporary_file)
                    ensure_owned_temporary(temporary_output, temporary_identity)
                ensure_owned_temporary(temporary_output, temporary_identity)
                publish_epub(
                    temporary_output,
                    output,
                    overwrite=request.overwrite,
                    temporary_identity=temporary_identity,
                )
            finally:
                if not temporary_file.closed:
                    temporary_file.close()
                if temporary_identity is not None:
                    unlink_owned_temporary(temporary_output, temporary_identity)
        result.output_bytes = output.stat().st_size
        result.success = True
    except (ArchiveError, FileExistsError, OSError, ValueError) as exc:
        result.errors.append(CompressionError(type(exc).__name__, str(exc)))
    # Core boundary: return unexpected failures without publishing partial output.
    except Exception as exc:
        result.errors.append(CompressionError("unexpected_error", str(exc)))
    return result
