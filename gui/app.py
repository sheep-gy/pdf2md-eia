# -*- coding: utf-8 -*-
"""EIA report to md 主窗口（Tkinter）。"""
from __future__ import annotations

import os
import queue
import re
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from . import envcheck, files, settings
from .install_wizard import InstallWizard, LegacyInstallDialog
from .jobrunner import Job, JobRunner, plan_parallel
from .platform_win import (acquire_single_instance, activate_window,
                           free_ram_gb, cpu_physical, open_path,
                           recommend_workers, set_dpi_awareness,
                           restore_aux_windows)

APP_TITLE = "EIA report to md"
STATUS_TAG = {
    "等待": "wait", "扫描中": "wait", "转换中": "run",
    "完成": "ok", "失败": "bad", "跳过": "skip",
    "不支持": "skip", "需要LibreOffice": "skip", "已停止": "skip",
}
PHASE_CN = {
    "probe": "分析页面",
    "probe_done": "页面分析完成",
    "mineru": "解析正文/表格",
    "mineru_done": "正文解析完成",
    "mineru_parallel": "分页并行解析",
    "mineru_parallel_fallback": "分页失败，回退整本解析",
    "repair": "检查漏字页",
    "repair_scan": "漏字页检查完成",
    "repair_done": "漏字页补跑完成",
    "render": "生成 Markdown",
    "write": "写入文件",
}


def run_gui(initial_paths=None):
    set_dpi_awareness()
    ok, _ = acquire_single_instance()
    if not ok:
        activate_window(APP_TITLE)
        return
    cfg = settings.load_config()
    app = App(cfg, initial_paths=initial_paths)
    app.mainloop()


