from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

import ebook_toolbox.services.epub_toc_repair.repair as repair_module
from ebook_toolbox.services.epub_toc_repair import TocRepairRequest, repair_epub_toc


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def make_epub(
    path: Path,
    *,
    version: int,
    pages: list[tuple[str, str]],
    toc: str | None,
    toc_label: str = "旧目录",
    toc_target: str = "missing.xhtml",
    page_bodies: dict[str, str] | None = None,
) -> None:
    manifest = []
    spine = []
    for index, (name, _label) in enumerate(pages, 1):
        manifest.append(
            f'<item id="page{index}" href="Text/{name}" media-type="application/xhtml+xml"/>'
        )
        spine.append(f'<itemref idref="page{index}"/>')
    spine_toc = ""
    if toc in {"ncx", "both"}:
        manifest.append(
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        )
        spine_toc = ' toc="ncx"'
    if toc in {"nav", "both"}:
        manifest.append(
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
            'properties="nav"/>'
        )
    opf = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="{version}.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">test-id</dc:identifier><dc:title>测试合集</dc:title>
  </metadata>
  <manifest>{''.join(manifest)}</manifest>
  <spine{spine_toc}>{''.join(spine)}</spine>
</package>'''
    container = '''<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles><rootfile full-path="OEBPS/content.opf"
    media-type="application/oebps-package+xml"/></rootfiles>
</container>'''
    ncx = f'''<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <docTitle><text>测试合集</text></docTitle><navMap>
    <navPoint id="old" playOrder="1"><navLabel><text>{toc_label}</text></navLabel>
      <content src="{toc_target}"/></navPoint>
  </navMap>
</ncx>'''
    nav = f'''<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:epub="http://www.idpf.org/2007/ops"><head><title>目录</title></head>
  <body><nav epub:type="toc"><h1>目录</h1><ol>
    <li><a href="{toc_target}">{toc_label}</a></li>
  </ol></nav><nav epub:type="landmarks"><ol>
    <li><a href="Text/ch1.xhtml">正文</a></li></ol></nav></body>
</html>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED
        )
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/styles/book.css", b"body { color: #222; }")
        archive.writestr("OEBPS/Images/cover.bin", b"unchanged cover bytes")
        for name, label in pages:
            document = (page_bodies or {}).get(
                name,
                f'''<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{label}</title></head>
                <body><h1>{label}</h1><p>正文保持不变。</p></body></html>''',
            )
            archive.writestr(
                f"OEBPS/Text/{name}",
                document,
            )
        if toc in {"ncx", "both"}:
            archive.writestr("OEBPS/toc.ncx", ncx)
        if toc in {"nav", "both"}:
            archive.writestr("OEBPS/nav.xhtml", nav)


def make_epubmerge_epub(path: Path) -> None:
    master_items = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
    ]
    spine = []
    for volume in (1, 2):
        master_items.append(
            f'<item id="a{volume}rootfile" href="../{volume}/content.opf" '
            'media-type="application/oebps-package+xml"/>'
        )
        for chapter in (1, 2):
            item_id = f"a{volume}c{chapter}"
            master_items.append(
                f'<item id="{item_id}" href="Text/v{volume}c{chapter}.xhtml" '
                'media-type="application/xhtml+xml"/>'
            )
            spine.append(f'<itemref idref="{item_id}"/>')
    master_opf = f'''<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
      <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>合并合集</dc:title></metadata>
      <manifest>{''.join(master_items)}</manifest><spine>{''.join(spine)}</spine></package>'''
    container = '''<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
      <rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>'''
    nav = '''<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><ol>
      <li><a href="missing.xhtml">损坏目录</a></li></ol></nav></body></html>'''
    labels = {1: ("プロローグ", "第一章 起程"), 2: ("第一章 归来", "あとがき")}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED
        )
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", master_opf)
        archive.writestr("OEBPS/nav.xhtml", nav)
        for volume in (1, 2):
            source_opf = '''<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
              <manifest><item id="c1" href="Text/c1.xhtml"/>
              <item id="c2" href="Text/c2.xhtml"/></manifest><spine/></package>'''
            source_ncx = f'''<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">
              <navMap><navPoint><navLabel><text>{labels[volume][0]}</text></navLabel>
              <content src="Text/c1.xhtml"/></navPoint>
              <navPoint><navLabel><text>{labels[volume][1]}</text></navLabel>
              <content src="Text/c2.xhtml"/></navPoint></navMap></ncx>'''
            archive.writestr(f"{volume}/content.opf", source_opf)
            archive.writestr(f"{volume}/toc.ncx", source_ncx)
            for chapter in (1, 2):
                archive.writestr(
                    f"OEBPS/Text/v{volume}c{chapter}.xhtml",
                    '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>无固定格式标题</title>'
                    '</head><body><p>正文</p></body></html>',
                )


