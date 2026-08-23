#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修复多卷合集及单卷 EPUB 的缺失/空白目录。版本 2.4，仅使用标准库。"""

import argparse
import html
import os
import posixpath
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

OPF = "http://www.idpf.org/2007/opf"
NCX = "http://www.daisy.org/z3986/2005/ncx/"
DC = "http://purl.org/dc/elements/1.1/"
CONTAINER = "urn:oasis:names:tc:opendocument:xmlns:container"
ET.register_namespace("", OPF)
ET.register_namespace("dc", DC)


def local(tag):
    return tag.rsplit("}", 1)[-1]


def text_of(node):
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


def manifest(root):
    return {x.get("id"): x.get("href") for x in root.iter() if local(x.tag) == "item" and x.get("id") and x.get("href")}


def find_rootfile(zf):
    root = ET.fromstring(zf.read("META-INF/container.xml"))
    for node in root.iter():
        if local(node.tag) == "rootfile":
            return node.get("full-path")
    raise ValueError("container.xml 中找不到主 OPF")


def parse_opf(raw):
    """解析 OPF；兼容仅遗漏 </spine> 的常见损坏文件。"""
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw
        if re.search(r"<spine\b", text, re.I) and not re.search(r"</spine\s*>", text, re.I):
            repaired, count = re.subn(r"</package\s*>", "</spine>\n</package>", text, count=1, flags=re.I)
            if count:
                return ET.fromstring(repaired)
        raise


def volume_ncxs(zf):
    found = []
    for name in zf.namelist():
        # 合并源常见两种位置：1/toc.ncx 与 1/OEBPS/toc.ncx。
        m = re.fullmatch(r"(\d+)/(?:OEBPS/)?toc\.ncx", name, re.I)
        if m:
            found.append((int(m.group(1)), name))
    return sorted(found)


def ncx_entries(zf, name):
    root = ET.fromstring(zf.read(name))
    entries = []
    for point in root.iter():
        if local(point.tag) != "navPoint":
            continue
        label = ""
        src = ""
        for child in point.iter():
            if local(child.tag) == "text" and not label:
                label = text_of(child)
            elif local(child.tag) == "content" and not src:
                src = child.get("src", "")
        if label and src:
            entries.append((label, src))
    return entries


def choose_source_opf(zf, opf_names, master_name, master_items, vol, entries):
    # epubmerge 会在主 manifest 中保留 a1rootfile、a2rootfile……，这是分卷
    # 与原始 OPF 的精确对应关系。必须优先使用它；不同卷常复用同一批 id，
    # 仅靠“id 命中数量”评分会在分数相同时误选上一卷 OPF，导致整卷错位。
    declared = master_items.get(f"a{vol}rootfile")
    if declared:
        declared_name = posixpath.normpath(posixpath.join(posixpath.dirname(master_name), declared))
        if declared_name in opf_names:
            root = ET.fromstring(zf.read(declared_name))
            return {href: iid for iid, href in manifest(root).items()}
    best = None
    best_score = -1
    source_paths = {x.partition("#")[0] for _, x in entries}
    for name in opf_names:
        if name == master_name:
            continue
        root = ET.fromstring(zf.read(name))
        items = manifest(root)
        href_to_id = {href: iid for iid, href in items.items()}
        score = 0
        for path in source_paths:
            iid = href_to_id.get(path)
            if iid and f"a{vol}{iid}" in master_items:
                score += 10
            elif iid:
                score += 1
        if score > best_score:
            best = (name, href_to_id)
            best_score = score
    if not best or best_score <= 0:
        raise ValueError(f"无法确定第 {vol} 卷对应的 OPF")
    return best[1]


def visible_chinese_map(zf):
    """从书内原有可视目录提取中文标题：中文段落通常紧邻日文链接之前。"""
    result = {}
    for name in zf.namelist():
        if not name.lower().endswith((".xhtml", ".html")):
            continue
        try:
            raw = zf.read(name).decode("utf-8", "ignore")
        except Exception:
            continue
        # 文件名不一定含 toc；用“目录文字＋多个链接”识别可视目录页。
        if len(re.findall(r'<a\b[^>]*href=', raw, re.I)) < 2 or not re.search(r"目次|目录|CONTENTS", raw, re.I):
            continue
        # 逐个链接，取链接前最近一个 <p> 的纯文本。
        for m in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>', raw, re.I):
            href = html.unescape(m.group(1))
            prefix = raw[:m.start()]
            ps = list(re.finditer(r"<p\b[^>]*>(.*?)</p\s*>", prefix, re.I | re.S))
            if not ps:
                continue
            label = re.sub(r"<[^>]+>", "", ps[-1].group(1))
            label = " ".join(html.unescape(label).replace("\u3000", " ").split())
            if not label or label.upper() in {"CONTENTS", "TABLE OF CONTENTS"}:
                continue
            base = posixpath.dirname(name)
            path, sep, frag = href.partition("#")
            full = posixpath.normpath(posixpath.join(base, path))
            # 主 OPF 位于 OEBPS 时，导航 href 应相对 OEBPS。
            rel = posixpath.relpath(full, "OEBPS") if full.startswith("OEBPS/") else full
            key = rel + (("#" + frag) if sep else "")
            result[key] = label
            result.setdefault(rel, label)
    return result


