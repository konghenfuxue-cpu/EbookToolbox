"""Single-book EPUB table-of-contents repair orchestration."""

from __future__ import annotations

import html
import os
import posixpath
import re
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from ebook_toolbox.services.epub_image_compressor.archive import (
    ArchiveError,
    build_epub,
    ensure_owned_temporary,
    publish_epub,
    safe_extract,
    unlink_owned_temporary,
    validate_output,
)

from .models import (
    TocEntry,
    TocRepairError,
    TocRepairRequest,
    TocRepairResult,
    TocRepairWarning,
)

OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"
XHTML_NS = "http://www.w3.org/1999/xhtml"
EPUB_NS = "http://www.idpf.org/2007/ops"

ET.register_namespace("", OPF_NS)
ET.register_namespace("dc", DC_NS)
ET.register_namespace("epub", EPUB_NS)

_VOLUME_RE = re.compile(
    r"^(?:第\s*[0-9０-９零〇一二三四五六七八九十百千万两]+\s*[卷部册冊篇]|"
    r"[上中下](?:卷|部|篇)|(?:volume|vol\.?|book|part)\s*[0-9ivxlcdm]+\b)",
    re.IGNORECASE,
)
_CHAPTER_RE = re.compile(
    r"^(?:[●◆◇☆★]?\s*(?:第?\s*[0-9０-９零〇一二三四五六七八九十百千万两]+\s*"
    r"[章话話节節回篇]|序章|楔子|引子|终章|終章|尾声|尾聲|后\s*记|後\s*記|"
    r"番外(?:篇|编)?|幕间|幕間|特典|短篇|prologue|epilogue|afterword|"
    r"chapter\s+[0-9ivxlcdm]+))",
    re.IGNORECASE,
)
_IGNORED_LABELS = {
    "封面",
    "表紙",
    "目录",
    "目次",
    "contents",
    "table of contents",
    "版权页",
    "版權頁",
    "奥付",
}


class _RepairFailure(Exception):
    def __init__(self, code: str, message: str, path: Path | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.path = path


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _namespace(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def _tag(namespace: str, local: str) -> str:
    return f"{{{namespace}}}{local}" if namespace else local


def _text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return " ".join("".join(node.itertext()).replace("\u3000", " ").split())


def _default_output(source: Path) -> Path:
    return source.with_name(f"{source.stem}_toc_fixed.epub")


def _paths_refer_to_same_file(source: Path, output: Path) -> bool:
    try:
        return source.samefile(output)
    except OSError:
        source_identity = os.path.normcase(os.path.abspath(source))
        output_identity = os.path.normcase(os.path.abspath(output.parent.resolve() / output.name))
        return source_identity == output_identity


def _opf_relative_href(base_href: str, linked_href: str) -> str | None:
    """Resolve a TOC-relative local href to an OPF-relative lexical href."""

    parsed = urlsplit(html.unescape(linked_href))
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    path = unquote(parsed.path).replace("\\", "/")
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(base_href), path))
    pure = PurePosixPath(joined)
    if pure.is_absolute() or ".." in pure.parts:
        return None
    return joined + (f"#{parsed.fragment}" if parsed.fragment else "")


def _relative_to_toc(opf_href: str, toc_href: str) -> str:
    path, separator, fragment = opf_href.partition("#")
    start = posixpath.dirname(toc_href) or "."
    relative = posixpath.relpath(path, start)
    return relative + (f"#{fragment}" if separator else "")


def _target_exists(root: Path, opf_path: Path, href: str) -> bool:
    path = unquote(href.partition("#")[0]).replace("/", os.sep)
    try:
        target = (opf_path.parent / path).resolve()
        extracted_root = root.resolve()
    except OSError:
        return False
    return target.is_file() and (target == extracted_root or extracted_root in target.parents)


