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
from ebook_toolbox.services.epub_image_compressor import compressor as compressor_module
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


def animated_webp_bytes() -> bytes:
    output = io.BytesIO()
    frames = [Image.new("RGB", (300, 300), color) for color in ("red", "blue")]
    frames[0].save(
        output,
        "WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
        icc_profile=srgb_profile_bytes(),
    )
    return output.getvalue()


def srgb_profile_bytes() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


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
    icc_profile = srgb_profile_bytes()
    image = Image.effect_noise((3600, 1800), 80).convert("RGB")
    exif = Image.Exif()
    exif[274] = 6
    exif[315] = "EbookToolbox test"
    encoded = io.BytesIO()
    image.save(encoded, "JPEG", quality=98, icc_profile=icc_profile, exif=exif)
    make_epub(source, {"page.jpg": encoded.getvalue()})
    profile = type(BALANCED)("test", 30, 800, 1)

    result = compress_epub(CompressionRequest(source, profile=profile))

    assert result.success
    assert result.images[0].status is ImageStatus.REPLACED
    with Image.open(io.BytesIO(read_member(result.output, "OEBPS/page.jpg"))) as output:
        assert output.info["icc_profile"] == icc_profile
        assert output.mode == "RGB"
        assert output.size == (400, 800)
        assert output.getexif().get(274) is None
        assert output.getexif()[315] == "EbookToolbox test"


def test_grayscale_jpeg_with_incompatible_icc_is_preserved(tmp_path):
    source = tmp_path / "gray-profiled.epub"
    image = Image.effect_noise((1200, 1800), 80).convert("L")
    encoded = io.BytesIO()
    image.save(encoded, "JPEG", quality=98, icc_profile=srgb_profile_bytes())
    original = encoded.getvalue()
    make_epub(source, {"gray.jpg": original})

    result = compress_epub(
        CompressionRequest(source, profile=type(BALANCED)("test", 30, 400, 1))
    )

    assert result.success
    assert result.images[0].status is ImageStatus.SKIPPED
    assert result.images[0].reason == "icc_profile_incompatible"
    assert read_member(result.output, "OEBPS/gray.jpg") == original


def test_webp_preserves_compatible_icc_profile(tmp_path):
    source = tmp_path / "profiled-webp.epub"
    icc_profile = srgb_profile_bytes()
    image = Image.effect_noise((1800, 3600), 80).convert("RGBA")
    image.putalpha(100)
    encoded = io.BytesIO()
    image.save(encoded, "WEBP", quality=98, icc_profile=icc_profile)
    make_epub(source, {"page.webp": encoded.getvalue()})

    result = compress_epub(
        CompressionRequest(source, profile=type(BALANCED)("test", 30, 800, 1))
    )

    assert result.success
    assert result.images[0].status is ImageStatus.REPLACED
    with Image.open(io.BytesIO(read_member(result.output, "OEBPS/page.webp"))) as output:
        assert output.mode == "RGBA"
        assert output.getextrema()[3] == (100, 100)
        assert output.info["icc_profile"] == icc_profile


def test_transparent_png_keeps_alpha(tmp_path):
    source = tmp_path / "transparent.epub"
    icc_profile = srgb_profile_bytes()
    image = Image.effect_noise((800, 1200), 80).convert("RGBA")
    image.putalpha(100)
    encoded = io.BytesIO()
    image.save(encoded, "PNG", icc_profile=icc_profile)
    make_epub(source, {"alpha.png": encoded.getvalue()})
    profile = type(BALANCED)("test", 72, 400, 1)

    result = compress_epub(CompressionRequest(source, profile=profile))

    assert result.success
    with Image.open(io.BytesIO(read_member(result.output, "OEBPS/alpha.png"))) as image:
        assert image.mode == "RGBA"
        assert image.getextrema()[3] == (100, 100)
        assert image.info["icc_profile"] == icc_profile


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


def test_multiframe_webp_with_icc_is_preserved(tmp_path):
    source = tmp_path / "animated-webp.epub"
    original = animated_webp_bytes()
    make_epub(source, {"animated.webp": original})

    result = compress_epub(
        CompressionRequest(source, profile=type(BALANCED)("test", 72, 100, 1))
    )

    assert result.success
    assert result.images[0].reason == "multiple_frames"
    assert any(warning.code == "animated_image_skipped" for warning in result.warnings)
    assert read_member(result.output, "OEBPS/animated.webp") == original


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


def test_temporary_output_resists_symlink_insertion_before_build(tmp_path, monkeypatch):
    source = tmp_path / "source.epub"
    output = tmp_path / "output.epub"
    victim = tmp_path / "victim.txt"
    victim_data = b"must remain unchanged"
    make_epub(source)
    victim.write_bytes(victim_data)
    real_build = compressor_module.build_epub
    observed_temporary = None

    def attack_before_build(root, temporary_file):
        nonlocal observed_temporary
        observed_temporary = Path(temporary_file.name)
        with pytest.raises(OSError):
            observed_temporary.symlink_to(victim)
        assert observed_temporary.is_file()
        assert not observed_temporary.is_symlink()
        real_build(root, temporary_file)

    monkeypatch.setattr(compressor_module, "build_epub", attack_before_build)

    result = compress_epub(CompressionRequest(source, output=output))

    assert result.success
    assert victim.read_bytes() == victim_data
    assert observed_temporary is not None
    assert not observed_temporary.exists()
    archive_module.validate_output(output)