def normalized_label(jp, target, zh):
    simple = {
        "表紙": "封面", "目次": "目录", "もくじ": "目录", "奥付": "版权页",
        "プロローグ": "序章", "エピローグ": "终章", "あとがき": "后记",
    }
    if jp in simple:
        return simple[jp]
    if jp.startswith("目次") or "contents" in jp.lower():
        return "目录"
    if target in zh:
        return zh[target]
    # 带副标题的序章/终章。
    for src, dst in (("プロローグ", "序章"), ("エピローグ", "终章"), ("あとがき", "后记")):
        if jp.startswith(src):
            return dst + jp[len(src):]
    return jp


def target_page_label(zf, master_name, href):
    """从合并后的实际正文页读取中文章名，避免目录仍显示原始日文章名。"""
    path = href.partition("#")[0]
    full = posixpath.normpath(posixpath.join(posixpath.dirname(master_name), path))
    if full not in zf.namelist() or not full.lower().endswith((".xhtml", ".html")):
        return ""
    try:
        root = ET.fromstring(zf.read(full))
    except Exception:
        return ""
    body = next((x for x in root.iter() if local(x.tag) == "body"), root)
    checked = 0
    pattern = re.compile(
        r"^(?:序章|终章|尾声|后\s*记|番外(?:篇|编)|加笔(?:番外|短篇)|书下|特典|短篇|"
        r"第\s*[0-9０-９零〇一二三四五六七八九十百]+\s*[话話章节節])(?:\s*.*)?$"
    )
    for node in body.iter():
        if local(node.tag) not in {"p", "h1", "h2", "h3", "h4", "h5", "h6"}:
            continue
        label = text_of(node).replace("　", " ")
        label = " ".join(label.split())
        if not label:
            continue
        checked += 1
        if len(label) <= 120 and pattern.fullmatch(label):
            if re.fullmatch(r"后\s*记", label):
                return "后记"
            return label
        if checked >= 16:
            break
    return ""


def visible_toc_sequence(zf, master_name, href):
    """读取无链接可视目录中的中文章名，并保留原有顺序。"""
    path = href.partition("#")[0]
    full = posixpath.normpath(posixpath.join(posixpath.dirname(master_name), path))
    if full not in zf.namelist():
        return []
    try:
        root = ET.fromstring(zf.read(full))
    except Exception:
        return []
    body = next((x for x in root.iter() if local(x.tag) == "body"), root)
    labels = []
    started = False
    for node in body.iter():
        if local(node.tag) not in {"p", "h1", "h2", "h3", "h4", "h5", "h6"}:
            continue
        label = text_of(node).replace("　", " ")
        label = " ".join(label.split())
        if not label:
            continue
        if label.upper() in {"CONTENTS", "TABLE OF CONTENTS"} or label in {"目录", "目次", "もくじ"}:
            started = True
            continue
        # 这是由原 NCX 明确指向的可视目录页，因此目录标记之后的短文本
        # 都应视为目录项；很多轻小说章节没有“第X话”等固定前缀。
        if started and len(label) <= 160:
            if re.fullmatch(r"后\s*记", label):
                label = "后记"
            elif re.match(r"^[0-9０-９零〇一二三四五六七八九十百]+\s*[章话話节節](?:\s|$)", label):
                label = "第" + label
            labels.append(label)
    return labels


def usable_fragment(zf, master_name, href, frag):
    """确认合并后的目标文件仍保留原锚点；锚点丢失时安全退回文件开头。"""
    if not frag:
        return ""
    full = posixpath.normpath(posixpath.join(posixpath.dirname(master_name), href))
    if full not in zf.namelist():
        return ""
    raw = zf.read(full)
    if f'id="{frag}"'.encode() in raw or f"id='{frag}'".encode() in raw:
        return frag
    return ""


def collect_volumes(zf, master_name, master_root):
    master_items = manifest(master_root)
    opf_names = [n for n in zf.namelist() if n.lower().endswith(".opf")]
    zh = visible_chinese_map(zf)
    volume_sources = volume_ncxs(zf)
    if not volume_sources:
        return collect_flat_volumes(zf, master_name, master_root)
    volumes = []
    for vol, ncx_name in volume_sources:
        source = ncx_entries(zf, ncx_name)
        href_to_id = choose_source_opf(zf, opf_names, master_name, master_items, vol, source)
        toc_labels = []
        for source_label, source_href in source:
            if normalized_label(source_label, "", {}) != "目录":
                continue
            toc_path = source_href.partition("#")[0]
            toc_iid = href_to_id.get(toc_path)
            toc_target = master_items.get(f"a{vol}{toc_iid}") if toc_iid else None
            if toc_target:
                toc_labels = visible_toc_sequence(zf, master_name, toc_target)
            break
        entries = []
        for jp, src in source:
            # 先按原始 NCX 标签排除目录、封面和版权页。若先读取目标页标题，
            # “Contents” 页面可能因页面内出现“序章”而被误识别成重复章节。
            source_kind = normalized_label(jp, "", {})
            if source_kind in {"目录", "封面", "版权页"}:
                continue
            path, sep, frag = src.partition("#")
            iid = href_to_id.get(path)
            target = master_items.get(f"a{vol}{iid}") if iid else None
            if not target:
                continue
            kept_frag = usable_fragment(zf, master_name, target, frag) if sep else ""
            href = target + (("#" + kept_frag) if kept_frag else "")
            label = target_page_label(zf, master_name, href) or normalized_label(jp, href, zh)
            entries.append((label, href))
        # 无链接可视目录仍可作为可靠的中文标题来源。仅在条目数量完整
        # 对应（或只少最后一篇后记）时批量采用，避免标题错套。
        if len(toc_labels) in {len(entries), len(entries) - 1}:
            for index, label in enumerate(toc_labels):
                entries[index] = (label, entries[index][1])
        useful = [(a, b) for a, b in entries if a not in {"目录", "封面", "版权页"}]
        if useful:
            volumes.append((f"第{vol}卷", useful))
    if len(volumes) < 2:
        grouped = collect_prefixed_groups(zf, master_name, master_root)
        if grouped:
            return grouped
        raise ValueError("未识别到至少两卷。分卷源目录可能已经丢失")
    return volumes


