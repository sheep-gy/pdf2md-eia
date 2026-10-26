# -*- coding: utf-8 -*-
"""把 Markdown 里的 HTML 合并表展开成逻辑网格。

为什么需要：md 里的复杂表用 rowspan/colspan 保存合并关系。
不会解析 HTML 的模型逐行读 <td> 会把列读错。这个脚本把每个合并单元格
展开到它覆盖的每一行/列，输出 TSV 或 JSON，任何模型都能直接读。

用法：
  python expand_tables.py 报告.md --list
  python expand_tables.py 报告.md --table 5
  python expand_tables.py 报告.md --table 5 --format json
  python expand_tables.py 报告.md --all --out grids/
"""
import argparse
import html
import json
import os
import re
import sys
from html.parser import HTMLParser


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.cell = None
        self.in_table = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self.in_table += 1
        elif tag == "tr" and self.in_table:
            self.row = []
        elif tag in ("td", "th") and self.in_table:
            try:
                rs = int(a.get("rowspan", 1))
            except ValueError:
                rs = 1
            try:
                cs = int(a.get("colspan", 1))
            except ValueError:
                cs = 1
            self.cell = {"t": "", "rs": max(1, rs), "cs": max(1, cs)}
        elif tag == "br" and self.cell is not None:
            self.cell["t"] += " / "

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append(self.cell)
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        elif tag == "table" and self.in_table:
            self.in_table -= 1

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["t"] += data


def clean(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    s = html.unescape(s)
    s = s.replace(" ", " ").replace("\t", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", s).strip()


def expand(rows, repeat_merged=True):
    occupied = set()
    grid = {}
    maxc = 0
    for r, row in enumerate(rows):
        c = 0
        for cell in row:
            while (r, c) in occupied:
                c += 1
            for dr in range(cell["rs"]):
                for dc in range(cell["cs"]):
                    occupied.add((r + dr, c + dc))
                    if repeat_merged or (dr == 0 and dc == 0):
                        grid[(r + dr, c + dc)] = clean(cell["t"])
            maxc = max(maxc, c + cell["cs"])
            c += cell["cs"]
    out = []
    for r in range(len(rows)):
        out.append([grid.get((r, c), "") for c in range(maxc)])
    return out


def find_tables(md):
    out = []
    for m in re.finditer(r"<table\b[^>]*>.*?</table>", md, re.S | re.I):
        before = md[:m.start()].rstrip().splitlines()
        caption = ""
        for ln in reversed(before[-4:]):
            s = ln.strip()
            if not s or s.startswith("<!--"):
                continue
            caption = re.sub(r"^#+\s*", "", s)
            break
        p = TableParser()
        p.feed(m.group(0))
        if not p.rows:
            continue
        out.append({"html": m.group(0), "caption": caption[:120],
                    "rows": expand(p.rows), "raw_rows": len(p.rows)})
    return out


def to_tsv(rows):
    return "\n".join("\t".join(c.replace("\t", " ") for c in r) for r in rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("md")
    ap.add_argument("--table", type=int, default=0, help="第几张 HTML 表（1 起）")
    ap.add_argument("--all", action="store_true", help="导出全部 HTML 表")
    ap.add_argument("--out", default="", help="--all 的输出目录")
    ap.add_argument("--format", default="tsv", choices=["tsv", "json"])
    ap.add_argument("--list", action="store_true", help="只列出表名和尺寸")
    ap.add_argument("--blank-merged", action="store_true",
                    help="合并单元格只在左上角保留值，其余留空")
    args = ap.parse_args()

    md = open(args.md, encoding="utf-8").read()
    tables = find_tables(md)
    if not tables:
        print("没有找到 HTML 表（简单表可能是管道表，不需要展开）。")
        return

    if args.blank_merged:
        for t in tables:
            # 重新展开，保留左上角
            p = TableParser()
            p.feed(t["html"])
            t["rows"] = expand(p.rows, repeat_merged=False)

    if args.list or (not args.table and not args.all):
        for i, t in enumerate(tables, 1):
            nr = len(t["rows"])
            nc = len(t["rows"][0]) if t["rows"] else 0
            print(f"{i:3d}  {nr:3d}行 x {nc:2d}列  {t['caption']}")
        print(f"\n共 {len(tables)} 张 HTML 表。用 --table N 或 --all 导出。")
        return

    if args.all:
        out = args.out or "."
        os.makedirs(out, exist_ok=True)
        index = []
        for i, t in enumerate(tables, 1):
            name = f"table_{i:03d}.tsv"
            path = os.path.join(out, name)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(to_tsv(t["rows"]))
            index.append({"index": i, "caption": t["caption"],
                          "rows": len(t["rows"]),
                          "cols": len(t["rows"][0]) if t["rows"] else 0,
                          "file": name})
        with open(os.path.join(out, "index.json"), "w", encoding="utf-8") as fh:
            json.dump(index, fh, ensure_ascii=False, indent=2)
        print(f"已导出 {len(tables)} 张表到 {out}")
        print(json.dumps(index, ensure_ascii=False, indent=2))
        return

    i = args.table
    if i < 1 or i > len(tables):
        print(f"表号超出范围：1..{len(tables)}", file=sys.stderr)
        sys.exit(2)
    t = tables[i - 1]
    print(f"# table {i}: {t['caption']}", file=sys.stderr)
    if args.format == "json":
        print(json.dumps({"index": i, "caption": t["caption"], "rows": t["rows"]},
                         ensure_ascii=False, indent=2))
    else:
        print(to_tsv(t["rows"]))


if __name__ == "__main__":
    main()
