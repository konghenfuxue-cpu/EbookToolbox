#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote
from xml.etree import ElementTree as ET

BAD_WIN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
TEXT_EXTS = {".opf", ".ncx", ".xhtml", ".html", ".htm", ".css", ".xml", ".svg", ".smil"}
MEDIA_TYPES = {
    ".xhtml": "application/xhtml+xml", ".html": "application/xhtml+xml",
    ".htm": "application/xhtml+xml", ".css": "text/css",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
    ".ncx": "application/x-dtbncx+xml", ".smil": "application/smil+xml",
    ".ttf": "font/ttf", ".otf": "font/otf", ".woff": "font/woff",
    ".woff2": "font/woff2", ".mp3": "audio/mpeg", ".mp4": "video/mp4",
}
URL_ATTR_RE = re.compile(
    r'(?P<prefix>\b(?:href|src|poster|full-path)\s*=\s*["\'])'
    r'(?P<url>[^"\']+)(?P<suffix>["\'])',
    re.I,
)
CSS_URL_RE = re.compile(r'(?P<prefix>\burl\(\s*["\']?)(?P<url>[^)"\']+)(?P<suffix>["\']?\s*\))', re.I)
CSS_IMPORT_RE = re.compile(r'(?P<prefix>@import\s+["\'])(?P<url>[^"\']+)(?P<suffix>["\'])', re.I)


def decode_text(data: bytes) -> tuple[str, str]:
    head = data[:300].decode("ascii", "ignore")
    match = re.search(r'encoding=["\']([^"\']+)', head, re.I)
    candidates = [match.group(1)] if match else []
    candidates += ["utf-8-sig", "utf-8", "gb18030"]
    for encoding in candidates:
        try:
            return data.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError):
            pass
    return data.decode("utf-8", "replace"), "utf-8"


def safe_category(path: str) -> str:
    parent = PurePosixPath(path).parent.name.lower()
    ext = PurePosixPath(path).suffix.lower()
    if ext in {".xhtml", ".html", ".htm"}:
        return "chapter"
    if ext == ".css":
        return "style"
    if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}:
        return "image"
    if ext in {".ttf", ".otf", ".woff", ".woff2"}:
        return "font"
    return re.sub(r"[^a-z0-9]+", "_", parent).strip("_") or "resource"


def needs_rename(path: str) -> bool:
    return any(BAD_WIN_CHARS.search(part) for part in PurePosixPath(path).parts)


def build_name_map(infos: list[zipfile.ZipInfo]) -> dict[str, str]:
    counters: defaultdict[tuple[str, str, str], int] = defaultdict(int)
    used: set[str] = set()
    mapping: dict[str, str] = {}
    for info in infos:
        if info.is_dir():
            continue
        old = info.filename.rstrip("/")
        if not old or old == "mimetype" or old in mapping:
            continue
        if not needs_rename(old) and old.casefold() not in used:
            new = old
        else:
            # The old implementation retained the original parent directory.
            # Some hostile/broken EPUBs put :|* in every directory component,
            # so changing only the leaf name still produced an invalid path.
            parts = PurePosixPath(old).parts
            top = parts[0] if parts and not BAD_WIN_CHARS.search(parts[0]) else "EPUB"
            category = safe_category(old)
            parent = posixpath.join(top, {
                "chapter": "Text",
                "style": "Styles",
                "image": "Images",
                "font": "Fonts",
            }.get(category, "Resources"))
            ext = PurePosixPath(old).suffix.lower()
            key = (parent, category, ext)
            while True:
                counters[key] += 1
                new = posixpath.join(parent, f"{category}{counters[key]:04d}{ext}")
                if new.casefold() not in used:
                    break
        mapping[old] = new
        used.add(new.casefold())
    return mapping