def _manifest_target(root: Path, opf_path: Path, href: str) -> Path:
    parsed = urlsplit(html.unescape(href))
    path = unquote(parsed.path)
    if parsed.scheme or parsed.netloc or not path or "\\" in path:
        raise _RepairFailure("unsafe_opf_href", f"Unsafe local OPF href: {href}", opf_path)
    target = (opf_path.parent / Path(*PurePosixPath(path).parts)).resolve()
    extracted_root = root.resolve()
    if target == extracted_root or extracted_root not in target.parents:
        raise _RepairFailure("unsafe_opf_href", f"OPF href escapes the EPUB: {href}", opf_path)
    return target


def _manifest(root: ET.Element) -> ET.Element:
    node = next((item for item in root.iter() if _local(item.tag) == "manifest"), None)
    if node is None:
        raise _RepairFailure("invalid_opf", "OPF does not contain a manifest")
    return node


def _spine(root: ET.Element) -> ET.Element:
    node = next((item for item in root.iter() if _local(item.tag) == "spine"), None)
    if node is None:
        raise _RepairFailure("invalid_opf", "OPF does not contain a spine")
    return node


def _items(root: ET.Element) -> dict[str, ET.Element]:
    return {
        item.get("id", ""): item
        for item in _manifest(root)
        if _local(item.tag) == "item" and item.get("id") and item.get("href")
    }


def _spine_hrefs(root: ET.Element) -> list[str]:
    items = _items(root)
    return [
        items[ref.get("idref", "")].get("href", "")
        for ref in _spine(root)
        if _local(ref.tag) == "itemref" and ref.get("idref", "") in items
    ]


def _page_label(path: Path) -> str:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return ""
    body = next((node for node in root.iter() if _local(node.tag) == "body"), root)
    for wanted in ("h1", "h2", "h3", "h4", "h5", "h6", "title"):
        for node in body.iter() if wanted != "title" else root.iter():
            if _local(node.tag) == wanted:
                label = _text(node)
                if label and label.casefold() not in {"untitled", "无标题"}:
                    return label
    return ""


def _structure_entries(candidates: list[tuple[str, str]]) -> list[TocEntry]:
    volumes: list[tuple[str, str, list[TocEntry]]] = []
    flat: list[TocEntry] = []
    current: tuple[str, str, list[TocEntry]] | None = None
    seen: set[str] = set()
    for label, href in candidates:
        normalized = label.casefold()
        if normalized in _IGNORED_LABELS or href in seen:
            continue
        if _VOLUME_RE.match(label):
            current = (label, href, [])
            volumes.append(current)
            seen.add(href)
        elif _CHAPTER_RE.match(label):
            entry = TocEntry(label, href)
            if current is not None:
                current[2].append(entry)
            else:
                flat.append(entry)
            seen.add(href)
    usable_volumes = [volume for volume in volumes if volume[2]]
    if len(usable_volumes) >= 2:
        return [TocEntry(label, href, tuple(children)) for label, href, children in usable_volumes]
    if len(volumes) == 1 and volumes[0][2]:
        return list(volumes[0][2])
    return flat


def _entries_from_spine(root: Path, opf_path: Path, opf_root: ET.Element) -> list[TocEntry]:
    candidates: list[tuple[str, str]] = []
    for href in _spine_hrefs(opf_root):
        path = href.partition("#")[0]
        if not path.lower().endswith((".xhtml", ".html", ".htm")):
            continue
        target = _manifest_target(root, opf_path, path)
        if not target.is_file():
            continue
        label = _page_label(target)
        if label:
            candidates.append((label, href))
    return _structure_entries(candidates)


def _raw_ncx_entries(path: Path) -> list[tuple[str, str]]:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return []
    result: list[tuple[str, str]] = []
    for point in (node for node in root.iter() if _local(node.tag) == "navPoint"):
        label_node = next((node for node in point.iter() if _local(node.tag) == "text"), None)
        content = next((node for node in point if _local(node.tag) == "content"), None)
        label = _text(label_node)
        src = content.get("src", "") if content is not None else ""
        if label and src:
            result.append((label, src))
    return result


