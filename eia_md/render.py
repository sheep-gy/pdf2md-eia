# -*- coding: utf-8 -*-
"""把 MinerU 的 middle_json 重新渲染成「给 AI 读的 Markdown」。

相比 MinerU 自带 md：
  - 加页锚点，方便回查原页
  - 原生页的表格用线条网格重建，修正多行单元格/合并表头的文字顺序
  - 扫描页保留 MinerU 的 OCR/表格结果
  - 丢弃图片二进制，只保留图题；压缩中文空格；去掉页眉页脚与重复水印
"""
import re

import pymupdf

from .table_rebuild import rebuild, _esc_keep
from .clean_table import split_container_table
from .text_extract import tighten

SKIP = {"page_number", "header", "footer", "aside_text"}


def _parts_text(parts):
    out = []
    for p in parts or []:
        if not isinstance(p, dict):
            if isinstance(p, str):
                out.append(p)
            continue
        t = p.get("type")
        c = p.get("content")
        if t == "equation_inline":
            if isinstance(c, str) and c.strip():
                out.append(f"${c.strip()}$")
            continue
        if t in ("image", "image_body"):
            continue
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, list):
            out.append(_parts_text(c))
    return "".join(out)


def _bbox_in(a, b):
    """a 的面积有多大比例落在 b 内。"""
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    area = max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))
    return inter / area


def _has_text_inside(blocks, table_bbox, self_block):
    for b in blocks:
        if b is self_block:
            continue
        if b.get("type") in ("text", "paragraph_title", "equation"):
            bb = b.get("bbox")
            if bb and _bbox_in(bb, table_bbox) > 0.5:
                return True
    return False


def _table_html(block, blocks, fitz_page, pl_page, page_w, page_h, native):
    body = None
    caption = ""
    for c in block.get("content", []):
        if c.get("type") == "table_body":
            body = c
        elif c.get("type") == "table_caption":
            caption = _parts_text(c.get("content"))
    if body is None:
        return caption, ""
    html = body.get("content") or ""
    if native and not _has_text_inside(blocks, body["bbox"], block):
        new = rebuild(fitz_page, pl_page, body["bbox"], page_w, page_h)
        if new:
            # 结构明显缩水时回退（防止嵌套表被拉平）
            if _cell_count(new) >= _cell_count(html) * 0.6:
                # 嵌套表（单元格里又出现“表 x-y”标题）且占大半页 -> 回退
                h = body["bbox"][3] - body["bbox"][1]
                if not (h > 0.55 and re.search(r"表\s*\d+\s*[-－]\s*\d+", new)):
                    html = new
    return caption, html


def _cell_count(html):
    return html.count("<td") + html.count("<th")


def _tighten_html(html):
    parts = re.split(r"(<[^>]+>)", html)
    return "".join(p if p.startswith("<") else tighten(p) for p in parts)


def _compact_table(html, allow_pipe=True):
    html = _tighten_html(html)
    html = re.sub(r"<tbody>|</tbody>", "", html)
    html = re.sub(r"<tr></tr>", "", html)
    html = re.sub(r">\s+<", "><", html)
    html = html.strip()
    return _maybe_pipe(html) if allow_pipe else html


def _maybe_pipe(html):
    """无合并、无换行的简单表转管道表，明显省 token。"""
    if "rowspan" in html or "colspan" in html or "<br" in html:
        return html
    rows = re.findall(r"<tr>(.*?)</tr>", html, re.S)
    if len(rows) < 2:
        return html
    table = []
    for r in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)
        if not cells:
            return html
        table.append([c.strip().replace("|", r"\|") for c in cells])
    n = len(table[0])
    if n < 2 or any(len(r) != n for r in table):
        return html
    out = ["| " + " | ".join(table[0]) + " |",
           "| " + " | ".join(["---"] * n) + " |"]
    for r in table[1:]:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def render_doc(middle, pdf_path, native_pages=None, page_marks=True):
    """middle: MinerU middle_json dict；native_pages: set(page_idx) 为文字层页。"""
    doc = pymupdf.open(pdf_path)
    import pdfplumber
    out = []
    repeat = {}
    last_caption = ""
    page_blocks = []
    with pdfplumber.open(pdf_path) as pl:
        for p in middle.get("pages", []):
            idx = p["page_idx"]
            fitz_page = doc[idx]
            pl_page = pl.pages[idx]
            W, H = pl_page.width, pl_page.height
            blocks = p.get("blocks", [])
            page_out = []
            native = (native_pages is None or idx in native_pages)
            for b in blocks:
                t = b.get("type")
                if t in SKIP:
                    continue
                if t in ("paragraph_title", "title"):
                    txt = tighten(_parts_text(b.get("content")))
                    if not txt:
                        continue
                    level = b.get("level") or 2
                    page_out.append("#" * min(max(int(level), 1), 6) + " " + txt)
                elif t == "text":
                    txt = tighten(_parts_text(b.get("content")))
                    if txt:
                        page_out.append(txt)
                elif t == "equation":
                    c = (b.get("content") or "").strip()
                    if c:
                        page_out.append("$$\n" + c + "\n$$")
                elif t == "table":
                    cap, html = _table_html(b, blocks, fitz_page, pl_page, W, H, native)
                    if cap:
                        cap = tighten(cap)
                        last_caption = cap
                        page_out.append(cap)
                    elif b.get("bbox") and b["bbox"][1] < 0.12:
                        mark = f"<!-- 续表：{last_caption} -->" if last_caption else "<!-- 续表 -->"
                        page_out.append(mark)
                    if html:
                        for part in split_container_table(_compact_table(html, bool(cap))):
                            page_out.append(part if part.startswith("<table") else tighten(part))
                elif t in ("image", "figure"):
                    txt = tighten(_parts_text(b.get("content")))
                    if txt:
                        page_out.append(txt)
                elif t == "list":
                    txt = tighten(_parts_text(b.get("content")))
                    if txt:
                        page_out.append(txt)
                else:
                    txt = tighten(_parts_text(b.get("content")))
                    if txt and len(txt) > 1 and not txt.isdigit():
                        page_out.append(txt)
            body = "\n\n".join(x for x in page_out if x.strip())
            if page_marks:
                out.append(f"<!-- p{idx + 1} -->")
            if body:
                out.append(body)
            page_blocks.append(body)
    doc.close()
    text = "\n\n".join(out)
    text = _drop_repeats(text, page_blocks)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def _drop_repeats(text, page_blocks):
    """删除在大量页面重复出现的短行（页眉、水印、页脚）。"""
    from collections import Counter
    cnt = Counter()
    for body in page_blocks:
        seen = set()
        for ln in body.splitlines():
            s = ln.strip()
            if 2 <= len(s) <= 40 and not s.startswith(("#", "<!--", "<")):
                seen.add(s)
        cnt.update(seen)
    n = max(3, len(page_blocks))
    bad = {s for s, c in cnt.items() if c >= max(3, int(n * 0.25))}
    if not bad:
        return text
    lines = []
    for ln in text.splitlines():
        if ln.strip() in bad:
            continue
        lines.append(ln)
    return "\n".join(lines)