def repair_opf(data: bytes, opf_path: str, available: set[str]) -> bytes:
    """Repair common OPF defects that make Calibre polish abort."""
    root = ET.fromstring(data)
    metadata = root.find(".//{*}metadata")
    if metadata is not None and metadata.find("{*}language") is None:
        ET.SubElement(metadata, "{http://purl.org/dc/elements/1.1/}language").text = "zh-CN"

    manifest = root.find(".//{*}manifest")
    removed_ids: set[str] = set()
    registered_targets: set[str] = set()
    if manifest is not None:
        for item in list(manifest):
            href = (item.get("href") or "").strip()
            target = posixpath.normpath(
                posixpath.join(posixpath.dirname(opf_path), split_tail(href)[0])
            ) if href else ""
            if not href or target not in available:
                if item.get("id"):
                    removed_ids.add(item.get("id", ""))
                manifest.remove(item)
            else:
                registered_targets.add(target)

        # Sigil plugins can see an orphaned image in the Book Browser but
        # cannot write it because no manifest ID exists. Register every
        # supported real resource that is not already present.
        used_ids = {item.get("id") for item in manifest if item.get("id")}
        opf_dir = posixpath.dirname(opf_path)
        counter = 0
        ns = manifest.tag.partition("}")[0] + "}" if manifest.tag.startswith("{") else ""
        for target in sorted(available):
            ext = PurePosixPath(target).suffix.lower()
            if (
                target in registered_targets
                or target == opf_path
                or target == "mimetype"
                or target.startswith("META-INF/")
                or ext not in MEDIA_TYPES
            ):
                continue
            counter += 1
            item_id = f"repair_resource_{counter:04d}"
            while item_id in used_ids:
                counter += 1
                item_id = f"repair_resource_{counter:04d}"
            used_ids.add(item_id)
            href = posixpath.relpath(target, opf_dir or ".")
            ET.SubElement(manifest, f"{ns}item", {
                "id": item_id,
                "href": href,
                "media-type": MEDIA_TYPES[ext],
            })
            registered_targets.add(target)

    spine = root.find(".//{*}spine")
    if spine is not None:
        for itemref in list(spine):
            if not itemref.get("idref") or itemref.get("idref") in removed_ids:
                spine.remove(itemref)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def split_tail(url: str) -> tuple[str, str]:
    positions = [p for p in (url.find("?"), url.find("#")) if p >= 0]
    if not positions:
        return url, ""
    cut = min(positions)
    return url[:cut], url[cut:]


def rewrite_one_url(url: str, old_doc: str, new_doc: str, mapping: dict[str, str]) -> str:
    stripped = url.strip()
    if not stripped or stripped.startswith(("#", "data:", "http:", "https:", "mailto:", "javascript:")):
        return url
    path_part, tail = split_tail(stripped.replace("\\", "/"))
    # Calibre/Sigil may expose hostile names either literally (:*) or as
    # percent-encoded paths (%3A%2A). Resolve both forms to the same ZIP name.
    decoded_path = unquote(path_part)
    if decoded_path.startswith("/"):
        target = posixpath.normpath(decoded_path.lstrip("/"))
    else:
        target = posixpath.normpath(posixpath.join(posixpath.dirname(old_doc), decoded_path))
    new_target = mapping.get(target)
    if not new_target:
        return url
    relative = posixpath.relpath(new_target, posixpath.dirname(new_doc) or ".")
    # Generated names contain only URI-safe ASCII, but quote defensively in
    # case a legal existing directory contains spaces or non-ASCII text.
    return quote(relative, safe="/-._~") + tail


def rewrite_text(data: bytes, old_doc: str, new_doc: str, mapping: dict[str, str]) -> bytes:
    text, encoding = decode_text(data)

    def replace(match: re.Match[str]) -> str:
        return match.group("prefix") + rewrite_one_url(
            match.group("url"), old_doc, new_doc, mapping
        ) + match.group("suffix")

    text = URL_ATTR_RE.sub(replace, text)
    text = CSS_URL_RE.sub(replace, text)
    text = CSS_IMPORT_RE.sub(replace, text)
    out_encoding = "utf-8-sig" if encoding.lower() == "utf-8-sig" else "utf-8"
    return text.encode(out_encoding)


def get_rootfile(zf: zipfile.ZipFile) -> str:
    root = ET.fromstring(zf.read("META-INF/container.xml"))
    node = root.find(".//{*}rootfile")
    if node is None or not node.get("full-path"):
        raise ValueError("META-INF/container.xml 未指定 OPF 文件")
    return node.get("full-path", "")


def validate_epub(path: Path) -> dict[str, int]:
    with zipfile.ZipFile(path) as zf:
        bad_crc = zf.testzip()
        if bad_crc:
            raise ValueError(f"ZIP 数据损坏：{bad_crc}")
        infos = zf.infolist()
        if not infos or infos[0].filename != "mimetype":
            raise ValueError("mimetype 不是压缩包第一项")
        if zf.read(infos[0]).strip() != b"application/epub+zip":
            raise ValueError("mimetype 内容错误")
        names = {i.filename.rstrip("/") for i in infos}
        if any(needs_rename(name) for name in names):
            raise ValueError("仍存在 Windows 非法内部文件名")
        opf_path = get_rootfile(zf)
        opf = ET.fromstring(zf.read(opf_path))
        manifest = {}
        for item in opf.findall(".//{*}manifest/{*}item"):
            item_id, href = item.get("id"), item.get("href")
            if item_id and href:
                target = posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), split_tail(href)[0]))
                manifest[item_id] = target
                if target not in names:
                    raise ValueError(f"manifest 资源不存在：{href}")
        relevant = {
            name for name in names
            if PurePosixPath(name).suffix.lower() in MEDIA_TYPES
            and name != opf_path and not name.startswith("META-INF/")
        }
        registered = set(manifest.values())
        orphaned = sorted(relevant - registered)
        if orphaned:
            raise ValueError(f"资源未登记到 manifest：{orphaned[0]}")
        spine = [x.get("idref") for x in opf.findall(".//{*}spine/{*}itemref") if x.get("idref")]
        missing = [x for x in spine if x not in manifest]
        if missing:
            raise ValueError(f"spine 引用了不存在的 manifest ID：{missing[0]}")
        readable = [x for x in spine if manifest[x] in names]
        if not readable:
            raise ValueError("spine 中没有可读取正文")
        return {"files": len(names), "manifest": len(manifest), "spine": len(readable)}


