# -*- coding: utf-8 -*-
"""依赖安装：uv → Python 环境 → MinerU → 模型 → CUDA → 校验。

所有步骤都自动完成，界面只需要一次“确认安装”。
每一步都会发 install_step / install_progress 事件，供进度条显示：

  install_step:     step, state, title, phase, message
  install_progress: step, pct, mb_done, mb_total, packages, installed, phase, text
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from . import envcheck, settings
from .platform_win import CREATE_NO_WINDOW, disk_free_gb, kill_tree, nvidia_info

MIRRORS = [
    "https://mirrors.aliyun.com/pypi/simple",
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    None,
]
UV_URLS = [
    "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip",
    "https://ghproxy.net/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip",
]
MODEL_SOURCES = ["modelscope", "huggingface", "auto"]

STEP_TITLES = {
    "uv": "uv 包管理器",
    "venv": "Python 3.12 环境",
    "pip": "MinerU / PDF 依赖",
    "models_basic": "基础模型",
    "models_standard": "增强补漏模型",
    "libreoffice": "LibreOffice（旧版 DOC/XLS）",
    "verify": "安装校验",
}


class InstallCancelled(Exception):
    pass


def _mb(value: float, unit: str) -> float:
    unit = (unit or "").upper()
    if unit in ("GB", "G", "GIB"):
        return value * 1024
    if unit in ("KB", "K", "KIB"):
        return value / 1024
    return value


class Installer:
    def __init__(self, cfg, emit, cancel: threading.Event,
                 install_standard: bool = True, install_gpu: bool = False,
                 install_legacy: bool = False, only_legacy: bool = False):
        self.cfg = cfg
        self.emit = emit
        self.cancel_event = cancel
        self.install_standard = install_standard
        self.install_gpu = install_gpu
        self.install_legacy = install_legacy
        self.only_legacy = only_legacy
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._state_path = settings.data_dir() / "install_state.json"
        self._state = self._load_state()
        self._prog: dict = {}
        self._log_path = settings.logs_dir() / (
            "install-" + time.strftime("%Y%m%d-%H%M%S") + ".log")

    # ---------- 状态 ----------
    def _load_state(self) -> dict:
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"steps": {}}

    def _save_state(self):
        try:
            self._state["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._state["env_dir"] = str(self.cfg.env_dir)
            self._state["mineru_home"] = str(self.cfg.mineru_home)
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            os.replace(tmp, self._state_path)
        except Exception:
            pass

    def _set_state(self, step: str, state: str, message: str = "",
                   title: str = "", phase: str = ""):
        title = title or STEP_TITLES.get(step, step)
        self._state.setdefault("steps", {})[step] = {
            "state": state, "message": message[:500],
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self._save_state()
        self.emit({"type": "install_step", "step": step, "state": state,
                   "title": title, "phase": phase, "message": message})

    # ---------- 日志/进度 ----------
    def _file_log(self, step: str, line: str):
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(f"[{time.strftime('%H:%M:%S')}][{step}] {line}\n")
        except Exception:
            pass

    def _parse_progress(self, step: str, line: str):
        st = self._prog.setdefault(step, {
            "pct": None, "mb": None, "total_mb": None,
            "packages": None, "installed": None, "phase": "download",
            "wheels": set(), "text": "",
        })
        changed = False
        # uv/pip 包数量
        m = re.search(r"Resolved (\d+) packages?", line)
        if m:
            st["packages"] = int(m.group(1))
            st["phase"] = "download"
            st["text"] = f"正在下载依赖：已解析 {st['packages']} 个包"
            changed = True
        m = re.search(r"Prepared (\d+) packages?", line)
        if m:
            st["packages"] = int(m.group(1))
            st["phase"] = "install"
            st["pct"] = 100.0
            st["text"] = f"正在安装：{st['packages']} 个依赖包"
            changed = True
        m = re.search(r"Installed (\d+) packages?", line)
        if m:
            st["installed"] = int(m.group(1))
            st["phase"] = "install"
            st["pct"] = 100.0
            st["text"] = f"已安装 {st['installed']} 个依赖包"
            changed = True
        # uv 下载的 wheel 数量
        if line.startswith("DEBUG Found fresh response") and ".whl" in line:
            url = line.split("for:", 1)[-1].strip()
            if url not in st["wheels"]:
                st["wheels"].add(url)
                changed = True
        if st["phase"] == "download" and st["wheels"] and st["packages"]:
            st["text"] = (f"正在下载依赖：{len(st['wheels'])}/{st['packages']} 个包")
            st["pct"] = min(100.0, len(st["wheels"]) * 100.0 / st["packages"])
            changed = True
        # 百分比
        m = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", line)
        if m:
            st["pct"] = min(100.0, float(m.group(1)))
            changed = True
        # uv 每个包的体积：Downloading chardet (1.2MiB) —— 累加
        m = re.search(r"Downloading\s+(\S+)\s+\((\d+(?:\.\d+)?)\s*"
                      r"(KiB|MiB|GiB|KB|MB|GB)\)", line, re.I)
        if m:
            pkg = m.group(1)
            files = st.setdefault("files", set())
            if pkg not in files:
                files.add(pkg)
                st["mb"] = (st["mb"] or 0) + _mb(float(m.group(2)), m.group(3))
            st["text"] = f"已下载 {st['mb']:.1f} MB（{len(files)} 个包）"
            if st["packages"]:
                st["pct"] = min(100.0, len(files) * 100.0 / st["packages"])
            changed = True
        else:
            m = re.search(r"\((\d+(?:\.\d+)?)\s*(KiB|MiB|GiB|KB|MB|GB)\)",
                          line, re.I)
            if m:
                st["mb"] = _mb(float(m.group(1)), m.group(2))
                st["text"] = f"已下载 {st['mb']:.1f} MB"
                changed = True
        # tqdm: 123M/819M 或 45%|...| 123/819
        m = re.search(r"(\d+(?:\.\d+)?)\s*(KiB|MiB|GiB|[KMG]?B?)\s*/\s*"
                      r"(\d+(?:\.\d+)?)\s*(KiB|MiB|GiB|[KMG]?B?)", line, re.I)
        if m:
            st["mb"] = _mb(float(m.group(1)), m.group(2))
            st["total_mb"] = _mb(float(m.group(3)), m.group(4))
            if st["total_mb"]:
                st["pct"] = min(100.0, st["mb"] * 100.0 / st["total_mb"])
            st["text"] = (f"正在下载：{st['mb']:.0f}/{st['total_mb']:.0f} MB"
                          + (f"（{st['pct']:.0f}%）" if st["pct"] is not None else ""))
            changed = True
        if changed:
            self.emit({
                "type": "install_progress", "step": step,
                "pct": st["pct"], "mb_done": st["mb"], "mb_total": st["total_mb"],
                "packages": st["packages"], "installed": st["installed"],
                "phase": st["phase"], "text": st["text"] or line[:180],
            })

    def _log(self, step: str, line: str):
        line = re.sub(r"\[[0-9;]*[A-Za-z]", "", line).rstrip()
        if not line:
            return
        self._file_log(step, line)
        self._parse_progress(step, line)
        # DEBUG/TRACE 只进文件，不刷界面
        if line.startswith(("DEBUG ", "TRACE ")):
            return
        self.emit({"type": "install_log", "step": step, "line": line})

    # ---------- 子进程 ----------
    def _run_stream(self, cmd: list[str], step: str, timeout: int = 3600,
                    env: dict | None = None, cwd: str | None = None,
                    title: str = "", phase: str = "download") -> int:
        self._set_state(step, "running", "", title=title, phase=phase)
        self.emit({"type": "install_log", "step": step,
                   "line": "$ " + " ".join(str(x) for x in cmd)})
        e = os.environ.copy()
        e.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                  "UV_HTTP_TIMEOUT": "120", "UV_CONCURRENT_DOWNLOADS": "4"})
        if env:
            e.update({k: str(v) for k, v in env.items()})
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                 text=True, encoding="utf-8", errors="replace",
                                 env=e, cwd=cwd, creationflags=CREATE_NO_WINDOW)
        except Exception as ex:
            self._set_state(step, "failed", str(ex), title=title)
            return -1
        with self._lock:
            self._proc = p

        def reader():
            try:
                buf = ""
                while True:
                    chunk = p.stdout.read(4096)
                    if not chunk:
                        break
                    buf += chunk
                    parts = re.split("[" + chr(13) + chr(10) + "]", buf)
                    buf = parts.pop()
                    for line in parts:
                        if line.strip():
                            self._log(step, line)
                if buf.strip():
                    self._log(step, buf)
            except Exception:
                pass

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        t0 = time.time()
        while p.poll() is None:
            if self.cancel_event.is_set():
                kill_tree(p.pid)
                try:
                    p.wait(timeout=10)
                except Exception:
                    pass
                t.join(3)
                with self._lock:
                    self._proc = None
                self._set_state(step, "cancelled", "用户取消", title=title)
                raise InstallCancelled()
            if time.time() - t0 > timeout:
                kill_tree(p.pid)
                try:
                    p.wait(timeout=10)
                except Exception:
                    pass
                t.join(3)
                with self._lock:
                    self._proc = None
                self._set_state(step, "failed", f"超时 {timeout}s", title=title)
                return -3
            time.sleep(0.2)
        t.join(5)
        rc = p.returncode
        with self._lock:
            self._proc = None
        if rc == 0:
            self._set_state(step, "ok", "完成", title=title, phase="done")
        else:
            self._set_state(step, "failed", f"退出码 {rc}", title=title)
        return rc

    def cancel(self):
        self.cancel_event.set()
        with self._lock:
            p = self._proc
        if p:
            kill_tree(p.pid)

    # ---------- 检查 ----------
    def _env_ok(self) -> bool:
        py = Path(self.cfg.mineru_py)
        if not py.is_file():
            return False
        code = "import mineru,pymupdf,pdfplumber,onnxruntime;print('ok')"
        rc, out = envcheck._run([str(py), "-X", "utf8", "-c", code], 40)
        return rc == 0 and "ok" in out

    def _gpu_ok(self) -> bool:
        if not self.install_gpu:
            return True
        py = Path(self.cfg.mineru_py)
        if not py.is_file():
            return False
        code = ("import torch, lmdeploy;"
                "print('cuda', torch.cuda.is_available())")
        rc, out = envcheck._run([str(py), "-X", "utf8", "-c", code], 60)
        return rc == 0 and "cuda True" in out

    def _kit_ok(self) -> bool:
        kit = Path(self.cfg.mineru_bin)
        if not kit.is_file():
            return False
        rc, _ = envcheck._run([str(kit), "version"], 20)
        return rc == 0

    def _models_ok(self, tier: str) -> bool:
        kit = Path(self.cfg.mineru_bin)
        if not kit.is_file():
            return False
        cmd = [str(kit), "models", "verify", "--tier", tier,
               "--small-backend", "onnx"]
        if tier == "standard":
            cmd += ["--vlm-engine", "llama-cpp"]
        rc, _ = envcheck._run(cmd, 40, env=envcheck._env(
            {"MINERU_HOME": self.cfg.mineru_home}))
        return rc == 0

    # ---------- 步骤 ----------
    def _step_uv(self) -> Path:
        uv = envcheck.locate_uv(self.cfg)
        if uv:
            self._set_state("uv", "ok", str(uv), title=STEP_TITLES["uv"],
                            phase="done")
            self.cfg.uv_path = str(uv)
            return uv
        self._set_state("uv", "running", "下载 uv", title=STEP_TITLES["uv"],
                        phase="download")
        uv = self._download_uv()
        if not uv:
            raise RuntimeError("uv 下载失败，请检查网络后重试")
        self.cfg.uv_path = str(uv)
        return uv

    def _download_uv(self) -> Path | None:
        dest_dir = settings.bin_dir()
        last = ""
        for url in UV_URLS:
            zip_path = dest_dir / "uv.zip"
            try:
                self._log("uv", f"下载 {url}")
                with urllib.request.urlopen(url, timeout=60) as resp:
                    total = int(resp.headers.get("Content-Length") or 0)
                    got = 0
                    with open(zip_path, "wb") as fh:
                        while True:
                            if self.cancel_event.is_set():
                                raise InstallCancelled()
                            chunk = resp.read(1024 * 256)
                            if not chunk:
                                break
                            fh.write(chunk)
                            got += len(chunk)
                            if total:
                                mb = got / 1048576
                                total_mb = total / 1048576
                                self.emit({
                                    "type": "install_progress", "step": "uv",
                                    "pct": round(got * 100 / total, 1),
                                    "mb_done": mb, "mb_total": total_mb,
                                    "phase": "download",
                                    "text": f"正在下载 uv：{mb:.1f}/{total_mb:.1f} MB",
                                })
                self._set_state("uv", "running", "正在安装 uv",
                                title=STEP_TITLES["uv"], phase="install")
                with zipfile.ZipFile(zip_path) as z:
                    names = [n for n in z.namelist() if n.lower().endswith("uv.exe")]
                    if not names:
                        raise RuntimeError("压缩包里没有 uv.exe")
                    z.extract(names[0], dest_dir)
                found = list(dest_dir.rglob("uv.exe"))
                if found:
                    target = dest_dir / "uv.exe"
                    if found[0] != target:
                        shutil.copyfile(found[0], target)
                    rc, out = envcheck._run([str(target), "--version"], 30)
                    if rc == 0:
                        self._log("uv", out.strip())
                        self._set_state("uv", "ok", str(target),
                                        title=STEP_TITLES["uv"], phase="done")
                        return target
            except InstallCancelled:
                raise
            except Exception as e:
                last = str(e)
                self._log("uv", f"失败：{e}")
        # winget 兜底
        self._log("uv", "尝试 winget 安装 uv")
        rc = self._run_stream(
            ["winget", "install", "--id", "astral-sh.uv", "-e",
             "--accept-package-agreements", "--accept-source-agreements",
             "--disable-interactivity"], "uv", timeout=600,
            title=STEP_TITLES["uv"], phase="install")
        if rc == 0:
            uv = envcheck.locate_uv(self.cfg)
            if uv:
                return uv
        self._log("uv", f"uv 获取失败：{last}")
        return None

    def _step_venv(self, uv: Path) -> None:
        env_py = Path(self.cfg.mineru_py)
        if env_py.is_file():
            rc, out = envcheck._run(
                [str(env_py), "-X", "utf8", "-c",
                 "import sys;print(sys.version.split()[0])"], 20)
            if rc == 0:
                self._set_state("venv", "ok", f"已存在 Python {out.strip()}",
                                title=STEP_TITLES["venv"], phase="done")
                return
        env_dir = Path(self.cfg.env_dir)
        env_dir.parent.mkdir(parents=True, exist_ok=True)
        py = envcheck.find_python()
        if py:
            self._log("venv", f"使用系统 Python {py['version']}：{py['path']}")
            cmd = [str(uv), "venv", "--python", str(py["path"]), str(env_dir)]
        else:
            self._log("venv", "系统没有 3.10–3.12，uv 将自动下载 Python 3.12")
            cmd = [str(uv), "venv", "--python", "3.12", str(env_dir)]
        rc = self._run_stream(cmd, "venv", timeout=900,
                              title=STEP_TITLES["venv"], phase="install")
        if rc != 0:
            raise RuntimeError("创建 Python 环境失败；可更换安装目录后重试")
        if not env_py.is_file():
            raise RuntimeError(f"环境创建后仍找不到 {env_py}")

    def _step_pip(self, uv: Path) -> None:
        need_gpu = self.install_gpu and not self._gpu_ok()
        if self._env_ok() and not need_gpu:
            self._set_state("pip", "ok", "依赖已存在",
                            title=STEP_TITLES["pip"], phase="done")
            return
        env_py = str(self.cfg.mineru_py)
        pkg = "mineru[full]>=4.0,<5" if self.install_gpu else "mineru>=4.0,<5"
        pkgs = [pkg, "pymupdf", "pdfplumber"]
        base = [str(uv), "pip", "install", "--python", env_py,
                "--upgrade", "--verbose", *pkgs]
        last = ""
        for mirror in MIRRORS:
            cmd = base + (["-i", mirror] if mirror else [])
            self._log("pip", "使用源：" + (mirror or "官方 PyPI"))
            rc = self._run_stream(cmd, "pip", timeout=7200,
                                  title=STEP_TITLES["pip"], phase="download")
            if rc == 0:
                return
            last = f"退出码 {rc}"
            if self.cancel_event.is_set():
                raise InstallCancelled()
        raise RuntimeError(f"依赖安装失败：{last}；可换镜像重试")

    def _step_model(self, tier: str) -> None:
        title = STEP_TITLES[f"models_{tier}"]
        if self._models_ok(tier):
            self._set_state(f"models_{tier}", "ok", "模型已存在",
                            title=title, phase="done")
            return
        kit = str(self.cfg.mineru_bin)
        extra = ["--small-backend", "onnx"]
        if tier == "standard":
            extra += ["--vlm-engine", "llama-cpp"]
        last = ""
        for source in MODEL_SOURCES:
            cmd = [kit, "models", "download", "--tier", tier, *extra,
                   "--source", source]
            self._log(f"models_{tier}", f"使用源：{source}")
            rc = self._run_stream(cmd, f"models_{tier}", timeout=10800,
                                  env={"MINERU_HOME": self.cfg.mineru_home},
                                  title=title, phase="download")
            if rc == 0:
                return
            last = f"退出码 {rc}"
        raise RuntimeError(f"{tier} 模型下载失败：{last}；可换网络重试")

    def _step_libreoffice(self) -> None:
        from office_convert import find_soffice
        got = find_soffice()
        if got:
            self._set_state("libreoffice", "ok", str(got),
                            title=STEP_TITLES["libreoffice"], phase="done")
            return
        self._set_state("libreoffice", "running", "未安装，准备自动安装",
                        title=STEP_TITLES["libreoffice"], phase="install")
        winget = shutil.which("winget")
        if not winget:
            raise RuntimeError(
                "未找到 winget，无法自动安装 LibreOffice。"
                "请手动安装：https://www.libreoffice.org/download/")
        cmd = [winget, "install", "--id", "TheDocumentFoundation.LibreOffice",
               "-e", "--silent", "--accept-package-agreements",
               "--accept-source-agreements", "--disable-interactivity"]
        rc = self._run_stream(cmd, "libreoffice", timeout=2400,
                              title=STEP_TITLES["libreoffice"], phase="install")
        got = find_soffice()
        if rc != 0 or not got:
            raise RuntimeError(
                "LibreOffice 安装失败（winget 退出码 %s）。"
                "如果弹出 UAC 请允许；仍失败可手动安装："
                "https://www.libreoffice.org/download/" % rc)
        self._set_state("libreoffice", "ok", str(got),
                        title=STEP_TITLES["libreoffice"], phase="done")

    def _step_verify(self) -> None:
        self._set_state("verify", "running", "正在校验依赖",
                        title=STEP_TITLES["verify"], phase="verify")
        ok = True
        details = []
        if not self._env_ok():
            ok = False
            details.append("Python 依赖不可用")
        if not self._kit_ok():
            ok = False
            details.append("mineru-kit 不可用")
        if not self._models_ok("basic"):
            ok = False
            details.append("basic 模型未就绪")
        if self.install_standard and not self._models_ok("standard"):
            ok = False
            details.append("standard 模型未就绪")
        if self.install_gpu and not self._gpu_ok():
            ok = False
            details.append("CUDA 后端未就绪（请确认 NVIDIA 驱动）")
        self._set_state("verify", "ok" if ok else "failed",
                        "；".join(details), title=STEP_TITLES["verify"],
                        phase="done")
        if not ok:
            raise RuntimeError("安装后校验失败：" + "；".join(details))

    # ---------- 主流程 ----------
    def run(self) -> bool:
        try:
            if self.only_legacy:
                self._step_libreoffice()
                self.emit({"type": "install_done", "ok": True})
                return True
            env_dir = Path(self.cfg.env_dir)
            home = Path(self.cfg.mineru_home)
            env_dir.parent.mkdir(parents=True, exist_ok=True)
            home.parent.mkdir(parents=True, exist_ok=True)
            free = disk_free_gb(env_dir.parent)
            need = 5.0 if not env_dir.exists() else 2.0
            if self.install_gpu:
                need = max(need, 8.0)
            if free < need:
                raise RuntimeError(
                    f"磁盘空间不足：{env_dir.parent} 所在盘只剩 {free} GB，"
                    f"建议至少 {need:.0f} GB")
            for p in (env_dir.parent, home.parent):
                probe = p / ".pdf2md_write_test"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()

            self.emit({"type": "install_log", "step": "start",
                       "line": f"安装目录：{env_dir}"})
            self.emit({"type": "install_log", "step": "start",
                       "line": f"模型目录：{home}"})
            if self.install_gpu:
                self.emit({"type": "install_log", "step": "start",
                           "line": "将安装 NVIDIA CUDA 后端（mineru[full]）"})

            uv = self._step_uv()
            self._step_venv(uv)
            self._step_pip(uv)
            self._step_model("basic")
            if self.install_standard:
                self._step_model("standard")
            if self.install_legacy:
                self._step_libreoffice()
            self._step_verify()
            self.emit({"type": "install_done", "ok": True})
            return True
        except InstallCancelled:
            self.emit({"type": "install_cancelled"})
            return False
        except Exception as e:
            msg = str(e)
            self._log("error", msg)
            self.emit({"type": "install_error", "message": msg})
            return False
