"""Safe internal-path mapping and reference rewriting."""

from __future__ import annotations

import posixpath
import re
import unicodedata
import zipfile
from collections import defaultdict
from pathlib import PurePosixPath
from urllib.parse import quote, unquote

from ebook_toolbox.services.epub_image_compressor.archive import ArchiveError

TEXT_EXTENSIONS = frozenset(
    {".opf", ".ncx", ".xhtml", ".html", ".htm", ".css", ".xml", ".svg", ".smil"}
)
MEDIA_TYPES = {
    ".xhtml": "application/xhtml+xml",
    ".html": "application/xhtml+xml",
    ".htm": "application/xhtml+xml",
    ".css": "text/css",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ncx": "application/x-dtbncx+xml",
    ".smil": "application/smil+xml",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
}

_WINDOWS_ILLEGAL = frozenset('<>:"|?*')
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
_URL_ATTRIBUTE_RE = re.compile(
    r'(?P<prefix>\b(?P<name>href|src|poster|full-path)\s*=\s*["\'])'
    r'(?P<url>[^"\']+)(?P<suffix>["\'])',
    re.IGNORECASE,
)
_CSS_URL_RE = re.compile(
    r'(?P<prefix>\burl\(\s*["\']?)(?P<url>[^)"\']+)(?P<suffix>["\']?\s*\))',
    re.IGNORECASE,
)
_CSS_IMPORT_RE = re.compile(
    r'(?P<prefix>@import\s+["\'])(?P<url>[^"\']+)(?P<suffix>["\'])',
    re.IGNORECASE,
)


def split_url_tail(url: str) -> tuple[str, str]:
    positions = [position for position in (url.find("?"), url.find("#")) if position >= 0]
    if not positions:
        return url, ""
    cut = min(positions)
    return url[:cut], url[cut:]


def _raw_parts(name: str) -> tuple[str, ...]:
    if not name or name.startswith("/") or "\\" in name:
        raise ArchiveError(f"Unsafe ZIP member path: {name}")
    stripped = name[:-1] if name.endswith("/") else name
    parts = tuple(stripped.split("/"))
    if not stripped or any(part in {"", ".", ".."} for part in parts):
        raise ArchiveError(f"Unsafe ZIP member path: {name}")
    return parts


def validate_source_member_names(infos: list[zipfile.ZipInfo]) -> list[zipfile.ZipInfo]:
    """Validate lexical safety while allowing Windows-inexpressible names to be repaired."""

    files: list[zipfile.ZipInfo] = []
    seen: set[str] = set()
    for info in infos:
        _raw_parts(info.filename)
        if info.is_dir() or info.filename.endswith("/"):
            continue
        if info.filename in seen:
            raise ArchiveError(f"Duplicate ZIP member name: {info.filename}")
        seen.add(info.filename)
        files.append(info)
    return files


def _part_needs_rename(part: str) -> bool:
    normalized = unicodedata.normalize("NFC", part)
    return (
        normalized != normalized.rstrip(" .")
        or any(ord(char) < 32 or char in _WINDOWS_ILLEGAL for char in normalized)
        or normalized.split(".", 1)[0].casefold() in _WINDOWS_RESERVED
    )


def _windows_key(path: str) -> tuple[str, ...]:
    return tuple(unicodedata.normalize("NFC", part).casefold() for part in _raw_parts(path))


def _category(path: str) -> str:
    extension = PurePosixPath(path).suffix.casefold()
    if extension in {".xhtml", ".html", ".htm"}:
        return "chapter"
    if extension == ".css":
        return "style"
    if extension in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}:
        return "image"
    if extension in {".ttf", ".otf", ".woff", ".woff2"}:
        return "font"
    return "resource"


