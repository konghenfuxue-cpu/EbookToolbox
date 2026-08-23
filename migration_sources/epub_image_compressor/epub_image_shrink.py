#!/usr/bin/env python3
"""安全批量压缩 EPUB 图片，面向手机阅读。需要 Pillow。"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image, ImageFile, ImageOps

ImageFile.LOAD_TRUNCATED_IMAGES = True


@dataclass(frozen=True)
class Profile:
    title: str
    quality: int
    max_long_edge: int
    min_bytes: int


PROFILES = {
    "1": Profile("手机均衡（推荐）", 72, 2400, 80 * 1024),
    "2": Profile("手机高画质", 82, 3000, 120 * 1024),
    "3": Profile("极限省空间", 58, 2000, 60 * 1024),
}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def safe_extract(zf: zipfile.ZipFile, target: Path) -> None:
    root = target.resolve()
    for info in zf.infolist():
        destination = (target / info.filename).resolve()
        if root != destination and root not in destination.parents:
            raise ValueError(f"EPUB 含不安全路径：{info.filename}")
    zf.extractall(target)


def find_cover_paths(root: Path) -> set[Path]:
    """从 OPF 元数据/属性识别封面；文件名识别只作补充。"""
    covers: set[Path] = set()
    for opf in root.rglob("*.opf"):
        try:
            tree = ET.parse(opf)
            manifest = []
            cover_ids = set()
            for elem in tree.iter():
                tag = elem.tag.rsplit("}", 1)[-1]
                if tag == "meta" and elem.attrib.get("name", "").lower() == "cover":
                    if elem.attrib.get("content"):
                        cover_ids.add(elem.attrib["content"])
                elif tag == "item":
                    manifest.append(elem)
            for item in manifest:
                props = item.attrib.get("properties", "").split()
                if item.attrib.get("id") in cover_ids or "cover-image" in props:
                    href = item.attrib.get("href")
                    if href:
                        covers.add((opf.parent / href.split("#", 1)[0]).resolve())
        except Exception:
            pass
    for image in root.rglob("*"):
        if image.is_file() and image.suffix.lower() in IMAGE_EXTS:
            if any(x in image.stem.lower() for x in ("cover", "frontcover", "封面")):
                covers.add(image.resolve())
    return covers


def flatten_alpha(image: Image.Image) -> Image.Image:
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, "white")
        return Image.alpha_composite(bg, rgba).convert("RGB")
    return image.convert("RGB")


def encode_candidate(path: Path, profile: Profile) -> tuple[bytes | None, str]:
    old_size = path.stat().st_size
    if old_size < profile.min_bytes:
        return None, "小图"

    with Image.open(path) as source:
        source.load()
        image = ImageOps.exif_transpose(source)
        original_size = image.size
        long_edge = max(image.size)
        if long_edge > profile.max_long_edge:
            scale = profile.max_long_edge / long_edge
            image = image.resize(
                (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                Image.Resampling.LANCZOS,
            )

        ext = path.suffix.lower()
        output = io.BytesIO()
        if ext in {".jpg", ".jpeg"}:
            image = flatten_alpha(image)
            image.save(
                output,
                format="JPEG",
                quality=profile.quality,
                optimize=True,
                progressive=True,
                subsampling=0 if profile.quality >= 80 else 2,
            )
        elif ext == ".png":
            # 不改变格式，避免透明图变白或修改 EPUB 内引用。
            image.save(output, format="PNG", optimize=True, compress_level=9)
        elif ext == ".webp":
            image.save(output, format="WEBP", quality=profile.quality, method=6)
        else:
            return None, "格式"

    data = output.getvalue()
    # 至少节省 5% 且不少于 8 KiB 才替换，避免无意义的有损重编码。
    if len(data) >= old_size * 0.95 or old_size - len(data) < 8 * 1024:
        return None, "未变小"
    size_text = f"{original_size[0]}x{original_size[1]}"
    return data, size_text


def build_epub(root: Path, output: Path) -> None:
    temp_output = output.with_suffix(output.suffix + ".tmp")
    temp_output.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(temp_output, "w", allowZip64=True) as zf:
            mimetype = root / "mimetype"
            if mimetype.is_file():
                zf.write(mimetype, "mimetype", compress_type=zipfile.ZIP_STORED)
            for file in sorted(root.rglob("*")):
                if file.is_file() and file != mimetype:
                    zf.write(
                        file,
                        file.relative_to(root).as_posix(),
                        compress_type=zipfile.ZIP_DEFLATED,
                        compresslevel=9,
                    )
        with zipfile.ZipFile(temp_output) as check:
            bad = check.testzip()
            if bad:
                raise ValueError(f"压缩包校验失败：{bad}")
            names = check.namelist()
            if "mimetype" in names and names[0] != "mimetype":
                raise ValueError("mimetype 不是首个文件")
        os.replace(temp_output, output)
    finally:
        temp_output.unlink(missing_ok=True)


def process_epub(epub: Path, profile: Profile, include_cover: bool) -> dict:
    old_epub_size = epub.stat().st_size
    output = epub.with_name(epub.stem + "_optimized.epub")
    result = {"output": output, "replaced": 0, "skipped": 0, "failed": 0,
              "image_saved": 0, "old_size": old_epub_size, "new_size": 0}
    with tempfile.TemporaryDirectory(prefix="epub_image_shrink_") as temp_name:
        root = Path(temp_name)
        with zipfile.ZipFile(epub) as zf:
            if zf.testzip() is not None:
                raise ValueError("原 EPUB 的 ZIP 数据已损坏")
            safe_extract(zf, root)
        covers = set() if include_cover else find_cover_paths(root)

        images = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
        for image in images:
            if image.resolve() in covers:
                result["skipped"] += 1
                continue
            try:
                old = image.stat().st_size
                data, reason = encode_candidate(image, profile)
                if data is None:
                    result["skipped"] += 1
                    continue
                image.write_bytes(data)
                saved = old - len(data)
                result["replaced"] += 1
                result["image_saved"] += saved
                print(f"    {image.name}: {old/1024:.0f} KB -> {len(data)/1024:.0f} KB  ({reason})")
            except Exception as exc:
                result["failed"] += 1
                print(f"    [图片失败] {image.name}: {exc}")

        build_epub(root, output)
    result["new_size"] = output.stat().st_size
    return result


def collect_epubs(target: Path) -> list[Path]:
    if target.is_file() and target.suffix.lower() == ".epub":
        return [target]
    if target.is_dir():
        return sorted(
            p for p in target.rglob("*.epub")
            if not p.stem.lower().endswith("_optimized")
        )
    raise ValueError("请输入 EPUB 文件或包含 EPUB 的文件夹")


def choose_profile(value: str | None) -> Profile:
    if value in PROFILES:
        return PROFILES[value]
    print("\n压缩模式：")
    for key, item in PROFILES.items():
        print(f"  {key}. {item.title}：JPEG {item.quality}，最长边 {item.max_long_edge}px")
    choice = input("请选择 [默认 1]：").strip() or "1"
    return PROFILES.get(choice, PROFILES["1"])


def main() -> int:
    parser = argparse.ArgumentParser(description="安全批量压缩 EPUB 图片")
    parser.add_argument("target", nargs="?", help="EPUB 文件或文件夹")
    parser.add_argument("--profile", choices=PROFILES, help="1均衡 / 2高画质 / 3极限")
    parser.add_argument("--include-cover", action="store_true", help="也压缩封面")
    args = parser.parse_args()

    target_text = args.target or input("请输入 EPUB 文件或文件夹（可拖入）：").strip().strip('"')
    target = Path(target_text).expanduser()
    profile = choose_profile(args.profile)
    try:
        files = collect_epubs(target)
    except Exception as exc:
        print(f"[错误] {exc}")
        return 2
    if not files:
        print("没有找到 EPUB 文件。")
        return 1

    print(f"\n发现 {len(files)} 本 EPUB；模式：{profile.title}")
    totals = {"old": 0, "new": 0, "replaced": 0, "failed_books": 0}
    for index, epub in enumerate(files, 1):
        print(f"\n[{index}/{len(files)}] {epub}")
        try:
            result = process_epub(epub, profile, args.include_cover)
            totals["old"] += result["old_size"]
            totals["new"] += result["new_size"]
            totals["replaced"] += result["replaced"]
            reduction = 100 * (1 - result["new_size"] / result["old_size"])
            print(f"  完成：替换 {result['replaced']}，跳过 {result['skipped']}，"
                  f"图片失败 {result['failed']}；EPUB 缩小 {reduction:.1f}%")
            print(f"  输出：{result['output']}")
        except Exception as exc:
            totals["failed_books"] += 1
            print(f"  [本书失败] {exc}")

    print("\n========== 全部完成 ==========")
    print(f"成功处理：{len(files) - totals['failed_books']} / {len(files)}")
    print(f"替换图片：{totals['replaced']}；失败书籍：{totals['failed_books']}")
    if totals["old"]:
        saved = totals["old"] - totals["new"]
        print(f"总体积：{totals['old']/1048576:.2f} MB -> {totals['new']/1048576:.2f} MB，"
              f"节省 {saved/1048576:.2f} MB（{saved/totals['old']*100:.1f}%）")
    return 0 if totals["failed_books"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