def manifest_target_paths(zf: zipfile.ZipFile) -> list[str]:
    """Return normalized real ZIP paths referenced by the package manifest."""
    opf_path = get_rootfile(zf)
    opf = ET.fromstring(zf.read(opf_path))
    targets: list[str] = []
    for item in opf.findall(".//{*}manifest/{*}item"):
        href = (item.get("href") or "").strip()
        if not href:
            continue
        path_part = unquote(split_tail(href)[0]).replace("\\", "/")
        target = posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), path_part))
        targets.append(target)
    return list(dict.fromkeys(targets))


def manifest_missing_paths(zf: zipfile.ZipFile) -> list[str]:
    """Return real ZIP paths referenced by OPF but absent from the archive."""
    names = {info.filename.rstrip("/") for info in zf.infolist() if not info.is_dir()}
    return [target for target in manifest_target_paths(zf) if target not in names]


def hydrate_from_reference(source: Path, reference: Path, work_dir: Path) -> tuple[Path, int]:
    """Build a temporary EPUB by restoring missing manifest members from an original EPUB."""
    source = source.resolve()
    reference = reference.resolve()
    if source == reference:
        raise ValueError("参考原书不能与待修复文件相同")
    if not zipfile.is_zipfile(reference):
        raise ValueError("参考原书不是有效的 ZIP/EPUB 文件")

    fd, name = tempfile.mkstemp(prefix="epub_rescue_", suffix=".epub", dir=work_dir)
    os.close(fd)
    hydrated = Path(name)
    try:
        with zipfile.ZipFile(source) as zin, zipfile.ZipFile(reference) as zref:
            missing = manifest_missing_paths(zin)
            if not missing:
                hydrated.unlink(missing_ok=True)
                return source, 0
            reference_names = {i.filename.rstrip("/") for i in zref.infolist() if not i.is_dir()}
            unavailable = [name for name in missing if name not in reference_names]
            if unavailable:
                raise ValueError(
                    f"正文/资源已丢失 {len(missing)} 项，参考原书只能补回 "
                    f"{len(missing) - len(unavailable)} 项；首个仍缺失：{unavailable[0]}"
                )
            with zipfile.ZipFile(hydrated, "w") as zout:
                written: set[str] = set()
                opf_path = get_rootfile(zin)
                source_keep = set(manifest_target_paths(zin)) | {"mimetype", opf_path}
                source_keep.update(
                    info.filename.rstrip("/") for info in zin.infolist()
                    if info.filename.startswith("META-INF/")
                )
                for archive, selected in ((zin, source_keep), (zref, set(missing))):
                    for info in archive.infolist():
                        member = info.filename.rstrip("/")
                        if info.is_dir() or not member or member in written:
                            continue
                        if selected is not None and member not in selected:
                            continue
                        zout.writestr(info, archive.read(info))
                        written.add(member)
        return hydrated, len(missing)
    except Exception:
        hydrated.unlink(missing_ok=True)
        raise


