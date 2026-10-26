# -*- coding: utf-8 -*-
"""首次运行自检 + 安装向导。

设计原则：
- 环境已就绪：只显示配置结论和并发确认，不显示安装位置、不提示下载。
- 缺少依赖：显示缺失项，只让用户选一个“安装目录”，软件自动建
  mineru_env / mineru_home 两个子目录；支持中文路径。
- 确定按钮固定在窗口底部，任何情况下都可见。
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from . import envcheck, settings
from .installer import Installer, STEP_TITLES


class InstallWizard(tk.Toplevel):
    def __init__(self, parent, cfg, report=None, on_done=None,
                 first_run: bool = False):
        super().__init__(parent)
        self.cfg = cfg
        self.on_done = on_done
        self.first_run = first_run
        self.report = report
        self.installer = None
        self._q: queue.Queue = queue.Queue()
        self._installing = False

        self.title("EIA report to md · 首次运行自检")
        self.geometry("860x600")
        self.minsize(760, 520)
        self.transient(parent)
        try:
            self.grab_set()
        except Exception:
            pass

        self.install_dir_var = tk.StringVar(value=self._default_install_dir())
        self.standard_var = tk.BooleanVar(value=bool(cfg.install_standard))
        self.legacy_var = tk.BooleanVar(value=bool(cfg.install_legacy))
        self.gpu_var = tk.BooleanVar(value=False)
        self.workers_var = tk.IntVar(value=max(1, min(3, int(cfg.workers or 1))))
        self.status_var = tk.StringVar(value="正在检查本机配置与依赖…")
        self.estimate_var = tk.StringVar(value="")
        self.resolved_var = tk.StringVar(value="")
        self.step_var = tk.StringVar(value="")
        self.step_progress_var = tk.StringVar(value="")
        self.step_order = ["uv", "venv", "pip", "models_basic",
                           "models_standard", "verify"]
        self._installed = False
        self._env_ready = False

        self.install_dir_var.trace_add("write", lambda *_: self._update_install_paths())
        self.workers_var.trace_add("write", lambda *_: None)

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if report is None:
            self.btn_primary.configure(state="disabled")
            threading.Thread(target=self._check_worker, daemon=True).start()
        else:
            self._apply_report(report)
        self.after(100, self._poll)

    # ---------- 布局 ----------
    def _default_install_dir(self) -> str:
        env = Path(self.cfg.env_dir)
        home = Path(self.cfg.mineru_home)
        if env.parent == home.parent and env.parent.exists():
            return str(env.parent)
        if Path("D:/").exists():
            return "D:\\PDF2MD"
        return str(settings.data_dir() / "runtime")

    def _build(self):
        pad = {"padx": 10, "pady": 4}

        head = ttk.Frame(self)
        head.pack(fill="x", **pad)
        ttk.Label(head, text="首次运行：先确认电脑配置和依赖",
                  font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        ttk.Label(head, textvariable=self.status_var,
                  foreground="#555").pack(anchor="w", pady=(4, 0))

        # 底部按钮先 pack，保证永远可见
        footer = ttk.Frame(self)
        footer.pack(side="bottom", fill="x", padx=10, pady=8)
        self.btn_primary = ttk.Button(footer, text="确认并开始使用",
                                      command=self._on_primary)
        self.btn_primary.pack(side="right", padx=4)
        self.btn_cancel = ttk.Button(footer, text="取消安装", command=self._on_cancel,
                                     state="disabled")
        self.btn_cancel.pack(side="right", padx=4)
        self.btn_later = ttk.Button(footer, text="稍后再说", command=self._on_later)
        self.btn_later.pack(side="left", padx=4)
        ttk.Button(footer, text="手动安装说明",
                   command=self._show_manual).pack(side="left", padx=4)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=10, pady=4)

        hw = ttk.LabelFrame(body, text="本机配置与推荐")
        hw.pack(fill="x", pady=(0, 4))
        self.hw_text = tk.Text(hw, height=5, wrap="word", borderwidth=0,
                               background=self.cget("background"))
        self.hw_text.pack(fill="x", padx=8, pady=6)
        self.hw_text.configure(state="disabled")

        cfgf = ttk.LabelFrame(body, text="确认配置")
        cfgf.pack(fill="x", pady=4)
        row = ttk.Frame(cfgf)
        row.pack(fill="x", padx=8, pady=4)
        ttk.Label(row, text="并发进程").pack(side="left")
        self.spin_workers = ttk.Spinbox(row, from_=1, to=3, width=5,
                                        textvariable=self.workers_var)
        self.spin_workers.pack(side="left", padx=6)
        ttk.Label(row, text="（按可用内存和物理核数推荐，每路约 2.5–3 GB）").pack(side="left")
        self.chk_gpu = ttk.Checkbutton(cfgf, text="使用 NVIDIA 独显",
                                       variable=self.gpu_var,
                                       command=self._refresh_state)
        self.chk_gpu.pack(anchor="w", padx=8, pady=2)
        self.gpu_hint = ttk.Label(cfgf, text="", foreground="#555",
                                  wraplength=780, justify="left")
        self.gpu_hint.pack(anchor="w", padx=8, pady=(0, 4))

        # 已就绪摘要
        self.ready_frame = ttk.LabelFrame(body, text="依赖状态")
        self.ready_label = ttk.Label(self.ready_frame, text="", justify="left",
                                     wraplength=780)
        self.ready_label.pack(anchor="w", padx=8, pady=8)

        # 依赖明细（仅缺依赖时显示）
        self.dep_frame = ttk.LabelFrame(body, text="缺少的依赖")
        cols = ("ok", "name", "detail", "size")
        self.dep = ttk.Treeview(self.dep_frame, columns=cols, show="headings", height=6)
        for c, t, w in (("ok", "状态", 46), ("name", "组件", 170),
                        ("detail", "说明", 330), ("size", "大小", 70)):
            self.dep.heading(c, text=t)
            self.dep.column(c, width=w, anchor="w" if c != "ok" else "center")
        self.dep.tag_configure("ok", foreground="#137333")
        self.dep.tag_configure("bad", foreground="#b00020")
        self.dep.tag_configure("warn", foreground="#9a6700")
        self.dep.pack(fill="x", padx=6, pady=6)

        # 安装目录（仅缺依赖时显示）
        self.install_frame = ttk.LabelFrame(
            body, text="安装位置（软件会在里面创建 mineru_env 和 mineru_home）")
        self.path_row = ttk.Frame(self.install_frame)
        self.path_row.pack(fill="x", padx=8, pady=4)
        ttk.Label(self.path_row, text="安装目录").pack(side="left")
        ttk.Entry(self.path_row, textvariable=self.install_dir_var).pack(
            side="left", fill="x", expand=True, padx=6)
        ttk.Button(self.path_row, text="选择…", command=self._browse_install).pack(side="left")
        self.resolved_label = ttk.Label(self.install_frame, textvariable=self.resolved_var,
                                        foreground="#555", wraplength=780)
        self.resolved_label.pack(anchor="w", padx=8)
        self.chk_std = ttk.Checkbutton(
            self.install_frame, text="同时安装增强模型（+1.2 GB，推荐）",
            variable=self.standard_var, command=self._refresh_state)
        self.chk_std.pack(anchor="w", padx=8, pady=3)
        self.chk_legacy = ttk.Checkbutton(
            self.install_frame,
            text="安装旧版 DOC/XLS 支持（LibreOffice，约 350 MB）",
            variable=self.legacy_var, command=self._update_estimate)
        self.chk_legacy.pack(anchor="w", padx=8, pady=3)
        ttk.Label(self.install_frame, textvariable=self.estimate_var,
                  foreground="#555", wraplength=780).pack(anchor="w", padx=8, pady=(0, 6))

        # 安装进度/日志（仅安装时显示）
        self.progress_frame = ttk.LabelFrame(body, text="安装进度")
        self.progress = ttk.Progressbar(self.progress_frame, mode="determinate",
                                        maximum=100)
        self.progress.pack(fill="x", padx=8, pady=(6, 2))
        ttk.Label(self.progress_frame, textvariable=self.step_var,
                  foreground="#333", font=("Microsoft YaHei UI", 9, "bold")).pack(
            anchor="w", padx=8)
        self.step_progress = ttk.Progressbar(self.progress_frame,
                                             mode="determinate", maximum=100)
        self.step_progress.pack(fill="x", padx=8, pady=(4, 2))
        ttk.Label(self.progress_frame, textvariable=self.step_progress_var,
                  foreground="#555").pack(anchor="w", padx=8, pady=(0, 6))

        self.log_frame = ttk.LabelFrame(body, text="安装日志")
        self.log = scrolledtext.ScrolledText(self.log_frame, height=7, state="disabled",
                                             font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=6, pady=6)

        self._update_install_paths()

    def _hide(self, *frames):
        for f in frames:
            f.pack_forget()

    def _show(self, frame, **kw):
        frame.pack(fill=kw.pop("fill", "x"), **kw)

    def _update_install_paths(self):
        env, home = self._resolved_paths()
        self.resolved_var.set(f"Python 环境：{env}\n模型目录：{home}")
        self._update_estimate()

    def _resolved_paths(self) -> tuple[Path, Path]:
        root = Path(self.install_dir_var.get().strip() or self._default_install_dir())
        return root / "mineru_env", root / "mineru_home"

    # ---------- 自检 ----------
    def _check_worker(self):
        try:
            rep = envcheck.check_environment(self.cfg, deep_gpu=True)
            self._q.put({"type": "report", "report": rep})
        except Exception as e:
            self._q.put({"type": "check_error", "message": str(e)})

    def _apply_report(self, rep: envcheck.EnvReport):
        self.report = rep
        hw = rep.hardware
        gpu = hw.gpu
        gpu_ready = bool(gpu and self._gpu_backend_ready(rep))
        lines = [
            f"CPU：{hw.cpu_physical} 个物理核 / {hw.cpu_logical} 线程",
            f"内存：{hw.ram_total_gb:.1f} GB（当前可用 {hw.ram_free_gb:.1f} GB）",
            ("显卡：" + (f'{gpu["name"]}，{gpu["vram_gb"]} GB 显存' if gpu
                        else ("、".join(hw.video) + "（集显，CUDA 不可用）"
                              if hw.video else "未检测到 NVIDIA 独显"))),
            f"磁盘：{hw.disk_free_gb:.1f} GB 可用",
            f"建议：{hw.recommended_workers} 个并发；{hw.gpu_note}",
        ]
        self.hw_text.configure(state="normal")
        self.hw_text.delete("1.0", "end")
        self.hw_text.insert("end", "\n".join(lines))
        self.hw_text.configure(state="disabled")

        self.workers_var.set(max(1, min(3, hw.recommended_workers)))
        self.standard_var.set(bool(self.cfg.install_standard))
        self.legacy_var.set(bool(self.cfg.install_legacy))
        if gpu:
            vram = float(gpu.get("vram_gb") or 0)
            self.gpu_var.set(vram >= 8)
            self.chk_gpu.configure(state="normal")
            self.gpu_hint.configure(
                text=("CUDA 后端已就绪，可勾选使用独显"
                      if gpu_ready else
                      "检测到独显，CUDA 后端未安装；勾选后将自动下载并安装"
                      "（约 3.5 GB），不需要手动操作"))
        else:
            self.gpu_var.set(False)
            self.chk_gpu.configure(state="disabled")
            self.gpu_hint.configure(text=hw.gpu_note)

        self._refresh_state()
        self.btn_primary.configure(state="normal")

    def _refresh_state(self):
        """按当前勾选和环境状态切换“已就绪/需安装”两种界面。"""
        if self._installing or not self.report:
            return
        rep = self.report
        std_missing = not (rep.by_key("models_standard")
                           and rep.by_key("models_standard").ok)
        gpu_missing = bool(rep.hardware.gpu and
                           not self._gpu_backend_ready(rep))
        need_install = ((not rep.ready)
                        or (std_missing and self.standard_var.get())
                        or (gpu_missing and self.gpu_var.get()))
        if need_install:
            self._show_install(rep, env_ready=rep.ready)
        else:
            self._show_ready(rep)

    @staticmethod
    def _gpu_backend_ready(rep) -> bool:
        g = rep.by_key("gpu")
        return bool(g and "后端就绪" in (g.detail or ""))

    def _gpu_ok_or_not_requested(self, rep) -> bool:
        if not self.gpu_var.get():
            return True
        return self._gpu_backend_ready(rep)

    def _show_ready(self, rep):
        """依赖已满足：不显示安装目录、不提示下载。"""
        self._hide(self.dep_frame, self.install_frame,
                   self.progress_frame, self.log_frame)
        self.ready_frame.pack(fill="x", pady=4)
        basic = rep.by_key("models_basic")
        std = rep.by_key("models_standard")
        ver = ""
        py = rep.by_key("mineru_py")
        if py and py.detail:
            ver = py.detail
        gpu = rep.hardware.gpu
        gpu_ready = bool(gpu and self._gpu_backend_ready(rep))
        parts = [
            f"✓ MinerU / Python：{ver or '已就绪'}",
            f"✓ 基础模型：{'已就绪' if basic and basic.ok else '未就绪'}",
            f"{'✓' if std and std.ok else '○'} 增强补漏模型："
            f"{'已就绪' if std and std.ok else '未安装（不影响基础转换）'}",
            (f"{'✓' if gpu_ready else '○'} 显卡："
             f"{(gpu or {}).get('name') or '未检测到'} · "
             f"{'CUDA 已就绪' if gpu_ready else '使用 CPU'}"),
        ]
        self.ready_label.configure(text="\n".join(parts))
        self.status_var.set("环境已就绪；请确认并发数后开始使用")
        self.btn_primary.configure(text="确认并开始使用")
        self.btn_later.configure(text="关闭")
        self.geometry("820x600")

    def _show_install(self, rep, env_ready: bool = False):
        """缺依赖：显示缺失项；仅在环境目录不存在时才让用户选安装目录。"""
        self._env_ready = env_ready
        self._hide(self.ready_frame)
        self.dep_frame.pack(fill="x", pady=4)
        self.install_frame.pack(fill="x", pady=4)
        self.progress_frame.pack(fill="x", pady=4)
        self.log_frame.pack(fill="both", expand=True, pady=4)
        if env_ready:
            self.install_frame.configure(text="安装选项（补装到现有环境）")
            self.path_row.pack_forget()
            self.resolved_label.pack_forget()
            self.status_var.set("检测到缺少的组件；点击下方按钮自动安装，无需手动操作")
        else:
            self.install_frame.configure(
                text="安装位置（软件会在里面创建 mineru_env 和 mineru_home）")
            self.path_row.pack(fill="x", padx=8, pady=4)
            self.resolved_label.pack(anchor="w", padx=8)
            self.status_var.set("缺少运行环境；选择安装目录后自动安装")
        for i in self.dep.get_children():
            self.dep.delete(i)
        for it in rep.items:
            if it.key == "gpu":
                # 有卡但后端没装时才提示；没有卡就不占版面
                if not (it.ok and "后端就绪" not in (it.detail or "")):
                    continue
            elif it.ok:
                continue
            mark = "✗" if it.level in ("required", "install") else "○"
            size = f"{it.size_mb} MB" if it.size_mb else ""
            tag = "bad" if it.level in ("required", "install") else "warn"
            detail = it.detail if len(it.detail) <= 70 else it.detail[:68] + "…"
            self.dep.insert("", "end", values=(mark, it.title, detail, size),
                            tags=(tag,))
        self.chk_std.configure(state="normal")
        self.chk_legacy.configure(state="normal")
        self.btn_primary.configure(text="开始安装并配置")
        self.btn_later.configure(text="稍后再说")
        self.status_var.set("缺少运行环境；选择安装目录后开始安装")
        self._update_install_paths()
        self.geometry("940x760")

    # ---------- 预估 ----------
    def _target_ready(self) -> bool:
        if self._env_ready:
            env, home = Path(self.cfg.env_dir), Path(self.cfg.mineru_home)
        else:
            env, home = self._resolved_paths()
        return ((env / "Scripts" / "python.exe").is_file()
                and (env / "Scripts" / "mineru-kit.exe").is_file()
                and (home / "models").is_dir())

    def _update_estimate(self):
        if not self.report:
            return
        rep = self.report
        target_ok = self._target_ready()
        try:
            need_env = not target_ok
            need_basic = (not target_ok) or not (
                rep.by_key("models_basic") and rep.by_key("models_basic").ok)
            need_std = bool(self.standard_var.get()) and (
                (not target_ok) or not (rep.by_key("models_standard")
                                        and rep.by_key("models_standard").ok))
            need_gpu = bool(self.gpu_var.get()) and bool(rep.hardware.gpu)
            try:
                from office_convert import find_soffice
                need_legacy = bool(self.legacy_var.get()) and not find_soffice()
            except Exception:
                need_legacy = False
            mb = envcheck.estimate_mb(need_basic, need_std, need_gpu, need_env,
                                      need_legacy)
        except Exception:
            mb = 0
        if mb:
            self.estimate_var.set(f"预计下载约 {mb/1024:.1f} GB；"
                                  f"请确保安装盘至少 {max(5, mb/1024+2):.0f} GB 可用")
        else:
            self.estimate_var.set("当前选择无需下载")

    # ---------- 路径 ----------
    def _browse_install(self):
        d = filedialog.askdirectory(
            title="选择安装目录（支持中文路径）",
            initialdir=self.install_dir_var.get() or str(Path.home()))
        if d:
            self.install_dir_var.set(d)

    # ---------- 安装 ----------
    def _on_primary(self):
        if self._installing:
            return
        if self.report and self.report.ready:
            env, home = Path(self.cfg.env_dir), Path(self.cfg.mineru_home)
        else:
            env, home = self._resolved_paths()
            if not str(self.install_dir_var.get()).strip():
                messagebox.showerror("安装目录", "请选择安装目录。", parent=self)
                return
        self.cfg.env_dir = str(env)
        self.cfg.mineru_home = str(home)
        self.cfg.mineru_py = str(env / "Scripts" / "python.exe")
        self.cfg.mineru_bin = str(env / "Scripts" / "mineru-kit.exe")
        self.cfg.workers = max(1, min(3, int(self.workers_var.get())))
        self.cfg.device = "cuda" if self.gpu_var.get() else "cpu"
        self.cfg.install_standard = bool(self.standard_var.get())
        self.cfg.install_legacy = bool(self.legacy_var.get())

        # 依赖已就绪且没有补装项：只保存配置
        if (self.report and self.report.ready and self._target_ready()
                and not self._needs_extra()):
            self._finish(True)
            return

        self._installing = True
        self.btn_primary.configure(state="disabled")
        self.btn_later.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.chk_std.configure(state="disabled")
        self.chk_legacy.configure(state="disabled")
        self.chk_gpu.configure(state="disabled")
        self.spin_workers.configure(state="disabled")
        self.progress.configure(value=0)
        self.step_var.set("准备安装…")
        self._log("开始安装；如中途取消，已完成的步骤会保留。")
        self.installer = Installer(
            self.cfg, emit=self._q.put, cancel=threading.Event(),
            install_standard=self.standard_var.get(),
            install_gpu=self.gpu_var.get(),
            install_legacy=self.legacy_var.get())
        threading.Thread(target=self.installer.run, daemon=True).start()

    def _needs_extra(self) -> bool:
        rep = self.report
        if not rep:
            return False
        std_missing = not (rep.by_key("models_standard")
                           and rep.by_key("models_standard").ok)
        need_std = bool(self.standard_var.get()) and std_missing
        need_gpu = bool(self.gpu_var.get()) and bool(rep.hardware.gpu)
        return bool(need_std or need_gpu)

    def _on_cancel(self):
        if self.installer:
            self.btn_cancel.configure(state="disabled", text="正在取消…")
            self.installer.cancel()

    def _on_later(self):
        ready = bool(self.report and self.report.ready)
        self.cfg.onboarding_done = True
        self.cfg.env_ok = ready
        settings.save_config(self.cfg)
        if self.on_done:
            self.on_done(self.cfg, ready)
        self.destroy()

    def _finish(self, ready: bool):
        self.cfg.onboarding_done = True
        self.cfg.env_ok = ready
        settings.save_config(self.cfg)
        if self.on_done:
            self.on_done(self.cfg, ready)
        if ready:
            msg = ("所有依赖已下载、安装并校验完成，可以开始转换了。"
                   if self._installed else "环境已就绪，可以开始转换了。")
            messagebox.showinfo("完成", msg, parent=self)
        self.destroy()

    def _show_manual(self):
        messagebox.showinfo("手动安装说明", envcheck.manual_install_text(self.cfg),
                            parent=self)

    # ---------- 事件 ----------
    def _poll(self):
        try:
            for _ in range(200):
                ev = self._q.get_nowait()
                self._apply_event(ev)
        except queue.Empty:
            pass
        try:
            if self.winfo_exists():
                self.after(100, self._poll)
        except tk.TclError:
            pass

    def _apply_event(self, ev: dict):
        t = ev.get("type")
        if t == "report":
            self._apply_report(ev["report"])
        elif t == "check_error":
            self.status_var.set("自检失败：" + ev.get("message", ""))
            self.btn_primary.configure(state="normal")
        elif t == "install_log":
            self._log(f'[{ev.get("step","")}] {ev.get("line","")}')
        elif t == "install_step":
            state = ev.get("state")
            step = ev.get("step")
            title = ev.get("title") or STEP_TITLES.get(step, step)
            phase = ev.get("phase", "")
            try:
                idx = self.step_order.index(step)
            except ValueError:
                idx = 0
            total = len(self.step_order)
            if state == "running":
                self.progress.configure(value=idx / total * 100)
                self.step_var.set(f"第 {idx+1}/{total} 步：{title}")
                self.step_progress.stop()
                self.step_progress.configure(mode="indeterminate")
                self.step_progress.start(12)
                phase_cn = {"download": "正在下载", "install": "正在安装",
                            "verify": "正在校验"}.get(phase, "正在处理")
                self.step_progress_var.set(f"{phase_cn}：{title}…")
            elif state == "ok":
                self.step_progress.stop()
                self.step_progress.configure(mode="determinate", value=100)
                self.progress.configure(value=(idx + 1) / total * 100)
                self.step_var.set(f"✓ 第 {idx+1}/{total} 步：{title} 完成")
                self.step_progress_var.set(f"完成：{title}")
            elif state in ("failed", "cancelled"):
                self.step_progress.stop()
                mark = "✗" if state == "failed" else "■"
                self.step_var.set(f"{mark} {title}：{ev.get('message','')}")
                self.step_progress_var.set(ev.get("message", ""))
        elif t == "install_progress":
            step = ev.get("step")
            title = STEP_TITLES.get(step, step)
            pct = ev.get("pct")
            text = ev.get("text") or ""
            if pct is not None:
                self.step_progress.stop()
                self.step_progress.configure(mode="determinate",
                                             value=max(0, min(100, float(pct))))
            if text:
                self.step_progress_var.set(f"{title}：{text}")
        elif t == "install_done":
            self._installed = True
            self.step_progress.stop()
            self.step_progress.configure(mode="determinate", value=100)
            self.progress.configure(value=100)
            self._log("全部依赖下载/安装完成，正在重新校验…")
            self.step_var.set("全部依赖已下载/安装，正在校验…")
            threading.Thread(target=self._recheck_after_install,
                             daemon=True).start()
        elif t == "post_report":
            rep = ev["report"]
            self._installing = False
            if rep.ready and self._gpu_ok_or_not_requested(rep):
                self._apply_report(rep)
                self._log("校验通过，环境已就绪。")
                self._finish(True)
            else:
                self._apply_report(rep)
                self.step_var.set("安装完成，但校验未通过，请查看日志")
                self.step_progress_var.set("部分组件未就绪")
                self.btn_primary.configure(state="normal", text="重试安装")
                self.btn_later.configure(state="normal")
                self.btn_cancel.configure(state="disabled", text="取消安装")
        elif t == "install_error":
            self._installing = False
            self.step_progress.stop()
            self._log("安装失败：" + ev.get("message", ""))
            self.step_var.set("安装失败：" + ev.get("message", ""))
            self.step_progress_var.set("安装失败，可重试或查看日志")
            self.btn_primary.configure(state="normal", text="重试安装")
            self.btn_later.configure(state="normal")
            self.btn_cancel.configure(state="disabled", text="取消安装")
            self.chk_std.configure(state="normal")
            self.chk_gpu.configure(state="normal")
            self.spin_workers.configure(state="normal")
        elif t == "install_cancelled":
            self._installing = False
            self.step_progress.stop()
            self.step_progress_var.set("已取消；已完成的步骤会保留")
            self.step_var.set("已取消；已完成的步骤会保留")
            self.btn_primary.configure(state="normal", text="继续安装")
            self.btn_later.configure(state="normal")
            self.btn_cancel.configure(state="disabled", text="取消安装")
            self.chk_std.configure(state="normal")
            self.chk_gpu.configure(state="normal")
            self.spin_workers.configure(state="normal")

    def _recheck_after_install(self):
        try:
            rep = envcheck.check_environment(self.cfg, deep_gpu=self.gpu_var.get())
            self._q.put({"type": "post_report", "report": rep})
        except Exception as e:
            self._q.put({"type": "install_error", "message": str(e)})

    def _log(self, line: str):
        self.log.configure(state="normal")
        self.log.insert("end", line.rstrip() + "\n")
        self.log.see("end")
        try:
            if int(self.log.index("end-1c").split(".")[0]) > 3000:
                self.log.delete("1.0", "500.0")
        except Exception:
            pass
        self.log.configure(state="disabled")

    def _on_close(self):
        if self._installing:
            if not messagebox.askyesno("退出", "安装仍在进行，退出会取消安装，确定？",
                                       parent=self):
                return
            if self.installer:
                self.installer.cancel()
        self.destroy()


class LegacyInstallDialog(tk.Toplevel):
    """只为旧版 DOC/XLS 安装 LibreOffice 的小窗口。"""

    def __init__(self, parent, cfg, on_done=None):
        super().__init__(parent)
        self.cfg = cfg
        self.on_done = on_done
        self._q: queue.Queue = queue.Queue()
        self.installer = None
        self.title("安装 LibreOffice（旧版 DOC/XLS）")
        self.geometry("700x460")
        self.transient(parent)
        try:
            self.grab_set()
        except Exception:
            pass
        ttk.Label(self, text="旧版 .doc / .xls 需要 LibreOffice",
                  font=("Microsoft YaHei UI", 13, "bold")).pack(
            anchor="w", padx=12, pady=(12, 4))
        ttk.Label(self, text="将通过 winget 自动安装，约 350 MB；"
                             "如弹出 UAC 请允许。安装完成后即可转换旧版文件。",
                  foreground="#555", wraplength=660).pack(anchor="w", padx=12)
        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x", padx=12, pady=10)
        self.log = scrolledtext.ScrolledText(self, height=14, state="disabled",
                                             font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=12, pady=4)
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=12, pady=8)
        self.btn_retry = ttk.Button(bar, text="重试", command=self.start,
                                    state="disabled")
        self.btn_retry.pack(side="left")
        ttk.Button(bar, text="关闭", command=self._close).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(100, self._poll)
        self.after(200, self.start)

    def start(self):
        from office_convert import find_soffice
        if find_soffice():
            self._done(True)
            return
        self.btn_retry.configure(state="disabled")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self._log("开始安装 LibreOffice…")
        self.installer = Installer(
            self.cfg, emit=self._q.put, cancel=threading.Event(),
            install_standard=False, install_gpu=False,
            install_legacy=True, only_legacy=True)
        threading.Thread(target=self.installer.run, daemon=True).start()

    def _poll(self):
        try:
            for _ in range(100):
                ev = self._q.get_nowait()
                t = ev.get("type")
                if t == "install_log":
                    self._log(ev.get("line", ""))
                elif t == "install_step":
                    if ev.get("state") == "ok":
                        self.progress.stop()
                        self.progress.configure(mode="determinate", value=100)
                    self._log(f'[{ev.get("step","")}] {ev.get("message","")}')
                elif t == "install_done":
                    self._done(True)
                    return
                elif t == "install_error":
                    self.progress.stop()
                    self.btn_retry.configure(state="normal")
                    self._log("安装失败：" + ev.get("message", ""))
                    messagebox.showerror("LibreOffice 安装失败",
                                         ev.get("message", ""), parent=self)
                elif t == "install_cancelled":
                    self.progress.stop()
                    self._log("已取消")
                    self.btn_retry.configure(state="normal")
        except queue.Empty:
            pass
        try:
            if self.winfo_exists():
                self.after(100, self._poll)
        except tk.TclError:
            pass

    def _done(self, ok: bool):
        self.progress.stop()
        if self.on_done:
            self.on_done(ok)
        if ok:
            messagebox.showinfo("完成", "LibreOffice 已安装，可以转换旧版 DOC/XLS 了。",
                                parent=self)
        self.destroy()

    def _close(self):
        if self.installer:
            self.installer.cancel()
        self.destroy()

    def _log(self, line: str):
        self.log.configure(state="normal")
        self.log.insert("end", line.rstrip() + "\n")
        self.log.see("end")
        try:
            if int(self.log.index("end-1c").split(".")[0]) > 2000:
                self.log.delete("1.0", "500.0")
        except Exception:
            pass
        self.log.configure(state="disabled")
