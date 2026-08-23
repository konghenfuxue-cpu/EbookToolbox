from __future__ import annotations

import hashlib
import io
import os
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from PIL import Image, ImageCms

from ebook_toolbox.services.epub_image_compressor import (
    BALANCED,
    HIGH_QUALITY,
    SPACE_SAVER,
    CompressionRequest,
    ImageStatus,
    compress_epub,
)
from ebook_toolbox.services.epub_image_compressor import archive as archive_module
from ebook_toolbox.services.epub_image_compressor import images as images_module

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


def test_jpeg_preserves_icc_profile_when_reencoded(tmp_path):
    source = tmp_path / "profiled.epub"
    icc_profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    image = Image.effect_noise((1800, 3600), 80).convert("RGB")
    encoded = io.BytesIO()
    image.save(encoded, "JPEG", quality=98, icc_profile=icc_profile)
    make_epub(source, {"page.jpg": encoded.getvalue()})
    profile = type(BALANCED)("test", 30, 800, 1)

    result = compress_epub(CompressionRequest(source, profile=profile))

    assert result.success
    assert result.images[0].status is ImageStatus.REPLACED
    with Image.open(io.BytesIO(read_member(result.output, "OEBPS/page.jpg"))) as output:
        assert output.info["icc_profile"] == icc_profile


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


@pytest.mark.parametrize(
    "name", ["cover.jpg", "front-cover.png", "front_cover.webp", "cover-image.jpg", "封面.png"]
)
@pytest.mark.parametrize("include_cover", [False, True])
def test_conventional_cover_filenames_follow_cover_policy(tmp_path, name, include_cover):
    source = tmp_path / f"book-{Path(name).suffix[1:]}.epub"
    image_format = (
        "PNG" if name.endswith(".png") else "WEBP" if name.endswith(".webp") else "JPEG"
    )
    make_epub(source, {name: image_bytes(image_format)})

    profile = type(BALANCED)("test", 20, 400, 1)
    result = compress_epub(
        CompressionRequest(source, profile=profile, include_cover=include_cover)
    )

    assert result.success
    assert result.images[0].reason == (None if include_cover else "cover")
    if include_cover:
        assert result.images[0].status is ImageStatus.REPLACED


@pytest.mark.parametrize("name", ["discover.jpg", "recovered.png", "discoveries.jpg"])
def test_cover_substrings_in_ordinary_filenames_are_compressed(tmp_path, name):
    source = tmp_path / f"book-{Path(name).stem}.epub"
    make_epub(source, {name: image_bytes("PNG" if name.endswith(".png") else "JPEG")})
    profile = type(BALANCED)("test", 20, 400, 1)

    result = compress_epub(CompressionRequest(source, profile=profile))

    assert result.success
    assert result.images[0].reason is None
    assert result.images[0].status is ImageStatus.REPLACED


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


@pytest.mark.parametrize("size", [(50_001, 1), (10_001, 10_000)])
def test_unsafe_dimensions_are_rejected_before_load(tmp_path, monkeypatch, size):
    path = tmp_path / "large.png"
    path.write_bytes(b"not decoded")

    class HeaderOnlyImage:
        format = "PNG"
        n_frames = 1

        def __init__(self):
            self.size = size
            self.load_called = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def load(self):
            self.load_called = True
            raise AssertionError("pixel data must not be decoded")

    source = HeaderOnlyImage()
    monkeypatch.setattr(images_module.Image, "open", lambda _path: source)

    encoded = images_module.encode_candidate(path, type(BALANCED)("test", 72, 100, 1))

    assert encoded.result.status is ImageStatus.SKIPPED
    assert encoded.result.reason == "unsafe_dimensions"
    assert encoded.result.warnings[0].code == "image_dimensions_exceeded"
    assert not source.load_called


@pytest.mark.parametrize(
    ("exception", "status"),
    [
        (Image.DecompressionBombWarning("warning"), ImageStatus.SKIPPED),
        (Image.DecompressionBombError("error"), ImageStatus.FAILED),
    ],
)
def test_pillow_decompression_bombs_are_structured(tmp_path, monkeypatch, exception, status):
    path = tmp_path / "bomb.png"
    path.write_bytes(b"header")

    def raise_bomb(_path):
        raise exception

    monkeypatch.setattr(images_module.Image, "open", raise_bomb)
    encoded = images_module.encode_candidate(path, type(BALANCED)("test", 72, 100, 1))

    assert encoded.result.status is status
    assert encoded.result.reason == "decompression_bomb"
    assert encoded.result.warnings or encoded.result.error is not None