def collect_prefixed_groups(zf, master_name, master_root):
    """分卷 NCX 已空时，按合并器留下的 a1、a2……书脊前缀恢复卷级目录。"""
    master_dir = posixpath.dirname(master_name)
    items = manifest(master_root)
    spine_node = next((x for x in master_root.iter() if local(x.tag) == "spine"), None)
    groups = {}
    for ref in list(spine_node) if spine_node is not None else []:
        iid = ref.get("idref", "")
        m = re.match(r"a(\d+)(.+)", iid)
        href = items.get(iid)
        if m and href and href.lower().endswith((".xhtml", ".html")):
            groups.setdefault(int(m.group(1)), []).append((iid, href))
    if len(groups) < 2:
        return []
    names = set(zf.namelist())
    volumes = []
    max_group = max(groups)
    for number in sorted(groups):
        docs = groups[number]
        start = None
        afterword = None
        fallback = None
        for iid, href in docs:
            full = posixpath.normpath(posixpath.join(master_dir, href))
            if full not in names:
                continue
            low = iid.lower()
            raw = zf.read(full).decode("utf-8", "ignore")
            plain = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", raw)).split())
            excluded = any(x in low for x in ("cover", "fmatter", "caution", "titlepage", "allcover", "colophon", "bookwalker", "bookmark"))
            if not excluded and fallback is None and len(plain) > 80:
                fallback = href
            if not excluded and (re.search(r"(?:p-)?0*0*1(?:\D|$)", low) or low.endswith("part0008")) and start is None:
                start = href
            if ("后记" in plain[:250] or re.search(r"后\s+记", plain[:250]) or "あとがき" in plain[:250]) and afterword is None:
                afterword = href
        start = start or fallback or docs[0][1]
        if number <= 16:
            vol_name = f"第{number}卷"
        elif number == 17:
            vol_name = "第16卷 BOOK☆WALKER限定短篇"
        elif number == 18:
            vol_name = "DJCD 特别篇"
        else:
            vol_name = f"附加内容 {number - 16}"
        entries = [("开始阅读", start)]
        if afterword and afterword != start:
            entries.append(("后记", afterword))
        volumes.append((vol_name, entries))
    return volumes


def extract_links(raw, doc_name, master_dir):
    """提取可视目录里的链接及其中文标题。"""
    result = []
    for m in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a\s*>', raw, re.I | re.S):
        href = html.unescape(m.group(1))
        if re.match(r"^[a-z]+:", href, re.I):
            continue
        label = re.sub(r"<[^>]+>", " ", m.group(2))
        label = " ".join(html.unescape(label).replace("\u3000", " ").split())
        if not label:
            continue
        path, sep, frag = href.partition("#")
        full = posixpath.normpath(posixpath.join(posixpath.dirname(doc_name), path))
        rel = posixpath.relpath(full, master_dir) if master_dir else full
        result.append((label, rel + (("#" + frag) if sep else "")))
    return result


def extract_links_allow_empty(raw, doc_name, master_dir):
    """提取导航目标；EPUB 3 损坏目录中的 a 标签可能没有任何文字。"""
    result = []
    for m in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a\s*>', raw, re.I | re.S):
        href = html.unescape(m.group(1))
        if re.match(r"^[a-z]+:", href, re.I):
            continue
        label = re.sub(r"<[^>]+>", " ", m.group(2))
        label = " ".join(html.unescape(label).replace("\u3000", " ").split())
        path, sep, frag = href.partition("#")
        full = posixpath.normpath(posixpath.join(posixpath.dirname(doc_name), path))
        rel = posixpath.relpath(full, master_dir) if master_dir else full
        result.append((label, rel + (("#" + frag) if sep else "")))
    return result


def plain_toc_labels(raw):
    """读取没有超链接的可视目录，如“序章／一章／幕间／终章”。"""
    labels = []
    for m in re.finditer(r"<(?:p|h[1-6])\b[^>]*>(.*?)</(?:p|h[1-6])\s*>", raw, re.I | re.S):
        label = re.sub(r"<[^>]+>", " ", m.group(1))
        label = " ".join(html.unescape(label).replace("\u3000", " ").split())
        if not label or label in {"目录", "目次", "もくじ"} or label.upper() in {"CONTENTS", "TABLE OF CONTENTS"}:
            continue
        if len(label) <= 160:
            labels.append(label)
    return labels