def _normalized_legacy_label(label: str) -> str:
    simple = {
        "表紙": "封面",
        "目次": "目录",
        "もくじ": "目录",
        "奥付": "版权页",
        "プロローグ": "序章",
        "エピローグ": "终章",
        "あとがき": "后记",
    }
    if label in simple:
        return simple[label]
    for source, replacement in (
        ("プロローグ", "序章"),
        ("エピローグ", "终章"),
        ("あとがき", "后记"),
    ):
        if label.startswith(source):
            return replacement + label[len(source) :]
    return label


def _source_manifest_mapping(opf_path: Path) -> dict[str, str]:
    try:
        root = ET.parse(opf_path).getroot()
    except (ET.ParseError, OSError):
        return {}
    return {
        unquote(item.get("href", "")).replace("\\", "/"): item_id
        for item_id, item in _items(root).items()
    }


def _choose_epubmerge_source_mapping(
    extracted_root: Path,
    opf_path: Path,
    master_items: dict[str, ET.Element],
    volume: int,
    source_entries: list[tuple[str, str]],
) -> dict[str, str]:
    declared = master_items.get(f"a{volume}rootfile")
    if declared is not None:
        declared_path = _manifest_target(extracted_root, opf_path, declared.get("href", ""))
        mapping = _source_manifest_mapping(declared_path)
        if mapping:
            return mapping

    source_paths = {unquote(src.partition("#")[0]).replace("\\", "/") for _, src in source_entries}
    best_mapping: dict[str, str] = {}
    best_score = 0
    for candidate in extracted_root.rglob("*.opf"):
        if candidate == opf_path:
            continue
        mapping = _source_manifest_mapping(candidate)
        score = sum(
            10 if path in mapping and f"a{volume}{mapping[path]}" in master_items else 0
            for path in source_paths
        )
        if score > best_score:
            best_mapping = mapping
            best_score = score
    return best_mapping


def _entries_from_epubmerge(
    extracted_root: Path, opf_path: Path, opf_root: ET.Element
) -> list[TocEntry]:
    """Recover volumes from epubmerge's per-volume NCX and aN manifest ids."""

    volume_ncxs: list[tuple[int, Path]] = []
    for path in extracted_root.rglob("*.ncx"):
        relative = path.relative_to(extracted_root).as_posix()
        match = re.fullmatch(r"(\d+)/(?:OEBPS/)?toc\.ncx", relative, re.IGNORECASE)
        if match:
            volume_ncxs.append((int(match.group(1)), path))
    if len(volume_ncxs) < 2:
        return []

    master_items = _items(opf_root)
    volumes: list[TocEntry] = []
    for volume, ncx_path in sorted(volume_ncxs):
        source_entries = _raw_ncx_entries(ncx_path)
        source_mapping = _choose_epubmerge_source_mapping(
            extracted_root, opf_path, master_items, volume, source_entries
        )
        children: list[TocEntry] = []
        for source_label, source_href in source_entries:
            normalized_label = _normalized_legacy_label(source_label)
            if normalized_label.casefold() in _IGNORED_LABELS:
                continue
            source_path, separator, fragment = source_href.partition("#")
            source_path = unquote(source_path).replace("\\", "/")
            source_id = source_mapping.get(source_path)
            target_item = master_items.get(f"a{volume}{source_id}") if source_id else None
            target_href = target_item.get("href", "") if target_item is not None else ""
            if not target_href or not _target_exists(extracted_root, opf_path, target_href):
                continue
            page_label = _page_label(_manifest_target(extracted_root, opf_path, target_href))
            label = page_label if _CHAPTER_RE.match(page_label) else normalized_label
            href = target_href + (f"#{fragment}" if separator and fragment else "")
            children.append(TocEntry(label, href))
        if children:
            volumes.append(TocEntry(f"第{volume}卷", children[0].href, tuple(children)))
    return volumes if len(volumes) >= 2 else []


