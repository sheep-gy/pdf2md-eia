# -*- coding: utf-8 -*-
"""用 PDF 线条网格重建表格，修正 MinerU 在「多行单元格 / 合并表头」上的文字顺序错误。

思路：
  1. MinerU 负责找出表格区域（bbox 很准，比 pdfplumber 的全页 find_tables 可靠）；
  2. 在 bbox 内读取真实线条，按线条交点建网格；
  3. 某条内部线缺失 => 相邻单元格合并，据此生成 rowspan/colspan；
  4. 单元格文字用 PyMuPDF 的 clip 取，上下标不会被打散；
  5. 与原始文字层做字符召回校验，低于阈值则放弃重建，回退 MinerU 原表。
"""
import re

import pymupdf

MIN_LINES = 2
RECALL_MIN = 0.86


def _cluster(vals, tol=2.0):
    vals = sorted(vals)
    out = []
    for v in vals:
        if not out or v - out[-1][-1] > tol:
            out.append([v])
        else:
            out[-1].append(v)
    return [sum(g) / len(g) for g in out]


def _segments(pl_page):
    vs, hs = [], []
    for o in list(pl_page.lines) + list(pl_page.rects):
        x0, y0, x1, y1 = o["x0"], o["top"], o["x1"], o["bottom"]
        w, h = abs(x1 - x0), abs(y1 - y0)
        if w <= 1.6 and h >= 2:
            vs.append((x0, min(y0, y1), max(y0, y1)))
        if h <= 1.6 and w >= 2:
            hs.append((y0, min(x0, x1), max(x0, x1)))
    return vs, hs


def _covered(segs, pos, lo, hi, tol=2.5, frac=0.6):
    need = (hi - lo) * frac
    for p, a, b in segs:
        if abs(p - pos) <= tol and min(b, hi) - max(a, lo) >= need:
            return True
    return False


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_keep(s):
    """转义文本，但保留我们生成的 sup/sub 标签。"""
    parts = re.split(r"(</?su[bp]>)", s)
    return "".join(p if p in ("<sup>", "</sup>", "<sub>", "</sub>") else _esc(p)
                   for p in parts)


def _cell_text(page, bbox):
    from .text_extract import extract
    x0, y0, x1, y1 = bbox
    if x1 - x0 < 1 or y1 - y0 < 1:
        return ""
    rect = pymupdf.Rect(x0 + 0.8, y0 + 0.8, x1 - 0.8, y1 - 0.8)
    t = extract(page, rect, keep_lines=True)
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    # 竖排窄列被拆成一行一两个字：拼回一条，省 token 也更像原意
    if len(lines) >= 3 and max(len(x) for x in lines) <= 2 and sum(map(len, lines)) >= 4:
        return _esc_keep("".join(lines))
    return "<br>".join(_esc_keep(ln) for ln in lines)


def _norm_chars(s):
    s = re.sub(r"<[^>]+>", "", s)
    return "".join(ch for ch in s if ch.isalnum())


def _recall(page, bbox, html):
    """重建文字对原文字层的召回（只比字母数字汉字，忽略空格标点）。"""
    x0, y0, x1, y1 = bbox
    raw = page.get_text("text", clip=pymupdf.Rect(x0, y0, x1, y1), sort=True) or ""
    a = _norm_chars(raw)
    b = _norm_chars(html)
    if not a:
        return 0.0
    from collections import Counter
    ca, cb = Counter(a), Counter(b)
    hit = sum(min(ca[k], cb[k]) for k in ca)
    return hit / len(a)


def rebuild(fitz_page, pl_page, bbox_norm, page_w, page_h):
    """bbox_norm: MinerU 的 0~1 归一化 bbox。返回 HTML 或 None。"""
    x0, y0 = bbox_norm[0] * page_w, bbox_norm[1] * page_h
    x1, y1 = bbox_norm[2] * page_w, bbox_norm[3] * page_h
    vs, hs = _segments(pl_page)
    # 只保留完全落在表格区域内的线，排除页框 / 相邻表格
    vs = [v for v in vs if x0 - 4 <= v[0] <= x1 + 4
          and v[1] >= y0 - 8 and v[2] <= y1 + 8]
    hs = [h for h in hs if y0 - 4 <= h[0] <= y1 + 4
          and h[1] >= x0 - 8 and h[2] <= x1 + 8]
    if len(vs) < MIN_LINES or len(hs) < MIN_LINES:
        return None
    xs = _cluster([v[0] for v in vs]
                  + [min(h[1] for h in hs), max(h[2] for h in hs)])
    ys = _cluster([h[0] for h in hs]
                  + [min(v[1] for v in vs), max(v[2] for v in vs)])
    nr, nc = len(ys) - 1, len(xs) - 1
    if nr <= 0 or nc <= 0 or nr > 300 or nc > 60:
        return None
    parent = list(range(nr * nc))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for r in range(nr):
        for c in range(nc):
            i = r * nc + c
            if c + 1 < nc and not _covered(vs, xs[c + 1], ys[r], ys[r + 1]):
                union(i, i + 1)
            if r + 1 < nr and not _covered(hs, ys[r + 1], xs[c], xs[c + 1]):
                union(i, i + nc)
    groups = {}
    for r in range(nr):
        for c in range(nc):
            groups.setdefault(find(r * nc + c), []).append((r, c))
    placed = {}
    for cells in groups.values():
        rs = [c[0] for c in cells]
        cs = [c[1] for c in cells]
        r0, r1, c0, c1 = min(rs), max(rs), min(cs), max(cs)
        bx = (xs[c0], ys[r0], xs[c1 + 1], ys[r1 + 1])
        placed[(r0, c0)] = (r1 - r0 + 1, c1 - c0 + 1, _cell_text(fitz_page, bx))
    out = ["<table>"]
    for r in range(nr):
        row = ["<tr>"]
        for c in range(nc):
            if (r, c) in placed:
                rsp, csp, txt = placed[(r, c)]
                attr = f' rowspan="{rsp}"' if rsp > 1 else ""
                attr += f' colspan="{csp}"' if csp > 1 else ""
                row.append(f"<td{attr}>{txt}</td>")
            else:
                covered = any(r0 <= r < r0 + rsp and c0 <= c < c0 + csp
                              for (r0, c0), (rsp, csp, _) in placed.items())
                if not covered:
                    row.append("<td></td>")
        row.append("</tr>")
        line = "".join(row)
        if line != "<tr></tr>":
            out.append(line)
    out.append("</table>")
    html = "".join(out)
    if _recall(fitz_page, (x0, y0, x1, y1), html) < RECALL_MIN:
        return None
    return html