def test_failed_fallback_copy_is_clean_and_retryable(tmp_path, monkeypatch):
    source = tmp_path / "source.epub"
    output = tmp_path / "output.epub"
    make_epub(source)
    before = hashlib.sha256(source.read_bytes()).digest()
    real_copy = archive_module.shutil.copyfileobj
    fail_copy = True

    monkeypatch.setattr(
        archive_module.os, "link", lambda *_args: (_ for _ in ()).throw(OSError())
    )

    def controlled_copy(src, dst, *args):
        nonlocal fail_copy
        if fail_copy and str(getattr(dst, "name", "")).endswith(".copying"):
            dst.write(src.read(8))
            fail_copy = False
            raise OSError("simulated full disk")
        real_copy(src, dst, *args)

    monkeypatch.setattr(archive_module.shutil, "copyfileobj", controlled_copy)

    failed = compress_epub(CompressionRequest(source, output=output))
    assert not failed.success
    assert not output.exists()
    assert not list(tmp_path.glob(".output.epub.*.copying"))
    assert hashlib.sha256(source.read_bytes()).digest() == before

    retried = compress_epub(CompressionRequest(source, output=output))
    assert retried.success
    assert output.is_file()
    assert hashlib.sha256(source.read_bytes()).digest() == before


def test_concurrent_publish_uses_call_owned_staging_files(tmp_path, monkeypatch):
    temporary_files = [tmp_path / f"temporary-{index}.epub" for index in range(2)]
    output = tmp_path / "output.epub"
    payloads = [f"output {index}".encode() for index in range(2)]
    for temporary, payload in zip(temporary_files, payloads, strict=True):
        temporary.write_bytes(payload)

    monkeypatch.setattr(
        archive_module.os, "link", lambda *_args: (_ for _ in ()).throw(OSError())
    )
    real_copy = archive_module.shutil.copyfileobj
    barrier = threading.Barrier(2)

    def synchronized_copy(source, destination, *args):
        if str(getattr(destination, "name", "")).endswith(".copying"):
            barrier.wait(timeout=5)
        real_copy(source, destination, *args)

    monkeypatch.setattr(archive_module.shutil, "copyfileobj", synchronized_copy)

    def publish(temporary):
        archive_module.publish_epub(temporary, output, overwrite=False)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(publish, temporary) for temporary in temporary_files]

    failures = [future.exception() for future in futures if future.exception() is not None]
    assert len(failures) == 1
    assert isinstance(failures[0], FileExistsError)
    assert output.read_bytes() in payloads
    assert not list(tmp_path.glob(".output.epub.*.copying"))


def test_concurrent_output_is_preserved_when_exclusive_open_fails(tmp_path, monkeypatch):
    temporary = tmp_path / "temporary.epub"
    output = tmp_path / "output.epub"
    temporary.write_bytes(b"new output")
    concurrent_data = b"concurrent output"

    monkeypatch.setattr(
        archive_module.os, "link", lambda *_args: (_ for _ in ()).throw(OSError())
    )
    real_open = Path.open

    def concurrent_open(path, mode="r", *args, **kwargs):
        if path == output and mode == "xb":
            output.write_bytes(concurrent_data)
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", concurrent_open)

    with pytest.raises(FileExistsError):
        archive_module.publish_epub(temporary, output, overwrite=False)

    assert output.read_bytes() == concurrent_data


def test_exclusive_copy_failure_removes_its_partial_output(tmp_path, monkeypatch):
    temporary = tmp_path / "temporary.epub"
    output = tmp_path / "output.epub"
    temporary.write_bytes(b"new output")

    monkeypatch.setattr(archive_module.os, "link", lambda *_args: (_ for _ in ()).throw(OSError()))
    real_copy = archive_module.shutil.copyfileobj

    def fail_final_copy(source, destination, *args):
        if Path(destination.name) == output:
            destination.write(source.read(2))
            raise OSError("simulated copy failure")
        real_copy(source, destination, *args)

    monkeypatch.setattr(archive_module.shutil, "copyfileobj", fail_final_copy)

    with pytest.raises(OSError, match="simulated copy failure"):
        archive_module.publish_epub(temporary, output, overwrite=False)

    assert not output.exists()