def _visible_toc_links(
    root: Path, opf_path: Path, toc_href: str
) -> tuple[str, list[tuple[str, str]]]:
    path = _manifest_target(root, opf_path, toc_href)
    try:
        document = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return "", []
    title = _page_label(path)
    links: list[tuple[str, str]] = []
    for anchor in (node for node in document.iter() if _local(node.tag) == "a"):
        label = _text(anchor)
        href = _opf_relative_href(toc_href, anchor.get("href", ""))
        if (
            label
            and href
            and label.casefold() not in _IGNORED_LABELS
            and _target_exists(root, opf_path, href)
        ):
            links.append((label, href))
    return title, links


def _entries_from_visible_tocs(
    root: Path, opf_path: Path, opf_root: ET.Element
) -> list[TocEntry]:
    """Recover the legacy script's reliable visible-contents-page cases."""

    candidates: list[tuple[str, list[tuple[str, str]]]] = []
    guide_hrefs = {
        node.get("href", "").partition("#")[0]
        for node in opf_root.iter()
        if _local(node.tag) == "reference" and node.get("type", "").casefold() == "toc"
    }
    for href in _spine_hrefs(opf_root):
        base = posixpath.basename(href.partition("#")[0]).casefold()
        named_toc = bool(
            re.fullmatch(r"(?:p-)?(?:contents|toc)(?:-?\d+)?\.x?html?", base)
        )
        if not named_toc and href.partition("#")[0] not in guide_hrefs:
            continue
        title, links = _visible_toc_links(root, opf_path, href)
        structured = _structure_entries(links)
        chapter_count = sum(bool(_CHAPTER_RE.match(label)) for label, _ in links)
        if structured and chapter_count >= 2:
            candidates.append((title, links))
    if len(candidates) == 1:
        return _structure_entries(candidates[0][1])
    if len(candidates) >= 2:
        volumes: list[TocEntry] = []
        for index, (title, links) in enumerate(candidates, 1):
            children = _structure_entries(links)
            if not children:
                return []
            label = title if _VOLUME_RE.match(title) else f"第{index}卷"
            volumes.append(TocEntry(label, children[0].href, tuple(children)))
        return volumes
    return []


def _parse_nav_ol(ol: ET.Element, nav_href: str) -> list[TocEntry]:
    result: list[TocEntry] = []
    for li in (child for child in ol if _local(child.tag) == "li"):
        anchor = next(
            (child for child in li if _local(child.tag) in {"a", "span"}), None
        )
        label = _text(anchor)
        linked = anchor.get("href", "") if anchor is not None else ""
        href = _opf_relative_href(nav_href, linked) if linked else None
        nested = next((child for child in li if _local(child.tag) == "ol"), None)
        children = tuple(_parse_nav_ol(nested, nav_href)) if nested is not None else ()
        if label and href:
            result.append(TocEntry(label, href, children))
    return result


def _read_nav(path: Path, nav_href: str) -> list[TocEntry]:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return []
    for nav in (node for node in root.iter() if _local(node.tag) == "nav"):
        nav_type = nav.get(f"{{{EPUB_NS}}}type", nav.get("epub:type", ""))
        if "toc" not in nav_type.split():
            continue
        ol = next((child for child in nav if _local(child.tag) == "ol"), None)
        return _parse_nav_ol(ol, nav_href) if ol is not None else []
    return []


def _read_ncx_points(parent: ET.Element, ncx_href: str) -> list[TocEntry]:
    result: list[TocEntry] = []
    for point in (child for child in parent if _local(child.tag) == "navPoint"):
        label_node = next((node for node in point.iter() if _local(node.tag) == "text"), None)
        content = next((node for node in point if _local(node.tag) == "content"), None)
        label = _text(label_node)
        href = _opf_relative_href(ncx_href, content.get("src", "")) if content is not None else None
        children = tuple(_read_ncx_points(point, ncx_href))
        if label and href:
            result.append(TocEntry(label, href, children))
    return result