def recover_empty_nav_toc(zf, master_name, master_root, master_dir, spine):
    """用空白 EPUB 3 nav 的目标，配对无链接可视目录页中的章名。"""
    nav_href = next(
        (
            node.get("href") for node in master_root.iter()
            if local(node.tag) == "item"
            and "nav" in node.get("properties", "").split()
            and node.get("href")
        ),
        "",
    )
    if not nav_href:
        return None
    nav_full = posixpath.normpath(posixpath.join(master_dir, nav_href))
    if nav_full not in zf.namelist():
        return None
    targets = extract_links_allow_empty(zf.read(nav_full).decode("utf-8", "ignore"), nav_full, master_dir)
    targets = [(label, href) for label, href in targets if href.partition("#")[0] in spine]
    # 已由旧版脚本修复过 nav 时，直接复用完整目录。这使 v2.2 可以继续
    # 给正文的图片式 h1 补充文字，不再误报“未识别到至少两卷”。
    labeled = [(label, href) for label, href in targets if label]
    if len(labeled) >= 2 and len(labeled) == len(targets):
        return labeled
    # 原始损坏书通常是：toc 项为空，landmarks（封面、正文、目录）有字。
    targets = [(label, href) for label, href in targets if not label]
    if len(targets) < 2:
        return None

    candidates = []
    for href in spine:
        base = posixpath.basename(href).lower()
        if not (re.fullmatch(r"(?:p-)?toc(?:-?\d+)?\.xhtml?", base) or "contents" in base):
            continue
        full = posixpath.normpath(posixpath.join(master_dir, href))
        if full not in zf.namelist():
            continue
        labels = plain_toc_labels(zf.read(full).decode("utf-8", "ignore"))
        if len(labels) == len(targets):
            candidates.append(labels)
    if len(candidates) != 1:
        return None
    return [(candidates[0][i], href) for i, (_, href) in enumerate(targets)]


def recover_fragment_toc(zf, master_dir, spine):
    """用损坏目录链接中的 fragment，反查真正包含该 id 的正文页。"""
    names = set(zf.namelist())
    fragment_targets = {}
    raws = {}
    for href in spine:
        full = posixpath.normpath(posixpath.join(master_dir, href))
        if full not in names or not href.lower().endswith((".xhtml", ".html")):
            continue
        raw = zf.read(full).decode("utf-8", "ignore")
        raws[href] = raw
        for frag in re.findall(r"\bid=[\"']([^\"']+)[\"']", raw, re.I):
            fragment_targets.setdefault(frag, href)

    best = []
    for _, raw in raws.items():
        found = []
        for m in re.finditer(r'<a\b[^>]*href=["\']([^"\']*#([^"\']+))["\'][^>]*>', raw, re.I):
            frag = html.unescape(m.group(2))
            target = fragment_targets.get(frag)
            if not target:
                continue
            # 双语目录通常在链接（日文）前一段放中文标题。
            prefix = raw[:m.start()]
            ps = list(re.finditer(r"<p\b[^>]*>(.*?)</p\s*>", prefix, re.I | re.S))
            label = ""
            if ps:
                label = re.sub(r"<[^>]+>", " ", ps[-1].group(1))
                label = " ".join(html.unescape(label).replace("\u3000", " ").split())
            if label and len(label) <= 160:
                found.append((label, target + "#" + frag))
        if len(found) > len(best):
            best = found
    return best if len(best) >= 3 else []


def patch_image_heading(raw, label):
    """给只有图片的 h1/h2 补不可见文字，供 Sigil/Calibre 生成目录。"""
    text = raw.decode("utf-8", "ignore")
    escaped = html.escape(label)

    def replace(match):
        inner = match.group(2)
        # 已经有可见或隐藏文字时保持原样，确保脚本可重复运行。
        plain = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", inner)).split())
        if plain or "data-toc-title=" in inner:
            return match.group(0)
        if not re.search(r"<img\b", inner, re.I):
            return match.group(0)
        hidden = f'<span data-toc-title="true" style="display:none">{escaped}</span>'
        return match.group(1) + inner + hidden + match.group(3)

    patched, count = re.subn(
        r"(<h([12])\b[^>]*>)(.*?)(</h\2\s*>)",
        lambda m: replace(_HeadingMatch(m)),
        text,
        count=1,
        flags=re.I | re.S,
    )
    return patched.encode("utf-8") if count and patched != text else raw


def patch_fragment_heading(raw, label, frag):
    """目标锚点处没有 heading 时补隐藏 h1，保留原来的可视排版。"""
    if not frag:
        return raw
    text = raw.decode("utf-8", "ignore")
    if f'data-toc-fragment="{html.escape(frag, quote=True)}"' in text:
        return raw
    pattern = re.compile(r'(<(?:div|section)\b[^>]*\bid=["\']' + re.escape(frag) + r'["\'][^>]*>)', re.I)
    hidden = (
        f'<h1 data-toc-fragment="{html.escape(frag, quote=True)}" '
        f'style="display:none">{html.escape(label)}</h1>'
    )
    patched, count = pattern.subn(lambda m: m.group(1) + hidden, text, count=1)
    return patched.encode("utf-8") if count else raw


class _HeadingMatch:
    """把正则分组整理为 patch_image_heading 需要的开头/正文/结尾。"""
    def __init__(self, match):
        self.parts = (match.group(1), match.group(3), match.group(4))

    def group(self, index):
        return self.parts[index - 1]


def page_label(zf, name):
    raw = zf.read(name).decode("utf-8", "ignore")
    for pattern in (r"<h[1-3]\b[^>]*>(.*?)</h[1-3]\s*>", r"<title\b[^>]*>(.*?)</title\s*>"):
        m = re.search(pattern, raw, re.I | re.S)
        if m:
            label = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).split())
            if label and label.lower() not in {"untitled", "目录"}:
                return label
    plain = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", raw)).split())
    for label in ("后记", "特典小册子", "尾声"):
        if label in plain[:100]:
            return label
    return posixpath.splitext(posixpath.basename(name))[0]


