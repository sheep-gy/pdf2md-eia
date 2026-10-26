# -*- coding: utf-8 -*-
"""PDF → AI 可读 Markdown（MinerU basic + 线条网格表格重建 + 清洗 + QA）。

用法：
  python convert.py "某文件.pdf" -o "输出目录"
  python convert.py "某目录" -o "输出目录" --pattern "报告书"
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import pymupdf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eia_md.render import render_doc
from eia_md.text_extract import tighten
from eia_md.native_page import render_native_page
from eia_md import gpu
import pdfplumber

MINERU_ENV = {
    "MINERU_HOME": os.environ.get("MINERU_HOME", "D:/mineru_home"),
    "MINERU_MODEL_SOURCE": os.environ.get("MINERU_MODEL_SOURCE", "local"),
    "PYTHONIOENCODING": "utf-8",
}
PY = os.environ.get("MINERU_PY", r"D:/mineru_env/Scripts/python.exe")
MINERU = os.environ.get("MINERU_BIN", r"D:/mineru_env/Scripts/mineru-kit.exe")
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def safe_utf8(s):
    """修复 PDF 解析出的代理字符：成对的还原，孤立的替换为 �。

    某些 PDF 用 UTF-16 代理对表示数学符号，直接 write_text 会抛
    UnicodeEncodeError: surrogates not allowed。
    """
    if not isinstance(s, str):
        return s
    b = s.encode("utf-16-le", "surrogatepass")
    return b.decode("utf-16-le", "replace")


def native_page_set(pdf_path):
    """返回有文字层的页号集合（0 基）与页级字符数。"""
    out, chars = set(), []
    doc = pymupdf.open(pdf_path)
    for i, page in enumerate(doc):
        try:
            n = len((page.get_text("text") or "").strip())
        except Exception:
            n = 0
        chars.append(n)
        if n >= 50:
            out.add(i)
    doc.close()
    return out, chars


def run_mineru(pdf_path, out_json, pages="all", tier="basic", ocr_mode="auto",
               device="auto", on_event=None):
    if on_event:
        on_event("phase", {"name": "mineru", "tier": tier, "ocr_mode": ocr_mode})
    cmd = [MINERU, "parse", str(pdf_path), "-o", str(out_json),
           "--tier", tier, "--pages", pages,
           "--ocr-mode", ocr_mode, "--format", "middle_json"]
    env = os.environ.copy()
    env.update(MINERU_ENV)
    env, use_gpu = gpu.apply_env(env, device)
    t0 = time.time()
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       env=env, timeout=6 * 3600, creationflags=CREATE_NO_WINDOW)
    dt = time.time() - t0
    if p.returncode != 0 or not Path(out_json).exists():
        tail = p.stdout.decode("utf-8", "replace")[-1200:]
        raise RuntimeError(f"mineru 失败 rc={p.returncode}: {tail}")
    if on_event:
        on_event("phase", {"name": "mineru_done", "seconds": round(dt, 1)})
    return dt


def norm_chars(s):
    return "".join(ch for ch in s if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def page_recall(pdf_page, md_text):
    """按字符种类算覆盖率，避免 PDF 文字层的重复字把召回拉低。"""
    raw = set(norm_chars(pdf_page.get_text("text") or ""))
    md = set(norm_chars(md_text))
    if not raw:
        return 1.0
    return len(raw & md) / len(raw)


def mid_page_text(mid_page):
    out = []
    for b in mid_page.get("blocks", []):
        c = b.get("content")
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, list):
            for x in c:
                if isinstance(x, dict):
                    cc = x.get("content")
                    if isinstance(cc, str):
                        out.append(cc)
                    elif isinstance(cc, list):
                        out.append(mid_page_text({"blocks": [{"content": cc}]}))
    return "".join(out)


def cover_of(mid_page, fitz_page):
    raw = set(norm_chars(fitz_page.get_text("text") or ""))
    if not raw:
        return 1.0
    got = set(norm_chars(re.sub(r"<[^>]+>", "", mid_page_text(mid_page))))
    return len(raw & got) / len(raw)


def repair_pages(pdf_path, mid, chars, out_dir, threshold=0.95, max_pages=80,
                 device="auto", tmp_dir=None, on_event=None):
    """basic 漏字的原生页，用 standard 重跑并替换 middle_json 对应页。"""
    doc = pymupdf.open(pdf_path)
    bad = []
    for p in mid.get("pages", []):
        i = p["page_idx"]
        if i < len(chars) and chars[i] >= 50:
            if cover_of(p, doc[i]) < threshold:
                bad.append(i)
    doc.close()
    total_bad = len(set(bad))
    bad = sorted(set(bad))[:max_pages]
    if on_event:
        on_event("phase", {"name": "repair_scan", "candidates": total_bad,
                           "will_repair": len(bad)})
    if not bad:
        return []
    # 连续页合并成区间，一次 standard 调用处理完，省掉反复加载模型
    segs, start, prev = [], bad[0], bad[0]
    for x in bad[1:]:
        if x == prev + 1:
            prev = x
        else:
            segs.append((start, prev))
            start = prev = x
    segs.append((start, prev))
    pages_arg = ",".join(f"{a + 1}-{b + 1}" if b > a else str(a + 1) for a, b in segs)
    by_idx = {p["page_idx"]: p for p in mid["pages"]}
    tmp = Path(tmp_dir or out_dir) / "~repair.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    cmd = [MINERU, "parse", str(pdf_path), "-o", str(tmp), "--tier", "standard",
           "--pages", pages_arg, "--ocr-mode", "txt", "--format", "middle_json"]
    env = os.environ.copy()
    env.update(MINERU_ENV)
    env, use_gpu = gpu.apply_env(env, device)
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       env=env, timeout=6 * 3600, creationflags=CREATE_NO_WINDOW)
    done = []
    if p.returncode == 0 and tmp.exists():
        rep = json.loads(tmp.read_text(encoding="utf-8"))
        for rp in rep.get("pages", []):
            by_idx[rp["page_idx"]] = rp
            done.append(rp["page_idx"])
        try:
            tmp.unlink()
        except OSError:
            pass
    mid["pages"] = [by_idx[i] for i in sorted(by_idx)]
    if on_event:
        on_event("phase", {"name": "repair_done", "pages": len(done)})
    return done


def native_overrides(pdf_path, mid, chars, threshold=0.95):
    """MinerU 补漏后仍缺字的页，用原生线条/文字层渲染兜底。"""
    doc = pymupdf.open(pdf_path)
    out = {}
    with pdfplumber.open(pdf_path) as pl:
        for p in mid.get("pages", []):
            i = p["page_idx"]
            if i >= len(chars) or chars[i] < 50:
                continue
            cur = cover_of(p, doc[i])
            if cur >= threshold:
                continue
            try:
                from eia_md.native_page import table_bands
                if len(table_bands(pl.pages[i])) > 2:
                    continue
                txt = render_native_page(doc[i], pl.pages[i])
            except Exception:
                continue
            raw = set(norm_chars(doc[i].get_text("text") or ""))
            got = set(norm_chars(txt))
            cov = len(raw & got) / max(1, len(raw))
            if cov > cur + 0.02:
                out[i] = txt
    doc.close()
    return out


def apply_overrides(md_text, overrides):
    if not overrides:
        return md_text
    parts = re.split(r"<!-- p(\d+) -->", md_text)
    # parts: [head, num, body, num, body, ...]
    res = [parts[0]]
    for i in range(1, len(parts), 2):
        n = int(parts[i])
        body = parts[i + 1]
        if (n - 1) in overrides:
            body = "\n\n" + overrides[n - 1] + "\n"
        res.append(f"<!-- p{n} -->")
        res.append(body)
    return "".join(res)


def recover_sentences(md_text, pdf_path, chars):
    """最后一道保险：整句级补录 MinerU 漏掉的正文句子。"""
    doc = pymupdf.open(pdf_path)
    parts = re.split(r"<!-- p(\d+) -->", md_text)
    pages = {int(parts[i]) - 1: parts[i + 1] for i in range(1, len(parts), 2)}
    added = []
    for i, body in pages.items():
        if i >= len(chars) or chars[i] < 50:
            continue
        have = set(norm_chars(body))
        if len(have) / max(1, len(set(norm_chars(doc[i].get_text("text") or "")))) >= 0.97:
            continue
        extra = []
        for ln in (doc[i].get_text("text") or "").splitlines():
            ln = ln.strip()
            if len(ln) < 12 or ln[-1] not in "。！？":
                continue
            n = norm_chars(ln)
            if not n or n in norm_chars(body):
                continue
            extra.append(ln)
        if extra:
            pages[i] = body + "\n\n<!-- 补录 -->\n\n" + "\n\n".join(extra[:6])
            added.append(i + 1)
    doc.close()
    if not added:
        return md_text, []
    res = [parts[0]]
    for i in range(1, len(parts), 2):
        n = int(parts[i])
        res.append(f"<!-- p{n} -->")
        res.append(pages.get(n - 1, parts[i + 1]))
    return "".join(res), added


def qa_doc(md_text, mid, pdf_path, chars):
    doc = pymupdf.open(pdf_path)
    # 按页锚点切分
    parts = re.split(r"<!-- p(\d+) -->", md_text)
    pages = {}
    for i in range(1, len(parts), 2):
        pages[int(parts[i]) - 1] = parts[i + 1]
    rec = []
    for i in sorted(pages):
        if i < len(doc) and chars[i] >= 50:
            rec.append(page_recall(doc[i], pages[i]))
    doc.close()
    n_tables_mid = sum(1 for p in mid.get("pages", []) for b in p.get("blocks", [])
                       if b.get("type") == "table")
    n_tables_md = md_text.count("<table") + len(re.findall(r"^\| *---", md_text, re.M))
    bad = 0
    for m in re.finditer(r"<td[^>]*>([^<]{1,30})</td>", md_text):
        s = m.group(1)
        if re.search(r"[\u4e00-\u9fff]\d{1,3}[\u4e00-\u9fff]", s):
            bad += 1
    pua = sum(1 for c in md_text if 0xE000 <= ord(c) <= 0xF8FF)
    return {
        "pages": len(chars),
        "parsed_pages": len(pages),
        "text_pages": sum(1 for x in chars if x >= 50),
        "chars": len(md_text),
        "tables_mid": n_tables_mid,
        "tables_md": n_tables_md,
        "min_page_recall": round(min(rec), 4) if rec else None,
        "md_pdf_chars_ratio": round(len(norm_chars(md_text)) / max(1, sum(chars)), 3),
        "avg_page_recall": round(sum(rec) / len(rec), 4) if rec else None,
        "pages_below_0.9": sum(1 for x in rec if x < 0.9),
        "suspicious_cells": bad,
        "pua_left": pua,
    }


def convert_one(pdf_path, out_dir, overwrite=False, keep_json=False, pages="all",
                device="auto", tier="basic", tmp_dir=None, on_event=None,
                out_stem=None):
    ev = on_event or (lambda *a, **k: None)
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tmp_dir) if tmp_dir else out_dir
    work_dir.mkdir(parents=True, exist_ok=True)
    stem = out_stem or pdf_path.stem
    md_path = out_dir / (stem + ".md")
    qa_path = out_dir / (stem + "_qa.json")
    if md_path.exists() and not overwrite:
        ev("skipped", {"path": str(md_path)})
        return {"path": str(md_path), "skipped": True}
    tmp = work_dir / ("~" + stem + ".json")
    t0 = time.time()
    ev("phase", {"name": "probe"})
    native, chars = native_page_set(pdf_path)
    ev("phase", {"name": "probe_done", "pages": len(chars),
                 "native_pages": len(native)})
    mode = "txt" if len(native) >= max(1, int(len(chars) * 0.9)) else "auto"
    dt = run_mineru(pdf_path, tmp, pages=pages, tier=tier, ocr_mode=mode,
                    device=device, on_event=ev)
    mid = json.loads(tmp.read_text(encoding="utf-8"))
    ev("phase", {"name": "repair"})
    repaired = repair_pages(pdf_path, mid, chars, out_dir, device=device,
                            tmp_dir=work_dir, on_event=ev)
    ev("phase", {"name": "render"})
    overrides = native_overrides(pdf_path, mid, chars)
    md = render_doc(mid, str(pdf_path), native_pages=native)
    md = apply_overrides(md, overrides)
    md, recovered = recover_sentences(md, str(pdf_path), chars)
    ev("phase", {"name": "write"})
    md_path.write_text(safe_utf8(md), encoding="utf-8")
    qa = qa_doc(md, mid, str(pdf_path), chars)
    qa.update({"source": str(pdf_path), "mineru_sec": round(dt, 1),
               "repaired_pages": repaired,
               "native_pages": sorted(k + 1 for k in overrides),
               "recovered_pages": recovered,
               "total_sec": round(time.time() - t0, 1)})
    qa_path.write_text(safe_utf8(json.dumps(qa, ensure_ascii=False, indent=2)), encoding="utf-8")
    if not keep_json:
        try:
            tmp.unlink()
        except OSError:
            pass
    ev("done", {"path": str(md_path), "qa": qa})
    return {"path": str(md_path), "qa": qa}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("-o", "--out", default="out")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--keep-json", action="store_true")
    ap.add_argument("--pattern", default="")
    ap.add_argument("--pages", default="all")
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    ap.add_argument("--tier", default="basic", choices=["basic", "standard", "advanced"])
    args = ap.parse_args()
    from office_convert import convert_any, SUPPORTED
    src = Path(args.input)
    files = []
    if src.is_file():
        files = [src]
    else:
        for p in sorted(src.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in SUPPORTED:
                continue
            if args.pattern and args.pattern not in str(p):
                continue
            files.append(p)
    print(f"共 {len(files)} 个文件", flush=True)
    ok = fail = 0
    for i, f in enumerate(files, 1):
        try:
            r = convert_any(f, args.out, overwrite=args.overwrite,
                            keep_json=args.keep_json, pages=args.pages,
                            device=args.device, tier=args.tier)
            print(f"[{i}/{len(files)}] {f.name} -> {r.get('path')}", flush=True)
            if r.get("qa"):
                print("   ", json.dumps(r["qa"], ensure_ascii=False), flush=True)
            ok += 1
        except Exception as e:
            fail += 1
            print(f"[{i}/{len(files)}] FAIL {f}: {e}", flush=True)
    print(f"完成 ok={ok} fail={fail}", flush=True)


if __name__ == "__main__":
    main()
