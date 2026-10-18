from __future__ import annotations

import copy
import io
import posixpath
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

SOURCE: Path
OUTPUT: Path

CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
OPF_NS = "http://www.idpf.org/2007/opf"
XHTML_NS = "http://www.w3.org/1999/xhtml"
EPUB_NS = "http://www.idpf.org/2007/ops"
NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"

PAGE_NAME = "resource-share.xhtml"
PAGE_ID = "codex-resource-share"
NAV_TITLE = "病娇・重女资源分享"

PAGE = Path(__file__).with_name("resource_share_page.xhtml").read_bytes()


def namespaces(xml: bytes) -> None:
    for _, value in ET.iterparse(io.BytesIO(xml), events=("start-ns",)):
        prefix, uri = value
        try:
            ET.register_namespace(prefix, uri)
        except ValueError:
            pass


def xml_bytes(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def safe_members(infos: list[zipfile.ZipInfo]) -> bool:
    for info in infos:
        p = PurePosixPath(info.filename.replace("\\", "/"))
        if p.is_absolute() or ".." in p.parts:
            return False
    return True


def unique_id(root: ET.Element, base: str) -> str:
    ids = {x.get("id") for x in root.iter() if x.get("id")}
    value, n = base, 2
    while value in ids:
        value, n = f"{base}-{n}", n + 1
    return value


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def matching_descendants(root: ET.Element, name: str):
    return (element for element in root.iter() if local_name(element.tag) == name)


def add_to_nav(data: bytes, nav_path: str, page_path: str) -> tuple[bytes, bool]:
    try:
        namespaces(data)
        root = ET.fromstring(data)
    except ET.ParseError:
        return data, False
    rel = posixpath.relpath(page_path, posixpath.dirname(nav_path) or ".")
    for nav in matching_descendants(root, "nav"):
        nav_type = nav.get(f"{{{EPUB_NS}}}type") or nav.get("type") or ""
        if "toc" not in nav_type.split():
            continue
        target = next((child for child in nav if local_name(child.tag) == "ol"), None)
        if target is None:
            continue
        parent_map = {child: parent for parent in root.iter() for child in parent}
        matches: list[tuple[ET.Element, ET.Element]] = []
        for li in matching_descendants(target, "li"):
            anchor = next((child for child in li if local_name(child.tag) == "a"), None)
            href = ((anchor.get("href") if anchor is not None else "") or "").split(
                "#", 1
            )[0]
            if (
                href
                and posixpath.normpath(
                    posixpath.join(posixpath.dirname(nav_path), href)
                )
                == page_path
            ):
                matches.append((li, anchor))
        if matches:
            keep_li, keep_anchor = matches[0]
            keep_anchor.set("href", rel)
            keep_anchor.text = NAV_TITLE
            for child in list(keep_li):
                if local_name(child.tag) == "ol":
                    keep_li.remove(child)
            for duplicate_li, _ in matches[1:]:
                parent = parent_map.get(duplicate_li)
                if parent is not None and duplicate_li in list(parent):
                    parent.remove(duplicate_li)
            return xml_bytes(root), True
        namespace = (
            target.tag[: target.tag.index("}") + 1]
            if target.tag.startswith("{")
            else ""
        )
        li = ET.Element(f"{namespace}li")
        a = ET.SubElement(li, f"{namespace}a", {"href": rel})
        a.text = NAV_TITLE
        target.insert(0, li)
        return xml_bytes(root), True
    return data, False


def add_to_ncx(data: bytes, ncx_path: str, page_path: str) -> tuple[bytes, bool]:
    try:
        namespaces(data)
        root = ET.fromstring(data)
    except ET.ParseError:
        return data, False
    nav_map = next(matching_descendants(root, "navMap"), None)
    if nav_map is None:
        return data, False
    rel = posixpath.relpath(page_path, posixpath.dirname(ncx_path) or ".")
    parent_map = {child: parent for parent in root.iter() for child in parent}
    matches: list[tuple[ET.Element, ET.Element]] = []
    for old_point in matching_descendants(nav_map, "navPoint"):
        content = next(
            (child for child in old_point if local_name(child.tag) == "content"), None
        )
        src = ((content.get("src") if content is not None else "") or "").split("#", 1)[
            0
        ]
        if (
            src
            and posixpath.normpath(posixpath.join(posixpath.dirname(ncx_path), src))
            == page_path
        ):
            matches.append((old_point, content))
    if matches:
        keep_point, keep_content = matches[0]
        keep_content.set("src", rel)
        label = next(
            (element for element in matching_descendants(keep_point, "text")), None
        )
        if label is not None:
            label.text = NAV_TITLE
        for child in list(keep_point):
            if local_name(child.tag) == "navPoint":
                keep_point.remove(child)
        for duplicate_point, _ in matches[1:]:
            parent = parent_map.get(duplicate_point)
            if parent is not None and duplicate_point in list(parent):
                parent.remove(duplicate_point)
        return xml_bytes(root), True
    namespace = (
        nav_map.tag[: nav_map.tag.index("}") + 1] if nav_map.tag.startswith("{") else ""
    )
    point = ET.Element(
        f"{namespace}navPoint", {"id": "nav-resource-share", "playOrder": "1"}
    )
    label = ET.SubElement(point, f"{namespace}navLabel")
    ET.SubElement(label, f"{namespace}text").text = NAV_TITLE
    ET.SubElement(point, f"{namespace}content", {"src": rel})
    for old_point in (
        child for child in nav_map if local_name(child.tag) == "navPoint"
    ):
        old_order = old_point.get("playOrder")
        if old_order and old_order.isdigit():
            old_point.set("playOrder", str(int(old_order) + 1))
    nav_map.insert(0, point)
    return xml_bytes(root), True


def reading_order_insert_index(
    zin: zipfile.ZipFile,
    manifest: ET.Element,
    spine: ET.Element,
    opf_dir: str,
    names: set[str],
) -> int:
    """Place the new page after cover/front matter and before the first chapter."""
    items = {
        item.get("id"): item
        for item in manifest.findall(f"{{{OPF_NS}}}item")
        if item.get("id")
    }
    refs = spine.findall(f"{{{OPF_NS}}}itemref")
    cover_index = -1
    chapter_index: int | None = None

    for index, ref in enumerate(refs):
        item = items.get(ref.get("idref"))
        if item is None or not item.get("href"):
            continue
        href = item.get("href", "").split("#", 1)[0]
        full = posixpath.normpath(posixpath.join(opf_dir, href))
        label = " ".join((item.get("id", ""), href, item.get("properties", ""))).lower()
        sample = b""
        if full in names and (item.get("media-type") or "") in {
            "application/xhtml+xml",
            "text/html",
        }:
            try:
                sample = zin.read(full)[:131072].lower()
            except (KeyError, OSError):
                pass

        is_cover = bool(
            re.search(r"(^|[^a-z])(cover|titlepage|coverpage)([^a-z]|$)", label)
        ) or any(
            token in sample
            for token in (
                b'epub:type="cover"',
                b"epub:type='cover'",
                b'name="cover"',
                b"name='cover'",
            )
        )
        is_chapter = bool(
            re.search(r"(^|[^a-z])(chapter|chap|ch\d+)([^a-z]|$)", label)
        ) or any(
            token in sample
            for token in (
                b'epub:type="chapter"',
                b"epub:type='chapter'",
                b'role="doc-chapter"',
                b"role='doc-chapter'",
                b'class="chapter"',
                b"class='chapter'",
            )
        )
        if is_cover:
            cover_index = index
        if chapter_index is None and is_chapter:
            chapter_index = index

    if chapter_index is not None and chapter_index > cover_index:
        return chapter_index
    if cover_index >= 0:
        return cover_index + 1
    return 0


def process(epub: Path) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(epub, "r") as zin:
            infos = zin.infolist()
            if not safe_members(infos):
                return "跳过", "包含不安全路径"
            names = {i.filename for i in infos}
            if "META-INF/encryption.xml" in names:
                return "跳过", "发现加密声明"
            if "META-INF/container.xml" not in names:
                return "跳过", "缺少 container.xml"
            container_data = zin.read("META-INF/container.xml")
            container = ET.fromstring(container_data)
            roots = container.findall(f".//{{{CONTAINER_NS}}}rootfile")
            if len(roots) != 1 or not roots[0].get("full-path"):
                return "跳过", f"OPF 根文件数量异常：{len(roots)}"
            opf_path = roots[0].get("full-path").replace("\\", "/")
            if opf_path not in names:
                return "跳过", "container 指向的 OPF 不存在"
            opf_data = zin.read(opf_path)
            namespaces(opf_data)
            opf = ET.fromstring(opf_data)
            manifest = opf.find(f"{{{OPF_NS}}}manifest")
            spine = opf.find(f"{{{OPF_NS}}}spine")
            if manifest is None or spine is None:
                return "跳过", "OPF 缺少 manifest 或 spine"

            opf_dir = posixpath.dirname(opf_path)
            page_path = posixpath.join(opf_dir, PAGE_NAME) if opf_dir else PAGE_NAME
            existing_item = None
            for item in manifest.findall(f"{{{OPF_NS}}}item"):
                href = (item.get("href") or "").split("#", 1)[0]
                full = posixpath.normpath(posixpath.join(opf_dir, href)) if href else ""
                if full == page_path or item.get("id") == PAGE_ID:
                    existing_item = item
                    break

            if existing_item is None:
                item_id = unique_id(opf, PAGE_ID)
                existing_item = ET.SubElement(
                    manifest,
                    f"{{{OPF_NS}}}item",
                    {
                        "id": item_id,
                        "href": PAGE_NAME,
                        "media-type": "application/xhtml+xml",
                    },
                )
            else:
                item_id = existing_item.get("id") or unique_id(opf, PAGE_ID)
                existing_item.set("id", item_id)
                existing_item.set("href", PAGE_NAME)
                existing_item.set("media-type", "application/xhtml+xml")

            if not any(
                ref.get("idref") == item_id
                for ref in spine.findall(f"{{{OPF_NS}}}itemref")
            ):
                insert_at = reading_order_insert_index(
                    zin, manifest, spine, opf_dir, names
                )
                spine.insert(
                    insert_at, ET.Element(f"{{{OPF_NS}}}itemref", {"idref": item_id})
                )

            replacements: dict[str, bytes] = {opf_path: xml_bytes(opf), page_path: PAGE}
            nav_added = False
            for item in manifest.findall(f"{{{OPF_NS}}}item"):
                href = item.get("href")
                if not href:
                    continue
                full = posixpath.normpath(
                    posixpath.join(opf_dir, href.split("#", 1)[0])
                )
                props = (item.get("properties") or "").split()
                media = item.get("media-type") or ""
                if "nav" in props and full in names:
                    replacements[full], changed = add_to_nav(
                        zin.read(full), full, page_path
                    )
                    nav_added = nav_added or changed
                if media == "application/x-dtbncx+xml" and full in names:
                    replacements[full], changed = add_to_ncx(
                        zin.read(full), full, page_path
                    )
                    nav_added = nav_added or changed

            OUTPUT.mkdir(parents=True, exist_ok=True)
            out = OUTPUT / epub.name
            with zipfile.ZipFile(out, "w") as zout:
                mime_info = next((i for i in infos if i.filename == "mimetype"), None)
                if mime_info is None:
                    return "跳过", "缺少 mimetype"
                new_mime = copy.copy(mime_info)
                new_mime.compress_type = zipfile.ZIP_STORED
                zout.writestr(new_mime, b"application/epub+zip")
                for info in infos:
                    if info.filename == "mimetype":
                        continue
                    payload = replacements.get(info.filename)
                    if payload is None:
                        payload = zin.read(info.filename)
                    zout.writestr(info, payload)
                if page_path not in names:
                    page_info = zipfile.ZipInfo(page_path)
                    page_info.compress_type = zipfile.ZIP_DEFLATED
                    page_info.external_attr = 0o644 << 16
                    zout.writestr(page_info, PAGE)

        with zipfile.ZipFile(out, "r") as check:
            bad = check.testzip()
            first = check.infolist()[0]
            if (
                bad
                or first.filename != "mimetype"
                or first.compress_type != zipfile.ZIP_STORED
            ):
                out.unlink(missing_ok=True)
                return "失败", f"输出验证失败：{bad or 'mimetype 包装异常'}"
            if page_path not in check.namelist():
                out.unlink(missing_ok=True)
                return "失败", "输出中未找到资源页"
        action = "已更新资源页" if page_path in names else "已添加资源页"
        return (
            "完成",
            f"{action}；目录项：{'已确认' if nav_added else '未找到可更新的导航文档'}",
        )
    except Exception as exc:
        return "失败", f"{type(exc).__name__}: {exc}"


def main() -> int:
    global SOURCE, OUTPUT
    if len(sys.argv) != 2:
        print("用法：请把 EPUB 文件或包含 EPUB 的文件夹拖到 CMD 启动器上。")
        return 2
    SOURCE = Path(sys.argv[1]).expanduser()
    if SOURCE.is_file() and SOURCE.suffix.lower() == ".epub":
        OUTPUT = SOURCE.parent / "已添加资源分享页"
        files = [SOURCE]
    elif SOURCE.is_dir():
        OUTPUT = SOURCE / "已添加资源分享页"
        files = sorted(SOURCE.glob("*.epub"))
    else:
        print(f"错误：输入路径不是 EPUB 文件或有效文件夹：{SOURCE}")
        return 2
    if not files:
        print(f"未找到 EPUB：{SOURCE}")
        return 1
    failures = 0
    for epub in files:
        status, detail = process(epub)
        print(f"{status}\t{epub.name}\t{detail}")
        if status != "完成":
            failures += 1
    print(f"合计\t{len(files)} 本\t成功 {len(files) - failures}，跳过或失败 {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