def leading_section_label(zf, name, fallback_number):
    raw = zf.read(name).decode("utf-8", "ignore")
    body = re.search(r"<body\b[^>]*>(.*?)</body\s*>", raw, re.I | re.S)
    plain = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", body.group(1) if body else raw)).split())
    m = re.match(r"([0-9０-９]+)(?:\s|$)", plain)
    if m:
        number = m.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        return f"第{number}节"
    return f"第{fallback_number}节"


def collect_segmented_contents(zf, master_dir, spine, toc_i, links):
    """一个总目录列出多卷，而每卷正文由多个连续文件组成。"""
    index_by_href = {href: i for i, href in enumerate(spine)}
    starts = []
    for label, href in links:
        path = href.partition("#")[0]
        if path in index_by_href:
            starts.append((index_by_href[path], label, href))
    if len(starts) < 2:
        return []
    starts.sort()
    cn = "零一二三四五六七八九十"
    volumes = []
    names = set(zf.namelist())
    for vi, (start, title, cover_href) in enumerate(starts, 1):
        end = starts[vi][0] if vi < len(starts) else len(spine)
        vol_name = f"第{cn[vi] if vi < len(cn) else vi}卷 - {title}"
        entries = [(f"卷首 - {title}", cover_href)]
        section_no = 1
        for href in spine[start + 1:end]:
            full = posixpath.normpath(posixpath.join(master_dir, href))
            base = posixpath.basename(href).lower()
            if full not in names or not href.lower().endswith((".xhtml", ".html")):
                continue
            if any(word in base for word in ("cover", "title", "message", "contents", "illus", "bookmark", "back", "nav.xhtml")):
                continue
            entries.append((leading_section_label(zf, full, section_no), href))
            section_no += 1
        volumes.append((vol_name, entries))
    return volumes


def single_toc_entries(zf, master_dir, links):
    """把单卷书已有的可视目录转换成阅读器导航目录。"""
    zh = visible_chinese_map(zf)
    names = set(zf.namelist())
    entries = []
    seen = set()
    for source_label, href in links:
        path = href.partition("#")[0]
        full = posixpath.normpath(posixpath.join(master_dir, path))
        if full not in names or href in seen:
            continue
        label = zh.get(href) or zh.get(path) or source_label
        label = " ".join(label.replace("　", " ").split())
        if re.fullmatch(r"后\s*记", label):
            label = "后记"
        elif label.startswith("结局"):
            label = "终章" + label[2:]
        number_title = re.fullmatch(r"([0-9０-９]+)\s+(.+)", label)
        if number_title:
            number = number_title.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
            label = f"第{number}章 - {number_title.group(2)}"
        if normalized_label(label, "", {}) in {"目录", "封面", "版权页"}:
            continue
        entries.append((label, href))
        seen.add(href)
    return entries


def looks_like_chapter_toc(entries):
    """区分“单卷章节目录”和“一个总目录列出多卷”的结构。"""
    if len(entries) < 2:
        return False
    chapterish = re.compile(
        r"^[●◆◇☆★]?\s*(?:序章|终章|结局|尾声|后记|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+|"
        r"幕间[零〇一二三四五六七八九十百0-9０-９]*|"
        r"あとがき|プロローグ|エピローグ|"
        r"第?\s*[0-9０-９零〇一二三四五六七八九十百]+\s*[章话話节節册冊本]?)(?:\s|$)"
    )
    return sum(bool(chapterish.match(label)) for label, _ in entries) >= 2


