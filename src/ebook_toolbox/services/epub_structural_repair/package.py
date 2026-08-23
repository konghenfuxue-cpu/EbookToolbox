"""OPF repair and repaired-archive structural validation."""

from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import BinaryIO
from urllib.parse import quote
from xml.etree import ElementTree as ET

from ebook_toolbox.services.epub_image_compressor.archive import (
    ArchiveError,
    validate_output,
)

from .paths import (
    MEDIA_TYPES,
    build_name_map,
    resolve_local_path,
    validate_source_member_names,
)

DC_NS = "http://purl.org/dc/elements/1.1/"


@dataclass(slots=True)
class PackageChanges:
    data: bytes
    added_resources: list[str] = field(default_factory=list)
    removed_manifest_items: list[str] = field(default_factory=list)
    removed_spine_items: list[str] = field(default_factory=list)
    language_added: bool = False


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _tag_like(parent: ET.Element, local: str) -> str:
    namespace = parent.tag[1:].split("}", 1)[0] if parent.tag.startswith("{") else ""
    return f"{{{namespace}}}{local}" if namespace else local


def rootfiles(archive: zipfile.ZipFile) -> list[str]:
    try:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
    except KeyError as exc:
        raise ArchiveError("META-INF/container.xml is missing") from exc
    except ET.ParseError as exc:
        raise ArchiveError("META-INF/container.xml is invalid") from exc
    result: list[str] = []
    for node in container.iter():
        if _local(node.tag) != "rootfile" or not node.get("full-path"):
            continue
        result.append(
            resolve_local_path(
                "META-INF/container.xml", node.get("full-path", ""), root_relative=True
            )
        )
    if not result:
        raise ArchiveError("container.xml does not declare an OPF rootfile")
    return list(dict.fromkeys(result))


def manifest_targets(data: bytes, opf_path: str) -> list[str]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ArchiveError(f"OPF is invalid XML: {opf_path}") from exc
    targets: list[str] = []
    for item in root.iter():
        if _local(item.tag) != "item":
            continue
        href = (item.get("href") or "").strip()
        if href:
            targets.append(resolve_local_path(opf_path, href))
    return list(dict.fromkeys(targets))


def repair_package(data: bytes, opf_path: str, available: set[str]) -> PackageChanges:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ArchiveError(f"OPF is invalid XML: {opf_path}") from exc
    metadata = next((node for node in root.iter() if _local(node.tag) == "metadata"), None)
    manifest = next((node for node in root.iter() if _local(node.tag) == "manifest"), None)
    spine = next((node for node in root.iter() if _local(node.tag) == "spine"), None)
    if metadata is None or manifest is None or spine is None:
        raise ArchiveError(f"OPF is missing metadata, manifest, or spine: {opf_path}")

    changes = PackageChanges(data=data)
    changed = False
    language = next((node for node in metadata if _local(node.tag) == "language"), None)
    if language is None or not "".join(language.itertext()).strip():
        if language is None:
            language = ET.SubElement(metadata, f"{{{DC_NS}}}language")
        language.text = "zh-CN"
        changes.language_added = True
        changed = True

    registered_targets: set[str] = set()
    used_ids: set[str] = set()
    for item in list(manifest):
        if _local(item.tag) != "item":
            continue
        item_id = (item.get("id") or "").strip()
        href = (item.get("href") or "").strip()
        target = ""
        if href:
            try:
                target = resolve_local_path(opf_path, href)
            except ArchiveError:
                target = ""
        if not item_id or item_id in used_ids or not target or target not in available:
            manifest.remove(item)
            changes.removed_manifest_items.append(item_id or href or "<empty item>")
            changed = True
            continue
        used_ids.add(item_id)
        registered_targets.add(target)

    opf_directory = posixpath.dirname(opf_path) or "."
    counter = 0
    for target in sorted(available, key=lambda value: (value.casefold(), value)):
        extension = PurePosixPath(target).suffix.casefold()
        if (
            target in registered_targets
            or target == opf_path
            or target == "mimetype"
            or target.startswith("META-INF/")
            or extension not in MEDIA_TYPES
        ):
            continue
        counter += 1
        item_id = f"repair_resource_{counter:04d}"
        while item_id in used_ids:
            counter += 1
            item_id = f"repair_resource_{counter:04d}"
        used_ids.add(item_id)
        href = quote(posixpath.relpath(target, opf_directory), safe="/-._~")
        ET.SubElement(
            manifest,
            _tag_like(manifest, "item"),
            {"id": item_id, "href": href, "media-type": MEDIA_TYPES[extension]},
        )
        registered_targets.add(target)
        changes.added_resources.append(target)
        changed = True

    for itemref in list(spine):
        if _local(itemref.tag) != "itemref":
            continue
        item_id = (itemref.get("idref") or "").strip()
        if not item_id or item_id not in used_ids:
            spine.remove(itemref)
            changes.removed_spine_items.append(item_id or "<empty idref>")
            changed = True

    if changed:
        ET.indent(root)
        changes.data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return changes


