# -*- coding: utf-8 -*-
"""处理「整页被当成一张表」的情况：把误吞进单元格的正文拆出来。"""
import re


def split_container_table(html):
    """返回若干块：表格片段 / 正文 / 表题。

    触发条件：某一行只有一个跨多列的 td，文字很长，且里面出现“表 x-y”表题。
    典型场景：页面上有整页外框，MinerU 把外框内的正文也当成表格。
    """
    if "rowspan" in html:
        return [html]
    rows = re.findall(r"<tr>.*?</tr>", html, re.S)
    for i, row in enumerate(rows):
        cells = re.findall(r"<td([^>]*)>(.*?)</td>", row, re.S)
        if len(cells) != 1:
            continue
        attrs, txt = cells[0]
        m = re.search(r'colspan=["\']?(\d+)', attrs)
        if not m or int(m.group(1)) < 2:
            continue
        plain = re.sub(r"<br\s*/?>", "\n", txt)
        plain = re.sub(r"<[^>]+>", "", plain).strip()
        if len(plain) < 120:
            continue
        caps = list(re.finditer(r"表\s*\d+\s*[-－]\s*\d+", plain))
        if not caps:
            continue
        cut = caps[-1].start()
        body, cap = plain[:cut].strip(), plain[cut:].strip()
        if len(body) < 40:
            continue
        body = re.sub(r"(?=[（(]\d+[）)])", "\n\n", body).strip()
        before = "<table>" + "".join(rows[:i]) + "</table>" if i else ""
        after = "<table>" + "".join(rows[i + 1:]) + "</table>" if i + 1 < len(rows) else ""
        return [x for x in (before, body, cap, after) if x]
    return [html]
