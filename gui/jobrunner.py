# -*- coding: utf-8 -*-
"""转换任务队列：每份 PDF 一个子进程，进度写文件，可停止。"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import settings
from .platform_win import CREATE_NO_WINDOW, CREATE_NEW_PROCESS_GROUP, kill_tree

JOB_TIMEOUT = 6 * 3600


@dataclass
class Job:
    iid: str
    pdf: Path
    pages: int = 0
    kind: str = ""
    fmt: str = "pdf"
    out_dir: Path = field(default_factory=Path)
    out_stem: str = ""
    md_path: Path = field(default_factory=Path)
    qa_path: Path = field(default_factory=Path)
    status: str = "等待"
    seconds: float = 0
    qa: dict = field(default_factory=dict)
    error: str = ""
    proc: subprocess.Popen | None = None
    job_dir: Path | None = None
    started_at: float = 0

    def __post_init__(self):
        if not self.out_stem:
            self.out_stem = self.pdf.stem
        if not self.md_path:
            self.md_path = self.out_dir / (self.out_stem + ".md")
        if not self.qa_path:
            self.qa_path = self.out_dir / (self.out_stem + "_qa.json")


class JobRunner:
    def __init__(self, cfg, events: queue.Queue, workers: int = 1):
        self.cfg = cfg
        self.events = events
        self.workers = max(1, min(3, int(workers)))
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._procs: dict[str, subprocess.Popen] = {}
        self._q: queue.Queue[Job] = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._running = False
        self._total = 0
        self._finished = 0

    # ---------- 对外 ----------
    def start(self, jobs: list[Job]):
        if self._running:
            return
        self._stop.clear()
        self._running = True
        self._total = len(jobs)
        self._finished = 0
        for j in jobs:
            self._q.put(j)
        for _ in range(self.workers):
            t = threading.Thread(target=self._worker, daemon=True)
            t.start()
            self._threads.append(t)
        threading.Thread(target=self._monitor, daemon=True).start()

    def stop(self):
        self._stop.set()
        with self._lock:
            procs = list(self._procs.values())
        for p in procs:
            kill_tree(p.pid)
        # 未开始的任务
        while True:
            try:
                job = self._q.get_nowait()
            except queue.Empty:
                break
            job.status = "已停止"
            self.events.put({"type": "job_done", "job": job})
            self._finished += 1

    def is_running(self) -> bool:
        return self._running

    def wait(self, timeout: float = 5) -> bool:
        end = time.time() + timeout
        for t in self._threads:
            left = max(0.1, end - time.time())
            t.join(left)
        return not any(t.is_alive() for t in self._threads)

    # ---------- 内部 ----------
    def _worker(self):
        while not self._stop.is_set():
            try:
                job = self._q.get(timeout=0.5)
            except queue.Empty:
                return
            try:
                self._run_job(job)
            except Exception as e:
                job.status = "失败"
                job.error = str(e)[:800]
                self.events.put({"type": "job_done", "job": job})
            finally:
                self._finished += 1

    def _monitor(self):
        for t in self._threads:
            t.join()
        self._running = False
        self.events.put({"type": "runner_done",
                         "total": self._total, "finished": self._finished})

    def _job_dir(self, job: Job) -> Path:
        name = f'{time.strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:6]}-{job.out_stem[:40]}'
        p = settings.jobs_dir() / name
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _child_cmd(self, job_json: Path) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "_job", str(job_json)]
        return [sys.executable, "-X", "utf8",
                str(settings.app_dir() / "cli.py"), "_job", str(job_json)]

    def _child_env(self) -> dict:
        e = os.environ.copy()
        e.update({
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "MINERU_PY": self.cfg.mineru_py,
            "MINERU_BIN": self.cfg.mineru_bin,
            "MINERU_HOME": self.cfg.mineru_home,
            "MINERU_MODEL_SOURCE": "local",
        })
        for k in ("PYTHONHOME", "PYTHONPATH"):
            e.pop(k, None)
        return e

    def _run_job(self, job: Job):
        if self._stop.is_set():
            job.status = "已停止"
            self.events.put({"type": "job_done", "job": job})
            return
        job.status = "转换中"
        job.started_at = time.time()
        job.job_dir = self._job_dir(job)
        progress = job.job_dir / "progress.jsonl"
        result = job.job_dir / "result.json"
        job_json = job.job_dir / "job.json"
        payload = {
            "schema": 1,
            "pdf": str(job.pdf),
            "out_dir": str(job.out_dir),
            "out_stem": job.out_stem,
            "overwrite": bool(getattr(self.cfg, "overwrite", False)),
            "keep_json": bool(getattr(self.cfg, "keep_json", False)),
            "pages": "all",
            "device": getattr(self.cfg, "device", "cpu") or "cpu",
            "tier": getattr(self.cfg, "tier", "basic") or "basic",
            "tmp_dir": str(job.job_dir),
            "progress_file": str(progress),
            "result_file": str(result),
            "pages_known": job.pages,
            "fmt": job.fmt,
            "parent_pid": os.getpid(),
        }
        job_json.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        self.events.put({"type": "job_started", "job": job})

        log_path = job.job_dir / "child.log"
        with open(log_path, "wb") as logf:
            try:
                p = subprocess.Popen(
                    self._child_cmd(job_json),
                    cwd=str(job.job_dir),
                    env=self._child_env(),
                    stdin=subprocess.DEVNULL,
                    stdout=logf,
                    stderr=subprocess.STDOUT,
                    creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
                )
            except Exception as e:
                job.status = "失败"
                job.error = str(e)[:800]
                self.events.put({"type": "job_done", "job": job})
                return
            job.proc = p
            with self._lock:
                self._procs[job.iid] = p
            try:
                self._tail(job, progress, p)
            finally:
                with self._lock:
                    self._procs.pop(job.iid, None)

        rc = p.returncode
        res = {}
        if result.exists():
            try:
                res = json.loads(result.read_text(encoding="utf-8"))
            except Exception:
                res = {}

        if self._stop.is_set() and not res.get("ok"):
            job.status = "已停止"
        elif res.get("ok"):
            r = res.get("result") or {}
            if r.get("skipped"):
                job.status = "跳过"
            else:
                job.status = "完成"
            job.qa = r.get("qa") or {}
        else:
            job.status = "失败"
            job.error = (res.get("error") or self._log_tail(log_path))[:800]
            if not job.error:
                job.error = f"子进程退出码 {rc}"
        job.seconds = round(time.time() - job.started_at, 1)
        if job.qa_path.exists() and not job.qa:
            try:
                job.qa = json.loads(job.qa_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        # 成功任务清理临时目录，失败保留便于排查
        if job.status in ("完成", "跳过") and job.job_dir:
            try:
                shutil.rmtree(job.job_dir, ignore_errors=True)
                job.job_dir = None
            except Exception:
                pass
        self.events.put({"type": "job_done", "job": job})

    def _tail(self, job: Job, progress: Path, p: subprocess.Popen):
        off = 0
        t0 = time.time()
        last_ev = 0
        while p.poll() is None:
            if self._stop.is_set():
                kill_tree(p.pid)
                try:
                    p.wait(timeout=10)
                except Exception:
                    pass
                break
            if time.time() - t0 > JOB_TIMEOUT:
                kill_tree(p.pid)
                job.error = "单份超过 6 小时，已终止"
                break
            if progress.exists():
                try:
                    with open(progress, "rb") as fh:
                        fh.seek(off)
                        chunk = fh.read()
                    if chunk:
                        off += len(chunk)
                        for raw in chunk.splitlines():
                            try:
                                ev = json.loads(raw.decode("utf-8", "replace"))
                            except Exception:
                                continue
                            if ev.get("ev") == "phase":
                                self.events.put({"type": "job_progress",
                                                 "job": job, "event": ev})
                            elif ev.get("ev") == "done":
                                job.qa = (ev.get("qa") or {})
                except Exception:
                    pass
            now = time.time()
            if now - last_ev > 1:
                self.events.put({"type": "job_tick", "job": job,
                                 "elapsed": round(now - job.started_at, 1)})
                last_ev = now
            time.sleep(0.3)
        try:
            p.wait(timeout=10)
        except Exception:
            kill_tree(p.pid)

    @staticmethod
    def _log_tail(path: Path) -> str:
        try:
            data = path.read_text(encoding="utf-8", errors="replace")
            return data[-1200:]
        except Exception:
            return ""