def validate_repaired_epub(path: BinaryIO) -> dict[str, int]:
    validate_output(path)
    path.seek(0)
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        files = validate_source_member_names(infos)
        mapping = build_name_map(infos)
        if any(mapping[info.filename] != info.filename for info in files):
            raise ArchiveError("Output still contains Windows-inexpressible member paths")
        names = {info.filename for info in files}
        package_paths = rootfiles(archive)
        manifest_count = 0
        readable_spine = 0
        registered: set[str] = set()
        for opf_path in package_paths:
            if opf_path not in names:
                raise ArchiveError(f"container.xml references a missing OPF: {opf_path}")
            try:
                root = ET.fromstring(archive.read(opf_path))
            except ET.ParseError as exc:
                raise ArchiveError(f"Output OPF is invalid XML: {opf_path}") from exc
            metadata = next(
                (node for node in root.iter() if _local(node.tag) == "metadata"), None
            )
            language = (
                next((node for node in metadata if _local(node.tag) == "language"), None)
                if metadata is not None
                else None
            )
            if language is None or not "".join(language.itertext()).strip():
                raise ArchiveError(f"Output OPF has no dc:language: {opf_path}")
            manifest: dict[str, str] = {}
            for item in root.iter():
                if _local(item.tag) != "item":
                    continue
                item_id = (item.get("id") or "").strip()
                href = (item.get("href") or "").strip()
                if not item_id or not href or item_id in manifest:
                    raise ArchiveError(f"Output OPF contains an invalid manifest item: {opf_path}")
                target = resolve_local_path(opf_path, href)
                if target not in names:
                    raise ArchiveError(f"Manifest resource is missing: {target}")
                manifest[item_id] = target
                registered.add(target)
            manifest_count += len(manifest)
            for itemref in root.iter():
                if _local(itemref.tag) != "itemref":
                    continue
                item_id = (itemref.get("idref") or "").strip()
                if item_id not in manifest:
                    raise ArchiveError(f"Spine references a missing manifest id: {item_id}")
                extension = PurePosixPath(manifest[item_id]).suffix.casefold()
                if extension in {".xhtml", ".html", ".htm", ".svg"}:
                    readable_spine += 1
        relevant = {
            name
            for name in names
            if PurePosixPath(name).suffix.casefold() in MEDIA_TYPES
            and name not in package_paths
            and not name.startswith("META-INF/")
        }
        orphaned = sorted(relevant - registered)
        if orphaned:
            raise ArchiveError(f"Resource is not registered in a manifest: {orphaned[0]}")
        if readable_spine == 0:
            raise ArchiveError("Output spine contains no readable content documents")
        return {"files": len(names), "manifest": manifest_count, "spine": readable_spine}