class App(tk.Tk):
    def __init__(self, cfg, initial_paths=None):
        super().__init__()
        self.cfg = cfg
        self.events: queue.Queue = queue.Queue()
        self.rows: dict[str, dict] = {}
        self._paths: set[str] = set()
        self._scan_stop = threading.Event()
        self._scan_thread = None
        self.runner: JobRunner | None = None
        self.env_report = None
        self.total_jobs = 0
        self.done_jobs = 0
        self._closing = False
        self._wizard = None
        self._last_normal_geom = None

        self.title(APP_TITLE)
        self.minsize(1020, 660)
        self._restore_geometry()
        self._set_icon()
        self._style()
        self._build()
        self._load_ui_from_cfg()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Configure>", self._on_configure, add="+")
        self.bind("<Map>", self._on_map, add="+")
        self.after(100, self._pump)
        self.after(500, self._watch_children)
        self.after(300, self._start_env_check)
        self.after(700, self._maybe_first_run)
        if initial_paths:
            self.after(900, lambda: self.add_paths(initial_paths))

    # ---------- 外观 ----------
    def _style(self):
        try:
            ttk.Style(self).theme_use("vista")
        except Exception:
            pass
        style = ttk.Style(self)
        style.configure(".", font=("Microsoft YaHei UI", 9))
        style.configure("Treeview", rowheight=25)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("TButton", padding=(8, 4))
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Banner.TFrame", background="#eef6ee")
        style.configure("BannerBad.TFrame", background="#fdeeee")
        style.configure("BannerWarn.TFrame", background="#fff8e1")

    def _set_icon(self):
        for p in (settings.resource_dir() / "assets" / "EIA-report-to-md.ico",
                  settings.app_dir() / "assets" / "EIA-report-to-md.ico"):
            if p.exists():
                try:
                    self.iconbitmap(str(p))
                    return
                except Exception:
                    pass

    def _restore_geometry(self):
        w = self.cfg.window or {}
        try:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            ww = max(1020, min(int(w.get("w", 1180)), sw - 40))
            hh = max(660, min(int(w.get("h", 720)), sh - 80))
            x = max(0, min(int(w.get("x", 80)), sw - ww))
            y = max(0, min(int(w.get("y", 60)), sh - hh))
            self.geometry(f"{ww}x{hh}+{x}+{y}")
        except Exception:
            self.geometry("1180x720")

    def _geometry_tuple(self):
        """保存 wm_geometry 的外框尺寸，避免反复最小化/恢复越缩越小。"""
        try:
            g = self.geometry()
            m = re.match(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)$", g)
            if m:
                return tuple(int(x) for x in m.groups())
        except Exception:
            pass
        return (self.winfo_width(), self.winfo_height(),
                self.winfo_x(), self.winfo_y())

    def _on_configure(self, event):
        if event.widget is not self:
            return
        try:
            if self.state() == "normal":
                self._last_normal_geom = self._geometry_tuple()
        except Exception:
            pass

    def _on_map(self, event):
        if event.widget is not self:
            return
        # 主窗口恢复时，把可能还最小化的向导/对话框一起带回来
        self.after(60, self._restore_children)

    def _watch_children(self):
        """主窗口正常时，自动把被 Windows 隐藏的向导/对话框带回来。"""
        try:
            if not self._closing and self.state() == "normal" and self.winfo_ismapped():
                self._restore_children()
        except Exception:
            pass
        if not self._closing:
            self.after(500, self._watch_children)

    def _restore_children(self):
        for w in (self._wizard, getattr(self, "_legacy_dialog", None)):
            if not w:
                continue
            try:
                if not w.winfo_exists():
                    continue
                if w.state() in ("iconic", "withdrawn") or not w.winfo_ismapped():
                    w.deiconify()
                w.lift()
            except Exception:
                pass
        try:
            restore_aux_windows()
        except Exception:
            pass

    def _reset_window(self):
        try:
            self.state("normal")
            self.geometry("1180x720+80+60")
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _build(self):
        self._build_menu()

        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=(8, 4))
        self.banner = ttk.Frame(top, style="BannerWarn.TFrame")
        self.banner.pack(fill="x")
        self.banner_dot = tk.Label(self.banner, text="●", fg="#9a6700",
                                   bg="#fff8e1", font=("Microsoft YaHei UI", 12, "bold"))
        self.banner_dot.pack(side="left", padx=(10, 4), pady=6)
        box = tk.Frame(self.banner, bg="#fff8e1")
        box.pack(side="left", fill="x", expand=True)
        self.banner_text = tk.Label(box, text="正在检查运行环境…",
                                    bg="#fff8e1", fg="#333",
                                    font=("Microsoft YaHei UI", 10, "bold"),
                                    anchor="w")
        self.banner_text.pack(fill="x")
        self.banner_detail = tk.Label(box, text="", bg="#fff8e1", fg="#666",
                                      anchor="w")
        self.banner_detail.pack(fill="x")
        ttk.Button(self.banner, text="检查", command=self._start_env_check).pack(
            side="right", padx=4, pady=6)
        ttk.Button(self.banner, text="安装/修复", command=self.open_wizard).pack(
            side="right", padx=4, pady=6)

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=4)

        left = ttk.Frame(paned)
        right = ttk.Frame(paned)
        paned.add(left, weight=3)
        paned.add(right, weight=2)
        self._build_left(left)
        self._build_right(right)

        bar = ttk.Frame(self)
        bar.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(bar, textvariable=self.status_var, anchor="w").pack(
            side="left", padx=10, pady=3)
        self.env_status_var = tk.StringVar(value="环境检查中")
        ttk.Label(bar, textvariable=self.env_status_var, anchor="e").pack(
            side="right", padx=10, pady=3)

    def _build_menu(self):
        m = tk.Menu(self)
        f = tk.Menu(m, tearoff=0)
        f.add_command(label="添加 PDF…", accelerator="Ctrl+O",
                      command=self.add_files)
        f.add_command(label="添加文件夹…", command=self.add_folder)
        f.add_separator()
        f.add_command(label="移除选中", accelerator="Delete",
                      command=self.remove_selected)
        f.add_command(label="清空列表", command=self.clear_all)
        f.add_separator()
        f.add_command(label="打开输出目录", command=self.open_output_dir)
        f.add_command(label="重置窗口位置/大小", command=self._reset_window)
        f.add_command(label="退出", command=self._on_close)
        m.add_cascade(label="文件", menu=f)

        e = tk.Menu(m, tearoff=0)
        e.add_command(label="检查环境", accelerator="F5", command=self._start_env_check)
        e.add_command(label="安装/修复环境", command=self.open_wizard)
        e.add_command(label="安装旧版 DOC/XLS 支持（LibreOffice）",
                      command=self.open_legacy_install)
        e.add_separator()
        e.add_command(label="打开配置目录", command=lambda: open_path(settings.data_dir()))
        e.add_command(label="打开日志目录", command=lambda: open_path(settings.logs_dir()))
        e.add_command(label="手动安装说明", command=self._show_manual)
        m.add_cascade(label="环境", menu=e)

        h = tk.Menu(m, tearoff=0)
        h.add_command(label="使用说明", command=self._show_help)
        h.add_command(label="关于", command=self._show_about)
        m.add_cascade(label="帮助", menu=h)
        self.config(menu=m)
        self.bind("<Control-o>", lambda e: self.add_files())
        self.bind("<F5>", lambda e: self._start_env_check())
        self.bind("<Delete>", lambda e: self.remove_selected())

    def _build_left(self, parent):
        tb = ttk.Frame(parent)
        tb.pack(fill="x", pady=(0, 4))
        ttk.Button(tb, text="添加 PDF…", command=self.add_files).pack(side="left")
        ttk.Button(tb, text="添加文件夹…", command=self.add_folder).pack(side="left", padx=4)
        ttk.Button(tb, text="移除", command=self.remove_selected).pack(side="left")
        ttk.Button(tb, text="清空", command=self.clear_all).pack(side="left", padx=4)
        self.count_var = tk.StringVar(value="0 个文件")
        ttk.Label(tb, textvariable=self.count_var).pack(side="right")

        wrap = ttk.Frame(parent)
        wrap.pack(fill="both", expand=True)
        cols = ("status", "name", "pages", "size", "kind", "elapsed", "recall", "out")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="extended")
        heads = {
            "status": ("状态", 78), "name": ("文件名", 300), "pages": ("页数", 55),
            "size": ("大小", 70), "kind": ("类型", 64), "elapsed": ("用时", 65),
            "recall": ("覆盖率", 65), "out": ("输出", 260),
        }
        for c, (t, w) in heads.items():
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w" if c in ("name", "out") else "center")
        self.tree.tag_configure("wait", foreground="#666")
        self.tree.tag_configure("run", foreground="#0b57d0")
        self.tree.tag_configure("ok", foreground="#137333")
        self.tree.tag_configure("bad", foreground="#b00020")
        self.tree.tag_configure("skip", foreground="#9a6700")
        ys = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        xs = ttk.Scrollbar(wrap, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", lambda e: self.open_selected())
        self.tree.bind("<Button-3>", self._popup)

        self.menu_row = tk.Menu(self, tearoff=0)
        self.menu_row.add_command(label="打开结果", command=self.open_selected)
        self.menu_row.add_command(label="打开所在文件夹", command=self.open_selected_folder)
        self.menu_row.add_command(label="打开 QA JSON", command=self.open_qa)
        self.menu_row.add_separator()
        self.menu_row.add_command(label="重新转换", command=self.retry_selected)
        self.menu_row.add_command(label="移除", command=self.remove_selected)

    def _build_right(self, parent):
        st = ttk.LabelFrame(parent, text="转换设置")
        st.pack(fill="x", pady=(0, 6))
        row = ttk.Frame(st)
        row.pack(fill="x", padx=8, pady=4)
        ttk.Label(row, text="输出").pack(side="left")
        self.out_mode_var = tk.StringVar(value="same")
        ttk.Radiobutton(row, text="源文件旁 md输出", variable=self.out_mode_var,
                        value="same", command=self._out_mode_changed).pack(side="left", padx=4)
        ttk.Radiobutton(row, text="指定目录", variable=self.out_mode_var,
                        value="custom", command=self._out_mode_changed).pack(side="left")
        row2 = ttk.Frame(st)
        row2.pack(fill="x", padx=8, pady=2)
        self.out_dir_var = tk.StringVar()
        self.out_entry = ttk.Entry(row2, textvariable=self.out_dir_var)
        self.out_entry.pack(side="left", fill="x", expand=True)
        self.out_btn = ttk.Button(row2, text="浏览…", command=self._browse_out)
        self.out_btn.pack(side="left", padx=4)
        self.preserve_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(st, text="指定目录时保留文件夹层级",
                        variable=self.preserve_var).pack(anchor="w", padx=8)

        grid = ttk.Frame(st)
        grid.pack(fill="x", padx=8, pady=4)
        ttk.Label(grid, text="档位").grid(row=0, column=0, sticky="w")
        self.tier_var = tk.StringVar(value="basic")
        ttk.Combobox(grid, textvariable=self.tier_var, width=10,
                     values=("basic", "standard", "advanced"),
                     state="readonly").grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(grid, text="设备").grid(row=0, column=2, sticky="w", padx=(10, 0))
        self.device_var = tk.StringVar(value="cpu")
        ttk.Combobox(grid, textvariable=self.device_var, width=8,
                     values=("cpu", "cuda"), state="readonly").grid(
            row=0, column=3, sticky="w", padx=4)
        ttk.Label(grid, text="并发").grid(row=0, column=4, sticky="w", padx=(10, 0))
        self.workers_var = tk.IntVar(value=1)
        ttk.Spinbox(grid, from_=1, to=3, width=5, textvariable=self.workers_var).grid(
            row=0, column=5, sticky="w", padx=4)
        ttk.Label(st, text="basic 约 3–4 秒/页；standard 更慢但漏字更少。"
                           "单个大 PDF 会自动拆页并行；并发 2 时约 6–8 GB 内存。",
                  foreground="#666", wraplength=380).pack(anchor="w", padx=8, pady=(0, 2))

        self.skip_var = tk.BooleanVar(value=True)
        self.overwrite_var = tk.BooleanVar(value=False)
        self.keep_json_var = tk.BooleanVar(value=False)
        opts = ttk.Frame(st)
        opts.pack(fill="x", padx=8)
        left = ttk.Frame(opts)
        left.pack(side="left", fill="y")
        ttk.Checkbutton(left, text="跳过少于 20 页的 PDF",
                        variable=self.skip_var).pack(anchor="w")
        ttk.Checkbutton(left, text="覆盖已存在的 md",
                        variable=self.overwrite_var).pack(anchor="w")
        ttk.Checkbutton(left, text="保留 middle JSON（调试用）",
                        variable=self.keep_json_var).pack(anchor="w")

        decl = tk.Frame(opts, bg="#f7f9fc", highlightbackground="#c9d3e0",
                        highlightthickness=1)
        decl.pack(side="right", anchor="n", padx=(14, 0))
        tk.Label(decl, text="声明", bg="#f7f9fc", fg="#333",
                 font=("Microsoft YaHei UI", 9, "bold"),
                 anchor="center").pack(fill="x", padx=8, pady=(5, 2))
        tk.Label(decl, text="软件开发者：sheepgao", bg="#f7f9fc",
                 fg="#333", font=("Microsoft YaHei UI", 9),
                 anchor="center").pack(fill="x", padx=8)
        tk.Label(decl, text="个人免费使用，商用联系开发者",
                 bg="#f7f9fc", fg="#555", font=("Microsoft YaHei UI", 8),
                 anchor="center", justify="center", wraplength=300).pack(
            fill="x", padx=8, pady=(2, 0))
        tk.Label(decl, text="QQ277062599", bg="#f7f9fc", fg="#555",
                 font=("Microsoft YaHei UI", 8),
                 anchor="center").pack(fill="x", padx=8, pady=(2, 5))

        btns = ttk.Frame(st)
        btns.pack(fill="x", padx=8, pady=6)
        self.btn_start = ttk.Button(btns, text="开始转换", style="Primary.TButton",
                                    command=self.start)
        self.btn_start.pack(side="left", fill="x", expand=True)
        self.btn_stop = ttk.Button(btns, text="停止", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left", padx=(6, 0))

        pf = ttk.LabelFrame(parent, text="进度")
        pf.pack(fill="x", pady=(0, 6))
        self.progress = ttk.Progressbar(pf, maximum=100, mode="determinate")
        self.progress.pack(fill="x", padx=8, pady=(6, 2))
        self.progress_var = tk.StringVar(value="尚未开始")
        ttk.Label(pf, textvariable=self.progress_var, foreground="#555").pack(
            anchor="w", padx=8, pady=(0, 6))

        lf = ttk.LabelFrame(parent, text="日志")
        lf.pack(fill="both", expand=True)
        self.log = scrolledtext.ScrolledText(lf, height=10, state="disabled",
                                             font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=6, pady=6)

    # ---------- 配置 ----------
    def _load_ui_from_cfg(self):
        self.out_mode_var.set(self.cfg.out_mode or "same")
        self.out_dir_var.set(self.cfg.out_dir or "")
        self.preserve_var.set(bool(self.cfg.preserve_tree))
        self.tier_var.set(self.cfg.tier or "basic")
        self.device_var.set(self.cfg.device or "cpu")
        self.workers_var.set(max(1, min(3, int(self.cfg.workers or 1))))
        self.skip_var.set(int(self.cfg.skip_lt_pages or 0) > 0)
        self.overwrite_var.set(bool(self.cfg.overwrite))
        self.keep_json_var.set(bool(self.cfg.keep_json))
        self._out_mode_changed()

    def _save_ui_to_cfg(self):
        self.cfg.out_mode = self.out_mode_var.get()
        self.cfg.out_dir = self.out_dir_var.get().strip()
        self.cfg.preserve_tree = bool(self.preserve_var.get())
        self.cfg.tier = self.tier_var.get()
        self.cfg.device = self.device_var.get()
        self.cfg.workers = max(1, min(3, int(self.workers_var.get())))
        self.cfg.skip_lt_pages = 20 if self.skip_var.get() else 0
        self.cfg.overwrite = bool(self.overwrite_var.get())
        self.cfg.keep_json = bool(self.keep_json_var.get())

    def _out_mode_changed(self):
        custom = self.out_mode_var.get() == "custom"
        state = "normal" if custom else "disabled"
        self.out_entry.configure(state=state)
        self.out_btn.configure(state=state)

    def _browse_out(self):
        d = filedialog.askdirectory(title="选择输出目录",
                                    initialdir=self.out_dir_var.get() or str(Path.home()))
        if d:
            self.out_dir_var.set(d)
            self.out_mode_var.set("custom")
            self._out_mode_changed()

    # ---------- 添加文件 ----------
    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="选择文件",
            filetypes=[("支持的文件", "*.pdf;*.docx;*.doc;*.xlsx;*.xls"),
                       ("PDF", "*.pdf"), ("Word", "*.docx;*.doc"),
                       ("Excel", "*.xlsx;*.xls"), ("所有文件", "*.*")])
        if paths:
            self.add_paths(paths)

    def add_folder(self):
        d = filedialog.askdirectory(title="选择包含 PDF 的文件夹")
        if d:
            self.add_paths([d])

    def add_paths(self, paths):
        items = []
        for raw in paths:
            p = Path(raw)
            if p.is_dir():
                items += files.scan_folder(p)
            elif p.is_file():
                items.append((p, Path()))
        # 去重
        new = []
        for pdf, rel in items:
            key = str(pdf.resolve()).casefold()
            if key in self._paths:
                continue
            self._paths.add(key)
            new.append((pdf, rel))
        if not new:
            return
        self.count_var.set(f"{len(self.rows)} 个文件")
        self.append_log(f"添加 {len(new)} 个文件，正在读取页数…")
        threading.Thread(target=self._scan_worker, args=(new,), daemon=True).start()

    def _scan_worker(self, items):
        n = 0
        for pdf, rel in items:
            if self._scan_stop.is_set():
                return
            info = files.probe_file(pdf)
            self.events.put({"type": "scan_item", "pdf": pdf, "rel": rel,
                             "fmt": files.file_format(pdf), **info})
            n += 1
            if n % 10 == 0:
                self.events.put({"type": "scan_progress", "count": n})
        self.events.put({"type": "scan_done", "count": n})

    def _add_row(self, pdf: Path, rel: Path, pages: int, kind: str, error: str = ""):
        key = str(pdf).casefold()
        for iid, r in self.rows.items():
            if str(r["pdf"]).casefold() == key:
                return iid
        iid = f"r{len(self.rows)+1}"
        try:
            size = pdf.stat().st_size / 1048576
        except Exception:
            size = 0
        fmt = files.file_format(pdf)
        supported = files.is_supported(pdf)
        status = ("不支持" if not supported
                  else ("失败" if error else "等待"))
        self.rows[iid] = {
            "pdf": pdf, "rel": rel, "fmt": fmt, "pages": pages, "kind": kind,
            "size": size, "status": status, "elapsed": "", "recall": "",
            "out": "", "error": error, "qa": {}, "iid": iid,
        }
        self._update_row(iid)
        return iid

    def _update_row(self, iid: str):
        r = self.rows.get(iid)
        if not r:
            return
        pages_txt = ("—" if r.get("fmt") != ".pdf"
                     else (r["pages"] or "?"))
        vals = (r["status"], r["pdf"].name, pages_txt,
                f'{r["size"]:.1f} MB', r["kind"], r["elapsed"],
                r["recall"], r["out"])
        tag = STATUS_TAG.get(r["status"], "wait")
        if self.tree.exists(iid):
            self.tree.item(iid, values=vals, tags=(tag,))
        else:
            self.tree.insert("", "end", iid=iid, values=vals, tags=(tag,))
        self.count_var.set(f"{len(self.rows)} 个文件")

    # ---------- 环境 ----------
    def _start_env_check(self):
        self.set_banner("正在检查运行环境…", "读取 CPU / 内存 / 显卡 / 依赖", "warn")
        threading.Thread(target=self._env_worker, daemon=True).start()

    def _env_worker(self):
        try:
            rep = envcheck.check_environment(self.cfg, deep_gpu=False)
            self.events.put({"type": "env_report", "report": rep})
        except Exception as e:
            self.events.put({"type": "env_error", "message": str(e)})

    def _apply_env_report(self, rep):
        self.env_report = rep
        self.cfg.env_ok = rep.ready
        hw = rep.hardware
        gpu = hw.gpu
        detail = (f'{hw.cpu_physical} 核 / {hw.cpu_logical} 线程 · '
                  f'内存 {hw.ram_total_gb:.1f} GB · '
                  f'{"GPU: " + gpu["name"] if gpu else "CPU"} · '
                  f'建议并发 {hw.recommended_workers}')
        if (self.cfg.workers <= 0 and hw.recommended_workers
                and int(self.workers_var.get() or 1) <= 1):
            self.cfg.workers = max(1, min(3, int(hw.recommended_workers)))
            self.workers_var.set(self.cfg.workers)
            self.append_log(f"已按硬件自检将并发设为 {self.cfg.workers}。")
        if rep.ready:
            self.set_banner("运行环境就绪", detail, "ok")
            self.env_status_var.set(f'环境就绪 · {self.cfg.device.upper()} · '
                                    f'{self.cfg.env_dir}')
        else:
            miss = "、".join(x.title for x in rep.missing_required)
            self.set_banner("缺少运行环境", f"{miss} · {detail}", "bad")
            self.env_status_var.set("环境未就绪")
        self._refresh_start_state()

    def set_banner(self, text: str, detail: str, level: str):
        bg = {"ok": "#eef6ee", "bad": "#fdeeee", "warn": "#fff8e1"}[level]
        fg = {"ok": "#137333", "bad": "#b00020", "warn": "#9a6700"}[level]
        style = {"ok": "Banner.TFrame", "bad": "BannerBad.TFrame",
                 "warn": "BannerWarn.TFrame"}[level]
        self.banner.configure(style=style)
        for w in (self.banner_dot, self.banner_text, self.banner_detail):
            try:
                w.configure(bg=bg)
            except Exception:
                pass
        self.banner_dot.configure(fg=fg)
        self.banner_text.configure(text=text)
        self.banner_detail.configure(text=detail)

    def _maybe_first_run(self):
        if not self.cfg.onboarding_done:
            self.open_wizard(first_run=True)
        elif self.env_report and not self.env_report.ready:
            self.append_log("环境未就绪；可点右上角“安装/修复”。")

    def open_wizard(self, first_run=False):
        if self._wizard and self._wizard.winfo_exists():
            self._wizard.lift()
            return
        self._wizard = InstallWizard(
            self, self.cfg, report=self.env_report,
            on_done=self._on_wizard_done, first_run=first_run)

    def _on_wizard_done(self, cfg, ready: bool):
        self.cfg = cfg
        self._load_ui_from_cfg()
        self.append_log("环境配置完成" if ready else "已跳过环境安装；暂时不能开始转换。")
        self._start_env_check()

    def _show_manual(self):
        messagebox.showinfo("手动安装说明", envcheck.manual_install_text(self.cfg),
                            parent=self)

    # ---------- 转换 ----------
    def _refresh_start_state(self):
        running = bool(self.runner and self.runner.is_running())
        ready = bool(self.env_report and self.env_report.ready)
        state = "disabled" if (running or not ready) else "normal"
        self.btn_start.configure(state=state)
        self.btn_stop.configure(state="normal" if running else "disabled")

    def _build_jobs(self):
        self._save_ui_to_cfg()
        if self.cfg.out_mode == "custom":
            if not self.cfg.out_dir:
                messagebox.showerror("输出目录", "请选择输出目录。", parent=self)
                return []
            try:
                Path(self.cfg.out_dir).mkdir(parents=True, exist_ok=True)
            except Exception as e:
                messagebox.showerror("输出目录", f"无法创建：{e}", parent=self)
                return []
        items = []
        for iid, r in self.rows.items():
            if r["status"] in ("不支持", "需要LibreOffice"):
                continue
            if not files.is_supported(r["pdf"]):
                r["status"] = "不支持"
                self._update_row(iid)
                continue
            if r["status"] == "完成" and not self.cfg.overwrite:
                continue
            out_dir, stem = files.plan_output(r["pdf"], self.cfg, r["rel"])
            items.append({"iid": iid, "pdf": r["pdf"], "out_dir": out_dir,
                          "stem": stem, "row": r})
        files.unique_outputs(items)
        jobs = []
        for it in items:
            r = it["row"]
            out_dir = Path(it["out_dir"])
            md_path = out_dir / (it["stem"] + ".md")
            reason = files.skip_reason(r["pdf"], r["pages"], md_path,
                                       self.cfg, fmt=r.get("fmt"))
            if reason:
                r["status"] = reason
                r["out"] = str(md_path)
                self._update_row(it["iid"])
                continue
            jobs.append(Job(iid=it["iid"], pdf=r["pdf"], pages=r["pages"],
                            kind=r["kind"], fmt=r.get("fmt", "pdf"),
                            out_dir=out_dir, out_stem=it["stem"],
                            md_path=md_path))
        return jobs

    def _check_legacy(self) -> bool:
        old = [r for r in self.rows.values()
               if r.get("fmt") in (".doc", ".xls")
               and r["status"] not in ("完成", "需要LibreOffice")]
        if not old:
            return True
        try:
            from office_convert import find_soffice
            if find_soffice():
                return True
        except Exception:
            pass
        if messagebox.askyesno(
                "需要 LibreOffice",
                "旧版 DOC/XLS 需要 LibreOffice（约 350 MB）。" + chr(10) +
                "现在自动安装吗？（安装期间可取消）", parent=self):
            self.open_legacy_install()
            return False
        for r in old:
            r["status"] = "需要LibreOffice"
            r["error"] = "需要先安装 LibreOffice"
            self._update_row(r["iid"])
        return True

    def open_legacy_install(self):
        if getattr(self, "_legacy_dialog", None) and self._legacy_dialog.winfo_exists():
            self._legacy_dialog.lift()
            return
        self._legacy_dialog = LegacyInstallDialog(
            self, self.cfg, on_done=self._after_legacy)

    def _after_legacy(self, ok: bool):
        if ok:
            for iid, r in self.rows.items():
                if r.get("fmt") in (".doc", ".xls") and r["status"] == "需要LibreOffice":
                    r["status"] = "等待"
                    r["error"] = ""
                    self._update_row(iid)
            self.append_log("LibreOffice 已就绪；请重新点“开始转换”。")

    def start(self):
        if self.runner and self.runner.is_running():
            return
        self._save_ui_to_cfg()
        if not self._check_legacy():
            return
        if not self.env_report or not self.env_report.ready:
            messagebox.showwarning("环境未就绪", "请先安装/修复运行环境。", parent=self)
            self.open_wizard()
            return
        jobs = self._build_jobs()
        if not jobs:
            messagebox.showinfo("没有可转换的文件",
                                "请添加 PDF，或检查是否都已被跳过。", parent=self)
            return
        # 并发上限按可用内存再算一次；用户坚持时可以按自己的设置跑
        eff, reason = recommend_workers(free_ram_gb(), cpu_physical())
        chosen = max(1, min(3, int(self.cfg.workers or 1)))
        if chosen > eff:
            if messagebox.askyesno(
                    "并发建议",
                    f"当前设置 {chosen} 个并发，但{reason}。\n\n"
                    f"点“是”仍按 {chosen} 个继续；点“否”改为建议的 {eff} 个。",
                    parent=self):
                chosen = min(3, chosen)
            else:
                chosen = eff
        # 只有一个文件时，把并发转成单文件内部的“分页并行”；
        # 多个文件时仍按文件级并发。
        file_workers, page_workers, cpu_threads = plan_parallel(
            jobs, chosen, free_ram_gb())
        self.cfg.workers = chosen
        settings.save_config(self.cfg)
        for iid in [j.iid for j in jobs]:
            self.rows[iid]["status"] = "等待"
            self._update_row(iid)
        self.total_jobs = len(jobs)
        self.done_jobs = 0
        self.progress.configure(maximum=max(1, self.total_jobs), value=0)
        self.progress_var.set(f"0 / {self.total_jobs}")
        if page_workers > 1:
            extra = f"（单文件分页并行 {page_workers} 路，每路 {cpu_threads} 线程）"
        elif file_workers > 1:
            extra = f"（每个文件 1 路，每路 {cpu_threads} 线程）"
        else:
            extra = "（单文件单路，引擎默认线程）"
        self.append_log(f"开始转换 {self.total_jobs} 份，{file_workers} 个文件并发"
                        f"{extra}，设备 {self.cfg.device}，档位 {self.cfg.tier}。")
        self.runner = JobRunner(self.cfg, self.events, workers=file_workers,
                                page_workers=page_workers,
                                cpu_threads=cpu_threads)
        self.runner.start(jobs)
        self._refresh_start_state()

    def stop(self):
        if self.runner and self.runner.is_running():
            self.btn_stop.configure(state="disabled")
            self.progress_var.set("正在停止并清理子进程…")
            self.append_log("正在停止…")
            self.runner.stop()

    def retry_selected(self):
        for iid in self.tree.selection():
            r = self.rows.get(iid)
            if not r:
                continue
            r["status"] = "等待"
            r["error"] = ""
            r["elapsed"] = ""
            r["recall"] = ""
            self._update_row(iid)

    def _finish_all(self):
        self._refresh_start_state()
        ok = sum(1 for r in self.rows.values() if r["status"] == "完成")
        fail = sum(1 for r in self.rows.values() if r["status"] == "失败")
        skip = sum(1 for r in self.rows.values() if r["status"].startswith("跳过"))
        self.progress_var.set(f"完成 {ok} · 失败 {fail} · 跳过 {skip}")
        self.append_log(f"本轮结束：完成 {ok}，失败 {fail}，跳过 {skip}。")

    # ---------- 事件泵 ----------
    def _pump(self):
        try:
            for _ in range(200):
                ev = self.events.get_nowait()
                self._apply_event(ev)
        except queue.Empty:
            pass
        if not self._closing:
            self.after(100, self._pump)

    def _apply_event(self, ev):
        t = ev.get("type")
        if t == "env_report":
            self._apply_env_report(ev["report"])
        elif t == "env_error":
            self.set_banner("环境检查失败", ev.get("message", ""), "bad")
        elif t == "scan_item":
            iid = self._add_row(ev["pdf"], ev["rel"], ev.get("pages", 0),
                                ev.get("kind", ""), ev.get("error", ""))
            r = self.rows.get(iid)
            if r and r["status"] == "等待":
                out_dir, stem = files.plan_output(r["pdf"], self.cfg, r["rel"])
                if files.skip_reason(r["pdf"], r["pages"],
                                     out_dir / (stem + ".md"), self.cfg,
                                     fmt=r.get("fmt")):
                    r["status"] = "跳过"
                    self._update_row(iid)
        elif t == "scan_progress":
            self.count_var.set(f"{ev['count']} 个文件已读取")
        elif t == "scan_done":
            self.count_var.set(f"{len(self.rows)} 个文件")
            self.append_log(f"文件读取完成，共 {len(self.rows)} 个。")
        elif t == "job_started":
            job = ev["job"]
            r = self.rows.get(job.iid)
            if r:
                r["status"] = "转换中"
                r["out"] = str(job.md_path)
                r["elapsed"] = "0s"
                self._update_row(job.iid)
            self.append_log(f"开始：{job.pdf.name}")
        elif t == "job_progress":
            job = ev["job"]
            name = (ev.get("event") or {}).get("name", "")
            if name in PHASE_CN:
                self.append_log(f"  {job.pdf.name}：{PHASE_CN[name]}")
        elif t == "job_tick":
            job = ev["job"]
            r = self.rows.get(job.iid)
            if r:
                r["elapsed"] = f"{int(ev.get('elapsed', 0))}s"
                self._update_row(job.iid)
        elif t == "job_done":
            self._on_job_done(ev["job"])
        elif t == "runner_done":
            self.done_jobs = self.total_jobs
            self.progress.configure(value=self.total_jobs)
            self._finish_all()

    def _on_job_done(self, job: Job):
        r = self.rows.get(job.iid)
        if r:
            if job.status in ("完成", "跳过", "已停止", "失败"):
                r["status"] = job.status
            r["elapsed"] = f"{job.seconds:.0f}s" if job.seconds else r["elapsed"]
            r["out"] = str(job.md_path) if job.md_path else r["out"]
            if job.qa:
                r["qa"] = job.qa
                avg = job.qa.get("avg_page_recall")
                if avg is not None:
                    r["recall"] = f"{float(avg):.3f}"
            if job.error:
                r["error"] = job.error
            self._update_row(job.iid)
        if job.status == "完成":
            self.append_log(f"完成：{job.pdf.name}（{job.seconds:.0f}s）")
        elif job.status == "失败":
            self.append_log(f"失败：{job.pdf.name}：{job.error[:200]}")
        elif job.status == "已停止":
            self.append_log(f"已停止：{job.pdf.name}")
        elif job.status == "跳过":
            self.append_log(f"跳过：{job.pdf.name}")
        self.done_jobs += 1
        self.progress.configure(value=min(self.total_jobs, self.done_jobs))
        self.progress_var.set(f"{self.done_jobs} / {self.total_jobs}")

    # ---------- 打开 ----------
    def _selected_rows(self):
        return [self.rows[i] for i in self.tree.selection() if i in self.rows]

    def open_selected(self):
        for r in self._selected_rows():
            out_dir, stem = files.plan_output(r["pdf"], self.cfg, r["rel"])
            md = out_dir / (stem + ".md")
            if md.exists():
                open_path(md)
            elif out_dir.exists():
                open_path(out_dir)
            else:
                messagebox.showinfo("结果", "还没有输出结果。", parent=self)

    def open_selected_folder(self):
        for r in self._selected_rows():
            out_dir, _ = files.plan_output(r["pdf"], self.cfg, r["rel"])
            open_path(out_dir if out_dir.exists() else r["pdf"].parent)

    def open_qa(self):
        for r in self._selected_rows():
            out_dir, stem = files.plan_output(r["pdf"], self.cfg, r["rel"])
            qa = out_dir / (stem + "_qa.json")
            if qa.exists():
                open_path(qa)

    def open_output_dir(self):
        if self.cfg.out_mode == "custom" and self.cfg.out_dir:
            open_path(self.cfg.out_dir)
        else:
            rows = self._selected_rows()
            if rows:
                out_dir, _ = files.plan_output(rows[0]["pdf"], self.cfg, rows[0]["rel"])
                open_path(out_dir if out_dir.exists() else rows[0]["pdf"].parent)
            else:
                messagebox.showinfo("输出目录", "请先选择文件，或设置指定输出目录。",
                                    parent=self)

    def _popup(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            if iid not in self.tree.selection():
                self.tree.selection_set(iid)
            self.menu_row.tk_popup(event.x_root, event.y_root)

    def remove_selected(self):
        for iid in list(self.tree.selection()):
            r = self.rows.pop(iid, None)
            if r:
                self._paths.discard(str(r["pdf"]).casefold())
            if self.tree.exists(iid):
                self.tree.delete(iid)
        self.count_var.set(f"{len(self.rows)} 个文件")

    def clear_all(self):
        if self.runner and self.runner.is_running():
            messagebox.showwarning("正在转换", "请先停止当前任务。", parent=self)
            return
        self.rows.clear()
        self._paths.clear()
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self.count_var.set("0 个文件")

    # ---------- 日志/帮助 ----------
    def append_log(self, line: str):
        ts = time.strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{ts}] {line}\n")
        self.log.see("end")
        try:
            n = int(self.log.index("end-1c").split(".")[0])
            if n > 5000:
                self.log.delete("1.0", "1000.0")
        except Exception:
            pass
        self.log.configure(state="disabled")
        try:
            with open(settings.logs_dir() / "gui.log", "a", encoding="utf-8") as fh:
                fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {line}\n")
        except Exception:
            pass

    def _show_help(self):
        messagebox.showinfo(
            "使用说明",
            "1. 点“添加 PDF”或“添加文件夹”。\n"
            "2. 选输出位置（默认源文件旁 md输出）。\n"
            "3. 点“开始转换”，完成后双击行打开 md。\n\n"
            "少于 20 页的 PDF 默认跳过。\n"
            "首次运行会自检环境，缺少依赖时按提示安装。",
            parent=self)

    def _show_about(self):
        hw = self.env_report.hardware if self.env_report else None
        extra = ""
        if hw:
            extra = (f"\nCPU: {hw.cpu_physical} 核 / {hw.cpu_logical} 线程"
                     f"\n内存: {hw.ram_total_gb:.1f} GB"
                     f"\n显卡: {hw.gpu_note}")
        messagebox.showinfo(
            "关于",
            f"EIA report to md 2.0.1\n"
            f"环境: {self.cfg.env_dir}\n"
            f"模型: {self.cfg.mineru_home}{extra}",
            parent=self)

    # ---------- 关闭 ----------
    def _on_close(self):
        if self.runner and self.runner.is_running():
            if not messagebox.askyesno("退出", "仍有转换任务，退出会停止并杀掉子进程，确定？",
                                       parent=self):
                return
            self.runner.stop()
            self.runner.wait(4)
        self._scan_stop.set()
        try:
            self._save_ui_to_cfg()
            # 最小化/最大化状态下不覆盖已保存的窗口尺寸，
            # 否则 Windows 会把 720 报成 684，反复重开会越缩越小。
            if self.state() == "normal":
                geom = self._geometry_tuple()
                self.cfg.window = {"w": geom[0], "h": geom[1],
                                   "x": geom[2], "y": geom[3]}
        except Exception:
            pass
        settings.save_config(self.cfg)
        self._closing = True
        self.destroy()
