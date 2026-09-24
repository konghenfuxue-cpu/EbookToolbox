from __future__ import annotations

import hashlib
import posixpath
import zipfile
from pathlib import Path
from urllib.parse import quote, unquote
from xml.etree import ElementTree as ET

import pytest

from ebook_toolbox.services.epub_structural_repair import (
    RepairRequest,
    repair_epub_structure,
)
from ebook_toolbox.services.epub_structural_repair import repair as repair_module


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def make_epub(
    path: Path,
    *,
    opf_path: str = "OEBPS/content.opf",
    chapter_path: str = "OEBPS/Text/chapter.xhtml",
    chapter_data: str = "<html xmlns='http://www.w3.org/1999/xhtml'><body>正文</body></html>",
    extra_members: dict[str, bytes | str] | None = None,
    extra_manifest: list[tuple[str, str, str]] | None = None,
    spine_ids: list[str] | None = None,
    language: str | None = "zh-CN",
    container_data: str | None = None,
    opf_data: str | None = None,
) -> None:
    opf_dir = posixpath.dirname(opf_path) or "."
    chapter_href = posixpath.relpath(chapter_path, opf_dir)
    manifest = [
        ("chapter", chapter_href, "application/xhtml+xml"),
        *(extra_manifest or []),
    ]
    language_xml = f"<dc:language>{language}</dc:language>" if language is not None else ""
    if opf_data is None:
        manifest_xml = "".join(
            f'<item id="{item_id}" href="{href}" media-type="{media}"/>'
            for item_id, href, media in manifest
        )
        spine_xml = "".join(
            f'<itemref idref="{item_id}"/>' for item_id in (spine_ids or ["chapter"])
        )
        opf_data = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>测试书</dc:title>{language_xml}
  </metadata>
  <manifest>{manifest_xml}</manifest>
  <spine>{spine_xml}</spine>