def test_temporary_output_remains_owned_throughout_build(tmp_path, monkeypatch):
    source = tmp_path / "source.epub"
    output = tmp_path / "output.epub"
    make_epub(source, {"page.jpg": image_bytes("JPEG")})
    real_build = compressor_module.build_epub
    real_write = archive_module.zipfile.ZipFile.write
    temporary_path = None
    temporary_identity = None
    ownership_checks = 0

    def track_build(root, temporary_file):
        nonlocal temporary_path, temporary_identity
        temporary_path = Path(temporary_file.name)
        temporary_identity = os.fstat(temporary_file.fileno())
        real_build(root, temporary_file)

    def check_each_write(zip_file, *args, **kwargs):
        nonlocal ownership_checks
        assert temporary_path is not None
        assert temporary_identity is not None
        archive_module.ensure_owned_temporary(temporary_path, temporary_identity)
        ownership_checks += 1
        return real_write(zip_file, *args, **kwargs)

    monkeypatch.setattr(compressor_module, "build_epub", track_build)
    monkeypatch.setattr(archive_module.zipfile.ZipFile, "write", check_each_write)

    result = compress_epub(CompressionRequest(source, output=output))

    assert result.success
    assert ownership_checks >= 3
    assert temporary_path is not None
    assert not temporary_path.exists()
    archive_module.validate_output(output)


def test_build_failure_cleans_only_owned_temporary_output(tmp_path, monkeypatch):
    source = tmp_path / "source.epub"
    output = tmp_path / "output.epub"
    make_epub(source)
    temporary_path = None

    def fail_build(_root, temporary_file):
        nonlocal temporary_path
        temporary_path = Path(temporary_file.name)
        temporary_file.write(b"partial output")
        temporary_file.flush()
        raise OSError("simulated build failure")

    monkeypatch.setattr(compressor_module, "build_epub", fail_build)

    result = compress_epub(CompressionRequest(source, output=output))

    assert not result.success
    assert any(error.message == "simulated build failure" for error in result.errors)
    assert temporary_path is not None
    assert not temporary_path.exists()
    assert not output.exists()
    assert not list(tmp_path.glob(".output.epub.*.tmp"))


def test_validation_failure_cleans_owned_temporary_output(tmp_path, monkeypatch):
    source = tmp_path / "source.epub"
    output = tmp_path / "output.epub"
    make_epub(source)

    def fail_validation(_temporary_file):
        raise archive_module.ArchiveError("simulated validation failure")

    monkeypatch.setattr(compressor_module, "validate_output", fail_validation)

    result = compress_epub(CompressionRequest(source, output=output))

    assert not result.success
    assert any(error.message == "simulated validation failure" for error in result.errors)
    assert not output.exists()
    assert not list(tmp_path.glob(".output.epub.*.tmp"))


def test_replaced_temporary_is_rejected_and_not_cleaned(tmp_path, monkeypatch):
    source = tmp_path / "source.epub"
    output = tmp_path / "output.epub"
    replacement_data = b"foreign temporary replacement"
    make_epub(source)
    real_publish = compressor_module.publish_epub
    temporary_path = None

    def replace_before_publish(temporary, destination, **kwargs):
        nonlocal temporary_path
        temporary_path = temporary
        temporary.unlink()
        temporary.write_bytes(replacement_data)
        real_publish(temporary, destination, **kwargs)

    monkeypatch.setattr(compressor_module, "publish_epub", replace_before_publish)

    result = compress_epub(CompressionRequest(source, output=output))

    assert not result.success
    assert any(error.code == "ArchiveError" for error in result.errors)
    assert temporary_path is not None
    assert temporary_path.read_bytes() == replacement_data
    assert not output.exists()


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
    real_link = archive_module.os.link
    fail_copy = True

    def staging_only_link(source, destination):
        if not str(source).endswith(".copying"):
            raise OSError("initial hard links unavailable")
        real_link(source, destination)

    monkeypatch.setattr(archive_module.os, "link", staging_only_link)

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

    real_link = archive_module.os.link

    def staging_only_link(source, destination):
        if not str(source).endswith(".copying"):
            raise OSError("initial hard links unavailable")
        real_link(source, destination)

    monkeypatch.setattr(archive_module.os, "link", staging_only_link)
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