def test_partial_cleanup_fails_closed_when_lstat_fails(tmp_path, monkeypatch):
    output = tmp_path / "output.epub"
    output.write_bytes(b"partial output")
    created_identity = output.stat()

    def fail_lstat(_path):
        raise OSError("simulated stat failure")

    monkeypatch.setattr(Path, "lstat", fail_lstat)

    archive_module._unlink_if_same_file(output, created_identity)

    assert output.read_bytes() == b"partial output"


def test_partial_cleanup_fails_closed_for_symlink_identity(tmp_path, monkeypatch):
    output = tmp_path / "output.epub"
    output.write_bytes(b"replacement output")
    created_identity = output.stat()
    symlink_identity = os.stat_result(
        (archive_module.stat.S_IFLNK, *tuple(output.lstat())[1:])
    )
    monkeypatch.setattr(Path, "lstat", lambda _path: symlink_identity)

    archive_module._unlink_if_same_file(output, created_identity)

    assert output.read_bytes() == b"replacement output"


def test_failed_copy_does_not_remove_concurrent_overwrite(tmp_path, monkeypatch):
    temporary = tmp_path / "temporary.epub"
    replacement = tmp_path / "replacement.epub"
    output = tmp_path / "output.epub"
    temporary.write_bytes(b"failing output")
    replacement_data = b"replacement output"
    replacement.write_bytes(replacement_data)

    monkeypatch.setattr(
        archive_module.os, "link", lambda *_args: (_ for _ in ()).throw(OSError())
    )
    real_copy = archive_module.shutil.copyfileobj
    real_cleanup = archive_module._unlink_if_same_file
    publish_replacement = threading.Event()
    replacement_done = threading.Event()

    def fail_final_copy(source, destination, *args):
        if Path(destination.name) == output:
            destination.write(source.read(2))
            raise OSError("simulated copy failure")
        real_copy(source, destination, *args)

    def replace_output():
        assert publish_replacement.wait(timeout=5)
        archive_module.publish_epub(replacement, output, overwrite=True)
        replacement_done.set()

    def cleanup_after_replacement(path, created_identity):
        publish_replacement.set()
        assert replacement_done.wait(timeout=5)
        real_cleanup(path, created_identity)

    monkeypatch.setattr(archive_module.shutil, "copyfileobj", fail_final_copy)
    monkeypatch.setattr(archive_module, "_unlink_if_same_file", cleanup_after_replacement)

    with ThreadPoolExecutor(max_workers=1) as executor:
        publisher = executor.submit(replace_output)
        with pytest.raises(OSError, match="simulated copy failure"):
            archive_module.publish_epub(temporary, output, overwrite=False)
        publisher.result(timeout=5)

    assert output.read_bytes() == replacement_data


def test_overwrite_replaces_output_symlink_without_touching_target(tmp_path):
    source = tmp_path / "source.epub"
    target = tmp_path / "target.epub"
    output = tmp_path / "output.epub"
    make_epub(source)
    target_data = b"keep target unchanged"
    target.write_bytes(target_data)
    try:
        output.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symbolic links are unavailable: {exc}")

    result = compress_epub(CompressionRequest(source, output=output, overwrite=True))

    assert result.success
    assert result.output == output
    assert not output.is_symlink()
    assert target.read_bytes() == target_data
    assert zipfile.is_zipfile(output)


def test_output_symlink_to_source_is_rejected(tmp_path):
    source = tmp_path / "source.epub"
    output = tmp_path / "output.epub"
    make_epub(source)
    before = source.read_bytes()
    try:
        output.symlink_to(source)
    except OSError as exc:
        pytest.skip(f"symbolic links are unavailable: {exc}")

    result = compress_epub(CompressionRequest(source, output=output, overwrite=True))

    assert not result.success
    assert output.is_symlink()
    assert source.read_bytes() == before
    assert any(error.code == "ValueError" for error in result.errors)
