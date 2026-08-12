"""EPUB cover discovery."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from .images import SUPPORTED_EXTENSIONS
from .models import CompressionWarning


def find_cover_paths(
    root: Path, opf_paths: tuple[Path, ...]
) -> tuple[set[Path], list[CompressionWarning]]:
    """Return resolved cover paths and non-fatal metadata parsing warnings."""

    covers: set[Path] = set()
    warnings: list[CompressionWarning] = []
    for opf in opf_paths:
        try:
            tree = ET.parse(opf)
            manifest = []
            cover_ids: set[str] = set()
            for element in tree.iter():
                tag = element.tag.rsplit("}", 1)[-1]
                if tag == "meta" and element.attrib.get("name", "").lower() == "cover":
                    content = element.attrib.get("content")
                    if content:
                        cover_ids.add(content)
                elif tag == "item":
                    manifest.append(element)
            for item in manifest:
                properties = item.attrib.get("properties", "").split()
                if item.attrib.get("id") in cover_ids or "cover-image" in properties:
                    href = item.attrib.get("href")
                    if href:
                        clean_href = unquote(href.split("#", 1)[0])
                        covers.add((opf.parent / clean_href).resolve())
        except Exception as exc:
            warnings.append(
                CompressionWarning("cover_metadata_invalid", str(exc), opf.relative_to(root))
            )

    for image in root.rglob("*"):
        if image.is_file() and image.suffix.lower() in SUPPORTED_EXTENSIONS:
            if any(marker in image.stem.lower() for marker in ("cover", "frontcover", "封面")):
                covers.add(image.resolve())
    return covers, warnings