def repair_epub(
    source: Path,
    output_dir: Path | None,
    overwrite: bool,
    destination_stem: str | None = None,
) -> Path:
    source = source.resolve()
    if not zipfile.is_zipfile(source):
        raise ValueError("不是有效的 ZIP/EPUB 文件")
    destination_dir = output_dir.resolve() if output_dir else source.parent
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{destination_stem or source.stem}_修复版.epub"
    if destination.exists() and not overwrite:
        raise FileExistsError(f"输出已存在：{destination.name}（使用 --overwrite 可覆盖）")

    with zipfile.ZipFile(source) as zin:
        if "META-INF/encryption.xml" in zin.namelist():
            encryption = zin.read("META-INF/encryption.xml")
            if b"EncryptedData" in encryption:
                raise ValueError("检测到 DRM/内容加密；本工具不会移除加密，请更换无 DRM 版本")
        infos = zin.infolist()
        mapping = build_name_map(infos)
        output_names = set(mapping.values()) | {"mimetype"}
        old_opf = get_rootfile(zin)
        new_opf = mapping.get(old_opf, old_opf)
        temp_fd, temp_name = tempfile.mkstemp(
            prefix="epub_repair_", suffix=".epub", dir=destination_dir
        )
        os.close(temp_fd)
        temp = Path(temp_name)
        try:
            with zipfile.ZipFile(temp, "w") as zout:
                zout.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
                written = {"mimetype"}
                for info in infos:
                    if info.is_dir():
                        continue
                    old = info.filename.rstrip("/")
                    if not old or old == "mimetype" or old in written:
                        continue
                    new = mapping[old]
                    if new in written:
                        continue
                    data = zin.read(info)
                    if PurePosixPath(old).suffix.lower() in TEXT_EXTS:
                        data = rewrite_text(data, old, new, mapping)
                    if old == old_opf:
                        data = repair_opf(data, new_opf, output_names)
                    new_info = zipfile.ZipInfo(new, info.date_time)
                    new_info.compress_type = zipfile.ZIP_DEFLATED
                    new_info.external_attr = info.external_attr
                    new_info.comment = info.comment
                    zout.writestr(new_info, data)
                    written.add(new)
            validate_epub(temp)
            os.replace(temp, destination)
        except Exception:
            temp.unlink(missing_ok=True)
            raise
    return destination


def find_epubs(inputs: list[str], recursive: bool) -> list[Path]:
    found: list[Path] = []
    for raw in inputs:
        path = Path(raw.strip('"')).resolve()
        if path.is_file() and path.suffix.lower() == ".epub":
            if not path.stem.endswith("_修复版"):
                found.append(path)
        elif path.is_dir():
            iterator = path.rglob("*.epub") if recursive else path.glob("*.epub")
            found.extend(p for p in iterator if not p.stem.endswith("_修复版"))
        else:
            print(f"[跳过] 不存在或不是 EPUB：{path}")
    return list(dict.fromkeys(found))


def calibre_check(path: Path) -> str:
    executable = shutil.which("ebook-meta")
    if not executable:
        return "未检测到 ebook-meta，已完成内部结构验证"
    result = subprocess.run([executable, str(path)], capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ValueError("Calibre ebook-meta 读取失败：" + (result.stderr.strip() or result.stdout.strip()))
    return "Calibre ebook-meta 读取通过"


def main() -> int:
    parser = argparse.ArgumentParser(description="批量修复 EPUB 内部 Windows 非法文件名及其引用")
    parser.add_argument("inputs", nargs="+", help="EPUB 文件或文件夹")
    parser.add_argument("-o", "--output-dir", type=Path, help="统一输出目录；默认与原文件同目录")
    parser.add_argument("-r", "--recursive", action="store_true", help="递归扫描子文件夹")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已有的“_修复版.epub”")
    parser.add_argument(
        "--reference", type=Path,
        help="参考原始 EPUB；当转换版已丢失正文/资源时，从原书补回后再修复",
    )
    args = parser.parse_args()

    files = find_epubs(args.inputs, args.recursive)
    if not files:
        print("没有找到需要处理的 EPUB。")
        return 2
    ok = failed = 0
    print(f"共找到 {len(files)} 个 EPUB。原文件不会被修改。")
    for index, source in enumerate(files, 1):
        print(f"\n[{index}/{len(files)}] {source}")
        try:
            repair_source = source
            recovered = 0
            with contextlib.ExitStack() as cleanup:
                if args.reference:
                    repair_source, recovered = hydrate_from_reference(
                        source, args.reference, (args.output_dir or source.parent).resolve()
                    )
                    if repair_source != source:
                        cleanup.callback(repair_source.unlink, missing_ok=True)
                else:
                    with zipfile.ZipFile(source) as check_zip:
                        missing = manifest_missing_paths(check_zip)
                    if missing:
                        raise ValueError(
                            f"正文/资源已丢失 {len(missing)} 项，不能仅靠改名恢复；"
                            "请用 --reference 指定未丢失内容的原始 EPUB"
                        )
                output = repair_epub(
                    repair_source, args.output_dir, args.overwrite,
                    destination_stem=source.stem,
                )
            stats = validate_epub(output)
            status = calibre_check(output)
            print(f"[成功] {output}")
            rescue_note = f"，从参考原书补回 {recovered} 项" if recovered else ""
            print(
                f"       正文 {stats['spine']} 项，资源 {stats['manifest']} 项"
                f"{rescue_note}；{status}"
            )
            ok += 1
        except Exception as exc:
            print(f"[失败] {exc}")
            failed += 1
    print(f"\n处理完成：成功 {ok}，失败 {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