def build_name_map(infos: list[zipfile.ZipInfo]) -> dict[str, str]:
    """Create a collision-free mapping to Windows-representable member paths."""

    files = validate_source_member_names(infos)
    keys_by_name = {info.filename: _windows_key(info.filename) for info in files}
    all_keys = set(keys_by_name.values())
    path_prefixes = {
        key[:index]
        for key in all_keys
        for index in range(1, len(key))
    }
    file_prefix_conflicts = {
        info.filename
        for info in files
        if keys_by_name[info.filename] in path_prefixes
    }
    counters: defaultdict[tuple[str, str, str], int] = defaultdict(int)
    assigned: set[tuple[str, ...]] = set()
    mapping: dict[str, str] = {}
    for info in files:
        old = info.filename
        if old == "mimetype":
            mapping[old] = old
            assigned.add(_windows_key(old))
            continue
        old_key = keys_by_name[old]
        parts = _raw_parts(old)
        legal = not any(_part_needs_rename(part) for part in parts)
        if legal and old_key not in assigned and old not in file_prefix_conflicts:
            new = old
        else:
            top = parts[0] if len(parts) > 1 and not _part_needs_rename(parts[0]) else "EPUB"
            category = _category(old)
            folder = {
                "chapter": "Text",
                "style": "Styles",
                "image": "Images",
                "font": "Fonts",
            }.get(category, "Resources")
            parent = posixpath.join(top, folder)
            extension = PurePosixPath(old).suffix.casefold()
            counter_key = (parent, category, extension)
            while True:
                counters[counter_key] += 1
                new = posixpath.join(
                    parent, f"{category}{counters[counter_key]:04d}{extension}"
                )
                new_key = _windows_key(new)
                if new_key not in assigned and new_key not in all_keys:
                    break
        mapping[old] = new
        assigned.add(_windows_key(new))
    return mapping


def _decode_text(data: bytes) -> tuple[str, str]:
    head = data[:300].decode("ascii", "ignore")
    declared = re.search(r'encoding=["\']([^"\']+)', head, re.IGNORECASE)
    candidates = [declared.group(1)] if declared else []
    candidates.extend(("utf-8-sig", "utf-8", "gb18030"))
    for encoding in candidates:
        try:
            return data.decode(encoding), encoding
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", "replace"), "utf-8"


def rewrite_url(
    url: str,
    old_document: str,
    new_document: str,
    mapping: dict[str, str],
    *,
    root_relative: bool = False,
) -> str:
    leading = url[: len(url) - len(url.lstrip())]
    trailing = url[len(url.rstrip()) :]
    stripped = url.strip()
    lowered = stripped.casefold()
    if (
        not stripped
        or stripped.startswith(("#", "//"))
        or lowered.startswith(
            ("data:", "http:", "https:", "mailto:", "javascript:", "tel:")
        )
    ):
        return url
    path_part, tail = split_url_tail(stripped.replace("\\", "/"))
    decoded = unquote(path_part)
    if decoded.startswith("/") or root_relative:
        target = posixpath.normpath(decoded.lstrip("/"))
    else:
        target = posixpath.normpath(posixpath.join(posixpath.dirname(old_document), decoded))
    if target == ".." or target.startswith("../"):
        return url
    new_target = mapping.get(target)
    if new_target is None:
        return url
    relative = posixpath.relpath(new_target, posixpath.dirname(new_document) or ".")
    if root_relative:
        relative = new_target
    rewritten = quote(relative, safe="/-._~") + tail
    return leading + rewritten + trailing


def rewrite_text(
    data: bytes, old_document: str, new_document: str, mapping: dict[str, str]
) -> bytes:
    text, encoding = _decode_text(data)

    def replace_attribute(match: re.Match[str]) -> str:
        rewritten = rewrite_url(
            match.group("url"),
            old_document,
            new_document,
            mapping,
            root_relative=match.group("name").casefold() == "full-path",
        )
        return match.group("prefix") + rewritten + match.group("suffix")

    def replace_css(match: re.Match[str]) -> str:
        rewritten = rewrite_url(match.group("url"), old_document, new_document, mapping)
        return match.group("prefix") + rewritten + match.group("suffix")

    rewritten = _URL_ATTRIBUTE_RE.sub(replace_attribute, text)
    rewritten = _CSS_URL_RE.sub(replace_css, rewritten)
    rewritten = _CSS_IMPORT_RE.sub(replace_css, rewritten)
    if rewritten == text:
        return data
    return rewritten.encode(encoding)


def resolve_local_path(document: str, url: str, *, root_relative: bool = False) -> str:
    """Resolve a local reference and reject paths that escape the EPUB root."""

    path_part, _tail = split_url_tail(url.strip().replace("\\", "/"))
    decoded = unquote(path_part)
    if not decoded:
        raise ArchiveError(f"Empty local reference in {document}")
    lowered = decoded.casefold()
    if lowered.startswith(("data:", "http:", "https:", "mailto:", "javascript:")):
        raise ArchiveError(f"Non-local resource reference in {document}: {url}")
    if decoded.startswith("/") or root_relative:
        target = posixpath.normpath(decoded.lstrip("/"))
    else:
        target = posixpath.normpath(posixpath.join(posixpath.dirname(document), decoded))
    if target in {"", ".", ".."} or target.startswith("../"):
        raise ArchiveError(f"Reference escapes the EPUB root: {url}")
    _raw_parts(target)
    return target
