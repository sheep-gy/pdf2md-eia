# -*- coding: utf-8 -*-
"""PDF 扫描、页数/类型探测、输出路径规划。"""
from __future__ import annotations

import re
from pathlib import Path

import pymupdf

SUPPORTED = {".pdf", ".docx", ".doc", ".xlsx", ".xls"}


def file_format(path: str | Path) -> str:
    return Path(path).suffix.lower()


def is_supported(path: str | Path) -> bool:
    return file_format(path) in SUPPORTED


def is_pdf(path: str | Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == b"%PDF"
    except Exception:
        return False


def probe_pdf(path: str | Path) -> dict:
    """快速页数/类型探测。只抽样 3 页，避免大文件拖慢界面。"""
    try:
        doc = pymupdf.open(path)
        n = doc.page_count
        idxs = sorted(set([0, n // 2, n - 1])) if n else []
        hit = 0
        for i in idxs:
            try:
                if len((doc[i].get_text("text") or "").strip()) >= 30:
                    hit += 1
            except Exception:
                pass
        doc.close()
        ratio = hit / max(1, len(idxs))
        kind = "text" if ratio >= 0.6 else ("scan" if ratio <= 0.2 else "mixed")
        return {"pages": n, "kind": kind, "error": ""}
    except Exception as e:
        return {"pages": 0, "kind": "error", "error": str(e)[:200]}


def probe_file(path: str | Path) -> dict:
    fmt = file_format(path)
    if fmt == ".pdf":
        return probe_pdf(path)
    if fmt in SUPPORTED:
        try:
            size = Path(path).stat().st_size
        except Exception:
            size = 0
        return {"pages": 0, "kind": fmt.lstrip("."), "error": "",
                "size_mb": round(size / 1048576, 2)}
    return {"pages": 0, "kind": "unsupported", "error": "不支持的文件类型"}


def scan_folder(folder: str | Path) -> list[tuple[Path, Path]]:
    """递归找支持的 PDF/Office 文件，返回 (文件, 相对父目录)。"""
    root = Path(folder)
    out = []
    for p in sorted(root.rglob("*")):
        try:
            if not p.is_file() or p.suffix.lower() not in SUPPORTED:
                continue
            rel = p.parent.relative_to(root)
        except Exception:
            rel = Path()
        out.append((p, rel))
    return out


def sanitize_stem(stem: str) -> str:
    s = re.sub(r'[<>:"/\\|?*]', "_", stem).strip().rstrip(". ")
    return s or "未命名"


def plan_output(pdf: Path, cfg, rel_parent: Path | None = None) -> tuple[Path, str]:
    """返回 (输出目录, 输出文件名 stem)。"""
    stem = sanitize_stem(pdf.stem)
    if getattr(cfg, "out_mode", "same") == "same":
        return pdf.parent / "md输出", stem
    root = Path(cfg.out_dir) if getattr(cfg, "out_dir", "") else pdf.parent / "md输出"
    if getattr(cfg, "preserve_tree", True) and rel_parent:
        root = root / rel_parent
    return root, stem


def unique_outputs(items: list[dict]) -> None:
    """就地解决统一输出目录下的同名冲突。

    每个 item 需要 pdf / out_dir / stem 字段；冲突时先加父目录名，再加 __2。
    """
    used: dict[tuple[str, str], dict] = {}
    for it in items:
        out_dir = str(Path(it["out_dir"]))
        key = (out_dir.casefold(), it["stem"].casefold())
        if key not in used:
            used[key] = it
            continue
        # 同名：先尝试父目录名
        suffix = sanitize_stem(it["pdf"].parent.name)[:20]
        cand = f'{it["stem"]}__{suffix}'
        k2 = (out_dir.casefold(), cand.casefold())
        n = 2
        while k2 in used:
            cand = f'{it["stem"]}__{suffix}__{n}'
            k2 = (out_dir.casefold(), cand.casefold())
            n += 1
        it["stem"] = cand
        used[k2] = it


def skip_reason(pdf: Path, pages: int, md_path: Path, cfg,
                fmt: str | None = None) -> str | None:
    fmt = fmt or file_format(pdf)
    limit = int(getattr(cfg, "skip_lt_pages", 20) or 0)
    if fmt == ".pdf" and limit > 0 and pages and pages < limit:
        return f"跳过(<{limit}页)"
    if md_path.exists() and not getattr(cfg, "overwrite", False):
        return "跳过(已存在)"
    return None