def collect_flat_volumes(zf, master_name, master_root):
    """处理只剩一个 OPF、但每卷仍保留可视目录页的扁平化合集。"""
    master_dir = posixpath.dirname(master_name)
    items = manifest(master_root)
    spine = []
    spine_node = next((x for x in master_root.iter() if local(x.tag) == "spine"), None)
    for ref in list(spine_node) if spine_node is not None else []:
        if local(ref.tag) == "itemref" and ref.get("idref") in items:
            spine.append(items[ref.get("idref")])
    toc_indexes = []
    for i, href in enumerate(spine):
        base = posixpath.basename(href).lower()
        if re.fullmatch(r"(?:p-)?contents(?:\d+)?\.xhtml?", base) or re.fullmatch(r"p-toc(?:-?\d+)?\.xhtml?", base):
            full = posixpath.normpath(posixpath.join(master_dir, href))
            if full in zf.namelist():
                links = extract_links(zf.read(full).decode("utf-8", "ignore"), full, master_dir)
                if links:
                    toc_indexes.append((i, href, links))
    # 有些单卷 EPUB 的可视目录使用随机文件名（例如 c2M.xhtml），只能
    # 从 OPF guide 的 type="toc" 或页面里的 CONTENTS 标记找到。
    if not toc_indexes:
        for node in master_root.iter():
            if local(node.tag) != "reference" or node.get("type", "").lower() != "toc":
                continue
            href = node.get("href", "")
            path = href.partition("#")[0]
            full = posixpath.normpath(posixpath.join(master_dir, path))
            if path in spine and full in zf.namelist():
                links = extract_links(zf.read(full).decode("utf-8", "ignore"), full, master_dir)
                if links:
                    toc_indexes.append((spine.index(path), path, links))
                    break
    if not toc_indexes:
        for i, href in enumerate(spine):
            full = posixpath.normpath(posixpath.join(master_dir, href))
            if full not in zf.namelist() or not href.lower().endswith((".xhtml", ".html")):
                continue
            raw = zf.read(full).decode("utf-8", "ignore")
            plain = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", raw)).split())
            if not re.search(r"(?:^|\s)(?:CONTENTS|目次|目录)(?:\s|$)", plain, re.I):
                continue
            links = extract_links(raw, full, master_dir)
            if len(links) >= 2:
                toc_indexes.append((i, href, links))
                break
    # 特殊损坏结构：EPUB 3 nav 保留了正确目标，但所有链接文字为空；
    # 另一张 p-toc/contents 页面保留章名，却没有链接。按顺序安全配对。
    if not toc_indexes:
        recovered = recover_empty_nav_toc(zf, master_name, master_root, master_dir, spine)
        if recovered:
            toc_indexes.append((-1, "", recovered))
    # 可视目录链接可能指向已经不存在的 part0004.html，但 #id001a 等
    # 锚点仍位于真实正文中；扫描全书 id 后即可恢复目标。
    if not toc_indexes:
        recovered = recover_fragment_toc(zf, master_dir, spine)
        if recovered:
            toc_indexes.append((-1, "", recovered))
    if len(toc_indexes) == 1:
        entries = single_toc_entries(zf, master_dir, toc_indexes[0][2])
        if looks_like_chapter_toc(entries):
            # 可视目录往往不列后记。若最后一个目录目标之后仍有明确的
            # 后记正文页，自动补入；版权页、书店广告等不会误收。
            linked = {href.partition("#")[0] for _, href in entries}
            last = max((spine.index(x) for x in linked if x in spine), default=-1)
            for href in spine[last + 1:]:
                full = posixpath.normpath(posixpath.join(master_dir, href))
                if full not in zf.namelist() or not href.lower().endswith((".xhtml", ".html")):
                    continue
                raw = zf.read(full).decode("utf-8", "ignore")
                plain = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", raw)).split())
                if re.search(r"(?:^|\s)(?:后\s*记|あとがき)(?:\s|$)", plain[:400]):
                    entries.append(("后记", href))
                    break
            # 空卷名表示单卷模式；nav.xhtml/toc.ncx 会直接列出章节，
            # 不额外制造一个“第1卷”父节点。
            return [("", entries)]
        segmented = collect_segmented_contents(zf, master_dir, spine, toc_indexes[0][0], toc_indexes[0][2])
        if segmented:
            return segmented
        if entries:
            return [("", entries)]
    if len(toc_indexes) < 2:
        raise ValueError("未识别到至少两卷：既没有分卷 toc.ncx，也没有足够的可视目录页")
    volumes = []
    names = set(zf.namelist())
    for vi, (toc_i, toc_href, links) in enumerate(toc_indexes, 1):
        entries = []
        seen = set()
        for label, href in links:
            path = href.partition("#")[0]
            full = posixpath.normpath(posixpath.join(master_dir, path))
            if full in names and href not in seen:
                entries.append((label, href)); seen.add(href)
        # 补充目录页未列出的后记、特典页。
        end = toc_indexes[vi][0] if vi < len(toc_indexes) else len(spine)
        for href in spine[toc_i + 1:end]:
            base = posixpath.basename(href).lower()
            if not re.match(r"(?:afterword|postscript|special|sp(?:[-_]|\.))", base):
                continue
            if "illus" in base or "illustration" in base:
                continue
            if any(x.partition("#")[0].lower() == href.lower() for _, x in entries):
                continue
            full = posixpath.normpath(posixpath.join(master_dir, href))
            if full in names:
                entries.append((page_label(zf, full), href))
        if entries:
            volumes.append((f"第{vi}卷", entries))
    return volumes


def relative_href(href, document_href):
    """把相对 OPF 的目标改写为相对当前导航文档的路径。"""
    path, sep, frag = href.partition("#")
    base = posixpath.dirname(document_href)
    rel = posixpath.relpath(path, base) if base else path
    return rel + (("#" + frag) if sep else "")


def make_visible_toc(volumes, document_href):
    out = ['<?xml version="1.0" encoding="utf-8"?>', '<!DOCTYPE html>',
           '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="zh" xml:lang="zh">',
           '<head><meta charset="utf-8"/><title>合集目录</title>',
           '<style>body{font-family:serif;line-height:1.7;margin:5%}h1{text-align:center}h2{margin-top:1.4em}li{margin:.25em 0}a{text-decoration:none;color:inherit}</style></head>',
           '<body><nav epub:type="toc" id="toc"><h1>合集目录</h1>']
    for vol, entries in volumes:
        out.append(f"<section><h2>{vol}</h2><ol>")
        out.extend(f'<li><a href="{html.escape(relative_href(href, document_href), quote=True)}">{html.escape(label)}</a></li>' for label, href in entries)
        out.append("</ol></section>")
    out.append("</nav></body></html>")
    return "\n".join(out).encode("utf-8")