</package>"""
    if container_data is None:
        container_data = f'''<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles><rootfile full-path="{opf_path}"/></rootfiles>
</container>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container_data)
        archive.writestr(opf_path, opf_data)
        archive.writestr(chapter_path, chapter_data)
        for name, data in (extra_members or {}).items():
            archive.writestr(name, data)


def read_opf(path: Path) -> tuple[zipfile.ZipFile, ET.Element, str]:
    archive = zipfile.ZipFile(path)
    container = ET.fromstring(archive.read("META-INF/container.xml"))
    opf_path = next(
        node.get("full-path", "") for node in container.iter() if local(node.tag) == "rootfile"
    )
    return archive, ET.fromstring(archive.read(unquote(opf_path))), unquote(opf_path)


def manifest_by_id(root: ET.Element) -> dict[str, ET.Element]:
    return {item.get("id", ""): item for item in root.iter() if local(item.tag) == "item"}


def test_direct_image_spine_is_detected_without_modifying_it(tmp_path: Path) -> None:
    source = tmp_path / "image-spine.epub"
    image = "OEBPS/Images/page-1.bin"
    make_epub(
        source,
        extra_members={image: b"jpeg bytes"},
        extra_manifest=[("page-image", "Images/page-1.bin", "image/jpeg")],
        spine_ids=["page-image"],
    )

    result = repair_epub_structure(RepairRequest(source))

    assert result.success
    assert result.detected_image_spine_items == [image]
    assert result.wrapped_image_spine_items == []
    assert "image_spine_detected" in {warning.code for warning in result.warnings}
    archive, root, _opf_path = read_opf(result.output)
    with archive:
        assert archive.read(image) == b"jpeg bytes"
        assert [node.get("idref") for node in root.iter() if local(node.tag) == "itemref"] == [
            "page-image"
        ]


def test_direct_image_spine_can_be_wrapped_in_generated_xhtml(tmp_path: Path) -> None:
    source = tmp_path / "wrapped-image-spine.epub"
    image = "OEBPS/Images/page 1.jpg"
    make_epub(
        source,
        extra_members={image: b"jpeg bytes"},
        extra_manifest=[("page-image", "Images/page%201.jpg", "image/jpeg")],
        spine_ids=["page-image"],
    )

    result = repair_epub_structure(RepairRequest(source, wrap_image_spine=True))

    assert result.success
    assert result.detected_image_spine_items == [image]
    assert result.wrapped_image_spine_items == [image]
    assert "image_spine_wrapped" in {warning.code for warning in result.warnings}
    archive, root, opf_path = read_opf(result.output)
    with archive:
        manifest = manifest_by_id(root)
        spine_id = next(
            node.get("idref", "") for node in root.iter() if local(node.tag) == "itemref"
        )
        assert spine_id.startswith("repair_image_spine_")
        wrapper = posixpath.normpath(
            posixpath.join(posixpath.dirname(opf_path), unquote(manifest[spine_id].get("href", "")))
        )
        wrapper_data = archive.read(wrapper).decode("utf-8")
        assert "../Images/page%201.jpg" in wrapper_data
        assert archive.read(image) == b"jpeg bytes"


def test_legal_epub_needs_no_rename_and_preserves_member_payloads(tmp_path: Path) -> None:
    source = tmp_path / "legal.epub"
    make_epub(source)
    source_hash = hashlib.sha256(source.read_bytes()).digest()
    with zipfile.ZipFile(source) as archive:
        chapter_before = archive.read("OEBPS/Text/chapter.xhtml")
        opf_before = archive.read("OEBPS/content.opf")

    result = repair_epub_structure(RepairRequest(source))

    assert result.success
    assert result.output == tmp_path / "legal_repaired.epub"
    assert result.renamed_resources == []
    assert source_hash == hashlib.sha256(source.read_bytes()).digest()
    with zipfile.ZipFile(result.output) as archive:
        assert archive.read("OEBPS/Text/chapter.xhtml") == chapter_before
        assert archive.read("OEBPS/content.opf") == opf_before


def test_windows_illegal_names_and_xhtml_references_are_rewritten(tmp_path: Path) -> None:
    source = tmp_path / "illegal.epub"
    chapter = "OEBPS/Text/bad:chapter.xhtml"
    image = "OEBPS/Images/bad*image.png"
    make_epub(
        source,
        chapter_path=chapter,
        chapter_data=(
            "<html xmlns='http://www.w3.org/1999/xhtml'><body>"
            "<img src='../Images/bad*image.png?size=2#cover'/>"
            "<a href='../Images/bad*image.png#download'>图片</a></body></html>"
        ),
        extra_members={image: b"image bytes"},
        extra_manifest=[("image", "Images/bad*image.png", "image/png")],
    )

    result = repair_epub_structure(RepairRequest(source))

    assert result.success
    renamed = {item.original_path: item.repaired_path for item in result.renamed_resources}
    assert chapter in renamed and image in renamed
    assert ":" not in renamed[chapter]
    assert "*" not in renamed[image]
    expected = quote(
        posixpath.relpath(renamed[image], posixpath.dirname(renamed[chapter])),
        safe="/-._~",
    )
    with zipfile.ZipFile(result.output) as archive:
        document = archive.read(renamed[chapter]).decode()
        assert f"{expected}?size=2#cover" in document
        assert f"{expected}#download" in document
        assert archive.read(renamed[image]) == b"image bytes"


def test_css_url_and_import_references_are_rewritten(tmp_path: Path) -> None:
    source = tmp_path / "css.epub"
    main_css = "OEBPS/Styles/main:style.css"
    imported_css = "OEBPS/Styles/theme?style.css"
    image = "OEBPS/Images/pic|one.png"
    make_epub(
        source,
        extra_members={
            main_css: (
                "@import 'theme%3Fstyle.css#theme'; "
                "body{background:url('../Images/pic|one.png?v=1#x')}"
            ),
            imported_css: "body { color: black; }",
            image: b"png",
        },
        extra_manifest=[
            ("main-css", "Styles/main:style.css", "text/css"),
            ("theme-css", "Styles/theme%3Fstyle.css", "text/css"),
            ("image", "Images/pic|one.png", "image/png"),
        ],
    )

    result = repair_epub_structure(RepairRequest(source))

    assert result.success
    renamed = {item.original_path: item.repaired_path for item in result.renamed_resources}
    with zipfile.ZipFile(result.output) as archive:
        css = archive.read(renamed[main_css]).decode()
    imported_relative = posixpath.relpath(
        renamed[imported_css], posixpath.dirname(renamed[main_css])
    )
    image_relative = posixpath.relpath(renamed[image], posixpath.dirname(renamed[main_css]))
    assert f"{imported_relative}#theme" in css
    assert f"{image_relative}?v=1#x" in css


def test_ncx_svg_and_smil_references_are_rewritten(tmp_path: Path) -> None:
    source = tmp_path / "linked-formats.epub"
    chapter = "OEBPS/Text/ch:one.xhtml"
    ncx = "OEBPS/toc:one.ncx"
    svg = "OEBPS/Images/diagram:one.svg"
    smil = "OEBPS/Overlay/track:one.smil"
    image = "OEBPS/Images/pic*one.png"
    audio = "OEBPS/Audio/voice|one.mp3"
    make_epub(
        source,
        chapter_path=chapter,
        extra_members={
            ncx: (
                "<ncx xmlns='http://www.daisy.org/z3986/2005/ncx/'><navMap><navPoint>"
                "<content src='Text/ch:one.xhtml?view=1#start'/></navPoint></navMap></ncx>"
            ),
            svg: (
                "<svg xmlns='http://www.w3.org/2000/svg'><image href='pic*one.png#layer'/></svg>"
            ),
            smil: (
                "<smil><body><par><text src='../Text/ch:one.xhtml#p1'/>"
                "<audio src='../Audio/voice|one.mp3?clip=1'/></par></body></smil>"
            ),
            image: b"png",
            audio: b"audio",
        },
        extra_manifest=[
            ("ncx", "toc:one.ncx", "application/x-dtbncx+xml"),
            ("svg", "Images/diagram:one.svg", "image/svg+xml"),
            ("smil", "Overlay/track:one.smil", "application/smil+xml"),
            ("image", "Images/pic*one.png", "image/png"),
            ("audio", "Audio/voice|one.mp3", "audio/mpeg"),
        ],
    )

    result = repair_epub_structure(RepairRequest(source))

    assert result.success
    renamed = {item.original_path: item.repaired_path for item in result.renamed_resources}
    with zipfile.ZipFile(result.output) as archive:
        rewritten_ncx = archive.read(renamed[ncx]).decode()
        rewritten_svg = archive.read(renamed[svg]).decode()
        rewritten_smil = archive.read(renamed[smil]).decode()
    assert "ch:one.xhtml" not in rewritten_ncx
    assert "?view=1#start" in rewritten_ncx
    assert "pic*one.png" not in rewritten_svg
    assert "#layer" in rewritten_svg
    assert "ch:one.xhtml" not in rewritten_smil
    assert "voice|one.mp3" not in rewritten_smil
    assert "#p1" in rewritten_smil and "?clip=1" in rewritten_smil


def test_case_and_unicode_normalization_collisions_are_made_distinct(tmp_path: Path) -> None:
    source = tmp_path / "collision.epub"
    paths = [
        "OEBPS/Images/Cover.JPG",
        "OEBPS/Images/cover.jpg",
        "OEBPS/Images/é.png",
        "OEBPS/Images/é.png",
    ]
    make_epub(
        source,
        extra_members={
            paths[0]: b"upper",
            paths[1]: b"lower",
            paths[2]: b"decomposed",
            paths[3]: b"composed",
        },
        extra_manifest=[
            ("upper", "Images/Cover.JPG", "image/jpeg"),
            ("lower", "Images/cover.jpg", "image/jpeg"),
            ("unicode", "Images/é.png", "image/png"),
            ("unicode-composed", "Images/é.png", "image/png"),
        ],
    )

    result = repair_epub_structure(RepairRequest(source))

    assert result.success
    with zipfile.ZipFile(result.output) as archive:
        names = archive.namelist()
        assert len(names) == len({name.casefold() for name in names})
        payloads = {archive.read(name) for name in names if name.endswith((".jpg", ".JPG"))}
    assert payloads == {b"upper", b"lower"}


def test_missing_manifest_registration_and_language_are_repaired(tmp_path: Path) -> None:
    source = tmp_path / "metadata.epub"
    make_epub(
        source,
        language=None,
        extra_members={"OEBPS/Images/封面.png": b"cover"},
    )

    result = repair_epub_structure(RepairRequest(source))

    assert result.success
    assert result.language_added
    assert "OEBPS/Images/封面.png" in result.added_manifest_resources
    archive, root, opf_path = read_opf(result.output)
    with archive:
        language = next(node for node in root.iter() if local(node.tag) == "language")
        assert "".join(language.itertext()) == "zh-CN"
        added = next(
            item
            for item in root.iter()
            if local(item.tag) == "item" and item.get("id", "").startswith("repair_resource_")
        )
        target = posixpath.normpath(
            posixpath.join(posixpath.dirname(opf_path), unquote(added.get("href", "")))
        )
        assert target == "OEBPS/Images/封面.png"


def test_missing_manifest_target_and_invalid_spine_item_are_removed(tmp_path: Path) -> None:
    source = tmp_path / "broken-opf.epub"
    make_epub(
        source,
        extra_manifest=[("missing", "Images/gone.png", "image/png")],
        spine_ids=["chapter", "missing", "ghost"],
    )

    result = repair_epub_structure(RepairRequest(source))

    assert result.success
    assert "missing" in result.removed_manifest_items
    assert result.removed_spine_items == ["missing", "ghost"]
    archive, root, _opf_path = read_opf(result.output)
    with archive:
        assert "missing" not in manifest_by_id(root)
        assert [node.get("idref") for node in root.iter() if local(node.tag) == "itemref"] == [
            "chapter"
        ]


def test_legal_chinese_paths_and_names_are_preserved(tmp_path: Path) -> None:
    source = tmp_path / "中文.epub"
    chapter = "OEBPS/正文/第一章.xhtml"
    make_epub(source, chapter_path=chapter)

    result = repair_epub_structure(RepairRequest(source))

    assert result.success
    assert all(item.original_path != chapter for item in result.renamed_resources)
    with zipfile.ZipFile(result.output) as archive:
        assert chapter in archive.namelist()


def test_output_exists_is_structured_and_source_is_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "source.epub"
    output = tmp_path / "existing.epub"
    make_epub(source)
    before = hashlib.sha256(source.read_bytes()).digest()
    output.write_bytes(b"keep")

    result = repair_epub_structure(RepairRequest(source, output=output))

    assert not result.success
    assert result.errors[0].code == "output_exists"
    assert output.read_bytes() == b"keep"
    assert hashlib.sha256(source.read_bytes()).digest() == before


def test_build_failure_cleans_owned_temporary_and_preserves_input(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source.epub"
    output = tmp_path / "output.epub"
    make_epub(source)
    before = hashlib.sha256(source.read_bytes()).digest()

    def fail_build(temporary_file, *_args, **_kwargs):
        temporary_file.write(b"partial")
        temporary_file.flush()
        raise OSError("simulated build failure")

    monkeypatch.setattr(repair_module, "_build_repaired_epub", fail_build)

    result = repair_epub_structure(RepairRequest(source, output=output))

    assert not result.success
    assert any(error.message == "simulated build failure" for error in result.errors)
    assert not output.exists()
    assert not list(tmp_path.glob(".output.epub.*.tmp.epub"))
    assert hashlib.sha256(source.read_bytes()).digest() == before


@pytest.mark.parametrize(
    ("name", "data", "expected"),
    [
        ("broken.epub", b"not a zip", "archive_error"),
        ("bad-container.epub", None, "archive_error"),
        ("bad-opf.epub", None, "archive_error"),
    ],
)
def test_corrupt_zip_container_and_opf_are_rejected(
    tmp_path: Path, name: str, data: bytes | None, expected: str
) -> None:
    source = tmp_path / name
    if data is not None:
        source.write_bytes(data)
    elif name == "bad-container.epub":
        make_epub(source, container_data="<broken")
    else:
        make_epub(source, opf_data="<broken")

    result = repair_epub_structure(RepairRequest(source))

    assert not result.success
    assert result.errors[0].code == expected
    assert not result.output.exists()


def test_unsafe_and_duplicate_zip_paths_are_rejected(tmp_path: Path) -> None:
    unsafe = tmp_path / "unsafe.epub"
    make_epub(unsafe)
    with zipfile.ZipFile(unsafe, "a") as archive:
        archive.writestr("../escape.xhtml", "bad")
    duplicate = tmp_path / "duplicate.epub"
    make_epub(duplicate)
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(duplicate, "a") as archive:
            archive.writestr("OEBPS/Text/chapter.xhtml", "duplicate")

    unsafe_result = repair_epub_structure(RepairRequest(unsafe))
    duplicate_result = repair_epub_structure(RepairRequest(duplicate))

    assert not unsafe_result.success
    assert "Unsafe ZIP member path" in unsafe_result.errors[0].message
    assert not duplicate_result.success
    assert "Duplicate ZIP member name" in duplicate_result.errors[0].message


def test_reference_restores_only_exact_missing_manifest_resource(tmp_path: Path) -> None:
    source = tmp_path / "source.epub"
    reference = tmp_path / "reference.epub"
    missing = "OEBPS/Images/missing.png"
    make_epub(
        source,
        extra_manifest=[("missing", "Images/missing.png", "image/png")],
    )
    make_epub(
        reference,
        extra_members={missing: b"restored bytes"},
        extra_manifest=[("missing", "Images/missing.png", "image/png")],
    )

    result = repair_epub_structure(RepairRequest(source, reference=reference))

    assert result.success
    assert result.restored_resources == [missing]
    with zipfile.ZipFile(result.output) as archive:
        assert archive.read(missing) == b"restored bytes"


def test_reference_mismatch_refuses_filename_guessing(tmp_path: Path) -> None:
    source = tmp_path / "source.epub"
    reference = tmp_path / "reference.epub"
    make_epub(
        source,
        extra_manifest=[("missing", "Images/missing.png", "image/png")],
    )
    make_epub(
        reference,
        extra_members={"Other/missing.png": b"same basename, wrong path"},
        extra_manifest=[("other", "../Other/missing.png", "image/png")],
    )

    result = repair_epub_structure(RepairRequest(source, reference=reference))

    assert not result.success
    assert result.errors[0].code == "reference_resource_mismatch"
    assert not result.output.exists()


def test_optional_calibre_absence_is_only_a_warning(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "calibre.epub"
    make_epub(source)
    monkeypatch.setattr(repair_module.shutil, "which", lambda _name: None)

    result = repair_epub_structure(RepairRequest(source, run_calibre_check=True))

    assert result.success
    assert result.calibre_status == "not_installed"
    assert "calibre_not_installed" in {warning.code for warning in result.warnings}


def test_illegal_opf_path_is_renamed_and_container_reference_is_updated(tmp_path: Path) -> None:
    source = tmp_path / "opf-name.epub"
    old_opf = "OEBPS/bad:content.opf"
    make_epub(source, opf_path=old_opf)

    result = repair_epub_structure(RepairRequest(source))

    assert result.success
    renamed = {item.original_path: item.repaired_path for item in result.renamed_resources}
    assert old_opf in renamed
    with zipfile.ZipFile(result.output) as archive:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        new_opf = next(
            node.get("full-path", "") for node in container.iter() if local(node.tag) == "rootfile"
        )
        assert unquote(new_opf) == renamed[old_opf]
        assert renamed[old_opf] in archive.namelist()
