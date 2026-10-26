# -*- coding: utf-8 -*-
"""从 PDF 文字层按区块 bbox 提取文本，保留上下标、复选框等语义。

- 行内按字号/基线判上下标（只在明确缩小时才包 <sup>/<sub>）
- 单字中文的伪上下标直接还原（PDF 字体的基线抖动很常见）
- Wingdings / 符号字体的私用区码位映射成 ☑ / □ 等
"""
import re
import statistics

import pymupdf

# Wingdings2 等符号字体的私用区码位（实测 HJ 报告常用 0xF052 表示勾选框）
PUA = {
    chr(0xF052): "☑",
    chr(0xF053): "☒",
    chr(0xF0FE): "☑",
    chr(0xF0A8): "☒",
    chr(0xF0FC): "✔",
    chr(0xF06F): "□",
    chr(0xF0A1): "□",
}
CJK_RE = re.compile(r"[一-鿿]")
WS_RE = re.compile(r"[ \t 　]+")


def _font_map(font):
    f = (font or "").lower()
    if "wingding" in f or "webding" in f or "symbol" in f:
        return PUA
    return {}


def _join_spans(spans):
    """spans: [(text, kind, size)] kind in {'n','sup','sub'}"""
    out = []
    for text, kind, _ in spans:
        if not text:
            continue
        if kind == "n":
            out.append(text)
        elif kind == "sup":
            out.append(f"<sup>{text}</sup>")
        else:
            out.append(f"<sub>{text}</sub>")
    s = "".join(out)
    # 中文单字被误判成上下标：还原
    s = re.sub(r"<(sup|sub)>([一-鿿]{1,2})</\1>", r"\2", s)
    s = s.replace("</sup><sup>", "").replace("</sub><sub>", "")
    return s


def extract(fitz_page, rect, keep_lines=True):
    """按 bbox 提取文本。rect 为 pymupdf.Rect 或 (x0,y0,x1,y1)。"""
    if not isinstance(rect, pymupdf.Rect):
        rect = pymupdf.Rect(*rect)
    d = fitz_page.get_text("rawdict", clip=rect, sort=False)
    lines = []
    for b in d.get("blocks", []):
        for ln in b.get("lines", []):
            spans = []
            for sp in ln.get("spans", []):
                if not sp.get("chars"):
                    continue
                txt = "".join(c["c"] for c in sp["chars"])
                fmap = _font_map(sp.get("font", ""))
                if fmap:
                    txt = "".join(fmap.get(c, c) for c in txt)
                # 去掉私用区残留（已映射的符号保留）
                txt = "".join(c for c in txt
                              if not (0xE000 <= ord(c) <= 0xF8FF) or c in "☑☒□✔")
                if not txt:
                    continue
                size = float(sp.get("size") or 0)
                origin = float(sp.get("origin", (0, 0))[1])
                spans.append((txt, size, origin))
            if not spans:
                continue
            sizes = [s for _, s, _ in spans if s > 0]
            base = statistics.median(sizes) if sizes else 0
            base_y = statistics.median([o for _, _, o in spans]) if spans else 0
            out = []
            for txt, size, oy in spans:
                kind = "n"
                if base and size and size < base * 0.78:
                    if oy < base_y - 0.8:
                        kind = "sup"
                    elif oy > base_y + 0.8:
                        kind = "sub"
                out.append((txt, kind, size))
            line = _join_spans(out).strip()
            if line:
                lines.append(line)
    lines = _dedup_nested(lines)
    if keep_lines:
        return "\n".join(lines)
    return "".join(lines)


def _dedup_nested(lines):
    """同一行被重复抽取时去重（保留最长的一条）。"""
    out = []
    for ln in lines:
        if out and (ln == out[-1] or (ln in out[-1] and len(ln) < len(out[-1]) * 0.6)):
            continue
        out.append(ln)
    return out


def tighten(s):
    """压缩中文之间的空格，去掉 Markdown 转义反斜杠，统一中文标点。"""
    s = s.replace("\\*", "*").replace("\\_", "_").replace("\\#", "#")
    s = WS_RE.sub(" ", s)
    s = re.sub(r"(?<=[一-鿿]) +(?=[一-鿿])", "", s)
    s = re.sub(r"(?<=[一-鿿]) +(?=[，。；：、！？）】”’])", "", s)
    s = re.sub(r"(?<=[（【“‘、，。；：]) +(?=[一-鿿])", "", s)
    s = re.sub(r"(?<=[一-鿿]) +(?=[0-9A-Za-z])", "", s)
    s = re.sub(r"(?<=[0-9A-Za-z%）]) +(?=[一-鿿])", "", s)
    # 半角标点按上下文改回中文
    for a, b in ((",", "，"), (";", "；"), (":", "："), ("?", "？"), ("!", "！")):
        s = re.sub(r"(?<=[一-鿿])" + re.escape(a), b, s)
        s = re.sub(re.escape(a) + r"(?=[一-鿿])", b, s)
    # 含中文的括号整体转全角；编号 (3) 后面跟中文也转
    s = re.sub(r"\(([^()]*[一-鿿][^()]*)\)", lambda m: "（" + m.group(1) + "）", s)
    s = re.sub(r"\((\d+)\)(?=[一-鿿])", lambda m: "（" + m.group(1) + "）", s)
    return s.strip()


def text_layer_chars(fitz_page):
    try:
        return len((fitz_page.get_text("text") or "").strip())
    except Exception:
        return 0