def test_publication_staging_resists_symlink_insertion(tmp_path, monkeypatch):
    temporary = tmp_path / "temporary.epub"
    output = tmp_path / "output.epub"
    victim = tmp_path / "victim.txt"
    victim_data = b"must remain unchanged"
    make_epub(temporary)
    victim.write_bytes(victim_data)
    real_link = archive_module.os.link
    real_publish = archive_module._publish_staging_no_clobber
    staging_path = None

    def staging_only_link(source, destination):
        if not str(source).endswith(".copying"):
            raise OSError("initial hard links unavailable")
        real_link(source, destination)

    def attack_staging(staging, destination):
        nonlocal staging_path
        staging_path = staging
        with pytest.raises(OSError):
            staging.symlink_to(victim)
        assert staging.is_file()
        assert not staging.is_symlink()
        real_publish(staging, destination)

    monkeypatch.setattr(archive_module.os, "link", staging_only_link)
    monkeypatch.setattr(archive_module, "_publish_staging_no_clobber", attack_staging)

    archive_module.publish_epub(temporary, output, overwrite=False)

    assert victim.read_bytes() == victim_data
    assert staging_path is not None
    assert not staging_path.exists()
    archive_module.validate_output(output)


def test_fallback_copy_is_invisible_until_atomic_publish(tmp_path, monkeypatch):
    temporary = tmp_path / "temporary.epub"
    output = tmp_path / "output.epub"
    make_epub(temporary, {"page.jpg": image_bytes("JPEG")})
    real_link = archive_module.os.link
    real_copy = archive_module.shutil.copyfileobj
    link_calls = 0
    copy_started = threading.Event()
    finish_copy = threading.Event()
    stop_observer = threading.Event()
    observed_absent_during_copy = threading.Event()
    observed_outputs = []

    def fail_initial_link(source, destination):
        nonlocal link_calls
        link_calls += 1
        if link_calls == 1:
            raise OSError("initial hard links unavailable")
        real_link(source, destination)

    def slow_copy(source, destination, *_args):
        destination.write(source.read(1024))
        copy_started.set()
        assert finish_copy.wait(timeout=5)
        real_copy(source, destination)

    monkeypatch.setattr(archive_module.os, "link", fail_initial_link)
    monkeypatch.setattr(archive_module.shutil, "copyfileobj", slow_copy)

    def observe_output():
        while not stop_observer.is_set():
            if output.exists():
                observed_outputs.append(output.read_bytes())
            elif copy_started.is_set():
                observed_absent_during_copy.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        observer = executor.submit(observe_output)
        publication = executor.submit(
            archive_module.publish_epub, temporary, output, overwrite=False
        )
        try:
            assert copy_started.wait(timeout=5)
            assert observed_absent_during_copy.wait(timeout=5)
            finish_copy.set()
            publication.result(timeout=5)
        finally:
            finish_copy.set()
            stop_observer.set()
        observer.result(timeout=5)

    archive_module.validate_output(output)
    complete_output = output.read_bytes()
    assert all(observed == complete_output for observed in observed_outputs)


def test_target_race_never_overwrites_existing_output(tmp_path, monkeypatch):
    temporary = tmp_path / "temporary.epub"
    output = tmp_path / "output.epub"
    make_epub(temporary)
    concurrent_data = b"concurrent output"
    monkeypatch.setattr(
        archive_module.os, "link", lambda *_args: (_ for _ in ()).throw(OSError())
    )
    real_publish = archive_module._publish_staging_no_clobber

    def race_before_publish(staging, destination):
        output.write_bytes(concurrent_data)
        real_publish(staging, destination)

    monkeypatch.setattr(archive_module, "_publish_staging_no_clobber", race_before_publish)

    with pytest.raises(FileExistsError):
        archive_module.publish_epub(temporary, output, overwrite=False)

    assert output.read_bytes() == concurrent_data
    assert not list(tmp_path.glob(".output.epub.*.copying"))


def test_unsafe_no_clobber_platform_returns_structured_error(tmp_path, monkeypatch):
    source = tmp_path / "source.epub"
    output = tmp_path / "output.epub"
    make_epub(source)
    monkeypatch.setattr(
        archive_module.os, "link", lambda *_args: (_ for _ in ()).throw(OSError())
    )
    monkeypatch.setattr(archive_module, "_IS_WINDOWS", False)

    result = compress_epub(CompressionRequest(source, output=output))

    assert not result.success
    assert result.errors[0].code == "ArchiveError"
    assert "Atomic no-clobber" in result.errors[0].message
    assert not output.exists()
    assert not list(tmp_path.glob(".output.epub.*.copying"))
    assert not list(tmp_path.glob(".output.epub.*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="Windows rename semantics only")
def test_windows_rename_fallback_publishes_complete_file(tmp_path, monkeypatch):
    temporary = tmp_path / "temporary.epub"
    output = tmp_path / "output.epub"
    make_epub(temporary)
    monkeypatch.setattr(
        archive_module.os, "link", lambda *_args: (_ for _ in ()).throw(OSError())
    )

    archive_module.publish_epub(temporary, output, overwrite=False)

    archive_module.validate_output(output)


def test_overwrite_atomically_replaces_existing_output(tmp_path, monkeypatch):
    temporary = tmp_path / "temporary.epub"
    output = tmp_path / "output.epub"
    make_epub(temporary)
    expected = temporary.read_bytes()
    output.write_bytes(b"old output")
    monkeypatch.setattr(
        archive_module.os,
        "link",
        lambda *_args: (_ for _ in ()).throw(AssertionError("link must not be used")),
    )

    archive_module.publish_epub(temporary, output, overwrite=True)

    assert output.read_bytes() == expected
    assert not temporary.exists()


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
