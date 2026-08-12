from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from ebook_toolbox.services.epub_image_compressor import (
    BALANCED,
    HIGH_QUALITY,
    SPACE_SAVER,
    CompressionRequest,
    ImageStatus,
    compress_epub,
)

CONTAINER = b'''<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles><rootfile full-path="OEBPS/content.opf"
    media-type="application/oebps-package+xml"/></rootfiles>
</container>'''


def image_bytes(format_name: str, size=(900, 1400), *, alpha=False) -> bytes:
    mode = "RGBA" if alpha else "RGB"
    image = Image.effect_noise(size, 80).convert(mode)
    if alpha:
        image.putalpha(100)
    output = io.BytesIO()
    options = {"quality": 98} if format_name in {"JPEG", "WEBP"} else {}
    image.save(output, format_name, **options)
    return output.getvalue()


def animated_png_bytes() -> bytes:
    output = io.BytesIO()
    frames = [Image.new("RGBA", (300, 300), color) for color in ("red", "blue")]
    frames[0].save(output, "PNG", save_all=True, append_images=frames[1:], duration=100, loop=0)
    return output.getvalue()


def make_epub(path: Path, images: dict[str, bytes] | None = None, *, cover: str | None = None):
    images = images or {}
    manifest = []
    for index, name in enumerate(images):
        extension = Path(name).suffix.lower()
        media = {
            ".jpg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }[extension]
        properties = ' properties="cover-image"' if name == cover else ""
        manifest.append(
            f'<item id="img{index}" href="{name}" media-type="{media}"{properties}/>'
        )
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        f'<manifest>{"".join(manifest)}</manifest><spine/></package>'
    ).encode()
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", CONTAINER)
        archive.writestr("OEBPS/content.opf", opf)
        for name, data in images.items():
            archive.writestr(f"OEBPS/{name}", data)


def read_member(path: Path, member: str) -> bytes:
    with zipfile.ZipFile(path) as archive:
        return archive.read(member)


def test_profiles_preserve_original_parameters():
    assert (BALANCED.quality, BALANCED.max_long_edge, BALANCED.min_bytes) == (72, 2400, 80 * 1024)
    assert (HIGH_QUALITY.quality, HIGH_QUALITY.max_long_edge, HIGH_QUALITY.min_bytes) == (
        82,
        3000,
        120 * 1024,
    )
    assert (SPACE_SAVER.quality, SPACE_SAVER.max_long_edge, SPACE_SAVER.min_bytes) == (
        58,
        2000,
        60 * 1024,
    )


def test_jpeg_is_resized(tmp_path):
    source = tmp_path / "book.epub"
    make_epub(source, {"page.jpg": image_bytes("JPEG", (1800, 3600))})
    profile = type(BALANCED)("test", 30, 800, 1)

    result = compress_epub(CompressionRequest(source, profile=profile))

    assert result.success
    assert result.images[0].status is ImageStatus.REPLACED
    with Image.open(io.BytesIO(read_member(result.output, "OEBPS/page.jpg"))) as image:
        assert max(image.size) == 800


def test_transparent_png_keeps_alpha(tmp_path):
    source = tmp_path / "transparent.epub"
    make_epub(source, {"alpha.png": image_bytes("PNG", (800, 1200), alpha=True)})
    profile = type(BALANCED)("test", 72, 400, 1)

    result = compress_epub(CompressionRequest(source, profile=profile))

    assert result.success
    with Image.open(io.BytesIO(read_member(result.output, "OEBPS/alpha.png"))) as image:
        assert image.mode == "RGBA"
        assert image.getextrema()[3] == (100, 100)


def test_gif_is_preserved_byte_for_byte(tmp_path):
    source = tmp_path / "gif.epub"
    output = io.BytesIO()
    Image.new("P", (20, 20)).save(output, "GIF")
    original = output.getvalue()
    make_epub(source, {"animation.gif": original})

    result = compress_epub(CompressionRequest(source))

    assert result.success
    assert read_member(result.output, "OEBPS/animation.gif") == original
    assert result.images == []


def test_multiframe_png_is_skipped_with_warning(tmp_path):
    source = tmp_path / "animated.epub"
    original = animated_png_bytes()
    make_epub(source, {"animated.png": original})
    profile = type(BALANCED)("test", 72, 100, 1)

    result = compress_epub(CompressionRequest(source, profile=profile))

    assert result.success
    assert result.images[0].reason == "multiple_frames"
    assert any(warning.code == "animated_image_skipped" for warning in result.warnings)
    assert read_member(result.output, "OEBPS/animated.png") == original


@pytest.mark.parametrize("include_cover,expected", [(False, "cover"), (True, None)])
def test_cover_policy(tmp_path, include_cover, expected):
    source = tmp_path / f"cover-{include_cover}.epub"
    original = image_bytes("JPEG", (1200, 1800))
    make_epub(source, {"art.jpg": original}, cover="art.jpg")
    profile = type(BALANCED)("test", 20, 400, 1)

    result = compress_epub(
        CompressionRequest(source, profile=profile, include_cover=include_cover)
    )

    assert result.success
    assert result.images[0].reason == expected
    if include_cover:
        assert result.images[0].status is ImageStatus.REPLACED
    else:
        assert read_member(result.output, "OEBPS/art.jpg") == original


def test_chinese_and_space_paths(tmp_path):
    directory = tmp_path / "中文 目录"
    directory.mkdir()
    source = directory / "一本 书.epub"
    make_epub(source)

    result = compress_epub(CompressionRequest(source))

    assert result.success
    assert result.output.name == "一本 书_optimized.epub"


def test_zip_slip_is_rejected_and_input_unchanged(tmp_path):
    source = tmp_path / "unsafe.epub"
    make_epub(source)
    with zipfile.ZipFile(source, "a") as archive:
        archive.writestr("../escaped.txt", "bad")
    before = hashlib.sha256(source.read_bytes()).digest()

    result = compress_epub(CompressionRequest(source))

    assert not result.success
    assert "Unsafe ZIP member path" in result.errors[0].message
    assert hashlib.sha256(source.read_bytes()).digest() == before
    assert not (tmp_path / "escaped.txt").exists()


def test_corrupt_zip_is_rejected_and_input_unchanged(tmp_path):
    source = tmp_path / "broken.epub"
    source.write_bytes(b"this is not a zip")
    before = hashlib.sha256(source.read_bytes()).digest()

    result = compress_epub(CompressionRequest(source))

    assert not result.success
    assert hashlib.sha256(source.read_bytes()).digest() == before
    assert not result.output.exists()


def test_output_mimetype_is_first_and_stored(tmp_path):
    source = tmp_path / "valid.epub"
    make_epub(source)

    result = compress_epub(CompressionRequest(source))

    assert result.success
    with zipfile.ZipFile(result.output) as archive:
        first = archive.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED


def test_existing_output_is_not_overwritten(tmp_path):
    source = tmp_path / "source.epub"
    output = tmp_path / "existing.epub"
    make_epub(source)
    output.write_bytes(b"keep me")

    result = compress_epub(CompressionRequest(source, output=output))

    assert not result.success
    assert output.read_bytes() == b"keep me"
    assert any(error.code == "FileExistsError" for error in result.errors)


def test_invalid_container_reports_failure_and_preserves_source(tmp_path):
    source = tmp_path / "invalid.epub"
    make_epub(source)
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", b"<broken")
    before = hashlib.sha256(source.read_bytes()).hexdigest()

    result = compress_epub(CompressionRequest(source))

    assert not result.success
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