def make_nav(volumes, document_href):
    out = ['<?xml version="1.0" encoding="utf-8"?>', '<!DOCTYPE html>',
           '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="zh" xml:lang="zh">',
           '<head><meta charset="utf-8"/><title>目录</title></head><body>',
           '<nav epub:type="toc" id="toc"><h1>目录</h1><ol>']
    for vol, entries in volumes:
        if vol:
            out.append(f'<li><a href="{html.escape(relative_href(entries[0][1], document_href), quote=True)}">{vol}</a><ol>')
            out.extend(f'<li><a href="{html.escape(relative_href(href, document_href), quote=True)}">{html.escape(label)}</a></li>' for label, href in entries)
            out.append("</ol></li>")
        else:
            out.extend(f'<li><a href="{html.escape(relative_href(href, document_href), quote=True)}">{html.escape(label)}</a></li>' for label, href in entries)
    out.append("</ol></nav></body></html>")
    return "\n".join(out).encode("utf-8")


def make_ncx(raw, volumes):
    root = ET.fromstring(raw)
    navmap = next((x for x in root.iter() if local(x.tag) == "navMap"), None)
    if navmap is None:
        raise ValueError("主 toc.ncx 中找不到 navMap")
    navmap[:] = []
    order = 1
    for vi, (vol, entries) in enumerate(volumes, 1):
        if not vol:
            for ci, (label, href) in enumerate(entries, 1):
                cp = ET.SubElement(navmap, f"{{{NCX}}}navPoint", {"id": f"ch-{ci}", "playOrder": str(order)}); order += 1
                cl = ET.SubElement(cp, f"{{{NCX}}}navLabel"); ET.SubElement(cl, f"{{{NCX}}}text").text = label
                ET.SubElement(cp, f"{{{NCX}}}content", {"src": href})
            continue
        vp = ET.SubElement(navmap, f"{{{NCX}}}navPoint", {"id": f"vol-{vi}", "playOrder": str(order)}); order += 1
        vl = ET.SubElement(vp, f"{{{NCX}}}navLabel"); ET.SubElement(vl, f"{{{NCX}}}text").text = vol
        ET.SubElement(vp, f"{{{NCX}}}content", {"src": entries[0][1]})
        for ci, (label, href) in enumerate(entries, 1):
            cp = ET.SubElement(vp, f"{{{NCX}}}navPoint", {"id": f"vol-{vi}-ch-{ci}", "playOrder": str(order)}); order += 1
            cl = ET.SubElement(cp, f"{{{NCX}}}navLabel"); ET.SubElement(cl, f"{{{NCX}}}text").text = label
            ET.SubElement(cp, f"{{{NCX}}}content", {"src": href})
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def patch_opf(root, toc_href):
    mani = next(x for x in root.iter() if local(x.tag) == "manifest")
    spine = next(x for x in root.iter() if local(x.tag) == "spine")
    old = next((x for x in mani if x.get("id") == "collection-toc"), None)
    if old is None:
        ET.SubElement(mani, f"{{{OPF}}}item", {"id": "collection-toc", "href": toc_href, "media-type": "application/xhtml+xml"})
    if not any(x.get("idref") == "collection-toc" for x in spine):
        spine.insert(1, ET.Element(f"{{{OPF}}}itemref", {"idref": "collection-toc"}))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def remove_generated_visible_toc(root):
    """单卷模式不保留旧版脚本插入的“合集目录”页面。"""
    mani = next(x for x in root.iter() if local(x.tag) == "manifest")
    spine = next(x for x in root.iter() if local(x.tag) == "spine")
    for node in list(spine):
        if node.get("idref") == "collection-toc":
            spine.remove(node)
    for node in list(mani):
        if node.get("id") == "collection-toc":
            mani.remove(node)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def normalize_opf_namespaces(root):
    """修复 manifest/item 被错误写入 XHTML 命名空间的 EPUB。"""
    mani = next((x for x in root.iter() if local(x.tag) == "manifest"), None)
    if mani is None:
        raise ValueError("主 OPF 中找不到 manifest")
    for node in list(mani):
        if local(node.tag) == "item" and node.tag != f"{{{OPF}}}item":
            node.tag = f"{{{OPF}}}item"


def validate_opf_spine(zf, master_name):
    """按 Calibre 的严格规则确认 spine 能解析到有效 XHTML 资源。"""
    root = ET.fromstring(zf.read(master_name))
    strict_items = {
        node.get("id"): node.get("href")
        for node in root.iter(f"{{{OPF}}}item")
        if node.get("id") and node.get("href")
        and node.get("media-type") == "application/xhtml+xml"
    }
    refs = [
        node.get("idref") for node in root.iter(f"{{{OPF}}}itemref")
        if node.get("idref")
    ]
    resolved = [strict_items[x] for x in refs if x in strict_items]
    if not resolved:
        raise ValueError("OPF 书脊没有可解析的正文；Calibre 会报告 Book is empty")
    base = posixpath.dirname(master_name)
    names = set(zf.namelist())
    missing = [href for href in resolved if posixpath.normpath(posixpath.join(base, href)) not in names]
    if missing:
        raise ValueError(f"OPF 书脊引用的正文不存在：{missing[0]}")


def validate(zf, master_dir, volumes):
    broken = []
    names = set(zf.namelist())
    for _, entries in volumes:
        for _, href in entries:
            path, sep, frag = href.partition("#")
            full = posixpath.normpath(posixpath.join(master_dir, path))
            if full not in names:
                broken.append(href)
            elif sep:
                raw = zf.read(full)
                if f'id="{frag}"'.encode() not in raw and f"id='{frag}'".encode() not in raw:
                    broken.append(href)
    if broken:
        raise ValueError(f"生成后发现 {len(broken)} 个无效跳转，第一个：{broken[0]}")