def _read_ncx(path: Path, ncx_href: str) -> list[TocEntry]:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return []
    nav_map = next((node for node in root.iter() if _local(node.tag) == "navMap"), None)
    return _read_ncx_points(nav_map, ncx_href) if nav_map is not None else []


def _entries_are_usable(
    entries: list[TocEntry], root: Path, opf_path: Path
) -> bool:
    def usable(entry: TocEntry) -> bool:
        return bool(entry.label.strip()) and _target_exists(root, opf_path, entry.href) and all(
            usable(child) for child in entry.children
        )

    return bool(entries) and all(usable(entry) for entry in entries)


def _append_nav_entries(parent: ET.Element, entries: list[TocEntry], nav_href: str) -> None:
    namespace = _namespace(parent.tag) or XHTML_NS
    for entry in entries:
        li = ET.SubElement(parent, _tag(namespace, "li"))
        anchor = ET.SubElement(li, _tag(namespace, "a"))
        anchor.set("href", _relative_to_toc(entry.href, nav_href))
        anchor.text = entry.label
        if entry.children:
            child_ol = ET.SubElement(li, _tag(namespace, "ol"))
            _append_nav_entries(child_ol, list(entry.children), nav_href)


def _write_nav(path: Path, nav_href: str, entries: list[TocEntry], title: str) -> None:
    try:
        root = ET.parse(path).getroot() if path.is_file() else None
    except ET.ParseError:
        root = None
    nav = None
    if root is not None:
        nav = next(
            (
                node
                for node in root.iter()
                if _local(node.tag) == "nav"
                and "toc"
                in node.get(f"{{{EPUB_NS}}}type", node.get("epub:type", "")).split()
            ),
            None,
        )
    if root is None or nav is None:
        root = ET.Element(_tag(XHTML_NS, "html"))
        root.set("lang", "zh-CN")
        head = ET.SubElement(root, _tag(XHTML_NS, "head"))
        ET.SubElement(head, _tag(XHTML_NS, "title")).text = title
        body = ET.SubElement(root, _tag(XHTML_NS, "body"))
        nav = ET.SubElement(body, _tag(XHTML_NS, "nav"))
        nav.set(f"{{{EPUB_NS}}}type", "toc")
    else:
        for child in list(nav):
            nav.remove(child)
    namespace = _namespace(nav.tag) or XHTML_NS
    ET.SubElement(nav, _tag(namespace, "h1")).text = "目录"
    ol = ET.SubElement(nav, _tag(namespace, "ol"))
    _append_nav_entries(ol, entries, nav_href)
    ET.indent(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _append_ncx_entries(
    parent: ET.Element, entries: list[TocEntry], ncx_href: str, counter: list[int]
) -> None:
    namespace = _namespace(parent.tag) or NCX_NS
    for entry in entries:
        counter[0] += 1
        point = ET.SubElement(
            parent,
            _tag(namespace, "navPoint"),
            {"id": f"navPoint-{counter[0]}", "playOrder": str(counter[0])},
        )
        label = ET.SubElement(point, _tag(namespace, "navLabel"))
        ET.SubElement(label, _tag(namespace, "text")).text = entry.label
        ET.SubElement(
            point,
            _tag(namespace, "content"),
            {"src": _relative_to_toc(entry.href, ncx_href)},
        )
        if entry.children:
            _append_ncx_entries(point, list(entry.children), ncx_href, counter)


def _write_ncx(path: Path, ncx_href: str, entries: list[TocEntry], title: str) -> None:
    try:
        root = ET.parse(path).getroot() if path.is_file() else None
    except ET.ParseError:
        root = None
    if root is None:
        root = ET.Element(_tag(NCX_NS, "ncx"), {"version": "2005-1"})
        head = ET.SubElement(root, _tag(NCX_NS, "head"))
        ET.SubElement(head, _tag(NCX_NS, "meta"), {"name": "dtb:uid", "content": ""})
        doc_title = ET.SubElement(root, _tag(NCX_NS, "docTitle"))
        ET.SubElement(doc_title, _tag(NCX_NS, "text")).text = title
        nav_map = ET.SubElement(root, _tag(NCX_NS, "navMap"))
    else:
        nav_map = next((node for node in root.iter() if _local(node.tag) == "navMap"), None)
        if nav_map is None:
            nav_map = ET.SubElement(root, _tag(_namespace(root.tag) or NCX_NS, "navMap"))
        else:
            for child in list(nav_map):
                nav_map.remove(child)
    _append_ncx_entries(nav_map, entries, ncx_href, [0])
    ET.indent(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _book_title(opf_root: ET.Element) -> str:
    title = next((node for node in opf_root.iter() if _local(node.tag) == "title"), None)
    return _text(title) or "EPUB"


def _unique_id(existing: set[str], preferred: str) -> str:
    candidate = preferred
    number = 2
    while candidate in existing:
        candidate = f"{preferred}-{number}"
        number += 1
    return candidate


def _find_toc_items(opf_root: ET.Element) -> tuple[ET.Element | None, ET.Element | None]:
    nav = None
    ncx = None
    for item in _items(opf_root).values():
        href = item.get("href", "")
        properties = item.get("properties", "").split()
        media_type = item.get("media-type", "")
        if nav is None and "nav" in properties:
            nav = item
        if ncx is None and (
            media_type == "application/x-dtbncx+xml" or href.lower().endswith(".ncx")
        ):
            ncx = item
    return nav, ncx


def _add_toc_item(
    opf_root: ET.Element, href: str, preferred_id: str, media_type: str, properties: str = ""
) -> ET.Element:
    manifest = _manifest(opf_root)
    item_id = _unique_id(set(_items(opf_root)), preferred_id)
    attributes = {"id": item_id, "href": href, "media-type": media_type}
    if properties:
        attributes["properties"] = properties
    return ET.SubElement(manifest, _tag(_namespace(manifest.tag), "item"), attributes)


def _parse_opf(path: Path) -> tuple[ET.Element, bool]:
    raw = path.read_bytes()
    try:
        return ET.fromstring(raw), False
    except ET.ParseError:
        text = raw.decode("utf-8", "ignore")
        if re.search(r"<spine\b", text, re.IGNORECASE) and not re.search(
            r"</spine\s*>", text, re.IGNORECASE
        ):
            repaired, count = re.subn(
                r"</package\s*>", "</spine>\n</package>", text, count=1, flags=re.IGNORECASE
            )
            if count:
                try:
                    return ET.fromstring(repaired), True
                except ET.ParseError:
                    pass
        raise _RepairFailure("invalid_opf", "The package document is not valid XML", path)


def _repair_package(
    extracted_root: Path,
    opf_path: Path,
    replace_existing: bool,
) -> tuple[list[TocEntry], list[Path], list[TocRepairWarning]]:
    # ``safe_extract`` returns resolved member paths. Normalize the root too so
    # every later ``relative_to`` comparison uses the same Windows path form.
    extracted_root = extracted_root.resolve()
    opf_path = opf_path.resolve()
    opf_root, opf_repaired = _parse_opf(opf_path)
    warnings: list[TocRepairWarning] = []
    modified: list[Path] = []
    nav_item, ncx_item = _find_toc_items(opf_root)
    nav_href = nav_item.get("href", "") if nav_item is not None else ""
    ncx_href = ncx_item.get("href", "") if ncx_item is not None else ""
    nav_path = _manifest_target(extracted_root, opf_path, nav_href) if nav_href else None
    ncx_path = _manifest_target(extracted_root, opf_path, ncx_href) if ncx_href else None
    nav_entries = _read_nav(nav_path, nav_href) if nav_path is not None else []
    ncx_entries = _read_ncx(ncx_path, ncx_href) if ncx_path is not None else []
    usable_nav = _entries_are_usable(nav_entries, extracted_root, opf_path)
    usable_ncx = _entries_are_usable(ncx_entries, extracted_root, opf_path)
    valid_nav = bool(
        usable_nav
        and nav_item is not None
        and nav_item.get("media-type") == "application/xhtml+xml"
    )
    valid_ncx = bool(
        usable_ncx
        and ncx_item is not None
        and ncx_item.get("media-type") == "application/x-dtbncx+xml"
    )
    version = opf_root.get("version", "2")
    try:
        epub3 = int(version.split(".", 1)[0]) >= 3
    except ValueError:
        epub3 = False
    primary_entries = (
        nav_entries
        if epub3 and valid_nav
        else ncx_entries
        if not epub3 and valid_ncx
        else []
    )

    if not replace_existing and primary_entries:
        warnings.append(
            TocRepairWarning("existing_toc_preserved", "The existing usable TOC was preserved")
        )
        return primary_entries, modified, warnings
    if not replace_existing:
        warnings.append(
            TocRepairWarning(
                "existing_toc_unusable",
                "No usable format-required TOC was available to preserve; repair was performed",
            )
        )

    entries = _entries_from_epubmerge(extracted_root, opf_path, opf_root)
    if entries:
        warnings.append(
            TocRepairWarning(
                "used_epubmerge_metadata",
                "Volume structure was recovered from epubmerge source metadata",
            )
        )
    if not entries:
        entries = _entries_from_spine(extracted_root, opf_path, opf_root)
    if not entries:
        entries = _entries_from_visible_tocs(extracted_root, opf_path, opf_root)
        if entries:
            warnings.append(
                TocRepairWarning(
                    "used_visible_toc",
                    "Chapter structure was recovered from a visible contents page",
                )
            )
    fallback = nav_entries if usable_nav else ncx_entries if usable_ncx else []
    if not entries and fallback:
        entries = fallback
        warnings.append(
            TocRepairWarning(
                "used_existing_toc_entries",
                "Chapter headings were not recognizable; validated existing TOC targets "
                "were reused",
            )
        )
    if not entries:
        raise _RepairFailure(
            "chapters_not_recognized",
            "No reliable chapter structure could be identified; the EPUB was not modified",
            opf_path,
        )

    opf_changed = opf_repaired
    if opf_repaired:
        warnings.append(
            TocRepairWarning(
                "opf_spine_repaired", "A missing closing spine tag was repaired", opf_path
            )
        )
    if epub3 and nav_item is None:
        nav_item = next(
            (
                item
                for item in _items(opf_root).values()
                if posixpath.basename(item.get("href", "")).casefold() == "nav.xhtml"
            ),
            None,
        )
        if nav_item is None:
            nav_href = "nav.xhtml"
            nav_item = _add_toc_item(
                opf_root, nav_href, "nav", "application/xhtml+xml", properties="nav"
            )
            warnings.append(
                TocRepairWarning("nav_created", "Missing EPUB3 nav document was created")
            )
        else:
            nav_href = nav_item.get("href", "")
            properties = set(nav_item.get("properties", "").split())
            properties.add("nav")
            nav_item.set("properties", " ".join(sorted(properties)))
            warnings.append(
                TocRepairWarning(
                    "nav_manifest_reference_repaired",
                    "An existing nav document was marked as the EPUB3 navigation document",
                )
            )
        nav_item.set("media-type", "application/xhtml+xml")
        nav_path = _manifest_target(extracted_root, opf_path, nav_href)
        opf_changed = True
    if not epub3 and ncx_item is None:
        ncx_href = "toc.ncx"
        ncx_item = _add_toc_item(
            opf_root, ncx_href, "ncx", "application/x-dtbncx+xml"
        )
        ncx_path = _manifest_target(extracted_root, opf_path, ncx_href)
        _spine(opf_root).set("toc", ncx_item.get("id", "ncx"))
        opf_changed = True
        warnings.append(TocRepairWarning("ncx_created", "Missing EPUB2 NCX document was created"))

    if nav_item is not None and nav_item.get("media-type") != "application/xhtml+xml":
        nav_item.set("media-type", "application/xhtml+xml")
        opf_changed = True
        warnings.append(
            TocRepairWarning(
                "nav_media_type_repaired", "The nav manifest media type was repaired"
            )
        )
    if ncx_item is not None and ncx_item.get("media-type") != "application/x-dtbncx+xml":
        ncx_item.set("media-type", "application/x-dtbncx+xml")
        opf_changed = True
        warnings.append(
            TocRepairWarning(
                "ncx_media_type_repaired", "The NCX manifest media type was repaired"
            )
        )

    if not epub3 and ncx_item is not None:
        spine = _spine(opf_root)
        ncx_id = ncx_item.get("id", "")
        if ncx_id and spine.get("toc") != ncx_id:
            spine.set("toc", ncx_id)
            opf_changed = True
            warnings.append(
                TocRepairWarning(
                    "ncx_spine_reference_repaired",
                    "The EPUB2 spine TOC reference was repaired",
                )
            )

    title = _book_title(opf_root)
    if nav_item is not None and nav_path is not None:
        if nav_path.exists() and not valid_nav:
            warnings.append(
                TocRepairWarning("invalid_nav_replaced", "The unusable nav was replaced")
            )
        _write_nav(nav_path, nav_href, entries, title)
        modified.append(nav_path.relative_to(extracted_root))
    if ncx_item is not None and ncx_path is not None:
        if ncx_path.exists() and not valid_ncx:
            warnings.append(
                TocRepairWarning("invalid_ncx_replaced", "The unusable NCX was replaced")
            )
        _write_ncx(ncx_path, ncx_href, entries, title)
        modified.append(ncx_path.relative_to(extracted_root))
    if opf_changed:
        ET.indent(opf_root)
        ET.ElementTree(opf_root).write(opf_path, encoding="utf-8", xml_declaration=True)
        modified.append(opf_path.relative_to(extracted_root))
    return entries, modified, warnings


def repair_epub_toc(request: TocRepairRequest) -> TocRepairResult:
    """Repair the TOC of one EPUB without modifying the input file."""

    source = Path(request.source)
    output = Path(request.output) if request.output is not None else _default_output(source)
    result = TocRepairResult(source=source, output=output)
    try:
        if not source.is_file() or source.suffix.lower() != ".epub":
            raise _RepairFailure("invalid_source", "Source must be an existing .epub file", source)
        source = source.resolve()
        result.source = source
        result.output = output
        if _paths_refer_to_same_file(source, output):
            raise _RepairFailure("source_output_same", "Output must not be the source EPUB")
        if output.exists() and not request.overwrite:
            raise _RepairFailure("output_exists", f"Output already exists: {output}", output)
        if not output.parent.is_dir():
            raise _RepairFailure(
                "invalid_output_directory", f"Output directory does not exist: {output.parent}"
            )

        with tempfile.TemporaryDirectory(prefix="ebook_toolbox_toc_") as directory:
            root = Path(directory).resolve() / "unpacked"
            root.mkdir()
            opf_paths = safe_extract(source, root)
            if len(opf_paths) != 1:
                raise _RepairFailure(
                    "multiple_renditions_unsupported",
                    "EPUBs with multiple package documents are not supported",
                )
            entries, modified, warnings = _repair_package(
                root, opf_paths[0], request.replace_existing
            )
            result.entries.extend(entries)
            result.modified_files.extend(modified)
            result.warnings.extend(warnings)

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
        result.success = True
    except _RepairFailure as exc:
        result.errors.append(TocRepairError(exc.code, str(exc), exc.path))
    except ArchiveError as exc:
        result.errors.append(TocRepairError("archive_error", str(exc), source))
    except FileExistsError as exc:
        result.errors.append(TocRepairError("output_exists", str(exc), output))
    except (OSError, ValueError) as exc:
        result.errors.append(TocRepairError(type(exc).__name__, str(exc)))
    except Exception as exc:
        result.errors.append(TocRepairError("unexpected_error", str(exc)))
    return result
