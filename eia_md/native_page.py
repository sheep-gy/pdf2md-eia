# -*- coding: utf-8 -*-
"""原生文字页兜底渲染：MinerU 漏块时，用 PDF 线条/文字层自己排一遍。

只用于「文字层完整、但 MinerU 漏了表格或正文」的页，作为最后一道保险。
"""
import re

import pymupdf

from .table_rebuild import rebuild
from .text_extract import extract, tighten

HEAD_RE = re.compile(
    r"^(?:[（(][一二三四五六七八九十百]+[）)]"
    r"|\d+(?:\.\d+)*[.、]"
    r"|[一二三四五六七八九十]+[、.])")
PAGE_RE = re.compile(r"^[—\-–\s]*\d+[—\-–\s]*$")


def _lines(pl_page):
    hs, vs = [], []
    for o in list(pl_page.lines) + list(pl_page.rects):
        x0, y0, x1, y1 = o["x0"], o["top"], o["x1"], o["bottom"]
        w, h = abs(x1 - x0), abs(y1 - y0)
        if h <= 1.6 and w >= 8:
            hs.append((y0, min(x0, x1), max(x0, x1)))
        if w <= 1.6 and h >= 8:
            vs.append((x0, min(y0, y1), max(y0, y1)))
    return hs, vs


def table_bands(pl_page):
    hs, vs = _lines(pl_page)
    hs.sort()
    if not hs:
        return []
    groups, cur = [], [hs[0]]
    for h in hs[1:]:
        if h[0] - cur[-1][0] <= 40:
            cur.append(h)
        else:
            groups.append(cur)
            cur = [h]
    groups.append(cur)
    bands = []
    for g in groups:
        if len(g) < 2:
            continue
        y0, y1 = min(h[0] for h in g), max(h[0] for h in g)
        x0, x1 = min(h[1] for h in g), max(h[2] for h in g)
        if y1 - y0 < 18 or x1 - x0 < 60:
            continue
        cnt = sum(1 for x, a, b in vs
                  if x0 - 4 <= x <= x1 + 4 and min(b, y1) - max(a, y0) >= 8)
        if cnt >= 2:
            bands.append((x0, y0, x1, y1))
    out = []
    for b in bands:
        if not any(_inside(b, o) for o in out):
            out.append(b)
    return out


def _inside(a, b):
    return (a[0] >= b[0] - 2 and a[2] <= b[2] + 2
            and a[1] >= b[1] - 2 and a[3] <= b[3] + 2)


def _overlap(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0) / max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))


def _norm(s):
    return "".join(c for c in s if c.isalnum() or "一" <= c <= "鿿")


def _band_residuals(fitz_page, band, html):
    """表框内没被重建表格吃进去的文字（表题、表下注释）。"""
    from collections import Counter
    have = Counter(_norm(re.sub(r"<[^>]+>", "", html)))
    out = []
    for blk in fitz_page.get_text("dict", sort=True).get("blocks", []):
        for ln in blk.get("lines", []):
            lb = ln.get("bbox")
            if not lb or not (band[0] - 3 <= lb[0] and lb[2] <= band[2] + 3
                              and band[1] - 3 <= lb[1] and lb[3] <= band[3] + 3):
                continue
            txt = extract(fitz_page, pymupdf.Rect(lb), keep_lines=True)
            txt = tighten("".join(txt.splitlines()))
            n = _norm(txt)
            if not n:
                continue
            need = Counter(n)
            hit = sum(min(need[k], have[k]) for k in need)
            if hit / len(n) < 0.85:
                out.append((lb[1], txt))
    return out


def render_native_page(fitz_page, pl_page):
    W, H = pl_page.width, pl_page.height
    bands = table_bands(pl_page)
    parts = []  # (y, kind, content)
    for b in bands:
        bx = (b[0] / W, b[1] / H, b[2] / W, b[3] / H)
        html = rebuild(fitz_page, pl_page, bx, W, H)
        if html:
            parts.append((b[1], "table", html))

    blocks = []
    for blk in fitz_page.get_text("dict", sort=True).get("blocks", []):
        for ln in blk.get("lines", []):
            lb = ln.get("bbox")
            if not lb or any(_overlap(lb, b) > 0.5 for b in bands):
                continue
            txt = extract(fitz_page, pymupdf.Rect(lb), keep_lines=True)
            txt = tighten("".join(txt.splitlines()))
            if not txt or PAGE_RE.match(txt):
                continue
            sizes = [sp.get("size", 0) for sp in ln.get("spans", [])]
            blocks.append((lb[1], lb[3], lb[0], max(sizes) if sizes else 0, txt))
    blocks.sort()
    long_sizes = [b[3] for b in blocks if len(b[4]) > 20]
    base = sorted(long_sizes)[len(long_sizes) // 2] if long_sizes else 12.0

    para, prev_bottom, prev_size = "", None, 0

    def flush():
        nonlocal para
        if para.strip():
            parts.append((prev_bottom or 0, "text", para.strip()))
        para = ""

    for top, bot, x0, size, txt in blocks:
        is_head = bool(HEAD_RE.match(txt)) and len(txt) < 50
        if is_head:
            flush()
            parts.append((top, "head", txt))
            prev_bottom = None
            continue
        if para and prev_bottom is not None and (top - prev_bottom) <= max(6, base * 1.7) \
                and abs(size - prev_size) <= 1.5:
            para += txt
        else:
            flush()
            para = txt
        prev_bottom, prev_size = bot, size
    flush()

    parts.sort(key=lambda x: x[0])
    out = []
    for _, kind, txt in parts:
        out.append(("## " + txt) if kind == "head" else txt)
    return "\n\n".join(out)