def labels_from_ncx(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("OEBPS/toc.ncx"))
    nav_map = next(node for node in root.iter() if local(node.tag) == "navMap")
    return [
        " ".join("".join(point.itertext()).split())
        for point in nav_map.iter()
        if local(point.tag) == "navLabel"
    ]


def labels_from_nav(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("OEBPS/nav.xhtml"))
    toc = next(
        node
        for node in root.iter()
        if local(node.tag) == "nav"
        and "toc" in node.get("{http://www.idpf.org/2007/ops}type", "").split()
    )
    return [
        " ".join("".join(node.itertext()).split())
        for node in toc.iter()
        if local(node.tag) == "a"
    ]


def standard_pages() -> list[tuple[str, str]]:
    return [("ch1.xhtml", "第一章 初遇"), ("ch2.xhtml", "第二章 重逢")]


def test_repairs_epub2_ncx_and_preserves_unrelated_members(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    make_epub(source, version=2, pages=standard_pages(), toc="ncx")
    before = hashlib.sha256(source.read_bytes()).digest()
    with zipfile.ZipFile(source) as archive:
        css_before = archive.read("OEBPS/styles/book.css")
        cover_before = archive.read("OEBPS/Images/cover.bin")

    result = repair_epub_toc(TocRepairRequest(source))

    assert result.success
    assert result.output == tmp_path / "book_toc_fixed.epub"
    assert labels_from_ncx(result.output) == ["第一章 初遇", "第二章 重逢"]
    assert hashlib.sha256(source.read_bytes()).digest() == before
    with zipfile.ZipFile(result.output) as archive:
        assert archive.read("OEBPS/styles/book.css") == css_before
        assert archive.read("OEBPS/Images/cover.bin") == cover_before


def test_repairs_epub3_nav_and_preserves_landmarks(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    make_epub(source, version=3, pages=standard_pages(), toc="nav")

    result = repair_epub_toc(TocRepairRequest(source))

    assert result.success
    assert labels_from_nav(result.output) == ["第一章 初遇", "第二章 重逢"]
    with zipfile.ZipFile(result.output) as archive:
        root = ET.fromstring(archive.read("OEBPS/nav.xhtml"))
    nav_types = {
        node.get("{http://www.idpf.org/2007/ops}type")
        for node in root.iter()
        if local(node.tag) == "nav"
    }
    assert nav_types == {"toc", "landmarks"}


def test_epub3_with_legacy_ncx_updates_both_navigation_documents(tmp_path: Path) -> None:
    source = tmp_path / "mixed.epub"
    make_epub(source, version=3, pages=standard_pages(), toc="both")

    result = repair_epub_toc(TocRepairRequest(source))

    assert result.success
    expected = ["第一章 初遇", "第二章 重逢"]
    assert labels_from_nav(result.output) == expected
    assert labels_from_ncx(result.output) == expected


@pytest.mark.parametrize("version,toc", [(2, "ncx"), (3, "nav")])
def test_builds_multivolume_chinese_hierarchy(
    tmp_path: Path, version: int, toc: str
) -> None:
    source = tmp_path / f"v{version}.epub"
    pages = [
        ("v1.xhtml", "第一卷 风起"),
        ("v1c1.xhtml", "第一章 启程"),
        ("v1c2.xhtml", "第二章 山雨"),
        ("v2.xhtml", "第二卷 星河"),
        ("v2c1.xhtml", "第一章 归来"),
        ("v2c2.xhtml", "第二章 新途"),
    ]
    make_epub(source, version=version, pages=pages, toc=toc)

    result = repair_epub_toc(TocRepairRequest(source))

    assert result.success
    assert [entry.label for entry in result.entries] == ["第一卷 风起", "第二卷 星河"]
    assert [[child.label for child in entry.children] for entry in result.entries] == [
        ["第一章 启程", "第二章 山雨"],
        ["第一章 归来", "第二章 新途"],
    ]


def test_recovers_epubmerge_per_volume_ncx_mapping(tmp_path: Path) -> None:
    source = tmp_path / "merged.epub"
    make_epubmerge_epub(source)

    result = repair_epub_toc(TocRepairRequest(source))

    assert result.success
    assert [entry.label for entry in result.entries] == ["第1卷", "第2卷"]
    assert [[child.label for child in entry.children] for entry in result.entries] == [
        ["序章", "第一章 起程"],
        ["第一章 归来", "后记"],
    ]
    assert "used_epubmerge_metadata" in {warning.code for warning in result.warnings}


def test_existing_toc_can_be_preserved_or_replaced(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    make_epub(
        source,
        version=3,
        pages=standard_pages(),
        toc="nav",
        toc_label="保留的目录名",
        toc_target="Text/ch1.xhtml",
    )

    preserved = repair_epub_toc(
        TocRepairRequest(source, output=tmp_path / "preserved.epub", replace_existing=False)
    )
    replaced = repair_epub_toc(
        TocRepairRequest(source, output=tmp_path / "replaced.epub", replace_existing=True)
    )

    assert preserved.success and replaced.success
    assert labels_from_nav(preserved.output) == ["保留的目录名"]
    assert preserved.modified_files == []
    assert {warning.code for warning in preserved.warnings} == {"existing_toc_preserved"}
    assert labels_from_nav(replaced.output) == ["第一章 初遇", "第二章 重逢"]


@pytest.mark.parametrize(
    "version,expected_file,warning",
    [(2, "toc.ncx", "ncx_created"), (3, "nav.xhtml", "nav_created")],
)
def test_missing_required_toc_is_created_and_referenced(
    tmp_path: Path, version: int, expected_file: str, warning: str
) -> None:
    source = tmp_path / f"missing-{version}.epub"
    make_epub(source, version=version, pages=standard_pages(), toc=None)

    result = repair_epub_toc(TocRepairRequest(source))

    assert result.success
    assert warning in {item.code for item in result.warnings}
    with zipfile.ZipFile(result.output) as archive:
        assert f"OEBPS/{expected_file}" in archive.namelist()
        opf = ET.fromstring(archive.read("OEBPS/content.opf"))
    items = [node for node in opf.iter() if local(node.tag) == "item"]
    if version == 2:
        ncx = next(item for item in items if item.get("href") == "toc.ncx")
        spine = next(node for node in opf.iter() if local(node.tag) == "spine")
        assert spine.get("toc") == ncx.get("id")
    else:
        nav = next(item for item in items if item.get("href") == "nav.xhtml")
        assert "nav" in nav.get("properties", "").split()


def test_unrecognized_chapters_return_error_without_output(tmp_path: Path) -> None:
    source = tmp_path / "unknown.epub"
    make_epub(
        source,
        version=3,
        pages=[("one.xhtml", "随笔"), ("two.xhtml", "附录资料")],
        toc=None,
    )
    before = source.read_bytes()

    result = repair_epub_toc(TocRepairRequest(source))

    assert not result.success
    assert result.errors[0].code == "chapters_not_recognized"
    assert source.read_bytes() == before
    assert not result.output.exists()


def test_recovers_chapters_from_visible_contents_page(tmp_path: Path) -> None:
    source = tmp_path / "visible.epub"
    make_epub(
        source,
        version=3,
        pages=[
            ("contents.xhtml", "目录"),
            ("one.xhtml", "春风又绿江南岸"),
            ("two.xhtml", "明月何时照我还"),
        ],
        toc=None,
        page_bodies={
            "contents.xhtml": '''<html xmlns="http://www.w3.org/1999/xhtml"><head><title>目录</title></head>
            <body><h1>目录</h1><ol>
            <li><a href="one.xhtml">第一章 春风又绿江南岸</a></li>
            <li><a href="two.xhtml">第二章 明月何时照我还</a></li>
            </ol></body></html>'''
        },
    )

    result = repair_epub_toc(TocRepairRequest(source))

    assert result.success
    assert [entry.label for entry in result.entries] == [
        "第一章 春风又绿江南岸",
        "第二章 明月何时照我还",
    ]
    assert "used_visible_toc" in {warning.code for warning in result.warnings}


def test_existing_output_is_not_overwritten(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    output = tmp_path / "existing.epub"
    make_epub(source, version=3, pages=standard_pages(), toc="nav")
    output.write_bytes(b"keep me")

    result = repair_epub_toc(TocRepairRequest(source, output=output))

    assert not result.success
    assert result.errors[0].code == "output_exists"
    assert output.read_bytes() == b"keep me"


def test_overwrite_replaces_output_but_never_source(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    output = tmp_path / "existing.epub"
    make_epub(source, version=3, pages=standard_pages(), toc="nav")
    source_before = source.read_bytes()
    output.write_bytes(b"old output")

    result = repair_epub_toc(TocRepairRequest(source, output=output, overwrite=True))

    assert result.success
    assert source.read_bytes() == source_before
    assert zipfile.is_zipfile(output)
    assert output.read_bytes() != b"old output"


def test_corrupt_epub_returns_structured_error(tmp_path: Path) -> None:
    source = tmp_path / "broken.epub"
    source.write_bytes(b"not a zip")
    before = source.read_bytes()

    result = repair_epub_toc(TocRepairRequest(source))

    assert not result.success
    assert result.errors[0].code == "archive_error"
    assert source.read_bytes() == before
    assert not result.output.exists()


def test_reports_real_repair_stages(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    make_epub(source, version=3, pages=standard_pages(), toc="nav")
    updates = []

    result = repair_epub_toc(TocRepairRequest(source), progress_callback=updates.append)

    assert result.success
    assert result.completed_stages == result.total_stages == 5
    assert [(update.stage, update.completed) for update in updates] == [
        ("extracting", 0),
        ("analyzing", 1),
        ("building", 2),
        ("validating", 3),
        ("publishing", 4),
        ("completed", 5),
    ]


def test_cancellation_after_analysis_does_not_create_output(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    make_epub(source, version=3, pages=standard_pages(), toc="nav")
    source_before = source.read_bytes()
    cancel_requested = False

    def record_progress(update) -> None:
        nonlocal cancel_requested
        if update.stage == "analyzing":
            cancel_requested = True

    result = repair_epub_toc(
        TocRepairRequest(source),
        progress_callback=record_progress,
        should_cancel=lambda: cancel_requested,
    )

    assert result.cancelled
    assert not result.success
    assert result.completed_stages == 2
    assert source.read_bytes() == source_before
    assert not result.output.exists()


def test_cancellation_after_build_preserves_existing_output_and_cleans_temp(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "book.epub"
    output = tmp_path / "existing.epub"
    make_epub(source, version=3, pages=standard_pages(), toc="nav")
    output.write_bytes(b"old output")
    cancel_requested = False
    original_build = repair_module.build_epub

    def build_then_cancel(root, destination) -> None:
        nonlocal cancel_requested
        original_build(root, destination)
        cancel_requested = True

    monkeypatch.setattr(repair_module, "build_epub", build_then_cancel)

    result = repair_epub_toc(
        TocRepairRequest(source, output=output, overwrite=True),
        should_cancel=lambda: cancel_requested,
    )

    assert result.cancelled
    assert not result.success
    assert result.completed_stages == 3
    assert output.read_bytes() == b"old output"
    assert list(tmp_path.glob(f".{output.name}.*.tmp")) == []
