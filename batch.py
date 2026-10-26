# -*- coding: utf-8 -*-
"""批量转换。默认只处理「报告书」，保留原目录层级，可断点续跑。

示例：
  python batch.py "D:/下载/各省环评报告" -o "D:/环评md" --pattern 报告书 --limit 5
  python batch.py ... --workers 2 --only text
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from office_convert import convert_any, SUPPORTED

import pymupdf


def probe_kind(path):
    """PDF 抽样判断 text/scan/mixed；Office 直接返回 office。"""
    if Path(path).suffix.lower() != ".pdf":
        return "office"
    try:
        doc = pymupdf.open(path)
        n = doc.page_count
        step = max(1, n // 5)
        idxs = sorted(set([0] + list(range(step, n, step))[:4] + [n - 1]))
        tp = 0
        for i in idxs:
            if len((doc[i].get_text("text") or "").strip()) >= 30:
                tp += 1
        doc.close()
        r = tp / max(1, len(idxs))
        return "text" if r >= 0.6 else ("scan" if r <= 0.2 else "mixed")
    except Exception:
        return "error"


def collect(root, pattern, only, limit):
    files = []
    for p in sorted(Path(root).rglob("*")):
        if not p.is_file() or p.suffix.lower() not in SUPPORTED:
            continue
        s = str(p)
        if pattern and pattern not in s:
            continue
        files.append(p)
    if only != "all":
        files = [f for f in files if probe_kind(f) == only]
    if limit:
        files = files[:limit]
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--pattern", default="报告书")
    ap.add_argument("--only", default="all",
                    choices=["all", "text", "scan", "mixed", "office"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    ap.add_argument("--tier", default="basic", choices=["basic", "standard", "advanced"])
    args = ap.parse_args()

    root = Path(args.root).resolve()
    out_root = Path(args.out).resolve()
    files = collect(root, args.pattern, args.only, args.limit)
    print(f"待处理 {len(files)} 份", flush=True)
    log_path = out_root / "_batch_log.jsonl"
    out_root.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    ok = skip = fail = 0

    def work(f):
        rel = f.relative_to(root)
        out_dir = out_root / rel.parent
        return f, convert_any(f, out_dir, overwrite=args.overwrite,
                              device=args.device, tier=args.tier)

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {ex.submit(work, f): f for f in files}
        for i, fut in enumerate(as_completed(futs), 1):
            f = futs[fut]
            try:
                _, r = fut.result()
                if r.get("skipped"):
                    skip += 1
                    status = "skip"
                else:
                    ok += 1
                    status = "ok"
                rec = {"file": str(f), "status": status, "qa": r.get("qa")}
                print(f"[{i}/{len(files)}] {status} {f.name}", flush=True)
            except Exception as e:
                fail += 1
                status = "fail"
                rec = {"file": str(f), "status": status, "error": str(e)[:500]}
                print(f"[{i}/{len(files)}] FAIL {f.name}: {e}", flush=True)
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"完成 ok={ok} skip={skip} fail={fail} 用时 {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