def process(src, overwrite=False):
    src = Path(src).resolve()
    if src.suffix.lower() != ".epub":
        return None
    out = src.with_name(src.stem + "（已整理目录）.epub")
    if out.exists() and not overwrite:
        print(f"[跳过] 输出已存在：{out.name}")
        return None
    with zipfile.ZipFile(src) as zin:
        master_name = find_rootfile(zin)
        master_dir = posixpath.dirname(master_name)
        master_root = parse_opf(zin.read(master_name))
        volumes = collect_volumes(zin, master_name, master_root)
        master_items = manifest(master_root)
        ncx_href = next((href for iid, href in master_items.items() if href.lower().endswith(".ncx")), None)
        # EPUB 3 的导航文件不一定叫 nav.xhtml；例如角川电子书常用
        # Text/navigation-documents.xhtml。应优先按 manifest 的 properties="nav"
        # 精确查找，文件名仅作为旧 EPUB 的兼容回退。
        nav_href = next(
            (
                node.get("href")
                for node in master_root.iter()
                if local(node.tag) == "item"
                and "nav" in node.get("properties", "").split()
                and node.get("href")
            ),
            next(
                (href for _, href in master_items.items() if posixpath.basename(href).lower() == "nav.xhtml"),
                "nav.xhtml",
            ),
        )
        single_volume = len(volumes) == 1 and not volumes[0][0]
        toc_href = None if single_volume else "Text/collection-toc.xhtml"
        replacements = {
            posixpath.normpath(posixpath.join(master_dir, nav_href)): make_nav(volumes, nav_href),
        }
        # 同步给图片式章节标题补隐藏文字。原有标题图片和阅读版式不变，
        # 但 Sigil/Calibre 的“从标题生成目录”可以读取真实章名。
        for _, entries in volumes:
            for label, href in entries:
                path = href.partition("#")[0]
                full = posixpath.normpath(posixpath.join(master_dir, path))
                if full in zin.namelist() and full.lower().endswith((".xhtml", ".html")):
                    patched = patch_image_heading(zin.read(full), label)
                    patched = patch_fragment_heading(patched, label, href.partition("#")[2])
                    if patched != zin.read(full):
                        replacements[full] = patched
        # 纯 EPUB 3 可能完全没有 toc.ncx；这种情况只重建 nav，不能臆造并
        # 读取一个不存在的 toc.ncx。EPUB 2/混合格式仍同步更新 NCX。
        if ncx_href:
            ncx_name = posixpath.normpath(posixpath.join(master_dir, ncx_href))
            replacements[ncx_name] = make_ncx(zin.read(ncx_name), volumes)
        # Sigil 对错误命名空间较宽容，但 Calibre 会忽略 XHTML 命名空间下
        # 的 manifest/item，继而认为 spine 为空。每次输出都规范化 OPF。
        normalize_opf_namespaces(master_root)
        if not single_volume:
            patch_opf(master_root, toc_href)
        elif "collection-toc" in master_items:
            remove_generated_visible_toc(master_root)
        replacements[master_name] = ET.tostring(master_root, encoding="utf-8", xml_declaration=True)
        toc_name = posixpath.normpath(posixpath.join(master_dir, toc_href)) if toc_href else None
        temp = out.with_suffix(out.suffix + ".tmp")
        with zipfile.ZipFile(temp, "w") as zout:
            if "mimetype" in zin.namelist():
                zout.writestr(zin.getinfo("mimetype"), zin.read("mimetype"), compress_type=zipfile.ZIP_STORED)
            for info in zin.infolist():
                if info.filename == "mimetype" or (toc_name and info.filename == toc_name):
                    continue
                if single_volume and info.filename == posixpath.normpath(posixpath.join(master_dir, "Text/collection-toc.xhtml")):
                    continue
                zout.writestr(info, replacements.get(info.filename, zin.read(info.filename)))
            if toc_name:
                zout.writestr(toc_name, make_visible_toc(volumes, toc_href))
    with zipfile.ZipFile(temp) as check:
        if check.testzip() is not None:
            raise ValueError("ZIP 完整性检查失败")
        validate(check, master_dir, volumes)
        validate_opf_spine(check, master_name)
    os.replace(temp, out)
    total = sum(len(x) for _, x in volumes)
    print(f"[完成] {src.name}")
    if len(volumes) == 1 and not volumes[0][0]:
        print(f"       单卷，{total} 个目录条目")
    else:
        print(f"       {len(volumes)} 卷，{total} 个目录条目")
    print(f"       输出：{out}")
    return out


def inputs_from(path):
    p = Path(path)
    if p.is_dir():
        return sorted(p.rglob("*.epub"))
    return [p]


def main():
    ap = argparse.ArgumentParser(description="修复多卷 EPUB 合集丢失的总目录")
    ap.add_argument("paths", nargs="*", help="EPUB 文件或文件夹")
    ap.add_argument("--overwrite", action="store_true", help="覆盖已有的（已整理目录）输出")
    args = ap.parse_args()
    paths = args.paths
    if not paths:
        value = input("请拖入 EPUB 文件或文件夹，然后按回车：").strip().strip('"')
        paths = [value] if value else []
    files = []
    for p in paths:
        files.extend(inputs_from(p.strip('"')))
    ok = fail = 0
    for f in files:
        try:
            if process(f, args.overwrite):
                ok += 1
        except Exception as e:
            fail += 1
            print(f"[失败] {f}: {e}")
    print(f"\n处理结束：成功 {ok}，失败 {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
